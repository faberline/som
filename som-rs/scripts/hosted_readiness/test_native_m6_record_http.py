"""GitHub-only actual Core/HTTP handoff protocol. No live service runs.

Context/transfer/admission/target business sources and the wire transport are
explicit doubles. The real Core, exact proposed recorder source, original
SourceBytes/helpers, HTTPResponse object and physical Git source checks run.
No genuine Context, provider/private4,129 cases or product is qualified.
"""
from __future__ import annotations
import ast
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from types import ModuleType
import unittest
from unittest import mock
import urllib.request
import urllib.error
import urllib.response

if not (os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and sys.platform=='linux' and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub hosted Linux -I -S -B required')
HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
BASE='som-rs/scripts/hosted_readiness/'
SELF='som-rs/scripts/react_output_acceptance/record_http.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
PINS={SELF:'51e92bb83b769836f2305fe9dd23028f3bc43c1f2c099a761a4d0dbf0e4a4a57',
      CONTEXT:'7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
      BASE+'native_active_controls.py':'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
      BASE+'native_qa_authority.py':'024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
      BASE+'test_native_active_controls.py':'1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201'}

def checked(path):
    raw=(REPO/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINS[path]:raise RuntimeError('exact source dependency differs: '+path)
    return raw

for path in PINS:checked(path)
protocol=ModuleType('_som_native_m6_http_protocol')
protocol.__file__,protocol.__package__=str(HERE/'test_native_active_controls.py'),''
sys.modules[protocol.__name__]=protocol
exec(compile(checked(BASE+'test_native_active_controls.py'),protocol.__file__,'exec',dont_inherit=True),protocol.__dict__)
source_tree=ast.parse(checked(SELF))
TARGETS=next(ast.literal_eval(n.value) for n in source_tree.body if isinstance(n,ast.Assign)
             and any(isinstance(x,ast.Name) and x.id=='NATIVE_TARGETS' for x in n.targets))
context_text=checked(CONTEXT).decode();context_tree=ast.parse(context_text)
parts=[ast.get_source_segment(context_text,n) for n in context_tree.body
       if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in {'require','digest','read','relative','SourceBytes','Refusal'}]
CONTEXT_SOURCE='from __future__ import annotations\nimport hashlib,os,re,stat,subprocess\nfrom pathlib import Path\n'+'\n\n'.join(parts)+'''
_ACTIVE=None
class Context:
    def __init__(self,root,revision,path,controls,authority):
        self.repo,self.helper_revision,self.candidate_revision=root,revision,revision
        self.path,self.raw=path,path.read_bytes()
        self.qa_mode,self.qa_source_authority='native-qa-v1',authority
        self.sources=SourceBytes(root,revision)
        self.qa_import_controls=controls.borrow()
def context():return _ACTIVE
'''
DATA_SOURCE='PRIMARY=RuntimeError("actual original target failure")\nRUNS=[]\nRESPONSES=[]\n'
TARGET_SOURCE='''import json,os,sys,urllib.request
from pathlib import Path
def main():
    mode,trace,code,out=sys.argv[1:5]
    facts={'name':__name__,'argv':list(sys.argv),'pid':os.getpid(),'file':__file__}
    data.RUNS.append(facts);Path(trace).write_text(json.dumps(facts))
    if mode in {'wire','partial-failure','wrong-port'}:
        port=18919 if mode=='wrong-port' else 18978
        value=urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/v1/compile',data=b'original raw request',method='POST'))
        data.RESPONSES.append(value)
        if mode=='partial-failure':
            assert value.read(3)==b'raw'
            (Path(out)/'calls/000001.response.raw').write_bytes(b'keep existing file')
            raise data.PRIMARY
        assert value.read()==b'raw response bytes'
        value.close()
    if mode=='source-change':Path(__file__).write_bytes(b'# changed actual tracked source\\n')
    if mode=='context-change':Path(sys.argv[5]).write_bytes(b'{"changed":true}')
    if mode=='environment-change':os.environ['SOM_M6_FOREIGN_MARKER']='changed by target'
    return int(code)
if __name__=='__main__':raise SystemExit(main())
'''
CALLER_SOURCE='''import sys
from _som_native_active_controls_binding import controls
if sys.argv[1:2]==['direct']:
    module=controls.load_module('fixture.http.call')
    raise SystemExit(module.run_checker(sys.argv[2],sys.argv[3],sys.argv[4:],'native-qa-v1'))
raise SystemExit(controls.execute_entry('entry.m6.record-http',sys.argv[1:]))
'''

class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self,fixture):
        super().__init__(fixture);self.revision=fixture.revision
        self.admission.update(helper_revision=self.revision,candidate_revision=self.revision)

class HttpFixture(protocol.Fixture):
    def __init__(self,case):
        key='SOM_FULL_PRODUCT_CONTEXT';present=key in os.environ;previous=os.environ.get(key)
        def restore_environment():
            if present:os.environ[key]=previous
            else:os.environ.pop(key,None)
        case.addCleanup(restore_environment)
        with mock.patch.object(protocol,'ProtocolTransfer',GitTransfer):super().__init__(case,CALLER_SOURCE,entry=True)
        self.context=self.domain.load_known('runtime.context',self.domain.prepare('runtime.context'),None)
        self.local=self.root/'.local';self.local.mkdir()
        self.context_path=self.local/'context.json';self.context_path.write_bytes(b'{"explicit_context_double":true}')
        self.ctx=self.context.Context(self.root,self.revision,self.context_path,self.controls,self.transfer.admission)
        self.context._ACTIVE=self.ctx;os.environ[key]=str(self.context_path)
        case.addCleanup(self.ctx.qa_import_controls.close)
        self.data=self.domain.load_known('fixture.http.data',self.domain.prepare('fixture.http.data'),None)
        self.original_response_closes=[]
        case.addCleanup(lambda:[close() for close in self.original_response_closes])
        self.initial=self.seeds['_som_native_trusted_bootstrap_entry']['module']
        self.sequence=0;self.transports=[]

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='caller':
            row['bindings']['__som_native_active_controls__']={'kind':'active-controls'}
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,CONTEXT_SOURCE,module='_som_m6_http_fixture_context')
        data=BASE+'synthetic_m6_http_data.py'
        super().add_role('fixture.http.data',data,DATA_SOURCE,module='_som_m6_http_fixture_data')
        super().add_role('m6.record-http.owner',SELF,checked(SELF),module='_som_m6_record_http_owner',bindings={
            '__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
            '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'},
            '__som_native_context_read__':{'kind':'role-export','role':'runtime.context','attribute':'read'}})
        bindings={'__som_native_active_controls__':{'kind':'active-controls'},
          '__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
          '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
          '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
          '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'},
          '__som_native_context_read__':{'kind':'role-export','role':'runtime.context','attribute':'read'},
          '__som_native_owner_module__':{'kind':'role-module','role':'m6.record-http.owner'},
          '__som_native_Recorder__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'NativeRecorder'},
          '__som_native_Call__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'NativeCall'},
          '__som_native_RecordingError__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'RecordingError'},
          '__som_native_finish_call__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'NativeCall.finish'},
          '__som_native_read_regular__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'read_regular'},
          '__som_native_physical__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'physical'},
          '__som_native_write_new__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'write_new'},
          '__som_native_error__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'_native_error'},
          '__som_native_recording_operations__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'_native_recording_operations'},
          '__som_native_trace__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'_native_trace'},
          '__som_native_secondary__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'_native_secondary'},
          '__som_native_digest__':{'kind':'role-export','role':'m6.record-http.owner','attribute':'digest'}}
        super().add_role('entry.m6.record-http',SELF,checked(SELF),kind='entry',module='__main__',bindings=bindings,
             callers=[{'role':'caller','function':'<module>'}])
        super().add_role('fixture.http.call',SELF,checked(SELF),module='_som_m6_fixture_http_call',bindings=bindings,
             callers=[{'role':'caller','function':'<module>'}])
        for path2,role2 in TARGETS.items():
            super().add_role(role2,path2,TARGET_SOURCE,kind='entry',module='__main__',
                bindings={'data':{'kind':'role-module','role':'fixture.http.data'}},
                callers=[{'role':'entry.m6.record-http','function':'_native_execute_checker'},
                         {'role':'fixture.http.call','function':'_native_execute_checker'}])
        super().add_role('fixture.extra',BASE+'synthetic_extra_http.py','raise RuntimeError("must not run")\n')
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],env=env,
            check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        git('init','--quiet');git('add','--','.')
        git('-c','user.name=M6 HTTP protocol','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null',
            'commit','--quiet','-m','source protocol data only')
        self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def transport(self,handler,http_class,request,**kwargs):
        self.transports.append(request)
        class Socket:
            def makefile(self,_mode):return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 18\r\n\r\nraw response bytes')
        value=http.client.HTTPResponse(Socket());value.begin()
        value.code,value.msg,value.url=value.status,value.reason,request.full_url
        self.original_response_closes.append(value.close)
        return value

    def arguments(self,script=None,mode='plain',code=0,direct=False):
        self.sequence+=1;self.out=self.local/f'raw-{self.sequence}';self.trace=self.local/f'target-{self.sequence}.json'
        target=script or next(iter(TARGETS));arguments=[mode,str(self.trace),str(code),str(self.out)]
        if mode=='context-change':arguments.append(str(self.context_path))
        if direct:return ['direct',str(self.out),str(self.root/target),*arguments]
        return ['--out',str(self.out),'--qa-mode','native-qa-v1','--',str(self.root/target),*arguments]

    def invoke(self,**kwargs):
        return self.initial._trusted_run(self.controls,'caller',self.arguments(**kwargs))

    def manifest(self):return json.loads((self.out/'manifest.json').read_bytes())

