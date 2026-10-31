"""GitHub Core protocol tests; no genuine Context or original M6 data grant.

The actual callback, Core and SourceBytes execute only on GitHub. Helper bodies
for oracle/postclose are explicit finite source doubles. Audit inputs are data.
The authority transfer and Context constructor remain protocol test seams.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import subprocess
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
M6='som-rs/scripts/react_output_acceptance/'
CALLBACK=M6+'m6_react_callback.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
ORACLE=BASE+'product_runtime/recorded_before_oracle.py'
LAUNCHER=BASE+'run_current_candidate_gate.py'
PINS={CALLBACK:'d1b84079364106bd16c80ddfd9637982d2f6a4b55380384b279fb02562b9d617',
      CONTEXT:'7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
      BASE+'native_active_controls.py':'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32',
      BASE+'test_native_active_controls.py':'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d',
      M6+'native_owner_input_binding.py':'86b40f342f13afd609047050a077cc2f85c57f4122ee6545ed312426893cbdd5'}
AUDIT_NAMES=('render_rules','refusal_rules','api_raw','linux_install_map','linux_input_raw','baseline_raw','audit130')
ROOT_AUDITOR_SNAPSHOTS={'audit130': '.github/actions/native-active-source-fixtures/m6-auditor-root-before/audit130.py.txt', 'api_raw': '.github/actions/native-active-source-fixtures/m6-auditor-root-before/api_raw.py.txt', 'baseline_raw': '.github/actions/native-active-source-fixtures/m6-auditor-root-before/baseline_raw.py.txt', 'linux_input_raw': '.github/actions/native-active-source-fixtures/m6-auditor-root-before/linux_input_raw.py.txt'}
ROOT_AUDITOR_SNAPSHOT_PINS={'.github/actions/native-active-source-fixtures/m6-auditor-root-before/audit130.py.txt': 'c6fa7c77f956977e6556685cccd089aa6b3da8997b5be469eaf0f061491e14af', '.github/actions/native-active-source-fixtures/m6-auditor-root-before/api_raw.py.txt': '75cdc4be60eba7eccf28dd5a50be74d524d4c9c9d33b070ef8f876f1f62a1292', '.github/actions/native-active-source-fixtures/m6-auditor-root-before/baseline_raw.py.txt': '197d66ec8648b093aa29405c532d3b65d3cc533ed111b3a8b36e63195543bb99', '.github/actions/native-active-source-fixtures/m6-auditor-root-before/linux_input_raw.py.txt': '4bf8859b2e8998ba652874bc654bb6739a232c56f4e1303e30d4add1d4979616'}
CURRENT_AUDITOR_PINS={'som-rs/scripts/react_output_acceptance/audit/api_raw.py': '25ff69a6c37623d6ed179d63f39eb04ee2c62e4c7697d0677e7d2fac1ad382f5', 'som-rs/scripts/react_output_acceptance/audit/audit130.py': '7b20271cbc7612594a88c81e9c94f0e41efd6bafd438463df60f55ddce7b5d1e', 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py': 'a4778091a8434aa12fcfc595a8a9040783e1101744732cbfae26a9e7355a7eeb', 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py': '74cb93117f979865233766041dac55a830b5184810a04ea6d3725a87d85726d9', 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py': 'f73ee9e9e36f0471ab67bab808ee86c71cf4fbe45641eaa94cdff647c6e60c28', 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py': '53592f0dde5646aed2a1d67952db6e495987e3a47a84b7647c3fd97b577044a8', 'som-rs/scripts/react_output_acceptance/audit/render_rules.py': '1121918d87d6cd77e4bcc024424f12fd86722fc9e2509fa7010dda84ff732aaf'}
CURRENT_AUDITOR_ROLES={'m6.runtime.audit.api-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.api-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_api_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/api_raw.py'}, 'm6.runtime.audit.audit130': {'bindings': {'ApiRawError': {'attribute': 'ApiRawError', 'kind': 'role-export', 'role': 'm6.runtime.audit.api-raw'}, 'BaselineRawError': {'attribute': 'BaselineRawError', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'LinuxInputError': {'attribute': 'LinuxInputError', 'kind': 'role-export', 'role': 'm6.runtime.audit.linux-input-raw'}, '__som_native_active_controls__': {'kind': 'active-controls'}, 'audit_linux_inputs': {'attribute': 'audit', 'kind': 'role-export', 'role': 'm6.runtime.audit.linux-input-raw'}, 'render_files': {'attribute': 'render_files', 'kind': 'role-export', 'role': 'm6.runtime.audit.render-rules'}, 'request_for': {'attribute': 'request_for', 'kind': 'role-export', 'role': 'm6.runtime.audit.refusal-rules'}, 'validate_api': {'attribute': 'validate', 'kind': 'role-export', 'role': 'm6.runtime.audit.api-raw'}, 'validate_controls': {'attribute': 'validate_controls', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'validate_python': {'attribute': 'validate_python', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'validate_rust': {'attribute': 'validate_rust', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}, 'verify_location': {'attribute': 'verify_location', 'kind': 'role-export', 'role': 'm6.runtime.audit.baseline-raw'}}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.api-raw', 'm6.runtime.audit.audit130', 'm6.runtime.audit.baseline-raw', 'm6.runtime.audit.linux-input-raw', 'm6.runtime.audit.linux-install-map', 'm6.runtime.audit.refusal-rules', 'm6.runtime.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_audit130', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/audit130.py'}, 'm6.runtime.audit.baseline-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.baseline-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_baseline_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py'}, 'm6.runtime.audit.linux-input-raw': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}, 'mapping': {'kind': 'role-module', 'role': 'm6.runtime.audit.linux-install-map'}}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.linux-input-raw'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.linux-input-raw', 'm6.runtime.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_linux_input_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py'}, 'm6.runtime.audit.linux-install-map': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.linux-input-raw'}], 'closure': ['m6.runtime.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_linux_install_map', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py'}, 'm6.runtime.audit.refusal-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.refusal-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_refusal_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py'}, 'm6.runtime.audit.render-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.runtime.owner.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.runtime.audit.audit130'}], 'closure': ['m6.runtime.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_runtime_audit_render_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/render_rules.py'}, 'm6.verify.audit.api-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.api-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_api_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/api_raw.py'}, 'm6.verify.audit.audit130': {'bindings': {'ApiRawError': {'attribute': 'ApiRawError', 'kind': 'role-export', 'role': 'm6.verify.audit.api-raw'}, 'BaselineRawError': {'attribute': 'BaselineRawError', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'LinuxInputError': {'attribute': 'LinuxInputError', 'kind': 'role-export', 'role': 'm6.verify.audit.linux-input-raw'}, '__som_native_active_controls__': {'kind': 'active-controls'}, 'audit_linux_inputs': {'attribute': 'audit', 'kind': 'role-export', 'role': 'm6.verify.audit.linux-input-raw'}, 'render_files': {'attribute': 'render_files', 'kind': 'role-export', 'role': 'm6.verify.audit.render-rules'}, 'request_for': {'attribute': 'request_for', 'kind': 'role-export', 'role': 'm6.verify.audit.refusal-rules'}, 'validate_api': {'attribute': 'validate', 'kind': 'role-export', 'role': 'm6.verify.audit.api-raw'}, 'validate_controls': {'attribute': 'validate_controls', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'validate_python': {'attribute': 'validate_python', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'validate_rust': {'attribute': 'validate_rust', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}, 'verify_location': {'attribute': 'verify_location', 'kind': 'role-export', 'role': 'm6.verify.audit.baseline-raw'}}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.api-raw', 'm6.verify.audit.audit130', 'm6.verify.audit.baseline-raw', 'm6.verify.audit.linux-input-raw', 'm6.verify.audit.linux-install-map', 'm6.verify.audit.refusal-rules', 'm6.verify.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_audit130', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/audit130.py'}, 'm6.verify.audit.baseline-raw': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.baseline-raw'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_baseline_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/baseline_raw.py'}, 'm6.verify.audit.linux-input-raw': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}, 'mapping': {'kind': 'role-module', 'role': 'm6.verify.audit.linux-install-map'}}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.linux-input-raw'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.linux-input-raw', 'm6.verify.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_linux_input_raw', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_input_raw.py'}, 'm6.verify.audit.linux-install-map': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.linux-input-raw'}], 'closure': ['m6.verify.audit.linux-install-map'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_linux_install_map', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/linux_install_map.py'}, 'm6.verify.audit.refusal-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.refusal-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_refusal_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/refusal_rules.py'}, 'm6.verify.audit.render-rules': {'bindings': {}, 'callers': [{'function': '_native_modules', 'role': 'common.owner-verify.m6-react003'}, {'function': '_native_auditor_route', 'role': 'm6.verify.audit.audit130'}], 'closure': ['m6.verify.audit.render-rules'], 'group': None, 'is_package': False, 'kind': 'module', 'module': '_som_m6_verify_audit_render_rules', 'package': '', 'source': 'som-rs/scripts/react_output_acceptance/audit/render_rules.py'}}
CURRENT_AUDITOR_STATIC_PINS={'som-rs/scripts/hosted_readiness/native_active_roles.schema.json': '29711f2ac4a3a8f1afccbf21625238ff28f63214fde1f2bf9f2615cf40ca7942', 'som-rs/scripts/hosted_readiness/native_active_policy_design_fixture.json': '35bbd6403930ad790d6a6bccac176138f1fd541f5b07a103479c8546f146a875'}
AUDIT_DATA_PINS={
    'render_rules':'1121918d87d6cd77e4bcc024424f12fd86722fc9e2509fa7010dda84ff732aaf',
    'refusal_rules':'53592f0dde5646aed2a1d67952db6e495987e3a47a84b7647c3fd97b577044a8',
    'api_raw':'75cdc4be60eba7eccf28dd5a50be74d524d4c9c9d33b070ef8f876f1f62a1292',
    'linux_install_map':'f73ee9e9e36f0471ab67bab808ee86c71cf4fbe45641eaa94cdff647c6e60c28',
    'linux_input_raw':'4bf8859b2e8998ba652874bc654bb6739a232c56f4e1303e30d4add1d4979616',
    'baseline_raw':'197d66ec8648b093aa29405c532d3b65d3cc533ed111b3a8b36e63195543bb99',
    'audit130':'c6fa7c77f956977e6556685cccd089aa6b3da8997b5be469eaf0f061491e14af'}

def checked(path,pin=None):
    raw=(REPO/path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=(pin or PINS[path]):raise RuntimeError('exact fixture dependency differs: '+path)
    return raw

def auditor_root_before(name):
    if name in ROOT_AUDITOR_SNAPSHOTS:
        path=ROOT_AUDITOR_SNAPSHOTS[name]
        file=REPO/path
        if file.is_symlink() or not file.is_file():
            raise RuntimeError('regular historical auditor source data required')
        return checked(path,ROOT_AUDITOR_SNAPSHOT_PINS[path])
    return checked(M6+'audit/'+name+'.py',AUDIT_DATA_PINS[name])

for path in PINS:checked(path)
for path,pin in ROOT_AUDITOR_SNAPSHOT_PINS.items():checked(path,pin)
protocol=ModuleType('_som_native_callback_protocol_fixture')
protocol.__file__,protocol.__package__=str(HERE/'test_native_active_controls.py'),''
sys.modules[protocol.__name__]=protocol
exec(compile(checked(BASE+'test_native_active_controls.py'),protocol.__file__,'exec',dont_inherit=True),protocol.__dict__)

class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self,fixture):
        super().__init__(fixture)
        self.revision=fixture.revision
        self.admission.update(helper_revision=self.revision,candidate_revision=self.revision)

class CallbackFixture(protocol.Fixture):
    def __init__(self,case):
        caller=('def run(controls,target="runtime"):\n'
                '    return controls.load_module({"runtime":"common.runtime.owner.m6-react003",'
                '"verify":"common.owner-verify.m6-react003","context":"runtime.context"}[target])\n')
        with mock.patch.object(protocol,'ProtocolTransfer',GitTransfer):
            super().__init__(case,caller)
        self.context=self.caller().run(self.controls,'context')
        self.sources=self.context.SourceBytes(self.root,self.revision)
        self.runtime=self.run()
        self.verify=self.caller().run(self.controls,'verify')

    def auditor_bytes(self,name):
        return auditor_root_before(name)

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        context_bindings={'__som_native_active_controls__':{'kind':'active-controls'}}
        super().add_role('runtime.context',CONTEXT,checked(CONTEXT),module='_som_callback_fixture_context',
            callers=[{'role':'caller','function':'run'}],bindings=context_bindings)
        for scope,callback_role,module in [('runtime','common.runtime.owner.m6-react003','_som_full_current_gate_owner_m6_react003'),
                                          ('verify','common.owner-verify.m6-react003','_som_current_gate_owner_verify_m6_react003')]:
            bindings={'__som_native_active_controls__':{'kind':'active-controls'}}
            if scope=='runtime':
                bindings.update({'__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
                    '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
                    '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
                    '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'}})
            super().add_role(callback_role,CALLBACK,checked(CALLBACK),module=module,
                callers=[{'role':'caller','function':'run'}],bindings=bindings)
            for kind,target,body in [('recorded-before',ORACLE,'VALUE = "explicit oracle source protocol only"\n'),
                                    ('postclose',LAUNCHER,'VALUE = "explicit postclose source protocol only"\n')]:
                target_role='m6.'+scope+'.'+kind
                namespace='_som_m6_'+scope+('_recorded_before_verifier' if kind=='recorded-before' else '_postclose_launcher')
                super().add_role(target_role,target,body,module=namespace,
                    callers=[{'role':callback_role,'function':'_native_modules'}])
            for name in AUDIT_NAMES:
                data_path=M6+'audit/'+name+'.py'
                super().add_role('m6.'+scope+'.audit.'+name.replace('_','-'),data_path,
                    self.auditor_bytes(name),module='_som_m6_'+scope+'_audit_'+name,
                    callers=[{'role':callback_role,'function':'_native_modules'}])
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],
            env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        git('init','--quiet');git('add','--','.')
        git('-c','user.name=Callback protocol fixture','-c','user.email=fixture@example.invalid',
            '-c','core.hooksPath=/dev/null','commit','--quiet','-m','synthetic source protocol only')
        self.revision=git('rev-parse','HEAD').decode().strip()
        return row

class NativeCallbackLoaderTests(unittest.TestCase):
    def test_two_actual_callback_namespaces_keep_closed_flags_and_standalone_refusal(self):
        f=CallbackFixture(self)
        self.assertIsNot(f.runtime,f.verify)
        self.assertEqual(f.runtime.__file__,f.verify.__file__)
        self.assertNotEqual(f.runtime.__name__,f.verify.__name__)
        for module,scope in ((f.runtime,'runtime'),(f.verify,'verify')):
            self.assertEqual(module._native_state(f.root)[1],scope)
            self.assertFalse(module.OWNER_INTEGRATION_REVIEWED)
            self.assertFalse(module.GRAPH_INTEGRATION_REVIEWED)
            with self.assertRaisesRegex(ValueError,'native pre-import source authority is not implemented'):
                module.admit(None,None,None,None)

    def test_real_core_fixed_helpers_keep_copy_identity_and_do_not_change_searchpath(self):
        f=CallbackFixture(self);before=list(sys.path)
        with f.runtime._native_modules(f.sources.read,f.root,'oracle') as runtime:
            self.assertEqual(runtime[0].VALUE,'explicit oracle source protocol only')
            original=runtime[0]
        self.assertIs(sys.modules[original.__name__],original)
        with f.runtime._native_modules(f.sources.read,f.root,'oracle') as again:self.assertIs(again[0],original)
        with f.verify._native_modules(f.sources.read,f.root,'oracle') as verification:
            self.assertIsNot(verification[0],original)
            self.assertNotEqual(verification[0].__name__,original.__name__)
            self.assertEqual(verification[0].__file__,original.__file__)
        self.assertEqual(sys.path,before)
        self.assertFalse(f.domain.closed)

    def test_foreign_legacy_namespace_is_refused_and_preserved_before_target(self):
        f=CallbackFixture(self);name='_som_m6_react_recorded_before_verifier';foreign=ModuleType(name)
        sys.modules[name]=foreign
        try:
            with self.assertRaisesRegex(ValueError,'preloaded legacy callback helper refused'):
                with f.runtime._native_modules(f.sources.read,f.root,'oracle'):pass
            self.assertIs(sys.modules[name],foreign)
            self.assertNotIn('m6.runtime.recorded-before',f.domain.records)
        finally:
            if sys.modules.get(name) is foreign:del sys.modules[name]

    def test_actual_git_working_bytes_change_refuses_before_helper_body(self):
        f=CallbackFixture(self);path=f.root/ORACLE;raw=path.read_bytes();path.write_bytes(raw+b'# changed\n')
        try:
            with self.assertRaisesRegex(ValueError,'source differs from committed Git bytes'):
                with f.runtime._native_modules(f.sources.read,f.root,'oracle'):pass
            self.assertNotIn('m6.runtime.recorded-before',f.domain.records)
        finally:path.write_bytes(raw)

    def test_all_auditor_sources_preflight_refuses_root_variant_before_any_body(self):
        f=CallbackFixture(self);before=list(sys.path)
        with self.assertRaisesRegex(ValueError,'exact c4 native auditor sources required; Root variant is separate'):
            with f.verify.source_bound_auditor(f.sources.read,f.root):pass
        self.assertTrue(all('m6.verify.audit.'+name.replace('_','-') not in f.domain.records for name in AUDIT_NAMES))
        self.assertEqual(sys.path,before)

    def test_runtime_context_and_services_pending_guards_do_not_call_foreign_hooks(self):
        f=CallbackFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign attribute hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign comparison hook')
        with self.assertRaisesRegex(ValueError,'genuine active native Context required; verify-only join pending'):
            f.runtime._native_run_gate_identity(Foreign(),Foreign())
        ctx=object.__new__(f.context.Context)
        ctx.repo,ctx.helper_revision,ctx.candidate_revision=f.root,f.revision,f.revision
        ctx.sources,ctx.qa_mode=f.sources,'native-qa-v1';ctx.qa_import_controls=f.controls.borrow()
        self.addCleanup(ctx.qa_import_controls.close)
        f.context._ACTIVE=ctx
        with self.assertRaisesRegex(ValueError,'completed source-owned Common Services class binding pending'):
            f.runtime._native_run_gate_identity(ctx,Foreign())
        self.assertEqual(calls,[])

    def test_first_error_keeps_trace_and_existing_secondary_while_every_scope_closes(self):
        f=CallbackFixture(self);b=f.runtime;first=RuntimeError('actual callback protocol first failure');calls=[]
        first.__som_native_callback_secondary__=(('earlier',first),)
        class ForeignName:
            def __hash__(self):calls.append('hash');raise AssertionError('foreign metadata hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign metadata hook')
        original_name=b.__name__;path=f.root/LAUNCHER;raw=path.read_bytes();lease=None;module=None;helper_name=None
        try:
            try:
                with b._native_borrow(f.root) as (lease,scope):
                    with b._native_modules(f.sources.read,f.root,'postclose') as modules:
                        module=modules[0];helper_name=module.__name__;module.__name__=ForeignName()
                        path.write_bytes(raw+b'# source changed after actual body\n')
                        b.__name__=ForeignName()
                        raise first
            except RuntimeError as error:
                self.assertIs(error,first)
                self.assertIn('test_first_error_keeps_trace_and_existing_secondary_while_every_scope_closes',
                    [row.name for row in traceback.extract_tb(error.__traceback__)])
                self.assertGreaterEqual(len(error.__som_native_callback_helper_secondary__),2)
                self.assertIs(error.__som_native_callback_secondary__[0][1],first)
                self.assertGreaterEqual(len(error.__som_native_callback_secondary__),2)
            else:self.fail('first body error must propagate')
            self.assertEqual(calls,[])
            with self.assertRaisesRegex(ValueError,'active source lease is closed'):lease.verify_installed()
            self.assertFalse(f.domain.closed)
        finally:
            path.write_bytes(raw);b.__name__=original_name
            if module is not None:module.__name__=helper_name

    def test_closed_owner_flag_remains_first_run_gate_guard(self):
        f=CallbackFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign run gate hook')
        with self.assertRaisesRegex(ValueError,'M6 shared owner integration is not implemented'):
            f.runtime.run_gate(Foreign(),None,Foreign(),None,'m6-react003')
        self.assertEqual(calls,[])

    def test_missing_binding_cannot_take_the_legacy_route_inside_core(self):
        f=CallbackFixture(self);module=f.verify;value=module.__dict__.pop('__som_native_active_controls__')
        try:
            with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced'):
                with module.source_bound_auditor(f.sources.read,f.root):pass
            name=f.core.__name__;core=sys.modules.pop(name)
            try:
                with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced|owned source module namespace changed'):
                    with module.source_bound_auditor(f.sources.read,f.root):pass
            finally:sys.modules[name]=core
        finally:module.__dict__['__som_native_active_controls__']=value


# Additional finite frame protocol. Old9 methods above stay byte/AST unchanged.
# Context source/constructor/environment/command are explicit doubles here.
# Real Core owns the actual callback and the fixed synthetic class exports.
FRAME_CONTEXT_SOURCE="from pathlib import Path\nimport hashlib\n_ACTIVE=None\nclass SourceBytes:\n    def __init__(self,root,revision):self.root,self.revision,self.pins=Path(root).absolute(),revision,{}\n    def read(self,path):\n        raw=(self.root/path).read_bytes();self.pins[path]=hashlib.sha256(raw).hexdigest();return raw\n    def verify(self):return None\nclass Context:\n    def child_environment(self):\n        self.builder_calls+=1\n        self.last_built_env=dict(self.frame)\n        return self.last_built_env\n    def command(self,argv,cwd,env,label,timeout=1800,check=True):\n        # Explicit command data double models only existing public redaction.\n        record={'argv':list(argv),'cwd':str(cwd),'environment':{k:('[redacted transport token]'if k=='SOM_DEPENDENCY_ACTIONS_TOKEN'else v)for k,v in env.items()},'exit_code':0,'wait_completed':False}\n        self.cache.setdefault('commands',[]).append(record)\n        return record\ndef context():return _ACTIVE\n"
class FrameFixture(CallbackFixture):
    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role=='m6.api.producer':
            body=FRAME_CONTEXT_SOURCE.encode();self.raw[CONTEXT]=body
            (self.root/CONTEXT).write_bytes(body)
        return row
    def active_context(self):
        ctx=object.__new__(self.context.Context)
        ctx.repo,ctx.helper_revision,ctx.candidate_revision=self.root,self.revision,self.revision
        ctx.sources,ctx.qa_mode=self.sources,'native-qa-v1'
        ctx.qa_import_controls=self.controls.borrow();self.stack.callback(ctx.qa_import_controls.close)
        ctx.builder_calls,ctx.cache=0,{}
        ctx.frame={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
            'RUNNER_TEMP':str(self.root),'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux',
            'GITHUB_SHA':self.revision,'GITHUB_WORKFLOW_SHA':self.revision,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1',
            'GITHUB_JOB':'required-callbacks','GITHUB_REPOSITORY':'faberline/som','GITHUB_REPOSITORY_ID':'1390374878',
            'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/protocol-only',
            'GITHUB_WORKFLOW_REF':'faberline/som/.github/workflows/rust-hosted-readiness.yml@refs/heads/protocol-only',
            'GITHUB_SERVER_URL':'https://github.com','GITHUB_API_URL':'https://api.github.com','RUNNER_NAME':'protocol-runner',
            'SOM_FULL_PRODUCT_CONTEXT':str(self.root/'context.json'),'SOM_DEPENDENCY_ACTIONS_TOKEN':'synthetic-actions-token-for-protocol'}
        self.context._ACTIVE=ctx
        return ctx


def legacy_frame_module():
    module=ModuleType('_som_callback_frame_legacy_protocol');module.__file__=str(REPO/CALLBACK)
    module.__package__,module.__cached__='',None
    exec(compile(checked(CALLBACK),module.__file__,'exec',dont_inherit=True),module.__dict__)
    return module

class NativeCallbackFrameTests(unittest.TestCase):
    def test_actual_Core_fixed_class_builder_preserves_full_frame_token_and_ignores_overrides(self):
        f=FrameFixture(self);ctx=f.active_context();calls=[]
        class ForeignEnviron:
            def __iter__(self):calls.append('iter');raise AssertionError('untrusted environment read')
            def __contains__(self,key):calls.append('contains');raise AssertionError('untrusted environment read')
            def __getitem__(self,key):calls.append('get');raise AssertionError('untrusted environment read')
        def foreign_builder():calls.append('builder');raise AssertionError('instance builder shadow ran')
        ctx.child_environment=foreign_builder
        actual=f.runtime.safe_child_environment(ctx,ForeignEnviron())
        self.assertIs(actual,ctx.last_built_env);self.assertEqual(actual,ctx.frame)
        self.assertEqual(ctx.builder_calls,1);self.assertEqual(calls,[])
        self.assertNotIn('RUNNER_ARCH',actual)
        command=ctx.command(['protocol-no-exec'],f.root,actual,'protocol')
        self.assertEqual(command['environment']['SOM_DEPENDENCY_ACTIONS_TOKEN'],'[redacted transport token]')
        evidence=f.root/'redacted-evidence';evidence.mkdir()
        row=f.runtime.command_evidence(ctx,evidence,'protocol',command)
        fallback=f.runtime.fallback_command_streams(ctx,evidence,'fallback',command)
        encoded=f.runtime.canonical({'row':row,'fallback':fallback})
        self.assertNotIn(b'synthetic-actions-token-for-protocol',encoded)
        f.controls.verify_installed();f.finish()

    def test_native_missing_invalid_token_or_crypto_keys_refuses_with_fixed_messages(self):
        f=FrameFixture(self);ctx=f.active_context();original=dict(ctx.frame);hooks=[]
        class ForeignToken:
            def __len__(self):hooks.append('len');raise AssertionError('foreign token hook')
            def __str__(self):hooks.append('str');raise AssertionError('foreign token hook')
            def isprintable(self):hooks.append('printable');raise AssertionError('foreign token hook')
        for value in (None,'','bad\nvalue','x'*8193,ForeignToken()):
            ctx.frame={**original,'SOM_DEPENDENCY_ACTIONS_TOKEN':value}
            with self.subTest(kind=type(value).__name__),self.assertRaisesRegex(ValueError,'bounded nonempty existing Actions authentication')as caught:
                f.runtime.safe_child_environment(ctx,{})
            self.assertNotIn('bad\nvalue',str(caught.exception));self.assertNotIn('x'*8193,str(caught.exception))
        for key in ('M6_INPUTS_KEY','M6_INPUTS_KEYRING','SOM_M6_ACCEPTANCE_INPUTS_V1','SOM_M6_ACCEPTANCE_INPUTS_V2'):
            ctx.frame={**original,key:'synthetic-crypto-value'}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'must not contain M6 input keys')as caught:
                f.runtime.safe_child_environment(ctx,{})
            self.assertNotIn('synthetic-crypto-value',str(caught.exception))
        self.assertEqual(hooks,[])
        ctx.frame=original;f.controls.verify_installed();f.finish()

    def test_foreign_Context_and_command_shadow_refuse_before_any_foreign_hook(self):
        f=FrameFixture(self);ctx=f.active_context();calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign Context hook')
        with self.assertRaisesRegex(ValueError,'genuine active native Context required'):
            f.runtime.safe_child_environment(Foreign(),{})
        ctx.command=Foreign()
        with self.assertRaisesRegex(ValueError,'actual Context command redaction owner required'):
            f.runtime.safe_child_environment(ctx,{})
        self.assertEqual(calls,[]);self.assertEqual(ctx.builder_calls,0)
        del ctx.command;f.controls.verify_installed();f.finish()

    def test_removed_actual_control_binding_never_falls_back_or_builds_environment(self):
        f=FrameFixture(self);ctx=f.active_context();actual=f.runtime.__dict__.pop('__som_native_active_controls__')
        try:
            with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced'):
                f.runtime.safe_child_environment(ctx,{})
            self.assertEqual(ctx.builder_calls,0)
        finally:f.runtime.__dict__['__som_native_active_controls__']=actual
        f.controls.verify_installed();f.finish()

    def test_normal_legacy_projection_keeps_exact_token_strip_and_old_frame_overrides(self):
        module=legacy_frame_module()
        class LegacyContext:
            def child_environment(self):return {'PATH':'legacy','SOM_DEPENDENCY_ACTIONS_TOKEN':'synthetic-legacy-token','UV_NO_INSTALLER_METADATA':'1'}
        result=module.safe_child_environment(LegacyContext(),{'RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_RUN_ID':'123'})
        self.assertEqual(result,{'PATH':'legacy','RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_RUN_ID':'123'})
        self.assertNotIn('SOM_DEPENDENCY_ACTIONS_TOKEN',result);self.assertNotIn('UV_NO_INSTALLER_METADATA',result)



class NativeCallbackDispatchFrameTests(unittest.TestCase):
    def test_first_dispatch_rechecks_command_after_environment_creation_before_foreign_hooks(self):
        f=FrameFixture(self);ctx=f.active_context();ctx.work=f.root
        env=f.runtime.safe_child_environment(ctx,{})
        calls=[]
        class ForeignCommand:
            def __call__(self,*args,**kwargs):calls.append('foreign-command');raise AssertionError('foreign command received env')
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign command hook')
        class ToolCommands:
            def tool_command(self,*args):calls.append('tool-command');raise AssertionError('argument dispatch ran too early')
        ctx.command=ForeignCommand();report={'rows':[]};out=f.root/'first-dispatch-evidence';out.mkdir()
        try:
            with self.assertRaisesRegex(ValueError,'redaction owner required at native dispatch')as caught:
                f.runtime.run_command(ctx,ToolCommands(),f.root,env,out,report,'collector','synthetic.py',[],1)
            self.assertEqual(calls,[]);self.assertEqual(ctx.cache.get('commands',[]),[])
            self.assertEqual(report,{'rows':[]})
            self.assertNotIn('synthetic-actions-token-for-protocol',str(caught.exception))
        finally:del ctx.command
        f.controls.verify_installed();f.finish()

    def test_same_checked_method_dispatches_once_and_second_shadow_is_refused(self):
        f=FrameFixture(self);ctx=f.active_context();ctx.work=f.root
        env=f.runtime.safe_child_environment(ctx,{});calls=[]
        class ForeignCommand:
            def __call__(self,*args,**kwargs):calls.append('foreign-command');raise AssertionError('foreign command received env')
        class ToolCommands:
            def tool_command(self,path,arguments):
                calls.append('tool-command')
                # The native dispatcher already retained the checked bound method.
                ctx.command=ForeignCommand()
                return ['explicit-no-process-protocol',str(path),*arguments]
        report={'rows':[]};out=f.root/'second-dispatch-evidence';out.mkdir()
        try:
            first=f.runtime.run_command(ctx,ToolCommands(),f.root,env,out,report,'collector','synthetic.py',[],1)
            report['rows'].append(first)
            self.assertEqual(calls,['tool-command']);self.assertEqual(len(ctx.cache['commands']),1)
            self.assertEqual(first['command']['environment']['SOM_DEPENDENCY_ACTIONS_TOKEN'],'[redacted transport token]')
            self.assertFalse(first['streams_complete']);self.assertFalse(first['command']['wait_completed'])
            with self.assertRaisesRegex(ValueError,'redaction owner required at native dispatch'):
                f.runtime.run_command(ctx,ToolCommands(),f.root,env,out,report,'audit','synthetic.py',[],1)
            self.assertEqual(calls,['tool-command']);self.assertEqual(len(ctx.cache['commands']),1)
            self.assertEqual(report['rows'],[first])
            self.assertNotIn(b'synthetic-actions-token-for-protocol',f.runtime.canonical(report))
        finally:del ctx.command
        f.controls.verify_installed();f.finish()



class CurrentAuditorFixture(CallbackFixture):
    def auditor_bytes(self,name):
        path=M6+'audit/'+name+'.py'
        return checked(path,CURRENT_AUDITOR_PINS[path])
    def __init__(self,case):
        super().__init__(case)
        import json
        for role,prototype in CURRENT_AUDITOR_ROLES.items():
            row=json.loads(json.dumps(prototype))
            self.rows[role]['bindings']=row['bindings']
            self.rows[role]['callers']=row['callers']
            self.rows[role]['closure']=row['closure']
        # No native sibling imports are permitted. This protocol uses the
        # exact real16 roots, not the rejected V1 extra audit test root.
        self.policy['imports']=[]
        for path,pin in CURRENT_AUDITOR_STATIC_PINS.items():checked(path,pin)
        design=json.loads(checked(BASE+'native_active_policy_design_fixture.json',
            CURRENT_AUDITOR_STATIC_PINS[BASE+'native_active_policy_design_fixture.json']))['design']
        self.search_roots=design['search_roots']
        self.policy['static_edges']=self.core.static_edges(self.transfer.read,self.policy['sources'],
            self.transfer.source_paths,self.search_roots)

class CurrentAuditorCompositionTests(unittest.TestCase):
    def test_actual_callback_preflight_yields_seven_source_bound_fixed_auditors_in_both_scopes(self):
        f=CurrentAuditorFixture(self);path=sys.path;before=tuple(path)
        self.assertEqual(len(f.search_roots),16)
        self.assertNotIn((M6+'audit').rstrip('/'),f.search_roots)
        self.assertEqual(f.policy['imports'],[])
        modules={}
        for callback,scope in ((f.runtime,'runtime'),(f.verify,'verify')):
            with callback.source_bound_auditor(f.sources.read,f.root) as audit:
                self.assertEqual(audit.__name__,'_som_m6_'+scope+'_audit_audit130')
                family={name:f.domain.records['m6.'+scope+'.audit.'+name.replace('_','-')].module
                    for name in AUDIT_NAMES}
                self.assertIs(audit,family['audit130']);self.assertEqual(len(family),7)
                for name,module in family.items():
                    source=M6+'audit/'+name+'.py'
                    raw=checked(source,CURRENT_AUDITOR_PINS[source])
                    self.assertEqual(f.sources.read(source),raw)
                    self.assertEqual((f.root/source).read_bytes(),raw)
                    self.assertEqual(module.__som_raw_sha256__,CURRENT_AUDITOR_PINS[source])
                    self.assertIs(sys.modules[module.__name__],module)
                aliases=(('render_files','render_rules','render_files'),('request_for','refusal_rules','request_for'),
                    ('validate_api','api_raw','validate'),('ApiRawError','api_raw','ApiRawError'),
                    ('audit_linux_inputs','linux_input_raw','audit'),('LinuxInputError','linux_input_raw','LinuxInputError'),
                    ('validate_python','baseline_raw','validate_python'),('validate_rust','baseline_raw','validate_rust'),
                    ('validate_controls','baseline_raw','validate_controls'),('verify_location','baseline_raw','verify_location'),
                    ('BaselineRawError','baseline_raw','BaselineRawError'))
                for alias,owner,attribute in aliases:self.assertIs(getattr(audit,alias),getattr(family[owner],attribute))
                self.assertIs(family['linux_input_raw'].mapping,family['linux_install_map'])
                # Actual unchanged Reader/Schema operate only on synthetic JSON.
                evidence=f.root/('auditor-protocol-'+scope+'.json');evidence.write_bytes(b'{"value":1}\n')
                reader=audit.Reader(f.root)
                self.assertEqual(reader.json(evidence),{'value':1})
                schema=audit.Schema(reader,[]);schema.validate(1,{'type':'integer'})
                with self.assertRaisesRegex(audit.EvidenceError,'schema type differs'):schema.validate(True,{'type':'integer'})
                self.assertFalse(callback.OWNER_INTEGRATION_REVIEWED)
                self.assertFalse(callback.GRAPH_INTEGRATION_REVIEWED)
                modules[scope]=family
            f.sources.verify();f.controls.verify_installed()
            self.assertIs(sys.path,path);self.assertEqual(tuple(path),before)
        for name in AUDIT_NAMES:
            self.assertIsNot(modules['runtime'][name],modules['verify'][name])
            self.assertEqual(modules['runtime'][name].__file__,modules['verify'][name].__file__)
            self.assertNotEqual(modules['runtime'][name].__name__,modules['verify'][name].__name__)
        owned=[m.__name__ for family in modules.values()for m in family.values()]
        f.sources.verify();f.controls.close()
        self.assertTrue(f.domain.closed)
        for name in owned:self.assertNotIn(name,sys.modules)
        self.assertIs(sys.path,path);self.assertEqual(tuple(path),before)

if __name__=='__main__':unittest.main()
