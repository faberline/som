"""Run the unchanged candidate preparer in the driver's process and retain its child.

The new Mac path alone uses this owner. Missing ownership or wait is never success.
"""
import contextlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
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
        def spawn(argv,*args,**kwargs):
            if isinstance(argv,(list,tuple)) and list(map(os.fspath,argv))==self.argv:
                need(self.process is None and kwargs.get('start_new_session') is True and
                     Path(kwargs.get('cwd'))==self.candidate)
                proc=original(argv,*args,**kwargs);self.process=proc
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
        self.events.append({'kind':'preparer-exit','exit_code':status})
        if status==0:need(self.process is not None and self.process.poll() is None)
        return status
    def wait_after_signal(self, timeout=10):
        proc=self.process;need(proc is not None and proc.poll() is None and os.getpgid(proc.pid)==proc.pid)
        os.killpg(proc.pid,signal.SIGTERM)
        try:
            code=proc.wait(timeout=timeout)
            self.events.append({'kind':'candidate-wait','pid':proc.pid,'state':'completed','exit_code':code})
            return code
        except subprocess.TimeoutExpired:
            self.events.append({'kind':'candidate-wait','pid':proc.pid,'state':'timeout','exit_code':None})
            raise
    def save(self):
        with (self.out/'candidate-owner.json').open('x') as f:
            json.dump({'version':'som.m6-owned-candidate.v1','events':self.events,'product_acceptance_passed':False},f,indent=2,sort_keys=True);f.write('\n')
