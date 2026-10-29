#!/usr/bin/env python3
"""Verify and extract the exact same-job uploaded encrypted artifact ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import zipfile

LIMIT = 512 * 1024 * 1024
MEMBER_LIMIT = 257 * 1024 * 1024
HEX = re.compile(r'[0-9a-f]{64}')


def need(value, reason):
    if not value:
        raise ValueError(reason)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract(archive, dest, expected_zip, expected_index):
    need(HEX.fullmatch(expected_zip) and HEX.fullmatch(expected_index), 'digest format refused')
    need(file_hash(archive) == expected_zip and not dest.exists() and not dest.is_symlink(), 'ZIP identity or destination differs')
    with zipfile.ZipFile(archive) as z:
        members = z.infolist(); names = [x.filename for x in members]
        need(len(names) == len(set(names)) and 'evidence-index.json' in names, 'ZIP universe refused')
        need(all(not x.is_dir() and '/' not in x.filename and '\\' not in x.filename and
                 stat.S_IFMT(x.external_attr >> 16) in (0, stat.S_IFREG) and x.file_size <= MEMBER_LIMIT
                 for x in members) and sum(x.file_size for x in members) <= LIMIT, 'ZIP member refused')
        need(z.getinfo('evidence-index.json').file_size <= 1024 * 1024, 'index too large')
        index_raw = z.read('evidence-index.json')
        need(len(index_raw) <= 1024 * 1024 and hashlib.sha256(index_raw).hexdigest() == expected_index, 'index differs')
        doc = json.loads(index_raw)
        need(doc.get('version') == 'som.m6-evidence-index.v1' and doc.get('status') == 'encrypted' and
             isinstance(doc.get('packages'), list) and 0 < len(doc['packages']) <= 1000, 'index scope refused')
        expected = {'evidence-index.json': expected_index}
        for number, entry in enumerate(doc['packages']):
            stem = f'package-{number:03}'
            need(entry['archive'] == stem + '.sealed' and entry['manifest'] == stem + '.manifest.json' and
                 HEX.fullmatch(entry['cipher_sha256']) and HEX.fullmatch(entry['manifest_sha256']), 'index package refused')
            expected[entry['archive']] = entry['cipher_sha256']
            expected[entry['manifest']] = entry['manifest_sha256']
        need(set(names) == set(expected), 'ZIP package set differs')
        dest.mkdir(mode=0o700)
        for item in members:
            h = hashlib.sha256(); total = 0
            with z.open(item) as source, (dest / item.filename).open('xb') as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    total += len(chunk); need(total <= item.file_size, 'ZIP length overflow')
                    h.update(chunk); target.write(chunk)
            need(total == item.file_size and h.hexdigest() == expected[item.filename], 'ZIP member digest differs')
    return {'packages': len(doc['packages']), 'index_sha256': expected_index, 'zip_sha256': expected_zip}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--zip', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--zip-sha256', required=True)
    p.add_argument('--index-sha256', required=True)
    args = p.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'hosted runner required')
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    for candidate in (args.zip, args.out):
        need(candidate.is_absolute() and candidate.is_relative_to(temp) and '..' not in candidate.parts and
             not any(p.is_symlink() for p in (candidate, *candidate.parents)), 'runner scratch required')
    print(json.dumps(extract(args.zip, args.out, args.zip_sha256, args.index_sha256), sort_keys=True))


if __name__ == '__main__':
    main()
