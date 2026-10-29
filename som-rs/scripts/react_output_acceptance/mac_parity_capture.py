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
import shutil
from unittest import mock
from record_api import Recorder

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
CHECKER=REPO/'som-rs/behavior/check_behavior.py'
PARITY_SOURCES=('som-rs/scripts/react_output_acceptance/run_hosted_acceptance.py', 'som-rs/scripts/react_output_acceptance/mac_parity_capture.py', 'som-rs/scripts/react_output_acceptance/product_http_recorder.py', 'som-rs/scripts/react_output_acceptance/parity_product_files.py', 'som-rs/scripts/react_output_acceptance/parity_rust_build.py', 'som-rs/scripts/react_output_acceptance/parity_python_runtime.py', 'som-rs/scripts/react_output_acceptance/parity_python_probe.py', 'som-rs/scripts/react_output_acceptance/parity_python_authority.py', 'som-rs/scripts/react_output_acceptance/record_rust006_http.py', 'som-rs/scripts/react_output_acceptance/record_api.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_parity.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_normalization.py', 'som-rs/scripts/react_output_acceptance/audit/audit_product_oracle.py', 'som-rs/scripts/react_output_acceptance/audit/audit_mac_rust006_extra.py', 'som-rs/behavior/check_behavior.py', 'som-rs/behavior/suite.py', 'som-rs/behavior/hosts.py', 'som-rs/behavior/faults.py', 'som-rs/som-targets/qa/check_targets.py', 'som-rs/som-targets/qa/check_rust_products.py', 'som-rs/tests/rust-output-v1/rust_profile.py', 'som-rs/tests/rust-output-v1/rust_profile_phase2.py', 'som-rs/scripts/rust_output_acceptance/python_replay.py', 'som-rs/scripts/compiler_acceptance/qa_harness.py', 'som-rs/scripts/compiler_acceptance/paths.py', 'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py', 'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py', 'som-rs/scripts/react_output_acceptance/install_derived_inputs.py', 'som-rs/scripts/react_output_acceptance/audit_supplement_provision.py', 'som-rs/scripts/react_output_acceptance/audit/independent_behavior_oracle.py', 'som-rs/scripts/react_output_acceptance/mac_rust006_extra.py', 'som-rs/scripts/react_output_acceptance/owned_candidate.py', 'som-rs/scripts/react_output_acceptance/audit/audit_candidate_owner.py', 'som-rs/scripts/react_output_acceptance/audit_workflow_origin.py', 'som-rs/scripts/react_output_acceptance/baseline_cleanup.py', 'som-rs/scripts/react_output_acceptance/audit/audit_baseline_cleanup.py', 'som-rs/scripts/react_output_acceptance/post_acceptance_audit.py', 'som-rs/scripts/react_output_acceptance/stage_final_evidence.py', 'som-rs/scripts/react_output_acceptance/external_acceptance_readback.py', 'som-rs/scripts/react_output_acceptance/cross_job_raw_reaudit.py', 'som-rs/scripts/react_output_acceptance/audit/audit130.py', 'som-rs/scripts/react_output_acceptance/audit/api_raw.py', 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py', 'som-rs/scripts/react_output_acceptance/audit/render_rules.py', 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py')

def source_hashes():
    return {name:hashlib.sha256((REPO/name).read_bytes()).hexdigest() for name in PARITY_SOURCES + ('som-rs/scripts/react_output_acceptance/macos_bottles.json','som-rs/scripts/react_output_acceptance/provision_macos.py','som-rs/scripts/react_output_acceptance/provision_python_identity.py')}

def postgres_tool_hashes():
    contract=json.loads((REPO/'som-rs/contracts/rust-output-v1/toolchain.json').read_bytes())['postgresql']
    directory=Path(contract['bin_dir'])
    return {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('postgres','initdb','pg_ctl')}

