"""GitHub-only checker target ledger protocol tests.

Real Core and exact production wrapper/raw capture run. Runtime Context and
SourceBytes, admission transfer, digest and selected target bodies are explicit
source/data doubles on physical synthetic paths. No real Context factory,
checker business gate, runtime, tool, service or product is qualified.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
protocol_path=Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol=ModuleType('_som_ts_checker_targets_core_protocol')
protocol.__file__,protocol.__cached__,protocol.__package__=str(protocol_path),None,''
exec(compile(protocol_path.read_bytes(),str(protocol_path),'exec',dont_inherit=True),protocol.__dict__)
BASE='som-rs/scripts/hosted_readiness/'
SELF=BASE+'typescript_regression_checker.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
RAW=BASE+'typescript_regression_raw_capture.py'
DIGEST=BASE+'typescript_native_digests.py'
HOST=BASE+'typescript_native_host.py'
SCHEMA=BASE+'typescript_regression_schema_capture.py'
HOST_ROLE='ctx.typescript-regression-native-host'
TS_TOOL='som-rs/som-targets/qa/check_ts_products.py'
ENTRY='entry.qa.typescript-wrapper'
TABLE={'som-rs/scripts/hosted_readiness/typescript_regression_rebase.py': {'module': '_som_ts_native_original_typescript002_refusal_rebase',
                                                                     'role': 'ts.checker.original-typescript002-refusal-rebase'},
 'som-rs/scripts/react_output_acceptance/check_typescript_baseline.py': {'module': '_som_ts_native_original_typescript002_baseline',
                                                                         'role': 'ts.checker.original-typescript002-baseline'},
 'som-rs/scripts/rust_output_acceptance/check_cli.py': {'module': '_som_ts_native_original_cli',
                                                        'role': 'ts.checker.original-cli'},
 'som-rs/scripts/rust_output_acceptance/check_rust_unchanged.py': {'module': '_som_ts_native_original_rust_unchanged',
                                                                   'role': 'ts.checker.original-rust-unchanged'},
 'som-rs/scripts/rust_output_acceptance/python_replay.py': {'module': '_som_ts_native_original_python_replay',
                                                            'role': 'ts.checker.original-python-replay'},
 'som-rs/scripts/rust_output_acceptance/run_acceptance.py': {'module': '_som_ts_native_original_rust_acceptance',
                                                             'role': 'ts.checker.original-rust-acceptance'},
 'som-rs/scripts/typescript_output_acceptance/check_bundles.py': {'module': '_som_ts_native_original_bundles',
                                                                  'role': 'ts.checker.original-bundles'},
 'som-rs/scripts/typescript_output_acceptance/check_python_baseline.py': {'module': '_som_ts_native_original_python_baseline',
                                                                          'role': 'ts.checker.original-python-baseline'},
 'som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py': {'module': '_som_ts_native_original_rt_diagnostics',
                                                                         'role': 'ts.checker.original-rt-diagnostics'},
 'som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py': {'module': '_som_ts_native_original_rust_baseline',
                                                                        'role': 'ts.checker.original-rust-baseline'},
 'som-rs/scripts/typescript_output_acceptance/check_target.py': {'module': '_som_ts_native_original_target',
                                                                 'role': 'ts.checker.original-target'},
 'som-rs/som-targets/qa/check_rust_products.py': {'module': '_som_ts_native_original_rust_products',
                                                  'role': 'ts.checker.original-rust-products'},
 'som-rs/som-targets/qa/check_ts_products.py': {'module': '_som_ts_native_original_ts_products',
                                                'role': 'ts.checker.original-ts-products'},
 'som-rs/tests/rust-output-v1/check_python_controls.py': {'module': '_som_ts_native_original_rust_python_controls',
                                                          'role': 'ts.checker.original-rust-python-controls'},
 'som-rs/tests/rust-output-v1/validate_cases.py': {'module': '_som_ts_native_original_validate_cases',
                                                   'role': 'ts.checker.original-validate-cases'},
 'som-rs/tests/typescript-output-v1/check_python_controls.py': {'module': '_som_ts_native_original_ts_python_controls',
                                                                'role': 'ts.checker.original-ts-python-controls'}}
TOOL='som-rs/scripts/typescript_output_acceptance/check_rt_diagnostics.py'
RUNTIME_SOURCE='''from pathlib import Path
_ACTIVE=None
class SourceBytes:
    def __init__(self,root,revision):self.root,self.revision,self.pins=root,revision,{}
    def read(self,path):
        raw=(self.root/path).read_bytes()
        import hashlib
        self.pins[path]=hashlib.sha256(raw).hexdigest()
        return raw
    def verify(self):return None
class Context:
    def __init__(self,root,allowed):
        self.repo,self.work=root,root/'work'
        self.work.mkdir()
        self.helper_revision=self.candidate_revision='a'*40
        self.qa_mode='native-qa-v1'
        self.qa_source_authority={'source_authority_active':True,'admitted_repository_python_sources':allowed}
        self.sources=SourceBytes(root,'a'*40)
        self.cache,self.data={},{}
        self.verified=0
    def _native_source_controls(self):
        from _som_native_active_context_binding import controls
        controls.verify_installed()
        return controls
    def module(self,key,path):
        controls=self._native_source_controls()
        return controls.load_module(controls.context_keys[key])
    def verify(self):
        self.verified+=1
        import builtins
        if hasattr(self,'protocol_host_target'):
            assert self.protocol_host_target.__som_protocol_host_live__ is True
            self.host_events.append('context')
        if getattr(builtins,'__som_ts_checker_context_failure__',None) is not None:
            raise builtins.__som_ts_checker_context_failure__
    def context_owner(self):return self
def context():return _ACTIVE
'''
TARGET_SOURCE='''from _som_native_active_controls_binding import controls
import builtins
controls.verify_installed()
BODY_REACHED=True
def main():
    controls.verify_installed()
    if getattr(builtins,'__som_ts_checker_first__',None) is not None:
        raise builtins.__som_ts_checker_first__
    return 0
'''

TS_TARGET_SOURCE=TARGET_SOURCE+"""from types import ModuleType
# Product/SQL roles are explicit data doubles; no business assertion is run.
ts_suite=ModuleType('_protocol_ts_suite')
rust_products=ModuleType('_protocol_rust_products')
def http_send(*args,**kwargs):return (200,'application/json',b'{}')
ts_suite.http_send=http_send
rust_products.psycopg=None
rust_products.schema=None
"""
HOST_SOURCE="""import builtins
import sys
def configure(ctx,checker):
    ctx.protocol_host_target=checker
    ctx.host_events=['configure']
    checker.__som_protocol_host_live__=True
    if getattr(builtins,'__som_ts_checker_configure_failure__',None) is not None:
        raise builtins.__som_ts_checker_configure_failure__
    return {'protocol_only':True,'product_acceptance_passed':False}
