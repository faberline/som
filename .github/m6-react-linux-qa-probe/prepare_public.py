#!/usr/bin/env python3
"""DRAFT: collect pinned public React Linux QA inputs, not acceptance."""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import subprocess
import tarfile
import urllib.parse
import urllib.request

SOURCE = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
LOCK_PATH = 'som-rs/contracts/react-output-v1/reference/package-lock.json'
PACKAGE_PATH = 'som-rs/contracts/react-output-v1/reference/package.json'
ARTIFACT_PATH = 'som-rs/contracts/native-tools-v1/artifacts.json'
TOOLCHAIN_PATH = 'som-rs/contracts/react-output-v1/toolchain.json'
PINS = {LOCK_PATH: 'c6f3856e1a620d78d4357ef54f89488612a5c1606bbf0219265fc984334b5814',
        PACKAGE_PATH: 'ba9cda19860b88d13f319bc8762c03ba738c81e8aa32158f35a6864b6c1d8230',
        ARTIFACT_PATH: '959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193',
        TOOLCHAIN_PATH: '49ad8ccd194b110e10a8141ecdfe2dafc7f649ff085ffb709bb06a7df7269bc2'}
NODE_ARCHIVE = '9f619528f1db5ddc41dccf54211066fb42228d69a156733c69cb9d6cc92e358c'
NODE_EXEC = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
PRETTIER_ARCHIVE = '9dc72045591793dff972432fb78722f7ab91aaa90a7fa8c1261ede55969246b7'
PRETTIER_TREE = 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299'
NODE_PREFIX = 'node-v26.5.0-linux-x64/'
NPM_PREFIX = NODE_PREFIX + 'lib/node_modules/npm/'
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_NODE_MEMBER = 256 * 1024 * 1024
PROGRESS = {'stage': 'validate-source', 'package': None, 'completed_packages': 0}

class Refusal(ValueError):
    pass

def need(ok, why):
    if not ok:
        raise Refusal(why)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def json_pairs(items):
    out = {}
    for key, value in items:
        need(key not in out, 'duplicate JSON member')
        out[key] = value
    return out

def parse(raw):
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=json_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal('nonfinite JSON')))
    except (UnicodeError, ValueError) as exc:
        raise Refusal('invalid JSON') from exc

def relative(name):
    need(type(name) is str and name and '\\' not in name and '\x00' not in name, 'invalid path')
    parts = name.rstrip('/').split('/')
    need(not PurePosixPath(name).is_absolute() and all(x not in ('', '.', '..') for x in parts), 'unsafe path')
    return '/'.join(parts)

def allowed_url(url, hostname):
    p = urllib.parse.urlsplit(url)
    need(p.scheme == 'https' and p.hostname == hostname and p.netloc == hostname and p.path.startswith('/')
         and not p.query and not p.fragment and not p.username and not p.password, 'public URL differs')
    return url

class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hostname):
        self.hostname = hostname
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, self.hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def download(url, hostname, max_bytes):
    allowed_url(url, hostname)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameHostRedirect(hostname))
    request = urllib.request.Request(url, headers={'User-Agent': 'som-react-qa-closure-probe/1'})
    with opener.open(request, timeout=90) as response:
        allowed_url(response.geturl(), hostname)
        need(response.status == 200, 'public download failed')
        raw = response.read(max_bytes + 1)
    need(len(raw) <= max_bytes, 'download exceeds bound')
    return raw

def lock_rows(lock):
    need(type(lock) is dict and lock.get('lockfileVersion') == 3 and type(lock.get('packages')) is dict,
         'lock format differs')
    rows = []
    for path, item in lock['packages'].items():
        if path == '':
            continue
        relative(path)
        need(path.startswith('node_modules/') and type(item) is dict and type(item.get('version')) is str,
             'lock package differs')
        name = path.removeprefix('node_modules/')
        need(name and 'node_modules/' not in name and type(item.get('integrity')) is str,
             'lock name or integrity differs')
        encoded = item['integrity']
        need(encoded.startswith('sha512-'), 'lock integrity algorithm differs')
        try:
            digest = base64.b64decode(encoded.removeprefix('sha512-'), validate=True)
        except ValueError as exc:
            raise Refusal('invalid lock integrity') from exc
        need(len(digest) == 64 and base64.b64encode(digest).decode('ascii') == encoded.removeprefix('sha512-'),
             'invalid lock integrity bytes')
        url = allowed_url(item.get('resolved'), 'registry.npmjs.org')
        rows.append({'path': path, 'name': name, 'version': item['version'], 'url': url,
                     'integrity': encoded, 'optional': item.get('optional') is True,
                     'license': item.get('license')})
    need(len(rows) == 123 and len({x['path'] for x in rows}) == len({x['url'] for x in rows}) == 123,
         'lock inventory differs')
    return sorted(rows, key=lambda x: x['path'])

