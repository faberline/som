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
import re
from pathlib import Path
import sys
import audit_mac_rust006_extra as a

REPO=Path(__file__).resolve().parents[4]
QA='http://127.0.0.1:18919'

def need(ok,why):
    if not ok:raise ValueError(why)

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

def audit_wire(root,parity_out,legacy,bound):
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
    transcript_steps=0;strict_steps=0
    for identity,prompt in cases:
        row=result[identity]
        for target in ('python','rust'):
            request={'format':'som.prompt-json.v1','prompt':prompt}
            if target=='rust':request['target']='rust'
            raw=take(indexed,'/v1/compile',request)
            bundle=a.obj(raw)['bundle']
            assembled=take(indexed,'/v1/assemble',{'bundle':bundle})
            files=a.obj(assembled)['files']
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
        steps=row.get('steps');strict=row.get('strict_steps')
        need(type(steps) is int and steps>0 and type(strict) is int and strict>=0
             and len(py['raw'])==len(py['transcript'])==steps
             and len(rs['raw'])==len(rs['transcript'])==steps+strict,'parity step count differs')
        for raw_list, transcript in ((py['raw'],py['transcript']),(rs['raw'],rs['transcript'])):
            for raw, normalized in zip(raw_list,transcript):
                need(raw.get('step')==normalized.get('step') and raw.get('method')==normalized.get('request',{}).get('method')
                     and type(raw.get('status')) is int and raw['status']==normalized.get('status')
                     and raw['status']!=500,'parity raw/normalized metadata differs')
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
    return {'cases':len(cases),'compiler_calls':total,'parity_steps':transcript_steps,'strict_steps':strict_steps,
            'body_normalization':'unverified','behavior_oracle':'unverified'}

def main():
    p=argparse.ArgumentParser()
    for x in ('raw','parity-out','public-run','legacy-run','out'):p.add_argument('--'+x,type=Path,required=True)
    args=p.parse_args();need(not args.out.exists(),'new report required')
    result={'version':'som.m6-mac-parity-audit.draft.v1','status':'unrun','product_acceptance_passed':False,'rows':[]}
    try:
        bound=a.approved_inputs(args.public_run,args.legacy_run)
        result['rows'].append({'id':'raw-structure','status':'observed','evidence':audit_wire(args.raw,args.parity_out,args.legacy_run,bound)})
    except Exception as exc:result['rows'].append({'id':'raw-structure','status':'unrun','error':type(exc).__name__+': '+str(exc)})
    for identity in ('body-normalization','behavior-oracle','process-cleanup','workflow-origin'):
        result['rows'].append({'id':identity,'status':'unrun'})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as stream:json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
    print(json.dumps({'status':'unrun','product_acceptance_passed':False}))
    return 1

if __name__=='__main__':raise SystemExit(main())
