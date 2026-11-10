"""Lossless source retention plus explicit typed, unit-normalized call projection."""
import copy
from fractions import Fraction
from .provenance import VERSION
from .validator import timestamp


def normalize(bundle):
    source=bundle['source']; tables=bundle['tables']
    profiles={};contracts={}
    for row in tables['profiles']['rows']: profiles.setdefault(row.get('case_id'),[]).append(row)
    for row in tables['contracts']['rows']: contracts.setdefault(row.get('case_id'),[]).append(row)
    calls=[]
    for raw in source.get('cargo_calls',[]):
        row=copy.deepcopy(raw);cid=row.get('call_id');p=profiles.get(cid,[]);c=contracts.get(cid,[])
        row['contract_id']=cid if len(c)==1 and c[0].get('vessel_id')==row.get('vessel_id') else None
        if len(c)==1:
            try:
                contract=c[0];allow=Fraction(str(contract['allowance_value']))
                if contract['laytime_basis']=='MT_PER_DAY':allow=Fraction(str(contract['quantity_mt']))*24/allow
                row['workbook_allowed_hours_exact']=str(allow)
            except (KeyError,ValueError,ZeroDivisionError):pass
        row['physical']=copy.deepcopy(p[0]) if len(p)==1 else None
        row['source_references']={'optimization_json_call_id':cid,'profile_sheet':tables['profiles']['sheet'],
            'profile_row':p[0]['_source_row'] if len(p)==1 else None,'contract_sheet':tables['contracts']['sheet'],
            'contract_row':c[0]['_source_row'] if len(c)==1 else None}
        try:
            kg=Fraction(str(row['cargo_quantity_mt']))*1000;volume=Fraction(str(row['cargo_volume_bbl']))
            row['cargo_quantity_kg_exact']=str(kg)
            if volume>0 and len(p)==1 and Fraction(str(p[0].get('bbl_per_mt')))*Fraction(str(row['cargo_quantity_mt']))==volume:
                row['density_kg_per_bbl_exact']=str(kg/volume)
                row['density_provenance']='SYNTHETIC_MT_BBL_AND_WORKBOOK_BBL_PER_MT_VERIFIED'
        except (ValueError,KeyError,ZeroDivisionError): pass
        for key in ('eta_at','nor_tender_at','laycan_from_at','laycan_to_at','fix_etb_at'):
            if row.get(key) is not None:
                try:row[key]=timestamp(row[key]).isoformat()
                except (ValueError,TypeError):pass
        calls.append(row)
    berths=copy.deepcopy(source.get('berths',[]));tanks=copy.deepcopy(source.get('tanks',[]))
    for records,table,key in [(berths,'berths','berth_id'),(tanks,'tanks','tank_id')]:
        lookup={r.get(key):r for r in tables[table]['rows']}
        for record in records:
            record['workbook_supplement']=copy.deepcopy(lookup.get(record.get(key)))
    flows=copy.deepcopy(bundle['flows'])
    for record in flows.get('segments',[]):
        for key in ('start_at','end_at'):
            try:record[key]=timestamp(record[key]).isoformat()
            except (KeyError,ValueError,TypeError):pass
    return {'schema_version':'DOCKTIMIZE_NORMALIZED_INPUT_V3','normalization_version':VERSION,
        'data_origin':source.get('data_origin'),'operational_approval':False,'calls':calls,
        'berths':berths,'tanks':tanks,
        'units':{'mass_mt':'metric_tonne','mass_kg':'kg','volume_bbl':'bbl','density_kg_per_bbl_exact':'exact_rational','tank_flow':'bbl/hour','berth_transfer_rate':'MT/hour','timezone':'Asia/Seoul'},
        'fixed_process_flows':flows,'source_flow_input':copy.deepcopy(bundle['flows']), 'workbook_tables':copy.deepcopy(tables),
        'source_input':copy.deepcopy(source)}
