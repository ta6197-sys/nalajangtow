#!/usr/bin/env python3
"""CP-SAT finite start-candidate berth allocation / demurrage experiment.

Requires OR-Tools (pip install ortools) to solve. The finite start candidate
set matches the previous MILP experiment; optimality, if achieved, applies
ONLY to this candidate-restricted model with the configured equivalent virtual berths.
No claim of unrestricted/global real-world optimality is made.
"""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,math,sys,time,threading
from collections import defaultdict
from datetime import datetime,timedelta
from pathlib import Path
from typing import Any
from weather_optimizer import WeatherSeries,WeatherRule,load_model,simulate_work,price_contract,_validate_schedule
from contracts import RULE_VERSION

ROOT=Path(__file__).resolve().parent
MODEL=ROOT/'input/model_input_140.json'
WEATHER=ROOT/'input/weather_for_optimizer_30min.csv'
CANDIDATES=ROOT/'input/candidate_options.json.gz'
REF=ROOT/'reference/current_baselines.json'
OUT=ROOT/'results'

NOMINAL=WeatherRule(4.8,1.0,'유의파고(m)','WORK_ONLY','ERROR')
STRESS=WeatherRule(3.6,0.8,'유의파고(m)','WORK_ONLY','ERROR')

def dt(x):return datetime.fromisoformat(x)

