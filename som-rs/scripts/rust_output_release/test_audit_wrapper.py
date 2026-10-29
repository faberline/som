"""Synthetic two-step wrapper tests. Run on GitHub-hosted runners."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

import audit_wrapper as wrapper


def sha(value):
    return hashlib.sha256(value).hexdigest()


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.auditor = self.root / 'audit.py'
        self.auditor.write_bytes(b'synthetic auditor')
        self.target_path = self.root / 'target.json'
        self.binding = b'non-secret binding'
        self.target = {'head': 'a' * 40, 'run_id': 1, 'attempt': 1, 'job_id': 2, 'artifact_id': 3,
                       'artifact_sha256': sha(b'cipher'), 'artifact_bytes': 6,
                       'auditor_sha256': sha(b'synthetic auditor'), 'producer_binding_sha256': sha(self.binding)}
        self.target_path.write_text(json.dumps(self.target))
        self.env = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                                               'RUNNER_OS': 'macOS', 'GITHUB_REPOSITORY': wrapper.REPO,
                                               'RUNNER_TEMP': str(self.root)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.auditor_patch = mock.patch.object(wrapper, 'AUDITOR', self.auditor)
        self.auditor_patch.start()
        self.addCleanup(self.auditor_patch.stop)
        self.target_patch = mock.patch.object(wrapper, 'TARGET', self.target_path)
        self.target_patch.start()
        self.addCleanup(self.target_patch.stop)
        self.calls = []
        self.run_patch = mock.patch.object(wrapper.subprocess, 'run', side_effect=self.fake_run)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.install_patch = mock.patch.object(wrapper, 'install_gh', return_value=self.root / 'tool/gh')
        self.install = self.install_patch.start()
        self.addCleanup(self.install_patch.stop)

    def fake_run(self, args, **kwargs):
        self.calls.append((args, dict(kwargs.get('env', os.environ))))
        directory = self.root / 'm6-independent-raw-audit'
        if args[0] == 'git' and args[1] == 'init':
            Path(args[-1]).mkdir()
        elif Path(args[0]).name == 'gh':
            kwargs['stdout'].write(b'cipher')
        elif str(self.auditor) in args and '--phase' in args and args[args.index('--phase') + 1] == 'fetch':
            (directory / 'binding.json').write_bytes(self.binding)
        return subprocess.CompletedProcess(args, 0)

    def test_steps_keep_tokens_separate(self):
        os.environ['GH_TOKEN'] = 'synthetic-token'
        wrapper.main('fetch')
        del os.environ['GH_TOKEN']
        os.environ['M6_INPUTS_KEYRING'] = 'synthetic-key'
        wrapper.main('inspect')
        fetch_env = next(env for args, env in self.calls if Path(args[0]).name == 'gh')
        audit_env = next(env for args, env in self.calls if '--phase' in args and
                         args[args.index('--phase') + 1] == 'fetch')
        inspect_env = next(env for args, env in self.calls if '--phase' in args and
                           args[args.index('--phase') + 1] == 'inspect')
        self.assertIn('GH_TOKEN', fetch_env)
        self.assertNotIn('M6_INPUTS_KEYRING', fetch_env)
        gh_args = next(args for args, _env in self.calls if Path(args[0]).name == 'gh')
        self.assertEqual(gh_args[0], str(self.root / 'tool/gh'))
        self.assertEqual(audit_env['M6_AUDIT_GH'], str(self.root / 'tool/gh'))
        self.assertNotIn('GH_TOKEN', inspect_env)
        self.assertIn('M6_INPUTS_KEYRING', inspect_env)

    def test_fetch_rejects_key_present(self):
        os.environ.update({'GH_TOKEN': 'synthetic-token', 'M6_INPUTS_KEYRING': 'synthetic-key'})
        with self.assertRaisesRegex(RuntimeError, 'token boundary'):
            wrapper.main('fetch')
        self.assertEqual(self.calls, [])

    def test_changed_binding_refused_before_inspect(self):
        os.environ['GH_TOKEN'] = 'synthetic-token'
        wrapper.main('fetch')
        del os.environ['GH_TOKEN']
        os.environ['M6_INPUTS_KEYRING'] = 'synthetic-key'
        (self.root / 'm6-independent-raw-audit/binding.json').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'handoff differs'):
            wrapper.main('inspect')
        self.assertFalse(any('--phase' in args and args[args.index('--phase') + 1] == 'inspect'
                             for args, _env in self.calls))

    def test_inspect_rejects_github_token_alias(self):
        os.environ['GH_TOKEN'] = 'synthetic-token'
        wrapper.main('fetch')
        del os.environ['GH_TOKEN']
        os.environ.update({'GITHUB_TOKEN': 'synthetic-token', 'M6_INPUTS_KEYRING': 'synthetic-key'})
        with self.assertRaisesRegex(RuntimeError, 'token boundary'):
            wrapper.main('inspect')


class InstallGhTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake_binary = b'synthetic gh executable'
        zip_path = self.root / 'source.zip'
        with zipfile.ZipFile(zip_path, 'w') as z:
            info = zipfile.ZipInfo(wrapper.GH_MEMBER)
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            z.writestr(info, self.fake_binary)
        self.zip_bytes = zip_path.read_bytes()
        for name, value in (('GH_ZIP_BYTES', len(self.zip_bytes)), ('GH_ZIP_SHA256', sha(self.zip_bytes)),
                            ('GH_BINARY_BYTES', len(self.fake_binary)), ('GH_BINARY_SHA256', sha(self.fake_binary))):
            patch = mock.patch.object(wrapper, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        machine = mock.patch.object(wrapper.platform, 'machine', return_value='arm64')
        machine.start()
        self.addCleanup(machine.stop)
        env = mock.patch.dict(os.environ, {'GH_TOKEN': 'secret1', 'GITHUB_TOKEN': 'secret2',
                                          'M6_INPUTS_KEYRING': 'secret3', 'ACTIONS_RUNTIME_TOKEN': 'secret4',
                                          'SSH_AUTH_SOCK': 'secret5'}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.curl_env = None
        self.curl_args = None
        run = mock.patch.object(wrapper.subprocess, 'run', side_effect=self.fake_curl)
        run.start()
        self.addCleanup(run.stop)
        version = mock.patch.object(wrapper.subprocess, 'check_output',
                                    return_value='gh version 2.103.0 (2026-10-21)\n')
        version.start()
        self.addCleanup(version.stop)

    def fake_curl(self, args, **kwargs):
        self.curl_env = kwargs['env']
        self.curl_args = args
        Path(args[args.index('--output') + 1]).write_bytes(self.zip_bytes)
        return subprocess.CompletedProcess(args, 0)

    def test_fixed_binary_download_does_not_receive_secrets(self):
        binary = wrapper.install_gh(self.root)
        self.assertEqual(binary.read_bytes(), self.fake_binary)
        for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING', 'ACTIONS_RUNTIME_TOKEN', 'SSH_AUTH_SOCK'):
            self.assertNotIn(name, self.curl_env)
        self.assertEqual(self.curl_args[1], '--disable')
        self.assertEqual(self.curl_args[self.curl_args.index('--proto') + 1], '=https')
        self.assertEqual(self.curl_args[self.curl_args.index('--proto-redir') + 1], '=https')
        self.assertNotIn('Authorization', ' '.join(self.curl_args))

    def test_mismatched_archive_refused_before_execution(self):
        self.zip_bytes += b'extra'
        with self.assertRaisesRegex(RuntimeError, 'ZIP differs'):
            wrapper.install_gh(self.root)

    def test_changed_archive_same_size_refused(self):
        self.zip_bytes = bytes([self.zip_bytes[0] ^ 1]) + self.zip_bytes[1:]
        with self.assertRaisesRegex(RuntimeError, 'ZIP differs'):
            wrapper.install_gh(self.root)

    def test_symlink_member_refused_even_when_archive_pinned(self):
        path = self.root / 'link.zip'
        with zipfile.ZipFile(path, 'w') as z:
            info = zipfile.ZipInfo(wrapper.GH_MEMBER)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, self.fake_binary)
        self.zip_bytes = path.read_bytes()
        with mock.patch.object(wrapper, 'GH_ZIP_BYTES', len(self.zip_bytes)), \
             mock.patch.object(wrapper, 'GH_ZIP_SHA256', sha(self.zip_bytes)):
            with self.assertRaisesRegex(RuntimeError, 'ZIP member differs'):
                wrapper.install_gh(self.root)

    def test_duplicate_member_refused_even_when_archive_pinned(self):
        path = self.root / 'duplicate.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w') as z:
                for _ in range(2):
                    info = zipfile.ZipInfo(wrapper.GH_MEMBER)
                    info.external_attr = (stat.S_IFREG | 0o755) << 16
                    z.writestr(info, self.fake_binary)
        self.zip_bytes = path.read_bytes()
        with mock.patch.object(wrapper, 'GH_ZIP_BYTES', len(self.zip_bytes)), \
             mock.patch.object(wrapper, 'GH_ZIP_SHA256', sha(self.zip_bytes)):
            with self.assertRaisesRegex(RuntimeError, 'ZIP member differs'):
                wrapper.install_gh(self.root)


if __name__ == '__main__':
    unittest.main()
