"""Small local-only HTTP UI for entering a scenario and running daily A/B/C."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import threading
import traceback
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from weather_optimizer import WeatherSeries
from rolling_horizon import RULE, simulate_call

from src.daily_planning.bootstrap_db import KST, exact_integer, kst_time
from src.daily_planning.context import ROOT, engine_calls, local_time, plain, prepare_run
from src.daily_planning.database import get_engine
from src.daily_planning.daylight import daylight_window
from src.daily_planning.input_loader import xlsx_rows
from src.daily_planning.models import (berth, call_state, contract, operation_event, planning_run,
                                       port_call, schedule_assignment, schedule_candidate, schedule_commit)
from src.daily_planning.operations import advance_run, confirm_candidate
from src.daily_planning.optimization import optimize_run
from src.daily_planning.evaluation import price_until
from src.daily_planning.validator import normalize_contract, time_value

EXPERIMENT = os.environ.get('WEB_EXPERIMENT_KEY', 'web-scenario')
INDEX = (ROOT / 'web' / 'index.html').read_bytes()
TEMPLATE = ROOT / 'templates' / 'input_template.xlsx'
UPLOADS = ROOT / 'results' / 'daily' / 'uploads'
CONTRACT_COLUMNS = ('contract_id', 'allowed_minutes', 'daily_demurrage_usd',
    'notice_hours', 'nor_valid', 'early_start_if_work', 'day_mode', 'weather_policy',
    'post_demurrage_exceptions', 'despatch_policy', 'holidays')
CALL_COLUMNS = ('call_id', 'contract_id', 'nor_at', 'available_at',
    'cargo_quantity_kg', 'work_minutes', 'known_at')
MUTATION_LOCK = threading.Lock()
ENGINE = None


def run_id_for(as_of):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'ulsan-clx:{EXPERIMENT}:{as_of.isoformat()}')


def summary_for(connection, run):
    candidates = []
    config = run['run_config']
    snapshot = config.get('input_snapshot', {})
    replay_cache = {}
    replay_calls = None
    replay_weather = None
    rows = connection.execute(sa.select(schedule_candidate).where(
        schedule_candidate.c.run_id == run['run_id']).order_by(schedule_candidate.c.candidate_rank)).mappings()
    for row in rows:
        metrics = row['evaluation_metrics']
        evaluation = next(e for e in metrics['evaluations']
                          if e['evaluation_id'] == metrics['comparison_evaluation_id'])
        assignments = connection.execute(sa.select(schedule_assignment).where(
            schedule_assignment.c.candidate_id == row['candidate_id'])
            .order_by(schedule_assignment.c.start_at)).mappings()
        listed = [dict(call_id=a['call_id'], berth_id=a['berth_id'],
                start_at=a['start_at'].isoformat(), completion_at=a['completion_at'].isoformat())
                for a in assignments]
        vessel_results = []
        for item in evaluation['vessel_results']:
            clock = item.get('contract_clock_at_completion')
            if clock is None:
                key = (item['call_id'], item['work_start_at'])
                if key not in replay_cache:
                    if replay_calls is None:
                        replay_calls = {call['call_id']: call for call in engine_calls(snapshot)}
                        replay_weather = WeatherSeries.from_csv(Path(snapshot['weather']['path']))
                    replay_cache[key] = simulate_call(replay_calls[item['call_id']],
                        local_time(item['work_start_at']), replay_weather, RULE)['contract_cost']
                clock = replay_cache[key]
            vessel_results.append({**{key: item[key] for key in (
                'call_id', 'assignment_kind', 'berth_id', 'work_start_at',
                'completion_at', 'total_wait_minutes',
                'demurrage_increment_to_completion_cents')},
                'allowed_minutes': clock['allowed_minutes'],
                'laytime_used_minutes': clock['laytime_used_minutes'],
                'demurrage_minutes': clock['demurrage_minutes'],
                'clock_start_at': clock['clock_start_at']})
        candidates.append(dict(rank=row['candidate_rank'], label='ABC'[row['candidate_rank'] - 1],
            candidate_id=str(row['candidate_id']), strategy=row['strategy'],
            cost_cents=row['simulated_demurrage_cents'],
            wait_minutes=evaluation['summary']['total_wait_minutes'],
            status=metrics['solver']['solver_status'],
            gap=metrics['solver']['relative_gap'],
            weather_pause_minutes=evaluation['summary']['weather_pause_minutes'],
            berth_change_count=evaluation['summary']['berth_change_count'],
            start_shift_minutes=evaluation['summary']['start_shift_minutes'],
            compared_tentative_call_count=evaluation['summary']['compared_tentative_call_count'],
            assignments=listed,
            vessel_results=vessel_results))
    operation = config.get('operation')
    return dict(run_id=str(run['run_id']), date=run['planning_as_of'].date().isoformat(),
                solver_status=run['solver_status'], candidates=candidates,
                berthing_policy=config.get('input_snapshot', {}).get('berthing_policy', 'LEGACY_UNRESTRICTED'),
                superseded=bool(config.get('superseded_at')),
                applied_candidate_id=config.get('confirmation', {}).get('candidate_id'),
                actual_day_cents=operation.get('actual_day_demurrage_cents') if operation else None,
                accumulated_cents=operation.get('accumulated_demurrage_cents') if operation else None,
                completed_count=len(operation['completed_call_ids']) if operation else None)


def current_state():
    with ENGINE.connect() as connection:
        events = [dict(r) for r in connection.execute(sa.select(operation_event).where(
            operation_event.c.experiment_key == EXPERIMENT)
            .order_by(operation_event.c.recorded_at, operation_event.c.event_id)).mappings()]
        cancelled_ids = {r['call_id'] for r in events if r['event_kind'] == 'CALL_CANCELLED'}
        contract_rows = list(connection.execute(sa.select(contract).order_by(contract.c.contract_id)).mappings())
        contracts = [dict(id=r['contract_id'], allowed_minutes=r['allowed_minutes'],
                          daily_cents=r['demurrage_daily_cents']) for r in contract_rows]
        uploaded_hashes = sorted({r['contract_terms'].get('_source', {}).get('sha256')
            for r in contract_rows if r['contract_terms'].get('_source', {}).get('sha256')})
        calls = [dict(id=r['call_id'], contract_id=r['contract_id'], cancelled=r['call_id'] in cancelled_ids,
                      known_at=r['known_at'].isoformat(), nor_at=r['nor_at'].isoformat(),
                      available_at=r['available_at'].isoformat(), work_minutes=r['work_minutes'],
                      cargo_quantity_kg=r['cargo_quantity_kg'])
                 for r in connection.execute(sa.select(port_call).order_by(port_call.c.known_at,
                                                                           port_call.c.call_id)).mappings()]
        latest = connection.scalar(sa.select(sa.func.max(call_state.c.state_at)).where(
            call_state.c.experiment_key == EXPERIMENT))
        if latest:
            as_of = latest
        elif any(not c['cancelled'] for c in calls):
            first = min(kst_time(c['known_at']) for c in calls if not c['cancelled'])
            as_of = first.replace(hour=0, minute=0, second=0, microsecond=0)
            if first > as_of:
                as_of += timedelta(days=1)
        else:
            as_of = None
        runs = [summary_for(connection, r) for r in connection.execute(sa.select(planning_run).where(
            planning_run.c.experiment_key == EXPERIMENT).order_by(planning_run.c.planning_as_of)).mappings()]
        current = next((r for r in reversed(runs) if as_of and r['date'] == as_of.date().isoformat()
                        and not r['superseded']), None)
        status_counts = {}
        by_status = {}
        if latest:
            status_counts = dict(connection.execute(sa.select(call_state.c.status, sa.func.count()).where(
                call_state.c.experiment_key == EXPERIMENT, call_state.c.state_at == latest)
                .group_by(call_state.c.status)).all())
            by_status = dict(connection.execute(sa.select(call_state.c.call_id, call_state.c.status).where(
                call_state.c.experiment_key == EXPERIMENT,
                call_state.c.state_at == latest)).all())
        status_counts['CANCELLED'] = len(cancelled_ids)
        for c in calls:
            c['status'] = 'CANCELLED' if c['cancelled'] else by_status.get(c['id'], 'REGISTERED')
    try:
        sunrise, sunset = daylight_window(as_of) if as_of else (None, None)
    except ValueError:
        sunrise, sunset = None, None
    return plain(dict(experiment_key=EXPERIMENT, contracts=contracts, calls=calls,
                      upload_locked=bool(contract_rows or calls), as_of=as_of.isoformat() if as_of else None,
                      sunrise_at=sunrise.isoformat() if sunrise else None,
                      sunset_at=sunset.isoformat() if sunset else None,
                      latest_state_at=latest.isoformat() if latest else None,
                      uploaded_files=uploaded_hashes,
                      status_counts=status_counts, current=current,
                      history=[r for r in runs if not r['superseded']],
                      events=[dict(event_kind=e['event_kind'], call_id=e['call_id'],
                                   event_at=e['event_at'], reason=e['reason']) for e in events]))


def supersede_unconfirmed(connection, as_of):
    if as_of is None:
        return
    for run in connection.execute(sa.select(planning_run).where(
            planning_run.c.experiment_key == EXPERIMENT,
            planning_run.c.planning_as_of == as_of)).mappings():
        config = dict(run['run_config'])
        if config.get('superseded_at'):
            continue
        if config.get('confirmation'):
            raise ValueError('당일 00:00 일정이 확정되었습니다. 당일 운영을 실행한 뒤 다음 날짜의 미확정 항차를 수정하세요.')
        config['superseded_at'] = datetime.now(KST).isoformat()
        connection.execute(planning_run.update().where(planning_run.c.run_id == run['run_id'])
                           .values(run_config=config, solver_status='SUPERSEDED'))


def record_event(connection, *, call_id, as_of, kind, actor, reason, before, after, run_id=None):
    connection.execute(operation_event.insert().values(
        event_id=uuid.uuid4(), experiment_key=EXPERIMENT, call_id=call_id,
        run_id=run_id, event_at=as_of, recorded_at=datetime.now(KST),
        event_kind=kind, actor=actor, reason=reason,
        before_value=plain(dict(before)), after_value=plain(dict(after))))


def add_contract(body):
    values = {key: body.get(key) for key in (
        'contract_id', 'allowed_minutes', 'daily_demurrage_usd', 'notice_hours',
        'early_start_if_work', 'day_mode', 'weather_policy',
        'post_demurrage_exceptions', 'despatch_policy')}
    values['nor_valid'] = 'Y'
    values['holidays'] = [v.strip() for v in str(body.get('holidays', '')).split(',') if v.strip()]
    normalized = normalize_contract(values)
    with ENGINE.begin() as connection:
        connection.execute(contract.insert().values(
            contract_id=normalized['contract_id'],
            allowed_minutes=normalized['allowed_minutes'],
            demurrage_daily_cents=normalized['daily_demurrage_cents'],
            contract_terms=normalized))
    return current_state()


def add_call(body):
    cid = str(body.get('call_id', '')).strip()
    contract_id = str(body.get('contract_id', '')).strip()
    if not cid or not contract_id:
        raise ValueError('항차 ID와 계약 ID를 입력하세요.')
    nor = kst_time(str(body['nor_at']))
    available = kst_time(str(body['available_at']))
    if any(t.second or t.microsecond for t in (nor, available)) or available < nor:
        raise ValueError('시각은 분 단위로 입력하고, 작업 가능 시각은 NOR 이후여야 합니다.')
    work = int(body['work_minutes'])
    quantity = int(body['cargo_quantity_kg'])
    if work <= 0 or quantity <= 0:
        raise ValueError('작업시간과 화물량은 양수여야 합니다.')
    known = kst_time(str(body['known_at'])) if body.get('known_at') else nor.replace(hour=0, minute=0, second=0, microsecond=0)
    if known.second or known.microsecond:
        raise ValueError('공개 시각은 분 단위여야 합니다.')
    with ENGINE.begin() as connection:
        latest = connection.scalar(sa.select(sa.func.max(call_state.c.state_at)).where(
            call_state.c.experiment_key == EXPERIMENT))
        current_as_of = latest or connection.scalar(sa.select(sa.func.max(planning_run.c.planning_as_of)).where(
            planning_run.c.experiment_key == EXPERIMENT))
        if current_as_of and known < current_as_of:
            raise ValueError('이미 운영한 날짜로 공개 시각을 소급할 수 없습니다. 현재 날짜 00:00 이후의 실제 접수 시각을 입력하세요.')
        if current_as_of and known <= current_as_of:
            supersede_unconfirmed(connection, current_as_of)
        if not connection.scalar(sa.select(contract.c.contract_id).where(contract.c.contract_id == contract_id)):
            raise ValueError('먼저 해당 계약을 등록하세요.')
        connection.execute(port_call.insert().values(
            call_id=cid, contract_id=contract_id, known_at=known, nor_at=nor,
            available_at=available, cargo_quantity_kg=quantity, work_minutes=work))
    return current_state()


def parse_workbook(body):
    filename = str(body.get('filename', ''))
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('XLSX 파일을 선택하세요.')
    try:
        raw = base64.b64decode(body.get('base64', ''), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('업로드 파일을 읽지 못했습니다.') from exc
    if not raw or len(raw) > 5_000_000:
        raise ValueError('XLSX 파일은 5 MB 이하여야 합니다.')
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 15_000_000:
                raise ValueError('압축 해제 크기가 너무 큽니다.')
    except BadZipFile as exc:
        raise ValueError('올바른 XLSX 파일이 아닙니다.') from exc
    digest = hashlib.sha256(raw).hexdigest()
    source = UPLOADS / f'{digest}.xlsx'
    try:
        with TemporaryDirectory() as directory:
            temporary = Path(directory) / 'input.xlsx'
            temporary.write_bytes(raw)
            contract_rows = xlsx_rows(temporary, 'contracts')
            call_rows = xlsx_rows(temporary, 'calls')
    except (KeyError, ValueError, BadZipFile) as exc:
        raise ValueError(f'시트 읽기 실패: {exc}') from exc
    if not contract_rows or not call_rows:
        raise ValueError('contracts와 calls 시트에 각각 1행 이상의 데이터가 필요합니다.')
    if tuple(contract_rows[0]) != CONTRACT_COLUMNS or tuple(call_rows[0]) not in (CALL_COLUMNS, CALL_COLUMNS[:-1]):
        raise ValueError('템플릿의 시트명과 열 이름·순서를 그대로 사용하세요.')
    errors = []
    contracts = []
    calls = []
    contract_ids = set()
    call_ids = set()
    for number, row in enumerate(contract_rows, 2):
        try:
            normalized = normalize_contract(dict(row, holidays=[v.strip() for v in
                str(row['holidays']).split(',') if v.strip()]))
            cid = normalized['contract_id']
            if cid in contract_ids:
                raise ValueError(f'중복 계약 ID: {cid}')
            contract_ids.add(cid)
            normalized['_source'] = dict(sha256=digest, sheet='contracts', row=number)
            contracts.append(dict(contract_id=cid, allowed_minutes=normalized['allowed_minutes'],
                demurrage_daily_cents=normalized['daily_demurrage_cents'],
                contract_terms=normalized))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            errors.append(f'contracts {number}행: {exc}')
    for number, row in enumerate(call_rows, 2):
        try:
            cid = str(row['call_id']).strip()
            contract_id = str(row['contract_id']).strip()
            if not cid or not contract_id:
                raise ValueError('call_id와 contract_id는 필수입니다.')
            if cid in call_ids:
                raise ValueError(f'중복 항차 ID: {cid}')
            if contract_id not in contract_ids:
                raise ValueError(f'계약 시트에 없는 contract_id: {contract_id}')
            call_ids.add(cid)
            nor = kst_time(time_value(row['nor_at']))
            available = kst_time(time_value(row['available_at']))
            if any(t.second or t.microsecond for t in (nor, available)) or available < nor:
                raise ValueError('시각은 분 단위이며 작업 가능 시각은 NOR 이후여야 합니다.')
            quantity = exact_integer(row['cargo_quantity_kg'])
            work = exact_integer(row['work_minutes'])
            if quantity <= 0 or work <= 0:
                raise ValueError('화물량과 작업시간은 양수여야 합니다.')
            known_raw = row.get('known_at')
            known = kst_time(time_value(known_raw)) if known_raw not in (None, '') else nor.replace(hour=0, minute=0, second=0, microsecond=0)
            if known.second or known.microsecond:
                raise ValueError('known_at은 분 단위여야 합니다.')
            calls.append(dict(call_id=cid, contract_id=contract_id,
                known_at=known,
                nor_at=nor, available_at=available, cargo_quantity_kg=quantity,
                work_minutes=work, _known_supplied=known_raw not in (None, '')))
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            errors.append(f'calls {number}행: {exc}')
    if errors:
        raise ValueError('\n'.join(errors[:20]))
    return filename, raw, digest, source, contracts, calls


def save_workbook_source(filename, raw, digest, source, contracts, calls):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if source.exists() and source.read_bytes() != raw:
        raise ValueError('업로드 해시 충돌이 발생했습니다.')
    created = not source.exists()
    if created:
        source.write_bytes(raw)
    manifest = dict(filename=filename, sha256=digest, contracts=len(contracts),
                    calls=len(calls), contract_sheet='contracts', call_sheet='calls')
    (UPLOADS / f'{digest}.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


def upload_workbook(body):
    filename, raw, digest, source, contracts, calls = parse_workbook(body)
    calls = [{k: v for k, v in item.items() if k != '_known_supplied'} for item in calls]
    with ENGINE.begin() as connection:
        if connection.scalar(sa.select(sa.func.count()).select_from(contract)) or connection.scalar(
                sa.select(sa.func.count()).select_from(port_call)):
            raise ValueError('이미 입력이 있습니다. 수정한 XLSX는 다시 업로드 미리보기를 사용하세요.')
        connection.execute(contract.insert(), contracts)
        connection.execute(port_call.insert(), calls)
    save_workbook_source(filename, raw, digest, source, contracts, calls)
    return current_state()


def workbook_changes(connection, contracts, calls, as_of):
    existing_contracts = {r['contract_id']: r for r in connection.execute(sa.select(contract)).mappings()}
    existing_calls = {r['call_id']: r for r in connection.execute(sa.select(port_call)).mappings()}
    cancelled = {r[0] for r in connection.execute(sa.select(operation_event.c.call_id).where(
        operation_event.c.experiment_key == EXPERIMENT,
        operation_event.c.event_kind == 'CALL_CANCELLED'))}
    result = []
    for row in contracts:
        old = existing_contracts.get(row['contract_id'])
        if old:
            for key in CONTRACT_COLUMNS:
                incoming = row['contract_terms'][key]
                saved = old['contract_terms'].get(key)
                if str(incoming) != str(saved):
                    raise ValueError(f"기존 계약 {row['contract_id']}의 {key} 값이 변경되었습니다. 기존 계약은 고정입니다.")
        else:
            result.append(dict(kind='CONTRACT_ADDED', id=row['contract_id'], before=None, after=row))
    known_contracts = set(existing_contracts) | {r['contract_id'] for r in contracts}
    confirmed_today = False
    if as_of:
        for run in connection.execute(sa.select(planning_run).where(
                planning_run.c.experiment_key == EXPERIMENT,
                planning_run.c.planning_as_of == as_of)).mappings():
            if run['run_config'].get('confirmation') and not run['run_config'].get('operation'):
                confirmed_today = True
    for raw in calls:
        row = dict(raw)
        supplied = row.pop('_known_supplied')
        cid = row['call_id']
        if row['contract_id'] not in known_contracts:
            raise ValueError(f'{cid}: 등록되지 않은 계약입니다.')
        old = existing_calls.get(cid)
        if old:
            if cid in cancelled:
                raise ValueError(f'{cid}: 취소된 항차는 XLSX로 다시 활성화할 수 없습니다.')
            if supplied and row['known_at'] != old['known_at']:
                raise ValueError(f'{cid}: 이미 접수된 항차의 known_at은 바꿀 수 없습니다.')
            row['known_at'] = old['known_at']
            fields = ('contract_id', 'nor_at', 'available_at', 'cargo_quantity_kg', 'work_minutes')
            if all(row[key] == old[key] for key in fields):
                continue
            if as_of:
                state = connection.execute(sa.select(call_state).where(
                    call_state.c.experiment_key == EXPERIMENT,
                    call_state.c.call_id == cid,
                    call_state.c.state_at == as_of)).mappings().first()
                if state and state['status'] in ('IN_PROGRESS', 'COMPLETED'):
                    raise ValueError(f'{cid}: 진행 중·완료 항차는 변경할 수 없습니다.')
                if confirmed_today and old['known_at'] <= as_of:
                    raise ValueError(f'{cid}: 당일 확정 뒤에는 당일 운영을 먼저 실행해야 합니다.')
                committed = connection.scalar(sa.select(schedule_commit.c.commit_id).join(
                    schedule_assignment, schedule_commit.c.assignment_id == schedule_assignment.c.assignment_id)
                    .where(schedule_commit.c.experiment_key == EXPERIMENT,
                           schedule_commit.c.status.in_(('LOCKED', 'IN_PROGRESS')),
                           schedule_assignment.c.call_id == cid))
                if committed:
                    raise ValueError(f'{cid}: 확정된 항차는 변경할 수 없습니다.')
            result.append(dict(kind='CALL_UPDATED', id=cid, before=dict(old), after=row))
        else:
            if as_of and row['known_at'] < as_of:
                raise ValueError(f'{cid}: 공개 시각을 현재 날짜보다 이전으로 소급할 수 없습니다.')
            if confirmed_today and row['known_at'] <= as_of:
                raise ValueError(f'{cid}: 당일 확정 뒤에는 당일 운영을 먼저 실행해야 합니다.')
            result.append(dict(kind='CALL_ADDED', id=cid, before=None, after=row))
    return result


def workbook_preview(body):
    filename, raw, digest, source, contracts, calls = parse_workbook(body)
    state = current_state()
    as_of = kst_time(state['as_of']) if state['as_of'] else None
    with ENGINE.connect() as connection:
        changes = workbook_changes(connection, contracts, calls, as_of)
    payload = plain(dict(sha256=digest, as_of=as_of, changes=changes))
    token = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return dict(filename=filename, sha256=digest, token=token,
                changes=[dict(kind=c['kind'], id=c['id'], before=plain(c['before']),
                              after=plain(c['after'])) for c in changes],
                missing_calls_kept=True, existing_contracts_unchanged=True)


def apply_workbook(body):
    preview = workbook_preview(body)
    if str(body.get('token', '')) != preview['token']:
        raise ValueError('미리보기 이후 입력 또는 운영 상태가 바뀌었습니다. 다시 미리보기를 확인하세요.')
    filename, raw, digest, source, contracts, calls = parse_workbook(body)
    as_of = kst_time(current_state()['as_of']) if current_state()['as_of'] else None
    with ENGINE.begin() as connection:
        changes = workbook_changes(connection, contracts, calls, as_of)
        payload = plain(dict(sha256=digest, as_of=as_of, changes=changes))
        token = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if token != preview['token']:
            raise ValueError('미리보기 이후 DB가 바뀌었습니다. 다시 확인하세요.')
        if as_of and any(c['kind'].startswith('CALL_') and c['after']['known_at'] <= as_of for c in changes):
            supersede_unconfirmed(connection, as_of)
        for item in changes:
            if item['kind'] == 'CONTRACT_ADDED':
                connection.execute(contract.insert().values(**item['after']))
            elif item['kind'] == 'CALL_ADDED':
                connection.execute(port_call.insert().values(**item['after']))
            else:
                after = item['after']
                connection.execute(port_call.update().where(port_call.c.call_id == item['id']).values(**{
                    key: after[key] for key in ('contract_id', 'nor_at', 'available_at',
                                               'cargo_quantity_kg', 'work_minutes')}))
                if as_of:
                    latest = connection.execute(sa.select(call_state).where(
                        call_state.c.experiment_key == EXPERIMENT,
                        call_state.c.call_id == item['id'],
                        call_state.c.state_at == as_of)).mappings().first()
                    if latest:
                        terms = connection.execute(sa.select(contract).where(
                            contract.c.contract_id == after['contract_id'])).mappings().one()
                        call = engine_calls({'contracts': [dict(terms)], 'calls': [after]})[0]
                        weather = WeatherSeries.from_csv(ROOT / 'input/weather_for_optimizer_30min.csv')
                        priced = price_until(call, None, local_time(as_of), weather)
                        clock = dict(latest['contract_clock_state'],
                                     remaining_work_minutes=after['work_minutes'],
                                     input_revised_at=datetime.now(KST).isoformat())
                        connection.execute(call_state.update().where(call_state.c.state_id == latest['state_id'])
                            .values(status='WAITING' if after['available_at'] <= as_of else 'PLANNED',
                                    remaining_work_kg=after['cargo_quantity_kg'],
                                    accrued_demurrage_cents=priced['demurrage_cents'],
                                    contract_clock_state=clock))
                record_event(connection, call_id=item['id'], as_of=as_of or after['known_at'],
                    kind='CALL_UPDATED', actor='XLSX', reason=f'XLSX 재업로드: {filename}',
                    before=item['before'], after=after)
    save_workbook_source(filename, raw, digest, source, contracts, calls)
    return current_state()


def current_workbook_bytes():
    """Export the current DB input in the same editable two-sheet format."""
    with ENGINE.connect() as connection:
        contracts = list(connection.execute(sa.select(contract).order_by(contract.c.contract_id)).mappings())
        cancelled = {r[0] for r in connection.execute(sa.select(operation_event.c.call_id).where(
            operation_event.c.experiment_key == EXPERIMENT,
            operation_event.c.event_kind == 'CALL_CANCELLED'))}
        calls = [r for r in connection.execute(sa.select(port_call).order_by(port_call.c.call_id)).mappings()
                 if r['call_id'] not in cancelled]
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ET.register_namespace('x', namespace)
    sheets = {
        'xl/worksheets/sheet1.xml': (CONTRACT_COLUMNS, [dict(r['contract_terms']) for r in contracts]),
        'xl/worksheets/sheet2.xml': (CALL_COLUMNS, [dict(r) for r in calls]),
    }
    output = io.BytesIO()
    with ZipFile(TEMPLATE) as original, ZipFile(output, 'w') as result:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename in sheets:
                columns, rows = sheets[item.filename]
                root = ET.fromstring(data)
                sheet_data = root.find(f'{{{namespace}}}sheetData')
                sheet_data.clear()
                for number, values in enumerate([dict(zip(columns, columns)), *rows], 1):
                    row = ET.SubElement(sheet_data, f'{{{namespace}}}row', {'r': str(number)})
                    for index, key in enumerate(columns):
                        value = values.get(key, '')
                        if key == 'holidays' and isinstance(value, list):
                            value = ','.join(value)
                        if isinstance(value, datetime):
                            value = value.isoformat()
                        if value is None:
                            value = ''
                        letters = ''
                        n = index + 1
                        while n:
                            n, rem = divmod(n-1, 26)
                            letters = chr(65+rem) + letters
                        cell = ET.SubElement(row, f'{{{namespace}}}c',
                                             {'r': f'{letters}{number}', 't': 'inlineStr'})
                        inline = ET.SubElement(cell, f'{{{namespace}}}is')
                        ET.SubElement(inline, f'{{{namespace}}}t').text = str(value)
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            result.writestr(item, data)
    return output.getvalue()


def change_call(body, *, cancel=False):
    cid = str(body.get('call_id', '')).strip()
    reason = str(body.get('reason', '')).strip()
    if not cid or not reason:
        raise ValueError('항차 ID와 변경·취소 사유를 입력하세요.')
    state = current_state()
    as_of = kst_time(state['as_of']) if state['as_of'] else None
    with ENGINE.begin() as connection:
        old = connection.execute(sa.select(port_call).where(port_call.c.call_id == cid)
                                 .with_for_update()).mappings().first()
        if old is None:
            raise ValueError('해당 항차가 없습니다.')
        if connection.scalar(sa.select(operation_event.c.event_id).where(
                operation_event.c.experiment_key == EXPERIMENT,
                operation_event.c.call_id == cid,
                operation_event.c.event_kind == 'CALL_CANCELLED')):
            raise ValueError('이미 취소된 항차입니다.')
        latest_state = connection.execute(sa.select(call_state).where(
            call_state.c.experiment_key == EXPERIMENT,
            call_state.c.call_id == cid,
            call_state.c.state_at == as_of).with_for_update()).mappings().first() if as_of else None
        if latest_state and latest_state['status'] in ('IN_PROGRESS', 'COMPLETED'):
            raise ValueError('진행 중·완료 항차는 이 화면에서 변경할 수 없습니다.')
        if as_of:
            selected = connection.execute(sa.select(planning_run).where(
                planning_run.c.experiment_key == EXPERIMENT,
                planning_run.c.planning_as_of == as_of)).mappings()
            for run in selected:
                if run['run_config'].get('confirmation') and not run['run_config'].get('operation'):
                    if old['known_at'] <= as_of:
                        raise ValueError('오늘 일정이 확정되었습니다. 당일 운영을 실행한 뒤 다음 날짜의 미확정 항차를 수정하세요.')
            if connection.scalar(sa.select(schedule_commit.c.commit_id).join(
                    schedule_assignment, schedule_commit.c.assignment_id == schedule_assignment.c.assignment_id)
                    .where(schedule_commit.c.experiment_key == EXPERIMENT,
                           schedule_commit.c.status.in_(('LOCKED', 'IN_PROGRESS')),
                           schedule_assignment.c.call_id == cid)):
                raise ValueError('확정된 항차는 일반 수정·취소 대상이 아닙니다.')
            supersede_unconfirmed(connection, as_of)
        if cancel:
            record_event(connection, call_id=cid, as_of=as_of or old['known_at'],
                         kind='CALL_CANCELLED', actor='USER', reason=reason,
                         before=old, after={'status': 'CANCELLED'})
        else:
            nor = kst_time(str(body.get('nor_at', old['nor_at'])))
            available = kst_time(str(body.get('available_at', old['available_at'])))
            quantity = exact_integer(body.get('cargo_quantity_kg', old['cargo_quantity_kg']))
            work = exact_integer(body.get('work_minutes', old['work_minutes']))
            contract_id = str(body.get('contract_id', old['contract_id'])).strip()
            if any(x.second or x.microsecond for x in (nor, available)) or available < nor:
                raise ValueError('NOR·작업 가능 시각은 분 단위이고 작업 가능 시각은 NOR 이후여야 합니다.')
            if quantity <= 0 or work <= 0:
                raise ValueError('화물량·작업분은 양수여야 합니다.')
            terms = connection.execute(sa.select(contract).where(contract.c.contract_id == contract_id)).mappings().first()
            if not terms:
                raise ValueError('등록되지 않은 계약 ID입니다.')
            after = dict(old, contract_id=contract_id, nor_at=nor,
                         available_at=available, cargo_quantity_kg=quantity, work_minutes=work)
            connection.execute(port_call.update().where(port_call.c.call_id == cid).values(
                contract_id=contract_id, nor_at=nor, available_at=available,
                cargo_quantity_kg=quantity, work_minutes=work))
            if latest_state:
                call = engine_calls({'contracts': [dict(terms)], 'calls': [after]})[0]
                weather = WeatherSeries.from_csv(ROOT / 'input/weather_for_optimizer_30min.csv')
                priced = price_until(call, None, local_time(as_of), weather)
                clock = dict(latest_state['contract_clock_state'], remaining_work_minutes=work,
                             input_revised_at=datetime.now(KST).isoformat())
                connection.execute(call_state.update().where(call_state.c.state_id == latest_state['state_id'])
                    .values(status='WAITING' if available <= as_of else 'PLANNED',
                            remaining_work_kg=quantity,
                            accrued_demurrage_cents=priced['demurrage_cents'],
                            contract_clock_state=clock))
            record_event(connection, call_id=cid, as_of=as_of or old['known_at'],
                         kind='CALL_UPDATED', actor='USER', reason=reason,
                         before=old, after=after)
    return current_state()


def edit_call(body):
    return change_call(body)


def cancel_call(body):
    return change_call(body, cancel=True)



def plan_next(_body):
    state = current_state()
    if not state['as_of']:
        raise ValueError('계약과 항차를 먼저 등록하세요.')
    if state['status_counts'].get('COMPLETED', 0) + state['status_counts'].get('CANCELLED', 0) >= len(state['calls']):
        raise ValueError('등록된 모든 항차가 완료·취소되었습니다. 새 항차를 등록하면 다시 계획할 수 있습니다.')
    as_of = kst_time(state['as_of'])
    if state['current'] and state['current']['candidates']:
        return state
    rid = run_id_for(as_of)
    with ENGINE.connect() as connection:
        if connection.scalar(sa.select(planning_run.c.run_id).where(planning_run.c.run_id == rid)):
            rid = uuid.uuid4()
    prepare_run(ENGINE, EXPERIMENT, as_of, run_id=rid, top_k=3)
    optimize_run(ENGINE, rid, time_limit=180)
    return current_state()


def apply_candidate(body):
    label = str(body.get('label', ''))
    if label not in ('A', 'B', 'C'):
        raise ValueError('A/B/C 중 표시된 후보를 선택하세요.')
    state = current_state()
    current = state['current']
    if not current or not current['candidates']:
        raise ValueError('먼저 후보를 계산하세요.')
    selected = next((c for c in current['candidates'] if c['label'] == label), None)
    if selected is None:
        raise ValueError('해당 후보가 비어 있습니다.')
    confirm_candidate(ENGINE, uuid.UUID(selected['candidate_id']))
    return current_state()


def operate_day(_body):
    state = current_state()
    current = state['current']
    if not current or not current['applied_candidate_id']:
        raise ValueError('먼저 당일 후보를 확정하세요.')
    advance_run(ENGINE, uuid.UUID(current['run_id']))
    updated = current_state()
    if updated['status_counts'].get('COMPLETED', 0) + updated['status_counts'].get('CANCELLED', 0) >= len(updated['calls']):
        return updated
    try:
        return plan_next({})
    except Exception as exc:
        # The 24h step has already committed. Return the advanced state so the
        # browser never reports the whole action as failed or retries the day.
        updated['next_plan_error'] = str(exc)
        return updated


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data):
        raw = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(INDEX)))
            self.end_headers()
            self.wfile.write(INDEX)
        elif path == '/input-template.xlsx':
            raw = TEMPLATE.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition', 'attachment; filename="input_template.xlsx"')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif path == '/current-input.xlsx':
            raw = current_workbook_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            self.send_header('Content-Disposition', 'attachment; filename="current_clx_input.xlsx"')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif path == '/api/status':
            try:
                self.respond(200, current_state())
            except Exception as exc:
                traceback.print_exc()
                self.respond(500, {'error': str(exc)})
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        actions = {'/api/contract': add_contract, '/api/call': add_call,
                   '/api/upload': upload_workbook, '/api/upload/preview': workbook_preview,
                   '/api/upload/apply': apply_workbook, '/api/plan': plan_next,
                   '/api/apply': apply_candidate, '/api/operate': operate_day,
                   '/api/call/edit': edit_call, '/api/call/cancel': cancel_call}
        action = actions.get(urlparse(self.path).path)
        if action is None:
            return self.respond(404, {'error': 'Not found'})
        if not MUTATION_LOCK.acquire(blocking=False):
            return self.respond(409, {'error': '다른 작업이 진행 중입니다.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > (7_000_000 if self.path.startswith('/api/upload') else 65536):
                raise ValueError('입력 크기가 너무 큽니다.')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('JSON 객체가 필요합니다.')
            self.respond(200, action(body))
        except (ValueError, KeyError, TypeError, IntegrityError) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self.respond(500, {'error': str(exc)})
        finally:
            MUTATION_LOCK.release()


def main():
    global ENGINE
    ENGINE = get_engine()
    with ENGINE.begin() as connection:
        if not connection.scalar(sa.select(sa.func.count()).select_from(berth)):
            connection.execute(berth.insert(), [dict(berth_id=f'BERTH-{i:02d}',
                berth_name=f'BERTH-{i:02d}', is_active=True) for i in range(1, 9)])
    server = ThreadingHTTPServer(('0.0.0.0', 8765), Handler)
    print('Local web: http://127.0.0.1:8765', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
