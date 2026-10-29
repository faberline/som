"""Restore an exited owner using synthetic files and fake process operations."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('recovery_profile_fixtures', HERE / 'test_compiler_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class FakeRun:
    def __init__(self, output, cwd):
        self.output, self.cwd = output, cwd
        self.children, self.events, self.evidence_errors = [], [], []

    def save(self, name, data):
        (self.output / name).write_bytes(data)

    def event(self, stage, **fields):
        self.events.append((stage, fields))

    def start(self, argv, health):
        return {'pid': 8123, 'argv': argv, 'port': 18940, 'cwd': self.cwd}

    def health(self, port, expected):
        return expected

    def canary(self, port, binaries, cases, label):
        profile.require(label == 'restored-default', 'wrong canary')


class StoppedCandidateRestoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='som-stopped-candidate-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.port = 18940
        self.rc, self.switch = (lambda x: (x['common'], x['switch']))(profile.isolated_modules())
        self.evidence = self.root / 'switch-evidence'
        self.evidence.mkdir()
        old_binary = self.root / 'old-som'
        old_binary.write_bytes(b'previous binary')
        old_assets = self.root / 'old-assets'
        old_assets.mkdir()
        old_command = [str(old_binary), 'serve', '--assets', str(old_assets), '--port', str(self.port)]
        old_identity = {'pid': 4222, 'argv': old_command, 'port': self.port, 'cwd': str(self.root),
                        'binary_sha256': profile.digest(old_binary), 'start': 'old start', 'uid': os.getuid()}
        self.previous = {'pid': 4222, 'command': old_command, 'identity': old_identity, 'health': {}}
        previous_bytes = json.dumps(self.previous).encode()
        (self.evidence / '0001-previous-default.json').write_bytes(previous_bytes)
        new_command = [str(self.root / 'new-som'), 'serve', '--assets', str(self.root / 'new-assets'), '--port', str(self.port)]
        identity = {'pid': 5432, 'argv': new_command, 'port': self.port, 'cwd': str(self.root),
                    'binary_sha256': 'candidate', 'start': 'candidate start', 'uid': os.getuid()}
        self.current = {'pid': 5432, 'command': new_command, 'identity': identity, 'health': {},
                        'evidence': str(self.evidence), 'previous_default_sha256': self.rc.sha_bytes(previous_bytes)}
        self.default = self.root / 'som-rs/runtime/default.json'
        self.default.parent.mkdir(parents=True)
        self.snapshot = json.dumps(self.current).encode()
        self.default.write_bytes(self.snapshot)
        self.pointers = {'runtime': {'kind': 'symlink', 'target': 'candidate'},
                         'assets': {'kind': 'symlink', 'target': 'candidate-assets'}}
        self.previous_pointers = {'runtime': {'kind': 'symlink', 'target': 'old'},
                                  'assets': {'kind': 'symlink', 'target': 'old-assets'}}
        self.journal = [{'stage': 'pointers', 'pointers': self.previous_pointers},
                        {'stage': 'committed', 'receipt': copy.deepcopy(self.current)}]
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        self.runs = []
        self.output = self.root / 'restore'
        self.patch(self.rc, 'check_prerequisites')
        self.patch(self.rc, 'apply_formatter_env')
        self.patch(self.rc, 'assert_current')
        self.patch(self.rc, 'assets_match')
        self.patch(self.rc, 'previous_cases', return_value=['pinned'])
        self.alive = self.patch(self.rc, 'alive', return_value=False)
        self.identity = self.patch(self.rc, 'identity', side_effect=AssertionError('unknown PID signalled'))
        self.port_free = self.patch(self.rc, 'port_free')
        self.stop = self.patch(self.rc, 'finish_stop', side_effect=AssertionError('unknown PID signalled'))
        self.links = self.patch(self.rc, 'atomic_link')
        self.patch(self.rc, 'link_state', side_effect=lambda path: self.pointers['runtime'] if path.name == 'current' and path.parent.name == 'runtime' else self.pointers['assets'])
        self.patch(self.rc, 'atomic_bytes', side_effect=lambda path, data: path.write_bytes(data))
        self.patch(self.switch, 'current_state', side_effect=lambda *_: (self.default, self.snapshot, self.current, self.pointers))
        self.patch(self.rc, 'Release', side_effect=self.run_factory)
        self.patch(self.switch.signal, 'signal')

    def patch(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_factory(self, output, cwd):
        run = FakeRun(output, cwd)
        self.runs.append(run)
        return run

    def restore(self):
        return self.switch.restore({}, self.root, self.port, self.output, None, True, self.evidence)

    def test_absent_candidate_restores_without_signalling_any_pid(self):
        self.restore()
        self.identity.assert_not_called()
        self.stop.assert_not_called()
        self.port_free.assert_called_once_with(self.port)
        self.assertEqual(self.links.call_count, 2)
        self.assertEqual(json.loads(self.default.read_bytes())['pid'], 8123)
        self.assertIn('restore_owner_absent', [stage for stage, _ in self.runs[0].events])

    def test_committed_receipt_mismatch_refuses_before_port_or_pointers(self):
        self.journal[-1]['receipt']['pid'] = 9999
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        with self.assertRaisesRegex(RuntimeError, 'committed receipt differs'):
            self.restore()
        self.port_free.assert_not_called()
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_foreign_listener_refuses_before_pointer_change(self):
        self.port_free.side_effect = RuntimeError('port occupied')
        with self.assertRaisesRegex(RuntimeError, 'port occupied'):
            self.restore()
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_pid_reappears_before_restore_refuses_without_signalling(self):
        self.alive.side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, 'became live'):
            self.restore()
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_alive_candidate_without_listener_refuses_without_signal(self):
        self.alive.return_value = True
        self.identity.side_effect = RuntimeError('listener absent')
        with self.assertRaisesRegex(RuntimeError, 'listener absent'):
            self.restore()
        self.port_free.assert_not_called()
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_changed_default_receipt_refuses_before_pointer_change(self):
        self.default.write_bytes(b'external change')
        with self.assertRaisesRegex(RuntimeError, 'default receipt changed'):
            self.restore()
        self.port_free.assert_not_called()
        self.links.assert_not_called()

    def test_identity_mismatch_refuses_before_pointer_change(self):
        self.current['identity']['port'] = 18941
        self.journal[-1]['receipt'] = copy.deepcopy(self.current)
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        with self.assertRaisesRegex(RuntimeError, 'identity differs'):
            self.restore()
        self.port_free.assert_not_called()
        self.links.assert_not_called()


if __name__ == '__main__':
    unittest.main()
