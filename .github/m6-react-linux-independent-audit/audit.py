#!/usr/bin/env python3
"""Unsealed independent audit of retained public React QA evidence, not acceptance."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import posixpath
import re
import tarfile
from urllib.parse import urlsplit

LOCK_SHA = 'c6f3856e1a620d78d4357ef54f89488612a5c1606bbf0219265fc984334b5814'
PACKAGE_SHA = 'ba9cda19860b88d13f319bc8762c03ba738c81e8aa32158f35a6864b6c1d8230'
AUTH_SHA = '959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193'
TOOLCHAIN_SHA = '49ad8ccd194b110e10a8141ecdfe2dafc7f649ff085ffb709bb06a7df7269bc2'
NODE_ARCHIVE_SHA = '9f619528f1db5ddc41dccf54211066fb42228d69a156733c69cb9d6cc92e358c'
NODE_SHA = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
PRETTIER_SHA = 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299'
BUNDLE_FORMATTER_SHA = '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8'
ROOT = 'som-react-linux-qa-closure'
NODE_PREFIX = 'node-v26.5.0-linux-x64/'
NPM_PREFIX = NODE_PREFIX + 'lib/node_modules/npm/'
MAX_TAR = 3 * 1024 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024

class AuditError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise AuditError(message)

def sha(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()

def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def bad_constant(_):
        raise AuditError('nonfinite JSON')
    try:
        return json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, ValueError) as exc:
        raise AuditError('invalid JSON') from exc

def valid_path(name):
    require(isinstance(name, str) and name and not any(c in name for c in ('\\', '\n', '\r', '\x00')),
            'invalid archive name')
    cleaned = name.rstrip('/')
    require(not cleaned.startswith('/') and all(part not in ('', '.', '..') for part in cleaned.split('/')),
            'unsafe archive name')
    return cleaned

def sha_stream(stream, algorithm='sha256'):
    h = hashlib.new(algorithm)
    while True:
        piece = stream.read(1024 * 1024)
        if not piece:
            break
        h.update(piece)
    return h.hexdigest()

def archive_index(archive, root=None, limit=MAX_TAR):
    result, total = {}, 0
    for m in archive.getmembers():
        name = valid_path(m.name)
        require(name not in result and 0 <= m.size <= MAX_FILE, 'duplicate or oversized archive member')
        require(m.isfile() or m.isdir() or m.issym(), 'unsupported archive member')
        if root:
            require(name == root or name.startswith(root + '/'), 'archive root differs')
        if m.issym():
            require(m.linkname and not m.linkname.startswith('/') and not any(c in m.linkname for c in ('\\', '\n', '\r', '\x00')),
                    'unsafe symlink')
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), m.linkname))
            require(not root or target == root or target.startswith(root + '/'), 'symlink leaves archive root')
        total += m.size
        require(total <= limit and len(result) < 300000, 'archive inventory exceeds bound')
        result[name] = m
    links = {name for name, member in result.items() if member.issym()}
    for name in result:
        parts = name.split('/')
        require(not any('/'.join(parts[:i]) in links for i in range(1, len(parts))), 'archive symlink parent')
    return result

def member_bytes(archive, index, name, max_size=MAX_FILE):
    member = index.get(name)
    require(member is not None and member.isfile() and member.size <= max_size, 'regular member missing')
    stream = archive.extractfile(member)
    require(stream is not None, 'member unreadable')
    data = stream.read(max_size + 1)
    require(len(data) == member.size, 'member truncated')
    return data

def file_hash(archive, index, name, algorithm='sha256'):
    member = index.get(name)
    require(member is not None and member.isfile(), 'regular member missing')
    stream = archive.extractfile(member)
    require(stream is not None, 'member unreadable')
    return sha_stream(stream, algorithm)

def link_valid(index, name, prefix):
    prefix = prefix.rstrip('/') + '/'
    seen = set()
    while index[name].issym():
        require(name not in seen, 'cyclic symlink')
        seen.add(name)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(name), index[name].linkname))
        require(target.startswith(prefix) and target in index, 'tree symlink target differs')
        name = target

def tree(archive, index, prefix, links_allowed):
    prefix = prefix.rstrip('/') + '/'
    lines, files, links = [], 0, 0
    for name in sorted(index):
        if not name.startswith(prefix):
            continue
        member = index[name]
        rel = name[len(prefix):]
        if member.isfile():
            lines.append(rel + '\0' + file_hash(archive, index, name) + '\n')
            files += 1
        elif member.issym():
            require(links_allowed, 'tree has symlink')
            link_valid(index, name, prefix)
            lines.append(rel + '\0symlink:' + member.linkname + '\n')
            links += 1
    return {'sha256': sha(''.join(lines).encode('utf-8')), 'files': files, 'symlinks': links}

def node_archive_snapshot(raw, licenses):
    """Decompress once. Never extract archive members to the filesystem."""
    nodes, digests, total = {}, {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r|xz') as official:
        for member in official:
            name = valid_path(member.name)
            require(name == NODE_PREFIX.rstrip('/') or name.startswith(NODE_PREFIX), 'node archive root differs')
            require(name not in nodes and 0 <= member.size <= MAX_FILE, 'node member repeated or oversized')
            require(member.isfile() or member.isdir() or member.issym(), 'node special member refused')
            if member.issym():
                require(member.linkname and not member.linkname.startswith('/')
                        and not any(c in member.linkname for c in ('\\', '\n', '\r', '\x00')), 'node link refused')
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), member.linkname))
                require(target.startswith(NODE_PREFIX), 'node link leaves archive')
            nodes[name] = member
            total += member.size
            require(total <= 1024 * 1024 * 1024 and len(nodes) < 300000, 'node archive exceeds bound')
            if member.isfile() and (name.startswith(NPM_PREFIX) or name == NODE_PREFIX + 'bin/node' or name in licenses):
                stream = official.extractfile(member)
                require(stream is not None, 'node member unreadable')
                h, count = hashlib.sha256(), 0
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    count += len(block)
                    h.update(block)
                require(count == member.size, 'node member truncated')
                digests[name] = h.hexdigest()
    links = {name for name, member in nodes.items() if member.issym()}
    for name in nodes:
        parts = name.split('/')
        require(not any('/'.join(parts[:i]) in links for i in range(1, len(parts))), 'node archive symlink parent')
    lines, files, link_count = [], 0, 0
    for name in sorted(nodes):
        if not name.startswith(NPM_PREFIX):
            continue
        member = nodes[name]
        rel = name[len(NPM_PREFIX):]
        if member.isfile():
            lines.append(rel + '\0' + digests[name] + '\n')
            files += 1
        elif member.issym():
            link_valid(nodes, name, NPM_PREFIX)
            lines.append(rel + '\0symlink:' + member.linkname + '\n')
            link_count += 1
    return {'sha256': sha(''.join(lines).encode('utf-8')), 'files': files, 'symlinks': link_count}, digests

def seg(hexadecimal):
    return hexadecimal[:2] + '/' + hexadecimal[2:4] + '/' + hexadecimal[4:]

def lock_packages(lock):
    require(isinstance(lock, dict) and lock.get('lockfileVersion') == 3 and isinstance(lock.get('packages'), dict), 'lock structure differs')
    entries = {name: row for name, row in lock['packages'].items() if name}
    require(len(entries) == 123, 'lock package count differs')
    urls = set()
    for path, row in entries.items():
        require(valid_path(path) == path and path.startswith('node_modules/') and 'node_modules/' not in path[len('node_modules/'):], 'lock package path differs')
        require(isinstance(row, dict) and isinstance(row.get('version'), str), 'lock entry differs')
        url = row.get('resolved')
        require(isinstance(url, str), 'lock url missing')
        parsed = urlsplit(url)
        require(parsed.scheme == 'https' and parsed.netloc == 'registry.npmjs.org' and parsed.path.startswith('/') and not parsed.query and not parsed.fragment, 'lock URL differs')
        require(url not in urls, 'lock URL repeated')
        urls.add(url)
        integrity_bytes(row.get('integrity'))
    return entries

def integrity_bytes(integrity):
    require(isinstance(integrity, str) and integrity.startswith('sha512-'), 'integrity algorithm differs')
    try:
        value = base64.b64decode(integrity[7:], validate=True)
    except (ValueError, TypeError) as exc:
        raise AuditError('invalid integrity') from exc
    require(len(value) == 64 and base64.b64encode(value).decode('ascii') == integrity[7:], 'integrity bytes differ')
    return value

def package_inventory(outer, index, lock, manifest, seed):
    rows = manifest.get('packages')
    entries = seed.get('entries')
    require(isinstance(rows, list) and isinstance(entries, list) and len(rows) == len(entries) == len(lock), 'package inventory length differs')
    manifests = {row.get('path'): row for row in rows}
    seeds = {row.get('path'): row for row in entries}
    require(len(manifests) == len(seeds) == len(lock) and set(manifests) == set(seeds) == set(lock), 'package inventory paths differ')
    expected_files = set()
    for path, expected in lock.items():
        row, se = manifests[path], seeds[path]
        name = path[len('node_modules/'):]
        require(row.get('name') == name and row.get('url') == expected['resolved'] and row.get('version') == expected['version']
                and row.get('integrity') == expected['integrity'] and row.get('optional') == (expected.get('optional') is True)
                and row.get('license') == expected.get('license'), 'manifest lock binding differs')
        require(all(se.get(key) == row.get(key) for key in ('path', 'url', 'integrity', 'sha256')), 'seed lock binding differs')
        digest = row.get('sha256')
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'tarball digest invalid')
        filename = 'tarballs/' + digest + '.tgz'
        require(row.get('file') == filename, 'tarball filename differs')
        raw = member_bytes(outer, index, ROOT + '/' + filename, 80 * 1024 * 1024)
        require(sha(raw) == digest and len(raw) == row.get('bytes') and hashlib.sha512(raw).digest() == integrity_bytes(expected['integrity']), 'package bytes differ')
        expected_files.add(ROOT + '/' + filename)
        inspect_package(raw, name, expected['version'], row)
    actual_files = {name for name, m in index.items() if name.startswith(ROOT + '/tarballs/') and m.isfile()}
    require(actual_files == expected_files, 'tarball file inventory differs')
    return manifests

def inspect_package(raw, expected_name, expected_version, row):
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        index = archive_index(tar, limit=1024 * 1024 * 1024)
        allowed = {'package'}
        if expected_name.startswith('@types/'):
            allowed.add(expected_name[len('@types/'):])
        roots = {path.split('/', 1)[0] for path in index}
        require(len(roots) == 1 and roots <= allowed and all(not m.issym() for m in index.values()), 'package archive roots differ')
        root = next(iter(roots))
        data = member_bytes(tar, index, root + '/package.json', 4 * 1024 * 1024)
        doc = strict_json(data)
        require(doc.get('name') == expected_name and doc.get('version') == expected_version, 'package identity differs')
        require(sha(data) == row.get('metadata_sha256') and doc.get('license') == row.get('declared_license') and len(index) == row.get('members'), 'package metadata receipt differs')
        license_files = {}
        for path, m in index.items():
            if m.isfile() and re.search(r'(^|/)(?:licen[cs]e|copying)(?:\.[^/]*)?$', path, re.I):
                license_files[path] = file_hash(tar, index, path)
        require(license_files == row.get('license_files'), 'package license receipt differs')

def node_source(outer, index, authority, manifest, report):
    raw = member_bytes(outer, index, ROOT + '/node-archive.tar.xz', 80 * 1024 * 1024)
    require(sha(raw) == NODE_ARCHIVE_SHA, 'node archive digest differs')
    rows = [row for row in authority.get('tools', []) if row.get('tool') == 'node' and row.get('host') == 'x86_64-unknown-linux-gnu']
    require(len(rows) == 1 and rows[0].get('archive_sha256') == NODE_ARCHIVE_SHA and rows[0].get('executable_sha256') == NODE_SHA and rows[0].get('archive_bytes') == len(raw), 'node source authority differs')
    licenses = rows[0].get('licenses')
    require(isinstance(licenses, dict) and len(licenses) > 0, 'node licenses missing')
    official_tree, digests = node_archive_snapshot(raw, licenses)
    require(digests.get(NODE_PREFIX + 'bin/node') == NODE_SHA and file_hash(outer, index, ROOT + '/node/bin/node') == NODE_SHA, 'node executable differs')
    retained_tree = tree(outer, index, ROOT + '/node/lib/node_modules/npm', True)
    require(official_tree == retained_tree == report.get('npm_tree'), 'complete npm trees differ')
    require(digests.get(NPM_PREFIX + 'bin/npm-cli.js') == manifest['node']['npm_cli_sha256'], 'npm CLI differs')
    for name, digest in licenses.items():
        require(digests.get(name) == digest, 'node license differs')
    require(manifest['node'].get('licenses') == licenses and manifest['node'].get('license_count') == len(licenses), 'node license receipt differs')
    return official_tree

def verify_cache(outer, index, rows):
    cache = ROOT + '/npm-cache-new'
    members = {name: member for name, member in index.items() if name == cache or name.startswith(cache + '/')}
    require(cache in members and members[cache].isdir(), 'cache missing')
    require(all(not m.issym() and m.mode & 0o222 == 0 for m in members.values()), 'cache writable or linked')
    prefix = cache + '/_cacache/index-v5/'
    records = {}
    expected_content = set()
    for name, member in members.items():
        if not name.startswith(prefix) or not member.isfile():
            continue
        data = member_bytes(outer, index, name, 1024 * 1024)
        for line in data.splitlines():
            if not line:
                continue
            parts = line.split(b'\t')
            require(len(parts) == 2 and re.fullmatch(b'[0-9a-f]{40}', parts[0]) and hashlib.sha1(parts[1]).hexdigest().encode() == parts[0], 'cache index line digest differs')
            entry = strict_json(parts[1])
            key = entry.get('key')
            require(isinstance(key, str) and key not in records and name == prefix + seg(sha(key.encode('utf-8'))), 'cache bucket or key differs')
            records[key] = entry
    expected_keys = {'make-fetch-happen:request-cache:' + row['url'] for row in rows.values()}
    require(set(records) == expected_keys and len(records) == 123, 'cache index inventory differs')
    for row in rows.values():
        key = 'make-fetch-happen:request-cache:' + row['url']
        entry = records[key]
        require(entry.get('integrity') == row['integrity'] and entry.get('size') == row['bytes']
                and isinstance(entry.get('metadata'), dict) and entry['metadata'].get('url') == row['url'], 'cache index binding differs')
        digest = integrity_bytes(row['integrity']).hex()
        path = cache + '/_cacache/content-v2/sha512/' + seg(digest)
        require(file_hash(outer, index, path, 'sha512') == digest and file_hash(outer, index, path) == row['sha256']
                and index[path].size == row['bytes'], 'cache content differs')
        expected_content.add(path)
    actual_content = {name for name, m in members.items() if name.startswith(cache + '/_cacache/content-v2/') and m.isfile()}
    require(actual_content == expected_content, 'cache content inventory differs')
    allowed = {name for name in members if name.startswith(prefix)} | expected_content
    require(all(name in allowed for name, m in members.items() if m.isfile()), 'unknown cache file')
    return tree(outer, index, cache, False)

def command_receipts(outer, index, report):
    commands = report.get('commands')
    names = ['node-version', 'npm-version', 'node-elf', 'npm-ci-offline', 'prettier-version']
    require(isinstance(commands, list) and [entry.get('name') for entry in commands] == names, 'command inventory differs')
    runroot = '/home/runner/work/_temp/som-react-linux-qa-closure'
    node = runroot + '/node/bin/node'
    npm = runroot + '/node/lib/node_modules/npm/bin/npm-cli.js'
    argv = [[node, '--version'], [node, npm, '--version'], ['/usr/bin/readelf', '-d', node],
            [node, npm, 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'],
            [node, runroot + '/probe/product/node_modules/prettier/bin/prettier.cjs', '--version']]
    stdout = {}
    for entry, expected in zip(commands, argv):
        require(entry.get('outcome') == 'exited' and entry.get('exit_code') == 0 and entry.get('argv') == expected, 'command receipt differs')
        for suffix in ('stdout', 'stderr'):
            raw = member_bytes(outer, index, ROOT + '/probe/' + entry['name'] + '.' + suffix, 16 * 1024 * 1024)
            require(sha(raw) == entry.get(suffix + '_sha256'), 'command output hash differs')
            if suffix == 'stdout':
                stdout[entry['name']] = raw.decode('utf-8', 'strict')
    require(stdout['node-version'].strip() == report.get('node_version') == 'v26.5.0', 'node version differs')
    require(stdout['npm-version'].strip() == report.get('npm_version') and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', report['npm_version']), 'npm version differs')
    require(stdout['prettier-version'].strip() == report.get('prettier_version') == '3.9.10', 'prettier version differs')
    require('(NEEDED)' in stdout['node-elf'] and not re.search(r'\(NEEDED\).*libnode', stdout['node-elf'], re.I), 'node ELF evidence differs')
    return len(commands)

def audit(repo, tar_path, expected_tar_sha, source_commit):
    require(re.fullmatch('[0-9a-f]{64}', expected_tar_sha) and re.fullmatch('[0-9a-f]{40}', source_commit), 'expected identity invalid')
    require(not tar_path.is_symlink() and tar_path.is_file() and tar_path.stat().st_size <= MAX_TAR, 'evidence tar invalid')
    with tar_path.open('rb') as stream:
        require(sha_stream(stream) == expected_tar_sha, 'tar digest differs')
    source = {}
    for path, digest in {'som-rs/contracts/react-output-v1/reference/package-lock.json': LOCK_SHA,
                         'som-rs/contracts/react-output-v1/reference/package.json': PACKAGE_SHA,
                         'som-rs/contracts/native-tools-v1/artifacts.json': AUTH_SHA,
                         'som-rs/contracts/react-output-v1/toolchain.json': TOOLCHAIN_SHA}.items():
        candidate = repo / path
        require(candidate.is_file() and not candidate.is_symlink(), 'source file missing')
        raw = candidate.read_bytes()
        require(sha(raw) == digest, 'source file digest differs')
        source[path] = raw
    lock = lock_packages(strict_json(source['som-rs/contracts/react-output-v1/reference/package-lock.json']))
    authority = strict_json(source['som-rs/contracts/native-tools-v1/artifacts.json'])
    toolchain = strict_json(source['som-rs/contracts/react-output-v1/toolchain.json'])
    require(toolchain['formatter']['sha256'] == BUNDLE_FORMATTER_SHA and toolchain['formatter']['prettier_tree_sha256'] == PRETTIER_SHA, 'React formatter source differs')
    with tarfile.open(tar_path, mode='r:') as outer:
        index = archive_index(outer, root=ROOT)
        manifest_raw = member_bytes(outer, index, ROOT + '/public-inputs.json', 16 * 1024 * 1024)
        seed_raw = member_bytes(outer, index, ROOT + '/seed-report.json', 16 * 1024 * 1024)
        manifest, seed = strict_json(manifest_raw), strict_json(seed_raw)
        report = strict_json(member_bytes(outer, index, ROOT + '/probe/report.json', 1024 * 1024))
        require(manifest.get('version') == 'som.react-linux-qa-public-inputs.v1' and manifest.get('source_commit') == source_commit
                and manifest.get('product_acceptance_passed') is False and seed.get('version') == 'som.react-linux-qa-cache-seed.v1'
                and seed.get('manifest_sha256') == sha(manifest_raw) and seed.get('lock_sha256') == LOCK_SHA
                and seed.get('product_acceptance_passed') is False, 'source receipt differs')
        require(report.get('version') == 'som.react-linux-qa-offline-probe.v1' and report.get('state') == 'offline-reference-install-observed'
                and report.get('product_acceptance_passed') is False and report.get('react_bundle_formatter_sha256') == BUNDLE_FORMATTER_SHA
                and report.get('node_archive_sha256') == NODE_ARCHIVE_SHA and report.get('node_sha256') == NODE_SHA, 'probe report differs')
        rows = package_inventory(outer, index, lock, manifest, seed)
        npm = node_source(outer, index, authority, manifest, report)
        cache = verify_cache(outer, index, rows)
        require(cache == report.get('cache_tree'), 'retained cache tree differs')
        prettier = tree(outer, index, ROOT + '/probe/product/node_modules/prettier', False)
        require(prettier == {'sha256': PRETTIER_SHA, 'files': 56, 'symlinks': 0} and prettier == report.get('prettier_tree'), 'prettier tree differs')
        require(file_hash(outer, index, ROOT + '/probe/product/package-lock.json') == LOCK_SHA
                and file_hash(outer, index, ROOT + '/probe/product/package.json') == PACKAGE_SHA, 'retained reference files differ')
        for name, version in (('@rolldown/binding-linux-x64-gnu', '1.2.15'), ('lightningcss-linux-x64-gnu', '1.33.0')):
            meta = strict_json(member_bytes(outer, index, ROOT + '/probe/product/node_modules/' + name + '/package.json', 4 * 1024 * 1024))
            require(meta.get('name') == name and meta.get('version') == version, 'installed platform package differs')
        commands = command_receipts(outer, index, report)
        formula = {'node': NODE_SHA, 'libnode': None, 'prettier_tree_sha256': PRETTIER_SHA}
        encoded = json.dumps(formula, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        require(report.get('qa_formatter_formula') == formula and report.get('qa_formatter_sha256') == sha(encoded), 'QA formatter formula differs')
    node_authority = next(x for x in authority['tools'] if x.get('tool') == 'node' and x.get('host') == 'x86_64-unknown-linux-gnu')
    return {'version': 'som.react-linux-independent-audit.v1', 'state': 'partial-evidence-consistent',
            'product_acceptance_passed': False, 'tar_sha256': expected_tar_sha, 'source_commit': source_commit,
            'package_count': len(rows), 'node_license_count': len(node_authority['licenses']),
            'npm_tree': npm, 'cache_tree': cache, 'prettier_tree': prettier, 'command_count': commands,
            'unverified': ['Cache and npm before/after equality is a recorder claim; only the final retained snapshot is independently checked.',
                           'Command exits and streams are recorded observations; execution environment and absence of network access were not independently observed.',
                           'Installed platform package metadata is checked; their other installed bytes are not independently compared to their tarballs.',
                           'The React Linux profile and cache are unsealed; no product or release gate is accepted.']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--tar', type=Path, required=True)
    parser.add_argument('--expected-tar-sha256', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub hosted Linux required')
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    require(args.output.is_absolute() and args.output.parent.resolve() == temp and not args.output.exists() and not args.output.is_symlink(), 'output path refused')
    try:
        result = audit(args.repo, args.tar, args.expected_tar_sha256, args.source_commit)
    except Exception as exc:
        result = {'version': 'som.react-linux-independent-audit.v1', 'state': 'failed', 'product_acceptance_passed': False,
                  'failure_type': type(exc).__name__}
        if isinstance(exc, AuditError):
            result['failure_reason'] = str(exc)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, sort_keys=True, indent=2)
        output.write('\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] == 'partial-evidence-consistent' else 1

if __name__ == '__main__':
    raise SystemExit(main())