def close(ctx,checker):
    if not hasattr(ctx,'protocol_host_target'):return None
    assert ctx.protocol_host_target is checker and ctx.verified==1
    ctx.host_events.append('close')
    checker.__som_protocol_host_live__=False
    if getattr(builtins,'__som_ts_checker_close_failure__',None) is not None:
        raise builtins.__som_ts_checker_close_failure__
"""


class Transfer(protocol.ProtocolTransfer):
    def verify_entry_source(self,path):self.fixture.case.assertEqual(path,SELF)


class CheckerFixture(protocol.Fixture):
    def __init__(self,case):
        with mock.patch.object(protocol,'ProtocolTransfer',Transfer):super().__init__(case)
        self.policy['imports']=[{'caller':ENTRY,'name':'full_product_context','level':0,
            'fromlist':[],'result':'runtime.context','from_roles':{}}]
        self.policy['static_edges']=[{'caller':SELF,'import':'full_product_context','source':CONTEXT}]
        self.policy['bridges']=[{'kind':'context-module','role':'runtime.context','function':'Context.module'}]
        self.policy['context_keys']={key:'test.checker.raw' for key in ('typescript_regression_raw_capture',)}
        self.policy['context_keys']['typescript_regression_native_host']=HOST_ROLE
        self.policy['context_keys']['typescript_regression_schema_capture']='test.checker.schema'
        for key in ('typescript_regression_preloaded_digests','typescript_regression_native_bundle_digests'):
            self.policy['context_keys'][key]='test.checker.digests'
        self.context_module=self.domain.load_known('runtime.context',self.domain.prepare('runtime.context'),None)
        self.ctx=self.context_module.Context(self.root,list(self.raw)+['admitted_unknown.py'])
        self.context_module._ACTIVE=self.ctx
        self.trusted=self.seeds['_som_native_trusted_bootstrap_entry']['module']._trusted_run

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,RUNTIME_SOURCE,module='_som_checker_fixture_context',
            bindings={'__som_native_active_controls__':{'kind':'active-controls'}},
            callers=[{'role':ENTRY,'function':'main'}])
        bindings={'__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_ContextModule__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
            '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'},
            '__som_native_CaptureRows__':{'kind':'role-export','role':'test.checker.raw','attribute':'CaptureRows'},
            '__som_native_CaptureOutcome__':{'kind':'role-export','role':'test.checker.raw','attribute':'CaptureOutcome'}}
        super().add_role(ENTRY,SELF,(protocol.REPO/SELF).read_bytes(),kind='entry',module='__main__',
            bindings=copy.deepcopy(bindings),callers=[{'role':'bootstrap.entry','function':'_trusted_run'}])
        super().add_role('test.checker.raw',RAW,(protocol.REPO/RAW).read_bytes(),
            module='_som_checker_actual_raw_capture',callers=[{'role':ENTRY,'function':'main'}])
        super().add_role('test.checker.digests',DIGEST,'def authorities(ctx,target): return (None,None,None,{})\n',
            module='_som_checker_digest_protocol_double',callers=[{'role':ENTRY,'function':'main'},
                {'role':ENTRY,'function':'native_bundle_expectations'}])
        super().add_role(HOST_ROLE,HOST,HOST_SOURCE,module='_som_full_typescript_regression_native_host',
            callers=[{'role':ENTRY,'function':'main'}])
        super().add_role('test.checker.schema',SCHEMA,'def install(target,root): return []\n',
            module='_som_checker_schema_protocol_double',callers=[{'role':ENTRY,'function':'main'}])
        for path,data in TABLE.items():
            super().add_role(data['role'],path,TS_TARGET_SOURCE if path==TS_TOOL else TARGET_SOURCE,module=data['module'],
                bindings={'__som_native_active_controls__':{'kind':'active-controls'}},
                callers=[{'role':ENTRY,'function':'_target'}])
        return row

    def execute(self,tool=TOOL):
        return self.trusted(self.controls,ENTRY,['--tool',tool,'--capture-root',str(self.ctx.work/'capture')])


class NativeTsCheckerTargetTests(unittest.TestCase):
    def test_registered_target_body_has_ledger_and_stays_Core_owned_after_postchecks(self):
        f=CheckerFixture(self)
        self.assertEqual(f.execute(),0)
        data=TABLE[TOOL];record=f.domain.records[data['role']];target=record.module
        self.assertTrue(target.BODY_REACHED)
        self.assertIs(sys.modules[data['module']],target)
        self.assertEqual(target.__file__,str(f.root/TOOL))
        self.assertIs(target.main.__globals__,record.namespace)
        self.assertTrue(any(target.main.__code__ is code for code in record.codes.values()))
        self.assertEqual(f.ctx.verified,1)
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes())
        self.assertTrue(post['entry_executed']);self.assertTrue(post['postcheck_complete'])
        self.assertFalse(post['product_acceptance_passed'])
        self.assertNotIn('_som_typescript_original_checker_entry',sys.modules)
        f.finish()
        self.assertNotIn(data['module'],sys.modules)

    def test_admitted_unknown_python_refuses_before_source_read_or_target_body(self):
        f=CheckerFixture(self)
        with self.assertRaisesRegex(ValueError,'unlisted native original checker source'):
            f.execute('admitted_unknown.py')
        self.assertNotIn('admitted_unknown.py',f.ctx.sources.pins)
        self.assertFalse(any(data['role'] in f.domain.records for data in TABLE.values()))

    def test_foreign_preloaded_namespace_is_preserved_and_refused_before_body(self):
        f=CheckerFixture(self);data=TABLE[TOOL];foreign=ModuleType(data['module'])
        sys.modules[data['module']]=foreign
        try:
            with self.assertRaisesRegex(ValueError,'preloaded original TypeScript checker refused'):
                f.execute()
            self.assertIs(sys.modules[data['module']],foreign)
            self.assertNotIn(data['role'],f.domain.records)
        finally:sys.modules.pop(data['module'],None)

    def test_fake_Context_refuses_without_attribute_hooks_before_target_body(self):
        f=CheckerFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):
                calls.append(name);raise AssertionError('foreign Context hook executed')
        actual=f.context_module._ACTIVE;f.context_module._ACTIVE=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'actual source-owned native Context instance required'):
                f.execute()
            self.assertEqual(calls,[])
            self.assertFalse(any(data['role'] in f.domain.records for data in TABLE.values()))
        finally:f.context_module._ACTIVE=actual

    def test_target_original_error_object_and_traceback_survive_actual_postcheck_write_failure(self):
        import builtins
        f=CheckerFixture(self);first=RuntimeError('actual registered target main error');later=OSError('postcheck writer refused')
        original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='checker-postcheck.json':raise later
            return original(path,*args,**kwargs)
        with mock.patch.object(builtins,'__som_ts_checker_first__',first,create=True),mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(RuntimeError)as caught:f.execute()
        self.assertIs(caught.exception,first)
        self.assertIn(TOOL,first.__som_typescript_failure_record__['traceback'])
        self.assertEqual(first.__som_typescript_secondary_failures__[-1]['role'],'checker_postcheck_evidence')
        self.assertIs(sys.modules[TABLE[TOOL]['module']],f.domain.records[TABLE[TOOL]['role']].module)

    def test_postcheck_writer_failure_after_main_success_is_not_passed(self):
        f=CheckerFixture(self);later=OSError('postcheck first failure');original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='checker-postcheck.json':raise later
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(OSError)as caught:f.execute()
        self.assertIs(caught.exception,later)

    def test_host_success_closes_only_after_Context_and_keeps_target_owner(self):
        f=CheckerFixture(self)
        self.assertEqual(f.execute(TS_TOOL),0)
        target=f.domain.records[TABLE[TS_TOOL]['role']].module
        self.assertEqual(f.ctx.host_events,['configure','context','close'])
        self.assertFalse(target.__som_protocol_host_live__)
        self.assertIs(sys.modules[TABLE[TS_TOOL]['module']],target)
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes())
        self.assertTrue(post['postcheck_complete'])
        self.assertFalse(post['product_acceptance_passed'])

    def test_partial_host_configure_and_context_and_close_failures_preserve_first_exception(self):
        import builtins
        f=CheckerFixture(self);first=RuntimeError('configure original');context_error=ValueError('context secondary')
        close_error=OSError('host close secondary')
        with mock.patch.object(builtins,'__som_ts_checker_configure_failure__',first,create=True), \
                mock.patch.object(builtins,'__som_ts_checker_context_failure__',context_error,create=True), \
                mock.patch.object(builtins,'__som_ts_checker_close_failure__',close_error,create=True):
            with self.assertRaises(RuntimeError)as caught:f.execute(TS_TOOL)
        self.assertIs(caught.exception,first)
        self.assertEqual(f.ctx.host_events,['configure','context','close'])
        self.assertFalse(f.ctx.protocol_host_target.__som_protocol_host_live__)
        self.assertEqual([row['role']for row in first.__som_typescript_secondary_failures__],['context','native_host'])
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes())
        self.assertFalse(post['postcheck_complete'])
        self.assertIn(HOST,first.__som_typescript_failure_record__['traceback'])
        self.assertIs(sys.modules[TABLE[TS_TOOL]['module']],f.ctx.protocol_host_target)


if __name__=='__main__':unittest.main()
