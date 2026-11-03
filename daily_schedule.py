"""Render supplied plans as complete KST calendar-day schedules.

This module does not optimize, price contracts, or derive work durations from SOF.
It preserves unfinished calls and reports all configured berths, including idle time.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
STATE_LABELS = {
    'WORK': '작업', 'WEATHER_PAUSE': '기상 중단', 'IDLE': '유휴',
    'COMPLETED': '당일 완료', 'CONTINUING': '다음 날 작업 계속',
    'WAITING': '다음 날 대기 이월',
}


def timestamp(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError('시각에는 시간대가 필요합니다. 예: 2026-11-02T06:00:00+09:00')
    at = at.astimezone(KST)
    if at.second or at.microsecond:
        raise ValueError('일정은 분 단위 시각이어야 합니다')
    return at


def minutes(start, end):
    return int((end - start).total_seconds() // 60)


def normalize(bundle):
    berths = bundle['berths']
    if not berths or any(not isinstance(b, str) or not b for b in berths) or len(set(berths)) != len(berths):
        raise ValueError('선석 ID는 비어 있지 않고 중복되지 않아야 합니다')
    calls = {}
    for call in bundle['calls']:
        cid = call['call_id']
        if not isinstance(cid, str) or not cid or cid in calls:
            raise ValueError('항차 ID가 비어 있거나 중복됩니다')
        calls[cid] = dict(call, available_at=timestamp(call['available_at']))
    plans = []
    seen = set()
    for plan in bundle['plans']:
        pid = plan['plan_id']
        if not isinstance(pid, str) or not pid or pid in seen:
            raise ValueError('계획안 ID가 비어 있거나 중복됩니다')
        seen.add(pid)
        scheduled = {}
        occupied = {b: [] for b in berths}
        for source in plan['schedule']:
            cid = source['call_id']
            if cid not in calls or cid in scheduled:
                raise ValueError(f'일정 항차가 없거나 중복됩니다: {cid}')
            bid = source['berth_id']
            if bid not in occupied:
                raise ValueError(f'등록되지 않은 선석: {bid}')
            start, end = timestamp(source['work_start_at']), timestamp(source['completion_at'])
            if start < calls[cid]['available_at'] or end <= start:
                raise ValueError(f'작업 시작·완료시각 오류: {cid}')
            if 'weather_pause_intervals' not in source:
                raise ValueError(f'기상 중단 구간이 필요합니다. 중단이 없으면 빈 목록을 넣으세요: {cid}')
            pauses = sorted((timestamp(a), timestamp(b)) for a, b in source['weather_pause_intervals'])
            previous = start
            for a, b in pauses:
                if a < previous or b <= a or b > end:
                    raise ValueError(f'기상 중단 구간의 범위·중복 오류: {cid}')
                previous = b
            scheduled[cid] = dict(source, work_start_at=start, completion_at=end, pauses=pauses)
            occupied[bid].append((start, end, cid))
        for bid, intervals in occupied.items():
            intervals.sort()
            for before, after in zip(intervals, intervals[1:]):
                if after[0] < before[1]:
                    raise ValueError(f'선석 점유 중복: {bid}, {before[2]}, {after[2]}')
        plans.append(dict(plan, scheduled=scheduled))
    if not plans:
        raise ValueError('출력할 계획안이 없습니다')
    return list(berths), calls, plans


def work_segments(row):
    at = row['work_start_at']
    for start, end in row['pauses']:
        if at < start:
            yield at, start, 'WORK'
        yield start, end, 'WEATHER_PAUSE'
        at = end
    if at < row['completion_at']:
        yield at, row['completion_at'], 'WORK'


def build_daily_schedule(bundle, planning_date):
    day = date.fromisoformat(planning_date)
    begin = datetime.combine(day, time.min, KST)
    finish = begin + timedelta(days=1)
    berths, calls, plans = normalize(bundle)
    output = {
        'schema_version': 'daily-schedule-v1', 'planning_date': day.isoformat(),
        'timezone': 'Asia/Seoul', 'day_start_at': begin.isoformat(),
        'day_end_exclusive_at': finish.isoformat(), 'berths': berths,
        'data_note': bundle.get('data_note', ''),
        'planning_input_status': bundle.get('planning_input_status', 'UNSPECIFIED'),
        'optimization_executed_by_this_module': False,
        'cost_calculation_executed_by_this_module': False,
        'limitations': [
            '입력받은 계획의 일일 상세 출력입니다. 이 모듈은 우선순위를 정하거나 새 일정을 최적화하지 않습니다.',
            '일말 이월은 계획대로 실행됐을 때의 예상 상태입니다. 다음 날에는 확인된 실제 상태로 갱신해야 합니다.',
            '당일 체선료는 계약 계산 연결 전까지 미산정으로 표시합니다. 전체 항차 비용을 당일 비용으로 합산하지 않습니다.',
        ], 'plans': [],
    }
    for plan in plans:
        rows, queue, carryover = [], [], []
        events = {b: [] for b in berths}
        provided_costs=plan.get('daily_costs',{})
        for cid, call in calls.items():
            available = call['available_at']
            if available >= finish:
                continue
            row = plan['scheduled'].get(cid)
            start = row['work_start_at'] if row else None
            end = row['completion_at'] if row else None
            if end is not None and end <= begin:
                continue
            name = call.get('vessel_name', cid)
            wait_start, wait_end = max(available, begin), min(start or finish, finish)
            wait_minutes = max(0, minutes(wait_start, wait_end))
            if wait_minutes:
                queue.append({'call_id': cid, 'vessel_name': name,
                              'start_at': wait_start.isoformat(), 'end_at': wait_end.isoformat(),
                              'minutes': wait_minutes,
                              'reason': 'WAITING_FOR_PLANNED_START' if row else 'NO_START_ASSIGNED'})
            work = pause = 0
            if row:
                for a, b, state in work_segments(row):
                    a, b = max(a, begin), min(b, finish)
                    if a >= b:
                        continue
                    duration = minutes(a, b)
                    if state == 'WORK':
                        work += duration
                    else:
                        pause += duration
                    events[row['berth_id']].append({
                        'call_id': cid, 'vessel_name': name, 'berth_id': row['berth_id'],
                        'start_at': a.isoformat(), 'end_at': b.isoformat(),
                        'state': state, 'minutes': duration,
                    })
            state = 'COMPLETED' if end and end <= finish else 'CONTINUING' if start and start < finish else 'WAITING'
            remaining = None
            if row:
                remaining = sum(max(0, minutes(max(a, finish), b))
                                for a, b, kind in work_segments(row) if kind == 'WORK' and b > finish)
            item = {
                'call_id': cid, 'vessel_name': name,
                'berth_id': row['berth_id'] if row else None,
                'available_at': available.isoformat(),
                'availability_basis': call.get('availability_basis', 'UNSPECIFIED'),
                'planned_work_start_at': start.isoformat() if start else None,
                'planned_completion_at': end.isoformat() if end else None,
                'carried_in': available < begin,
                'occupied_at_day_start': bool(start and start < begin and end > begin),
                'day_wait_minutes': wait_minutes, 'day_work_minutes': work,
                'day_weather_pause_minutes': pause,
                'day_end_state': state, 'remaining_planned_work_minutes': remaining,
                'daily_estimated_demurrage_cents': None,
                'daily_cost_status': 'NOT_CALCULATED',
                'daily_despatch_due_cents':None,
            }
            if cid in provided_costs:
                cost=provided_costs[cid]
                if any(type(cost.get(k)) is not int or cost[k]<0 for k in ['demurrage_cents','despatch_due_cents']):
                    raise ValueError('Daily accrued cost must be explicitly calculated nonnegative integer cents')
                item.update(daily_estimated_demurrage_cents=cost['demurrage_cents'],
                            daily_despatch_due_cents=cost['despatch_due_cents'],daily_cost_status='CALCULATED_BY_PLANNING_ENGINE')
            rows.append(item)
            if state != 'COMPLETED':
                carryover.append(dict(item))
        rows.sort(key=lambda r: (r['planned_work_start_at'] or finish.isoformat(), r['berth_id'] or '', r['call_id']))
        timelines = []
        for bid in berths:
            booked = sorted(events[bid], key=lambda e: e['start_at'])
            complete = []
            cursor = begin
            for event in booked:
                event_start = timestamp(event['start_at'])
                if cursor < event_start:
                    complete.append(idle_event(bid, cursor, event_start))
                complete.append(event)
                cursor = timestamp(event['end_at'])
            if cursor < finish:
                complete.append(idle_event(bid, cursor, finish))
            timelines.append({'berth_id': bid, 'events': complete})
        output['plans'].append({
            'plan_id': plan['plan_id'], 'label': plan.get('label', plan['plan_id']),
            'method': plan.get('method', 'UNSPECIFIED'),
            'summary': {
                'calls_in_day': len(rows),
                'completed_calls': sum(r['day_end_state'] == 'COMPLETED' for r in rows),
                'continuing_calls': sum(r['day_end_state'] == 'CONTINUING' for r in rows),
                'waiting_at_day_end': sum(r['day_end_state'] == 'WAITING' for r in rows),
                'work_minutes': sum(r['day_work_minutes'] for r in rows),
                'weather_pause_minutes': sum(r['day_weather_pause_minutes'] for r in rows),
                'waiting_minutes': sum(r['day_wait_minutes'] for r in rows),
                'daily_estimated_demurrage_cents':sum(r['daily_estimated_demurrage_cents'] for r in rows) if all(r['daily_estimated_demurrage_cents'] is not None for r in rows) and 'daily_costs' in plan else None,
                'daily_despatch_due_cents':sum(r['daily_despatch_due_cents'] for r in rows) if all(r['daily_despatch_due_cents'] is not None for r in rows) and 'daily_costs' in plan else None,
            },
            'calls': rows, 'berth_timelines': timelines, 'waiting_intervals': queue,
            'next_day_planned_state': carryover,
        })
    return output


def idle_event(bid, start, end):
    return {'call_id': None, 'vessel_name': None, 'berth_id': bid,
            'start_at': start.isoformat(), 'end_at': end.isoformat(),
            'state': 'IDLE', 'minutes': minutes(start, end)}


def cell(value):
    return str(value if value is not None else '미정').replace('|', '\\|').replace('\n', ' ')


def display_time(value):
    return timestamp(value).strftime('%m-%d %H:%M') if value else '미배정'


def render_markdown(report):
    lines = [f"# {report['planning_date']} 하루 일정", '',
             '한국시간 00:00~24:00. 표의 다음 날 완료시각은 그대로 표시하고, 당일 시간표만 날짜 경계에서 나눕니다.',
             '', report['data_note'], '',
             ('**당일 체선료는 연결된 계획 엔진에서 산정한 증가분입니다. 과거 발생액을 다시 합산하지 않습니다.**'
              if all(p['summary']['daily_estimated_demurrage_cents'] is not None for p in report['plans'])
              else '**당일 체선료: 미산정. 이 출력 과정에서는 최적화·계약 비용 계산을 실행하지 않았습니다.**'), '']
    for plan in report['plans']:
        lines += [f"## {cell(plan['label'])} ({cell(plan['plan_id'])})", '',
                  '### 선박별 일정 리스트', '',
                  '| 순번 | 항차 / 선박 | 선석 | 예정 작업 시작 | 예정 완료 | 당일 작업 / 기상 중단 / 대기(분) | 당일 체선료(USD) | 일말 상태 |',
                  '|---:|---|---|---|---|---:|---:|---|']
        for n, row in enumerate(plan['calls'], 1):
            cost_text='미산정' if row['daily_estimated_demurrage_cents'] is None else f"{row['daily_estimated_demurrage_cents']/100:,.2f}"
            lines.append(f"| {n} | {cell(row['call_id'])} / {cell(row['vessel_name'])} | {cell(row['berth_id'])} | "
                         f"{display_time(row['planned_work_start_at'])} | {display_time(row['planned_completion_at'])} | "
                         f"{row['day_work_minutes']} / {row['day_weather_pause_minutes']} / {row['day_wait_minutes']} | "
                         f"{cost_text} | {STATE_LABELS[row['day_end_state']]} |")
        if not plan['calls']:
            lines.append('| — | 해당 항차 없음 | — | — | — | — | — | — |')
        lines += ['', '### 선석별 24시간 시간표', '',
                  '| 선석 | 구간 시작 | 구간 종료 | 상태 | 항차 |', '|---|---|---|---|---|']
        for berth in plan['berth_timelines']:
            for event in berth['events']:
                lines.append(f"| {cell(berth['berth_id'])} | {display_time(event['start_at'])} | "
                             f"{display_time(event['end_at'])} | {STATE_LABELS[event['state']]} | {cell(event['call_id']) if event['call_id'] else '—'} |")
        lines += ['', '### 선박별 대기 구간', '',
                  '| 항차 | 대기 시작 | 대기 종료 | 대기(분) | 배정 상태 |', '|---|---|---|---:|---|']
        for interval in plan['waiting_intervals']:
            reason = '예정 작업 시작 대기' if interval['reason'] == 'WAITING_FOR_PLANNED_START' else '시작시각 미배정'
            lines.append(f"| {cell(interval['call_id'])} | {display_time(interval['start_at'])} | "
                         f"{display_time(interval['end_at'])} | {interval['minutes']} | {reason} |")
        if not plan['waiting_intervals']:
            lines.append('| — | — | — | 0 | 대기 항차 없음 |')
        lines += ['', '### 다음 날 이월 예정', '']
        if not plan['next_day_planned_state']:
            lines.append('- 이월 항차 없음')
        for row in plan['next_day_planned_state']:
            left = row['remaining_planned_work_minutes']
            lines.append(f"- {cell(row['call_id'])}: {STATE_LABELS[row['day_end_state']]}, "
                         f"남은 예정 작업시간 {str(left)+'분' if left is not None else '미정'}, "
                         f"원래 가용시각 {row['available_at']} 유지")
        lines.append('')
    lines += ['## 출력 범위', ''] + ['- ' + note for note in report['limitations']]
    return '\n'.join(lines) + '\n'


def write_report(report, directory):
    dest = Path(directory)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('기존 출력은 보존합니다. 비어 있거나 새 출력 폴더를 지정하세요')
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'daily_plan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (dest / '하루_상세일정.md').write_text(render_markdown(report), encoding='utf-8')
    event_fields = ['plan_id', 'berth_id', 'start_at', 'end_at', 'state', 'call_id', 'vessel_name', 'minutes']
    with (dest / 'daily_berth_schedule.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=event_fields)
        writer.writeheader()
        for plan in report['plans']:
            for berth in plan['berth_timelines']:
                for event in berth['events']:
                    writer.writerow(dict(event, plan_id=plan['plan_id']))
    call_fields = ['plan_id', 'call_id', 'vessel_name', 'berth_id', 'available_at', 'availability_basis',
                   'planned_work_start_at', 'planned_completion_at', 'carried_in', 'occupied_at_day_start',
                   'day_wait_minutes', 'day_work_minutes', 'day_weather_pause_minutes', 'day_end_state',
                   'remaining_planned_work_minutes', 'daily_estimated_demurrage_cents', 'daily_cost_status','daily_despatch_due_cents']
    with (dest / 'daily_calls.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=call_fields)
        writer.writeheader()
        for plan in report['plans']:
            for row in plan['calls']:
                writer.writerow(dict(row, plan_id=plan['plan_id']))


def main():
    parser = argparse.ArgumentParser(description='입력 계획별 하루 상세 일정 출력; 최적화·비용 계산 없음')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--date', required=True, help='KST calendar date, YYYY-MM-DD')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(args.input.read_text(encoding='utf-8'))
    report = build_daily_schedule(bundle, args.date)
    write_report(report, args.output_dir)
    print(f"DAILY_SCHEDULE_OK: {len(report['plans'])} plans / {len(report['berths'])} berths; {args.output_dir}")


if __name__ == '__main__':
    main()
