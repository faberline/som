#!/usr/bin/env python3
"""Pin compiler-only release canaries from the complete accepted #130 evidence.

This reads saved bytes. It does not contact a service or run a compiler.
CLI expectations are derived from accepted HTTP bytes and are marked as such.
The derivation must first match all nine retained successful CLI captures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc

RECEIPT = 'som-rs/evidence/react-output-acceptance-2026-10-29-issue130.json'
RECEIPT_SHA = '5a4d6a0271f57605f0209ff0a4697e92694742b167a04895d6f894aa9cbac46d'
AUDIT_SUMMARY_SHA = '86c43d3b817f2480869f589dbe1baee15901c166c8d7d3da837e68a3d227dc58'
BASELINE_PINS = {
    'som': '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c',
    'build.json': '0918984e7c9c00c5299d62d38209658385b960f996d84bf0514b52c04c528d73',
    'runtime.json': '7c4ba46a498bb0f601bd67ff77f99dfe75e4ee1669d6af3c53f3b338a550b970',
    'source.json': 'd40578d8845fff8bfeff627924aa501e56f3973eddb09c2d871e25adec2bbbe5',
}
FINAL_CANARY_COMPILE_SHA = {
    ('python', 'PAIR_V7_FLOW_CONTRACT'): '1e3817f10bdafd70ed7cf21e6a5adba42432ed5bbf07abf9a4096add4cd35b19',
    ('python', 'services14-0000'): '817e7e80d907a029a7c0063770e5cf6d9e5a7565f26eace1b8444f57c6846734',
    ('rust', 'example-v1'): '6acb39533d290ac836446ccb613c059592019ba90755dcf8133d3198f9ac20c5',
    ('rust', 'example-v5'): '129990e19ee15fa9a3a685d75afad315198dbc4593b41d2da8e583afb8bf0c84',
}


def final_receipt(path, expected_sha, revision):
    """Check the supplied Mac acceptance receipt; the caller owns its authority."""
    rc.require(path.is_absolute() and all(not part.is_symlink() for part in (path, *path.parents)),
               'acceptance receipt must be a physical absolute path')
    rc.require(re.fullmatch(r'[0-9a-f]{64}', expected_sha) is not None and rc.digest(path) == expected_sha,
               'acceptance receipt identity differs')
    rc.require(re.fullmatch(r'[0-9a-f]{40}', revision) is not None, 'final source revision required')
    accepted = parse(path.read_bytes())
    candidate = accepted.get('candidate', {})
    audit = accepted.get('independent_audit', {})
    rc.require(accepted.get('version') == 'som.react-output-acceptance-evidence.v1'
               and accepted.get('issue') == 130
               and accepted.get('status') == 'accepted_after_independent_raw_audit'
               and candidate.get('source_commit') == revision
               and re.fullmatch(r'[0-9a-f]{64}', candidate.get('binary_sha256', '')) is not None
               and candidate['binary_sha256'] != '59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308'
               and accepted.get('baseline', {}).get('binary_sha256') == BASELINE_PINS['som']
               and re.fullmatch(r'[0-9a-f]{64}', audit.get('summary_sha256', '')) is not None
               and re.fullmatch(r'[0-9a-f]{64}', audit.get('audit_sha256', '')) is not None,
               'final Mac acceptance receipt differs')
    return accepted


def final_review(accepted):
    """Join the receipt to the separately retained review of these same bytes."""
    review = accepted.get('independent_review', {})
    relative = review.get('path', '')
    expected = review.get('sha256', '')
    rc.require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
               and '..' not in Path(relative).parts and Path(relative).as_posix() == relative
               and isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected) is not None,
               'independent review reference differs')
    path = rc.REPO / relative
    rc.require(all(not part.is_symlink() for part in (path, *path.parents)) and rc.digest(path) == expected,
               'independent review bytes differ')
    record = parse(path.read_bytes())
    rc.require(record.get('version') == 'som.react-output-independent-acceptance-review.v1'
               and record.get('issue') == 130
               and record.get('verdict') == 'accept_issue130_phase1_only'
               and record.get('findings') == []
               and record.get('candidate_binary_sha256') == accepted['candidate']['binary_sha256']
               and record.get('compiler_source_commit') == accepted['candidate']['source_commit']
               and record.get('audit_sha256') == accepted['independent_audit']['audit_sha256']
               and record.get('raw_run_sha256') == accepted['report_sha256']['run/run.json'],
               'independent review does not bind this acceptance')
    return path


def final_candidate_source(accepted, raw, revision):
    """Bind the retained candidate's actual source receipt to the accepted revision."""
    source = parse(raw)
    rc.require(source.get('commit') == revision == accepted['candidate']['source_commit'],
               'candidate source receipt names another commit')
    return source


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            rc.require(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    def bad(_value):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)


