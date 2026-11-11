"""Bounded, deterministic V5 call/berth ETB candidates and release expansion."""
from collections import defaultdict
from datetime import timedelta
import time
from ..integrated_input.validator import timestamp,tokens
from ..physical_scheduling.phases import replay
from ..physical_scheduling.validator import validate_result,pair_conflicts
from ..physical_scheduling.compatibility import vessel_berth_duration_seconds
from ..physical_scheduling.conflict_index import iter_potential_pairs
from ..physical_scheduling.release_index import ReleaseEventIndex


def _unresolved_0200(etb,seconds):
    pob=etb-timedelta(seconds=seconds)
    for n in (-1,0):
        start=(etb+timedelta(days=n)).replace(hour=2,minute=0,second=0,microsecond=0)
        if pob<start+timedelta(hours=6) and etb>=start:return True
    return False


class CandidatePool:
    def __init__(self,snapshot,calls,eligible,policy,as_of_at,*,commitments=(),pair_limit,total_limit,deadline):
        self.snapshot=snapshot;self.calls=calls;self.eligible=eligible;self.policy=policy
        self.begin=timestamp(as_of_at);self.end=self.begin+timedelta(hours=72);self.deadline=deadline
        self.berths={b['berth_id']:b for b in snapshot['berths']};self.commitments=list(commitments)
        self.pair_limit=pair_limit;self.total_limit=total_limit;self.accepted=defaultdict(dict);self.rejected=[];self.rows={};self.attempts=defaultdict(set);self.expansion_events=[]
    def check(self):
        if time.perf_counter()>=self.deadline:raise TimeoutError('Shared candidate-generation time budget exhausted')
    def count(self):return sum(len(v) for v in self.accepted.values())
    def add(self,cid,bid,at,reason,round_no):
        self.check();at=timestamp(at) if isinstance(at,str) else at;key=(cid,bid);call=self.calls[cid];berth=self.berths[bid]
        if not self.begin<=at<self.end:return False
        if at in self.accepted[key]:self.accepted[key][at]['reasons'].add(reason);return False
        if at in self.attempts[key]:return False
        if len(self.attempts[key])>=self.pair_limit*8:return False
        self.attempts[key].add(at)
        if len(self.accepted[key])>=self.pair_limit or self.count()>=self.total_limit:return False
        cfg=self.policy['berths'][bid]
        if _unresolved_0200(at,cfg['berthing_seconds']):
            self.rejected.append({'call_id':cid,'berth_id':bid,'etb_at':at.isoformat(),'code':'POLICY_UNRESOLVED','reason':'02:00–08:00 berthing restriction unresolved','source':reason,'round':round_no});return False
        try:
            row=replay(call,berth,at.isoformat(),self.policy,restrictions=self.snapshot['workbook_tables']['restrictions']['rows'])
            if timestamp(row['pob_at'])<self.begin:raise ValueError('Pilot boarding before planning boundary')
            checked=validate_result(self.snapshot,[row],self.policy)
            if checked['status']!='VALID':raise ValueError('Independent physical validation: '+str(checked['issues']))
        except (ValueError,KeyError,TypeError) as exc:
            self.rejected.append({'call_id':cid,'berth_id':bid,'etb_at':at.isoformat(),'code':'PHYSICAL_OPTION_REJECTED','reason':str(exc),'source':reason,'round':round_no});return False
        self.accepted[key][at]={'reasons':{reason},'round':round_no};self.rows[(cid,bid,at)]=row;return True
    def initial(self):
        for cid in sorted(self.calls,key=lambda cid:(not self.calls[cid]['fixed_etb_is_hard'],cid)):
            call=self.calls[cid]
            for bid in sorted(self.eligible[cid]):
                self.check();cfg=self.policy['berths'][bid];berth=self.berths[bid]
                maneuver=timedelta(seconds=cfg['berthing_seconds']+cfg.get('pilot_boarding_to_berthing_seconds',0))
                if call['fixed_etb_is_hard']:
                    self.add(cid,bid,call['fix_etb_at'],'HARD_FIX_ETB',0);continue
                earliest=max(self.begin,timestamp(call['eta_at']))+maneuver
                anchors=[(earliest,'ETA_AND_BERTHING_DURATION')]
                for w in ((cfg.get('pilotage_windows_by_call') or {}).get(cid) or {}).get('berthing',cfg.get('pilotage_windows') or []):
                    a=timestamp(w['start_at']);anchors.append((max(self.begin,timestamp(call['eta_at']),a)+maneuver,'PILOTAGE_WINDOW_START'))
                    if w.get('latest_start_at'):anchors.append((timestamp(w['latest_start_at'])+maneuver,'LAST_POB_BOUNDARY'))
                for w in cfg['work_windows']:
                    anchors.append((timestamp(w['start_at'])-timedelta(seconds=cfg['hose_connection_seconds']),'WORK_WINDOW_START'))
                    anchors.append((timestamp(w['end_at'])-timedelta(seconds=cfg['hose_connection_seconds']+vessel_berth_duration_seconds(call,berth)),'WORK_WINDOW_END'))
                for block in self.snapshot['workbook_tables']['restrictions']['rows']:
                    if block['berth_id']==bid and block.get('cargo_group') in (None,'',call['cargo_group']):
                        anchors.append((timestamp(block['end_local'])+maneuver,'BERTH_RESTRICTION_END'))
                        anchors.append((timestamp(block['start_local'])-timedelta(seconds=cfg['hose_connection_seconds']+vessel_berth_duration_seconds(call,berth)),'BERTH_RESTRICTION_START'))
                for r in self.commitments:
                    if r['berth_id']==bid:
                        anchors.append((timestamp(r['physical_clearance_at'])+maneuver,'PROTECTED_BERTH_CLEARANCE'))
                        anchors.append((timestamp(r['switching_end_at']),'PROTECTED_SWITCH_RELEASE'))
                # Keep event seconds exactly; a single grid point supplies exploration.
                grid=earliest.replace(minute=0,second=0,microsecond=0)
                while grid<earliest:grid+=timedelta(minutes=30)
                anchors.append((grid,'EXPLORATION_GRID_30M'))
                for at,why in sorted(anchors,key=lambda x:(x[0],x[1])):self.add(cid,bid,at,why,0)
    def affected_calls(self):
        rows=list(self.rows.values());affected={cid for cid in self.calls if not any(self.accepted[(cid,bid)] for bid in self.eligible[cid])}
        for i,j in iter_potential_pairs(self.snapshot,self.policy,rows,check_deadline=self.check):
            if pair_conflicts(rows[i],rows[j],self.snapshot,self.policy):affected.update((rows[i]['call_id'],rows[j]['call_id']))
        # A non-pairwise resource constraint may be responsible.
        return affected or set(self.calls)
    def expand(self,round_no,targets=None):
        self.check();before=self.count();rows=list(self.rows.values());index=ReleaseEventIndex(rows,self.commitments,self.policy,'ALL_OPTIONS_V1')
        groups=self.snapshot['workbook_tables']['groups']['rows']
        for cid in sorted(self.calls):
            if targets is not None and cid not in targets:continue
            call=self.calls[cid]
            if call['fixed_etb_is_hard']:continue
            for bid in sorted(self.eligible[cid]):
                self.check();cfg=self.policy['berths'][bid];maneuver=timedelta(seconds=cfg['berthing_seconds']+cfg.get('pilot_boarding_to_berthing_seconds',0))
                earliest=max(self.begin,timestamp(call['eta_at']))+maneuver;anchors=[]
                for row in rows+self.commitments:
                    if row['call_id']==cid:continue
                    if row['berth_id']==bid:
                        anchors.append((timestamp(row['physical_clearance_at'])+maneuver,'OTHER_BERTH_CLEARANCE:'+row['call_id']))
                    for group in groups:
                        members=tokens(group.get('berth_ids'))
                        if group.get('rule_type')=='SAME_CARGO_NOT_CONCURRENT' and bid in members and row['berth_id'] in members and row['berth_id']!=bid and row.get('cargo_group')==call.get('cargo_group'):
                            anchors.append((timestamp(row['etu_at']),'OTHER_GROUP_CLEARANCE:'+row['call_id']))
                dummy={'berth_id':bid,'etb_at':earliest.isoformat(),'call_id':cid}
                for at in index.later_releases(dummy):anchors.append((timestamp(at),'INDEXED_SWITCH_RELEASE'))
                for rejected in self.rejected:
                    if rejected.get('call_id')==cid and rejected.get('berth_id')==bid and rejected.get('round')==round_no-1:
                        rejected_at=timestamp(rejected['etb_at']);grid=rejected_at.replace(minute=0,second=0,microsecond=0)
                        while grid<=rejected_at:grid+=timedelta(minutes=30)
                        anchors.append((grid,'REJECTED_OPTION_NEXT_GRID:'+rejected['code']))
                base=earliest.replace(minute=0,second=0,microsecond=0)
                while base<earliest:base+=timedelta(minutes=30)
                anchors.append((base+timedelta(minutes=30*round_no),'EXPLORATION_GRID_30M'))
                for at,why in sorted(anchors,key=lambda x:(x[0],x[1])):
                    if at>=earliest:self.add(cid,bid,at,why,round_no)
        self.expansion_events.append({'round':round_no,'reason':'CANDIDATE_SHORTAGE_OR_FINITE_CONFLICT','affected_call_ids':sorted(targets or self.calls),'added':self.count()-before})
        return self.count()-before
    def expand_cost(self,round_no,schedule,priced):
        """Add validated local and release anchors; retain every accepted candidate."""
        before=self.count();by_id={r['call_id']:r for r in schedule}
        locked={r['call_id'] for r in self.commitments}
        order=sorted(self.calls,key=lambda cid:(-priced.get(cid,0),cid))
        for cid in order:
            if cid in locked or self.calls[cid].get('fixed_etb_is_hard'):continue
            selected=by_id.get(cid)
            for bid in sorted(self.eligible[cid]):
                cfg=self.policy['berths'][bid];maneuver=timedelta(seconds=cfg['berthing_seconds']+cfg.get('pilot_boarding_to_berthing_seconds',0))
                anchors=[]
                if selected:
                    for offset in (-30,30):anchors.append((timestamp(selected['etb_at'])+timedelta(minutes=offset),f'INCUMBENT_OFFSET_{offset:+d}M'))
                for other in schedule:
                    if other['call_id']!=cid and other['berth_id']==bid:
                        anchors.append((timestamp(other['physical_clearance_at'])+maneuver,'INCUMBENT_BERTH_RELEASE:'+other['call_id']))
                for at,reason in sorted(anchors,key=lambda x:(x[0],x[1])):self.add(cid,bid,at,reason,round_no)
        self.expansion_events.append({'round':round_no,'reason':'COST_AWARE_INCUMBENT_AND_RELEASE','affected_call_ids':order,'added':self.count()-before})
        return self.count()-before
    def domain(self):
        return {cid:{bid:[a.isoformat() for a in sorted(self.accepted[(cid,bid)])] for bid in sorted(self.eligible[cid])} for cid in sorted(self.calls)}
    def reasons(self):
        return {cid:{bid:[{'etb_at':a.isoformat(),'reasons':sorted(v['reasons']),'round':v['round']} for a,v in sorted(self.accepted[(cid,bid)].items())] for bid in sorted(self.eligible[cid])} for cid in sorted(self.calls)}
