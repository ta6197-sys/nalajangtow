"""Build an immutable, separate V3 synthetic flow-extension snapshot."""
import argparse
import json
from pathlib import Path
from src.daily_planning.integrated_input.flow_extension import build_extended_snapshot,validate_extended_snapshot
from src.daily_planning.integrated_input.provenance import canonical,digest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path);p.add_argument('--base-flow',type=Path,required=True);p.add_argument('--extension',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    source=json.loads(a.snapshot.read_text());extended=build_extended_snapshot(source,base_flow_path=a.base_flow,extension_path=a.extension);report=validate_extended_snapshot(extended)
    if not report['data_preflight_pass']:raise ValueError('Extended preflight failed: '+str(report['issues']))
    meta=extended['fixed_process_flows']['extension_provenance'];manifest={'scenario':'V3_CRUDE_PLUS_3_EXTENDED','model_version':meta['version'],'base_snapshot_sha256':digest(canonical(source).encode()),'extended_snapshot_sha256':digest(canonical(extended).encode()),'base_flow_sha256':meta['base_flow_sha256'],'extension_sha256':meta['extension_sha256'],'extension_known_at':meta['known_at'],'extension_start_at':meta['extension_start_at'],'flow_end_at':extended['fixed_process_flows']['flow_horizon_end_at'],'segments':len(extended['fixed_process_flows']['segments']),'operational_approval':False}
    out=a.output_dir;out.mkdir(parents=True,exist_ok=False)
    for name,value in [('normalized_snapshot.json',extended),('manifest.json',manifest),('validation_report.json',report)]:
        with (out/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2)
    print(json.dumps({'manifest':manifest,'data_preflight_pass':report['data_preflight_pass'],'output_dir':str(out)},ensure_ascii=False))
if __name__=='__main__':main()