class NativeHttpTests(unittest.TestCase):
    def test_all_original_five_targets_use_actual_fresh_entries_main_argv_and_exit(self):
        f=HttpFixture(self);main,argv=sys.modules['__main__'],sys.argv
        for index,(path,role) in enumerate(TARGETS.items()):
            expected=7 if index==0 else 0
            self.assertEqual(f.invoke(script=path,code=expected),expected)
            manifest=f.manifest();proof=manifest['native_source_execution'];run=f.data.RUNS[-1]
            self.assertEqual(manifest['script']['path'],path);self.assertEqual(proof['role'],role)
            self.assertEqual(run['name'],'__main__');self.assertEqual(run['argv'],manifest['argv'])
            self.assertEqual(run['pid'],proof['current_pid']);self.assertEqual(run['file'],str(f.root/path))
            self.assertTrue(proof['execute_entry_returned']);self.assertEqual(proof['returned_exit_code'],expected)
            self.assertTrue(proof['source_postcheck']);self.assertFalse(proof['product_acceptance_passed'])
            self.assertNotIn('actual_argv',proof);self.assertIn('requested_checker_argv',proof)
            self.assertIn('pending separately reviewed',proof['nested_execution_list_join'])
            self.assertNotIn(role,f.domain.records);self.assertNotIn('entry.m6.record-http',f.domain.records)
            self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv)
            self.assertEqual(f.domain.entry_depth,0);self.assertEqual(f.domain.suspended_mains,[])
        self.assertEqual(len(f.data.RUNS),5);self.assertIsNone(f.domain.group)
        self.assertFalse(f.ctx.qa_import_controls._closed)

    def test_native_ports_and_actual_raw_response_survive_without_network(self):
        f=HttpFixture(self);original=urllib.request.AbstractHTTPHandler.do_open
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            self.assertEqual(f.invoke(mode='wire'),0)
            self.assertEqual(urllib.request.AbstractHTTPHandler.do_open,f.transport)
        manifest=f.manifest();call=manifest['calls'][0]
        self.assertEqual(manifest['ports'],[18978,18979]);self.assertEqual(call['status'],200)
        self.assertTrue(call['complete']);self.assertEqual(call['request_sha256'],hashlib.sha256(b'original raw request').hexdigest())
        self.assertEqual((f.out/call['response_file']).read_bytes(),b'raw response bytes')
        self.assertIs(urllib.request.AbstractHTTPHandler.do_open,original)
        self.assertEqual(len(f.transports),1)

    def test_legacy_port_cannot_enter_native_transport(self):
        f=HttpFixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            self.assertEqual(f.invoke(mode='wrong-port'),1)
        self.assertEqual(f.transports,[]);self.assertEqual(f.manifest()['recording_errors'],['request_refused','recording_failed'])
        self.assertEqual(f.manifest()['native_source_execution']['first_error']['type'],'RecordingError')

    def test_extra_admitted_source_refuses_before_output_and_target_body(self):
        f=HttpFixture(self)
        with self.assertRaisesRegex(ValueError,'outside exact original five'):
            f.invoke(script=BASE+'synthetic_extra_http.py',direct=True)
        self.assertFalse(f.out.exists());self.assertEqual(f.data.RUNS,[])
        self.assertEqual(f.domain.entry_depth,0)

    def test_foreign_context_and_fake_handle_never_run_attribute_hooks_or_legacy(self):
        f=HttpFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign hook')
        f.context._ACTIVE=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'actual active native Context required'):f.invoke(direct=True)
        finally:f.context._ACTIVE=f.ctx
        self.assertFalse(f.out.exists());self.assertEqual(calls,[])
        fake=ModuleType('_som_http_unbound_fake');fake.__file__=str(REPO/SELF)
        fake.__dict__['__som_native_active_controls__']=Foreign()
        exec(compile(checked(SELF),fake.__file__,'exec',dont_inherit=True),fake.__dict__)
        with self.assertRaises(ModuleNotFoundError):fake.run_checker(f.out,next(iter(TARGETS)),[],'native-qa-v1')
        self.assertEqual(calls,[]);self.assertFalse(f.out.exists());self.assertEqual(f.data.RUNS,[])
        fake.__dict__.pop('__som_native_active_controls__')
        core=sys.modules.pop('_som_native_active_controls');name='_som_m6_checked_router';holder=Foreign()
        self.assertNotIn(name,sys.modules);sys.modules[name]=holder
        try:
            with self.assertRaisesRegex(ValueError,'requires actual Core handoff'):
                fake.run_checker(f.out,next(iter(TARGETS)),[],'native-qa-v1')
            self.assertIs(sys.modules[name],holder);self.assertEqual(calls,[]);self.assertFalse(f.out.exists())
        finally:
            if sys.modules.get(name) is holder:del sys.modules[name]
            sys.modules['_som_native_active_controls']=core

    def test_removed_binding_with_and_without_core_key_keeps_actual_guard(self):
        f=HttpFixture(self);module=f.domain.load_known('fixture.http.call',f.domain.prepare('fixture.http.call'),None)
        for remove_key in (False,True):
            controls=module.__dict__.pop('__som_native_active_controls__');core=None;before=list(sys.path)
            try:
                if remove_key:core=sys.modules.pop('_som_native_active_controls')
                with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced'):
                    module.run_checker(f.local/'must-not-exist',next(iter(TARGETS)),[],'native-qa-v1')
                self.assertEqual(sys.path,before);self.assertFalse((f.local/'must-not-exist').exists())
                self.assertEqual(f.data.RUNS,[])
            finally:
                module.__dict__['__som_native_active_controls__']=controls
                if core is not None:sys.modules['_som_native_active_controls']=core

    def test_original_primary_identity_and_trace_survive_real_finalize_write_failure(self):
        f=HttpFixture(self);main,argv=sys.modules['__main__'],sys.argv
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='partial-failure',direct=True)
            except RuntimeError as error:
                self.assertIs(error,f.data.PRIMARY)
                self.assertIn('main',[row.name for row in traceback.extract_tb(error.__traceback__)])
                self.assertTrue(any(isinstance(item,FileExistsError) for item in error.__som_native_http_secondary__))
            else:self.fail('actual original target failure must remain primary')
        manifest=f.manifest();proof=manifest['native_source_execution']
        self.assertEqual(proof['first_error']['type'],'RuntimeError');self.assertFalse(proof['execute_entry_returned'])
        self.assertFalse(proof['source_postcheck']);self.assertEqual(manifest['exit_code'],1)
        self.assertTrue(any(row['phase']=='finish-http-call' for row in proof['secondary_errors']))
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'keep existing file')
        self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv);self.assertEqual(f.domain.entry_depth,0)
        self.assertFalse(f.ctx.qa_import_controls._closed)

    def test_actual_tracked_target_and_original_context_bytes_are_postchecked(self):
        for mode in ('source-change','context-change'):
            with self.subTest(mode=mode):
                f=HttpFixture(self);target=f.root/next(iter(TARGETS));source=target.read_bytes();context=f.context_path.read_bytes()
                try:
                    with self.assertRaises(ValueError):f.invoke(mode=mode,direct=True)
                    proof=f.manifest()['native_source_execution']
                    self.assertFalse(proof['source_postcheck']);self.assertTrue(proof['execute_entry_returned'])
                    self.assertEqual(f.domain.entry_depth,0)
                finally:
                    target.write_bytes(source);f.context_path.write_bytes(context)
                    f.ctx.qa_import_controls.close();f.stack.close()

    def test_actual_native_environment_is_preserved_and_changed_target_state_refuses(self):
        f=HttpFixture(self);before=dict(os.environ)
        with self.assertRaisesRegex(ValueError,'changed wrapper process state'):
            f.invoke(mode='environment-change',direct=True)
        self.assertEqual(dict(os.environ),before)
        proof=f.manifest()['native_source_execution'];self.assertEqual(proof['first_error']['phase'],'process-state')
        self.assertFalse(proof['source_postcheck']);self.assertEqual(f.domain.entry_depth,0)


