#!/usr/bin/env python3
"""Independently inspect a sealed M6 rehearsal artifact, on a GitHub runner.

The caller restores the sealed artifact with restore_hosted_evidence.mjs.
This program validates the resulting bytes against the original ZIP manifests.
It records only hashes, counts and coverage. It never runs recorded commands,
contacts recorded ports, signals recorded PIDs, or uploads plaintext.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

REPO = 'faberline/som'
REPOSITORY_ID = 1390374878
WORKFLOW_ID = 399871729
WORKFLOW_PATH = '.github/workflows/m6-release.yml'
WORKFLOW_SHA256 = '163584a9ae3a7045b6a939ada696b50b0565bc913dd15de6f577981f8382381e'
CANDIDATE_SHA256 = '59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308'
PREVIOUS_SHA256 = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
RESTORE_SOURCES = {
    'som-rs/scripts/react_output_acceptance/restore_hosted_evidence.mjs':
        'ac5bfe21853712a38a8f5f032f44a4151c7bf7c2c54b669095704ba20e84a822',
    'som-rs/scripts/react_output_acceptance/sealed_inputs.mjs':
        'bda649080df1fced5ac6cfabdc300d9d9b1b3d9a53a1240c11fdce80e0b1e9c9',
}
SCENARIOS = ('switch', 'after_stop', 'after_start', 'after_pointer', 'after_commit')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def relative(name):
    require(isinstance(name, str) and name and '\\' not in name and '\x00' not in name,
            'invalid relative path')
    parts = name.split('/')
    require(all(p not in ('', '.', '..') for p in parts) and not name.startswith('/'), 'unsafe path')
    return PurePosixPath(name)


def regular(root, name):
    rel = relative(name)
    p = root
    for part in rel.parts:
        p = p / part
        st = p.lstat()
        require(not stat.S_ISLNK(st.st_mode), 'symlink in restored path')
    require(stat.S_ISREG(st.st_mode), 'not a regular restored file')
    return p


def load(root, name):
    return json.loads(regular(root, name).read_bytes())


def verify_universe(archive, root):
    """Bind the complete restored regular-file universe to the exact ZIP."""
    entries = {}
    with zipfile.ZipFile(archive) as z:
        names = [i.filename for i in z.infolist()]
        require(len(names) == len(set(names)), 'duplicate ZIP member')
        require('evidence-index.json' in names, 'missing index')
        index_raw = z.read('evidence-index.json')
        index = json.loads(index_raw)
        require(index.get('version') == 'som.m6-evidence-index.v1', 'wrong evidence index')
        packages = index['packages']
        require(isinstance(packages, list) and packages, 'empty packages')
        expected = {'evidence-index.json'}
        for i, item in enumerate(packages):
            manifest, cipher = f'package-{i:03}.manifest.json', f'package-{i:03}.sealed'
            require(item['manifest'] == manifest and item['archive'] == cipher, 'package name mismatch')
            require(manifest not in expected and cipher not in expected, 'duplicate package')
            expected.update((manifest, cipher))
            raw = z.read(manifest)
            require(sha(raw) == item['manifest_sha256'], 'manifest hash mismatch')
            h = hashlib.sha256()
            with z.open(cipher) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            require(h.hexdigest() == item['cipher_sha256'], 'cipher hash mismatch')
            manifest_doc = json.loads(raw)
            require(manifest_doc['cipher_sha256'] == item['cipher_sha256'], 'manifest cipher mismatch')
            files = manifest_doc['files']
            require(len(files) == item['total_files'] == manifest_doc['total_files'], 'file count mismatch')
            for f in files:
                name = str(relative(f['path']))
                require(name not in entries and HASH.fullmatch(f['sha256']) is not None, 'duplicate/bad file')
                require(type(f['bytes']) is int and f['bytes'] >= 0 and type(f['mode']) is int,
                        'bad file metadata')
                entries[name] = f
        require(set(names) == expected, 'ZIP universe differs')
    actual = set()
    def walk_error(error):
        raise error
    require(stat.S_ISDIR(root.lstat().st_mode), 'restored root is not a directory')
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        d = Path(directory)
        for name in dirs:
            st = (d / name).lstat()
            require(not stat.S_ISLNK(st.st_mode) and stat.S_ISDIR(st.st_mode), 'invalid restored directory')
        for name in files:
            p = d / name
            rel = p.relative_to(root).as_posix()
            regular(root, rel)
            actual.add(rel)
    require(actual == set(entries), 'restored file universe differs')
    for name, item in entries.items():
        p = regular(root, name)
        st = p.stat()
        require(st.st_size == item['bytes'] and stat.S_IMODE(st.st_mode) == item['mode']
                and digest(p) == item['sha256'], 'restored file mismatch: ' + name)
    return {'index_sha256': sha(index_raw), 'packages': len(packages), 'files': len(entries)}


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def bind_github(run_id, attempt, job_id, artifact_id, head, archive):
    base = f'repos/{REPO}'
    run = api(f'{base}/actions/runs/{run_id}/attempts/{attempt}')
    require(run['id'] == run_id and run['run_attempt'] == attempt and run['head_sha'] == head
            and run['path'] == WORKFLOW_PATH and run['workflow_id'] == WORKFLOW_ID
            and run['event'] in ('pull_request', 'workflow_dispatch')
            and run['repository']['id'] == REPOSITORY_ID and run['repository']['full_name'] == REPO
            and run['head_repository']['id'] == REPOSITORY_ID
            and run['status'] == 'completed' and run['conclusion'] == 'success', 'run mismatch')
    jobs = api(f'{base}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')['jobs']
    selected = [j for j in jobs if j['id'] == job_id]
    require(len(selected) == 1 and selected[0]['name'] == 'Four-target switch and rollback on QA ports'
            and selected[0]['status'] == 'completed' and selected[0]['conclusion'] == 'success', 'job mismatch')
    steps = {s['name']: s for s in selected[0]['steps']}
    for name in ('Record the tested source revision', 'Rehearse the four targets and five recovery scenarios offline',
                 'Keep the encrypted raw rehearsal'):
        require(steps[name]['conclusion'] == 'success', 'producer step did not succeed: ' + name)
    artifact = api(f'{base}/actions/artifacts/{artifact_id}')
    observed_sha = digest(archive)
    require(artifact['id'] == artifact_id and artifact['name'] == 'm6-release-evidence'
            and not artifact['expired'] and artifact['workflow_run']['id'] == run_id
            and artifact['workflow_run']['head_sha'] == head and artifact['digest'] == 'sha256:' + observed_sha
            and artifact['workflow_run']['repository_id'] == REPOSITORY_ID
            and artifact['workflow_run']['head_repository_id'] == REPOSITORY_ID
            and artifact['size_in_bytes'] == archive.stat().st_size, 'artifact metadata mismatch')
    # Artifact metadata does not include the attempt or job. Bind those to the
    # actual upload action log from the precise job of the precise attempt.
    logs = subprocess.check_output(['gh', 'api', f'{base}/actions/jobs/{job_id}/logs'], text=True)
    require(f'Artifact m6-release-evidence.zip successfully finalized. Artifact ID {artifact_id}' in logs
            and f'SHA256 digest of uploaded artifact zip is {observed_sha}' in logs,
            'producer upload log mismatch')
    return {'run_id': run_id, 'attempt': attempt, 'job_id': job_id, 'artifact_id': artifact_id,
            'head_sha': head, 'artifact_sha256': observed_sha}


def check_checkout(source, head):
    got = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    require(got == head, 'source HEAD mismatch')
    status = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'],
                                     text=True)
    require(not status, 'source checkout is dirty')
    require(digest(regular(source, WORKFLOW_PATH)) == WORKFLOW_SHA256, 'workflow source mismatch')
    for path, expected in RESTORE_SOURCES.items():
        require(digest(regular(source, path)) == expected, 'evidence restorer source mismatch')


def check_source(source, head, report):
    check_checkout(source, head)
    scripts = source / 'som-rs/scripts/rust_output_release'
    recorded = report['script_sha256']
    require(set(recorded) == {p.name for p in scripts.glob('*.py')}, 'source script set mismatch')
    for name, expected in recorded.items():
        require(relative(name).name == name and digest(regular(scripts, name)) == expected,
                'producer source differs: ' + name)


def decrypt_original_zip(archive, root, source):
    """Restore the authenticated ZIP using the pinned producer's source."""
    require(not root.exists() and root.parent.is_dir(), 'restore target must be fresh')
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
    require(runner_temp in root.parent.resolve().parents or root.parent.resolve() == runner_temp,
            'restore target must be under RUNNER_TEMP')
    with tempfile.TemporaryDirectory(prefix='m6-sealed-audit-', dir=runner_temp) as temp:
        sealed = Path(temp)
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            require(len(names) == len(set(names)) and 'evidence-index.json' in names, 'bad ZIP entries')
            index_raw = z.read('evidence-index.json')
            index = json.loads(index_raw)
            require(index.get('version') == 'som.m6-evidence-index.v1' and index['packages'], 'invalid index')
            expected = {'evidence-index.json'}
            for i, item in enumerate(index['packages']):
                manifest, cipher = f'package-{i:03}.manifest.json', f'package-{i:03}.sealed'
                require(item['manifest'] == manifest and item['archive'] == cipher, 'unexpected package name')
                expected.update((manifest, cipher))
            require(set(names) == expected, 'unexpected ZIP member')
            for item in infos:
                with z.open(item) as src, (sealed / item.filename).open('xb') as dst:
                    for block in iter(lambda: src.read(1024 * 1024), b''):
                        dst.write(block)
        script = regular(source, 'som-rs/scripts/react_output_acceptance/restore_hosted_evidence.mjs')
        done = subprocess.run(['node', str(script), '--root', str(sealed), '--out', str(root),
                               '--index-sha256', sha(index_raw)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=1200, check=False)
        require(done.returncode == 0, 'authenticated evidence restoration failed')
        receipt = json.loads(done.stdout)
        require(receipt['status'] == 'restored' and receipt['index_sha256'] == sha(index_raw),
                'restoration receipt mismatch')
        return {'index_sha256': receipt['index_sha256'], 'package_count': receipt['package_count'],
                'file_count': receipt['file_count']}


def bound_input(root, config, binding):
    prefix = config['repo'] + '/.local/rust-compiler-goal/acceptance/react-131/'
    path = binding['path']
    require(isinstance(path, str) and path.startswith(prefix), 'input path outside captured root')
    file = regular(root, path[len(prefix):])
    require(digest(file) == binding['sha256'], 'input binding differs')
    return binding['sha256']


def compare_exchanges(root, config, report, candidate, previous):
    """Compare all retained HTTP request/reply pairs and CLI stdout bytes.

    This joins by a multiset, because raw files do not name their caller or
    record each HTTP route. It does not infer an exchange's HTTP status or exit.
    """
    cases = {
        'previous': sum((config['previous_canaries'][f] for f in ('python', 'rust', 'typescript')), []),
        'new': sum((config[f + '_canaries'] for f in ('python', 'rust', 'typescript', 'react')), []),
    }
    for role, rows in cases.items():
        require(len(rows) == (6 if role == 'previous' else 8), 'canary inventory differs')
        for case in rows:
            for step in case['steps']:
                bound_input(root, config, step['response'])
                if step['kind'] == 'http':
                    require(step['path'] in ('/v1/compile', '/v1/assemble'), 'unexpected HTTP route')
                    bound_input(root, config, step['request'])
                elif step['kind'] == 'cli':
                    require(step['binaries'] == [role], 'unexpected CLI role')
                    for item in step['inputs']:
                        bound_input(root, config, item)
                else:
                    raise ValueError('unknown canary kind')
    counts = Counter()
    successful = report['scenarios'][0]
    committed_journal = load(root, 'run/rehearsal/switch/switch/journal.json')
    candidate_health = committed_journal[-1]['receipt']['health']
    previous_health = load(root, 'run/rehearsal/switch/root/som-rs/runtime/default.json')['health']
    require(successful['name'] == 'switch', 'first scenario differs')
    health_counts = {'resume': 3, 'check': 1, 'restore': 2, 'check-restored': 1}
    switch_health = {'switch': 5, 'after_stop': 5, 'after_start': 6, 'after_pointer': 6, 'after_commit': 7}
    for row in report['scenarios']:
        base = root / 'run/rehearsal' / row['name']
        units = ['resume', 'switch', 'check'] + (['restore', 'check-restored'] if row['name'] == 'switch' else [])
        old_bin, new_bin = config['previous']['binary']['path'], config['candidate']['binary']['path']
        installed = row['root'] + '/som-rs/runtime/versions/' + candidate + '/som'
        for unit in units:
            directory = base / unit
            sequence = []
            def extend(label, role):
                sequence.extend((label, role, c) for c in cases[role])
            if unit == 'resume':
                extend('resumed-previous-default', 'previous')
            elif unit == 'switch':
                extend('current-default', 'previous')
                extend('clone', 'previous')
                extend('clone', 'new')
                if row['name'] in ('switch', 'after_pointer', 'after_commit'):
                    extend('new-default', 'new')
                if row['name'] in ('switch', 'after_commit'):
                    extend('new-default-after-pointer', 'new')
                if row['name'] != 'switch':
                    extend('restored-default', 'previous')
            elif unit == 'restore':
                extend('restored-default', 'previous')
            else:
                extend('post-check', 'new' if row['name'] == 'switch' and unit == 'check' else 'previous')
            journal = load(root, 'run/rehearsal/' + row['name'] + '/' + unit + '/journal.json')
            events = []
            active = {}
            for event in journal:
                if event['stage'] == 'started':
                    argv = event['argv']
                    require(isinstance(argv, list) and argv[-2] == '--port' and argv[-1].isdigit(), 'bad started argv')
                    port = int(argv[-1])
                    if port != row['port']:
                        require(18919 <= port <= 18999 and port not in active, 'bad clone port')
                        active[port] = event['pid']
                elif event['stage'] == 'stopped':
                    matches = [port for port, pid in active.items() if pid == event['pid']]
                    require(len(matches) <= 1, 'ambiguous stopped clone')
                    for port in matches:
                        del active[port]
                elif event['stage'] == 'canary_passed':
                    port = event['port']
                    if event['label'] == 'clone':
                        require(port in active, 'clone canary has no active recorded owner')
                    else:
                        require(port == row['port'], 'non-clone canary on unexpected port')
                    events.append(event)
            require(not active, 'clone lacks a recorded stop')
            require(len(events) == len(sequence), 'canary event count differs')
            for event, (label, _role, case) in zip(events, sequence):
                require(event['label'] == label and event['name'] == case['name'], 'canary event order differs')
            expected_http, expected_cli = [], []
            for event, (_label, role, case) in zip(events, sequence):
                for step in case['steps']:
                    if step['kind'] == 'http':
                        expected_http.append((step['request']['sha256'], step['response']['sha256']))
                    else:
                        expected_cli.append((role, tuple(step['args']), step['response']['sha256'], event['port']))
            observed_http, observed_cli = [], []
            paired_responses, paired_stdout, paired_stderr = set(), set(), set()
            for request in sorted(directory.glob('[0-9][0-9][0-9][0-9]-request.raw')):
                number = int(request.name[:4])
                response = directory / f'{number + 1:04d}-response.raw'
                require(response.is_file() and not response.is_symlink(), 'HTTP response missing')
                paired_responses.add(response.name)
                observed_http.append((digest(request), digest(response)))
            for command in sorted(directory.glob('[0-9][0-9][0-9][0-9]-cli-argv.json')):
                number = int(command.name[:4])
                stdout = directory / f'{number + 1:04d}-cli-stdout.raw'
                stderr = directory / f'{number + 2:04d}-cli-stderr.raw'
                require(stdout.is_file() and stderr.is_file() and not stdout.is_symlink() and not stderr.is_symlink(),
                        'CLI output missing')
                argv = json.loads(command.read_bytes())
                require(isinstance(argv, list) and len(argv) >= 4 and argv[-2] == '--url', 'CLI argv mismatch')
                match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]+)', argv[-1])
                require(match is not None, 'CLI URL mismatch')
                role = 'previous' if argv[0] == old_bin else 'new' if argv[0] in (new_bin, installed) else None
                require(role is not None, 'CLI binary path mismatch')
                paired_stdout.add(stdout.name)
                paired_stderr.add(stderr.name)
                observed_cli.append((role, tuple(argv[1:-2]), digest(stdout), int(match.group(1))))
            # Response-only files are health checks and are not HTTP canaries.
            # Every CLI stdout/stderr must have a corresponding argv.
            require(paired_stdout == {p.name for p in directory.glob('*-cli-stdout.raw')}
                    and paired_stderr == {p.name for p in directory.glob('*-cli-stderr.raw')}, 'orphan CLI output')
            other_responses = [p for p in directory.glob('*-response.raw') if p.name not in paired_responses]
            require(len(other_responses) == (switch_health[row['name']] if unit == 'switch' else health_counts[unit]),
                    'unpaired health response count differs')
            for response in other_responses:
                body = json.loads(response.read_bytes())
                require(body == candidate_health or body == previous_health, 'unknown unpaired response')
            require(observed_http == expected_http, 'HTTP request/reply sequence differs')
            require(observed_cli == expected_cli, 'CLI argv/stdout/port sequence differs')
            counts['http_pairs'] += len(observed_http)
            counts['cli_records'] += len(observed_cli)
    return {'http_pairs': counts['http_pairs'], 'cli_records': counts['cli_records'],
            'join': 'per-unit source order and producer canary events; identical requests do not identify their caller',
            'http_route_per_exchange': 'not_recorded'}


