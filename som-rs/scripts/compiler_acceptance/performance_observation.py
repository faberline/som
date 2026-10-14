#!/usr/bin/env python3
"""Read-only HTTP performance observations for the seven public SOM examples.

This records timings and point-in-time RSS. It defines no performance gate.
The service is never started or changed by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PACKAGE = Path(__file__).resolve().parent
if (PACKAGE / 'qa_harness.py').is_file():
    ACCEPTANCE = PACKAGE
else:
    ACCEPTANCE = Path(os.environ.get('SOM_REPO', '/Users/chrischeng/faberlines/som')) / 'som-rs/scripts/compiler_acceptance'
os.environ.setdefault('SOM_REPO', str(ACCEPTANCE.parent.parent.parent))
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
import qa_harness as qa  # noqa: E402
from public_products import process_identity  # noqa: E402


ROOT = find_repo(__file__) if (Path(__file__).resolve().parent / 'som-rs').is_dir() else Path(os.environ.get('SOM_REPO', '/Users/chrischeng/faberlines/som')).resolve()
OUT_ROOT = default_output_root(ROOT)
DEFAULT_OUT = OUT_ROOT / 'performance-003'
MIN_FREE_BYTES = 256 * 1024 * 1024
ESTIMATED_RESPONSE_ALLOWANCE = 22 * 1024 * 1024


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def exact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def load_examples() -> list[dict[str, Any]]:
    examples_dir = ROOT / 'som-rs/contracts/compiler-v1/examples'
    index = json.loads((examples_dir / 'index.json').read_text(encoding='utf-8'))
    selected = [item for item in index['cases'] if item['id'] in {f'v{i}' for i in range(1, 8)}]
    if {item['id'] for item in selected} != {f'v{i}' for i in range(1, 8)} or len(selected) != 7:
        raise ValueError('public example index must contain exactly v1 through v7')
    result = []
    for item in selected:
        prompt_path = examples_dir / item['prompt']
        cnl_path = examples_dir / item['cnl']
        prompt_raw = prompt_path.read_bytes()
        cnl_raw = cnl_path.read_bytes()
        prompt = json.loads(prompt_raw)
        if prompt.get('version') != f"som.prompt.{item['id']}":
            raise ValueError(f"{item['id']} prompt version does not match its fixture name")
        result.append({
            'id': item['id'], 'prompt': prompt, 'cnl': cnl_raw.decode('utf-8'),
            'prompt_path': str(prompt_path), 'cnl_path': str(cnl_path),
            'prompt_sha256': sha_bytes(prompt_raw), 'cnl_sha256': sha_bytes(cnl_raw),
            'prompt_bytes': len(prompt_raw), 'cnl_bytes': len(cnl_raw),
        })
    return result


def inventory() -> dict[str, Any]:
    examples = load_examples()
    requests = []
    for example in examples:
        json_compile = {'format': 'som.prompt-json.v1', 'prompt': example['prompt']}
        cnl_compile = {'format': 'som.cnl.v1', 'source': example['cnl']}
        for repeat in range(1, 4):
            for stage, body in (
                ('compile_cnl', cnl_compile), ('compile_json', json_compile),
                ('assemble_saved_complete_bundle', {'bundle': '<compile-json bundle saved from this repetition>'}),
                ('generate', example['prompt']),
            ):
                requests.append({
                    'case': example['id'], 'repeat': repeat, 'stage': stage,
                    'request_bytes': None if stage == 'assemble_saved_complete_bundle' else len(exact_json_bytes(body)),
                    'assemble_bytes_deferred_to_saved_bundle': stage == 'assemble_saved_complete_bundle',
                })
    return {
        'version': 'som.compiler-performance-inventory.v1',
        'examples': examples,
        'repetitions_per_example': 3,
        'operations_per_repetition': ['compile_cnl', 'compile_json', 'assemble_saved_complete_bundle', 'generate'],
        'request_count': len(requests), 'requests': requests,
        'estimated_disk_bytes': ESTIMATED_RESPONSE_ALLOWANCE,
        'estimated_disk_mib': round(ESTIMATED_RESPONSE_ALLOWANCE / (1024 * 1024), 2),
        'estimate_note': 'Approximate raw-response allowance plus metadata; it is not a response cap or acceptance threshold.',
        'measurement_note': 'RSS is sampled after each complete HTTP response. It is a point-in-time process snapshot, not peak or global memory; native Ruff child memory is outside this observation.',
        'performance_thresholds': None,
    }


def raw_http(base_url: str, path: str, body_raw: bytes) -> tuple[int, list[list[str]], bytes, float]:
    request = urllib.request.Request(
        base_url.rstrip('/') + path, data=body_raw,
        headers={'Content-Type': 'application/json'}, method='POST',
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    started = time.perf_counter_ns()
    try:
        with opener.open(request, timeout=120) as response:
            raw = response.read()
            status = response.status
            headers = [[str(k), str(v)] for k, v in response.headers.raw_items()]
    except urllib.error.HTTPError as error:
        raw = error.read()
        status = error.code
        headers = [[str(k), str(v)] for k, v in error.headers.raw_items()]
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return status, headers, raw, elapsed_ms


def rss_snapshot(pid: int) -> dict[str, Any]:
    result = subprocess.run(
        ['ps', '-ww', '-p', str(pid), '-o', 'pid=,rss=,comm='],
        capture_output=True, text=True, check=False, timeout=10,
    )
    fields = result.stdout.strip().split(None, 2)
    if result.returncode != 0 or len(fields) != 3 or int(fields[0]) != pid:
        return {'pid': pid, 'available': False, 'returncode': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    return {'pid': pid, 'available': True, 'rss_kib': int(fields[1]), 'command': fields[2]}


def bind_candidate(args: argparse.Namespace, asset_index: dict[str, Any]) -> dict[str, Any]:
    binary = args.candidate_binary.resolve(strict=True)
    source_path = args.source_json.resolve(strict=True)
    source = json.loads(source_path.read_text(encoding='utf-8'))
    archive = source_path.parent / 'source.tar'
    build_path = args.build_receipt.resolve(strict=True)
    runtime_path = args.candidate_runtime.resolve(strict=True)
    runtime_acceptance_path = args.runtime_receipt.resolve(strict=True)
    asset_root = source_path.parent / 'assets'
    manifest_path = asset_root / 'manifest.json'
    source_archive_sha = sha_bytes(archive.read_bytes())
    source_json_sha = sha_bytes(source_path.read_bytes())
    binary_sha = sha_bytes(binary.read_bytes())
    build = json.loads(build_path.read_text(encoding='utf-8'))
    candidate_runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    acceptance = json.loads(runtime_acceptance_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if source.get('archive_sha256') != source_archive_sha:
        raise ValueError('source archive does not match source.json')
    if (build.get('complete') is not True or build.get('source_unchanged') is not True
            or build.get('source_sha256') != source_json_sha or build.get('binary_sha256') != binary_sha):
        raise ValueError('candidate binary or source does not match complete build receipt')
    if candidate_runtime.get('binary_sha256') != binary_sha or candidate_runtime.get('source_sha256') != source_json_sha:
        raise ValueError('candidate runtime receipt does not bind the built binary and source')
    live_mismatches = []
    for relative, expected in source.get('files', {}).items():
        path = ROOT / relative
        if not path.is_file() or sha_bytes(path.read_bytes()) != expected:
            live_mismatches.append(relative)
    if live_mismatches:
        raise ValueError('candidate source differs from current source files: ' + ', '.join(live_mismatches[:12]))
    expected_payloads = {'blocks.json', 'legacy-blocks.json', 'quality-blocks.json',
                         'pyproject.toml', 'quality.json', 'ruff'}
    payloads = manifest.get('files')
    if not isinstance(payloads, dict) or set(payloads) != expected_payloads or not payloads:
        raise ValueError('candidate asset manifest must bind the exact six required payload files')
    missing_assets = []
    for relative, expected in payloads.items():
        path = asset_root / relative
        if not path.is_file() or sha_bytes(path.read_bytes()) != expected:
            missing_assets.append(relative)
    if missing_assets:
        raise ValueError('candidate asset payload differs from manifest: ' + ', '.join(missing_assets[:12]))
    backend_hashes = {entry['backend']['sha256'] for entry in asset_index.get('by_prompt_version', {}).values()}
    backend_manifest_sha = sha_bytes(qa.canonical(manifest.get('backend')).encode('utf-8'))
    if len(backend_hashes) != 1 or backend_manifest_sha not in backend_hashes:
        raise ValueError('candidate backend assets differ from independent asset index')
    acceptance_checks = acceptance.get('checks', [])
    if (acceptance.get('passed') is not True or acceptance.get('status') != 'passed'
            or len(acceptance_checks) != 37 or not all(row.get('passed') is True for row in acceptance_checks)
            or acceptance.get('candidate_binary_sha256') != binary_sha
            or acceptance.get('asset_index_sha256') != sha_bytes(args.asset_index.read_bytes())):
        raise ValueError('37/37 runtime acceptance receipt does not bind this binary and asset index')
    identity_before = process_identity(runtime_path, binary, args.http_base)
    return {
        'candidate_binary': str(binary), 'candidate_binary_sha256': binary_sha,
        'source_json': str(source_path), 'source_json_sha256': source_json_sha,
        'source_archive': str(archive.resolve()), 'source_archive_sha256': source_archive_sha,
        'build_receipt': str(build_path), 'build_receipt_sha256': sha_bytes(build_path.read_bytes()),
        'candidate_runtime_receipt': str(runtime_path), 'candidate_runtime_receipt_sha256': sha_bytes(runtime_path.read_bytes()),
        'runtime_acceptance_receipt': str(runtime_acceptance_path), 'runtime_acceptance_receipt_sha256': sha_bytes(runtime_acceptance_path.read_bytes()),
        'asset_manifest': str(manifest_path.resolve()), 'asset_manifest_sha256': sha_bytes(manifest_path.read_bytes()),
        'asset_index': str(args.asset_index.resolve()), 'asset_index_sha256': sha_bytes(args.asset_index.read_bytes()),
        'package_backend_sha256': backend_manifest_sha,
        'identity_before': identity_before,
    }


def observe(args: argparse.Namespace) -> dict[str, Any]:
    out = require_new_output(args.out, OUT_ROOT)
    freeze = qa.verify_freeze(ROOT)
    if freeze.get('latest_hashes_match') is not True or freeze.get('latest_bound_file_count') != 42:
        raise ValueError('frozen compiler contract chain or latest 42-file hashes do not verify')
    free_before = shutil.disk_usage(ROOT).free
    if args.min_free_bytes < MIN_FREE_BYTES:
        raise ValueError(f'minimum free-space guard cannot be below {MIN_FREE_BYTES} bytes')
    if free_before < args.min_free_bytes:
        raise OSError(f'free space {free_before} is below required guard {args.min_free_bytes}')
    if ESTIMATED_RESPONSE_ALLOWANCE > 30 * 1024 * 1024:
        raise AssertionError('estimated evidence footprint exceeds the approved 30 MiB planning bound')
    asset_index = json.loads(args.asset_index.read_text(encoding='utf-8'))
    errors = qa.validate_asset_index_document(asset_index)
    if errors:
        raise ValueError('invalid independent asset index: ' + '; '.join(errors))
    policy = json.loads((ROOT / 'som-rs/contracts/compiler-v1/selection-policy.json').read_text(encoding='utf-8'))
    candidate = bind_candidate(args, asset_index)
    examples = load_examples()
    out.mkdir(parents=True)
    write_json(out / 'candidate-identity.json', candidate)
    write_json(out / 'inventory.json', inventory())
    report: dict[str, Any] = {
        'version': 'som.compiler-performance-observation.v1',
        'purpose': 'observational only; no latency or memory thresholds',
        'candidate': {k: v for k, v in candidate.items() if k != 'identity_before'},
        'freeze_verification': freeze,
        'identity_before': candidate['identity_before'],
        'inventory': {'examples': [x['id'] for x in examples], 'repetitions': 3, 'request_count': 84},
        'observations': [], 'comparisons': [],
        'rss_scope': 'service PID only, sampled after each full HTTP response; snapshots are not peak/global memory and exclude native Ruff child processes',
        'free_space_before_bytes': free_before,
        'estimated_disk_bytes': ESTIMATED_RESPONSE_ALLOWANCE,
    }
    pid = candidate['identity_before']['pid']
    try:
        for example in examples:
            case_root = out / example['id']
            case_root.mkdir()
            write_json(case_root / 'fixture.json', {
                'id': example['id'], 'prompt': example['prompt'], 'cnl': example['cnl'],
                'prompt_sha256': example['prompt_sha256'], 'cnl_sha256': example['cnl_sha256'],
            })
            baseline_bundle: dict[str, Any] | None = None
            baseline_files: dict[str, str] | None = None
            for repeat in range(1, 4):
                rep_root = case_root / f'repeat-{repeat:02d}'
                rep_root.mkdir()
                requests = [
                    ('compile-cnl', '/v1/compile', {'format': 'som.cnl.v1', 'source': example['cnl']}),
                    ('compile-json', '/v1/compile', {'format': 'som.prompt-json.v1', 'prompt': example['prompt']}),
                ]
                results: dict[str, tuple[int, dict[str, Any], bytes]] = {}
                for name, path, body in requests:
                    body_raw = exact_json_bytes(body)
                    row = perform_one(rep_root, name, path, body_raw, args.http_base, pid)
                    report['observations'].append(row)
                    parsed = read_response_json(row)
                    results[name] = (row['http_status'], parsed, body_raw)
                cnl_status, cnl_body, _ = results['compile-cnl']
                json_status, json_body, _ = results['compile-json']
                if cnl_status != 200 or cnl_body.get('status') != 'ok':
                    report['comparisons'].append({'case': example['id'], 'repeat': repeat, 'check': 'compile_cnl_success', 'equal': False})
                    continue
                if json_status != 200 or json_body.get('status') != 'ok' or not isinstance(json_body.get('bundle'), dict):
                    report['comparisons'].append({'case': example['id'], 'repeat': repeat, 'check': 'compile_json_success', 'equal': False})
                    continue
                cnl_bundle, json_bundle = cnl_body.get('bundle'), json_body['bundle']
                bundle_equal = qa.exact_equal(cnl_bundle, json_bundle) and qa.canonical(cnl_bundle) == qa.canonical(json_bundle)
                bundle_problems = qa.verify_bundle(json_bundle, example['prompt'], asset_index, policy)
                report['comparisons'].append({
                    'case': example['id'], 'repeat': repeat, 'check': 'cnl_json_complete_bundle',
                    'equal': bundle_equal, 'json_bundle_validation_problems': bundle_problems,
                    'json_bundle_sha256': qa.sha(json_bundle), 'cnl_bundle_sha256': qa.sha(cnl_bundle),
                })
                if baseline_bundle is None:
                    baseline_bundle = json_bundle
                    write_json(case_root / 'saved-complete-bundle.json', baseline_bundle)
                repeat_bundle_equal = qa.exact_equal(baseline_bundle, json_bundle) and qa.canonical(baseline_bundle) == qa.canonical(json_bundle)
                report['comparisons'].append({'case': example['id'], 'repeat': repeat, 'check': 'repeat_bundle_stability', 'equal': repeat_bundle_equal, 'bundle_sha256': qa.sha(json_bundle)})
                saved_bundle = json.loads((case_root / 'saved-complete-bundle.json').read_text(encoding='utf-8'))
                assemble_body = {'bundle': saved_bundle}
                assemble_row = perform_one(rep_root, 'assemble-saved-bundle', '/v1/assemble', exact_json_bytes(assemble_body), args.http_base, pid)
                report['observations'].append(assemble_row)
                assemble_body_json = read_response_json(assemble_row)
                if assemble_row['http_status'] != 200 or assemble_body_json.get('status') != 'ok' or not isinstance(assemble_body_json.get('files'), dict):
                    report['comparisons'].append({'case': example['id'], 'repeat': repeat, 'check': 'assemble_success', 'equal': False})
                    continue
                assembled_files = assemble_body_json['files']
                if baseline_files is None:
                    baseline_files = assembled_files
                    write_json(case_root / 'saved-complete-files.json', baseline_files)
                assemble_stable = qa.exact_equal(baseline_files, assembled_files) and qa.canonical(baseline_files) == qa.canonical(assembled_files)
                report['comparisons'].append({'case': example['id'], 'repeat': repeat, 'check': 'repeat_assembled_files_stability', 'equal': assemble_stable, 'files_sha256': qa.sha(assembled_files)})
                generate_row = perform_one(rep_root, 'generate', '/v1/generate', exact_json_bytes(example['prompt']), args.http_base, pid)
                report['observations'].append(generate_row)
                generate_body = read_response_json(generate_row)
                generated_files = generate_body.get('files')
                files_equal = (generate_row['http_status'] == 200 and generate_body.get('status') == 'ok'
                               and isinstance(generated_files, dict) and qa.exact_equal(assembled_files, generated_files)
                               and qa.canonical(assembled_files) == qa.canonical(generated_files))
                report['comparisons'].append({
                    'case': example['id'], 'repeat': repeat, 'check': 'assembled_generate_full_files',
                    'equal': files_equal,
                    'assembled_files_sha256': qa.sha(assembled_files),
                    'generated_files_sha256': qa.sha(generated_files) if isinstance(generated_files, dict) else None,
                })
    finally:
        try:
            identity_after = process_identity(args.candidate_runtime, args.candidate_binary, args.http_base)
            report['identity_after'] = identity_after
            report['identity_stable'] = identity_after == candidate['identity_before']
        except Exception as error:
            report['identity_after_error'] = f'{type(error).__name__}: {error}'
            report['identity_stable'] = False
        report['free_space_after_bytes'] = shutil.disk_usage(ROOT).free
        report['observation_count'] = len(report['observations'])
        report['comparison_count'] = len(report['comparisons'])
        report['observed_request_bytes'] = sum(row['request_bytes'] for row in report['observations'])
        report['observed_response_bytes'] = sum(row['response_bytes'] for row in report['observations'])
        report['all_requests_completed'] = len(report['observations']) == 84
        report['successful_http_responses'] = sum(row.get('http_status') == 200 for row in report['observations'])
        report['rss_samples_complete'] = (len(report['observations']) == 84
                                          and all(row.get('rss_after_response', {}).get('available') is True
                                                  for row in report['observations']))
        report['comparison_passed'] = (
            report['all_requests_completed']
            and report['successful_http_responses'] == 84
            and report['rss_samples_complete']
            and all(row.get('equal') is True and not row.get('json_bundle_validation_problems')
                    for row in report['comparisons'])
            and len(report['comparisons']) == 84
        )
        write_json(out / 'result.json', report)
    return report


def perform_one(directory: Path, name: str, path: str, request_raw: bytes,
                base_url: str, pid: int) -> dict[str, Any]:
    prefix = directory / name
    (prefix.with_suffix('.request.raw')).write_bytes(request_raw)
    status, headers, response_raw, elapsed_ms = raw_http(base_url, path, request_raw)
    prefix.with_suffix('.response.raw').write_bytes(response_raw)
    write_json(prefix.with_suffix('.response.headers.json'), headers)
    try:
        response_json = json.loads(response_raw)
        write_json(prefix.with_suffix('.response.json'), response_json)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pass
    rss = rss_snapshot(pid)
    return {
        'case': directory.parent.name, 'repeat': directory.name, 'operation': name,
        'path': path, 'http_status': status, 'elapsed_ms': elapsed_ms,
        'response_bytes': len(response_raw), 'request_bytes': len(request_raw),
        'request_sha256': sha_bytes(request_raw), 'response_sha256': sha_bytes(response_raw),
        'response_headers_path': str(prefix.with_suffix('.response.headers.json')),
        'request_path': str(prefix.with_suffix('.request.raw')),
        'response_path': str(prefix.with_suffix('.response.raw')),
        'rss_after_response': rss,
    }


def read_response_json(row: dict[str, Any]) -> dict[str, Any]:
    path = Path(row['response_path'])
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--http-base')
    parser.add_argument('--candidate-binary', type=Path)
    parser.add_argument('--source-json', type=Path)
    parser.add_argument('--build-receipt', type=Path)
    parser.add_argument('--candidate-runtime', type=Path)
    parser.add_argument('--runtime-receipt', type=Path)
    parser.add_argument('--asset-index', type=Path)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--min-free-bytes', type=int, default=MIN_FREE_BYTES)
    args = parser.parse_args()
    if args.min_free_bytes < MIN_FREE_BYTES:
        parser.error(f'--min-free-bytes cannot be less than {MIN_FREE_BYTES}')
    if args.inventory_only:
        print(json.dumps(inventory(), ensure_ascii=False, indent=2))
        return 0
    required = ('http_base', 'candidate_binary', 'source_json', 'runtime_receipt', 'asset_index')
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error('execution requires: ' + ', '.join('--' + name.replace('_', '-') for name in missing))
    args.candidate_runtime = args.candidate_runtime or args.source_json.parent / 'runtime.json'
    args.build_receipt = args.build_receipt or args.source_json.parent / 'build.json'
    report = observe(args)
    print(json.dumps({
        'output': str(args.out.resolve()), 'observation_count': report['observation_count'],
        'comparison_count': report['comparison_count'], 'identity_stable': report['identity_stable'],
        'all_requests_completed': report['all_requests_completed'],
        'comparison_passed': report['comparison_passed'],
        'rss_samples_complete': report['rss_samples_complete'],
        'performance_thresholds': None,
    }, ensure_ascii=False))
    return 0 if report['identity_stable'] and report['comparison_passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
