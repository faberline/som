#!/usr/bin/env python3
"""Collect missing Rust006 checks on the current Mac candidate; not acceptance.

The outer driver owns the candidate. This helper never starts or stops it. It
records protocol and process data under the caller's repo-local run. Independent
raw audit and outer service cleanup remain required.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RECORDER = HERE / 'record_rust006_http.py'
CLUSTER = HERE / 'mac_rust006_cluster.py'
REPLAY = REPO / 'som-rs/scripts/rust_output_acceptance/python_replay.py'
CASES = REPO / 'som-rs/tests/rust-output-v1/validate_cases.py'
PRODUCTS = REPO / 'som-rs/som-targets/qa/check_rust_products.py'
QA = 'http://127.0.0.1:18919'
ENV = ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'CI', 'SDKROOT',
       'DEVELOPER_DIR', 'CARGO_HOME', 'RUSTUP_HOME', 'GITHUB_ACTIONS',
       'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH', 'RUNNER_TEMP',
       'CARGO_NET_OFFLINE', 'PYTHONDONTWRITEBYTECODE', 'SOM_TS_PRETTIER')


def need(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def physical(path, *, exists=True):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
         'absolute physical path required')
    if exists:
        need(path.exists() and path.resolve() == path, 'physical path missing')
    return path


def read(path):
    path = physical(path)
    need(path.is_file() and path.stat().st_size <= 128 * 1024 * 1024, 'bounded regular input required')
    return json.loads(path.read_bytes())


def git(*args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), *args], timeout=30).strip()


def record_command(out, index, name, argv, cwd, env, timeout=7200):
    stdout, stderr = out / 'logs' / (name + '.stdout'), out / 'logs' / (name + '.stderr')
    row = {'index': index, 'name': name, 'argv': [str(v) for v in argv], 'cwd': str(cwd),
           'exit_code': None, 'wait_result': None, 'state': 'not_started', 'cleanup': 'unverified',
           'environment': dict(env)}
    start = time.monotonic()
    try:
        with stdout.open('xb') as out_stream, stderr.open('xb') as err_stream:
            proc = subprocess.Popen(row['argv'], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=out_stream, stderr=err_stream, start_new_session=True)
            row.update(pid=proc.pid, state='running')
            try:
                row['wait_result'] = proc.wait(timeout=timeout)
                row.update(exit_code=proc.returncode, state='completed')
                # Direct child wait does not prove daemon or descendant cleanup.
                row['cleanup'] = 'requires-independent-raw-audit'
            except subprocess.TimeoutExpired:
                row['state'] = 'timeout'
                try: os.killpg(proc.pid, signal.SIGINT)
                except ProcessLookupError: pass
                try: row['cleanup_wait_result'] = proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    try: os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    row['cleanup_wait_result'] = proc.wait(timeout=30)
                row['exit_code'] = proc.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        row['error_type'] = type(exc).__name__
        if row['state'] != 'timeout': row['state'] = 'process-error'
    finally:
        row['seconds'] = time.monotonic() - start
        for key, path in (('stdout', stdout), ('stderr', stderr)):
            if path.is_file():
                row[key] = str(path.relative_to(out))
                row[key + '_sha256'] = sha(path)
    return row


def observe(out, phase, runtime):
    pid, port = runtime['pid'], runtime['port']
    commands = {'ps': ['/bin/ps', '-ww', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,command='],
                'cwd': ['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                'listener': ['/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t']}
    obs = {'pid': pid, 'port': port, 'commands': {}}
    for name, argv in commands.items():
        p = subprocess.run(argv, capture_output=True, timeout=30, check=False)
        record = {'argv': argv, 'exit_code': p.returncode}
        for key in ('stdout', 'stderr'):
            path = out / 'logs' / f'{phase}-{name}.{key}'
            path.write_bytes(getattr(p, key))
            record[key] = str(path.relative_to(out))
            record[key + '_sha256'] = sha(path)
        obs['commands'][name] = record
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(QA + '/healthz', timeout=30) as resp:
        raw = resp.read()
        path = out / 'logs' / f'{phase}-health.raw'
        path.write_bytes(raw)
        obs['health'] = {'status': resp.status, 'file': str(path.relative_to(out)), 'sha256': sha(path)}
    return obs


def main():
    p = argparse.ArgumentParser()
    for name in ('candidate', 'out', 'public-run', 'legacy-run', 'parity', 'python'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--candidate-revision', required=True)
    args = p.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS' and platform.system() == 'Darwin'
         and platform.machine() == 'arm64', 'GitHub Mac arm64 required')
    need(re.fullmatch('[0-9a-f]{40}', args.candidate_revision) and
         git('rev-parse', '--verify', 'HEAD^{commit}').decode() == args.candidate_revision and
         os.environ.get('GITHUB_SHA') == args.candidate_revision, 'exact dispatch source required')
    candidate, out = physical(args.candidate), physical(args.out, exists=False)
    need(out.is_relative_to(REPO / '.local') and not out.exists(), 'fresh repo-local output required')
    for item in (args.public_run, args.legacy_run, args.parity): physical(item)
    python = Path(os.path.abspath(args.python))
    need(python == REPO / 'som-rs/runtime/acceptance-m3-venv/bin/python' and python.is_file()
         and not any(parent.is_symlink() for parent in python.parents), 'frozen Mac venv interpreter required')
    build, runtime, source = (read(candidate / x) for x in ('build.json', 'runtime.json', 'source.json'))
    digest = sha(candidate / 'som')
    need(build.get('complete') is True and build.get('source_unchanged') is True
         and build.get('tree_before', {}).get('head') == args.candidate_revision == source.get('commit')
         and runtime.get('port') == 18919 and type(runtime.get('pid')) is int and runtime['pid'] > 1
         and runtime.get('accepted') is False and build.get('binary_sha256') == runtime.get('binary_sha256') == digest
         and sha(candidate / 'source.json') == build.get('source_sha256') == runtime.get('source_sha256')
         and sha(candidate / 'source.tar') == source.get('archive_sha256')
         and sha(candidate / 'assets/manifest.json') == build.get('asset_manifest_sha256'), 'candidate identity differs')
    out.mkdir(parents=True, mode=0o700)
    (out / 'logs').mkdir(mode=0o700)
    env = {k: os.environ[k] for k in ENV if k in os.environ}
    env.update(CARGO_NET_OFFLINE='true', PYTHONDONTWRITEBYTECODE='1')
    report = {'version': 'som.m6-mac-rust006-extra.v1', 'candidate_revision': args.candidate_revision,
              'candidate_binary_sha256': digest, 'candidate_dir': str(candidate),
              'source_sha256': sha(candidate / 'source.json'), 'parity_sha256': sha(args.parity),
              'runner': {key: os.environ.get(key) for key in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT',
                         'GITHUB_ACTIONS','RUNNER_ENVIRONMENT','RUNNER_OS','RUNNER_ARCH')},
              'public_result_sha256': sha(args.public_run / 'result.json'),
              'legacy_result_sha256': sha(args.legacy_run / 'result.json'), 'rows': [],
              'execution_complete': False, 'product_acceptance_passed': False,
              'independent_audit': 'not-run', 'cleanup': 'requires-independent-raw-audit'}
    try:
        report['candidate_before'] = observe(out, 'before', runtime)
        if report['candidate_before']['health']['status'] != 200:
            raise ValueError('candidate health failed')
        commands = [
            ('python-replay', [args.python, RECORDER, '--out', out / 'raw-python-replay', '--', REPLAY,
             '--asset-break', '--base-url', QA, '--public-run', args.public_run, '--legacy-run', args.legacy_run,
             '--out', out / 'python-replay.json']),
            ('validate-cases', [args.python, RECORDER, '--out', out / 'raw-validate-cases', '--', CASES,
             '--base-url', QA, '--out', out / 'validate-cases.json']),
            ('rust-refusals', [args.python, CLUSTER, '--raw-out', out / 'raw-rust-refusals', '--lifecycle', out / 'cluster-lifecycle',
             '--cluster-root', out / 'cluster-work', '--', '--qa', QA, '--work', out / 'rust-products-work',
             '--target-dir', out / 'rust-target', '--out', out / 'rust-products.json', '--no-gates',
             '--refusals', 'phase2', '--parity', args.parity]),
        ]
        for index, (name, command) in enumerate(commands, 1):
            row = record_command(out, index, name, command, REPO, env)
            report['rows'].append(row)
            if row['state'] != 'completed' or row['wait_result'] != 0 or row['exit_code'] != 0:
                break
        report['candidate_after'] = observe(out, 'after', runtime)
        report['execution_complete'] = (len(report['rows']) == 3 and all(
            row['state'] == 'completed' and type(row['wait_result']) is int and row['wait_result'] == row['exit_code'] == 0
            for row in report['rows']) and sha(candidate / 'som') == digest and
            git('rev-parse', '--verify', 'HEAD^{commit}').decode() == args.candidate_revision)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        report['error_type'] = type(exc).__name__
    finally:
        (out / 'collector.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    print(json.dumps({'execution_complete': report['execution_complete'], 'product_acceptance_passed': False}))
    return 0 if report['execution_complete'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
