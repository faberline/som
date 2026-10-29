"""Synthetic tests for the unsealed Linux QA profile. Run on GitHub only."""
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

def sha(data):
    return hashlib.sha256(data).hexdigest()

def expected_tree(items):
    data = ''.join(name + '\0' + sha(value) + '\n' for name, value in sorted(items.items()))
    return sha(data.encode()), len(items), 0

@contextlib.contextmanager
def fixture():
    temp = Path(tempfile.mkdtemp(prefix='som-profile-fixture-', dir=os.environ['RUNNER_TEMP']))
    root = temp / 'som-react-linux-qa-closure'
    node = root / 'node/bin/node'
    npm = root / 'node/lib/node_modules/npm'
    cache = root / 'npm-cache-new'
    node.parent.mkdir(parents=True)
    node.write_bytes(b'fake node')
    node.chmod(0o755)
    (npm / 'bin').mkdir(parents=True)
    (npm / 'bin/npm-cli.js').write_bytes(b'fake npm')
    (npm / 'LICENSE').write_bytes(b'license')
    cache.mkdir()
    (cache / 'item').write_bytes(b'cached')
    (cache / 'item').chmod(0o444)
    cache.chmod(0o555)
    node_sha = sha(b'fake node')
    npm_tree = expected_tree({'bin/npm-cli.js': b'fake npm', 'LICENSE': b'license'})
    cache_tree = expected_tree({'item': b'cached'})
    doc = copy.deepcopy(p.PROFILE)
    doc['node']['sha256'] = node_sha
    doc['npm'].update(sha256=npm_tree[0], files=npm_tree[1])
    doc['cache'].update(sha256=cache_tree[0], files=cache_tree[1])
    raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    path = temp / 'som-react-linux-qa-profile.json'
    path.write_bytes(raw)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, {'RUNNER_TEMP': str(temp)}))
        stack.enter_context(mock.patch.object(p, 'PROFILE', doc))
        stack.enter_context(mock.patch.object(p, 'NODE_SHA256', node_sha))
        stack.enter_context(mock.patch.object(p, 'NPM_TREE', npm_tree))
        stack.enter_context(mock.patch.object(p, 'CACHE_TREE', cache_tree))
        stack.enter_context(mock.patch.object(p, 'PROFILE_SHA256', sha(raw)))
        stack.enter_context(mock.patch.object(p, 'validate_host'))
        yield path, node, npm, cache

class ProfileTests(unittest.TestCase):
    def test_tree_uses_frozen_path_order_when_lexical_order_differs(self):
        root = Path(tempfile.mkdtemp(prefix='som-profile-order-', dir=os.environ['RUNNER_TEMP']))
        (root / 'lib/cli').mkdir(parents=True)
        (root / 'lib/cli.js').write_bytes(b'outer')
        (root / 'lib/cli/entry.js').write_bytes(b'inner')
        rows = [('lib/cli/entry.js', b'inner'), ('lib/cli.js', b'outer')]
        stream = ''.join(name + '\0' + hashlib.sha256(data).hexdigest() + '\n' for name, data in rows).encode()
        self.assertEqual(p.safe_tree(root, False), (hashlib.sha256(stream).hexdigest(), 2, 0))

    def test_file_sha_streams_large_input_without_file_digest(self):
        root = Path(tempfile.mkdtemp(prefix='som-profile-hash-', dir=os.environ['RUNNER_TEMP']))
        path = root / 'large'
        data = b'a' * (1024 * 1024) + b'last block'
        path.write_bytes(data)
        with mock.patch.object(hashlib, 'file_digest', create=True, side_effect=AssertionError('Python 3.11 only')):
            self.assertEqual(p.file_sha(path), hashlib.sha256(data).hexdigest())

    def test_unsealed_profile_fails_before_host(self):
        with mock.patch.object(p, 'PROFILE_SHA256', None), mock.patch.object(p, 'validate_host') as host:
            with self.assertRaisesRegex(p.ProfileError, 'not sealed'):
                p.validate_profile(Path('/unused'))
            host.assert_not_called()

    def test_host_must_be_github_ubuntu_2204_x86_64(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}), \
             mock.patch.object(p.platform, 'system', return_value='Linux'), \
             mock.patch.object(p.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(p.Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="22.04"\n'):
            p.validate_host()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}), \
             mock.patch.object(p.platform, 'system', return_value='Linux'), \
             mock.patch.object(p.platform, 'machine', return_value='x86_64'), \
             mock.patch.object(p.Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'):
            with self.assertRaisesRegex(p.ProfileError, 'host version differs'):
                p.validate_host()

    def test_bound_profile_and_recheck(self):
        with fixture() as (path, node, npm, cache):
            qa = p.validate_profile(path)
            self.assertEqual(qa['node'], str(node))
            self.assertEqual(qa['npm_cli'], str(npm / 'bin/npm-cli.js'))
            self.assertEqual(qa['cache'], cache)
            p.recheck(qa)

    def test_missing_tampered_profile_and_node_fail(self):
        with fixture() as (path, node, _, _):
            with self.assertRaises(p.ProfileError):
                p.validate_profile(path.parent / 'other.json')
            node.write_bytes(b'tampered')
            with self.assertRaisesRegex(p.ProfileError, 'node differs'):
                p.validate_profile(path)
        with fixture() as (path, _, _, _):
            path.write_bytes(b'{"version":"wrong"}\n')
            with self.assertRaisesRegex(p.ProfileError, 'authority differs'):
                p.validate_profile(path)

    def test_qa_recheck_refuses_npm_cache_and_profile_mutation(self):
        with fixture() as (path, _, npm, _):
            qa = p.validate_profile(path)
            (npm / 'bin/npm-cli.js').write_bytes(b'modified')
            with self.assertRaisesRegex(p.ProfileError, 'npm changed'):
                p.recheck(qa)
        with fixture() as (path, _, _, cache):
            qa = p.validate_profile(path)
            cache.chmod(0o755)
            with self.assertRaisesRegex(p.ProfileError, 'writable'):
                p.recheck(qa)
        with fixture() as (path, _, _, _):
            qa = p.validate_profile(path)
            path.write_bytes(b'different')
            with self.assertRaisesRegex(p.ProfileError, 'profile changed'):
                p.recheck(qa)

    def test_qa_recheck_refuses_nonexecutable_node(self):
        with fixture() as (path, node, _, _):
            qa = p.validate_profile(path)
            node.chmod(0o644)
            with self.assertRaisesRegex(p.ProfileError, 'not executable'):
                p.recheck(qa)

    def test_tree_rejects_links_special_files_and_scanning_errors(self):
        root = Path(tempfile.mkdtemp(prefix='som-profile-tree-', dir=os.environ['RUNNER_TEMP']))
        (root / 'target').write_bytes(b'x')
        (root / 'link').symlink_to('target')
        with self.assertRaisesRegex(p.ProfileError, 'link refused'):
            p.safe_tree(root, False)
        special = Path(tempfile.mkdtemp(prefix='som-profile-special-', dir=os.environ['RUNNER_TEMP']))
        os.mkfifo(special / 'fifo')
        with self.assertRaisesRegex(p.ProfileError, 'special file'):
            p.safe_tree(special, False)
        def broken(*args, onerror, **kwargs):
            onerror(PermissionError('fixture'))
            return iter(())
        with mock.patch.object(p.os, 'walk', side_effect=broken), self.assertRaisesRegex(p.ProfileError, 'unreadable'):
            p.safe_tree(root, False)

    def test_duplicate_and_nonfinite_json_are_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(p.ProfileError):
                p.strict_json(raw)

if __name__ == '__main__':
    unittest.main()
