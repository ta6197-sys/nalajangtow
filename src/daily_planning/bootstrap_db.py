"""Import the existing synthetic dataset without rewriting existing rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from .input_loader import bootstrap
from .models import berth, contract, port_call

KST = timezone(timedelta(hours=9))


def kst_time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


def exact_integer(value):
    value = Decimal(str(value))
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError('Input must be an exact integer')
    return int(value)


def remaining_kg(total_kg, remaining_minutes, work_minutes):
    """Integer ceiling without floating point or cumulative rounding drift."""
    if any(type(x) is not int for x in (total_kg, remaining_minutes, work_minutes)):
        raise ValueError('Quantity and work minutes must be integers')
    if total_kg < 0 or work_minutes <= 0 or not 0 <= remaining_minutes <= work_minutes:
        raise ValueError('Invalid quantity or work balance')
    return (total_kg * remaining_minutes + work_minutes - 1) // work_minutes


def import_existing_inputs(engine):
    contracts, calls, berths, sources = bootstrap()
    rows = [
        (contract, [dict(
            contract_id=c['contract_id'], allowed_minutes=c['allowed_minutes'],
            demurrage_daily_cents=exact_integer(Decimal(c['daily_demurrage_usd']) * 100),
            contract_terms={k: v for k, v in c.items() if k != 'contract_id'},
        ) for c in contracts]),
        (berth, [dict(berth_id=b, berth_name=b, is_active=True) for b in berths]),
        (port_call, [dict(
            call_id=c['call_id'], contract_id=c['contract_id'],
            known_at=kst_time(c['nor_at']).replace(hour=0, minute=0, second=0, microsecond=0),
            nor_at=kst_time(c['nor_at']), available_at=kst_time(c['available_at']),
            cargo_quantity_kg=exact_integer(c['cargo_quantity_kg']),
            work_minutes=c['processing_minutes'],
        ) for c in calls]),
    ]
    counts = {}
    with engine.begin() as connection:
        # Serialize concurrent bootstrap attempts; a differing existing row fails
        # the entire import rather than silently replacing source data.
        connection.execute(sa.text('SELECT pg_advisory_xact_lock(17012026)'))
        for table, values in rows:
            inserted = 0
            key = list(table.primary_key.columns)[0]
            for value in values:
                result = connection.execute(insert(table).values(**value).on_conflict_do_nothing()
                                            .returning(key)).first()
                inserted += result is not None
                stored = connection.execute(sa.select(table).where(key == value[key.name])).mappings().one()
                if dict(stored) != value:
                    raise ValueError(f'Existing {table.name} differs: {value[key.name]}')
            counts[table.name] = {'source_rows': len(values), 'inserted': inserted}
    return dict(status='PASS', counts=counts, sources=sources,
                known_at_policy='NOR_DAY_MIDNIGHT_KST', weather_information_policy='PERFECT_INFORMATION',
                availability_source='SCENARIO_NOR_PROXY',
                remaining_quantity_policy='CEIL_PROPORTIONAL_WITH_EXACT_WORK_MINUTES')