def load_inputs():
    with gzip.open(CANDIDATES,'rt',encoding='utf-8') as f: package=json.load(f)
    assert hashlib.sha256(MODEL.read_bytes()).hexdigest()==package['model_sha256'],'model input changed'
    assert hashlib.sha256(WEATHER.read_bytes()).hexdigest()==package['weather_sha256'],'weather input changed'
    assert package['contract_rule_version']==RULE_VERSION,'contract rule version changed; rebuild candidates'
    for name,digest in package['pricing_code_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,f'pricing code changed: {name}; rebuild candidates'
    calls,berths=load_model(MODEL)
    weather=WeatherSeries.from_csv(WEATHER)
    options=package['options']; groups=package['option_lists']
    assert len(calls)==len(groups)==len(package['calls'])
    assert package['berths']==berths,'candidate/model berth lists differ'
    assert len(options)>len(calls)
    assert all(package['calls'][i]==c['call_id'] for i,c in enumerate(calls))
    assert sum(len(g) for g in groups)==len(options)
    for i,g in enumerate(groups):
        assert g,('missing options',i)
        for j in g:
            z=options[j]
            assert z['call_idx']==i and z['call_id']==calls[i]['call_id']
            assert dt(z['start'])>=dt(calls[i]['available_at'])
            assert dt(z['end'])>dt(z['start'])
            assert isinstance(z['cents'],int) and z['cents']>=0
            assert z['contract_cost']['demurrage_cents']==z['cents']
            assert z['cents']-z['despatch_cents']==z['net_owner_receivable_cents']
    return package,calls,berths,weather

def overlap_capacity(selected, options, capacity):
    """Exact half-open interval capacity checker; avoids 30-minute rounding."""
    events=[]
    for j in selected.values():
        z=options[j];events.extend(((dt(z['start']),+1),(dt(z['end']),-1)))
    # At coincident endpoints process departure before arrival.
    events.sort(key=lambda pair:(pair[0],pair[1]))
    occupancy=peak=0
    for t,d in events:
        occupancy+=d;peak=max(peak,occupancy)
        if occupancy>capacity:raise AssertionError(f'capacity exceeded at {t}: {occupancy}')
        if occupancy<0:raise AssertionError('invalid interval ordering')
    assert occupancy==0
    return peak

def verify_selection(selected,options,groups,capacity):
    assert len(selected)==len(groups)
    for i,g in enumerate(groups):assert selected[i] in g,(i,selected[i])
    peak=overlap_capacity(selected,options,capacity)
    return {'peak_berths_used':peak,'cost_cents':sum(options[j]['cents'] for j in selected.values()),
            'despatch_cents':sum(options[j].get('despatch_cents',0) for j in selected.values()),
            'weather_pause_minutes':sum(options[j]['downtime_min'] for j in selected.values())}

def reference_selection(options,groups,calls,capacity):
    prior=json.loads(REF.read_text(encoding='utf-8'))
    assert prior['model_sha256']==hashlib.sha256(MODEL.read_bytes()).hexdigest(),'initial hint model is stale'
    assert prior['weather_sha256']==hashlib.sha256(WEATHER.read_bytes()).hexdigest(),'initial hint weather is stale'
    assert prior['berth_count']==capacity and prior['call_count']==len(calls)
    indices_by_time={(z['call_id'],z['start'],z['end']):j for j,z in enumerate(options)}
    chosen={}
    for rr in prior['schedule']:
        i=next(i for i,c in enumerate(calls) if c['call_id']==rr['call_id'])
        key=(rr['call_id'],rr['work_start_at'],rr['completion_at'])
        if key not in indices_by_time:
            raise AssertionError(f'Cannot use baseline hint: schedule option missing: {key}')
        chosen[i]=indices_by_time[key]
    stats=verify_selection(chosen,options,groups,capacity)
    assert stats['cost_cents']==prior['summary']['base_cents'],('reference cost mismatch',stats)
    return chosen,stats

def decode_solution(chosen,options,calls,berths,weather):
    alloc=[]
    occupied_until=[min(dt(z['start']) for z in options)]*len(berths)
    for i,j in sorted(chosen.items(),key=lambda pair:(options[pair[1]]['start'],pair[0])):
        z=options[j];st=dt(z['start']);en=dt(z['end'])
        free=[b for b,t in enumerate(occupied_until) if t<=st]
        if not free:raise AssertionError(f'{len(berths)}-berth capacity violation in decoded solution')
        b=min(free,key=lambda b:(occupied_until[b],b));occupied_until[b]=en
        c=calls[i]
        # These are not floating point estimates; schedule is revalidated after decoding.
        alloc.append({'call_id':c['call_id'],'berth_id':berths[b],
            'nor_at':c['available_at'],'work_start_at':z['start'],'completion_at':z['end'],
            'weather_pause_minutes':z['downtime_min'],'weather_pause_intervals':z['pauses'],
            'clock_start_at':z['clock_start'],'demurrage_minutes':z['demurrage_minutes'],
            'demurrage_cents':z['cents'],
            'despatch_cents':z.get('despatch_cents'),
            'net_owner_receivable_cents':z.get('net_owner_receivable_cents'),
            'contract_cost':z.get('contract_cost')})
    _validate_schedule(alloc,calls)
    return alloc

def replay(schedule,calls,berths,weather,rule,delay=False):
    """Resimulate locked berth order under an independent weather / readiness scenario.
    The planned work start remains a lower bound, i.e. no opportunistic reoptimization.
    """
    byid={c['call_id']:c for c in calls}
    per=defaultdict(list)
    for row in schedule:per[row['berth_id']].append(row)
    rows=[]
    for b in berths:
        free=weather.first
        for row in sorted(per[b],key=lambda r:(r['work_start_at'],r['call_id'])):
            c=byid[row['call_id']]
            start=max(dt(row['work_start_at']),free)
            if delay and int(c['call_id'].split('-')[-1])%10==0:
                start=max(start,dt(c['available_at'])+timedelta(hours=6))
            start,end,pauses=simulate_work(start,int(c['processing_minutes']),weather,rule)
            price=price_contract(c['contract'],start,end,pauses,weather,rule)
            rows.append({'call_id':c['call_id'],'berth_id':b,'nor_at':c['available_at'],
                'work_start_at':start.isoformat(),'completion_at':end.isoformat(),
                'demurrage_cents':int(price['demurrage_cents']),
                'despatch_cents':int(price['despatch_cents']),
                'net_owner_receivable_cents':int(price['net_owner_receivable_cents']),
                'weather_pause_minutes':sum(int((e-s).total_seconds()/60) for s,e in pauses)})
            free=end
    _validate_schedule(rows,calls)
    return {'cost_cents':sum(x['demurrage_cents'] for x in rows),
            'despatch_cents':sum(x['despatch_cents'] for x in rows),
            'net_owner_receivable_cents':sum(x['net_owner_receivable_cents'] for x in rows),
            'total_weather_pause_minutes':sum(x['weather_pause_minutes'] for x in rows),
            'end':max(r['completion_at'] for r in rows)}

def make_ortools_model(options,groups,capacity,objective='cost',cap_cents=None,previous=None,hint=None,fixed_intervals=None):
    """Create complete restricted-candidate CP-SAT with optional intervals and AddCumulative."""
    from ortools.sat.python import cp_model
    model=cp_model.CpModel()
    base=min(dt(z['start']) for z in options)
    x=[model.NewBoolVar(f'opt_{j}') for j in range(len(options))]
    intervals=[]
    for j,z in enumerate(options):
        st=int((dt(z['start'])-base).total_seconds()/60)
        length=int((dt(z['end'])-dt(z['start'])).total_seconds()/60)
        assert length>0
        intervals.append(model.NewOptionalIntervalVar(st,length,st+length,x[j],f'job_{j}'))
    for indices in groups:model.AddExactlyOne(x[j] for j in indices)
    for n,(start,end) in enumerate(fixed_intervals or []):
        st=int((dt(start)-base).total_seconds()/60)
        length=int((dt(end)-dt(start)).total_seconds()/60)
        if length<=0:raise ValueError('Fixed berth occupancy must have positive duration')
        intervals.append(model.NewIntervalVar(st,length,st+length,f'ongoing_{n}'))
    model.AddCumulative(intervals,[1]*len(intervals),capacity)
    if cap_cents is not None:
        model.Add(sum(int(z['cents'])*x[j] for j,z in enumerate(options)) <= int(cap_cents))
    if previous:
        # A candidate is materially different if the selected start is >= 3 hours away.
        for idx,old in enumerate(previous):
            qualifying=[]
            for i,old_j in old.items():
                anchor=dt(options[old_j]['start'])
                qualifying.extend(x[j] for j in groups[i] if abs((dt(options[j]['start'])-anchor).total_seconds())>=3*3600)
            model.Add(sum(qualifying)>=min(5,len(groups)))
    if objective=='cost':coef=[int(z['cents']) for z in options]
    elif objective=='wait':coef=[int(z['delay_min']) for z in options]
    elif objective=='downtime':coef=[int(z['downtime_min']) for z in options]
    elif objective=='stress':
        if any('stress_cents' not in z for z in options):raise ValueError('stress_cents have not been precomputed')
        coef=[int(z['stress_cents']) for z in options]
    else:raise ValueError(f'Invalid objective {objective}')
    model.Minimize(sum(coef[j]*x[j] for j in range(len(options))))
    if hint:
        for i,g in enumerate(groups):
            for j in g:model.AddHint(x[j],int(j==hint[i]))
    return model,x

def solve_plan(name,options,groups,calls,berths,weather,hint,objective,time_limit,workers,cap_cents=None,previous=None,random_seed=42):
    from ortools.sat.python import cp_model
    model,x=make_ortools_model(options,groups,len(berths),objective,cap_cents,previous,hint)
    solver=cp_model.CpSolver()
    solver.parameters.max_time_in_seconds=float(time_limit)
    solver.parameters.num_search_workers=int(workers)
    solver.parameters.random_seed=int(random_seed)
    solver.parameters.relative_gap_limit=0.0
    error=model.validate()
    if error:raise ValueError(f'Invalid CP-SAT model: {error}')
    class Progress(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            super().__init__();self.value=None;self.bound=None
        def on_solution_callback(self):
            self.value=float(self.ObjectiveValue());self.bound=float(self.BestObjectiveBound())
        def emit(self):
            gap=None if self.value is None or self.bound is None else max(0.0,(self.value-self.bound)/max(1.0,abs(self.value)))
            print('PROGRESS',json.dumps({'plan':name,'elapsed_seconds':round(time.monotonic()-t0,1),'objective':self.value,'bound':self.bound,'relative_gap':gap}),flush=True)
    progress=Progress()
    solver.best_bound_callback=lambda bound:setattr(progress,'bound',float(bound))
    finished=threading.Event()
    t0=time.monotonic()
    def heartbeat():
        while not finished.wait(30):progress.emit()
    reporter=threading.Thread(target=heartbeat,daemon=True);reporter.start()
    print('SOLVE_START',json.dumps({'name':name,'calls':len(calls),'berths':len(berths),'options':len(options),'time_limit_seconds':time_limit,'objective':objective}),flush=True)
    try:status=solver.Solve(model,progress)
    finally:
        finished.set();reporter.join(timeout=1)
    duration=time.monotonic()-t0
    status_label=solver.StatusName(status)
    result={'name':name,'objective':objective,'solver_status':status_label,'seconds':round(duration,2),
            'scope':f'optimality applies only to the {len(options)} finite start-time candidates with {len(berths)} identical virtual berths',
            'call_count':len(calls),'berth_count':len(berths),'option_count':len(options)}
    if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):return result,None
    selected={i:next(j for j in g if solver.Value(x[j])==1) for i,g in enumerate(groups)}
    v=verify_selection(selected,options,groups,len(berths))
    schedule=decode_solution(selected,options,calls,berths,weather)
    base=replay(schedule,calls,berths,weather,NOMINAL)
    if base['cost_cents']!=v['cost_cents']:raise AssertionError(f'Nominal replay differs: {name}: {base} != {v}')
    assert base['despatch_cents']==v['despatch_cents'],'Nominal despatch replay differs'
    storm=replay(schedule,calls,berths,weather,STRESS)
    late=replay(schedule,calls,berths,weather,NOMINAL,delay=True)
    # Objective bound is in same objective units (cents, minutes, etc) as selected objective.
    value=int(round(solver.ObjectiveValue()));bound=float(solver.BestObjectiveBound())
    gap=max(0.0,(value-bound)/max(1.0,abs(value)))
    result.update({'solver_objective':value,'solver_bound':bound,'relative_gap':gap,
      'nominal_cents':v['cost_cents'],'storm_cents':storm['cost_cents'],'late_cents':late['cost_cents'],
      'despatch_cents':base['despatch_cents'],'net_owner_receivable_cents':base['net_owner_receivable_cents'],
      'objective_cost_definition':'gross demurrage cents; despatch reported separately, not subtracted from objective',
      'contract_rule_version':RULE_VERSION,
      'weather_pause_minutes':v['weather_pause_minutes'],'storm_pause_minutes':storm['total_weather_pause_minutes'],
      'peak_capacity':v['peak_berths_used'], 'end':max(x['completion_at'] for x in schedule),
      'mean_wait_minutes':sum((dt(row['work_start_at'])-dt(row['nor_at'])).total_seconds()/60 for row in schedule)/len(schedule),
      'schedule':schedule})
    return result,selected

def write_outputs(results,call_count,berth_count,option_count):
    OUT.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for result in results:
        p=OUT/f"{result['name']}.json"
        p.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        summaries.append({k:v for k,v in result.items() if k!='schedule'})
    (OUT/'cp_sat_summary.json').write_text(json.dumps({'scope':f'finite candidate set, {call_count} calls / {berth_count} identical virtual berths',
        'call_count':call_count,'berth_count':berth_count,'option_count':option_count,
        'has_any_optimal_proof':any(r.get('solver_status')=='OPTIMAL' for r in results),
        'results':summaries},ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'cp_sat_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['name','solver_status','seconds','solver_objective','solver_bound','relative_gap','nominal_cents','despatch_cents','net_owner_receivable_cents','storm_cents','late_cents','weather_pause_minutes','storm_pause_minutes','peak_capacity','end','mean_wait_minutes']
        wr=csv.DictWriter(f,fieldnames=fields);wr.writeheader();wr.writerows({k:r.get(k,'') for k in fields} for r in results)
    with (OUT/'cp_sat_schedules.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['plan','call_id','berth_id','nor_at','work_start_at','completion_at','demurrage_minutes','demurrage_cents','despatch_cents','net_owner_receivable_cents','weather_pause_minutes']
        wr=csv.DictWriter(f,fieldnames=fields);wr.writeheader()
        for r in results:
            for row in r.get('schedule',[]):wr.writerow({k:r['name'] if k=='plan' else row.get(k,'') for k in fields})
    return summaries

def main():
    global OUT
    ap=argparse.ArgumentParser()
    ap.add_argument('--time-limit',type=int,default=180,help='time seconds per solve')
    ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--alternatives',type=int,default=3,choices=range(0,4))
    ap.add_argument('--cost-slack',type=float,default=0.07,help='max nominal cost excess over best found base (fraction)')
    ap.add_argument('--dry-run',action='store_true',help='validate input/hint without OR-Tools')
    ap.add_argument('--output-dir',type=Path,help='output directory; default: results/berths_<count>')
    args=ap.parse_args()
    package,calls,berths,weather=load_inputs();options=package['options'];groups=package['option_lists']
    OUT=(args.output_dir or ROOT/'results'/f'berths_{len(berths)}').resolve()
    OUT.mkdir(parents=True,exist_ok=True)
    hint,stats=reference_selection(options,groups,calls,len(berths))
    base_replay=replay(decode_solution(hint,options,calls,berths,weather),calls,berths,weather,NOMINAL)
    assert base_replay['cost_cents']==stats['cost_cents']
    print('PREFLIGHT_OK',{'calls':len(calls),'options':len(options),'berths':len(berths),
      'warm_start_USD':stats['cost_cents']/100,'peak':stats['peak_berths_used']},flush=True)
    if args.dry_run:
        (OUT/'preflight.json').write_text(json.dumps({'result':'PASS','cp_sat_executed':False,'calls':len(calls),'options':len(options),
            'berths':len(berths),'berth_ids':berths,
            'warm_start_cents':stats['cost_cents'],'replay_matches':True,'capacity_peak':stats['peak_berths_used'],
            'warm_start_origin':json.loads(REF.read_text())['source'],
            'contract_rule_version':RULE_VERSION,
            'processing_duration_policy':'USER_RECONFIRMED_SOF_WORK_SUM',
            'validation_scope':'full-horizon candidate model; rolling horizon deferred'},indent=2),encoding='utf-8')
        return
    try:from ortools.sat.python import cp_model
    except ImportError as e:
        raise SystemExit('OR-Tools is required to execute CP-SAT: python -m pip install ortools; model and data preflight passed') from e
    if any('stress_cents' not in z for z in options):
        raise SystemExit('Missing precomputed stress costs: python precompute_stress.py')
    output=[]; chosen_list=[]
    plan,selected=solve_plan('CP_SAT_MIN_COST',options,groups,calls,berths,weather,hint,'cost',args.time_limit,args.workers)
    output.append(plan)
    if selected is None:
        write_outputs(output,len(calls),len(berths),len(options)); print('CP-SAT did not find a feasible schedule; see results',flush=True);return
    chosen_list.append(selected)
    cap=int(math.floor(plan['nominal_cents']*(1+args.cost_slack)))
    for name,obj in [('CP_SAT_ALT_WAIT','wait'),('CP_SAT_ALT_DOWNTIME','downtime'),('CP_SAT_ALT_STORM','stress')][:args.alternatives]:
        alternative,chosen=solve_plan(name,options,groups,calls,berths,weather,selected,obj,args.time_limit,args.workers,
          cap_cents=cap,previous=chosen_list)
        output.append(alternative)
        if chosen is not None:chosen_list.append(chosen)
    summ=write_outputs(output,len(calls),len(berths),len(options))
    for row in summ:print('RESULT',json.dumps(row,ensure_ascii=False),flush=True)
    print('OUTPUT_DIR',OUT,flush=True)
if __name__=='__main__':main()
