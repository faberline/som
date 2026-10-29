"""Synthetic React QA probe fixtures. Run only on a GitHub-hosted runner."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import offline_probe as p


def scratch():
    # The runner is ephemeral. Leave fixtures in its temporary directory.
    return Path(tempfile.mkdtemp(prefix='som-react-offline-fixture-'))


class OfflineProbeFixtures(unittest.TestCase):
    def test_regular_file_digest_and_refusals(self):
        root = scratch()
        file = root / 'source'
        file.write_bytes(b'fixture bytes\n')
        self.assertEqual(p.digest_file(file), hashlib.sha256(b'fixture bytes\n').hexdigest())
        (root / 'link').symlink_to('source')
        for invalid in (root, root / 'missing', root / 'link'):
            with self.subTest(path=invalid), self.assertRaises(p.Refusal):
                p.digest_file(invalid)

    def test_tree_hash_is_sorted_and_detects_content_changes(self):
        root = scratch()
        (root / 'nested').mkdir()
        (root / 'nested' / 'b').write_bytes(b'bravo')
        (root / 'a').write_bytes(b'alpha')
        lines = ('a\0' + hashlib.sha256(b'alpha').hexdigest() + '\n'
                 + 'nested/b\0' + hashlib.sha256(b'bravo').hexdigest() + '\n').encode()
        expected = {'sha256': hashlib.sha256(lines).hexdigest(), 'files': 2, 'symlinks': 0}
        self.assertEqual(p.tree(root, False), expected)
        (root / 'a').write_bytes(b'changed')
        self.assertNotEqual(p.tree(root, False)['sha256'], expected['sha256'])

    def test_tree_accepts_only_internal_live_links_when_enabled(self):
        root = scratch()
        (root / 'file').write_bytes(b'value')
        (root / 'link').symlink_to('file')
        target_hash = hashlib.sha256(b'value').hexdigest()
        content = ('file\0' + target_hash + '\nlink\0symlink:file\n').encode()
        self.assertEqual(p.tree(root, True), {'sha256': hashlib.sha256(content).hexdigest(),
                                             'files': 1, 'symlinks': 1})
        with self.assertRaises(p.Refusal):
            p.tree(root, False)

        outside_root = scratch()
        outside = outside_root / 'outside'
        outside.write_bytes(b'external')
        for target in (str(outside), '../outside', 'missing'):
            test_root = scratch()
            (test_root / 'bad-link').symlink_to(target)
            with self.subTest(target=target), self.assertRaises(p.Refusal):
                p.tree(test_root, True)

    def test_tree_refuses_unsafe_names_special_files_and_root_links(self):
        for name in ('bad\nname', 'bad\\name'):
            root = scratch()
            (root / name).write_bytes(b'x')
            with self.subTest(name=name), self.assertRaises(p.Refusal):
                p.tree(root, False)
        root = scratch()
        os.mkfifo(root / 'fifo')
        with self.assertRaises(p.Refusal):
            p.tree(root, False)
        link_root = scratch()
        (link_root / 'dir').mkdir()
        (link_root / 'dir-link').symlink_to('dir', target_is_directory=True)
        with self.assertRaises(p.Refusal):
            p.tree(link_root / 'dir-link', True)

    def test_protect_tree_removes_write_bits_without_changing_bytes(self):
        root = scratch()
        nested = root / 'nested'
        nested.mkdir()
        file = nested / 'file'
        file.write_bytes(b'cache fixture')
        root.chmod(0o755)
        nested.chmod(0o775)
        file.chmod(0o664)
        before = p.tree(root, False)
        p.protect_tree(root)
        self.assertEqual(p.tree(root, False), before)
        for path in (root, nested, file):
            with self.subTest(path=path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o222, 0)

    def test_protect_tree_refuses_links_and_missing_root(self):
        root = scratch()
        (root / 'file').write_bytes(b'cache')
        (root / 'link').symlink_to('file')
        with self.assertRaises(p.Refusal):
            p.protect_tree(root)
        with self.assertRaises(p.Refusal):
            p.protect_tree(scratch() / 'missing')

    def test_cache_snapshot_keeps_file_bytes_and_modes(self):
        root = scratch()
        nested = root / 'nested'
        nested.mkdir()
        content = nested / 'content'
        content.write_bytes(b'input cache')
        p.protect_tree(root)
        snapshot = p.cache_snapshot(root)
        self.assertEqual(snapshot['entries']['nested']['kind'], 'directory')
        self.assertEqual(snapshot['entries']['nested/content']['sha256'], hashlib.sha256(b'input cache').hexdigest())
        self.assertEqual(snapshot['entries']['nested/content']['mode'] & 0o222, 0)
        content.chmod(0o644)
        with self.assertRaises(p.Refusal):
            p.cache_snapshot(root)

    def test_tree_and_protect_tree_refuse_walk_errors(self):
        root = scratch()

        def failed_walk(path, *, topdown, followlinks, onerror):
            self.assertEqual(path, root)
            self.assertFalse(followlinks)
            onerror(PermissionError('synthetic scan failure'))
            return iter(())

        for operation, reason in ((lambda: p.tree(root, False), 'tree scan failed'),
                                  (lambda: p.protect_tree(root), 'cache scan failed')):
            with self.subTest(reason=reason), mock.patch.object(p.os, 'walk', side_effect=failed_walk):
                with self.assertRaisesRegex(p.Refusal, reason) as caught:
                    operation()
                self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_command_receipts_record_exits_timeouts_and_spawn_failures(self):
        argv = ['synthetic-tool', '--fixture']
        env = {'FIXTURE': 'only'}
        cases = (
            ('success', subprocess.CompletedProcess(argv, 0, b'ok\n', b'notice\n'), None,
             'exited', 0, b'ok\n', b'notice\n'),
            ('nonzero', subprocess.CompletedProcess(argv, 7, b'partial\n', b'error\n'), None,
             'exited', 7, b'partial\n', b'error\n'),
            ('timeout', None, subprocess.TimeoutExpired(argv, 600, output=b'partial\n', stderr=b'late\n'),
             'timed_out', None, b'partial\n', b'late\n'),
            ('spawn', None, OSError('synthetic spawn error'), 'spawn_failed', None, b'', b''),
        )
        for name, result, error, outcome, code, stdout, stderr in cases:
            with self.subTest(case=name):
                root = scratch()
                receipts = []
                with mock.patch.object(p.subprocess, 'run', return_value=result, side_effect=error) as run:
                    if name == 'success':
                        self.assertEqual(p.run(argv, env, root, root, name, receipts), stdout)
                    else:
                        with self.assertRaises(p.Refusal):
                            p.run(argv, env, root, root, name, receipts)
                    run.assert_called_once_with(argv, cwd=root, env=env, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, timeout=600, check=False)
                self.assertEqual((root / (name + '.stdout')).read_bytes(), stdout)
                self.assertEqual((root / (name + '.stderr')).read_bytes(), stderr)
                self.assertEqual(receipts, [{'name': name, 'argv': argv, 'outcome': outcome, 'exit_code': code,
                                             'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
                                             'stderr_sha256': hashlib.sha256(stderr).hexdigest()}])


if __name__ == '__main__':
    unittest.main()
