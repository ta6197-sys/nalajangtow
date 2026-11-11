import copy
import unittest
from fractions import Fraction
from datetime import timedelta
from tests.test_v5_cost_aware import make_case
from tests.test_v5_physical_integration import START
from src.daily_planning.integrated_input.validator import timestamp
from src.daily_planning.integrated_input.provenance import canonical,digest
from src.daily_planning.physical_scheduling.phases import replay
from src.daily_planning.integrated_v3.v5_inventory_events import prepare_inventory_events
from src.daily_planning.integrated_v3.coordinator import validate_v5_candidate

END='2026-11-04T00:00:00+09:00'

class InventoryEventsTest(unittest.TestCase):
    def setUp(self):
        self.s,self.p,self.times=make_case();self.s['calls'][1]['direction']='LOAD'
        self.rows=[replay(c,self.s['berths'][i],self.times[c['call_id']][0],self.p) for i,c in enumerate(self.s['calls'])]
    def prepare(self,rows=None,**kw):return prepare_inventory_events(self.s,self.rows if rows is None else rows,start_at=START,end_at=END,**kw)
    def test_exact_replay_and_opposite_gross_rates(self):
        data=self.prepare();tank=data['tanks']['T'];replay_result=validate_v5_candidate(self.s,self.rows,as_of_at=START,projection_end_at=END)
        self.assertEqual(replay_result['status'],'INVENTORY_VALID',replay_result)
        observed={timestamp(p['at']):Fraction(p['inventory_bbl']) for p in replay_result['tank_result']['inventories']['T']}
        for i,t in enumerate(tank['events']):
            expected=Fraction(500)+tank['external_cumulative_bbl'][i]+sum((p['signed_cumulative_bbl'][i] for p in data['options']),Fraction(0))
            self.assertEqual(expected,observed[t])
        self.assertTrue(any(r>0 for r in data['options'][0]['receipt_rates_bbl_per_hour']))
        self.assertTrue(any(r>0 for r in data['options'][1]['dispatch_rates_bbl_per_hour']))
        self.assertEqual([p['quantity_bbl'] for p in data['options']],[Fraction(100),Fraction(100)])
        self.assertEqual(tank['events'],sorted(set(tank['events'])))
    def test_multiple_segments_fraction_and_all_option_events(self):
        c=self.s['calls'][0];c['cargo_quantity_mt']='1/7';c['cargo_volume_bbl']='10/7'
        row=copy.deepcopy(self.rows[0]);a=timestamp(row['work_start_at']);row['work_intervals']=[[a.isoformat(),(a+timedelta(seconds=25)).isoformat()],[(a+timedelta(seconds=120)).isoformat(),(a+timedelta(seconds=147)).isoformat()]]
        row['work_end_at']=row['work_intervals'][-1][1]
        other=copy.deepcopy(row);other['work_intervals']=[[(a+timedelta(seconds=300)).isoformat(),(a+timedelta(seconds=352)).isoformat()]];other['work_end_at']=other['work_intervals'][-1][1]
        data=self.prepare([row,other]);self.assertEqual(data['options'][0]['quantity_bbl'],Fraction(10,7));self.assertEqual(data['options'][0]['segments'][-1]['rate_bbl_per_hour'],'300/7')
        self.assertIn(a+timedelta(seconds=300),data['tanks']['T']['events'])
        validated=validate_v5_candidate(self.s,[row],as_of_at=START,projection_end_at=END);self.assertEqual(validated['status'],'INVENTORY_VALID',validated)
        observed={timestamp(p['at']):Fraction(p['inventory_bbl']) for p in validated['tank_result']['inventories']['T']}
        for i,t in enumerate(data['tanks']['T']['events']):
            if t in observed:self.assertEqual(observed[t],Fraction(500)+data['options'][0]['signed_cumulative_bbl'][i])
    def test_flow_changes_and_missing_coverage(self):
        f=self.s['fixed_process_flows']['segments'][0];f['end_at']='2026-11-03T12:00:00+09:00';g=copy.deepcopy(f);g.update(start_at=f['end_at'],end_at=END,fixed_net_flow_bbl_per_hour='-3/2');self.s['fixed_process_flows']['segments'].append(g)
        data=self.prepare();self.assertEqual(data['tanks']['T']['external_cumulative_bbl'][-1],Fraction(-18));g['start_at']='2026-11-03T13:00:00+09:00'
        with self.assertRaisesRegex(ValueError,'gap'):self.prepare()
    def test_carry_and_commitment(self):
        data=self.prepare([self.rows[0]]);p=data['options'][0];transfer=p['transfer'];start=timestamp(transfer['start_at'])+timedelta(minutes=30)
        carry={'call_id':p['call_id'],'berth_id':p['berth_id'],'tank_id':'T','at':start.isoformat(),'remaining_bbl':'50','transfer_fingerprint':digest(canonical(transfer).encode())}
        d=prepare_inventory_events(self.s,[self.rows[0]],start_at=start.isoformat(),end_at=END,carried_transfers=[carry],commitments=[transfer]);self.assertEqual(d['options'][0]['signed_cumulative_bbl'][-1],Fraction(50))
        carry['remaining_bbl']='51'
        with self.assertRaisesRegex(ValueError,'carried'):prepare_inventory_events(self.s,[self.rows[0]],start_at=start.isoformat(),end_at=END,carried_transfers=[carry])
    def test_completed_physical_commitment_has_no_cargo(self):
        begin='2026-11-03T05:00:00+09:00'
        result=prepare_inventory_events(self.s,[self.rows[0]],start_at=begin,end_at=END,physical_commitments=[self.rows[0]])
        self.assertTrue(result['options'][0]['completed']);self.assertEqual(result['options'][0]['signed_cumulative_bbl'][-1],0)
        with self.assertRaisesRegex(ValueError,'protected'):prepare_inventory_events(self.s,[self.rows[0]],start_at=begin,end_at=END)
