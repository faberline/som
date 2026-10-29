#!/usr/bin/env python3
"""Independent Mac parity raw-input structural audit draft; never acceptance.

This joins source cases to unordered compiler calls and actual parity files.
It does not yet rederive frozen behavior normalization, process ownership,
or every refusal oracle, so it always returns unrun.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import audit_mac_rust006_extra as a
import audit_product_normalization as independent_normalization
import audit_product_oracle as independent_oracle
import independent_behavior_oracle as frozen_oracle

REPO=Path(__file__).resolve().parents[4]
QA='http://127.0.0.1:18919'
REVIEWED_ORACLE_SHA='8599438d6019795028b1bc28a51d2d16c2e682cc4012a5c9250a0984316f8fe7'
PARITY_SOURCES=('som-rs/scripts/react_output_acceptance/run_hosted_acceptance.py', 'som-rs/scripts/react_output_acceptance/mac_parity_capture.py', 'som-rs/scripts/react_output_acceptance/product_http_recorder.py', 'som-rs/scripts/react_output_acceptance/parity_product_files.py', 'som-rs/scripts/react_output_acceptance/parity_rust_build.py', 'som-rs/scripts/react_output_acceptance/parity_python_runtime.py', 'som-rs/scripts/react_output_acceptance/record_rust006_http.py', 'som-rs/scripts/react_output_acceptance/record_api.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_parity.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_normalization.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_oracle.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_rust006_extra.py', 'som-rs/behavior/check_behavior.py', 'som-rs/behavior/suite.py', 'som-rs/behavior/hosts.py', 'som-rs/behavior/faults.py', 'som-rs/som-targets/qa/check_targets.py', 'som-rs/som-targets/qa/check_rust_products.py', 'som-rs/tests/rust-output-v1/rust_profile.py', 'som-rs/tests/rust-output-v1/rust_profile_phase2.py', 'som-rs/scripts/rust_output_acceptance/python_replay.py', 'som-rs/scripts/compiler_acceptance/qa_harness.py', 'som-rs/scripts/compiler_acceptance/paths.py', 'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py', 'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py', 'som-rs/scripts/react_output_acceptance/install_derived_inputs.py', 'som-rs/scripts/react_output_acceptance/audit_supplement_provision.py', 'som-rs/scripts/react_output_acceptance/audit/independent_behavior_oracle.py', 'som-rs/scripts/react_output_acceptance/mac_rust006_extra.py', 'som-rs/scripts/react_output_acceptance/owned_candidate.py', 'som-rs/scripts/react_output_acceptance/audit/audit_candidate_owner.py', 'som-rs/scripts/react_output_acceptance/audit_workflow_origin.py', 'som-rs/scripts/react_output_acceptance/baseline_cleanup.py', 'som-rs/scripts/react_output_acceptance/audit/audit_baseline_cleanup.py', 'som-rs/scripts/react_output_acceptance/post_acceptance_audit.py', 'som-rs/scripts/react_output_acceptance/stage_final_evidence.py', 'som-rs/scripts/react_output_acceptance/external_acceptance_readback.py', 'som-rs/scripts/react_output_acceptance/cross_job_raw_reaudit.py', 'som-rs/scripts/react_output_acceptance/audit/audit130.py', 'som-rs/scripts/react_output_acceptance/audit/api_raw.py', 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py', 'som-rs/scripts/react_output_acceptance/audit/render_rules.py', 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py')

def need(ok,why):
    if not ok:raise ValueError(why)

def verify_git_sources(repo, revision, names):
    import subprocess
    need(re.fullmatch('[0-9a-f]{40}',revision) is not None,'parity revision invalid')
    actual=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'rev-parse','--verify','HEAD^{commit}'],timeout=30).decode().strip()
    need(actual==revision,'parity source HEAD differs')
    result={}
    for name in names:
        entry=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'ls-tree','-z',revision,'--',name],timeout=30)
        need(entry.startswith((b'100644 blob ',b'100755 blob ')) and entry.endswith(b'\t'+name.encode()+b'\0')
             and entry.count(b'\0')==1,'parity source Git entry differs')
        raw=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'show',revision+':'+name],timeout=30)
        need(raw==a.read(repo/name),'parity executed source differs from Git')
        result[name]=a.sha(raw)
    return result

def audit_sources(revision,lifecycle,raw,parity_out):
    import os
    need(os.environ.get('GITHUB_SHA')==revision and os.environ.get('GITHUB_ACTIONS')=='true'
         and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('RUNNER_OS')=='macOS'
         and os.environ.get('RUNNER_ARCH')=='ARM64','parity runner identity differs')
    sources=verify_git_sources(REPO,revision,PARITY_SOURCES)
    need(sources['som-rs/scripts/react_output_acceptance/audit/independent_behavior_oracle.py']==REVIEWED_ORACLE_SHA,
         'reviewed oracle source differs')
    receipt=a.json_file(lifecycle/'receipt.json')
    need(receipt.get('source_revision')==revision and receipt.get('source_before')==sources and receipt.get('source_after')==sources,
         'parity collector source snapshot differs')
    manifest=a.json_file(raw/'manifest.json')
    checker=REPO/'som-rs/behavior/check_behavior.py';python=REPO/'som-rs/runtime/acceptance-m3-venv/bin/python'
    expected=[str(checker),'parity','--qa',QA,'--out',str(parity_out),
              '--target-dir',str(parity_out.parent.parent/'rust006-parity-target'),'--python',str(python),'--jobs','4']
    need(manifest.get('argv')==expected and manifest.get('exit_code')==0, 'parity checker command differs')
    return {'source_files':len(sources),'revision':revision,'outer_invocation_provenance':'unverified'}

def source_cases(legacy,bound):
    contract=REPO/'som-rs/contracts/rust-output-v1'
    inventory=a.json_file(contract/'case-inventory.json')
    rows=[]
    for entry in inventory['public_examples']:
        if entry['rust']=='in_scope':
            raw=a.read(REPO/entry['prompt'])
            need(a.sha(raw)==entry['prompt_file_sha256'],'example prompt differs')
            rows.append((entry['id'],a.obj(raw)))
    wanted={e['id']:e for e in inventory['version_positive_fixtures'] if e['rust']=='in_scope'}
    for item in a.json_file(REPO/'som-rs/tests/compiler-v1/cnl-positive-fixtures.json')['cases']:
        if item['id'] in wanted:
            need(a.h(item['expected_prompt'])==wanted[item['id']]['prompt_sha256'],'fixture prompt differs')
            rows.append((item['id'],item['expected_prompt']))
    root=REPO/'som-rs/tests/rust-output-v1/cases'
    cases={r['id']:r for r in a.json_file(root/'index.json')['cases']}
    for entry in inventory['coverage_cases']:
        raw=a.read(root/cases[entry['id']]['prompt'])
        need(a.sha(raw)==entry['prompt_file_sha256'],'coverage prompt differs')
        rows.append((entry['id'],a.obj(raw)))
    for entry in inventory['legacy_in_scope']:
        prompt=bound.json(legacy/f"{entry['inventory_index']:04d}"/'assemble.request.json')['bundle']['prompt']
        need(a.h(prompt)==entry['prompt_sha256'],'historic prompt differs')
        rows.append((entry['id'],prompt))
    need(len(rows)==115 and len({r[0] for r in rows})==115,'case set differs')
    return rows

def raw_exchange_index(root):
    manifest=a.json_file(root/'manifest.json')
    script='som-rs/behavior/check_behavior.py'
    need(manifest.get('version')=='som.m6-rust006-http-recording.v1' and manifest.get('exit_code')==0
         and manifest.get('recording_errors')==[] and manifest.get('script',{}).get('path')==script
         and manifest['script'].get('sha256')==a.sha(a.read(REPO/script)), 'parity recorder differs')
    rows=manifest.get('calls');need(type(rows) is list,'parity raw calls missing')
    indexed=defaultdict(list)
    for number,row in enumerate(rows,1):
        request_rel=f'calls/{number:06d}.request.raw';response_rel=f'calls/{number:06d}.response.raw'
        need(row.get('id')==number and row.get('method')=='POST' and row.get('complete') is True
             and row.get('error') is None and row.get('status')==200
             and row.get('url') in (QA+'/v1/compile',QA+'/v1/assemble')
             and row.get('request_file')==request_rel and row.get('response_file')==response_rel, 'parity raw call differs')
        request,response=a.read(root/request_rel),a.read(root/response_rel)
        need(a.sha(request)==row.get('request_sha256') and a.sha(response)==row.get('response_sha256')
             and a.equal(a.json_file(root/f'calls/{number:06d}.json'),row),'parity raw bytes differ')
        route=row['url'][len(QA):]
        indexed[(route,request)].append(response)
    return indexed,len(rows)

def take(index,route,request):
    key=(route,a.compact(request)); rows=index.get(key)
    need(rows,'expected unordered compiler call missing')
    return rows.pop()

def case_key(identity):return re.sub(r'[^a-z0-9]+','_',identity.lower()).strip('_')

def check_record(raw, normalized):
    """Recompute request body, media, and fixed error normalization from raw records.

    Success-row ID, timestamp and floating-field normalization remains unverified.
    """
    need(type(raw) is dict and type(normalized) is dict and type(normalized.get('request')) is dict, 'record shape differs')
    request=normalized['request']; status=raw.get('status')
    need(raw.get('step') == normalized.get('step') and raw.get('method') == request.get('method')
         and type(status) is int and status == normalized.get('status'), 'record request or status differs')
    body=raw.get('body'); need((body is None and 'body' not in request) or
                                 (type(body) is str and request.get('body') == body), 'request body differs')
    content=raw.get('content_type')
    need(type(content) is str and (content == 'application/json' and 'content_type' not in request or
                                   content != 'application/json' and request.get('content_type') == content),
         'request content type differs')
    raw_media=raw.get('response_content_type')
    need(raw_media is None or type(raw_media) is str, 'response content type invalid')
    media=raw_media.split(';',1)[0].strip().lower() if raw_media else None
    need(normalized.get('media') == media, 'response media differs')
    payload=raw.get('response'); need(type(payload) is str, 'response bytes missing')
    if status == 204:
        need(payload == '' and normalized.get('body') is None, '204 body differs')
        return 'empty'
    if status == 422:
        document=a.obj(payload.encode('utf-8'))
        need(type(document) is dict and set(document) == {'detail'} and type(document['detail']) is list
             and document['detail'], '422 response shape differs')
        heads=set()
        for issue in document['detail']:
            need(type(issue) is dict and {'type','loc','msg'} <= set(issue) and type(issue['loc']) is list and issue['loc'],
                 '422 detail differs')
            heads.add(str(issue['loc'][0]))
        need(a.equal(normalized.get('body'), {'422_loc0': sorted(heads)}), '422 normalization differs')
        return 'validation-error'
    if not 200 <= status < 300:
        need(a.equal(a.obj(payload.encode('utf-8')), normalized.get('body')), 'error body differs')
        return 'error'
    return 'success-unverified'

def audit_parity_postgres(lifecycle):
    """Audit this parity run's own PostgreSQL commands and observed cleanup."""
    import shlex
    receipt=a.json_file(lifecycle/'receipt.json')
    need(receipt.get('version')=='som.m6-mac-parity-capture.v1' and receipt.get('exit_code')==0
         and receipt.get('recording_errors')==[], 'parity lifecycle incomplete')
    tools=a.json_file(REPO/'som-rs/contracts/rust-output-v1/toolchain.json')['postgresql']
    bindir=Path(tools['bin_dir']); names=('postgres','initdb','pg_ctl')
    expected={name:tools[name]['sha256'] for name in names}
    need(receipt.get('postgres_tools_before')==receipt.get('postgres_tools_after')==expected
         and all(a.sha((bindir/name).read_bytes())==expected[name] for name in names), 'parity postgres tools differ')
    events=receipt['events']
    init=[(i,e) for i,e in enumerate(events) if e.get('kind')=='initdb']
    ctl=[(i,e) for i,e in enumerate(events) if e.get('kind')=='pg_ctl']
    posts=[(i,e) for i,e in enumerate(events) if e.get('kind')=='postmaster']
    snap=[(i,e) for i,e in enumerate(events) if e.get('kind')=='process' and e.get('phase') in ('before-pg-stop','after-stop')]
    need(len(init)==1 and len(ctl)==2 and len(posts)==2 and len(snap)==2
         and [e['phase'] for _,e in posts]==['after-start','before-stop']
         and [e['phase'] for _,e in snap]==['before-pg-stop','after-stop']
         and init[0][0]<ctl[0][0]<posts[0][0]<posts[1][0]<snap[0][0]<ctl[1][0]<snap[1][0],
         'parity postgres event order differs')
    parsed=[]
    for _,event in posts:
        raw=a.read(lifecycle/event['raw']['file']);need(a.sha(raw)==event['raw']['sha256'],'postmaster bytes differ')
        lines=raw.decode().splitlines();need(len(lines)>=4 and lines[0].isdigit() and lines[2].isdigit() and lines[3].isdigit(), 'postmaster invalid')
        parsed.append(lines[:4])
    need(parsed[0]==parsed[1], 'postmaster changed')
    pid=int(parsed[0][0]);data=Path(parsed[0][1]);port=int(parsed[0][3])
    need(pid>1 and data.is_absolute() and data.name=='pgdata' and data.parent.name.startswith('som-behavior-')
         and 1024<=port<=65535 and port not in (8765,18765,18917,18919), 'postmaster identity invalid')
    options=(f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c timezone=UTC "
             f"-c log_timezone=UTC -c max_connections=300 -c fsync=off")
    commands=[(init[0][1],[str(bindir/'initdb'),'-D',str(data),'-U','postgres','-E','UTF8','--locale=C','--auth=trust','--no-sync']),
      (ctl[0][1],[str(bindir/'pg_ctl'),'-D',str(data),'-l',str(data.parent/'postgres.log'),'-o',options,'-w','start']),
      (ctl[1][1],[str(bindir/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'])]
    for row,argv in commands:
        code,_=a.command_raw(lifecycle,row,argv);need(code==0,'parity postgres command failed');a.audit_direct_command_wait(events,row)
    before,after=snap[0][1],snap[1][1]
    need(before['pid']==after['pid']==pid and type(before.get('pgid')) is int and before['pgid']>0,'postgres PID differs')
    ps=['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command=']
    cwd=['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn']
    code,raw=a.command_raw(lifecycle,before['ps'],ps);fields=raw.decode().strip().split(None,3)
    need(code==0 and len(fields)==4 and fields[0]==str(pid) and fields[1].isdigit() and fields[2]==str(before['pgid']), 'postgres process differs')
    argv=shlex.split(fields[3]);need(argv and Path(argv[0]).name=='postgres' and '-D' in argv and argv[argv.index('-D')+1]==str(data),'postgres argv differs')
    code,raw=a.command_raw(lifecycle,before['cwd'],cwd);lines=raw.decode().splitlines()
    need(code==0 and [x[1:] for x in lines if x.startswith('p')]==[str(pid)] and [x[1:] for x in lines if x.startswith('n')]==[str(data)],'postgres cwd differs')
    def table(event):
        code,raw=a.command_raw(lifecycle,event['table'],['/bin/ps','-axo','pid=,ppid=,pgid='])
        need(code==0,'postgres table missing');result={}
        for line in raw.decode().splitlines():
            f=line.split();need(len(f)==3 and all(x.isdigit() for x in f) and int(f[0]) not in result,'postgres table invalid')
            result[int(f[0])]=(int(f[1]),int(f[2]))
        return result
    earlier,later=table(before),table(after);need(pid in earlier and earlier[pid][1]==before['pgid'],'postgres table identity differs')
    owned={pid}
    while True:
        more=owned|{child for child,(parent,_) in earlier.items() if parent in owned}
        if more==owned:break
        owned=more
    need(not owned.intersection(later),'observed postgres process remains')
    listener=['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t']
    for name,command in (('ps',ps),('cwd',cwd),('listener',listener)):
        code,raw=a.command_raw(lifecycle,after[name],command);need(code==1 and not raw,'postgres still present or unknown')
    need(after.get('port')==port and after.get('pgid') is None and after.get('pgid_error_errno')==3,'postgres port or identity differs')
    return {'pid':pid,'port':port,'observed_processes_absent':len(owned),'unobserved_descendants':'unverified'}

def audit_product_owners(root, manifest, groups):
    """Check case-owned process/listener observations and direct child waits."""
    receipt=a.json_file(root.parent/'receipt.json'); events=receipt.get('events'); owners=manifest.get('owners')
    need(receipt.get('version')=='som.m6-mac-parity-capture.v1' and receipt.get('recording_errors')==[]
         and type(events) is list and type(owners) is list, 'product lifecycle missing')
    by_key={}; owner_ids=set()
    for index,owner in enumerate(owners,1):
        key=(owner.get('case'),owner.get('target')); pid=owner.get('pid');base=owner.get('base')
        need(owner.get('id')==index and key not in by_key and type(pid) is int and pid>1, 'product owner identity differs')
        before,after=owner.get('start_event'),owner.get('stop_event')
        need(type(before) is int and type(after) is int and 0<=before<after<len(events), 'product owner sequence differs')
        start,stop=events[before],events[after];port=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(base).port
        need(type(port) is int and all(e.get('kind')=='process' and e.get('pid')==pid and e.get('port')==port
             for e in (start,stop)) and start.get('phase')=='product-ready' and stop.get('phase')=='product-stopped',
             'product owner snapshots differ')
        prior=[(i,e) for i,e in enumerate(events[:before]) if e.get('kind')=='child-start' and e.get('pid')==pid]
        need(len(prior)==1,'product child start missing'); child=prior[0][1]
        cwd=child.get('cwd'); argv=child.get('argv')
        need(type(cwd) is str and Path(cwd).is_absolute() and Path(cwd).name ==
             ('py_' if key[1]=='python' else 'rs_')+case_key(key[0]) and type(argv) is list and argv,
             'product case command identity differs')
        if key[1]=='rust': need(argv == [str(Path(cwd)/'som_app')], 'Rust product command differs')
        else:
            need(len(argv)>=8 and argv[1:3]==['-m','uvicorn'] and '--host' in argv and
                 argv[argv.index('--host')+1]=='127.0.0.1' and '--port' in argv and
                 argv[argv.index('--port')+1]==str(port), 'Python product command differs')
        cwd_argv=['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn']
        code,cwd_raw=a.command_raw(root.parent,start['cwd'],cwd_argv)
        need(code==0 and ('n'+cwd) in cwd_raw.decode().splitlines(), 'product process cwd differs')
        need(any(e.get('kind')=='child-wait' and e.get('identity')==child.get('identity') and e.get('pid')==pid
                 and e.get('state')=='completed' and e.get('waited_pid')==pid and type(e.get('raw_status')) is int
                 and type(e.get('exit_code')) is int and os.waitstatus_to_exitcode(e['raw_status'])==e['exit_code']
                 for e in events[before:after]), 'product direct child wait missing')
        listener=['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t']
        code,raw=a.command_raw(root.parent,start['listener'],listener)
        need(code==0 and set(raw.decode().split())=={str(pid)}, 'product listening PID differs')
        code,raw=a.command_raw(root.parent,stop['listener'],listener)
        need(code==1 and not raw, 'product listener still present or unknown')
        ps=['/bin/ps','-axo','pid=,ppid=,pgid=,command=']
        def table(event):
            code,raw=a.command_raw(root.parent,event['table'],ps);need(code==0, 'product process table missing')
            result={}
            for line in raw.decode().splitlines():
                fields=line.strip().split(None,3)
                need(len(fields)==4 and all(x.isdigit() for x in fields[:3]) and int(fields[0]) not in result,
                     'product process table invalid')
                result[int(fields[0])]=(int(fields[1]),int(fields[2]),fields[3])
            return result
        seen=table(start); after_table=table(stop)
        need(pid in seen and pid not in after_table, 'product process missing or still present')
        command=seen[pid][2]
        need((str(Path(cwd)/'som_app') in command if key[1]=='rust' else 'uvicorn' in command),
             'observed process command differs')
        descendants={pid}
        while True:
            found={child_pid for child_pid,(ppid,_,_) in seen.items() if ppid in descendants}
            updated=descendants|found
            if updated==descendants:break
            descendants=updated
        need(not descendants.intersection(after_table),'observed product descendant still present')
        by_key[key]=owner;owner_ids.add(index)
    need(set(by_key)==set(groups),'product owner inventory differs')
    for key,calls in groups.items():
        owner=by_key[key]
        need(all(row.get('owner')==owner['id'] and row.get('base')==owner['base'] for row in calls),
             'product call is not bound to case process')
    return len(owners)

def audit_product_http(root, transcripts, prompts, *, require_owners=True, check_normalization=True):
    """Join retained byte-exact product exchanges to each saved suite transcript."""
    from urllib.parse import urlsplit
    manifest=a.json_file(root/'manifest.json')
    need(manifest.get('version')=='som.m6-mac-product-http.v1' and manifest.get('recording_errors')==[]
         and manifest.get('product_acceptance_passed') is False and type(manifest.get('calls')) is list,
         'product raw recording differs')
    groups=defaultdict(list); byte_count=0
    for number,row in enumerate(manifest['calls'],1):
        need(row.get('id')==number and row.get('complete') is True and row.get('target') in ('python','rust')
             and type(row.get('case')) is str, 'product call identity differs')
        key=(row['case'],row['target']); groups[key].append(row)
        need(row.get('sequence')==len(groups[key]), 'product sequence differs')
        base=urlsplit(row.get('base',''))
        need(base.scheme=='http' and base.hostname=='127.0.0.1' and base.port and base.port not in (8765,18765,18917,18919)
             and base.path in ('','/') and not base.query and not base.fragment and not base.username and not base.password,
             'product endpoint differs')
        req=f'calls/{number:06d}.request.raw';res=f'calls/{number:06d}.response.raw'
        need(row.get('request_file')==req and row.get('response_file')==res,'product raw paths differ')
        request=a.read(root/req); response=a.read(root/res);byte_count+=len(request)+len(response)
        need(a.sha(request)==row.get('request_sha256') and a.sha(response)==row.get('response_sha256')
             and type(row.get('body_present')) is bool and (row['body_present'] or not request), 'product raw bytes differ')
    need(manifest.get('bytes')==byte_count and set(groups)==set(transcripts),'product call inventory differs')
    owner_count=audit_product_owners(root,manifest,groups) if require_owners else None
    normalized_successes=0; observed_crud_rows=0; oracle_cases=0
    for key,records in transcripts.items():
        need(key[0] in prompts,'source prompt missing')
        normalizer=independent_normalization.Normalizer(prompts[key[0]]) if check_normalization else None
        oracle=independent_oracle.PartialOracle(prompts[key[0]]) if check_normalization else None
        oracle_entries=[]
        calls=groups[key];need(len(calls)==len(records),'product call count differs')
        for row,(record,normalized) in zip(calls,records):
            request=a.read(root/row['request_file']);response=a.read(root/row['response_file'])
            body=request.decode('utf-8','replace') if row['body_present'] else None
            need(row.get('category')==normalized.get('category') and row.get('step')==normalized.get('step')
                 and row.get('step')==record.get('step') and row.get('method')==record.get('method')
                 and row.get('path')==record.get('path') and row.get('content_type')==record.get('content_type')
                 and row.get('status')==record.get('status') and row.get('response_content_type')==record.get('response_content_type')
                 and body==record.get('body') and response.decode('utf-8','replace')==record.get('response'),
                 'product raw call and transcript differ')
            if normalizer is not None:
                normalized_successes += normalizer.check(record,normalized,response)
                observed_crud_rows += oracle.check(record,request,response)
                oracle_entries.append((normalized,request if row['body_present'] else None))
        if normalizer is not None:
            frozen_oracle.check(prompts[key[0]],oracle_entries,include_strict=(key[1]=='rust'))
            oracle_cases+=1
    return {'case_targets':len(groups),'calls':len(manifest['calls']),'bytes':byte_count,'observed_owners':owner_count,
            'source_origin':'requires-workflow-origin-join','normalized_successes':normalized_successes,'observed_crud_rows':observed_crud_rows,
            'oracle_case_targets_checked':oracle_cases,'behavior_scope':'recorded-case-targets-only',
            'unobserved_descendants':'unverified'}

def audit_product_files(lifecycle, expected):
    receipt=a.json_file(lifecycle/'receipt.json');snapshots=receipt.get('product_file_snapshots');events=receipt.get('events')
    manifest=a.json_file(lifecycle/'product-http/manifest.json');owners=manifest.get('owners')
    need(type(snapshots) is list and type(events) is list and type(owners) is list,'product snapshots missing')
    indexed={}; owner_map={(o.get('case'),o.get('target')):o for o in owners}
    need(len(owner_map)==len(owners),'product owners repeat')
    for position,row in enumerate(snapshots,1):
        key=(row.get('case'),row.get('target'));need(row.get('id')==position and key in expected and key not in indexed,'product snapshot identity differs')
        rows=row.get('files');need(type(rows) is list and rows,'product file inventory missing')
        expected_files=expected[key];need(type(expected_files) is dict and len(rows)==len(expected_files),'product file count differs')
        seen=set();total=0
        for index,item in enumerate(rows,1):
            name=item.get('path');rel=item.get('file');mode=item.get('mode');size=item.get('bytes')
            need(type(name) is str and name in expected_files and name not in seen and rel==f'{position:04d}/{index:04d}.raw'
                 and type(mode) is int and 0<=mode<=0o777 and not mode&0o111 and type(size) is int and 0<=size<=16*1024*1024,'product file metadata differs')
            payload=a.read(lifecycle/'product-files'/rel);wanted=expected_files[name]
            need(type(wanted) is str and payload==wanted.encode('utf-8') and a.sha(payload)==item.get('sha256') and len(payload)==size,'assembled and written product differ')
            seen.add(name);total+=size
        need(seen==set(expected_files) and total==row.get('bytes') and total<=128*1024*1024,'product file inventory differs')
        launch=row.get('launch_files');need(type(launch) is list and len(launch)==len(rows),'launch file inventory missing')
        for index,(first,last) in enumerate(zip(rows,launch),1):
            rel=f'{position:04d}/launch-{index:04d}.raw'
            need(last.get('file')==rel and all(last.get(k)==first.get(k) for k in ('path','sha256','bytes','mode')),
                 'launch file metadata differs')
            payload=a.read(lifecycle/'product-files'/rel)
            need(a.sha(payload)==last['sha256'] and len(payload)==last['bytes'] and payload==expected_files[last['path']].encode('utf-8'),
                 'launch source differs from assembled product')
        owner=owner_map.get(key);need(owner is not None,'product snapshot owner missing')
        selected=[(i,e) for i,e in enumerate(events) if e.get('kind')=='product-files' and e.get('snapshot_id')==position]
        need(len(selected)==1 and selected[0][1]=={'kind':'product-files','snapshot_id':position,'case':key[0],'target':key[1],'cwd':row['cwd']},'product snapshot event differs')
        child=[(i,e) for i,e in enumerate(events[:owner['start_event']]) if e.get('kind')=='child-start' and e.get('pid')==owner['pid']]
        launch_event=[(i,e) for i,e in enumerate(events) if e.get('kind')=='product-files-launch' and e.get('snapshot_id')==position]
        need(len(launch_event)==1 and launch_event[0][1]=={'kind':'product-files-launch','snapshot_id':position,'case':key[0],'target':key[1],'cwd':row['cwd']},'launch snapshot event differs')
        need(len(child)==1 and selected[0][0]<launch_event[0][0]<child[0][0] and child[0][1].get('cwd')==row.get('cwd'),'product snapshots are not before the case server')
        indexed[key]=row
    need(set(indexed)==set(expected)==set(owner_map),'product snapshot case set differs')
    return {'case_targets':len(indexed),'file_count':sum(len(x['files']) for x in indexed.values())}

def audit_python_runtimes(lifecycle,cases,files,original_repo):
    import ast
    source=ast.parse(a.read(REPO/'som-rs/behavior/hosts.py').decode('utf-8'))
    values=[ast.literal_eval(node.value) for node in source.body if isinstance(node,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='HOST_SHIM' for t in node.targets)]
    need(len(values)==1 and type(values[0]) is str,'frozen Python shim missing');shim_expected=values[0].encode()
    receipt=a.json_file(lifecycle/'receipt.json');events=receipt['events'];rows=receipt.get('python_runtimes')
    owners=a.json_file(lifecycle/'product-http/manifest.json')['owners'];owner_map={o['case']:o for o in owners if o.get('target')=='python'}
    need(type(rows) is list and len(owner_map)==len(cases),'Python runtime inventory missing')
    executable=str(original_repo/'som-rs/runtime/acceptance-m3-venv/bin/python');seen=set()
    for number,row in enumerate(rows,1):
        case=row.get('case');need(row.get('id')==number and case in cases and case not in seen,'Python runtime case differs')
        owner=owner_map.get(case);need(owner is not None,'Python owner missing');cwd=row.get('cwd')
        need(type(cwd) is str and Path(cwd).is_absolute() and row.get('interpreter')==executable,'Python interpreter path differs')
        expected_files=files[(case,'python')];names={name.split('/')[0] for name in expected_files if name.count('/')==1 and name.endswith('/api.py')}
        need(len(names)==1,'Python package differs');package=next(iter(names));version=cases[case]['version']
        port=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(owner['base']).port
        shim=str(Path(cwd).parent/'_shim/som_behavior_host.py')
        if version=='som.prompt.v5': app=package+'.app:app';extra=[]
        else:app='som_behavior_host:app';extra=['--app-dir',str(Path(shim).parent)]
        argv=[executable,'-m','uvicorn',app,*extra,'--host','127.0.0.1','--port',str(port),'--no-access-log','--log-level','warning']
        need(row.get('argv')==argv and row.get('shim_path')==shim and row.get('shim_file')==f'shim-{number:04d}.raw','Python invocation differs')
        retained=a.read(lifecycle/'python-runtimes'/row['file']);need(row['file']==row['sha256']+'.raw' and a.sha(retained)==row['sha256'] and len(retained)==row.get('bytes'),'Python interpreter bytes differ')
        shim_raw=a.read(lifecycle/'python-runtimes'/row['shim_file']);need(shim_raw==shim_expected and a.sha(shim_raw)==row.get('shim_sha256'),'Python shim differs')
        need(all(type(row.get(k)) is int and row[k]>0 for k in ('device','inode','bytes')) and type(row.get('mode')) is int and row['mode']&0o111,'Python interpreter stat differs')
        pre=[(i,e) for i,e in enumerate(events) if e.get('kind')=='python-runtime' and e.get('runtime_id')==number]
        need(len(pre)==1 and pre[0][1].get('case')==case and pre[0][1].get('cwd')==cwd,'Python runtime event differs')
        launch=[i for i,e in enumerate(events[:pre[0][0]]) if e.get('kind')=='product-files-launch' and e.get('case')==case and e.get('target')=='python' and e.get('cwd')==cwd]
        need(len(launch)==1,'Python launch source snapshot missing')
        children=[(i,e) for i,e in enumerate(events[pre[0][0]+1:owner['start_event']],pre[0][0]+1) if e.get('kind')=='child-start' and e.get('pid')==owner['pid']]
        need(len(children)==1 and children[0][1].get('argv')==argv and children[0][1].get('cwd')==cwd,'Python server child differs')
        env=children[0][1].get('environment');need(type(env) is dict and env.get('PYTHONPATH')==cwd and env.get('PYTHONDONTWRITEBYTECODE')=='1','Python import path differs')
        if version!='som.prompt.v5':need(env.get('SOM_BEHAVIOR_PACKAGE')==package,'Python shim package differs')
        snapshot=events[owner['start_event']];pid=owner['pid']
        code,raw=a.command_raw(lifecycle,snapshot['image_stat'],['/usr/bin/stat','-L','-f','%d %i %z %Lp',executable]);parts=raw.decode().split()
        need(code==0 and len(parts)==4 and all(x.isdigit() for x in parts) and [int(parts[0]),int(parts[1]),int(parts[2]),int(parts[3],8)]==[row[k] for k in ('device','inode','bytes','mode')],'Python interpreter live stat differs')
        code,started=a.command_raw(lifecycle,snapshot['start_time'],['/bin/ps','-p',str(pid),'-o','lstart=']);need(code==0 and started.strip(),'Python process start time missing')
        code,image=a.command_raw(lifecycle,snapshot['image'],['/usr/sbin/lsof','-nP','-a','-p',str(pid),'-d','txt','-FfDin']);need(code==0,'Python mapped image unavailable')
        records=[];record=None
        for line in image.decode().splitlines():
            if line.startswith('f'):record={'fd':line[1:]};records.append(record)
            elif record is not None and line[:1] in ('D','i','n'):
                need(line[0] not in record,'Python mapped image field repeated');record[line[0]]=line[1:]
        found=False
        for item in records:
            try:
                if item.get('fd')=='txt' and int(item.get('D',''),0)==row['device'] and int(item.get('i',''))==row['inode']:found=True
            except ValueError:pass
        need(found,'Python mapped interpreter inode differs');seen.add(case)
    need(seen==set(cases)==set(owner_map),'Python runtime cases differ')
    return {'cases':len(seen),'interpreter_images':'observed','source_tree':'same-case-assembled-files'}

def audit_rust_binaries(lifecycle, expected_cases, original_target=None):
    receipt=a.json_file(lifecycle/'receipt.json');events=receipt['events'];rows=receipt.get('rust_binary_copies')
    owners=a.json_file(lifecycle/'product-http/manifest.json')['owners']
    owner_map={o['case']:o for o in owners if o.get('target')=='rust'}
    need(type(rows) is list and len(owner_map)==len(expected_cases),'rust binary inventory missing')
    tools=a.json_file(REPO/'som-rs/contracts/rust-output-v1/toolchain.json')['rust']
    names=('rustc','cargo','rustfmt','cargo-fmt','cargo-clippy','clippy-driver')
    expected_tools={name:tools[name]['sha256'] for name in names}
    need(receipt.get('rust_tools_before')==receipt.get('rust_tools_after')==expected_tools,'rust toolchain observation differs')
    cargo=[str(Path(tools['bin_dir'])/'cargo'),'build','--locked','--offline','-q']
    target=original_target if original_target is not None else lifecycle.parent.parent/'rust006-parity-target/debug/som_app'
    seen=set()
    for number,row in enumerate(rows,1):
        case=row.get('case');need(row.get('id')==number and case in expected_cases and case not in seen,'rust binary case differs')
        owner=owner_map.get(case);need(owner is not None,'rust binary owner missing')
        raw=a.read(lifecycle/'product-binaries'/f'{number:04d}.raw')
        need(row.get('file')==f'{number:04d}.raw' and a.sha(raw)==row.get('sha256') and row.get('source')==str(target),'rust binary bytes differ')
        source=row.get('source_stat');copied=row.get('target_stat')
        need(type(source) is dict and type(copied) is dict and source.get('size')==copied.get('size')==len(raw)
             and all(type(copied.get(k)) is int and copied[k]>0 for k in ('device','inode','size'))
             and type(copied.get('mode')) is int and copied['mode']&0o111,'rust binary stat differs')
        copies=[(i,e) for i,e in enumerate(events) if e.get('kind')=='product-binary' and e.get('binary_id')==number]
        need(len(copies)==1 and copies[0][1].get('case')==case and copies[0][1].get('cwd')==str(Path(row['target']).parent),'rust copy event differs')
        ci=copies[0][0];cwd=str(Path(row['target']).parent)
        starts=[(i,e) for i,e in enumerate(events[:ci]) if e.get('kind')=='child-start' and e.get('argv')==cargo and e.get('cwd')==cwd]
        need(len(starts)==1,'rust cargo child missing');si,start=starts[0]
        env=start.get('environment');bindir=Path(tools['bin_dir']);target_dir=target.parent.parent
        need(type(env) is dict and env.get('CARGO_TARGET_DIR')==str(target_dir) and env.get('CARGO_NET_OFFLINE')=='true'
             and env.get('RUSTC')==str(bindir/'rustc') and env.get('CARGO')==str(bindir/'cargo')
             and env.get('RUSTFMT')==str(bindir/'rustfmt') and type(env.get('PATH')) is str
             and env['PATH'].startswith(str(bindir)+':'),'rust cargo environment differs')
        cleans=[(i,e) for i,e in enumerate(events[:si]) if e.get('kind')=='rust-rebuild-clean' and e.get('case')==case]
        need(len(cleans)==1 and cleans[0][1].get('target')==str(target_dir) and type(cleans[0][1].get('before')) is list
             and len(cleans[0][1]['before'])<=4096 and cleans[0][1].get('after')==[],'rust pre-build cleanup differs')
        snapshots=[(i,e) for i,e in enumerate(events[:si]) if e.get('kind')=='product-files' and e.get('case')==case and e.get('target')=='rust' and e.get('cwd')==cwd]
        need(len(snapshots)==1 and snapshots[0][0]<cleans[0][0]<si,'rust assembled source or cleanup order differs')
        waits=[(i,e) for i,e in enumerate(events[si+1:ci],si+1) if e.get('kind')=='child-wait' and e.get('identity')==start.get('identity') and e.get('pid')==start.get('pid') and e.get('state')=='completed']
        need(len(waits)==1 and waits[0][1].get('waited_pid')==start.get('pid') and waits[0][1].get('raw_status')==0 and waits[0][1].get('exit_code')==0,'rust cargo actual wait missing')
        hashes=[(i,e) for i,e in enumerate(events[waits[0][0]+1:ci],waits[0][0]+1) if e.get('kind')=='product-binary-hash' and e.get('binary_id')==number]
        need(len(hashes)==2 and [e.get('label') for _,e in hashes]==['source','target'],'rust binary hash commands missing')
        for (_,event),name in zip(hashes,('source','target')):
            path=row[name];code,content=a.command_raw(lifecycle,event,['/usr/bin/shasum','-a','256',path])
            a.audit_direct_command_wait(events,event,since=waits[0][0]+1)
            need(code==0 and content.decode()==row['sha256']+'  '+path+'\n','rust binary hash command differs')
        product=[(i,e) for i,e in enumerate(events[ci+1:owner['start_event']],ci+1) if e.get('kind')=='child-start' and e.get('pid')==owner['pid']]
        launch=[i for i,e in enumerate(events[ci+1:owner['start_event']],ci+1) if e.get('kind')=='product-files-launch' and e.get('case')==case and e.get('target')=='rust' and e.get('cwd')==cwd]
        need(len(product)==len(launch)==1 and launch[0]<product[0][0] and product[0][1].get('cwd')==cwd and product[0][1].get('argv')==[row['target']],'rust server child or launch source differs')
        start_snapshot=events[owner['start_event']];pid=owner['pid']
        code,raw_stat=a.command_raw(lifecycle,start_snapshot['image_stat'],['/usr/bin/stat','-f','%d %i %z %Lp',row['target']])
        fields=raw_stat.decode().split();need(code==0 and len(fields)==4 and all(f.isdigit() for f in fields)
             and [int(fields[0]),int(fields[1]),int(fields[2]),int(fields[3],8)]==[copied[k] for k in ('device','inode','size','mode')],'rust live binary stat differs')
        code,raw_time=a.command_raw(lifecycle,start_snapshot['start_time'],['/bin/ps','-p',str(pid),'-o','lstart='])
        need(code==0 and raw_time.decode().strip(),'rust process start time missing')
        code,raw_image=a.command_raw(lifecycle,start_snapshot['image'],['/usr/sbin/lsof','-nP','-a','-p',str(pid),'-d','txt','-FfDin'])
        need(code==0,'rust mapped image unavailable');mapped=[];record=None
        for line in raw_image.decode().splitlines():
            if line.startswith('f'):record={'fd':line[1:]};mapped.append(record)
            elif record is not None and line[:1] in ('D','i','n'):
                need(line[0] not in record,'duplicate mapped image field');record[line[0]]=line[1:]
        matched=False
        for record in mapped:
            try:
                if record.get('fd')=='txt' and int(record.get('D',''),0)==copied['device'] and int(record.get('i',''))==copied['inode']:matched=True
            except ValueError:pass
        need(matched,'rust process image inode differs');seen.add(case)
    need(seen==set(expected_cases)==set(owner_map),'rust binary set differs')
    return {'cases':len(seen),'retained_binaries':len(rows)}

def audit_wire(root,parity_out,legacy,bound,product_http,original_target=None,original_repo=None):
    cases=source_cases(legacy,bound)
    keys=[case_key(name) for name,_ in cases]
    need(len(set(keys))==len(keys),'raw transcript filename collision')
    indexed,total=raw_exchange_index(root)
    need(total==len(cases)*4,'compiler call count differs')
    report=a.json_file(parity_out/'report-parity.json')
    rows=report.get('rows');summary=report.get('summary')
    need(type(rows) is list and type(summary) is dict and summary.get('mode')=='parity'
         and summary.get('complete') is True and len(rows)==len(cases),'parity report incomplete')
    result={r['id']:r for r in rows}
    need(len(result)==len(rows) and set(result)=={x[0] for x in cases},'parity IDs differ')
    transcript_steps=0;strict_steps=0;checked_errors=0; transcripts={};product_files={}
    for identity,prompt in cases:
        row=result[identity]
        for target in ('python','rust'):
            request={'format':'som.prompt-json.v1','prompt':prompt}
            if target=='rust':request['target']='rust'
            raw=take(indexed,'/v1/compile',request)
            bundle=a.obj(raw)['bundle']
            assembled=take(indexed,'/v1/assemble',{'bundle':bundle})
            files=a.obj(assembled)['files'];product_files[(identity,target)]=files
            need(a.sha(a.compact(files))==row[target+'_files_sha256'],'candidate product bytes differ')
        data={}
        for target,prefix in (('python','py'),('rust','rs')):
            path=parity_out/'raw'/(prefix+'_'+case_key(identity)+'.json')
            raw=a.read(path)
            need(a.sha(raw)==row[target+'_raw_sha256'],'parity raw transcript hash differs')
            data[target]=a.obj(raw)
            need(type(data[target].get('raw')) is list and type(data[target].get('transcript')) is list
                 and type(data[target].get('failures')) is list,'parity raw structure differs')
        py,rs=data['python'],data['rust']
        transcripts[(identity,'python')]=list(zip(py['raw'],py['transcript'])); transcripts[(identity,'rust')]=list(zip(rs['raw'],rs['transcript']))
        need(not py['failures'] and not rs['failures'] and row.get('passed') is True and
             all(row.get(k)==0 for k in ('python_failures','rust_failures','strict_failures','differences')),
             'parity run or oracle reported failure')
        steps=row.get('steps');strict=row.get('strict_steps')
        need(type(steps) is int and steps>0 and type(strict) is int and strict>=0
             and len(py['raw'])==len(py['transcript'])==steps
             and len(rs['raw'])==len(rs['transcript'])==steps+strict,'parity step count differs')
        for raw_list, transcript in ((py['raw'],py['transcript']),(rs['raw'],rs['transcript'])):
            for raw, normalized in zip(raw_list,transcript):
                kind=check_record(raw,normalized)
                checked_errors += kind != 'success-unverified'
                need(raw['status'] != 500, 'parity returned 500')
        for left,right in zip(py['transcript'],rs['transcript'][:steps]):
            for key in ('step','category','request','status','media','body'):
                need(key in left and key in right and a.equal(left[key],right[key]), 'normalized parity differs')
        for left,right in zip(py['raw'],rs['raw'][:steps]):
            need(left.get('step')==right.get('step') and left.get('method')==right.get('method')
                 and left.get('status')==right.get('status'),'parity wire status differs')
        for record in rs['raw'][steps:]:
            match=re.match(r'^(D[1-5]): ',record.get('step',''))
            if match:
                want={'D1':422,'D2':422,'D3':404,'D4':422,'D5':404}[match.group(1)]
                need(record['status']==want,'strict refusal status differs')
            else:
                need('D1 to D5 changed no row' in record.get('step','') or
                     'D1 to D5 added no row' in record.get('step',''),'unknown strict step')
                need(record['status']==200,'post-strict check failed')
        transcript_steps+=steps;strict_steps+=strict
    need(not any(indexed.values()),'unexpected parity compiler calls')
    product_evidence=audit_product_http(product_http,transcripts,dict(cases))
    file_evidence=audit_product_files(product_http.parent,product_files)
    binary_evidence=audit_rust_binaries(product_http.parent,{identity for identity,_ in cases},original_target)
    python_evidence=audit_python_runtimes(product_http.parent,dict(cases),product_files,original_repo if original_repo is not None else REPO)
    return {'product_http':product_evidence,'product_files':file_evidence,'rust_binaries':binary_evidence,'python_runtimes':python_evidence,'cases':len(cases),'compiler_calls':total,'parity_steps':transcript_steps,'strict_steps':strict_steps,
            'checked_error_or_empty_bodies':checked_errors,'success_body_normalization':'recomputed-on-recorded-successes','behavior_oracle':'recomputed-on-recorded-cases'}

def main():
    p=argparse.ArgumentParser()
    for x in ('raw','parity-out','public-run','legacy-run','out'):p.add_argument('--'+x,type=Path,required=True)
    p.add_argument('--product-http',type=Path,required=True)
    p.add_argument('--candidate-revision',required=True)
    p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--approved-input-config',type=Path,required=True)
    p.add_argument('--provenance-root',type=Path,required=True)
    args=p.parse_args();need(not args.out.exists(),'new report required')
    result={'version':'som.m6-mac-parity-audit.draft.v1','status':'unrun','product_acceptance_passed':False,'rows':[]}
    observed=False
    try:
        source=audit_sources(args.candidate_revision,args.product_http.parent,args.raw,args.parity_out)
        candidate=a.audit_candidate(args.product_http.parent/'candidate-probe',args.candidate,args.candidate_revision)
        result['rows'].append({'id':'same-candidate-runtime','status':'observed','evidence':candidate})
        result['rows'].append({'id':'same-head-sources','status':'observed','evidence':source})
        bound=a.approved_inputs(args.public_run,args.legacy_run,args.approved_input_config,args.provenance_root)
        result['rows'].append({'id':'raw-structure','status':'observed','evidence':audit_wire(args.raw,args.parity_out,args.legacy_run,bound,args.product_http)})
        observed=True
    except Exception as exc:result['rows'].append({'id':'raw-structure','status':'unrun','error':type(exc).__name__+': '+str(exc)})
    for identity in ('body-normalization','behavior-oracle','process-cleanup','workflow-origin'):
        result['rows'].append({'id':identity,'status':'unrun'})
    result['scoped_raw_status']='observed' if observed else 'unrun'
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as stream:json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
    result_status='observed' if observed else 'unrun'
    print(json.dumps({'status':result_status,'product_acceptance_passed':False}))
    return 0 if observed else 1

if __name__=='__main__':raise SystemExit(main())
