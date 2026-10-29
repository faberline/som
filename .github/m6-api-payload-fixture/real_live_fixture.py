"""Exercise actual live verifier with synthetic source/receipt authority only.

This is not an independent review, sealed amendment, or product acceptance.
The external test workflow pins the exact fixture checkout. It constructs a
fresh runtime and venv in the same job before invoking this script.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import react_api_python_payload as provider

repo = Path(os.environ['GITHUB_WORKSPACE'])
temp = Path(os.environ['RUNNER_TEMP'])
revision = os.environ['GITHUB_SHA']
assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip() == revision

class GitSources:
    def read(self, name):
        raw = (repo / name).read_bytes()
        committed = subprocess.check_output(['git', '-C', str(repo), 'show', revision + ':' + name])
        if raw != committed:
            raise ValueError('fixture Git source differs: ' + name)
        return raw

sources = GitSources()
authority_path = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
authority_raw = sources.read(authority_path)
authority = json.loads(authority_raw)
files = dict(authority['source_sha256'])
files[authority_path] = hashlib.sha256(authority_raw).hexdigest()
real_loader = provider.load_source

def synthetic_receipt_loader(ctx, name, module_name):
    if name != provider.RETAINED:
        return real_loader(ctx, name, module_name)
    # This is deliberately a test double. Real React amendment 003 is unsealed.
    if module_name in sys.modules:
        raise ValueError('fixture verifier already loaded')
    module = ModuleType(module_name)
    module.__file__ = str(repo / name)
    module.sealed_files = lambda _repo: dict(files)
    sys.modules[module_name] = module
    return module

provider.load_source = synthetic_receipt_loader
runtime_root = temp / 'som-product-runtime-qualification'
base = runtime_root / 'install/python/bin/python3.12'
python = temp / 'som-react-python-closure/environments/react-api-qa/venv/bin/python'
assert os.environ['SOM_NATIVE_RUNTIME_ROOT'] == str(runtime_root)
# Execute the real Context.runtime() and source-bound admitted_runtime(), not
# a synthetic runtime result. Only its constructor/source controls are fixtures.
context_path = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
context_raw = sources.read(context_path)
context_module = ModuleType('_fixture_real_context')
context_module.__file__ = str(repo / context_path)
exec(compile(context_raw, context_module.__file__, 'exec', dont_inherit=True), context_module.__dict__)
ctx = context_module.Context.__new__(context_module.Context)
ctx.qa_mode, ctx.repo, ctx.helper_revision, ctx.candidate_revision = 'native-qa-v1', repo, revision, revision
ctx.sources = context_module.SourceBytes(repo, revision)
ctx.modules, ctx.cache, ctx.module_lock = {}, {}, threading.RLock()
ctx.data = {'runtime_root':str(runtime_root)}
ctx.qa_import_controls = SimpleNamespace(verify_installed=lambda: None)
ctx.qa_source_authority = {'source_authority_active':True,
    'admitted_repository_python_sources':sorted(provider.REPOSITORY_PYTHON_EXECUTION_CLOSURE)}
runtime = ctx.runtime()
assert runtime['tools']['python'] == base
assert runtime['identity']['reuse_receipt_sha256']
collector_path = 'som-rs/scripts/react_output_acceptance/run_acceptance.py'
collector_raw = sources.read(collector_path)

def load_collector():
    collector = ModuleType('_fixture_collector')
    collector.__file__ = str(repo / collector_path)
    old_argv = sys.argv
    try:
        sys.argv = [str(repo / collector_path), '--api-profile']
        exec(compile(collector_raw, collector.__file__, 'exec', dont_inherit=True), collector.__dict__)
    finally:
        sys.argv = old_argv
    return collector

# The real collector loader refuses any foreign preload before executing it.
foreign = ModuleType('retained_evidence')
sys.modules['retained_evidence'] = foreign
try:
    try:
        load_collector()
    except ValueError:
        pass
    else:
        raise AssertionError('collector accepted foreign retained preload')
finally:
    assert sys.modules.pop('retained_evidence') is foreign

collector = load_collector()
retained = sys.modules['retained_evidence']
assert retained is collector.retained_evidence
payload = provider.admit(ctx, python)
record = payload.record()
assert record['requirements_count'] == 39 and record['wheel_count'] == 40
assert record['site'] == str(python.parent.parent / 'lib/python3.12/site-packages')
assert record['product_acceptance_passed'] is False and record['pth_executed'] is False
assert record['source_revision'] == revision
assert sys.modules['retained_evidence'] is retained
def native_branch(relative, initial):
    raw = sources.read(relative)
    parsed = ast.parse(raw, filename=str(repo / relative))
    matches = [item for item in ast.walk(parsed) if isinstance(item, ast.If)
               and isinstance(item.test, ast.BoolOp) and 'native_context.qa_mode' in ast.unparse(item.test)
               and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                       and call.func.attr == 'collector_validate' for call in ast.walk(item))]
    assert len(matches) == 1, relative
    segment = ast.fix_missing_locations(ast.Module(body=[matches[0]], type_ignores=[]))
    env = {'native_context':ctx, 'retained_evidence':retained,
           'args':SimpleNamespace(api_profile=temp / 'som-react-api-context.json'),
           'repo':repo, 'REPO':repo, 'linux_qa_profile':None, 'sys':sys, **initial}
    exec(compile(segment, str(repo / relative), 'exec', dont_inherit=True), env)
    return env

collector_result = native_branch(collector_path, {'api_module':None})
api, validation = collector_result['api_module'], collector_result['api_context']
assert sys.modules['retained_evidence'] is retained
assert validation['python'] == str(python)
assert api.full_record(validation)['version'] == 'som.react-linux-api-full-validation.v1'
api.recheck(validation)
case_result = native_branch('som-rs/scripts/react_output_acceptance/check_products.py',
                            {'api_context_module':None})
case_result['api_context_module'].recheck(case_result['api_qa'])
provider.admit(ctx, python)
assert sys.modules['retained_evidence'] is retained
print(json.dumps({'fixture':'synthetic-authority-real-live-verifier', 'product_acceptance_passed':False,
                  'context_runtime':'real-Context.runtime-with-fixture-construction',
                  'collector_loader':'actual-collector-source-positive-and-foreign-preload-refusal',
                  'collector_validation':'source-extracted-native-branches-actual-api-verify-full-record-recheck',
                  'record':record}, sort_keys=True))
