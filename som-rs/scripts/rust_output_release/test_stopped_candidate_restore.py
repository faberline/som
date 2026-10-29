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


class FakeChild:
    def __init__(self, pid):
        self.pid, self.alive = pid, True
        self.poll_error = self.wait_error = False
        self.wait_count = 0

    def poll(self):
        if self.poll_error:
            raise OSError('poll unavailable')
        return None if self.alive else 0

    def wait(self, timeout):
        self.wait_count += 1
        if self.wait_error:
            raise OSError('wait unavailable')
        profile.require(not self.alive, 'child still alive')
        return 0


class FakeRun:
    def __init__(self, fixture, output, cwd):
        self.fixture, self.output, self.cwd = fixture, output, cwd
        self.children, self.events, self.evidence_errors = [], [], []

    def save(self, name, data):
        (self.output / name).write_bytes(data)

    def event(self, stage, **fields):
        self.events.append((stage, fields))

    def start(self, argv, health):
        if self.fixture.start_error:
            raise RuntimeError('listener taken before start')
        if self.fixture.start_child_failure:
            child = FakeChild(8123)
            child.alive = self.fixture.start_child_failure == 'alive'
            child.poll_error = self.fixture.start_child_failure == 'poll-error'
            child.wait_error = self.fixture.start_child_failure == 'wait-error'
            self.children.append(child)
            raise RuntimeError('start failed after child creation')
        self.children.append(FakeChild(8123))
        return {'pid': 8123, 'argv': argv, 'port': 18940, 'cwd': self.cwd}

    def health(self, port, expected):
        return expected

    def canary(self, port, binaries, cases, label):
        profile.require(label == 'restored-default', 'wrong canary')
        if self.fixture.alter_assets_during_canary:
            self.fixture.actual_pointers['assets'] = copy.deepcopy(self.fixture.previous_pointers['assets'])
        if self.fixture.canary_error:
            raise RuntimeError('canary failed')

    def stop_child(self, owner):
        profile.require(owner['pid'] == self.children[0].pid, 'unowned child')
        profile.require(self.fixture.rc.identity(owner['pid'], owner['argv'], owner['port']) == owner,
                        'owned child identity changed')
        self.children[0].alive = False
        self.children[0].wait(timeout=20)


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
        self.previous_pointers = {'runtime': {'kind': 'symlink', 'target': str(old_binary.parent)},
                                  'assets': {'kind': 'symlink', 'target': str(old_assets)}}
        self.journal = [{'stage': 'pointers', 'pointers': self.previous_pointers},
                        {'stage': 'committed', 'receipt': copy.deepcopy(self.current)}]
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        self.runs = []
        self.output = self.root / 'restore'
        self.start_error = self.canary_error = self.fail_asset_link = self.alter_assets_during_canary = False
        self.start_child_failure = None
        self.actual_pointers = copy.deepcopy(self.pointers)
        self.patch(self.rc, 'check_prerequisites')
        self.patch(self.rc, 'apply_formatter_env')
        self.patch(self.rc, 'assert_current')
        self.patch(self.rc, 'assets_match')
        self.patch(self.rc, 'previous_cases', return_value=['pinned'])
        self.alive = self.patch(self.rc, 'alive', return_value=False)
        self.identity = self.patch(self.rc, 'identity', side_effect=self.owner_identity)
        self.port_free = self.patch(self.rc, 'port_free')
        self.stop = self.patch(self.rc, 'finish_stop', side_effect=AssertionError('unknown PID signalled'))
        self.links = self.patch(self.rc, 'atomic_link', side_effect=self.set_link)
        self.patch(self.rc, 'link_state', side_effect=lambda path: self.actual_pointers[self.link_name(path)])
        self.patch(self.rc, 'atomic_bytes', side_effect=lambda path, data: path.write_bytes(data))
        self.patch(self.switch, 'current_state', side_effect=lambda *_: (self.default, self.snapshot, self.current, self.pointers))
        self.patch(self.rc, 'Release', side_effect=self.run_factory)
        self.patch(self.switch.signal, 'signal')

    def patch(self, obj, name, **kwargs):
        patcher = mock.patch.object(obj, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_factory(self, output, cwd):
        run = FakeRun(self, output, cwd)
        self.runs.append(run)
        return run

    def owner_identity(self, pid, argv, port):
        profile.require(pid == 8123 and self.runs and self.runs[0].children[0].alive,
                        'unknown or exited PID')
        return {'pid': 8123, 'argv': argv, 'port': port, 'cwd': str(self.root)}

    @staticmethod
    def link_name(path):
        return 'runtime' if path.parent.name == 'runtime' else 'assets'

    def set_link(self, path, value):
        name = self.link_name(path)
        if name == 'assets' and self.fail_asset_link:
            self.fail_asset_link = False
            raise OSError('simulated link failure')
        self.actual_pointers[name] = copy.deepcopy(value)

    def restore(self):
        return self.switch.restore({}, self.root, self.port, self.output, None, True, self.evidence)

    def test_absent_candidate_restores_without_signalling_any_pid(self):
        self.restore()
        self.assertTrue(self.identity.call_count >= 2)
        self.assertTrue(all(call.args[0] == 8123 for call in self.identity.call_args_list))
        self.stop.assert_not_called()
        self.port_free.assert_called_once_with(self.port)
        self.assertEqual(self.links.call_count, 2)
        self.assertEqual(json.loads(self.default.read_bytes())['pid'], 8123)
        self.assertIn('restore_owner_absent', [stage for stage, _ in self.runs[0].events])

    def test_committed_receipt_mismatch_refuses_before_port_or_pointers(self):
        self.journal[-1]['receipt']['pid'] = 9999
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        with self.assertRaisesRegex(RuntimeError, 'committed receipt differs') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.port_free.assert_not_called()
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_foreign_listener_refuses_before_pointer_change(self):
        self.port_free.side_effect = RuntimeError('port occupied')
        with self.assertRaisesRegex(RuntimeError, 'port occupied') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.links.assert_not_called()
        self.stop.assert_not_called()

    def test_pid_reappears_before_restore_refuses_without_signalling(self):
        self.alive.side_effect = [False, True]
        with self.assertRaisesRegex(RuntimeError, 'became live') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
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
        with self.assertRaisesRegex(RuntimeError, 'default receipt changed') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.port_free.assert_not_called()
        self.links.assert_not_called()

    def test_identity_mismatch_refuses_before_pointer_change(self):
        self.current['identity']['port'] = 18941
        self.journal[-1]['receipt'] = copy.deepcopy(self.current)
        (self.evidence / 'journal.json').write_text(json.dumps(self.journal))
        with self.assertRaisesRegex(RuntimeError, 'identity differs') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.port_free.assert_not_called()
        self.links.assert_not_called()

    def test_port_taken_after_probe_refuses_before_pointer_change(self):
        self.start_error = True
        with self.assertRaisesRegex(RuntimeError, 'listener taken before start') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.links.assert_not_called()
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_start_failure_with_live_unadmitted_child_requires_operator_without_signal(self):
        self.start_child_failure = 'alive'
        with self.assertRaisesRegex(RuntimeError, 'RECOVERY_FAILED_REQUIRES_OPERATOR') as error:
            self.restore()
        self.assertIn('unadmitted child exit unverified', str(error.exception))
        self.assertIn('child 8123 remains alive or exit is unknown', str(error.exception))
        self.assertTrue(self.runs[0].children[0].alive)
        self.identity.assert_not_called()
        self.stop.assert_not_called()
        self.links.assert_not_called()
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_start_failure_with_unobservable_child_requires_operator(self):
        for failure in ('poll-error', 'wait-error'):
            with self.subTest(failure=failure):
                self.start_child_failure = failure
                self.output = self.root / ('restore-' + failure)
                with self.assertRaisesRegex(RuntimeError, 'RECOVERY_FAILED_REQUIRES_OPERATOR') as error:
                    self.restore()
                self.assertIn('unadmitted child exit unverified', str(error.exception))
                self.assertIn('poll unavailable' if failure == 'poll-error' else 'wait unavailable',
                              str(error.exception))
                self.identity.assert_not_called()
                self.stop.assert_not_called()
                self.links.assert_not_called()
                self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_start_failure_with_confirmed_waited_child_keeps_original_failure(self):
        self.start_child_failure = 'exited'
        with self.assertRaisesRegex(RuntimeError, 'RECOVERY_FAILED_REQUIRES_OPERATOR') as error:
            self.restore()
        self.assertIn('start failed after child creation', str(error.exception))
        self.assertIn('cleanup_errors=[]', str(error.exception))
        self.assertEqual(self.runs[0].children[0].wait_count, 1)
        self.identity.assert_not_called()
        self.stop.assert_not_called()
        self.links.assert_not_called()
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_canary_failure_stops_owned_child_before_pointer_change(self):
        self.canary_error = True
        with self.assertRaisesRegex(RuntimeError, 'canary failed') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.links.assert_not_called()
        self.assertFalse(self.runs[0].children[0].alive)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_second_pointer_failure_restores_first_and_stops_owned_child(self):
        self.fail_asset_link = True
        with self.assertRaisesRegex(RuntimeError, 'simulated link failure') as error:
            self.restore()
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.assertEqual(self.actual_pointers, self.pointers)
        self.assertFalse(self.runs[0].children[0].alive)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_external_pointer_change_is_never_overwritten(self):
        self.alter_assets_during_canary = True
        with self.assertRaisesRegex(RuntimeError, 'RECOVERY_FAILED_REQUIRES_OPERATOR'):
            self.restore()
        self.links.assert_not_called()
        self.assertEqual(self.actual_pointers['assets'], self.previous_pointers['assets'])
        self.assertFalse(self.runs[0].children[0].alive)

    def test_owner_lost_after_canary_does_not_commit(self):
        self.identity.side_effect = RuntimeError('owner no longer has listener')
        with self.assertRaisesRegex(RuntimeError, 'RECOVERY_FAILED_REQUIRES_OPERATOR'):
            self.restore()
        self.links.assert_not_called()
        self.assertTrue(self.runs[0].children[0].alive)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_live_approval_path_is_rechecked_after_lock(self):
        approval_path = self.root / 'approval.json'
        approval_check = self.patch(self.switch, 'check_approval', return_value={'approved': True})
        self.switch.restore({}, self.root, self.port, self.output, approval_path, False, self.evidence)
        self.assertEqual(approval_check.call_count, 2)
        self.assertTrue(all(call.args[0] == approval_path for call in approval_check.call_args_list))

    def actual_approval(self):
        config = {'candidate': {'binary': {'sha256': 'synthetic-candidate'}}}
        approval_path = self.root / 'real-approval.json'
        approval = {'action': 'restore-default-8765-rust-output', 'approved': True,
                    'candidate_binary_sha256': 'synthetic-candidate',
                    'script_sha256': self.rc.digest(self.switch.__file__),
                    'common_sha256': self.rc.digest(self.rc.__file__),
                    'prerequisites_sha256': self.rc.receipt_sha(config),
                    'default_sha256': self.rc.sha_bytes(self.snapshot), 'quote': 'synthetic approval'}
        approval_path.write_text(json.dumps(approval))
        return config, approval_path, approval

    def test_live_restore_accepts_exact_approval_twice(self):
        config, approval_path, _ = self.actual_approval()
        self.switch.restore(config, self.root, self.port, self.output, approval_path, False, self.evidence)
        self.assertEqual(json.loads(self.default.read_bytes())['pid'], 8123)

    def test_live_restore_refuses_approval_changed_after_first_check(self):
        config, approval_path, approval = self.actual_approval()
        def alter_after_initial_check(_port):
            approval['approved'] = False
            approval_path.write_text(json.dumps(approval))
        self.port_free.side_effect = alter_after_initial_check
        with self.assertRaisesRegex(RuntimeError, 'approval approved differs') as error:
            self.switch.restore(config, self.root, self.port, self.output, approval_path, False, self.evidence)
        self.assertIn('RECOVERY_FAILED_REQUIRES_OPERATOR', str(error.exception))
        self.assertEqual(self.runs[0].children, [])
        self.links.assert_not_called()
        self.assertEqual(self.default.read_bytes(), self.snapshot)


if __name__ == '__main__':
    unittest.main()
