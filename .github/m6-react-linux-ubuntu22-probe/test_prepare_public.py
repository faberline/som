"""Synthetic closure-source fixtures. Execute only on a GitHub runner."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import prepare_public as p


def tar_data(entries, mode='w:gz'):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        for name, content, link in entries:
            member = tarfile.TarInfo(name)
            if link is not None:
                member.type = tarfile.SYMTYPE
                member.linkname = link
                tar.addfile(member)
            else:
                member.size = len(content)
                member.mode = 0o755 if name.endswith('/bin/node') else 0o644
                tar.addfile(member, io.BytesIO(content))
    return buf.getvalue()


class PreparePublicFixtures(unittest.TestCase):
    def test_json_urls_and_paths_refuse_unsafe_inputs(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(p.Refusal):
                p.parse(raw)
        self.assertEqual(p.allowed_url('https://registry.npmjs.org/pkg/-/pkg.tgz', 'registry.npmjs.org'),
                         'https://registry.npmjs.org/pkg/-/pkg.tgz')
        for url in ('http://registry.npmjs.org/x', 'https://user@registry.npmjs.org/x',
                    'https://registry.npmjs.org.evil/x', 'https://registry.npmjs.org/x?q=1'):
            with self.subTest(url=url), self.assertRaises(p.Refusal):
                p.allowed_url(url, 'registry.npmjs.org')
        for name in ('../outside', '/absolute', 'a//b', 'a/../b', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(p.Refusal):
                p.relative(name)

    def test_lock_requires_all_123_exact_sha512_entries(self):
        sri = 'sha512-' + base64.b64encode(hashlib.sha512(b'synthetic').digest()).decode()
        packages = {'': {}}
        for i in range(123):
            packages[f'node_modules/test-{i}'] = {'version': '1.0.0',
                'resolved': f'https://registry.npmjs.org/test-{i}/-/test-{i}-1.0.0.tgz', 'integrity': sri}
        lock = {'lockfileVersion': 3, 'packages': packages}
        self.assertEqual(len(p.lock_rows(lock)), 123)
        packages['node_modules/test-0']['integrity'] = 'sha1-invalid'
        with self.assertRaises(p.Refusal):
            p.lock_rows(lock)

    def test_package_identity_and_archive_paths(self):
        data = json.dumps({'name': '@scope/demo', 'version': '1.0.0', 'license': 'MIT'}).encode()
        blob = tar_data([('package/package.json', data, None), ('package/LICENSE', b'license', None)])
        row = {'name': '@scope/demo', 'version': '1.0.0'}
        result = p.inspect_package(blob, row)
        self.assertEqual(result['metadata_sha256'], p.sha(data))
        self.assertEqual(result['license_files'], {'package/LICENSE': p.sha(b'license')})
        with self.assertRaises(p.Refusal):
            p.inspect_package(blob, {**row, 'version': '2.0.0'})
        bad = tar_data([('package/package.json', data, None), ('package/../escape', b'x', None)])
        with self.assertRaises(p.Refusal):
            p.inspect_package(bad, row)
        linked = tar_data([('package/package.json', data, None), ('package/link', b'', '../outside')])
        with self.assertRaises(p.Refusal):
            p.inspect_package(linked, row)

    def test_node_extraction_pins_identity_and_limits_links(self):
        prefix = p.NODE_PREFIX
        node = b'synthetic-node'
        cli = b'synthetic-npm'
        license_bytes = b'synthetic-license'
        entries = [(prefix + 'bin/node', node, None), (p.NPM_PREFIX + 'bin/npm-cli.js', cli, None),
                   (p.NPM_PREFIX + 'LICENSE', license_bytes, None),
                   (p.NPM_PREFIX + 'bin/npm-link', b'', 'npm-cli.js')]
        blob = tar_data(entries, 'w:xz')
        row = {'archive_bytes': len(blob), 'licenses': {p.NPM_PREFIX + 'LICENSE': p.sha(license_bytes)}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(p, 'NODE_ARCHIVE', p.sha(blob)), \
             mock.patch.object(p, 'NODE_EXEC', p.sha(node)):
            root = Path(tmp)
            result = p.extract_node(blob, row, root)
            self.assertEqual(result['node_sha256'], p.sha(node))
            self.assertEqual(result['npm_cli_sha256'], p.sha(cli))
            self.assertEqual((root / 'lib/node_modules/npm/bin/npm-link').read_bytes(), cli)
        with self.assertRaises(p.Refusal):
            p.safe_link_target(p.NPM_PREFIX + 'bin/link', '../../../../../../../etc/passwd')
        with self.assertRaises(p.Refusal):
            p.safe_link_target(p.NPM_PREFIX + 'bin/link', '/etc/passwd')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(p, 'NODE_ARCHIVE', p.sha(blob)), \
             mock.patch.object(p, 'NODE_EXEC', p.sha(node)), mock.patch.object(p, 'MAX_NODE_MEMBER', 2):
            with self.assertRaises(p.Refusal):
                p.extract_node(blob, row, Path(tmp))

    def test_type_package_named_root_and_mixed_roots(self):
        metadata = json.dumps({'name': '@types/aria-query', 'version': '1.0.0'}).encode()
        row = {'name': '@types/aria-query', 'version': '1.0.0'}
        good = tar_data([('aria-query/package.json', metadata, None), ('aria-query/LICENSE', b'L', None)])
        self.assertEqual(p.inspect_package(good, row)['metadata_sha256'], p.sha(metadata))
        mixed = tar_data([('aria-query/package.json', metadata, None), ('package/other', b'x', None)])
        with self.assertRaises(p.Refusal):
            p.inspect_package(mixed, row)
        wrong = tar_data([('other/package.json', metadata, None)])
        with self.assertRaises(p.Refusal):
            p.inspect_package(wrong, row)


if __name__ == '__main__':
    unittest.main()
