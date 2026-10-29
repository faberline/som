#!/usr/bin/env python3
"""Verify restored role bytes and normalize the TS cache to read-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

PROFILE_SHA = 'eb409609b02050dfad5f500275ef81e12b32cc5899ea89b0d1f192e0d82c6422'


def need(value, reason):
    if not value:
        raise ValueError(reason)


def physical(path):
    path = Path(path)
    need(path.is_absolute() and path.exists() and '..' not in path.parts and
         not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(profile, run):
    profile = physical(profile); run = physical(run)
    raw = profile.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == PROFILE_SHA, 'profile differs')
    doc = json.loads(raw)
    need(doc['status'] == 'incomplete' and doc['product_acceptance'] == 'not_run', 'draft profile required')
    roles = {k: v['files'] for k, v in doc['roles'].items()}
    roles['rs_prompts'] = doc['siblings']['rs_prompts']['files']
    for role, expected in roles.items():
        root = physical(run / ('siblings' if role == 'rs_prompts' else 'roles') / role)
        found = {}
        for folder, dirs, files in os.walk(root, followlinks=False):
            parent = Path(folder)
            for name in dirs + files:
                path = parent / name; info = path.lstat()
                need(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'restored special member refused')
                if stat.S_ISREG(info.st_mode):
                    need(info.st_nlink == 1, 'restored hardlink refused')
                    found[path.relative_to(root).as_posix()] = path
        need(set(found) == set(expected), 'restored role universe differs')
        for name, value in expected.items():
            path = found[name]; info = path.stat()
            need(info.st_size == value['size'] and digest(path) == value['sha256'], 'restored bytes differ')
            if role == 'npm_cache':
                need(stat.S_IMODE(info.st_mode) in (0o644, 0o444), 'sealed cache source mode differs')
                path.chmod(0o444)
            need(stat.S_IMODE(path.stat().st_mode) == value['mode'], 'restored mode differs')
        if role == 'npm_cache':
            logs = root / '_logs'; logs.mkdir(exist_ok=True)
            need(not any(logs.iterdir()), 'npm log directory not empty')
            for folder, dirs, _ in os.walk(root):
                Path(folder).chmod(0o555)
                for name in dirs:
                    (Path(folder) / name).chmod(0o555)
    return {'version': 'som.ts-role-transport-restoration-draft.v1', 'status': 'verified-restored-draft-bytes',
            'profile_sha256': PROFILE_SHA, 'role_counts': {k: len(v) for k, v in roles.items()},
            'npm_cache_readonly': True, 'npm_empty_logs': True, 'product_acceptance': 'not_run'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
         'GitHub-hosted runner required')
    temp = physical(Path(os.environ['RUNNER_TEMP']))
    need(physical(args.run).is_relative_to(temp), 'runner scratch required')
    print(json.dumps(verify(args.profile, args.run), sort_keys=True))


if __name__ == '__main__':
    main()
