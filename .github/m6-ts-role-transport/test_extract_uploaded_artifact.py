import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from extract_uploaded_artifact import extract


def sha(data):
    return hashlib.sha256(data).hexdigest()


class UploadedArtifactTests(unittest.TestCase):
    def fixture(self, root, extra=None):
        payload = b'encrypted payload'; manifest = b'manifest bytes'
        index = json.dumps({'version': 'som.m6-evidence-index.v1', 'status': 'encrypted',
                            'packages': [{'archive': 'package-000.sealed', 'manifest': 'package-000.manifest.json',
                                          'cipher_sha256': sha(payload), 'manifest_sha256': sha(manifest)}]}).encode()
        archive = root / 'artifact.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            out.writestr('evidence-index.json', index)
            out.writestr('package-000.sealed', payload)
            out.writestr('package-000.manifest.json', manifest)
            if extra is not None:
                out.writestr(extra, b'extra')
        return archive, sha(archive.read_bytes()), sha(index)

    def test_extract_exact_uploaded_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); archive, digest, index = self.fixture(root)
            extract(archive, root / 'out', digest, index)
            self.assertEqual((root / 'out/package-000.sealed').read_bytes(), b'encrypted payload')

    def test_changed_zip_index_and_extra_members_refused(self):
        for variant in ('zip', 'index', 'extra', 'traversal'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive, digest, index = self.fixture(root, {'extra': 'extra', 'traversal': '../escape'}.get(variant))
                with self.assertRaises(ValueError):
                    extract(archive, root / 'out', '0' * 64 if variant == 'zip' else digest,
                            '0' * 64 if variant == 'index' else index)


if __name__ == '__main__':
    unittest.main()
