#!/usr/bin/env python3
"""Run pinned M6 canaries on a GitHub-hosted runner; this is not host proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc

FAMILIES = {'candidate': ('python', 'rust', 'typescript', 'react'),
            'previous': ('python', 'rust', 'typescript')}
REPOSITORY_ID = 1390374878


def need(ok, message):
    if not ok:
        raise ValueError(message)


def same(left, right):
    return json.dumps(left, sort_keys=True, separators=(',', ':'), ensure_ascii=False) == \
        json.dumps(right, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def normalize_case(case):
    steps = []
    for step in case['steps']:
        row = {key: step[key] for key in ('kind', 'name', 'expectation_origin') if key in step}
        row['response_sha256'] = step['response']['sha256']
        if step['kind'] == 'http':
            row.update(path=step['path'], request_sha256=step['request']['sha256'])
        else:
            need(step['kind'] == 'cli', 'unknown canary step')
            inputs = step['inputs']
            bindings = {item['path']: item['sha256'] for item in inputs}
            need(len(bindings) == len(inputs), 'duplicate CLI input path')
            row.update(args=[('sha256:' + bindings[value] if value in bindings else value)
                             for value in step['args']],
                       inputs_sha256=[item['sha256'] for item in inputs], binaries=step['binaries'])
        steps.append(row)
    return {'name': case['name'], 'family': case['family'], 'source': case['source'], 'steps': steps}


def inventory(config):
    """Build a private in-memory projection; callers must not print or publish it."""
    return {'version': 'som.m6.runner-canary-expected.v1',
            'accepted_receipt_sha256': config['gates']['accepted_issue130']['sha256'],
            'compiler_hashes': {role: {key: config[role][key]['sha256']
                                for key in ('binary', 'build', 'runtime', 'source')}
                                for role in FAMILIES},
            'gate_hashes': {key: value['sha256'] for key, value in config['gates'].items()},
            'gate_assertions': {key: value['assertions'] for key, value in config['gates'].items()},
            'formatter_tree_sha256': config['typescript_formatter']['tree_sha256'],
            'cases': {'candidate': {family: [normalize_case(case) for case in config[family + '_canaries']]
                                    for family in FAMILIES['candidate']},
                      'previous': {family: [normalize_case(case) for case in config['previous_canaries'][family]]
                                   for family in FAMILIES['previous']}}}


def bindings(config):
    """Return every source used by the case set, in deterministic order."""
    for role, families in FAMILIES.items():
        groups = config['previous_canaries'] if role == 'previous' else {
            name: config[name + '_canaries'] for name in families}
        for family in families:
            for case in groups[family]:
                for step in case['steps']:
                    yield step['response']
                    if step['kind'] == 'http':
                        yield step['request']
                    for value in step.get('inputs', []):
                        yield value


def pin_binding(binding, roots):
    need(type(binding) is dict and set(binding) == {'path', 'sha256'}, 'invalid file binding')
    path = Path(binding['path'])
    need(path.is_absolute() and not path.is_symlink() and path.is_file(), 'bound file is not regular')
    resolved = path.resolve(strict=True)
    need(any(resolved.is_relative_to(root) for root in roots), 'bound file leaves runner roots')
    need(rc.digest(resolved) == binding['sha256'], 'bound file bytes differ')


def validate_config(config, expected, workspace, runner_temp=None):
    """Check all accepted cases and identities, not only the selected role."""
    roots = (workspace,) if runner_temp is None else (workspace, runner_temp)
    need(rc.compiler_profile(config), 'compiler-only profile required')
    for role in FAMILIES:
        actual = {key: config[role][key]['sha256'] for key in ('binary', 'build', 'runtime', 'source')}
        need(same(actual, expected['compiler_hashes'][role]), 'compiler identity differs')
        for key in ('binary', 'build', 'runtime', 'source'):
            pin_binding(config[role][key], roots)
    need(set(config['gates']) == set(expected['gate_hashes']), 'acceptance gate set differs')
    for key, gate in config['gates'].items():
        need(gate['sha256'] == expected['gate_hashes'][key]
             and same(gate['assertions'], expected['gate_assertions'][key]), 'acceptance gate differs')
        pin_binding({'path': gate['path'], 'sha256': gate['sha256']}, roots)
    need(config['typescript_formatter']['tree_sha256'] == expected['formatter_tree_sha256'],
         'formatter identity differs')
    need(set(config['previous_canaries']) == set(FAMILIES['previous']), 'previous family set differs')
    for role, families in FAMILIES.items():
        groups = config['previous_canaries'] if role == 'previous' else {
            name: config[name + '_canaries'] for name in families}
        for family in families:
            actual = [normalize_case(case) for case in groups[family]]
            need(same(actual, expected['cases'][role][family]), 'accepted canary cases differ')
    for binding in bindings(config):
        pin_binding(binding, roots)
    rc.check_prerequisites(config)


def runner_identity():
    need(os.environ.get('GITHUB_ACTIONS') == 'true'
         and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and os.environ.get('RUNNER_OS') == 'macOS'
         and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som', 'GitHub-hosted macOS required')
    run_id, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    need(run_id.isdecimal() and attempt.isdecimal(), 'workflow run identity missing')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    repository = event['repository']
    need(repository['id'] == REPOSITORY_ID and repository['full_name'] == 'faberline/som',
         'workflow repository differs')
    if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        head_repo = event['pull_request']['head']['repo']
        base_repo = event['pull_request']['base']['repo']
        need(head_repo['id'] == base_repo['id'] == REPOSITORY_ID
             and head_repo['full_name'] == base_repo['full_name'] == 'faberline/som',
             'fork pull request refused')
        expected = event['pull_request']['head']['sha']
    elif os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch':
        expected = os.environ.get('GITHUB_SHA', '')
    else:
        raise ValueError('unsupported GitHub event')
    env = dict(os.environ)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING'):
        env.pop(name, None)
    actual = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=rc.REPO,
                                     text=True, timeout=30, env=env).strip()
    need(re.fullmatch('[0-9a-f]{40}', expected or '') and actual == expected,
         'checkout is not the workflow source')
    return {'run_id': run_id, 'run_attempt': attempt, 'source_commit': actual}


def error_type(error):
    value = type.__getattribute__(type(error), '__name__')
    return value if type(value) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,79}', value) else 'BaseException'


def raw_evidence(output):
    result = []
    for path in sorted(output.iterdir()):
        need(path.name == 'journal.json' or re.fullmatch(
            r'[0-9]{4}-(request\.raw|response\.raw|cli-argv\.json|cli-stdout\.raw|cli-stderr\.raw)', path.name),
            'unexpected raw evidence name')
        need(path.is_file() and not path.is_symlink(), 'unexpected raw evidence entry')
        result.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': rc.digest(path)})
    return result


def sync_receipt(stream):
    stream.flush()
    os.fsync(stream.fileno())


def write_receipt(path, receipt):
    """Exclusive write; a receipt alone is never evidence of successful execution."""
    with path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
        sync_receipt(stream)


def run_cases(config_path, anchor, workspace, role, port, output, runner, runner_temp=None):
    receipt = {'version': 'som.m6.hosted-canary-result.v1', 'passed': False,
               'role': role, 'port': port, 'runner': runner, 'started_at': time.time(),
               'production_host_proven': False,
               'production_acceptance_passed': False, 'check_default_replaced': False,
               'per_exchange_status_or_exit': 'not separately recorded by Release',
               'seal_required_before_retention': True, 'step_outcome_required': True,
               'evidence_failures': []}
    primary = None
    primary_traceback = None
    evidence_errors = []
    phase = 'preflight'
    postcheck_ready = False
    try:
        need(output.is_dir() and not output.is_symlink() and not any(output.iterdir()),
             'fresh private output required')
        need(not any(name in os.environ for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING')),
             'credentials must not be present during canaries')
        need(role in FAMILIES, 'unknown compiler role')
        rc.harness_port(port)
        prior_path = anchor['prior_path']
        need(config_path.resolve(strict=True) != prior_path.resolve(strict=True), 'independent prior required')
        current_before, prior_before = rc.digest(config_path), rc.digest(prior_path)
        need(prior_before == anchor['prior_sha256'], 'restored prior changed')
        config, prior = rc.load(config_path), rc.load(prior_path)
        expected = inventory(prior)
        need(same(inventory(config), expected), 'prior canary projection differs')
        validate_config(config, expected, workspace, runner_temp)
        cases = rc.candidate_cases(config) if role == 'candidate' else rc.previous_cases(config)
        role_name = 'new' if role == 'candidate' else 'previous'
        expected_names = [case['name'] for family in FAMILIES[role]
                          for case in expected['cases'][role][family]]
        need([case['name'] for case in cases] == expected_names, 'selected case order differs')
        runner_obj = rc.Release(output, str(workspace))
        rc.apply_formatter_env(config)
        binary = rc.bound(config[role]['binary'])
        receipt.update({'client_sha256': rc.digest(__file__), 'common_sha256': rc.digest(rc.__file__),
                        'candidate_binary_sha256': config['candidate']['binary']['sha256'],
                        'previous_binary_sha256': config['previous']['binary']['sha256'],
                        'expected_case_names': expected_names})
        postcheck_ready = True
        phase = 'canary'
        runner_obj.canary(port, {role_name: binary}, cases, 'hosted-' + role)
        phase = 'postcheck'
        events = [row['name'] for row in runner_obj.events if row['stage'] == 'canary_passed']
        need(events == expected_names, 'canary journal differs')
        need(not runner_obj.evidence_errors, 'raw evidence write failed')
        receipt['passed'] = True
    except BaseException as error:
        primary = error
        primary_traceback = BaseException.__dict__['__traceback__'].__get__(error, type(error))
        receipt.update({'first_error_phase': phase, 'first_error_type': error_type(error)})
    finally:
        receipt['ended_at'] = time.time()
        if postcheck_ready:
            try:
                validate_config(config, expected, workspace, runner_temp)
                need(rc.digest(config_path) == current_before and rc.digest(prior_path) == prior_before
                     and rc.digest(anchor['archive_path']) == anchor['archive_sha256'],
                     'prerequisites changed during canary')
                receipt['inputs_postchecked'] = True
            except BaseException as error:
                receipt['passed'] = False
                receipt['inputs_postchecked'] = False
                if primary is None:
                    primary = error
                    primary_traceback = BaseException.__dict__['__traceback__'].__get__(error, type(error))
                    receipt.update({'first_error_phase': 'postcheck', 'first_error_type': error_type(error)})
                else:
                    evidence_errors.append(error)
                    receipt['evidence_failures'].append({'phase': 'inputs_postcheck', 'type': error_type(error)})
        try:
            receipt['raw_files'] = raw_evidence(output)
            if receipt['passed']:
                need(any(item['name'] == 'journal.json' for item in receipt['raw_files']), 'canary journal missing')
        except BaseException as error:
            receipt['passed'] = False
            evidence_errors.append(error)
            receipt['evidence_failures'].append({'phase': 'raw_hash', 'type': error_type(error)})
        try:
            path = output / 'hosted-canary-result.json'
            write_receipt(path, receipt)
        except BaseException as error:
            receipt['passed'] = False
            evidence_errors.append(error)
    if primary is not None:
        if evidence_errors:
            try:
                BaseException.add_note(primary, 'A separate evidence operation failed; the run remains failed.')
            except BaseException:
                pass
        raise BaseException.with_traceback(primary, primary_traceback)
    if evidence_errors:
        raise evidence_errors[0]
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--prior-archive', type=Path, required=True)
    parser.add_argument('--role', choices=tuple(FAMILIES), required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    runner = runner_identity()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    need(workspace == rc.REPO.resolve(strict=True), 'workspace differs from checkout')
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    output = args.out
    need(output.is_absolute() and not output.exists() and not output.is_symlink()
         and output.parent.resolve(strict=True).is_relative_to(temp), 'fresh runner temp output required')
    need(args.prerequisites.is_absolute() and args.prerequisites.is_file()
         and not args.prerequisites.is_symlink()
         and args.prerequisites.resolve(strict=True).is_relative_to(workspace),
         'runner workspace prerequisites required')
    output.mkdir(mode=0o700)
    import rehearsal_anchor
    anchor = rehearsal_anchor.restore_verified_prior(args.prior_archive, temp, workspace)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'M6_INPUTS_KEYRING'):
        os.environ.pop(name, None)
    result = run_cases(args.prerequisites, anchor, workspace, args.role, args.port, output, runner, temp)
    print(json.dumps({'passed': result['passed'], 'production_acceptance_passed': False}))
    return 0 if result['passed'] else 1


def entry():
    try:
        return main()
    except BaseException:
        print('hosted canary failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(entry())
