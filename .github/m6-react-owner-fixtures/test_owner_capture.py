"""Synthetic shared-owner routing checks. Run only on GitHub hosted runners."""
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


class OwnerCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.repo = root / 'repo'; self.repo.mkdir(); (self.repo / '.local').mkdir()
        self.out = root / 'owner'; self.out.mkdir()
        self.candidate = root / 'candidate'; self.candidate.mkdir()
        self.legacy = root / 'legacy'; self.legacy.mkdir()
        self.rust = root / 'rust'; self.rust.mkdir()
        self.qa = root / 'qa.json'; self.qa.write_text('{}')
        self.venv = root / 'venv'; (self.venv / 'bin').mkdir(parents=True)
        (self.venv / 'bin/python').write_bytes(b'fixture interpreter, never executed')
        self.api = root / 'api.json'; self.api.write_text(json.dumps({
            'version': 'som.react-linux-api-profile.v1', 'venv': str(self.venv), 'node_profile': str(self.qa)}))
        self.tar = root / 'closure.tar'; self.tar.write_bytes(b'fixture')
        self.collector = self.repo / '.local/collector'
        self.audit = self.repo / '.local/audit.json'
        self.profile = 'som-rs/scripts/hosted_readiness/baseline.json'
        self.rev = 'a' * 40
        self.source_map = {name: ('fixture:' + name).encode() for name in
                           (subject.SELF, subject.COLLECTOR, subject.AUDITOR, self.profile)}
        pins = {}
        def read_source(path):
            raw = self.source_map[path]; pins[path] = subject.sha(raw); return raw
        self.source = SimpleNamespace(read=read_source, pins=pins)
        config = {'baseline_profile': self.profile, 'legacy_run': str(self.legacy),
            'rust_baseline_run': str(self.rust), 'qa_profile': str(self.qa), 'api_profile': str(self.api),
            'api_closure_tar': str(self.tar), 'collector_out': str(self.collector), 'audit_out': str(self.audit)}
        authority = {'source_authority_active': True, 'helper_revision': self.rev, 'candidate_revision': self.rev,
            'product_acceptance_passed': False, 'compiler_acceptance_passed': False,
            'whole_acceptance_complete': False,
            'admitted_repository_python_sources': [subject.SELF, subject.COLLECTOR, subject.AUDITOR]}
        self.ctx = SimpleNamespace(repo=self.repo, work=root, helper_revision=self.rev, candidate_revision=self.rev,
            qa_mode='native-qa-v1', qa_source_authority=authority, data={'m6_react_inputs': config},
            sources=self.source, cache={'commands': []}, verify=mock.Mock(),
            child_environment=lambda: {'PATH': '/bin'})
        self.services = SimpleNamespace(
            start_current=mock.Mock(return_value={'url': 'http://127.0.0.1:18978', 'candidate_dir': str(self.candidate)}),
            start_baseline=mock.Mock(return_value={'url': 'http://127.0.0.1:18979'}),
            verify_all=mock.Mock(), close_all=mock.Mock())
        self.module = SimpleNamespace(context=lambda:self.ctx, tool_command=lambda script, args: [str(script), *args])
        self.interrupt = False
        self.runtime_links = False

        def command(argv, cwd, env, label, timeout, check):
            index = len(self.ctx.cache['commands'])
            stdout, stderr = root / ('stdout' + str(index)), root / ('stderr' + str(index))
            stdout.write_bytes(('stdout:' + label).encode()); stderr.write_bytes(('stderr:' + label).encode())
            if label == 'm6-react-collector':
                self.collector.mkdir(); (self.collector / 'run.json').write_text('{"partial":true}')
                if self.runtime_links:
                    outside = root / 'foreign-secret'; outside.write_text('not owned raw evidence')
                    bindir = self.collector / 'products/case/node_modules/.bin'
                    bindir.mkdir(parents=True)
                    (bindir / 'tool').symlink_to(outside)
                    (bindir / 'safe.log').write_bytes(b'previous raw log')
                    foreign_dir = root / 'foreign-directory'; foreign_dir.mkdir()
                    (foreign_dir / 'secret').write_bytes(b'not owned')
                    (self.collector / 'foreign-dir').symlink_to(foreign_dir, target_is_directory=True)
                    report_dir = self.collector / 'products/case/reports'; report_dir.mkdir()
                    (report_dir / 'case.json').write_text('{"raw":"retained"}')
            else:
                self.audit.write_text('{"rows":[],"totals":{"unrun":1}}')
            record = {'argv': argv, 'pid': 100 + index, 'exit_code': 0,
                'wait_completed': True, 'stdout': stdout.name, 'stderr': stderr.name,
                'stdout_sha256': subject.sha(stdout.read_bytes()), 'stderr_sha256': subject.sha(stderr.read_bytes())}
            self.ctx.cache['commands'].append(record)
            if self.interrupt and label == 'm6-react-collector':
                record['exit_code'] = None; record['wait_completed'] = False
                raise TimeoutError('fixture partial collector')
            return record
        self.ctx.command = command

    def invoke(self):
        with mock.patch.object(subject, 'OWNER_INTEGRATION_REVIEWED', True), \
             mock.patch.dict('sys.modules', {'full_product_context': self.module}):
            return subject.run_gate(self.ctx, self.candidate, self.services, self.out, subject.GATE)

    def test_routes_both_real_cli_shapes_and_retains_partial_without_acceptance(self):
        result = self.invoke()
        self.assertEqual(result, json.loads((self.out / 'result.json').read_bytes()))
        self.assertEqual([row['id'] for row in result['rows']], ['collector', 'audit'])
        self.assertEqual((self.out / 'collector/run.json').read_text(), '{"partial":true}')
        self.assertEqual((self.out / 'audit.json').read_text(), '{"rows":[],"totals":{"unrun":1}}')
        self.assertIn('--legacy-run', result['rows'][0]['command']['argv'])
        self.assertIn('--api-closure-tar', result['rows'][1]['command']['argv'])
        self.assertFalse(result['all_required_passed']); self.assertFalse(result['owned_cleanup_complete'])
        self.assertFalse(result['product_acceptance_passed'])
        self.services.start_baseline.assert_called_once_with(self.profile)
        self.services.close_all.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'not implemented'):
            subject.verify_result(self.out, self.source.read, self.rev, self.rev, subject.GATE,
                                  repo=self.repo, proof_roots={})

    def test_interrupted_collector_keeps_raw_failure_and_does_not_close_owned_services(self):
        self.interrupt = True
        self.runtime_links = True
        result = self.invoke()
        self.assertIn('fixture partial collector', result['error'])
        self.assertFalse(result['rows'][0]['command']['wait_completed'])
        self.assertEqual((self.out / 'collector/run.json').read_text(), '{"partial":true}')
        self.assertTrue((self.out / 'logs/collector.stdout').is_file())
        self.assertEqual((self.out / 'collector/products/case/reports/case.json').read_text(), '{"raw":"retained"}')
        self.assertEqual((self.out / 'collector/products/case/node_modules/.bin/safe.log').read_bytes(), b'previous raw log')
        self.assertFalse((self.out / 'collector/products/case/node_modules/.bin/tool').exists())
        self.assertFalse((self.out / 'collector/products/case/node_modules/.bin/tool').is_symlink())
        self.assertFalse((self.out / 'collector/foreign-dir').exists())
        self.assertFalse((self.out / 'collector/foreign-dir').is_symlink())
        capture = result['collector_capture']
        self.assertEqual({row['path'] for row in capture['excluded']},
                         {'products/case/node_modules/.bin/tool', 'foreign-dir'})
        self.assertTrue(capture['unverified_runtime_or_evidence'])
        self.assertFalse(capture['all_observed_files_copied']); self.assertFalse(capture['complete'])
        self.assertFalse(capture['file_modes_preserved']); self.assertFalse(capture['empty_directories_preserved'])
        self.assertFalse(capture['sealed_transport_verified'])
        self.assertFalse(result['owned_cleanup_complete']); self.services.close_all.assert_not_called()

    def test_special_file_is_excluded_without_losing_other_raw_files(self):
        self.collector.mkdir()
        os.mkfifo(self.collector / 'runtime-fifo')
        (self.collector / 'report.json').write_bytes(b'raw report')
        capture = subject.copy_capture(self.collector, self.out / 'collector')
        self.assertEqual((self.out / 'collector/report.json').read_bytes(), b'raw report')
        self.assertEqual(capture['excluded'], [{'path': 'runtime-fifo', 'reason': 'special_file_not_copied'}])
        self.assertTrue(capture['unverified_runtime_or_evidence']); self.assertFalse(capture['complete'])

    def test_unreadable_file_does_not_prevent_retaining_other_files(self):
        self.collector.mkdir()
        bad = self.collector / 'a-unreadable.raw'; bad.write_bytes(b'unreadable fixture')
        (self.collector / 'z-report.raw').write_bytes(b'important raw data')
        original = subject.read
        def read_fixture(path, *args, **kwargs):
            if Path(path) == bad:
                raise PermissionError('synthetic missing read permission')
            return original(path, *args, **kwargs)
        with mock.patch.object(subject, 'read', side_effect=read_fixture):
            capture = subject.copy_capture(self.collector, self.out / 'collector')
        self.assertEqual((self.out / 'collector/z-report.raw').read_bytes(), b'important raw data')
        self.assertEqual(capture['errors'], [{'path': 'a-unreadable.raw', 'error': 'PermissionError'}])
        self.assertTrue(capture['unverified_runtime_or_evidence']); self.assertFalse(capture['complete'])

    def test_missing_inputs_fails_before_starting_baseline(self):
        self.ctx.data['m6_react_inputs'].pop('baseline_profile')
        result = self.invoke()
        self.assertIn('explicit M6 owner inputs are unavailable', result['error'])
        self.assertFalse(result['all_required_passed']); self.services.start_baseline.assert_not_called()
        self.services.close_all.assert_not_called()