FAILURE_TARGET_SOURCE = '''import json,os,sys,urllib.request
from pathlib import Path
def main():
    mode,trace,code,out=sys.argv[1:5]
    data.RUNS.append({'name':__name__,'argv':list(sys.argv),'pid':os.getpid(),'file':__file__})
    Path(trace).write_text(json.dumps(data.RUNS[-1]))
    def opened(number):
        value=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18978/v1/compile',data=('request '+str(number)).encode(),method='POST'))
        data.NEW_RESPONSES.append(value)
        return value
    if mode=='caught-transport':
        try:opened(1)
        except RuntimeError as error:data.CAUGHT=error
        return 0
    if mode=='multi-pending':
        for number in (1,2):assert opened(number).read(3)==b'raw'
        (Path(out)/'calls/000001.response.raw').write_bytes(b'keep existing file')
        raise data.PRIMARY
    value=opened(1)
    if mode=='caught-read':
        try:value.read()
        except RuntimeError as error:
            data.CAUGHT=error
            value.close()
            data.CLOSED=True
    elif mode=='body-guard':
        with value:
            assert value.read(3)==b'raw'
            raise data.PRIMARY
    elif mode=='close-fail':
        assert value.read(3)==b'raw'
        value.close()
    elif mode=='read-and-close':
        with value:value.read()
    else:
        value.read()
        value.close()
    return int(code)
if __name__=='__main__':raise SystemExit(main())
'''

