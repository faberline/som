"""Capture the direct QA baseline stop, wait, and observed process evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

PORT=18917

def sha(raw):return hashlib.sha256(raw).hexdigest()

def stop(out,env,process,frozen):
    out=Path(out);frozen=Path(frozen)
    result={'version':'som.m6-baseline-cleanup.v1','pid':None,'events':[],
            'status':'unverified','product_acceptance_passed':False}
    if process is None or frozen is None:
        return result
    pid=process.pid;result['pid']=pid; result['parent_pid']=os.getpid()
    result['argv']=[str(frozen/'som'),'serve','--assets',str(frozen/'assets'),'--port',str(PORT)]
    result['cwd']=str(frozen)
    def command(name,argv):
        row={'kind':'os-command','name':name,'argv':argv,'exit_code':None}
        try:
            done=subprocess.run(argv,env=env,capture_output=True,check=False,timeout=30)
            row['exit_code']=done.returncode
            for key in ('stdout','stderr'):
                raw=getattr(done,key);filename='baseline-cleanup-'+name+'.'+key
                with (out/filename).open('xb') as f:f.write(raw)
                row[key]={'file':filename,'sha256':sha(raw)}
        except (OSError,subprocess.SubprocessError) as exc:row['error_type']=type(exc).__name__
        result['events'].append(row)
    command('ps',['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command='])
    command('cwd',['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'])
    command('listener',['/usr/sbin/lsof','-nP',f'-iTCP:{PORT}','-sTCP:LISTEN','-t'])
    command('before',['/bin/ps','-axo','pid=,ppid=,pgid=,command='])
    try:
        process.terminate();result['events'].append({'kind':'signal','pid':pid,'signal':'TERM','state':'sent'})
    except OSError as exc:
        result['events'].append({'kind':'signal','pid':pid,'signal':'TERM','state':'error','errno':exc.errno})
    try:
        code=process.wait(timeout=10);result['events'].append({'kind':'wait','pid':pid,'state':'completed','exit_code':code})
    except subprocess.TimeoutExpired:
        result['events'].append({'kind':'wait','pid':pid,'state':'timeout','exit_code':None})
        try:process.kill();result['events'].append({'kind':'cleanup-signal','pid':pid,'signal':'KILL','state':'sent'})
        except OSError as exc:result['events'].append({'kind':'cleanup-signal','pid':pid,'signal':'KILL','state':'error','errno':exc.errno})
        try:
            code=process.wait(timeout=10);result['events'].append({'kind':'cleanup-wait','pid':pid,'state':'completed','exit_code':code})
        except subprocess.SubprocessError:result['events'].append({'kind':'cleanup-wait','pid':pid,'state':'unverified','exit_code':None})
    command('after',['/bin/ps','-axo','pid=,ppid=,pgid=,command='])
    command('listener-after',['/usr/sbin/lsof','-nP',f'-iTCP:{PORT}','-sTCP:LISTEN','-t'])
    with (out/'baseline-cleanup.json').open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    return result
