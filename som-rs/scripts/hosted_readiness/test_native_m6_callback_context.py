"""GitHub-only M6 Context location binding tests.

The whole actual driver executes with real physical paths and checked source
bytes. Sources/admission/profile documents are explicit data doubles. These
tests never admit native sources, original M6 inputs, a runtime or products.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub Linux -I -S -B required')
HERE=Path(__file__).absolute().parent
SOURCE=HERE/'run_full_products.py'
EXPECTED_DRIVER_SHA='b267bb52ae7f43ee6e7d649c57a44c313a2a6d86f69427d454189cb9e652fc82'
raw=SOURCE.read_bytes()
if hashlib.sha256(raw).hexdigest()!=EXPECTED_DRIVER_SHA:
    raise RuntimeError('exact reviewed driver source required')
driver=ModuleType('_som_m6_context_locations_fixture')
driver.__file__,driver.__package__=str(SOURCE),''
sys.modules[driver.__name__]=driver
exec(compile(raw,driver.__file__,'exec',dont_inherit=True),driver.__dict__)
PROFILE='som-rs/scripts/hosted_readiness/inputs/recorded-before-profile-v1.json'

class Sources:
    """Only literal source records are doubled; no source admission is granted."""
    def __init__(self):self.calls=[]
    def read(self,path):
        self.calls.append(path)
        if path==PROFILE:return b'{"explicit_data_double":true}'
        if path in driver.CALLBACK_ADAPTERS.values():return b'# explicit adapter bytes data double\n'
        raise AssertionError('unexpected source read')

class M6CallbackContextTests(unittest.TestCase):
    def fixture(self,gate='m6-react003'):
        temporary=tempfile.TemporaryDirectory(prefix='som-m6-context-locations-')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();repo=root/'repo';repo.mkdir();(repo/'.local').mkdir()
        baseline=root/'baseline';baseline.mkdir()
        args=SimpleNamespace(repo=repo,mode='sealed-candidate-products',prepare_context_only=True,
            callback_gate=gate,baseline_input=[[PROFILE,str(baseline)]],m6_react_input=None,
            artifact_inputs=root/'artifact-catalog/input.json')
        args.artifact_inputs.parent.mkdir()
        for name in ('assembly_root','wheel_root','runtime_root','rust_tools_root','cargo_root',
                     'carrier_root','python_inputs_root','rust_inputs_root','current_companions_root','build_provenance_root'):
            path=root/name;path.mkdir();setattr(args,name,path)
        out=root/'prepared-context'
        inputs={}
        for name in ('legacy_run','rust_baseline_run'):
            path=root/name;path.mkdir();inputs[name]=str(path)
        for name in ('qa_profile','api_profile','api_closure_tar'):
            path=root/name;path.write_bytes(b'explicit non-admitted input data');inputs[name]=str(path)
        inputs.update(baseline_profile=PROFILE,collector_out=str(repo/'.local/collector'),audit_out=str(repo/'.local/audit'))
        args.m6_react_input=[[k,v] for k,v in inputs.items()] if gate=='m6-react003' else None
        patch=mock.patch.dict(os.environ,{'RUNNER_TEMP':str(root)})
        patch.start();self.addCleanup(patch.stop)
        sources=Sources();admission={'admitted_repository_python_sources':list(driver.CALLBACK_ADAPTERS.values())}
        return args,sources,admission,out,inputs

    def test_all_eight_explicit_locations_bind_exactly_without_creating_outputs(self):
        args,sources,admission,out,inputs=self.fixture()
        got=driver.callback_context_bindings(args,sources,admission,out)
        self.assertEqual(got['m6_react_inputs'],inputs)
        self.assertEqual(got['baseline_inputs'],{PROFILE:args.baseline_input[0][1]})
        path=driver.CALLBACK_ADAPTERS['m6-react003']
        self.assertEqual(got['gate_adapters'],{'m6-react003':{'path':path,'sha256':hashlib.sha256(sources.read(path)).hexdigest()}})
        self.assertFalse(out.exists())
        self.assertFalse(Path(inputs['collector_out']).exists());self.assertFalse(Path(inputs['audit_out']).exists())
        self.assertFalse(any(key in got for key in ('source_authority_active','product_acceptance_passed','complete')))

    def test_missing_duplicate_unknown_and_foreign_names_refuse_without_hooks(self):
        args,sources,admission,out,inputs=self.fixture();original=list(args.m6_react_input);calls=[]
        class Foreign:
            def __hash__(self):calls.append('hash');raise AssertionError('foreign hash hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign equality hook')
        for pairs,pattern in [(original[:-1],'all eight'),(original[:-1]+[original[0]],'unknown or duplicate'),
                              (original[:-1]+[['not_an_input','value']],'unknown or duplicate'),
                              (original[:-1]+[[Foreign(),'value']],'unknown or duplicate')]:
            args.m6_react_input=pairs
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValueError,pattern):driver.callback_context_bindings(args,sources,admission,out)
        self.assertEqual(calls,[])

    def test_exact_baseline_and_physical_types_are_required(self):
        args,sources,admission,out,inputs=self.fixture()
        changed=dict(inputs);changed['baseline_profile']='other.json';args.m6_react_input=list(map(list,changed.items()))
        with self.assertRaisesRegex(ValueError,'exact recorded-before'):driver.callback_context_bindings(args,sources,admission,out)
        changed=dict(inputs);changed['api_profile']=inputs['legacy_run'];args.m6_react_input=list(map(list,changed.items()))
        with self.assertRaisesRegex(ValueError,'wrong physical file type'):driver.callback_context_bindings(args,sources,admission,out)
        linked=Path(inputs['legacy_run']).parent/'linked-api';linked.symlink_to(inputs['api_profile'])
        changed=dict(inputs);changed['api_profile']=str(linked);args.m6_react_input=list(map(list,changed.items()))
        with self.assertRaisesRegex(ValueError,'physical and absolute'):driver.callback_context_bindings(args,sources,admission,out)

    def test_output_collision_protected_inputs_and_missing_admission_refuse(self):
        args,sources,admission,out,inputs=self.fixture()
        changed=dict(inputs);changed['audit_out']=changed['collector_out'];args.m6_react_input=list(map(list,changed.items()))
        with self.assertRaisesRegex(ValueError,'overlaps protected'):driver.callback_context_bindings(args,sources,admission,out)
        args.m6_react_input=list(map(list,inputs.items()));args.wheel_root=Path(inputs['collector_out']).parent
        with self.assertRaisesRegex(ValueError,'overlaps protected'):driver.callback_context_bindings(args,sources,admission,out)
        args.wheel_root=Path(inputs['legacy_run']).parent/'wheel_root'
        with self.assertRaisesRegex(ValueError,'not in actual native source admission'):
            driver.callback_context_bindings(args,sources,{'admitted_repository_python_sources':[]},out)
        self.assertFalse(Path(inputs['collector_out']).exists())

    def test_original_ts_shape_and_inactive_return_stay_exact_and_m6_values_cannot_leak(self):
        args,sources,admission,out,inputs=self.fixture('typescript-regression')
        got=driver.callback_context_bindings(args,sources,admission,out)
        self.assertEqual(set(got),{'baseline_inputs','gate_adapters'})
        args.m6_react_input=[['baseline_profile',PROFILE]]
        with self.assertRaisesRegex(ValueError,'fixed M6 callback gate'):driver.callback_context_bindings(args,sources,admission,out)
        args.callback_gate=None;args.baseline_input=[]
        with self.assertRaisesRegex(ValueError,'fixed M6 callback gate'):driver.callback_context_bindings(args,sources,admission,out)
        args.m6_react_input=None
        self.assertEqual(driver.callback_context_bindings(args,sources,admission,out),{})

if __name__=='__main__':unittest.main()
