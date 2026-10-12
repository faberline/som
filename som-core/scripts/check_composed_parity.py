"""Batch byte parity and matched rejection categories, without model inference."""
from copy import deepcopy
import hashlib
import argparse
import itertools
import json
from pathlib import Path
import subprocess

from som_core import composed as c
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('preserve prior parity evidence')
    def valid_cases():
        for name in ('regression','train','dev','heldout'):
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
    library=load_blocks(ROOT/'som-code-python/data/blocks/composed')
    catalog=json.loads(c.CATALOG.read_text())
    failures=[]; rejected=0; count=0; digest=hashlib.sha256()
    pending=itertools.chain(valid_cases(), cases)
    while batch:=list(itertools.islice(pending,128)):
        sent=''.join(c.canonical(row)+'\n' for row in batch);digest.update(sent.encode())
        result=subprocess.run([str(ROOT/'som-rs/target/release/som-verify'),str(ROOT/'som-code-python/data/blocks/composed/blocks.json')],input=sent,text=True,capture_output=True,check=True)
        output=[json.loads(line) for line in result.stdout.splitlines()]
        assert len(output)==len(batch)
        for case,rust in zip(batch,output,strict=True):
            try:
                c.validate(case['prompt'])
                d=c.decode(case['encoded']) if 'encoded' in case else case['dsl']
                c.validate_plan(case['prompt'],d,catalog)
                files=assemble(resolve_prompt_refs(case['prompt'],d),library)
                assert rust['status']=='ok' and rust['files']==files and rust['dsl']==d and rust['features']==c.features(case['prompt']), 'valid parity'
            except V1Error as error:
                rejected+=1
                if rust.get('code')!=error.code: failures.append({'case':count,'python':error.code,'rust':rust})
            except Exception as error: failures.append({'case':count,'error':str(error),'rust':str(rust)[:500]})
            count+=1
    report={'cases':count,'valid':count-rejected,'rejected':rejected,'failures':failures,'passed':not failures,'input_sha256':digest.hexdigest()}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report)); raise SystemExit(bool(failures))


if __name__=='__main__':main()
