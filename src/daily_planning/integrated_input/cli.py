"""Offline V3 preflight. Dry-run performs no filesystem writes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from .loader import load_inputs,InputError
from .normalizer import normalize
from .validator import validate
from .provenance import canonical,digest,VERSION,KST


def run(data_dir,output_dir,dry_run=False):
    try:
        bundle=load_inputs(data_dir);snapshot=normalize(bundle);report=validate(snapshot)
    except (InputError,ValueError,KeyError,TypeError) as exc:
        return {'status':getattr(exc,'code','INVALID_SCHEMA'),'data_preflight_pass':False,
                'issues':[{'code':getattr(exc,'code','INVALID_SCHEMA'),'explanation':str(exc)}]},None
    try: code=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):code=None
    code_hash=digest(b''.join(p.name.encode()+p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
    manifest={'adapter_code_sha256':code_hash,'artifacts':bundle['artifacts'],'historical_parent_source_workbook_sha256':bundle['source'].get('source_workbook_sha256'),
        'input_schema_versions':[bundle['source'].get('schema_version'),bundle['flows'].get('schema_version')],
        'data_origin':bundle['source'].get('data_origin'),'seed':bundle['source'].get('seed'),
        'operating_policy':bundle['source'].get('operational_rules'), 'normalization_version':VERSION,'git_commit':code,
        'normalized_snapshot_sha256':digest(canonical(snapshot).encode()),'operational_approval':False}
    run_id=digest(canonical(manifest).encode())[:24]
    manifest.update(run_id=run_id,generation_timestamp=datetime.now(KST).isoformat())
    summary={'counts':report['counts'],'data_preflight_pass':report['data_preflight_pass'],
             'unsupported_policy_count':len(report['pending_operational_validation']),
             'pending_contract_precision_count':len(report['pending_contract_validation']),'settlement_ready':report['settlement_ready'],
             'physical_scheduling_feasibility':'NOT_EVALUATED'}
    if not dry_run:
        folder=Path(output_dir)/run_id
        if folder.exists():
            for name,value in [('normalized_snapshot.json',snapshot),('validation_report.json',report),('data_quality_summary.json',summary)]:
                if json.loads((folder/name).read_text())!=value:raise ValueError('Existing immutable output differs: '+str(folder/name))
            previous=json.loads((folder/'manifest.json').read_text());previous.pop('generation_timestamp',None)
            comparison=dict(manifest);comparison.pop('generation_timestamp',None)
            if previous!=comparison:raise ValueError('Existing manifest differs')
            manifest=json.loads((folder/'manifest.json').read_text())
        else:
            folder.mkdir(parents=True,exist_ok=False)
            for name,value in [('manifest.json',manifest),('normalized_snapshot.json',snapshot),('validation_report.json',report),('data_quality_summary.json',summary)]:
                with (folder/name).open('x',encoding='utf-8') as stream:stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    return report,manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path,default=Path('results/v3_preflight'));parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:report,manifest=run(args.data_dir,args.output_dir,args.dry_run)
    except (ValueError,OSError) as exc:
        print(json.dumps({'status':'INVALID_SCHEMA','issues':[str(exc)]},ensure_ascii=False));return 2
    print(json.dumps({'validation':report,'manifest':manifest,'dry_run':args.dry_run},ensure_ascii=False,indent=2))
    return 0 if report['data_preflight_pass'] else 2

if __name__=='__main__':sys.exit(main())
