"""Synthetic retained API evidence tests, GitHub-hosted runners only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import linux_api_evidence as evidence


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture():
    root = Path(tempfile.mkdtemp(dir=os.environ['RUNNER_TEMP'])).resolve()
    repo, runtime, closure = root / 'repo', root / 'runtime', root / 'closure'
    repo.mkdir()
    (runtime / 'install/python/bin').mkdir(parents=True)
    python = runtime / 'install/python/bin/python3.12'
    python.write_bytes(b'synthetic python')
    python.chmod(0o755)
    (runtime / 'install/python/bin/python3').symlink_to('python3.12')
    named = closure / 'environments/react-api-qa'
    (named / 'venv/bin').mkdir(parents=True)
    (named / 'venv/bin/python').symlink_to(python)
    (named / 'venv/value').write_bytes(b'installed bytes')
    frozen = closure / 'closures' / evidence.KEY
    frozen.mkdir(parents=True)
    (closure / 'logs').mkdir()
    (runtime / 'qualification-evidence').mkdir()
    source_input = runtime.parent / 'react39-inputs'
    (source_input / 'audit').mkdir(parents=True)
    for path in (runtime / 'receipt.json', runtime / 'artifact-origin.json',
                 runtime / 'qualification-evidence/receipt.json', runtime / 'qualification-evidence/installed-files.json',
                 runtime / 'qualification-evidence/source-pins.json', runtime / 'qualification-evidence/build-intermediates.json',
                 source_input / 'source-run.json', source_input / 'source-artifact.json',
                 source_input / 'audit-run.json', source_input / 'audit-artifact.json',
                 source_input / 'audit/react-python-independent-audit.json',
                 frozen / 'requirements.selected.lock',
                 frozen / 'requirements.offline.lock', named / 'live-provision-receipt.json',
                 named / 'live-installed-files.json', named / 'live-installed-metadata.json',
                 named / 'pyproject.toml', named / 'uv.lock', named / 'requirements.in',
                 closure / 'probe-installed-metadata.py',
                 *(closure / 'logs' / (label + '.' + stream)
                   for label in ('fresh-offline-frozen-sync', 'fresh-offline-no-site-metadata')
                   for stream in ('stdout', 'stderr'))):
        path.write_bytes(path.name.encode())
    wheel_rows = []
    for number in range(40):
        name = f'pkg{number}'
        wheel = frozen / 'wheel-audits' / name / 'inventory.json'
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(name.encode())
        wheel_rows.append({'name': name, 'audit_sha256': digest(wheel.read_bytes())})
    (frozen / 'receipt.json').write_text(json.dumps({'wheels': wheel_rows}))
    source_rel = 'som-rs/scripts/react_output_acceptance/example.py'
    source = repo / source_rel
    source.parent.mkdir(parents=True)
    source.write_bytes(b'source bytes')
    auth_rel = 'som-rs/contracts/react-output-v1/linux-api-qa-authority.json'
    authority_path = repo / auth_rel
    authority_path.parent.mkdir(parents=True)
    authority = {'source_sha256': {source_rel: digest(source.read_bytes())}}
    authority_raw = json.dumps(authority).encode()
    authority_path.write_bytes(authority_raw)
    profile = {'runtime_root': str(runtime), 'closure_root': str(closure), 'venv': str(named / 'venv')}
    profile_path = root / 'api-profile.json'
    profile_path.write_text(json.dumps(profile))
    module = SimpleNamespace(AUTHORITY=auth_rel, authority=lambda _: (authority, digest(authority_raw)),
                             read=lambda path, _: path.read_bytes(), parse=json.loads)
    context = {'_repo': repo, '_profile_path': profile_path, '_source_shas': authority['source_sha256'],
               'authority_sha256': digest(authority_raw)}
    out = repo / 'run/api-qa-evidence'
    out.mkdir(parents=True)
    return root, repo, runtime, named, out, context, module


class RetainedApiEvidenceFixtures(unittest.TestCase):
    def test_retains_source_inputs_and_matching_physical_inventories(self):
        root, repo, runtime, named, out, context, module = fixture()
        state, live = evidence.begin(out, context, module)
        self.assertTrue(evidence.finish(out, context, module, state, live))
        final = json.loads((out / 'retained.json').read_bytes())
        self.assertEqual(final['status'], 'passed')
        self.assertEqual((out / final['profile']['before']['path']).read_bytes(), context['_profile_path'].read_bytes())
        self.assertEqual((out / final['inputs']['live-installed-files.json']['path']).read_bytes(),
                         (named / 'live-installed-files.json').read_bytes())
        runtime_before = json.loads((out / 'runtime-before.json').read_bytes())
        venv_before = json.loads((out / 'venv-before.json').read_bytes())
        self.assertEqual(runtime_before, json.loads((out / 'runtime-after.json').read_bytes()))
        self.assertEqual(venv_before, json.loads((out / 'venv-after.json').read_bytes()))
        self.assertEqual(venv_before['entries']['bin/python']['target'], str(runtime / 'install/python/bin/python3.12'))

    def test_venv_mutation_fails_and_saves_observed_after(self):
        _, _, _, named, out, context, module = fixture()
        state, live = evidence.begin(out, context, module)
        (named / 'venv/value').write_bytes(b'changed')
        self.assertFalse(evidence.finish(out, context, module, state, live))
        self.assertEqual(json.loads((out / 'retained.json').read_bytes())['status'], 'failed')
        self.assertTrue((out / 'venv-after.json').is_file())

    def test_external_link_and_missing_input_refuse(self):
        _, _, runtime, named, out, context, module = fixture()
        (named / 'venv/escape').symlink_to('/etc/passwd')
        with self.assertRaises(evidence.EvidenceError):
            evidence.snapshot(named / 'venv', runtime / 'install/python/bin/python3.12')
        with self.assertRaises((evidence.EvidenceError, OSError)):
            evidence.retain_file(named / 'missing', out / 'missing', out)


if __name__ == '__main__':
    unittest.main()
