"""Actual Core/entry protocol on GitHub; Context/provider/targets are doubles.

No genuine Context constructor, API payload/private group, input, or product
qualification is claimed. Test sources run only on an admitted GitHub runner.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and sys.platform=='linux' and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub hosted Linux -I -S -B required')
HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
BASE='som-rs/scripts/hosted_readiness/'
M6='som-rs/scripts/react_output_acceptance/'
ENTRY=M6+'m6_native_entry.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
PROVIDER=M6+'react_api_python_payload.py'
API=M6+'linux_api_context.py'
RETAINED=M6+'retained_evidence.py'
LIVE=M6+'linux_api_live_inputs.py'
NODE='som-rs/tests/react-output-v1/linux_qa_profile.py'
PINS={ENTRY:'73d4ca8d82c41c37c94ce991d5e1c6829f413e1564b9fe94c11d335ed2a144e7',CONTEXT:'7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
      BASE+'native_active_controls.py':'eb0f804a8aa7de0857213d34478adc869fe51f7259c5552439dca109e4e6a038',
      BASE+'native_qa_authority.py':'024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1',
      BASE+'test_native_active_controls.py':'1a5852fb1693bcb921fcb19e9a20fb8281751e85f6f0ea2805b88323d230a201'}

def checked(path):
    raw=(REPO/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINS[path]:raise RuntimeError('exact source dependency differs: '+path)
    return raw

for path in PINS:checked(path)
protocol=ModuleType('_som_m6_entry_protocol_fixture')
protocol.__file__,protocol.__package__=str(HERE/'test_native_active_controls.py'),''
sys.modules[protocol.__name__]=protocol
exec(compile(checked(BASE+'test_native_active_controls.py'),protocol.__file__,'exec',dont_inherit=True),protocol.__dict__)
entry_tree=ast.parse(checked(ENTRY))
TARGETS=next(ast.literal_eval(n.value) for n in entry_tree.body if isinstance(n,ast.Assign)
             and any(isinstance(x,ast.Name) and x.id=='NATIVE_TARGETS' for x in n.targets))
# Real original SourceBytes/helper ASTs, used inside an explicitly synthetic Context.
context_tree=ast.parse(checked(CONTEXT));context_text=checked(CONTEXT).decode()
context_parts=[ast.get_source_segment(context_text,n) for n in context_tree.body
               if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in {'require','digest','read','relative','SourceBytes','Refusal'}]
CONTEXT_SOURCE='from __future__ import annotations\nimport hashlib,os,re,stat,subprocess\nfrom pathlib import Path\n'+'\n\n'.join(context_parts)+'''
_ACTIVE=None
class Context:
    def __init__(self,root,revision,path,site,controls,authority):
        self.repo,self.helper_revision,self.candidate_revision=root,revision,revision
        self.path,self.raw=path,path.read_bytes()
        self.qa_mode,self.qa_source_authority='native-qa-v1',authority
        self.sources=SourceBytes(root,revision)
        self.qa_import_controls=controls.borrow()
        self.site,self.payload_calls,self.verify_calls=site,0,0
        self.fail_verify=False
    def python_payload(self,executable):
        self.payload_calls+=1
        return {'role':'react-api-qa','python':str(Path(executable).absolute()),'site':str(self.site),
                'requirements_count':39,'wheel_count':40,'pth_executed':False,'product_acceptance_passed':False}
    def verify(self):
        self.verify_calls+=1
        self.sources.verify()
        if self.fail_verify:raise RuntimeError('owned Context postcheck secondary')
def context():return _ACTIVE
'''
API_SOURCE='''import hashlib,sys
CALLS=[]
def validate_case(profile,repo,before):
    CALLS.append('validate')
    return {'python':sys.executable,'context_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),
            'full_before_sha256':hashlib.sha256(before.read_bytes()).hexdigest()}
def recheck_case(live):CALLS.append('recheck')
'''
PROVIDER_SOURCE='''from contextlib import contextmanager
EVENTS=[]
class _NativeOwner:
    def __init__(self,ctx):
        self.ctx=ctx
        self.members={'api':api,'live':live,'retained':retained,'node':node}
def admit(ctx,executable):raise RuntimeError('explicit provider admission double; never actual runtime proof')
@contextmanager
def _private_helpers(ctx):
    EVENTS.append('enter')
    try:yield api,live,_NativeOwner(ctx)
    except BaseException as error:
        error.__som_native_payload_cleanup_secondary__=(RuntimeError('provider owned close secondary'),)
        raise
    finally:EVENTS.append('exit')
'''
TARGET_SOURCE='''import json,os,sys
from pathlib import Path
def main():
    frames=[];frame=sys._getframe()
    while frame is not None:
        if frame.f_code is core_entry.__code__:frames.append(frame.f_code.co_qualname)
        frame=frame.f_back
    Path(sys.argv[1]).write_text(json.dumps({'name':__name__,'argv':sys.argv,'pid':os.getpid(),'entry_depth':len(frames)}))
    mode=sys.argv[3] if len(sys.argv)>3 else 'plain'
    if mode=='primary':raise RuntimeError('actual target primary')
    if mode=='mutate':Path(sys.argv[4]).write_bytes(b'# changed actual tracked API source\\n')
    return int(sys.argv[2])
if __name__=='__main__':raise SystemExit(main())
'''

class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self,fixture):
        super().__init__(fixture);self.revision=fixture.revision
        self.admission.update(helper_revision=self.revision,candidate_revision=self.revision)

class EntryFixture(protocol.Fixture):
    def __init__(self,case):
        key='SOM_FULL_PRODUCT_CONTEXT';present=key in os.environ;previous=os.environ.get(key)
        def restore_environment():
            if present:os.environ[key]=previous
            else:os.environ.pop(key,None)
        # Registered before the Core stack cleanup, so owner closes first.
        case.addCleanup(restore_environment)
        caller=('import sys\nfrom _som_native_active_controls_binding import controls\n'
                'raise SystemExit(controls.execute_entry("entry.qa.m6-native",sys.argv[1:]))\n')
        with mock.patch.object(protocol,'ProtocolTransfer',GitTransfer):super().__init__(case,caller,entry=True)
        self.context=self.domain.load_known('runtime.context',self.domain.prepare('runtime.context'),None)
        self.data=self.root/'data';self.data.mkdir();self.site=self.data/'site';self.site.mkdir()
        self.context_path=self.data/'context.json';self.context_path.write_bytes(b'{"explicit_context_double":true}')
        self.profile=self.data/'profile.json';self.profile.write_bytes(b'{"profile_data_double":true}')
        self.before=self.data/'before.json';self.before.write_bytes(b'{"before_data_double":true}')
        self.ctx=self.context.Context(self.root,self.revision,self.context_path,self.site,self.controls,self.transfer.admission)
        case.addCleanup(self.ctx.qa_import_controls.close)
        self.context._ACTIVE=self.ctx
        os.environ[key]=str(self.context_path)
        self.initial=self.seeds['_som_native_trusted_bootstrap_entry']['module']
        self.sequence=0

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,CONTEXT_SOURCE,module='_som_m6_entry_fixture_context')
        for role2,path2,body in [('fixture.m6.api',API,API_SOURCE),('fixture.m6.retained',RETAINED,'def sealed_files(repo):return {}\n'),
                                 ('fixture.m6.live',LIVE,'VALUE="live source double"\n'),('fixture.m6.node',NODE,'VALUE="node source double"\n')]:
            super().add_role(role2,path2,body,module='_som_'+role2.replace('.','_'))
        provider_bindings={name:{'kind':'role-module','role':'fixture.m6.'+name}
                           for name in ['api','retained','live','node']}
        super().add_role('m6.api.provider',PROVIDER,PROVIDER_SOURCE,module='_som_m6_entry_fixture_provider',bindings=provider_bindings)
        bindings={'__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
            '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'},
            '__som_native_provider__':{'kind':'role-module','role':'m6.api.provider'},
            '__som_native_provider_private_helpers__':{'kind':'role-export','role':'m6.api.provider','attribute':'_private_helpers'},
            '__som_native_provider_admit__':{'kind':'role-export','role':'m6.api.provider','attribute':'admit'},
            '__som_native_NativeOwner__':{'kind':'role-export','role':'m6.api.provider','attribute':'_NativeOwner'}}
        super().add_role('entry.qa.m6-native',ENTRY,checked(ENTRY),kind='entry',module='__main__',
            callers=[{'role':'caller','function':'<module>'}],bindings=bindings)
        super().add_role('fixture.m6.entry-probe',ENTRY,checked(ENTRY),module='_som_m6_fixture_probe',
            bindings={'__som_native_active_controls__':{'kind':'active-controls'}})
        for path2,role2 in TARGETS.items():
            body=TARGET_SOURCE;caller_rows=[{'role':'entry.qa.m6-native','function':'_native_execute'}]
            target_bindings={'core_entry':{'kind':'role-export','role':'bootstrap.core','attribute':'_Domain.execute_entry'}}
            if path2==M6+'record_http.py':
                body=('import sys\nfrom _som_native_active_controls_binding import controls\n'
                      'raise SystemExit(controls.execute_entry("entry.m6.python-baseline",sys.argv[1:]))\n')
                target_bindings={'__som_native_active_controls__':{'kind':'active-controls'}}
            if path2=='som-rs/scripts/typescript_output_acceptance/check_python_baseline.py':
                caller_rows.append({'role':'entry.m6.record-http','function':'<module>'})
            super().add_role(role2,path2,body,kind='entry',module='__main__',callers=caller_rows,bindings=target_bindings)
        extra=BASE+'synthetic_extra_admitted.py';super().add_role('fixture.extra-admitted',extra,'raise RuntimeError("must not run")\n')
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],env=env,
            check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        git('init','--quiet');git('add','--','.')
        git('-c','user.name=M6 entry protocol','-c','user.email=fixture@example.invalid','-c','core.hooksPath=/dev/null',
            'commit','--quiet','-m','source protocol doubles only')
        self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def invoke(self,script=None,*,code=0,scope='step',mode='plain',helper=None):
        self.sequence+=1;trace=self.data/f'trace-{self.sequence}.json';post=self.data/f'post-{self.sequence}.json'
        argv=['--repo',str(self.root),'--product-context',str(self.context_path),'--helper-revision',helper or self.revision,
              '--candidate-revision',self.revision,'--api-profile',str(self.profile),'--full-before',str(self.before),
              '--postcheck',str(post),'--script',script or next(iter(TARGETS)),'--scope',scope,'--',str(trace),str(code),mode]
        if mode=='mutate':argv.append(str(self.root/API))
        result=self.initial._trusted_run(self.controls,'caller',argv)
        return result,json.loads(post.read_bytes()),json.loads(trace.read_bytes()) if trace.exists() else None

    def provider(self):return self.domain.records['m6.api.provider'].module

class NativeM6EntryTests(unittest.TestCase):
    def test_actual_third_entry_keeps_original_main_argv_exit_and_restores_owner(self):
        f=EntryFixture(self);main,argv=sys.modules['__main__'],sys.argv;handlers={n:signal.getsignal(n) for n in (signal.SIGTERM,signal.SIGHUP)}
        code,report,target=f.invoke(code=7)
        self.assertEqual(code,7);self.assertEqual(report['entry_exit_code'],7)
        self.assertTrue(report['source_postcheck']);self.assertEqual(target['name'],'__main__')
        self.assertEqual(target['entry_depth'],3);self.assertEqual(target['argv'][0],str(f.root/next(iter(TARGETS))))
        self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv)
        self.assertEqual(f.domain.entry_depth,0);self.assertEqual(f.domain.suspended_mains,[])
        self.assertNotIn('entry.qa.m6-native',f.domain.records)
        self.assertNotIn('_som_m6_checked_router',sys.modules)
        self.assertEqual({n:signal.getsignal(n) for n in handlers},handlers)
        self.assertEqual(f.provider().EVENTS,['enter','exit']);self.assertIsNone(f.domain.group)
        self.assertFalse(report['product_acceptance_passed'])
        again,second,_=f.invoke()
        self.assertEqual(again,0);self.assertTrue(second['source_postcheck'])
        self.assertNotIn('entry.qa.m6-native',f.domain.records)
        self.assertEqual(f.provider().EVENTS,['enter','exit','enter','exit'])

    def test_unique_fourth_entry_has_no_invented_numeric_depth_cap(self):
        f=EntryFixture(self)
        code,report,target=f.invoke(M6+'record_http.py')
        self.assertEqual(code,0);self.assertEqual(target['entry_depth'],4)
        self.assertTrue(report['source_postcheck']);self.assertIsNone(f.domain.group)
        self.assertNotIn('entry.m6.python-baseline',f.domain.records)
        self.assertEqual(len(report['executed']),1)
        self.assertIn('nested wrapper manifests',report['execution_scope'])

    def test_old_graph_helper_APIs_refuse_without_foreign_attribute_access(self):
        f=EntryFixture(self);calls=[]
        probe=f.domain.load_known('fixture.m6.entry-probe',f.domain.prepare('fixture.m6.entry-probe'),None)
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('legacy authority hook')
        with self.assertRaisesRegex(ValueError,'not native authority'):
            probe.admit_live_inputs(Foreign(),None,None,None,None)
        with self.assertRaisesRegex(ValueError,'not native authority'):
            probe.case_postcheck(None,None,None,Foreign(),None,None)
        self.assertEqual(calls,[])

    def test_admitted_source_outside_exact16_table_cannot_execute(self):
        f=EntryFixture(self)
        code,report,target=f.invoke(BASE+'synthetic_extra_admitted.py')
        self.assertEqual(code,1);self.assertIsNone(target)
        self.assertIn('outside exact16 entry table',report['error'])
        self.assertEqual(report['executed'],[]);self.assertFalse(report['entry_exit_code'])
        self.assertEqual(f.domain.entry_depth,0)

    def test_wrong_revision_is_exact_bootstrap_guard_before_provider_or_target(self):
        f=EntryFixture(self)
        with self.assertRaisesRegex(ValueError,'same exact helper and candidate required'):
            f.invoke(helper='a'*40)
        if 'm6.api.provider' in f.domain.records:self.assertEqual(f.provider().EVENTS,[])
        self.assertEqual(f.domain.entry_depth,0);self.assertEqual(f.domain.suspended_mains,[])

    def test_foreign_active_context_cannot_run_hooks_or_legacy_bootstrap(self):
        f=EntryFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign Context hook')
        f.context._ACTIVE=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'actual active native Context required'):f.invoke()
        finally:f.context._ACTIVE=f.ctx
        self.assertEqual(calls,[]);self.assertEqual(f.domain.entry_depth,0)

    def test_preloaded_router_is_preserved_and_never_used_as_authority(self):
        f=EntryFixture(self);name='_som_m6_checked_router';foreign=ModuleType(name);sys.modules[name]=foreign
        try:
            with self.assertRaisesRegex(ValueError,'preloaded M6 router refused'):f.invoke()
            self.assertIs(sys.modules[name],foreign)
        finally:
            if sys.modules.get(name) is foreign:del sys.modules[name]
        self.assertEqual(f.domain.entry_depth,0)

    def test_primary_target_trace_and_all_scope_secondaries_survive_cleanup(self):
        f=EntryFixture(self);f.ctx.fail_verify=True;main,argv=sys.modules['__main__'],sys.argv
        code,report,target=f.invoke(mode='primary')
        self.assertEqual(code,1);self.assertIn('actual target primary',report['error'])
        self.assertIn('main',report['error_traceback']);self.assertIsNone(report['entry_exit_code'])
        self.assertFalse(report['source_postcheck'])
        self.assertTrue(any('scope-postcheck'==row['phase'] for row in report['secondary_errors']))
        self.assertTrue(any('provider-attached' in row['phase'] for row in report['secondary_errors']))
        self.assertEqual(f.provider().EVENTS,['enter','exit'])
        self.assertIs(sys.modules['__main__'],main);self.assertIs(sys.argv,argv);self.assertEqual(f.domain.entry_depth,0)

    def test_actual_tracked_source_change_after_target_refuses_postcheck(self):
        f=EntryFixture(self);path=f.root/API;raw=path.read_bytes()
        try:
            code,report,target=f.invoke(mode='mutate')
            self.assertEqual(code,1);self.assertFalse(report['source_postcheck'])
            self.assertIn('source differs from committed Git bytes',report['error'])
            self.assertTrue(report['secondary_errors']);self.assertEqual(f.provider().EVENTS,['enter','exit'])
        finally:path.write_bytes(raw)

    def test_case_scope_keeps_original_case_recheck_without_full_step_verify(self):
        f=EntryFixture(self)
        code,report,target=f.invoke(scope='case')
        self.assertEqual(code,0);self.assertTrue(report['source_postcheck'])
        self.assertEqual(f.ctx.verify_calls,0)
        api=f.domain.records['fixture.m6.api'].module
        self.assertEqual(api.CALLS.count('validate'),1);self.assertGreaterEqual(api.CALLS.count('recheck'),3)
        self.assertEqual(f.provider().EVENTS,['enter','exit']);self.assertFalse(f.domain.closed)

if __name__=='__main__':unittest.main()
