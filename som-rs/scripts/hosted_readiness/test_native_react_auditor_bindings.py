"""Whole c4 auditor/Core import protocol tests, only on GitHub.

Actual Core and all seven source modules execute. Native sibling imports are
not run. Actual frozen16 roots and the finite schema are checked. Full _Policy
is still pending. Source transfer, admission,
Context/profile/data and the constructor are explicit synthetic seams. These
fixtures do not qualify M6 runtime, credentials, real audit data or products.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
from types import ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub Linux -I -S -B required')

HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
BASE='som-rs/scripts/hosted_readiness/'
AUDIT='som-rs/scripts/react_output_acceptance/audit/'
NAMES=('render_rules','refusal_rules','api_raw','linux_install_map','linux_input_raw','baseline_raw','audit130')
PINS={'som-rs/scripts/react_output_acceptance/audit/api_raw.py': '25ff69a6c37623d6ed179d63f39eb04ee2c62e4c7697d0677e7d2fac1ad382f5', 'som-rs/scripts/react_output_acceptance/audit/audit130.py': '7b20271cbc7612594a88c81e9c94f0e41efd6bafd438463df60f55ddce7b5d1e', 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py': 'a4778091a8434aa12fcfc595a8a9040783e1101744732cbfae26a9e7355a7eeb', 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py': '74cb93117f979865233766041dac55a830b5184810a04ea6d3725a87d85726d9', 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py': 'f73ee9e9e36f0471ab67bab808ee86c71cf4fbe45641eaa94cdff647c6e60c28', 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py': '53592f0dde5646aed2a1d67952db6e495987e3a47a84b7647c3fd97b577044a8', 'som-rs/scripts/react_output_acceptance/audit/render_rules.py': '1121918d87d6cd77e4bcc024424f12fd86722fc9e2509fa7010dda84ff732aaf', 'som-rs/scripts/hosted_readiness/native_active_controls.py': 'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32', 'som-rs/scripts/hosted_readiness/test_native_active_controls.py': 'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d', 'som-rs/scripts/hosted_readiness/native_qa_authority.py': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1', 'som-rs/scripts/hosted_readiness/native_active_roles.schema.json': '29711f2ac4a3a8f1afccbf21625238ff28f63214fde1f2bf9f2615cf40ca7942', 'som-rs/scripts/hosted_readiness/native_active_policy_design_fixture.json': '35bbd6403930ad790d6a6bccac176138f1fd541f5b07a103479c8546f146a875'}
ROLE_TABLE={'m6.runtime.audit.api-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.api-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_api_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/api_raw.py'}, 'm6.runtime.audit.audit130': {'bindings': {'ApiRawError': {'attribute': 'ApiRawError', 'kind': 'role-export', 'role': 'm6.runtime.audit.api-raw'}, 'BaselineRawError': {'attribute': 'BaselineRawError', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'LinuxInputError': {'attribute': 'LinuxInputError', 'kind': 'role-export', 'role': 'm6.runtime.audit.linux-input-raw'}, '__som_native_active_controls__': {'kind': 'active-controls'}, 'audit_linux_inputs': {'attribute': 'audit', 'kind': 'role-export', 'role': 'm6.runtime.audit.linux-input-raw'}, 'render_files': {'attribute': 'render_files', 'kind': 'role-export', 'role': 'm6.runtime.audit.render-rules'}, 'request_for': {'attribute': 'request_for', 'kind': 'role-export', 'role': 'm6.runtime.audit.refusal-rules'}, 'validate_api': {'attribute': 'validate', 'kind': 'role-export', 'role': 'm6.runtime.audit.api-raw'}, 'validate_controls': {'attribute': 'validate_controls', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'validate_python': {'attribute': 'validate_python', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'validate_rust': {'attribute': 'validate_rust', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'verify_location': {'attribute': 'verify_location', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.api-raw', 'm6.runtime.audit.audit130', 'm6.runtime.audit.baseline-raw', 'm6.runtime.audit.linux-input-raw', 'm6.runtime.audit.linux-install-map', 'm6.runtime.audit.refusal-rules', 'm6.runtime.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_audit130', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/audit130.py'}, 'm6.runtime.audit.baseline-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.baseline-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_baseline_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py'}, 'm6.runtime.audit.linux-input-raw': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}, 'mapping': {'kind': 'role-module', 'role': 'm6.runtime.audit.linux-install-map'}}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.linux-input-raw'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.linux-input-raw', 'm6.runtime.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_linux_input_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py'}, 'm6.runtime.audit.linux-install-map': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.linux-input-raw'}], 'closure': ['m6.runtime.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_linux_install_map', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py'}, 'm6.runtime.audit.refusal-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.refusal-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_refusal_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py'}, 'm6.runtime.audit.render-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_render_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/render_rules.py'}, 'm6.verify.audit.api-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.api-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_api_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/api_raw.py'}, 'm6.verify.audit.audit130': {'bindings': {'ApiRawError': {'attribute': 'ApiRawError', 'kind': 'role-export', 'role': 'm6.verify.audit.api-raw'}, 'BaselineRawError': {'attribute': 'BaselineRawError', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'LinuxInputError': {'attribute': 'LinuxInputError', 'kind': 'role-export', 'role': 'm6.verify.audit.linux-input-raw'}, '__som_native_active_controls__': {'kind': 'active-controls'}, 'audit_linux_inputs': {'attribute': 'audit', 'kind': 'role-export', 'role': 'm6.verify.audit.linux-input-raw'}, 'render_files': {'attribute': 'render_files', 'kind': 'role-export', 'role': 'm6.verify.audit.render-rules'}, 'request_for': {'attribute': 'request_for', 'kind': 'role-export', 'role': 'm6.verify.audit.refusal-rules'}, 'validate_api': {'attribute': 'validate', 'kind': 'role-export', 'role': 'm6.verify.audit.api-raw'}, 'validate_controls': {'attribute': 'validate_controls', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'validate_python': {'attribute': 'validate_python', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'validate_rust': {'attribute': 'validate_rust', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'verify_location': {'attribute': 'verify_location', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.api-raw', 'm6.verify.audit.audit130', 'm6.verify.audit.baseline-raw', 'm6.verify.audit.linux-input-raw', 'm6.verify.audit.linux-install-map', 'm6.verify.audit.refusal-rules', 'm6.verify.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_audit130', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/audit130.py'}, 'm6.verify.audit.baseline-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.baseline-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_baseline_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py'}, 'm6.verify.audit.linux-input-raw': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}, 'mapping': {'kind': 'role-module', 'role': 'm6.verify.audit.linux-install-map'}}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.linux-input-raw'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.linux-input-raw', 'm6.verify.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_linux_input_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py'}, 'm6.verify.audit.linux-install-map': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.linux-input-raw'}], 'closure': ['m6.verify.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_linux_install_map', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py'}, 'm6.verify.audit.refusal-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.refusal-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_refusal_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py'}, 'm6.verify.audit.render-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_render_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/render_rules.py'}}

def checked(path):
    raw=(REPO/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINS[path]:
        raise RuntimeError('exact auditor protocol source differs: '+path)
    return raw

for path in PINS:checked(path)
protocol=ModuleType('_som_native_auditor_protocol_fixture')
protocol.__file__,protocol.__package__=str(HERE/'test_native_active_controls.py'),''
sys.modules[protocol.__name__]=protocol
exec(compile(checked(BASE+'test_native_active_controls.py'),protocol.__file__,'exec',dont_inherit=True),protocol.__dict__)

class AuditorFixture(protocol.Fixture):
    def __init__(self,case):
        caller=('def run(controls,scope="runtime"):\n'
                '    return controls.load_module({"runtime":"m6.runtime.audit.audit130",'
                '"verify":"m6.verify.audit.audit130"}[scope])\n')
        super().__init__(case,caller)
        # Direct _Domain construction is the explicit authority/constructor
        # seam. It is not full _Policy or genuine Context admission.
        self.policy['imports']=[]
        for role,row in ROLE_TABLE.items():self.rows[role]['closure']=list(row['closure'])
        design=json.loads(checked(BASE+'native_active_policy_design_fixture.json'))['design']
        self.search_roots=design['search_roots']
        self.policy['static_edges']=self.core.static_edges(self.transfer.read,self.policy['sources'],
            self.transfer.source_paths,self.search_roots)

    def add_role(self,role,path,raw,**kwargs):
        result=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return result
        for target,prototype in ROLE_TABLE.items():
            row=json.loads(json.dumps(prototype))
            callers=row['callers']
            if target.endswith('audit130'):callers.append({'role':'caller','function':'run'})
            super().add_role(target,row['source'],checked(row['source']),kind=row['kind'],
                module=row['module'],callers=callers,group=row['group'],bindings=row['bindings'])
        return result

    def modules(self,scope):
        return {name:self.domain.records['m6.'+scope+'.audit.'+name.replace('_','-')].module for name in NAMES}

class NativeAuditorRouteTests(unittest.TestCase):
    def test_whole_seven_modules_original_reader_schema_errors_and_import_identities(self):
        f=AuditorFixture(self);path=sys.path;members=tuple(path)
        a=f.run();modules=f.modules('runtime')
        self.assertEqual(len(modules),7)
        self.assertIs(a,modules['audit130'])
        for name,module in modules.items():
            role='m6.runtime.audit.'+name.replace('_','-')
            record=f.domain.records[role]
            self.assertIs(record.module,module)
            self.assertIs(record.namespace,module.__dict__)
            self.assertEqual(module.__file__,str(f.root/AUDIT/(name+'.py')))
            self.assertEqual(module.__som_raw_sha256__,PINS[AUDIT+name+'.py'])
            self.assertEqual((f.root/AUDIT/(name+'.py')).read_bytes(),checked(AUDIT+name+'.py'))
            self.assertIs(sys.modules[module.__name__],module)
            for function in record.owned_exports.values():
                self.assertIn(id(function.__code__),f.domain.codes)
                self.assertTrue(any(code is function.__code__ and r.namespace is function.__globals__
                    for code,r in f.domain.codes[id(function.__code__)]))
        self.assertIs(a.render_files,modules['render_rules'].render_files)
        self.assertIs(a.request_for,modules['refusal_rules'].request_for)
        self.assertIs(a.validate_api,modules['api_raw'].validate)
        self.assertIs(a.ApiRawError,modules['api_raw'].ApiRawError)
        self.assertIs(a.audit_linux_inputs,modules['linux_input_raw'].audit)
        self.assertIs(a.LinuxInputError,modules['linux_input_raw'].LinuxInputError)
        self.assertIs(a.validate_python,modules['baseline_raw'].validate_python)
        self.assertIs(a.validate_rust,modules['baseline_raw'].validate_rust)
        self.assertIs(a.validate_controls,modules['baseline_raw'].validate_controls)
        self.assertIs(a.verify_location,modules['baseline_raw'].verify_location)
        self.assertIs(a.BaselineRawError,modules['baseline_raw'].BaselineRawError)
        self.assertIs(modules['linux_input_raw'].mapping,modules['linux_install_map'])
        (f.root/'evidence.json').write_bytes(b'{"value":1}\n')
        reader=a.Reader(f.root)
        self.assertEqual(reader.json('evidence.json'),{'value':1})
        self.assertEqual(reader.files['evidence.json'],hashlib.sha256(b'{"value":1}\n').hexdigest())
        with self.assertRaisesRegex(a.EvidenceError,'acceptance tool may only be hashed'):
            reader.bytes(AUDIT+'audit130.py')
        schema=a.Schema(reader,[])
        schema.validate({'value':1},{'type':'object','required':['value'],'properties':{'value':{'type':'integer'}}})
        with self.assertRaisesRegex(a.EvidenceError,'schema type differs'):
            schema.validate(True,{'type':'integer'})
        with self.assertRaises(a.EvidenceError) as caught:schema.validate({}, {'unsupported-witness':True})
        self.assertEqual(caught.exception.status,'unrun')
        other=f.caller().run(f.controls,'verify');verification=f.modules('verify')
        self.assertIsNot(a,other)
        for name in NAMES:
            self.assertIsNot(modules[name],verification[name])
            self.assertEqual(modules[name].__file__,verification[name].__file__)
        f.controls.verify_installed()
        self.assertIs(sys.path,path);self.assertEqual(tuple(path),members)
        owned=[m.__name__ for m in (*modules.values(),*verification.values())]
        f.controls.close()
        self.assertTrue(f.domain.closed)
        for name in owned:self.assertNotIn(name,sys.modules)
        self.assertIs(sys.path,path);self.assertEqual(tuple(path),members)

    def test_native_route_never_calls_any_search_path_mutator(self):
        class GuardedPath(list):
            def insert(self,*args):raise AssertionError('native path insert')
            def append(self,*args):raise AssertionError('native path append')
            def extend(self,*args):raise AssertionError('native path extend')
            def remove(self,*args):raise AssertionError('native path remove')
            def pop(self,*args):raise AssertionError('native path pop')
            def clear(self,*args):raise AssertionError('native path clear')
            def __setitem__(self,*args):raise AssertionError('native path replace')
            def __delitem__(self,*args):raise AssertionError('native path delete')
        original=sys.path;path=GuardedPath(original);sys.path=path
        try:
            f=AuditorFixture(self);a=f.run();self.assertTrue(a._native_auditor_route())
            f.controls.verify_installed();f.controls.close()
            self.assertIs(sys.path,path);self.assertEqual(path,original)
        finally:sys.path=original

    def test_removed_binding_and_removed_core_key_refuse_before_foreign_hooks(self):
        f=AuditorFixture(self);a=f.run();route=a._native_auditor_route
        original=a.__dict__.pop('__som_native_active_controls__')
        try:
            with self.assertRaisesRegex(ValueError,'binding was replaced'):route()
            core=sys.modules.pop(f.core.__name__)
            try:
                with self.assertRaisesRegex(ValueError,'binding was replaced|namespace changed'):route()
            finally:sys.modules[f.core.__name__]=core
        finally:a.__dict__['__som_native_active_controls__']=original
        calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign control hook')
        a.__dict__['__som_native_active_controls__']=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'binding was replaced'):route()
            self.assertEqual(calls,[])
        finally:a.__dict__['__som_native_active_controls__']=original
        f.controls.verify_installed()

    def test_fake_reserved_module_and_foreign_module_class_refuse_before_hook(self):
        calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign handle hook')
        fake=ModuleType('_som_native_active_controls_binding');fake.controls=Foreign()
        for filename,forbidden in (('audit130','Reader'),('linux_input_raw','LinuxInputError')):
            raw=checked(AUDIT+filename+'.py')
            module=ModuleType('_som_m6_runtime_audit_'+filename)
            module.__file__=str(REPO/AUDIT/(filename+'.py'))
            with mock.patch.dict(sys.modules,{'_som_native_active_controls_binding':fake}):
                with self.assertRaisesRegex(ValueError,'no actual fixed active-controls binding'):
                    exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
            self.assertEqual(calls,[])
            self.assertNotIn(forbidden,module.__dict__)
        f=AuditorFixture(self);a=f.run();route=a._native_auditor_route
        provider=f.modules('runtime')['api_raw'];old_name=provider.__name__
        class ForeignName:
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign name comparison')
            def __hash__(self):calls.append('hash');raise AssertionError('foreign name hash')
        provider.__name__=ForeignName()
        try:
            with self.assertRaisesRegex(ValueError,'actual provider identity differs'):route()
            self.assertEqual(calls,[])
        finally:provider.__name__=old_name
        class ForeignModule(ModuleType):
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign module hook')
        a.__class__=ForeignModule
        try:
            with self.assertRaisesRegex(ValueError,'namespace changed'):route()
            self.assertEqual(calls,[])
        finally:ModuleType.__setattr__(a,'__class__',ModuleType)

    def test_unknown_namespace_and_missing_injection_refuse_before_original_imports(self):
        for kind in ('namespace','binding','audit-export','linux-mapping'):
            with self.subTest(kind=kind):
                f=AuditorFixture(self)
                row=f.rows['m6.runtime.audit.audit130']
                if kind=='namespace':row['module']='_som_unlisted_auditor'
                elif kind=='binding':row['bindings'].pop('__som_native_active_controls__')
                elif kind=='audit-export':row['bindings'].pop('validate_api')
                else:f.rows['m6.runtime.audit.linux-input-raw']['bindings'].pop('mapping')
                path=sys.path;before=tuple(path)
                with self.assertRaisesRegex(ValueError,'fixed module namespace differs|control binding was replaced|original fixed export differs|actual fixed mapping differs'):
                    f.run()
                self.assertNotIn('m6.runtime.audit.audit130',f.domain.records)
                # Existing fixed providers can already be completed. The
                # original audit130 body itself never passes its guard.
                self.assertIs(sys.path,path);self.assertEqual(tuple(path),before)
                f.controls.close()

    def test_real_frozen16_static_resolution_and_schema_keep_full_policy_pending(self):
        f=AuditorFixture(self)
        design_fixture=json.loads(checked(BASE+'native_active_policy_design_fixture.json'))
        self.assertEqual(f.search_roots,design_fixture['design']['search_roots'])
        self.assertEqual(len(f.search_roots),16)
        self.assertNotIn(AUDIT.rstrip('/'),f.search_roots)
        self.assertEqual(f.policy['imports'],[])
        sibling_spellings={'render_rules','refusal_rules','api_raw','linux_input_raw','baseline_raw','linux_install_map'}
        self.assertFalse(any(row['caller'].startswith(AUDIT) and row['import'] in sibling_spellings
            for row in f.policy['static_edges']))
        schema_raw=checked(BASE+'native_active_roles.schema.json');schema=f.core.parse(schema_raw)
        pending={**f.policy,'version':'som.native-active-role-policy.v1','complete':False,
            'pending':['explicit synthetic protocol authority and incomplete actual source admission'],
            'product_acceptance_passed':False,'compiler_acceptance_passed':False,
            'whole_acceptance_complete':False,'design':design_fixture['design'],
            'groups':{'m6.api.payload':{**f.policy['groups']['m6.api.payload'],
                'members':{name:role for name,(role,_,_) in f.core.PRIVATE.items()},
                'producer_role':'m6.api.producer'}}}
        f.core.validate(pending,schema,schema)
        saved={path:f.raw.get(path)for path in (f.core.SCHEMA,f.core.POLICY)}
        try:
            f.raw[f.core.SCHEMA]=schema_raw;f.raw[f.core.POLICY]=f.core.canonical(pending)
            with self.assertRaisesRegex(ValueError,'active finite role policy remains pending'):
                f.core._Policy(f.transfer)
        finally:
            for path,raw in saved.items():
                if raw is None:f.raw.pop(path,None)
                else:f.raw[path]=raw
        # Actual direct-Core loading is still meaningful, and explicitly does
        # not turn that pending formal policy into a positive admission.
        a=f.run();self.assertTrue(a._native_auditor_route())
        self.assertEqual(len(f.modules('runtime')),7)
        f.controls.verify_installed()

    def test_original_reader_first_error_survives_owned_cleanup_error(self):
        f=AuditorFixture(self);a=f.run();reader=a.Reader(f.root);first=None;foreign=None;name=a.__name__
        before=sys.path;members=tuple(before)
        try:
            with f.controls:
                try:reader.bytes('actual-missing-evidence.json')
                except a.EvidenceError as error:
                    first=error
                    foreign=ModuleType('foreign retained module replacement')
                    sys.modules[name]=foreign
                    raise
        except a.EvidenceError as error:
            self.assertIs(error,first)
            self.assertEqual(error.status,'unrun')
            self.assertIn('test_original_reader_first_error_survives_owned_cleanup_error',
                [row.name for row in traceback.extract_tb(error.__traceback__)])
            self.assertTrue(error.__protocol_secondary__)
        else:self.fail('original Reader error must remain first')
        try:
            self.assertTrue(f.domain.closed)
            self.assertIs(sys.modules[name],foreign)
            self.assertIs(sys.path,before);self.assertEqual(tuple(before),members)
        finally:
            if sys.modules.get(name) is foreign:del sys.modules[name]

if __name__=='__main__':unittest.main()
