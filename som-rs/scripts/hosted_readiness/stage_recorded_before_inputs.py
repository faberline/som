"""Restore exact historical before inputs as data in one new runner directory.

This does not admit a profile, start the oracle, or grant product acceptance.
Original ZIPs and the full original 18-input member set remain explicit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import traceback
import zipfile

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'stage_recorded_before_inputs.py'
GRAPH = BASE + 'same_candidate_graph.py'
TRANSPORT = BASE + 'sealed_current_raw.py'
PROFILE = BASE + 'inputs/recorded-before-profile-v1.json'
APPROVED_INDEX_SHA = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
MAX_FILE = 257 * 1024**2
MAX_TOTAL = 2 * 1024**3
ROLES = {'original', 'calibration', 'fresh'}


def need(value, message):
    if not value: raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for name, value in rows:
            need(name not in result, 'duplicate JSON key')
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def relative(name):
    need(type(name) is str and name and not name.startswith('/') and '\\' not in name
         and all(p not in {'', '.', '..'} for p in name.split('/'))
         and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'clean relative member required')
    return name


def physical(path, *, directory=False):
    path = Path(path).absolute()
    need('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    info = path.stat()
    need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
         'physical directory or single-link regular file required')
    return path


def identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read(path, maximum=MAX_FILE):
    path = physical(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(before.st_size <= maximum, 'file exceeds bound')
        raw = stream.read(maximum + 1)
        need(len(raw) == before.st_size and identity(os.fstat(stream.fileno())) == identity(before)
             and identity(path.lstat()) == identity(before), 'file changed while read')
    return raw


def write(path, raw, mode=0o600):
    path = Path(path)
    need(not path.exists() and not path.is_symlink(), 'fresh output file required')
    path.parent.mkdir(parents=True, exist_ok=True)
    need(not any(p.is_symlink() for p in path.parents), 'physical output parent required')
    with path.open('xb') as stream: stream.write(raw)
    path.chmod(mode)


def copy_exact(source, destination, expected_sha, expected_size, maximum=MAX_TOTAL):
    source = physical(source)
    need(type(expected_size) is int and 0 <= expected_size <= maximum
         and re.fullmatch('[0-9a-f]{64}', expected_sha), 'bounded exact file pin required')
    before = source.lstat(); digest = hashlib.sha256(); count = 0
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream, Path(destination).open('xb') as output:
        need(identity(os.fstat(stream.fileno())) == identity(before), 'source changed before copy')
        while raw := stream.read(1024**2):
            count += len(raw); need(count <= expected_size, 'source exceeded pinned size')
            digest.update(raw); output.write(raw)
        need(identity(os.fstat(stream.fileno())) == identity(before)
             and identity(source.lstat()) == identity(before), 'source changed during copy')
    need(count == expected_size and digest.hexdigest() == expected_sha, 'whole copied file differs')
    need(Path(destination).stat().st_size == expected_size, 'copied size differs')


def artifact_metadata(pin, current):
    need(type(pin) is dict and set(pin) == {'id', 'name', 'size_in_bytes', 'digest', 'run_id', 'head_sha', 'index_sha256'},
         'exact historical artifact profile required')
    origin = current.get('workflow_run', {})
    need(all(current.get(k) == pin[k] for k in ('id', 'name', 'size_in_bytes', 'digest'))
         and current.get('expired') is False and origin.get('id') == pin['run_id']
         and origin.get('head_sha') == pin['head_sha'] and origin.get('repository_id') == 1390374878,
         'historical artifact metadata differs')
    need(type(pin['size_in_bytes']) is int and 0 < pin['size_in_bytes'] <= MAX_TOTAL
         and re.fullmatch('sha256:[0-9a-f]{64}', pin['digest'])
         and re.fullmatch('[0-9a-f]{64}', pin['index_sha256']), 'historical artifact byte pin differs')


def extract_sealed(archive_path, root, pin):
    """Read only an already whole-hash-checked original ZIP. Never extract code."""
    root = Path(root); need(not root.exists(), 'fresh sealed directory required')
    with zipfile.ZipFile(physical(archive_path)) as archive:
        infos = archive.infolist(); names = [item.filename for item in infos]
        need(0 < len(names) <= 2001 and len(names) == len(set(names)), 'bounded unique ZIP members required')
        need('evidence-index.json' in names, 'original evidence index missing')
        for item in infos:
            relative(item.filename)
            need('/' not in item.filename and not item.is_dir() and not item.flag_bits & 1
                 and stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG)
                 and 0 <= item.file_size <= MAX_FILE, 'regular top-level encrypted ZIP member required')
        index_raw = archive.read('evidence-index.json')
        need(len(index_raw) <= 1024**2 and sha(index_raw) == pin['index_sha256'], 'original evidence index differs')
        index = parse(index_raw)
        need(index['version'] == 'som.m6-evidence-index.v1'
             and type(index['packages']) is list and 1 <= len(index['packages']) <= 1000,
             'original evidence index schema differs')
        expected = {'evidence-index.json'}; records = []
        for number, package in enumerate(index['packages']):
            prefix = f'package-{number:03d}'
            need(package['archive'] == prefix + '.sealed' and package['manifest'] == prefix + '.manifest.json',
                 'original package sequence differs')
            expected.update((package['archive'], package['manifest']))
            raw = archive.read(package['manifest'])
            need(sha(raw) == package['manifest_sha256'], 'original manifest differs')
            manifest = parse(raw)
            need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1'
                 and manifest['cipher_sha256'] == package['cipher_sha256']
                 and manifest['total_files'] == package['total_files'] == len(manifest['files']),
                 'original package identity differs')
            records.append((package, raw))
        need(set(names) == expected and sum(item.file_size for item in infos) <= MAX_TOTAL, 'complete encrypted ZIP universe differs')
        root.mkdir(mode=0o700)
        write(root / 'evidence-index.json', index_raw)
        for package, raw in records:
            write(root / package['manifest'], raw)
            with archive.open(package['archive']) as source, (root / package['archive']).open('xb') as target:
                digest = hashlib.sha256(); count = 0
                while chunk := source.read(1024**2):
                    count += len(chunk); need(count <= MAX_FILE, 'encrypted member exceeds bound')
                    digest.update(chunk); target.write(chunk)
            need(digest.hexdigest() == package['cipher_sha256'], 'original ciphertext differs')
    return {'index_sha256': sha(index_raw), 'encrypted_members': len(names)}


def stage_approved(metadata_root, members_root, out):
    """Copy every original 18-input member. Extra later-package roles stay outside."""
    metadata_root = physical(metadata_root, directory=True)
    members_root = physical(members_root, directory=True)
    raw = read(metadata_root / 'inputs-index.json', 1024**2)
    need(sha(raw) == APPROVED_INDEX_SHA, 'original approved 18-input index differs')
    index = parse(raw)
    need(index['version'] == 'som.m6-input-index.v1' and len(index['packages']) == 18, 'original 18-input scope required')
    out = Path(out); need(not out.exists(), 'new dedicated approved root required')
    out.mkdir(mode=0o700); write(out / 'inputs-index.json', raw)
    restored = out / 'restored'; restored.mkdir(mode=0o700)
    files = {}; total = 0
    for number, package in enumerate(index['packages']):
        prefix = f'package-{number:03d}'
        need(package['archive'] == prefix + '.sealed' and package['manifest'] == prefix + '.manifest.json',
             'original approved package order differs')
        manifest_raw = read(metadata_root / package['manifest'])
        need(sha(manifest_raw) == package['manifest_sha256'], 'original approved manifest differs')
        manifest = parse(manifest_raw)
        need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1'
             and manifest['total_files'] == len(manifest['files']), 'original approved file scope differs')
        write(out / package['manifest'], manifest_raw)
        for item in manifest['files']:
            name = relative(item['path'])
            need(name not in files and set(item) == {'path', 'sha256', 'bytes', 'mode'}
                 and item['mode'] in (0o644, 0o755) and type(item['bytes']) is int
                 and 0 <= item['bytes'] <= MAX_FILE, 'original approved member differs')
            source = physical(members_root / name); target = restored / name
            need(stat.S_IMODE(source.stat().st_mode) == item['mode'], 'original approved member mode differs')
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_exact(source, target, item['sha256'], item['bytes'], MAX_FILE); target.chmod(item['mode'])
            files[name] = dict(item); total += item['bytes']
            need(len(files) <= 20000 and total <= MAX_TOTAL, 'approved member universe exceeds bound')
    need(not any(a in files for name in files for a in (str(p) for p in Path(name).parents) if a != '.'),
         'approved file and directory conflict')
    return {'original_index_sha256': sha(raw), 'complete_original_files': files, 'bytes': total,
            'later_extra_input_roles_copied': False}


def stage_inputs(sources, graph, transport, profile_path, zips, approved_metadata, approved_members, node, out, evidence):
    profile_raw = sources.read(profile_path); profile = parse(profile_raw)
    need(profile_path == PROFILE and profile['version'] == 'som.linux-qa-recorded-before.v1'
         and profile['role'] == 'recorded-same-request-before'
         and type(profile['admission_complete']) is bool and set(profile['artifacts']) == ROLES,
         'explicit tracked recorded-before profile required')
    need(set(zips) == ROLES, 'three exact original ZIP roles required')
    out, evidence = Path(out), Path(evidence)
    need(not out.exists() and not evidence.exists(), 'new dedicated stage and evidence roots required')
    out.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    record = {'version':'som.recorded-before-input-stage.v1', 'profile_sha256':sha(profile_raw),
        'helper_revision':sources.revision, 'candidate_revision':sources.revision,
        'complete':False, 'profile_admission_changed':False, 'product_acceptance_passed':False}
    primary_error = None
    try:
        record['active_phase'] = 'qualify-transport-node'
        record['active_role'] = None
        node_record = transport.checked_node(sources, node); catalog = {}
        for role in sorted(ROLES):
            record['active_role'] = role
            record['active_phase'] = 'historical-artifact-metadata'
            pin = profile['artifacts'][role]
            metadata = graph.artifact_metadata('faberline/som', pin['id']); artifact_metadata(pin, metadata)
            directory = out / role; directory.mkdir(mode=0o700)
            record['active_phase'] = 'copy-original-whole-ZIP'
            copy_exact(zips[role], directory / 'artifact.zip', pin['digest'].removeprefix('sha256:'), pin['size_in_bytes'])
            record['active_phase'] = 'extract-original-encrypted-members'
            extract_sealed(directory / 'artifact.zip', directory / 'sealed', pin)
            command_root = evidence / role; command_root.mkdir(mode=0o700)
            record['active_phase'] = 'authenticate-and-restore-existing-M6-envelope'
            transport.run_m6(sources, node, node_record, transport.SCRIPTS[1],
                ['--root', directory / 'sealed', '--out', directory / 'restored', '--index-sha256', pin['index_sha256']],
                command_root)
            catalog[role] = metadata; sources.verify()
        record['active_role'] = 'approved-original18'
        record['active_phase'] = 'copy-complete-original18-member-set'
        approved = stage_approved(approved_metadata, approved_members, out / 'approved')
        write(out / 'catalog.json', canonical(catalog) + b'\n')
        record['active_phase'] = 'final-source-and-Node-postcheck'
        record['active_role'] = None
        need(sources.read(profile_path) == profile_raw and transport.checked_node(sources, node) == node_record,
             'source profile or qualified transport Node changed')
        sources.verify()
        record.update(complete=True, artifacts=profile['artifacts'],
            live_metadata_canonical_sha256=sha(canonical(catalog)), approved=approved,
            factory_qualification='unrun; reviewed profile admission and real prepare_data remain required')
    except BaseException as error:
        primary_error = error
        record['first_error'] = {'type': type(error).__name__, 'message': str(error),
            'phase': record.get('active_phase'), 'role': record.get('active_role'),
            'traceback': traceback.format_exc()}
        raise
    finally:
        try:
            write(evidence / 'stage-result.json', canonical(record) + b'\n')
        except BaseException as secondary:
            if primary_error is None: raise
            # An evidence write failure cannot replace the original refusal.
            # If the private filesystem is unavailable, keep it on the actual
            # original exception. No complete evidence or success is claimed.
            try:
                primary_error.__som_stage_evidence_error__ = {
                    'type': type(secondary).__name__, 'message': str(secondary)}
            except BaseException: pass
    return record


def main():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux staging only')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'zip-root', 'approved-metadata-root', 'approved-members-root', 'node', 'out', 'evidence-out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision'): parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    graph_raw = read(args.repo / GRAPH)
    import subprocess
    from types import ModuleType
    need(graph_raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(args.repo),
         'cat-file', 'blob', args.helper_revision + ':' + GRAPH]), 'exact Git graph bootstrap required')
    graph = ModuleType('_som_before_stage_source_graph'); graph.__file__ = str(args.repo / GRAPH)
    exec(compile(graph_raw, graph.__file__, 'exec', dont_inherit=True), graph.__dict__)
    sources, _, _ = graph.admit_source(args.repo, args.helper_revision, args.candidate_revision)
    sources.read(SELF)
    transport = graph.raw_module(sources, '_som_before_stage_transport', TRANSPORT)
    temporary = physical(os.environ['RUNNER_TEMP'], directory=True)
    inputs = [physical(p, directory=True) for p in (args.zip_root, args.approved_metadata_root, args.approved_members_root)]
    outputs = [args.out.absolute(), args.evidence_out.absolute()]
    need(all(temporary in p.parents and '..' not in p.parts for p in [*inputs, *outputs])
         and all(not p.exists() and physical(p.parent, directory=True) for p in outputs)
         and all(a != b and a not in b.parents and b not in a.parents for a in outputs for b in [*inputs, *outputs] if a is not b),
         'separate new runner output roots required')
    zips = {name: args.zip_root / (name + '.zip') for name in ROLES}
    need({p.name for p in args.zip_root.iterdir()} == {name + '.zip' for name in ROLES}, 'exact ZIP input universe required')
    stage_inputs(sources, graph, transport, PROFILE, zips, args.approved_metadata_root, args.approved_members_root,
                 args.node, outputs[0], outputs[1])
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        sys.stderr.write('recorded-before input staging refused; retain stage evidence\n')
        raise SystemExit(1)
