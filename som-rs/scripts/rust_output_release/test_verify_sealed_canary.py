#!/usr/bin/env python3
"""Synthetic sealed-manifest coverage fixtures; GitHub-hosted runners only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

if not (os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
        and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'):
    raise RuntimeError('execute these fixtures only on a GitHub-hosted runner')

import verify_sealed_canary as subject


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SealedCoverage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.raw, self.sealed = root / 'raw', root / 'sealed'
        path = self.raw / 'run/candidate/0001-request.raw'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'synthetic fixture')
        path.chmod(0o644)
        self.sealed.mkdir()
        self.path = path
        self.manifest = {'cipher_sha256': sha(b'synthetic cipher'), 'total_files': 1,
                         'files': [{'path': 'run/candidate/0001-request.raw',
                                    'sha256': sha(b'synthetic fixture'), 'bytes': 17, 'mode': 0o644}]}
        self.write_manifest()

    def write_manifest(self):
        manifest_raw = json.dumps(self.manifest).encode()
        (self.sealed / 'package-000.manifest.json').write_bytes(manifest_raw)
        (self.sealed / 'package-000.sealed').write_bytes(b'synthetic cipher')
        index = {'version': 'som.m6-evidence-index.v1', 'status': 'encrypted', 'packages': [{
            'manifest': 'package-000.manifest.json', 'archive': 'package-000.sealed',
            'manifest_sha256': sha(manifest_raw), 'cipher_sha256': sha(b'synthetic cipher'),
            'total_files': self.manifest['total_files']}]}
        (self.sealed / 'evidence-index.json').write_text(json.dumps(index))

    def test_complete_synthetic_coverage(self):
        self.assertTrue(subject.verify(self.raw, self.sealed))

    def test_private_nonexecutable_raw_mode_matches_sealer_normalization(self):
        self.path.chmod(0o600)
        self.assertTrue(subject.verify(self.raw, self.sealed))

    def test_private_executable_raw_mode_matches_sealer_normalization(self):
        self.path.chmod(0o700)
        self.manifest['files'][0]['mode'] = 0o755
        self.write_manifest()
        self.assertTrue(subject.verify(self.raw, self.sealed))

    def test_changed_raw_is_refused(self):
        self.path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)

    def test_extra_raw_is_refused(self):
        (self.path.parent / '0002-response.raw').write_bytes(b'extra')
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)

    def test_uncollected_root_file_is_refused(self):
        (self.raw / 'hosted-canary-result.json').write_text('{}')
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)

    def test_duplicate_manifest_path_is_refused(self):
        self.manifest['files'].append(dict(self.manifest['files'][0]))
        self.manifest['total_files'] = 2
        self.write_manifest()
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)

    def test_extra_sealed_plaintext_is_refused(self):
        (self.sealed / 'unsealed.txt').write_text('synthetic')
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)

    def test_symlink_raw_is_refused(self):
        self.path.unlink()
        target = self.raw.parent / 'outside'
        target.write_bytes(b'synthetic fixture')
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            subject.verify(self.raw, self.sealed)


if __name__ == '__main__':
    unittest.main()
