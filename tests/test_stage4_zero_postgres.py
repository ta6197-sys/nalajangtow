"""Opt-in, rollback-only PostgreSQL parity test for Stage-4 zero-minute phases.

Runs in the existing source DB but creates only unique ephemeral experiment
keys and rolls every write back, including on assertion failure. It does not
test SK physical V3 feasibility (its 33 incompatible synthetic calls remain
blocked by strict V3 preflight in separate tests).

RUN_STAGE4_DB_TESTS=1 python -m unittest discover -s tests -p 'test_stage4_zero_postgres.py' -v
"""
from __future__ import annotations

import os
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa

from weather_optimizer import WeatherSeries
from src.daily_planning.berth_phases import ZERO_PHASE_CONFIG
from src.daily_planning.context import local_time, read_snapshot
from src.daily_planning.candidate_store import save_evaluated_candidate
from src.daily_planning.database import get_engine
from src.daily_planning.evaluation import evaluate_baseline
from src.daily_planning.exact_search import build_domain
from src.daily_planning.models import (
    berth, call_state, contract, planning_run, port_call,
    schedule_assignment, schedule_candidate,
)
from src.daily_planning.operations import advance_in_transaction, confirm_in_transaction


@unittest.skipUnless(
    os.environ.get("RUN_STAGE4_DB_TESTS") == "1",
    "Requires explicit Stage-4 isolated PostgreSQL integration opt-in",
)
class Stage4ZeroPostgresTest(unittest.TestCase):
    @staticmethod
    def _candidate_rows(connection, candidate_id):
        return [dict(r) for r in connection.execute(
            sa.select(schedule_assignment.c.call_id,
                      schedule_assignment.c.berth_id,
                      schedule_assignment.c.start_at,
                      schedule_assignment.c.completion_at)
            .where(schedule_assignment.c.candidate_id == candidate_id)
            .order_by(schedule_assignment.c.call_id)
        ).mappings()]

    @staticmethod
    def _seed_rows(domain):
        """Use only evaluated scheduled options, excluding deferred placeholders."""
        return [domain["options"][idx]["row"] for idx in domain["seeds"][0]
                if domain["options"][idx]["row"] is not None]

    @staticmethod
    def _assignment_identity(rows):
        return sorted((r["call_id"], r["berth_id"],
                       local_time(r.get("start_at", r.get("work_start_at"))),
                       local_time(r["completion_at"])) for r in rows)

    @staticmethod
    def _state_identity(step):
        """Compare economic/physical state, not test-specific UUIDs and hashes."""
        return sorted((
            state["call_id"], state["status"], state["berth_id"],
            state["remaining_work_kg"], state["accrued_demurrage_cents"],
            state["contract_clock_state"]["remaining_work_minutes"],
        ) for state in step["next_states"])

    def test_three_day_legacy_vs_zero_phase_with_full_rollback(self):
        engine = get_engine()
        self.assertEqual(engine.url.database, "ulsan_clx_source_daywork_v2")
        legacy_key = "stage4-legacy-tx-" + uuid.uuid4().hex
        zero_key = "stage4-zero-tx-" + uuid.uuid4().hex
        keys = (legacy_key, zero_key)
        baseline_source_count = None
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(contract)), 144)
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(port_call)), 140)
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(berth)), 8)

                    source = connection.execute(
                        sa.select(planning_run)
                        .where(planning_run.c.experiment_key == "source-daywork-v2")
                        .order_by(planning_run.c.planning_as_of, planning_run.c.run_id)
                        .limit(1)
                    ).mappings().one()
                    source_run_id = source["run_id"]
                    baseline_source_count = connection.scalar(
                        sa.select(sa.func.count()).select_from(call_state)
                        .where(call_state.c.experiment_key == "source-daywork-v2"))
                    first_candidate = connection.scalar(
                        sa.select(schedule_candidate.c.candidate_id)
                        .where(schedule_candidate.c.run_id == source_run_id,
                               schedule_candidate.c.candidate_rank == 1))
                    self.assertIsNotNone(first_candidate)
                    first_rows = self._candidate_rows(connection, first_candidate)
                    policy = source["run_config"]["input_snapshot"].get(
                        "berthing_policy", "LEGACY_UNRESTRICTED")
                    as_of = source["planning_as_of"]
                    weather_path = source["weather_file_path"]
                    self.assertTrue(Path(weather_path).exists())
                    weather = WeatherSeries.from_csv(Path(weather_path))

                    # The test uses two NEW keys. No source candidate/state is changed.
                    # Disabling the V3 opt-in here does not change V3's strict preflight:
                    # these are non-V3, legacy berth baseline comparisons only.
                    with patch.dict(os.environ, {
                        "BERTH_PHASE_EXPERIMENT_KEY": zero_key,
                        "SK_PHYSICAL_EXPERIMENT_KEY": "__stage4_not_physical_v3__",
                    }):
                        worked_segments = 0
                        for day in range(3):
                            snapshots = {}
                            results = {}
                            schedules = {}
                            for key in keys:
                                snap = read_snapshot(
                                    connection, key, as_of, weather_path,
                                    berthing_policy=policy)
                                snapshots[key] = snap
                                if key == zero_key:
                                    self.assertEqual(
                                        snap.get("berth_phase"), ZERO_PHASE_CONFIG)
                                else:
                                    self.assertNotIn("berth_phase", snap)

                                # The initial schedule is an existing, validated
                                # source candidate. Subsequent days use each
                                # experiment's independently generated same seed.
                                if day == 0:
                                    chosen = first_rows
                                else:
                                    domain = build_domain(snap, weather)
                                    chosen = self._seed_rows(domain)
                                schedules[key] = chosen
                                results[key] = evaluate_baseline(
                                    snap, chosen, weather)

                            self.assertEqual(
                                self._assignment_identity(schedules[legacy_key]),
                                self._assignment_identity(schedules[zero_key]))
                            old = results[legacy_key]
                            new = results[zero_key]
                            self.assertEqual(
                                old["schedule_signature"], new["schedule_signature"])
                            self.assertEqual(
                                old["freeze_signature"], new["freeze_signature"])
                            self.assertEqual(
                                old["evaluation"]["summary"],
                                new["evaluation"]["summary"])
                            self.assertEqual(
                                old["evaluation"]["berth_metrics"],
                                new["evaluation"]["berth_metrics"])
                            self.assertEqual(
                                len(old["evaluation"]["vessel_results"]),
                                len(new["evaluation"]["vessel_results"]))
                            self.assertEqual(
                                [r["call_id"] for r in old["evaluation"]["vessel_results"]],
                                [r["call_id"] for r in new["evaluation"]["vessel_results"]])
                            for r in new["evaluation"]["vessel_results"]:
                                self.assertEqual(
                                    local_time(r["berthing_start_at"]),
                                    local_time(r["work_start_at"]))
                                self.assertEqual(
                                    local_time(r["cargo_work_start_at"]),
                                    local_time(r["work_start_at"]))
                                self.assertEqual(
                                    local_time(r["cargo_work_end_at"]),
                                    local_time(r["completion_at"]))
                                self.assertEqual(
                                    local_time(r["berth_release_at"]),
                                    local_time(r["completion_at"]))
                                self.assertEqual(r["berthing_minutes"], 0)
                                self.assertEqual(r["unberthing_minutes"], 0)

                            operating_steps = {}
                            for key in keys:
                                run_id = uuid.uuid4()
                                snap = snapshots[key]
                                run = dict(
                                    run_id=run_id, experiment_key=key,
                                    planning_as_of=as_of,
                                    horizon_end_at=as_of + timedelta(hours=72),
                                    freeze_end_at=as_of + timedelta(hours=24),
                                    input_snapshot_path="/tmp/stage4-rollback-only-no-export.json",
                                    weather_file_path=weather_path,
                                    run_config={"input_snapshot": snap},
                                    solver_status="FEASIBLE",
                                )
                                connection.execute(planning_run.insert().values(**run))
                                candidate_id = uuid.uuid4()
                                result = results[key]
                                save_evaluated_candidate(
                                    connection, run_id, candidate_id=candidate_id,
                                    strategy="STAGE4_TRANSACTIONAL_PARITY",
                                    candidate_rank=1,
                                    expected_demurrage_cents=result["evaluation"]["summary"][
                                        "demurrage_increment_to_completion_cents"],
                                    evaluation_result=result,
                                    solver_details={"solver_status": "FEASIBLE",
                                                    "relative_gap": None},
                                )
                                confirmation = confirm_in_transaction(
                                    connection, candidate_id)
                                self.assertEqual(confirmation["status"], "CONFIRMED")
                                operating_steps[key] = advance_in_transaction(
                                    connection, run_id)

                            left = operating_steps[legacy_key]
                            right = operating_steps[zero_key]
                            for metric in (
                                "actual_day_demurrage_cents",
                                "actual_day_despatch_cents",
                                "accumulated_demurrage_cents",
                                "completed_call_ids",
                                "ongoing_call_ids",
                            ):
                                self.assertEqual(left[metric], right[metric], metric)
                            self.assertEqual(
                                self._state_identity(left), self._state_identity(right))
                            for state in right["next_states"]:
                                sched = state["contract_clock_state"].get("schedule", {})
                                if sched:
                                    self.assertEqual(
                                        local_time(sched["berth_release_at"]),
                                        local_time(sched["completion_at"]))
                                    self.assertEqual(
                                        local_time(sched["berthing_start_at"]),
                                        local_time(sched["work_start_at"]))
                                    worked_segments += len(sched["work_intervals"])
                            as_of = as_of + timedelta(hours=24)
                        self.assertGreater(worked_segments, 0)

                    # Still within the transaction: all writes belong only
                    # to ephemeral keys, never the source experiment.
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(call_state)
                        .where(call_state.c.experiment_key == "source-daywork-v2")),
                        baseline_source_count)
                finally:
                    transaction.rollback()

            # Verify with a fresh connection: no stage4 test state survives.
            with engine.connect() as connection:
                for key in keys:
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(planning_run)
                        .where(planning_run.c.experiment_key == key)), 0)
                    self.assertEqual(connection.scalar(
                        sa.select(sa.func.count()).select_from(call_state)
                        .where(call_state.c.experiment_key == key)), 0)
                self.assertEqual(connection.scalar(
                    sa.select(sa.func.count()).select_from(call_state)
                    .where(call_state.c.experiment_key == "source-daywork-v2")),
                    baseline_source_count)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
