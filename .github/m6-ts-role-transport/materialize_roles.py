#!/usr/bin/env python3
"""Materialize draft TS input roles from source-bound runner scratch.

This verifies the complete file universe and never grants acceptance.  The
caller must first authenticate the 19-package restore and the public Node
archive against their pinned authorities.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from role_inventory import inventory

PROFILE_SHA = '1f9cb5d59f3d6c742161fa72c5cc29d15abe0860f82e71f6342b1b2780ea024f'
PREFIXES = {
    'public_run': '.local/rust-compiler-goal/acceptance/public-products-003b',
    'legacy_run': '.local/rust-compiler-goal/acceptance/legacy544',
    'rust_run': '.local/rust-compiler-goal/acceptance/rust-output-055/run-003',
    'npm_cache': '.local/typescript-output/npm-cache-001',
    'rs_prompts': '.local/rust-output-phase2/rs-products-001',
}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def physical(path, exists=True):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    if exists:
        need(path.exists(), 'source missing')
    return path


def regular_tree(root):
    root = physical(root)
    need(root.is_dir(), 'role root not a directory')
    found = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        folder = Path(directory)
        for name in dirs + files:
            member = folder / name
            info = member.lstat()
            need(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'role link or special file refused')
            if stat.S_ISREG(info.st_mode):
                need(info.st_nlink == 1, 'hardlink refused')
                found[member.relative_to(root).as_posix()] = member
    return found


def copy_verified(source, destination, identity, source_mode=None):
    source = physical(source)
    info = source.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'regular source required')
    if source_mode is not None:
        need(stat.S_IMODE(info.st_mode) == source_mode, 'source mode differs')
    target = destination
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(); count = 0
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as src, target.open('xb') as dst:
        before = os.fstat(src.fileno())
        while chunk := src.read(1024 * 1024):
            count += len(chunk)
            need(count <= identity['size'], 'source length exceeds profile')
            digest.update(chunk); dst.write(chunk)
        after = os.fstat(src.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'source changed during copy')
    need(count == identity['size'] and digest.hexdigest() == identity['sha256'], 'source bytes differ')
    target.chmod(identity['mode'])


def materialize(profile, approved, node_root, out):
    raw = physical(profile).read_bytes()
    need(hashlib.sha256(raw).hexdigest() == PROFILE_SHA, 'draft profile identity differs')
    doc = json.loads(raw)
    need(doc['status'] == 'incomplete' and doc['product_acceptance'] == 'not_run', 'draft profile required')
    approved = physical(approved); node_root = physical(node_root); out = physical(out, exists=False)
    need(not out.exists(), 'fresh output required')
    maps = {name: row['files'] for name, row in doc['roles'].items()}
    maps['rs_prompts'] = doc['siblings']['rs_prompts']['files']
    need(set(maps) == set(PREFIXES) | {'node_tools'}, 'role universe differs')
    out.mkdir(mode=0o700)
    for role, expected in maps.items():
        source_root = node_root if role == 'node_tools' else approved / PREFIXES[role]
        actual = inventory(source_root)
        need(set(actual) == set(expected), 'complete source role universe differs')
        if role == 'npm_cache':
            actual = {k: dict(v, mode=0o444) for k, v in actual.items()}
        need(actual == expected, 'source role identities differ')
        destination_root = out / ('siblings' if role == 'rs_prompts' else 'roles') / role
        destination_root.mkdir(parents=True, mode=0o700)
        for name, info in sorted(expected.items()):
            need(not Path(name).is_absolute() and '..' not in Path(name).parts, 'invalid role member')
            if info.get('type') == 'internal-alias':
                continue
            copy_verified(source_root / name, destination_root / name, info,
                          source_mode=0o644 if role != 'node_tools' else info['mode'])
        for name, info in sorted(expected.items()):
            if info.get('type') == 'internal-alias':
                need(role == 'node_tools', 'alias outside native tools refused')
                target = physical(destination_root / name, exists=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(info['target'], target)
        if role == 'npm_cache':
            logs = destination_root / '_logs'
            logs.mkdir(exist_ok=True)
            need(not any(logs.iterdir()), 'npm logs directory must be empty')
            for folder, dirs, _ in os.walk(destination_root):
                Path(folder).chmod(0o555)
                for name in dirs:
                    (Path(folder) / name).chmod(0o555)
        need(inventory(destination_root) == expected, 'copied role identity differs')
    receipt = {'version': 'som.ts-role-materialization-draft.v1', 'status': 'verified-draft-input-bytes',
               'profile_sha256': PROFILE_SHA, 'role_counts': {k: len(v) for k, v in maps.items()},
               'npm_cache_readonly': True, 'npm_empty_logs': True,
               'current_run_artifact': 'unrun', 'product_acceptance': 'not_run'}
    (out / 'materialization-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main():
    p = argparse.ArgumentParser()
    for name in ('profile', 'approved', 'node-root', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(materialize(args.profile, args.approved, args.node_root, args.out), sort_keys=True))


if __name__ == '__main__':
    main()
