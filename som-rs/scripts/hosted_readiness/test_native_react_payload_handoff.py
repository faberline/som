"""GitHub protocol fixtures. No real Context or39/40 qualification is claimed.

Actual Core, provider source, SourceBytes, producer Sources and Git run here.
Context construction, source admission, React receipt, checker and runtime
qualification are explicit finite protocol seams. No service or tool runs.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub Linux -I -S -B required')

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
M6 = 'som-rs/scripts/react_output_acceptance/'
SELF = M6 + 'react_api_python_payload.py'
LIVE = M6 + 'linux_api_live_inputs.py'
PRODUCER = BASE + 'product_python/produce_wheels.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
PROFILE = 'som-rs/contracts/react-output-v1/m12-native-api-payload-owner-v1.json'
PROVIDER_ROLE = 'fixture.payload.provider'
EXPECTED = {
    SELF: '669b2ea374ce905b93d15fa400a613e43c95677dfd1984bb405b413b3c5f035a',
    LIVE: '3ead6a90dc0350d143ba7a55b0e3ad9f4e6f62357259a7a4252b6d8362976c7e',
    PRODUCER: '02d06583060b12601f3038709f2d2a9a0643d93cda7670220d592fd821ed580e',
    CONTEXT: '7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
    BASE + 'native_active_controls.py': 'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32',
    BASE + 'test_native_active_controls.py': 'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d',
}


def checked(path):
    raw = (REPO / path).read_bytes()
    if path in EXPECTED and hashlib.sha256(raw).hexdigest() != EXPECTED[path]:
        raise RuntimeError('exact fixture dependency differs: ' + path)
    return raw


for relative in EXPECTED:
    checked(relative)
protocol = ModuleType('_som_react_payload_protocol_fixture')
protocol.__file__, protocol.__package__ = str(HERE / 'test_native_active_controls.py'), ''
sys.modules[protocol.__name__] = protocol
exec(compile(checked(BASE + 'test_native_active_controls.py'), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)


class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.revision = fixture.revision
        self.admission.update(helper_revision=self.revision, candidate_revision=self.revision)


class PayloadFixture(protocol.Fixture):
    def __init__(self, case, *, pending=False, fail_group=False):
        self.pending, self.fail_group = pending, fail_group
        caller = ('def run(controls, target="provider"):\n'
                  '    return controls.load_module({"provider":"fixture.payload.provider",'
                  '"context":"runtime.context","runtime":"fixture.runtime"}[target])\n')
        with mock.patch.object(protocol, 'ProtocolTransfer', GitTransfer):
            super().__init__(case, caller)
        self.stack.enter_context(mock.patch.dict(os.environ, {
            'RUNNER_TEMP': str(self.root / 'runner'), 'GITHUB_SHA': self.revision,
        }))
        self.runner = self.root / 'runner'; self.runner.mkdir()
        self.venv = self.runner / 'som-react-python-closure/environments/react-api-qa/venv'
        (self.venv / 'bin').mkdir(parents=True)
        (self.venv / 'lib/python3.12/site-packages').mkdir(parents=True)
        self.runtime_root = self.runner / 'som-product-runtime-qualification'
        base = self.runtime_root / 'install/python/bin/python3.12'; base.parent.mkdir(parents=True)
        base.write_bytes(b'inert executable fixture; NEVER RUN'); base.chmod(0o700)
        self.python = self.venv / 'bin/python'; self.python.symlink_to(base)
        (self.runtime_root / 'install/postgresql/bin').mkdir(parents=True)
        (self.runtime_root / 'receipt.json').write_bytes(b'{"fixture":"unqualified-runtime"}\n')
        (self.venv.parent / 'live-provision-receipt.json').write_bytes(b'{"fixture":"not-a-live-qualification"}\n')
        (self.runner / 'raw-evidence.bin').write_bytes(b'real protocol raw bytes, not original39wheel proof')
        public = {'runtime_root': str(self.runtime_root), 'venv': str(self.venv)}
        (self.runner / 'som-react-api-context.json').write_text(json.dumps(public))
        self.context_module = self.caller().run(self.controls, 'context')
        ctx = object.__new__(self.context_module.Context)
        ctx.repo, ctx.helper_revision, ctx.candidate_revision = self.root, self.revision, self.revision
        ctx.sources = self.context_module.SourceBytes(self.root, self.revision)
        ctx.qa_mode, ctx.qa_source_authority = 'native-qa-v1', self.transfer.admission
        ctx.qa_import_controls = self.controls.borrow()
        runtime = self.caller().run(self.controls, 'runtime')
        ctx.cache = {'runtime': {'reuse': runtime, 'qualification': {}, 'inventory': {},
            'tools': {'python': base}, 'identity': {'reuse_receipt_sha256': hashlib.sha256((self.runtime_root / 'receipt.json').read_bytes()).hexdigest()}}}
        ctx.data = {'runtime_root': str(self.runtime_root)}
        self.context_module._ACTIVE = ctx
        self.ctx, self.provider = ctx, self.run()

    def add_role(self, role, path, raw, **kwargs):
        if role != 'm6.api.producer':
            return super().add_role(role, path, raw, **kwargs)
        row = super().add_role(role, PRODUCER, checked(PRODUCER),
            module='som_react_api_verified_wheel_producer',
            callers=[{'role': 'm6.api.payload.live', 'function': 'bound_sources'}])
        super().add_role('runtime.context', CONTEXT, checked(CONTEXT), module='_som_payload_fixture_context',
            callers=[{'role': 'caller', 'function': 'run'}],
            bindings={'__som_native_active_controls__': {'kind': 'active-controls'}})
        bindings = {'__som_native_active_controls__': {'kind': 'active-controls'},
            '__som_native_context_module__': {'kind': 'role-module', 'role': 'runtime.context'},
            '__som_native_Context__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'Context'},
            '__som_native_SourceBytes__': {'kind': 'role-export', 'role': 'runtime.context', 'attribute': 'SourceBytes'},
            '__som_native_Group__': {'kind': 'role-export', 'role': 'bootstrap.core', 'attribute': '_Group'}}
        super().add_role(PROVIDER_ROLE, SELF, checked(SELF), module='_som_payload_fixture_provider',
            callers=[{'role': 'caller', 'function': 'run'}], bindings=bindings)
        super().add_role('fixture.runtime', BASE + 'fixture_payload_runtime.py',
            'COUNT = 0\ndef verify_installed_tree(root, inventory, qualification):\n'
            '    global COUNT\n    COUNT += 1\n    return {"fixture_only": True}\n',
            callers=[{'role': 'caller', 'function': 'run'}])
        retained = ('import hashlib\nfrom pathlib import Path\n'
            'def sealed_files(repo):\n'
            '    path = Path(repo)/' + repr(PROFILE) + '\n'
            '    return {' + repr(PROFILE) + ':hashlib.sha256(path.read_bytes()).hexdigest()}\n')
        api = '''import hashlib,json,os
from pathlib import Path
COUNT = {"inputs":0,"verify":0,"full_record":0,"recheck":0}
def inputs(path, repo):
    COUNT["inputs"] += 1
    raw=Path(path).read_bytes(); profile=json.loads(raw)
    native=(Path(repo)/PROFILE).read_bytes()
    auth={"source_sha256":json.loads(native)["source_sha256"]}
    receipt=(Path(profile["venv"]).parent/"live-provision-receipt.json").read_bytes()
    return repo,path,profile,auth,hashlib.sha256(native).hexdigest(),hashlib.sha256(raw).hexdigest(),hashlib.sha256(receipt).hexdigest()
def verify(repo,path,profile,auth,auth_sha,profile_sha,receipt_sha,node,live):
    COUNT["verify"] += 1
    result=live.verify(profile)
    result.update(_profile=profile,_node_module=node,_live_module=live)
    return result
def full_record(context):
    COUNT["full_record"] += 1
    return {"fixture_only":True,"python":context["python"]}
def recheck(context):
    COUNT["recheck"] += 1
    context["_live_module"].verify(context["_profile"])
'''
        api = 'PROFILE=' + repr(PROFILE) + '\n' + api
        if self.fail_group:
            api += '\nraise RuntimeError("actual protocol private-body first failure")\n'
        native_function = next(n for n in ast.parse(checked(LIVE)).body
                               if isinstance(n, ast.FunctionDef) and n.name == '_native_producer_sources')
        live = '''import hashlib,os,sys
from pathlib import Path
_binding=None
FIRST=RuntimeError("actual protocol verifier first failure")
COUNT={"verify":0,"source":0,"authority":0,"runtime":0,"raw":0,"requirements39":0,"wheels40":0,"venv":0}
def bound_sources(repo, revision):
    global _binding
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    state,profile=__som_native_payload_provider__._native_state_for_live(sys.modules[__name__],repo,revision)
    producer=controls.load_module("m6.api.producer")
    __som_native_payload_provider__._native_accept_producer(sys.modules[__name__],producer)
    scope=controls.load_fragment("m6.api.payload.producer-sources")
    try:
        sources=scope._native_producer_sources(repo,revision)
        sources.read(SELF)
        sources.verify()
    finally:
        scope.close()
    if _binding is None:
        _binding={"repo":repo,"revision":revision,"producer":producer,"native_owner":state,
                  "reuse":None,"qualification":None,"runtime_inventory":None,"tools":None,"native":None}
    return _binding,sources,{}
def verify(profile):
    binding,_,_=bound_sources(Path(__file__).absolute().parents[3],os.environ["GITHUB_SHA"])
    for key in COUNT:COUNT[key]+=1
    raw=(Path(os.environ["RUNNER_TEMP"])/"raw-evidence.bin").read_bytes()
    if (Path(os.environ["RUNNER_TEMP"])/"fail-verify").exists():raise FIRST
    binding["reuse"].verify_installed_tree(Path(profile["runtime_root"]),binding["runtime_inventory"],binding["qualification"])
    receipt=(Path(profile["venv"]).parent/"live-provision-receipt.json").read_bytes()
    return {"status":"verified-live-inputs","python":profile["venv"]+"/bin/python",
        "pg_bin":profile["runtime_root"]+"/install/postgresql/bin",
        "live_receipt_sha256":hashlib.sha256(receipt).hexdigest(),
        "runtime_receipt_sha256":binding["native"]["reuse_receipt_sha256"],
        "closure_receipt_sha256":hashlib.sha256(raw).hexdigest(),
        "venv_inventory_sha256":hashlib.sha256(raw+b"venv fixture").hexdigest(),
        "requirements_count":39,"wheel_count":40}
'''
        live = 'from __future__ import annotations\nSELF=' + repr(SELF) + '\n' + live + '\n' + ast.unparse(native_function) + '\n'
        for key, body in (('retained', retained), ('api', api), ('live', live), ('node', 'VALUE="inert node protocol boundary"\n')):
            target_role, target, name = self.core.PRIVATE[key]
            binding = {'__som_native_active_controls__': {'kind': 'active-controls'}}
            if key == 'live':
                binding.update(__som_native_payload_provider__={'kind': 'role-module', 'role': PROVIDER_ROLE},
                    __som_native_OwnerState__={'kind': 'role-export', 'role': PROVIDER_ROLE, 'attribute': '_NativeOwner'},
                    __som_native_PayloadError__={'kind': 'role-export', 'role': PROVIDER_ROLE, 'attribute': 'PayloadError'},
                    __som_native_AdmittedPayload__={'kind': 'role-export', 'role': PROVIDER_ROLE, 'attribute': 'AdmittedPayload'})
            super().add_role(target_role, target, body, kind='private', module=name, group='m6.api.payload', bindings=binding)
        frag = super().add_role('m6.api.payload.producer-sources', LIVE, live, kind='fragment',
            module='_som_payload_fixture_producer_sources',
            callers=[{'role': 'm6.api.payload.live', 'function': 'bound_sources'}],
            bindings={'__som_native_ProducerSources__': {'kind': 'role-export', 'role': 'm6.api.producer', 'attribute': 'Sources'}})
        selected = ast.Module(body=[n for n in ast.parse(live).body if isinstance(n, ast.FunctionDef) and n.name=='_native_producer_sources'],type_ignores=[])
        frag['selector']={'kind':'top-level-functions','names':['_native_producer_sources'],
            'ast_sha256':hashlib.sha256(ast.dump(selected,include_attributes=False).encode()).hexdigest()}
        self.group_callers = [{'role':PROVIDER_ROLE,'function':'_native_acquire'}]
        # Base Fixture installs its fixed group policy after this hook returns.
        for rel in (BASE+'product_runtime/reuse_runtime.py', BASE+'product_runtime/qualify_runtime.py',
                    BASE+'product_runtime/native_runtime_verifier.py'):
            raw_data=checked(rel);self.raw[rel]=raw_data
            target=self.root/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw_data)
        sources={path:hashlib.sha256(raw).hexdigest() for path,raw in self.raw.items()}
        profile={'version':'som.m12.native-react-payload-owner-source.v1',
            'status':'draft_pending_root_review' if self.pending else 'reviewed_source_only',
            'complete':not self.pending,'pending':['protocol pending control'] if self.pending else [],
            'lifetime':'one-exact-Core-Context-owner','producer_role':'m6.api.producer',
            'producer_module':'som_react_api_verified_wheel_producer',
            'producer_sha256':EXPECTED[PRODUCER],'source_sha256':sources,'product_acceptance_passed':False}
        profile_raw=json.dumps(profile,sort_keys=True).encode();self.raw[PROFILE]=profile_raw
        target=self.root/PROFILE;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(profile_raw)
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],
            env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        git('init','--quiet');git('add','--','.')
        git('-c','user.name=Payload protocol fixture','-c','user.email=fixture@example.invalid',
            '-c','core.hooksPath=/dev/null','commit','--quiet','-m','synthetic protocol sources only')
        self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def run(self):
        self.domain.policy['groups']['m6.api.payload']['callers'] = self.group_callers
        return self.caller().run(self.controls)


class NativePayloadHandoffTests(unittest.TestCase):
    def test_repeat_collector_callbacks_and_nested_admit_use_one_owner_and_rerun_checks(self):
        f=PayloadFixture(self);p=f.provider
        first=p.admit(f.ctx,f.python);state=p._NATIVE_OWNER
        group,producer=state.group,state.producer
        second=p.admit(f.ctx,f.python)
        self.assertEqual(first,second)
        api,context=p.collector_validate(f.ctx,f.runner/'som-react-api-context.json')
        before=api.full_record(context)
        p.admit(f.ctx,f.python)
        api.recheck(context)
        self.assertEqual(api.full_record(context),before)
        self.assertIs(p._NATIVE_OWNER,state);self.assertIs(state.group,group);self.assertIs(state.producer,producer)
        self.assertIs(api,state.members['api'])
        self.assertEqual(state.members['live'].COUNT,{key:6 for key in state.members['live'].COUNT})
        self.assertEqual(api.COUNT,{'inputs':5,'verify':1,'full_record':2,'recheck':1})
        self.assertTrue(state.committed);self.assertFalse(state.failed)
        self.assertFalse(first.record()['product_acceptance_passed'])
        self.assertTrue(all(sys.modules.get(name) is state.members[key] for key,(_,_,name) in p.NATIVE_MEMBERS.items()))
        self.assertFalse(any(record.role=='m6.api.payload.producer-sources' for record in f.domain.records.values()))

    def test_preloaded_private_name_refuses_without_touching_its_owner(self):
        f=PayloadFixture(self);p=f.provider;name=p.NATIVE_MEMBERS['live'][2]
        foreign=ModuleType(name);sys.modules[name]=foreign
        try:
            with self.assertRaisesRegex(p.PayloadError,'initial native private/producer namespace is preloaded'):
                p.admit(f.ctx,f.python)
            self.assertIs(sys.modules[name],foreign);self.assertIsNone(p._NATIVE_OWNER)
            self.assertNotIn('m6.api.producer',f.domain.records)
        finally:
            if sys.modules.get(name) is foreign:del sys.modules[name]

    def test_preloaded_producer_refuses_before_foreign_hooks(self):
        f=PayloadFixture(self);p=f.provider;calls=[]
        foreign=ModuleType(p.PRODUCER_MODULE)
        def hook(name):calls.append(name);raise AssertionError('foreign producer hook')
        foreign.__getattr__=hook;sys.modules[p.PRODUCER_MODULE]=foreign
        try:
            with self.assertRaisesRegex(p.PayloadError,'initial native private/producer namespace is preloaded'):
                p.admit(f.ctx,f.python)
            self.assertEqual(calls,[]);self.assertIs(sys.modules[p.PRODUCER_MODULE],foreign)
            self.assertIsNone(p._NATIVE_OWNER)
        finally:
            if sys.modules.get(p.PRODUCER_MODULE) is foreign:del sys.modules[p.PRODUCER_MODULE]

    def test_wrong_owner_revision_foreign_state_and_producer_refuse_before_hooks(self):
        f=PayloadFixture(self);p=f.provider;p.admit(f.ctx,f.python);state=p._NATIVE_OWNER;calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign state hook')
        p._NATIVE_OWNER=Foreign()
        try:
            with self.assertRaisesRegex(p.PayloadError,'actual native payload owner state required'):
                p._native_current(f.ctx)
        finally:p._NATIVE_OWNER=state
        saved=state.producer;state.producer=Foreign()
        try:
            with self.assertRaisesRegex(p.PayloadError,'committed native owner binding/runtime identity differs'):
                p._native_current(f.ctx)
        finally:state.producer=saved
        old=f.ctx.helper_revision;f.ctx.helper_revision='0'*40
        try:
            with self.assertRaisesRegex(p.PayloadError,'actual native Context root/revisions differ'):
                p._native_current(f.ctx)
        finally:f.ctx.helper_revision=old
        clone=object.__new__(f.context_module.Context);clone.__dict__.update(f.ctx.__dict__)
        f.context_module._ACTIVE=clone
        try:
            with self.assertRaisesRegex(p.PayloadError,'native payload owner identity differs'):
                p._native_current(clone)
        finally:f.context_module._ACTIVE=f.ctx
        self.assertEqual(calls,[]);self.assertFalse(state.failed)

    def test_changed_real_source_refuses_before_a_second_checker_run(self):
        f=PayloadFixture(self);p=f.provider;p.admit(f.ctx,f.python)
        live=p._NATIVE_OWNER.members['live'];before=dict(live.COUNT)
        path=f.root/SELF;raw=path.read_bytes();path.write_bytes(raw+b'\n# actual source mutation\n')
        try:
            with self.assertRaisesRegex(ValueError,'source differs from committed Git bytes'):
                p.admit(f.ctx,f.python)
            self.assertEqual(live.COUNT,before)
        finally:path.write_bytes(raw)

    def test_first_verifier_failure_closes_private4_retains_producer_and_poisons(self):
        f=PayloadFixture(self);p=f.provider;(f.runner/'fail-verify').write_bytes(b'fixture failure')
        try:p.admit(f.ctx,f.python)
        except RuntimeError as primary:
            state=p._NATIVE_OWNER;self.assertIs(primary,state.members['live'].FIRST)
            self.assertIs(primary,p._NATIVE_FAILURE)
            import traceback
            self.assertIn('verify',[row.name for row in traceback.extract_tb(primary.__traceback__)])
            status=primary.__som_native_payload_partial_rollback__
            self.assertTrue(status['private4_closed'] and status['borrow_closed'])
            self.assertTrue(status['producer_retained_until_outer_owner_cleanup'])
            self.assertEqual(status['private_names_remaining'],[])
            self.assertIs(sys.modules[p.PRODUCER_MODULE],state.producer)
            self.assertIn('m6.api.producer',f.domain.records)
            runtime=f.ctx.cache['runtime']['reuse'];count=runtime.COUNT
            with self.assertRaisesRegex(p.PayloadError,'native payload owner is poisoned') as again:p.admit(f.ctx,f.python)
            self.assertIs(again.exception.__cause__,primary);self.assertEqual(runtime.COUNT,count)
        else:self.fail('original verifier error must propagate')

    def test_partial_group_body_failure_preserves_first_error_and_rolls_back_only_its_roles(self):
        f=PayloadFixture(self,fail_group=True);p=f.provider
        with self.assertRaisesRegex(RuntimeError,'actual protocol private-body first failure') as caught:
            p.admit(f.ctx,f.python)
        self.assertIs(p._NATIVE_FAILURE,caught.exception)
        self.assertIsNone(p._NATIVE_OWNER)
        self.assertTrue(all(name not in sys.modules for _,_,name in p.NATIVE_MEMBERS.values()))
        self.assertNotIn('m6.api.producer',f.domain.records)
        self.assertFalse(f.domain.closed);self.assertIn(PROVIDER_ROLE,f.domain.records)
        self.assertEqual(caught.exception.__som_native_payload_partial_rollback__['private_names_remaining'],[])

    def test_pending_source_profile_refuses_before_private_or_producer_code(self):
        f=PayloadFixture(self,pending=True);p=f.provider
        runtime=f.ctx.cache['runtime']['reuse'];before=runtime.COUNT
        with self.assertRaisesRegex(p.PayloadError,'native owner source profile remains draft or pending'):
            p.admit(f.ctx,f.python)
        self.assertIsNone(p._NATIVE_OWNER);self.assertEqual(runtime.COUNT,before)
        self.assertTrue(all(role not in f.domain.records for role,_,_ in p.NATIVE_MEMBERS.values()))
        self.assertNotIn('m6.api.producer',f.domain.records)

    def test_final_actual_core_owner_cleanup_removes_private4_producer_and_finder(self):
        f=PayloadFixture(self);p=f.provider;p.admit(f.ctx,f.python)
        names=[name for _,_,name in p.NATIVE_MEMBERS.values()]+[p.PRODUCER_MODULE]
        self.assertTrue(all(name in sys.modules for name in names))
        f.controls.close()
        self.assertTrue(f.domain.closed);self.assertTrue(all(name not in sys.modules for name in names))
        self.assertEqual(tuple(sys.meta_path),f.original_finders)
        self.assertIs(__import__('importlib.util',fromlist=['util']).spec_from_file_location,f.original_spec)
        with self.assertRaises(ValueError):p.admit(f.ctx,f.python)


if __name__=='__main__':unittest.main()
