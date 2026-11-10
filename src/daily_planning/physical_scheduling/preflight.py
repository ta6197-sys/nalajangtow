"""Report prerequisites before constructing any V3 scheduling model."""
from .compatibility import compatibility


def inspect_physical_inputs(snapshot):
    issues=[]
    pairs=[]
    for call in snapshot['calls']:
        eligible=[b['berth_id'] for b in snapshot['berths'] if not compatibility(call,b)]
        if not eligible:
            issues.append({'code':'NO_COMPATIBLE_BERTH','call_id':call['call_id']})
        pairs.extend({'call_id':call['call_id'],'berth_id':bid} for bid in eligible)
    for field in ('berthing_duration','hose_connection_duration','unberthing_duration','pilotage_windows','tug_requirements'):
        issues.append({'code':'MISSING_PHYSICAL_DATA','source_field':field,
                       'explanation':'Not supplied by the normalized V3 workbook/JSON; explicit versioned scheduling policy required.'})
    rules=snapshot['workbook_tables']['policy']['rows']
    switch=[r for r in rules if r.get('rule_key')=='switching_after_previous_work_minutes']
    if switch:
        issues.append({'code':'OPERATIONAL_POLICY_CONFLICT','source_field':'dock_4142.switching',
                       'explanation':'Workbook measures switching after cargo work; previous requirement measures after hose disconnection. Explicit policy version required.',
                       'source_rows':[r['_source_row'] for r in switch]})
    return {'status':'MISSING_PHYSICAL_DATA' if issues else 'VALID','issues':issues,
            'compatible_pairs':pairs,'operational_approval':False,'physical_scheduling_feasibility':'NOT_EVALUATED'}
