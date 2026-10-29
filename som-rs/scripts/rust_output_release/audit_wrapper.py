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
import platform
import re
import stat
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
AUDITOR = HERE / 'audit_release_raw.py'
TARGET = HERE / 'audit_target.json'
REPO = 'faberline/som'
GH_RELEASE_URL = 'https://github.com/cli/cli/releases/download/v2.103.0/gh_2.103.0_macOS_arm64.zip'
GH_ZIP_SHA256 = '54be566e9f614bea5de8021e95b9ad33dfee3b9d2ea120c9c294c00defaa5014'
GH_BINARY_SHA256 = '1ffcdcff76db02ff4f9de0fbf784ed4b6d18e434e89450db314b3f77d0f9443b'
GH_ZIP_BYTES = 14105033
GH_BINARY_BYTES = 38914960
GH_MEMBER = 'gh_2.103.0_macOS_arm64/bin/gh'


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def install_gh(directory):
    """Download a public fixed release with no credentials in the environment."""
    need(platform.machine() == 'arm64', 'fixed GitHub CLI needs macOS arm64')
    public_env = {k: v for k, v in os.environ.items()
                  if not re.search(r'(TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL)', k, re.IGNORECASE)
                  and k != 'SSH_AUTH_SOCK'}
    archive = directory / 'gh-release.zip'
    done = subprocess.run(['curl', '--disable', '--fail', '--silent', '--show-error', '--location',
                    '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '5',
                    '--max-time', '180', '--max-filesize', str(GH_ZIP_BYTES), '--output', str(archive), GH_RELEASE_URL],
                   check=False, timeout=200, env=public_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    need(done.returncode == 0, 'public GitHub CLI download failed')
    need(archive.stat().st_size == GH_ZIP_BYTES and digest(archive) == GH_ZIP_SHA256,
         'official GitHub CLI ZIP differs')
    tool_dir = directory / 'tool'
    tool_dir.mkdir(mode=0o700)
    binary = tool_dir / 'gh'
    with zipfile.ZipFile(archive) as z:
        matches = [i for i in z.infolist() if i.filename == GH_MEMBER]
        need(len(matches) == 1 and not matches[0].is_dir() and stat.S_ISREG(matches[0].external_attr >> 16)
             and matches[0].file_size == GH_BINARY_BYTES,
             'official GitHub CLI ZIP member differs')
        total = 0
        with z.open(matches[0]) as src, binary.open('xb') as dst:
            for block in iter(lambda: src.read(1024 * 1024), b''):
                total += len(block)
                need(total <= GH_BINARY_BYTES, 'GitHub CLI member exceeds limit')
                dst.write(block)
    need(total == GH_BINARY_BYTES and digest(binary) == GH_BINARY_SHA256, 'GitHub CLI binary differs')
    binary.chmod(0o700)
    version = subprocess.check_output([str(binary), '--version'], text=True, timeout=30).splitlines()[0]
    need(re.fullmatch(r'gh version 2\.103\.0 \(2026-10-21\)', version) is not None,
         'fixed GitHub CLI version differs')
    print('verified GitHub CLI version: ' + version, flush=True)
    return binary


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
        gh = install_gh(directory)
        subprocess.run(['git', 'init', '--quiet', str(source)], check=True, timeout=120)
        subprocess.run(['git', '-C', str(source), 'remote', 'add', 'origin', 'https://github.com/faberline/som.git'],
                       check=True, timeout=120)
        subprocess.run(['git', '-C', str(source), 'fetch', '--quiet', '--depth=1', 'origin', target['head']],
                       check=True, timeout=300)
        subprocess.run(['git', '-C', str(source), 'checkout', '--quiet', '--detach', 'FETCH_HEAD'],
                       check=True, timeout=120)
        with archive.open('xb') as stream:
            done = subprocess.run([str(gh), 'api', f'repos/{REPO}/actions/artifacts/{target["artifact_id"]}/zip'],
                                  stdout=stream, stderr=subprocess.PIPE, check=False, timeout=900)
        need(done.returncode == 0, 'producer artifact download failed')
        need(archive.stat().st_size == target['artifact_bytes'] and digest(archive) == target['artifact_sha256'],
             'downloaded artifact differs')
        audit_env = {**os.environ, 'M6_AUDIT_GH': str(gh)}
        subprocess.run([sys.executable, str(AUDITOR), '--phase', 'fetch', *common], check=True, timeout=600,
                       env=audit_env)
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
