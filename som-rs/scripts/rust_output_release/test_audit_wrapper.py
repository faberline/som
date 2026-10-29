"""Synthetic two-step wrapper tests. Run on GitHub-hosted runners."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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

    def fake_run(self, args, **kwargs):
        self.calls.append((args, dict(os.environ)))
        directory = self.root / 'm6-independent-raw-audit'
        if args[0] == 'git' and args[1] == 'init':
            Path(args[-1]).mkdir()
        elif args[0] == 'gh':
            kwargs['stdout'].write(b'cipher')
        elif str(self.auditor) in args and '--phase' in args and args[args.index('--phase') + 1] == 'fetch':
            (directory / 'binding.json').write_bytes(self.binding)
        return None

    def test_steps_keep_tokens_separate(self):
        os.environ['GH_TOKEN'] = 'synthetic-token'
        wrapper.main('fetch')
        del os.environ['GH_TOKEN']
        os.environ['M6_INPUTS_KEYRING'] = 'synthetic-key'
        wrapper.main('inspect')
        fetch_env = next(env for args, env in self.calls if args[0] == 'gh')
        inspect_env = next(env for args, env in self.calls if '--phase' in args and
                           args[args.index('--phase') + 1] == 'inspect')
        self.assertIn('GH_TOKEN', fetch_env)
        self.assertNotIn('M6_INPUTS_KEYRING', fetch_env)
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


if __name__ == '__main__':
    unittest.main()
