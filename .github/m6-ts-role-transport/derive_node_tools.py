#!/usr/bin/env python3
"""Build a draft Linux Node/npm role map from the frozen official archive.

This is source-data preparation. It makes no product acceptance claim.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
from urllib.request import Request, build_opener

BASE = 'node-v26.5.0-linux-x64/'
URL = 'https://nodejs.org/dist/v26.5.0/node-v26.5.0-linux-x64.tar.xz'
ARCHIVE_SHA = '9f619528f1db5ddc41dccf54211066fb42228d69a156733c69cb9d6cc92e358c'
NODE_SHA = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
EXPECTED_PIN_SHA = '959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193'


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--pin', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--metadata', type=Path, required=True)
    args = p.parse_args()
    pin_raw = args.pin.read_bytes()
    need(sha(pin_raw) == EXPECTED_PIN_SHA, 'native pin source differs')
    pins = json.loads(pin_raw)
    rows = [row for row in pins['tools'] if row['tool'] == 'node' and row['host'] == 'x86_64-unknown-linux-gnu']
    need(len(rows) == 1, 'exact node role required')
    node = rows[0]
    need(node['url'] == URL and node['archive_sha256'] == ARCHIVE_SHA and
         node['executable_member'] == BASE + 'bin/node' and node['executable_sha256'] == NODE_SHA,
         'node pin differs')
    need(not args.out.exists() and not args.metadata.exists(), 'fresh outputs required')
    request = Request(URL, headers={'User-Agent': 'som-ts-input-qualification/1'})
    with build_opener().open(request, timeout=60) as response:
        need(response.geturl().startswith('https://nodejs.org/'), 'redirect refused')
        blob = response.read(50 * 1024 * 1024 + 1)
    need(len(blob) <= 50 * 1024 * 1024 and sha(blob) == ARCHIVE_SHA, 'node archive differs')
    selected = {}
    seen = set()
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:xz') as archive:
        for item in archive:
            name = item.name
            pp = PurePosixPath(name)
            need(name not in seen and not pp.is_absolute() and all(x not in ('', '.', '..') for x in pp.parts), 'invalid member')
            seen.add(name)
            selected_member = (name == BASE + 'bin/node' or name == BASE + 'LICENSE' or
                               name.startswith(BASE + 'lib/node_modules/npm/'))
            if not selected_member:
                continue
            need(item.isdir() or item.isfile(), 'selected archive link or special member refused')
            if item.isdir():
                continue
            need(0 <= item.size <= 256 * 1024 * 1024, 'member size refused')
            expanded += item.size
            need(expanded <= 512 * 1024 * 1024, 'expanded archive limit')
            raw = archive.extractfile(item).read()
            need(len(raw) == item.size, 'member truncated')
            relative = name[len(BASE):]
            mode = 0o755 if relative == 'bin/node' else 0o644
            selected[relative] = {'sha256': sha(raw), 'size': len(raw), 'mode': mode, 'data': raw}
    need(selected.get('bin/node', {}).get('sha256') == NODE_SHA, 'node binary differs')
    need('lib/node_modules/npm/bin/npm-cli.js' in selected, 'npm CLI missing')
    for name, expected in node['licenses'].items():
        need(name.startswith(BASE) and selected.get(name[len(BASE):], {}).get('sha256') == expected,
             'pinned license missing or changed')
    args.out.mkdir(parents=True, mode=0o700)
    files = {}
    for name, row in sorted(selected.items()):
        target = args.out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(row['data'])
        target.chmod(row['mode'])
        files[name] = {key: row[key] for key in ('sha256', 'size', 'mode')}
    result = {'version': 'som.ts-node-tools-source-inventory-draft.v1', 'status': 'draft',
              'pin_sha256': EXPECTED_PIN_SHA, 'archive_sha256': ARCHIVE_SHA,
              'node_sha256': NODE_SHA, 'files': files,
              'source_qualified_runtime': False, 'portability_calibration': 'unrun',
              'product_acceptance': 'not_run'}
    with args.metadata.open('x') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps({'status': 'draft', 'files': len(files), 'expanded_bytes': expanded,
                      'node_sha256': NODE_SHA, 'npm_cli_present': True,
                      'pinned_licenses': len(node['licenses']), 'product_acceptance': 'not_run'}, sort_keys=True))


if __name__ == '__main__':
    main()
