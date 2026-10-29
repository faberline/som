"""Real process recovery integration; synthetic data, GitHub runners only.

This is not product or release acceptance. It exercises a stand-in native
binary and real OS process, port, signal, pointer and receipt operations.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release_common as rc
import switch

spec = importlib.util.spec_from_file_location('recovery_fixture_profile', HERE / 'test_compiler_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub runner only')
class RecoveryLoopback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compile_dir = tempfile.TemporaryDirectory(prefix='m6-recovery-standin-')
        cls.addClassCleanup(cls.compile_dir.cleanup)
        cls.binaries = {}
        for i, role in enumerate(('previous', 'candidate')):
            binary = Path(cls.compile_dir.name) / role
            subprocess.run(['cc', '-O2', f'-DRECOVERY_ROLE={i}', '-o', str(binary),
                            str(HERE / 'recovery_standin.c')], check=True, timeout=120)
            cls.binaries[role] = binary
        if rc.digest(cls.binaries['previous']) == rc.digest(cls.binaries['candidate']):
            raise RuntimeError('stand-in roles must have different binary hashes')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='m6-recovery-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.old_repo, self.old_release = rc.REPO, rc.Release
        self.old_env = dict(os.environ)
        self.signal_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        self.runs = []
        self.port = None
        # addCleanup also runs if any later setUp step fails.
        self.addCleanup(self.cleanup_processes_and_environment)
        owner = self
        class TrackedRelease(self.old_release):
            def __init__(self, output, cwd):
                super().__init__(output, cwd)
                owner.runs.append(self)
        rc.REPO, rc.Release = self.repo, TrackedRelease
        self.fixture = profile.Fixture(self.repo, rc)
        self.config = self.fixture.config
        for role in ('previous', 'candidate'):
            binding = self.config[role]
            shutil.copy2(self.binaries[role], binding['binary']['path'])
            sha = rc.digest(binding['binary']['path'])
            binding['binary']['sha256'] = sha
            for name in ('runtime', 'build'):
                path = Path(binding[name]['path'])
                doc = json.loads(path.read_bytes())
                doc['binary_sha256'] = sha
                path.write_text(json.dumps(doc))
                binding[name]['sha256'] = rc.digest(path)
        for role in ('previous', 'candidate'):
            os.environ['STANDIN_HEALTH_' + role.upper()] = json.dumps(self.fixture.runtimes[role]['health'])
        self.root = self.repo / '.local/rust-compiler-goal/acceptance/loopback/root'
        (self.root / 'som-rs/runtime').mkdir(parents=True)
        (self.root / 'som-rs/compiler-assets').mkdir(parents=True)
        self.port = rc.free_harness_port()
        self.assertIn(self.port, range(18919, 19000))
        self.assertNotIn(self.port, (8765, 18765))
        old = self.config['previous']
        self.old_argv = [old['binary']['path'], 'serve', '--assets', old['assets']['path'], '--port', str(self.port)]
        self.old_output = self.root.parent / 'old'
        self.old_output.mkdir(parents=True)
        old_run = rc.Release(self.old_output, str(self.repo))
        identity = old_run.start(self.old_argv, self.fixture.runtimes['previous']['health'])
        self.old_child = old_run.children[-1]
        self.reaper = threading.Thread(target=self.old_child.wait, daemon=True)
        self.reaper.start()
        previous = {'version': 'som.default-runtime.v2', 'accepted': True, 'pid': identity['pid'],
                    'command': self.old_argv, 'identity': identity,
                    'health': self.fixture.runtimes['previous']['health']}
        self.default = self.root / 'som-rs/runtime/default.json'
        self.default.write_text(json.dumps(previous))
        (self.root / 'som-rs/runtime/current').symlink_to(Path(self.old_argv[0]).parent)
        (self.root / 'som-rs/compiler-assets/current').symlink_to(self.old_argv[3])
        self.evidence = self.root.parent / 'switch'
        switch.execute(self.config, self.root, self.port, self.evidence, None, True, None)
        self.reaper.join(timeout=20)
        self.assertFalse(self.reaper.is_alive())
        self.assertEqual(self.old_child.poll(), -signal.SIGTERM)
        self.current = json.loads(self.default.read_bytes())
        self.candidate = next(p for r in self.runs for p in r.children if p.pid == self.current['pid'])

    def cleanup_processes_and_environment(self):
        # Only terminate processes created and held by this test.
        errors = []
        try:
            for run in reversed(self.runs):
                for child in run.children:
                    try:
                        if child.poll() is None:
                            try:
                                child.terminate()
                            except ProcessLookupError:
                                pass
                        child.wait(timeout=20)
                    except BaseException as error:
                        errors.append(f'child {child.pid}: {error!r}')
            if self.port is not None:
                try:
                    rc.port_free(self.port)
                except BaseException as error:
                    errors.append(f'port {self.port}: {error!r}')
        finally:
            for sig, handler in self.signal_handlers.items():
                signal.signal(sig, handler)
            rc.REPO, rc.Release = self.old_repo, self.old_release
            os.environ.clear()
            os.environ.update(self.old_env)
        if errors:
            raise RuntimeError('test cleanup failed: ' + '; '.join(errors))

    def stop_candidate(self):
        self.candidate.terminate()
        self.assertEqual(self.candidate.wait(timeout=20), -signal.SIGTERM)
        self.assertFalse(rc.alive(self.candidate.pid))

    def frozen_state(self):
        return (self.default.read_bytes(), rc.link_state(self.root / 'som-rs/runtime/current'),
                rc.link_state(self.root / 'som-rs/compiler-assets/current'))

    def restore(self, name='restore'):
        switch.restore(self.config, self.root, self.port, self.root.parent / name, None, True, self.evidence)

    def test_dead_candidate_is_restored_and_pinned_canaries_run(self):
        self.stop_candidate()
        self.restore()
        restored = json.loads(self.default.read_bytes())
        self.assertNotEqual(restored['pid'], self.candidate.pid)
        self.assertEqual(restored['command'], self.old_argv)
        self.assertEqual(rc.identity(restored['pid'], self.old_argv, self.port), restored['identity'])
        self.assertEqual(rc.link_state(self.root / 'som-rs/runtime/current')['target'], str(Path(self.old_argv[0]).parent))
        self.assertEqual(rc.link_state(self.root / 'som-rs/compiler-assets/current')['target'], self.old_argv[3])
        events = json.loads((self.root.parent / 'restore/journal.json').read_bytes())
        self.assertEqual(events[-1]['stage'], 'restore_verified')
        self.assertEqual(sum(event['stage'] == 'canary_passed' for event in events), 6)

    def test_new_listener_blocks_recovery_and_is_not_signalled(self):
        self.stop_candidate()
        occupied = self.root.parent / 'occupant'
        occupied.mkdir()
        run = rc.Release(occupied, str(self.repo))
        other = run.start(self.old_argv, self.fixture.runtimes['previous']['health'])
        child = run.children[-1]
        before = self.frozen_state()
        with self.assertRaises((RuntimeError, OSError, subprocess.SubprocessError)):
            self.restore()
        self.assertEqual(self.frozen_state(), before)
        self.assertIsNone(child.poll())
        self.assertEqual(rc.identity(child.pid, self.old_argv, self.port), other)

    def test_live_candidate_without_listener_is_not_signalled(self):
        self.assertIsNone(self.candidate.poll())
        self.assertEqual(rc.identity(self.candidate.pid, self.current['command'], self.port),
                         self.current['identity'])
        os.kill(self.candidate.pid, signal.SIGUSR1)
        # A successful global lsof/bind proof establishes that SIGUSR1 closed it.
        import time
        deadline = time.monotonic() + 10
        while True:
            try:
                rc.port_free(self.port)
                break
            except (RuntimeError, OSError, subprocess.SubprocessError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
        before = self.frozen_state()
        with self.assertRaises((RuntimeError, OSError, subprocess.SubprocessError)):
            self.restore()
        self.assertEqual(self.frozen_state(), before)
        self.assertIsNone(self.candidate.poll())

    def test_live_candidate_existing_restore_path(self):
        reaper = threading.Thread(target=self.candidate.wait, daemon=True)
        reaper.start()
        self.restore()
        reaper.join(timeout=20)
        self.assertEqual(self.candidate.poll(), -signal.SIGTERM)
        restored = json.loads(self.default.read_bytes())
        self.assertEqual(restored['command'], self.old_argv)
        self.assertEqual(rc.identity(restored['pid'], self.old_argv, self.port), restored['identity'])


if __name__ == '__main__':
    unittest.main()
