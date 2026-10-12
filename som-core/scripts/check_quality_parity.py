"""Full final-byte parity with the same pinned native Ruff on each side."""
from copy import deepcopy
import hashlib
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import os
import time

from som_core import composed as c, quality
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('preserve prior parity evidence')
    def valid_cases():
        for name in ('regression','train','dev','heldout','blind'):
            with (ROOT/f'som-core/data/composed/{name}.jsonl').open() as source:
                for line in source:
                    row=json.loads(line)
                    yield {'prompt':row['prompt'],'encoded':c.encode(row['dsl'])}
    with (ROOT/'som-core/data/composed/regression.jsonl').open() as source:
        base=json.loads(next(source))
    cases=[]
    for slot,name in [('integrations','auth'),('app','batch'),('app','workflow'),('app','head'),('models','parent'),('schemas','child'),('app','app'),('database','database')]:
        d=deepcopy(base['dsl']); f=next(f for f in d['files'] if f['path']==c.SLOTS[slot]); f['blocks']=[b for b in f['blocks'] if b['name']!=name]
        cases.append({'prompt':base['prompt'],'dsl':d})
    for mutation in ('database','binding','duplicate','ref','route_order','type','raw_source','missing_eos','unknown_token','bad_path','wrong_contract','duplicate_operation','route_collision','null_blocks','null_file','null_block','null_refs'):
        p,d=deepcopy(base['prompt']),deepcopy(base['dsl']); api=next(f for f in d['files'] if f['path']==c.SLOTS['app'])
        if mutation=='database': api['blocks'][0]['id']='api.business.head.mongodb'
        if mutation=='binding': api['blocks'][1]['params']['path']={'$ref':'child.path'}
        if mutation=='duplicate': d['files'].append(deepcopy(d['files'][0]))
        if mutation=='ref': api['blocks'][1]['refs']=[]
        if mutation=='route_order': api['blocks'][1],api['blocks'][2]=api['blocks'][2],api['blocks'][1]
        if mutation=='type': p['resources']['parent']['fields'][0]['required']='true'
        if mutation=='raw_source': api['blocks'][1]['params']['path']='print(1)'
        if mutation=='bad_path': d['files'][0]['path']='../bad.py'
        if mutation=='wrong_contract': p['contract']['sha256']='0'*64
        if mutation=='duplicate_operation': p['operations'][1]=deepcopy(p['operations'][0])
        if mutation=='route_collision': p['resources']['child']['path']='/export'
        if mutation=='null_blocks': d['files'][0]['blocks']=None
        if mutation=='null_file': d['files'][0]=None
        if mutation=='null_block': d['files'][0]['blocks'][0]=None
        if mutation=='null_refs': api['blocks'][0]['refs']=None
        case={'prompt':p,'dsl':d}
        if mutation=='missing_eos': case={'prompt':p,'encoded':c.encode(d).removesuffix('<eos>')}
        if mutation=='unknown_token': case={'prompt':p,'encoded':'<wrong><eos>'}
        cases.append(case)
    library=load_blocks(ROOT/'som-code-python/data/blocks/composed-quality')
    catalog=json.loads(quality.CATALOG.read_text())
    from concurrent.futures import ThreadPoolExecutor, as_completed
    model=ROOT/'som-rs/models/composed-quality-candidate'
    binary=ROOT/'som-rs/target/composed-quality/release/som-verify'
    all_cases=list(itertools.chain(valid_cases(), cases))
    digest=hashlib.sha256(''.join(c.canonical(row)+'\n' for row in all_cases).encode()).hexdigest()
    batches=[(offset,all_cases[offset:offset+32]) for offset in range(0,len(all_cases),32)]
    failures=[]; records=[]
    assets=[binary,model/"manifest.json",model/"ruff",quality.PROFILE,quality.CATALOG,ROOT/"som-code-python/data/blocks/composed-quality/blocks.json"]
    assets_before={str(path):quality.sha256(path) for path in assets}
    started=time.monotonic()
    def check_batch(item):
        offset,batch=item
        sent=''.join(c.canonical(row)+'\n' for row in batch)
        result=subprocess.run([str(binary),str(ROOT/'som-code-python/data/blocks/composed-quality/blocks.json'),str(model)],input=sent,text=True,capture_output=True,check=True,env=os.environ|{"SOM_VERIFY_WORKERS":"4"})
        output=[json.loads(line) for line in result.stdout.splitlines()]
        assert len(output)==len(batch)
        def check_case(item):
            index,(case,rust)=item
            entry={'case':index}
            try:
                c.validate(case['prompt'])
                d=c.decode(case['encoded']) if 'encoded' in case else case['dsl']
                c.validate_plan(case['prompt'],d,catalog)
                files=quality.reference(case['prompt'],d,library,model/'ruff')
                assert rust['status']=='ok' and rust['files']==files and rust['dsl']==d and rust['features']==c.features(case['prompt']), 'valid final-byte parity'
                entry.update(passed=True,kind='valid',files_sha256=hashlib.sha256(c.canonical(files).encode()).hexdigest())
            except V1Error as error:
                entry.update(passed=rust.get('code')==error.code,kind='rejected',python=error.code,rust=rust.get('code'))
            except Exception as error:
                entry.update(passed=False,kind='failure',error=str(error))
            if not entry['passed']:
                entry['input']=case;entry['rust']=rust
            return entry
        with ThreadPoolExecutor(max_workers=4) as reference_pool:
            return list(reference_pool.map(check_case,enumerate(zip(batch,output,strict=True),offset)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(check_batch,batch):batch[0] for batch in batches}
        for future in as_completed(futures):
            try:
                records.extend(future.result())
            except Exception as error:
                records.append({'case':futures[future],'passed':False,'kind':'batch_failure','error':str(error)})
            records.sort(key=lambda row:row['case'])
            failures=[row for row in records if not row['passed']]
            report={'cases':len(records),'expected_cases':len(all_cases),'valid':sum(row['kind']=='valid' for row in records),'rejected':sum(row['kind']=='rejected' for row in records),'failures':failures,'passed':len(records)==len(all_cases) and not failures,'input_sha256':digest,'model_manifest_sha256':quality.sha256(model/'manifest.json'),'blocks_sha256':quality.sha256(ROOT/'som-code-python/data/blocks/composed-quality/blocks.json'),'formatter_sha256':quality.sha256(model/'ruff'),'profile_sha256':quality.sha256(quality.PROFILE),'records':records,'asset_hashes':assets_before,'seconds':time.monotonic()-started}
            args.output.write_text(json.dumps(report,indent=2)+'\n')
            if len(records)%256==0 or failures:
                print(json.dumps({key:report[key] for key in ('cases','expected_cases','valid','rejected','passed')}),flush=True)
    report['assets_unchanged']=assets_before=={str(path):quality.sha256(path) for path in assets}
    report['passed']=report['passed'] and report['assets_unchanged']
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ('cases','expected_cases','valid','rejected','passed','seconds')}))
    raise SystemExit(not report['passed'])


if __name__=='__main__':main()
