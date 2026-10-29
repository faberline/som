#!/usr/bin/env python3
"""Copy bounded final-audit evidence into the sealer's existing run subtree."""
import argparse
import hashlib
import json
from pathlib import Path

NAMES=('candidate-owner.json','baseline-cleanup.json','candidate-owner-audit.json',
       'baseline-cleanup-audit.json','post-acceptance-observations.json')

def stage(root, run_info, jobs_info):
    root=Path(root);destination=root/'run'/'final-audit-evidence'
    if not root.is_dir() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise ValueError('physical root required')
    run=root/'run'
    if run.is_symlink() or (run.exists() and not run.is_dir()):raise ValueError('physical run required')
    run.mkdir(exist_ok=True);destination.mkdir(mode=0o700)
    sources={name:root/name for name in NAMES}
    sources.update({'origin-run.json':Path(run_info),'origin-jobs.json':Path(jobs_info)})
    present={};missing=[]
    for name,source in sources.items():
        if not source.exists():missing.append(name);continue
        if not source.is_file() or source.is_symlink() or any(p.is_symlink() for p in source.parents):
            raise ValueError('physical source required')
        raw=source.read_bytes()
        if len(raw)>128*1024*1024:raise ValueError('source exceeds limit')
        with (destination/name).open('xb') as stream:stream.write(raw)
        present[name]=hashlib.sha256(raw).hexdigest()
    inventory={'version':'som.m6-final-audit-evidence.v1','present_sha256':present,'missing':missing,
               'product_acceptance_passed':False}
    with (destination/'inventory.json').open('x') as stream:json.dump(inventory,stream,sort_keys=True,indent=2);stream.write('\n')
    return inventory

def main():
    p=argparse.ArgumentParser()
    for name in ('root','run-info','jobs-info'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();result=stage(args.root,args.run_info,args.jobs_info)
    print(json.dumps({'present':len(result['present_sha256']),'missing':result['missing']}))
    return 0 if not result['missing'] else 1
if __name__=='__main__':raise SystemExit(main())
