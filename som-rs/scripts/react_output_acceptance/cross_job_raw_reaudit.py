#!/usr/bin/env python3
"""Recheck bounded Mac raw evidence on a new GH job without impersonating the old job.

The original twelve-step audit runs only when the checked-out workspace and
restored output retain the same logical paths. Recorded PIDs and inodes describe
the source job. This tool does not issue product acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'audit'))
import audit_mac_rust006_extra as extra
import audit_mac_parity as parity
import audit_candidate_owner as owner
import audit_baseline_cleanup as baseline
import external_acceptance_readback as readback
import post_acceptance_audit as post

REPO=HERE.parents[2]

def need(value):
    if not value:raise ValueError('cross-job raw audit refused')

def old_paths(restored,revision):
    runtime=extra.json_file(restored/'candidate/runtime.json');argv=runtime.get('argv')
    need(type(argv) is list and len(argv)==6 and argv[1]=='serve' and argv[2]=='--assets' and argv[4:]==['--port','18919'])
    binary=Path(argv[0]);need(binary.is_absolute() and binary.name=='som')
    candidate=binary.parent;out=candidate.parent
    need(candidate.name=='candidate' and out.name=='acceptance' and out.parent.name=='m6-hosted' and out.parent.parent.name=='.local')
    repo=out.parent.parent.parent;need(argv[3]==str(candidate/'assets'))
    summary=extra.json_file(restored/'summary.json')
    need(summary.get('head_before')==summary.get('head_after')==revision and summary.get('pre_origin_passed') is True)
    return {'repo':repo,'out':out,'candidate':candidate,'baseline':repo/'.local/m6-hosted/baseline'}

def check_cross_source(lifecycle,raw,paths,revision):
    sources=parity.verify_git_sources(REPO,revision,parity.PARITY_SOURCES)
    need(sources['som-rs/scripts/react_output_acceptance/audit/independent_behavior_oracle.py']==parity.REVIEWED_ORACLE_SHA)
    receipt=extra.json_file(lifecycle/'receipt.json')
    need(receipt.get('source_revision')==revision and receipt.get('source_before')==receipt.get('source_after')==sources)
    manifest=extra.json_file(raw/'manifest.json')
    checker=paths['repo']/'som-rs/behavior/check_behavior.py';python=paths['repo']/'som-rs/runtime/acceptance-m3-venv/bin/python'
    expected=[str(checker),'parity','--qa',parity.QA,'--out',str(paths['out']/'run/rust006-parity'),
              '--target-dir',str(paths['out']/'rust006-parity-target'),'--python',str(python),'--jobs','4']
    need(manifest.get('argv')==expected and manifest.get('exit_code')==0)
    return {'source_revision':revision,'source_files':len(sources),'source_job_values':'recorded-not-live'}

def recheck(restored,config,provenance,revision,origin_run_id=None,origin_attempt=None):
    paths=old_paths(restored,revision);run=restored/'run';candidate=restored/'candidate';rust=run/'rust006-extra'
    lifecycle=run/'rust006-parity-lifecycle';raw=run/'rust006-parity-http';parity_out=run/'rust006-parity'
    public=REPO/'.local/rust-compiler-goal/acceptance/public-products-003b'
    legacy=REPO/'.local/rust-compiler-goal/acceptance/legacy544/run-005'
    evidence=run/'final-audit-evidence';rows=[]
    def check(name,call):
        try:rows.append({'id':name,'status':'passed','evidence':call()})
        except Exception as exc:rows.append({'id':name,'status':'unverified','error_type':type(exc).__name__})
    bound=None
    try:bound=extra.approved_inputs(public,legacy,config,provenance);rows.append({'id':'approved-inputs','status':'passed'})
    except Exception as exc:rows.append({'id':'approved-inputs','status':'unverified','error_type':type(exc).__name__})
    def inputs():need(bound is not None);return bound
    check('same-git-source',lambda:check_cross_source(lifecycle,raw,paths,revision))
    check('same-candidate-parity',lambda:extra.audit_candidate(lifecycle/'candidate-probe',candidate,revision,paths['candidate']))
    check('parity-postgres-observed-stop',lambda:parity.audit_parity_postgres(lifecycle))
    check('parity-raw',lambda:parity.audit_wire(raw,parity_out,legacy,inputs(),lifecycle/'product-http',paths['out']/'rust006-parity-target/debug/som_app'))
    check('python-replay',lambda:extra.audit_replay(rust/'raw-python-replay',public,legacy,inputs()))
    check('validate-cases',lambda:extra.audit_cases(rust/'raw-validate-cases'))
    check('rust-refusals',lambda:extra.audit_refusals(rust/'raw-rust-refusals',legacy,inputs()))
    check('same-candidate-rust',lambda:extra.audit_candidate(rust,candidate,revision,paths['candidate']))
    check('rust-postgres-observed-stop',lambda:extra.audit_cluster(rust/'cluster-lifecycle',paths['out']/'run/rust006-extra/cluster-work'))
    check('candidate-observed-cleanup',lambda:owner.audit(restored,candidate,paths['candidate'],evidence/'candidate-owner.json'))
    check('baseline-observed-cleanup',lambda:baseline.audit(restored,REPO/'.local/m6-hosted/baseline',original_frozen=paths['baseline'],receipt_path=evidence/'baseline-cleanup.json'))
    def original_gate():
        # audit130 compares absolute recorded paths in JSON and raw OS output.
        # Do not rewrite those bytes. Require the reviewer workspace and restore
        # root to have the exact original logical path; otherwise refuse.
        need(REPO==paths['repo'] and restored==paths['out'])
        return post.audit_original_gate(REPO,restored,candidate,run,revision)
    check('original-12-step-recheck',original_gate)
    check('rust-command-records-recheck',lambda:extra.audit_command_records(rust,candidate,public,legacy,parity_out/'report-parity.json',origin_repo=paths['repo'],origin_out=paths['out'],origin_run_id=origin_run_id,origin_attempt=origin_attempt))
    status='raw-checks-complete' if all(row['status']=='passed' for row in rows) else 'incomplete'
    return {'version':'som.m6-cross-job-raw-reaudit.draft.v1','status':status,'rows':rows,
            'origin_source_revision':revision,'origin_processes':'historical-observations-only',
            'current_runner':'independent-reviewer','product_acceptance_passed':False}

def main():
    p=argparse.ArgumentParser()
    for name in ('archive','restored','approved-input-config','provenance-root','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('head','run-id','artifact-id'):p.add_argument('--'+name,required=True)
    a=p.parse_args();need(not a.out.exists() and not a.out.is_symlink())
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
    try:
        need(re.fullmatch('[0-9a-f]{40}',a.head) is not None and a.run_id.isdigit() and a.artifact_id.isdigit())
        prefix='repos/faberline/som/actions/'
        gh_run=readback.gh_api(prefix+'runs/'+a.run_id);jobs=readback.gh_api(prefix+'runs/'+a.run_id+'/jobs?per_page=100')
        artifact=readback.gh_api(prefix+'artifacts/'+a.artifact_id)
        need(gh_run.get('id')==int(a.run_id) and artifact.get('id')==int(a.artifact_id))
        authority=readback.check(a.archive,a.restored,gh_run,jobs,artifact,a.head)
        result=recheck(a.restored,a.approved_input_config,a.provenance_root,a.head,gh_run['id'],gh_run['run_attempt']);result['origin_authority']=authority
    except Exception as exc:result={'version':'som.m6-cross-job-raw-reaudit.draft.v1','status':'incomplete',
                                    'product_acceptance_passed':False,'error_type':type(exc).__name__}
    with a.out.open('x') as stream:json.dump(result,stream,sort_keys=True,indent=2);stream.write('\n')
    print(json.dumps({'status':result['status'],'product_acceptance_passed':False}))
    return 0 if result['status']=='raw-checks-complete' else 1
if __name__=='__main__':raise SystemExit(main())
