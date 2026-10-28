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

HERE = Path(__file__).resolve().parent
SCENARIOS = ('switch', 'after_stop', 'after_start', 'after_pointer', 'after_commit')


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
        journal = rc.load(root / 'switch/journal.json')
        complete_journal(journal)
        final = 'committed' if name == 'switch' else 'rollback_verified'
        rc.require(journal[-1]['stage'] == final, 'switch final journal stage differs')
        calls = row['calls']
        rc.require([c['name'] for c in calls] == (['switch', 'check', 'restore', 'check-restored']
                   if name == 'switch' else ['switch', 'check']), 'scenario call sequence differs')
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
    args = parser.parse_args()
    rc.require(os.environ.get('GITHUB_ACTIONS') == 'true'
               and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
               and os.environ.get('RUNNER_OS') == 'macOS'
               and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som', 'use the GitHub-hosted M6 release workflow')
    out = args.out.resolve()
    parent = rc.REPO / '.local/rust-compiler-goal/acceptance'
    rc.require(parent in out.parents and not out.exists(), 'preserve existing outputs and use the QA evidence directory')
    rc.require(not (rc.REPO / 'som-rs/runtime/default.json').exists(), 'hosted rehearsal must not have a production default')
    for port in rc.PROTECTED_PORTS:
        rc.port_free(port)
    out.mkdir(parents=True)
    (out / 'run').mkdir()
    summary = {'version': 'som.m6-release-rehearsal.v1', 'passed': False, 'status': 'failed',
               'production_default_switch': 'not authorized or executed',
               'tag': 'not authorized or created', 'github_release': 'not authorized or created',
               'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=rc.REPO, text=True).strip()}
    try:
        for name, argv in [
                ('prepare', [sys.executable, str(HERE / 'prepare_compiler_inputs.py'), '--raw-root', str(args.raw_root.resolve()),
                             '--audit-root', str(args.audit_root.resolve()), '--out', str(out / 'run/inputs')]),
                ('rehearse', [sys.executable, str(HERE / 'rehearse.py'), '--prerequisites', str(out / 'run/inputs/prerequisites.json'),
                              '--out', str(out / 'run/rehearsal')])]:
            with (out / f'{name}.stdout').open('xb') as stdout, (out / f'{name}.stderr').open('xb') as stderr:
                done = subprocess.run(argv, cwd=rc.REPO, stdout=stdout, stderr=stderr, timeout=1800)
            rc.require(done.returncode == 0, f'{name} failed with exit {done.returncode}; inspect retained raw logs')
        config = rc.load(out / 'run/inputs/prerequisites.json')
        rc.check_prerequisites(config)
        report = verify_report(out / 'run/rehearsal', config)
        for port in rc.PROTECTED_PORTS:
            rc.port_free(port)
        rc.require(not (rc.REPO / 'som-rs/runtime/default.json').exists(), 'production default receipt appeared')
        summary.update({'passed': True, 'status': 'passed', 'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                        'previous_binary_sha256': config['previous']['binary']['sha256'],
                        'prerequisites_sha256': rc.digest(out / 'run/inputs/prerequisites.json'),
                        'rehearsal_sha256': rc.digest(out / 'run/rehearsal/rehearsal.json'),
                        'scenarios': {r['name']: r['passed'] for r in report['scenarios']},
                        'candidate_canaries': {f: len(config[f + '_canaries']) for f in ('python', 'rust', 'typescript', 'react')},
                        'previous_canaries': {f: len(cases) for f, cases in config['previous_canaries'].items()},
                        'protected_ports_unused': sorted(rc.PROTECTED_PORTS), 'all_scenario_owners_stopped': True,
                        'cli_expectations': config['cli_calibration']})
    except Exception as error:
        summary['error'] = str(error)
        raise
    finally:
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
