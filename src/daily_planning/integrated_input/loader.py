"""Read exact source bytes; NFC matching never substitutes older inputs."""
import io
import json
from pathlib import Path
import unicodedata
from .provenance import cell,digest

FILES=('Docktimize_11선석_ETA_NOR_FIX_외부공정유량_v3.xlsx','Docktimize_최적화입력_ETA_NOR_FIX_v3.json','Docktimize_공정유량_6h_v3.json')
V5_FILES=('Docktimize_v5_FIX_ETB15_SOF정리_선박제원연결.xlsx','Docktimize_v5_FIX_ETB15_선박제원포함.json','Docktimize_공정유량_6h_3탱크_1000만bbl.json','Docktimize_v5_입력정리_변경검증.txt')
BALANCED_FILES=('Docktimize_v5_선박입력_BALANCED.json','Docktimize_공정유량_6h_3탱크_1000만bbl_BALANCED.json')
BALANCED_EXT_PROFILE='V5_BALANCED_154_EXT_20270109'
BALANCED_EXT_FILES=('Docktimize_v5_선박입력_BALANCED.json','Docktimize_공정유량_6h_3탱크_1000만bbl_BALANCED_20270109.json')
SCENARIO_FILES={'V3_ORIGINAL':FILES,'V3_CRUDE_PLUS_3':('Docktimize_11선석_ETA_NOR_FIX_외부공정유량_v3_CRUDE_PLUS.xlsx','Docktimize_최적화입력_ETA_NOR_FIX_v3_CRUDE_PLUS.json',FILES[2]),'V5_3TANK':V5_FILES,'V5_BALANCED_154':BALANCED_FILES, BALANCED_EXT_PROFILE:BALANCED_EXT_FILES}
V5_SHEETS={'contracts':('03_계약조건',{'case_id','vessel_id','quantity_mt'}), 'sof':('04_SOF이력',{'event_id','case_id','event_code'}), 'labels':('05_정답라벨',{'case_id','allowed_hours'}), 'eta':('12_ETA_입력',{'case_id','eta_local','fix_etb_local'}), 'tanks':('13_탱크입력',{'tank_id','capacity_bbl','initial_inventory_bbl'}), 'berths':('14_선석정보',{'berth_id','min_pbl_m','connected_tank_ids'}), 'profiles':('17_선박제원',{'case_id','vessel_id','bbl_per_mt'}), 'calls':('24_최적화입력_항차',{'call_id','vessel_id','eta_local'}), 'flows':('25_공정유량_6h',{'tank_id','start_local','end_local','fixed_net_flow_bbl_h'})}
SHEETS={'contracts':('03_계약조건',{'case_id','contract_clause_ko','nor_tender_local'}),
 'sof':('04_SOF이력',{'event_id','case_id','event_code'}),
 'eta':('12_ETA_ETB',{'case_id','eta_local','etb_local','fixed_etb_local'}),
 'tanks':('13_탱크입력',{'tank_id','capacity_bbl','initial_inventory_bbl'}),
 'berths':('14_선석정보',{'berth_id','max_dwt_mt','connected_tank_ids'}),
 'groups':('15_선석그룹제약',{'rule_id','rule_type','berth_ids'}),
 'restrictions':('16_선석제약시간',{'restriction_id','berth_id','start_local','end_local'}),
 'profiles':('17_선박제원',{'case_id','vessel_id','dwt_mt','loa_m','draft_m','grt','bbl_per_mt'}),
 'handling':('19_하역시간보정',{'case_id','min_required_minutes','suggested_work_minutes'}),
 'calls':('24_최적화입력_항차',{'call_id','baseline_berth_reference_only','fix_policy'}),
 'policy':('28_운영정책_4142',{'policy_id','rule_key','value'})}

class InputError(ValueError):
    def __init__(self,code,message): self.code=code;super().__init__(message)


