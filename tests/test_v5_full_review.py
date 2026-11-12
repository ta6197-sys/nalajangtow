"""Pure checkpoint reader tests; no OR-Tools or DB needed."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.daily_planning.integrated_v3.v5_run_review import (
    InvalidPreviewRun, fingerprint, list_runs, load_checkpoints, resolve_run,
    run_summary, window_view)


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / 'v5-example1'
        self.run.mkdir()
        self.start = datetime(2026, 11, 3, tzinfo=timezone(timedelta(hours=9)))
        self.config = {'start_at': self.start.isoformat(), 'windows': 2,
                       'rolling_optimizer_mode': 'INTEGRATED_COST_AWARE_V1',
                       'objective_mode': 'MIN_TOTAL_DEMURRAGE',
                       'inventory_constraint_mode': 'EXACT_EVENT_BALANCE_V1',
                       'scope': ['SYN-1'], 'source_fingerprint': 'source-a'}
        self.manifest = {'run_fingerprint': fingerprint(self.config), 'config': self.config}
        self.write('manifest.json', self.manifest)
        self.write('execution_report.json', {'run_id':'v5-example1',
              'status':'REQUESTED_WINDOWS_VALIDATED','source_fingerprint':'source-a',
              'requested_scope_count':1,'validated_window_count':2,'cumulative_demurrage_cents': 100,
              'all_scope_calls_completed': True})
        self.write('tanks_public.json', [{'tank_id':'T','cargo_group':'CRUDE','min_inventory_bbl':'0','capacity_bbl':'1000'}])
        self.write('calls_public.json', [{'call_id':'SYN-1','eta_at':self.start.isoformat()}])
        state = {'inventory': {'T':'500'},'cargo_progress': {'SYN-1':{'completed_bbl':'10','remaining_bbl':'0'}}}
        self.first = self.cp(1, state, 'prior-first')
        self.second = self.cp(2, state, self.first['next_state_fingerprint'])
        self.write('window_001.json', self.first)
        self.write('window_002.json', self.second)

    def write(self, name, obj):
        (self.run / name).write_text(json.dumps(obj), encoding='utf-8')

    def cp(self, index, state, prior):
        at = self.start + timedelta(hours=24*(index-1))
        row = {'call_id':'SYN-1','berth_id':'B1', 'etb_at':at.isoformat(),
               'berthing_start_at':at.isoformat(),
               'physical_clearance_at': (at+timedelta(hours=3)).isoformat()}
        candidate = {'status':'VALIDATED','total_demurrage_cents':100,
                     'vessel_demurrage_breakdown':{'SYN-1':{'status':'SETTLED','demurrage_cents':100}},
                     'tank_result': {'inventories': {'T':[{'at':at.isoformat(),'inventory_bbl':'500'}]}},
                     'optimizer_result': {'total_projected_demurrage_cents':100,
                                          'optimization_status':'OPTIMAL',
                                          'final_candidate_domain':{'optimality_proven':True},
                                          'cost_improvement_history':[]}}
        cp = {'window_id':index, 'as_of_at':at.isoformat(),
              'planning_horizon_end':(at+timedelta(hours=72)).isoformat(),
              'run_fingerprint':self.manifest['run_fingerprint'],
              'optimization_configuration':self.config,
              'prior_state_fingerprint':prior,
              'next_state_fingerprint': fingerprint(state),
              'next_state':copy.deepcopy(state),
              'carried_inventory':copy.deepcopy(state['inventory']),
              'cargo_progress':copy.deepcopy(state['cargo_progress']),
              'selected_schedule':[row],
              'validation_results': {'physical':'VALID','inventory':'VALID','settlement':'SETTLED','carryover':'VALID'},
              'summary': {'window_id':index,'as_of_at':at.isoformat(),'window_demurrage_cents':100,
                          'selected_call_ids':['SYN-1'],'final_domain_optimality_proven':True,'solver_status':'OPTIMAL'},
              'candidate':candidate}
        cp['checkpoint_fingerprint'] = fingerprint(cp)
        return cp

    def test_read_verified_run_and_window(self):
        r = run_summary(self.root, 'v5-example1')
        self.assertEqual(r['validated_window_count'],2)
        w = window_view(self.root,'v5-example1',1)
        self.assertEqual(w['tank_inventory']['T'][0]['inventory_bbl'],'500')
        self.assertTrue(w['final_candidate_domain']['optimality_proven'])

    def test_listing(self):
        self.assertEqual(len(list_runs(self.root)['runs']),1)

    def test_path_traversal_rejected(self):
        for name in ('../secret', '../v5-example1', '.hidden', 'v5/evil'):
            with self.subTest(name=name), self.assertRaises(InvalidPreviewRun):
                resolve_run(self.root, name)

    def test_tampered_checkpoint_rejected(self):
        d = copy.deepcopy(self.first)
        d['carried_inventory']['T']='0'
        self.write('window_001.json',d)
        with self.assertRaisesRegex(InvalidPreviewRun,'fingerprint'):
            load_checkpoints(self.run)

    def test_continuity_rejected_even_with_recomputed_checksum(self):
        d = copy.deepcopy(self.second)
        d['prior_state_fingerprint']='not-the-prior-state'
        d['checkpoint_fingerprint']=fingerprint({k:v for k,v in d.items() if k!='checkpoint_fingerprint'})
        self.write('window_002.json',d)
        with self.assertRaisesRegex(InvalidPreviewRun,'continuity'):
            load_checkpoints(self.run)

    def test_fake_optimality_rejected(self):
        d=copy.deepcopy(self.first)
        d['candidate']['optimizer_result']['final_candidate_domain']['optimality_proven']=False
        d['checkpoint_fingerprint']=fingerprint({k:v for k,v in d.items() if k!='checkpoint_fingerprint'})
        self.write('window_001.json',d)
        with self.assertRaisesRegex(InvalidPreviewRun,'optimality'):
            load_checkpoints(self.run)

    def test_inconsistent_cost_rejected(self):
        d=copy.deepcopy(self.first)
        d['summary']['window_demurrage_cents']=200
        d['checkpoint_fingerprint']=fingerprint({k:v for k,v in d.items() if k!='checkpoint_fingerprint'})
        self.write('window_001.json',d)
        with self.assertRaisesRegex(InvalidPreviewRun,'cost'):
            load_checkpoints(self.run)

    def test_unvalidated_schedule_rejected(self):
        d=copy.deepcopy(self.first)
        d['validation_results']['inventory']='INVALID'
        d['checkpoint_fingerprint']=fingerprint({k:v for k,v in d.items() if k!='checkpoint_fingerprint'})
        self.write('window_001.json',d)
        with self.assertRaisesRegex(InvalidPreviewRun,'Unvalidated'):
            load_checkpoints(self.run)

    def test_non_integrated_manifest_rejected(self):
        config=copy.deepcopy(self.config)
        config['rolling_optimizer_mode']='LEGACY_ENUMERATION'
        self.write('manifest.json',{'run_fingerprint':fingerprint(config),'config':config})
        with self.assertRaisesRegex(InvalidPreviewRun,'integrated'):
            load_checkpoints(self.run)

    def test_symlinked_run_rejected(self):
        link=self.root/'v5-symlink'
        try: link.symlink_to(self.run, target_is_directory=True)
        except (OSError,NotImplementedError): self.skipTest('No symlinks')
        with self.assertRaises(InvalidPreviewRun):
            resolve_run(self.root,'v5-symlink')


if __name__ == '__main__': unittest.main()