def check_raw(root, source, head):
    report = load(root, 'run/rehearsal/rehearsal.json')
    summary = load(root, 'summary.json')
    require(summary['version'] == 'som.m6-release-rehearsal.v1' and summary['passed'] is True
            and summary['status'] == 'passed' and summary['source_commit'] == head
            and summary['scenarios'] == {name: True for name in SCENARIOS}
            and summary['offline_resume'] == {'passed': True, 'scenarios': 5}, 'summary mismatch')
    require(summary['rehearsal_sha256'] == digest(regular(root, 'run/rehearsal/rehearsal.json')),
            'rehearsal summary hash mismatch')
    require(report['version'] == 'som.rust-output-switch-rehearsal.v1' and report['passed'] is True
            and report['profile'] == 'compiler-only' and report['live_default_accessed'] is False,
            'rehearsal report mismatch')
    check_source(source, head, report)
    config_file = regular(root, 'run/inputs/prerequisites.json')
    require(digest(config_file) == report['prerequisites']['sha256'], 'prerequisites hash mismatch')
    config = json.loads(config_file.read_bytes())
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    require(report['prerequisites_receipt_sha256'] == sha(canonical)
            and summary['prerequisites_sha256'] == digest(config_file), 'prerequisites receipt mismatch')
    original_root = config['repo'] + '/.local/rust-compiler-goal/acceptance/react-131'
    require(report['prerequisites']['path'] == original_root + '/run/inputs/prerequisites.json',
            'original prerequisites path mismatch')
    candidate = config['candidate']['binary']['sha256']
    previous = config['previous']['binary']['sha256']
    require(candidate == summary['candidate_binary_sha256'] == CANDIDATE_SHA256
            and previous == summary['previous_binary_sha256'] == PREVIOUS_SHA256
            and candidate != previous, 'binary hashes mismatch')
    require(summary['protected_ports_unused'] == [8765, 18765] and summary['all_scenario_owners_stopped'] is True,
            'producer cleanup or protected-port attestation differs')
    for phase in ('before', 'after'):
        observations = summary['protected_port_check'][phase]
        require([o['port'] for o in observations] == [8765, 18765]
                and all(o['exit_code'] == 1 and o['stdout_empty'] is True and o['stderr_empty'] is True
                        for o in observations), 'protected-port observation differs')
    require([r['name'] for r in report['scenarios']] == list(SCENARIOS), 'scenario inventory mismatch')
    totals = Counter()
    for row in report['scenarios']:
        name = row['name']
        port = row['port']
        require(type(port) is int and 18919 <= port <= 18999 and port not in (8765, 18765), 'bad QA port')
        require(row['passed'] is True and row['problems'] == [], 'failed scenario')
        base = 'run/rehearsal/' + name
        original_scenario = original_root + '/run/rehearsal/' + name
        require(row['root'] == original_scenario + '/root', 'scenario root mismatch')
        seed = row['offline_seed']
        require(seed['exit_code'] == 0 and seed['old_pid_alive'] is False
                and type(seed['pid']) is int and seed['pid'] > 1, 'seed mismatch')
        require(seed['argv'][0] == '/opt/homebrew/Cellar/python@3.12/3.12.11_1/bin/python3.12'
                and seed['argv'][1:] == ['-c', 'import os; print(os.getpid())'], 'seed argv mismatch')
        for stream in ('stdout', 'stderr'):
            raw = regular(root, base + '/' + seed[stream + '_file']).read_bytes()
            require(sha(raw) == seed[stream + '_sha256'], 'seed stream mismatch')
            require(raw == (f"{seed['pid']}\n".encode() if stream == 'stdout' else b''), 'seed bytes mismatch')
        require(digest(regular(root, base + '/' + seed['receipt'])) == seed['default_sha256'], 'seed receipt mismatch')
        stale = load(root, base + '/' + seed['receipt'])
        require(stale['pid'] == seed['pid'] and stale['identity']['pid'] == seed['pid']
                and stale['identity']['port'] == port and stale['identity']['binary_sha256'] == previous,
                'stale seed identity mismatch')
        calls = [row['resume']] + row['calls']
        require([c['name'] for c in calls] == (['resume', 'switch', 'check', 'restore', 'check-restored']
                if name == 'switch' else ['resume', 'switch', 'check']), 'call order mismatch')
        for call in calls:
            callname = call['name']
            require((call['exit_code'] != 0) == (callname == 'switch' and name != 'switch'), 'call exit mismatch')
            expected_script = 'resume_default.py' if callname == 'resume' else 'switch.py' if callname in ('switch', 'restore') else 'check_default.py'
            argv = [seed['argv'][0], config['repo'] + '/som-rs/scripts/rust_output_release/' + expected_script,
                    '--prerequisites', report['prerequisites']['path'], '--root', row['root'], '--port', str(port)]
            if callname in ('resume', 'switch'):
                argv += ['--rehearsal', '--execute', '--output', original_scenario + '/' + callname]
                if callname == 'switch' and name != 'switch':
                    argv += ['--inject-failure', name]
            elif callname == 'restore':
                argv += ['--rehearsal', '--restore', original_scenario + '/switch',
                         '--output', original_scenario + '/restore']
            else:
                check_sha = candidate if name == 'switch' and callname == 'check' else previous
                argv += ['--expect-binary-sha256', check_sha, '--output', original_scenario + '/' + callname]
            require(call['argv'] == argv, 'call argv mismatch')
            require(call['started_at'] <= call['ended_at'], 'call time mismatch')
            for stream in ('stdout', 'stderr'):
                require(digest(regular(root, base + '/' + callname + '.' + stream)) == call[stream + '_sha256'],
                        'call stream mismatch')
            totals['calls'] += 1
        for unit in ('resume', 'switch') + (('restore',) if name == 'switch' else ()):
            journal = load(root, base + '/' + unit + '/journal.json')
            require(isinstance(journal, list) and journal, 'missing journal')
            for event in journal:
                require(not event.get('evidence_errors') and event['stage'] not in
                        ('OWNED_CLONE_CLEANUP_FAILED', 'ROLLBACK_FAILED_REQUIRES_OPERATOR'), 'journal failure')
            expected = 'resume_verified' if unit == 'resume' else 'restore_verified' if unit == 'restore' else 'committed' if name == 'switch' else 'rollback_verified'
            require(journal[-1]['stage'] == expected, 'journal terminal stage mismatch')
            if unit == 'switch' and name != 'switch':
                require(any(e['stage'] == 'failed' and f'injected failure at {name}' in e['error'] for e in journal),
                        'missing injected failure')
            totals['canary_events'] += sum(e['stage'] == 'canary_passed' for e in journal)
            if unit == 'switch':
                require(row['switch_stages'] == [e['stage'] for e in journal], 'recorded switch stages mismatch')
        resume = load(root, base + '/resume/result.json')
        resume_journal = load(root, base + '/resume/journal.json')
        require(resume['passed'] is True and resume['status'] == 'passed'
                and resume['old_pid'] == seed['pid'] and resume['old_pid_alive'] is False
                and resume['stale_snapshot_sha256'] == seed['default_sha256']
                and resume['previous_binary_sha256'] == previous
                and not resume['evidence_errors'] and not resume['cleanup_errors'], 'resume result mismatch')
        snapshots = sorted((root / base / 'switch').glob('*-previous-default.json'))
        require(len(snapshots) == 1 and digest(snapshots[0]) == resume['default_sha256'],
                'fresh previous snapshot mismatch')
        fresh = json.loads(snapshots[0].read_bytes())
        require(fresh['identity'] == resume['owner'] == resume_journal[-1]['owner']
                and fresh['pid'] == resume['owner']['pid'] != seed['pid']
                and fresh['identity']['binary_sha256'] == previous, 'resumed owner binding differs')
        final = load(root, base + '/root/som-rs/runtime/default.json')
        require(final['identity']['binary_sha256'] == previous and final['pid'] == row['stopped_pid']
                and final['identity']['pid'] == row['stopped_pid'] and final['identity']['port'] == port
                and not final.get('rollback_evidence_errors'), 'final receipt mismatch')
        for check in ('check',) + (('check-restored',) if name == 'switch' else ()):
            result = load(root, base + '/' + check + '/result.json')
            expected_hash = candidate if name == 'switch' and check == 'check' else previous
            require(result['passed'] is True and result['expect_binary_sha256'] == expected_hash
                    and set(result['checks']) == {'owner', 'health', 'pointers', 'previous_compiler_inputs',
                                                 'canaries', 'owner_after'}
                    and all(v['passed'] is True for v in result['checks'].values()), 'check result mismatch')
        totals['scenarios'] += 1
    exchanges = compare_exchanges(root, config, report, candidate, previous)
    return {'source_head': head, 'candidate_sha256': candidate, 'previous_sha256': previous,
            'counts': dict(totals), 'historical_pid_state': 'not_rechecked',
            'http_status_per_exchange': 'not_recorded', 'cli_exit_per_exchange': 'not_recorded',
            'independent_wait_transcript': 'not_recorded',
            'protected_ports_and_stopped_owners': 'producer_attestation',
            'producer_success_control_flow': 'source_bound_attestation_not_direct_wait_record',
            'raw_exchanges': exchanges}


