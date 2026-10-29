#!/usr/bin/env python3
"""Reversibly carry the complete public Node/npm role through the sealer.

The sealer intentionally skips node_modules.  A bounded, validated inner ZIP
keeps that policy unchanged while preserving the role's exact original paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

PROFILE_SHA = 'eb409609b02050dfad5f500275ef81e12b32cc5899ea89b0d1f192e0d82c6422'
MAX_ZIP = 128 * 1024 * 1024


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def physical(path, exists=True):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and
         not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    if exists:
        need(path.exists(), 'path missing')
    return path


def valid_name(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and all(part not in ('', '.', '..') for part in name.split('/'))


def entries(root):
    found = {}
    for folder, dirs, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        for name in dirs + files:
            path = parent / name; st = path.lstat()
            need(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), 'link or special file refused')
            if stat.S_ISREG(st.st_mode):
                need(st.st_nlink == 1, 'hardlink refused')
                relative = path.relative_to(root).as_posix()
                need(valid_name(relative) and relative not in found, 'unsafe or duplicate member')
                found[relative] = path
    return found


def node_profile(profile):
    raw = physical(profile).read_bytes()
    need(sha(raw) == PROFILE_SHA, 'profile differs')
    doc = json.loads(raw)
    need(doc['status'] == 'incomplete' and doc['product_acceptance'] == 'not_run', 'draft profile required')
    items = dict(doc['roles']['node_tools']['files'])
    need(items.pop('bin/node')['sha256'] == '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538',
         'node profile differs')
    return items


def pack(profile, run):
    run = physical(run); root = physical(run / 'roles/node_tools')
    expected = node_profile(profile); found = entries(root)
    need(set(expected) == set(found), 'complete node source universe differs')
    archive_path = run / 'node-tools-files.zip'; receipt_path = run / 'node-tools-transport.json'
    backup = run.parent.parent / 'node-tools-source-backup'
    need(all(not p.exists() and not p.is_symlink() for p in (archive_path, receipt_path, backup)), 'transport output exists')
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=False) as archive:
        for name, identity in sorted(expected.items()):
            source = physical(found[name]); st = source.stat()
            need(st.st_size == identity['size'] and stat.S_IMODE(st.st_mode) == identity['mode'], 'source metadata differs')
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3; info.external_attr = (stat.S_IFREG | identity['mode']) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            h = hashlib.sha256(); count = 0
            with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream, archive.open(info, 'w') as target:
                before = os.fstat(stream.fileno())
                while chunk := stream.read(1024 * 1024):
                    count += len(chunk); need(count <= identity['size'], 'source size overflow')
                    h.update(chunk); target.write(chunk)
                after = os.fstat(stream.fileno())
            need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                 (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and
                 count == identity['size'] and h.hexdigest() == identity['sha256'], 'source bytes differ')
    size = archive_path.stat().st_size
    need(size <= MAX_ZIP, 'inner archive exceeds sealer cap')
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    record = {'version': 'som.ts-node-role-transport.v1', 'profile_sha256': PROFILE_SHA,
              'archive_sha256': digest, 'archive_bytes': size, 'files': len(expected),
              'node_binary': 'separate-ordered-chunks', 'product_acceptance': 'not_run'}
    with receipt_path.open('x') as stream:
        json.dump(record, stream, sort_keys=True, indent=2); stream.write('\n')
    # Move only this verified generated role outside the sealer root; keep it
    # for recovery until the hosted runner ends. The ZIP is the transport copy.
    os.rename(root, backup)
    return record


def restore(profile, run):
    run = physical(run); expected = node_profile(profile)
    archive_path = physical(run / 'node-tools-files.zip'); record_path = physical(run / 'node-tools-transport.json')
    record = json.loads(record_path.read_bytes()); raw = archive_path.read_bytes()
    need(record.get('version') == 'som.ts-node-role-transport.v1' and record.get('profile_sha256') == PROFILE_SHA and
         record.get('archive_sha256') == sha(raw) and record.get('archive_bytes') == len(raw) <= MAX_ZIP and
         record.get('files') == len(expected) and record.get('node_binary') == 'separate-ordered-chunks' and
         record.get('product_acceptance') == 'not_run', 'transport receipt differs')
    root = physical(run / 'roles/node_tools', exists=False)
    need(not root.exists(), 'node role destination exists'); root.mkdir(parents=True, mode=0o700)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist(); names = [m.filename for m in members]
        need(len(names) == len(set(names)) and set(names) == set(expected), 'archive universe differs')
        for member in members:
            name = member.filename; identity = expected[name]
            need(valid_name(name) and not member.is_dir() and member.file_size == identity['size'] and
                 stat.S_IFMT(member.external_attr >> 16) == stat.S_IFREG and
                 stat.S_IMODE(member.external_attr >> 16) == identity['mode'], 'archive metadata differs')
            target = physical(root / name, exists=False); target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256(); count = 0
            with archive.open(member) as source, target.open('xb') as dst:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk); need(count <= identity['size'], 'archive member overflow')
                    h.update(chunk); dst.write(chunk)
            need(count == identity['size'] and h.hexdigest() == identity['sha256'], 'archive member bytes differ')
            target.chmod(identity['mode'])
    return {'status': 'restored', 'files': len(expected), 'archive_sha256': sha(raw),
            'product_acceptance': 'not_run'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('pack', 'restore'))
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
         'GitHub-hosted runner required')
    temp = physical(Path(os.environ['RUNNER_TEMP']))
    run = physical(args.run)
    need(run.is_relative_to(temp), 'runner scratch required')
    if args.action == 'pack':
        need(run == temp / 'packet' / 'run', 'exact packet scratch path required')
    result = pack(args.profile, args.run) if args.action == 'pack' else restore(args.profile, args.run)
    print(json.dumps({'action': args.action, 'status': 'prepared' if args.action == 'pack' else result['status'],
                      'files': result['files'], 'archive_sha256': result['archive_sha256'],
                      'product_acceptance': 'not_run'}, sort_keys=True))


if __name__ == '__main__':
    main()
