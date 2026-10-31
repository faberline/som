"""Real Git sentinel and real pending-authority fixtures. GitHub only."""
import json
import importlib.util
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
REVISION = os.environ['HELPER_REVISION']
SELF = 'som-rs/scripts/hosted_readiness/run_full_products.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
AUTHORITY = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'


def real_source(name):
    raw = (REPO / name).read_bytes()
    assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
                                          'cat-file', 'blob', REVISION + ':' + name])
    return raw


def loaded(name, path):
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(REPO / path), None, ''
    exec(compile(real_source(path), module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


d = loaded('entry_driver_under_test', SELF)
context_source = loaded('entry_real_source_reader', CONTEXT)


class RealEntryBarrierTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.work = self.root / 'work'; self.work.mkdir()
        self.git('init', '--quiet')
        self.write(SELF, real_source(SELF))
        self.marker = self.root / 'entry-executed'
        self.write('sentinel.py', ('from pathlib import Path\nPath(' + repr(str(self.marker)) + ').write_text("BAD")\n').encode())
        self.context_marker = self.root / 'context-import-executed'
        self.write(CONTEXT, ('from pathlib import Path\nPath(' + repr(str(self.context_marker)) + ').write_text("BAD")\n').encode())
        for name in ('full_product_context', '_som_full_native_source_admission'):
            self.addCleanup(sys.modules.pop, name, None)

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.repo), *args],
                                       stderr=subprocess.DEVNULL)

    def write(self, name, raw):
        path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def commit(self):
        self.git('add', '--all')
        self.git('-c', 'user.name=Source Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--no-verify', '-m', 'source fixture')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def context_file(self, revision):
        path = self.root / 'context.json'
        path.write_text(json.dumps({'repo': str(self.repo), 'helper_revision': revision,
            'candidate_revision': revision, 'work_root': str(self.work), 'stage': 'phase1',
            'shard': {'index': 0, 'count': 1}, 'qa_mode': 'native-qa-v1'}))
        return path

    def test_actual_git_tracked_unlisted_entry_is_not_read_or_executed(self):
        revision = self.commit(); path = self.context_file(revision)
        actual = context_source.SourceBytes(self.repo, revision)
        real_read = actual.read; calls = []
        def read(name):
            calls.append(name)
            return real_read(name)
        actual.read = read
        authority = {'source_authority_active': True, 'admitted_repository_python_sources': ['allowed.py']}
        class TinyContext:
            def __init__(inner, _):
                inner.sources, inner.qa_source_authority = actual, authority
            def verify(inner): actual.verify()
        tiny = SimpleNamespace(Context=TinyContext, relative=context_source.relative,
                               __som_native_source_authority__=authority)
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.root), 'SOM_FULL_PRODUCT_CONTEXT': str(path)}), \
             mock.patch.object(d, 'load_context_module', return_value=tiny), self.assertRaisesRegex(ValueError, 'entry itself'):
            d.execute_tool(['sentinel.py'])
        self.assertNotIn('sentinel.py', calls)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.git('show', revision + ':sentinel.py'), (self.repo / 'sentinel.py').read_bytes())
        result = json.loads(next((self.work / 'checker-postchecks').iterdir()).read_bytes())
        self.assertFalse(result['entry_executed'])

    def test_actual_pending_native_policy_refuses_before_context_import_or_entry(self):
        bootstrap_path = 'som-rs/scripts/hosted_readiness/test_native_source_bootstrap.py'
        protocol = loaded('entry_bootstrap_protocol_fixture', bootstrap_path)
        # Only GitHub API/workflow metadata is a protocol double. Graph.admit_source,
        # the source reader and authority.verify remain their actual source bodies.
        fixture = protocol.Fixture(self, graph_suffix='''
def source_workflow_binding(candidate, environ=None, reader=None):
    return {'explicit_protocol_workflow_double': True}
''')
        for name in ('som-rs/contracts/rust-output-v1/phase2/pins.json',
                     'som-rs/contracts/rust-output-v1/phase2/behavior.json',
                     'som-rs/evidence/rust-compiler-contract-amendment-004.json'):
            target = fixture.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(real_source(name))
        for name in (CONTEXT, 'sentinel.py'):
            target = fixture.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.repo / name).read_bytes())
        # This is a real pending receipt, not a mocked Context or verifier error.
        (fixture.root / 'som-rs/evidence/rust-compiler-contract-amendment-005.json').write_bytes(
            json.dumps({'amendment': 5, 'status': 'pending',
                        'previous_receipt': 'som-rs/evidence/rust-compiler-contract-amendment-004.json',
                        'previous_receipt_sha256': '2f0c12e6fed43b8364649a0afff207e72b15738179cf56a8ff69036e4534f42c'}).encode())
        fixture.git('add', '--all')
        fixture.git('commit', '-m', 'Actual pending authority before QA entry')
        fixture.revision = fixture.git('rev-parse', 'HEAD').strip()
        for key in ('GITHUB_SHA', 'GITHUB_WORKFLOW_SHA'):
            fixture.env[key] = os.environ[key] = fixture.revision
        fixture.run['head_sha'] = fixture.job['head_sha'] = fixture.revision
        fixture.run['referenced_workflows'][0]['sha'] = fixture.revision
        fixture.select_job('experimental-native-compile')
        forbidden_blobs = {fixture.git('rev-parse', fixture.revision + ':' + name).strip()
                           for name in ('sentinel.py', CONTEXT)}
        pending_blob = fixture.git('rev-parse', fixture.revision +
            ':som-rs/evidence/rust-compiler-contract-amendment-005.json').strip()
        real_check_output = subprocess.check_output
        with fixture.bootstrap() as bootstrap, \
             mock.patch.object(subprocess, 'check_output', wraps=real_check_output) as calls:
            graph = bootstrap.load_admission_module('same-candidate-graph')
            with self.assertRaisesRegex(ValueError, '^numbered authority is pending, stale or has a false chain$') as first:
                graph.admit_source(fixture.root, fixture.revision, fixture.revision)
            self.assertIs(type(first.exception), ValueError)
            self.assertNotIn('_som_native_trusted_bootstrap_entry', bootstrap._records)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.context_marker.exists())
        blob_reads = [str(argument) for call in calls.call_args_list for argument in call.args[0]]
        self.assertFalse(any(value.endswith(':sentinel.py') or value.endswith(':' + CONTEXT) for value in blob_reads))
        self.assertFalse(forbidden_blobs.intersection(blob_reads))
        self.assertIn(pending_blob, blob_reads)
        self.assertFalse((self.work / 'checker-postchecks').exists())

    def legacy_matching_pyc(self, mode, fail_entry):
        helper_name = '_som_legacy_barrier_helper'
        self.addCleanup(sys.modules.pop, helper_name, None)
        sys.modules.pop(helper_name, None)
        bad = self.root / 'cached-body-executed'
        good = self.root / 'raw-helper-result'
        self.write(helper_name + '.py', b'VALUE = "checked raw Git source"\n')
        entry = ('import ' + helper_name + '\nfrom pathlib import Path\n'
                 + 'Path(' + repr(str(good)) + ').write_text(' + helper_name + '.VALUE)\n')
        if fail_entry:
            entry += 'raise RuntimeError("actual legacy entry failure")\n'
        self.write('legacy_entry.py', entry.encode())
        revision = self.commit()
        path = self.context_file(revision)
        data = json.loads(path.read_bytes())
        if mode is None:
            del data['qa_mode']
        else:
            data['qa_mode'] = mode
        path.write_text(json.dumps(data))
        helper = self.repo / (helper_name + '.py')
        info = helper.stat()
        cached = Path(importlib.util.cache_from_source(str(helper)))
        cached.parent.mkdir()
        malicious = ('from pathlib import Path\nPath(' + repr(str(bad))
                     + ').write_text("BAD")\nVALUE = "cached malicious body"\n')
        cached.write_bytes(importlib.util.MAGIC_NUMBER
            + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
            + marshal.dumps(compile(malicious, str(helper), 'exec', dont_inherit=True)))
        # The cache has the real tracked helper's timestamp and size. Normal Python accepts it.
        actual = context_source.SourceBytes(self.repo, revision)
        class TinyContext:
            def __init__(inner, _): inner.sources = actual
            def verify(inner): actual.verify()
        tiny = SimpleNamespace(Context=TinyContext, relative=context_source.relative,
                               install_source_finder=context_source.install_source_finder,
                               __som_native_source_authority__=None)
        before_finders, before_path = tuple(sys.meta_path), list(sys.path)
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.root), 'SOM_FULL_PRODUCT_CONTEXT': str(path)}), \
             mock.patch.object(d, 'load_context_module', return_value=tiny):
            if fail_entry:
                with self.assertRaisesRegex(RuntimeError, 'actual legacy entry failure'):
                    d.execute_tool(['legacy_entry.py'])
            else:
                self.assertEqual(d.execute_tool(['legacy_entry.py']), 0)
        self.assertEqual(good.read_text(), 'checked raw Git source')
        self.assertFalse(bad.exists())
        self.assertIn(helper_name + '.py', actual.pins)
        self.assertEqual(sys.modules[helper_name].__cached__, None)
        self.assertEqual(tuple(sys.meta_path), before_finders)
        self.assertEqual(sys.path, before_path)
        result = json.loads(next((self.work / 'checker-postchecks').iterdir()).read_bytes())
        self.assertTrue(result['entry_executed'])
        self.assertTrue(result['source_runtime_tools_inputs_unchanged'])
        self.assertEqual('error' in result, fail_entry)

    def test_default_legacy_import_uses_raw_git_and_restores_own_guard(self):
        self.legacy_matching_pyc(None, False)

    def test_explicit_preparation_import_uses_raw_git_on_entry_failure(self):
        self.legacy_matching_pyc('experimental-preparation', True)

    def test_explicit_preparation_bootstrap_accepts_its_supported_mode(self):
        self.write(CONTEXT, b'BOOTSTRAP_MARKER = "checked explicit preparation"\n')
        revision = self.commit()
        module = d.load_context_module(self.repo, revision, 'experimental-preparation', revision)
        self.assertEqual(module.BOOTSTRAP_MARKER, 'checked explicit preparation')
        self.assertIsNone(module.__som_native_source_authority__)


if __name__ == '__main__': unittest.main(verbosity=2)
