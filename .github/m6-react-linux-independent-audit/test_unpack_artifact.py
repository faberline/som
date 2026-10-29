"""Synthetic archive tests; execute only on a GitHub-hosted runner."""
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile
import unpack_artifact as target

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

def fixture(tar, side=None, extra=None):
    out = io.BytesIO()
    if side is None:
        side = hashlib.sha256(tar).hexdigest() + '  /tmp/' + target.TAR_NAME + '\n'
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr(target.TAR_NAME, tar)
        archive.writestr(target.SIDE_NAME, side)
        if extra is not None:
            archive.writestr(extra, b'extra')
    return out.getvalue()

class UnpackTests(unittest.TestCase):
    def run_case(self, raw, expected=None):
        root = Path(tempfile.mkdtemp(prefix='som-react-artifact-fixture-'))
        source, output = root / 'react-qa-artifact.zip', root / target.TAR_NAME
        source.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with mock.patch.object(target, 'EXPECTED_ZIP', expected or digest), \
             mock.patch.object(target, 'ZIP_SIZE', len(raw)), \
             mock.patch.dict(os.environ, {'RUNNER_TEMP': str(root)}), \
             mock.patch('sys.argv', ['unpack_artifact.py', str(source), str(output)]):
            target.main()
        return output.read_bytes()

    def test_valid_artifact(self):
        self.assertEqual(self.run_case(fixture(b'public data')), b'public data')

    def test_wrong_zip_digest_and_unexpected_member(self):
        with self.assertRaises(ValueError):
            self.run_case(fixture(b'public data'), expected='0' * 64)
        with self.assertRaises(ValueError):
            self.run_case(fixture(b'public data', extra='../escape'))

    def test_wrong_tar_hash_and_bad_sidecar(self):
        for side in ('0' * 64 + '  /tmp/' + target.TAR_NAME + '\n', '../bad'):
            with self.subTest(side=side), self.assertRaises(ValueError):
                self.run_case(fixture(b'public data', side=side))

if __name__ == '__main__':
    unittest.main()
