#!/usr/bin/env python3
"""Calibrate recorded request slots; no server and no product acceptance.

Run only after the caller restores and verifies the approved input packages and
#130 evidence into separate scratch roots. Missing source bytes are unrun.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
import stat
import urllib.parse

INVENTORY_SHA = '83489c5cc729b7f79e513cfbf2b6c7c93cca717daff093a19f101d566ca6c44f'
EVIDENCE_INDEX_SHA = '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea'
REPORTS = {'python':'0678867a693a8707cd03aa4a5442556aaf42cbc1fb90e47cf849bf701422e70a',
           'rust':'37c74335072cc93a64eb7663cc90c1fdd522e4d4be6a4f47e23f82b18e434b84',
           'ts':'f4bbfd8e12c8616d5134fa01e05d5ba8facbbd7615cf376373d6bf958008f5ad'}
COUNTS = {'react_python':2684, 'rust006':699, 'typescript125':492}
class Unrun(Exception): pass
class Invalid(Exception): pass

def require(value, message):
    if not value: raise Invalid(message)

def digest(data): return hashlib.sha256(data).hexdigest()
def read(path):
    if not path.is_file() or path.is_symlink(): raise Unrun('missing source')
    return path.read_bytes()
def doc(path): return json.loads(read(path))
def relative(value):
    p=Path(value)
    require(not p.is_absolute() and all(x not in ('', '.', '..') for x in p.parts), 'invalid member path')
    return p

def evidence_manifest(sealed):
    raw=read(sealed/'evidence-index.json')
    require(digest(raw)==EVIDENCE_INDEX_SHA, 'evidence index differs')
    index=json.loads(raw); members={}
    for package in index['packages']:
        manifest_raw=read(sealed/relative(package['manifest']))
        require(digest(manifest_raw)==package['manifest_sha256'], 'manifest differs')
        manifest=json.loads(manifest_raw)
        require(manifest['cipher_sha256']==package['cipher_sha256'], 'cipher identity differs')
        for item in manifest['files']:
            name=str(relative(item['path']))
            require(name not in members, 'duplicate evidence member')
            members[name]=item['sha256']
    return members

class Sources:
    def __init__(self, root, members): self.root=root; self.members=members; self.cache={}
    def read(self, member):
        name=str(relative(member))
        if name not in self.cache:
            expected=self.members.get(name)
            if expected is None: raise Unrun('unbound evidence member')
            value=read(self.root/name)
            require(digest(value)==expected, 'evidence bytes differ')
            self.cache[name]=value
        return self.cache[name]
    def json(self, member): return json.loads(self.read(member))

def source_candidate(slot, candidate, sources, manifests):
    require(candidate.get('manifest_bound') is True, 'candidate unbound')
    req=sources.read(candidate['request_member']); resp=sources.read(candidate['response_member'])
    require(digest(req)==candidate['request_package']['sha256']==slot['historical_request_sha256'], 'request hash differs')
    require(digest(resp)==candidate['response_package']['sha256']==candidate['response_sha256'], 'response hash differs')
    require(candidate['status']==slot['report_status'] and digest(resp)==slot['report_response_sha256'], 'report response differs')
    if candidate['capture'] in ('python','rust'):
        name=candidate['capture']; call_id=candidate['call_id']; call=manifests[name][call_id]
        prefix=f'run/{name}-baseline-raw/calls/{call_id:06d}'
        require(sources.json(prefix+'.json')==call and call['complete'] is True and call['error'] is None, 'recorder call differs')
        parsed=urllib.parse.urlsplit(call['url'])
        require(call['method']==slot['method'] and parsed.scheme=='http' and parsed.hostname=='127.0.0.1'
                and parsed.port==18917 and parsed.path==slot['endpoint'] and not parsed.query and not parsed.fragment,
                'recorded endpoint differs')
        require(candidate['request_member']==prefix+'.request.raw' and candidate['response_member']==prefix+'.response.raw'
                and call['request_sha256']==digest(req) and call['response_sha256']==digest(resp)
                and call['status']==candidate['status'], 'recorder bytes differ')
    elif candidate['capture']=='ts125':
        # TS check_typescript_baseline saves request and response bytes directly;
        # its original report is the status/endpoint authority, not a wire recorder.
        require(slot['method']=='POST' and slot['endpoint'] in ('/v1/compile','/v1/assemble'), 'TS endpoint differs')
    else: raise Invalid('unknown capture')
    return req,resp

def verify_inputs(repo, input_root, config_path):
    sys.path.insert(0,str(repo/'som-rs/scripts/react_output_acceptance'))
    import fetch_hosted_inputs as fetch
    config=doc(config_path)
    require(config.get('commit')=='1de8f05430a72f8d76f6159786cca71b0ae1a911' and
            config.get('index_sha256')=='c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf',
            'input authority differs')
    prefixes=('.local/rust-compiler-goal/acceptance/public-products-003b/',
              '.local/rust-compiler-goal/acceptance/legacy544/',
              '.local/rust-compiler-goal/acceptance/rust-output-055/run-003/',
              '.local/rust-output-phase2/rs-products-001/')
    selected={}
    for package in fetch.load_manifests(repo,config):
        for item in package['manifest']['files']:
            name=item['path']
            if name.startswith(prefixes):
                require(name not in selected, 'duplicate approved input')
                selected[name]=item
    require(selected, 'no approved inputs')
    for name,item in selected.items():
        path=input_root/relative(name); raw=read(path)
        require(len(raw)==item['bytes'] and digest(raw)==item['sha256'] and
                stat.S_IMODE(path.stat().st_mode)==item['mode'], 'approved input differs')
    return len(selected)

def owner_inputs(repo, input_root):
    tsdir=repo/'som-rs/scripts/typescript_output_acceptance'
    reactdir=repo/'som-rs/scripts/react_output_acceptance'
    sys.path[:0]=[str(reactdir),str(tsdir)]
    import check_python_baseline as py
    import check_rust_baseline as rust
    import check_bundles as ts
    import check_typescript_baseline as ts_owner
    acceptance=input_root/'.local/rust-compiler-goal/acceptance'
    public=acceptance/'public-products-003b'; legacy=acceptance/'legacy544/run-005'
    public_rows,_=py.read_public_inputs(public); legacy_rows,_=py.read_legacy_inputs(legacy)
    require(len(public_rows)==127 and len(legacy_rows)==544, 'Python input counts differ')
    python={(r['source'],r['id']):r for r in public_rows+legacy_rows}
    require(len(python)==671, 'duplicate Python input')
    rust.RUNTIME_ROOT=input_root
    run=acceptance/'rust-output-055/run-003'
    r1,_,p1=rust.load_phase1_cases(run); r2,_,p2=rust.load_phase2_cases(run)
    require(not p1 and not p2, 'Rust loader problems')
    rust_rows={(r['phase'],r['id']):r for r in r1+r2}
    require(len(rust_rows)==699, 'Rust input counts differ')
    inventory=doc(ts.CONTRACT/'case-inventory.json'); examples=doc(ts.CONTRACT/'target-examples.json')
    ts_cases=ts.collect_in_scope_cases(inventory,examples)
    old,problems=ts.collect_legacy_in_scope_cases(inventory,legacy,ts.EXPECTED_LEGACY_IN_SCOPE)
    require(not problems, 'TS collector problems')
    cases={(c['group'],c['id']):c for c in ts_cases+old}
    require(len(cases)==125, 'TS input counts differ')
    return py,rust,ts,ts_owner,python,rust_rows,cases

def build_requests(slots, sources, modules):
    py,rust,ts,ts_owner,python,rust_rows,cases=modules
    # Each assembly starts from its same-case and same-form captured before compile response.
    bykey={(s['cohort'],s['group'],s['case'],s['phase'],s['form'],s['variant'],s['endpoint']):s for s in slots}
    require(len(bykey)==len(slots), 'duplicate slot key')
    ts_report=sources.json('run/ts-baseline/report.json')
    require(digest(sources.read('run/ts-baseline/report.json'))==REPORTS['ts'] and ts_report['passed'] is True,
            'TS report differs')
    ts_rows={(r['group'],r['id']):r for r in ts_report['cases']}
    require(len(ts_rows)==125, 'TS report count differs')
    def request(s):
        cohort=s['cohort']; endpoint=s['endpoint']
        if cohort=='react_python':
            item=python.get((s['group'],s['case']))
            if not item or item.get('not_run_reason') or item.get('body') is None: raise Unrun('Python input unavailable')
            body=item['body']; require('target' not in body, 'unexpected Python target')
            if endpoint=='/v1/compile':
                if s['variant']=='none':
                    raw=item.get('raw_bytes')
                    if raw is not None:
                        try:
                            if json.loads(raw.decode('utf-8'))==body: return raw
                        except (ValueError,UnicodeDecodeError): pass
                    return py.compact_json_bytes(body)
                require(s['variant']=='python', 'unknown Python variant')
                return py.compact_json_bytes({**body,'target':'python'})
        elif cohort=='rust006':
            item=rust_rows.get((s['phase'],s['case']))
            if not item or item.get('not_run_reason') or not isinstance(item.get('prompt'),dict): raise Unrun('Rust input unavailable')
            require(endpoint=='/v1/compile','unexpected Rust endpoint')
            return rust.compact_json_bytes({'format':'som.prompt-json.v1','target':'rust','prompt':item['prompt']})
        elif cohort=='typescript125':
            case=cases.get((s['group'],s['case']))
            if not case or case.get('not_run_error') or case.get('file_problems'): raise Unrun('TS input unavailable')
            if endpoint=='/v1/compile':
                matches=[body.encode('utf-8') for form,body in case['requests'] if form==s['form']]
                if not matches and case.get('derive_json_from_cnl_bundle') and s['form']=='json':
                    row=ts_rows[(s['group'],s['case'])]; prior=row['requests'][0]
                    qa_raw=sources.read('run/ts-baseline/'+prior['qa_response_file'])
                    require(prior['qa_status']==200 and digest(qa_raw)==prior['qa_sha256'], 'TS derived QA source differs')
                    bundle=json.loads(qa_raw)['bundle']
                    return ts_owner.compact({'format':'som.prompt-json.v1','prompt':bundle['prompt'],'target':'typescript'}).encode('utf-8')
                require(len(matches)==1,'TS request form differs')
                return matches[0]
        else: raise Invalid('unknown cohort')
        require(endpoint=='/v1/assemble', 'unknown endpoint')
        key=(cohort,s['group'],s['case'],s['phase'],s['form'],s['variant'],'/v1/compile')
        compile_slot=bykey.get(key)
        if not compile_slot or not compile_slot['source_candidates']: raise Unrun('compile response unavailable')
        responses=[sources.read(c['response_member']) for c in compile_slot['source_candidates']]
        require(all(raw==responses[0] for raw in responses), 'compile responses conflict')
        require(digest(responses[0])==compile_slot['report_response_sha256'] and compile_slot['report_status']==200,
                'compile response identity differs')
        bundle=json.loads(responses[0])['bundle']
        if cohort=='react_python': return py.compact_json_bytes({'bundle':bundle})
        return ts_owner.compact({'bundle':bundle}).encode('utf-8')
    return request

def run(repo,input_root,evidence,sealed,inventory,out,config):
    require(digest(read(inventory))==INVENTORY_SHA, 'inventory identity differs')
    slots=doc(inventory)['slots']; active=[s for s in slots if s['cohort'] in COUNTS]
    counts=collections.Counter(s['cohort'] for s in active)
    require(dict(counts)==COUNTS and len({s['slot_id'] for s in active})==3875, 'slot scope differs')
    require(sum(s['cohort']=='rust006_conditional_assemble' for s in slots)==697, 'fresh-before scope differs')
    sources=Sources(evidence,evidence_manifest(sealed))
    for name,path in [('python','run/python-baseline/report.json'),('rust','run/rust-baseline/report.json'),('ts','run/ts-baseline/report.json')]:
        require(digest(sources.read(path))==REPORTS[name], 'report identity differs')
    manifests={}
    for name in ('python','rust'):
        raw=sources.json('run/'+name+'-baseline-raw/manifest.json')
        require(raw['exit_code']==0 and not raw['recording_errors'], 'recording failed')
        calls=raw['calls']; manifests[name]={c['id']:c for c in calls}
        require(len(manifests[name])==len(calls), 'duplicate recorder id')
    ts_report=sources.json('run/ts-baseline/report.json')
    ts_rows={(row['group'],row['id']):row for row in ts_report['cases']}
    for slot in active:
        if slot['cohort']!='typescript125': continue
        row=ts_rows[(slot['group'],slot['case'])]
        matching=[r for r in row['requests'] if r['form']==slot['form']]
        require(row['passed'] is True and len(matching)==1, 'TS report slot differs')
        record=matching[0]; action='compile' if slot['endpoint']=='/v1/compile' else 'assemble'
        prefix='baseline' if action=='compile' else 'baseline_assemble'
        request_name=record['request_file'] if action=='compile' else record['baseline_assemble_request_file']
        request_sha=record['body_sha256'] if action=='compile' else record['baseline_assemble_request_sha256']
        slot['report_status']=record[prefix+'_status']
        slot['report_response_sha256']=record[prefix+'_sha256']
        require(slot['historical_request_sha256']==request_sha, 'TS report request differs')
        for c in slot['source_candidates']:
            if c['capture']=='ts125':
                require(c['request_member']=='run/ts-baseline/'+request_name and
                        c['response_member']=='run/ts-baseline/'+record[prefix+'_response_file'],
                        'TS report member differs')
    approved_file_count=verify_inputs(repo,input_root,config)
    modules=owner_inputs(repo,input_root); construct=build_requests(active,sources,modules)
    rows=[]
    for s in active:
        row={'slot_id':s['slot_id'],'cohort':s['cohort'],'case':s['case'],'method':s['method'],
             'endpoint':s['endpoint'],'source_candidates':len(s['source_candidates']),'state':'unrun',
             'runtime_comparison':'unrun','historical_request_sha256':s['historical_request_sha256']}
        try:
            require(s['method']=='POST' and s['endpoint'] in ('/v1/compile','/v1/assemble'), 'slot route differs')
            candidates=s['source_candidates']
            if not candidates: raise Unrun('no recorded candidate')
            recorded=[source_candidate(s,c,sources,manifests) for c in candidates]
            require(all(item==recorded[0] for item in recorded), 'candidate bytes conflict')
            created=construct(s)
            require(digest(created)==s['historical_request_sha256'], 'serializer SHA differs')
            require(all(created==pair[0] for pair in recorded), 'serializer bytes differ')
            row.update(state='calibrated',rebuilt_sha256=digest(created),verified_candidates=len(recorded))
        except Unrun as e: row['reason']=str(e)
        except Exception as e: row.update(state='failed',reason=type(e).__name__+': '+str(e))
        rows.append(row)
    totals=collections.Counter((r['cohort'],r['state']) for r in rows)
    result={'version':'som.recorded-before-calibration.v1','inventory_sha256':INVENTORY_SHA,
            'evidence_index_sha256':EVIDENCE_INDEX_SHA,'approved_file_count':approved_file_count,'roles':counts,'rows':rows,
            'counts':[{'cohort':c,'state':state,'count':n} for (c,state),n in sorted(totals.items())],
            'fresh_before_assemble_697':'unrun','runtime_comparison':'unrun','product_acceptance':'not_run',
            'passed':len(rows)==3875 and all(r['state']=='calibrated' for r in rows)}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f: json.dump(result,f,sort_keys=True,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ('counts','fresh_before_assemble_697','runtime_comparison','product_acceptance','passed')},sort_keys=True))
    return 0 if result['passed'] else 1

def main():
    p=argparse.ArgumentParser()
    for name in ('repo','input-root','evidence','sealed','inventory','out','config'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try:
        return run(a.repo,a.input_root,a.evidence,a.sealed,a.inventory,a.out,a.config)
    except Exception as exc:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('x') as f:
            json.dump({'version':'som.recorded-before-calibration.v1','passed':False,
                       'stage':'preparation','error_type':type(exc).__name__,
                       'unrun':3875,'fresh_before_assemble_697':'unrun',
                       'runtime_comparison':'unrun','product_acceptance':'not_run'},f,sort_keys=True);f.write('\n')
        return 1
if __name__=='__main__': raise SystemExit(main())
