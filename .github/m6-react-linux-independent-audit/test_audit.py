"""Synthetic audit tests. Run only on a GitHub-hosted runner."""
import base64
import hashlib
import io
import json
import os
import tarfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
import audit as a


def archive(entries, mode='w'):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode=mode) as tar:
        for name, data, kind, target in entries:
            member = tarfile.TarInfo(name)
            if kind == 'dir':
                member.type = tarfile.DIRTYPE
                member.mode = 0o555
                tar.addfile(member)
            elif kind == 'link':
                member.type = tarfile.SYMTYPE
                member.linkname = target
                member.mode = 0o555
                tar.addfile(member)
            else:
                member.size = len(data)
                member.mode = 0o444
                tar.addfile(member, io.BytesIO(data))
    return out.getvalue()


def opened(raw, mode='r:'):
    return tarfile.open(fileobj=io.BytesIO(raw), mode=mode)


class SyntheticAudit(unittest.TestCase):
    def test_json_and_paths_fail_closed(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(a.AuditError):
                a.strict_json(raw)
        for path in ('../outside', '/absolute', 'safe/../outside', 'safe//outside', 'safe\\outside', 'safe\nname'):
            with self.subTest(path=path), self.assertRaises(a.AuditError):
                a.valid_path(path)

    def test_archive_refuses_duplicates_escape_and_symlink_parent(self):
        bads = [
            [('root/x', b'1', 'file', None), ('root/x', b'2', 'file', None)],
            [('root/../outside', b'1', 'file', None)],
            [('root/link', b'', 'link', '../../outside')],
            [('root/link', b'', 'link', 'target'), ('root/link/child', b'x', 'file', None)],
        ]
        for entries in bads:
            with self.subTest(entries=entries), opened(archive(entries)) as tar:
                with self.assertRaises(a.AuditError):
                    a.archive_index(tar, 'root')

    def test_tree_hash_and_link_are_derived_from_raw_members(self):
        raw = archive([('root/a', b'A', 'file', None), ('root/l', b'', 'link', 'a')])
        with opened(raw) as tar:
            index = a.archive_index(tar, 'root')
            expected = ('a\0' + a.sha(b'A') + '\nl\0symlink:a\n').encode()
            self.assertEqual(a.tree(tar, index, 'root', True), {'sha256': a.sha(expected), 'files': 1, 'symlinks': 1})
            with self.assertRaises(a.AuditError):
                a.tree(tar, index, 'root', False)

    def test_tree_refuses_cyclic_and_dangling_links(self):
        for entries in ([('root/a', b'', 'link', 'b'), ('root/b', b'', 'link', 'a')],
                        [('root/a', b'', 'link', 'missing')]):
            with self.subTest(entries=entries), opened(archive(entries)) as tar:
                index = a.archive_index(tar, 'root')
                with self.assertRaises(a.AuditError):
                    a.tree(tar, index, 'root', True)

    def test_type_package_metadata_binding_and_mixed_root_refusal(self):
        metadata = json.dumps({'name': '@types/demo', 'version': '1.2.3', 'license': 'MIT'}).encode()
        valid = archive([('demo/package.json', metadata, 'file', None), ('demo/LICENSE', b'L', 'file', None)], 'w:gz')
        row = {'metadata_sha256': a.sha(metadata), 'declared_license': 'MIT', 'members': 2,
               'license_files': {'demo/LICENSE': a.sha(b'L')}}
        a.inspect_package(valid, '@types/demo', '1.2.3', row)
        with self.assertRaises(a.AuditError):
            a.inspect_package(valid, '@types/demo', '2.0.0', row)
        mixed = archive([('demo/package.json', metadata, 'file', None), ('package/LICENSE', b'L', 'file', None)], 'w:gz')
        with self.assertRaises(a.AuditError):
            a.inspect_package(mixed, '@types/demo', '1.2.3', row)

    def test_node_archive_tree_and_license_must_match_retained_bytes(self):
        p = a.NODE_PREFIX
        np = a.NPM_PREFIX
        node = b'fake-node'
        cli = b'fake-npm-cli'
        # The source order differs from the tree's lexical order.
        official = archive([(p + 'bin/node', node, 'file', None),
                            (np + 'bin/npm-cli.js', cli, 'file', None),
                            (np + 'bin/alias', b'', 'link', 'npm-cli.js'),
                            (np + 'LICENSE', b'L', 'file', None)], 'w:xz')
        license_map = {np + 'LICENSE': a.sha(b'L')}
        authority = {'tools': [{'tool': 'node', 'host': 'x86_64-unknown-linux-gnu',
                                'archive_sha256': a.sha(official), 'archive_bytes': len(official),
                                'executable_sha256': a.sha(node), 'licenses': license_map}]}
        manifest = {'node': {'npm_cli_sha256': a.sha(cli), 'license_count': 1, 'licenses': license_map}}
        outer = archive([(a.ROOT + '/node-archive.tar.xz', official, 'file', None),
                         (a.ROOT + '/node/bin/node', node, 'file', None),
                         (a.ROOT + '/node/lib/node_modules/npm/bin/npm-cli.js', cli, 'file', None),
                         (a.ROOT + '/node/lib/node_modules/npm/bin/alias', b'', 'link', 'npm-cli.js'),
                         (a.ROOT + '/node/lib/node_modules/npm/LICENSE', b'L', 'file', None)])
        with opened(outer) as tar, mock.patch.object(a, 'NODE_ARCHIVE_SHA', a.sha(official)), mock.patch.object(a, 'NODE_SHA', a.sha(node)):
            index = a.archive_index(tar, a.ROOT)
            expected = a.tree(tar, index, a.ROOT + '/node/lib/node_modules/npm', True)
            self.assertEqual(a.node_source(tar, index, authority, manifest, {'npm_tree': expected}), expected)
            with self.assertRaises(a.AuditError):
                a.node_source(tar, index, authority, manifest, {'npm_tree': {**expected, 'files': 999}})

    def test_cache_index_and_content_bind_every_key(self):
        payload = b'public package fixture'
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(payload).digest()).decode()
        cache = a.ROOT + '/npm-cache-new'
        rows, members = {}, [(cache, b'', 'dir', None)]
        for i in range(123):
            url = f'https://registry.npmjs.org/p{i}/-/p{i}-1.tgz'
            rows[f'node_modules/p{i}'] = {'url': url, 'integrity': integrity, 'bytes': len(payload), 'sha256': a.sha(payload)}
            key = 'make-fetch-happen:request-cache:' + url
            entry = {'key': key, 'integrity': integrity, 'size': len(payload), 'metadata': {'url': url}}
            raw = json.dumps(entry, separators=(',', ':')).encode()
            line = b'\n' + hashlib.sha1(raw).hexdigest().encode() + b'\t' + raw
            path = cache + '/_cacache/index-v5/' + a.seg(a.sha(key.encode()))
            members.append((path, line, 'file', None))
        content = cache + '/_cacache/content-v2/sha512/' + a.seg(hashlib.sha512(payload).hexdigest())
        members.append((content, payload, 'file', None))
        with opened(archive(members)) as tar:
            index = a.archive_index(tar, a.ROOT)
            self.assertEqual(a.verify_cache(tar, index, rows)['files'], 124)
            corrupt = dict(rows)
            corrupt['node_modules/p0'] = {**corrupt['node_modules/p0'], 'sha256': '0' * 64}
            with self.assertRaises(a.AuditError):
                a.verify_cache(tar, index, corrupt)
        damaged = list(members)
        name, data, kind, target = damaged[1]
        damaged[1] = (name, data.replace(b'\t', b'\tX', 1), kind, target)
        with opened(archive(damaged)) as tar:
            with self.assertRaises(a.AuditError):
                a.verify_cache(tar, a.archive_index(tar, a.ROOT), rows)

    def test_command_receipt_requires_all_successes_and_output_hashes(self):
        root = '/home/runner/work/_temp/som-react-linux-qa-closure'
        node = root + '/node/bin/node'
        npm = root + '/node/lib/node_modules/npm/bin/npm-cli.js'
        specs = [('node-version', [node, '--version'], b'v26.5.0\n'),
                 ('npm-version', [node, npm, '--version'], b'11.17.0\n'),
                 ('node-elf', ['/usr/bin/readelf', '-d', node], b' (NEEDED) libc.so\n'),
                 ('npm-ci-offline', [node, npm, 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'], b'ok\n'),
                 ('prettier-version', [node, root + '/probe/product/node_modules/prettier/bin/prettier.cjs', '--version'], b'3.9.10\n')]
        entries, cmds = [], []
        for name, args, out in specs:
            cmds.append({'name': name, 'argv': args, 'outcome': 'exited', 'exit_code': 0,
                         'stdout_sha256': a.sha(out), 'stderr_sha256': a.sha(b'')})
            entries.extend([(a.ROOT + '/probe/' + name + '.stdout', out, 'file', None),
                            (a.ROOT + '/probe/' + name + '.stderr', b'', 'file', None)])
        report = {'commands': cmds, 'node_version': 'v26.5.0', 'npm_version': '11.17.0', 'prettier_version': '3.9.10'}
        with opened(archive(entries)) as tar:
            index = a.archive_index(tar, a.ROOT)
            self.assertEqual(a.command_receipts(tar, index, report), 5)
            failed = {**report, 'commands': [*cmds[:-1], {**cmds[-1], 'exit_code': 1}]}
            with self.assertRaises(a.AuditError):
                a.command_receipts(tar, index, failed)
            corrupted = {**report, 'commands': [{**cmds[0], 'stdout_sha256': '0' * 64}, *cmds[1:]]}
            with self.assertRaises(a.AuditError):
                a.command_receipts(tar, index, corrupted)


if __name__ == '__main__':
    unittest.main()
