#!/usr/bin/env python3
"""Restart a stopped, pinned previous compiler. Live use needs separate approval."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc
import switch


def check_approval(path, config, snapshot):
    approval = rc.load(path)
    expected = {'action': 'resume-default-8765-compiler', 'approved': True,
                'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                'script_sha256': rc.digest(__file__), 'common_sha256': rc.digest(rc.__file__),
                'prerequisites_sha256': rc.receipt_sha(config), 'default_sha256': rc.sha_bytes(snapshot)}
    for key, value in expected.items():
        rc.require(type(approval.get(key)) is type(value) and approval.get(key) == value, f'approval {key} differs')
    rc.require(isinstance(approval.get('quote'), str) and approval['quote'].strip(), 'approval has no quote')
    return approval


def state(config, root, port, rehearsal):
    rc.require(rc.compiler_profile(config), 'resume needs the explicit compiler-only profile')
    switch.check_place(root, port, rehearsal)
    rc.check_prerequisites(config)
    prepared = switch.current_state(root, port)
    _, _, current, _ = prepared
    rc.assert_current(config, current)
    pid = current['pid']
    rc.require(type(pid) is int and pid > 1 and pid != os.getpid(), 'stale PID must be a positive non-self PID')
    owner = current['identity']
    rc.require(owner['pid'] == pid and owner['argv'] == current['command'] and owner['port'] == port,
               'stale identity differs from its receipt')
    cwd = Path(owner['cwd'])
    rc.require(cwd.is_absolute() and cwd.is_dir(), 'previous working directory is unavailable')
    rc.require(not rc.alive(pid), 'recorded previous owner is still alive')
    return prepared


def plan(config, root, port, rehearsal=False):
    _, snapshot, current, _ = state(config, root, port, rehearsal)
    return {'version': 'som.compiler-default-resume-plan.v1', 'mutations': 0,
            'root': str(root), 'port': port, 'old_pid': current['pid'], 'old_pid_alive': False,
            'previous_binary_sha256': config['previous']['binary']['sha256'],
            'default_sha256': rc.sha_bytes(snapshot), 'prerequisites_sha256': rc.receipt_sha(config)}


def execute(config, root, port, output, approval=None, rehearsal=False):
    default_path, snapshot, current, pointers = state(config, root, port, rehearsal)
    approved = None if rehearsal else check_approval(approval, config, snapshot)
    rc.apply_formatter_env(config)
    rc.require(output.is_absolute(), 'fresh absolute output is required')
    output.mkdir(parents=True, exist_ok=False)
    run = rc.Release(output, current['identity']['cwd'])
    run.save('stale-default.json', snapshot)
    run.save('prerequisites.json', rc.canon(config).encode())
    stale_sha = rc.sha_bytes(snapshot)
    owner, committed = None, None
    result = {'version': 'som.compiler-default-resume.v1', 'passed': False, 'status': 'failed',
              'stale_snapshot_sha256': stale_sha, 'old_pid': current['pid'], 'old_pid_alive': False,
              'previous_binary_sha256': config['previous']['binary']['sha256'], 'rehearsal': rehearsal,
              'evidence_errors': [], 'cleanup_errors': [], 'stale_snapshot_restored': False}

    def unchanged_pointers():
        rc.require(rc.link_state(root / 'som-rs/runtime/current') == pointers['runtime']
                   and rc.link_state(root / 'som-rs/compiler-assets/current') == pointers['assets'], 'pointers changed externally')

    with (root / 'som-rs/runtime/.default-switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            rc.check_prerequisites(config)
            rc.require(default_path.read_bytes() == snapshot, 'default receipt changed externally')
            unchanged_pointers()
            rc.require(not rc.alive(current['pid']), 'recorded previous owner became alive')
            rc.port_free(port)
            run.event('stale_owner_absent', pid=current['pid'], alive=False, port=port,
                      stale_snapshot_sha256=stale_sha, pointers=pointers, approval=approved, rehearsal=rehearsal)
            run.event('start_intent', argv=current['command'], cwd=current['identity']['cwd'])
            owner = run.start(current['command'], current['health'])
            run.event('new_owner', owner=owner)
            rc.require(owner['pid'] != current['pid'], 'new owner must have a fresh PID')
            rc.assert_current(config, {**current, 'identity': owner})
            rc.require(rc.identity(owner['pid'], current['command'], port) == owner, 'new owner differs')
            run.health(port, current['health'])
            run.canary(port, {'previous': Path(current['command'][0])}, rc.previous_cases(config), 'resumed-previous-default')
            rc.require(not run.evidence_errors, 'resume evidence is incomplete')
            rc.check_prerequisites(config)
            unchanged_pointers()
            rc.require(default_path.read_bytes() == snapshot, 'default receipt changed externally')
            receipt = {**current, 'pid': owner['pid'], 'identity': owner, 'resume_evidence': str(output),
                       'process_logs': str(output / 'process-*.log'), 'resumed_default_sha256': stale_sha,
                       'resume_previous_pid': current['pid']}
            committed = (json.dumps(receipt, indent=2) + '\n').encode()
            rc.atomic_bytes(default_path, committed)
            run.event('resume_committed', owner=owner, stale_snapshot_sha256=stale_sha)
            rc.require(rc.identity(owner['pid'], current['command'], port) == owner, 'post-commit owner differs')
            run.health(port, current['health'])
            unchanged_pointers()
            rc.require(default_path.read_bytes() == committed, 'resumed receipt changed externally')
            rc.require(not run.evidence_errors, 'resume evidence is incomplete')
            result.update(passed=True, status='passed', owner=owner, default_sha256=rc.sha_bytes(committed))
            rc.atomic_bytes(output / 'result.json', (json.dumps(result, indent=2) + '\n').encode())
            run.event('resume_verified', owner=owner, stale_snapshot_sha256=stale_sha,
                      default_sha256=rc.sha_bytes(committed), evidence_errors=list(run.evidence_errors))
            return result
        except BaseException as error:
            run.recovering = True
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(signum, rc.rollback_defer_signal)
            run.event('failed', error=repr(error), stale_snapshot_sha256=stale_sha)
            cleanup_errors = []
            # Run.start handles startup failure through its own Popen handle.
            # Every owner here was admitted by this Run and must still match.
            owned = dict(run.owners)
            if owner is not None:
                owned[owner['pid']] = owner
            for admitted in owned.values():
                try:
                    if rc.alive(admitted['pid']):
                        run.stop_child(admitted)
                except BaseException as cleanup:
                    cleanup_errors.append(repr(cleanup))
            # Startup can fail before identity admission. Its Popen handle is
            # still ours, but no unverified PID may receive another signal.
            for child in run.children:
                try:
                    if child.poll() is None:
                        cleanup_errors.append(f'child handle {child.pid} remains alive; requires operator cleanup')
                except BaseException as cleanup:
                    cleanup_errors.append(f'child handle status is unavailable; requires operator cleanup: {cleanup!r}')
            try:
                unchanged_pointers()
                rc.require(default_path.read_bytes() in (snapshot, committed), 'default changed externally; stale restore refused')
                rc.require(not cleanup_errors, 'owned resumed process cleanup failed')
                if default_path.read_bytes() != snapshot:
                    rc.atomic_bytes(default_path, snapshot)
                result['stale_snapshot_restored'] = True
            except BaseException as cleanup:
                cleanup_errors.append(repr(cleanup))
            result.update(passed=False, status='failed', error=repr(error), cleanup_errors=cleanup_errors,
                          evidence_errors=list(run.evidence_errors))
            run.event('resume_failed', **result)
            try:
                rc.atomic_bytes(output / 'result.json', (json.dumps(result, indent=2) + '\n').encode())
            except OSError:
                pass
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prerequisites', 'root', 'output', 'approval'):
        parser.add_argument('--' + name, type=Path, required=name in ('prerequisites', 'root'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--rehearsal', action='store_true')
    args = parser.parse_args(argv)
    config, root = rc.load(args.prerequisites), args.root.resolve()
    if not args.execute:
        print(json.dumps(plan(config, root, args.port, args.rehearsal)))
        return 0
    rc.require(args.output is not None and args.output.is_absolute(), 'fresh absolute output is required')
    rc.require(args.rehearsal or args.approval is not None, 'live resume needs its separate approval')
    def interrupted(signum, _frame):
        raise RuntimeError(f'interrupted by signal {signum}')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    print(json.dumps(execute(config, root, args.port, args.output, args.approval, args.rehearsal)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