def producer_receipt(identity, archive):
    """A non-secret, deterministic handoff between the two runner steps."""
    return {'version': 'som.m6-raw-audit-producer-binding.v1', 'identity': identity,
            'archive_bytes': archive.stat().st_size, 'audit_source_sha256': digest(Path(__file__))}


def verify_receipt(path, expected_sha, identity, archive):
    require(HASH.fullmatch(expected_sha) is not None and digest(path) == expected_sha,
            'producer binding receipt SHA mismatch')
    receipt = json.loads(path.read_bytes())
    require(receipt == producer_receipt(identity, archive)
            and digest(archive) == identity['artifact_sha256'], 'producer binding receipt differs')
    return receipt


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('fetch', 'inspect'), required=True)
    for name in ('archive', 'source', 'identity'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('restored', 'out'):
        p.add_argument('--' + name, type=Path)
    for name in ('run-id', 'attempt', 'job-id', 'artifact-id'):
        p.add_argument('--' + name, type=int, required=True)
    p.add_argument('--head', required=True)
    p.add_argument('--artifact-sha256', required=True)
    p.add_argument('--identity-sha256')
    args = p.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('GITHUB_REPOSITORY') == REPO, 'GitHub-hosted runner required')
    require(re.fullmatch(r'[0-9a-f]{40}', args.head) is not None, 'invalid source head')
    require(HASH.fullmatch(args.artifact_sha256) is not None, 'invalid artifact hash')
    archive, source = args.archive.resolve(), args.source.resolve()
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
    for path in (archive, args.identity.resolve()):
        require(runner_temp in path.parents, 'artifact and binding must remain under RUNNER_TEMP')
    require(archive.is_file() and source.is_dir() and digest(archive) == args.artifact_sha256, 'input SHA mismatch')
    check_checkout(source, args.head)
    expected = {'run_id': args.run_id, 'attempt': args.attempt, 'job_id': args.job_id,
                'artifact_id': args.artifact_id, 'head_sha': args.head, 'artifact_sha256': args.artifact_sha256}
    if args.phase == 'fetch':
        require('M6_INPUTS_KEYRING' not in os.environ, 'decryption key present in fetch step')
        require(args.identity_sha256 is None and not args.identity.exists(), 'fetch output is not fresh')
        identity = bind_github(args.run_id, args.attempt, args.job_id, args.artifact_id, args.head, archive)
        require(identity == expected, 'producer identity differs')
        raw = (json.dumps(producer_receipt(identity, archive), sort_keys=True, separators=(',', ':')) + '\n').encode()
        with args.identity.open('xb') as stream:
            stream.write(raw)
        print(json.dumps({'phase': 'fetch', 'identity_sha256': sha(raw), 'artifact_sha256': args.artifact_sha256}))
        return
    require(args.identity_sha256 is not None and args.restored is not None and args.out is not None,
            'inspect needs a pinned receipt and fresh paths')
    require('GH_TOKEN' not in os.environ and 'GITHUB_TOKEN' not in os.environ,
            'GitHub token present in inspect step')
    require(not args.identity.is_symlink() and args.identity.is_file(), 'binding must be a regular file')
    require(runner_temp in args.out.resolve().parents, 'audit output must remain under RUNNER_TEMP')
    verify_receipt(args.identity, args.identity_sha256, expected, archive)
    root = args.restored.resolve()
    require(not root.exists(), 'restore target is not fresh')
    restoration = decrypt_original_zip(archive, root, source)
    universe = verify_universe(archive, root)
    require(restoration['index_sha256'] == universe['index_sha256']
            and restoration['package_count'] == universe['packages']
            and restoration['file_count'] == universe['files'], 'restoration/universe mismatch')
    result = check_raw(root, source, args.head)
    report = {'version': 'som.m6-release-raw-audit-draft.v1', 'artifact': expected,
              'producer_binding_sha256': args.identity_sha256,
              'restoration': restoration, 'universe': universe, 'inspection': result, 'product_acceptance': False}
    require(not args.out.exists(), 'output already exists')
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
