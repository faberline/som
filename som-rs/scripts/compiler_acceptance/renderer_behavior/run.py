"""Execution-only runner. Preparation does not authorize service startup."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
ACCEPTANCE_DIR = ROOT.parent
if str(ACCEPTANCE_DIR) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_DIR))
from paths import default_output_root, find_repo, require_new_output
REPO = find_repo(__file__)
OUTPUT_ROOT = default_output_root(REPO) / 'renderer_behavior'
DEFAULT_PREPARATION = OUTPUT_ROOT / 'preparation'
SCRIPTS = REPO / 'som-core/scripts'
sys.path[:0] = [str(SCRIPTS), str(REPO / 'som-core/src'), str(ROOT)]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')

def write_product(root, files):
    root.mkdir(parents=True, exist_ok=False)
    normalized = set()
    for name, source in files.items():
        assert isinstance(name, str) and isinstance(source, str)
        relative = PurePosixPath(name)
        assert not relative.is_absolute() and relative.parts and '..' not in relative.parts
        assert '\\' not in name and '\x00' not in name
        path = (root / str(relative)).resolve()
        assert path.is_relative_to(root.resolve()) and path not in normalized
        normalized.add(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')

def check_identity(args):
    from public_products import process_identity, get_health
    binary = args.candidate_binary.resolve(strict=True)
    assert sha(binary) == args.candidate_sha256, 'candidate disk hash differs from approved input'
    runtime_path = binary.parent / 'runtime.json'
    runtime = json.loads(runtime_path.read_text())
    assert runtime['pid'] == args.candidate_pid
    process = process_identity(runtime_path, binary, args.http_base)
    source_path = binary.parent / 'source.json'
    build_path = binary.parent / 'build.json'
    build = json.loads(build_path.read_text())
    assert build['complete'] is True and build['source_unchanged'] is True
    assert build['source_sha256'] == runtime['source_sha256'] == sha(source_path)
    assert build['binary_sha256'] == args.candidate_sha256
    source = json.loads(source_path.read_text())
    assert source['files'] and source['archive_sha256'] == sha(binary.parent / 'source.tar')
    assert all(sha(REPO / name) == digest for name, digest in source['files'].items())
    status, health, raw = get_health(args.http_base)
    assert status == 200 and health == runtime['health']
    assert health['model_loaded'] is False and health['purpose'] == 'som.rust-compiler.v1'
    return {'binary': str(binary), 'binary_sha256': sha(binary), 'pid': args.candidate_pid,
            'base_url': args.http_base, 'process': process, 'health': health,
            'health_raw_sha256': hashlib.sha256(raw).hexdigest(),
            'source_json_sha256': sha(source_path), 'source_archive_sha256': source['archive_sha256'],
            'build_sha256': sha(build_path), 'runtime_receipt_sha256': sha(runtime_path),
            'approval_label': args.approval_label}

def checked_call(client, directory, label, endpoint, request):
    import qa_harness as qa
    raw = json.dumps(request, ensure_ascii=False, separators=(',', ':'))
    (directory / (label + '.request.raw.json')).write_text(raw, encoding='utf-8')
    status, response = client.call(endpoint, body_raw=raw)
    qa.persist_api_exchange(directory, label, request, status, response, client.last_response_raw)
    assert status == 200 and response.get('status') == 'ok', (status, response)
    return response

def stage(directory, name, operation):
    started = time.monotonic()
    try:
        with (directory / (name + '.stdout.log')).open('w') as stdout, \
                (directory / (name + '.stderr.log')).open('w') as stderr, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            outcome = operation()
        record = {'completed': True, 'seconds': time.monotonic() - started, 'outcome': outcome}
    except Exception as exc:
        record = {'completed': False, 'seconds': time.monotonic() - started,
                  'error_type': type(exc).__name__, 'error': str(exc), 'traceback': traceback.format_exc()}
    write(directory / (name + '.json'), record)
    return record

def execute(args):
    import qa_harness as qa
    from verify_python_quality import verify as quality_verify
    from verify_flow_service import verify as flow_verify
    from m3_local_fixture import M3Fixture, interruption
    from behavior_support import focused_behavior
    from acceptance_cleanup import cleanup_after_behavior
    preparation_dir = args.preparation.resolve()
    assert preparation_dir.is_relative_to(OUTPUT_ROOT)
    preparation = json.loads((preparation_dir / 'preparation.json').read_text())
    dependencies = json.loads((preparation_dir / 'dependencies.json').read_text())
    assert preparation['ready_dependencies'] is True
    assert sys.executable == dependencies['python_executable'], 'use the qualified acceptance venv'
    for path, expected in preparation['source_sha256'].items():
        assert sha(Path(path)) == expected, 'checker/contract source drift: ' + path
    for tool in dependencies['binaries'].values():
        assert tool['exists'] and sha(Path(tool['path'])) == tool['sha256'], 'tool bytes drift: ' + str(tool['path'])
    qa.verify_freeze(REPO)
    identity = check_identity(args)
    output = require_new_output(args.output, OUTPUT_ROOT)
    output.mkdir(parents=True)
    write(output / 'candidate-identity.json', identity)
    for filename in ('preparation.json', 'dependencies.json'):
        shutil.copyfile(preparation_dir / filename, output / filename)
    write(output / 'runner-source-hashes.json', {
        str(path): sha(path) for path in (ROOT / 'run.py', ROOT / 'behavior_support.py', ROOT / 'prepare.py')})
    client = qa.Client(args.http_base, None)
    records = []
    products = []
    started = time.time()
    for case in preparation['cases']:
        directory = output / case['id']
        directory.mkdir()
        assert sha(Path(case['prompt_path'])) == case['prompt_sha256']
        assert sha(Path(case['cnl_path'])) == case['cnl_sha256']
        prompt = json.loads(Path(case['prompt_path']).read_text())
        cnl = Path(case['cnl_path']).read_text()
        shutil.copyfile(case['prompt_path'], directory / 'prompt.json')
        shutil.copyfile(case['cnl_path'], directory / 'source.cnl')
        shutil.copyfile(case['origin_path'], directory / 'origin.json')
        record = {'id': case['id'], 'known_checker_limits': preparation['known_checker_limits'][case['id']],
                  'compile_equivalence': False, 'strict_quality_passed': False,
                  'unchanged_full_m2_acceptance': False, 'independent_focused_behavior_passed': False,
                  'quality_status': 'UNRUN', 'unchanged_full_m2_status': 'UNRUN',
                  'independent_focused_behavior_status': 'UNRUN'}
        records.append(record)

        def compile_assemble():
            bundles = {}
            files = {}
            problems = []
            requests = {'json': {'format': 'som.prompt-json.v1', 'prompt': prompt},
                        'cnl': {'format': 'som.cnl.v1', 'source': cnl}}
            for label, request in requests.items():
                try:
                    response = checked_call(client, directory, 'compile-' + label, '/v1/compile', request)
                    assert isinstance(response.get('bundle'), dict)
                    assert qa.exact_equal(response['bundle']['prompt'], prompt)
                    bundle_path = directory / ('compile-' + label + '.bundle.json')
                    write(bundle_path, response['bundle'])
                    # Assemble the complete saved bundle without replacing plan or hashes.
                    saved = json.loads(bundle_path.read_text())
                    assert qa.exact_equal(saved, response['bundle'])
                    bundles[label] = saved
                    assembled = checked_call(client, directory, 'assemble-' + label, '/v1/assemble', {'bundle': saved})
                    assert isinstance(assembled.get('files'), dict)
                    files[label] = assembled['files']
                    write(directory / ('assemble-' + label + '.files.json'), files[label])
                except Exception as exc:
                    failure = {'format': label, 'error': str(exc), 'traceback': traceback.format_exc()}
                    problems.append(failure)
                    write(directory / ('compile-assemble-' + label + '.failure.json'), failure)
            assert not problems, problems
            assert qa.exact_equal(bundles['json'], bundles['cnl']), 'complete CNL/JSON bundle difference'
            assert qa.exact_equal(files['json'], files['cnl']), 'complete CNL/JSON product difference'
            for kind in ('product', 'focused-product'):
                write_product(directory / kind, files['json'])
            return {'passed': True, 'file_sha256': {name: hashlib.sha256(source.encode()).hexdigest()
                                                   for name, source in files['json'].items()}}

        record['compile_assemble'] = stage(directory, 'compile-assemble', compile_assemble)
        record['compile_equivalence'] = record['compile_assemble'].get('completed') is True
        if record['compile_equivalence']:
            record['quality'] = stage(directory, 'strict-quality',
                                      lambda: quality_verify(directory / 'product', directory / 'quality'))
            record['strict_quality_passed'] = record['quality'].get('outcome', {}).get('passed') is True
            record['quality_status'] = 'PASS' if record['strict_quality_passed'] else 'FAIL'
            products.append((case, prompt, directory, record))
        write(directory / 'case-result.json', record)
        print(json.dumps({'id': case['id'], 'compile_equivalence': record['compile_equivalence'],
                          'strict_quality_passed': record['strict_quality_passed']}), flush=True)

    fixture_failure = None
    if products:
        try:
            with (output / 'fixture.stdout.log').open('w') as stdout, \
                    (output / 'fixture.stderr.log').open('w') as stderr, \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), interruption():
                fixture_owner = M3Fixture(output / 'fixture')
                with fixture_owner as fixture:
                    write(output / 'fixture-manifest.json', fixture)
                    assert fixture['test_only'] is True and fixture['m3_postgresql']['host'] == '127.0.0.1'
                    for case, prompt, directory, record in products:
                        # This unchanged result stays separate from focused observations.
                        record['existing_behavior'] = stage(directory, 'unchanged-full-m2',
                            lambda: flow_verify(prompt, directory / 'product', fixture))
                        record['unchanged_full_m2_acceptance'] = (
                            record['existing_behavior'].get('outcome', {}).get('full_m2_acceptance') is True)
                        record['unchanged_full_m2_status'] = 'PASS' if record['unchanged_full_m2_acceptance'] else 'FAIL'
                        record['focused_behavior'] = stage(directory, 'independent-focused-behavior',
                            lambda: focused_behavior(case['id'], prompt, directory / 'focused-product',
                                                     fixture, directory / 'independent-checks'))
                        record['independent_focused_behavior_passed'] = (
                            record['focused_behavior'].get('outcome', {}).get('passed') is True)
                        record['independent_focused_behavior_status'] = 'PASS' if record['independent_focused_behavior_passed'] else 'FAIL'
                        record['cleanup'] = cleanup_after_behavior(output, [
                            directory / 'product/.venv', directory / 'focused-product/.venv',
                            directory / 'quality/mypy-cache'], behavior_complete=True)
                        write(directory / 'cleanup-receipt.json', record['cleanup'])
                        write(directory / 'case-result.json', record)
                        write(output / 'partial-results.json', records)
        except Exception as exc:
            fixture_failure = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
            write(output / 'fixture-failure.json', fixture_failure)
    identity_after = check_identity(args)
    write(output / 'candidate-identity-after.json', identity_after)
    identity_stable = identity_after == identity
    summary = {'version': 'som.compiler-renderer-complete-product-result.v1',
               'candidate_after': identity_after, 'candidate_identity_stable': identity_stable,
               'cleanup_passed': all(row.get('cleanup', {}).get('passed') is True for row in records),
               'candidate': identity, 'started_unix': started, 'completed_unix': time.time(), 'cases': records,
               'compile_pairs_passed': sum(row['compile_equivalence'] for row in records),
               'strict_quality_passed': sum(row['strict_quality_passed'] for row in records),
               'unchanged_full_m2_passed': sum(row['unchanged_full_m2_acceptance'] for row in records),
               'independent_focused_behavior_passed': sum(row['independent_focused_behavior_passed'] for row in records),
               'fixture_failure': fixture_failure,
               'full_m2_pass_claim': 'Only exact unchanged verifier true outcomes count; fallback never converts a limit or failure into this pass.',
               'production_changes': [], 'model_run': False,
               'controller_acceptance': 'Root must review complete results and explicit verifier limitations.'}
    write(output / 'summary.json', summary)
    print(json.dumps({key: summary[key] for key in ('compile_pairs_passed', 'strict_quality_passed',
                     'unchanged_full_m2_passed', 'independent_focused_behavior_passed', 'fixture_failure')}), flush=True)
    # These exact historical-checker failures have separate focused coverage.
    # They remain failed full-M2 results, never promoted to full-M2 passes.
    fixture_error = '{"detail":[{"type":"extra_forbidden","loc":["body","parent","title"],"msg":"Extra inputs are not permitted"}]}'
    expected_limits = {
        'PUBLIC_v7-branch-null': ('AssertionError', fixture_error),
        'PUBLIC_v7-empty-branch': ('AssertionError', fixture_error),
        'PUBLIC_v7-empty-field-segments': ('KeyError', "''"),
        'REF_BRANCH_EXPORT_SAME_TYPE': ('AssertionError', 'unresolved branch probes: choose'),
        **{'REF_COMPARE_' + op: ('AssertionError', 'unresolved branch probes: compare')
           for op in ('EQ', 'NE', 'LT', 'LE', 'GT', 'GE')},
    }
    scope_rows = []
    for row in records:
        original = row.get('existing_behavior', {})
        if row['id'] in expected_limits:
            kind, error = expected_limits[row['id']]
            matched = (original.get('completed') is False
                       and original.get('error_type') == kind and original.get('error') == error
                       and row['unchanged_full_m2_acceptance'] is False)
            scope_rows.append({'id': row['id'], 'full_m2_status': 'FAIL',
                               'known_checker_limit_matched': matched,
                               'expected_error_type': kind, 'expected_error': error})
        else:
            matched = row['id'] == 'REF_NULLABLE_OPTIONAL_FIELD' and row['unchanged_full_m2_acceptance'] is True
            scope_rows.append({'id': row['id'], 'full_m2_status': row['unchanged_full_m2_status'],
                               'required_full_m2_pass': matched})
    expected_ids = set(expected_limits) | {'REF_NULLABLE_OPTIONAL_FIELD'}
    scope_matched = (len(scope_rows) == 11
                     and len({row['id'] for row in scope_rows}) == 11
                     and {row['id'] for row in scope_rows} == expected_ids) and all(
        row.get('known_checker_limit_matched', row.get('required_full_m2_pass', False))
        for row in scope_rows)
    summary['unchanged_m2_scope'] = scope_rows
    summary['unchanged_m2_scope_matched'] = scope_matched
    summary['scoped_required_passed'] = (
        len(records) == 11 and fixture_failure is None and identity_stable and scope_matched
        and summary['cleanup_passed'] and all(row['compile_equivalence']
        and row['strict_quality_passed'] and row['independent_focused_behavior_passed'] for row in records))
    write(output / 'unchanged-m2-scope.json', scope_rows)
    write(output / 'summary.json', summary)
    physical_data = (output / 'fixture/postgres', output / 'fixture/providers/mongo')
    write(output / 'artifact-hashes.json', {str(path.relative_to(output)): sha(path)
                                           for path in sorted(output.rglob('*')) if path.is_file()
                                           and '.venv' not in path.relative_to(output).parts
                                           and '__pycache__' not in path.relative_to(output).parts
                                           and not any(path.is_relative_to(root) for root in physical_data)
                                           and path.name != 'artifact-hashes.json'})
    return 0 if summary['scoped_required_passed'] else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='use only after root explicitly approves a final candidate run')
    parser.add_argument('--http-base')
    parser.add_argument('--candidate-binary', type=Path)
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--candidate-pid', type=int)
    parser.add_argument('--approval-label')
    parser.add_argument('--preparation', type=Path, default=DEFAULT_PREPARATION)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.execute:
        print('UNRUN. Preparation only. Wait for root candidate identity and explicit execution instruction.')
        return 0
    if not all((args.http_base, args.candidate_binary, args.candidate_sha256,
                args.candidate_pid, args.approval_label, args.output)):
        parser.error('--execute requires all candidate identity, approval label, and output arguments')
    return execute(args)

if __name__ == '__main__':
    raise SystemExit(main())
