"""Standalone, opt-in tank feasibility optimization with exact replay."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction
import hashlib
import json
import math
import time

from .inventory import project_inventory
from .network import quantity, validate_network
from .validator import parse_time, seconds_between, validate_schedule
from .milp import MODEL_VERSION, SCALE_BBL, FEASIBILITY_TOLERANCE, build_model


def _error(status, code, message, fingerprint=None):
    return {'status':status,'solver_status':'NOT_AVAILABLE','selected_assignments':{},
            'objective':None,'objective_bound':None,'numerical_validation':'NOT_VALIDATED',
            'failure_reasons':[{'code':code,'reason':str(message)}],
            'model_version':MODEL_VERSION,'input_fingerprint':fingerprint,'operational_approval':False}


def _decimal(value):
    """Render an exact rational as a finite decimal or fail closed."""
    value=Fraction(value); den=value.denominator
    twos=fives=0
    while den % 2 == 0: den//=2;twos+=1
    while den % 5 == 0: den//=5;fives+=1
    if den != 1: raise ValueError('Exact rate has no finite decimal representation')
    places=max(twos,fives)
    scaled=value.numerator*(2**(places-twos))*(5**(places-fives))
    sign='-' if scaled < 0 else ''
    digits=str(abs(scaled)).rjust(places+1,'0')
    return sign+(digits if not places else digits[:-places]+'.'+digits[-places:])


def _prepare(network, requirements, windows, start, end, mode):
    if not isinstance(network, dict) or not isinstance(network.get('tanks'), list) or not isinstance(network.get('connections'), list):
        return None,_error('MISSING_PHYSICAL_DATA','MISSING_TANK_DATA','Tank master or connections missing')
    allowed_network = {'schema_version','data_origin','required_tank_count','required_total_capacity_bbl','tanks','connections','notes','operational_approval','source_reference'}
    if set(network)-allowed_network:
        return None,_error('MISSING_PHYSICAL_DATA','UNSUPPORTED_PHYSICAL_CONSTRAINT','Unsupported network fields')
    required_tank_fields = ('tank_id','cargo_type','capacity_bbl','min_operating_bbl','max_operating_bbl','initial_stock_bbl','fixed_process_net_flow_bbl_per_hour')
    if any(not isinstance(t, dict) or any(key not in t or t[key] is None for key in required_tank_fields) for t in network['tanks']):
        return None,_error('MISSING_PHYSICAL_DATA','MISSING_TANK_DATA','Required tank field missing')
    if any(not isinstance(c, dict) or any(key not in c or c[key] is None for key in ('berth_id','tank_id','max_transfer_rate_bbl_per_hour')) for c in network['connections']):
        return None,_error('MISSING_PHYSICAL_DATA','MISSING_TANK_DATA','Required connection field missing')
    allowed_connection = {'berth_id','tank_id','max_transfer_rate_bbl_per_hour','notes','source_reference'}
    allowed_tank = set(required_tank_fields) | {'notes','source_reference'}
    if any(set(c)-allowed_connection for c in network['connections']) or any(set(t)-allowed_tank for t in network['tanks']):
        return None,_error('MISSING_PHYSICAL_DATA','UNSUPPORTED_PHYSICAL_CONSTRAINT','Tank or connection has unsupported fields')
    index=validate_network(network)
    if mode not in ('AUTO','LP','MILP'): raise ValueError('mode must be AUTO, LP, or MILP')
    start=parse_time(start,'horizon_start_at');end=parse_time(end,'horizon_end_at')
    if end<=start: raise ValueError('Invalid horizon')
    if not isinstance(requirements,list) or not requirements or not isinstance(windows,list):
        raise ValueError('Nonempty requirements and windows arrays required')
    calls={}
    for raw in requirements:
        if not isinstance(raw,dict): raise ValueError('Invalid vessel requirement')
        allowed_call={'call_id','berth_id','cargo_type','direction','tank_id','cargo_volume_bbl','carried_volume_bbl','cargo_mass_kg','density_kg_per_bbl','notes','source_reference'}
        if set(raw)-allowed_call:
            return None,_error('INVALID_INPUT','UNSUPPORTED_VESSEL_FIELD','Unsupported vessel requirement fields')
        cid=raw.get('call_id');berth=raw.get('berth_id');cargo=raw.get('cargo_type')
        if not isinstance(cid,str) or not cid or cid in calls or not isinstance(berth,str) or not berth or not isinstance(cargo,str) or not cargo:
            raise ValueError('Missing or duplicate call identity, berth, or cargo')
        if raw.get('direction') not in ('DISCHARGE','LOAD'): raise ValueError('Invalid cargo direction')
        if raw.get('cargo_volume_bbl') is None:
            return None,_error('MISSING_PHYSICAL_DATA','MISSING_VOLUME_OR_CONVERSION',cid)
        total=quantity(raw.get('cargo_volume_bbl'),cid+'.cargo_volume_bbl')
        carried=quantity(raw.get('carried_volume_bbl',0),cid+'.carried_volume_bbl')
        if not 0<=carried<total: raise ValueError(f'{cid}: invalid carried volume')
        if carried and not raw.get('tank_id'):
            return None,_error('MISSING_PHYSICAL_DATA','MISSING_CARRIED_TANK',cid)
        if ('cargo_mass_kg' in raw) != ('density_kg_per_bbl' in raw):
            return None,_error('MISSING_PHYSICAL_DATA','MISSING_MASS_CONVERSION',cid)
        if 'cargo_mass_kg' in raw:
            mass=quantity(raw['cargo_mass_kg'],'cargo_mass_kg');density=quantity(raw['density_kg_per_bbl'],'density_kg_per_bbl')
            if mass<=0 or density<=0 or total*density!=mass: raise ValueError(f'{cid}: mass and density do not match cargo')
        calls[cid]=dict(raw,remaining=total-carried,total=total,carried=carried)
    win_by_id={i:[] for i in calls};boundaries={start,end}
    at=start
    while at<end:
        at=min(at+timedelta(minutes=30),end);boundaries.add(at)
    for w in windows:
        if not isinstance(w,dict): raise ValueError('Invalid transfer window')
        if set(w)-{'call_id','start_at','end_at','fixed_rate_bbl_per_hour','notes','source_reference'}:
            return None,_error('INVALID_INPUT','UNSUPPORTED_WINDOW_FIELD','Unsupported transfer-window fields')
        cid=w.get('call_id')
        if cid not in calls: raise ValueError('Window has unknown call')
        a=parse_time(w.get('start_at'),'window.start_at');b=parse_time(w.get('end_at'),'window.end_at')
        if a<start or b>end or b<=a: raise ValueError('Window outside horizon or empty')
        rate=quantity(w['fixed_rate_bbl_per_hour'],'fixed_rate') if 'fixed_rate_bbl_per_hour' in w else None
        if rate is not None and rate<=0: raise ValueError('Fixed rate must be positive')
        win_by_id[cid].append((a,b,rate));boundaries.update((a,b))
    for cid,parts in win_by_id.items():
        if not parts: return None,_error('MISSING_PHYSICAL_DATA','MISSING_TRANSFER_WINDOW',cid)
        parts.sort(key=lambda x:x[0])
        if any(a[1]>b[0] for a,b in zip(parts,parts[1:])): raise ValueError(f'{cid}: overlapping windows')
    tank_by=index['tank_by_id'];conn=index['connection_by_pair'];pairs={}
    for cid,call in calls.items():
        connected=[k for (b,k) in conn if b==call['berth_id']]
        if call.get('tank_id') is not None:
            connected=[k for k in connected if k==call['tank_id']]
        if not connected: return None,_error('MISSING_PHYSICAL_DATA','TANK_CONNECTION_UNAVAILABLE',cid)
        compatible=sorted(k for k in connected if tank_by[k]['cargo_type']==call['cargo_type'])
        if not compatible: return None,_error('INFEASIBLE','TANK_CARGO_INCOMPATIBLE',cid)
        pairs[cid]=compatible
        maximum = max(sum((quantity(conn[(call['berth_id'], k)]['max_transfer_rate_bbl_per_hour'], 'rate')
                           * Fraction(seconds_between(a,b), 3600) for a,b,_ in win_by_id[cid]), Fraction(0))
                      for k in compatible)
        if maximum < call['remaining']:
            return None,_error('INFEASIBLE','TRANSFER_RATE_INFEASIBLE',cid)
    selected_mode=mode
    if mode=='AUTO': selected_mode='LP' if all(len(v)==1 for v in pairs.values()) else 'MILP'
    if selected_mode=='LP' and any(len(v)!=1 for v in pairs.values()):
        raise ValueError('LP requires a fixed, unique tank assignment per call')
    fixed=all(all(w[2] is not None for w in parts) for parts in win_by_id.values())
    tanks={k:dict(initial=quantity(t['initial_stock_bbl'],'initial'),minimum=quantity(t['min_operating_bbl'],'minimum'),
                  maximum=quantity(t['max_operating_bbl'],'maximum'),flow=quantity(t['fixed_process_net_flow_bbl_per_hour'],'flow')) for k,t in tank_by.items()}
    def available(i,a,b): return any(x<=a and b<=y for x,y,_ in win_by_id[i])
    def rate(i,k): return quantity(conn[(calls[i]['berth_id'],k)]['max_transfer_rate_bbl_per_hour'],'rate')
    def fixed_rate(i,a,b): return next(r for x,y,r in win_by_id[i] if x<=a and b<=y)
    return dict(start=start,end=end,calls=calls,tanks=tanks,pairs=pairs,windows=win_by_id,boundaries=sorted(boundaries),available=available,rate=rate,fixed_rate=fixed_rate,mode=selected_mode,fixed=fixed),None


def _schedule(data, allocations):
    """Use finite-decimal rates and exact residual reconciliation before replay."""
    calls=[];segments=[];rounding=[]
    for cid,call in data['calls'].items():
        tank,parts=allocations[cid]
        record={k:call[k] for k in ('call_id','berth_id','cargo_type','direction')}
        record.update(tank_id=tank,cargo_volume_bbl=_decimal(call['remaining']))
        if 'density_kg_per_bbl' in call:
            density=quantity(call['density_kg_per_bbl'],'density');record.update(density_kg_per_bbl=_decimal(density),cargo_mass_kg=_decimal(call['remaining']*density))
        calls.append(record)
        amounts=[]; quantized_deltas=[]
        for start,end,raw_rate in parts:
            fixed_rate=data['fixed_rate'](cid,start,end)
            if fixed_rate is not None:
                rate=fixed_rate
                quantized_deltas.append('0')
            else:
                rate=Fraction(Decimal(str(raw_rate)).quantize(Decimal('0.000000001'),rounding=ROUND_HALF_EVEN))
                quantized_deltas.append(str(rate-Fraction(str(raw_rate))))
            if rate>0: amounts.append([start,end,rate,fixed_rate is not None])
        if not amounts: raise ValueError(f'{cid}: solver supplied no positive transfer')
        delivered=sum((rate*Fraction(seconds_between(a,b),3600) for a,b,rate,_ in amounts),Fraction(0))
        residual=call['remaining']-delivered
        variable_indices=[idx for idx,item in enumerate(amounts) if not item[3]]
        adjustment=Fraction(0)
        if residual:
            if not variable_indices: raise ValueError(f'{cid}: fixed rates do not reconcile')
            idx=variable_indices[-1]; a,b,rate,_=amounts[idx]
            adjusted=rate+residual*Fraction(3600,seconds_between(a,b))
            if adjusted<=0: raise ValueError(f'{cid}: numerical reconciliation produced nonpositive rate')
            correction_limit = Fraction(str(FEASIBILITY_TOLERANCE))*SCALE_BBL*(len(parts)+1)*Fraction(3600,seconds_between(a,b))
            if abs(adjusted-rate) > correction_limit:
                raise ValueError(f'{cid}: exact reconciliation exceeds declared solver tolerance')
            adjustment=adjusted-rate; amounts[idx][2]=adjusted
        rounding.append({'call_id':cid,'rate_reconciliation_bbl_per_hour':str(adjustment), 'segment_rate_quantization_deltas':quantized_deltas})
        for a,b,rate,_ in amounts:
            segments.append(dict(call_id=cid,start_at=a.isoformat(),end_at=b.isoformat(),rate_bbl_per_hour=_decimal(rate)))
    return {'schema_version':'TANK_SCHEDULE_V1','horizon_start_at':data['start'].isoformat(),
            'horizon_end_at':data['end'].isoformat(),'calls':calls,'segments':segments},rounding



def _fixed_schedule(data, allocations):
    schedule={'schema_version':'TANK_SCHEDULE_V1','horizon_start_at':data['start'].isoformat(),
              'horizon_end_at':data['end'].isoformat(),'calls':[], 'segments':[]}
    for cid,call in data['calls'].items():
        row={k:call[k] for k in ('call_id','berth_id','cargo_type','direction')}
        row.update(tank_id=allocations[cid][0],cargo_volume_bbl=_decimal(call['remaining']))
        if 'density_kg_per_bbl' in call:
            density=quantity(call['density_kg_per_bbl'],'density')
            row.update(density_kg_per_bbl=_decimal(density),cargo_mass_kg=_decimal(call['remaining']*density))
        schedule['calls'].append(row)
        for a,b,rate in data['windows'][cid]:
            schedule['segments'].append(dict(call_id=cid,start_at=a.isoformat(),end_at=b.isoformat(),rate_bbl_per_hour=_decimal(rate)))
    return schedule

def solve_tank_allocation(network,vessel_requirements,transfer_windows,*,horizon_start_at,horizon_end_at,mode='AUTO',time_limit_seconds=30):
    """Solve only the tank subproblem; return no schedule unless exact validation passes."""
    total_start=time.perf_counter()
    try:
        fingerprint=hashlib.sha256(json.dumps({'network':network,'requirements':vessel_requirements,'windows':transfer_windows,'start':horizon_start_at,'end':horizon_end_at,'mode':mode},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if isinstance(time_limit_seconds,bool) or not isinstance(time_limit_seconds,(int,float)) or not math.isfinite(time_limit_seconds) or time_limit_seconds<=0: raise ValueError('Invalid time limit')
        data,err=_prepare(network,vessel_requirements,transfer_windows,horizon_start_at,horizon_end_at,mode)
        if err: err['input_fingerprint']=fingerprint;return err
    except (ValueError,TypeError,KeyError,OverflowError) as exc: return _error('INVALID_INPUT','INVALID_INPUT',exc,locals().get('fingerprint'))
    actual_mode=data['mode']
    # V1 forbids concurrent use of a berth or tank, even if rates are small.
    # LP cannot decide sequencing: fail closed where fixed-assignment windows overlap.
    if actual_mode=='LP' and not data['fixed']:
        for i,call in data['calls'].items():
            for j,other in data['calls'].items():
                if i>=j: continue
                if call['berth_id']==other['berth_id'] or data['pairs'][i][0]==data['pairs'][j][0]:
                    if any(max(a,c)<min(b,d) for a,b,_ in data['windows'][i] for c,d,_ in data['windows'][j]):
                        return _error('INVALID_INPUT','UNSUPPORTED_SHARED_RESOURCE_SEQUENCE','LP requires non-overlapping shared-resource windows; use MILP',fingerprint)
    stats={'construction_seconds':0.,'solver_seconds':0.,'variable_count':0,'constraint_count':0,'binary_variable_count':0}
    allocations={};solver_status='NOT_INVOKED';objective=None;bound=None
    if data['fixed'] and all(len(v)==1 for v in data['pairs'].values()):
        actual_mode='DETERMINISTIC'
        for cid,parts in data['windows'].items(): allocations[cid]=(data['pairs'][cid][0],[(a,b,str(rate)) for a,b,rate in parts])
        # Fixed rates are checked as supplied, without residual adjustment.
        try:
            schedule = _fixed_schedule(data, allocations)
        except (ValueError, TypeError, OverflowError) as exc:
            return _error('NUMERICAL_VALIDATION_FAILED','SCHEDULE_REPRESENTATION_FAILED',exc,fingerprint)
        reconciliation=[]
    else:
        stage = 'MODEL_CONSTRUCTION'
        try:
            import pyomo.environ as pyo
            from pyomo.opt import TerminationCondition
            m,stats=build_model(data,actual_mode)
            solver=pyo.SolverFactory('highs')
            if not solver.available(False): return _error('SOLVER_UNAVAILABLE','SOLVER_UNAVAILABLE','HiGHS is unavailable',fingerprint)
            solver.options['time_limit']=float(time_limit_seconds)
            solver.options['primal_feasibility_tolerance']=FEASIBILITY_TOLERANCE
            solver.options['dual_feasibility_tolerance']=FEASIBILITY_TOLERANCE
            solver.options['mip_feasibility_tolerance']=FEASIBILITY_TOLERANCE
            stage = 'SOLVER'
            t=time.perf_counter();result=solver.solve(m,load_solutions=False);stats['solver_seconds']=time.perf_counter()-t
            term=result.solver.termination_condition;solver_status=str(term)
            if term == TerminationCondition.infeasible:
                return dict(_error('INFEASIBLE','TANK_ALLOCATION_INFEASIBLE',solver_status,fingerprint), mode=actual_mode, solver_status=solver_status, timing=stats)
            if not len(result.solution):
                status = 'TIME_LIMIT_NO_INCUMBENT' if term == TerminationCondition.maxTimeLimit else 'SOLVER_ERROR'
                return dict(_error(status,'SOLVER_NO_INCUMBENT',solver_status,fingerprint), mode=actual_mode, solver_status=solver_status, timing=stats)
            stage = 'RECONSTRUCTION'
            m.solutions.load_from(result)
            objective=float(pyo.value(m.objective))
            raw_bound=result.problem.lower_bound
            bound=float(raw_bound) if raw_bound is not None and math.isfinite(float(raw_bound)) else None
            for cid in data['calls']:
                selected=[k for k in data['pairs'][cid] if actual_mode=='LP' or pyo.value(m.y[cid,k])>0.5]
                if len(selected)!=1: raise ValueError('Invalid tank selection from solver')
                k=selected[0];parts=[]
                for i,tank,t in m.A:
                    if i!=cid or tank!=k: continue
                    q=pyo.value(m.q[i,tank,t]);a,b=data['boundaries'][t:t+2]
                    fixed_rate = data['fixed_rate'](cid, a, b)
                    if fixed_rate is not None:
                        parts.append((a,b,_decimal(fixed_rate)))
                    elif q>0:
                        parts.append((a,b,q*SCALE_BBL*3600/seconds_between(a,b)))
                allocations[cid]=(k,parts)
            if data['fixed']:
                schedule = _fixed_schedule(data, allocations)
                reconciliation = []
            else:
                schedule,reconciliation=_schedule(data,allocations)
        except (ImportError,ModuleNotFoundError) as exc: return _error('SOLVER_UNAVAILABLE','SOLVER_UNAVAILABLE',exc,fingerprint)
        except Exception as exc:
            status = 'NUMERICAL_VALIDATION_FAILED' if stage == 'RECONSTRUCTION' else 'SOLVER_ERROR'
            return dict(_error(status,stage + '_FAILED',exc,fingerprint), mode=actual_mode, solver_status=solver_status, timing=stats)
    started=time.perf_counter()
    try:
        check=validate_schedule(network,schedule)
        projection=project_inventory(network,schedule)
    except (ValueError,TypeError,KeyError) as exc: return _error('NUMERICAL_VALIDATION_FAILED','EXACT_VALIDATION_ERROR',exc,fingerprint)
    validation_seconds=time.perf_counter()-started
    if check['issues'] or projection['status']!='FEASIBLE':
        if actual_mode=='DETERMINISTIC': return dict(_error('INFEASIBLE','DETERMINISTIC_INFEASIBLE','Exact validation failed',fingerprint), failure_reasons=projection['issues'], mode=actual_mode, solver_status=solver_status)
        return dict(_error('NUMERICAL_VALIDATION_FAILED','EXACT_VALIDATION_FAILED','Exact validation failed',fingerprint), failure_reasons=projection['issues'], mode=actual_mode, solver_status=solver_status)
    return {'status':'OPTIMAL' if actual_mode!='DETERMINISTIC' and solver_status=='optimal' else 'FEASIBLE',
            'mode':actual_mode,'solver_status':solver_status,'selected_assignments':{i:k for i,(k,_) in allocations.items()},
            'schedule':schedule,'inventory':projection,'objective':objective,'objective_bound':bound,
            'cargo_reconciliation':{cid:{'original_bbl':str(call['total']), 'carried_bbl':str(call['carried']),
                                          'remaining_bbl':str(call['remaining']),
                                          'scheduled_bbl':str(sum((segment['volume'] for segment in check['segments'] if segment['call_id']==cid),Fraction(0)))}
                                    for cid,call in data['calls'].items()},
            'numerical_validation':'EXACT_PASS','reconciliation':reconciliation,'tolerances':{'primal':FEASIBILITY_TOLERANCE,'dual':FEASIBILITY_TOLERANCE,'mip':FEASIBILITY_TOLERANCE,'scale_bbl':SCALE_BBL},
            'timing':dict(stats,validation_seconds=validation_seconds,total_seconds=time.perf_counter()-total_start),
            'time_limit_seconds':time_limit_seconds,
            'optimality_scope':'Zero objective feasibility within supplied event intervals and one-tank policy; not global scheduling optimality',
            'failure_reasons':[],'model_version':MODEL_VERSION,'input_fingerprint':fingerprint,'operational_approval':False}
