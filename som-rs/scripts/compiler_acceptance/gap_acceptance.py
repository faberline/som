#!/usr/bin/env python3
"""Prepared checker for structural limits, redirect refusal, and all-family replay.

Preparation does not start a service or run a candidate. At execution this
script starts only owned candidate children on OS-selected loopback ports.
Candidate bundles are mutation/replay inputs only; expected errors and equality
rules come from the frozen contract.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path: sys.path.insert(0, str(CODE_DIR))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
ROOT = find_repo(__file__)
OUTPUT_ROOT = default_output_root(ROOT)
CONTRACT = ROOT / 'som-rs/contracts/compiler-v1'
EXAMPLES = CONTRACT / 'examples' 
import qa_harness as qa
import runtime_acceptance as runtime

VERSIONS = (1, 2, 3, 4, 5, 6, 7)
LIMIT_VERSIONS = (1, 3, 6, 7)
REQUIRED = (
    [f'scope_v{v}_file_limit' for v in LIMIT_VERSIONS]
    + [f'scope_v{v}_block_limit' for v in LIMIT_VERSIONS]
    + [name for v in VERSIONS for name in (
        f'fresh_v{v}_cross_format_bundle',
        f'fresh_v{v}_new_process_compile_bundle_parity',
        f'fresh_v{v}_saved_bundle_replay')]
    + ['redirect_cli_compile', 'redirect_cli_assemble']
)


def write_json(path: Path, value: Any) -> None:
    runtime.write_json(path, value)


def raw_json(value: Any) -> bytes:
    return runtime.json_bytes(value)


def save_exchange(directory: Path, name: str, path: str, body: bytes,
                  status: int, response: bytes, expected: dict[str, Any]) -> dict[str, Any]:
    return runtime.record_http(directory, name, 'POST', path, body, status, response, expected)


def compile_request(directory: Path, service: runtime.Service, version: int,
                    kind: str, prompt: dict[str, Any], cnl: str,
                    asset_index: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    value = ({'format': 'som.prompt-json.v1', 'prompt': prompt} if kind == 'json'
             else {'format': 'som.cnl.v1', 'source': cnl})
    body = raw_json(value)
    status, response = runtime.request(service.url, '/v1/compile', body)
    directory.mkdir(parents=True, exist_ok=True)
    row = save_exchange(directory, f'compile-{kind}', service.url + '/v1/compile', body,
                        status, response, {'http_status': 200, 'status': 'ok'})
    parsed = row['response']['json']
    bundle = parsed.get('bundle') if isinstance(parsed, dict) else None
    row['prompt_exact'] = isinstance(bundle, dict) and qa.exact_equal(bundle.get('prompt'), prompt)
    row['bundle_errors'] = runtime.check_bundle(bundle, prompt, asset_index) if isinstance(bundle, dict) else ['compile returned no bundle']
    row['passed'] = row['passed'] and row['prompt_exact'] and isinstance(bundle, dict) and not row['bundle_errors']
    if isinstance(bundle, dict):
        write_json(directory / f'compile-{kind}.bundle.json', bundle)
    return (bundle if isinstance(bundle, dict) else None), row


def rehash(bundle: dict[str, Any]) -> None:
    bundle['hashes']['prompt_sha256'] = runtime.canonical_hash(bundle['prompt'])
    bundle['hashes']['dsl_sha256'] = runtime.canonical_hash(bundle['dsl'])
    bundle['hashes']['payload_sha256'] = runtime.canonical_hash(
        {key: bundle[key] for key in ('version', 'prompt', 'dsl', 'assets')})


def make_file_overflow(bundle: dict[str, Any], maximum: int) -> dict[str, Any]:
    value = deepcopy(bundle)
    files = value['dsl']['files']
    if not files:
        raise ValueError('independent candidate seed bundle has no DSL files')
    base = deepcopy(files[0])
    package = value['prompt']['package']
    while len(files) <= maximum:
        clone = deepcopy(base)
        clone['path'] = f'{package}/qa_over_limit_{len(files)}.py'
        files.append(clone)
    rehash(value)
    return value


def make_block_overflow(bundle: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(bundle)
    files = value['dsl']['files']
    target = next((row for row in files if isinstance(row.get('blocks'), list) and row['blocks']), None)
    if target is None:
        raise ValueError('independent candidate seed bundle has no DSL block to duplicate')
    block = deepcopy(target['blocks'][0])
    target['blocks'] = [deepcopy(block) for _ in range(65)]
    rehash(value)
    return value


def over_limit_cases(service: runtime.Service, asset_index: dict[str, Any],
                     root: Path, outcomes: dict[str, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    seeds: dict[int, dict[str, Any]] = {}
    for version in LIMIT_VERSIONS:
        prompt = runtime.load_public_prompt(version)
        cnl = (EXAMPLES / f'v{version}.cnl').read_text(encoding='utf-8')
        directory = root / 'scope-seeds' / f'v{version}'
        bundle, row = compile_request(directory, service, version, 'json', prompt, cnl, asset_index)
        row['id'] = f'scope_v{version}_seed_compile'
        row['bundle_errors'] = runtime.check_bundle(bundle, prompt, asset_index) if bundle else ['compile returned no bundle']
        row['passed'] = row['passed'] and not row['bundle_errors']
        outcomes[row['id']] = row
        if bundle is not None and row['passed']:
            seeds[version] = bundle

    for version in LIMIT_VERSIONS:
        bundle = seeds.get(version)
        for kind in ('file', 'block'):
            case_id = f'scope_v{version}_{kind}_limit'
            evidence = root / 'scope-limits' / case_id
            evidence.mkdir(parents=True, exist_ok=True)
            if bundle is None:
                outcomes[case_id] = {'id': case_id, 'passed': False, 'status': 'unrun',
                                     'reason': 'independent public seed compile failed'}
                continue
            maximum = 8 if bundle['dsl']['version'] == 'som.dsl.v1' else 12
            mutated = make_file_overflow(bundle, maximum) if kind == 'file' else make_block_overflow(bundle)
            request_body = raw_json({'bundle': mutated})
            status, response = runtime.request(service.url, '/v1/assemble', request_body)
            row = save_exchange(evidence, 'assemble-over-limit', service.url + '/v1/assemble',
                                request_body, status, response,
                                {'http_status': 422, 'code': 'SCOPE_TOO_LARGE'})
            row.update({'id': case_id, 'prompt_version': version,
                        'file_count': len(mutated['dsl']['files']),
                        'block_count': sum(len(x.get('blocks', [])) for x in mutated['dsl']['files']),
                        'rehashes_recomputed_independently': True})
            outcomes[case_id] = row
    return seeds


def all_family_replay(binary: Path, assets: Path, root: Path,
                      outcomes: dict[str, dict[str, Any]],
                      processes: list[dict[str, Any]],
                      asset_index: dict[str, Any]) -> dict[int, dict[str, Any]]:
    cases = {row['id']: row for row in json.loads((EXAMPLES / 'index.json').read_text(encoding='utf-8'))['cases']}
    bundles: dict[int, dict[str, Any]] = {}
    generated: dict[int, dict[str, Any]] = {}
    for version in VERSIONS:
        case = cases[f'v{version}']
        prompt = json.loads((EXAMPLES / case['prompt']).read_text(encoding='utf-8'))
        cnl = (EXAMPLES / case['cnl']).read_text(encoding='utf-8')
        version_root = root / f'family-v{version}'
        first = runtime.Service(binary, assets, version_root / 'process-a.log')
        try:
            started = first.start()
            if not started:
                for suffix in ('cross_format_bundle', 'new_process_compile_bundle_parity', 'saved_bundle_replay'):
                    key = f'fresh_v{version}_{suffix}'
                    outcomes[key] = {'id': key, 'passed': False, 'status': 'unrun',
                                     'reason': 'owned process A did not become healthy'}
                continue
            json_bundle, json_row = compile_request(version_root / 'process-a', first,
                                                    version, 'json', prompt, cnl, asset_index)
            cnl_bundle, cnl_row = compile_request(version_root / 'process-a', first,
                                                  version, 'cnl', prompt, cnl, asset_index)
            pair_ok = (json_bundle is not None and cnl_bundle is not None
                       and qa.exact_equal(json_bundle, cnl_bundle)
                       and json_row['passed'] and cnl_row['passed'])
            pair_id = f'fresh_v{version}_cross_format_bundle'
            pair = {'id': pair_id, 'passed': pair_ok,
                    'json_compile': json_row, 'cnl_compile': cnl_row,
                    'complete_bundle_equal': json_bundle is not None and cnl_bundle is not None
                    and qa.exact_equal(json_bundle, cnl_bundle)}
            outcomes[pair_id] = pair
            if json_bundle is None:
                for suffix in ('new_process_compile_bundle_parity', 'saved_bundle_replay'):
                    key = f'fresh_v{version}_{suffix}'
                    outcomes[key] = {'id': key, 'passed': False, 'status': 'unrun',
                                     'reason': 'process A did not return an independently checkable bundle'}
                continue
            bundles[version] = json_bundle
            if not pair_ok:
                # Keep exercising the fresh process and replay with the JSON bundle,
                # while preserving CNL/JSON disagreement as an independent failure.
                pass
            prompt_body = raw_json(prompt)
            status, response = runtime.request(first.url, '/v1/generate', prompt_body)
            gen_dir = version_root / 'process-a'
            gen_row = save_exchange(gen_dir, 'direct-generate', first.url + '/v1/generate',
                                    prompt_body, status, response,
                                    {'http_status': 200, 'status': 'ok'})
            parsed = gen_row['response']['json']
            generated[version] = parsed if isinstance(parsed, dict) else {}
            gen_row['id'] = f'process_a_v{version}_direct_generate'
            outcomes[gen_row['id']] = gen_row
        finally:
            close_a = first.close()
            write_json(version_root / 'process-a-cleanup.json', close_a)
            processes.append({'name': f'fresh-v{version}-process-a', **close_a,
                              'log_path': str(first.log_path)})

        if version not in bundles:
            for suffix in ('new_process_compile_bundle_parity', 'saved_bundle_replay'):
                key = f'fresh_v{version}_{suffix}'
                outcomes.setdefault(key, {'id': key, 'passed': False, 'status': 'unrun',
                                          'reason': 'no process-A bundle'})
            continue
        second = runtime.Service(binary, assets, version_root / 'process-b.log')
        try:
            started = second.start()
            if not started:
                for suffix in ('new_process_compile_bundle_parity', 'saved_bundle_replay'):
                    key = f'fresh_v{version}_{suffix}'
                    outcomes[key] = {'id': key, 'passed': False, 'status': 'unrun',
                                     'reason': 'owned process B did not become healthy'}
                continue
            second_bundle, second_row = compile_request(version_root / 'process-b', second,
                                                        version, 'json', prompt, cnl, asset_index)
            equal_bundle = second_bundle is not None and qa.exact_equal(bundles[version], second_bundle)
            parity_id = f'fresh_v{version}_new_process_compile_bundle_parity'
            outcomes[parity_id] = {'id': parity_id, 'passed': equal_bundle and second_row['passed'],
                                   'complete_bundle_equal_across_processes': equal_bundle,
                                   'process_b_compile': second_row}
            bare_path = version_root / 'process-a' / 'compile-json.bundle.json'
            replay_payload = {'bundle': bundles[version]}
            replay_bytes = raw_json(replay_payload)
            replay_status, replay_response = runtime.request(second.url, '/v1/assemble', replay_bytes)
            replay_row = save_exchange(version_root / 'process-b', 'saved-bundle-replay',
                                       second.url + '/v1/assemble', replay_bytes, replay_status,
                                       replay_response, {'http_status': 200, 'status': 'ok'})
            replay_body = replay_row['response']['json']
            files = replay_body.get('files') if isinstance(replay_body, dict) else None
            direct_files = generated.get(version, {}).get('files')
            same_files = isinstance(files, dict) and isinstance(direct_files, dict) and qa.exact_equal(files, direct_files)
            replay_id = f'fresh_v{version}_saved_bundle_replay'
            outcomes[replay_id] = {'id': replay_id, 'passed': replay_row['passed'] and same_files,
                                   'complete_files_equal_direct_generate': same_files,
                                   'bare_bundle_path': str(bare_path),
                                   'replay_uses_only': ['saved complete bundle', 'same pinned local assets'],
                                   'replay_response': replay_row}
        finally:
            close_b = second.close()
            write_json(version_root / 'process-b-cleanup.json', close_b)
            processes.append({'name': f'fresh-v{version}-process-b', **close_b,
                              'log_path': str(second.log_path)})

    return bundles


def redirect_refusal(binary: Path, bundle: dict[str, Any],
                     root: Path, outcomes: dict[str, dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    shared = {'source_hits': 0, 'destination_hits': 0, 'source_requests': []}
    destination = None

    class SinkHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            shared['destination_hits'] += 1
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, fmt, *args):
            return

    sink = ThreadingHTTPServer(('127.0.0.1', 0), SinkHandler)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()
    sink_url = f'http://127.0.0.1:{sink.server_port}/sink'
    expected_error = {'status': 'error', 'code': 'REDIRECT_TEST',
                      'detail': 'redirect body must be preserved'}
    expected_error_raw = json.dumps(expected_error, separators=(',', ':')).encode('utf-8')
    runtime.write_bytes(root / 'redirect-307-response.raw', expected_error_raw)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            shared['source_hits'] += 1
            request_bytes = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            shared['source_requests'].append({'path': self.path,
                                              'content_type': self.headers.get('Content-Type'),
                                              'body': request_bytes})
            body = expected_error_raw
            self.send_response(307)
            self.send_header('Location', sink_url)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, fmt, *args):
            return

    redirect = ThreadingHTTPServer(('127.0.0.1', 0), RedirectHandler)
    thread = Thread(target=redirect.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{redirect.server_port}'
        cases = [
            ('redirect_cli_compile', [str(binary), 'compile', str(EXAMPLES / 'v7.cnl'), '--url', base], 'compile'),
            ('redirect_cli_assemble', None, 'assemble'),
        ]
        bundle_path = root / 'redirect-test.bundle.json'
        write_json(bundle_path, bundle)
        for case_id, argv, command in cases:
            if argv is None:
                argv = [str(binary), 'assemble', str(bundle_path), '--url', base]
            before = dict(shared)
            result, cleanup = runtime.run_cli(argv, timeout=60)
            row = runtime.save_cli(root, case_id, argv, result)
            captured = (shared['source_requests'][-1]
                        if len(shared['source_requests']) == before['source_hits'] + 1 else None)
            raw_request_path = root / f'{case_id}.http-request.raw'
            if captured is not None:
                runtime.write_bytes(raw_request_path, captured['body'])
            try:
                parsed_stderr = json.loads(result.stderr)
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_stderr = None
            row.update({'id': case_id, 'http_method': 'POST', 'http_path': '/v1/' + command,
                        'destination_hits_before': before['destination_hits'],
                        'destination_hits_after': shared['destination_hits'],
                        'source_hits_before': before['source_hits'],
                        'source_hits_after': shared['source_hits'],
                        'http_request_path': str(raw_request_path) if captured is not None else None,
                        'http_request_sha256': runtime.sha(captured['body']) if captured is not None else None,
                        'http_request_bytes': len(captured['body']) if captured is not None else None,
                        'http_request_path_received': captured['path'] if captured is not None else None,
                        'http_content_type': captured['content_type'] if captured is not None else None,
                        'stderr_json': parsed_stderr,
                        'http_response_path': str(root / 'redirect-307-response.raw'),
                        'http_response_sha256': runtime.sha(expected_error_raw),
                        'http_response_bytes': len(expected_error_raw),
                        'error_body_preserved': parsed_stderr == expected_error,
                        'child_cleanup': cleanup,
                        'passed': result.returncode == 2 and result.stdout == b''
                        and parsed_stderr == expected_error and shared['source_hits'] == before['source_hits'] + 1
                        and shared['destination_hits'] == before['destination_hits']})
            outcomes[case_id] = row
    finally:
        redirect.shutdown(); redirect.server_close(); thread.join(timeout=5)
        sink.shutdown(); sink.server_close(); sink_thread.join(timeout=5)
    write_json(root / 'redirect-server-counters.json', {
        'source_hits': shared['source_hits'], 'destination_hits': shared['destination_hits'],
        'source_requests': [{'path': row['path'], 'content_type': row['content_type'],
                             'body_sha256': runtime.sha(row['body']), 'body_bytes': len(row['body'])}
                            for row in shared['source_requests']]})


def finalize(report: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> None:
    for case_id in REQUIRED:
        row = outcomes.get(case_id)
        if row is None:
            row = {'id': case_id, 'passed': False, 'status': 'unrun',
                   'reason': 'required case produced no evidence'}
            outcomes[case_id] = row
    report['checks'] = [outcomes[key] for key in REQUIRED]
    report['required_case_ids'] = list(REQUIRED)
    report['required_case_count'] = len(REQUIRED)
    report['unrun'] = [row['id'] for row in report['checks'] if row.get('status') == 'unrun']
    report['passed'] = len(report['checks']) == len(REQUIRED) and all(row.get('passed') is True for row in report['checks']) and not report['unrun']
    report['status'] = 'passed' if report['passed'] else 'failed_or_incomplete'


def execute(binary: Path, assets: Path, asset_index_path: Path, output: Path) -> dict[str, Any]:
    for path in (binary, assets, asset_index_path):
        if not path.exists():
            raise FileNotFoundError(path)
    if not binary.is_file() or not assets.is_dir():
        raise ValueError('binary must be a file and assets must be a directory')
    output = require_new_output(output, OUTPUT_ROOT)
    asset_index = json.loads(asset_index_path.read_text(encoding='utf-8'))
    errors = qa.validate_asset_index_document(asset_index)
    if errors:
        raise ValueError('invalid independent per-version asset index: ' + '; '.join(errors))
    freeze = qa.verify_freeze(ROOT)
    output.mkdir(parents=True)
    outcomes: dict[str, dict[str, Any]] = {}
    report = {'version': 'som.compiler-gap-acceptance.v1', 'status': 'running',
              'scope': ['rehashed structural limits', 'HTTP 307 redirect refusal',
                        'fresh-process compile and replay for prompt v1-v7'],
              'candidate_binary': str(binary.resolve()),
              'candidate_binary_sha256': runtime.sha(binary.read_bytes()),
              'checker_sha256': runtime.sha(Path(__file__).read_bytes()),
              'assets': str(assets.resolve()), 'asset_index': str(asset_index_path.resolve()),
              'asset_index_sha256': runtime.sha(asset_index_path.read_bytes()),
              'freeze_verification': freeze,
              'expected_source': 'frozen CONTRACT.md, bundle-cases, public v1-v7 examples, and fixed error/status assertions',
              'candidate_outputs_are_inputs_only': True,
              'processes': [], 'checks': [], 'unrun': [], 'passed': False}
    try:
        service = runtime.Service(binary.resolve(), assets.resolve(), output / 'limit-server.log')
        try:
            if service.start():
                seeds = over_limit_cases(service, asset_index, output, outcomes)
                closed = service.close()
                report['processes'].append({'name': 'structural-limit-service', **closed,
                                            'log_path': str(service.log_path)})
                if 7 in seeds:
                    redirect_refusal(binary.resolve(), seeds[7], output / 'redirect', outcomes)
                else:
                    for cid in ('redirect_cli_compile', 'redirect_cli_assemble'):
                        outcomes[cid] = {'id': cid, 'passed': False, 'status': 'unrun',
                                         'reason': 'valid public v7 bundle was not available'}
            else:
                report['unrun'].append('limit server failed startup')
                service.close()
        finally:
            if service.cleanup_record is None:
                report['processes'].append(service.close())
        all_family_replay(binary.resolve(), assets.resolve(), output / 'fresh-replay', outcomes,
                          report['processes'], asset_index)
    except Exception as error:
        report['fatal_error'] = {'type': type(error).__name__, 'detail': str(error)}
    finalize(report, outcomes)
    report['supporting_observations'] = {key: value for key, value in outcomes.items() if key not in REQUIRED}
    write_json(output / 'result.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--asset-index', type=Path, required=True,
                        help='independently generated final per-version asset index')
    parser.add_argument('--out', type=Path, default=OUTPUT_ROOT / 'gap-acceptance/run')
    args = parser.parse_args()
    output = require_new_output(args.out, OUTPUT_ROOT)
    result = execute(args.binary, args.assets, args.asset_index, output)
    print(json.dumps({'status': result['status'], 'passed': sum(x.get('passed') is True for x in result['checks']),
                      'required': result['required_case_count'], 'unrun': result['unrun'],
                      'out': str(args.out.resolve())}, indent=2))
    raise SystemExit(0 if result['passed'] else 2)


if __name__ == '__main__':
    main()
