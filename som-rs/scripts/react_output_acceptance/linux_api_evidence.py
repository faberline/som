"""Unsealed run evidence for Linux React API QA; never accepts a product."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat


VERSION = 'som.react-linux-api-retained-evidence.v1'
KEY = '4669d1f312255b4d628224faad1326423741eac16521c3d59677eb994cd2f90b'


class EvidenceError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise EvidenceError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value, replace=False):
    with path.open('w' if replace else 'x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def snapshot(root, runtime_python=None):
    """Snapshot physical entries without walking or copying symlink targets."""
    root = Path(root)
    need(root.is_absolute() and root.resolve() == root and root.is_dir() and not root.is_symlink(), 'tree root differs')
    entries = {}
    def failed(_exc):
        raise EvidenceError('tree unreadable')
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False, onerror=failed):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            need(not any(c in rel for c in ('\x00', '\n', '\r', '\\')), 'tree name invalid')
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                need(target and not any(c in target for c in ('\x00', '\n', '\r', '\\')), 'tree link invalid')
                resolved = path.resolve(strict=True)
                inside = resolved == root or resolved.is_relative_to(root)
                python_link = runtime_python is not None and rel in ('bin/python', 'bin/python3', 'bin/python3.12') \
                              and resolved == runtime_python
                need(inside or python_link, 'tree link escapes allowed roots')
                entries[rel] = {'type': 'symlink', 'mode': mode, 'target': target}
            elif stat.S_ISREG(info.st_mode):
                entries[rel] = {'type': 'file', 'mode': mode, 'size': info.st_size, 'sha256': file_sha(path)}
            elif stat.S_ISDIR(info.st_mode):
                entries[rel] = {'type': 'directory', 'mode': mode}
            else:
                raise EvidenceError('tree special entry refused')
    return {'version': 'som.react-linux-api-tree-snapshot.v1', 'root_mode': stat.S_IMODE(root.stat().st_mode),
            'entries': entries}


def retain_file(source, destination, root):
    source, destination = Path(source), Path(destination)
    need(source.is_absolute() and not any(p.is_symlink() for p in (source, *source.parents))
         and source.is_file(), 'retained input missing or linked')
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open('rb') as inp, destination.open('xb') as out:
        for block in iter(lambda: inp.read(1024 * 1024), b''):
            digest.update(block)
            out.write(block)
    value = digest.hexdigest()
    need(file_sha(source) == value and file_sha(destination) == value, 'retained input changed')
    return {'path': destination.relative_to(root).as_posix(), 'sha256': value, 'size': destination.stat().st_size}


def begin(root, context, api_module):
    root = Path(root)
    need(root.is_dir() and not root.is_symlink(), 'API evidence root differs')
    repo, profile_path = context['_repo'], context['_profile_path']
    auth, authority_sha = api_module.authority(repo)
    need(auth['source_sha256'] == context['_source_shas'] and authority_sha == context['authority_sha256'], 'source authority differs')
    profile = api_module.parse(api_module.read(profile_path, 4096))
    runtime, closure_root = Path(profile['runtime_root']), Path(profile['closure_root'])
    named, closure = closure_root / 'environments/react-api-qa', closure_root / 'closures' / KEY
    state = {'version': VERSION, 'status': 'incomplete', 'profile': {}, 'authority': {}, 'sources': {},
             'inputs': {}, 'inventories': {}, 'problems': [],
             'bytes_scope': 'runtime and venv contents are represented by observed inventories; rederive from pinned artifacts and wheels'}
    write_json(root / 'retained.json', state)
    try:
        for kind, source in (('profile', profile_path), ('authority', repo / api_module.AUTHORITY)):
            state[kind]['before'] = retain_file(source, root / (kind + '-before.json'), root)
        for rel, expected in sorted(auth['source_sha256'].items()):
            record = retain_file(repo / rel, root / 'source-before' / rel, root)
            need(record['sha256'] == expected, 'source differs from authority')
            state['sources'][rel] = {'before': record}
        inputs = {'runtime-receipt.json': runtime / 'receipt.json',
                  'runtime-artifact-origin.json': runtime / 'artifact-origin.json',
                  'runtime-qualification-receipt.json': runtime / 'qualification-evidence/receipt.json',
                  'runtime-installed-files.json': runtime / 'qualification-evidence/installed-files.json',
                  'runtime-source-pins.json': runtime / 'qualification-evidence/source-pins.json',
                  'runtime-build-intermediates.json': runtime / 'qualification-evidence/build-intermediates.json',
                  'closure-receipt.json': closure / 'receipt.json',
                  'live-provision-receipt.json': named / 'live-provision-receipt.json',
                  'live-installed-files.json': named / 'live-installed-files.json',
                  'live-installed-metadata.json': named / 'live-installed-metadata.json',
                  'selected.lock': closure / 'requirements.selected.lock',
                  'offline.lock': closure / 'requirements.offline.lock',
                  'pyproject.toml': named / 'pyproject.toml', 'uv.lock': named / 'uv.lock',
                  'requirements.in': named / 'requirements.in',
                  'probe-installed-metadata.py': closure_root / 'probe-installed-metadata.py'}
        source_input = runtime.parent / 'react39-inputs'
        for name in ('source-run.json', 'source-artifact.json', 'audit-run.json', 'audit-artifact.json'):
            inputs['provenance/' + name] = source_input / name
        inputs['provenance/react-python-independent-audit.json'] = source_input / 'audit/react-python-independent-audit.json'
        closure_doc = api_module.parse(api_module.read(closure / 'receipt.json', 32 * 1024 * 1024))
        wheels = closure_doc.get('wheels') if isinstance(closure_doc, dict) else None
        need(isinstance(wheels, list) and len(wheels) == 40 and all(isinstance(row, dict) for row in wheels),
             'wheel evidence inventory differs')
        seen = set()
        for row in wheels:
            name = row.get('name')
            need(isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]*', name) is not None
                 and name not in seen and isinstance(row.get('audit_sha256'), str), 'wheel evidence name differs')
            seen.add(name)
            path = closure / 'wheel-audits' / name / 'inventory.json'
            need(file_sha(path) == row['audit_sha256'], 'wheel evidence hash differs')
            inputs['wheel-audits/' + name + '/inventory.json'] = path
        for label in ('fresh-offline-frozen-sync', 'fresh-offline-no-site-metadata'):
            for stream in ('stdout', 'stderr'):
                inputs['logs/' + label + '.' + stream] = closure_root / 'logs' / (label + '.' + stream)
        for name, source in inputs.items():
            state['inputs'][name] = retain_file(source, root / 'inputs' / name, root)
        for name, source, python in (('runtime', runtime / 'install', None),
                                     ('venv', Path(profile['venv']), runtime / 'install/python/bin/python3.12')):
            value = snapshot(source, python)
            dest = root / (name + '-before.json')
            write_json(dest, value)
            state['inventories'][name] = {'before': {'path': dest.relative_to(root).as_posix(), 'sha256': file_sha(dest)}}
        write_json(root / 'retained.json', state, replace=True)
        return state, {'profile': profile, 'source_sha256': dict(auth['source_sha256']), 'inputs': inputs}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state['status'] = 'failed'
        state['problems'].append(type(exc).__name__)
        write_json(root / 'retained.json', state, replace=True)
        raise


def finish(root, context, api_module, state, runtime):
    root, repo = Path(root), context['_repo']
    try:
        auth, authority_sha = api_module.authority(repo)
        need(auth['source_sha256'] == runtime['source_sha256'] and authority_sha == context['authority_sha256'], 'source authority changed')
        for kind, source in (('profile', context['_profile_path']), ('authority', repo / api_module.AUTHORITY)):
            after = retain_file(source, root / (kind + '-after.json'), root)
            state[kind]['after'] = after
            need(after['sha256'] == state[kind]['before']['sha256'], 'profile or authority changed')
        for rel, source in state['sources'].items():
            after = retain_file(repo / rel, root / 'source-after' / rel, root)
            source['after'] = after
            need(after['sha256'] == source['before']['sha256'], 'source changed')
        for name, record in state['inputs'].items():
            need(file_sha(runtime['inputs'][name]) == record['sha256'] == file_sha(root / record['path']), 'retained input changed')
        profile = runtime['profile']
        source_runtime = Path(profile['runtime_root'])
        for name, source, python in (('runtime', source_runtime / 'install', None),
                                     ('venv', Path(profile['venv']), source_runtime / 'install/python/bin/python3.12')):
            after = snapshot(source, python)
            dest = root / (name + '-after.json')
            write_json(dest, after)
            record = {'path': dest.relative_to(root).as_posix(), 'sha256': file_sha(dest)}
            state['inventories'][name]['after'] = record
            before_path = root / state['inventories'][name]['before']['path']
            need(file_sha(before_path) == state['inventories'][name]['before']['sha256']
                 and json.loads(before_path.read_text()) == after, 'observed tree changed')
        for section in ('profile', 'authority'):
            for record in state[section].values():
                need(file_sha(root / record['path']) == record['sha256'], 'retained bytes changed')
        for records in state['sources'].values():
            for record in records.values():
                need(file_sha(root / record['path']) == record['sha256'], 'retained source changed')
        state['status'] = 'passed'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state['status'] = 'failed'
        state['problems'].append(type(exc).__name__)
    write_json(root / 'retained.json', state, replace=True)
    return state['status'] == 'passed'
