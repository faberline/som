#!/usr/bin/env python3
"""Independent raw audit draft for Mac Rust006 extras; incomplete, never acceptance.

Checks raw Python replay and frozen coverage wire exchanges. Rust refusal and
OS cleanup checks are deliberately unrun until their full independent checks
exist. The exit is always nonzero; it cannot approve a release.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
ALLOWED = {'backend', 'block_library', 'catalog', 'compiler_contract', 'selection_policy'}
QA = 'http://127.0.0.1:18919'

def need(value, text):
    if not value: raise ValueError(text)

def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(path):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'physical file required')
    need(path.stat().st_size <= 128 * 1024 * 1024, 'file too large')
    return path.read_bytes()
def obj(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            need(k not in result, 'duplicate member')
            result[k] = v
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
def json_file(path): return obj(read(path))
def canonical(v):
    if v is None: return 'null'
    if v is True: return 'true'
    if v is False: return 'false'
    if type(v) is int: return str(v)
    if type(v) is float:
        need(math.isfinite(v), 'nonfinite number')
        s = repr(v)
        if 'e' in s:
            a, b = s.split('e', 1)
            sign = '-' if b.startswith('-') else '+' if b.startswith('+') else ''
            digits = b.lstrip('+-')
            if sign == '-' and len(digits) < 2: digits = digits.zfill(2)
            s = a + 'e' + sign + digits
        return s
    if type(v) is str: return json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    if type(v) is list: return '[' + ','.join(canonical(x) for x in v) + ']'
    if type(v) is dict:
        need(all(type(k) is str for k in v), 'bad object key')
        return '{' + ','.join(canonical(k) + ':' + canonical(v[k]) for k in sorted(v)) + '}'
    raise ValueError('unsupported JSON type')
def equal(a, b):
    return type(a) is type(b) and (a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a) if isinstance(a, dict)
        else len(a) == len(b) and all(equal(x,y) for x,y in zip(a,b)) if isinstance(a, list) else a == b)

class Calls:
    def __init__(self, root, script):
        self.root = root
        manifest = json_file(root / 'manifest.json')
        need(manifest.get('version') == 'som.m6-rust006-http-recording.v1' and manifest.get('exit_code') == 0
             and manifest.get('recording_errors') == [] and manifest.get('script', {}).get('path') == script
             and manifest['script']['sha256'] == sha(read(REPO / script)), 'raw recorder identity differs')
        self.rows = manifest['calls']
        need(type(self.rows) is list, 'calls missing')
        self.index = 0
    def take(self, route, request):
        need(self.index < len(self.rows), 'call missing')
        row = self.rows[self.index]
        self.index += 1
        num = self.index
        need(row.get('id') == num and row.get('method') == 'POST' and row.get('url') == QA + route
             and row.get('complete') is True and row.get('error') is None and type(row.get('status')) is int,
             'call identity differs')
        request_rel = f'calls/{num:06d}.request.raw'
        response_rel = f'calls/{num:06d}.response.raw'
        need(row.get('request_file') == request_rel and row.get('response_file') == response_rel,
             'call file name differs')
        got, response = read(self.root / request_rel), read(self.root / response_rel)
        need(got == request and sha(got) == row.get('request_sha256') and sha(response) == row.get('response_sha256'),
             'raw call bytes differ')
        saved = json_file(self.root / f'calls/{num:06d}.json')
        need(equal(saved, row), 'per-call record differs')
        return row['status'], response
    def finish(self): need(self.index == len(self.rows), 'extra calls')

def request_bytes(d, label):
    p = d / (label + '.request.raw')
    if p.is_file(): return read(p)
    return json.dumps(json_file(d / (label + '.request.json')), ensure_ascii=False, separators=(',', ':')).encode()
def expected(d, label):
    status = json_file(d / (label + '.response.json'))['http_status']
    return status, base64.b64decode(read(d / (label + '.response.raw.b64')), validate=False)

def audit_replay(raw, public, legacy):
    calls = Calls(raw, 'som-rs/scripts/rust_output_acceptance/python_replay.py')
    pub = json_file(public / 'result.json')
    leg = json_file(legacy / 'result.json')
    public_rows, legacy_rows = pub['records'], leg['records']
    need(len(public_rows) == 127 and len(legacy_rows) == 544 and pub.get('complete') is True
         and leg.get('status') == 'passed' and all(row.get('passed') is True for row in public_rows + legacy_rows),
         'frozen inputs incomplete')
    need(len({r['id'] for r in public_rows}) == 127 and len({r['id'] for r in legacy_rows}) == 544,
         'frozen case IDs duplicated')
    assets = {}
    def compile_one(directory, label):
        request = request_bytes(directory, label)
        expected_status, old_raw = expected(directory, label)
        status, new_raw = calls.take('/v1/compile', request)
        need(status == expected_status == 200, 'compile status differs')
        old, new = obj(old_raw), obj(new_raw)
        old_bundle, new_bundle = old['bundle'], new['bundle']
        old_asset, new_asset = old_bundle['assets'], new_bundle['assets']
        need({k for k in set(old_asset) | set(new_asset) if not equal(old_asset.get(k), new_asset.get(k))} <= ALLOWED,
             'nonadmitted asset differs')
        old_copy, new_copy = copy.deepcopy(old), copy.deepcopy(new)
        old_copy['bundle'].pop('assets'); new_copy['bundle'].pop('assets')
        old_copy['bundle']['hashes'].pop('payload_sha256'); new_hash = new_copy['bundle']['hashes'].pop('payload_sha256')
        need(equal(old_copy, new_copy), 'compile changed beyond assets')
        payload = {k:new_bundle[k] for k in ('version', 'prompt', 'dsl', 'assets')}
        need(sha(canonical(payload).encode()) == new_hash, 'compile payload hash differs')
        prompt = new_bundle['hashes']['prompt_sha256']
        if prompt in assets: need(equal(assets[prompt], new_asset), 'prompt assets changed')
        assets[prompt] = new_asset
    def assemble_one(directory, record):
        request = request_bytes(directory, 'assemble')
        status, body = calls.take('/v1/assemble', request)
        need(status == 400 and obj(body).get('code') == 'ASSET_HASH_MISMATCH', 'old bundle refusal differs')
        content = obj(request)
        bundle = content['bundle']
        prompt = bundle['hashes']['prompt_sha256']
        need(prompt in assets and ('prompt_sha256' not in record or prompt == record['prompt_sha256']),
             'missing compile assets or prompt mismatch')
        bundle['assets'] = assets[prompt]
        bundle['hashes']['payload_sha256'] = sha(canonical({k:bundle[k] for k in ('version','prompt','dsl','assets')}).encode())
        rebased = json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode()
        status, result = calls.take('/v1/assemble', rebased)
        want_status, want_result = expected(directory, 'assemble')
        need(status == want_status and result == want_result, 'rebased product differs')
        files = obj(result)['files']
        need(sha(canonical(files).encode()) == record['product_sha256'], 'product hash differs')
        if 'product_file_sha256' in record:
            need({name:sha(text.encode()) for name,text in sorted(files.items())} == record['product_file_sha256'],
                 'product file hashes differ')
    for index, record in enumerate(public_rows):
        directory = public / f'{index:03d}'
        need(json_file(directory / 'result.json')['id'] == record['id'], 'public case mismatch')
        for label in ('compile-cnl', 'compile-json'): compile_one(directory, label)
        assemble_one(directory, record)
    compile_dirs = sorted(p for p in (legacy / 'compile').iterdir() if p.is_dir())
    need(len(compile_dirs) == 540 and {p.name for p in compile_dirs} == {r['prompt_sha256'] for r in legacy_rows},
         'legacy compile source set differs')
    for directory in compile_dirs:
        for label in ('compile-json', 'compile-cnl'): compile_one(directory, label)
    for index, record in enumerate(legacy_rows): assemble_one(legacy / f'{index:04d}', record)
    calls.finish()
    return {'public': len(public_rows), 'legacy': len(legacy_rows), 'legacy_compiles': len(compile_dirs), 'raw_calls': calls.index}

def audit_cases(raw):
    calls = Calls(raw, 'som-rs/tests/rust-output-v1/validate_cases.py')
    root = REPO / 'som-rs/tests/rust-output-v1/cases'
    cases = json_file(root / 'index.json')['cases']
    expected_rows = json_file(REPO / 'som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json')['cases']
    frozen = {r['id']:r for r in expected_rows}
    need(len(cases) == len(frozen) and len({r['id'] for r in cases}) == len(cases), 'coverage inventory differs')
    for case in cases:
        prompt_raw, cnl_raw = read(root / case['prompt']), read(root / case['cnl'])
        need(sha(prompt_raw) == case['prompt_file_sha256'] and sha(cnl_raw) == case['cnl_sha256'], 'coverage input differs')
        prompt, cnl = obj(prompt_raw), cnl_raw.decode()
        req_json = json.dumps({'format':'som.prompt-json.v1', 'prompt':prompt}).encode()
        req_cnl = json.dumps({'format':'som.cnl.v1', 'source':cnl}).encode()
        sj, rj = calls.take('/v1/compile', req_json)
        sc, rc = calls.take('/v1/compile', req_cnl)
        need(sj == sc == 200, 'coverage compile failed')
        bj, bc = obj(rj)['bundle'], obj(rc)['bundle']
        need(equal(bj,bc) and bj['hashes']['prompt_sha256'] == frozen[case['id']]['prompt_sha256'], 'coverage bundle differs')
        sa, result = calls.take('/v1/assemble', json.dumps({'bundle':bj}).encode())
        need(sa == 200, 'coverage assemble failed')
        files = obj(result)['files']
        need({name:sha(text.encode()) for name,text in sorted(files.items())} == frozen[case['id']]['python_files'],
             'coverage file hashes differ')
    calls.finish()
    return {'cases':len(cases), 'raw_calls':calls.index}


def compact(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
def h(value): return sha(canonical(value).encode())
def rehash(bundle):
    result = {k: copy.deepcopy(v) for k,v in bundle.items() if k != 'hashes'}
    result['hashes'] = {'prompt_sha256': h(result['prompt']), 'dsl_sha256': h(result['dsl']), 'payload_sha256': h(result)}
    return result

def replace_name(value, old):
    if type(value) is dict: return {k:replace_name(v,old) for k,v in value.items()}
    if type(value) is list: return [replace_name(v,old) for v in value]
    return 'Title' if value == old else value

def pointer(root, value):
    need(type(value) is str and value.startswith('/'), 'pointer missing')
    for part in value[1:].split('/'):
        part=part.replace('~1','/').replace('~0','~')
        root = root[int(part)] if type(root) is list else root[part]
    return root

def audit_refusals(raw, legacy):
    import jsonschema
    import referencing
    calls = Calls(raw, 'som-rs/som-targets/qa/check_rust_products.py')
    contract=REPO/'som-rs/contracts/rust-output-v1'
    examples=REPO/'som-rs/contracts/compiler-v1/examples'
    inventory=json_file(contract/'case-inventory.json')
    plan=json_file(contract/'acceptance-plan.json')
    recipes={r['id']:r for r in plan['assemble_refusals']['recipes']}
    need(set(recipes)=={'A1','A2','A3','A4','A5','A6','A7','A8','A9'}, 'refusal plan differs')
    schema=json_file(contract/'bundle.schema.json')
    prompt_schema=json_file(REPO/'som-rs/contracts/compiler-v1/compiler-prompt.schema.json')
    validator=jsonschema.Draft202012Validator(schema, registry=referencing.Registry().with_resource(
        prompt_schema['$id'], referencing.Resource.from_contents(prompt_schema)))
    def send(route, body): return calls.take(route,compact(body))
    def compile_rust(prompt):
        status,body=send('/v1/compile',{'format':'som.prompt-json.v1','prompt':prompt,'target':'rust'})
        need(status==200,'Rust compile failed'); return obj(body)['bundle']
    def compile_python(prompt):
        status,body=send('/v1/compile',{'format':'som.prompt-json.v1','prompt':prompt})
        need(status==200,'Python compile failed'); return obj(body)['bundle']
    def frozen_example(name):
        row=next(r for r in inventory['public_examples'] if r['id']=='example-'+name)
        raw_prompt=read(REPO/row['prompt'])
        need(sha(raw_prompt)==row['prompt_file_sha256'], 'example source differs')
        return obj(raw_prompt)
    v1,v3,v5,v6=(frozen_example(name) for name in ('v1','v3','v5','v6'))
    base=compile_rust(v1)
    compile_rust(v5) # The runner prepares the v5 base before phase2 recipes.
    python=compile_python(v1)
    def variations(base, python, extra):
        a1=copy.deepcopy(base); a1['assets']['formatter']['sha256']=sha(b'a different rustfmt binary')
        a2=copy.deepcopy(base)
        for key in ('catalog','block_library','backend','formatter'): a2['assets'][key]=python['assets'][key]
        a4=copy.deepcopy(base); a4['target']='python'
        a5=copy.deepcopy(base); a5['dsl']['files'][0]['blocks'][0]['id']='orm.base'
        a7=copy.deepcopy(base); digest=a7['hashes']['payload_sha256']
        a7['hashes']['payload_sha256']=('0' if digest[0]!='0' else '1')+digest[1:]
        a9=copy.deepcopy(base)
        while len(a9['dsl']['files'])<13:
            a9['dsl']['files'].append({**copy.deepcopy(a9['dsl']['files'][-1]),'path':'README.md'})
        result={'A1':rehash(a1),'A2':rehash(a2),'A4':rehash(a4),'A5':rehash(a5),'A7':a7,'A9':rehash(a9)}
        if extra: result['A3']=rehash({**python,'target':'rust'})
        return result
    count=0
    def refuse(label,bundle,code,status,pointer_path):
        nonlocal count
        body={'bundle':bundle}; request=compact(body)
        got,raw_reply=calls.take('/v1/assemble',request)
        response=obj(raw_reply)
        need(got==status and response.get('code')==code,'refusal result differs: '+label)
        diagnostics=response.get('diagnostics')
        need(type(diagnostics) is list and len(diagnostics)==1 and diagnostics[0].get('prompt_pointer')==pointer_path,
             'refusal pointer differs: '+label)
        start,end=diagnostics[0].get('byte_start'),diagnostics[0].get('byte_end')
        need(type(start) is int and type(end) is int and 0<=start<=end<=len(request)
             and request[start:end]==compact(pointer(body,pointer_path)), 'refusal span differs: '+label)
        count += 1
    for key,bundle in sorted(variations(base,python,True).items()):
        r=recipes[key]; refuse(key,bundle,r['code'],r['http_status'],r['prompt_pointer'])
    rs13=next(row for row in json_file(contract/'negative-cases.json')['cases'] if row['id'].startswith('RS13'))
    py3=compile_python(v3)
    a6=copy.deepcopy(base); a6['prompt']=obj(rs13['body'].encode())['prompt']
    for key,value in py3['assets'].items():
        if key not in ('catalog','block_library','backend','formatter'): a6['assets'][key]=value
    refuse('A6',rehash(a6),rs13['expected']['code'],rs13['http_status'],'/bundle'+rs13['expected']['prompt_pointer'])
    renamed=copy.deepcopy(base)
    old=base['prompt']['resource']['fields'][0]['name']
    renamed['prompt']=replace_name(renamed['prompt'],old)
    renamed['dsl']=replace_name(renamed['dsl'],old)
    renamed=rehash(renamed)
    status,first=send('/v1/assemble',{'bundle':renamed})
    need(status==200,'A8 assembly failed')
    rebuilt=compile_rust(renamed['prompt'])
    status,second=send('/v1/assemble',{'bundle':rebuilt})
    need(status==200 and first==second,'A8 renamed product differs')
    count+=1
    v6base=compile_rust(v6)
    py6=compile_python(v6)
    for key,bundle in sorted(variations(v6base,py6,False).items()):
        r=recipes[key]; refuse(key+'_PUBLIC_v6',bundle,r['code'],r['http_status'],r['prompt_pointer'])
    need(count==15,'phase2 refusal count differs')
    # Derive the exact 115 cases from frozen inventory and source inputs.
    rows=[]
    for entry in inventory['public_examples']:
        if entry['rust']=='in_scope':
            prompt_raw,cnl_raw=read(REPO/entry['prompt']),read(REPO/entry['cnl'])
            need(sha(prompt_raw)==entry['prompt_file_sha256'] and sha(cnl_raw)==entry['cnl_sha256'],'example bytes differ')
            rows.append((entry['id'],obj(prompt_raw),cnl_raw.decode()))
    selected={entry['id']:entry for entry in inventory['version_positive_fixtures'] if entry['rust']=='in_scope'}
    fixtures=json_file(REPO/'som-rs/tests/compiler-v1/cnl-positive-fixtures.json')['cases']
    for item in fixtures:
        if item['id'] in selected:
            entry=selected[item['id']]
            need(h(item['expected_prompt'])==entry['prompt_sha256'] and sha(item['cnl'].encode())==entry['cnl_sha256'],
                 'CNL fixture differs')
            rows.append((item['id'],item['expected_prompt'],item['cnl']))
    cases_root=REPO/'som-rs/tests/rust-output-v1/cases'
    cases={row['id']:row for row in json_file(cases_root/'index.json')['cases']}
    for entry in inventory['coverage_cases']:
        item=cases[entry['id']]; prompt_raw=read(cases_root/item['prompt']); cnl_raw=read(cases_root/item['cnl'])
        need(sha(prompt_raw)==entry['prompt_file_sha256'] and sha(cnl_raw)==entry['cnl_sha256'], 'coverage source differs')
        rows.append((entry['id'],obj(prompt_raw),cnl_raw.decode()))
    for entry in inventory['legacy_in_scope']:
        request=json_file(legacy/f"{entry['inventory_index']:04d}"/'assemble.request.json')
        prompt=request['bundle']['prompt']
        need(h(prompt)==entry['prompt_sha256'],'legacy prompt differs')
        rows.append((entry['id'],prompt,None))
    need(len(rows)==115 and len({x[0] for x in rows})==115,'Rust case set differs')
    layout=json_file(contract/'product-layout.json')
    expected={r['path'] for r in layout['files'] if '<' not in r['path']}
    reference=contract/'reference'
    for identity,prompt,cnl in rows:
        requests=[{'format':'som.prompt-json.v1','prompt':prompt,'target':'rust'}]
        if cnl is not None: requests.append({'format':'som.cnl.v1','source':cnl,'target':'rust'})
        first=None
        for request in requests:
            for _ in range(2):
                status,response=send('/v1/compile',request)
                need(status==200 and (first is None or first==response),'nondeterministic Rust compile: '+identity)
                first=response
        bundle=obj(first)['bundle']
        need(not list(validator.iter_errors(bundle)), 'Rust bundle schema differs: '+identity)
        files=bundle['dsl']['files']; blocks=[b for f in files for b in f['blocks']]
        need(len(files)<=12 and len(blocks)<=64 and all(b['id'].startswith('rust.') for b in blocks),'Rust DSL differs')
        status,one=send('/v1/assemble',{'bundle':bundle}); status2,two=send('/v1/assemble',{'bundle':bundle})
        need(status==status2==200 and one==two,'Rust assemble differs: '+identity)
        output=obj(one)['files']
        migrations=[x for x in output if x.startswith('migrations/')]
        need(set(output)-set(migrations)==expected and migrations==['migrations/0001_'+prompt['migration']['revision']+'.sql'],
             'Rust output layout differs')
        for name in ('Cargo.toml','Cargo.lock'):
            need(output[name].encode()==read(reference/name),'reference Cargo file differs')
    calls.finish()
    return {'refusals':count,'bundle_cases':len(rows),'raw_calls':calls.index}


def main():
    p=argparse.ArgumentParser()
    for name in ('run','candidate','public-run','legacy-run','parity','out'): p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    need(not args.out.exists() and not args.out.is_symlink(), 'new report required')
    report={'version':'som.m6-mac-rust006-extra-audit.draft.v1','product_acceptance_passed':False,
            'rows':[], 'status':'unrun'}
    for name, action in [('python-replay', lambda:audit_replay(args.run/'raw-python-replay',args.public_run,args.legacy_run)),
                         ('validate-cases', lambda:audit_cases(args.run/'raw-validate-cases')),
                         ('phase2-rust-refusals', lambda:audit_refusals(args.run/'raw-rust-refusals',args.legacy_run))]:
        try: report['rows'].append({'id':name,'status':'passed','evidence':action()})
        except Exception as exc: report['rows'].append({'id':name,'status':'failed','error':type(exc).__name__ + ': ' + str(exc)})
    # No affirmative outcome until the refusal recipes, source provenance,
    # process/port ownership and PostgreSQL teardown can all be rederived.
    for name in ('candidate-source-and-process', 'postgres-cleanup'):
        report['rows'].append({'id':name,'status':'unrun','reason':'independent raw rule not implemented'})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as f: json.dump(report,f,sort_keys=True,indent=2); f.write('\n')
    print(json.dumps({'status':'unrun','product_acceptance_passed':False}))
    return 1

if __name__ == '__main__': raise SystemExit(main())
