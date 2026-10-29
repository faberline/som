"""Synthetic diagnostics tests; execute only on a GitHub-hosted runner."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import tree_order as a


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def fixture():
    digest = hashlib.sha256(b'cache').hexdigest()
    snapshot = {'root_mode': 0o555, 'entries': {'a': {'kind': 'directory', 'mode': 0o555},
                'a/x': {'kind': 'file', 'mode': 0o444, 'sha256': digest},
                'a.txt': {'kind': 'file', 'mode': 0o444, 'sha256': digest}}}
    entries = {a.NPM_PREFIX + 'a/x': b'npm-a', a.NPM_PREFIX + 'a.txt': b'npm-b',
               a.PRETTIER_PREFIX + 'a/x': b'pretty-a', a.PRETTIER_PREFIX + 'a.txt': b'pretty-b',
               a.SNAPSHOTS[0]: raw(snapshot), a.SNAPSHOTS[1]: raw(snapshot)}
    memory = io.BytesIO()
    with tarfile.open(fileobj=memory, mode='w') as tar:
        for name, data in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    tar_raw = memory.getvalue()
    directory = Path(tempfile.mkdtemp(prefix='som-order-synthetic-'))
    archive = directory / 'fixture.zip'
    with zipfile.ZipFile(archive, mode='w') as writer:
        writer.writestr(a.TAR_NAME, tar_raw)
    zsha = a.sha(archive.read_bytes())
    run = {'id': a.RUN, 'head_sha': a.HEAD, 'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
    artifact = {'id': a.ARTIFACT, 'expired': False, 'digest': 'sha256:' + zsha,
                'workflow_run': {'id': a.RUN, 'head_sha': a.HEAD}}
    rows = {'npm': [('a/x', a.sha(b'npm-a')), ('a.txt', a.sha(b'npm-b'))],
            'prettier': [('a/x', a.sha(b'pretty-a')), ('a.txt', a.sha(b'pretty-b'))],
            'cache': [('a/x', digest), ('a.txt', digest)]}
    expected = {name: (len(value), a.ordered(value)['lexical'][2]) for name, value in rows.items()}
    return archive, tar_raw, run, artifact, zsha, expected


class OrderFixtureTests(unittest.TestCase):
    def test_three_independent_trees_have_both_exact_orders_and_raw_lists(self):
        archive, tar, run, artifact, zsha, expected = fixture()
        with mock.patch.object(a, 'ZIP_SHA', zsha), mock.patch.object(a, 'TAR_SHA', a.sha(tar)), mock.patch.object(a, 'EXPECTED', expected):
            report, output = a.diagnose(archive, run, artifact)
        self.assertEqual(len(output), 6)
        for label in ('npm', 'cache', 'prettier'):
            row = report['trees'][label]
            self.assertEqual(row['files'], 2)
            self.assertFalse(row['same_order'])
            self.assertEqual(row['first_difference'], {'index_zero_based': 0, 'lexical': 'a.txt', 'path': 'a/x'})
            for order in ('lexical', 'path'):
                self.assertEqual(a.sha(output[label + '-' + order + '.rows']), row[order + '_sha256'])
        self.assertNotEqual(output['npm-lexical.rows'], output['prettier-lexical.rows'])
        self.assertIn('not re-read', report['limitation'])

    def test_metadata_and_tar_sha_are_bound(self):
        archive, tar, run, artifact, zsha, expected = fixture()
        with mock.patch.object(a, 'ZIP_SHA', zsha), mock.patch.object(a, 'TAR_SHA', a.sha(tar)), mock.patch.object(a, 'EXPECTED', expected):
            with self.assertRaises(a.Refused):
                a.diagnose(archive, {**run, 'head_sha': 'wrong'}, artifact)
            with self.assertRaises(a.Refused):
                a.diagnose(archive, run, {**artifact, 'id': True})
        with mock.patch.object(a, 'ZIP_SHA', zsha), mock.patch.object(a, 'TAR_SHA', '0' * 64), mock.patch.object(a, 'EXPECTED', expected):
            with self.assertRaisesRegex(a.Refused, 'TAR SHA differs'):
                a.diagnose(archive, run, artifact)

    def test_duplicates_and_unsafe_names_are_rejected_without_deduplication(self):
        rows = [('same', '0' * 64), ('same', '1' * 64)]
        with self.assertRaisesRegex(a.Refused, 'duplicate tree path'):
            a.ordered(rows)
        for path in ('../escape', '/absolute', 'bad\rname', 'foo//bar'):
            with self.subTest(path=path), self.assertRaises(a.Refused):
                a.valid_name(path)
        with self.assertRaises(a.Refused):
            a.strict_json(b'{"entries": {}, "entries": {}}')

    def test_snapshot_record_type_and_content_are_validated(self):
        invalid = {'root_mode': False, 'entries': {}}
        with self.assertRaises(a.Refused):
            a.cache_rows(raw(invalid))
        invalid = {'root_mode': 0o555, 'entries': {'missing/child': {'kind': 'file', 'mode': 0o444, 'sha256': '0' * 64}}}
        with self.assertRaises(a.Refused):
            a.cache_rows(raw(invalid))

    def test_changed_snapshot_and_duplicate_archive_member_are_refused(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                before = {'root_mode': 0o555, 'entries': {}}
                after = {'root_mode': 0o555, 'entries': {'other': {'kind': 'directory', 'mode': 0o555}}}
                memory = io.BytesIO()
                with tarfile.open(fileobj=memory, mode='w') as tar:
                    for name, data in ((a.SNAPSHOTS[0], raw(before)), (a.SNAPSHOTS[1], raw(after))):
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
                    if duplicate:
                        member = tarfile.TarInfo(a.SNAPSHOTS[0])
                        member.size = 0
                        tar.addfile(member, io.BytesIO())
                source = memory.getvalue()
                with mock.patch.object(a, 'TAR_SHA', a.sha(source)):
                    with self.assertRaises(a.Refused):
                        a.read_tar(io.BytesIO(source))

if __name__ == '__main__':
    unittest.main()
