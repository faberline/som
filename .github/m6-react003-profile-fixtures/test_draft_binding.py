"""DRAFT synthetic profile-binding checks. Run on GitHub-hosted Ubuntu 22.04 only.

The node and tree identities in the positive case are simulated. These fixtures
cannot establish the real artifact's provenance or product acceptance.
"""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import linux_qa_profile as p

PROFILE = Path(__file__).with_name('qa-profile.draft.json')
EXPECTED = 'e050319a7b91a56b01167e720fc4b5f08eb92b4fb1df01c00f13422091d7d6a0'

@contextlib.contextmanager
def setup():
    with tempfile.TemporaryDirectory(prefix='react003-draft-', dir=os.environ['RUNNER_TEMP']) as tmp:
        temp = Path(tmp).resolve()
        root = temp / 'som-react-linux-qa-closure'
        node = root / 'node/bin/node'
        npm = root / 'node/lib/node_modules/npm'
        cache = root / 'npm-cache-new'
        node.parent.mkdir(parents=True)
        node.write_bytes(b'synthetic node, never execute')
        node.chmod(0o755)
        (npm / 'bin').mkdir(parents=True)
        (npm / 'bin/npm-cli.js').write_bytes(b'synthetic npm, never execute')
        cache.mkdir()
        (cache / 'item').write_bytes(b'synthetic cache')
        (cache / 'item').chmod(0o444)
        cache.chmod(0o555)
        path = temp / 'som-react-linux-qa-profile.json'
        path.write_bytes(PROFILE.read_bytes())
        try:
            with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(temp)}):
                yield path, node, npm, cache
        finally:
            cache.chmod(0o755)

@contextlib.contextmanager
def identities(node, npm, cache, *, node_sha=None, cache_tree=None, npm_tree=None):
    # Only identity observations are simulated; profile bytes, checks and host are real.
    def tree(path, readonly):
        if path == npm and not readonly:
            return p.NPM_TREE if npm_tree is None else npm_tree
        if path == cache and readonly:
            return p.CACHE_TREE if cache_tree is None else cache_tree
        raise AssertionError('unexpected tree or mode')
    real_file_sha = p.file_sha
    def file_sha(path):
        if path == node:
            return p.NODE_SHA256 if node_sha is None else node_sha
        return real_file_sha(path)
    with mock.patch.object(p, 'file_sha', side_effect=file_sha), \
         mock.patch.object(p, 'safe_tree', side_effect=tree):
        yield

class DraftBindingTests(unittest.TestCase):
    def test_exact_bytes_and_source_binding(self):
        raw = PROFILE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED)
        self.assertEqual(p.PROFILE_SHA256, EXPECTED)
        self.assertEqual(p.strict_json(raw), p.PROFILE)
        with setup() as (path, node, npm, cache), identities(node, npm, cache):
            result = p.validate_profile(path)
            self.assertEqual(result['profile_sha256'], EXPECTED)
            self.assertEqual(result['node'], str(node))
            self.assertEqual(result['npm_cli'], str(npm / 'bin/npm-cli.js'))
            self.assertEqual(result['cache'], cache)

    def test_wrong_raw_bytes_fail_even_when_json_value_is_the_same(self):
        with setup() as (path, node, npm, cache), identities(node, npm, cache):
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(p.ProfileError, 'authority differs'):
                p.validate_profile(path)

    def test_wrong_fields_fail_after_digest_gate(self):
        # Test semantic equality independently of the preceding raw-byte hash gate.
        for field in ('run', 'artifact', 'commit', 'tar_sha256'):
            with self.subTest(field=field), setup() as (path, node, npm, cache), identities(node, npm, cache):
                doc = copy.deepcopy(p.PROFILE)
                doc['source'][field] = 'wrong'
                raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
                path.write_bytes(raw)
                with mock.patch.object(p, 'PROFILE_SHA256', hashlib.sha256(raw).hexdigest()):
                    with self.assertRaisesRegex(p.ProfileError, 'contents differ'):
                        p.validate_profile(path)

    def test_wrong_node_npm_and_cache_identities_fail(self):
        with setup() as (path, node, npm, cache), identities(node, npm, cache, node_sha='0' * 64):
            with self.assertRaisesRegex(p.ProfileError, 'node differs'):
                p.validate_profile(path)
        with setup() as (path, node, npm, cache), identities(node, npm, cache, npm_tree=('0' * 64, 1921, 0)):
            with self.assertRaisesRegex(p.ProfileError, 'npm differs'):
                p.validate_profile(path)
        with setup() as (path, node, npm, cache), identities(node, npm, cache, cache_tree=('0' * 64, 246, 0)):
            with self.assertRaisesRegex(p.ProfileError, 'cache differs'):
                p.validate_profile(path)

    def test_actual_wrong_synthetic_node_is_rejected_without_identity_mock(self):
        with setup() as (path, node, npm, cache):
            with self.assertRaisesRegex(p.ProfileError, 'node differs'):
                p.validate_profile(path)

    def test_real_cache_hash_and_permissions_are_checked(self):
        with setup() as (path, node, npm, cache):
            # Simulate only the node and npm; scan the synthetic cache for real.
            real_tree = p.safe_tree
            def trees(root, readonly):
                return p.NPM_TREE if root == npm and not readonly else real_tree(root, readonly)
            real_file_sha = p.file_sha
            def file_sha(item):
                return p.NODE_SHA256 if item == node else real_file_sha(item)
            with mock.patch.object(p, 'file_sha', side_effect=file_sha), \
                 mock.patch.object(p, 'safe_tree', side_effect=trees):
                with self.assertRaisesRegex(p.ProfileError, 'cache differs'):
                    p.validate_profile(path)
                cache.chmod(0o755)
                with self.assertRaisesRegex(p.ProfileError, 'writable'):
                    p.validate_profile(path)

if __name__ == '__main__':
    unittest.main()
