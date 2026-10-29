#!/usr/bin/env python3
"""Independent raw audit draft for Mac Rust006 extras; incomplete, never acceptance.

Checks raw Python replay, coverage, Rust refusal and OS observations. Command
provenance, parity and outer cleanup remain deliberately unrun. The exit is
always nonzero; it cannot approve a release.
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


# This must be replaced with the actual independently reviewed, human-approved
# source index hash after any separately authorized supplemental input upload.
# No branch name, prior 16-package permission, or caller boolean grants it.
HISTORIC_RESPONSE_APPROVED_INDEX_SHA256 = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
HISTORIC_RESPONSE_APPROVED_COMMIT = '1de8f05430a72f8d76f6159786cca71b0ae1a911'

def git_blob(commit, name):
    import subprocess
    need(re.fullmatch('[0-9a-f]{40}',commit) is not None and
         re.fullmatch(r'(?:inputs-index\.json|package-[0-9]{3}\.manifest\.json)',name) is not None,
         'input Git blob identity differs')
    return subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'show',commit+':'+name],timeout=120)

def manifest_map(index_raw, expected_sha, load):
    need(type(expected_sha) is str and re.fullmatch('[0-9a-f]{64}',expected_sha) is not None
         and sha(index_raw)==expected_sha, 'input index source differs')
    index=obj(index_raw)
    need(type(index) is dict and set(index)=={'version','packages'} and index['version']=='som.m6-input-index.v1'
         and type(index['packages']) is list and 1<=len(index['packages'])<=1000, 'input index differs')
    files={}; names=set()
    for package in index['packages']:
        need(type(package) is dict and set(package)=={'archive','manifest','manifest_sha256'}
             and re.fullmatch(r'package-[0-9]{3}\.sealed',package.get('archive','')) is not None
             and package['manifest']==package['archive'][:-7]+'.manifest.json'
             and package['manifest'] not in names, 'input package identity differs')
        names.add(package['manifest'])
        raw=load(package['manifest'])
        need(sha(raw)==package['manifest_sha256'],'input manifest hash differs')
        manifest=obj(raw)
        need(type(manifest) is dict and set(manifest)=={'version','cipher_sha256','uncompressed_bytes','total_files','files'}
             and manifest['version']=='som.m6-sealed-inputs-manifest.v1'
             and re.fullmatch('[0-9a-f]{64}',manifest['cipher_sha256']) is not None
             and type(manifest['files']) is list and type(manifest['total_files']) is int
             and manifest['total_files']==len(manifest['files']), 'input manifest differs')
        for row in manifest['files']:
            need(type(row) is dict and set(row)=={'path','sha256','bytes','mode'} and
                 type(row['path']) is str and row['path'] and not row['path'].startswith('/') and
                 '\\' not in row['path'] and all(part not in ('','.','..') for part in row['path'].split('/')) and
                 type(row['bytes']) is int and row['bytes']>=0 and row['mode'] in (0o644,0o755) and
                 type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}',row['sha256']) is not None and
                 row['path'] not in files, 'input member identity differs')
            files[row['path']]={'sha256':row['sha256'],'bytes':row['bytes']}
    return files

class BoundInputs:
    def __init__(self,repo,files):
        self.repo=repo
        self.files=files
    def read(self,path):
        path=Path(path)
        need(path.is_absolute() and path.is_relative_to(self.repo), 'input outside repository')
        relative=path.relative_to(self.repo).as_posix()
        need(relative in self.files, 'input is not in approved package manifest')
        data=read(path); expected=self.files[relative]
        need(len(data)==expected['bytes'] and sha(data)==expected['sha256'], 'input bytes differ from approved manifest')
        return data
    def json(self,path):return obj(self.read(path))

def merge_derived(files, original, repo):
    combined=dict(files)
    for name,data in original.items():
        need(name not in combined and read(repo/name)==data,'derived response bytes or source role differs')
        combined[name]={'sha256':sha(data),'bytes':len(data),'source':'later-capture-derived'}
    return combined

def approved_inputs(public,legacy,config_path=None,provenance_root=None):
    need(public==REPO/'.local/rust-compiler-goal/acceptance/public-products-003b' and
         legacy==REPO/'.local/rust-compiler-goal/acceptance/legacy544/run-005', 'historic source roots differ')
    need(config_path is not None and provenance_root is not None, 'two-source historic provenance is required')
    config=json_file(config_path)
    need(type(config) is dict and set(config)=={'version','commit','index_sha256'} and
         config['version']=='som.m6-hosted-inputs.v1' and config['commit']==HISTORIC_RESPONSE_APPROVED_COMMIT and
         config['index_sha256']==HISTORIC_RESPONSE_APPROVED_INDEX_SHA256, 'approved 18-package source differs')
    index=git_blob(config['commit'],'inputs-index.json')
    files=manifest_map(index,config['index_sha256'],lambda name:git_blob(config['commit'],name))
    # This path was produced from the approved #130 capture; it is never relabeled
    # as the historical wire. Recompute and join its exact source in this job.
    import sys
    sys.path.insert(0,str(REPO/'som-rs/scripts/react_output_acceptance'))
    try:
        import reconstruct_historical_responses as derived
        import verify_historical_response_provenance as provenance
        map_raw=read(REPO/'som-rs/scripts/react_output_acceptance/historical-response-reuse-map.json')
        temp=Path(provenance_root).resolve(strict=True)
        original=derived.derive(map_raw,temp/'input-packages',REPO,temp/'evidence-sealed',temp/'evidence')
        evidence=derived.indexed_files(temp/'evidence-sealed',derived.EVIDENCE_SHA,True)
        load=lambda name:derived.checked(temp/'evidence',name,evidence)
        recording=load(provenance.BASE+'manifest.json'); report=load('run/python-baseline/report.json')
        need(sha(recording)==provenance.RECORDING_SHA and sha(report)==provenance.REPORT_SHA,
             'later capture raw authorities differ')
        joined=provenance.join(derived.parse(map_raw),derived.parse(recording),derived.parse(report),load)
        need(joined['derived_pairs']==668 and len(original)==1336,'derived response inventory differs')
        files=merge_derived(files,original,REPO)
    finally:
        sys.path.pop(0)
    return BoundInputs(REPO,files)


def request_bytes(bound, d, label):
    p = d / (label + '.request.raw')
    if p.is_file(): return bound.read(p)
    return json.dumps(bound.json(d / (label + '.request.json')), ensure_ascii=False, separators=(',', ':')).encode()
def expected(bound, d, label):
    status = bound.json(d / (label + '.response.json'))['http_status']
    return status, base64.b64decode(bound.read(d / (label + '.response.raw.b64')), validate=False)

def audit_replay(raw, public, legacy, bound):
    calls = Calls(raw, 'som-rs/scripts/rust_output_acceptance/python_replay.py')
    pub = bound.json(public / 'result.json')
    leg = bound.json(legacy / 'result.json')
    public_rows, legacy_rows = pub['records'], leg['records']
    need(len(public_rows) == 127 and len(legacy_rows) == 544 and pub.get('complete') is True
         and leg.get('status') == 'passed' and all(row.get('passed') is True for row in public_rows + legacy_rows),
         'frozen inputs incomplete')
    need(len({r['id'] for r in public_rows}) == 127 and len({r['id'] for r in legacy_rows}) == 544,
         'frozen case IDs duplicated')
    assets = {}
    def compile_one(directory, label):
        request = request_bytes(bound, directory, label)
        expected_status, old_raw = expected(bound, directory, label)
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
        request = request_bytes(bound, directory, 'assemble')
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
        want_status, want_result = expected(bound, directory, 'assemble')
        need(status == want_status and result == want_result, 'rebased product differs')
        files = obj(result)['files']
        need(sha(canonical(files).encode()) == record['product_sha256'], 'product hash differs')
        if 'product_file_sha256' in record:
            need({name:sha(text.encode()) for name,text in sorted(files.items())} == record['product_file_sha256'],
                 'product file hashes differ')
    for index, record in enumerate(public_rows):
        directory = public / f'{index:03d}'
        need(bound.json(directory / 'result.json')['id'] == record['id'], 'public case mismatch')
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

def audit_refusals(raw, legacy, bound):
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
        request=bound.json(legacy/f"{entry['inventory_index']:04d}"/'assemble.request.json')
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



def command_raw(root, row, expected):
    need(type(row) is dict and row.get('argv') == expected and type(row.get('exit_code')) is int,
         'OS command differs')
    output = {}
    for stream in ('stdout','stderr'):
        record=row.get(stream)
        need(type(record) is dict and type(record.get('file')) is str and
             re.fullmatch(r'(?:os|call)-[0-9]{4}\.(?:stdout|stderr)',record['file']), 'OS log reference differs')
        raw=read(root/record['file'])
        need(sha(raw)==record.get('sha256'),'OS log hash differs')
        output[stream]=raw
    need(not output['stderr'],'OS command stderr not empty')
    return row['exit_code'],output['stdout']

def audit_cluster(root):
    receipt=json_file(root/'receipt.json')
    need(receipt.get('version')=='som.m6-mac-rust006-cluster.v1' and receipt.get('exit_code')==0
         and receipt.get('recording_errors')==[] and receipt.get('product_acceptance_passed') is False,
         'cluster record incomplete')
    port=receipt['port']
    need(type(port) is int and 18919<=port<19000 and port not in (18917,18919,8765,18765),'cluster port differs')
    tools=json_file(REPO/'som-rs/contracts/rust-output-v1/toolchain.json')['postgresql']
    bin_dir=Path(tools['bin_dir'])
    for name in ('postgres','initdb','pg_ctl'):
        row=receipt['tools'][name]
        need(row['source']==str(bin_dir/name) and row['file']==f'postgresql-tools/{name}'
             and row['source_before']==row['source_after']==row['sha256']==tools[name]['sha256']
             and sha(read(root/row['file']))==tools[name]['sha256'], 'retained PostgreSQL tool differs')
    events=receipt['events']
    init=[e for e in events if e.get('kind')=='initdb']
    controls=[e for e in events if e.get('kind')=='pg_ctl']
    post=[e for e in events if e.get('kind')=='postmaster']
    snapshots=[e for e in events if e.get('kind')=='process']
    need(len(init)==1 and len(controls)==2 and len(post)==2 and len(snapshots)==2, 'cluster event count differs')
    need([x['phase'] for x in post]==['after-start','before-stop'] and
         [x['phase'] for x in snapshots]==['before-pg-stop','after-stop'], 'cluster event order differs')
    cluster=root.parent/'cluster-work'
    data=str(cluster/'pgdata')
    initial=[str(bin_dir/'initdb'),'-D',data,'-U','postgres','-E','UTF8','--locale=C','--auth=trust','--no-sync']
    code,_=command_raw(root,init[0],initial)
    need(code==0,'initdb failed')
    options=(f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c timezone=UTC "
             f"-c log_timezone=UTC -c max_connections=300 -c fsync=off")
    start=[str(bin_dir/'pg_ctl'),'-D',data,'-l',str(cluster/'postgres.log'),'-o',options,'-w','start']
    stop=[str(bin_dir/'pg_ctl'),'-D',data,'-m','fast','-w','stop']
    for record,argv in zip(controls,(start,stop)):
        code,_=command_raw(root,record,argv)
        need(code==0,'pg_ctl failed')
    parsed=[]
    for event in post:
        record=event['raw']
        need(record['file'] in ('postmaster-after-start.pid','postmaster-before-stop.pid'),'postmaster file differs')
        raw=read(root/record['file'])
        need(sha(raw)==record['sha256'],'postmaster hash differs')
        lines=raw.decode().splitlines()
        need(len(lines)>=4 and lines[0].isdigit() and int(lines[0])>1 and lines[1]==data
             and lines[2].isdigit() and lines[3]==str(port),'postmaster identity differs')
        parsed.append(lines[:4])
    need(parsed[0]==parsed[1],'postmaster identity changed')
    pid=int(parsed[0][0]); before,after=snapshots
    need(before['pid']==after['pid']==pid and type(before.get('pgid')) is int and before['pgid']>0,
         'cluster snapshot identity differs')
    ps=['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command=']
    cwd=['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn']
    code,psraw=command_raw(root,before['ps'],ps)
    fields=psraw.decode().strip().split(None,3)
    need(code==0 and len(fields)==4 and fields[0]==str(pid) and fields[1].isdigit() and fields[2].isdigit()
         and fields[2]==str(before['pgid']), 'PostgreSQL process snapshot differs')
    import shlex
    argv=shlex.split(fields[3])
    need(argv and Path(argv[0]).name=='postgres' and '-D' in argv and argv[argv.index('-D')+1]==data,
         'PostgreSQL command differs')
    code,cwdraw=command_raw(root,before['cwd'],cwd)
    lines=cwdraw.decode().splitlines()
    need(code==0 and [x[1:] for x in lines if x.startswith('p')]==[str(pid)]
         and [x[1:] for x in lines if x.startswith('n')]==[data], 'PostgreSQL cwd differs')
    table_command=['/bin/ps','-axo','pid=,ppid=,pgid=']
    def process_table(record):
        code, raw=command_raw(root,record,table_command)
        need(code==0,'process table unavailable')
        result={}
        for line in raw.decode().splitlines():
            fields=line.split()
            need(len(fields)==3 and all(field.isdigit() for field in fields),'invalid process table')
            child, parent, group=map(int,fields)
            need(child not in result,'duplicate process table PID')
            result[child]=(parent,group)
        return result
    table_before,table_after=process_table(before['table']),process_table(after['table'])
    need(pid in table_before and table_before[pid][1]==before['pgid'], 'postmaster missing from process table')
    descendants=set()
    frontier={pid}
    while frontier:
        children={child for child,(parent,_) in table_before.items() if parent in frontier} - descendants - {pid}
        descendants.update(children)
        frontier=children
    need(not ({pid}|descendants).intersection(table_after), 'observed PostgreSQL process remains')
    for key,cmd in [('ps',ps),('cwd',cwd),('listener',['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'])]:
        code,value=command_raw(root,after[key],cmd)
        need(code==1 and not value, 'PostgreSQL process or listener still present')
    need(after.get('pgid') is None and after.get('pgid_error_errno')==3, 'PostgreSQL pid still present')
    return {'pid':pid,'port':port,'tool_files':3,'observed_descendants':len(descendants),
            'master_and_observed_descendants_absent':True, 'unobserved_descendants':'unverified'}

def audit_candidate(run, candidate, expected_revision):
    report=json_file(run/'collector.json')
    build=json_file(candidate/'build.json'); runtime=json_file(candidate/'runtime.json'); source=json_file(candidate/'source.json')
    digest=sha(read(candidate/'som'))
    need(report.get('version')=='som.m6-mac-rust006-extra.v1' and report.get('candidate_revision')==expected_revision
         and report.get('candidate_binary_sha256')==digest and report.get('product_acceptance_passed') is False
         and build.get('binary_sha256')==runtime.get('binary_sha256')==digest
         and build.get('complete') is True and build.get('source_unchanged') is True
         and build.get('tree_before',{}).get('head')==source.get('commit')==expected_revision
         and sha(read(candidate/'source.json'))==build.get('source_sha256')==runtime.get('source_sha256')
         and sha(read(candidate/'source.tar'))==source.get('archive_sha256')
         and sha(read(candidate/'assets/manifest.json'))==build.get('asset_manifest_sha256'), 'candidate bytes differ')
    need(type(source.get('files')) is dict and source['files'], 'source file map missing')
    for rel,expected in source['files'].items():
        path=Path(rel)
        need(not path.is_absolute() and '..' not in path.parts and sha(read(REPO/path))==expected,'build source file differs')
    pid,port=runtime['pid'],runtime['port']
    need(type(pid) is int and pid>1 and port==18919 and runtime.get('accepted') is False,'candidate PID/port differs')
    argv=runtime['argv']
    need(argv==[str(candidate/'som'),'serve','--assets',str(candidate/'assets'),'--port','18919'], 'candidate argv differs')
    for phase in ('before','after'):
        snap=report['candidate_'+phase]
        need(snap['pid']==pid and snap['port']==port,'candidate snapshot differs')
        commands={'ps':['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command='],
                  'cwd':['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],
                  'listener':['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t']}
        raw={}
        for name,command in commands.items():
            row=snap['commands'][name]
            need(row['argv']==command and row['exit_code']==0,'candidate command failed')
            for stream in ('stdout','stderr'):
                path=run/row[stream]
                need(path.is_relative_to(run/'logs') and sha(read(path))==row[stream+'_sha256'],'candidate log differs')
                raw[(name,stream)]=read(path)
            need(not raw[(name,'stderr')],'candidate command stderr differs')
        fields=raw[('ps','stdout')].decode().strip().split(None,3)
        need(len(fields)==4 and fields[0]==str(pid) and fields[1].isdigit() and fields[2].isdigit()
             and fields[3]==' '.join(argv),'candidate process command differs')
        lines=raw[('cwd','stdout')].decode().splitlines()
        need([x[1:] for x in lines if x.startswith('p')]==[str(pid)]
             and [x[1:] for x in lines if x.startswith('n')]==[str(candidate)],'candidate cwd differs')
        need(raw[('listener','stdout')].decode().split()==[str(pid)],'candidate listener differs')
        health=snap['health']; health_path=run/health['file']
        need(health['status']==200 and health_path.is_relative_to(run/'logs')
             and sha(read(health_path))==health['sha256'] and equal(obj(read(health_path)),runtime['health']),
             'candidate health differs')
    return {'binary_sha256':digest,'pid':pid,'port':port,'source_files':len(source['files'])}



def audit_command_records(run, candidate, public, legacy, parity):
    import subprocess
    report=json_file(run/'collector.json')
    revision=report['candidate_revision']
    need(type(revision) is str and re.fullmatch('[0-9a-f]{40}',revision), 'source revision missing')
    actual=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),
                                    'rev-parse','--verify','HEAD^{commit}'],timeout=30).decode().strip()
    need(actual==revision, 'source HEAD differs')
    sources=('som-rs/scripts/react_output_acceptance/mac_rust006_extra.py',
             'som-rs/scripts/react_output_acceptance/mac_rust006_cluster.py',
             'som-rs/scripts/react_output_acceptance/record_rust006_http.py',
             'som-rs/scripts/react_output_acceptance/record_api.py',
             'som-rs/scripts/rust_output_acceptance/python_replay.py',
             'som-rs/tests/rust-output-v1/validate_cases.py',
             'som-rs/som-targets/qa/check_rust_products.py',
             'som-rs/behavior/hosts.py',
             'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py',
             'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py',
             'som-rs/scripts/react_output_acceptance/install_derived_inputs.py',
             'som-rs/scripts/react_output_acceptance/audit_supplement_provision.py')
    for rel in sources:
        entry=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),
                                       'ls-tree','-z',revision,'--',rel],timeout=30)
        need(entry.startswith((b'100644 blob ',b'100755 blob ')) and
             entry.endswith(b'\t'+rel.encode()+b'\0') and entry.count(b'\0')==1,'source Git entry differs')
        raw=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),
                                     'show',revision+':'+rel],timeout=30)
        need(raw==read(REPO/rel),'executable source differs from Git')
    runner=report.get('runner')
    need(type(runner) is dict and runner.get('GITHUB_SHA')==revision and runner.get('GITHUB_ACTIONS')=='true'
         and runner.get('RUNNER_ENVIRONMENT')=='github-hosted' and runner.get('RUNNER_OS')=='macOS'
         and runner.get('RUNNER_ARCH')=='ARM64' and all(type(runner.get(k)) is str and runner[k].isdigit()
             for k in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')), 'runner identity differs')
    python=REPO/'som-rs/runtime/acceptance-m3-venv/bin/python'
    script=REPO/'som-rs/scripts/react_output_acceptance'
    expected=[
        [python,script/'record_rust006_http.py','--out',run/'raw-python-replay','--',
         REPO/'som-rs/scripts/rust_output_acceptance/python_replay.py','--asset-break','--base-url',QA,
         '--public-run',public,'--legacy-run',legacy,'--out',run/'python-replay.json'],
        [python,script/'record_rust006_http.py','--out',run/'raw-validate-cases','--',
         REPO/'som-rs/tests/rust-output-v1/validate_cases.py','--base-url',QA,'--out',run/'validate-cases.json'],
        [python,script/'mac_rust006_cluster.py','--raw-out',run/'raw-rust-refusals',
         '--lifecycle',run/'cluster-lifecycle','--cluster-root',run/'cluster-work','--','--qa',QA,
         '--work',run/'rust-products-work','--target-dir',run/'rust-target','--out',run/'rust-products.json',
         '--no-gates','--refusals','phase2','--parity',parity]]
    names=('python-replay','validate-cases','rust-refusals')
    rows=report.get('rows')
    need(type(rows) is list and len(rows)==3 and report.get('execution_complete') is True
         and report.get('product_acceptance_passed') is False,'command set incomplete')
    need(report.get('candidate_dir')==str(candidate) and report.get('parity_sha256')==sha(read(parity))
         and report.get('public_result_sha256')==sha(read(public/'result.json'))
         and report.get('legacy_result_sha256')==sha(read(legacy/'result.json')), 'command inputs differ')
    env_keys={'HOME','PATH','TMPDIR','LANG','LC_ALL','LC_CTYPE','CI','SDKROOT','DEVELOPER_DIR','CARGO_HOME',
              'RUSTUP_HOME','GITHUB_ACTIONS','RUNNER_ENVIRONMENT','RUNNER_OS','RUNNER_ARCH','RUNNER_TEMP',
              'CARGO_NET_OFFLINE','PYTHONDONTWRITEBYTECODE','SOM_TS_PRETTIER'}
    for index,(row,name,argv) in enumerate(zip(rows,names,expected),1):
        need(type(row) is dict and row.get('index')==index and row.get('name')==name
             and row.get('argv')==[str(x) for x in argv] and row.get('cwd')==str(REPO)
             and row.get('state')=='completed' and type(row.get('pid')) is int and row['pid']>1
             and type(row.get('exit_code')) is int and row['exit_code']==0
             and type(row.get('wait_result')) is int and row['wait_result']==0, 'direct command wait differs')
        env=row.get('environment')
        need(type(env) is dict and set(env)<=env_keys and env.get('CARGO_NET_OFFLINE')=='true'
             and env.get('PYTHONDONTWRITEBYTECODE')=='1', 'child environment differs')
        for stream in ('stdout','stderr'):
            rel=f'logs/{name}.{stream}'
            need(row.get(stream)==rel and row.get(stream+'_sha256')==sha(read(run/rel)), 'direct command log differs')
    return {'commands':len(rows),'source_revision':revision,'runner_id':runner['GITHUB_RUN_ID'],
            'run_attempt':runner['GITHUB_RUN_ATTEMPT']}


def main():
    p=argparse.ArgumentParser()
    for name in ('run','candidate','public-run','legacy-run','parity','out'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--approved-input-config',type=Path,required=True)
    p.add_argument('--provenance-root',type=Path,required=True)
    args=p.parse_args()
    need(not args.out.exists() and not args.out.is_symlink(), 'new report required')
    report={'version':'som.m6-mac-rust006-extra-audit.draft.v1','product_acceptance_passed':False,
            'rows':[], 'status':'unrun'}
    try:
        inputs=approved_inputs(args.public_run,args.legacy_run,args.approved_input_config,args.provenance_root)
    except Exception as exc:
        inputs=None
        report['rows'].append({'id':'historic-input-authority','status':'unrun',
                               'error':type(exc).__name__ + ': ' + str(exc)})
    def require_inputs():
        need(inputs is not None,'historic input authority unrun')
        return inputs
    for name, action in [('python-replay', lambda:audit_replay(args.run/'raw-python-replay',args.public_run,args.legacy_run,require_inputs())),
                         ('validate-cases', lambda:audit_cases(args.run/'raw-validate-cases')),
                         ('phase2-rust-refusals', lambda:audit_refusals(args.run/'raw-rust-refusals',args.legacy_run,require_inputs())),
                         ('candidate-source-and-process', lambda:audit_candidate(args.run,args.candidate,
                              json_file(args.run/'collector.json')['candidate_revision'])),
                         ('postgres-observed-stop', lambda:audit_cluster(args.run/'cluster-lifecycle')),
                         ('command-records', lambda:audit_command_records(args.run,args.candidate,args.public_run,args.legacy_run,args.parity))]:
        try:
            evidence=action()
            report['rows'].append({'id':name,'status':'passed','evidence':evidence})
        except Exception as exc: report['rows'].append({'id':name,'status':'failed','error':type(exc).__name__ + ': ' + str(exc)})
    # No affirmative outcome until the refusal recipes, source provenance,
    # process/port ownership and PostgreSQL teardown can all be rederived.
    for name in ('outer-run-head-provenance', 'same-candidate-parity', 'outer-owned-candidate-cleanup'):
        report['rows'].append({'id':name,'status':'unrun','reason':'independent raw rule not implemented'})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as f: json.dump(report,f,sort_keys=True,indent=2); f.write('\n')
    print(json.dumps({'status':'unrun','product_acceptance_passed':False}))
    return 1

if __name__ == '__main__': raise SystemExit(main())
