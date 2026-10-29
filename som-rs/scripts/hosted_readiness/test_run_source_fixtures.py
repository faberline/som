"""Exercise real fixture payload and source mutation guards on GitHub only."""
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('Run these fixtures on GitHub only')

path = Path(__file__).with_name('run_source_fixtures.py')
r = ModuleType('_source_fixture_runner_test_subject')
r.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), r.__dict__)


class FixturePayloadGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.site = self.root / 'site'; self.site.mkdir()
        self.members = {'validators.py': b'VALUE = "original source"\n',
                        'fixture-1.dist-info/METADATA': b'Name: fixture\nVersion: 1\n',
                        'fixture-1.dist-info/RECORD': b'original record\n'}
        for name, raw in self.members.items():
            target = self.site / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        self.audits = {'fixture': {'dist_info_dir': 'fixture-1.dist-info', 'members': [
            {'path': name, 'sha256': r.sha(raw), 'size': len(raw)} for name, raw in self.members.items()]}}

    def check(self):
        return r.check_installed_scope(self.site, self.audits, [], self.root / 'wheels')

    def test_exact_original_payload_and_bounded_installer_metadata_pass(self):
        (self.site / 'fixture-1.dist-info/INSTALLER').write_bytes(b'pip\n')
        (self.site / 'fixture-1.dist-info/REQUESTED').write_bytes(b'')
        self.assertEqual(set(self.check()), set(self.members) | {
            'fixture-1.dist-info/INSTALLER', 'fixture-1.dist-info/REQUESTED'})

    def test_real_matching_timestamp_pyc_cannot_join_verified_source(self):
        source = self.site / 'validators.py'
        cache = Path(importlib.util.cache_from_source(str(source))); cache.parent.mkdir()
        info = source.stat()
        code = compile("raise AssertionError('unowned cached code')", str(source), 'exec')
        cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
                          + marshal.dumps(code))
        with self.assertRaisesRegex(ValueError, 'cached or automatic'):
            self.check()

    def test_original_wheel_header_is_verified_at_the_actual_pip_target_path(self):
        body = b'typedef int source_fixture_header;\n'
        self.audits['fixture']['members'].append({'path': 'fixture-1.data/headers/fixture.h',
                                                 'sha256': r.sha(body), 'size': len(body)})
        target = self.site / 'include/python/fixture/fixture.h'
        target.parent.mkdir(parents=True); target.write_bytes(body)
        self.assertEqual(self.check()['include/python/fixture/fixture.h']['sha256'], r.sha(body))
        target.write_bytes(body + b'changed')
        with self.assertRaisesRegex(ValueError, 'installed fixture bytes differ'):
            self.check()

    def test_unowned_python_extension_and_startup_files_are_refused(self):
        for name in ('extra.py', 'extra.so', 'unexpected.pth', 'sitecustomize.py'):
            with self.subTest(name=name):
                extra = self.site / name; extra.write_bytes(b'unowned')
                try:
                    with self.assertRaises(ValueError): self.check()
                finally:
                    extra.unlink()

    def test_changed_payload_plus_rewritten_inventory_cannot_change_parent_authority(self):
        inventory = self.check()
        saved = self.root / 'site-inventory.json'; saved.write_text(json.dumps(inventory))
        original_digest = r.sha(saved.read_bytes())
        (self.site / 'validators.py').write_bytes(b'VALUE = "changed"\n')
        inventory['validators.py'].update(sha256=r.sha((self.site / 'validators.py').read_bytes()),
                                          size=(self.site / 'validators.py').stat().st_size)
        saved.write_text(json.dumps(inventory))
        marker = self.root / 'entry-ran'
        entry = self.root / 'entry.py'; entry.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', r.BOOTSTRAP, str(self.site),
            original_digest, r.sha(entry.read_bytes()), str(self.root), str(entry)], capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())


class FixtureGitGuards(unittest.TestCase):
    def repository(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        subprocess.run(['/usr/bin/git', '-C', str(root), 'init', '--quiet'], check=True)
        source = root / 'module.py'; source.write_bytes(b'VALUE = 1\n')
        self.commit(root)
        revision = r.git(root, 'rev-parse', 'HEAD').decode().strip()
        return root, revision, r.source_inventory(root, revision)

    def commit(self, root):
        subprocess.run(['/usr/bin/git', '-C', str(root), 'add', '--all'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), '-c', 'user.name=Source Fixture',
            '-c', 'user.email=source@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '--quiet',
            '--no-verify', '-m', 'source fixture'], check=True)

    def test_later_source_mutation_is_refused_before_execution(self):
        root, revision, expected = self.repository()
        (root / 'module.py').write_bytes(b'VALUE = 2\n')
        with self.assertRaisesRegex(ValueError, 'tracked source changed'):
            r.verify_sources(root, revision, expected)

    def test_changed_head_and_untracked_import_cache_are_refused(self):
        root, revision, expected = self.repository()
        cache = root / '__pycache__'; cache.mkdir(); (cache / 'module.cpython-312.pyc').write_bytes(b'cache')
        with self.assertRaisesRegex(ValueError, 'unowned repository import'):
            r.verify_sources(root, revision, expected)
        (cache / 'module.cpython-312.pyc').unlink(); cache.rmdir()
        (root / 'module.py').write_bytes(b'VALUE = 3\n'); self.commit(root)
        with self.assertRaisesRegex(ValueError, 'fixture HEAD changed'):
            r.verify_sources(root, revision, expected)

    def test_untracked_unittest_package_directory_alias_is_refused(self):
        root, revision, expected = self.repository()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        outside = Path(temporary.name).resolve()
        marker = outside / 'unowned-package-ran'
        (outside / '__init__.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
        (root / 'unittest').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unowned repository import directory'):
            r.verify_sources(root, revision, expected)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