def rust_tool_hashes():
    contract=json.loads((REPO/'som-rs/contracts/rust-output-v1/toolchain.json').read_bytes())['rust']
    directory=Path(contract['bin_dir'])
    return {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('rustc','cargo','rustfmt','cargo-fmt','cargo-clippy','clippy-driver')}

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
            words=[os.fspath(x) for x in argv] if isinstance(argv,(list,tuple)) else str(argv)
            event={'kind':'child-start','identity':identity,'pid':proc.pid,'argv':words,
                   'cwd':os.fspath(kw.get('cwd') or Path.cwd())}
            if isinstance(words,list) and (words[1:3]==['-m','uvicorn'] or words[1:]==['build','--locked','--offline','-q']):
                supplied=kw.get('env') if kw.get('env') is not None else os.environ
                allowed=('PYTHONPATH','PYTHONDONTWRITEBYTECODE','SOM_BEHAVIOR_PACKAGE','CARGO_TARGET_DIR',
                         'CARGO_NET_OFFLINE','RUSTC','CARGO','RUSTFMT','PATH')
                event['environment']={key:supplied[key] for key in allowed if key in supplied}
                if words[1:3]==['-m','uvicorn']:
                    event['unexpected_python_environment_keys']=sorted(key for key in supplied if key.startswith('PYTHON') and key not in ('PYTHONPATH','PYTHONDONTWRITEBYTECODE'))
            self.events.append(event)
        original=proc.wait
        local=threading.local();direct_exit=[]
        # Popen._try_wait converts ECHILD to a fabricated zero status. Keep its
        # behavior for the caller, but retain only an actual waitpid result as
        # evidence. A cached returncode produces no such observation.
        def observed_try_wait(flags):
            try:waited,status=os.waitpid(proc.pid,flags)
            except ChildProcessError:return proc.pid,0
            if waited and getattr(local,'active',False):
                local.reaped=(waited,status)
            return waited,status
        proc._try_wait=observed_try_wait
        def observed_wait(*args,**kwargs):
            local.active=True;local.reaped=None
            try:code=original(*args,**kwargs)
            except BaseException as exc:
                with self.lock:self.events.append({'kind':'child-wait','identity':identity,'pid':proc.pid,
                                                   'state':'unverified','error_type':type(exc).__name__})
                raise
            finally:local.active=False
            reaped=local.reaped
            event={'kind':'child-wait','identity':identity,'pid':proc.pid,'state':'unverified','exit_code':code}
            if reaped is not None and reaped[0]==proc.pid and os.waitstatus_to_exitcode(reaped[1])==code:
                event.update(state='completed',waited_pid=reaped[0],raw_status=reaped[1]);direct_exit.append(code)
            elif direct_exit and direct_exit[-1]==code:
                event['state']='cached-after-direct-wait'
            with self.lock:self.events.append(event)
            return code
        proc.wait=observed_wait
        return proc
    def product_snapshot(self,pid,phase,port,image=None,follow=False):
        """One process table plus the minimum ownership-specific lsof calls."""
        event={'kind':'process','pid':pid,'phase':phase,'port':port,
               'table':self.command(['/bin/ps','-axo','pid=,ppid=,pgid=,command=']),
               'listener':self.command(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'])}
        if phase=='product-ready':
            if image is not None:
                event['image']=self.command(['/usr/sbin/lsof','-nP','-a','-p',str(pid),'-d','txt','-FfDin'])
                event['image_stat']=self.command(['/usr/bin/stat',*(['-L'] if follow else []),'-f','%d %i %z %Lp',str(image)])
                event['start_time']=self.command(['/bin/ps','-p',str(pid),'-o','lstart='])
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
    postgres_before=postgres_tool_hashes()
    rust_before=rust_tool_hashes()
    revision=os.environ.get('GITHUB_SHA')
    python=Path(os.path.abspath(args.python))
    need(python==REPO/'som-rs/runtime/acceptance-m3-venv/bin/python' and python.is_file(), 'frozen venv required')
    lifecycle.mkdir(parents=True,mode=0o700)
    initial=Path(os.environ['RUNNER_TEMP'])/'m6-python-provision-identity'
    need(initial.is_dir() and not initial.is_symlink() and all(p.is_file() and not p.is_symlink() and p.stat().st_size<=128*1024*1024 for p in initial.iterdir()),'provision Python identity missing')
    shutil.copytree(initial,lifecycle/'python-provision-identity')
    import record_rust006_http
    observer=TrackingRecorder(lifecycle)
    import parity_python_authority
    python_authority=parity_python_authority.capture(observer,lifecycle,python,HERE/'parity_python_probe.py',REPO,
        Path(os.environ['RUNNER_TEMP'])/'m6-provision/report.json')
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
    import parity_product_files
    files_snapshot=parity_product_files.Snapshots(lifecycle/'product-files')
    import parity_rust_build
    rust_copies=parity_rust_build.Copies(lifecycle/'product-binaries')
    import parity_python_runtime
    python_runtimes=parity_python_runtime.Runtimes(lifecycle/'python-runtimes')
    original_force=hosts.force_rebuild
    def observed_force(directory):
        before=None
        try:before=parity_rust_build.rebuild_inventory(directory);case,target_name=product.context()
        except Exception as exc:
            with observer.lock:observer.errors.append('RebuildBefore'+type(exc).__name__)
        original_force(directory)
        try:
            after=parity_rust_build.rebuild_inventory(directory)
            if before is None or target_name!='rust':raise ValueError('rebuild identity differs')
            with observer.lock:observer.events.append({'kind':'rust-rebuild-clean','case':case,'target':str(directory),'before':before,'after':after})
        except Exception as exc:
            with observer.lock:observer.errors.append('RebuildAfter'+type(exc).__name__)
    original_copy=hosts.shutil.copy2
    def observed_copy(source,destination,*a,**kw):
        result=original_copy(source,destination,*a,**kw)
        if Path(source)==target/'debug/som_app' and Path(destination).name=='som_app':
            try:
                case,target_name=product.context()
                if target_name!='rust':raise ValueError('rust copy context differs')
                row=rust_copies.capture(source,destination,case)
                with observer.lock:
                    for label,path in (('source',source),('target',destination)):
                        command=observer.command(['/usr/bin/shasum','-a','256',str(path)])
                        observer.events.append({'kind':'product-binary-hash','binary_id':row['id'],'label':label,**command})
                    observer.events.append({'kind':'product-binary','binary_id':row['id'],'case':case,'cwd':str(Path(destination).parent)})
            except Exception as exc:
                with observer.lock:observer.errors.append('ProductBinary'+type(exc).__name__)
        return result
    original_write=hosts.products.write
    def observed_write(root,files):
        original_write(root,files)
        try:
            case,target_name=product.context();row=files_snapshot.capture(Path(root),case,target_name)
            with observer.lock:observer.events.append({'kind':'product-files','snapshot_id':row['id'],'case':case,'target':target_name,'cwd':str(root)})
        except Exception as exc:
            with observer.lock:observer.errors.append('ProductFiles'+type(exc).__name__)
    original_serve=hosts.serve
    @contextlib.contextmanager
    def observed_serve(*a,**kw):
        original=original_serve(*a,**kw); owner=None; identity=None; pid=None; port=None;python_row=None
        try:
            try:
                pre_identity=product.context()
                launch=files_snapshot.capture_launch(a[1],pre_identity[0],pre_identity[1])
                with observer.lock:observer.events.append({'kind':'product-files-launch','snapshot_id':launch['id'],'case':pre_identity[0],'target':pre_identity[1],'cwd':str(a[1])})
                if pre_identity[1]=='python':
                    python_row=python_runtimes.capture(pre_identity[0],a[1],a[0])
                    with observer.lock:observer.events.append({'kind':'python-runtime','runtime_id':python_row['id'],'case':pre_identity[0],'cwd':str(a[1])})
            except Exception as exc:
                with observer.lock:observer.errors.append('PythonRuntime'+type(exc).__name__)
            with original as base:
                try:
                    identity=product.context(); process=original.gen.gi_frame.f_locals['process']; pid=process.pid
                    port=int(base.rsplit(':',1)[1])
                    with observer.lock:
                        if identity[1]=='rust':
                            observer.product_snapshot(pid,'product-ready',port,Path(process.args[0]))
                        else:
                            observer.product_snapshot(pid,'product-ready',port)
                            event=observer.events[-1]
                            event['image_ps']=observer.command(['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command='])
                            ps=(lifecycle/event['image_ps']['stdout']['file']).read_bytes().decode().strip().split(None,3)
                            need(event['image_ps']['exit_code']==0 and len(ps)==4 and ps[0]==str(pid),'Python image process missing')
                            image=python_runtimes.bind_image(python_row,ps[3])
                            event['image']=observer.command(['/usr/sbin/lsof','-nP','-a','-p',str(pid),'-d','txt','-FfDin'])
                            event['image_stat']=observer.command(['/usr/bin/stat','-L','-f','%d %i %z %Lp',str(image)])
                            event['start_time']=observer.command(['/bin/ps','-p',str(pid),'-o','lstart='])
                        start_event=observer.events.index(event) if identity[1]=='python' else len(observer.events)-1
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
             mock.patch.object(hosts.products,'write',observed_write), mock.patch.object(hosts.shutil,'copy2',observed_copy), mock.patch.object(hosts,'force_rebuild',observed_force), \
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
        try:postgres_after=postgres_tool_hashes()
        except (OSError,ValueError,KeyError):postgres_after=None;observer.errors.append('PostgresToolAfterUnverified')
        try:rust_after=rust_tool_hashes()
        except (OSError,ValueError,KeyError):rust_after=None;observer.errors.append('RustToolAfterUnverified')
        receipt={'version':'som.m6-mac-parity-capture.v1','exit_code':code,'events':observer.events,
                 'postgres_tools_before':postgres_before,'postgres_tools_after':postgres_after,
                 'product_file_snapshots':files_snapshot.rows,'rust_binary_copies':rust_copies.rows,
                 'python_runtimes':python_runtimes.rows,'python_authority':python_authority,
                 'rust_tools_before':rust_before,'rust_tools_after':rust_after,
                 'source_revision':revision,'source_before':before_sources,'source_after':source_hashes(),
                 'recording_errors':observer.errors,'product_acceptance_passed':False,
                 'cleanup':'requires-independent-raw-audit'}
        (lifecycle/'receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return code if not observer.errors else 1

if __name__=='__main__':raise SystemExit(main())
