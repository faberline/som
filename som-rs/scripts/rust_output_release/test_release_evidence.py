"""Mutation checks for the immutable release artifact boundary. Run on GitHub."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('fetch_release_evidence', HERE / 'fetch_release_evidence.py')
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)


class ReleaseArtifactTests(unittest.TestCase):
    def metadata(self):
        expected = fetch.ARTIFACTS['original']
        return {'id': expected['id'], 'name': expected['name'], 'size_in_bytes': expected['bytes'],
                'digest': 'sha256:' + expected['sha256'], 'expired': False,
                'workflow_run': {'id': expected['run_id'], 'head_sha': expected['commit'],
                                 'repository_id': fetch.REPOSITORY_ID}}

    def test_metadata_identity(self):
        metadata = self.metadata()
        fetch.verify_metadata(metadata, fetch.ARTIFACTS['original'])
        for key, value in [('id', 1), ('name', 'different'), ('size_in_bytes', 1),
                           ('digest', 'sha256:' + '0' * 64), ('expired', True)]:
            changed = copy.deepcopy(metadata)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch.verify_metadata(changed, fetch.ARTIFACTS['original'])
        for key in ('id', 'head_sha', 'repository_id'):
            changed = copy.deepcopy(metadata)
            changed['workflow_run'][key] = 'different'
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch.verify_metadata(changed, fetch.ARTIFACTS['original'])

    def archive(self, root, members):
        archive = root / 'data.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            for name, value in members:
                bundle.writestr(name, value)
        expected = {'bytes': archive.stat().st_size, 'sha256': fetch.digest(archive),
                    'packages': 1, 'index_sha256': hashlib.sha256(b'index').hexdigest()}
        return archive, expected

    def test_extract_exact_members_and_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, expected = self.archive(root, [('evidence-index.json', b'index'),
                ('package-000.sealed', b'cipher'), ('package-000.manifest.json', b'manifest')])
            fetch.extract(archive, root / 'out', expected)
            self.assertEqual((root / 'out/package-000.sealed').read_bytes(), b'cipher')
            with self.assertRaises(ValueError):
                fetch.extract(archive, root / 'out', expected)

    def test_extra_missing_and_traversal_members_refused(self):
        base = [('evidence-index.json', b'index'), ('package-000.sealed', b'cipher'),
                ('package-000.manifest.json', b'manifest')]
        for members in (base[:-1], base + [('extra', b'x')],
                        base + [('../escape', b'x')], base + [base[0]]):
            with self.subTest(members=[m[0] for m in members]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, expected = self.archive(root, members)
                with self.assertRaises(ValueError):
                    fetch.extract(archive, root / 'out', expected)
                self.assertFalse((root / 'out').exists())

    def test_wrong_archive_or_index_hash_refused(self):
        for key in ('sha256', 'index_sha256'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, expected = self.archive(root, [('evidence-index.json', b'index'),
                    ('package-000.sealed', b'cipher'), ('package-000.manifest.json', b'manifest')])
                expected[key] = '0' * 64
                with self.assertRaises(ValueError):
                    fetch.extract(archive, root / 'out', expected)


if __name__ == '__main__':
    unittest.main()
