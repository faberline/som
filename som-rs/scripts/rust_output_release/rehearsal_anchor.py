#!/usr/bin/env python3
"""Restore one independently audited sealed rehearsal on a hosted runner.

No historical request, reply, case metadata, or new digest is printed.
The caller must never publish the restored directory without sealing it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import tempfile
import time
import unicodedata
import zipfile

import release_common as rc

REPO = 'faberline/som'
REPOSITORY_ID = 1390374878
RUN_ID, ATTEMPT, JOB_ID = 40863011173, 1, 123410697132
HEAD = '57f0d249af7cdf06dbd2f2008f8c2275fe2bf257'
WORKFLOW_ID = 399871729
ARTIFACT_ID, ARCHIVE_SIZE = 12892064549, 111991966
ARCHIVE_SHA256 = '8d24fc4779a6585b4b5ecc7dfd5d5744f19345de80ce9cb1ac0a18a762389c8d'
GH_SHA256 = '1ffcdcff76db02ff4f9de0fbf784ed4b6d18e434e89450db314b3f77d0f9443b'
NODE_SHA256 = '70851490e028b3d699a8d6d4e1de909af2a989359ae807974c92af9c6580a8e8'
LIBNODE_SHA256 = '0c2dfacde99b9381ec37fbd222891271c7f455cc0524294928e3e34199d57ec3'
RESTORER_SHA256 = 'ac5bfe21853712a38a8f5f032f44a4151c7bf7c2c54b669095704ba20e84a822'
SEALED_INPUTS_SHA256 = 'bda649080df1fced5ac6cfabdc300d9d9b1b3d9a53a1240c11fdce80e0b1e9c9'
NODE = Path('/opt/homebrew/Cellar/node/26.5.0/bin/node')
LIBNODE = Path('/opt/homebrew/Cellar/node/26.5.0/lib/libnode.147.dylib')
MAX_LOG = 8 * 1024 * 1024
HASH = re.compile('[0-9a-f]{64}\\Z')


def need(condition):
    if not condition:
        raise ValueError('sealed rehearsal anchor refused')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def reviewed_gh(temp):
    path = Path(os.environ['M6_AUDIT_GH'])
    need(path.is_absolute() and not path.is_symlink() and path.is_file()
         and path.resolve(strict=True).is_relative_to(temp) and rc.digest(path) == GH_SHA256)
    return str(path)


def api(gh, path):
    env = dict(os.environ)
    env.pop('M6_INPUTS_KEYRING', None)
    done = subprocess.run([gh, 'api', path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=120, check=False, env=env)
    need(done.returncode == 0 and len(done.stdout) <= 8 * 1024 * 1024)
    return json.loads(done.stdout)


def bounded_job_log(gh, endpoint):
    env = dict(os.environ)
    env.pop('M6_INPUTS_KEYRING', None)
    proc = subprocess.Popen([gh, 'api', '--allow-escape-sequences', endpoint],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    data, deadline = bytearray(), time.monotonic() + 120
    try:
        while True:
            remaining = deadline - time.monotonic()
            need(remaining > 0)
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
            if not ready:
                continue
            block = os.read(proc.stdout.fileno(), 65536)
            if not block:
                break
            data.extend(block)
            need(len(data) <= MAX_LOG)
        need(proc.wait(timeout=max(1, deadline - time.monotonic())) == 0)
        return bytes(data)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=20)
        proc.stdout.close()


def verify_upload_log(raw):
    clean = re.sub(rb'\x1b\[[0-9;]*m', b'', raw)
    need(all((b >= 32 and b != 127) or b in (9, 10) for b in clean))
    decoded = clean.decode('utf-8-sig', 'strict')
    need(all(c in ('\n', '\t') or unicodedata.category(c) not in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp')
             for c in decoded))
    lines = decoded.split('\n')
    stamp = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z '
    final = [(i, m) for i, line in enumerate(lines) if
             (m := re.fullmatch(stamp + r'Artifact m6-release-evidence\.zip successfully finalized\. Artifact ID ([0-9]+)', line))]
    need(len(final) == 1 and int(final[0][1].group(1)) == ARTIFACT_ID)
    index = final[0][0]
    hashes = [m.group(1) for line in lines[:index] if
              (m := re.fullmatch(stamp + r'SHA256 digest of uploaded artifact zip is ([0-9a-f]{64})', line))]
    links = [m for line in lines[index + 1:] if (m := re.fullmatch(
        stamp + r'Artifact download URL: https://github\.com/faberline/som/actions/runs/([0-9]+)/artifacts/([0-9]+)', line))]
    need(hashes and hashes[-1] == ARCHIVE_SHA256 and len(links) == 1
         and int(links[0].group(1)) == RUN_ID and int(links[0].group(2)) == ARTIFACT_ID)


def bind_github(gh, archive):
    base = 'repos/' + REPO
    run = api(gh, f'{base}/actions/runs/{RUN_ID}/attempts/{ATTEMPT}')
    need(run['id'] == RUN_ID and run['run_attempt'] == ATTEMPT and run['head_sha'] == HEAD
         and run['path'] == '.github/workflows/m6-release.yml' and run['workflow_id'] == WORKFLOW_ID
         and run['event'] in ('pull_request', 'workflow_dispatch') and run['status'] == 'completed'
         and run['conclusion'] == 'success' and run['repository']['id'] == REPOSITORY_ID
         and run['repository']['full_name'] == REPO and run['head_repository']['id'] == REPOSITORY_ID)
    jobs = api(gh, f'{base}/actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs?per_page=100')['jobs']
    selected = [job for job in jobs if job['id'] == JOB_ID]
    need(len(selected) == 1 and selected[0]['name'] == 'Four-target switch and rollback on QA ports'
         and selected[0]['status'] == 'completed' and selected[0]['conclusion'] == 'success')
    steps = {step['name']: step for step in selected[0]['steps']}
    need(len(steps) == len(selected[0]['steps']))
    for name in ('Record the tested source revision', 'Rehearse the four targets and five recovery scenarios offline',
                 'Keep the encrypted raw rehearsal'):
        need(steps[name]['conclusion'] == 'success')
    artifact = api(gh, f'{base}/actions/artifacts/{ARTIFACT_ID}')
    source = artifact['workflow_run']
    need(artifact['id'] == ARTIFACT_ID and artifact['name'] == 'm6-release-evidence'
         and artifact['expired'] is False and artifact['digest'] == 'sha256:' + ARCHIVE_SHA256
         and artifact['size_in_bytes'] == ARCHIVE_SIZE and source['id'] == RUN_ID
         and source['head_sha'] == HEAD and source['repository_id'] == REPOSITORY_ID
         and source['head_repository_id'] == REPOSITORY_ID
         and archive.stat().st_size == ARCHIVE_SIZE and rc.digest(archive) == ARCHIVE_SHA256)
    verify_upload_log(bounded_job_log(gh, f'{base}/actions/jobs/{JOB_ID}/logs'))


def unpack_sealed(archive, sealed):
    need(not sealed.exists() and not sealed.is_symlink())
    sealed.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        need(len(names) == len(set(names)) and 'evidence-index.json' in names)
        item = bundle.getinfo('evidence-index.json')
        need(item.file_size <= 1024 * 1024)
        index_raw = bundle.read(item)
        index = json.loads(index_raw)
        need(index.get('version') == 'som.m6-evidence-index.v1' and len(index['packages']) == 10)
        expected = {'evidence-index.json'}
        for number, entry in enumerate(index['packages']):
            stem = f'package-{number:03d}'
            need(entry['archive'] == stem + '.sealed' and entry['manifest'] == stem + '.manifest.json'
                 and HASH.fullmatch(entry['manifest_sha256']) and HASH.fullmatch(entry['cipher_sha256']))
            expected.update((entry['archive'], entry['manifest']))
        need(set(names) == expected and sum(item.file_size for item in infos) <= 256 * 1024 * 1024)
        for item in infos:
            mode = stat.S_IFMT(item.external_attr >> 16)
            need(mode in (0, stat.S_IFREG) and not item.flag_bits & 1 and item.file_size <= 128 * 1024 * 1024)
            with bundle.open(item) as source, (sealed / item.filename).open('xb') as target:
                count = 0
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    count += len(block)
                    need(count <= item.file_size)
                    target.write(block)
                need(count == item.file_size)
    for entry in index['packages']:
        need(rc.digest(sealed / entry['manifest']) == entry['manifest_sha256']
             and rc.digest(sealed / entry['archive']) == entry['cipher_sha256'])
    return sha(index_raw)


def physical(root, relative):
    path = root
    for part in Path(relative).parts:
        need(part not in ('', '.', '..'))
        path = path / part
        need(not path.is_symlink())
    need(path.is_file() and path.resolve(strict=True).is_relative_to(root))
    return path


def restore_verified_prior(archive, temp, workspace):
    need(temp.is_absolute() and not temp.is_symlink() and temp.is_dir()
         and temp.resolve(strict=True) == temp
         and Path(os.environ['RUNNER_TEMP']).resolve(strict=True) == temp)
    need(archive.is_absolute() and not archive.is_symlink() and archive.is_file()
         and archive.resolve(strict=True).is_relative_to(temp))
    gh = reviewed_gh(temp)
    bind_github(gh, archive)
    restorer = workspace / 'som-rs/scripts/react_output_acceptance/restore_hosted_evidence.mjs'
    sealed_inputs = workspace / 'som-rs/scripts/react_output_acceptance/sealed_inputs.mjs'
    need(rc.digest(restorer) == RESTORER_SHA256 and rc.digest(sealed_inputs) == SEALED_INPUTS_SHA256
         and rc.digest(NODE) == NODE_SHA256 and rc.digest(LIBNODE) == LIBNODE_SHA256)
    staging = Path(tempfile.mkdtemp(prefix='m6-canary-prior-', dir=temp))
    need(staging.parent == temp and not staging.is_symlink() and staging.is_dir()
         and stat.S_IMODE(staging.stat().st_mode) == 0o700)
    sealed, restored = staging / 'sealed', staging / 'restored'
    need(not restored.exists() and not restored.is_symlink())
    index_sha = unpack_sealed(archive, sealed)
    env = dict(os.environ)
    env.pop('GH_TOKEN', None)
    env.pop('GITHUB_TOKEN', None)
    done = subprocess.run([str(NODE), str(restorer), '--root', str(sealed), '--out', str(restored),
                           '--index-sha256', index_sha], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=env, timeout=1200, check=False)
    need(done.returncode == 0 and len(done.stdout) <= 1024 * 1024)
    result = json.loads(done.stdout)
    need(result['status'] == 'restored' and result['index_sha256'] == index_sha
         and result['package_count'] == 10 and result['file_count'] == 1996)
    root = restored.resolve(strict=True)
    prior = physical(root, 'run/inputs/prerequisites.json')
    summary = rc.load(physical(root, 'summary.json'))
    rehearsal_path = physical(root, 'run/rehearsal/rehearsal.json')
    rehearsal = rc.load(rehearsal_path)
    need(summary['passed'] is True and summary['status'] == 'passed' and summary['source_commit'] == HEAD
         and summary['rehearsal_sha256'] == rc.digest(rehearsal_path)
         and summary['prerequisites_sha256'] == rc.digest(prior)
         and rehearsal['passed'] is True and rehearsal['profile'] == 'compiler-only'
         and rehearsal['prerequisites']['sha256'] == rc.digest(prior)
         and rc.digest(archive) == ARCHIVE_SHA256)
    return {'prior_path': prior, 'prior_sha256': rc.digest(prior),
            'archive_path': archive, 'archive_sha256': ARCHIVE_SHA256}