class FailureFixture(HttpFixture):
    def __init__(self, case):
        super().__init__(case)
        self.data.NEW_RESPONSES=[]
        self.mode='normal'
        self.close_attempts=0
        self.close_secondary=RuntimeError('actual response close secondary')
        self.base_closes=[]
        self.foreign_hooks=[]
        fixture=self
        class ForeignContext:
            def __getattribute__(self,name):
                fixture.foreign_hooks.append(name)
                raise AssertionError('foreign Context hook')
        self.foreign_context=ForeignContext()
        def cleanup_responses():
            for close in self.base_closes:close()
        case.addCleanup(cleanup_responses)

    def add_role(self, role, path, raw, **kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='m6.api.producer':
            for target in TARGETS:
                value=FAILURE_TARGET_SOURCE.encode()
                self.raw[target]=value
                (self.root/target).write_bytes(value)
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
            def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],env=env,
                check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            git('add','--','.')
            git('-c','user.name=M6 HTTP negative protocol','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null',
                'commit','--quiet','-m','additional target data doubles only')
            self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def collision(self):
        (self.out/'calls/000001.response.raw').write_bytes(b'keep existing file')

    def transport(self, handler, http_class, request, **kwargs):
        if self.mode=='transport':
            self.transports.append(request);self.collision()
            raise self.data.PRIMARY
        if self.mode=='attach':
            self.transports.append(request);self.collision();fixture=self
            class BrokenResponse:
                @property
                def status(self):raise fixture.data.PRIMARY
                def close(self):
                    fixture.close_attempts+=1
                    raise fixture.close_secondary
            return BrokenResponse()
        if self.mode in {'close-owner-change','body-owner-change'}:
            self.transports.append(request);fixture=self
            class Socket:
                def makefile(self,_mode):return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 18\r\n\r\nraw response bytes')
            class ChangedOnExit(http.client.HTTPResponse):
                def __exit__(self,*exception):
                    fixture.context._ACTIVE=fixture.foreign_context
                    return super().__exit__(*exception)
                def close(self):
                    fixture.close_attempts+=1
                    return super().close()
            value=ChangedOnExit(Socket());value.begin()
            value.code,value.msg,value.url=value.status,value.reason,request.full_url
        else:
            value=super().transport(handler,http_class,request,**kwargs)
        self.base_closes.append(value.close)
        if self.mode in {'read','read-and-close','close-owner-change','read-owner-change','caught-read'}:
            def failed_read(*args,**kwargs):
                if self.mode in {'read','read-and-close'}:self.collision()
                if self.mode=='read-owner-change':self.context._ACTIVE=self.foreign_context
                raise self.data.PRIMARY
            value.read=failed_read
        if self.mode in {'close','read-and-close'}:
            def failed_close():
                self.close_attempts+=1;self.collision()
                raise self.data.PRIMARY if self.mode=='close' else self.close_secondary
            value.close=failed_close
        if self.mode=='raw-only':self.collision()
        return value

