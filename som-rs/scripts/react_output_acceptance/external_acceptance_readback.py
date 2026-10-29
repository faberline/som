#!/usr/bin/env python3
"""Check completed GH job and restored encrypted evidence; never runs a product."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

REPO_ID=1390374878
WORKFLOW='.github/workflows/m6-mac-final-acceptance.yml'
JOB='Frozen M6 tools and optional full acceptance'
ARTIFACT='m6-sealed-raw-evidence'
ROWS=('approved-inputs','parity-sources','parity-candidate','parity-postgres-observed-stop',
      'parity-raw-and-observed-product-cleanup','python-replay','validate-cases','phase2-rust-refusals',
      'rust-candidate','postgres-observed-stop','rust-command-records','candidate-observed-group-cleanup',
      'candidate-identity-join','baseline-observed-cleanup','original-12-step-gate','workflow-origin')
REQUIRED=('candidate-owner.json','baseline-cleanup.json','candidate-owner-audit.json',
          'baseline-cleanup-audit.json','post-acceptance-observations.json','origin-run.json','origin-jobs.json')

def need(value):
    if not value:raise ValueError('external readback refused')

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def obj(path):
    def unique(items):
        result={}
        for k,v in items:need(k not in result);result[k]=v
        return result
    return json.loads(path.read_bytes(),object_pairs_hook=unique)

def check(archive,restored,run,jobs,artifact,head):
    need(re.fullmatch('[0-9a-f]{40}',head) is not None and run.get('head_sha')==head
         and run.get('path')==WORKFLOW and run.get('event')=='workflow_dispatch'
         and run.get('status')=='completed' and run.get('conclusion')=='success')
    for key in ('repository','head_repository'):
        need(run.get(key,{}).get('id')==REPO_ID and run[key].get('full_name')=='faberline/som')
    need(type(run.get('id')) is int and type(run.get('run_attempt')) is int)
    selected=[j for j in jobs.get('jobs',[]) if j.get('name')==JOB]
    need(jobs.get('total_count')==len(jobs.get('jobs',[])) and len(selected)==1)
    job=selected[0];need(type(job.get('id')) is int and job.get('status')=='completed' and job.get('conclusion')=='success')
    steps=job.get('steps');need(type(steps) is list)
    for name in ('Run one candidate and the independent audit','Independently inspect workflow origin',
                 'Recheck observed evidence after cleanup','Stage final raw audit evidence','Seal the raw evidence',
                 'Keep encrypted raw evidence'):
        matches=[x for x in steps if x.get('name')==name]
        need(len(matches)==1 and matches[0].get('status')=='completed' and matches[0].get('conclusion')=='success')
    arun=artifact.get('workflow_run',{})
    need(type(artifact.get('id')) is int and artifact.get('name')==ARTIFACT and artifact.get('expired') is False
         and arun.get('id')==run['id'] and arun.get('head_sha')==head
         and arun.get('repository_id')==arun.get('head_repository_id')==REPO_ID
         and artifact.get('digest')=='sha256:'+digest(archive))
    need(restored.is_dir() and not restored.is_symlink() and not any(p.is_symlink() for p in restored.parents))
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist();names=[x.filename for x in entries]
        need(len(names)==len(set(names)) and 'evidence-index.json' in names and all(not x.is_dir() for x in entries))
        indexraw=z.read('evidence-index.json');index=json.loads(indexraw)
        need(index.get('version')=='som.m6-evidence-index.v1' and index.get('status')=='encrypted'
             and index.get('product_acceptance')=='read-summary' and type(index.get('packages')) is list and index['packages'])
        expected={'evidence-index.json'};files={}
        for number,pkg in enumerate(index['packages']):
            name=f'package-{number:03d}';manifest=name+'.manifest.json';cipher=name+'.sealed'
            need(pkg.get('manifest')==manifest and pkg.get('archive')==cipher)
            expected.update((manifest,cipher));raw=z.read(manifest)
            need(hashlib.sha256(raw).hexdigest()==pkg.get('manifest_sha256'))
            h=hashlib.sha256()
            with z.open(cipher) as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
            need(h.hexdigest()==pkg.get('cipher_sha256'))
            doc=json.loads(raw);need(doc.get('cipher_sha256')==pkg['cipher_sha256'] and len(doc.get('files',[]))==pkg.get('total_files'))
            for item in doc['files']:
                rel=item.get('path');p=Path(rel) if type(rel) is str else Path('/')
                need(not p.is_absolute() and '..' not in p.parts and rel not in files and type(item.get('bytes')) is int)
                target=restored/p;need(target.is_file() and not target.is_symlink() and not any(x.is_symlink() for x in target.parents)
                     and target.stat().st_size==item['bytes'] and digest(target)==item.get('sha256'))
                files[rel]=item['sha256']
        need(set(names)==expected)
    actual={p.relative_to(restored).as_posix() for p in restored.rglob('*') if p.is_file()}
    need(actual==set(files))
    evidence=restored/'run/final-audit-evidence';inventory=obj(evidence/'inventory.json')
    need(inventory.get('version')=='som.m6-final-audit-evidence.v1' and inventory.get('missing')==[]
         and set(inventory.get('present_sha256',{}))==set(REQUIRED) and inventory.get('product_acceptance_passed') is False)
    for name in REQUIRED:need(digest(evidence/name)==inventory['present_sha256'][name])
    preliminary=obj(evidence/'post-acceptance-observations.json')
    need(preliminary.get('version')=='som.m6-post-acceptance-observations.v1' and preliminary.get('status')=='provisional' and preliminary.get('final_verdict')=='awaiting-external-readback'
         and preliminary.get('product_acceptance_passed') is False and preliminary.get('rows')
         and len(preliminary['rows'])==len(ROWS) and {x.get('id') for x in preliminary['rows']}==set(ROWS)
         and all(x.get('status')=='passed' for x in preliminary['rows']))
    origin=next((x.get('evidence') for x in preliminary['rows'] if x.get('id')=='workflow-origin'),None)
    live_run=obj(evidence/'origin-run.json');live_jobs=obj(evidence/'origin-jobs.json')
    need(origin and origin.get('run_id')==live_run.get('id')==run['id'] and origin.get('job_id')==job['id']
         and origin.get('head_sha')==live_run.get('head_sha')==head and live_run.get('status')=='in_progress'
         and any(j.get('id')==job['id'] for j in live_jobs.get('jobs',[])))
    return {'status':'readback-verified','run_id':run['id'],'job_id':job['id'],'head_sha':head,
            'artifact_id':artifact['id'],'artifact_sha256':digest(archive),'restored_files':len(files),
            'product_acceptance_passed':False,'final_verdict':'requires-independent-raw-recheck',
            'cleanup_scope':'observed processes and listeners only'}

def gh_api(path):
    raw=subprocess.check_output(['gh','api',path],timeout=60)
    return json.loads(raw)

def main():
    p=argparse.ArgumentParser()
    for name in ('archive','restored','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('head','run-id','artifact-id'):p.add_argument('--'+name,required=True)
    a=p.parse_args();need(not a.out.exists() and not a.out.is_symlink())
    try:
        need(a.run_id.isdigit() and a.artifact_id.isdigit())
        prefix='repos/faberline/som/actions/'
        run=gh_api(prefix+'runs/'+a.run_id)
        jobs=gh_api(prefix+'runs/'+a.run_id+'/jobs?per_page=100')
        artifact=gh_api(prefix+'artifacts/'+a.artifact_id)
        need(run.get('id')==int(a.run_id) and artifact.get('id')==int(a.artifact_id))
        result=check(a.archive,a.restored,run,jobs,artifact,a.head)
    except Exception as exc:result={'status':'unverified','product_acceptance_passed':False,'error_type':type(exc).__name__}
    with a.out.open('x') as stream:json.dump(result,stream,sort_keys=True,indent=2);stream.write('\n')
    print(json.dumps(result,sort_keys=True));return 0 if result['status']=='readback-verified' else 1
if __name__=='__main__':raise SystemExit(main())