def inspect_package(blob, row):
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz')
        members = archive.getmembers()
    except (tarfile.TarError, OSError) as exc:
        raise Refusal('npm tarball invalid') from exc
    need(0 < len(members) <= 20000, 'npm member count differs')
    names, license_files, metadata, expanded = set(), {}, None, 0
    for member in members:
        name = relative(member.name)
        need(name == 'package' or name.startswith('package/'), 'npm member outside package')
        need(name not in names and 0 <= member.size <= 32 * 1024 * 1024, 'npm member repeated or too large')
        names.add(name)
        expanded += member.size
        need(expanded <= MAX_TOTAL, 'npm expanded bytes exceed bound')
        need(member.isfile() or member.isdir(), 'npm special or linked entry refused')
        if member.isfile() and (name == 'package/package.json' or re.search(r'(^|/)(?:licen[cs]e|copying)(?:\.[^/]*)?$', name, re.I)):
            data = archive.extractfile(member).read(member.size + 1)
            need(len(data) == member.size, 'npm member truncated')
            if name == 'package/package.json':
                metadata = data
            else:
                license_files[name] = sha(data)
    archive.close()
    need(metadata is not None, 'npm package metadata missing')
    doc = parse(metadata)
    need(doc.get('name') == row['name'] and doc.get('version') == row['version'], 'npm package identity differs')
    return {'metadata_sha256': sha(metadata), 'declared_license': doc.get('license'),
            'license_files': license_files, 'members': len(names)}

def safe_link_target(member_name, target):
    need(type(target) is str and target and not target.startswith('/') and '\\' not in target and '\x00' not in target,
         'node symlink target refused')
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member_name), target))
    need(resolved.startswith(NPM_PREFIX), 'node symlink leaves npm subtree')
    return resolved