class NativeHttpFirstErrorTests(unittest.TestCase):
    def assert_actual_primary(self, f, mode, *, secondary_type=FileExistsError):
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode=mode,direct=True)
            except RuntimeError as error:
                self.assertIs(error,f.data.PRIMARY)
                frames=[row.name for row in traceback.extract_tb(error.__traceback__)]
                self.assertTrue(any(name in frames for name in ('transport','failed_read','failed_close','status','main')))
                self.assertTrue(any(isinstance(item,secondary_type) for item in error.__som_native_http_secondary__))
                return error
            else:self.fail('the actual first callback error must remain primary')

    def test_transport_error_stays_primary_when_its_raw_finalizer_also_fails(self):
        f=FailureFixture(self);f.mode='transport';main,argv=sys.modules['__main__'],sys.argv
        error=self.assert_actual_primary(f,'transport-fail')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['type'],'RuntimeError')
        self.assertIn('http-callback',proof['first_error']['phase'])
        self.assertTrue(any(row['type']=='FileExistsError' for row in proof['secondary_errors']))
        self.assertEqual((f.out/'calls/000001.request.raw').read_bytes(),b'request 1')
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'keep existing file')
        self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv);self.assertEqual(f.domain.entry_depth,0)
        self.assertIs(error,f.data.PRIMARY)

    def test_read_error_keeps_original_identity_trace_and_finalize_secondary(self):
        f=FailureFixture(self);f.mode='read'
        error=self.assert_actual_primary(f,'read-fail')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['phase'],'http-callback:read-finalize')
        self.assertFalse(proof['raw_finalizations'][0]['saved'])
        self.assertIs(error,f.data.PRIMARY);self.assertFalse(proof['source_postcheck'])

    def test_close_error_stays_primary_when_final_raw_write_fails(self):
        f=FailureFixture(self);f.mode='close'
        self.assert_actual_primary(f,'close-fail')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(f.close_attempts,1)
        self.assertEqual(proof['first_error']['phase'],'http-callback:close-finalize')
        self.assertFalse(proof['execute_entry_returned'])

    def test_attach_error_survives_finalize_and_response_close_failures(self):
        f=FailureFixture(self);f.mode='attach'
        error=self.assert_actual_primary(f,'attach-fail')
        self.assertEqual(f.close_attempts,1)
        self.assertTrue(any(item is f.close_secondary for item in error.__som_native_http_secondary__))
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['phase'],'http-callback:attach-finalize')
        self.assertTrue(any('attach-response-close' in row['phase'] for row in proof['secondary_errors']))

    def test_active_read_error_survives_real_response_context_close_failure(self):
        f=FailureFixture(self);f.mode='read-and-close'
        error=self.assert_actual_primary(f,'read-and-close')
        self.assertEqual(f.close_attempts,1)
        self.assertTrue(any(item is f.close_secondary for item in error.__som_native_http_secondary__))
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['phase'],'http-callback:read-finalize')
        self.assertTrue(any('response-close' in row['phase'] for row in proof['secondary_errors']))

    def test_second_pending_call_is_saved_after_first_finalizer_write_failure(self):
        f=FailureFixture(self);main,argv=sys.modules['__main__'],sys.argv
        self.assert_actual_primary(f,'multi-pending')
        manifest=f.manifest();proof=manifest['native_source_execution']
        self.assertEqual(len(manifest['calls']),2)
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'keep existing file')
        second=manifest['calls'][1]
        self.assertEqual((f.out/second['response_file']).read_bytes(),b'raw')
        self.assertEqual(json.loads((f.out/'calls/000002.json').read_bytes()),second)
        self.assertEqual([(row['call_id'],row['saved']) for row in proof['raw_finalizations']],[(1,False),(2,True)])
        self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv);self.assertEqual(f.domain.entry_depth,0)
        self.assertFalse(proof['source_postcheck']);self.assertFalse(f.ctx.qa_import_controls._closed)

    def test_caught_transport_error_cannot_hide_failed_raw_evidence(self):
        f=FailureFixture(self);f.mode='transport'
        self.assert_actual_primary(f,'caught-transport')
        self.assertIs(f.data.CAUGHT,f.data.PRIMARY)
        proof=f.manifest()['native_source_execution']
        self.assertTrue(proof['execute_entry_returned']);self.assertEqual(proof['returned_exit_code'],0)
        self.assertEqual(f.manifest()['exit_code'],1);self.assertFalse(proof['source_postcheck'])

    def test_successful_read_with_failed_save_has_the_actual_save_error_primary(self):
        f=FailureFixture(self);f.mode='raw-only'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            with self.assertRaises(FileExistsError):f.invoke(mode='wire',direct=True)
        manifest=f.manifest();proof=manifest['native_source_execution']
        self.assertEqual(proof['first_error']['type'],'FileExistsError')
        self.assertTrue(manifest['calls'][0]['complete'])
        self.assertEqual(f.data.NEW_RESPONSES[0].length,0)
        self.assertFalse(proof['raw_finalizations'][0]['saved'])
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'keep existing file')
        self.assertFalse(proof['source_postcheck'])

    def test_saved_read_error_survives_owner_change_before_response_close(self):
        f=FailureFixture(self);f.mode='close-owner-change'
        try:
            error=self.assert_actual_primary(f,'read-and-close',secondary_type=ValueError)
            proof=f.manifest()['native_source_execution']
            self.assertIs(error,f.data.PRIMARY)
            self.assertEqual(proof['first_error']['phase'],'http-callback:close-source-guard')
            self.assertTrue(proof['raw_finalizations'][0]['saved'])
            self.assertEqual(f.close_attempts,0);self.assertEqual(f.foreign_hooks,[])
            self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'')
            self.assertFalse(proof['source_postcheck'])
        finally:f.context._ACTIVE=f.ctx

    def test_read_error_survives_owner_change_before_its_raw_finalizer(self):
        f=FailureFixture(self);f.mode='read-owner-change'
        try:
            self.assert_actual_primary(f,'read-and-close',secondary_type=ValueError)
            proof=f.manifest()['native_source_execution']
            self.assertEqual(proof['first_error']['phase'],'http-callback:read-finalize')
            self.assertFalse(proof['raw_finalizations'][0]['attempted'])
            self.assertFalse(proof['raw_finalizations'][0]['saved'])
            self.assertFalse((f.out/'calls/000001.response.raw').exists())
            self.assertFalse((f.out/'calls/000001.json').exists())
            self.assertEqual(f.close_attempts,0);self.assertEqual(f.foreign_hooks,[])
            self.assertFalse(proof['source_postcheck'])
        finally:f.context._ACTIVE=f.ctx

    def test_target_body_error_stays_primary_when_close_owner_guard_refuses(self):
        f=FailureFixture(self);f.mode='body-owner-change'
        try:
            self.assert_actual_primary(f,'body-guard',secondary_type=ValueError)
            proof=f.manifest()['native_source_execution']
            self.assertEqual(proof['first_error']['phase'],'http-callback:close-source-guard')
            self.assertEqual(f.close_attempts,0);self.assertEqual(f.foreign_hooks,[])
            self.assertFalse((f.out/'calls/000001.response.raw').exists())
            self.assertFalse(proof['source_postcheck'])
        finally:f.context._ACTIVE=f.ctx

    def test_caught_read_error_allows_successful_close_to_return_normally(self):
        f=FailureFixture(self);f.mode='caught-read'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            self.assertEqual(f.invoke(mode='caught-read',direct=True),0)
        manifest=f.manifest();proof=manifest['native_source_execution']
        self.assertIs(f.data.CAUGHT,f.data.PRIMARY);self.assertTrue(f.data.CLOSED)
        self.assertEqual(manifest['recording_errors'],[])
        self.assertTrue(proof['source_postcheck']);self.assertTrue(proof['execute_entry_returned'])
        self.assertEqual(proof['returned_exit_code'],0)
        self.assertEqual(proof['raw_finalizations'],[{'call_id':1,'attempted':True,'saved':True}])
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'')
        self.assertEqual(json.loads((f.out/'calls/000001.json').read_bytes()),manifest['calls'][0])


