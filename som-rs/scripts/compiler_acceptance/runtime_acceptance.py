#!/usr/bin/env python3
"""Prepared runtime boundary checker for the frozen SOM compiler contract.

This file is not run during preparation. When root invokes it, it starts only
candidate child processes on OS-selected loopback ports and records exact
cleanup. All fixtures come from public frozen v6/v7 examples and contracts.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path: sys.path.insert(0, str(CODE_DIR))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
ROOT = find_repo(__file__)
OUTPUT_ROOT = default_output_root(ROOT)
CONTRACT = ROOT / 'som-rs/contracts/compiler-v1'
EXAMPLES = CONTRACT / 'examples'
COMPILE_LIMIT = 262_144
GENERATE_LIMIT = 262_144
ASSEMBLE_LIMIT = 2_097_152
EXPECTED_COMPACT_CASE = {'literal': '中' * 22_000, 'type': 'string'}
REQUIRED_CASES = [
    'health_contract', 'cli_compile_full_output', 'legacy_generate_cli_full_output',
    'cli_generate_full_response_matches_http', 'cli_bare_bundle_canonical_assemble',
    'explicit_cnl_format_accepted', 'explicit_json_format_accepted',
    'explicit_formats_same_bundle', 'missing_format', 'unsupported_format',
    'explicit_cnl_format_missing_source', 'duplicate_json_key', 'invalid_utf8',
    'compile_413', 'assemble_413', 'generate_preserved_413',
    'large_valid_compact_output', 'saved_bundle_replay_fresh_process',
    'tamper_block', 'tamper_formatter', 'tamper_profile', 'tamper_manifest',
    'self_signed_block', 'missing_required_asset', 'loaded_formatter_changed',
    'loaded_profile_changed', 'cli_http_reject_compile', 'cli_http_reject_assemble',
    'cli_missing_file_compile', 'cli_missing_file_assemble',
    'cli_invalid_utf8_compile', 'cli_invalid_utf8_assemble',
    'cli_duplicate_bundle_json', 'cli_connection_refused_compile',
    'cli_connection_refused_assemble', 'cli_nonloopback_compile',
    'cli_nonloopback_assemble',
]
CONTRACT_INPUTS = {
    'bundle_cases': CONTRACT / 'bundle-cases.json',
    'acceptance_plan': CONTRACT / 'acceptance-plan.json',
    'api_schema': CONTRACT / 'api.schema.json',
    'bundle_schema': CONTRACT / 'bundle.schema.json',
    'v6_prompt': EXAMPLES / 'v6.prompt.json',
    'v7_prompt': EXAMPLES / 'v7.prompt.json',
    'v7_cnl': EXAMPLES / 'v7.cnl',
    'output_budget': ROOT / 'som-rs/contracts/m2-output-budget.v2.json',
}

import qa_harness as qa  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'preserve existing evidence: {path}')
    path.write_bytes(data)


def write_json(path: Path, value: Any) -> None:
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def record_http(directory: Path, name: str, method: str, url: str,
                request_bytes: bytes | None, status: int, response_bytes: bytes,
                expected: dict[str, Any]) -> dict[str, Any]:
    if request_bytes is not None:
        write_bytes(directory / f'{name}.request.raw', request_bytes)
    write_bytes(directory / f'{name}.response.raw', response_bytes)
    try:
        parsed: Any = json.loads(response_bytes)
        write_json(directory / f'{name}.response.json', parsed)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    actual = {'http_status': status, 'body_sha256': sha(response_bytes),
              'body_bytes': len(response_bytes), 'json': parsed}
    row = {'method': method, 'url': url, 'request_bytes': len(request_bytes or b''),
           'request_sha256': sha(request_bytes or b''), 'response': actual,
           'expected': expected, 'passed': False}
    if 'http_status' in expected:
        row['passed'] = status == expected['http_status']
    if 'code' in expected:
        row['passed'] = row['passed'] and isinstance(parsed, dict) and parsed.get('code') == expected['code']
    if 'status' in expected:
        row['passed'] = row['passed'] and isinstance(parsed, dict) and parsed.get('status') == expected['status']
    return row


def request(base: str, path: str, body: bytes | None = None,
            method: str = 'POST') -> tuple[int, bytes]:
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json'} if body is not None else {})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request_obj, file_pointer, code, message, headers, new_url):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def has_exact_typed_result_field(files: dict[str, Any], field: str, expected: str) -> bool:
    """Find an exact typed result string in generated Python without executing it."""
    for filename, source in files.items():
        if not isinstance(filename, str) or not filename.endswith('.py') or not isinstance(source, str):
            continue
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            continue
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == 'model_validate'
                     and node.args and isinstance(node.args[0], ast.Dict)):
            parent = parents.get(call)
            if isinstance(parent, ast.Assign):
                output_assignment = any(isinstance(target, ast.Name) and target.id == 'output'
                                        for target in parent.targets)
            elif isinstance(parent, ast.AnnAssign):
                output_assignment = isinstance(parent.target, ast.Name) and parent.target.id == 'output'
            else:
                output_assignment = False
            if not output_assignment:
                continue
            result = call.args[0]
            for key_node, value_node in zip(result.keys, result.values):
                if not isinstance(key_node, ast.Constant) or key_node.value != field:
                    continue
                try:
                    value = ast.literal_eval(value_node)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    continue
                if type(value) is str and value == expected:
                    return True
    return False


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def canonical_hash(value: Any) -> str:
    return sha(qa.canonical(value).encode('utf-8'))


def load_public_prompt(version: int) -> dict[str, Any]:
    value = json.loads((EXAMPLES / f'v{version}.prompt.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != f'som.prompt.v{version}':
        raise ValueError(f'frozen public v{version} prompt is malformed')
    return value


def check_bundle(bundle: Any, prompt: dict[str, Any], asset_index: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(bundle, dict):
        return ['compile result is not a bundle object']
    if set(bundle) != {'version', 'prompt', 'dsl', 'assets', 'hashes'}:
        errors.append('bundle root keys differ from the frozen bundle shape')
        return errors
    if bundle.get('version') != 'som.dsl-bundle.v1':
        errors.append('wrong bundle version')
    if bundle.get('prompt') != prompt:
        errors.append('bundle prompt differs from the independent fixture')
    if not isinstance(bundle.get('dsl'), dict):
        errors.append('bundle DSL is not an object')
    assets = bundle.get('assets')
    expected_assets = asset_index.get('by_prompt_version', {}).get(prompt['version'])
    if assets != expected_assets:
        errors.append('bundle assets differ from the independently frozen per-version index')
    hashes = bundle.get('hashes')
    expected = {
        'prompt_sha256': canonical_hash(prompt),
        'dsl_sha256': canonical_hash(bundle.get('dsl')),
        'payload_sha256': canonical_hash({k: bundle[k] for k in ('version', 'prompt', 'dsl', 'assets')}),
    }
    if hashes != expected:
        errors.append('bundle hashes differ from independently recomputed canonical hashes')
    return errors


def next_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


class Service:
    def __init__(self, binary: Path, assets: Path, log_path: Path):
        self.binary = binary
        self.assets = assets
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None
        self.log_file = None
        self.port = next_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.cleanup_action = 'not_started'
        self.closed = False
        self.cleanup_record: dict[str, Any] | None = None

    def start(self, timeout: float = 15.0) -> bool:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_path.open('wb')
        self.process = subprocess.Popen(
            [str(self.binary), 'serve', '--assets', str(self.assets), '--port', str(self.port)],
            stdin=subprocess.DEVNULL, stdout=self.log_file, stderr=subprocess.STDOUT,
            close_fds=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.log_file.flush()
                return False
            try:
                status, raw = request(self.url, '/healthz', None, method='GET')
                if status == 200:
                    self.health = json.loads(raw)
                    return True
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                pass
            time.sleep(0.05)
        return False

    def close(self) -> dict[str, Any]:
        if self.cleanup_record is not None:
            return self.cleanup_record
        code = self.process.poll() if self.process else None
        if self.process and code is None:
            self.process.terminate()
            self.cleanup_action = 'terminate_then_wait'
            try:
                code = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.cleanup_action = 'kill_after_terminate_timeout'
                code = self.process.wait(timeout=5)
        elif self.process:
            self.cleanup_action = 'already_exited'
        if self.log_file and not self.closed:
            self.log_file.flush()
            self.log_file.close()
            self.closed = True
        self.cleanup_record = {'pid': self.process.pid if self.process else None,
                               'port': self.port, 'exit_code_after_cleanup': code,
                               'cleanup_action': self.cleanup_action,
                               'log_path': str(self.log_path)}
        return self.cleanup_record


def save_cli(output: Path, name: str, argv: list[str], result: subprocess.CompletedProcess[bytes]) -> dict[str, Any]:
    write_bytes(output / f'{name}.stdout.raw', result.stdout)
    write_bytes(output / f'{name}.stderr.raw', result.stderr)
    return {'argv': argv, 'exit_code': result.returncode,
            'stdout_sha256': sha(result.stdout), 'stdout_bytes': len(result.stdout),
            'stderr_sha256': sha(result.stderr), 'stderr_bytes': len(result.stderr),
            'stdout_path': str(output / f'{name}.stdout.raw'),
            'stderr_path': str(output / f'{name}.stderr.raw')}


def run_cli(argv: list[str], timeout: float = 180.0) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True)
    cleanup = 'normal_exit'
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        child.terminate()
        cleanup = 'terminate_then_wait'
        try:
            stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            cleanup = 'kill_after_terminate_timeout'
            stdout, stderr = child.communicate(timeout=5)
    result = subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
    return result, {'pid': child.pid, 'exit_code': child.returncode, 'cleanup_action': cleanup}


def cli_diagnostic_case(output: Path, name: str, argv: list[str], expected_exit: int,
                        required_code: str | None = None) -> dict[str, Any]:
    result, cleanup = run_cli(argv, timeout=120)
    row = save_cli(output, name, argv, result)
    row['child_cleanup'] = cleanup
    try:
        diagnostic = json.loads(result.stderr)
    except (UnicodeDecodeError, json.JSONDecodeError):
        diagnostic = None
    row['stderr_is_json'] = isinstance(diagnostic, dict)
    row['diagnostic_code'] = diagnostic.get('code') if isinstance(diagnostic, dict) else None
    row['expected_exit_code'] = expected_exit
    row['required_code'] = required_code
    row['passed'] = result.returncode == expected_exit and isinstance(diagnostic, dict)
    if required_code is not None:
        row['passed'] = row['passed'] and row['diagnostic_code'] == required_code
    if expected_exit == 2:
        row['passed'] = row['passed'] and not result.stdout
    return row


def finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    present = {item.get('id') for item in report['checks']}
    for name in REQUIRED_CASES:
        if name not in present:
            report['checks'].append({'id': name, 'status': 'unrun', 'passed': False,
                                     'detail': 'required case produced no evidence'})
            report.setdefault('unrun', []).append(name)
    report['required_case_ids'] = REQUIRED_CASES
    report['required_case_count'] = len(REQUIRED_CASES)
    report['status'] = ('passed' if len(report['checks']) == len(REQUIRED_CASES)
                        and all(item.get('passed') is True for item in report['checks'])
                        else 'failed')
    report['passed'] = report['status'] == 'passed'
    return report


def startup_refusal(binary: Path, base_assets: Path, output: Path, name: str,
                    mutate: str, self_sign: bool = False, remove: str | None = None) -> dict[str, Any]:
    destination = output / 'startup-fixtures' / name / 'assets'
    shutil.copytree(base_assets, destination)
    target = destination / (remove or mutate)
    if remove:
        target.unlink()
    elif mutate == 'manifest.json':
        os.chmod(target, target.stat().st_mode | 0o200)
        manifest = json.loads(target.read_text(encoding='utf-8'))
        manifest['files']['blocks.json'] = '0' * 64
        target.write_bytes((qa.canonical(manifest) + '\n').encode('utf-8'))
    else:
        os.chmod(target, target.stat().st_mode | 0o200)
        data = target.read_bytes()
        target.write_bytes(data + b'\x00')
        if self_sign:
            manifest_path = destination / 'manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['files'][mutate] = sha(target.read_bytes())
            manifest_path.write_bytes((qa.canonical(manifest) + '\n').encode('utf-8'))
    service = Service(binary, destination, output / 'startup-fixtures' / name / 'process.log')
    started = False
    try:
        started = service.start(timeout=2.0)
    finally:
        cleanup = service.close()
    log = service.log_path.read_bytes()
    expected_code = 'ASSET_UNAVAILABLE' if remove else 'ASSET_HASH_MISMATCH'
    code_present = expected_code.encode('ascii') in log
    return {'id': name, 'expected': 'startup_refusal', 'mutated_asset': remove or mutate,
            'self_signed_local_manifest': self_sign, 'server_accepted': started,
            'expected_error_code': expected_code, 'error_code_in_log': code_present,
            'process_exit_code': cleanup['exit_code_after_cleanup'],
            'log_sha256': sha(log), 'log_path': str(service.log_path),
            'passed': (not started and cleanup['exit_code_after_cleanup'] not in (None, 0)
                       and code_present),
            'cleanup': cleanup}


def run(binary: Path, assets: Path, asset_index_path: Path, output: Path) -> dict[str, Any]:
    for candidate in (binary, assets, asset_index_path):
        if not candidate.exists():
            raise FileNotFoundError(candidate)
    if not binary.is_file() or not assets.is_dir():
        raise ValueError('candidate binary must be a file and candidate assets must be a directory')
    output = require_new_output(output, OUTPUT_ROOT)
    asset_index = json.loads(asset_index_path.read_text(encoding='utf-8'))
    index_errors = qa.validate_asset_index_document(asset_index)
    if index_errors:
        raise ValueError('invalid independent asset index: ' + '; '.join(index_errors))
    output.mkdir(parents=True)
    for name in ('http', 'cli', 'replay', 'startup-fixtures', 'at-use-tamper'):
        (output / name).mkdir()

    prompt_v7 = load_public_prompt(7)
    prompt_v6 = load_public_prompt(6)
    report: dict[str, Any] = {
        'version': 'som.compiler-runtime-boundary-result.v1', 'status': 'running',
        'candidate_binary': str(binary), 'candidate_binary_sha256': sha(binary.read_bytes()),
        'assets_path': str(assets), 'asset_index_path': str(asset_index_path),
        'asset_index_sha256': sha(asset_index_path.read_bytes()),
        'frozen_input_hashes': {name: sha(path.read_bytes()) for name, path in CONTRACT_INPUTS.items()},
        'scope': 'Runtime boundary and replay checks only; no corpus/model work.',
        'checks': [], 'processes': [], 'unrun': [], 'passed': False,
    }

    server = Service(binary, assets, output / 'http/main-process.log')
    bare_bundle = None
    generate_json = None
    try:
        if not server.start():
            report['checks'].append({'id': 'health_contract', 'passed': False,
                                     'detail': 'candidate did not become healthy'})
            report['unrun'].append('valid startup failed; remaining required cases could not run')
            report['processes'].append(server.close())
            finalize_report(report)
            write_json(output / 'result.json', report)
            return report
        health = server.health
        write_json(output / 'http/health.json', health)
        manifest = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))
        health_hash_checks = {
            'asset_manifest_sha256': health.get('asset_manifest_sha256') == canonical_hash(manifest),
            'backend_sha256': health.get('backend_sha256') == canonical_hash(manifest.get('backend')),
            'current_blocks_sha256': health.get('blocks_sha256') == sha((assets / 'blocks.json').read_bytes()),
            'legacy_blocks_sha256': health.get('legacy_blocks_sha256') == sha((assets / 'legacy-blocks.json').read_bytes()),
            'quality_blocks_sha256': health.get('quality_blocks_sha256') == sha((assets / 'quality-blocks.json').read_bytes()),
            'formatter_sha256': health.get('formatter_sha256') == sha((assets / 'ruff').read_bytes()),
        }
        report['checks'].append({
            'id': 'health_contract',
            'expected': {'purpose': 'som.rust-compiler.v1', 'model_loaded': False},
            'actual': health,
            'hash_checks': health_hash_checks,
            'passed': (health.get('purpose') == 'som.rust-compiler.v1'
                      and health.get('model_loaded') is False
                      and all(health_hash_checks.values())),
        })
        report['processes'].append({'name': 'main', 'pid': server.process.pid,
                                    'port': server.port, 'log_path': str(server.log_path)})

        # Full old CLI envelopes and a bare saved-bundle replay.
        cnl_path = EXAMPLES / 'v7.cnl'
        bundle_path = output / 'cli/compiled-bare-bundle.json'
        compile_argv = [str(binary), 'compile', str(cnl_path), '--url', server.url]
        compile_run, compile_cleanup = run_cli(compile_argv, timeout=120)
        compile_record = save_cli(output / 'cli', 'compile-v7', compile_argv, compile_run)
        compile_record['child_cleanup'] = compile_cleanup
        try:
            bare_bundle = json.loads(compile_run.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        if isinstance(bare_bundle, dict):
            write_json(bundle_path, bare_bundle)
        compile_errors = check_bundle(bare_bundle, prompt_v7, asset_index)
        compile_record['bundle_errors'] = compile_errors
        compile_record['passed'] = compile_run.returncode == 0 and not compile_errors
        report['checks'].append({'id': 'cli_compile_full_output', **compile_record})

        generate_argv = [str(binary), 'generate', str(EXAMPLES / 'v7.prompt.json'), '--url', server.url]
        generate_run, generate_cleanup = run_cli(generate_argv, timeout=180)
        generate_record = save_cli(output / 'cli', 'generate-v7', generate_argv, generate_run)
        generate_record['child_cleanup'] = generate_cleanup
        try:
            generate_json = json.loads(generate_run.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            generate_json = None
        generate_record['response_shape_valid'] = isinstance(generate_json, dict) and generate_json.get('status') == 'ok' and isinstance(generate_json.get('files'), dict)
        generate_record['passed'] = generate_run.returncode == 0 and generate_record['response_shape_valid']
        report['checks'].append({'id': 'legacy_generate_cli_full_output', **generate_record})

        direct_generate_request = json_bytes(prompt_v7)
        direct_status, direct_raw = request(server.url, '/v1/generate', direct_generate_request)
        direct_row = record_http(output / 'http', 'direct-v7-generate', 'POST',
                                 server.url + '/v1/generate', direct_generate_request,
                                 direct_status, direct_raw, {'http_status': 200, 'status': 'ok'})
        direct_json = direct_row['response']['json']
        direct_row['matches_cli_full_response'] = direct_json == generate_json
        direct_row['passed'] = direct_row['passed'] and direct_row['matches_cli_full_response']
        report['checks'].append({'id': 'cli_generate_full_response_matches_http', **direct_row})

        if bare_bundle is not None:
            assemble_argv = [str(binary), 'assemble', str(bundle_path), '--url', server.url]
            assemble_run, assemble_cleanup = run_cli(assemble_argv, timeout=180)
            assemble_record = save_cli(output / 'cli', 'assemble-v7', assemble_argv, assemble_run)
            assemble_record['child_cleanup'] = assemble_cleanup
            try:
                assemble_json = json.loads(assemble_run.stdout)
            except (UnicodeDecodeError, json.JSONDecodeError):
                assemble_json = None
            assemble_record['files_match_generate'] = (
                isinstance(assemble_json, dict) and isinstance(generate_json, dict)
                and assemble_json.get('files') == generate_json.get('files'))
            assemble_record['passed'] = assemble_run.returncode == 0 and assemble_record['files_match_generate']
            report['checks'].append({'id': 'cli_bare_bundle_canonical_assemble', **assemble_record})

            # CLI HTTP failures keep exit 2 and a complete JSON error on stderr.
            invalid_cnl_path = output / 'cli/http-rejected.cnl'
            write_bytes(invalid_cnl_path, b'This is not valid SOM CNL.\n')
            argv = [str(binary), 'compile', str(invalid_cnl_path), '--url', server.url]
            row = cli_diagnostic_case(output / 'cli', 'http-reject-compile', argv, 2)
            report['checks'].append({'id': 'cli_http_reject_compile', **row})

            rejected_bundle = copy.deepcopy(bare_bundle)
            rejected_bundle.setdefault('hashes', {})['payload_sha256'] = '0' * 64
            rejected_bundle_path = output / 'cli/http-rejected.bundle.json'
            write_json(rejected_bundle_path, rejected_bundle)
            argv = [str(binary), 'assemble', str(rejected_bundle_path), '--url', server.url]
            row = cli_diagnostic_case(output / 'cli', 'http-reject-assemble', argv, 2,
                                      required_code='BUNDLE_HASH_MISMATCH')
            report['checks'].append({'id': 'cli_http_reject_assemble', **row})

        # Both supported formats are explicit and agree on the frozen public v7 prompt.
        positive_format_rows = []
        formats = [
            ('explicit_cnl_format_accepted', {'format': 'som.cnl.v1',
                                              'source': (EXAMPLES / 'v7.cnl').read_text(encoding='utf-8')}),
            ('explicit_json_format_accepted', {'format': 'som.prompt-json.v1', 'prompt': prompt_v7}),
        ]
        for name, body_value in formats:
            body = json_bytes(body_value)
            status, raw = request(server.url, '/v1/compile', body)
            row = record_http(output / 'http', name, 'POST', server.url + '/v1/compile', body,
                              status, raw, {'http_status': 200, 'status': 'ok'})
            parsed = row['response']['json']
            bundle = parsed.get('bundle') if isinstance(parsed, dict) else None
            row['bundle_errors'] = check_bundle(bundle, prompt_v7, asset_index)
            row['passed'] = row['passed'] and not row['bundle_errors']
            positive_format_rows.append(bundle)
            report['checks'].append({'id': name, **row})
        formats_equal = positive_format_rows[0] == positive_format_rows[1]
        report['checks'].append({'id': 'explicit_formats_same_bundle', 'passed': formats_equal,
                                 'expected': 'CNL and typed JSON compile to the same canonical bundle'})

        # Local file, UTF-8, duplicate JSON, transport, and URL-policy failures.
        missing_cnl = output / 'cli/does-not-exist.cnl'
        missing_bundle = output / 'cli/does-not-exist.bundle.json'
        for name, argv in (
            ('cli_missing_file_compile', [str(binary), 'compile', str(missing_cnl), '--url', server.url]),
            ('cli_missing_file_assemble', [str(binary), 'assemble', str(missing_bundle), '--url', server.url]),
        ):
            row = cli_diagnostic_case(output / 'cli', name, argv, 1, required_code='LOCAL_ERROR')
            report['checks'].append({'id': name, **row})

        invalid_cnl_path = output / 'cli/invalid-utf8.cnl'
        invalid_bundle_path = output / 'cli/invalid-utf8.bundle.json'
        write_bytes(invalid_cnl_path, b'\xff\xfe')
        write_bytes(invalid_bundle_path, b'\xff\xfe')
        for name, argv in (
            ('cli_invalid_utf8_compile', [str(binary), 'compile', str(invalid_cnl_path), '--url', server.url]),
            ('cli_invalid_utf8_assemble', [str(binary), 'assemble', str(invalid_bundle_path), '--url', server.url]),
        ):
            row = cli_diagnostic_case(output / 'cli', name, argv, 1, required_code='LOCAL_ERROR')
            report['checks'].append({'id': name, **row})

        duplicate_bundle_path = output / 'cli/duplicate.bundle.json'
        write_bytes(duplicate_bundle_path, b'{"version":"som.dsl-bundle.v1","version":"som.dsl-bundle.v1"}')
        argv = [str(binary), 'assemble', str(duplicate_bundle_path), '--url', server.url]
        row = cli_diagnostic_case(output / 'cli', 'duplicate-bundle-json', argv, 1,
                                  required_code='INVALID_JSON')
        report['checks'].append({'id': 'cli_duplicate_bundle_json', **row})

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(('127.0.0.1', 0))
            refused_url = f'http://127.0.0.1:{reserved.getsockname()[1]}'
            for name, argv in (
                ('cli_connection_refused_compile', [str(binary), 'compile', str(EXAMPLES / 'v7.cnl'), '--url', refused_url]),
                ('cli_connection_refused_assemble', [str(binary), 'assemble', str(bundle_path), '--url', refused_url]),
            ):
                row = cli_diagnostic_case(output / 'cli', name, argv, 1, required_code='LOCAL_ERROR')
                report['checks'].append({'id': name, **row})

        nonloopback_url = 'http://192.0.2.1:8080'
        for name, argv in (
            ('cli_nonloopback_compile', [str(binary), 'compile', str(EXAMPLES / 'v7.cnl'), '--url', nonloopback_url]),
            ('cli_nonloopback_assemble', [str(binary), 'assemble', str(bundle_path), '--url', nonloopback_url]),
        ):
            row = cli_diagnostic_case(output / 'cli', name, argv, 1, required_code='LOCAL_ERROR')
            report['checks'].append({'id': name, **row})

        # Missing/unsupported format, duplicate-key and invalid UTF-8 boundaries.
        request_cases = [
            ('missing_format', b'{"prompt":{}}', 422, 'UNSUPPORTED_VERSION'),
            ('unsupported_format', b'{"format":"other.v9"}', 422, 'UNSUPPORTED_VERSION'),
            ('explicit_cnl_format_missing_source', b'{"format":"som.cnl.v1"}', 400, 'INVALID_SCHEMA'),
            ('duplicate_json_key', b'{"format":"som.cnl.v1","format":"som.cnl.v1","source":""}', 400, 'INVALID_JSON'),
            ('invalid_utf8', b'{"format":"som.cnl.v1","source":"\xff"}', 400, 'INVALID_JSON'),
        ]
        for name, body, expected_status, expected_code in request_cases:
            status, raw = request(server.url, '/v1/compile', body)
            row = record_http(output / 'http', name, 'POST', server.url + '/v1/compile', body,
                              status, raw, {'http_status': expected_status, 'code': expected_code})
            report['checks'].append({'id': name, **row})

        # Transport limits remain exact at HTTP framing and preserve 413 bodies.
        compile_over = b'{"format":"som.cnl.v1","source":"' + b'x' * COMPILE_LIMIT + b'"}'
        assemble_over = b'{"bundle":{},"padding":"' + b'x' * ASSEMBLE_LIMIT + b'"}'
        generate_over = b'{"version":"som.prompt.v7","padding":"' + b'x' * GENERATE_LIMIT + b'"}'
        for name, path, body in (
            ('compile_413', '/v1/compile', compile_over),
            ('assemble_413', '/v1/assemble', assemble_over),
            ('generate_preserved_413', '/v1/generate', generate_over),
        ):
            status, raw = request(server.url, path, body)
            row = record_http(output / 'http', name, 'POST', server.url + path, body,
                              status, raw, {'http_status': 413})
            report['checks'].append({'id': name, **row})

        # Large legal compact output is observed in full; the literal is independent.
        large_prompt = copy.deepcopy(prompt_v7)
        large_prompt['flow']['result']['label'] = copy.deepcopy(EXPECTED_COMPACT_CASE)
        write_json(output / 'http/large-compact.expected-prompt.json', large_prompt)
        large_request = json_bytes(large_prompt)
        status, raw = request(server.url, '/v1/generate', large_request)
        write_bytes(output / 'http/large-compact.full-response.raw', raw)
        try:
            large_body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            large_body = None
        all_files = large_body.get('files', {}) if isinstance(large_body, dict) else {}
        if not isinstance(all_files, dict):
            all_files = {}
        output_bytes = sum(len(text.encode('utf-8')) for text in all_files.values() if isinstance(text, str))
        contains_literal = has_exact_typed_result_field(all_files, 'label', EXPECTED_COMPACT_CASE['literal'])
        large_passed = (status == 200 and isinstance(large_body, dict) and large_body.get('status') == 'ok'
                        and isinstance(all_files, dict) and output_bytes > 65_536 and contains_literal)
        report['checks'].append({
            'id': 'large_valid_compact_output',
            'http_status': status, 'response_path': str(output / 'http/large-compact.full-response.raw'),
            'response_sha256': sha(raw), 'response_bytes': len(raw),
            'generated_file_count': len(all_files) if isinstance(all_files, dict) else None,
            'formatted_utf8_output_bytes': output_bytes,
            'literal_expected': {'json_pointer': '/flow/result/label', 'type': 'string',
                                 'text_chars': 22_000, 'utf8_bytes': 66_000,
                                 'value_sha256': sha(EXPECTED_COMPACT_CASE['literal'].encode('utf-8'))},
            'expected_observation_only': 'compact bytes have no 65536-byte compiler gate or model token limit',
            'literal_exact': contains_literal, 'passed': large_passed,
        })
    finally:
        report['processes'].append(server.close())

    # Fresh OS process replay uses only the saved bundle and the same pinned assets.
    if bare_bundle is not None:
        replay = Service(binary, assets, output / 'replay/replay-process.log')
        try:
            started = replay.start()
            bundle_payload = json_bytes({'bundle': bare_bundle})
            if started:
                status, raw = request(replay.url, '/v1/assemble', bundle_payload)
                write_bytes(output / 'replay/saved-bundle-assemble.full-response.raw', raw)
                try:
                    body = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                equal = isinstance(body, dict) and isinstance(generate_json, dict) and body.get('files') == generate_json.get('files')
                report['checks'].append({'id': 'saved_bundle_replay_fresh_process',
                                         'http_status': status, 'response_path': str(output / 'replay/saved-bundle-assemble.full-response.raw'),
                                         'response_sha256': sha(raw), 'files_match_direct_generate': equal,
                                         'inputs_used': ['saved bare bundle', 'pinned local assets'],
                                         'cnl_or_model_available_to_replay_call': False,
                                         'passed': status == 200 and equal})
            else:
                report['checks'].append({'id': 'saved_bundle_replay_fresh_process', 'passed': False,
                                         'detail': 'fresh process did not become healthy'})
            report['processes'].append({'name': 'replay', 'pid': replay.process.pid if replay.process else None,
                                        'port': replay.port, 'log_path': str(replay.log_path)})
        finally:
            report['processes'].append(replay.close())

    # Mutate copies only. These are isolated startup-refusal fixtures.
    tamper_cases = [
        ('tamper_block', 'blocks.json', False, None),
        ('tamper_formatter', 'ruff', False, None),
        ('tamper_profile', 'pyproject.toml', False, None),
        ('tamper_manifest', 'manifest.json', False, None),
        ('self_signed_block', 'blocks.json', True, None),
        ('missing_required_asset', '', False, 'legacy-blocks.json'),
    ]
    for name, asset, self_sign, remove in tamper_cases:
        report['checks'].append(startup_refusal(binary, assets, output, name, asset,
                                                self_sign=self_sign, remove=remove))

    # At-use identity checks run after a successful asset admission.
    for name, asset in (('loaded_formatter_changed', 'ruff'), ('loaded_profile_changed', 'pyproject.toml')):
        fixture_assets = output / 'at-use-tamper' / name / 'assets'
        fixture_assets.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(assets, fixture_assets)
        process = Service(binary, fixture_assets, output / 'at-use-tamper' / name / 'process.log')
        try:
            started = process.start()
            response_row = {'id': name, 'expected': 'OUTPUT_QUALITY_FAILED', 'startup_passed': started}
            if started:
                target = fixture_assets / asset
                os.chmod(target, target.stat().st_mode | 0o200)
                target.write_bytes(target.read_bytes() + b'\x00')
                body = json_bytes(prompt_v6)
                status, raw = request(process.url, '/v1/generate', body)
                row = record_http(output / 'at-use-tamper' / name, 'after-mutation', 'POST',
                                  process.url + '/v1/generate', body, status, raw,
                                  {'http_status': 500, 'code': 'OUTPUT_QUALITY_FAILED'})
                response_row.update(row)
                response_row['mutated_asset_sha256'] = sha(target.read_bytes())
                response_row['passed'] = started and row['passed']
            else:
                response_row['passed'] = False
            report['checks'].append(response_row)
            report['processes'].append({'name': name, 'pid': process.process.pid if process.process else None,
                                        'port': process.port, 'log_path': str(process.log_path)})
        finally:
            report['processes'].append(process.close())

    if bare_bundle is None:
        report['unrun'].append('cli_bare_bundle_canonical_assemble (CLI compile did not produce a parseable bundle)')
        report['unrun'].append('CLI HTTP rejection cases requiring a valid seed bundle')
    finalize_report(report)
    write_json(output / 'result.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--asset-index', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=OUTPUT_ROOT / 'runtime-acceptance/run')
    args = parser.parse_args()
    output = require_new_output(args.out, OUTPUT_ROOT)
    result = run(args.binary.resolve(), args.assets.resolve(), args.asset_index.resolve(), output)
    print(json.dumps({'status': result['status'], 'checks': len(result['checks']),
                      'passed': sum(row.get('passed') is True for row in result['checks']),
                      'required': len(result['checks']), 'out': str(args.out.resolve())}, indent=2))
    raise SystemExit(0 if result['passed'] else 2)


if __name__ == '__main__':
    main()
