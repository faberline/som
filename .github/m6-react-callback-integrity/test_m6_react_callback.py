"""Synthetic callback checks. Run on GitHub-hosted runners only."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import m6_react_callback as subject


class CallbackFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        (self.repo / '.local').mkdir()
        self.assembly = root / 'assembly'; self.assembly.mkdir()
        self.carrier = root / 'carrier'; self.carrier.mkdir()
        self.candidate = root / 'candidate'; self.candidate.mkdir()
        self.rev = 'a' * 40
        binary = b'fixture binary'
        (self.candidate / 'som').write_bytes(binary)
        digest = subject.sha(binary)
        (self.candidate / 'build.json').write_text(json.dumps({
            'tree_before': {'head': self.rev}, 'complete': True, 'binary_sha256': digest}))
        (self.candidate / 'runtime.json').write_text(json.dumps({'binary_sha256': digest, 'port': 18978}))
        self.legacy = root / 'legacy'; self.legacy.mkdir()
        self.rust = root / 'rust'; self.rust.mkdir()
        self.qa = root / 'node.json'; self.qa.write_text('{}')
        self.api = root / 'api.json'; self.api.write_text('{}')
        self.tar = root / 'closure.tar'; self.tar.write_bytes(b'fixture')
        self.ctx = SimpleNamespace(repo=self.repo, data={'assembly_root': str(self.assembly),
            'candidate_revision': self.rev, 'runtime_root': str(root)})
        self.graph = SimpleNamespace(verify_compile_proof=lambda root, rev: {
            'binary_identity': {'sha256': digest}})
        self.args = SimpleNamespace(repo=self.repo, qa_mode='native-qa-v1', assembly_root=self.assembly,
            candidate_revision=self.rev, carrier_root=self.carrier, candidate_dir=self.candidate,
            port=18978, baseline='http://127.0.0.1:18979', legacy_run=self.legacy,
            rust_baseline_run=self.rust, qa_profile=self.qa, api_profile=self.api,
            api_closure_tar=self.tar, collector_out=self.repo / '.local' / 'run', out=root / 'callback')

    def test_current_candidate_and_physical_output_are_admitted(self):
        proof = subject.validate_args(self.args, self.ctx, self.graph)
        self.assertEqual(proof['binary_identity']['sha256'], subject.sha(b'fixture binary'))
        self.assertFalse(self.args.out.exists())
        self.assertFalse(self.args.collector_out.exists())

    def test_wrong_candidate_bytes_revision_port_and_baseline_refuse(self):
        for case in ('binary', 'revision', 'port', 'baseline'):
            with self.subTest(case=case):
                with mock.patch.object(self.args, 'baseline', 'http://127.0.0.1:18980' if case == 'baseline' else self.args.baseline), \
                     mock.patch.object(self.args, 'port', 8765 if case == 'port' else self.args.port), \
                     mock.patch.object(self.args, 'candidate_revision', 'b' * 40 if case == 'revision' else self.rev), \
                     mock.patch.object(self.graph, 'verify_compile_proof', return_value={
                         'binary_identity': {'sha256': 'f' * 64 if case == 'binary' else subject.sha(b'fixture binary')}}):
                    with self.assertRaises(ValueError): subject.validate_args(self.args, self.ctx, self.graph)
                self.assertFalse(self.args.out.exists())

    def test_output_outside_repo_or_existing_output_refuses(self):
        for collector in (self.repo.parent / 'outside', self.repo / '.local'):
            with self.subTest(collector=collector), mock.patch.object(self.args, 'collector_out', collector):
                with self.assertRaises(ValueError): subject.validate_args(self.args, self.ctx, self.graph)

    def test_unreviewed_source_and_unimplemented_verifier_fail_closed(self):
        self.assertFalse(subject.GRAPH_INTEGRATION_REVIEWED)
        with self.assertRaises(ValueError):
            subject.verify_result(self.args.out, lambda _: b'', self.rev, self.rev,
                                  repo=self.repo, proof_roots={})

    def test_disabled_guard_precedes_any_graph_or_context_work(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
                'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux'}), \
             mock.patch.object(subject.sys, 'platform', 'linux'), \
             mock.patch.object(subject, 'physical') as physical, \
             mock.patch.object(subject, 'admit') as admit, \
             mock.patch.object(subject, 'validate_args') as validate:
            with self.assertRaisesRegex(ValueError, 'graph integration is unreviewed'):
                subject.run(self.args)
        physical.assert_not_called()
        admit.assert_not_called()
        validate.assert_not_called()

    def test_independent_bootstrap_refuses_before_git_or_import(self):
        with mock.patch.object(subject, 'git') as git, \
             mock.patch.object(subject, 'ModuleType') as module:
            with self.assertRaisesRegex(ValueError, 'pre-import source authority is not implemented'):
                subject.admit(self.repo, self.rev, self.rev, self.repo / 'context.json')
        git.assert_not_called()
        module.assert_not_called()

    def test_shared_owner_entry_refuses_without_accessing_services_or_inputs(self):
        class Unavailable:
            def __getattribute__(self, name):
                raise AssertionError('unavailable shared owner input accessed: ' + name)
        with self.assertRaisesRegex(ValueError, 'shared owner integration is not implemented'):
            subject.run_gate(Unavailable(), Unavailable(), Unavailable(), Unavailable(), subject.GATE)

    def test_enabling_outer_guard_alone_still_blocks_bootstrap(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
                'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_OS': 'Linux'}), \
             mock.patch.object(subject.sys, 'platform', 'linux'), \
             mock.patch.object(subject, 'GRAPH_INTEGRATION_REVIEWED', True), \
             mock.patch.object(subject, 'physical', side_effect=lambda path, **_: path), \
             mock.patch.object(subject, 'git') as git, \
             mock.patch.object(subject, 'ModuleType') as module:
            self.args.product_context = self.repo / 'context.json'
            self.args.helper_revision = self.rev
            with self.assertRaisesRegex(ValueError, 'pre-import source authority is not implemented'):
                subject.run(self.args)
        git.assert_not_called()
        module.assert_not_called()

    def test_child_environment_excludes_transport_token_and_qualification_setting(self):
        ctx = SimpleNamespace(child_environment=lambda: {'PATH': '/bin',
            'SOM_DEPENDENCY_ACTIONS_TOKEN': 'must-not-propagate', 'UV_NO_INSTALLER_METADATA': '1'})
        actual = subject.safe_child_environment(ctx, {'RUNNER_ENVIRONMENT': 'github-hosted',
                                                        'GITHUB_SHA': self.rev, 'GH_TOKEN': 'do-not-copy'})
        self.assertEqual(actual['RUNNER_ENVIRONMENT'], 'github-hosted')
        self.assertEqual(actual['GITHUB_SHA'], self.rev)
        self.assertNotIn('SOM_DEPENDENCY_ACTIONS_TOKEN', actual)
        self.assertNotIn('UV_NO_INSTALLER_METADATA', actual)
        self.assertNotIn('GH_TOKEN', actual)

    def test_timed_out_command_keeps_partial_raw_logs_without_success(self):
        work = Path(self.temp.name) / 'work'; work.mkdir()
        out = Path(self.temp.name) / 'saved'; out.mkdir()
        (work / 'stdout').write_bytes(b'partial stdout')
        (work / 'stderr').write_bytes(b'partial stderr')
        command = {'argv': ['fixture'], 'pid': 20, 'exit_code': None, 'wait_completed': False,
                   'stdout': 'stdout', 'stderr': 'stderr',
                   'stdout_sha256': subject.sha(b'partial stdout'), 'stderr_sha256': subject.sha(b'partial stderr')}
        class FakeContext:
            def __init__(self): self.work = work; self.cache = {'commands': []}
            def command(self, *args, **kwargs):
                self.cache['commands'].append(command)
                raise TimeoutError('fixture timeout')
        module = SimpleNamespace(tool_command=lambda script, args: [str(script), *args])
        report = {'rows': []}
        with self.assertRaises(TimeoutError):
            subject.run_command(FakeContext(), module, self.repo, {}, out, report, 'collector',
                                subject.COLLECTOR, [], 1)
        self.assertEqual((out / 'logs/collector.stdout').read_bytes(), b'partial stdout')
        self.assertEqual((out / 'logs/collector.stderr').read_bytes(), b'partial stderr')
        self.assertFalse(report['rows'][0]['command']['wait_completed'])
        self.assertIsNone(report['rows'][0]['postcheck'])


if __name__ == '__main__':
    unittest.main()