V3_TARGET_SOURCE = FAILURE_TARGET_SOURCE.replace('import json,os,sys,urllib.request',
    'import json,os,sys,urllib.request,urllib.error,concurrent.futures').replace(
    "    value=opened(1)\n", '''    if mode=='nested-calls':
        with opened(1) as first:
            assert first.read(3)==b'raw'
            with opened(2) as second:assert second.read(3)==b'raw'
        return 0
    if mode=='parallel-calls':
        def worker(number):
            with opened(number) as value:assert value.read()==b'raw response bytes'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(worker,(1,2)))
        return 0
    if mode=='http-error-body':
        try:opened(1)
        except urllib.error.HTTPError as error:
            data.HTTP_ERROR=error
            with error:
                assert error.read(3)==b'raw'
                raise data.PRIMARY
        raise AssertionError('actual HTTPError was required')
    value=opened(1)
    if mode=='same-context-swap':
        data.CONTEXT_MODULE._ACTIVE=data.REPLACEMENT
        try:value.read()
        finally:data.CONTEXT_MODULE._ACTIVE=data.ORIGINAL
        raise AssertionError('replacement Context must refuse')
    if mode=='ambient-close-fail':
        try:raise data.HANDLED
        except LookupError:value.close()
        raise AssertionError('actual close must fail')
    if mode=='with-body-close-fail':
        with value:
            assert value.read(3)==b'raw'
            raise data.PRIMARY
    if mode=='foreign-exit':
        data.KIND.__exit__=data.FOREIGN_EXIT
        raise data.PRIMARY
    if mode=='foreign-close-code':
        data.CLOSE.__code__=data.FOREIGN_EXIT.__code__
        raise data.PRIMARY
    if mode=='exit-reentry':
        with value:assert value.read(3)==b'raw'
        return 0
''')

