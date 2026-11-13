"""Self-contained read-only regression tests for the supplied policy patch."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from extend_v5_synthetic_policy import (  # noqa
    BASE_BLOBS, BASIC_OUT, STAGE2B_OUT, POLICY_OUT, PILOT_OUT,
    BASE_END, EXT_END, START, _blob, emit, outputs, extend_source,
)
from diagnose_s20_candidates import diagnose  # noqa


class PolicyExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = ROOT / 'tests' / 'fixtures' / 'source_repo'

    def test_original_sources_git_blob_identity(self):
        for name, expected in BASE_BLOBS.items():
            self.assertEqual(_blob((self.src / 'config' / name).read_bytes()), expected)

    def test_22_berth_windows_extended_only(self):
        orig = json.loads((self.src/'config'/'v5_user_policy_20261112.synthetic.json').read_text())
        new = outputs(self.src)[POLICY_OUT]
        self.assertEqual(len(new['berths']), 11)
        count = 0
        for bid, row in new['berths'].items():
            old = orig['berths'][bid]
            for k in ('pilotage_windows', 'work_windows'):
                self.assertEqual(old[k][0]['start_at'], START)
                self.assertEqual(old[k][0]['end_at'], BASE_END)
                self.assertEqual(row[k][0], {'start_at': START, 'end_at': EXT_END})
                count += 1
            a = copy.deepcopy(row)
            a['pilotage_windows'] = old['pilotage_windows']
            a['work_windows'] = old['work_windows']
            self.assertEqual(a, old)
        self.assertEqual(count, 22)
        z = copy.deepcopy(new)
        z['berths'] = orig['berths']
        self.assertEqual(z, orig)

    def test_55_departure_windows_extended_only(self):
        orig = json.loads((self.src/'config'/'v5_balanced_pilotage_context.json').read_text())
        new = outputs(self.src)[PILOT_OUT]
        self.assertEqual(len(new['departure_windows_by_berth_call']), 11)
        count = 0
        for bid, ids in new['departure_windows_by_berth_call'].items():
            self.assertEqual(len(ids), 5)
            for cid, win in ids.items():
                self.assertEqual(orig['departure_windows_by_berth_call'][bid][cid][0]['end_at'], BASE_END)
                self.assertEqual(win[0], {'start_at': START, 'end_at': EXT_END})
                count += 1
        self.assertEqual(count, 55)
        self.assertEqual(new['daylight_path'], '../src/daily_planning/daylight_windows.csv')
        reconstructed = copy.deepcopy(new)
        reconstructed['daylight_path'] = orig['daylight_path']
        reconstructed['departure_windows_by_berth_call'] = orig['departure_windows_by_berth_call']
        self.assertEqual(reconstructed, orig)

    def test_versioned_execution_configs(self):
        obj = outputs(self.src)
        base = obj[BASIC_OUT]
        self.assertEqual(base['policy_file'], POLICY_OUT)
        self.assertEqual(base['pilotage_context_file'], PILOT_OUT)
        self.assertEqual(base['scenario'], 'V5_BALANCED_154_EXT_20270109')
        st = obj[STAGE2B_OUT]
        self.assertEqual(st['max_deferral_probes'], 8)
        self.assertEqual(st['deferral_policy_version'], 'V5_ROLLING_SAFE_ADMISSION_V1')
        self.assertEqual(st['inventory_risk_policy_version'], 'V5_INVENTORY_RISK_20_80_10_90_V1')
        self.assertEqual(st['inventory_policy_version'], 'V5_PHYSICAL_BOUNDS_OPERATING_WARNINGS_V1')

    def test_non_overwrite_and_roundtrip_verify(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            value = emit(self.src, out, apply=False)
            self.assertEqual({r['status'] for r in value['files']}, {'WOULD_CREATE'})
            self.assertEqual({r['status'] for r in emit(self.src,out,apply=True)['files']}, {'CREATED'})
            self.assertEqual({r['status'] for r in emit(self.src,out,verify=True)['files']}, {'MATCH'})
            (out / POLICY_OUT).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Existing new-version file differs'):
                emit(self.src,out,apply=True)

    def test_source_hash_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)/'config';temp.mkdir()
            for x in (self.src/'config').iterdir():
                (temp/x.name).write_bytes(x.read_bytes())
            p = temp/'v5_balanced_pilotage_context.json'
            p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'Unexpected baseline Git blob'):
                outputs(temp.parent)

    def test_unknown_source_windows_block(self):
        old = json.loads((self.src/'config'/'v5_user_policy_20261112.synthetic.json').read_text())
        p = json.loads((self.src/'config'/'v5_balanced_pilotage_context.json').read_text())
        old['berths']['BERTH-02']['work_windows'][0]['end_at'] = '2026-11-08T00:00:00+09:00'
        with self.assertRaisesRegex(ValueError, 'Unexpected source window'):
            extend_source(old, p)

    def test_65_window_coverage_math(self):
        d = datetime.fromisoformat(START)
        required = d + timedelta(hours=24*(65-1)+72)
        self.assertLessEqual(required, datetime.fromisoformat(EXT_END))

    def test_origin_not_mutated(self):
        old = json.loads((self.src/'config'/'v5_user_policy_20261112.synthetic.json').read_text())
        pilot = json.loads((self.src/'config'/'v5_balanced_pilotage_context.json').read_text())
        before = copy.deepcopy((old,pilot))
        extend_source(old,pilot)
        self.assertEqual((old,pilot),before)


class S20DiagnosticTests(unittest.TestCase):
    def sample(self):
        return {'window_id':3,'as_of_at':'2026-11-05T00:00:00+09:00','validated':False,
                'selected_call_ids':['DEM-0030','DEM-0084'],
                'selected_calls_with_zero_candidates':['DEM-0030','DEM-0084'],
                'domain_counts_by_call':{'DEM-0030':0,'DEM-0084':0},
                'domain_counts_by_call_and_berth':{'DEM-0030':{'BERTH-02':0},'DEM-0084':{'BERTH-03':0}},
                'rejection_records':[{'call_id':'DEM-0030','berth_id':'BERTH-02',
                                      'code':'PHYSICAL_OPTION_REJECTED','reason':'POB earlier than ETA','etb_at':'2026-11-05T00:30:00+09:00'},
                                     {'call_id':'DEM-0084','berth_id':'BERTH-03',
                                      'code':'PHYSICAL_OPTION_REJECTED','reason':'Outside pilotage window','etb_at':'2026-11-05T06:00:00+09:00'}],
                'triage_classification':'SOME_CALLS_HAVE_NO_VALIDATED_PHYSICAL_OPTIONS',
                'rejection_records_truncated':False}

    def test_diagnosis_no_unfounded_infeasibility_claim(self):
        out=diagnose(self.sample(),('DEM-0030','DEM-0084'))
        self.assertFalse(out['physical_infeasibility_proven'])
        self.assertTrue(out['calls']['DEM-0030']['zero_generated_valid_options'])
        self.assertEqual(out['calls']['DEM-0084']['rejection_record_count_in_saved_sample'],1)

    def test_optional_eta_calculation(self):
        snapshot={'calls':[{'call_id':'DEM-0030','eta_at':'2026-11-05T08:00:00+09:00',
                           'eligible_berth_ids':['BERTH-02'],'fixed_etb_is_hard':False}]}
        policy={'berths':{'BERTH-02':{'berthing_seconds':5400}}}
        out=diagnose(self.sample(),('DEM-0030',),snapshot=snapshot,policy=policy)
        self.assertEqual(out['calls']['DEM-0030']['earliest_etb_by_berth']['BERTH-02']['earliest_etb_from_eta_and_berthing_at'],
                         '2026-11-05T09:30:00+09:00')

    def test_bad_format_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unsupported evidence format'):
            diagnose({'validated':False},('DEM-0030',))


if __name__=='__main__':
    unittest.main()
