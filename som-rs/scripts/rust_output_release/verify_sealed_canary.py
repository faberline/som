#!/usr/bin/env python3
"""Match sealed output manifests to private runner raw files without printing them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import run_hosted_canaries as client

FILE = re.compile(r'run/(candidate|previous)/(journal\.json|hosted-canary-result\.json|'
                  r'[0-9]{4}-(request\.raw|response\.raw|cli-argv\.json|cli-stdout\.raw|cli-stderr\.raw))\Z')


def need(ok):
    if not ok:
        raise ValueError('sealed evidence coverage refused')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path, root):
    need(not path.is_symlink() and path.is_file() and path.resolve(strict=True).is_relative_to(root))
    info = path.stat()
    return {'sha256': digest(path), 'bytes': info.st_size, 'mode': stat.S_IMODE(info.st_mode)}


def verify(raw, sealed):
    raw, sealed = raw.resolve(strict=True), sealed.resolve(strict=True)
    need(raw != sealed and raw not in sealed.parents and sealed not in raw.parents)
    need({p.name for p in raw.iterdir()} == {'run'} and not (raw / 'run').is_symlink())
    found = {}
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(raw / 'run', followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in dirs:
            need(not (base / name).is_symlink())
        for name in files:
            path = base / name
            relative = path.relative_to(raw).as_posix()
            need(FILE.fullmatch(relative) is not None and relative not in found)
            record = regular(path, raw)
            # Match seal_hosted_evidence.mjs: executable files become 0755;
            # every other file becomes 0644 in the sealed manifest.
            record['mode'] = 0o755 if record['mode'] & 0o111 else 0o644
            found[relative] = record
    need(bool(found))
    index_path = sealed / 'evidence-index.json'
    regular(index_path, sealed)
    index = json.loads(index_path.read_bytes())
    need(index['version'] == 'som.m6-evidence-index.v1' and index['status'] == 'encrypted')
    need(type(index['packages']) is list and bool(index['packages']))
    sealed_names, covered = {'evidence-index.json'}, {}
    for number, entry in enumerate(index['packages']):
        stem = f'package-{number:03d}'
        manifest_name, archive_name = stem + '.manifest.json', stem + '.sealed'
        need(entry['manifest'] == manifest_name and entry['archive'] == archive_name)
        sealed_names.update((manifest_name, archive_name))
        manifest_path, archive_path = sealed / manifest_name, sealed / archive_name
        need(regular(manifest_path, sealed)['sha256'] == entry['manifest_sha256']
             and regular(archive_path, sealed)['sha256'] == entry['cipher_sha256'])
        manifest = json.loads(manifest_path.read_bytes())
        need(manifest['cipher_sha256'] == entry['cipher_sha256']
             and len(manifest['files']) == entry['total_files'] == manifest['total_files'])
        for item in manifest['files']:
            name = item['path']
            need(name not in covered and FILE.fullmatch(name) is not None
                 and type(item['bytes']) is int and item['bytes'] >= 0
                 and type(item['mode']) is int and item['mode'] in (0o644, 0o755)
                 and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']))
            covered[name] = {key: item[key] for key in ('sha256', 'bytes', 'mode')}
    need({p.name for p in sealed.iterdir()} == sealed_names and covered == found)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--sealed-root', type=Path, required=True)
    args = parser.parse_args()
    client.runner_identity()
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    for path in (args.raw_root, args.sealed_root):
        need(path.is_absolute() and not path.is_symlink() and path.resolve(strict=True).is_relative_to(temp))
    verify(args.raw_root, args.sealed_root)
    print('{"sealed_coverage":true,"production_acceptance_passed":false}')
    return 0


if __name__ == '__main__':
    try:
        status = main()
    except BaseException:
        print('sealed evidence verification failed', file=sys.stderr)
        status = 1
    raise SystemExit(status)