class V3Fixture(FailureFixture):
    def __init__(self, case):
        super().__init__(case)
        self.read_attempts=0;self.shadow=None;self.recorder_seen=None
        self.data.HANDLED=LookupError('already handled and unrelated')
        self.data.CONTEXT_MODULE,self.data.ORIGINAL=self.context,self.ctx
        self.underlying=[]
        self.data.FOREIGN_EXIT=self.foreign_exit
        self.data.KIND=http.client.HTTPResponse

    def foreign_exit(self,*args,**kwargs):
        self.foreign_hooks.append('foreign exit')
        raise AssertionError('foreign exit/code must not run')

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='m6.api.producer':
            for target in TARGETS:
                self.raw[target]=V3_TARGET_SOURCE.encode();(self.root/target).write_bytes(self.raw[target])
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
            def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],env=env,
                check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            git('add','--','.')
            git('-c','user.name=M6 HTTP exit protocol','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null',
                'commit','--quiet','-m','explicit exit and owner negative target data')
            self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def actual_recorder(self):
        hook=urllib.request.AbstractHTTPHandler.do_open
        self.case.assertEqual(hook.__qualname__,'NativeRecorder.__init__.<locals>.enter.<locals>.recording_open')
        opened=next(cell.cell_contents for cell in hook.__closure__ if callable(cell.cell_contents)
                    and getattr(cell.cell_contents,'__qualname__','')=='NativeRecorder.__init__.<locals>.opened')
        recorder=next(cell.cell_contents for name,cell in zip(opened.__code__.co_freevars,opened.__closure__) if name=='self')
        self.case.assertEqual(type(recorder).__name__,'NativeRecorder')
        return recorder

    def transport(self,handler,http_class,request,**kwargs):
        if self.mode not in {'shadow-read','same-context','http-error-close','body-close','reentry-close'}:
            return super().transport(handler,http_class,request,**kwargs)
        self.recorder_seen=self.actual_recorder()
        value=HttpFixture.transport(self,handler,http_class,request,**kwargs)
        self.base_closes.append(value.close);self.underlying.append(value)
        original_read=value.read
        def counted_read(*args,**kwargs):
            self.read_attempts+=1
            if self.mode=='shadow-read':
                class Foreign:
                    def __getattribute__(_self,name):
                        self.foreign_hooks.append(name)
                        raise AssertionError('foreign sink/container hook')
                    def __call__(_self,*args,**kwargs):
                        self.foreign_hooks.append('call')
                        raise AssertionError('foreign sink called')
                foreign=Foreign()
                if self.shadow=='call-recorder':
                    call=self.recorder_seen.calls[0]
                    call.recorder=foreign
                else:setattr(self.recorder_seen,self.shadow,foreign)
                raise self.data.PRIMARY
            return original_read(*args,**kwargs)
        value.read=counted_read
        if self.mode in {'http-error-close','body-close'}:
            if self.mode=='http-error-close':value.status=value.code=400
            def failed_close():
                self.close_attempts+=1;self.collision()
                raise self.close_secondary
            value.close=failed_close
        if self.mode=='reentry-close':
            def reenter_close():return http.client.HTTPResponse.__exit__(value,None,None,None)
            value.close=reenter_close
        return value


