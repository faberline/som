#!/usr/bin/env python3
"""DRAFT React003 metadata calculator. GitHub-hosted runner only.

Outputs candidate metadata, not a receipt, review or acceptance decision.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

PREVIOUS = 'som-rs/evidence/react-output-contract-amendment-002.json'
PREVIOUS_SHA = 'eb4e120c2a573dc06cf068841ec7b0b0f0e2349d74ae2bff5df7dcade41fe962'
PREVIOUS_TARGET = 'd48cbd2a790554bab14a38bd6092e5975e82027c0a6da1ef46d8ff7affdbf638'
PREVIOUS_COUNT = 54
PROFILE_SHA = '5e2e4852b32e8bdc5b60ad60e50fb8e6a235690334848c13fb29ca55cb0d6c00'
ROOTS = ('som-rs/contracts/react-output-v1', 'som-rs/tests/react-output-v1', 'som-rs/evidence/react-output-v1')
ARCHIVE_ROOT = 'som-rs/evidence/react-output-contract-archive'
PROFILE = 'som-rs/contracts/react-output-v1/som-react-linux-qa-profile.json'
HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_FILE = 32 * 1024 * 1024

class Refusal(ValueError):
    pass

def need(condition, message):
    if not condition:
        raise Refusal(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    def nonfinite(_):
        raise Refusal('nonfinite JSON value')
    try:
        return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Refusal('invalid JSON') from exc

def relative(value):
    need(isinstance(value, str) and value and not any(c in value for c in ('\\', '\0', '\n', '\r')), 'invalid path')
    path = PurePosixPath(value)
    need(not path.is_absolute() and str(path) == value and all(part not in ('', '.', '..') for part in path.parts), 'unsafe path')
    return path

def physical(root, rel):
    path = root
    for part in relative(rel).parts:
        path = path / part
        need(not path.is_symlink(), 'linked path refused')
    need(path.is_relative_to(root), 'path escapes repository')
    return path

def read_file(path, limit=MAX_FILE):
    need(not path.is_symlink(), 'linked input refused')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Refusal('input missing or unsafe') from exc
    try:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_size <= limit, 'input nonregular or oversized')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) <= limit, 'input oversized')
        return raw
    finally:
        os.close(fd)

def map_paths(repo):
    files, contents = {}, {}
    def onerror(_):
        raise Refusal('contract tree scan failed')
    for prefix in ROOTS:
        root = physical(repo, prefix)
        need(root.is_dir(), 'contract root missing')
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            dirs[:] = [name for name in dirs if name != '__pycache__']
            for name in sorted(dirs + names):
                path = Path(directory) / name
                rel = path.relative_to(repo).as_posix()
                physical(repo, rel)
                mode = path.lstat().st_mode
                need(not stat.S_ISLNK(mode), 'contract symlink refused')
                if stat.S_ISDIR(mode):
                    continue
                need(stat.S_ISREG(mode), 'contract special file refused')
                if '__pycache__' in path.parts:
                    continue
                raw = read_file(path)
                files[rel], contents[rel] = sha(raw), raw
    return files, contents

def validate_profile(raw):
    need(sha(raw) == PROFILE_SHA, 'draft profile bytes differ')
    doc = strict_json(raw)
    need(isinstance(doc, dict) and set(doc) == {'version', 'host', 'source', 'node', 'npm', 'cache', 'prettier', 'reference_formatter_sha256'}, 'profile fields differ')
    need(doc.get('version') == 'som.react-linux-qa-profile.v1', 'profile version differs')
    expected = {
        'source': {'commit', 'run', 'artifact', 'artifact_zip_sha256', 'tar_sha256'},
        'node': {'path', 'sha256'}, 'npm': {'root', 'sha256', 'files', 'symlinks'},
        'cache': {'path', 'sha256', 'files', 'symlinks'},
        'prettier': {'sha256', 'files', 'symlinks'},
    }
    for key, fields in expected.items():
        need(isinstance(doc.get(key), dict) and set(doc[key]) == fields, 'profile nested fields differ')
    return doc

def calculate(repo, plan):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
         'GitHub hosted runner required')
    repo = Path(repo)
    need(repo.is_absolute() and repo.is_dir() and repo.resolve(strict=True) == repo and not any(p.is_symlink() for p in (repo, *repo.parents)), 'repository is not physical')
    need(isinstance(plan, dict) and set(plan) == {'changed_paths', 'added_paths', 'archives', 'new_sha256'}, 'plan fields differ')
    changed_expected, added_expected, archives = plan['changed_paths'], plan['added_paths'], plan['archives']
    for values in (changed_expected, added_expected):
        need(isinstance(values, list) and all(isinstance(x, str) for x in values), 'plan paths invalid')
        need(values == sorted(set(values)), 'plan paths must be sorted and unique')
        for name in values:
            relative(name)
    need(not set(changed_expected) & set(added_expected), 'plan changes overlap')
    need(isinstance(archives, dict) and set(archives) == set(changed_expected), 'archive map differs from changes')
    new_pins = plan['new_sha256']
    need(isinstance(new_pins, dict) and set(new_pins) == set(changed_expected + added_expected)
         and all(isinstance(value, str) and HEX.fullmatch(value) for value in new_pins.values()), 'new source pins differ from plan')
    raw = read_file(physical(repo, PREVIOUS))
    need(sha(raw) == PREVIOUS_SHA, 'previous receipt SHA differs')
    prior = strict_json(raw)
    need(isinstance(prior, dict) and prior.get('amendment') == 2
         and prior.get('status') == 'amendment_sealed_after_independent_review', 'previous receipt identity differs')
    old = prior.get('files')
    target = prior.get('target_contract')
    need(isinstance(old, dict) and len(old) == PREVIOUS_COUNT and old, 'previous files map count differs')
    need(all(isinstance(v, str) and HEX.fullmatch(v) for v in old.values()), 'previous files map digest invalid')
    for path in old:
        relative(path)
        need(any(path.startswith(root + '/') for root in ROOTS), 'previous path outside contract roots')
    need(isinstance(target, dict) and target.get('id') == 'som.react-output-contract.v1'
         and target.get('sha256') == PREVIOUS_TARGET == sha(canonical(old)), 'previous target differs')
    files, contents = map_paths(repo)
    need(set(old) <= set(files), 'previous contract file removed')
    changed = sorted(k for k in files if k in old and files[k] != old[k])
    added = sorted(k for k in files if k not in old)
    need(changed == changed_expected and added == added_expected, 'actual changes differ from plan')
    need(all(files[path] == new_pins[path] for path in changed + added), 'new source bytes differ from plan')
    need(PROFILE in files, 'draft QA profile missing')
    validate_profile(contents[PROFILE])
    records = []
    for path in changed:
        archive = archives[path]
        relative(archive)
        need(archive.startswith(ARCHIVE_ROOT + '/'), 'archive outside designated directory')
        archive_raw = read_file(physical(repo, archive))
        need(sha(archive_raw) == old[path], 'archived old bytes differ')
        records.append({'path': path, 'old_sha256': old[path], 'new_sha256': files[path],
                        'archived_copy': archive, 'archived_copy_sha256': old[path]})
    new_target = sha(canonical(files))
    # The profile has an exact field whitelist. Also reject embedding the newly
    # calculated digest in any map input. The containing receipt is external.
    for path, raw_content in contents.items():
        need(new_target.encode() not in raw_content.lower(), 'target digest embedded in mapped input')
    for path in changed + added:
        need(b'react-output-contract-amendment-003.json' not in contents[path],
             'new receipt referenced by changed or added mapped input')
    need(not any(PREVIOUS == root or PREVIOUS.startswith(root + '/') for root in ROOTS), 'receipt is inside map roots')
    return {'version': 'som.react003-candidate-metadata.draft.v1', 'status': 'DRAFT',
            'review': 'PENDING', 'previous_receipt': PREVIOUS, 'previous_receipt_sha256': PREVIOUS_SHA,
            'target_contract': {'id': 'som.react-output-contract.v1', 'previous_sha256': PREVIOUS_TARGET,
                                'sha256': new_target},
            'changed_paths': changed, 'added_paths': added, 'changes': records, 'files': files,
            'limitations': ['candidate metadata only; no seal, review or acceptance',
                            'acyclic dependency of all source semantics requires independent review']}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args(argv)
    try:
        need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', 'GitHub hosted runner required')
        temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        for path in (args.plan, args.out):
            need(path.is_absolute() and '..' not in path.parts and path.parent.resolve(strict=True) == path.parent
                 and path.is_relative_to(temp) and not path.is_symlink(), 'plan/output must be physical runner-temp paths')
        need(args.plan != args.out and not args.out.exists(), 'output already exists or overlaps plan')
        need(not args.out.is_relative_to(args.repo), 'output must be outside repository')
        plan = strict_json(read_file(args.plan, 1024 * 1024))
        candidate = calculate(args.repo, plan)
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(candidate, stream, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write('\n')
        print(json.dumps({'status': 'DRAFT', 'target_sha256': candidate['target_contract']['sha256'], 'output': str(args.out)}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print('refused: ' + str(exc), file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
