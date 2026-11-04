"""Import the user-selected contract/SOF workbook without synthetic input filling.

Only the workbook's documented allowed-hours and duration formulas are supported.
Both are independently recalculated from their literal inputs before import.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

import sqlalchemy as sa

from .bootstrap_db import exact_integer, kst_time
from .input_loader import fingerprint
from .models import berth, contract, port_call
from .validator import normalize_contract

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
EPOCH = datetime(1899, 12, 30)


def excel_time(value):
    if '-' in str(value):
        return datetime.fromisoformat(str(value))
    minutes = (Decimal(str(value))*1440).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return EPOCH+timedelta(minutes=int(minutes))


def source_rows(path, sheet_name, formula_column=None):
    """Read values as data, never execute formulas or trust their cached result."""
    with zipfile.ZipFile(path) as book:
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(x.itertext()) for x in ET.fromstring(book.read('xl/sharedStrings.xml'))]
        sheets = ET.fromstring(book.read('xl/workbook.xml')).find('s:sheets', NS)
        sheet = next(s for s in sheets if s.get('name') == sheet_name)
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in rels if r.get('Id') == rid)
        member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        matrix = []
        for row in ET.fromstring(book.read(member)).findall('.//s:sheetData/s:row', NS):
            values = {}
            for cell in row:
                letters = ''.join(x for x in cell.get('r', '') if x.isalpha())
                index = 0
                for letter in letters:
                    index = index*26+ord(letter)-64
                formula = cell.find('s:f', NS)
                if formula is not None and letters != formula_column:
                    raise ValueError(f'Unsupported source formula in {sheet_name}!{cell.get("r")}')
                if formula is not None and formula.text:
                    expected = {'K': 'IF(I2="FIXED_HOURS",J2,H2/J2*24)',
                                'G': 'ROUND((F2-E2)*1440,0)'}[formula_column]
                    if formula.text != expected:
                        raise ValueError(f'Unexpected source formula: {formula.text}')
                value = cell.find('s:v', NS)
                raw = value.text if value is not None else ''
                if cell.get('t') == 's':
                    raw = strings[int(raw)]
                elif cell.get('t') == 'inlineStr':
                    raw = ''.join(cell.find('s:is', NS).itertext())
                elif cell.get('t') == 'e':
                    raise ValueError(f'Excel error: {sheet_name}!{cell.get("r")}')
                values[index-1] = raw
            if any(v not in ('', None) for v in values.values()):
                matrix.append((int(row.get('r')), values))
        header = [matrix[0][1].get(i, '') for i in range(max(matrix[0][1])+1)]
        if not all(header) or len(set(header)) != len(header):
            raise ValueError(f'Invalid source columns: {sheet_name}')
        return [dict({key: row.get(i, '') for i,key in enumerate(header)}, _row=number)
                for number,row in matrix[1:]]


def prepare_source(workbook):
    source = fingerprint(workbook)
    contracts_raw = source_rows(workbook, '03_계약조건', 'K')
    sof = source_rows(workbook, '04_SOF이력', 'G')
    excluded = source_rows(workbook, '06_제외일')
    holidays, work = defaultdict(list), defaultdict(int)
    for row in excluded:
        holidays[row['case_id']].append(excel_time(row['excluded_date_local']).date().isoformat())
    for row in sof:
        duration = int((excel_time(row['event_end_local'])-excel_time(row['event_start_local'])).total_seconds()/60)
        if duration <= 0 or duration != exact_integer(row['duration_min_excel']):
            raise ValueError(f'SOF duration mismatch: {row["event_id"]}')
        if row['event_code'] == 'WORK':
            work[row['case_id']] += duration
    contracts, calls, manual = [], [], []
    ids = set()
    for row in contracts_raw:
        cid = row['case_id']
        if cid in ids:
            raise ValueError(f'Duplicate source contract: {cid}')
        ids.add(cid)
        basis = row['laytime_basis']
        allowance = Decimal(row['allowance_value'])
        if basis == 'FIXED_HOURS':
            hours = allowance
        elif basis == 'MT_PER_DAY' and allowance > 0:
            hours = Decimal(row['quantity_mt'])/allowance*24
        else:
            raise ValueError(f'Unsupported allowance basis: {cid} / {basis}')
        if abs(hours-Decimal(row['allowed_hours_excel'])) > Decimal('0.000001'):
            raise ValueError(f'Allowed-hours formula mismatch: {cid}')
        terms = normalize_contract(dict(contract_id=cid,
            allowed_minutes=int((hours*60).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
            daily_demurrage_usd=row['demurrage_usd_day'],notice_hours=row['notice_hours'],
            nor_valid=row['nor_valid'], early_start_if_work=row['early_start_if_work'],
            day_mode=row['day_mode'],weather_policy=row['weather_policy'],
            post_demurrage_exceptions=row['post_demurrage_exceptions'],
            despatch_policy=row['despatch_policy'],holidays=holidays[cid]))
        terms.update(source_type=row['source_type'],vessel_name=row['vessel_name'],
            original_fields={k:v for k,v in row.items() if k!='_row'},
            _source=dict(source,sheet='03_계약조건',row=row['_row']))
        contracts.append(terms)
        if work[cid] == 0:
            manual.append(dict(call_id=cid,reason='NO_SOF_WORK_EVENTS',source_row=row['_row']))
            continue
        nor = excel_time(row['nor_tender_local'])
        calls.append(dict(call_id=cid,contract_id=cid,nor_at=kst_time(nor).isoformat(),
            available_at=kst_time(nor).isoformat(), known_at=kst_time(nor.replace(hour=0,minute=0)).isoformat(),
            cargo_quantity_kg=exact_integer(Decimal(row['quantity_mt'])*1000),work_minutes=work[cid]))
    unknown = {r['case_id'] for r in sof}-ids
    if unknown:
        raise ValueError(f'SOF references missing contracts: {sorted(unknown)}')
    return dict(schema_version=1,source=source,source_type='USER_SELECTED_WORKBOOK',
        contracts=contracts,calls=calls,manual_review=manual,
        assumptions=dict(availability='NOR_PROXY',known_at='NOR_DAY_MIDNIGHT_KST',
            processing='INDEPENDENT_SUM_OF_SOF_WORK_MINUTES',berths='EIGHT_EQUIVALENT_VIRTUAL_BERTHS'),
        validation=dict(contracts=len(contracts),ready_calls=len(calls),sof_events=len(sof),
            work_minutes=sum(c['work_minutes'] for c in calls),
            allowed_hours_recalculated=True,sof_durations_recalculated=True))


def import_source(engine, payload):
    if fingerprint(payload['source']['path']) != payload['source']:
        raise ValueError('Source workbook changed after preparation')
    with engine.begin() as connection:
        if connection.scalar(sa.select(sa.func.count()).select_from(contract)) or connection.scalar(sa.select(sa.func.count()).select_from(port_call)):
            raise ValueError('Source import requires an empty input database; existing records are preserved')
        connection.execute(contract.insert(), [dict(contract_id=c['contract_id'],
            allowed_minutes=c['allowed_minutes'],demurrage_daily_cents=c['daily_demurrage_cents'],
            contract_terms=c) for c in payload['contracts']])
        connection.execute(port_call.insert(), [dict(c,**{k:kst_time(c[k]) for k in ('nor_at','available_at','known_at')}) for c in payload['calls']])
        if not connection.scalar(sa.select(sa.func.count()).select_from(berth)):
            connection.execute(berth.insert(), [dict(berth_id=f'BERTH-{i:02d}',berth_name=f'BERTH-{i:02d}',is_active=True) for i in range(1,9)])
    return payload['validation']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('workbook',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    payload=prepare_source(args.workbook)
    if args.output.exists() and json.loads(args.output.read_text()) != payload:
        raise ValueError('Existing source audit differs; preserve it and choose a new output path')
    if args.apply:
        from .database import get_engine
        import_source(get_engine(),payload)
    if not args.output.exists():
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(validation=payload['validation'],manual_review=payload['manual_review']),ensure_ascii=False))


if __name__=='__main__':
    main()
