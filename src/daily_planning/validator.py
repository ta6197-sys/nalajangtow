"""Normalize exact minutes/amounts and collect actionable per-record errors."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from contracts import validate_contract
from weather_optimizer import at_datetime

KST = timezone(timedelta(hours=9))
CONTRACT_FIELDS = ('allowed_minutes', 'daily_demurrage_usd', 'notice_hours', 'early_start_if_work',
                   'nor_valid', 'day_mode', 'weather_policy', 'post_demurrage_exceptions', 'despatch_policy')
STATUSES = {'PLANNED', 'WAITING', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'}


def time_value(value):
    if isinstance(value, (int, float, Decimal)):
        minutes = Decimal(str(value)) * 1440
        nearest = minutes.to_integral_value()
        if abs(minutes - nearest) > Decimal('0.00001'):
            raise ValueError('Excel timestamp must resolve to whole minutes')
        return datetime(1899, 12, 30) + timedelta(minutes=int(nearest))
    text = str(value).strip()
    # XLSX numeric cells are deliberately read as strings.
    if text and text.replace('.', '', 1).isdigit() and len(text.split('.')[0]) >= 4:
        return time_value(Decimal(text))
    return at_datetime(text)


def timestamp(value):
    return time_value(value).isoformat()


def integer(value, name, minimum=0):
    x = Decimal(str(value))
    if not x.is_finite() or x != x.to_integral_value() or x < minimum:
        raise ValueError(f'{name}: expected integer >= {minimum}')
    return int(x)


def list_value(value):
    if value in ('', None):
        return []
    if isinstance(value, list):
        return value
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError('Expected JSON array')
    return parsed


def normalize_contract(raw):
    c = deepcopy(raw)
    if not str(c.get('contract_id', '')).strip():
        raise ValueError('contract_id is required')
    c['contract_id'] = str(c['contract_id']).strip()
    for name in CONTRACT_FIELDS:
        if c.get(name) in ('', None):
            raise ValueError(f'{name} is required')
    c['allowed_minutes'] = integer(c['allowed_minutes'], 'allowed_minutes')
    for name in ('daily_demurrage_usd', 'notice_hours'):
        val = Decimal(str(c[name]))
        if not val.is_finite() or val < 0:
            raise ValueError(f'{name}: expected finite nonnegative number')
        c[name] = str(val.normalize())
    # Keep the legacy USD/day input while also exposing exact integer cents.
    c['daily_demurrage_cents'] = integer(Decimal(c['daily_demurrage_usd']) * 100, 'daily_demurrage_cents')
    c['holidays'] = [time_value(v).date().isoformat() if 'T' in str(v) else str(v)[:10]
                     for v in list_value(c.get('holidays', []))]
    for value in c['holidays']:
        datetime.strptime(value, '%Y-%m-%d')
    validate_contract(c)
    return c


def normalize_call(raw, contracts, berths, mode):
    c = deepcopy(raw)
    for name in ('call_id', 'contract_id', 'vessel_id', 'processing_minutes', 'nor_at'):
        if c.get(name) in ('', None):
            raise ValueError(f'{name} is required')
    for name in ('call_id', 'contract_id', 'vessel_id'):
        c[name] = str(c[name]).strip()
        if not c[name]:
            raise ValueError(f'{name} is required')
    if c['contract_id'] not in contracts:
        raise ValueError(f'Unknown contract_id: {c["contract_id"]}')
    c['processing_minutes'] = integer(c['processing_minutes'], 'processing_minutes', 1)
    c['nor_at'] = timestamp(c['nor_at'])
    for name in ('eta', 'ata'):
        if c.get(name) not in ('', None):
            c[name] = timestamp(c[name])
    if c.get('available_at') not in ('', None):
        c['available_at'] = timestamp(c['available_at'])
        if c.get('availability_basis') not in ('CONFIRMED_READY_AT', 'SCENARIO_NOR_PROXY'):
            raise ValueError('available_at requires CONFIRMED_READY_AT or SCENARIO_NOR_PROXY availability_basis')
        if mode != 'simulation' and c['availability_basis'] == 'SCENARIO_NOR_PROXY':
            raise ValueError('NOR proxy is only permitted in simulation mode')
    elif c.get('eta'):
        c['available_at'], c['availability_basis'] = c['eta'], 'ETA'
    elif mode == 'simulation':
        c['available_at'], c['availability_basis'] = c['nor_at'], 'SCENARIO_NOR_PROXY'
    else:
        raise ValueError('eta or confirmed available_at is required; NOR is not ETA')
    if time_value(c['available_at']) < time_value(c['nor_at']):
        # The current contract engine needs a known valid NOR before work can be priced.
        c['available_at'] = c['nor_at']
        c['availability_basis'] += '_AND_VALID_NOR'
    c['compatible_berths'] = list_value(c.get('compatible_berths', berths))
    if not c['compatible_berths'] or not set(c['compatible_berths']) <= set(berths):
        raise ValueError('compatible_berths must be a nonempty subset of known berths')
    if c.get('cargo_quantity_kg') not in ('', None):
        c['cargo_quantity_kg'] = integer(c['cargo_quantity_kg'], 'cargo_quantity_kg', 1)
    c.setdefault('source_type', 'USER_SCENARIO' if mode == 'simulation' else 'USER_OPERATION')
    c.setdefault('verification_status', 'SCENARIO' if mode == 'simulation' else 'UNVERIFIED')
    return c


def engine_call(call, contracts):
    contract = deepcopy(contracts[call['contract_id']])
    contract['nor_at'] = call['nor_at']
    return dict(call, contract=contract)


def clean_content(record):
    return {k: v for k, v in record.items() if k not in ('_source', '_version')}


def combine(existing, incoming, normalizer, key, errors):
    merged = deepcopy(existing)
    changes, duplicate_ids, seen = [], [], set()
    for index, raw in enumerate(incoming, 2):
        identifier = raw.get(key, '')
        try:
            value = normalizer(raw)
            identifier = value[key]
            if identifier in seen:
                raise ValueError(f'duplicate {key} within input: {identifier}')
            seen.add(identifier)
            if identifier in merged:
                if clean_content(merged[identifier]) != clean_content(value):
                    raise ValueError(f'{key} conflicts with stored data: {identifier}; use explicit update flow')
                duplicate_ids.append(identifier)
            else:
                merged[identifier] = value
                changes.append(value)
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors.append({'input': key, 'id': identifier, 'row': index,
                           'source': raw.get('_source'), 'message': str(exc)})
    return merged, changes, duplicate_ids