def extract_node(blob, row, root):
    need(len(blob) == row['archive_bytes'] and sha(blob) == NODE_ARCHIVE, 'node archive differs')
    archive = tarfile.open(fileobj=io.BytesIO(blob), mode='r:xz')
    members = archive.getmembers()
    need(0 < len(members) < 30000, 'node member count differs')
    names, selected, links, license_hashes, expanded = set(), [], [], {}, 0
    licenses = row['licenses']
    for member in members:
        name = relative(member.name)
        need(name not in names and 0 <= member.size <= MAX_NODE_MEMBER, 'node member repeated or too large')
        names.add(name)
        expanded += member.size
        need(expanded <= MAX_TOTAL, 'node expanded bytes exceed bound')
        if name in licenses:
            need(member.isfile(), 'node license is not regular')
            raw = archive.extractfile(member).read(member.size + 1)
            need(len(raw) == member.size and sha(raw) == licenses[name], 'node license differs')
            license_hashes[name] = sha(raw)
        if name != NODE_PREFIX + 'bin/node' and not name.startswith(NPM_PREFIX):
            continue
        need(member.isfile() or member.isdir() or member.issym(), 'node selected special entry refused')
        if member.issym():
            safe_link_target(name, member.linkname)
            links.append((name, member.linkname))
        else:
            selected.append((name, member))
    need(set(license_hashes) == set(licenses), 'node licenses missing')
    need(any(n == NODE_PREFIX + 'bin/node' for n, _ in selected), 'node executable missing')
    selected_names = {n for n, _ in selected}
    link_names = {n for n, _ in links}
    for name in selected_names | link_names:
        parts = PurePosixPath(name).parts
        need(not any('/'.join(parts[:i]) in link_names for i in range(1, len(parts))), 'archive link is a parent')
    for name, member in selected:
        relative_name = name.removeprefix(NODE_PREFIX)
        target = root / relative_name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        data = archive.extractfile(member).read(member.size + 1)
        need(len(data) == member.size, 'node member truncated')
        with target.open('xb') as stream:
            stream.write(data)
        target.chmod(0o755 if member.mode & 0o111 else 0o644)
    for name, link in links:
        target = root / name.removeprefix(NODE_PREFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(link)
        need(target.resolve().is_relative_to(root.resolve()) and target.exists(), 'node link target invalid')
    archive.close()
    node = root / 'bin/node'
    need(node.is_file() and not node.is_symlink() and sha(node.read_bytes()) == NODE_EXEC, 'node executable differs')
    cli = root / 'lib/node_modules/npm/bin/npm-cli.js'
    need(cli.is_file() and not cli.is_symlink(), 'bundled npm CLI missing')
    return {'archive_sha256': sha(blob), 'node_sha256': NODE_EXEC, 'license_count': len(license_hashes),
            'licenses': license_hashes, 'npm_cli_sha256': sha(cli.read_bytes()), 'links': len(links)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'host differs')
    repo = args.repo
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    out = args.out
    need(repo.is_absolute() and repo.resolve() == repo and out.is_absolute() and out.parent == temp
         and out.name == 'som-react-linux-qa-closure' and not out.exists() and not out.is_symlink(), 'output refused')
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()
    need(head == os.environ.get('GITHUB_SHA') and re.fullmatch('[0-9a-f]{40}', head), 'checkout differs')
    subprocess.run(['git', 'merge-base', '--is-ancestor', SOURCE, head], cwd=repo, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    raw_sources = {}
    for name, expected in PINS.items():
        p = repo / name
        need(not p.is_symlink() and p.is_file(), 'source missing')
        raw = p.read_bytes()
        need(sha(raw) == expected, 'source differs')
        raw_sources[name] = raw
    lock, artifact, toolchain = (parse(raw_sources[x]) for x in (LOCK_PATH, ARTIFACT_PATH, TOOLCHAIN_PATH))
    rows = lock_rows(lock)
    node_rows = [x for x in artifact['tools'] if x.get('tool') == 'node' and x.get('host') == 'x86_64-unknown-linux-gnu']
    need(len(node_rows) == 1 and node_rows[0]['archive_sha256'] == NODE_ARCHIVE
         and node_rows[0]['executable_sha256'] == NODE_EXEC, 'native node authority differs')
    need(toolchain['formatter']['sha256'] == '636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8'
         and toolchain['formatter']['prettier_tree_sha256'] == PRETTIER_TREE, 'React reference formatter differs')
    out.mkdir()
    (out / 'node').mkdir()
    (out / 'tarballs').mkdir()
    node_row = node_rows[0]
    PROGRESS['stage'] = 'download-node'
    node_raw = download(node_row['url'], 'nodejs.org', MAX_ARCHIVE)
    PROGRESS['stage'] = 'inspect-node'
    node_info = extract_node(node_raw, node_row, out / 'node')
    with (out / 'node-archive.tar.xz').open('xb') as stream:
        stream.write(node_raw)
    inventory, total = [], 0
    for row in rows:
        PROGRESS.update(stage='download-package', package=row['name'])
        raw = download(row['url'], 'registry.npmjs.org', MAX_ARCHIVE)
        total += len(raw)
        need(total <= MAX_TOTAL, 'package download total exceeds bound')
        actual = 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode('ascii')
        need(actual == row['integrity'], 'package integrity differs')
        PROGRESS['stage'] = 'inspect-package'
        meta = inspect_package(raw, row)
        digest = sha(raw)
        if row['name'] == 'prettier':
            need(digest == PRETTIER_ARCHIVE, 'prettier archive differs')
        filename = digest + '.tgz'
        destination = out / 'tarballs' / filename
        if destination.exists() or destination.is_symlink():
            need(not destination.is_symlink() and destination.is_file() and destination.read_bytes() == raw,
                 'shared tarball differs')
        else:
            with destination.open('xb') as stream:
                stream.write(raw)
        inventory.append({**row, 'sha256': digest, 'bytes': len(raw), 'file': 'tarballs/' + filename, **meta})
        PROGRESS['completed_packages'] = len(inventory)
        (out / 'partial-inventory.json').write_text(json.dumps({'state': 'incomplete', 'packages': inventory}, sort_keys=True, indent=2) + '\n')
    result = {'version': 'som.react-linux-qa-public-inputs.v1', 'source_commit': head,
              'role': 'unsealed public QA preparation; not acceptance', 'product_acceptance_passed': False,
              'source_pins': PINS, 'node': node_info, 'packages': inventory, 'package_bytes': total}
    (out / 'public-inputs.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'state': 'public-inputs-prepared', 'packages': len(inventory), 'product_acceptance_passed': False}))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        report = {'state': 'failed', 'failure_type': type(exc).__name__, 'progress': PROGRESS,
                  'product_acceptance_passed': False}
        if isinstance(exc, Refusal):
            report['failure_reason'] = str(exc)
        print(json.dumps(report))
        raise SystemExit(1)
