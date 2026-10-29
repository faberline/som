"""Run the unchanged candidate preparer in the driver's process and retain its child.

The new Mac path alone uses this owner. Missing ownership or wait is never success.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import time
from unittest import mock

class OwnershipError(ValueError):pass

def need(ok):
    if not ok:raise OwnershipError('candidate ownership refused')

class CandidateOwner:
    def __init__(self, out, candidate, port):
        self.out=Path(out);self.candidate=Path(candidate);self.port=port;self.process=None;self.events=[]
        self.argv=[str(self.candidate/'som'),'serve','--assets',str(self.candidate/'assets'),'--port',str(port)]
        need(port not in (8765,18765) and 1<=port<=65535)
    def run_preparer(self, script, arguments, env, *, timeout=14400):
        original=subprocess.Popen;before_argv=sys.argv;before_env=dict(os.environ);old_handler=signal.getsignal(signal.SIGALRM)
        original_poll=None
        def spawn(argv,*args,**kwargs):
            nonlocal original_poll
            if isinstance(argv,(list,tuple)) and list(map(os.fspath,argv))==self.argv:
                need(self.process is None and kwargs.get('start_new_session') is True and
                     Path(kwargs.get('cwd'))==self.candidate)
                proc=original(argv,*args,**kwargs);self.process=proc;original_poll=proc.poll
                def non_reaping_poll():
                    result=subprocess.run(['/bin/ps','-p',str(proc.pid),'-o','pid=,ppid=,pgid=,stat='],
                                          capture_output=True,check=False,timeout=10)
                    fields=result.stdout.decode().split()
                    if result.returncode!=0 or len(fields)!=4 or fields[:3]!=[str(proc.pid),str(os.getpid()),str(proc.pid)]:return -1
                    return -1 if fields[3].startswith('Z') else None
                proc.poll=non_reaping_poll
                self.events.append({'kind':'candidate-start','pid':proc.pid,'parent_pid':os.getpid(),'argv':self.argv,'cwd':str(self.candidate)})
                return proc
            return original(argv,*args,**kwargs)
        def expired(signum,frame):raise TimeoutError('candidate preparer timeout')
        status=1
        try:
            os.environ.clear();os.environ.update(env);sys.argv=[str(script),*arguments]
            signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,timeout)
            with (self.out/'prepare.stdout').open('w') as stdout,(self.out/'prepare.stderr').open('w') as stderr, \
                 contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr),mock.patch.object(subprocess,'Popen',spawn):
                try:
                    runpy.run_path(str(script),run_name='__main__');status=0
                except SystemExit as exc:status=exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_handler)
            sys.argv=before_argv;os.environ.clear();os.environ.update(before_env)
            if self.process is not None and original_poll is not None:self.process.poll=original_poll
        self.events.append({'kind':'preparer-exit','exit_code':status})
        if status==0:need(self.process is not None and self.process.returncode is None)
        return status
    def wait_after_signal(self, timeout=10, grace=0.25):
        proc=self.process
        # Never poll/wait before group signals: an unreaped direct child reserves
        # its PID, so this PID cannot be recycled as a new process-group ID.
        need(proc is not None and proc.returncode is None and signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL)
        # ps reads identity without waitpid. A zombie is still our unreaped
        # child and keeps its PID reserved; any other owner/PGID is refused.
        identity_argv=['/bin/ps','-ww','-p',str(proc.pid),'-o','pid=,ppid=,pgid=,stat=,command=']
        result=subprocess.run(identity_argv,capture_output=True,check=False,timeout=10)
        fields=result.stdout.decode().strip().split(None,4)
        need(result.returncode==0 and len(fields)==5 and fields[0]==str(proc.pid)
             and fields[1]==str(os.getpid()) and fields[2]==str(proc.pid)
             and (fields[3].startswith('Z') or fields[4]==' '.join(self.argv)))
        # Darwin getpgid returns ESRCH for zombies. If the leader exits
        # between ps and getpgid, re-read identity without reaping it.
        if not fields[3].startswith('Z'):
            try:
                need(os.getpgid(proc.pid)==proc.pid)
            except ProcessLookupError:
                result=subprocess.run(identity_argv,capture_output=True,check=False,timeout=10)
                fields=result.stdout.decode().strip().split(None,4)
                need(proc.returncode is None and result.returncode==0 and len(fields)==5 and
                     fields[:3]==[str(proc.pid),str(os.getpid()),str(proc.pid)] and fields[3].startswith('Z'))
        evidence_ok=True
        try:
            with (self.out/'candidate-signal-identity.stdout').open('xb') as f:f.write(result.stdout)
            with (self.out/'candidate-signal-identity.stderr').open('xb') as f:f.write(result.stderr)
        except OSError as exc:
            evidence_ok=False
            self.events.append({'kind':'candidate-evidence-error','error_type':type(exc).__name__})
        self.events.append({'kind':'candidate-signal-identity','pid':proc.pid,'parent_pid':os.getpid(),
                            'pgid':proc.pid,'state':fields[3],'argv':identity_argv,'exit_code':result.returncode,
                            'stdout_sha256':hashlib.sha256(result.stdout).hexdigest(),
                            'stderr_sha256':hashlib.sha256(result.stderr).hexdigest()})
        try:
            os.killpg(proc.pid,signal.SIGTERM)
            self.events.append({'kind':'candidate-signal','pid':proc.pid,'signal':'TERM'})
        except ProcessLookupError:
            self.events.append({'kind':'candidate-signal','pid':proc.pid,'signal':'TERM','group':'absent'})
        time.sleep(grace)
        try:
            os.killpg(proc.pid,signal.SIGKILL)
            self.events.append({'kind':'candidate-signal','pid':proc.pid,'signal':'KILL'})
        except ProcessLookupError:
            self.events.append({'kind':'candidate-signal','pid':proc.pid,'signal':'KILL','group':'absent'})
        try:
            code=proc.wait(timeout=timeout)
            self.events.append({'kind':'candidate-wait','pid':proc.pid,'state':'completed','exit_code':code})
            need(evidence_ok)
            return code
        except subprocess.TimeoutExpired:
            self.events.append({'kind':'candidate-wait','pid':proc.pid,'state':'timeout','exit_code':None})
            try:
                os.killpg(proc.pid,signal.SIGKILL)
                code=proc.wait(timeout=timeout)
                self.events.append({'kind':'candidate-cleanup-wait','pid':proc.pid,'state':'completed','exit_code':code})
            except (OSError,subprocess.SubprocessError):
                self.events.append({'kind':'candidate-cleanup-wait','pid':proc.pid,'state':'unverified','exit_code':None})
            raise
    def save(self):
        with (self.out/'candidate-owner.json').open('x') as f:
            json.dump({'version':'som.m6-owned-candidate.v1','events':self.events,'product_acceptance_passed':False},f,indent=2,sort_keys=True);f.write('\n')
