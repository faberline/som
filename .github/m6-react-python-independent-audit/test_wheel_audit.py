"""Synthetic wheel checks. Run only on a GitHub-hosted runner."""
import base64
import hashlib
import io
import os
import stat
import unittest
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import wheel_audit as a


def digest(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()


def make_fixture(*, metadata_version='1.0.0', header_tag='py2.py3-none-any', tampered_record=False,
                 missing_record=False, include_notice=True, extra=None, signature=None, signature_recorded=True):
    filename = 'demo-1.0.0-py2.py3-none-any.whl'
    prefix = 'demo-1.0.0.dist-info/'
    metadata = ('Metadata-Version: 2.3\nName: demo\nVersion: ' + metadata_version
                + '\nRequires-Python: >=3.12\nRequires-Dist: other>=1\nLicense: MIT\n'
                + ('License-File: LICENSE\n' if include_notice else '') + '\n').encode()
    wheel = ('Wheel-Version: 1.0\nTag: ' + header_tag + '\n\n').encode()
    files = {'demo/__init__.py': b'fixture code\n', prefix + 'METADATA': metadata,
             prefix + 'WHEEL': wheel}
    if include_notice:
        files[prefix + 'LICENSE'] = b'fixture license\n'
    if signature:
        files[prefix + signature] = b'fixture signature\n'
    rows = []
    for index, (name, value) in enumerate(files.items()):
        algorithm = ('sha256', 'sha384', 'sha512')[index % 3]
        encoded = base64.urlsafe_b64encode(hashlib.new(algorithm, value).digest()).rstrip(b'=').decode()
        if tampered_record and index == 0:
            encoded = 'bad'
        if not (missing_record and index == 0) and not (signature and name == prefix + signature and not signature_recorded):
            rows.append([name, algorithm + '=' + encoded, str(len(value))])
    record_name = prefix + 'RECORD'
    rows.append([record_name, '', ''])
    record = ''.join(','.join(row) + '\n' for row in rows).encode()
    files[record_name] = record
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        if extra is not None:
            name, value, kind = extra
            if kind == 'symlink':
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, value)
            else:
                archive.writestr(name, value)
    raw = data.getvalue()
    members = [{'path': name, 'sha256': digest(value), 'size': len(value),
                'record_binding': 'self' if name == record_name else
                                  'external-signature' if signature and name == prefix + signature and not signature_recorded
                                  else 'sha256'}
               for name, value in sorted(files.items())]
    notices = []
    if include_notice:
        notice_path = prefix + 'LICENSE'
        body = files[notice_path]
        notices.append({'path': notice_path, 'sha256': digest(body), 'size': len(body),
                        'preserved': digest(notice_path.encode()) + '.txt'})
    receipt = {'filename': filename, 'sha256': digest(raw), 'size': len(raw), 'name': 'demo', 'version': '1.0.0',
               'metadata_sha256': digest(metadata), 'record_sha256': digest(record),
               'dist_info_dir': prefix.rstrip('/'), 'data_dir': 'demo-1.0.0.data',
               'directories': [], 'record_entries': rows, 'wheel_metadata_sha256': digest(wheel),
               'requires_dist': ['other>=1'], 'requires_python': '>=3.12', 'license_expression': None,
               'license_label': 'MIT', 'license_files_declared': ['LICENSE'] if include_notice else [],
               'license_notices': notices,
               'notice_status': 'present' if notices else 'none_in_publisher_wheel; independent legal review unresolved',
               'members': members, 'record_verified': True, 'independent_metadata_license_audit': 'unrun'}
    return raw, filename, receipt, files


class WheelAuditTests(unittest.TestCase):
    def test_real_bytes_metadata_record_tags_and_notice(self):
        raw, filename, receipt, files = make_fixture()
        result = a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)
        self.assertEqual(result['wheel_tags'], ['py2-none-any', 'py3-none-any'])
        self.assertEqual(result['raw_evidence']['METADATA'], files['demo-1.0.0.dist-info/METADATA'])
        self.assertEqual(result['raw_evidence']['RECORD'], files['demo-1.0.0.dist-info/RECORD'])
        self.assertEqual(result['raw_evidence'][receipt['license_notices'][0]['preserved']],
                         files['demo-1.0.0.dist-info/LICENSE'])
        self.assertEqual(result['legal_license_review'], 'unrun')
        self.assertIs(result['product_acceptance_passed'], False)

    def test_missing_notice_is_reported_as_unreviewed(self):
        raw, filename, receipt, _ = make_fixture(include_notice=False)
        result = a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)
        self.assertEqual(result['identity']['notice_status'], 'none_in_publisher_wheel; independent legal review unresolved')
        self.assertEqual(result['legal_license_review'], 'unrun')

    def test_signature_is_unverified_with_or_without_record_entry(self):
        for signature in ('RECORD.jws', 'RECORD.p7s'):
            for recorded in (True, False):
                with self.subTest(signature=signature, recorded=recorded):
                    raw, filename, receipt, _ = make_fixture(signature=signature, signature_recorded=recorded)
                    result = a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)
                    self.assertEqual(result['signatures_unverified'], ['demo-1.0.0.dist-info/' + signature])

    def test_record_hash_and_missing_entry_are_refused(self):
        for choice in ('bad-hash', 'missing'):
            with self.subTest(choice=choice):
                raw, filename, receipt, _ = make_fixture(tampered_record=choice == 'bad-hash',
                                                        missing_record=choice == 'missing')
                with self.assertRaises(a.WheelAuditError):
                    a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)

    def test_wrong_identity_tag_and_recorded_notice_fail(self):
        raw, filename, receipt, _ = make_fixture(metadata_version='9.9.9')
        with self.assertRaises(a.WheelAuditError):
            a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)
        raw, filename, receipt, _ = make_fixture(header_tag='py3-none-any')
        with self.assertRaises(a.WheelAuditError):
            a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)
        raw, filename, receipt, _ = make_fixture()
        receipt['license_notices'][0]['sha256'] = '0' * 64
        with self.assertRaises(a.WheelAuditError):
            a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)

    def test_duplicate_traversal_and_link_fail(self):
        for extra in (('demo/__init__.py', b'other', 'file'), ('../outside', b'x', 'file'),
                      ('demo/linked', b'../../outside', 'symlink'), ('demo/data/', b'directory data', 'file')):
            with self.subTest(extra=extra[0]):
                raw, filename, receipt, _ = make_fixture(extra=extra)
                with self.assertRaises(a.WheelAuditError):
                    a.audit_wheel(raw, filename, 'demo', '1.0.0', receipt)

    def test_bounds_and_truncated_zip_fail(self):
        raw, filename, receipt, _ = make_fixture()
        bad = raw[:-16]
        receipt['sha256'], receipt['size'] = digest(bad), len(bad)
        with self.assertRaises(a.WheelAuditError):
            a.audit_wheel(bad, filename, 'demo', '1.0.0', receipt)
        self.assertRaises(a.WheelAuditError, a.expanded_tags, ('a.' * 4097) + '-none-any')


if __name__ == '__main__':
    unittest.main()
