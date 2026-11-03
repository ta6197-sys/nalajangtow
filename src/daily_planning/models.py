"""User-approved reduced ERD. Amounts are cents; quantities are integer kg."""
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint

metadata = sa.MetaData()


def ident(name):
    return sa.Column(name, UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def moment(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


contract = sa.Table(
    'contract', metadata,
    sa.Column('contract_id', sa.Text, primary_key=True),
    sa.Column('allowed_minutes', sa.Integer, nullable=False),
    sa.Column('demurrage_daily_cents', sa.BigInteger, nullable=False),
    sa.Column('contract_terms', JSONB, nullable=False),
    sa.CheckConstraint('allowed_minutes >= 0', name='ck_contract_allowed'),
    sa.CheckConstraint('demurrage_daily_cents >= 0', name='ck_contract_rate'),
)

port_call = sa.Table(
    'port_call', metadata,
    sa.Column('call_id', sa.Text, primary_key=True),
    sa.Column('contract_id', sa.Text, sa.ForeignKey('contract.contract_id'), nullable=False),
    moment('known_at'), moment('nor_at'), moment('available_at'),
    sa.Column('cargo_quantity_kg', sa.BigInteger, nullable=False),
    sa.Column('work_minutes', sa.Integer, nullable=False),
    sa.CheckConstraint('cargo_quantity_kg >= 0', name='ck_call_quantity'),
    sa.CheckConstraint('work_minutes > 0', name='ck_call_work'),
)

berth = sa.Table(
    'berth', metadata,
    sa.Column('berth_id', sa.Text, primary_key=True),
    sa.Column('berth_name', sa.Text, nullable=False),
    sa.Column('is_active', sa.Boolean, nullable=False),
)

call_state = sa.Table(
    'call_state', metadata, ident('state_id'),
    sa.Column('call_id', sa.Text, sa.ForeignKey('port_call.call_id'), nullable=False),
    sa.Column('berth_id', sa.Text, sa.ForeignKey('berth.berth_id'), nullable=True),
    sa.Column('experiment_key', sa.Text, nullable=False), moment('state_at'),
    sa.Column('status', sa.Text, nullable=False),
    sa.Column('remaining_work_kg', sa.BigInteger, nullable=False),
    sa.Column('accrued_demurrage_cents', sa.BigInteger, nullable=False),
    sa.Column('contract_clock_state', JSONB, nullable=False),
    sa.UniqueConstraint('call_id', 'experiment_key', 'state_at', name='uq_call_state_at'),
    sa.CheckConstraint('remaining_work_kg >= 0', name='ck_state_quantity'),
    sa.CheckConstraint('accrued_demurrage_cents >= 0', name='ck_state_demurrage'),
)

planning_run = sa.Table(
    'planning_run', metadata, ident('run_id'),
    sa.Column('experiment_key', sa.Text, nullable=False),
    moment('planning_as_of'), moment('horizon_end_at'), moment('freeze_end_at'),
    sa.Column('input_snapshot_path', sa.Text, nullable=False),
    sa.Column('weather_file_path', sa.Text, nullable=False),
    sa.Column('run_config', JSONB, nullable=False),
    sa.Column('solver_status', sa.Text, nullable=False),
    sa.CheckConstraint('planning_as_of < freeze_end_at AND freeze_end_at <= horizon_end_at',
                       name='ck_run_windows'),
)

schedule_candidate = sa.Table(
    'schedule_candidate', metadata, ident('candidate_id'),
    sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('planning_run.run_id'), nullable=False),
    sa.Column('strategy', sa.Text, nullable=False),
    sa.Column('candidate_rank', sa.Integer, nullable=False),
    sa.Column('expected_demurrage_cents', sa.BigInteger, nullable=False),
    sa.Column('simulated_demurrage_cents', sa.BigInteger, nullable=True),
    sa.Column('evaluation_metrics', JSONB, nullable=False),
    sa.UniqueConstraint('run_id', 'candidate_rank', name='uq_candidate_rank'),
    sa.CheckConstraint('candidate_rank > 0', name='ck_candidate_rank'),
    sa.CheckConstraint('expected_demurrage_cents >= 0', name='ck_candidate_expected'),
    sa.CheckConstraint('simulated_demurrage_cents >= 0', name='ck_candidate_simulated'),
)

schedule_assignment = sa.Table(
    'schedule_assignment', metadata, ident('assignment_id'),
    sa.Column('candidate_id', UUID(as_uuid=True), sa.ForeignKey('schedule_candidate.candidate_id'), nullable=False),
    sa.Column('call_id', sa.Text, sa.ForeignKey('port_call.call_id'), nullable=False),
    sa.Column('berth_id', sa.Text, sa.ForeignKey('berth.berth_id'), nullable=False),
    moment('start_at'), moment('completion_at'),
    sa.UniqueConstraint('candidate_id', 'call_id', name='uq_assignment_call'),
    sa.CheckConstraint('completion_at > start_at', name='ck_assignment_window'),
)

schedule_commit = sa.Table(
    'schedule_commit', metadata, ident('commit_id'),
    sa.Column('assignment_id', UUID(as_uuid=True), sa.ForeignKey('schedule_assignment.assignment_id'), nullable=False),
    sa.Column('experiment_key', sa.Text, nullable=False),
    sa.Column('berth_id', sa.Text, sa.ForeignKey('berth.berth_id'), nullable=False),
    moment('confirmed_at'), moment('locked_from_at'), moment('locked_until_at'),
    sa.Column('status', sa.Text, nullable=False),
    sa.UniqueConstraint('assignment_id', name='uq_commit_assignment'),
    sa.CheckConstraint('locked_until_at > locked_from_at', name='ck_commit_window'),
    ExcludeConstraint(('experiment_key', '='), ('berth_id', '='),
                      (sa.func.tstzrange(sa.column('locked_from_at'), sa.column('locked_until_at'), '[)'), '&&'),
                      name='ex_commit_berth_overlap', using='gist',
                      where=sa.text("status IN ('LOCKED', 'IN_PROGRESS', 'COMPLETED')")),
)

operation_event = sa.Table(
    'operation_event', metadata, ident('event_id'),
    sa.Column('experiment_key', sa.Text, nullable=False),
    sa.Column('call_id', sa.Text, sa.ForeignKey('port_call.call_id'), nullable=False),
    sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('planning_run.run_id'), nullable=True),
    moment('event_at'), moment('recorded_at'),
    sa.Column('event_kind', sa.Text, nullable=False),
    sa.Column('actor', sa.Text, nullable=False),
    sa.Column('reason', sa.Text, nullable=False),
    sa.Column('before_value', JSONB, nullable=False),
    sa.Column('after_value', JSONB, nullable=False),
    sa.CheckConstraint("event_kind IN ('CALL_UPDATED', 'CALL_CANCELLED', 'ADMIN_RESCHEDULE', 'ADMIN_DEFER')",
                       name='ck_operation_event_kind'),
    sa.CheckConstraint("length(trim(reason)) > 0", name='ck_operation_event_reason'),
)
