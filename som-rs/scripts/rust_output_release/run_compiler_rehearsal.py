#!/usr/bin/env python3
"""Run the #131 compiler switch and rollback rehearsal on a GitHub-hosted runner."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc
import rehearsal_supervision as supervision

HERE = Path(__file__).resolve().parent
SCENARIOS = ('switch', 'after_stop', 'after_start', 'after_pointer', 'after_commit')


def protected_ports_absent():
    """Read listener metadata only. Never bind, listen, connect or signal."""
    observations = []
    for port in sorted(rc.PROTECTED_PORTS):
        argv = ['/usr/sbin/lsof', '-nP', '-a', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fp']
        done = subprocess.run(argv, capture_output=True, timeout=30)
        rc.require(done.returncode == 1 and done.stdout == b'' and done.stderr == b'',
                   f'cannot confirm protected port {port} has no listener')
        observations.append({'port': port, 'argv': argv, 'exit_code': done.returncode,
                             'stdout_empty': True, 'stderr_empty': True})
    return observations


def complete_journal(journal):
    rc.require(isinstance(journal, list) and bool(journal), 'journal is missing')
    for event in journal:
        rc.require(not event.get('evidence_errors'), 'journal has evidence write errors')
        rc.require(event['stage'] not in ('OWNED_CLONE_CLEANUP_FAILED', 'ROLLBACK_FAILED_REQUIRES_OPERATOR'),
                   'journal reports a cleanup or rollback failure')


def verify_checks(path, expected_sha):
    result = rc.load(path)
    rc.require(result['passed'] is True and result['expect_binary_sha256'] == expected_sha,
               'independent default check failed or checked another binary')
    rc.require(set(result['checks']) == {'owner', 'health', 'pointers', 'previous_compiler_inputs', 'canaries', 'owner_after'},
               'default check set differs')
    rc.require(all(row['passed'] is True for row in result['checks'].values()), 'an independent default check failed')


def verify_resume(directory, row, config):
    seed, call = row['offline_seed'], row['resume']
    rc.require(type(seed['pid']) is int and seed['pid'] > 1 and seed['old_pid_alive'] is False
               and seed['exit_code'] == 0, 'offline seed is not a successful reaped child')
    rc.require(seed['argv'][1:] == ['-c', 'import os; print(os.getpid())'], 'offline seed command differs')
    rc.require(seed['receipt'] == 'stand-in/stale-default.json'
               and seed['stdout_file'] == 'stand-in/seed.stdout' and seed['stderr_file'] == 'stand-in/seed.stderr',
               'offline seed paths differ')
    for stream in ('stdout', 'stderr'):
        path = directory / seed[stream + '_file']
        rc.require(rc.digest(path) == seed[stream + '_sha256'], 'offline seed stream changed')
        rc.require(path.read_bytes() == (f'{seed["pid"]}\n'.encode() if stream == 'stdout' else b''),
                   'offline seed output is not its completed PID')
    rc.require(rc.digest(directory / seed['receipt']) == seed['default_sha256'], 'offline stale receipt changed')
    stale = rc.load(directory / seed['receipt'])
    rc.require(stale['pid'] == seed['pid'] and stale['identity']['binary_sha256'] == config['previous']['binary']['sha256'],
               'offline seed names another previous compiler')
    rc.require(call['name'] == 'resume' and call['exit_code'] == 0
               and type(call.get('wait_result')) is int and call['wait_result'] == 0,
               'offline resume did not succeed or its wait is missing')
    for stream in ('stdout', 'stderr'):
        rc.require(rc.digest(directory / f'resume.{stream}') == call[stream + '_sha256'], 'resume subprocess stream changed')
    journal = rc.load(directory / 'resume/journal.json')
    complete_journal(journal)
    rc.require(not any(event['stage'] in ('failed', 'resume_failed') for event in journal), 'resume journal reports a failure')
    rc.require(journal[-1]['stage'] == 'resume_verified', 'resume did not verify')
    result = rc.load(directory / 'resume/result.json')
    rc.require(result['passed'] is True and result['status'] == 'passed'
               and result['old_pid'] == seed['pid'] and result['old_pid_alive'] is False
               and result['stale_snapshot_sha256'] == seed['default_sha256']
               and result['previous_binary_sha256'] == config['previous']['binary']['sha256']
               and not result.get('evidence_errors') and not result.get('cleanup_errors'), 'resume proof failed')
    absent = [event for event in journal if event['stage'] == 'stale_owner_absent']
    rc.require(len(absent) == 1 and absent[0]['pid'] == seed['pid'] and absent[0]['alive'] is False
               and absent[0]['port'] == row['port'], 'resume did not prove the stale owner absent')
    snapshots = list((directory / 'switch').glob('*-previous-default.json'))
    rc.require(len(snapshots) == 1 and rc.digest(snapshots[0]) == result['default_sha256'],
               'switch did not use the freshly resumed receipt')
    fresh = rc.load(snapshots[0])
    rc.require(rc.exact_json(fresh['identity'], result['owner'])
               and rc.exact_json(journal[-1]['owner'], result['owner'])
               and fresh['pid'] == result['owner']['pid'] and fresh['pid'] != seed['pid']
               and fresh['identity']['binary_sha256'] == config['previous']['binary']['sha256'],
               'resumed owner differs from the switch input')
    expected = [case['name'] for family in ('python', 'rust', 'typescript')
                for case in config['previous_canaries'][family]]
    actual = [event['name'] for event in journal if event['stage'] == 'canary_passed'
              and event.get('label') == 'resumed-previous-default']
    rc.require(actual == expected, 'resume did not run every previous canary')
    rc.require(not rc.alive(seed['pid']), 'offline seed PID is alive')


def verify_report(directory, config):
    report = rc.load(directory / 'rehearsal.json')
    rc.require(report['passed'] is True and report['profile'] == 'compiler-only'
               and report['live_default_accessed'] is False, 'compiler rehearsal failed')
    rc.require([row['name'] for row in report['scenarios']] == list(SCENARIOS), 'rehearsal scenario set differs')
    for row in report['scenarios']:
        name = row['name']
        rc.require(row['passed'] is True and row['problems'] == [], 'rehearsal scenario failed')
        rc.harness_port(row['port'])
        root = directory / name
        rc.require(row['root'] == str(root / 'root'), 'scenario root differs')
        verify_resume(root, row, config)
        journal = rc.load(root / 'switch/journal.json')
        complete_journal(journal)
        final = 'committed' if name == 'switch' else 'rollback_verified'
        rc.require(journal[-1]['stage'] == final, 'switch final journal stage differs')
        calls = row['calls']
        rc.require([c['name'] for c in calls] == (['switch', 'check', 'restore', 'check-restored']
                   if name == 'switch' else ['switch', 'check']), 'scenario call sequence differs')
        rc.require(all(type(c.get('wait_result')) is int and c['wait_result'] == c['exit_code'] for c in calls),
                   'scenario command wait is missing or differs')
        rc.require(all(c['exit_code'] == 0 for c in calls[1:]), 'check or restore failed')
        rc.require((calls[0]['exit_code'] == 0) == (name == 'switch'), 'switch exit differs')
        for call in calls:
            for stream in ('stdout', 'stderr'):
                rc.require(rc.digest(root / f'{call["name"]}.{stream}') == call[stream + '_sha256'], 'subprocess stream changed')
        expected = config['candidate' if name == 'switch' else 'previous']['binary']['sha256']
        verify_checks(root / 'check/result.json', expected)
        if name == 'switch':
            restore = rc.load(root / 'restore/journal.json')
            complete_journal(restore)
            rc.require(restore[-1]['stage'] == 'restore_verified', 'restore journal did not verify')
            verify_checks(root / 'check-restored/result.json', config['previous']['binary']['sha256'])
        else:
            rc.require(any(event['stage'] == 'failed' and f'injected failure at {name}' in event['error']
                           for event in journal), 'the intended failure did not occur')
        receipt = rc.load(root / 'root/som-rs/runtime/default.json')
        rc.require(not receipt.get('rollback_evidence_errors'), 'restored receipt has evidence write errors')
        rc.require(receipt['identity']['binary_sha256'] == config['previous']['binary']['sha256'], 'scenario did not end on previous compiler')
        rc.require(receipt['pid'] == row['stopped_pid'] and not rc.alive(receipt['pid']), 'scenario owner remains alive')
        rc.port_free(row['port'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--audit-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mac-accepted-receipt', type=Path)
    parser.add_argument('--mac-accepted-receipt-sha256')
    parser.add_argument('--mac-candidate-revision')
    args = parser.parse_args()
    mac_values = (args.mac_accepted_receipt, args.mac_accepted_receipt_sha256, args.mac_candidate_revision)
    rc.require(all(value is None for value in mac_values) or all(value is not None for value in mac_values),
               'all final Mac acceptance inputs are required together')
    rc.require(os.environ.get('GITHUB_ACTIONS') == 'true'
               and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
               and os.environ.get('RUNNER_OS') == 'macOS'
               and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som', 'use the GitHub-hosted M6 release workflow')
    out = args.out.resolve()
    parent = rc.REPO / '.local/rust-compiler-goal/acceptance'
    rc.require(parent in out.parents and not out.exists(), 'preserve existing outputs and use the QA evidence directory')
    rc.require(not (rc.REPO / 'som-rs/runtime/default.json').exists(), 'hosted rehearsal must not have a production default')
    protected_before = protected_ports_absent()
    out.mkdir(parents=True)
    (out / 'run').mkdir()
    summary = {'version': 'som.m6-release-rehearsal.v1', 'passed': False, 'status': 'failed',
               'production_default_switch': 'not authorized or executed',
               'tag': 'not authorized or created', 'github_release': 'not authorized or created',
               'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=rc.REPO, text=True).strip()}
    try:
        mac_args = ([] if args.mac_accepted_receipt is None else [
            '--mac-accepted-receipt', str(args.mac_accepted_receipt),
            '--mac-accepted-receipt-sha256', args.mac_accepted_receipt_sha256,
            '--mac-candidate-revision', args.mac_candidate_revision])
        for name, argv in [
                ('prepare', [sys.executable, str(HERE / 'prepare_compiler_inputs.py'), '--raw-root', str(args.raw_root.resolve()),
                             '--audit-root', str(args.audit_root.resolve()), '--out', str(out / 'run/inputs'), *mac_args]),
                ('rehearse', [sys.executable, str(HERE / 'rehearse.py'), '--prerequisites', str(out / 'run/inputs/prerequisites.json'),
                              '--out', str(out / 'run/rehearsal')])]:
            call = supervision.invoke(argv, rc.REPO, out, name, timeout=1800, grace=300,
                                      lifecycle_dir=out / 'run')
            rc.require(call['outcome'] == 'completed' and type(call['wait_result']) is int
                       and call['wait_result'] == call['exit_code'] == 0,
                       f'{name} did not complete cleanly; inspect retained lifecycle and logs')
        config = rc.load(out / 'run/inputs/prerequisites.json')
        rc.check_prerequisites(config)
        report = verify_report(out / 'run/rehearsal', config)
        protected_after = protected_ports_absent()
        rc.require(not (rc.REPO / 'som-rs/runtime/default.json').exists(), 'production default receipt appeared')
        summary.update({'passed': True, 'status': 'passed', 'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                        'previous_binary_sha256': config['previous']['binary']['sha256'],
                        'prerequisites_sha256': rc.digest(out / 'run/inputs/prerequisites.json'),
                        'rehearsal_sha256': rc.digest(out / 'run/rehearsal/rehearsal.json'),
                        'scenarios': {r['name']: r['passed'] for r in report['scenarios']},
                        'offline_resume': {'passed': True, 'scenarios': len(report['scenarios'])},
                        'candidate_canaries': {f: len(config[f + '_canaries']) for f in ('python', 'rust', 'typescript', 'react')},
                        'previous_canaries': {f: len(cases) for f, cases in config['previous_canaries'].items()},
                        'protected_ports_unused': sorted(rc.PROTECTED_PORTS), 'all_scenario_owners_stopped': True,
                        'protected_port_check': {'method': 'read_only_lsof; no bind/listen/connect',
                                                 'before': protected_before, 'after': protected_after},
                        'cli_expectations': config['cli_calibration']})
    except Exception as error:
        summary['error'] = str(error)
        raise
    finally:
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