def cli_json_bytes(raw, member=None):
    """serde_json's sorted, two-space format, with original HTTP number tokens."""
    class Number(str):
        pass
    document = json.loads(raw, parse_int=Number, parse_float=Number)
    if member is not None:
        document = document[member]

    def pretty(value, depth):
        if isinstance(value, Number):
            return str(value)
        indent, child = '  ' * depth, '  ' * (depth + 1)
        if isinstance(value, dict):
            if not value:
                return '{}'
            entries = [json.dumps(key, ensure_ascii=False) + ': ' + pretty(value[key], depth + 1)
                       for key in sorted(value)]
            return '{\n' + child + (',\n' + child).join(entries) + '\n' + indent + '}'
        if isinstance(value, list):
            if not value:
                return '[]'
            return '[\n' + child + (',\n' + child).join(pretty(v, depth + 1) for v in value) + '\n' + indent + ']'
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return (pretty(document, 0) + '\n').encode()


def compact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def binding(path):
    return {'path': str(path), 'sha256': rc.digest(path)}


class Sources:
    def __init__(self, root):
        self.root = root
        self.files = {}

    def read(self, relative, expected=None):
        path = Path(relative)
        rc.require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == relative,
                   'source path leaves its evidence directory')
        path = self.root / path
        for parent in (path, *path.parents):
            rc.require(not parent.is_symlink(), 'symlink evidence refused')
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        rc.require(expected is None or sha == expected, f'accepted source changed: {relative}')
        rc.require(relative not in self.files or self.files[relative] == sha, f'source changed between reads: {relative}')
        self.files[relative] = sha
        return raw

    def json(self, relative, expected=None):
        return parse(self.read(relative, expected))

    def call(self, family, identity):
        directory = f'run/{family}-baseline-raw'
        manifest = self.json(directory + '/manifest.json')
        rc.require(manifest['version'] == 'som.m6-http-recording.v1'
                   and manifest['exit_code'] == 0 and manifest['recording_errors'] == [],
                   'baseline recording did not pass')
        calls = [row for row in manifest['calls'] if row['id'] == identity]
        rc.require(len(calls) == 1, 'raw HTTP call is missing or repeated')
        call = calls[0]
        rc.require(call['complete'] is True and call['error'] is None and call['status'] == 200
                   and call['method'] == 'POST', 'raw HTTP call was not a complete success')
        raw = [self.read(directory + '/' + call[kind + '_file'], call[kind + '_sha256'])
               for kind in ('request', 'response')]
        return call, *raw


class Pinner:
    def __init__(self, out):
        self.out = out

    def write(self, relative, raw):
        path = self.out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        return binding(path)

    def case(self, side, family, name, compile_request, compile_response, assemble_request,
             assemble_response, provenance, python_family=None):
        prefix = f'inputs/{side}/{family}/{name}'
        request, reply = parse(compile_request), parse(compile_response)
        assembly, assembled = parse(assemble_request), parse(assemble_response)
        rc.require(reply.get('status') == assembled.get('status') == 'ok', 'canary reply is not successful')
        rc.require(request.get('target', 'python') == family, 'compile request target differs')
        rc.require(rc.exact_json(reply.get('bundle'), assembly.get('bundle')), 'assemble request names another bundle')
        rc.require(reply['bundle'].get('target', 'python') == family, 'compile bundle target differs')
        steps = []
        for operation, body, response in [('compile', compile_request, compile_response),
                                          ('assemble', assemble_request, assemble_response)]:
            steps.append({'kind': 'http', 'name': operation, 'path': '/v1/' + operation,
                          'request': self.write(prefix + '/' + operation + '.request', body),
                          'response': self.write(prefix + '/' + operation + '.response', response)})
        bundle = self.write(prefix + '/bundle.json', compact(assembly['bundle']))
        steps.append({'kind': 'cli', 'name': 'cli-assemble', 'args': ['assemble', bundle['path']],
                      'inputs': [bundle], 'binaries': ['new' if side == 'candidate' else 'previous'],
                      'response': self.write(prefix + '/assemble.stdout', cli_json_bytes(assemble_response)),
                      'expectation_origin': 'derived_from_accepted_http; not a historical CLI capture'})
        if request.get('format') == 'som.cnl.v1':
            source = self.write(prefix + '/source.cnl', request['source'].encode())
            args = ['compile', source['path']]
            if family != 'python':
                args += ['--target', family]
            steps.append({'kind': 'cli', 'name': 'cli-compile', 'args': args, 'inputs': [source],
                          'binaries': ['new' if side == 'candidate' else 'previous'],
                          'response': self.write(prefix + '/compile.stdout', cli_json_bytes(compile_response, 'bundle')),
                          'expectation_origin': 'derived_from_accepted_http; calibrated against nine CLI captures'})
        return {'name': name, 'family': python_family or family, 'steps': steps, 'source': provenance}