def load_inputs(data_dir, *, scenario='V3_ORIGINAL'):
    if scenario not in SCENARIO_FILES:raise InputError('INVALID_SCHEMA','Unknown scenario: '+str(scenario))
    files=SCENARIO_FILES[scenario]
    root=Path(data_dir)
    paths={}
    if not root.is_dir(): raise InputError('MISSING_INPUT',str(root))
    for name in files:
        matches=[p for p in root.iterdir() if p.is_file() and unicodedata.normalize('NFC',p.name)==name]
        if not matches: raise InputError('MISSING_INPUT',name)
        if len(matches)!=1: raise InputError('INVALID_SCHEMA','Ambiguous Unicode filenames: '+name)
        paths[name]=matches[0]
    raw={name:path.read_bytes() for name,path in paths.items()}
    artifacts=[{'filename':paths[name].name,'logical_filename':name,'sha256':digest(raw[name]),'bytes':len(raw[name])} for name in files]
    if scenario in ('V5_BALANCED_154',BALANCED_EXT_PROFILE):
        def unique_object(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise InputError('INVALID_SCHEMA','Duplicate JSON key: '+key)
                result[key]=value
            return result
        try:
            source=json.loads(raw[files[0]],parse_float=str,object_pairs_hook=unique_object)
            flows=json.loads(raw[files[1]],parse_float=str,object_pairs_hook=unique_object)
            if not isinstance(source,dict) or not isinstance(flows,dict):raise InputError('INVALID_SCHEMA','JSON roots must be objects')
            for key in ('cargo_calls','berths','tanks','vessel_profiles'):
                if not isinstance(source.get(key),list) or any(not isinstance(row,dict) for row in source[key]):raise InputError('INVALID_SCHEMA','Invalid '+key+' array')
            if not isinstance(flows.get('segments'),list) or any(not isinstance(row,dict) for row in flows['segments']):raise InputError('INVALID_SCHEMA','Invalid flow segments')
            if not isinstance(source.get('planning_policy'),dict) or not isinstance(source.get('operational_rules'),dict):raise InputError('INVALID_SCHEMA','Missing policy objects')
            result={'scenario_id':'V5_BALANCED_154','source':source,'flows':flows,'tables':None,'artifacts':artifacts}
            if scenario==BALANCED_EXT_PROFILE:result['source_profile_id']=scenario
            return result
        except InputError:raise
        except (ValueError,TypeError,UnicodeDecodeError) as exc:raise InputError('INVALID_SCHEMA',str(exc)) from exc
    try:
        source=json.loads(raw[files[1]],parse_float=str)
        flows=json.loads(raw[files[2]],parse_float=str)
        if not isinstance(source,dict) or not isinstance(flows,dict):raise InputError('INVALID_SCHEMA','JSON roots must be objects')
        for key in (('cargo_calls','berths','tanks','vessel_profiles') if scenario=='V5_3TANK' else ('cargo_calls','berths','tanks')):
            if not isinstance(source.get(key),list) or any(not isinstance(r,dict) for r in source[key]):raise InputError('INVALID_SCHEMA','Invalid '+key+' array')
        if scenario!='V5_3TANK' and any(not isinstance(row.get('baseline_reference_only',{}),dict) for row in source['cargo_calls']):raise InputError('INVALID_SCHEMA','baseline_reference_only must be an object')
        if not isinstance(flows.get('segments'),list) or any(not isinstance(r,dict) for r in flows['segments']):raise InputError('INVALID_SCHEMA','Invalid flow segments')
        if not isinstance(source.get('planning_policy'),dict) or not isinstance(source.get('operational_rules'),dict):raise InputError('INVALID_SCHEMA','Missing policy objects')
        from openpyxl import load_workbook
        workbook=load_workbook(io.BytesIO(raw[files[0]]),read_only=True,data_only=True)
        tables={}
        try:
            all_tables={}
            for sheet in workbook:
                it=sheet.iter_rows(values_only=True);headers=next(it,())
                headers=[str(h) if h is not None else None for h in headers]
                present=[h for h in headers if h is not None]
                if len(present)!=len(set(present)):raise InputError('INVALID_SCHEMA','Duplicate workbook header: '+sheet.title)
                rows=[]
                for number,values in enumerate(it,2):
                    if any(v is not None for v in values):
                        row={h:cell(v) for h,v in zip(headers,values) if h is not None}
                        row['_source_row']=number;rows.append(row)
                all_tables[sheet.title]={'sheet':sheet.title,'headers':[h for h in headers if h], 'rows':rows}
            for key,(preferred,required) in (V5_SHEETS if scenario=='V5_3TANK' else SHEETS).items():
                candidates=[v for v in all_tables.values() if required<=set(v['headers'])]
                exact=all_tables.get(preferred)
                table=exact if exact and required<=set(exact['headers']) else (candidates[0] if len(candidates)==1 else None)
                if table is None: raise InputError('INVALID_SCHEMA','Missing or ambiguous workbook headers: '+preferred)
                tables[key]=table
        finally:
            workbook.close()
    except InputError: raise
    except (ValueError,KeyError,TypeError) as exc: raise InputError('INVALID_SCHEMA',str(exc)) from exc
    result={'source':source,'flows':flows,'tables':tables,'artifacts':artifacts}
    if scenario=='V5_3TANK':result['scenario_id']=scenario
    return result
