#!/usr/bin/env python3
"""DRAFT: restore selected public inputs for a GitHub-only QA replay."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import tarfile

ROOT = 'som-react-linux-qa-closure'
PRODUCER = '62637173ffeecde2655482377a218b22bf54618a'
TAR_SHA = '5f453d9e3f4fc17e36c9c16d17dacc157a34915fc7c9e477f49da3e81b807171'
ARTIFACT_ID = 12873066929
RUN_ID = 40817465924
TREES = ('node', 'tarballs', 'npm-cache-new')
FILES = ('node-archive.tar.xz', 'public-inputs.json', 'seed-report.json')
REPORT = 'probe/report.json'
MAX_FILES = 150000
MAX_MEMBER = 512 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024 * 1024

class Refusal(ValueError):
    pass

def need(value, reason):
    if not value:
        raise Refusal(reason)

def path_name(name):
    need(isinstance(name, str) and name and '\\' not in name and '\x00' not in name, 'invalid archive path')
    parts = name.rstrip('/').split('/')
    need(not PurePosixPath(name).is_absolute() and all(p not in ('', '.', '..') for p in parts), 'unsafe archive path')
    return '/'.join(parts)

def selected(name):
    if name == ROOT:
        return True
    if not name.startswith(ROOT + '/'):
        return False
    rel = name[len(ROOT) + 1:]
    return rel in FILES or rel == REPORT or any(rel == item or rel.startswith(item + '/') for item in TREES)

def inspect(members):
    need(0 < len(members) <= MAX_FILES, 'archive member count differs')
    seen, chosen, links, size = set(), {}, {}, 0
    for member in members:
        name = path_name(member.name)
        need(name not in seen, 'duplicate archive path')
        seen.add(name)
        if not selected(name):
            continue
        need(member.isfile() or member.isdir() or member.issym(), 'unsupported archive member')
        need(0 <= member.size <= MAX_MEMBER and not member.mode & 0o7000, 'archive member limit or mode differs')
        size += member.size
        need(size <= MAX_TOTAL, 'archive total exceeds bound')
        if member.issym():
            need(name.startswith(ROOT + '/node/lib/node_modules/npm/'), 'link outside npm')
            target = member.linkname
            need(target and not target.startswith('/') and '\\' not in target and '\x00' not in target, 'unsafe link')
            dest = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            need(dest.startswith(ROOT + '/node/lib/node_modules/npm/'), 'link leaves npm')
            links[name] = target
        chosen[name] = member
    required = {ROOT, *(ROOT + '/' + x for x in TREES), *(ROOT + '/' + x for x in FILES), ROOT + '/' + REPORT}
    need(required <= chosen.keys(), 'required archive paths missing')
    need(chosen[ROOT].isdir() and all(chosen[ROOT + '/' + x].isdir() for x in TREES), 'required directory differs')
    need(all(chosen[ROOT + '/' + x].isfile() for x in (*FILES, REPORT)), 'required file differs')
    for name in chosen:
        parts = name.split('/')
        need(not any('/'.join(parts[:i]) in links for i in range(1, len(parts))), 'archive link is a parent')
    return chosen

def restore(tarpath, target, expected):
    need(not tarpath.is_symlink() and tarpath.is_file() and re.fullmatch('[0-9a-f]{64}', expected), 'tar input refused')
    digest = hashlib.sha256()
    with tarpath.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    actual = digest.hexdigest()
    need(actual == expected, 'tar hash differs')
    need(not target.exists() and not target.is_symlink(), 'output exists')
    with tarfile.open(tarpath, 'r:') as tar:
        chosen = inspect(tar.getmembers())
        target.mkdir(mode=0o700)
        dirs, links = [], []
        for name, member in chosen.items():
            rel = name[len(ROOT):].lstrip('/')
            if not rel:
                dirs.append((target, member.mode)); continue
            if rel == REPORT:
                rel = 'producer-report.json'
            path = target / rel
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                dirs.append((path, member.mode))
            elif member.issym():
                links.append((path, member.linkname))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = tar.extractfile(member)
                need(data is not None, 'archive content missing')
                remaining = member.size
                with path.open('xb') as out:
                    while remaining:
                        block = data.read(min(1024 * 1024, remaining))
                        need(block, 'archive content truncated')
                        out.write(block)
                        remaining -= len(block)
                path.chmod(member.mode & 0o777)
        for path, link in links:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(link)
        npm = target / 'node/lib/node_modules/npm'
        for path, _ in links:
            need(path.resolve().is_relative_to(npm.resolve()) and path.exists(), 'unresolved npm link')
        # The new root must remain writable for the new probe directory.
        for path, mode in sorted(dirs, key=lambda item: len(item[0].parts), reverse=True):
            if path != target:
                path.chmod(mode & 0o777)
    receipt = {'state': 'public-source-restored', 'producer_head': PRODUCER,
               'source_tar_sha256': actual, 'artifact_id': ARTIFACT_ID,
               'run_id': RUN_ID, 'product_acceptance_passed': False}
    (target / 'restore-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    print(json.dumps(receipt))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tar', type=Path, required=True)
    parser.add_argument('--expected-tar-sha256', required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'host refused')
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    need(args.tar == temp / 'react-linux-qa-public-inputs.tar' and not args.tar.is_symlink(), 'tar path refused')
    need(args.expected_tar_sha256 == TAR_SHA, 'fixed source tar identity differs')
    restore(args.tar, temp / ROOT, args.expected_tar_sha256)

if __name__ == '__main__':
    main()
