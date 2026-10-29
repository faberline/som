"""Capture the direct QA baseline stop, wait, and observed process evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import signal
import time

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
    command('ps',['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,stat=,command='])
    command('cwd',['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'])
    command('listener',['/usr/sbin/lsof','-nP',f'-iTCP:{PORT}','-sTCP:LISTEN','-t'])
    command('before',['/bin/ps','-axo','pid=,ppid=,pgid=,command='])
    verified=False
    try:
        ps=(out/'baseline-cleanup-ps.stdout').read_bytes().decode().strip().split(None,4)
        cwd=(out/'baseline-cleanup-cwd.stdout').read_bytes().decode().splitlines()
        listeners=(out/'baseline-cleanup-listener.stdout').read_bytes().decode().split()
        verified=(all(e.get('exit_code')==0 and not e.get('error_type') for e in result['events'][:3]) and
                  process.returncode is None and signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL and
                  len(ps)==5 and ps[0]==str(pid) and ps[1]==str(os.getpid()) and ps[2].isdigit() and
                  not ps[3].startswith('Z') and ps[4]==' '.join(result['argv']) and
                  [x[1:] for x in cwd if x.startswith('p')]==[str(pid)] and
                  [x[1:] for x in cwd if x.startswith('n')]==[str(frozen)] and listeners==[str(pid)])
    except (OSError,ValueError):pass
    signal_event={'kind':'signal','pid':pid,'signal':'TERM','state':'skipped-unverified'}
    if verified:
        try:os.kill(pid,signal.SIGTERM);signal_event['state']='syscall-succeeded'
        except OSError as exc:signal_event.update(state='syscall-error',errno=exc.errno)
    result['events'].append(signal_event)
    def reap(timeout):
        deadline=time.monotonic()+timeout
        while True:
            try:waited,status=os.waitpid(pid,os.WNOHANG)
            except ChildProcessError:return {'kind':'wait','pid':pid,'state':'not-a-child','exit_code':None}
            if waited==pid:
                code=os.waitstatus_to_exitcode(status);process.returncode=code
                return {'kind':'wait','pid':pid,'state':'reaped','waited_pid':waited,'raw_status':status,'exit_code':code}
            if time.monotonic()>=deadline:return {'kind':'wait','pid':pid,'state':'timeout','exit_code':None}
            time.sleep(.05)
    if process.returncode is None:
        wait=reap(10);result['events'].append(wait)
        if wait['state']=='timeout' and verified:
            try:
                os.kill(pid,signal.SIGKILL);result['events'].append({'kind':'cleanup-signal','pid':pid,'signal':'KILL','state':'syscall-succeeded'})
            except OSError as exc:result['events'].append({'kind':'cleanup-signal','pid':pid,'signal':'KILL','state':'syscall-error','errno':exc.errno})
            cleanup=reap(10);cleanup['kind']='cleanup-wait';result['events'].append(cleanup)
    else:result['events'].append({'kind':'wait','pid':pid,'state':'already-reaped','exit_code':process.returncode})
    command('after',['/bin/ps','-axo','pid=,ppid=,pgid=,command='])
    command('listener-after',['/usr/sbin/lsof','-nP',f'-iTCP:{PORT}','-sTCP:LISTEN','-t'])
    with (out/'baseline-cleanup.json').open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    return result
