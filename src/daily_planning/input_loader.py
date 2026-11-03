"""Read immutable source files and tabular daily CSV, JSON and XLSX inputs."""
from __future__ import annotations

import csv
import hashlib
import json
import posixpath
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def fingerprint(path):
    path = Path(path)
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size}


def xlsx_rows(path, sheet_name=None):
    """Values only; reject formulas rather than trusting an old cached result."""
    with zipfile.ZipFile(path) as book:
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(x.itertext()) for x in ET.fromstring(book.read('xl/sharedStrings.xml'))]
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        sheets = list(workbook.find('s:sheets', NS))
        sheet = next((s for s in sheets if s.get('name') == sheet_name), None) if sheet_name else sheets[0]
        if sheet is None:
            raise ValueError(f'Excel sheet not found: {sheet_name}')
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        target = next(x.get('Target') for x in rels if x.get('Id') == rid)
        member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        matrix = []
        for row in ET.fromstring(book.read(member)).findall('.//s:sheetData/s:row', NS):
            values = {}
            for cell in row:
                ref = cell.get('r', '')
                letters = ''.join(x for x in ref if x.isalpha())
                index = 0
                for letter in letters:
                    index = index * 26 + ord(letter) - 64
                if cell.find('s:f', NS) is not None:
                    raise ValueError(f'{path}: formula in {ref}; supply literal values')
                value = cell.find('s:v', NS)
                raw = value.text if value is not None else ''
                if cell.get('t') == 's':
                    raw = strings[int(raw)]
                elif cell.get('t') == 'inlineStr':
                    raw = ''.join(cell.find('s:is', NS).itertext())
                elif cell.get('t') == 'e':
                    raise ValueError(f'{path}: Excel error in {ref}: {raw}')
                values[index - 1] = raw
            if any(v not in ('', None) for v in values.values()):
                matrix.append(values)
        if not matrix:
            return []
        header = [matrix[0].get(i, '') for i in range(max(matrix[0]) + 1)]
        if not all(header) or len(header) != len(set(header)):
            raise ValueError(f'{path}: blank or duplicate column names')
        return [{key: row.get(i, '') for i, key in enumerate(header)} for row in matrix[1:]]


def read_records(path, kind=None):
    path = Path(path)
    if path.suffix.lower() == '.xlsx':
        return xlsx_rows(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(f'{path}: missing or duplicate column names')
            records = list(reader)
            if any(None in row for row in records):
                raise ValueError(f'{path}: more values than column names')
            return records
    if path.suffix.lower() == '.json':
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        records = value.get(kind, value.get('rows')) if isinstance(value, dict) else value
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError(f'{path}: expected a list of records')
        return records
    raise ValueError(f'Unsupported input type: {path.suffix}')


def daily_inputs(directory):
    directory = Path(directory)
    data, sources = {}, []
    for kind in ('new_contracts', 'new_calls', 'operational_updates', 'weather_forecast'):
        matches = [directory / (kind + suffix) for suffix in ('.csv', '.xlsx', '.json')
                   if (directory / (kind + suffix)).exists()]
        if len(matches) > 1:
            raise ValueError(f'{kind}: multiple input files; provide one format per input')
        data[kind] = read_records(matches[0], kind) if matches else []
        if matches:
            source = fingerprint(matches[0])
            sources.append(source)
            data[kind] = [dict(row, _source={**source, 'row': i + 2})
                          for i, row in enumerate(data[kind])]
    return data, sources


def bootstrap():
    model_path = ROOT / 'input/model_input_140.json'
    contracts_path = ROOT / 'simulation/inputs/normalized/contracts.csv'
    holiday_path = ROOT / 'simulation/inputs/normalized/holidays.csv'
    model = json.loads(model_path.read_text())
    holidays = {}
    for row in read_records(holiday_path):
        holidays.setdefault(row['case_id'], []).append(row['excluded_date_local'])
    contracts = []
    for raw in read_records(contracts_path):
        allowed = Decimal(raw['allowed_hours_excel']) * 60
        allowed = int(allowed.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        contracts.append(dict(contract_id=raw['case_id'], allowed_minutes=allowed,
                              daily_demurrage_usd=raw['demurrage_usd_day'],
                              notice_hours=raw['notice_hours'], early_start_if_work=raw['early_start_if_work'],
                              nor_valid=raw['nor_valid'], day_mode=raw['day_mode'],
                              weather_policy=raw['weather_policy'],
                              post_demurrage_exceptions=raw['post_demurrage_exceptions'],
                              despatch_policy=raw['despatch_policy'], holidays=holidays.get(raw['case_id'], []),
                              source_type='SYNTHETIC', verification_status='SCENARIO', original_fields=raw,
                              _source=fingerprint(contracts_path)))
    calls = []
    raw_by_id = {c['contract_id']: c['original_fields'] for c in contracts}
    for c in model['calls']:
        raw = raw_by_id[c['call_id']]
        calls.append(dict(call_id=c['call_id'], contract_id=c['call_id'], vessel_id=c['vessel_id'],
                          vessel_name=c['vessel_name'], nor_at=c['contract']['nor_at'],
                          available_at=c['available_at'], availability_basis='SCENARIO_NOR_PROXY',
                          processing_minutes=c['processing_minutes'], cargo_type=raw['cargo'],
                          cargo_quantity_kg=str(Decimal(raw['quantity_mt']) * 1000),
                          compatible_berths=model['berths'], source_type='SYNTHETIC',
                          verification_status='SCENARIO', _source=fingerprint(model_path)))
    return contracts, calls, model['berths'], [fingerprint(p) for p in (model_path, contracts_path, holiday_path)]
