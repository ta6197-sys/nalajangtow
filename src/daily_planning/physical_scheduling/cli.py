"""Offline V3 berth-subproblem runner; never confirms a schedule."""
import argparse
import json
from pathlib import Path
import sys

from .preflight import inspect_physical_inputs
from .policy import validate_policy,event_definition_issues
from .service import solve_berth_schedule
from .user_policy import build_user_policy


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',required=True,type=Path);parser.add_argument('--policy',type=Path)
    parser.add_argument('--candidate-etbs',type=Path);parser.add_argument('--as-of')
    parser.add_argument('--preflight-only',action='store_true');parser.add_argument('--output-file',type=Path)
    parser.add_argument('--time-limit',type=float,default=30)
    parser.add_argument('--build-user-policy',action='store_true');parser.add_argument('--daylight-file',type=Path)
    parser.add_argument('--night-permissions',type=Path);parser.add_argument('--coverage-end');parser.add_argument('--berth-overrides',type=Path)
    args=parser.parse_args()
    try:
        snapshot=json.loads(args.snapshot.read_text())
        if args.build_user_policy:
            if not args.daylight_file or not args.as_of or not args.coverage_end:raise ValueError('Policy builder requires --daylight-file, --as-of and --coverage-end')
            permissions=json.loads(args.night_permissions.read_text()) if args.night_permissions else None
            overrides=json.loads(args.berth_overrides.read_text()) if args.berth_overrides else None
            result=build_user_policy(snapshot,args.daylight_file,start_at=args.as_of,end_at=args.coverage_end,night_permissions=permissions,berth_overrides=overrides)
        elif args.policy is None:
            result=inspect_physical_inputs(snapshot)
        else:
            policy=json.loads(args.policy.read_text())
            if args.preflight_only:
                validate_policy(policy,[b['berth_id'] for b in snapshot['berths'] if b.get('active')=='Y'])
                issues=event_definition_issues(snapshot,policy)
                result={'status':'UNSUPPORTED_OPERATIONAL_POLICY' if issues else 'POLICY_INPUTS_VALID',
                        'issues':issues,'operational_approval':False,'scope':'Policy inputs only; no scheduling feasibility evaluated'}
            else:
                if not args.as_of or args.candidate_etbs is None:raise ValueError('--as-of and --candidate-etbs are required for solving')
                candidates=json.loads(args.candidate_etbs.read_text())
                result=solve_berth_schedule(snapshot,policy=policy,as_of_at=args.as_of,candidate_etbs=candidates,time_limit_seconds=args.time_limit)
        content=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
        if args.output_file:
            with args.output_file.open('x',encoding='utf-8') as stream:stream.write(content)
        print(content,end='')
        return 0 if result['status'] in ('BERTH_SUBSYSTEM_FEASIBLE','POLICY_INPUTS_VALID') else 2
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'status':'INVALID_INPUT','issues':[{'reason':str(exc)}],'operational_approval':False},ensure_ascii=False))
        return 2

if __name__=='__main__':sys.exit(main())
