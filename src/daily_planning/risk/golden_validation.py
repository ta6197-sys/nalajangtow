"""Read-only audit of all original workbook SOF timelines and Golden labels."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import json

from contracts import contract_clock_start, settle_intervals
from .second_precision import settle_intervals_seconds
from .second_precision import POLICY_VERSION, ASSUMPTION_VERSION


def validate_settlement_config(config: dict) -> dict:
    expected = dict(schema_version=1, policy=POLICY_VERSION,
        precision='SECOND', assumption_version=ASSUMPTION_VERSION,
        simulation_only=True)
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f'Invalid settlement configuration: {key}')
    if config.get('clock_start_divergence_policy') != 'BLOCK':
        raise ValueError('Clock-start divergence policy must remain BLOCK pending source correction')
    if (type(config.get('expected_clock_start_divergences')) is not int
            or config['expected_clock_start_divergences'] < 0):
        raise ValueError('Invalid expected clock-start divergence count')
    if (type(config.get('golden_sof_event_count')) is not int
            or config['golden_sof_event_count'] < 1):
        raise ValueError('Invalid Golden SOF count')
    if (not isinstance(config.get('golden_workbook_path'), str)
            or not config['golden_workbook_path']):
        raise ValueError('Golden workbook path required')
    digest = config.get('golden_workbook_sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('Golden workbook SHA-256 required')
    return dict(config)


def load_settlement_config(path: str | Path) -> dict:
    return validate_settlement_config(json.loads(Path(path).read_text(encoding='utf-8')))


def _cents(value) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _golden_labels(path: Path) -> dict:
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book['05_정답라벨']
        iterator = iter(sheet.values)
        header = next(iterator)
        labels = {}
        for cells in iterator:
            if not cells or cells[0] in (None, ''):
                continue
            row = dict(zip(header, cells))
            cid = row['case_id']
            if cid in labels:
                raise ValueError(f'Duplicate Golden label: {cid}')
            labels[cid] = row
        return labels
    finally:
        book.close()


def validate_golden_workbook(path: str | Path) -> dict:
    """Report field-level mismatches; never modify source or expected labels."""
    from ..input_loader import fingerprint
    from ..source_workbook import prepare_source, source_rows, excel_time

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'Golden workbook missing: {path}')
    source = fingerprint(path)
    payload = prepare_source(path)
    originals = {r['case_id']: r for r in source_rows(path, '03_계약조건', 'K')}
    sof = source_rows(path, '04_SOF이력', 'G')
    labels = _golden_labels(path)
    by_case = defaultdict(list)
    for event in sof:
        by_case[event['case_id']].append(event)
    mismatches = []
    clock_divergences = []
    derived_clock_mismatches = []
    exact = 0
    fields = ('allowed_minutes', 'laytime_used_minutes', 'demurrage_minutes',
              'unused_laytime_minutes', 'pre_excluded_minutes',
              'post_excluded_minutes', 'first_demurrage_at', 'demurrage_cents',
              'despatch_cents', 'net_owner_receivable_cents')
    contract_ids = {term['contract_id'] for term in payload['contracts']}
    missing_labels = sorted(contract_ids - set(labels))
    extra_labels = sorted(set(labels) - contract_ids)
    for term in payload['contracts']:
        cid = term['contract_id']
        if cid not in originals or cid not in labels:
            continue
        row, gold = originals[cid], labels[cid]
        contract = dict(term, nor_at=excel_time(row['nor_tender_local']).isoformat())
        effective = excel_time(row['effective_start_local'])
        derived = contract_clock_start(contract, excel_time(row['operation_start_local']))
        if effective != derived:
            clock_divergences.append(dict(contract_id=cid,
                source_effective_start_at=effective.isoformat(),
                derived_effective_start_at=derived.isoformat(),
                delta_seconds=int((derived-effective).total_seconds()),
                suspected_cause='SOURCE_EFFECTIVE_START_DIFFERS_FROM_NOR_NOTICE_RULE'))
        events = [(excel_time(e['event_start_local']),
                   excel_time(e['event_end_local']), e['event_code'])
                  for e in sorted(by_case[cid], key=lambda x: Decimal(x['sequence']))]
        try:
            legacy = settle_intervals(contract, effective,
                excel_time(row['completion_local']), events)
            second = settle_intervals_seconds(contract, effective,
                excel_time(row['completion_local']), events)
        except Exception as exc:
            mismatches.append(dict(contract_id=cid, field='settlement_exception',
                expected='valid minute-aligned settlement', actual=str(exc),
                suspected_cause='SOURCE_TIMELINE_OR_CONTRACT_RULE_ERROR'))
            continue
        expected = dict(allowed_minutes=gold['allowed_minutes'],
            laytime_used_minutes=gold['laytime_used_minutes'],
            demurrage_minutes=gold['demurrage_minutes'],
            unused_laytime_minutes=gold['unused_laytime_minutes'],
            pre_excluded_minutes=gold['pre_excluded_minutes'],
            post_excluded_minutes=gold['post_excluded_minutes'],
            first_demurrage_at=(gold['first_demurrage_local'].isoformat()
                if gold['first_demurrage_local'] else None),
            demurrage_cents=_cents(gold['demurrage_charge_usd_excel']),
            despatch_cents=_cents(gold['despatch_credit_usd_excel']),
            net_owner_receivable_cents=_cents(gold['net_owner_receivable_usd_excel']))
        if effective != derived:
            try:
                derived_price = settle_intervals(contract, derived,
                    excel_time(row['completion_local']), events)
                for field in fields:
                    if derived_price[field] != expected[field]:
                        derived_clock_mismatches.append(dict(contract_id=cid,
                            field=field, expected=expected[field],
                            actual=derived_price[field],
                            suspected_cause='DERIVED_NOR_NOTICE_START_DIFFERS_FROM_SOURCE_EFFECTIVE_START'))
            except Exception as exc:
                derived_clock_mismatches.append(dict(contract_id=cid,
                    field='settlement_exception', expected='Golden settlement',
                    actual=str(exc), suspected_cause=
                    'DERIVED_NOR_NOTICE_START_DIFFERS_FROM_SOURCE_EFFECTIVE_START'))
        case_mismatch = False
        for field in fields:
            if legacy[field] != expected[field]:
                mismatches.append(dict(contract_id=cid, field=field,
                    expected=expected[field], actual=legacy[field],
                    suspected_cause='SOURCE_OR_GOLDEN_LABEL_CONFLICT'))
                case_mismatch = True
            if second[field] != legacy[field]:
                mismatches.append(dict(contract_id=cid, field=field,
                    expected=legacy[field], actual=second[field],
                    suspected_cause='SECOND_PRECISION_MINUTE_PARITY_REGRESSION'))
                case_mismatch = True
        expected_despatch_minutes = (gold['unused_laytime_minutes']
            if contract['despatch_policy']=='HALF_WORKING_TIME' else 0)
        if gold['despatch_minutes'] != expected_despatch_minutes:
            mismatches.append(dict(contract_id=cid,field='despatch_minutes',
                expected=expected_despatch_minutes,actual=gold['despatch_minutes'],
                suspected_cause='SOURCE_OR_GOLDEN_LABEL_CONFLICT'))
            case_mismatch = True
        if gold['event_count'] != len(events):
            mismatches.append(dict(contract_id=cid,field='event_count',
                expected=len(events),actual=gold['event_count'],
                suspected_cause='SOURCE_OR_GOLDEN_LABEL_CONFLICT'))
            case_mismatch = True
        if not case_mismatch:
            exact += 1
    return dict(source=source, contract_count=len(contract_ids),
        sof_event_count=len(sof), golden_label_count=len(labels),
        exact_settlement_parity_count=exact,
        missing_label_ids=missing_labels, extra_label_ids=extra_labels,
        settlement_mismatches=mismatches,
        clock_start_divergence_count=len(clock_divergences),
        clock_start_divergences=clock_divergences,
        derived_clock_golden_mismatch_count=len(derived_clock_mismatches),
        derived_clock_golden_mismatches=derived_clock_mismatches,
        settlement_parity_complete=(len(contract_ids)==144 and len(labels)==144
            and exact==144 and not mismatches and not missing_labels and not extra_labels))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Read-only Risk Golden settlement audit')
    parser.add_argument('--workbook', required=True)
    parser.add_argument('--out')
    args = parser.parse_args()
    report = validate_golden_workbook(args.workbook)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding='utf-8')
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
