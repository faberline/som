#!/usr/bin/env python3
"""#51 compiler-to-compiler default switch. Default mode checks files and prints a plan.

--execute switches the default named by ROOT/som-rs/runtime/default.json on
PORT to the prerequisites candidate:

1. The current owner must equal the receipt identity and health.
2. Python canaries on the current default (read-only).
3. A fresh legacy M2 clone on a harness port: full health and generate canaries.
4. A fresh clone of the current command on a harness port: health and Python canaries.
5. A fresh candidate on a harness port: health, Python and Rust canaries.
6. Install the binary by hash, stop the verified owner (SIGTERM only), start the
   installed binary on PORT, run every canary, set runtime/current, check again
   and write the v2 default receipt.
7. Any failure after the stop intent restores the previous command, pointer and
   receipt and runs the Python canaries on it.

--restore EVIDENCE is the recovery command after a committed switch: it puts
the previous default of that switch back the same way.

The live default (ROOT is the repository, PORT 8765) needs --approval. A
rehearsal (--rehearsal) uses a sandbox ROOT under .local and a harness port and
may inject a failure. Every byte exchanged is saved in the fresh --output.

usage: switch.py --prerequisites FILE [--root DIR --port N --output NEW_DIR
                 (--execute | --restore EVIDENCE) [--approval FILE | --rehearsal [--inject-failure STAGE]]]
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc  # noqa: E402

STAGES = ('after_stop', 'after_start', 'after_pointer', 'after_commit')
SANDBOX_PARENT = rc.REPO / '.local/rust-compiler-goal/acceptance'


def inject(selected, stage):
    if selected == stage:
        raise RuntimeError(f'injected failure at {stage}')


def check_approval(path, action, config, snapshot):
    approval = rc.load(path)
    expected = {'action': action, 'approved': True,
                'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                'script_sha256': rc.digest(__file__), 'common_sha256': rc.digest(rc.__file__),
                'prerequisites_sha256': rc.receipt_sha(config), 'default_sha256': rc.sha_bytes(snapshot)}
    for key, value in expected.items():
        rc.require(approval.get(key) == value, f'approval {key} differs')
    rc.require(isinstance(approval.get('quote'), str) and approval['quote'], 'approval has no quote')
    return approval


def check_place(root, port, rehearsal):
    if rehearsal:
        rc.harness_port(port)
        rc.require(root != rc.REPO and SANDBOX_PARENT in root.parents, 'a rehearsal root lives under .local acceptance')
    else:
        rc.require(root == rc.REPO and port == 8765, 'the live default is the repository on 8765')


def install(binary, root):
    destination = root / 'som-rs/runtime/versions' / rc.digest(binary) / 'som'
    destination.parent.mkdir(parents=True, exist_ok=True)
    rc.require(not destination.is_symlink(), 'version destination must not be a symlink')
    if not destination.exists():
        temp = destination.with_name(destination.name + f'.stage-{os.getpid()}')
        shutil.copy2(binary, temp)
        os.rename(temp, destination)
    rc.require(rc.digest(destination) == rc.digest(binary), 'installed binary differs')
    return destination


def current_state(root, port):
    default_path = root / 'som-rs/runtime/default.json'
    snapshot = default_path.read_bytes()
    current = json.loads(snapshot)
    rc.validate_argv(current['command'])
    rc.require(current['version'] == 'som.default-runtime.v2' and current['accepted'] is True, 'not an accepted receipt')
    rc.require(current['command'][1:3] == ['serve', '--assets'] and current['command'][4:] == ['--port', str(port)],
               'receipt is not a compiler command on this port')
    rc.require(rc.digest(current['command'][0]) == current['identity']['binary_sha256'], 'default binary differs')
    pointers = {'runtime': rc.link_state(root / 'som-rs/runtime/current'),
                'assets': rc.link_state(root / 'som-rs/compiler-assets/current')}
    rc.require(pointers['runtime'] == {'kind': 'symlink', 'target': str(Path(current['command'][0]).parent)},
               'runtime/current is not the default binary')
    rc.require(pointers['assets'] == {'kind': 'symlink', 'target': current['command'][3]},
               'compiler-assets/current is not the default assets')
    return default_path, snapshot, current, pointers


def bring_back(run, root, port, previous, pointers, stop, binaries, python):
    """Stop what serves PORT for this transaction and restore the previous default."""
    for owner in stop:
        if owner is not None and rc.alive(owner['pid']):
            if any(child.pid == owner['pid'] for child in run.children):
                run.stop_child(owner)
            else:
                rc.finish_stop(owner)
    rc.atomic_link(root / 'som-rs/runtime/current', pointers['runtime'])
    rc.require(rc.link_state(root / 'som-rs/compiler-assets/current') == pointers['assets'],
               'compiler-assets/current changed externally')
    restored = run.start(previous['command'], previous['health'])
    run.health(port, previous['health'])
    run.canary(port, binaries, python, 'restored-default')
    return restored


def execute(config, root, port, output, approval, rehearsal, selected):
    runtime, legacy = rc.check_prerequisites(config)
    default_path, snapshot, current, pointers = current_state(root, port)
    if not rehearsal:
        approval = check_approval(approval, 'switch-default-8765-rust-output', config, snapshot)
    candidate = Path(config['candidate']['binary']['path'])
    previous_binary = Path(current['command'][0])
    assets = Path(current['command'][3])
    rc.assets_match(assets, current['health'])
    rc.assets_match(assets, runtime['health'])
    python, rust = config['python_canaries'], config['rust_canaries']
    output.mkdir(parents=True, exist_ok=False)
    run = rc.Release(output, current['identity']['cwd'])
    run.save('prerequisites.json', rc.canon(config).encode())
    run.save('previous-default.json', snapshot)
    run.event('pointers', pointers=pointers, approval=approval if not rehearsal else None,
              rehearsal=rehearsal, inject=selected)
    with (root / 'som-rs/runtime/.default-switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = rc.identity(current['pid'], current['command'], port)
        rc.require(owner == current['identity'], 'live owner differs from the receipt')
        run.health(port, current['health'])
        clients = {'previous': previous_binary, 'new': candidate, 'legacy': Path(config['legacy_binary']['path'])}
        run.canary(port, clients, python, 'current-default')
        harness = set() if not rehearsal else {port}
        for argv, health, cases in [
                (legacy['command'][:-1], legacy['health'], None),
                (current['command'][:-1], current['health'], python),
                ([str(candidate), 'serve', '--assets', str(assets), '--port'], runtime['health'], python + rust)]:
            clone = run.start(argv + [str(rc.free_harness_port(harness))], health)
            try:
                if cases is None:
                    run.m2_canary(clone['port'], clients, config['m2_canaries'])
                else:
                    run.canary(clone['port'], clients, cases, 'clone')
            finally:
                run.stop_child(clone)
        installed = install(candidate, root)
        run.event('immutable_install_verified', binary=str(installed))
        argv = [str(installed), 'serve', '--assets', str(assets), '--port', str(port)]
        served = {'previous': previous_binary, 'new': installed}
        changed, new_owner, committed = False, None, None
        try:
            rc.require(default_path.read_bytes() == snapshot, 'default receipt changed externally')
            rc.require(rc.identity(owner['pid'], owner['argv'], port) == owner, 'owner changed before stop')
            changed = True
            run.event('stop_intent', owner=owner)
            rc.terminate(owner)
            inject(selected, 'after_stop')
            new_owner = run.start(argv, runtime['health'])
            run.event('new_default_owner', owner=new_owner)
            inject(selected, 'after_start')
            run.canary(port, served, python + rust, 'new-default')
            rc.require(rc.link_state(root / 'som-rs/runtime/current') == pointers['runtime'], 'pointer changed externally')
            rc.atomic_link(root / 'som-rs/runtime/current', {'kind': 'symlink', 'target': str(installed.parent)})
            run.event('pointer_set', name='runtime', target=str(installed.parent))
            inject(selected, 'after_pointer')
            run.health(port, runtime['health'])
            run.canary(port, served, python + rust, 'new-default-after-pointer')
            rc.require(rc.identity(new_owner['pid'], argv, port) == new_owner, 'new owner changed')
            rc.require(default_path.read_bytes() == snapshot, 'default receipt changed externally')
            receipt = {'version': 'som.default-runtime.v2', 'pid': new_owner['pid'], 'command': argv,
                       'health': runtime['health'], 'identity': new_owner, 'accepted': True,
                       'process_logs': str(output / 'process-*.log'), 'evidence': str(output),
                       'previous_default_sha256': rc.sha_bytes(snapshot)}
            committed = (json.dumps(receipt, indent=2) + '\n').encode()
            rc.atomic_bytes(default_path, committed)
            run.event('committed', receipt=receipt)
            inject(selected, 'after_commit')
        except BaseException as failure:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(signum, rc.rollback_defer_signal)
            run.recovering = True
            run.event('failed', error=repr(failure), rollback_required=changed)
            for child in list(run.owners.values()):
                if child['port'] != port and rc.alive(child['pid']):
                    try:
                        run.stop_child(child)
                    except BaseException as cleanup:
                        run.event('OWNED_CLONE_CLEANUP_FAILED', owner=child, error=repr(cleanup))
            if changed:
                try:
                    admitted = [o for o in run.owners.values() if o['port'] == port and o['argv'][0] == str(installed)]
                    restored = bring_back(run, root, port, current, pointers, admitted + [owner],
                                          {'previous': previous_binary, 'new': candidate}, python)
                    rc.require(default_path.read_bytes() in (snapshot, committed), 'default receipt changed externally')
                    rollback = {**current, 'pid': restored['pid'], 'identity': restored,
                                'process_logs': str(output / 'process-*.log'), 'rollback_evidence': str(output),
                                'rollback_from': new_owner, 'rolled_back_default_sha256': rc.sha_bytes(snapshot),
                                'rollback_evidence_errors': list(run.evidence_errors)}
                    rc.atomic_bytes(default_path, (json.dumps(rollback, indent=2) + '\n').encode())
                    run.event('rollback_verified', owner=restored, evidence_errors=list(run.evidence_errors))
                except BaseException as rollback_failure:
                    run.event('ROLLBACK_FAILED_REQUIRES_OPERATOR', error=repr(rollback_failure))
                    raise RuntimeError(f'rollback failed: {rollback_failure}; original failure: {failure}') \
                        from rollback_failure
            raise


def restore(config, root, port, output, approval, rehearsal, evidence):
    """Recovery: put back the previous default of a committed switch."""
    rc.check_prerequisites(config)
    default_path, snapshot, current, pointers = current_state(root, port)
    rc.require(Path(current['evidence']) == evidence, 'the default was not committed by this evidence')
    journal = rc.load(evidence / 'journal.json')
    rc.require(journal[-1]['stage'] == 'committed', 'evidence has no committed switch')
    previous_bytes = next(p for p in sorted(evidence.iterdir()) if p.name.endswith('-previous-default.json')).read_bytes()
    rc.require(rc.sha_bytes(previous_bytes) == current['previous_default_sha256'], 'previous receipt differs')
    previous = json.loads(previous_bytes)
    previous_pointers = journal[0]['pointers']
    if not rehearsal:
        approval = check_approval(approval, 'restore-default-8765-rust-output', config, snapshot)
    rc.require(rc.digest(previous['command'][0]) == previous['identity']['binary_sha256'], 'previous binary differs')
    rc.assets_match(Path(previous['command'][3]), previous['health'])
    output.mkdir(parents=True, exist_ok=False)
    run = rc.Release(output, previous['identity']['cwd'])
    run.save('current-default.json', snapshot)
    run.event('restore_admitted', evidence=str(evidence), approval=approval if not rehearsal else None)
    with (root / 'som-rs/runtime/.default-switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = rc.identity(current['pid'], current['command'], port)
        rc.require(owner == current['identity'], 'live owner differs from the receipt')
        run.event('stop_intent', owner=owner)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, rc.rollback_defer_signal)
        run.recovering = True
        restored = bring_back(run, root, port, previous, previous_pointers, [owner],
                              {'previous': Path(previous['command'][0]), 'new': Path(current['command'][0])},
                              config['python_canaries'])
        rc.require(default_path.read_bytes() == snapshot, 'default receipt changed externally')
        receipt = {**previous, 'pid': restored['pid'], 'identity': restored,
                   'process_logs': str(output / 'process-*.log'), 'restore_evidence': str(output),
                   'restored_from': owner, 'replaced_default_sha256': rc.sha_bytes(snapshot)}
        rc.atomic_bytes(default_path, (json.dumps(receipt, indent=2) + '\n').encode())
        run.event('restore_verified', owner=restored, evidence_errors=list(run.evidence_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=rc.REPO)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--rehearsal', action='store_true')
    parser.add_argument('--inject-failure', choices=STAGES)
    args = parser.parse_args()
    config = rc.load(args.prerequisites)
    root = args.root.resolve()
    check_place(root, args.port, args.rehearsal)
    rc.require(args.inject_failure is None or (args.rehearsal and args.execute), 'failures are injected in rehearsals only')
    if not args.execute and args.restore is None:
        rc.check_prerequisites(config)
        _, snapshot, current, pointers = current_state(root, args.port)
        print(json.dumps({'mode': 'plan', 'mutations': 0, 'service_calls': 0,
                          'prerequisites_sha256': rc.receipt_sha(config), 'default_sha256': rc.sha_bytes(snapshot),
                          'script_sha256': rc.digest(__file__), 'common_sha256': rc.digest(rc.__file__),
                          'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                          'current_pid': current['pid'], 'pointers': pointers,
                          'plan': (__doc__ or '').split('\n\n')[1]}, indent=1))
        return
    rc.require(args.output is not None and args.output.is_absolute(), 'a fresh absolute --output is required')
    rc.require(args.rehearsal or args.approval is not None, 'the live default needs --approval')

    def interrupted(signum, frame):
        raise RuntimeError(f'interrupted by signal {signum}')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    if args.restore is not None:
        restore(config, root, args.port, args.output, args.approval, args.rehearsal, args.restore.resolve())
    else:
        execute(config, root, args.port, args.output, args.approval, args.rehearsal, args.inject_failure)
    print(json.dumps({'output': str(args.output), 'passed': True}))


if __name__ == '__main__':
    main()
