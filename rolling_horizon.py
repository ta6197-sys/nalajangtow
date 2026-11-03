"""Historical-scenario rolling planning: next-day arrivals, fixed ongoing work.

SOF WORK sums are fixed workload inputs by explicit user choice. This validates
planning and carryover, not weather/ETA forecasting or real operation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ortools.sat.python import cp_model

from contracts import RULE_VERSION
from cp_sat_solver import make_ortools_model
from daily_schedule import build_daily_schedule, write_report
from weather_optimizer import (WeatherSeries, WeatherRule, load_model, simulate_work,
                               price_contract, at_datetime as dt, _validate_schedule)

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
RULE = WeatherRule(4.8, 1.0, '유의파고(m)', 'WORK_ONLY', 'ERROR')
SHIFTS = [-72, -48, -24, -12, -6, -3, 0, 3, 6, 12, 24, 48, 72]


def kst(at):
    return dt(at).replace(tzinfo=KST).isoformat()


def workload_remaining(row, call, cut):
    a, b = dt(row['work_start_at']), min(dt(row['completion_at']), cut)
    worked = max(0, int((b-a).total_seconds()/60))
    for x, y in row['weather_pause_intervals']:
        worked -= max(0, int((min(dt(y), b)-max(dt(x), a)).total_seconds()/60))
    remaining = call['processing_minutes']-worked
    if not 0 <= remaining <= call['processing_minutes']:
        raise AssertionError('Invalid remaining workload')
    return remaining


def free_times(berths, begin, ongoing):
    free = {bid: begin for bid in berths}
    seen = set()
    for row in ongoing:
        bid = row['berth_id']
        if bid not in free or bid in seen:
            raise AssertionError('Invalid ongoing berth occupancy')
        seen.add(bid)
        free[bid] = max(begin, dt(row['completion_at']))
    return free


def simulate_call(call, proposed, weather, rule):
    start, end, pauses = simulate_work(proposed, call['processing_minutes'], weather, rule)
    price = price_contract(call['contract'], start, end, pauses, weather, rule)
    return {'call_id': call['call_id'], 'nor_at': call['available_at'],
            'work_start_at': start.isoformat(), 'completion_at': end.isoformat(),
            'weather_pause_intervals': [[a.isoformat(), b.isoformat()] for a, b in pauses],
            'weather_pause_minutes': sum(int((b-a).total_seconds()/60) for a, b in pauses),
            'clock_start_at': price['clock_start_at'], 'demurrage_minutes': price['demurrage_minutes'],
            'demurrage_cents': price['demurrage_cents'], 'despatch_cents': price['despatch_cents'],
            'net_owner_receivable_cents': price['net_owner_receivable_cents'], 'contract_cost': price}


def baseline(calls, berths, begin, ongoing, weather, rule, order, simulate=simulate_call):
    byid = {c['call_id']: c for c in calls}
    rank = {cid: i for i, cid in enumerate(order)}
    free = free_times(berths, begin, ongoing)
    pending = set(byid)
    rows = []
    while pending:
        bid = min(berths, key=lambda b: (free[b], b))
        earliest = free[bid]
        eligible = [cid for cid in pending if dt(byid[cid]['available_at']) <= earliest]
        if not eligible:
            earliest = max(earliest, min(dt(byid[cid]['available_at']) for cid in pending))
            eligible = [cid for cid in pending if dt(byid[cid]['available_at']) <= earliest]
        cid = min(eligible, key=rank.__getitem__)
        row = simulate(byid[cid], max(begin, earliest), weather, rule)
        row['berth_id'] = bid
        rows.append(row)
        free[bid] = dt(row['completion_at'])
        pending.remove(cid)
    return rows


def build_day_candidates(calls, berths, begin, ongoing, weather, rule):
    orders = {
        'FCFS': sorted(calls, key=lambda c: (dt(c['available_at']), c['call_id'])),
        'DAILY_RATE': sorted(calls, key=lambda c: (dt(c['available_at']).date(), -c['contract']['daily_demurrage_usd'], c['call_id'])),
        'DAILY_SHORT': sorted(calls, key=lambda c: (dt(c['available_at']).date(), c['processing_minutes'], c['call_id'])),
    }
    baselines = {name: baseline(calls, berths, begin, ongoing, weather, rule, [c['call_id'] for c in order])
                 for name, order in orders.items()}
    anchors = {c['call_id']: [] for c in calls}
    for rows in baselines.values():
        for row in rows:
            anchors[row['call_id']].append(dt(row['work_start_at']))
    options, groups = [], []
    for i, call in enumerate(calls):
        earliest = max(begin, dt(call['available_at']))
        proposals = {earliest, *anchors[call['call_id']]}
        for anchor in anchors[call['call_id']][:2]:
            proposals.update(anchor+timedelta(hours=h) for h in SHIFTS)
        variants = {}
        for proposed in sorted(proposals):
            row = simulate_call(call, max(earliest, proposed), weather, rule)
            variants[row['work_start_at']] = row
        group = []
        for start, row in sorted(variants.items()):
            group.append(len(options))
            options.append({'call_idx': i, 'call_id': call['call_id'], 'start': start,
                            'end': row['completion_at'], 'cents': row['demurrage_cents'],
                            'delay_min': int((dt(start)-dt(call['available_at'])).total_seconds()/60),
                            'row': row})
        groups.append(group)
    return options, groups, baselines


def solve_day(calls, berths, begin, ongoing, weather, rule, time_limit, tie_break_wait):
    if not calls:
        return [], {name: [] for name in ['FCFS', 'DAILY_RATE', 'DAILY_SHORT']}, {
            'primary_status': 'NO_NEW_DECISIONS', 'primary_gap': 0, 'secondary_status': 'NO_NEW_DECISIONS',
            'seconds': 0, 'candidate_count': 0, 'primary_objective_cents': 0}
    options, groups, baselines = build_day_candidates(calls, berths, begin, ongoing, weather, rule)
    lookup = {(z['call_id'], z['start'], z['end']): j for j, z in enumerate(options)}
    idx = {c['call_id']: i for i, c in enumerate(calls)}
    best = min(baselines.values(), key=lambda rows: (sum(r['demurrage_cents'] for r in rows), sum((dt(r['work_start_at'])-dt(next(c['available_at'] for c in calls if c['call_id']==r['call_id']))).total_seconds() for r in rows)))
    hint = {idx[r['call_id']]: lookup[(r['call_id'], r['work_start_at'], r['completion_at'])] for r in best}
    fixed = [(begin.isoformat(), r['completion_at']) for r in ongoing]
    model, x = make_ortools_model(options, groups, len(berths), hint=hint, fixed_intervals=fixed)
    if model.validate():
        raise ValueError(model.validate())
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 42
    solver.parameters.relative_gap_limit = 0
    t0 = time.monotonic()
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f'No daily feasible plan: {solver.StatusName(status)}')
    selected = {i: next(j for j in g if solver.Value(x[j])) for i, g in enumerate(groups)}
    value, bound = int(round(solver.ObjectiveValue())), float(solver.BestObjectiveBound())
    record = {'primary_status': solver.StatusName(status), 'primary_objective_cents': value,
              'primary_bound_cents': bound, 'primary_gap': max(0, (value-bound)/max(1, abs(value))),
              'secondary_status': 'NOT_RUN', 'candidate_count': len(options)}
    remaining = time_limit-(time.monotonic()-t0)
    if tie_break_wait and status == cp_model.OPTIMAL and remaining > 0.01:
        secondary, y = make_ortools_model(options, groups, len(berths), objective='wait', cap_cents=value,
                                         hint=selected, fixed_intervals=fixed)
        secondary.Add(sum(z['cents']*y[j] for j, z in enumerate(options)) == value)
        second = cp_model.CpSolver()
        second.parameters.max_time_in_seconds = remaining
        second.parameters.num_search_workers = 8
        second.parameters.random_seed = 42
        second_status = second.Solve(secondary)
        record['secondary_status'] = second.StatusName(second_status)
        if second_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected = {i: next(j for j in g if second.Value(y[j])) for i, g in enumerate(groups)}
            record['secondary_wait_objective_minutes'] = int(round(second.ObjectiveValue()))
    record['seconds'] = round(time.monotonic()-t0, 3)
    assert sum(options[j]['cents'] for j in selected.values()) == value
    free = free_times(berths, begin, ongoing)
    decoded = []
    for i, j in sorted(selected.items(), key=lambda item: (options[item[1]]['start'], item[0])):
        row = dict(options[j]['row'])
        choices = [bid for bid in berths if free[bid] <= dt(row['work_start_at'])]
        if not choices:
            raise AssertionError('Daily capacity solution could not be assigned to a free berth')
        bid = min(choices, key=lambda b: (free[b], b))
        row['berth_id'] = bid
        free[bid] = dt(row['completion_at'])
        decoded.append(row)
    return decoded, baselines, record


def partial_price(call, row, cut, weather, rule):
    if row is None:
        # No work has started: notice clock applies, without inventing early work.
        marker = dt(call['contract']['nor_at'])+timedelta(hours=call['contract']['notice_hours'])
        return price_contract(call['contract'], marker, cut, [], weather, rule)
    return price_contract(call['contract'], dt(row['work_start_at']), min(cut, dt(row['completion_at'])),
                          [(dt(a), dt(b)) for a, b in row['weather_pause_intervals']], weather, rule)


def daily_costs(calls, schedule, begin, end, weather, rule):
    byid = {r['call_id']: r for r in schedule}
    costs = {}
    for call in calls:
        row = byid.get(call['call_id'])
        before = partial_price(call, row, begin, weather, rule)
        after = partial_price(call, row, end, weather, rule)
        delta = after['demurrage_cents']-before['demurrage_cents']
        if delta < 0:
            raise AssertionError('Daily gross demurrage must not decrease')
        due = after['despatch_cents'] if row and begin < dt(row['completion_at']) <= end else 0
        costs[call['call_id']] = {'demurrage_cents': delta, 'despatch_due_cents': due}
    return costs


def display_row(row):
    return dict(row, work_start_at=kst(row['work_start_at']), completion_at=kst(row['completion_at']),
                weather_pause_intervals=[[kst(a), kst(b)] for a, b in row['weather_pause_intervals']])


def run_rolling(calls, berths, weather, rule=RULE, time_limit=180, tie_break_wait=True, output_dir=None):
    if not calls:
        raise ValueError('No rolling calls')
    byid = {c['call_id']: c for c in calls}
    if len(byid) != len(calls):
        raise ValueError('Duplicate rolling call IDs')
    output = Path(output_dir) if output_dir else None
    if output:
        if output.exists() and any(output.iterdir()):
            raise ValueError('Existing rolling output is preserved; choose a new directory')
        output.mkdir(parents=True, exist_ok=True)
    first = min(dt(c['available_at']) for c in calls).replace(hour=0, minute=0)
    begin = first
    committed = {}
    ledger = {cid: 0 for cid in byid}
    daily = []
    paid_despatch = 0
    labels = {'FCFS': '도착순', 'DAILY_RATE': '같은 날짜 내 일일 요율 우선',
              'DAILY_SHORT': '같은 날짜 내 작업시간 우선', 'CP_SAT_MIN_COST': '체선료 최소·동일 비용 내 대기 최소'}
    while True:
        end = begin+timedelta(days=1)
        if end > weather.end:
            raise ValueError('Weather scenario does not cover completion of rolling planning')
        ongoing = [r for r in committed.values() if dt(r['completion_at']) > begin]
        waiting = [c for c in calls if dt(c['available_at']) < end and c['call_id'] not in committed]
        active = [byid[r['call_id']] for r in ongoing]+waiting
        assert all(dt(c['available_at']) < end for c in active)
        print('ROLLING_DAY_START', json.dumps({'date': begin.date().isoformat(), 'ongoing': len(ongoing),
              'waiting_and_next_day_arrivals': len(waiting)}, ensure_ascii=False), flush=True)
        chosen, baselines, record = solve_day(waiting, berths, begin, ongoing, weather, rule, time_limit, tie_break_wait)
        for schedule in [chosen, *baselines.values()]:
            _validate_schedule(ongoing+schedule, active)
        plans = []
        for name, schedule in [*baselines.items(), ('CP_SAT_MIN_COST', chosen)]:
            full = ongoing+schedule
            costs = daily_costs(active, full, begin, end, weather, rule)
            plans.append({'plan_id': name, 'label': labels[name], 'method': name,
                          'schedule': [display_row(r) for r in full], 'daily_costs': costs,
                          'evaluation_cost_to_completion_cents': sum(r['demurrage_cents'] for r in full)})
        bundle = {'berths': berths,
                  'calls': [{'call_id': c['call_id'], 'vessel_name': c.get('vessel_name', c['call_id']),
                             'available_at': kst(c['available_at']),
                             'availability_basis': 'NOR_PROXY_KNOWN_TARGET_DAY_ARRIVAL_SCENARIO'} for c in active],
                  'plans': plans, 'planning_input_status': 'CALCULATED_ROLLING_HISTORICAL_SCENARIO',
                  'data_note': '실제 계산한 일일 재계획·이월 시나리오입니다. SOF WORK는 고정 작업량으로 사용하고 과거 기상 시나리오를 적용했습니다. 전날에 다음 날의 NOR 가용시각을 계획 입력으로 안다는 실험 가정이며, 실제 ETA·기상 예측 성능을 뜻하지 않습니다.'}
        report = build_daily_schedule(bundle, begin.date().isoformat())
        report['limitations'][-1] = '당일 체선료는 계약 계산기의 누적 발생액 차이입니다. 조출료는 완료 당일에만 별도 계상합니다. 최적화 목적은 해당 계획 대상의 완료까지 체선료이며 당일 증가분만 최소화하지 않습니다.'
        report['rolling_context'] = {'planning_on': (begin-timedelta(days=1)).date().isoformat(),
            'target_day': begin.date().isoformat(), 'input_state_source': 'SIMULATED_PRIOR_DAY_END_STATE',
            'future_arrival_policy': 'TARGET_DAY_ONLY', 'included_call_ids': sorted(c['call_id'] for c in active),
            'weather_mode': 'HISTORICAL_SCENARIO_NOT_FORECAST', 'solver': record,
            'future_planned_starts_are_tentative': True}
        for row in chosen:
            if dt(row['work_start_at']) < end:
                if row['call_id'] in committed:
                    raise AssertionError('A call was started twice')
                committed[row['call_id']] = dict(row)
        opt = next(p for p in report['plans'] if p['plan_id'] == 'CP_SAT_MIN_COST')
        for call in active:
            actual = committed.get(call['call_id'])
            accrued = partial_price(call, actual, end, weather, rule)['demurrage_cents']
            if accrued < ledger[call['call_id']]:
                raise AssertionError('Carried contract ledger decreased')
            ledger[call['call_id']] = accrued
        day_fee = opt['summary']['daily_estimated_demurrage_cents']
        assert sum(ledger.values()) == sum(d['actual_day_demurrage_cents'] for d in daily)+day_fee
        paid_despatch += opt['summary']['daily_despatch_due_cents']
        checkpoint = {'state_at': kst(end), 'state_source': 'SIMULATED_SCENARIO_EXECUTION',
                      'accumulated_demurrage_cents': sum(ledger.values()), 'demurrage_ledger_by_call': dict(ledger),
                      'completed_call_ids': sorted(cid for cid, r in committed.items() if dt(r['completion_at']) <= end),
                      'ongoing': [], 'waiting': []}
        for call in calls:
            cid = call['call_id']
            if dt(call['available_at']) >= end:
                continue
            row = committed.get(cid)
            if row and dt(row['completion_at']) <= end:
                continue
            price = partial_price(call, row, end, weather, rule)
            item = {'call_id': cid, 'original_nor_at': kst(call['available_at']),
                    'remaining_work_minutes': workload_remaining(row, call, end) if row else call['processing_minutes'],
                    'accrued_demurrage_cents': ledger[cid], 'contract_state': price}
            if row:
                item.update(berth_id=row['berth_id'], original_work_start_at=kst(row['work_start_at']))
                checkpoint['ongoing'].append(item)
            else:
                item.update(berth_id=None, work_start_at=None)
                checkpoint['waiting'].append(item)
        daily_record = dict(report['rolling_context'], actual_day_demurrage_cents=day_fee,
                            actual_day_despatch_due_cents=opt['summary']['daily_despatch_due_cents'],
                            accumulated_demurrage_cents=sum(ledger.values()),
                            completed_total=len(checkpoint['completed_call_ids']),
                            ongoing_at_end=len(checkpoint['ongoing']), waiting_at_end=len(checkpoint['waiting']))
        daily.append(daily_record)
        if output:
            directory = output / begin.date().isoformat()
            write_report(report, directory)
            (directory/'next_day_state.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print('ROLLING_DAY_DONE', json.dumps(daily_record, ensure_ascii=False), flush=True)
        if len(checkpoint['completed_call_ids']) == len(calls):
            break
        begin = end
    schedule = sorted(committed.values(), key=lambda r: (r['work_start_at'], r['berth_id'], r['call_id']))
    _validate_schedule(schedule, calls)
    for row in schedule:
        call = byid[row['call_id']]
        assert workload_remaining(row, call, dt(row['completion_at'])) == 0
        price = partial_price(call, row, dt(row['completion_at']), weather, rule)
        assert price['demurrage_cents'] == row['demurrage_cents'] == ledger[row['call_id']]
        assert price['despatch_cents'] == row['despatch_cents']
    assert sum(r['despatch_cents'] for r in schedule) == paid_despatch
    total = sum(ledger.values())
    result = {'schema_version': 'rolling-scenario-v1', 'contract_rule_version': RULE_VERSION,
              'scope': 'Target-day arrivals only; historical weather scenario; fixed SOF WORK; no forecast accuracy claim',
              'call_count': len(calls), 'berth_count': len(berths), 'day_count': len(daily),
              'time_limit_per_day_seconds': time_limit, 'tie_break_wait': tie_break_wait,
              'first_planning_on': (first-timedelta(days=1)).date().isoformat(),
              'first_target_day': first.date().isoformat(),
              'gross_demurrage_cents': total, 'despatch_cents': paid_despatch,
              'net_owner_receivable_cents': total-paid_despatch,
              'mean_wait_hours': sum((dt(r['work_start_at'])-dt(byid[r['call_id']]['available_at'])).total_seconds()/3600 for r in schedule)/len(calls),
              'last_completion_at': max(r['completion_at'] for r in schedule),
              'all_calls_completed_once': True, 'daily_ledger_matches_final_settlement': True,
              'all_work_durations_verified': True, 'days': daily, 'schedule': schedule}
    if output:
        (output/'rolling_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        with (output/'committed_schedule.csv').open('w', encoding='utf-8-sig', newline='') as f:
            fields = ['call_id','berth_id','nor_at','work_start_at','completion_at','weather_pause_minutes','demurrage_minutes','demurrage_cents','despatch_cents','net_owner_receivable_cents']
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            writer.writerows({k:r[k] for k in fields} for r in schedule)
    return result


def main():
    parser = argparse.ArgumentParser(description='Next-day rolling optimization under the supplied historical scenario')
    parser.add_argument('--model', type=Path, default=ROOT/'input/model_input_140.json')
    parser.add_argument('--weather', type=Path, default=ROOT/'input/weather_for_optimizer_30min.csv')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--time-limit', type=float, default=180)
    parser.add_argument('--no-wait-tie-break', action='store_true')
    args = parser.parse_args()
    if args.time_limit <= 0:
        raise ValueError('Positive daily time limit required')
    calls, berths = load_model(args.model)
    weather = WeatherSeries.from_csv(args.weather)
    model_hash = hashlib.sha256(args.model.read_bytes()).hexdigest()
    weather_hash = hashlib.sha256(args.weather.read_bytes()).hexdigest()
    result = run_rolling(calls, berths, weather, time_limit=args.time_limit,
                         tie_break_wait=not args.no_wait_tie_break, output_dir=args.output_dir)
    assert hashlib.sha256(args.model.read_bytes()).hexdigest() == model_hash
    assert hashlib.sha256(args.weather.read_bytes()).hexdigest() == weather_hash
    (args.output_dir/'input_provenance.json').write_text(json.dumps({
        'model':str(args.model.resolve()),'model_sha256':model_hash,'weather':str(args.weather.resolve()),
        'weather_sha256':weather_hash,'weather_mode':'HISTORICAL_SCENARIO_NOT_FORECAST',
        'initial_state':'All eight virtual berths free before first supplied call; no other vessel workload assumed',
        'future_arrivals':'Only next-day NOR availability is admitted; no later call is used by the day solver',
        'state':'Simulated scenario state, not measured operational state'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('ROLLING_COMPLETE',json.dumps({k:result[k] for k in ['call_count','berth_count','day_count','gross_demurrage_cents','last_completion_at']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
