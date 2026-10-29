#!/usr/bin/env python3
"""Copy rehearsal proof for sealing; store the two QA pointers as JSON data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat

SCENARIOS = {'switch', 'after_stop', 'after_start', 'after_pointer', 'after_commit'}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def stage(root, out):
    need(root.is_absolute() and out.is_absolute() and root.is_dir(), 'use absolute evidence directories')
    need(not out.exists() and not out.is_symlink() and not out.is_relative_to(root), 'preserve existing evidence')
    for parent in (root, *root.parents):
        need(not parent.is_symlink(), 'evidence root has a symlink')
    out.mkdir(parents=True, mode=0o700)
    files, links = 0, []
    for source in sorted(root.rglob('*')):
        relative = source.relative_to(root)
        destination = out / relative
        info = source.lstat()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(info.st_mode):
            parts = relative.parts
            admitted = (len(parts) == 7 and parts[:2] == ('run', 'rehearsal')
                        and parts[2] in SCENARIOS and parts[3:5] == ('root', 'som-rs')
                        and parts[5] in ('runtime', 'compiler-assets') and parts[6] == 'current')
            need(admitted, f'unexpected evidence symlink: {relative}')
            record = {'version': 'som.m6-rehearsal-pointer.v1', 'path': relative.as_posix(),
                      'kind': 'symlink', 'target': os.readlink(source)}
            sidecar = destination.with_name(destination.name + '.symlink.json')
            with sidecar.open('x') as stream:
                stream.write(json.dumps(record, indent=2) + '\n')
            links.append(relative.as_posix())
        elif stat.S_ISDIR(info.st_mode):
            destination.mkdir(exist_ok=True)
        else:
            need(stat.S_ISREG(info.st_mode) and info.st_size <= 128 * 1024 * 1024,
                 'special or oversized evidence file')
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as incoming, destination.open('xb') as outgoing:
                opened = os.fstat(incoming.fileno())
                need((opened.st_dev, opened.st_ino, opened.st_size) == (info.st_dev, info.st_ino, info.st_size),
                     'evidence file changed before copy')
                shutil.copyfileobj(incoming, outgoing)
                after = os.fstat(incoming.fileno())
                need((after.st_size, after.st_mtime_ns, after.st_ctime_ns) == (info.st_size, info.st_mtime_ns, info.st_ctime_ns),
                     'evidence file changed during copy')
            destination.chmod(0o755 if info.st_mode & 0o111 else 0o644)
            files += 1
    return {'version': 'som.m6-release-staging.v1', 'regular_files': files,
            'pointer_sidecars': links, 'source_changed': False, 'product_acceptance': 'read-summary'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.root.absolute(), args.out.absolute())))


if __name__ == '__main__':
    main()