def calibrate(source):
    report = source.json('run/react-target/report.json')
    cli = report['cli']
    rc.require(cli['passed'] is True and cli['total'] == cli['passed_count'] == 22, 'retained CLI gate differs')
    rows = [r for r in cli['rows'] if r['http_status'] == 200]
    rc.require(len(rows) == 9 and all(r['exit_code'] == 0 and r['passed'] is True for r in rows), 'retained CLI success set differs')
    for row in rows:
        response = source.read('run/react-target/' + row['http_response_file'], row['http_response_sha256'])
        stdout = source.read('run/react-target/' + row['stdout_file'], row['stdout_sha256'])
        rc.require(stdout == cli_json_bytes(response, 'bundle'), 'CLI pretty format calibration failed')
    return {'captured_compile_cases': len(rows), 'passed': True,
            'assemble_expectations': 'derived from accepted HTTP, then checked byte for byte in rehearsal'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, required=True)
    parser.add_argument('--audit-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mac-accepted-receipt', type=Path)
    parser.add_argument('--mac-accepted-receipt-sha256')
    parser.add_argument('--mac-candidate-revision')
    args = parser.parse_args()
    raw, audit_root, out = args.raw_root.resolve(), args.audit_root.resolve(), args.out.resolve()
    rc.require(not out.exists(), 'preserve existing release inputs')
    values = (args.mac_accepted_receipt, args.mac_accepted_receipt_sha256, args.mac_candidate_revision)
    rc.require(all(value is None for value in values) or all(value is not None for value in values),
               'all final Mac acceptance inputs are required together')
    final_mac = args.mac_accepted_receipt is not None
    if final_mac:
        receipt_path = args.mac_accepted_receipt
        accepted = final_receipt(receipt_path, args.mac_accepted_receipt_sha256, args.mac_candidate_revision)
        review_path = final_review(accepted)
        audit_summary_sha = accepted['independent_audit']['summary_sha256']
    else:
        receipt_path = rc.REPO / RECEIPT
        rc.require(rc.digest(receipt_path) == RECEIPT_SHA, 'accepted #130 receipt differs')
        accepted = rc.load(receipt_path)
        audit_summary_sha = AUDIT_SUMMARY_SHA
    source, audited = Sources(raw), Sources(audit_root)
    for relative, expected in accepted['report_sha256'].items():
        source.read(relative, expected)
    actual_run = source.json('run/run.json') if final_mac else None
    if final_mac:
        rc.require(actual_run.get('passed') is True and actual_run.get('failed') == []
                   and actual_run.get('not_run') == [] and actual_run.get('record_raw_http') is True,
                   'final raw acceptance run is not complete')
        for key in ('candidate_url', 'baseline_url'):
            rc.require(isinstance(actual_run.get(key), str) and actual_run[key].startswith('http://127.0.0.1:'),
                       'final acceptance URL differs')
        rc.require(actual_run['candidate_url'] != actual_run['baseline_url'], 'candidate and baseline URLs match')
    source.read('summary.json', accepted['runtime']['original_summary_sha256'])
    audited.read('audit.json', accepted['independent_audit']['audit_sha256'])
    summary = audited.json('summary.json', audit_summary_sha)
    rc.require(summary['passed'] is True and summary['audit_totals']['failed'] == summary['audit_totals']['unrun'] == 0,
               'retained independent audit did not pass')
    candidate = raw / 'candidate'
    for name, key in [('som', 'binary_sha256'), ('build.json', 'build_sha256'),
                      ('runtime.json', 'runtime_sha256'), ('source.json', 'source_receipt_sha256')]:
        raw_candidate = source.read('candidate/' + name, accepted['candidate'][key])
        if final_mac and name == 'source.json':
            final_candidate_source(accepted, raw_candidate, args.mac_candidate_revision)
    previous = rc.REPO / '.local/m6-hosted/baseline'
    for name, expected in BASELINE_PINS.items():
        rc.require(rc.digest(previous / name) == expected, 'accepted previous compiler differs')
    calibration = calibrate(source)
    out.mkdir(parents=True)
    pin = Pinner(out)
    canaries = {family: [] for family in ('python', 'rust', 'typescript', 'react')}
    before = {family: [] for family in ('python', 'rust', 'typescript')}
    for name, family, new_compile, old_compile, new_assemble, old_assemble in [
            ('PAIR_V7_FLOW_CONTRACT', 'flow', 47, 49, 51, 54),
            ('services14-0000', 'legacy', 993, 994, 996, 1005)]:
        for side, compile_id, assemble_id, destination in [
                ('candidate', new_compile, new_assemble, canaries['python']),
                ('previous', old_compile, old_assemble, before['python'])]:
            cc, request, response = source.call('python', compile_id)
            ac, arequest, aresponse = source.call('python', assemble_id)
            rc.require(cc['url'].endswith('/v1/compile') and ac['url'].endswith('/v1/assemble'), 'Python canary routes differ')
            if final_mac:
                base = actual_run['candidate_url' if side == 'candidate' else 'baseline_url']
                rc.require(cc['url'] == base + '/v1/compile' and ac['url'] == base + '/v1/assemble'
                           and hashlib.sha256(request).hexdigest() == FINAL_CANARY_COMPILE_SHA['python', name],
                           'Python canary case or compiler side differs')
            destination.append(pin.case(side, 'python', name, request, response, arequest, aresponse,
                {'kind': 'captured_http', 'compile_call': compile_id, 'assemble_call': assemble_id}, family))
    for name, new_compile, old_compile, assemble_id in [('example-v1', 1, 5, 14), ('example-v5', 17, 20, 26)]:
        new_call, request, response = source.call('rust', new_compile)
        assembly_call, arequest, aresponse = source.call('rust', assemble_id)
        if final_mac:
            rc.require(new_call['url'] == actual_run['candidate_url'] + '/v1/compile'
                       and assembly_call['url'] == actual_run['candidate_url'] + '/v1/assemble'
                       and hashlib.sha256(request).hexdigest() == FINAL_CANARY_COMPILE_SHA['rust', name],
                       'Rust candidate canary case differs')
        canaries['rust'].append(pin.case('candidate', 'rust', name, request, response, arequest, aresponse,
            {'kind': 'captured_http', 'compile_call': new_compile, 'assemble_call': assemble_id}))
        old_call, request, response = source.call('rust', old_compile)
        if final_mac:
            rc.require(old_call['url'] == actual_run['baseline_url'] + '/v1/compile'
                       and hashlib.sha256(request).hexdigest() == FINAL_CANARY_COMPILE_SHA['rust', name],
                       'Rust previous canary case differs')
        previous_request = compact({'bundle': parse(response)['bundle']})
        before['rust'].append(pin.case('previous', 'rust', name, request, response, previous_request, aresponse,
            {'kind': 'captured_compile_and_derived_assemble', 'compile_call': old_compile,
             'assemble_expected_source': 'accepted candidate assembly and unchanged Rust product bytes',
             'historical_baseline_assemble_capture': False, 'candidate_assemble_call': assemble_id}))
    ts_report = source.json('run/ts-baseline/report.json')
    for name in ('example-v1', 'example-v5'):
        case = next(c for c in ts_report['cases'] if c['id'] == name)
        record = next(r for r in case['requests'] if r['form'] == 'json')
        request = source.read('run/ts-baseline/' + record['request_file'], record['body_sha256'])
        for side, label, destination in [('candidate', 'qa', canaries['typescript']), ('previous', 'baseline', before['typescript'])]:
            response = source.read('run/ts-baseline/' + record[label + '_response_file'], record[label + '_sha256'])
            arequest = source.read('run/ts-baseline/' + record[label + '_assemble_request_file'], record[label + '_assemble_request_sha256'])
            aresponse = source.read('run/ts-baseline/' + record[label + '_assemble_response_file'], record[label + '_assemble_sha256'])
            files = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in parse(aresponse)['files'].items()}
            rc.require(files == record['files_sha256' if label == 'qa' else 'baseline_files_sha256'], 'TypeScript product hashes differ')
            destination.append(pin.case(side, 'typescript', name, request, response, arequest, aresponse,
                {'kind': 'captured_http', 'case': name, 'form': 'json', 'side': label}))
    for name in ('example-v1', 'example-v4'):
        stem = 'run/bundles/responses/bundle_checks__' + name + '__'
        request, response, arequest, aresponse = [source.read(stem + tail) for tail in (
            'compile_json_1.request.raw', 'compile_json_1.json', 'assemble_1.request.raw', 'assemble_1.json')]
        canaries['react'].append(pin.case('candidate', 'react', name, request, response, arequest, aresponse,
            {'kind': 'captured_http', 'case': name, 'compile': 'compile_json_1', 'assemble': 'assemble_1'}))
    formatter = rc.REPO / '.local/m6-hosted/prettier'

    def compiler(directory):
        runtime = rc.load(directory / 'runtime.json')
        return {**{key: binding(directory / filename) for key, filename in (
            ('binary', 'som'), ('runtime', 'runtime.json'), ('build', 'build.json'), ('source', 'source.json'))},
            'assets': {'path': str(directory / 'assets'), 'manifest_sha256': runtime['health']['asset_manifest_sha256']}}

    acceptance_assertions = ({'/status': 'accepted_after_independent_raw_audit',
                              '/issue': 130,
                              '/candidate/source_commit': args.mac_candidate_revision,
                              '/candidate/binary_sha256': accepted['candidate']['binary_sha256']} if final_mac else {
                              '/status': 'accepted_after_independent_raw_audit',
                              '/source_boundary/frozen_contracts_tests_compiler_targets_and_templates_unchanged': True})
    config = {'version': 'som.compiler-output-switch-prerequisites.v1', 'profile': 'compiler-only',
              'repo': str(rc.REPO), 'base_controller': {'path': str(rc.BASE_PATH), 'sha256': rc.BASE_SHA256},
              'candidate': compiler(candidate), 'previous': compiler(previous),
              'gates': {
                  'accepted_issue130': {**binding(receipt_path), 'assertions': acceptance_assertions},
                  'complete_runtime': {**binding(raw / 'run/run.json'), 'assertions': {'/passed': True, '/failed': [], '/not_run': []}},
                  'independent_audit': {**binding(audit_root / 'audit.json'), 'assertions': {'/totals/failed': 0, '/totals/unrun': 0}},
                  'audit_summary': {**binding(audit_root / 'summary.json'), 'assertions': {'/passed': True}}},
              **{family + '_canaries': cases for family, cases in canaries.items()},
              'previous_canaries': before,
              'typescript_formatter': {'env': 'SOM_TS_PRETTIER', 'path': str(formatter),
                                       'tree_sha256': rc.formatter_tree_sha(formatter)},
              'cli_calibration': calibration,
              'source_files': source.files, 'audit_source_files': audited.files}
    if final_mac:
        config['gates']['independent_source_review'] = {**binding(review_path), 'assertions': {
            '/issue': 130, '/verdict': 'accept_issue130_phase1_only',
            '/candidate_binary_sha256': accepted['candidate']['binary_sha256'],
            '/compiler_source_commit': args.mac_candidate_revision,
            '/audit_sha256': accepted['independent_audit']['audit_sha256'],
            '/raw_run_sha256': accepted['report_sha256']['run/run.json'], '/findings': []}}
    rc.check_prerequisites(config)
    (out / 'prerequisites.json').write_text(json.dumps(config, indent=1) + '\n')
    print(json.dumps({'passed': True, 'candidate_cases': {k: len(v) for k, v in canaries.items()},
                      'previous_cases': {k: len(v) for k, v in before.items()}, 'cli_calibration': calibration}))


if __name__ == '__main__':
    main()
