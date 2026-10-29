#!/usr/bin/env python3
"""Collect actual same-candidate parity on a hosted Mac; not acceptance."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import importlib
import hashlib
import contextlib
from unittest import mock
from record_api import Recorder

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
CHECKER=REPO/'som-rs/behavior/check_behavior.py'
PARITY_SOURCES=('som-rs/scripts/react_output_acceptance/run_hosted_acceptance.py', 'som-rs/scripts/react_output_acceptance/mac_parity_capture.py', 'som-rs/scripts/react_output_acceptance/product_http_recorder.py', 'som-rs/scripts/react_output_acceptance/record_rust006_http.py', 'som-rs/scripts/react_output_acceptance/record_api.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_parity.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_normalization.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_oracle.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_rust006_extra.py', 'som-rs/behavior/check_behavior.py', 'som-rs/behavior/suite.py', 'som-rs/behavior/hosts.py', 'som-rs/behavior/faults.py', 'som-rs/som-targets/qa/check_targets.py', 'som-rs/som-targets/qa/check_rust_products.py', 'som-rs/tests/rust-output-v1/rust_profile.py', 'som-rs/tests/rust-output-v1/rust_profile_phase2.py', 'som-rs/scripts/rust_output_acceptance/python_replay.py', 'som-rs/scripts/compiler_acceptance/qa_harness.py', 'som-rs/scripts/compiler_acceptance/paths.py', 'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py', 'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py', 'som-rs/scripts/react_output_acceptance/install_derived_inputs.py', 'som-rs/scripts/react_output_acceptance/audit_supplement_provision.py', 'som-rs/scripts/react_output_acceptance/audit/independent_behavior_oracle.py', 'som-rs/scripts/react_output_acceptance/mac_rust006_extra.py', 'som-rs/scripts/react_output_acceptance/owned_candidate.py', 'som-rs/scripts/react_output_acceptance/audit/audit_candidate_owner.py')

def source_hashes():
    return {name:hashlib.sha256((REPO/name).read_bytes()).hexdigest() for name in PARITY_SOURCES}

def need(value,reason):
    if not value:raise ValueError(reason)

class TrackingRecorder(Recorder):
    def __init__(self,out):
        super().__init__(out,subprocess.run,subprocess.Popen)
        self.lock=threading.RLock();self.child_sequence=0
    def popen(self,argv,*a,**kw):
        proc=self.original_popen(argv,*a,**kw)
        with self.lock:
            self.child_sequence+=1; identity=self.child_sequence
            self.events.append({'kind':'child-start','identity':identity,'pid':proc.pid,
                'argv':[os.fspath(x) for x in argv] if isinstance(argv,(list,tuple)) else str(argv),
                'cwd':os.fspath(kw.get('cwd') or Path.cwd())})
        original=proc.wait
        def observed_wait(*args,**kwargs):
            try:code=original(*args,**kwargs)
            except BaseException as exc:
                with self.lock:self.events.append({'kind':'child-wait','identity':identity,'pid':proc.pid,
                                                   'state':'unverified','error_type':type(exc).__name__})
                raise
            with self.lock:self.events.append({'kind':'child-wait','identity':identity,'pid':proc.pid,
                                               'state':'completed','exit_code':code})
            return code
        proc.wait=observed_wait
        return proc
    def product_snapshot(self,pid,phase,port):
        """One process table plus the minimum ownership-specific lsof calls."""
        event={'kind':'process','pid':pid,'phase':phase,'port':port,
               'table':self.command(['/bin/ps','-axo','pid=,ppid=,pgid=,command=']),
               'listener':self.command(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'])}
        if phase=='product-ready':
            event['cwd']=self.command(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'])
        self.events.append(event)

    def run(self,argv,*a,**kw):
        command=[os.fspath(x) for x in argv] if isinstance(argv,(list,tuple)) else []
        # Only the recorded pg_ctl/initdb/identity snapshots mutate log sequence.
        if command and (Path(command[0]).name in ('pg_ctl','initdb') or
                        (Path(command[0]).name=='lsof' and 'cwd' in command)):
            with self.lock:return super().run(argv,*a,**kw)
        return self.original_run(argv,*a,**kw)

def main():
    p=argparse.ArgumentParser()
    for name in ('raw-out','lifecycle','parity-out','target-dir','python','candidate'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--candidate-revision',required=True)
    args=p.parse_args()
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and os.environ.get('RUNNER_OS')=='macOS','GitHub Mac required')
    paths=[Path(os.path.abspath(x)) for x in (args.raw_out,args.lifecycle,args.parity_out,args.target_dir)]
    need(all(x.is_relative_to(REPO/'.local') and not x.exists() and not x.is_symlink()
             and not any(parent.is_symlink() for parent in x.parents) for x in paths), 'new owned paths required')
    raw,lifecycle,parity,target=paths
    candidate=Path(os.path.abspath(args.candidate))
    need(candidate.is_relative_to(REPO/'.local') and candidate.is_dir() and not candidate.is_symlink(), 'candidate root differs')
    before_sources=source_hashes()
    revision=os.environ.get('GITHUB_SHA')
    python=Path(os.path.abspath(args.python))
    need(python==REPO/'som-rs/runtime/acceptance-m3-venv/bin/python' and python.is_file(), 'frozen venv required')
    lifecycle.mkdir(parents=True,mode=0o700)
    import record_rust006_http
    observer=TrackingRecorder(lifecycle)
    import mac_rust006_extra as candidate_probe
    probe=lifecycle/'candidate-probe';probe.mkdir(mode=0o700);(probe/'logs').mkdir(mode=0o700)
    runtime=json.loads((candidate/'runtime.json').read_bytes())
    need(runtime.get('port')==18919 and runtime.get('accepted') is False and
         args.candidate_revision==os.environ.get('GITHUB_SHA'), 'candidate source or port differs')
    candidate_before=candidate_probe.observe(probe,'before',runtime)
    # Load the same suite module that check_behavior imports. Record at its
    # transport seam without changing the HTTP result consumed by the suite.
    sys.path.insert(0, str(CHECKER.parent))
    try:
        suite=importlib.import_module('suite')
        hosts=importlib.import_module('hosts')
    finally:
        sys.path.pop(0)
    import product_http_recorder
    product=product_http_recorder.ProductRecorder(lifecycle/'product-http',suite)
    original_serve=hosts.serve
    @contextlib.contextmanager
    def observed_serve(*a,**kw):
        original=original_serve(*a,**kw); owner=None; identity=None; pid=None; port=None
        try:
            with original as base:
                try:
                    identity=product.context(); process=original.gen.gi_frame.f_locals['process']; pid=process.pid
                    port=int(base.rsplit(':',1)[1])
                    with observer.lock:
                        observer.product_snapshot(pid,'product-ready',port); start_event=len(observer.events)-1
                        owner=product.bind(identity,base,pid,start_event)
                except Exception as exc:
                    with observer.lock: observer.errors.append('ProductOwnerStart'+type(exc).__name__)
                yield base
        finally:
            if owner is not None:
                try:
                    with observer.lock:
                        observer.product_snapshot(pid,'product-stopped',port); stop_event=len(observer.events)-1
                        product.release(identity,owner,stop_event)
                except Exception as exc:
                    with observer.lock: observer.errors.append('ProductOwnerStop'+type(exc).__name__)
    code=1
    try:
        with mock.patch.object(subprocess,'run',observer.run), mock.patch.object(subprocess,'Popen',observer.popen), \
             mock.patch.object(suite,'http_send',product.send), mock.patch.object(suite,'run',product.run), \
             mock.patch.object(suite.Run,'strict',lambda owner: product.strict(owner)), \
             mock.patch.object(hosts,'serve',observed_serve):
            code=record_rust006_http.run_checker(raw,CHECKER,['parity','--qa','http://127.0.0.1:18919',
                 '--out',str(parity),'--target-dir',str(target),'--python',str(python),'--jobs','4'])
    finally:
        try:
            candidate_after=candidate_probe.observe(probe,'after',runtime)
        except Exception as exc:
            candidate_after=None;observer.errors.append('CandidateAfter'+type(exc).__name__)
        collector={'version':'som.m6-mac-rust006-extra.v1','candidate_revision':args.candidate_revision,
                   'candidate_binary_sha256':hashlib.sha256((candidate/'som').read_bytes()).hexdigest(),
                   'product_acceptance_passed':False,'candidate_before':candidate_before,'candidate_after':candidate_after}
        (probe/'collector.json').write_text(json.dumps(collector,indent=2,sort_keys=True)+'\n')
        if not product.finish(): observer.errors.append('ProductHTTPRecordingFailed')
        starts=[e for e in observer.events if e.get('kind')=='postmaster' and e.get('phase')=='after-start']
        if starts:
            try:
                row=starts[-1]['raw']; content=(lifecycle/row['file']).read_bytes().splitlines()
                observer.snapshot(int(content[0]),'after-stop',int(content[3]))
            except Exception as exc:observer.errors.append(type(exc).__name__)
        else:observer.errors.append('PostmasterNotObserved')
        with observer.lock:
            starts=[e for e in observer.events if e.get('kind')=='child-start']
            waits=[e for e in observer.events if e.get('kind')=='child-wait']
            if any(not any(w.get('identity')==e['identity'] and w.get('state')=='completed'
                           and type(w.get('exit_code')) is int for w in waits) for e in starts):
                observer.errors.append('DirectChildWaitMissing')
            if any(e.get('state')=='unverified' for e in waits):observer.errors.append('WaitUnverified')
        receipt={'version':'som.m6-mac-parity-capture.v1','exit_code':code,'events':observer.events,
                 'source_revision':revision,'source_before':before_sources,'source_after':source_hashes(),
                 'recording_errors':observer.errors,'product_acceptance_passed':False,
                 'cleanup':'requires-independent-raw-audit'}
        (lifecycle/'receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return code if not observer.errors else 1

if __name__=='__main__':raise SystemExit(main())
