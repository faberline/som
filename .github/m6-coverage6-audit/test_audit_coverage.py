"""Run synthetic evidence-audit fixtures on a GitHub runner only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import audit_coverage as a
import synthetic_fixture


class AuditCoverageFixtures(unittest.TestCase):
    def test_json_rejects_duplicates_nonfinite_and_invalid_utf8(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(a.AuditError):
                a.parse(raw)

    def test_lsof_requires_one_pid_and_canonical_unique_descriptors(self):
        self.assertTrue(a.lsof_owned(b'p42\nf6\n', 42))
        self.assertTrue(a.lsof_owned(b'p42\nf0\nf6\n', 42))
        for raw in (b'p42\n', b'p9\nf6\n', b'p42\nf6\np9\nf7\n', b'p42\nf6\nf06\n',
                    b'p42\nf6\nf6\n', b'p42\nfabc\n', b'p42\nf6', b'p42\r\nf6\r\n'):
            with self.subTest(raw=raw):
                self.assertFalse(a.lsof_owned(raw, 42))

    def test_frozen_product_map_requires_all_files_and_bytes(self):
        files = {'src/a.py': 'print(1)\n', 'README.md': 'hello\n'}
        expected = {key: a.sha(value.encode()) for key, value in files.items()}
        self.assertEqual(a.check_files(files, expected), expected)
        for wrong in ({'src/a.py': expected['src/a.py']}, {**expected, 'extra': '0' * 64},
                      {**expected, 'README.md': '0' * 64}):
            with self.subTest(wrong=wrong), self.assertRaises(a.AuditError):
                a.check_files(files, wrong)
        with self.assertRaises(a.AuditError):
            a.check_files({'../bad': 'x'}, {'../bad': a.sha(b'x')})

    def test_vectors_and_signed_zero_are_preserved(self):
        p = Path(os.environ['GITHUB_WORKSPACE']) / 'som-rs/contracts/compiler-v1/canonical-vectors.json'
        a.check_vectors(a.parse(p.read_bytes()))
        self.assertNotEqual(a.canonical(-0.0), a.canonical(0.0))

    def test_saved_requires_exact_path_length_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'value').write_bytes(b'abc')
            record = {'path': 'value', 'bytes': 3, 'sha256': a.sha(b'abc')}
            self.assertEqual(a.saved(root, record, 'value'), b'abc')
            for bad in ({**record, 'bytes': 4}, {**record, 'sha256': '0' * 64}, {**record, 'path': '../value'}):
                with self.subTest(bad=bad), self.assertRaises(a.AuditError):
                    a.saved(root, bad, 'value')
            (root / 'link').symlink_to(root / 'value')
            with self.assertRaises(a.AuditError):
                a.physical(root, 'link')

    def test_complete_six_case_evidence_and_adversarial_mutations(self):
        vectors = (Path(os.environ['GITHUB_WORKSPACE']) / 'som-rs/contracts/compiler-v1/canonical-vectors.json').read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            repo, root, authority, pins, wires, summary = synthetic_fixture.make(Path(tmp), vectors)
            summary_path = root / 'summary.json'
            def write_summary():
                summary_path.write_bytes(a.compact(summary))
            with patch.object(a, 'AUTHORITY', authority), patch.object(a, 'PINS', pins), patch.object(a, 'WIRES', wires):
                result = a.audit(repo, root)
                self.assertEqual(result['checked_cases'], 6)
                self.assertFalse(result['product_acceptance'])
                summary['ownership_probes'] = summary['ownership_probes'][:-1]
                write_summary()
                with self.assertRaises(a.AuditError):
                    a.audit(repo, root)
        for kind in ('unrun', 'float_pid', 'float_cleanup_pid', 'wrong_raw', 'extra', 'link'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                repo, root, authority, pins, wires, summary = synthetic_fixture.make(
                    Path(tmp), vectors, link_name='run/owned-001/stdout.raw' if kind == 'link' else None)
                if kind == 'unrun':
                    summary['rows'][0]['status'] = 'unrun'
                    (root / 'summary.json').write_bytes(a.compact(summary))
                elif kind == 'float_pid':
                    summary['ownership_probes'][0]['pid'] = 42.0
                    (root / 'summary.json').write_bytes(a.compact(summary))
                elif kind == 'float_cleanup_pid':
                    summary['cleanup']['pid'] = 42.0
                    (root / 'summary.json').write_bytes(a.compact(summary))
                elif kind == 'wrong_raw':
                    (root / 'run/owned-001/stdout.raw').write_bytes(b'p99\nf6\n')
                elif kind == 'extra':
                    (root / 'run/extra').write_bytes(b'')
                with patch.object(a, 'AUTHORITY', authority), patch.object(a, 'PINS', pins), patch.object(a, 'WIRES', wires):
                    with self.assertRaises(a.AuditError):
                        a.audit(repo, root)

    def test_case_id_duplicates_fail(self):
        with self.assertRaises(a.AuditError):
            a.unique([{'id': 'same'}, {'id': 'same'}])


if __name__ == '__main__':
    unittest.main()
