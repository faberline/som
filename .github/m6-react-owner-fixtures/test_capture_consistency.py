"""Synthetic local capture checks. Run on GitHub hosted runners only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import m6_react_callback as subject


class LocalCaptureFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'capture'
        self.root.mkdir()
        self.source = b'actual synthetic callback source'
        self.rev = 'a' * 40
        logs = self.root / 'logs'
        logs.mkdir()
        (logs / 'collector.stdout').write_bytes(b'partial actual output')
        (logs / 'collector.stderr').write_bytes(b'failure detail')
        (logs / 'collector.postcheck.json').write_text('{"postcheck":"fixture"}')
        self.report = {'version': 'som.m6-react-graph-callback.v1', 'gate': subject.GATE,
            'helper_revision': self.rev, 'candidate_revision': self.rev, 'product_acceptance_passed': False,
            'owned_waits_complete': False, 'owned_cleanup_complete': False,
            'bound_source_sha256': {subject.SELF: subject.sha(self.source)},
            'rows': [{'id': 'collector', 'streams_complete': True,
                'stdout': 'logs/collector.stdout', 'stderr': 'logs/collector.stderr',
                'postcheck': 'logs/collector.postcheck.json',
                'postcheck_sha256': subject.sha((logs / 'collector.postcheck.json').read_bytes()),
                'command': {'argv': ['fixture'], 'exit_code': 9, 'wait_completed': False,
                   'stdout': 'original-stdout', 'stderr': 'original-stderr',
                   'stdout_sha256': subject.sha(b'partial actual output'),
                   'stderr_sha256': subject.sha(b'failure detail')}}]}
        self.write_report()

    def write_report(self):
        self.report['saved_files_sha256'] = {k: v for k, v in subject.inventory(self.root).items() if k != 'result.json'}
        (self.root / 'result.json').write_text(json.dumps(self.report))

    def check(self, source=None):
        return subject.check_local_capture_consistency(self.root,
            lambda name: self.source if source is None else source, self.rev, self.rev)

    def test_failure_does_not_claim_wait_cleanup_or_acceptance(self):
        result = self.check()
        self.assertTrue(result['local_bytes_consistent'])
        self.assertEqual(result['observed_rows'], ['collector'])
        for key in ('process_waits_verified', 'service_cleanup_verified', 'audit_semantics_verified',
                    'sealed_transport_verified', 'product_acceptance_passed'):
            self.assertFalse(result[key])

    def test_tampered_stream_source_and_extra_files_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'source bytes differ'):
            self.check(b'other')
        path = self.root / 'logs/collector.stdout'
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'file universe differs'):
            self.check()
        path.write_bytes(b'partial actual output')
        (self.root / 'extra').write_bytes(b'not declared')
        with self.assertRaisesRegex(ValueError, 'file universe differs'):
            self.check()

    def test_recomputed_inventory_cannot_hide_changed_log_binding(self):
        (self.root / 'logs/collector.stdout').write_bytes(b'other')
        self.write_report()
        with self.assertRaisesRegex(ValueError, 'retained stream differs'):
            self.check()

    def test_duplicate_json_and_false_stream_status_fail(self):
        (self.root / 'result.json').write_text('{"version":1,"version":2}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.check()
        self.report['rows'][0]['streams_complete'] = False
        self.write_report()
        with self.assertRaisesRegex(ValueError, 'stream completeness'):
            self.check()

    def test_symlink_refused(self):
        (self.root / 'link').symlink_to('logs/collector.stdout')
        with self.assertRaisesRegex(ValueError, 'linked'):
            self.check()

    def test_special_file_refused(self):
        os.mkfifo(self.root / 'fifo')
        with self.assertRaisesRegex(ValueError, 'special'):
            self.check()

    def test_verifier_stays_closed_even_with_consistent_capture(self):
        self.check()
        with mock.patch.object(subject, 'shared_postclose_proof', return_value={}):
            with self.assertRaisesRegex(ValueError, 'shared post-close proof differs'):
                subject.verify_result(self.root, lambda _: self.source, self.rev, self.rev,
                                      repo=self.root, proof_roots={})
