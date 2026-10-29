#!/usr/bin/env python3
"""Diagnose ordering in one pinned public replay, not acceptance evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import tarfile
import zipfile

RUN = 40818588943
HEAD = '448862a76656095696f2791d46428a0b87e063e1'
ARTIFACT = 12873517359
ZIP_SHA = 'b117cb220f22d9045dd1727dcd2beb0b487e42287830895202ebf659ee4d5fff'
TAR_SHA = 'e4c43d06da51dbcb780f4c8aef41c53121dd7c081aff907019793c5ae6239d7b'
TAR_NAME = 'react-ubuntu22-replay.tar'
NPM_PREFIX = 'probe/source-replay/lib/node_modules/npm/'
PRETTIER_PREFIX = 'probe/product/node_modules/prettier/'
SNAPSHOTS = ('probe/cache-before.json', 'probe/cache-after.json')
EXPECTED = {'npm': (1921, '69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558'),
            'cache': (246, '5267851624684b07307609f59d0b13705bd7934a1fd59bde2045463a3ee6ddda'),
            'prettier': (56, 'f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299')}
MAX_TAR = 2 * 1024**3
MAX_MEMBER = 512 * 1024**2
MAX_ENTRIES = 200000

class Refused(ValueError):
    pass

def need(value, reason):
    if not value:
        raise Refused(reason)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    try:
        return json.loads(data.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refused('nonfinite JSON')))
    except (UnicodeError, ValueError) as exc:
        raise Refused('invalid JSON') from exc

def valid_name(name):
    need(isinstance(name, str) and name and not any(c in name for c in ('\\', '\x00', '\n', '\r')),
         'unsafe path')
    need(not PurePosixPath(name).is_absolute() and all(p not in ('', '.', '..') for p in name.split('/')),
         'unsafe path')
    return name

def hash_stream(source):
    d = hashlib.sha256()
    while True:
        block = source.read(1024 * 1024)
        if not block:
            return d.hexdigest()
        d.update(block)

def host():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub hosted Linux required')
    lines = Path('/etc/os-release').read_text(encoding='utf-8').splitlines()
    fields = {}
    for line in lines:
        if '=' in line:
            key, val = line.split('=', 1)
            need(key not in fields, 'duplicate OS field')
            fields[key] = val.strip('"')
    need(fields.get('ID') == 'ubuntu' and fields.get('VERSION_ID') == '22.04', 'Ubuntu 22.04 required')

def metadata(run, artifact):
    need(type(run.get('id')) is int and run['id'] == RUN and run.get('head_sha') == HEAD
         and run.get('status') == 'completed' and run.get('conclusion') == 'success'
         and type(run.get('run_attempt')) is int and run['run_attempt'] == 1, 'run metadata differs')
    workflow = artifact.get('workflow_run')
    need(type(artifact.get('id')) is int and artifact['id'] == ARTIFACT and artifact.get('expired') is False
         and artifact.get('digest') == 'sha256:' + ZIP_SHA and isinstance(workflow, dict)
         and type(workflow.get('id')) is int and workflow['id'] == RUN and workflow.get('head_sha') == HEAD,
         'artifact metadata differs')

def cache_rows(raw):
    doc = strict_json(raw)
    need(isinstance(doc, dict) and set(doc) == {'root_mode', 'entries'}
         and type(doc['root_mode']) is int and 0 <= doc['root_mode'] <= 0o7777
         and doc['root_mode'] & 0o222 == 0 and isinstance(doc['entries'], dict), 'invalid cache snapshot')
    entries, rows = doc['entries'], []
    for name, item in entries.items():
        valid_name(name)
        need(isinstance(item, dict) and type(item.get('mode')) is int and 0 <= item['mode'] <= 0o7777
             and item['mode'] & 0o222 == 0, 'invalid cache mode')
        if item.get('kind') == 'directory':
            need(set(item) == {'kind', 'mode'}, 'invalid cache directory')
        else:
            need(item.get('kind') == 'file' and set(item) == {'kind', 'mode', 'sha256'}
                 and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                 'invalid cache file')
            rows.append((name, item['sha256']))
        parts = name.split('/')
        for i in range(1, len(parts)):
            parent = entries.get('/'.join(parts[:i]))
            need(isinstance(parent, dict) and parent.get('kind') == 'directory', 'cache parent missing')
    return doc, rows

class HashedReader:
    def __init__(self, source):
        self.source, self.digest, self.size = source, hashlib.sha256(), 0
    def read(self, amount=-1):
        data = self.source.read(amount)
        self.size += len(data)
        need(self.size <= MAX_TAR, 'TAR exceeds bound')
        self.digest.update(data)
        return data

def read_tar(source):
    stream = HashedReader(source)
    names, links, trees, snapshots, total = set(), set(), {'npm': [], 'prettier': []}, {}, 0
    with tarfile.open(fileobj=stream, mode='r|') as tar:
        for member in tar:
            name = valid_name(member.name.rstrip('/'))
            need(name == 'probe' or name.startswith('probe/'), 'unexpected TAR root')
            need(name not in names and len(names) < MAX_ENTRIES and 0 <= member.size <= MAX_MEMBER,
                 'duplicate or oversized TAR member')
            names.add(name)
            total += member.size
            need(total <= MAX_TAR and (member.isfile() or member.isdir() or member.issym()), 'invalid TAR member')
            if member.issym():
                need(not name.startswith(NPM_PREFIX) and not name.startswith(PRETTIER_PREFIX), 'tool link refused')
                links.add(name)
                continue
            if not member.isfile():
                continue
            if name in SNAPSHOTS:
                need(member.size <= 16 * 1024**2, 'cache snapshot too large')
                data = tar.extractfile(member).read(member.size + 1)
                need(len(data) == member.size, 'cache snapshot truncated')
                snapshots[name] = data
            else:
                for label, prefix in (('npm', NPM_PREFIX), ('prettier', PRETTIER_PREFIX)):
                    if name.startswith(prefix):
                        relative = valid_name(name[len(prefix):])
                        trees[label].append((relative, hash_stream(tar.extractfile(member))))
    while stream.read(1024 * 1024):
        pass
    need(stream.digest.hexdigest() == TAR_SHA, 'TAR SHA differs')
    for name in names:
        parts = name.split('/')
        need(not any('/'.join(parts[:i]) in links for i in range(1, len(parts))), 'TAR link parent refused')
    need(set(snapshots) == set(SNAPSHOTS), 'cache snapshots missing')
    before, rows_before = cache_rows(snapshots[SNAPSHOTS[0]])
    after, rows_after = cache_rows(snapshots[SNAPSHOTS[1]])
    need(json.dumps(before, sort_keys=True, separators=(',', ':')) ==
         json.dumps(after, sort_keys=True, separators=(',', ':')), 'cache snapshots differ')
    need(rows_before == rows_after or dict(rows_before) == dict(rows_after), 'cache rows differ')
    trees['cache'] = rows_before
    return trees, {name: sha(snapshots[name]) for name in SNAPSHOTS}

def ordered(rows):
    names = [name for name, _ in rows]
    need(len(names) == len(set(names)), 'duplicate tree path')
    result = {}
    for order, key in (('lexical', lambda name: name), ('path', lambda name: PurePosixPath(name))):
        rows_ordered = sorted(rows, key=lambda row: key(row[0]))
        raw = ''.join(name + '\0' + digest + '\n' for name, digest in rows_ordered).encode('utf-8')
        result[order] = (rows_ordered, raw, sha(raw))
    return result

def diagnose(zip_path, run, artifact):
    metadata(run, artifact)
    need(zip_path.is_file() and not zip_path.is_symlink(), 'ZIP missing or linked')
    with zip_path.open('rb') as source:
        need(hash_stream(source) == ZIP_SHA, 'ZIP SHA differs')
    with zipfile.ZipFile(zip_path) as archive:
        matches = [i for i in archive.infolist() if i.filename == TAR_NAME]
        need(len(matches) == 1 and matches[0].file_size <= MAX_TAR, 'replay TAR ZIP entry differs')
        with archive.open(matches[0]) as source:
            trees, snapshots = read_tar(source)
    outputs, report = {}, {'version': 'som.react-tree-order-diagnostic.v1',
        'source': {'run': RUN, 'head': HEAD, 'artifact': ARTIFACT, 'zip_sha256': ZIP_SHA, 'tar_sha256': TAR_SHA},
        'cache_snapshot_sha256': snapshots,
        'limitation': 'Cache paths and digests come from retained snapshots; original cache bytes were not re-read. This is a diagnostic, not acceptance.',
        'trees': {}}
    for label in ('npm', 'cache', 'prettier'):
        rows = trees[label]
        result = ordered(rows)
        count, expected = EXPECTED[label]
        need(len(rows) == count and result['lexical'][2] == expected, label + ' frozen lexical tree differs')
        left, right = result['lexical'][0], result['path'][0]
        first = next(({'index_zero_based': i, 'lexical': a[0], 'path': b[0]}
                      for i, (a, b) in enumerate(zip(left, right)) if a != b), None)
        report['trees'][label] = {'files': len(rows), 'lexical_sha256': result['lexical'][2],
            'path_sha256': result['path'][2], 'same_order': first is None, 'first_difference': first,
            'source': 'cache snapshots, no raw bytes' if label == 'cache' else 'raw TAR files'}
        for order in ('lexical', 'path'):
            outputs[label + '-' + order + '.rows'] = result[order][1]
    return report, outputs

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--run-json', required=True, type=Path)
    parser.add_argument('--artifact-json', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    host()
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    for item in (args.zip, args.run_json, args.artifact_json):
        need(item.is_absolute() and item.parent.resolve(strict=True) == temp and item.is_file()
             and not item.is_symlink(), 'input must be a direct RUNNER_TEMP regular file')
    need(args.out.is_absolute() and args.out.parent.resolve(strict=True) == temp and not args.out.exists()
         and not args.out.is_symlink(), 'output must be a new direct RUNNER_TEMP directory')
    report, outputs = diagnose(args.zip, strict_json(args.run_json.read_bytes()), strict_json(args.artifact_json.read_bytes()))
    args.out.mkdir(mode=0o700)
    for name, raw in outputs.items():
        (args.out / name).write_bytes(raw)
    (args.out / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, sort_keys=True))

if __name__ == '__main__':
    main()
