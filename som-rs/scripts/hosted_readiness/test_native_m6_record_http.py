"""GitHub-only actual Core/HTTP handoff protocol. No live service runs.

Context/transfer/admission/target business sources and the wire transport are
explicit doubles. The real Core, exact proposed recorder source, original
SourceBytes/helpers, HTTPResponse object and physical Git source checks run.
No genuine Context, provider/private4,129 cases or product is qualified.
The early provider below is synthetic control-only fixed-role wiring.
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
import threading
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
PINS={SELF:'ed0dd5841a47cdab12453b64187a3f45d432d481c2acc055ae2ff6f24e844588',
      CONTEXT:'7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
      BASE+'native_active_controls.py':'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
      BASE+'native_qa_authority.py':'024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
      BASE+'test_native_active_controls.py':'1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201'}

def checked(path):
    raw=(REPO/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINS[path]:raise RuntimeError('exact source dependency differs: '+path)
    return raw

# This SELF pin binds the final consumer candidate bytes.
_HTTP_PROVIDER_PATH=BASE+'http_native_primitives.py'
PINS[_HTTP_PROVIDER_PATH]='562e5ded76e44ba3b14a54428c5d53d09026adc99a6b2ba75b65164178798924'
_HTTP_PROVIDER_CONTROL_ONLY=True
_HTTP_PROVIDER_ROLES={'http.native-primitives.bound': {'bindings': {'FRAME_GET': {'attribute': 'FRAME_GET',
                                                             'kind': 'role-export',
                                                             'role': 'http.native-primitives.source'},
                                               'GEN_CLOSE': {'attribute': 'GEN_CLOSE',
                                                             'kind': 'role-export',
                                                             'role': 'http.native-primitives.source'},
                                               'GEN_SEND': {'attribute': 'GEN_SEND',
                                                            'kind': 'role-export',
                                                            'role': 'http.native-primitives.source'},
                                               'OBJECT_NEW': {'attribute': 'OBJECT_NEW',
                                                              'kind': 'role-export',
                                                              'role': 'http.native-primitives.source'},
                                               'RLOCK_ACQUIRE': {'attribute': 'RLOCK_ACQUIRE',
                                                                 'kind': 'role-export',
                                                                 'role': 'http.native-primitives.source'},
                                               'RLOCK_NEW': {'attribute': 'RLOCK_NEW',
                                                             'kind': 'role-export',
                                                             'role': 'http.native-primitives.source'},
                                               'RLOCK_OWNED': {'attribute': 'RLOCK_OWNED',
                                                               'kind': 'role-export',
                                                               'role': 'http.native-primitives.source'},
                                               'RLOCK_RELEASE': {'attribute': 'RLOCK_RELEASE',
                                                                 'kind': 'role-export',
                                                                 'role': 'http.native-primitives.source'}},
                                  'callers': [{'function': '_trusted_run', 'role': 'bootstrap.entry'}],
                                  'closure': ['http.native-primitives.bound',
                                              'http.native-primitives.source'],
                                  'group': None,
                                  'is_package': False,
                                  'kind': 'module',
                                  'module': '_som_http_native_primitives_bound',
                                  'package': '',
                                  'source': 'som-rs/scripts/hosted_readiness/http_native_primitives.py'},
 'http.native-primitives.source': {'bindings': {'FRAME_GET': {'attribute': '_getframe',
                                                              'kind': 'stdlib',
                                                              'module': 'sys'},
                                                'GEN_CLOSE': {'attribute': 'GeneratorType.close',
                                                              'kind': 'stdlib',
                                                              'module': 'types'},
                                                'GEN_SEND': {'attribute': 'GeneratorType.send',
                                                             'kind': 'stdlib',
                                                             'module': 'types'},
                                                'OBJECT_NEW': {'attribute': 'object.__new__',
                                                               'kind': 'stdlib',
                                                               'module': 'builtins'},
                                                'RLOCK_ACQUIRE': {'attribute': 'RLock.acquire',
                                                                  'kind': 'stdlib',
                                                                  'module': '_thread'},
                                                'RLOCK_NEW': {'attribute': 'RLock.__new__',
                                                              'kind': 'stdlib',
                                                              'module': '_thread'},
                                                'RLOCK_OWNED': {'attribute': 'RLock._is_owned',
                                                                'kind': 'stdlib',
                                                                'module': '_thread'},
                                                'RLOCK_RELEASE': {'attribute': 'RLock.release',
                                                                  'kind': 'stdlib',
                                                                  'module': '_thread'}},
                                   'callers': [{'function': '_trusted_run', 'role': 'bootstrap.entry'}],
                                   'closure': ['http.native-primitives.source'],
                                   'group': None,
                                   'is_package': False,
                                   'kind': 'module',
                                   'module': '_som_http_native_primitives_source',
                                   'package': '',
                                   'source': 'som-rs/scripts/hosted_readiness/http_native_primitives.py'}}
_HTTP_PROVIDER_BINDINGS={'__som_native_HTTP_FRAME_GET__': {'attribute': 'FRAME_GET',
                                   'kind': 'role-export',
                                   'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_GEN_CLOSE__': {'attribute': 'GEN_CLOSE',
                                   'kind': 'role-export',
                                   'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_GEN_SEND__': {'attribute': 'GEN_SEND',
                                  'kind': 'role-export',
                                  'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_OBJECT_NEW__': {'attribute': 'OBJECT_NEW',
                                    'kind': 'role-export',
                                    'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_RLOCK_ACQUIRE__': {'attribute': 'RLOCK_ACQUIRE',
                                       'kind': 'role-export',
                                       'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_RLOCK_NEW__': {'attribute': 'RLOCK_NEW',
                                   'kind': 'role-export',
                                   'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_RLOCK_OWNED__': {'attribute': 'RLOCK_OWNED',
                                     'kind': 'role-export',
                                     'role': 'http.native-primitives.bound'},
 '__som_native_HTTP_RLOCK_RELEASE__': {'attribute': 'RLOCK_RELEASE',
                                       'kind': 'role-export',
                                       'role': 'http.native-primitives.bound'}}
_HTTP_PROVIDER_BOOTSTRAP_SOURCE=('def _trusted_run(controls, role, argv):\n'
    '    bound=controls.load_module("http.native-primitives.bound")\n'
    '    if role=="fixture.synthetic-provider-warm-only":return bound\n'
    '    return controls.execute_entry(role, argv)\n')

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
        # Sequential subtests own distinct Core domains and fixed module names.
        # Close the preceding real owner before constructing the next fixture.
        previous_fixture=getattr(case,'_som_http_fixture_owner',None)
        if previous_fixture is not None:
            previous_fixture.restore_response_methods()
            previous_fixture.ctx.qa_import_controls.close()
            previous_fixture.stack.close()
            case.assertTrue(previous_fixture.domain.closed)
        key='SOM_FULL_PRODUCT_CONTEXT';present=key in os.environ;previous=os.environ.get(key)
        def restore_environment():
            if present:os.environ[key]=previous
            else:os.environ.pop(key,None)
        case.addCleanup(restore_environment)
        with mock.patch.object(protocol,'ProtocolTransfer',GitTransfer):super().__init__(case,CALLER_SOURCE,entry=True)
        # Public fixed-role handoff occurs before Context or HTTP owner construction.
        # This is a synthetic source/control fixture, not native-origin qualification.
        self.initial=self.seeds['_som_native_trusted_bootstrap_entry']['module']
        self.native_provider=self.initial._trusted_run(self.controls,'fixture.synthetic-provider-warm-only',[])
        self.native_provider_source=sys.modules['_som_http_native_primitives_source']
        case.assertTrue(_HTTP_PROVIDER_CONTROL_ONLY)
        self.context=self.domain.load_known('runtime.context',self.domain.prepare('runtime.context'),None)
        self.local=self.root/'.local';self.local.mkdir()
        self.context_path=self.local/'context.json';self.context_path.write_bytes(b'{"explicit_context_double":true}')
        self.ctx=self.context.Context(self.root,self.revision,self.context_path,self.controls,self.transfer.admission)
        self.context._ACTIVE=self.ctx;os.environ[key]=str(self.context_path)
        case.addCleanup(self.ctx.qa_import_controls.close)
        self.data=self.domain.load_known('fixture.http.data',self.domain.prepare('fixture.http.data'),None)
        self.original_response_closes=[]
        self.original_response_methods=[]
        case.addCleanup(lambda:[close() for close in self.original_response_closes])
        case.addCleanup(self.restore_response_methods)
        self.initial=self.seeds['_som_native_trusted_bootstrap_entry']['module']
        self.sequence=0;self.transports=[]
        case._som_http_fixture_owner=self

    def restore_response_methods(self):
        # Test-owned data restoration runs after assertions and before fixture
        # Core teardown. A retained HTTPError closer must see the real method,
        # not a negative-test replacement when its actual destructor runs.
        for response, originals in self.original_response_methods:
            fields=object.__getattribute__(response,'__dict__')
            for name,present,value in originals:
                if present:dict.__setitem__(fields,name,value)
                else:dict.pop(fields,name,None)

    def synthetic_seed(self,name,path,raw):
        if name=='_som_native_trusted_bootstrap_entry':
            expected='def _trusted_run(controls, role, argv):\n    return controls.execute_entry(role, argv)\n'
            if raw!=expected:raise RuntimeError('synthetic bootstrap seed changed before provider wiring')
            raw=_HTTP_PROVIDER_BOOTSTRAP_SOURCE
        return super().synthetic_seed(name,path,raw)

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='caller':
            row['bindings']['__som_native_active_controls__']={'kind':'active-controls'}
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,CONTEXT_SOURCE,module='_som_m6_http_fixture_context')
        data=BASE+'synthetic_m6_http_data.py'
        super().add_role('fixture.http.data',data,DATA_SOURCE,module='_som_m6_http_fixture_data')
        for provider_role,provider_row in _HTTP_PROVIDER_ROLES.items():
            provider_role_row=super().add_role(provider_role,_HTTP_PROVIDER_PATH,checked(_HTTP_PROVIDER_PATH),
                kind=provider_row['kind'],module=provider_row['module'],
                callers=[dict(value) for value in provider_row['callers']],
                group=provider_row['group'],bindings={name:dict(value) for name,value in provider_row['bindings'].items()})
            provider_role_row['package']=provider_row['package']
            provider_role_row['is_package']=provider_row['is_package']
            provider_role_row['closure']=list(provider_row['closure'])
        super().add_role('m6.record-http.owner',SELF,checked(SELF),module='_som_m6_record_http_owner',bindings={
            '__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
            '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'},
            '__som_native_context_read__':{'kind':'role-export','role':'runtime.context','attribute':'read'}})
        self.rows['m6.record-http.owner']['bindings'].update(
            {name:dict(value) for name,value in _HTTP_PROVIDER_BINDINGS.items()})
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
        bindings.update({name:dict(value) for name,value in _HTTP_PROVIDER_BINDINGS.items()})
        for name in ('_NativeRecordingState','_NativeCallState','_NativeResponseState','_NativeCallbackSeal',
                     '_NativeExitFrame','_NativeExitState','_NativeExitDescriptor','_NativeIssuer'):
            bindings['__som_native_fixed_'+name+'__']={'kind':'role-export','role':'m6.record-http.owner','attribute':name}
        bindings['__som_native_HTTP_issuer__']={'kind':'role-export','role':'m6.record-http.owner','attribute':'_NATIVE_ISSUER'}
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
        fields=object.__getattribute__(value,'__dict__')
        self.original_response_methods.append((value,tuple((name,name in fields,dict.get(fields,name))
            for name in ('read','close'))))
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
                    original_exit=super().__exit__
                    fixture.context._ACTIVE=fixture.foreign_context
                    return original_exit(*exception)
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
        self.case.assertEqual(hook.__qualname__,'_NativeRecordingState.enter.<locals>.recording_open')
        state=next(cell.cell_contents for name,cell in zip(hook.__code__.co_freevars,hook.__closure__) if name=='state')
        self.case.assertEqual(type(state).__name__,'_NativeRecordingState')
        recorder=state.recorder
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


V4_TARGET_SOURCE = V3_TARGET_SOURCE.replace('import json,os,sys,urllib.request,urllib.error,concurrent.futures',
    'import json,os,sys,urllib.request,urllib.error,concurrent.futures,threading\nfrom types import FunctionType').replace(
    '    value=opened(1)\n', '''    if mode in {'v4-two','v4-cell','v4-row','v4-record-fields'}:
        first,second=opened(1),opened(2)
        assert first.read(3)==second.read(3)==b'raw'
        if mode=='v4-two':return 0
        if mode=='v4-record-fields':
            for name in data.RECORD_KEYS:data.RECORDER.calls[0].record[name]=data.HOOK_VALUE
            try:raise data.PRIMARY
            except RuntimeError as error:
                data.ORIGINAL_TRACE=error.__traceback__
                raise
        recorder=data.RECORDER
        row=recorder._native_operations[6][0]
        function=row[1]
        if mode=='v4-cell':function.__closure__[0].cell_contents=data.FOREIGN
        else:
            def cell(value):return (lambda:value).__closure__[0]
            clone=FunctionType(function.__code__,function.__globals__,argdefs=function.__defaults__,
                closure=tuple(cell(data.FOREIGN) for item in function.__closure__))
            clone.__kwdefaults__=function.__kwdefaults__
            recorder._native_operations[6][0]=(row[0],clone,(clone.__code__,clone.__globals__,
                clone.__defaults__,clone.__kwdefaults__,clone.__closure__))
        raise data.PRIMARY
    if mode=='v4-http-error-method':
        try:opened(1)
        except urllib.error.HTTPError as error:
            data.HTTP_ERROR=error
            assert error.read(3)==b'raw'
            setattr(error.fp,data.SHADOW,data.FOREIGN)
            with error:raise data.PRIMARY
        raise AssertionError('actual HTTPError required')
    value=opened(1)
    if mode=='v4-source':
        assert value.read(3)==b'raw'
        vars(data.SOURCES)[data.SHADOW]=data.FOREIGN
        return 0
    if mode=='v4-borrow':
        assert value.read(3)==b'raw'
        data.LEASE_CLASS.close=data.DESCRIPTOR
        raise data.PRIMARY
    if mode=='v4-method':
        assert value.read(3)==b'raw'
        setattr(value,data.SHADOW,data.FOREIGN)
        with value:raise data.PRIMARY
    if mode=='v4-original-method':
        assert value.read(3)==b'raw'
        state=vars(data.RECORDER)['_native_state'].registered[0]
        function=data.FOREIGN;actual=function.__func__
        cells=actual.__closure__ or ()
        signature=(function,function.__self__,actual,actual.__code__,actual.__globals__,actual.__builtins__,
            actual.__defaults__,actual.__kwdefaults__,actual.__closure__,
            tuple((cell,cell.cell_contents) for cell in cells),
            tuple(actual.__kwdefaults__.items()) if type(actual.__kwdefaults__) is dict else None)
        setattr(state,data.SHADOW,signature)
        with value:
            try:raise data.PRIMARY
            except RuntimeError as error:
                data.ORIGINAL_TRACE=error.__traceback__
                raise
    if mode=='v4-descriptor-cell':
        assert value.read(3)==b'raw'
        state=vars(data.RECORDER)['_native_state']
        record=data.OWNER_MODULE._NATIVE_ISSUER.recordings[id(state)]
        function=record['exits'][0][3][0]
        function.__closure__[0].cell_contents=data.FOREIGN
        with value:data.BODY_ENTERED=True
        raise AssertionError('changed exit cell must refuse before body')
    if mode=='v4-threads':
        assert value.read(3)==b'raw'
        def worker(error):
            try:
                with value:raise error
            except BaseException as actual:
                data.RESULTS.append((error,actual,actual.__traceback__))
        first=threading.Thread(target=worker,args=(data.A,));first.start()
        assert data.ENTERED.wait(10)
        second=threading.Thread(target=worker,args=(data.B,));second.start();second.join(10)
        assert not second.is_alive()
        data.RELEASE.set();first.join(10)
        assert not first.is_alive()
        return 0
''',1)


class V4Fixture(V3Fixture):
    def __init__(self,case):
        super().__init__(case)
        self.data.SOURCES=self.ctx.sources
        self.data.LEASE_CLASS=type(self.controls)
        self.data.BODY_ENTERED=False
        self.data.RESULTS=[]
        self.data.A,self.data.B=RuntimeError('first thread body'),LookupError('second thread body')
        self.data.ENTERED,self.data.RELEASE=threading.Event(),threading.Event()
        self.data.FOREIGN=self.foreign
        self.cleanup_controller=None

    def foreign(self,*args,**kwargs):
        self.foreign_hooks.append('foreign callback')
        raise AssertionError('foreign callback must not run')

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='m6.api.producer':
            for target in TARGETS:
                self.raw[target]=V4_TARGET_SOURCE.encode();(self.root/target).write_bytes(self.raw[target])
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
            def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],env=env,
                check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            git('add','--','.')
            git('-c','user.name=HTTP V4 finite protocol','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null',
                'commit','--quiet','-m','finite source-owned V4 target negative data')
            self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def transport(self,handler,http_class,request,**kwargs):
        recorder=self.actual_recorder()
        self.recorder_seen=recorder
        self.data.RECORDER=recorder
        self.data.OWNER_MODULE=sys.modules[type(recorder).__module__]
        value=super().transport(handler,http_class,request,**kwargs)
        if self.mode=='v4-http-error':value.status=value.code=400
        if self.mode=='v4-threads':
            original=value.close
            def blocked_close():
                self.close_attempts+=1;self.data.ENTERED.set()
                if not self.data.RELEASE.wait(10):raise RuntimeError('fixture release deadline')
                return original()
            value.close=blocked_close
        return value


class NativeHttpV4Tests(unittest.TestCase):
    def test_real_class_alias_two_calls_restore_verify_and_true_owner_cleanup(self):
        f=V4Fixture(self)
        self.assertIs(f.data.KIND,http.client.HTTPResponse)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            self.assertEqual(f.invoke(mode='v4-two',direct=True),0)
        proof=f.manifest()['native_source_execution']
        self.assertTrue(proof['source_postcheck'])
        self.assertEqual(len(f.manifest()['calls']),2)
        self.assertTrue(all(row['saved'] for row in proof['raw_finalizations']))
        self.assertTrue(all(row['restored'] and row['postcheck_passed'] for row in proof['response_exit_scope']))
        f.controls.verify_installed()
        f.ctx.qa_import_controls.close()
        f.finish()
        self.assertTrue(f.domain.closed)
        self.assertNotIn('_som_m6_record_http_owner',sys.modules)

    def test_late_SourceBytes_git_read_verify_shadows_refuse_before_dispatch(self):
        for name in ('git','read','verify'):
            with self.subTest(name=name):
                f=V4Fixture(self);f.data.SHADOW=name
                try:
                    with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                        with self.assertRaisesRegex(ValueError,'SourceBytes instance dispatch changed'):
                            f.invoke(mode='v4-source',direct=True)
                    self.assertEqual(f.foreign_hooks,[])
                    proof=f.manifest()['native_source_execution']
                    self.assertFalse(proof['source_postcheck'])
                    self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
                    self.assertFalse((f.out/'calls/000001.response.raw').exists())
                finally:vars(f.ctx.sources).pop(name,None)

    def test_nonowning_close_descriptor_is_not_looked_up_and_other_cleanup_runs(self):
        f=V4Fixture(self);cls=type(f.controls);original=vars(cls)['close']
        class ForeignDescriptor:
            def __get__(self,*args):
                f.foreign_hooks.append('descriptor lookup')
                raise AssertionError('foreign close descriptor must not run')
        f.data.DESCRIPTOR=ForeignDescriptor()
        try:
            with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                try:f.invoke(mode='v4-borrow',direct=True)
                except RuntimeError as error:self.assertIs(error,f.data.PRIMARY)
                else:self.fail('actual body primary required')
            self.assertEqual(f.foreign_hooks,[])
            proof=f.manifest()['native_source_execution']
            self.assertFalse(proof['source_postcheck'])
            self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
            state=vars(f.recorder_seen)['_native_state'];borrow=state.borrow[0]
            self.assertFalse(object.__getattribute__(borrow,'_closed'))
        finally:
            type.__setattr__(cls,'close',original)
            if f.recorder_seen is not None:
                state=vars(f.recorder_seen)['_native_state']
                if state.borrow is not None:original(state.borrow[0])

    def test_actual_finalizer_cell_change_never_runs_hook_and_second_call_is_saved(self):
        f=V4Fixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='v4-cell',direct=True)
            except RuntimeError as error:self.assertIs(error,f.data.PRIMARY)
            else:self.fail('actual target body primary required')
        self.assertEqual(f.foreign_hooks,[])
        self.assertFalse((f.out/'calls/000001.response.raw').exists())
        self.assertEqual((f.out/'calls/000002.response.raw').read_bytes(),b'raw')
        proof=f.manifest()['native_source_execution']
        self.assertTrue(any(row['call_id']==1 and not row['attempted'] for row in proof['raw_finalizations']))
        self.assertTrue(any(row['call_id']==2 and row['saved'] for row in proof['raw_finalizations']))

    def test_forged_public_finisher_signature_cannot_grant_clone_authority(self):
        f=V4Fixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='v4-row',direct=True)
            except RuntimeError as error:self.assertIs(error,f.data.PRIMARY)
            else:self.fail('actual target body primary required')
        self.assertEqual(f.foreign_hooks,[])
        self.assertEqual((f.out/'calls/000001.response.raw').read_bytes(),b'raw')
        self.assertEqual((f.out/'calls/000002.response.raw').read_bytes(),b'raw')
        self.assertTrue(all(row['saved'] for row in f.manifest()['native_source_execution']['raw_finalizations']))

    def test_descriptor_checks_original_cell_before_returning_or_binding_callable(self):
        f=V4Fixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            with self.assertRaisesRegex(ValueError,'constructor-issued callback invocation changed'):
                f.invoke(mode='v4-descriptor-cell',direct=True)
        self.assertFalse(f.data.BODY_ENTERED)
        self.assertEqual(f.foreign_hooks,[])
        proof=f.manifest()['native_source_execution']
        self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
        self.assertFalse(proof['source_postcheck'])

    def test_registered_response_read_close_and_HTTPError_inner_replacements_never_run(self):
        for mode,name in (('v4-method','read'),('v4-method','close'),('v4-http-error-method','close')):
            with self.subTest(mode=mode,name=name):
                f=V4Fixture(self);f.data.SHADOW=name
                if mode=='v4-http-error-method':f.mode='v4-http-error'
                with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                    try:f.invoke(mode=mode,direct=True)
                    except RuntimeError as error:self.assertIs(error,f.data.PRIMARY)
                    else:self.fail('actual explicit body primary required')
                self.assertEqual(f.foreign_hooks,[])
                proof=f.manifest()['native_source_execution']
                self.assertFalse(proof['source_postcheck'])
                self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
                self.assertFalse((f.out/'calls/000001.response.raw').exists())

    def test_same_response_two_threads_keep_own_body_and_one_exit_owner(self):
        f=V4Fixture(self);f.mode='v4-threads'
        try:
            with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                try:f.invoke(mode='v4-threads',direct=True)
                except LookupError as error:self.assertIs(error,f.data.B)
                else:self.fail('overlapping response exit must refuse')
            self.assertEqual(len(f.data.RESULTS),2)
            for expected,actual,trace in f.data.RESULTS:
                self.assertIs(actual,expected);self.assertIsNotNone(trace)
            self.assertEqual(f.close_attempts,1)
            self.assertEqual(f.foreign_hooks,[])
            state=vars(f.recorder_seen)['_native_state']
            self.assertTrue(all(row.exit_frame is None for row in state.registered))
            self.assertTrue(all(row['restored'] for row in f.manifest()['native_source_execution']['response_exit_scope']))
        finally:f.data.RELEASE.set()


    def test_constructor_record_pins_and_original_delegate_seals_refuse_before_foreign_hooks(self):
        for keys,value_kind in ((('id',),'foreign'),(('response_file',),'foreign'),
                                (('id','response_file'),'foreign'),(('id',),'wrong-int'),
                                (('response_file',),'wrong-path')):
            with self.subTest(record_fields=keys,value_kind=value_kind):
                f=V4Fixture(self)
                class ForeignValue:
                    def __format__(self,*args):
                        f.foreign_hooks.append('record format');raise AssertionError('foreign record format must not run')
                    def __rtruediv__(self,*args):
                        f.foreign_hooks.append('record path');raise AssertionError('foreign record path must not run')
                    def __fspath__(self):
                        f.foreign_hooks.append('record fspath');raise AssertionError('foreign record fspath must not run')
                    def __str__(self):
                        f.foreign_hooks.append('record string');raise AssertionError('foreign record string must not run')
                f.data.RECORD_KEYS=keys
                f.data.HOOK_VALUE=(ForeignValue() if value_kind=='foreign' else
                                   999999 if value_kind=='wrong-int' else 'calls/999999.response.raw')
                original_open=urllib.request.AbstractHTTPHandler.do_open
                with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                    try:f.invoke(mode='v4-record-fields',direct=True)
                    except RuntimeError as error:
                        self.assertIs(error,f.data.PRIMARY)
                        trace=error.__traceback__;actual=[]
                        while trace is not None:actual.append(trace);trace=trace.tb_next
                        self.assertTrue(any(value is f.data.ORIGINAL_TRACE for value in actual))
                    else:self.fail('actual record mutation body primary required')
                self.assertEqual(f.foreign_hooks,[])
                self.assertIs(urllib.request.AbstractHTTPHandler.do_open,original_open)
                self.assertFalse((f.out/'calls/000001.response.raw').exists())
                self.assertEqual((f.out/'calls/000002.response.raw').read_bytes(),b'raw')
                state=vars(f.recorder_seen)['_native_state']
                self.assertIs(object.__getattribute__(state.borrow[0],'_closed'),True)
                self.assertTrue(all(row['restored'] for row in state.exit_events))
                storage=BaseException.__dict__['__dict__'].__get__(f.data.PRIMARY,BaseException)
                self.assertTrue(any(type(value) is ValueError and 'original HTTP record value or identity changed' in str(value)
                                    for value in storage['__som_native_http_secondary__']))
        for name in ('original_read','original_close'):
            with self.subTest(original_delegate=name):
                f=V4Fixture(self);f.data.SHADOW=name
                with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                    try:f.invoke(mode='v4-original-method',direct=True)
                    except RuntimeError as error:
                        self.assertIs(error,f.data.PRIMARY)
                        trace=error.__traceback__;actual=[]
                        while trace is not None:actual.append(trace);trace=trace.tb_next
                        self.assertTrue(any(value is f.data.ORIGINAL_TRACE for value in actual))
                    else:self.fail('actual delegate mutation body primary required')
                self.assertEqual(f.foreign_hooks,[])
                self.assertFalse((f.out/'calls/000001.response.raw').exists())
                state=vars(f.recorder_seen)['_native_state']
                self.assertIs(object.__getattribute__(state.borrow[0],'_closed'),True)
                self.assertTrue(all(row['restored'] for row in state.exit_events))
                self.assertFalse(f.manifest()['native_source_execution']['source_postcheck'])



_CONSUMER_TARGET_CASES = '''    def origin_values(recorder):
        channel=vars(recorder)['_native_origin']
        assert channel.gi_frame is not None
        values=channel.gi_frame.f_locals
        assert values['recorder'] is recorder and values['capture_count']==1
        return channel,values
    def trace_has(error,code):
        node=error.__traceback__
        while node is not None:
            if node.tb_frame.f_code is code:return True
            node=node.tb_next
        return False
    def record_refusal(label,error,code):
        assert type(error) is ValueError and trace_has(error,code)
        data.REFUSALS.append((label,error,error.__traceback__))
    def clone_function(function):
        clone=FunctionType(function.__code__,function.__globals__,function.__name__,
                           function.__defaults__,function.__closure__)
        clone.__kwdefaults__=function.__kwdefaults__
        assert clone is not function and clone.__code__ is function.__code__
        assert clone.__globals__ is function.__globals__
        return clone
    def actual_arguments(function,frame):
        code=function.__code__;values=frame.f_locals
        assert not code.co_flags & (4|8)
        args=[values[name] for name in code.co_varnames[:code.co_argcount]]
        keywords={name:values[name] for name in
            code.co_varnames[code.co_argcount:code.co_argcount+code.co_kwonlyargcount]}
        return args,keywords
    def two_pending():
        first,second=opened(1),opened(2)
        assert first.read(3)==second.read(3)==b'raw'
        data.TARGET_REACHED.append((mode,len(data.NEW_RESPONSES),len(data.READ_CALLS)))
        return first,second,data.RECORDER,vars(data.RECORDER)['_native_state']
    if mode=='consumer-forged-issuer':
        first,second,recorder,state=two_pending();owner=data.OWNER_MODULE
        channel,values=origin_values(recorder);data.CAPTURED_ORIGINS.append(channel)
        original=owner._NATIVE_ISSUER
        forged=object.__new__(type(original))
        forged.recordings=dict(original.recordings);forged.calls=dict(original.calls);forged.helpers=original.helpers
        original_record=vars(state)['_native_record'];forged_record=dict(original_record)
        forged_record['calls']=list(original_record['calls'])
        forged_record['responses']=list(original_record['responses']);forged_record['exits']=list(original_record['exits'])
        forged_record['sinks']=dict(original_record['sinks'])
        call=recorder.calls[0];call_state=vars(call)['_native_state']
        old_callbacks,old_seals=call_state.callbacks,call_state.seals
        old_finishers=list(state.finishers)
        def foreign_finalizer(*args,**kwargs):
            data.FOREIGN_FINALIZERS.append('called')
            raise AssertionError('forged finalizer must not run')
        cells=foreign_finalizer.__closure__ or ()
        signature=(foreign_finalizer,None,foreign_finalizer,foreign_finalizer.__code__,
            foreign_finalizer.__globals__,foreign_finalizer.__builtins__,foreign_finalizer.__defaults__,
            foreign_finalizer.__kwdefaults__,foreign_finalizer.__closure__,
            tuple((cell,cell.cell_contents) for cell in cells),
            tuple(foreign_finalizer.__kwdefaults__.items()) if type(foreign_finalizer.__kwdefaults__) is dict else None)
        seal=object.__new__(type(old_seals[0]));seal.controller=state;seal.state=call_state;seal.signature=signature
        callbacks=(foreign_finalizer,*old_callbacks[1:]);seals=(seal,*old_seals[1:])
        old_issue=call_state._native_call_record
        forged_issue=(call_state,callbacks,seals,(signature,*old_issue[3][1:]),call,state,old_issue[6])
        forged_record['calls'][0]=forged_issue;forged.recordings[id(state)]=forged_record
        forged.calls[id(call_state)]=forged_issue
        try:
            with first:
                owner._NATIVE_ISSUER=forged
                call_state.callbacks,call_state.seals=callbacks,seals
                state.finishers[0]=(call,foreign_finalizer,(foreign_finalizer.__code__,foreign_finalizer.__globals__,
                    foreign_finalizer.__defaults__,foreign_finalizer.__kwdefaults__,foreign_finalizer.__closure__))
                try:raise data.PRIMARY
                except RuntimeError as error:
                    data.ORIGINAL_TRACE=error.__traceback__
                    raise
        finally:
            owner._NATIVE_ISSUER=original
            call_state.callbacks,call_state.seals=old_callbacks,old_seals
            state.finishers[:]=old_finishers
            data.PRIVATE_ERRORS=[extra for phase,primary,trace,extra in original_record['failures']
                if extra is not None and trace_has(extra,owner._native_transaction.__code__)]
        raise AssertionError('actual target primary must escape the response exit')
    if mode=='consumer-creation-probes':
        first,second,recorder,state=two_pending();channel,values=origin_values(recorder)
        data.A_RECORDER,data.A_ORIGIN=recorder,channel
        creator,constructor=values['creator_frame'],values['constructor_frame']
        producer=creator.f_locals['producer'];acquisition=constructor.f_locals['acquisition']
        original_send_count=values['send_count'];before_reads=len(data.READ_CALLS)
        fake=clone_function(producer)(*actual_arguments(producer,channel.gi_frame)[0],
                                      **actual_arguments(producer,channel.gi_frame)[1])
        try:
            try:data.PROVIDER.GEN_SEND(fake,None)
            except ValueError as error:
                record_refusal('fake-first-send',error,producer.__code__)
                assert str(error)=='literal HTTP producer needs its actual first sender'
                failed=error.__traceback__
                while failed.tb_frame.f_code is not producer.__code__:failed=failed.tb_next
                observed=failed.tb_frame.f_locals
                assert observed['capture_count']==0 and observed['phase']=='unprimed'
                assert not any(name in observed for name in ('recorder','state','lock'))
                data.CREATION_RESULTS.append(('fake-first-send',0))
            else:raise AssertionError('foreign first send captured an expected row')
        finally:data.PROVIDER.GEN_CLOSE(fake)
        clone=clone_function(acquisition);args,keywords=actual_arguments(acquisition,creator)
        try:clone(*args,**keywords)
        except ValueError as error:
            record_refusal('cloned-factory',error,acquisition.__code__)
            assert str(error)=='literal HTTP acquisition needs its actual creator'
            failed=error.__traceback__
            while failed.tb_frame.f_code is not acquisition.__code__:failed=failed.tb_next
            assert 'producer' not in failed.tb_frame.f_locals
            data.CREATION_RESULTS.append(('cloned-factory',0))
        else:raise AssertionError('foreign cloned factory allocated a receiver')
        assert channel.gi_frame.f_locals['capture_count']==1
        assert channel.gi_frame.f_locals['send_count']==original_send_count
        assert len(data.READ_CALLS)==before_reads
        # B is a real nested allocation. A remains in its live actual target.
        data.NESTED_RUN()
        assert data.CROSS_REFUSAL_OBSERVED and data.CROSS_B_ORIGIN.gi_frame is None
        assert channel.gi_frame is not None
        assert channel.gi_frame.f_locals['send_count']==original_send_count
        assert len(data.READ_CALLS)==before_reads
        assert channel.gi_frame.f_locals['capture_count']==1
        second.read();second.close();data.SAFE_A_AFTER_CROSS=True
        return 0
    if mode in {'consumer-outside-lock','consumer-contended-lock','consumer-short-reentry','consumer-short-guard'}:
        first,second,recorder,state=two_pending();channel,values=origin_values(recorder)
        provider=data.PROVIDER;lock=values['lock'];owner=data.OWNER_MODULE
        def read_worker(response,label):
            data.WORKER_STARTED.set()
            try:
                response.read();response.close();data.WORKER_RESULTS.append((label,None))
            except BaseException as error:data.WORKER_RESULTS.append((label,error))
            finally:data.WORKER_DONE.set()
        if mode=='consumer-outside-lock':
            data.BLOCK_FIRST=True
            worker=threading.Thread(target=read_worker,args=(first,'A'));data.WORKERS.append(worker);worker.start()
            try:
                assert data.BLOCK_ENTERED.wait(10)
                other=threading.Thread(target=read_worker,args=(second,'B'));data.WORKERS.append(other);other.start()
                assert data.OTHER_COMPLETED.wait(10)
                other.join(10);assert not other.is_alive()
                assert worker.is_alive() and not data.BLOCK_RELEASE.is_set()
            finally:
                data.BLOCK_RELEASE.set()
                for thread in data.WORKERS:thread.join(10)
                assert all(not thread.is_alive() for thread in data.WORKERS)
            assert all(not thread.is_alive() for thread in data.WORKERS)
            assert sorted(data.WORKER_RESULTS)==[('A',None),('B',None)]
        elif mode=='consumer-contended-lock':
            assert provider.RLOCK_ACQUIRE(lock,True) is True
            worker=threading.Thread(target=read_worker,args=(second,'B'));data.WORKERS.append(worker)
            try:
                worker.start();assert data.WORKER_STARTED.wait(10)
                deadline=time.monotonic()+10;observed=False
                while time.monotonic()<deadline and not data.WORKER_DONE.is_set():
                    frame=sys._current_frames().get(worker.ident)
                    while frame is not None:
                        if (frame.f_code is owner._native_transaction.__code__
                                and frame.f_lineno==data.TRANSACTION_ACQUIRE_LINE
                                and frame.f_locals.get('lock') is lock):
                            observed=True;break
                        frame=frame.f_back
                    if observed:break
                assert observed and not data.WORKER_DONE.is_set()
                data.CONTENDED_FRAME_SEEN=True
            finally:
                provider.RLOCK_RELEASE(lock)
                if worker.ident is not None:worker.join(10)
                assert not worker.is_alive()
            assert data.WORKER_RESULTS==[('B',None)]
        elif mode=='consumer-short-reentry':
            assert provider.RLOCK_ACQUIRE(lock,True) is True
            sends=channel.gi_frame.f_locals['send_count']
            try:
                try:owner._native_transaction(state,'preflight')
                except ValueError as error:
                    record_refusal('same-thread-short-reentry',error,owner._native_transaction.__code__)
                    assert 'same-thread' in str(error)
                else:raise AssertionError('same-thread short reentry was not refused')
                assert provider.RLOCK_OWNED(lock) is True
                assert channel.gi_frame.f_locals['send_count']==sends
            finally:provider.RLOCK_RELEASE(lock)
            assert provider.RLOCK_OWNED(lock) is False
            second.read();second.close();data.SAFE_AFTER_GUARD=True
        else:
            call_state=vars(recorder.calls[0])['_native_state'];expected=call_state.expected
            original=expected['recorder'];sends=channel.gi_frame.f_locals['send_count']
            expected['recorder']=object()
            try:
                try:owner._NativeCallState.check(call_state)
                except ValueError as error:
                    record_refusal('short-guard-error',error,owner._native_transaction.__code__)
                    data.GUARD_ERROR,data.GUARD_TRACE=error,error.__traceback__
                else:raise AssertionError('actual private guard error was accepted')
            finally:expected['recorder']=original
            assert provider.RLOCK_OWNED(lock) is False
            data.GUARD_SEND_DELTA=channel.gi_frame.f_locals['send_count']-sends
            assert data.GUARD_SEND_DELTA>0
            node=data.GUARD_TRACE;failed_transactions=0
            while node is not None:
                failed_transactions+=node.tb_frame.f_code is owner._native_transaction.__code__
                node=node.tb_next
            assert failed_transactions==1
            second.read();second.close();data.SAFE_AFTER_GUARD=True
            raise data.GUARD_ERROR.with_traceback(data.GUARD_TRACE)
        return 0
'''

CONSUMER_TARGET_SOURCE=V4_TARGET_SOURCE.replace(
    'import json,os,sys,urllib.request,urllib.error,concurrent.futures,threading\n',
    'import json,os,sys,urllib.request,urllib.error,concurrent.futures,threading,time\n').replace(
    '    value=opened(1)\n',_CONSUMER_TARGET_CASES+'    value=opened(1)\n',1)

# Only the explicit synthetic Context double has this finite constructor probe.
# No production Context function or admitted native function is patched.
CONTEXT_SOURCE=CONTEXT_SOURCE.replace('def context():return _ACTIVE\n',
    '_CONSUMER_GETTER_HOOK=None\n'
    'def context():\n'
    '    if _CONSUMER_GETTER_HOOK is not None:_CONSUMER_GETTER_HOOK()\n'
    '    return _ACTIVE\n')


class ConsumerFixture(V4Fixture):
    """Synthetic controls only. No qualified runtime, Context or native origin."""
    def invoke(self,**kwargs):
        # These identity/trace witnesses use the existing direct source route.
        # The production CLI keeps its unchanged error-to-exit-code behavior.
        kwargs.setdefault('direct',True)
        return super().invoke(**kwargs)

    def __init__(self,case):
        super().__init__(case)
        self.data.PROVIDER=self.native_provider
        self.data.READ_CALLS=[];self.data.CLOSE_CALLS=[];self.data.REFUSALS=[]
        self.data.CREATION_RESULTS=[];self.data.CAPTURED_ORIGINS=[]
        self.data.FOREIGN_FINALIZERS=[];self.data.TARGET_REACHED=[]
        self.data.PRIVATE_ERRORS=[];self.data.WORKERS=[];self.data.WORKER_RESULTS=[]
        self.data.WORKER_STARTED=threading.Event();self.data.WORKER_DONE=threading.Event()
        self.data.BLOCK_ENTERED=threading.Event();self.data.BLOCK_RELEASE=threading.Event()
        self.data.OTHER_COMPLETED=threading.Event();self.data.BLOCK_FIRST=False
        self.data.CONTENDED_FRAME_SEEN=False;self.data.SAFE_AFTER_GUARD=False
        self.data.CROSS_REFUSAL_OBSERVED=False;self.data.SAFE_A_AFTER_CROSS=False
        self.data.NESTED_RUN=self.nested_recording
        self.ctx._synthetic_foreign_new_calls=[]
        transaction=next(node for node in source_tree.body if isinstance(node,ast.FunctionDef)
                         and node.name=='_native_transaction')
        acquire=[node for node in ast.walk(transaction) if isinstance(node,ast.Call)
                 and isinstance(node.func,ast.Name) and node.func.id=='rlock_acquire']
        case.assertEqual(len(acquire),1)
        self.data.TRANSACTION_ACQUIRE_LINE=acquire[0].lineno
        def stop_workers():
            self.data.BLOCK_RELEASE.set()
            for thread in self.data.WORKERS:
                if thread.ident is not None:thread.join(10)
            case.assertTrue(all(not thread.is_alive() for thread in self.data.WORKERS))
        case.addCleanup(stop_workers)

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='m6.api.producer':
            super().add_role('fixture.http.consumer-nested-caller',BASE+'synthetic_http_consumer_nested.py',
                CALLER_SOURCE,kind='entry',module='__main__',
                bindings={'__som_native_active_controls__':{'kind':'active-controls'}},
                callers=[{'role':'bootstrap.entry','function':'_trusted_run'}])
            self.rows['fixture.http.call']['callers'].append(
                {'role':'fixture.http.consumer-nested-caller','function':'<module>'})
            for target in TARGETS:
                self.raw[target]=CONSUMER_TARGET_SOURCE.encode()
                (self.root/target).write_bytes(self.raw[target])
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
                 'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
            def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],
                env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            git('add','--','.')
            git('-c','user.name=HTTP finite consumer controls','-c','user.email=fixture@example.invalid',
                '-c','core.hooksPath=/dev/null','commit','--quiet','-m','finite synthetic consumer target controls only')
            self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def nested_recording(self):
        """Construct real B only until the finite cross-recording origin refusal."""
        original_out,original_trace=self.out,self.trace
        owner=self.data.OWNER_MODULE;changed=[];original_hook=self.context._CONSUMER_GETTER_HOOK
        a=self.data.A_ORIGIN
        self.case.assertIsNotNone(a.gi_frame)
        original_a_sends=a.gi_frame.f_locals['send_count']
        def observe_genuine_B():
            if changed:return
            frame=sys._getframe(1)
            while frame is not None:
                if (frame.f_code.co_qualname=='NativeRecorder.__init__'
                        and frame.f_globals is owner.__dict__):break
                frame=frame.f_back
            if frame is None:return
            b=frame.f_locals['self'];entry=frame.f_back
            facts=entry.f_locals
            signature=facts['_native_constructor_init_signature']
            self.case.assertIs(signature[3],frame.f_code)
            self.case.assertIs(signature[4],frame.f_globals)
            b_channel=facts['_native_original_channel']
            self.case.assertIs(vars(b)['_native_origin'],b_channel)
            self.case.assertIsNot(b_channel,a)
            self.case.assertIsNotNone(b_channel.gi_frame)
            b_values=b_channel.gi_frame.f_locals
            self.case.assertIs(b_values['recorder'],b)
            self.case.assertEqual(b_values['capture_count'],1)
            self.case.assertEqual(b_values['phase'],'initializing')
            self.case.assertIsNot(b,self.data.A_RECORDER)
            state=vars(b)['_native_state']
            self.case.assertIs(vars(state)['_native_origin'],b_channel)
            changed.append((state,b_channel,b))
            self.data.CROSS_B_ORIGIN=b_channel
            state._native_origin=a
        self.context._CONSUMER_GETTER_HOOK=observe_genuine_B
        runs=len(self.data.RUNS);transports=len(self.transports)
        nested_trace=None
        try:
            args=self.arguments(mode='plain',direct=True);nested_trace=self.trace
            try:self.initial._trusted_run(self.controls,'fixture.http.consumer-nested-caller',args)
            except ValueError as error:
                node=error.__traceback__;private=False
                while node is not None:
                    private=private or node.tb_frame.f_code is owner._native_transaction.__code__
                    node=node.tb_next
                self.case.assertTrue(private,'nested B refused before reaching its actual private receiver check')
                self.case.assertIn('another recording',str(error))
                self.data.REFUSALS.append(('cross-recording',error,error.__traceback__))
                self.data.CROSS_REFUSAL_OBSERVED=True
            else:self.case.fail('genuine B accepted genuine A as its private origin')
            self.case.assertEqual(len(changed),1,'genuine B initialization seam was not reached')
            self.case.assertEqual(len(self.data.RUNS),runs)
            self.case.assertEqual(len(self.transports),transports)
            self.case.assertFalse(nested_trace.exists())
            self.case.assertIsNone(changed[0][1].gi_frame,'captured original B producer was not closed')
            self.case.assertIsNotNone(a.gi_frame,'closing B used the replaced locator and closed A')
            self.case.assertEqual(a.gi_frame.f_locals['send_count'],original_a_sends)
        finally:
            self.context._CONSUMER_GETTER_HOOK=original_hook
            for state,b_channel,b in changed:state._native_origin=b_channel
            self.out,self.trace=original_out,original_trace

    def transport(self,handler,http_class,request,**kwargs):
        value=super().transport(handler,http_class,request,**kwargs)
        number=len(self.transports);read,close=value.read,value.close
        def counted_read(*args,**kwargs):
            self.data.READ_CALLS.append(number)
            if self.data.BLOCK_FIRST and number==1:
                self.data.BLOCK_ENTERED.set()
                if not self.data.BLOCK_RELEASE.wait(10):raise RuntimeError('bounded delegate release missing')
            result=read(*args,**kwargs)
            if self.data.BLOCK_FIRST and number==2:self.data.OTHER_COMPLETED.set()
            return result
        def counted_close():
            self.data.CLOSE_CALLS.append(number)
            return close()
        value.read,value.close=counted_read,counted_close
        return value


def _consumer_foreign_new_factory():
    marker=None
    def foreign(kind,out,ports=(18917,18919),*,initial_ctx):
        if marker is None:pass
        initial_ctx._synthetic_foreign_new_calls.append('foreign constructor dispatched')
        raise AssertionError('foreign constructor must not dispatch')
    return foreign


_consumer_foreign_new=_consumer_foreign_new_factory()


class NativeHttpConsumerOriginTests(unittest.TestCase):
    def assert_trace_node(self,error,original):
        node=error.__traceback__
        while node is not None:
            if node is original:return
            node=node.tb_next
        self.fail('actual original traceback node was not retained')

    def test_same_class_forged_global_issuer_and_matching_public_rows_never_authorize_foreign_finalizer(self):
        f=ConsumerFixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            try:f.invoke(mode='consumer-forged-issuer')
            except RuntimeError as error:actual=error
            else:self.fail('actual target primary did not escape')
        self.assertIs(actual,f.data.PRIMARY)
        self.assert_trace_node(actual,f.data.ORIGINAL_TRACE)
        self.assertEqual(len(f.data.RUNS),1);self.assertEqual(len(f.transports),2)
        self.assertEqual(f.data.TARGET_REACHED,[('consumer-forged-issuer',2,2)])
        self.assertEqual(f.data.FOREIGN_FINALIZERS,[]);self.assertEqual(f.foreign_hooks,[])
        self.assertTrue(f.data.PRIVATE_ERRORS,'no actual private source refusal was observed')
        proof=f.manifest()['native_source_execution']
        self.assertEqual(len(f.manifest()['calls']),2)
        self.assertTrue(any(row['call_id']==2 and row['saved'] for row in proof['raw_finalizations']))
        self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
        state=vars(f.recorder_seen)['_native_state'];borrow=state.borrow[0]
        self.assertIs(object.__getattribute__(borrow,'_owns'),False)
        self.assertIs(object.__getattribute__(borrow,'_closed'),True)

    def test_constructor_origin_rejects_fake_first_send_cloned_factory_and_cross_recording_reuse(self):
        f=ConsumerFixture(self)
        with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
            self.assertEqual(f.invoke(mode='consumer-creation-probes'),0)
            self.assertEqual(f.data.CREATION_RESULTS,[('fake-first-send',0),('cloned-factory',0)])
            self.assertIsNone(f.data.A_ORIGIN.gi_frame)
            self.assertIsNone(f.data.CROSS_B_ORIGIN.gi_frame)
            self.assertTrue(f.data.SAFE_A_AFTER_CROSS)
        self.assertEqual([row[0] for row in f.data.REFUSALS],
            ['fake-first-send','cloned-factory','cross-recording'])
        self.assertEqual(len(f.data.RUNS),1);self.assertEqual(len(f.transports),2)
        self.assertEqual(f.data.TARGET_REACHED,[('consumer-creation-probes',2,2)])
        self.assertEqual(f.foreign_hooks,[])
        for variant in ('wrapper','code','defaults','keywords','cell'):
            with self.subTest(constructor_mutation=variant):
                seen=[];restoration=[];original_mkdir=Path.mkdir
                def mutate_after_capture(path,*args,**kwargs):
                    result=original_mkdir(path,*args,**kwargs)
                    if path.name!='calls' or seen:return result
                    frame=sys._getframe(1)
                    while frame is not None and frame.f_code.co_name!='_native_run_checker':frame=frame.f_back
                    self.assertIsNotNone(frame)
                    values=frame.f_locals
                    signature=values['_native_constructor_signature'];wrapper=values['_native_constructor_wrapper']
                    kind=values['_native_constructor_kind'];function=object.__getattribute__(wrapper,'__func__')
                    self.assertIs(signature[0],function);self.assertIs(type(wrapper),staticmethod)
                    self.assertIs(values['_native_constructor_snapshot'][0],kind)
                    self.assertEqual(function.__code__.co_freevars,('__class__',))
                    self.assertEqual(len(function.__closure__),1)
                    self.assertIs(function.__closure__[0].cell_contents,kind)
                    restoration.append((kind,wrapper,function,function.__code__,function.__defaults__,function.__kwdefaults__,
                                        tuple((cell,cell.cell_contents) for cell in function.__closure__)))
                    seen.append((kind,signature,frame.f_code,frame.f_globals['_native_class_check'].__code__,
                                 frame.f_globals['_native_check_invocation'].__code__))
                    if variant=='wrapper':type.__setattr__(kind,'__new__',staticmethod(_consumer_foreign_new))
                    elif variant=='code':function.__code__=_consumer_foreign_new.__code__
                    elif variant=='defaults':function.__defaults__=(object(),)
                    elif variant=='keywords':function.__kwdefaults__={'initial_ctx':object()}
                    else:function.__closure__[0].cell_contents=object()
                    return result
                before_runs,before_transports=len(f.data.RUNS),len(f.transports)
                try:
                    with mock.patch.object(Path,'mkdir',mutate_after_capture):
                        try:f.invoke(mode='plain')
                        except ValueError as error:refusal_trace=error.__traceback__
                        else:self.fail('changed constructor was dispatched')
                    self.assertEqual(len(seen),1,'healthy constructor capture seam was not reached')
                    self.assertTrue(any(node.tb_frame.f_code is seen[0][3] or node.tb_frame.f_code is seen[0][4]
                        for node in self.trace_nodes(refusal_trace)),
                        'refusal did not reach the held source constructor seals')
                    self.assertTrue(all(node.tb_frame.f_code is not _consumer_foreign_new.__code__
                        for node in self.trace_nodes(refusal_trace)))
                    self.assertTrue(all(node.tb_frame.f_code is not seen[0][1][3]
                        for node in self.trace_nodes(refusal_trace)),
                        'constructor body ran before the captured invocation was checked')
                    self.assertEqual(f.ctx._synthetic_foreign_new_calls,[])
                    self.assertEqual(len(f.data.RUNS),before_runs);self.assertEqual(len(f.transports),before_transports)
                finally:
                    for kind,wrapper,function,code,defaults,keywords,cells in reversed(restoration):
                        function.__code__,function.__defaults__,function.__kwdefaults__=code,defaults,keywords
                        for cell,value in cells:cell.cell_contents=value
                        type.__setattr__(kind,'__new__',wrapper)

    def trace_nodes(self,node):
        while node is not None:
            yield node;node=node.tb_next

    def test_private_arbitration_allows_different_responses_and_refuses_same_thread_short_reentry(self):
        for mode in ('consumer-outside-lock','consumer-contended-lock','consumer-short-reentry','consumer-short-guard'):
            with self.subTest(mode=mode):
                f=ConsumerFixture(self)
                try:
                    with mock.patch.object(urllib.request.AbstractHTTPHandler,'do_open',f.transport):
                        if mode=='consumer-short-guard':
                            try:f.invoke(mode=mode)
                            except ValueError as error:actual=error
                            else:self.fail('actual short-transaction guard error did not escape')
                            self.assertIs(actual,f.data.GUARD_ERROR)
                            self.assert_trace_node(actual,f.data.GUARD_TRACE)
                        else:self.assertEqual(f.invoke(mode=mode),0)
                    self.assertEqual(len(f.data.RUNS),1);self.assertEqual(len(f.transports),2)
                    self.assertEqual(f.data.TARGET_REACHED,[(mode,2,2)])
                    self.assertTrue(all(not thread.is_alive() for thread in f.data.WORKERS))
                    self.assertEqual(f.foreign_hooks,[])
                    if mode=='consumer-outside-lock':self.assertEqual(sorted(f.data.WORKER_RESULTS),[('A',None),('B',None)])
                    elif mode=='consumer-contended-lock':self.assertTrue(f.data.CONTENDED_FRAME_SEEN)
                    else:self.assertTrue(f.data.SAFE_AFTER_GUARD)
                    proof=f.manifest()['native_source_execution']
                    self.assertTrue(all(row['restored'] for row in proof['response_exit_scope']))
                    self.assertTrue(any(row['call_id']==2 and row['saved'] for row in proof['raw_finalizations']))
                finally:self.doCleanups()


if __name__=='__main__':unittest.main()
