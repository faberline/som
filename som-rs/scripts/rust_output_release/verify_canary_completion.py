#!/usr/bin/env python3
"""Check both runner role receipts; no production or GitHub outcome is inferred."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import release_common as rc
import rehearsal_anchor
import run_hosted_canaries as client


def unique(items):
    result = {}
    for key, value in items:
        client.need(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(_value):
    raise ValueError('non-finite JSON value')


def load(path):
    client.need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 32 * 1024 * 1024,
                'missing or invalid evidence')
    return json.loads(path.read_bytes(), object_pairs_hook=unique, parse_constant=invalid_constant)


def expected_raw_names(cases):
    index, names = 0, set()
    for case in cases:
        for step in case['steps']:
            suffixes = ('request.raw', 'response.raw') if step['kind'] == 'http' else (
                'cli-argv.json', 'cli-stdout.raw', 'cli-stderr.raw') * len(step['binaries'])
            for suffix in suffixes:
                index += 1
                names.add(f'{index:04d}-{suffix}')
    return names | {'journal.json'}


def verify_role(root, role, expected, runner):
    client.need((root / 'run').is_dir() and not (root / 'run').is_symlink(), 'run directory missing')
    output = root / 'run' / role
    client.need(output.is_dir() and not output.is_symlink(), 'role output missing')
    receipt = load(output / 'hosted-canary-result.json')
    client.need(receipt['version'] == 'som.m6.hosted-canary-result.v1'
                and receipt['passed'] is True and receipt['role'] == role
                and receipt['runner'] == runner and receipt['inputs_postchecked'] is True
                and receipt['production_host_proven'] is False
                and receipt['production_acceptance_passed'] is False
                and receipt['check_default_replaced'] is False
                and receipt['seal_required_before_retention'] is True
                and receipt['step_outcome_required'] is True
                and receipt['evidence_failures'] == [], 'role receipt failed')
    client.need(receipt['client_sha256'] == rc.digest(client.__file__)
                and receipt['common_sha256'] == rc.digest(rc.__file__)
                and receipt['candidate_binary_sha256'] == expected['compiler_hashes']['candidate']['binary']
                and receipt['previous_binary_sha256'] == expected['compiler_hashes']['previous']['binary'],
                'role source or binary differs')
    rc.harness_port(receipt['port'])
    cases = [case for family in client.FAMILIES[role] for case in expected['cases'][role][family]]
    names = [case['name'] for case in cases]
    client.need(len(names) == (8 if role == 'candidate' else 6)
                and receipt['expected_case_names'] == names, 'role cases differ')
    journal = load(output / 'journal.json')
    client.need(type(journal) is list and len(journal) == len(names), 'role journal differs')
    for name, event in zip(names, journal):
        client.need(event['stage'] == 'canary_passed' and event['name'] == name
                    and event['port'] == receipt['port'] and event['label'] == 'hosted-' + role,
                    'role event differs')
    raw = receipt['raw_files']
    client.need(type(raw) is list and len({row['name'] for row in raw}) == len(raw), 'raw list differs')
    expected_files = expected_raw_names(cases)
    client.need({row['name'] for row in raw} == expected_files
                and {p.name for p in output.iterdir()} == expected_files | {'hosted-canary-result.json'},
                'raw evidence set differs')
    for row in raw:
        path = output / row['name']
        client.need(path.is_file() and not path.is_symlink() and type(row['bytes']) is int
                    and path.stat().st_size == row['bytes'] and rc.digest(path) == row['sha256'],
                    'raw evidence bytes differ')


def verify(prior, current, root, runner, step_outcome):
    # The caller supplies the actual GitHub step result. A caller-provided
    # string is not, by itself, a GitHub attestation.
    client.need(step_outcome == 'success', 'runner step did not succeed')
    expected = client.inventory(prior)
    client.need(client.same(client.inventory(current), expected), 'prior projection differs')
    for role in ('candidate', 'previous'):
        verify_role(root, role, expected, runner)
    return {'complete': True, 'production_acceptance_passed': False,
            'github_step_outcome_independently_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--prior-archive', type=Path, required=True)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--step-outcome', required=True)
    args = parser.parse_args()
    runner = client.runner_identity()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    client.need(workspace == rc.REPO.resolve(strict=True), 'checkout differs')
    client.need(args.step_outcome == 'success', 'runner step did not succeed')
    for path in (args.prerequisites, args.raw_root):
        client.need(path.is_absolute() and not path.is_symlink(), 'absolute physical path required')
    client.need(args.prerequisites.resolve(strict=True).is_relative_to(workspace)
                and args.raw_root.resolve(strict=True).is_relative_to(temp), 'evidence path differs')
    anchor = rehearsal_anchor.restore_verified_prior(args.prior_archive, temp, workspace)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING'):
        os.environ.pop(name, None)
    prior_before = rc.digest(anchor['prior_path'])
    current_before = rc.digest(args.prerequisites)
    client.need(prior_before == anchor['prior_sha256'], 'prior bytes changed')
    prior, current = rc.load(anchor['prior_path']), rc.load(args.prerequisites)
    expected = client.inventory(prior)
    client.validate_config(current, expected, workspace, temp)
    result = verify(prior, current, args.raw_root, runner, args.step_outcome)
    client.need(rc.digest(anchor['prior_path']) == prior_before
                and rc.digest(args.prerequisites) == current_before
                and rc.digest(anchor['archive_path']) == anchor['archive_sha256'], 'source bytes changed')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    try:
        status = main()
    except BaseException:
        print('runner completeness verification failed', file=sys.stderr)
        status = 1
    raise SystemExit(status)
