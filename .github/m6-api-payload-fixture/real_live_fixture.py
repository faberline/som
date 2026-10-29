"""Exercise actual live verifier with synthetic source/receipt authority only.

This is not an independent review, sealed amendment, or product acceptance.
The external test workflow pins the exact fixture checkout. It constructs a
fresh runtime and venv in the same job before invoking this script.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
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
ctx = SimpleNamespace(qa_mode='native-qa-v1', repo=repo, helper_revision=revision,
    candidate_revision=revision, sources=sources, data={'runtime_root':str(runtime_root)},
    qa_import_controls=SimpleNamespace(verify_installed=lambda: None),
    qa_source_authority={'source_authority_active':True,
        'admitted_repository_python_sources':[provider.SELF, provider.RETAINED, provider.API, provider.LIVE]},
    runtime=lambda: {'tools':{'python':str(base)}})
payload = provider.admit(ctx, python)
record = payload.record()
assert record['requirements_count'] == 39 and record['wheel_count'] == 40
assert record['site'] == str(python.parent.parent / 'lib/python3.12/site-packages')
assert record['product_acceptance_passed'] is False and record['pth_executed'] is False
assert record['source_revision'] == revision
print(json.dumps({'fixture':'synthetic-authority-real-live-verifier', 'product_acceptance_passed':False,
                  'record':record}, sort_keys=True))
