import unittest
from datetime import datetime, timedelta, timezone
from src.daily_planning.integrated_v3.v5_completion_evidence import audit_s5_completion

T=datetime(2026,11,12,9,tzinfo=timezone(timedelta(hours=9)))
def dt(i): return (T+timedelta(days=i)).isoformat()

def fixture(done=5,cents=150,windows=3):
    ids=[f'C{i}' for i in range(5)]; completed=ids[:done]
    source={cid:{'cargo_volume_bbl':'10.25'} for cid in ids}
    state={'at':dt(windows),'completed_rows':[{'call_id':c,'etu_at':dt(1),'work_end_at':dt(0)} for c in completed],
      'contract_accounting_state':{cid:{'demurrage_cents':30,'settlement':{'status':'SETTLED'}} for cid in completed},
      'cargo_progress':{cid:{'completed_bbl':'10.25','remaining_bbl':'0'} for cid in completed},
      'in_progress_contract_accounting':{},'previous_schedule':[],'carried_transfers':[]}
    report={'scope_call_ids':ids,'status':'REQUESTED_WINDOWS_VALIDATED','requested_windows':windows,'validated_window_count':windows,
      'source_fingerprint':'src','completed_scope_count':done,'all_scope_calls_completed':done==5,
      'cumulative_demurrage_cents':cents,'unresolved_accounting_call_ids':[],
      'simulation_only':True,'operational_approval':False}
    manifest={'config':{'scope':sorted(ids),'source_fingerprint':'src','policy_fingerprint':'pol','start_at':dt(0),
       'windows':windows,'rolling_optimizer_mode':'INTEGRATED_COST_AWARE_V1',
       'objective_mode':'MIN_TOTAL_DEMURRAGE','inventory_constraint_mode':'EXACT_EVENT_BALANCE_V1',
       'tie_break_mode':'CANONICAL_ETB_BERTH_V1'}}
    cps=[{'window_id':i} for i in range(1,windows)] + [{'window_id':windows,'next_state':state,'completed_calls':completed}]
    return report,manifest,cps,source

def run(f,replay='PASS'):
    r,m,c,s=f
    return audit_s5_completion(r,m,c,source_calls=s,replay_status=replay,
      expected_source_fingerprint='src',expected_policy_fingerprint='pol',
      expected_start_at=dt(0),expected_windows=3,expected_tie_break_mode='CANONICAL_ETB_BERTH_V1')

def codes(result):return [x['code'] for x in result['issues']]

class TestCompletion(unittest.TestCase):
    def test_complete(self):
        r=run(fixture());self.assertEqual(r['status'],'PASS_S5_ALL_FIVE_COMPLETED_SETTLED_AND_REPLAYED');self.assertEqual(r['settled_cents_sum'],150)
    def test_partial_scope(self):
        r=run(fixture(done=2,cents=None));self.assertEqual(r['status'],'VALIDATED_WINDOWS_SCOPE_INCOMPLETE');self.assertEqual(len(r['remaining_call_ids']),3)
    def test_null_cumulative(self):self.assertIn('CUMULATIVE_COST_UNRESOLVED',codes(run(fixture(cents=None))))
    def test_mismatched_cents(self):self.assertIn('CUMULATIVE_COST_MISMATCH',codes(run(fixture(cents=151))))
    def test_replay_missing(self):self.assertIn('INDEPENDENT_REPLAY_NOT_PASSED',codes(run(fixture(),replay='NOT_RUN')))
    def test_wrong_source(self):
        f=list(fixture());f[0]['source_fingerprint']='other';self.assertIn('SOURCE_FINGERPRINT_MISMATCH',codes(run(f)))
    def test_wrong_policy(self):
        f=list(fixture());f[1]['config']['policy_fingerprint']='other';self.assertIn('POLICY_FINGERPRINT_MISMATCH',codes(run(f)))
    def test_cargo_imbalance(self):
        f=list(fixture());f[2][-1]['next_state']['cargo_progress']['C0']['remaining_bbl']='0.01';self.assertIn('CARGO_NOT_FULLY_RECONCILED',codes(run(f)))
    def test_carried_transfer_left(self):
        f=list(fixture());f[2][-1]['next_state']['carried_transfers']=[{'call_id':'C0'}];self.assertIn('CARRIED_TRANSFERS_REMAIN',codes(run(f)))
    def test_incomplete_window_chain(self):
        f=list(fixture());f[0]['status']='STOPPED_UNRESOLVED';self.assertIn('INCOMPLETE_OR_UNVALIDATED_WINDOW_CHAIN',codes(run(f)))
    def test_invalid_etu(self):
        f=list(fixture());f[2][-1]['next_state']['completed_rows'][0]['etu_at']=dt(4);self.assertIn('ETU_AFTER_COMMITTED_STATE',codes(run(f)))
    def test_missing_accounting(self):
        f=list(fixture());del f[2][-1]['next_state']['contract_accounting_state']['C0'];self.assertIn('MISSING_FINAL_SETTLEMENT',codes(run(f)))
    def test_wrong_reported_completion(self):
        f=list(fixture());f[0]['all_scope_calls_completed']=False;self.assertIn('REPORTED_COMPLETION_FLAG_MISMATCH',codes(run(f)))
    def test_non_integer_money(self):
        f=list(fixture());f[0]['cumulative_demurrage_cents']=True;self.assertIn('CUMULATIVE_COST_UNRESOLVED',codes(run(f)))
    def test_no_double_count(self):self.assertNotIn('sum_of_window_costs',run(fixture()))

if __name__=='__main__':unittest.main()
