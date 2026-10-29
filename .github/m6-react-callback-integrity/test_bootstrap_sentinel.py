"""Untrusted matching Git HEAD cannot open the missing native bootstrap."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import m6_react_callback as subject


class BootstrapSentinel(unittest.TestCase):
    def test_matching_real_git_head_and_self_claimed_policy_cannot_execute_code(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
            root = Path(directory).resolve()
            repo = root / 'repo'
            repo.mkdir()
            marker = root / 'untrusted-code-executed'
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.STDOUT).decode().strip()
            git('init', '-q')
            graph = repo / subject.GRAPH
            authority = repo / 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
            graph.parent.mkdir(parents=True)
            malicious = ('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\n').encode()
            graph.write_bytes(malicious)
            authority.write_bytes(malicious)
            (repo / 'untrusted-authority.json').write_text('{"source_authority_active":true,"review":"APPROVED"}')
            context = repo / 'context.json'
            context.write_text('{}')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'untrusted synthetic input')
            revision = git('rev-parse', 'HEAD')
            args = SimpleNamespace(repo=repo, product_context=context,
                helper_revision=revision, candidate_revision=revision)
            with mock.patch.object(subject, 'GRAPH_INTEGRATION_REVIEWED', True), \
                 mock.patch.object(subject, 'git', wraps=subject.git) as callback_git, \
                 self.assertRaisesRegex(ValueError, 'pre-import source authority is not implemented'):
                subject.run(args)
            callback_git.assert_not_called()
            self.assertFalse(marker.exists())