class NativeHttpV3Tests(unittest.TestCase):
    def test_error_metadata_never_calls_reason_errno_string_or_traceback_properties(self):
        f=V3Fixture(self);f.mode='transport';hooks=[]
        class Error(RuntimeError):
            @property
            def reason(self):hooks.append('reason');raise AssertionError('reason hook')
            @property
            def errno(self):hooks.append('errno');raise AssertionError('errno hook')
            @property
            def __traceback__(self):hooks.append('traceback');raise AssertionError('trace hook')
            def __str__(self):hooks.append('str');raise AssertionError('string hook')
        f.data.PRIMARY=Error('original actual error')
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='transport-fail',direct=True)
            except Error as error:
                self.assertIs(error,f.data.PRIMARY)
                trace=BaseException.__dict__['__traceback__'].__get__(error,BaseException)
                self.assertIn('transport',[row.name for row in traceback.extract_tb(trace)])
            else:self.fail('original actual callback error required')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['type'],'Error');self.assertIsNone(proof['first_error']['errno'])
        self.assertTrue(any(row['type']=='FileExistsError' for row in proof['secondary_errors']))
        self.assertEqual(hooks,[]);self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
        self.assertFalse(proof['source_postcheck'])

    def test_same_class_matching_context_cannot_replace_initial_owner_for_a_read(self):
        f=V3Fixture(self);f.mode='same-context'
        replacement=f.context.Context(f.root,f.revision,f.context_path,f.controls,f.transfer.admission)
        self.addCleanup(replacement.qa_import_controls.close);f.data.REPLACEMENT=replacement
        # Isolate the Context pointer. Other original ownership pointers match.
        replacement.sources=f.ctx.sources
        replacement.qa_import_controls=f.ctx.qa_import_controls
        replacement.qa_source_authority=f.ctx.qa_source_authority
        replacement.path,replacement.raw=f.ctx.path,f.ctx.raw
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            with self.assertRaisesRegex(ValueError,'original HTTP owner pointer changed'):
                f.invoke(mode='same-context-swap',direct=True)
        self.assertIs(f.context._ACTIVE,f.ctx);self.assertEqual(f.read_attempts,0)
        proof=f.manifest()['native_source_execution']
        self.assertFalse(proof['source_postcheck']);self.assertFalse(proof['raw_finalizations'][0]['attempted'])
        self.assertEqual((f.out/'calls/000001.request.raw').read_bytes(),b'request 1')
        self.assertFalse((f.out/'calls/000001.response.raw').exists())

    def test_shadow_recorder_call_sinks_and_containers_never_run_foreign_hooks(self):
        for field in ('_native_failure','_native_finalization','native_failures','native_finalizations','errors','call-recorder'):
            with self.subTest(field=field):
                f=V3Fixture(self);f.mode='shadow-read';f.shadow=field
                with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                    try:f.invoke(mode='read-fail',direct=True)
                    except RuntimeError as error:self.assertIs(error,f.data.PRIMARY)
                    else:self.fail('actual first read error required')
                self.assertEqual(f.foreign_hooks,[])
                proof=f.manifest()['native_source_execution']
                self.assertEqual(proof['first_error']['type'],'RuntimeError')
                self.assertFalse(proof['raw_finalizations'][0]['attempted'])
                self.assertFalse((f.out/'calls/000001.response.raw').exists())
                self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))

    def test_actual_httpresponse_and_httperror_object_types_survive_explicit_exit_scope(self):
        f=V3Fixture(self);f.mode='http-error-close'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='http-error-body',direct=True)
            except RuntimeError as error:
                self.assertIs(error,f.data.PRIMARY)
                self.assertTrue(any(value is f.close_secondary for value in error.__som_native_http_secondary__))
            else:self.fail('actual with-body error required')
        self.assertIs(type(f.underlying[0]),http.client.HTTPResponse)
        self.assertIs(type(f.data.HTTP_ERROR),urllib.error.HTTPError)
        self.assertIs(f.data.HTTP_ERROR.fp,f.underlying[0]);self.assertIs(f.data.HTTP_ERROR.file,f.underlying[0])
        self.assertEqual(f.close_attempts,1)
        proof=f.manifest()['native_source_execution'];self.assertFalse(proof['source_postcheck'])
        self.assertTrue(all(row['installed'] and row['restored'] for row in proof['response_exit_scope']))

    def test_unrelated_handled_exception_does_not_become_close_error_primary(self):
        f=V3Fixture(self);f.mode='close'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='ambient-close-fail',direct=True)
            except RuntimeError as error:self.assertIs(error,f.data.PRIMARY);self.assertIsNot(error,f.data.HANDLED)
            else:self.fail('actual close error required')
        self.assertEqual(f.close_attempts,1)
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['type'],'RuntimeError')
        self.assertEqual(proof['first_error']['phase'],'http-callback:close-finalize')
        self.assertTrue(any(row['type']=='FileExistsError' for row in proof['secondary_errors']))

    def test_true_with_body_error_remains_primary_over_close_and_raw_failures(self):
        f=V3Fixture(self);f.mode='body-close'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='with-body-close-fail',direct=True)
            except RuntimeError as error:
                self.assertIs(error,f.data.PRIMARY)
                self.assertTrue(any(value is f.close_secondary for value in error.__som_native_http_secondary__))
            else:self.fail('actual with-body error required')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(proof['first_error']['phase'],'http-callback:response-close')
        self.assertEqual(f.close_attempts,1);self.assertFalse(proof['raw_finalizations'][0]['saved'])
        self.assertTrue(any(row['type']=='FileExistsError' for row in proof['secondary_errors']))

    def test_different_nested_and_worker_calls_keep_separate_explicit_exit_tokens(self):
        for mode in ('nested-calls','parallel-calls'):
            with self.subTest(mode=mode):
                f=V3Fixture(self)
                with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                    self.assertEqual(f.invoke(mode=mode,direct=True),0)
                manifest=f.manifest();proof=manifest['native_source_execution']
                self.assertEqual(len(manifest['calls']),2);self.assertEqual(manifest['recording_errors'],[])
                self.assertTrue(all(row['saved'] for row in proof['raw_finalizations']))
                self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
                self.assertTrue(proof['source_postcheck']);self.assertEqual(f.domain.entry_depth,0)

    def test_late_foreign_exit_field_is_preserved_and_other_owned_field_is_restored(self):
        for kind in (http.client.HTTPResponse,urllib.response.addbase):
            with self.subTest(kind=kind):
                f=V3Fixture(self);f.data.KIND=kind
                namespace=vars(kind);present='__exit__' in namespace;original=namespace.get('__exit__')
                try:
                    with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                        with self.assertRaises(RuntimeError) as raised:f.invoke(mode='foreign-exit',direct=True)
                    self.assertIs(raised.exception,f.data.PRIMARY)
                    self.assertIs(vars(kind).get('__exit__'),f.data.FOREIGN_EXIT)
                    self.assertEqual(f.foreign_hooks,[])
                    proof=f.manifest()['native_source_execution'];self.assertFalse(proof['source_postcheck'])
                    self.assertEqual(sum(row['foreign_field_preserved'] for row in proof['response_exit_scope']),1)
                    self.assertEqual(sum(row['restored'] for row in proof['response_exit_scope']),1)
                finally:
                    if present:kind.__exit__=original
                    else:del kind.__exit__

    def test_changed_original_stdlib_method_code_refuses_without_running_foreign_body(self):
        f=V3Fixture(self);method=http.client.HTTPResponse.close;before=method.__code__
        f.data.CLOSE=method
        # The fixture owns this temporary mutation. The recorder owns only exit fields.
        class ForeignClose:
            def close(self):
                __class__
                raise AssertionError('foreign method body must not run')
        f.data.FOREIGN_EXIT=ForeignClose.close
        self.assertEqual(method.__code__.co_freevars,f.data.FOREIGN_EXIT.__code__.co_freevars)
        try:
            with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                with self.assertRaises(RuntimeError) as raised:f.invoke(mode='foreign-close-code',direct=True)
            self.assertIs(raised.exception,f.data.PRIMARY)
            self.assertFalse(f.manifest()['native_source_execution']['source_postcheck'])
            self.assertIs(method.__code__,f.data.FOREIGN_EXIT.__code__)
            self.assertEqual(f.foreign_hooks,[])
        finally:method.__code__=before

    def test_same_call_exit_reentry_fails_after_actual_target_entry_and_restores_fields(self):
        f=V3Fixture(self);f.mode='reentry-close'
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            with self.assertRaisesRegex(ValueError,'owned response exit reentry'):
                f.invoke(mode='exit-reentry',direct=True)
        proof=f.manifest()['native_source_execution']
        self.assertTrue(proof['execute_entry_attempted']);self.assertEqual(len(f.data.RUNS),1)
        self.assertFalse(proof['source_postcheck'])
        self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))


if __name__=='__main__':unittest.main()
