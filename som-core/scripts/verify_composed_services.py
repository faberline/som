"""One entry point for source integrity and all complete-service behavior checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'som-core/src'))
from som_core import composed as c
from som_core.v1 import load_blocks,assemble,resolve_prompt_refs
from verify_composed_related import verify
from audit_business_transactions import verify_transactions,source_hashes
from verify_composed_journeys import verify_journeys


def verify_all(prompt,root,fixture,standard=True):
    # Import before capture so every verifier uses a persistent server log.
    import verify_composed_subsets
    import verify_composed_transactions
    from composed_evidence import capture
    from fixture_guard import reserve_fixture
    with reserve_fixture(fixture), capture(root) as evidence:
        result = _verify_all(prompt,root,fixture,standard)
        result['evidence_directory'] = str(evidence)
        result['requirement_sha256'] = hashlib.sha256(c.canonical(prompt).encode()).hexdigest()
        result['evidence_sha256'] = {
            str(path.relative_to(evidence)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in evidence.rglob('*') if path.is_file()}
        return result


def _verify_all(prompt,root,fixture,standard=True):
    before=source_hashes(root)
    old=c.legacy(prompt) if prompt['version']==c.VERSION else prompt
    if prompt['version']==c.VERSION and (not standard or len(prompt['operations'])!=11):
        from verify_composed_subsets import verify_subset
        result=verify_subset(prompt,root,fixture)
    else:
        result=verify(old,root,fixture)
        result['transactions']=verify_transactions(old,root,fixture)
        result['business_journeys']=verify_journeys(old,root,fixture)
    assert before==source_hashes(root),'source changed during checks'
    result['source_sha256']=before
    result['status']='passed'
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data',type=Path,default=ROOT/'som-core/data/composed/regression.jsonl')
    ap.add_argument('--fixture',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--url'); ap.add_argument('--indices'); a=ap.parse_args()
    if a.output.exists(): raise ValueError('preserve existing evaluation output')
    rows=[json.loads(x) for x in a.data.read_text().splitlines()]
    library=load_blocks(ROOT/'som-code-python/data/blocks/composed')
    fixture=json.loads(a.fixture.read_text())
    report={'kind':'rust' if a.url else 'python-reference','data_sha256':hashlib.sha256(a.data.read_bytes()).hexdigest(),'verifier_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*composed*.py')},'results':[]}
    for index in map(int,a.indices.split(',')) if a.indices else range(len(rows)):
        row=rows[index]; p=row['prompt']; started=time.monotonic()
        root=Path(tempfile.mkdtemp(prefix='som-composed-',dir='/private/tmp'))
        result={'index':index,'package':p['package'],'directory':str(root)}
        try:
            if a.url:
                import httpx
                response=httpx.post(a.url+'/v1/generate',json=p,timeout=65); response.raise_for_status(); files=response.json()['files']
            else:
                c.validate_plan(p,row['dsl']); files=assemble(resolve_prompt_refs(p,row['dsl']),library)
            for name,source in files.items():
                dest=root/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_text(source)
            result.update(verify_all(p,root,fixture,standard=a.data.name=='regression.jsonl'))
        except Exception as error:
            from composed_evidence import failure_details
            result.update(status='failed',error=traceback.format_exc(),failure=failure_details(error))
        result['seconds']=time.monotonic()-started
        report['results'].append(result); report['passed']=all(r['status']=='passed' for r in report['results']) and len(report['results'])==(len(a.indices.split(',')) if a.indices else len(rows))
        a.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'index':index,'status':result['status'],'seconds':result['seconds'],'error':result.get('error','')[-1800:]}),flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
