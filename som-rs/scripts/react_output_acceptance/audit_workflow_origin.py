"""Check bounded GH run/job/source metadata, not product acceptance."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REPO_ID=1390374878
class Refused(ValueError):pass

def need(ok):
    if not ok:raise Refused('GitHub workflow origin refused')

def verify(repo,env,run,jobs,*,workflow,job_name,completed_step,event):
    revision=env.get('GITHUB_SHA','');workflow_sha=env.get('GITHUB_WORKFLOW_SHA','')
    need(env.get('GITHUB_ACTIONS')=='true' and env.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and env.get('RUNNER_OS')=='macOS' and env.get('RUNNER_ARCH')=='ARM64'
         and env.get('GITHUB_REPOSITORY')=='faberline/som' and env.get('GITHUB_EVENT_NAME')==event
         and re.fullmatch('[0-9a-f]{40}',revision) is not None and workflow_sha==revision
         and env.get('GITHUB_RUN_ID','').isdigit() and env.get('GITHUB_RUN_ATTEMPT','').isdigit())
    need(env.get('GITHUB_WORKFLOW_REF')=='faberline/som/'+workflow+'@'+env.get('GITHUB_REF',''))
    need(run.get('id')==int(env['GITHUB_RUN_ID']) and run.get('run_attempt')==int(env['GITHUB_RUN_ATTEMPT'])
         and run.get('event')==event and run.get('head_sha')==revision and run.get('path')==workflow
         and run.get('status')=='in_progress')
    for key in ('repository','head_repository'):
        need(run.get(key,{}).get('id')==REPO_ID and run[key].get('full_name')=='faberline/som')
    need(type(jobs.get('jobs')) is list and jobs.get('total_count')==len(jobs['jobs']))
    selected=[job for job in jobs['jobs'] if job.get('name')==job_name and job.get('runner_name')==env.get('RUNNER_NAME')]
    need(len(selected)==1 and selected[0].get('status')=='in_progress' and type(selected[0].get('id')) is int)
    steps=selected[0].get('steps');need(type(steps) is list)
    checked=[step for step in steps if step.get('name')==completed_step]
    need(len(checked)==1 and checked[0].get('status')=='completed' and checked[0].get('conclusion')=='success')
    head=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'rev-parse','--verify','HEAD^{commit}'],timeout=30).decode().strip()
    need(head==revision)
    entry=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'ls-tree','-z',revision,'--',workflow],timeout=30)
    need(entry.startswith(b'100644 blob ') and entry.endswith(b'\t'+workflow.encode()+b'\0') and entry.count(b'\0')==1)
    source=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'show',revision+':'+workflow],timeout=30)
    path=repo/workflow;need(path.is_file() and not path.is_symlink() and path.read_bytes()==source)
    return {'status':'observed','run_id':run['id'],'job_id':selected[0]['id'],'head_sha':revision,
            'workflow_sha256':hashlib.sha256(source).hexdigest(),'product_acceptance':'not_inferred'}

def main():
    import argparse
    p=argparse.ArgumentParser()
    for name in ('run','jobs','workflow','job-name','completed-step','event'):p.add_argument('--'+name,required=True)
    a=p.parse_args();repo=Path(os.environ['GITHUB_WORKSPACE']).resolve()
    result=verify(repo,os.environ,json.loads(Path(a.run).read_bytes()),json.loads(Path(a.jobs).read_bytes()),
                  workflow=a.workflow,job_name=a.job_name,completed_step=a.completed_step,event=a.event)
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
