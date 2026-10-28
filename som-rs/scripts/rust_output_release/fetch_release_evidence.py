#!/usr/bin/env python3
"""Fetch the two immutable #130 evidence artifacts. This does not run a product."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

REPOSITORY = 'faberline/som'
REPOSITORY_ID = 1390374878
ARTIFACTS = {
    'original': {
        'id': 12843589912, 'name': 'm6-sealed-raw-evidence',
        'run_id': 40743738705, 'commit': '40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0',
        'bytes': 249935784, 'sha256': '33fbad9d63cfa257636384b9b7c10e1406f2306922e4f28e8189c79f703b041e',
        'index_sha256': '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea',
        'packages': 23,
    },
    'audit': {
        'id': 12846405979, 'name': 'm6-retained-audit-evidence',
        'run_id': 40755984489, 'commit': 'afd8739faae3e267cc1185d56de1e15831c6a177',
        'bytes': 7406130, 'sha256': '56b9d0423f7c9ef7311d5e6148994706cba44d8aa2751cd6b1d0f0964e856398',
        'index_sha256': '87ae9d65d1f8b643986e69e4f1da93a7f3fa3736cae7cec70a4fcf71290a36d2',
        'packages': 1,
    },
}


def need(condition, message='release evidence refused'):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_metadata(metadata, expected):
    run = metadata.get('workflow_run', {})
    need(tuple(metadata.get(k) for k in ('id', 'name', 'size_in_bytes', 'digest'))
         == (expected['id'], expected['name'], expected['bytes'], 'sha256:' + expected['sha256'])
         and metadata.get('expired') is False,
         'artifact identity, length, digest or expiry differs')
    need(run.get('id') == expected['run_id'] and run.get('head_sha') == expected['commit']
         and run.get('repository_id') == REPOSITORY_ID, 'artifact source differs')


def extract(archive, out, expected):
    need(not archive.is_symlink() and archive.is_file(), 'artifact is not a regular archive')
    need(archive.stat().st_size == expected['bytes'] and digest(archive) == expected['sha256'],
         'artifact bytes differ')
    need(not out.exists() and not out.is_symlink(), 'preserve existing extraction')
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names = [p.filename for p in members]
        admitted = {'evidence-index.json'} | {
            f'package-{i:03d}.{suffix}' for i in range(expected['packages'])
            for suffix in ('sealed', 'manifest.json')}
        need(len(names) == len(set(names)) and set(names) == admitted, 'artifact member set differs')
        need(sum(p.file_size for p in members) <= 512 * 1024 * 1024, 'artifact expansion is too large')
        for member in members:
            mode = stat.S_IFMT(member.external_attr >> 16)
            need(mode in (0, stat.S_IFREG) and 0 <= member.file_size <= 128 * 1024 * 1024,
                 'artifact member type or length differs')
            need(not member.flag_bits & 1, 'encrypted ZIP member refused')
        out.mkdir(mode=0o700)
        for member in members:
            count = 0
            with bundle.open(member) as source, (out / member.filename).open('xb') as target:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    need(count <= member.file_size, 'artifact member is too long')
                    target.write(chunk)
            need(count == member.file_size, 'artifact member is incomplete')
    need(digest(out / 'evidence-index.json') == expected['index_sha256'], 'artifact index differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true'
         and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS'
         and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY,
         'this fetch is for the hosted M6 release workflow')
    out = args.out.absolute()
    need(not out.exists() and not out.is_symlink(), 'preserve existing download')
    out.mkdir(parents=True, mode=0o700)
    for name, expected in ARTIFACTS.items():
        base = f'repos/{REPOSITORY}/actions/artifacts/{expected["id"]}'
        metadata_raw = subprocess.check_output(['gh', 'api', base], timeout=120)
        need(len(metadata_raw) <= 1024 * 1024)
        metadata = json.loads(metadata_raw)
        verify_metadata(metadata, expected)
        (out / f'{name}-metadata.json').write_bytes(metadata_raw)
        archive = out / f'{name}.zip'
        with archive.open('xb') as stream:
            subprocess.run(['gh', 'api', base + '/zip'], stdout=stream, check=True, timeout=300)
        extract(archive, out / name, expected)
    receipt = {'version': 'som.m6-release-download.v1', 'artifacts': ARTIFACTS, 'passed': True}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'passed': True, 'artifacts': list(ARTIFACTS)}))


if __name__ == '__main__':
    main()
