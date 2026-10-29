#!/usr/bin/env python3
"""Scratch-only wrapper for the existing M6 release workflow's two steps.

The checked-in target JSON contains public producer IDs and hashes only.
No token, key, restored content or secret is written to that file or logs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
AUDITOR = HERE / 'audit_release_raw.py'
TARGET = HERE / 'audit_target.json'
REPO = 'faberline/som'


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(phase):
    need(phase in ('fetch', 'inspect'), 'unknown phase')
    need(os.environ.get('GITHUB_ACTIONS') == 'true'
         and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS'
         and os.environ.get('GITHUB_REPOSITORY') == REPO, 'GitHub-hosted macOS required')
    target = json.loads(TARGET.read_bytes())
    need(set(target) == {'head', 'run_id', 'attempt', 'job_id', 'artifact_id', 'artifact_sha256',
                         'artifact_bytes', 'auditor_sha256', 'producer_binding_sha256'}, 'target schema differs')
    need(re.fullmatch(r'[0-9a-f]{40}', target['head']) is not None, 'bad producer head')
    for name in ('artifact_sha256', 'auditor_sha256', 'producer_binding_sha256'):
        need(re.fullmatch(r'[0-9a-f]{64}', target[name]) is not None, 'bad target hash')
    for name in ('run_id', 'attempt', 'job_id', 'artifact_id', 'artifact_bytes'):
        need(type(target[name]) is int and target[name] > 0, 'bad target identity')
    need(digest(AUDITOR) == target['auditor_sha256'], 'auditor source differs')
    temp = Path(os.environ['RUNNER_TEMP']).resolve()
    directory = temp / 'm6-independent-raw-audit'
    source, archive, binding = directory / 'producer-source', directory / 'producer.zip', directory / 'binding.json'
    common = ['--archive', str(archive), '--source', str(source), '--identity', str(binding),
              '--run-id', str(target['run_id']), '--attempt', str(target['attempt']),
              '--job-id', str(target['job_id']), '--artifact-id', str(target['artifact_id']),
              '--head', target['head'], '--artifact-sha256', target['artifact_sha256']]
    if phase == 'fetch':
        need(bool(os.environ.get('GH_TOKEN')) and 'M6_INPUTS_KEYRING' not in os.environ,
             'fetch step token boundary differs')
        directory.mkdir(mode=0o700, exist_ok=False)
        subprocess.run(['git', 'init', '--quiet', str(source)], check=True, timeout=120)
        subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', 'https://github.com/faberline/som.git'],
                       check=True, timeout=120)
        subprocess.run(['git', '-C', str(source), 'fetch', '--quiet', '--depth=1', 'origin', target['head']],
                       check=True, timeout=300)
        subprocess.run(['git', '-C', str(source), 'checkout', '--quiet', '--detach', 'FETCH_HEAD'],
                       check=True, timeout=120)
        with archive.open('xb') as stream:
            subprocess.run(['gh', 'api', f'repos/{REPO}/actions/artifacts/{target["artifact_id"]}/zip'],
                           stdout=stream, check=True, timeout=900)
        need(archive.stat().st_size == target['artifact_bytes'] and digest(archive) == target['artifact_sha256'],
             'downloaded artifact differs')
        subprocess.run([sys.executable, str(AUDITOR), '--phase', 'fetch', *common], check=True, timeout=600)
        need(digest(binding) == target['producer_binding_sha256'], 'fetch handoff differs from fixed target')
    else:
        need('GH_TOKEN' not in os.environ and 'GITHUB_TOKEN' not in os.environ
             and bool(os.environ.get('M6_INPUTS_KEYRING')),
             'inspect step token boundary differs')
        need(directory.is_dir() and digest(binding) == target['producer_binding_sha256'], 'handoff differs')
        subprocess.run([sys.executable, str(AUDITOR), '--phase', 'inspect', *common,
                        '--identity-sha256', target['producer_binding_sha256'],
                        '--restored', str(directory / 'restored'), '--out', str(directory / 'audit.json')],
                       check=True, timeout=1800)


if __name__ == '__main__':
    need(len(sys.argv) == 2, 'one phase required')
    main(sys.argv[1])
