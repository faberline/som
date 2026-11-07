#!/usr/bin/env python3
"""Split and reconstruct the pinned Node binary for bounded sealed transport.

Only a freshly materialized, private runner packet may be changed.  Chunk and
whole hashes are rechecked.  The historical input bytes are never published.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

NODE_SHA = '3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
NODE_BYTES = 148010504
CHUNK_BYTES = 64 * 1024 * 1024


def need(value, reason):
    if not value:
        raise ValueError(reason)


def physical(path, exists=True):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    if exists:
        need(path.exists(), 'missing path')
    return path


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def split(run, *, expected_sha=NODE_SHA, expected_bytes=NODE_BYTES, chunk_bytes=CHUNK_BYTES):
    run = physical(run)
    need(run.is_dir() and 0 < chunk_bytes <= CHUNK_BYTES and expected_bytes > 0, 'bad split scope')
    source = physical(run / 'roles/node_tools/bin/node')
    info = source.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o755
         and info.st_size == expected_bytes and file_hash(source) == expected_sha, 'node source differs')
    out = run / 'node-binary-chunks'; need(not out.exists(), 'chunk destination exists')
    out.mkdir(mode=0o700)
    rows = []; total = 0; digest = hashlib.sha256()
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        before = os.fstat(stream.fileno())
        while True:
            raw = stream.read(chunk_bytes)
            if not raw:
                break
            number = len(rows); name = f'{number:03d}.part'
            with (out / name).open('xb') as target:
                target.write(raw); target.flush(); os.fsync(target.fileno())
            total += len(raw); digest.update(raw)
            rows.append({'path': 'node-binary-chunks/' + name, 'size': len(raw),
                         'sha256': hashlib.sha256(raw).hexdigest()})
        after = os.fstat(stream.fileno())
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'node changed during split')
    need(total == expected_bytes and digest.hexdigest() == expected_sha and rows, 'split bytes differ')
    record = {'version': 'som.ts-node-chunk-transport.v1', 'source': 'roles/node_tools/bin/node',
              'size': total, 'sha256': expected_sha, 'mode': 0o755, 'chunk_bytes': chunk_bytes,
              'chunks': rows, 'product_acceptance': 'not_run'}
    target = run / 'node-chunk-transport.json'
    with target.open('x') as stream:
        json.dump(record, stream, sort_keys=True, indent=2); stream.write('\n')
    # Only remove the freshly created packet's exact source after preserving all
    # bytes and binding them to the immutable original inode and digest.
    final = source.stat()
    need((final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) ==
         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), 'node changed before removal')
    source.unlink()
    return record


def restore(run, *, expected_sha=NODE_SHA, expected_bytes=NODE_BYTES):
    run = physical(run)
    record_path = physical(run / 'node-chunk-transport.json')
    record = json.loads(record_path.read_bytes())
    need(record.get('version') == 'som.ts-node-chunk-transport.v1' and
         record.get('source') == 'roles/node_tools/bin/node' and record.get('sha256') == expected_sha and
         record.get('size') == expected_bytes and record.get('mode') == 0o755 and
         record.get('product_acceptance') == 'not_run', 'chunk authority differs')
    rows = record.get('chunks'); need(type(rows) is list and rows, 'chunks missing')
    chunk_dir = physical(run / 'node-binary-chunks')
    need({p.name for p in chunk_dir.iterdir()} == {f'{i:03d}.part' for i in range(len(rows))}, 'chunk universe differs')
    output = physical(run / 'roles/node_tools/bin/node', exists=False)
    need(not output.exists(), 'node output exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(); count = 0
    with output.open('xb') as target:
        for i, row in enumerate(rows):
            need(row.get('path') == f'node-binary-chunks/{i:03d}.part' and type(row.get('size')) is int and
                 0 < row['size'] <= CHUNK_BYTES, 'chunk metadata differs')
            part = physical(run / row['path']); info = part.stat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == row['size'], 'chunk file differs')
            h = hashlib.sha256()
            with os.fdopen(os.open(part, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    count += len(data); need(count <= expected_bytes, 'chunk size overflow')
                    digest.update(data); h.update(data); target.write(data)
            need(h.hexdigest() == row['sha256'], 'chunk digest differs')
        target.flush(); os.fsync(target.fileno())
    need(count == expected_bytes and digest.hexdigest() == expected_sha, 'reassembled node differs')
    output.chmod(0o755)
    return {'status': 'restored', 'sha256': expected_sha, 'size': expected_bytes,
            'chunks': len(rows), 'product_acceptance': 'not_run'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('split', 'restore'))
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'GitHub-hosted runner required')
    temp = physical(Path(os.environ['RUNNER_TEMP']))
    need(physical(args.run).is_relative_to(temp), 'runner scratch required')
    result = split(args.run) if args.action == 'split' else restore(args.run)
    print(json.dumps({'action': args.action, 'status': 'prepared' if args.action == 'split' else result['status'],
                      'size': result['size'], 'sha256': result['sha256'],
                      'chunks': len(result['chunks']) if args.action == 'split' else result['chunks'],
                      'product_acceptance': 'not_run'}, sort_keys=True))


if __name__ == '__main__':
    main()
