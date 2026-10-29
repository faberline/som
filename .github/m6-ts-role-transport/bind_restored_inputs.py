#!/usr/bin/env python3
"""Bind verified draft roots to a same-run encrypted GitHub artifact."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import verify_restored_roles as verifier

NAME = 'same-candidate-typescript-regression-inputs'
ROLES = ('public_run', 'legacy_run', 'rust_run', 'npm_cache', 'node_tools')


def need(value, message):
    if not value:
        raise ValueError(message)


def api(repo, suffix):
    raw = subprocess.check_output(['gh', 'api', 'repos/' + repo + suffix])
    return json.loads(raw)


def check_origin(meta, run, artifact_id, repo_id, run_id, attempt, head):
    expiry = datetime.fromisoformat(meta['expires_at'].replace('Z', '+00:00'))
    need(meta['id'] == artifact_id and meta['name'] == NAME and meta['expired'] is False and
         expiry > datetime.now(timezone.utc) and re.fullmatch(r'sha256:[0-9a-f]{64}', meta['digest']) and
         meta['workflow_run']['id'] == run_id and
         meta['workflow_run']['head_sha'] == head and meta['workflow_run']['repository_id'] == repo_id and
         run['id'] == run_id and run['run_attempt'] == attempt and run['head_sha'] == head and
         run['repository']['id'] == repo_id, 'live artifact or workflow origin differs')


def bind(profile, run_root, artifact_id, receipt_path, context_path):
    profile = verifier.physical(profile); run_root = verifier.physical(run_root)
    raw = profile.read_bytes(); profile_sha = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    verifier.verify(profile, run_root)
    roots = {role: verifier.physical(run_root / 'roles' / role) for role in ROLES}
    siblings = {'rs_prompts': verifier.physical(run_root / 'siblings/rs_prompts')}
    all_roots = list(roots.values()) + list(siblings.values())
    need(all(a != b and a not in b.parents and b not in a.parents
             for i, a in enumerate(all_roots) for b in all_roots[i + 1:]), 'overlapping roots refused')
    head = os.environ['GITHUB_SHA']; run_id = int(os.environ['GITHUB_RUN_ID'])
    attempt = int(os.environ['GITHUB_RUN_ATTEMPT']); repo = os.environ['GITHUB_REPOSITORY']
    repo_id = int(os.environ['GITHUB_REPOSITORY_ID'])
    meta = api(repo, '/actions/artifacts/' + str(artifact_id))
    gh_run = api(repo, '/actions/runs/' + str(run_id))
    check_origin(meta, gh_run, artifact_id, repo_id, run_id, attempt, head)
    receipt = {'version': 'som.typescript-regression-restored-inputs.v1',
               'helper_revision': head, 'candidate_revision': head, 'run_id': str(run_id),
               'run_attempt': str(attempt), 'profile_sha256': profile_sha,
               'roots': {k: str(v) for k, v in roots.items()},
               'files': {k: doc['roles'][k]['files'] for k in ROLES},
               'siblings': {k: str(v) for k, v in siblings.items()},
               'sibling_files': {k: doc['siblings'][k]['files'] for k in siblings}}
    receipt_raw = (json.dumps(receipt, sort_keys=True, indent=2) + '\n').encode()
    need(not receipt_path.exists() and not context_path.exists(), 'output collision')
    with receipt_path.open('xb') as stream:
        stream.write(receipt_raw)
    artifact = {'id': artifact_id, 'name': NAME, 'digest': meta['digest'],
                'receipt_sha256': hashlib.sha256(receipt_raw).hexdigest()}
    context = {'roots': receipt['roots'], 'siblings': receipt['siblings'],
               'receipt': str(receipt_path), 'artifact': artifact}
    with context_path.open('x') as stream:
        stream.write(json.dumps(context, sort_keys=True, indent=2) + '\n')
    return {'status': 'draft-origin-bound', 'artifact_id': artifact_id, 'artifact_digest': meta['digest'],
            'receipt_sha256': artifact['receipt_sha256'], 'profile_sha256': profile_sha,
            'run_id': run_id, 'attempt': attempt, 'product_acceptance': 'not_run'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--artifact-id', type=int, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--context', type=Path, required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
         'GitHub-hosted runner required')
    temp = verifier.physical(Path(os.environ['RUNNER_TEMP']))
    for path in (args.run, args.receipt, args.context):
        need(path.is_absolute() and path.is_relative_to(temp) and '..' not in path.parts and
             not any(p.is_symlink() for p in (path, *path.parents)), 'runner scratch required')
    print(json.dumps(bind(args.profile, args.run, args.artifact_id, args.receipt, args.context), sort_keys=True))


if __name__ == '__main__':
    main()
