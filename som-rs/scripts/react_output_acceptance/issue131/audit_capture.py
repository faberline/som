#!/usr/bin/env python3
"""Independent, hosted-only audit of the sealed Rust fresh-before capture."""
import argparse
from collections import Counter
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys

INVENTORY_SHA = '83489c5cc729b7f79e513cfbf2b6c7c93cca717daff093a19f101d566ca6c44f'
REPORT_SHA = '37c74335072cc93a64eb7663cc90c1fdd522e4d4be6a4f47e23f82b18e434b84'
RECORDING_SHA = '34114b9069f47db454ab3030df63fbee2f142a6c1c6acb0fc62ab1883400fbfa'
BASELINE_SHA = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
RUSTFMT_SHA = '2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f'


def need(value):
    if not value: raise ValueError('independent capture audit refused')


def read(path):
    need(path.is_file() and not path.is_symlink())
    return path.read_bytes()


def digest(raw): return hashlib.sha256(raw).hexdigest()


def obj(path): return json.loads(read(path))


def audit(args, checker):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS' and platform.machine() == 'arm64')
    need(digest(read(args.inventory)) == INVENTORY_SHA)
    report_path = args.evidence / 'run/rust-baseline/report.json'
    recorder_root = args.evidence / 'run/rust-baseline-raw'
    need(digest(read(report_path)) == REPORT_SHA and digest(read(recorder_root/'manifest.json')) == RECORDING_SHA)
    report, recording = obj(report_path), obj(recorder_root/'manifest.json')
    need(report['passed'] is True and recording['exit_code'] == 0 and recording['recording_errors'] == [])
    rows = {(row['phase'],row['id'],row['check']):row for row in report['rows']}
    need(len(rows) == len(report['rows']))
    calls = recording['calls']
    call_by_id = {call['id']:call for call in calls}
    need(len(call_by_id) == len(calls))
    qa = {}
    for call in calls:
        if call.get('method') == 'POST' and call.get('url') == 'http://127.0.0.1:18919/v1/assemble':
            qa.setdefault((call['request_sha256'],call['response_sha256'],call['status']), []).append(call)
    slots=obj(args.inventory)['slots']
    compile_slots={(s['phase'],s['case']):s for s in slots if s['cohort']=='rust006'}
    assemble_slots={(s['phase'],s['case']):s for s in slots if s['cohort']=='rust006_conditional_assemble'}
    need(len(compile_slots)==699 and len(assemble_slots)==697)
    positives={k for k,v in compile_slots.items() if v['report_status']==200}
    refusals=set(compile_slots)-positives
    need(positives==set(assemble_slots) and {x[1] for x in refusals}=={'RS12_CNL_V3_FILTER_LIMIT','RS13_JSON_V3_FILTER_OFFSET'})
    plan=obj(args.capture/'plan.json'); receipt=obj(args.capture/'receipt.json')
    need(plan['role']=='fresh-before' and plan['historical_wire'] is False and plan['product_acceptance']=='not_run')
    need(receipt['role']=='fresh-before' and receipt['historical_wire'] is False and receipt['product_acceptance']=='not_run'
         and receipt['passed'] is True and receipt['error'] is None and receipt['unrun']==0 and not receipt['cleanup_errors'])
    expected_plan={row['slot_id']:row for row in plan['slots']}
    captured={row['slot_id']:row for row in receipt['rows']}
    need(len(expected_plan)==len(plan['slots'])==697 and len(captured)==len(receipt['rows'])==697)
    need(set(expected_plan)==set(captured)=={s['slot_id'] for s in assemble_slots.values()})
    need({(r['phase'],r['case']) for r in plan['refusals']}==refusals)
    for refusal in plan['refusals']:
        source=compile_slots[(refusal['phase'],refusal['case'])]
        need(refusal['status']==source['report_status'] and refusal['response_sha256']==source['report_response_sha256']
             and refusal['role']=='historical-compile-refusal-no-assemble')
    run=args.run003
    products_raw=read(run/'rust-products.json');need(digest(products_raw)==report['rust_products_sha256'])
    phase1={r['id']:r for r in json.loads(products_raw)['cases']}
    summary=obj(run/'phase2/summary.json')
    def call_data(call, expected_status):
        number=call['id']; prefix=f'calls/{number:06d}'
        need(call['complete'] is True and call['error'] is None and call['status']==expected_status
             and call['request_file']==prefix+'.request.raw' and call['response_file']==prefix+'.response.raw')
        need(obj(recorder_root/(prefix+'.json'))==call)
        request=read(recorder_root/(prefix+'.request.raw')); response=read(recorder_root/(prefix+'.response.raw'))
        need(digest(request)==call['request_sha256'] and digest(response)==call['response_sha256'])
        return request,response
    for key in sorted(positives):
        phase,case=key; old=compile_slots[key]; slot=assemble_slots[key]; item=expected_plan[slot['slot_id']]; got=captured[slot['slot_id']]
        need(item['case']==case and item['phase']==phase and item['method']=='POST' and item['endpoint']=='/v1/assemble')
        compile_row=rows[(phase,case,'compile_equal')]
        need(compile_row['passed'] is True and compile_row['baseline_status']==200)
        raw_bundle=None
        for candidate in old['source_candidates']:
            call=call_by_id[candidate['call_id']]
            need(call['url']=='http://127.0.0.1:18917/v1/compile' and call['method']=='POST')
            creq,cresp=call_data(call,200)
            need(digest(creq)==old['historical_request_sha256'] and digest(cresp)==old['report_response_sha256']
                 and digest(cresp)==compile_row['baseline_response_sha256'])
            need(raw_bundle is None or raw_bundle==cresp);raw_bundle=cresp
        need(raw_bundle is not None)
        document=json.loads(raw_bundle);need(document['status']=='ok' and type(document['bundle']) is dict)
        request=json.dumps({'bundle':document['bundle']},ensure_ascii=False,separators=(',',':')).encode()
        refusal=rows[(phase,case,'baseline_bundle_refused')]
        need(refusal['passed'] is True and refusal['request_sha256']==refusal['qa_request_sha256']==digest(request))
        prior=qa.get((refusal['request_sha256'],refusal['qa_response_sha256'],400),[])
        need(prior and item['prior_candidate_raw_request_state']=='verified')
        prior_paths=[]
        for call in prior:
            old_request,_=call_data(call,400);need(old_request==request)
            prior_paths.append(f"run/rust-baseline-raw/calls/{call['id']:06d}.request.raw")
        need(item['prior_candidate_raw_request_sources']==prior_paths)
        fresh_request=read(args.capture/'requests'/(str(slot['slot_id'])+'.raw'))
        need(fresh_request==request and item['request_sha256']==digest(request))
        if phase=='phase1':
            files_sha=phase1[case]['files_sha256']
        else:
            name=re.sub(r'[^A-Za-z0-9]+','_',case).strip('_')+'.json'; raw=read(run/'phase2/cases'/name)
            need(digest(raw)==summary['case_sha256'][case]==report['records_sha256']['phase2_case_files'][case])
            files_sha=json.loads(raw)['rust']['files_sha256']
        reference=rows[(phase,case,'rebased_files')]
        need(reference['passed'] is True and reference['recorded_files_sha256']==reference['files_sha256']==files_sha)
        accepted=qa.get((reference['qa_request_sha256'],reference['qa_response_sha256'],200),[])
        need(accepted)
        accepted_maps=[]
        for call in accepted:
            _,response=call_data(call,200); files=json.loads(response)['files']
            need(type(files) is dict and checker.canonical_sha256(files)==files_sha)
            accepted_maps.append(files)
        need(all(checker.qa.exact_equal(accepted_maps[0],other) for other in accepted_maps))
        response=read(args.capture/'responses'/(str(slot['slot_id'])+'.raw'))
        need(got['status']==200 and got['comparison']=='passed' and got['matched'] is True
             and got['response_sha256']==digest(response))
        actual=json.loads(response)['files']
        need(type(actual) is dict and checker.qa.exact_equal(actual,accepted_maps[0]))
    for key in refusals:
        source=compile_slots[key]; report_row=rows[(key[0],key[1],'compile_equal')]
        need(source['report_status']==report_row['baseline_status'] and report_row['passed'] is True)
        for candidate in source['source_candidates']:
            call=call_by_id[candidate['call_id']]; request,response=call_data(call,source['report_status'])
            need(call['url']=='http://127.0.0.1:18917/v1/compile' and
                 digest(request)==source['historical_request_sha256'] and digest(response)==source['report_response_sha256'])
    release=obj(args.repo/'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json')['candidate']
    baseline=args.baseline
    need(release['binary_sha256']==BASELINE_SHA==digest(read(baseline/'som'))==receipt['baseline_sha256']==receipt['baseline_sha256_after'])
    need(receipt['baseline_provenance']['build_commit']==release['build_commit']=='8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af')
    for name,field in (('source.json','source_json_sha256'),('build.json','build_receipt_sha256'),('runtime.json','runtime_receipt_sha256')):
        need(digest(read(baseline/name))==release[field]==receipt['baseline_provenance'][name])
    need(receipt['rustfmt']['sha256']==receipt['rustfmt_sha256_after']==RUSTFMT_SHA and receipt['assets_unchanged'] is True)
    asset_files={p.relative_to(baseline/'assets').as_posix():digest(read(p)) for p in (baseline/'assets').rglob('*') if p.is_file()}
    need(asset_files==receipt['assets'])
    health=read(args.capture/'health.raw')
    need(receipt['health_status']==200 and receipt['health_sha256']==digest(health) and json.loads(health)==obj(baseline/'runtime.json')['health'])
    identity=receipt['process_start'];ending=receipt['process_end']; pid=receipt['pid']
    for field in ('pid','parent_pid','pgid','start_time','binary_device','binary_inode','binary_size'):
        need(identity[field]==ending[field])
    need(pid==identity['pid']==identity['pgid'] and receipt['owner_pid']==identity['parent_pid'])
    for field in ('binary_device','binary_inode','binary_size'):
        need(identity[field]==receipt['binary_prelaunch'][field])
    for stage,identity in (('start',identity),('end',ending)):
        for name,record in identity['commands'].items():
            stdout=read(args.capture/'process-evidence'/(stage+'-'+name+'.stdout'))
            stderr=read(args.capture/'process-evidence'/(stage+'-'+name+'.stderr'))
            need(record['exit_code']==0 and not stderr and digest(stdout)==record['stdout_sha256'] and digest(stderr)==record['stderr_sha256'])
        text=read(args.capture/'process-evidence'/(stage+'-listeners.stdout')).decode().splitlines()
        need([s[1:] for s in text if s.startswith('p')]==[str(pid)])
        fields=read(args.capture/'process-evidence'/(stage+'-identity.stdout')).decode().strip().split(None,4)
        need(len(fields)==5 and fields[:3]==[str(pid),str(receipt['owner_pid']),str(pid)]
             and not fields[3].startswith('Z') and fields[4]==' '.join(receipt['argv']))
        need(read(args.capture/'process-evidence'/(stage+'-start_time.stdout')).decode().strip()==identity['start_time'])
        lines=read(args.capture/'process-evidence'/(stage+'-executable.stdout')).decode().splitlines()
        descriptors=[]; current=None
        for line in lines:
            if line.startswith('f'):current={'fd':line[1:]};descriptors.append(current)
            elif current is not None and line[:1] in ('D','i','n'):current[line[0]]=line[1:]
        need(any(d.get('fd')=='txt' and 'D' in d and 'i' in d and int(d['D'],0)==identity['binary_device']
                 and int(d['i'])==identity['binary_inode'] for d in descriptors))
    listener=receipt['listener_after']; bind=receipt['bind_after']; sockets=receipt['sockets_after']
    ls=read(args.capture/'process-evidence/cleanup-listeners.stdout');le=read(args.capture/'process-evidence/cleanup-listeners.stderr')
    ns=read(args.capture/'process-evidence/cleanup-sockets.stdout');ne=read(args.capture/'process-evidence/cleanup-sockets.stderr')
    need(listener['listener_absent'] is True and listener['exit_code']==1 and not ls and not le
         and digest(ls)==listener['stdout_sha256'] and digest(le)==listener['stderr_sha256'])
    need(obj(args.capture/'process-evidence/cleanup-bind.json')==bind and sockets['exit_code']==0 and not ne
         and digest(ns)==sockets['stdout_sha256'] and digest(ne)==sockets['stderr_sha256'])
    states=[]
    for line in ns.decode().splitlines():
        f=line.split()
        if f and f[0] in ('tcp4','tcp6') and len(f)>=6 and f[3].endswith('.18917'):states.append(f[5])
    need(states==sockets['states'] and 'LISTEN' not in states)
    need(bind['available'] is True or (bind['errno']==errno.EADDRINUSE and 'TIME_WAIT' in states))
    owner=obj(args.capture/'candidate-owner.json'); events=owner['events']
    waits=[e for e in events if e.get('kind') in ('candidate-wait','candidate-cleanup-wait') and e.get('state')=='completed']
    need(len(waits)==1 and waits[0]['exit_code']==receipt['wait_exit'] and type(receipt['wait_exit']) is int
         and receipt['process_group_gone'] is True)
    snapshots=[e for e in events if e.get('kind')=='candidate-group-observation']
    need({e['stage'] for e in snapshots}=={'before-term','before-kill'})
    for event in snapshots:
        stdout=read(args.capture/('candidate-group-'+event['stage']+'.stdout'))
        stderr=read(args.capture/('candidate-group-'+event['stage']+'.stderr'))
        need(event['exit_code']==0 and not stderr and digest(stdout)==event['stdout_sha256']
             and digest(stderr)==event['stderr_sha256'])
        observed=[line.split(None,4) for line in stdout.decode().splitlines() if line.strip()]
        need(any(len(fields)==5 and fields[:3]==[str(pid),str(receipt['owner_pid']),str(pid)] for fields in observed))
    return {'status':'passed','role':'fresh-before','historical_wire':False,'product_acceptance':'not_run',
            'source_cases':697,'original_request_bytes_verified':697,'fresh_requests_verified':697,
            'fresh_responses_and_complete_filemaps_verified':697,'historical_compile_refusals':2,
            'runtime_status_counts':dict(Counter(r['status'] for r in captured.values())),
            'direct_wait_exit':receipt['wait_exit'],'listener_absent':True,'bind_available':bind['available'],
            'socket_states':dict(Counter(states)),'rustfmt_before_after_sha256':RUSTFMT_SHA,
            'process_group_gone':'producer_attested','node_libnode_prettier_after':'unverified'}


def main():
    parser=argparse.ArgumentParser()
    for name in ('repo','inventory','evidence','run003','baseline','capture'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.repo/'som-rs/scripts/typescript_output_acceptance'))
    import check_rust_baseline as checker
    try:print(json.dumps(audit(args,checker),sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'product_acceptance':'not_run'},sort_keys=True))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
