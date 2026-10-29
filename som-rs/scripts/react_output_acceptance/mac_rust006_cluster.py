#!/usr/bin/env python3
"""Record the Rust006 refusal checker inside a temporary Mac QA PostgreSQL cluster.

Unsealed draft. It is not an acceptance verdict. On timeout or incomplete
process evidence, the caller must mark cleanup unverified and fail closed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CHECKER = REPO / 'som-rs/som-targets/qa/check_rust_products.py'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def need(value, message):
    if not value:
        raise ValueError(message)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw-out', type=Path, required=True)
    parser.add_argument('--lifecycle', type=Path, required=True)
    parser.add_argument('--cluster-root', type=Path, required=True)
    parser.add_argument('checker_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS', 'GitHub macOS runner required')
    argv = args.checker_args[1:] if args.checker_args[:1] == ['--'] else args.checker_args
    need(argv and '--pg' not in argv and '--no-gates' in argv and
         argv.count('--refusals') == 1 and argv[argv.index('--refusals') + 1] == 'phase2',
         'exact Rust006 refusal checker required')
    paths = [Path(os.path.abspath(p)) for p in (args.raw_out, args.lifecycle, args.cluster_root)]
    need(all(p.is_relative_to(REPO / '.local') and not p.exists() and not p.is_symlink()
             and not any(parent.is_symlink() for parent in p.parents) for p in paths), 'new owned paths required')
    raw, lifecycle, cluster_root = paths
    lifecycle.mkdir(parents=True, mode=0o700)
    cluster_root.mkdir(parents=True, mode=0o700)
    sys.path.insert(0, str(REPO / 'som-rs/behavior'))
    import hosts
    import record_rust006_http
    from mac_parity_capture import TrackingRecorder
    observer = TrackingRecorder(lifecycle)
    toolchain = json.loads((REPO / 'som-rs/contracts/rust-output-v1/toolchain.json').read_bytes())['postgresql']
    tool_dir = Path(toolchain['bin_dir'])
    retained_tools = lifecycle / 'postgresql-tools'
    retained_tools.mkdir(mode=0o700)
    tool_observations = {}
    for name in ('postgres', 'initdb', 'pg_ctl'):
        source, target = tool_dir / name, retained_tools / name
        need(source.is_file() and sha(source) == toolchain[name]['sha256'], 'PostgreSQL tool differs')
        shutil.copyfile(source, target, follow_symlinks=True)
        need(sha(source) == sha(target) == toolchain[name]['sha256'], 'retained PostgreSQL tool differs')
        tool_observations[name] = {'source': str(source), 'file': str(target.relative_to(lifecycle)),
                                   'sha256': sha(target), 'source_before': sha(source)}
    code = 1
    port = None
    try:
        port = hosts.Ports().take()
        need(port not in (8765, 18765, 18917, 18919), 'protected or compiler port refused')
        with mock.patch.object(subprocess, 'run', observer.run), mock.patch.object(subprocess, 'Popen', observer.popen):
            with hosts.cluster(cluster_root, port) as pg:
                code = record_rust006_http.run_checker(raw, CHECKER, ['--pg', pg, *argv])
    finally:
        starts = [event for event in observer.events if event.get('kind') == 'postmaster' and event.get('phase') == 'after-start']
        if starts:
            try:
                item = starts[-1]['raw']
                pid = int((lifecycle / item['file']).read_bytes().splitlines()[0])
                observer.snapshot(pid, 'after-stop', port)
            except Exception as exc:
                observer.errors.append(type(exc).__name__)
        else:
            observer.errors.append('PostmasterNotObserved')
        for name in tool_observations:
            try:
                tool_observations[name]['source_after'] = sha(tool_dir / name)
                if tool_observations[name]['source_after'] != tool_observations[name]['sha256']:
                    observer.errors.append('PostgresqlToolChanged')
            except OSError as exc:
                observer.errors.append(type(exc).__name__)
        receipt = {'version': 'som.m6-mac-rust006-cluster.v1', 'port': port, 'exit_code': code,
                   'events': observer.events, 'recording_errors': observer.errors, 'tools': tool_observations,
                   'checker_sha256': sha(CHECKER), 'product_acceptance_passed': False,
                   'cleanup_claim': 'requires-independent-raw-audit'}
        (lifecycle / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return code if not observer.errors else 1

if __name__ == '__main__':
    raise SystemExit(main())
