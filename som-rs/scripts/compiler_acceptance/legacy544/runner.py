#!/usr/bin/env python3
"""Independent scratch runner for SOM compiler-v1's 544-product regression.

This runner never invokes a model. Compiler output is an observation. Frozen
prompts, accepted plan policy, archived products, and existing behavior/quality
checks remain independent inputs. It writes only below its acceptance output.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import traceback
from typing import Any

sys.dont_write_bytecode = True

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path: sys.path.insert(0, str(CODE_DIR))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
REPO = find_repo(__file__)
OUTPUT_ROOT = default_output_root(REPO)
OUTPUT_DIR = OUTPUT_ROOT / 'legacy544'
DEFAULT_ASSET_INDEX = OUTPUT_ROOT / 'per-version-asset-index-final.json'
DEFAULT_ASSET_PROVENANCE = OUTPUT_ROOT / 'per-version-asset-index-provenance-final.json'
M2 = REPO / 'som-rs/evals/m2-flow-2026-10-04/r31'
ROOT_RECEIPT = REPO / 'som-rs/evidence/rust-compiler-contract-freeze-2026-10-13.json'
AMENDMENT_001_RECEIPT = REPO / 'som-rs/evidence/rust-compiler-contract-amendment-001.json'
AMENDMENT_RECEIPT = REPO / 'som-rs/evidence/rust-compiler-contract-amendment-002.json'
ROOT_RECEIPT_SHA256 = '2ce6e7027687c7e06ee92a4d6c326fd5f00b6e17f83d5bd80db4e82ebb8cee79'
AMENDMENT_001_RECEIPT_SHA256 = '041b33e72d22833091c69ed6407ba979a1b5320e81214aa939e588032af4a797'
AMENDMENT_RECEIPT_SHA256 = 'c95b79a89737b2568e7b92529c7b5012c6035bc8e62f830eb010693a4445326d'
CONTRACT = REPO / 'som-rs/contracts/compiler-v1'
TESTS = REPO / 'som-rs/tests/compiler-v1'
SOM_CORE = REPO / 'som-core/src'
SCRIPTS = REPO / 'som-core/scripts'

sys.path.insert(0, str(SOM_CORE))
sys.path.insert(0, str(SCRIPTS))
import qa_harness as qa  # noqa: E402
from som_core import flow_compact, quality  # noqa: E402
from som_core.v1 import load_blocks  # noqa: E402
from prepare_flow_training import cases as m2_cases  # noqa: E402


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_health(url: str) -> tuple[int, dict[str, Any], bytes]:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or not parsed.port or parsed.port == 8765 or parsed.path not in ('', '/')):
        raise ValueError('an explicit candidate loopback HTTP port other than 8765 is required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    request = urllib.request.Request(url.rstrip('/') + '/healthz', method='GET')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=20) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    return status, json.loads(raw), raw


def candidate_process_binding(runtime_path: Path, binary: Path, url: str) -> dict[str, Any]:
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    pid, port, argv = runtime.get('pid'), runtime.get('port'), runtime.get('argv')
    parsed = urllib.parse.urlsplit(url)
    if (type(pid) is not int or pid <= 0 or type(port) is not int or port != parsed.port
            or not isinstance(argv, list) or not argv or Path(argv[0]).resolve() != binary.resolve()):
        raise ValueError('candidate runtime PID, argv, binary, or port metadata is invalid')
    if runtime.get('binary_sha256') != file_sha(binary):
        raise ValueError('candidate runtime metadata has the wrong binary hash')
    ps = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'pid=,uid=,lstart=,command='], capture_output=True, text=True,
                        check=False, timeout=10)
    fields = ps.stdout.strip().split(None, 7) if ps.returncode == 0 else []
    if (len(fields) != 8 or int(fields[0]) != pid or int(fields[1]) != os.getuid()
            or shlex.split(fields[7]) != argv):
        raise ValueError('candidate process PID, owner, start time, or argv differs from runtime receipt')
    sock = subprocess.run(['lsof', '-nP', '-iTCP:' + str(port),
                           '-sTCP:LISTEN', '-Fpcn'], capture_output=True, text=True,
                          check=False, timeout=10)
    lines = sock.stdout.splitlines()
    listener_names = [line[1:] for line in lines if line.startswith('n')]
    loopback_names = ('127.0.0.1:' + str(port), '[::1]:' + str(port))
    if (sock.returncode != 0 or set(line for line in lines if line.startswith('p')) != {'p' + str(pid)}
            or not listener_names or not all(any(host in name for host in loopback_names)
                                             for name in listener_names)):
        raise ValueError('candidate PID does not exclusively own its recorded loopback listening port')
    exe = subprocess.run(['lsof', '-nP', '-a', '-p', str(pid), '-d', 'txt', '-Fn'],
                         capture_output=True, text=True, check=False, timeout=10)
    exe_lines = exe.stdout.splitlines()
    if exe.returncode != 0 or 'n' + str(binary.resolve()) not in exe_lines:
        raise ValueError('candidate PID does not have the hashed binary loaded as its executable')
    return {'pid': pid, 'uid': int(fields[1]), 'lstart': ' '.join(fields[2:7]),
            'argv': argv, 'port': port, 'binary_sha256': runtime['binary_sha256'],
            'source_sha256': runtime.get('source_sha256'), 'lsof_listener_fields': lines,
            'lsof_executable_fields': exe_lines}


def candidate_identity(binary: Path, source_path: Path, runtime_path: Path, base_url: str,
                       asset_index_path: Path, assets_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = json.loads(source_path.read_text(encoding='utf-8'))
    archive = source_path.parent / 'source.tar'
    build_path = source_path.parent / 'build.json'
    candidate_runtime_path = source_path.parent / 'runtime.json'
    manifest = assets_path / 'manifest.json'
    build = json.loads(build_path.read_text(encoding='utf-8'))
    candidate_runtime = json.loads(candidate_runtime_path.read_text(encoding='utf-8'))
    identity = {
        'candidate_binary': str(binary.resolve()), 'candidate_binary_sha256': file_sha(binary),
        'source_json': str(source_path.resolve()), 'source_json_sha256': file_sha(source_path),
        'runtime_acceptance_receipt': str(runtime_path.resolve()),
        'runtime_acceptance_receipt_sha256': file_sha(runtime_path),
        'source_archive': str(archive.resolve()), 'source_archive_sha256': file_sha(archive),
        'build_receipt': str(build_path.resolve()), 'build_receipt_sha256': file_sha(build_path),
        'candidate_runtime_receipt': str(candidate_runtime_path.resolve()),
        'candidate_runtime_receipt_sha256': file_sha(candidate_runtime_path),
        'asset_manifest': str(manifest.resolve()), 'asset_manifest_sha256': file_sha(manifest),
        'asset_index': str(asset_index_path.resolve()), 'asset_index_sha256': file_sha(asset_index_path),
    }
    if source.get('archive_sha256') != identity['source_archive_sha256']:
        raise ValueError('candidate source archive hash differs from source.json')
    if (build.get('complete') is not True or build.get('source_unchanged') is not True
            or build.get('source_sha256') != identity['source_json_sha256']
            or build.get('binary_sha256') != identity['candidate_binary_sha256']):
        raise ValueError('candidate binary/source differs from the complete build receipt')
    if (candidate_runtime.get('binary_sha256') != identity['candidate_binary_sha256']
            or candidate_runtime.get('source_sha256') != identity['source_json_sha256']):
        raise ValueError('candidate runtime receipt does not bind the built binary and source')
    mismatches = []
    for relative, expected in source.get('files', {}).items():
        source_file = REPO / relative
        actual = file_sha(source_file) if source_file.is_file() else 'MISSING'
        if actual != expected:
            mismatches.append(relative)
    if mismatches:
        raise ValueError('candidate source.json differs from live sources: ' + ', '.join(mismatches[:12]))
    package = json.loads((assets_path / 'manifest.json').read_text(encoding='utf-8'))
    asset_index = json.loads(asset_index_path.read_text(encoding='utf-8'))
    backend_rows = [entry['backend']['sha256'] for entry in asset_index['by_prompt_version'].values()]
    backend_sha = hashlib.sha256(qa.canonical(package.get('backend')).encode()).hexdigest()
    if not backend_rows or len(set(backend_rows)) != 1 or backend_rows[0] != backend_sha:
        raise ValueError('candidate package backend hash differs from independently frozen asset index')
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    if (runtime.get('status') != 'passed' or runtime.get('passed') is not True
            or runtime.get('candidate_binary_sha256') != identity['candidate_binary_sha256']
            or len(runtime.get('checks', [])) != 37 or not all(row.get('passed') is True for row in runtime.get('checks', []))):
        raise ValueError('runtime receipt does not bind this candidate binary')
    if runtime.get('asset_index_sha256') != identity['asset_index_sha256']:
        raise ValueError('runtime receipt does not bind this asset index')
    health_row = next((row for row in runtime.get('checks', []) if row.get('id') == 'health_contract'), None)
    health = (health_row or {}).get('actual')
    if not isinstance(health, dict) or health.get('backend_sha256') != backend_rows[0]:
        raise ValueError('runtime health receipt does not bind the package backend hash')
    if not isinstance(health, dict) or health.get('purpose') != 'som.rust-compiler.v1' or health.get('model_loaded') is not False:
        raise ValueError('runtime receipt lacks compiler-only health evidence')
    if candidate_runtime.get('health') != health:
        raise ValueError('candidate runtime health differs from independent runtime acceptance')
    identity['process_before'] = candidate_process_binding(candidate_runtime_path, binary, base_url)
    return identity, health


def canonical_product(files: dict[str, str]) -> str:
    return qa.canonical(files)


def environment_manifest(root: Path) -> dict[str, Any]:
    rows = []
    config = root / 'pyvenv.cfg'
    if config.is_file():
        rows.append({'path': 'pyvenv.cfg', 'bytes': config.stat().st_size, 'sha256': file_sha(config)})
    for metadata in sorted(root.rglob('METADATA')):
        if not metadata.parent.name.endswith('.dist-info'):
            continue
        content = metadata.read_text(encoding='utf-8', errors='replace').splitlines()
        name = next((line.partition(':')[2].strip() for line in content if line.startswith('Name: ')), None)
        version = next((line.partition(':')[2].strip() for line in content if line.startswith('Version: ')), None)
        rows.append({'path': str(metadata.relative_to(root)), 'name': name, 'version': version,
                     'bytes': metadata.stat().st_size, 'sha256': file_sha(metadata)})
    return {'entries': rows, 'entry_count': len(rows)}


def path_is_safe_owned(target: Path, output: Path) -> bool:
    root = output.resolve()
    if output.is_symlink() or not output.resolve().is_relative_to(root):
        return False
    try:
        relative = target.relative_to(output)
    except ValueError:
        return False
    current = output
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return target.resolve().is_relative_to(root)


def process_mentions_path(path: Path) -> tuple[bool, list[dict[str, str]]]:
    result = subprocess.run(['ps', '-ww', '-axo', 'pid=,command='], capture_output=True, text=True,
                            check=False, timeout=10)
    if result.returncode != 0:
        raise RuntimeError('cannot verify owned environment process absence: ' + result.stderr[-1000:])
    needle = str(path.resolve())
    live = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and needle in parts[1]:
            live.append({'pid': parts[0], 'command': parts[1][:500]})
    return not live, live


def cleanup_completed_job(output: Path, job_dir: Path, product: Path,
                          quality_evidence: Path) -> dict[str, Any]:
    """Remove only this finished row's virtualenv and mypy cache after process check."""
    out_root = output.resolve()
    targets = [product / '.venv', quality_evidence / 'mypy-cache']
    record: dict[str, Any] = {'policy': 'completed job only; exact owned paths; no global cache removal',
                              'targets': [], 'passed': True,
                              'free_bytes_before': shutil.disk_usage(out_root).free}
    for target in targets:
        item: dict[str, Any] = {'path': str(target), 'removed': False,
                                'free_bytes_before': shutil.disk_usage(out_root).free}
        try:
            if not path_is_safe_owned(target, output):
                raise ValueError('target is a symlink or resolves outside this run output')
            if not target.exists():
                item.update({'present': False, 'bytes_before': 0, 'bytes_after': 0})
                item['free_bytes_after'] = shutil.disk_usage(out_root).free
                record['targets'].append(item)
                continue
            before = sum(p.stat().st_size for p in target.rglob('*') if p.is_file()) if target.is_dir() else target.stat().st_size
            item['present'] = True
            item['bytes_before'] = before
            venv = product / '.venv'
            if target == venv:
                item['environment_manifest'] = environment_manifest(target)
            absent, live = process_mentions_path(target)
            item['owned_path_processes_absent'] = absent
            item['live_processes'] = live
            if not absent:
                raise RuntimeError('a process still uses this exact completed-job cleanup path')
            if not path_is_safe_owned(target, output):
                raise ValueError('target safety changed before deletion')
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            item['removed'] = True
            item['bytes_after'] = 0
            item['free_bytes_after'] = shutil.disk_usage(out_root).free
        except Exception as exc:
            item['error'] = type(exc).__name__ + ': ' + str(exc)
            record['passed'] = False
        record['targets'].append(item)
    record['free_bytes_after_all'] = shutil.disk_usage(out_root).free
    return record


def hashes_map(paths: set[Path]) -> dict[str, str]:
    expanded: set[Path] = set()
    for path in paths:
        if path.is_dir():
            expanded.update(item for item in path.rglob('*') if item.is_file())
        elif path.is_file():
            expanded.add(path)
    return {str(p): file_sha(p) for p in sorted(expanded)}


def load_prompt_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def build_jobs(asset_index_path: Path = DEFAULT_ASSET_INDEX,
               asset_provenance_path: Path = DEFAULT_ASSET_PROVENANCE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Mirror run_m3_protection.jobs without reading archived DSL answer files."""
    freeze_chain = qa.verify_freeze(REPO)
    asset_index = json.loads(asset_index_path.read_text(encoding='utf-8'))
    asset_provenance = json.loads(asset_provenance_path.read_text(encoding='utf-8'))
    index_errors = qa.validate_asset_index_document(asset_index)
    if index_errors:
        raise ValueError('invalid final per-version asset index: ' + '; '.join(index_errors))
    if asset_provenance.get('package_manifest_matches_independent_sources') is not True:
        raise ValueError('final compiler package manifest does not match independent source hashes')
    jobs: list[dict[str, Any]] = []
    source_files: set[Path] = set()
    source_groups = [
        ('services14', 'composed/regression.jsonl'),
        ('compositions120', 'composed/heldout.jsonl'),
        ('blind80', 'composed/blind.jsonl'),
        ('legacy100', 'service-regression-v4/heldout.jsonl'),
    ]
    for group, relative in source_groups:
        path = REPO / 'som-core/data' / relative
        source_files.add(path)
        for index, row in enumerate(load_prompt_rows(path)):
            jobs.append({
                'id': f'{group}-{index:04d}',
                'group': group,
                'family': 'legacy100' if group == 'legacy100' else 'legacy214',
                'index': index,
                'prompt': row['prompt'],
                'gold': M2 / f'legacy-generation-002/comparison/model/{group}/{index:04d}.json',
                'variant': 'standard',
                'behavior': True,
            })

    split_ids: dict[str, set[str]] = {}
    for split in ('formal', 'dev'):
        path = REPO / f'som-core/data/flow-v7-r10/{split}.jsonl'
        source_files.add(path)
        rows = load_prompt_rows(path)
        split_ids[split] = {str(row['id']) for row in rows}

    for identity, prompt, variant in m2_cases():
        if identity in split_ids['formal']:
            partition = 'formal114'
        elif identity in split_ids['dev']:
            partition = 'dev12'
        elif identity.startswith('alternative-'):
            partition = 'alternative-plans'
        else:
            partition = 'maximum-matrix'
        jobs.append({
            'id': 'm2-' + identity,
            'group': 'm2-quality218',
            'family': 'quality218',
            'partition': partition,
            'prompt': prompt,
            'variant': variant,
            'behavior': identity in split_ids['formal'] | split_ids['dev'],
            'gold': M2 / f'training-prerequisite-001/{identity}/response.json',
        })

    supplement_root = M2 / 'supplementary-acceptance-003'
    for source in sorted(supplement_root.iterdir()):
        requirement = source / 'requirement.json'
        response = source / 'response.json'
        independent_dsl_path = source / 'dsl.json'
        if not source.is_dir() or not independent_dsl_path.is_file():
            continue
        source_files.add(requirement)
        source_files.add(independent_dsl_path)
        row = json.loads(requirement.read_text(encoding='utf-8'))
        independent_dsl_raw = independent_dsl_path.read_bytes()
        independent_dsl = json.loads(independent_dsl_raw)
        # This archived plan is used only as a separately authored assembler
        # input for checked-variant acceptance. It never selects compiler output.
        flow_steps = independent_dsl.get('flow', {}).get('steps', [])
        checked = any(
            isinstance(step, dict) and isinstance(step.get('id'), str)
            and step['id'].endswith('.checked')
            for step in flow_steps
        )
        jobs.append({
            'id': 'supplement-' + row['id'],
            'group': 'm2-supplement12',
            'family': 'supplement12',
            'partition': 'supplement12',
            'prompt': row['prompt'],
            'variant': 'checked' if checked else 'standard',
            'independent_dsl_path': independent_dsl_path if checked else None,
            'independent_dsl_sha256': hashlib.sha256(independent_dsl_raw).hexdigest() if checked else None,
            'independent_dsl': independent_dsl if checked else None,
            'behavior': True,
            'gold': response,
        })

    checked_ids = {j['id'] for j in jobs if j['variant'] == 'checked'}
    expected_checked_ids = {
        'm2-alternative-checked',
        'supplement-mongodb-child-patch-read-checked',
        'supplement-postgresql-child-read-checked',
    }
    if checked_ids != expected_checked_ids:
        raise ValueError(f'approved checked-variant inventory changed: {sorted(checked_ids)}')
    blocks_path = REPO / 'som-code-python/data/blocks/flow-v7-compact-self/blocks.json'
    checked_plan_validation = []
    blocks = load_blocks(blocks_path.parent)
    for job in jobs:
        if job['variant'] != 'checked':
            continue
        plan = job.get('independent_dsl')
        if plan is None:
            plan = flow_compact.reference(job['prompt'], blocks, 'checked')
        flow_compact.flow.validate_plan(job['prompt'], plan, flow_compact.metadata())
        checked_plan_validation.append({
            'id': job['id'],
            'validator': 'existing som_core.flow.validate_plan',
            'dsl_sha256': canonical_hash(plan),
            'source': 'public flow_compact.reference' if job.get('independent_dsl') is None else 'separately archived independent DSL',
            'passed': True,
        })

    if len(jobs) != 544 or len({job['id'] for job in jobs}) != 544:
        raise ValueError(f'expected 544 unique jobs, found {len(jobs)}')
    prompt_jobs: dict[str, list[str]] = {}
    for job in jobs:
        prompt_jobs.setdefault(qa.sha(job['prompt']), []).append(job['id'])
    duplicate_prompt_groups = [
        {'prompt_sha256': digest, 'job_ids': sorted(identities)}
        for digest, identities in sorted(prompt_jobs.items()) if len(identities) > 1
    ]
    counts = {
        'disjoint_groups': {
            'legacy100': sum(j['family'] == 'legacy100' for j in jobs),
            'legacy214': sum(j['family'] == 'legacy214' for j in jobs),
            'quality218': sum(j['family'] == 'quality218' for j in jobs),
            'supplement12': sum(j['family'] == 'supplement12' for j in jobs),
        },
        'legacy214_subgroups': {
            name: sum(j['group'] == name for j in jobs)
            for name in ('services14', 'compositions120', 'blind80')
        },
        'quality218_partitions': {
            name: sum(j.get('partition') == name for j in jobs)
            for name in ('formal114', 'dev12', 'maximum-matrix', 'alternative-plans')
        },
        'overlapping_quality_scopes': {
            'formal114': sum(j.get('partition') == 'formal114' for j in jobs),
            'dev12': sum(j.get('partition') == 'dev12' for j in jobs),
            'quality218': sum(j['family'] == 'quality218' for j in jobs),
            'behavior126': sum(j['family'] == 'quality218' and j['behavior'] for j in jobs),
            'quality_parity_only92': sum(j['family'] == 'quality218' and not j['behavior'] for j in jobs),
        },
        'supplement_behavior': sum(j['family'] == 'supplement12' and j['behavior'] for j in jobs),
        'job_count': len(jobs),
        'unique_prompt_count': len(prompt_jobs),
        'duplicate_prompt_group_count': len(duplicate_prompt_groups),
        'assembly_count': len(jobs),
        'compile_pair_count': len({qa.sha(job['prompt']) for job in jobs}),
    }
    expected_counts = {
        'disjoint_groups': {'legacy100': 100, 'legacy214': 214, 'quality218': 218, 'supplement12': 12},
        'legacy214_subgroups': {'services14': 14, 'compositions120': 120, 'blind80': 80},
        'quality218_partitions': {'formal114': 114, 'dev12': 12, 'maximum-matrix': 90, 'alternative-plans': 2},
        'overlapping_quality_scopes': {'formal114': 114, 'dev12': 12, 'quality218': 218, 'behavior126': 126, 'quality_parity_only92': 92},
        'supplement_behavior': 12,
        'job_count': 544,
        'unique_prompt_count': 540,
        'assembly_count': 544,
        'compile_pair_count': 540,
    }
    if any(counts[key] != expected for key, expected in expected_counts.items()):
        raise ValueError(f'544 inventory accounting changed: {counts}')

    job_records = []
    for job in jobs:
        gold_path: Path = job['gold']
        source_files.add(gold_path)
        raw = gold_path.read_bytes()
        gold = json.loads(raw)
        files = gold.get('files')
        if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise ValueError(f'{job["id"]}: archived response must contain a string files map')
        job_records.append({
            'id': job['id'], 'family': job['family'], 'group': job['group'],
            'partition': job.get('partition'), 'index': job.get('index'),
            'variant': job['variant'], 'behavior': job['behavior'],
            'independent_dsl_path': str(job['independent_dsl_path']) if job.get('independent_dsl_path') else None,
            'independent_dsl_sha256': job.get('independent_dsl_sha256'),
            'prompt_sha256': qa.sha(job['prompt']),
            'gold_response_path': str(gold_path),
            'gold_response_sha256': hashlib.sha256(raw).hexdigest(),
            'gold_product_sha256': hashlib.sha256(canonical_product(files).encode()).hexdigest(),
            'gold_file_sha256': {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                                 for name, text in sorted(files.items())},
        })

    source_files.update({
        ROOT_RECEIPT, AMENDMENT_001_RECEIPT, AMENDMENT_RECEIPT,
        CONTRACT / 'CONTRACT.md', CONTRACT / 'selection-policy.json',
        TESTS / 'serializer.py', TESTS / 'field-refusal-inventory.json',
        SCRIPTS / 'run_m3_protection.py', SCRIPTS / 'prepare_flow_training.py',
        SCRIPTS / 'verify_service_v3.py', SCRIPTS / 'verify_composed_services.py',
        SCRIPTS / 'verify_flow_service.py', SCRIPTS / 'verify_python_quality.py',
        SCRIPTS / 'm3_local_fixture.py', SCRIPTS / 'local_flow_services.py',
        SCRIPTS / 'local_business_services.py',
        SCRIPTS / 'quality_policy.py', SCRIPTS / 'verify_composed_related.py',
        SCRIPTS / 'audit_business_transactions.py', SCRIPTS / 'verify_composed_journeys.py',
        SCRIPTS / 'verify_composed_subsets.py', SCRIPTS / 'verify_composed_transactions.py',
        SCRIPTS / 'composed_evidence.py', SCRIPTS / 'fixture_guard.py',
        SCRIPTS / 'verify_related.py', SCRIPTS / 'business_test_support.py',
        SCRIPTS / 'flow_test_backends.py', SCRIPTS / 'flow_branch_cases.py',
        SCRIPTS / 'flow_fault_checks.py', SCRIPTS / 'flow_provider_fault_checks.py',
        SCRIPTS / 'flow_row_history.py', SCRIPTS / 'verify_business_workflows.py',
        SCRIPTS / 'flow_gcs_fixture.py',
        CODE_DIR / 'qa_harness.py', CODE_DIR / 'asset_index_generator.py',
        CODE_DIR / 'runtime_acceptance.py', CODE_DIR / 'gap_acceptance.py',
        CODE_DIR / 'public_products.py', CODE_DIR / 'public_reference.py',
        CODE_DIR / 'paths.py',
        CODE_DIR / '__init__.py',
        CODE_DIR / 'legacy544/runner.py',
        REPO / 'som-core/src/som_core/flow.py',
        REPO / 'som-core/src/som_core/flow_compact.py',
        REPO / 'som-core/src/som_core/flow_codec.py',
        REPO / 'som-core/src/som_core/composed.py',
        REPO / 'som-core/src/som_core/compact_names.py',
        REPO / 'som-core/src/som_core/quality.py',
        REPO / 'som-core/src/som_core/v1.py',
        REPO / 'som-code-python/data/blocks/flow-v7-compact-self/blocks.json',
        REPO / 'som-code-python/data/block-contracts/flow-v7-compact.json',
        REPO / 'som-rs/contracts/python-literal-encoding.v1.json',
        REPO / 'som-rs/contracts/python-quality-v1/pyproject.toml',
        REPO / 'som-code-python/data/blocks/v3',
        REPO / 'som-code-python/data/blocks/v4',
        REPO / 'som-code-python/data/blocks/composed',
        asset_index_path,
        asset_provenance_path,
    })
    source_files.update({CODE_DIR / "diagnostic_http_acceptance.py", CODE_DIR / "extra-http-cases.json"})
    source_files.update(p for p in (CODE_DIR / "renderer_behavior").rglob("*")
                        if p.is_file() and p.suffix in (".py", ".json", ".cnl", ".md"))
    input_hashes = hashes_map(source_files)
    if file_sha(AMENDMENT_RECEIPT) != AMENDMENT_RECEIPT_SHA256:
        raise ValueError('compiler amendment receipt hash mismatch')
    if file_sha(ROOT_RECEIPT) != ROOT_RECEIPT_SHA256:
        raise ValueError('original compiler freeze receipt hash mismatch')
    if file_sha(AMENDMENT_001_RECEIPT) != AMENDMENT_001_RECEIPT_SHA256:
        raise ValueError('compiler amendment 001 receipt hash mismatch')
    amended = json.loads(AMENDMENT_RECEIPT.read_text(encoding='utf-8'))
    if amended.get('previous_receipt_sha256') != AMENDMENT_001_RECEIPT_SHA256:
        raise ValueError('compiler amendment chain mismatch')
    mismatches = []
    for relative, expected in amended['files'].items():
        path = REPO / relative
        actual = file_sha(path) if path.is_file() else 'MISSING'
        if actual != expected:
            mismatches.append({'path': relative, 'expected': expected, 'actual': actual})
    if mismatches:
        raise ValueError(f'amended frozen contract mismatch: {mismatches[:5]}')

    inventory = {
        'version': 'som.compiler-legacy544-inventory.v1',
        'scope': 'Historical products are regression inputs. No old model is loaded or run.',
        'compiler_freeze': {
            'chain': freeze_chain,
            'original_receipt_sha256': ROOT_RECEIPT_SHA256,
            'amendment_receipt_sha256': AMENDMENT_RECEIPT_SHA256,
            'amended_contract_files_match': True,
            'amended_contract_file_count': len(amended['files']),
        },
        'asset_index_oracle': {
            'path': str(asset_index_path),
            'sha256': file_sha(asset_index_path),
            'provenance_path': str(asset_provenance_path),
            'provenance_sha256': file_sha(asset_provenance_path),
            'package_manifest_matches_independent_sources': True,
        },
        'counts': counts,
        'duplicate_prompt_groups': duplicate_prompt_groups,
        'groups': job_records,
        'source_hashes': input_hashes,
        'planned_run': {
            'compile_json_and_equivalent_cnl_per_unique_prompt': counts['unique_prompt_count'],
            'assemble_full_bundle_per_product_job': counts['assembly_count'],
            'checked_variant_assembly_jobs': sum(j['variant'] == 'checked' for j in jobs),
            'checked_variant_jobs_by_family': {
                family: sum(j['family'] == family and j['variant'] == 'checked' for j in jobs)
                for family in ('quality218', 'supplement12')
            },
            'compiler_variant': 'standard only; case identity never selects compiler output',
            'checked_bundle_sources': {
                'quality218': 'public flow_compact.reference(prompt, blocks, checked), then independently recompute bundle hashes',
                'supplement12': 'separately archived independently authored DSL for checked rows only, then recompute bundle hashes',
            },
            'duplicate_prompt_groups': duplicate_prompt_groups,
            'checked_plan_prevalidation': checked_plan_validation,
        },
    }
    return jobs, inventory


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def inventory_markdown(inventory: dict[str, Any]) -> str:
    c = inventory['counts']
    return '\n'.join([
        '# SOM compiler 544 regression inventory', '',
        'This report records input identities only. It does not contain prompts or generated files.', '',
        f"Frozen contract amendment: `{inventory['compiler_freeze']['amendment_receipt_sha256']}`.",
        f"Amended frozen files checked: {inventory['compiler_freeze']['amended_contract_file_count']}.", '',
        '| Disjoint group | Jobs |', '|---|---:|',
        *[f'| {name} | {count} |' for name, count in c['disjoint_groups'].items()],
        f"| **Total** | **{c['job_count']}** |", '',
        f"Unique prompt inputs: {c['unique_prompt_count']}. Full bundle assembly jobs: {c['assembly_count']}.",
        f"Legacy214 split: {c['legacy214_subgroups']}.",
        f"Quality218 labels: {c['quality218_partitions']}.",
        f"Overlapping quality scopes (not additive): {c['overlapping_quality_scopes']}.",
        f"Supplement full-behavior jobs: {c['supplement_behavior']}.", '',
        'Compile JSON and equivalent CNL for each unique prompt. The compiler always uses its fixed standard policy.',
        'There are three checked archived-product rows: one quality218 row from the public Python reference and two supplement12 rows with separately archived DSL. Validate each plan, update only the DSL and hashes, and send the complete bundle to assemble. Do not use a case ID to choose compile output.',
        'The two alternative quality plans share one prompt. The 218 products remain two separately archived outputs, and each output is assembled from its own plan. A third standard quality row shares that prompt.',
        'Run original behavior checks, strict Python quality checks, formatter idempotence, and full archived-file parity under the existing group scopes.', '',
        'No compiler, model, server, or behavior fixture was started while preparing this inventory.', '',
    ])


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(qa.canonical(value).encode('utf-8')).hexdigest()


def independent_bundle(prompt: dict[str, Any], bundle: dict[str, Any], variant: str,
                       blocks: dict[str, Any], stored_dsl: dict[str, Any] | None = None) -> dict[str, Any]:
    """For checked flow acceptance, replace only the plan with an independent DSL."""
    if variant == 'standard':
        return copy.deepcopy(bundle)
    if variant != 'checked' or prompt.get('version') != 'som.prompt.v7':
        raise ValueError(f'unsupported assembly variant {variant!r}')
    result = copy.deepcopy(bundle)
    if stored_dsl is not None:
        result['dsl'] = copy.deepcopy(stored_dsl)
    else:
        result['dsl'] = flow_compact.reference(prompt, blocks, 'checked')
    flow_compact.flow.validate_plan(prompt, result['dsl'], flow_compact.metadata())
    result['hashes'] = {
        'prompt_sha256': canonical_hash(result['prompt']),
        'dsl_sha256': canonical_hash(result['dsl']),
        'payload_sha256': canonical_hash({k: result[k] for k in ('version', 'prompt', 'dsl', 'assets')}),
    }
    return result


def load_serializer():
    path = TESTS / 'serializer.py'
    spec = importlib.util.spec_from_file_location('som_frozen_serializer', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load frozen CNL serializer')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.serialize_prompt


def endpoint(client: qa.Client, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    # Keep even malformed or non-object responses as evidence before judging them.
    return client.call(path, body)


def response_field(response: Any, name: str) -> Any:
    return response.get(name) if isinstance(response, dict) else None


def archive_for(job: dict[str, Any]) -> tuple[dict[str, str], str, dict[str, str]]:
    raw = job['gold'].read_bytes()
    parsed = json.loads(raw)
    files = parsed.get('files')
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        raise ValueError(f'{job["id"]}: malformed archived product')
    return files, hashlib.sha256(raw).hexdigest(), {
        path: hashlib.sha256(text.encode('utf-8')).hexdigest() for path, text in sorted(files.items())
    }


def write_product(root: Path, files: dict[str, str]) -> None:
    resolved_root = root.resolve()
    written: set[Path] = set()
    for name, source in files.items():
        if not isinstance(name, str) or not isinstance(source, str):
            raise ValueError('product paths and contents must be strings')
        relative = Path(name)
        if (not relative.parts or relative.is_absolute() or '\\' in name
                or any(part in {'', '.', '..'} for part in relative.parts)):
            raise ValueError(f'unsafe product path: {name!r}')
        target = (resolved_root / relative).resolve()
        if not target.is_relative_to(resolved_root):
            raise ValueError(f'product path escapes its output directory: {name!r}')
        if target in written:
            raise ValueError(f'product paths collide after normalization: {name!r}')
        written.add(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')


def run_job_behavior(job: dict[str, Any], prompt: dict[str, Any], product: Path,
                     response_path: Path, fixture: Any, scripts: Path) -> dict[str, Any] | None:
    if job['family'] == 'legacy100':
        checked = subprocess.run(
            [sys.executable, str(scripts / 'verify_service_v3.py'),
             '--cases', str(REPO / 'som-core/data/service-regression-v4/heldout.jsonl'),
             '--heldout-index', str(job['index']), '--files-json', str(response_path)],
            capture_output=True, text=True, check=False, timeout=180,
        )
        log_dir = response_path.parent
        (log_dir / 'legacy100-verifier.stdout.txt').write_text(checked.stdout, encoding='utf-8')
        (log_dir / 'legacy100-verifier.stderr.txt').write_text(checked.stderr, encoding='utf-8')
        return {'checker': 'verify_service_v3.py', 'exit_code': checked.returncode,
                'stdout_path': str(log_dir / 'legacy100-verifier.stdout.txt'),
                'stderr_path': str(log_dir / 'legacy100-verifier.stderr.txt'),
                'stdout_sha256': hashlib.sha256(checked.stdout.encode()).hexdigest(),
                'stderr_sha256': hashlib.sha256(checked.stderr.encode()).hexdigest(),
                'passed': checked.returncode == 0}
    if job['family'] == 'legacy214':
        from verify_composed_services import verify_all
        outcome = verify_all(prompt, product, fixture, standard=job['group'] == 'services14')
        return {'checker': 'verify_composed_services.verify_all', 'outcome': outcome,
                'passed': outcome.get('status') == 'passed'}
    if job['family'] == 'quality218' and job['behavior']:
        from verify_flow_service import verify
        outcome = verify(prompt, product, fixture)
        return {'checker': 'verify_flow_service.verify', 'outcome': outcome,
                'passed': bool(outcome.get('full_m2_acceptance'))}
    if job['family'] == 'supplement12':
        from verify_flow_service import verify
        outcome = verify(prompt, product, fixture)
        return {'checker': 'verify_flow_service.verify', 'outcome': outcome,
                'passed': bool(outcome.get('full_m2_acceptance'))}
    return None


def run_acceptance(client: qa.Client, inventory: dict[str, Any], jobs: list[dict[str, Any]],
                   output: Path, asset_index_path: Path, formatter: Path,
                   quality_profile: Path, asset_provenance_path: Path,
                   identity: dict[str, Any], expected_health: dict[str, Any]) -> dict[str, Any]:
    output = require_new_output(output, OUTPUT_DIR)
    assets = json.loads(asset_index_path.read_text(encoding='utf-8'))
    if file_sha(asset_index_path) != inventory['asset_index_oracle']['sha256']:
        raise ValueError('asset index differs from the independently reviewed inventory')
    if file_sha(asset_provenance_path) != inventory['asset_index_oracle']['provenance_sha256']:
        raise ValueError('asset provenance differs from the independently reviewed inventory')
    provenance = json.loads(asset_provenance_path.read_text(encoding='utf-8'))
    if provenance.get('package_manifest_matches_independent_sources') is not True:
        raise ValueError('candidate package manifest does not match independently read sources')
    if not isinstance(assets, dict):
        raise ValueError('asset index must be a JSON object')
    asset_index_errors = qa.validate_asset_index_document(assets)
    if asset_index_errors:
        raise ValueError('invalid per-version asset index: ' + '; '.join(asset_index_errors))
    policy = json.loads((CONTRACT / 'selection-policy.json').read_text(encoding='utf-8'))
    serialize_prompt = load_serializer()
    blocks_path = REPO / 'som-code-python/data/blocks/flow-v7-compact-self/blocks.json'
    blocks = load_blocks(blocks_path.parent)
    output.mkdir(parents=True, exist_ok=False)
    before_status, before_body, before_raw = candidate_health(client.base_url or '')
    (output / 'health-before.response.raw').write_bytes(before_raw)
    write_json(output / 'health-before.response.json', before_body)
    write_json(output / 'candidate-identity.json', identity)
    if before_status != 200 or before_body != expected_health:
        write_json(output / 'identity-refusal.json', {'reason': 'serving health differs from bound runtime receipt',
                  'http_status': before_status, 'expected': expected_health, 'actual': before_body})
        raise ValueError('serving endpoint does not match the final candidate runtime receipt')
    compile_cache: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    expected_inventory_sha = hashlib.sha256(qa.canonical(inventory).encode()).hexdigest()
    compile_evidence_root = output / 'compile'
    compile_evidence_root.mkdir()

    # Compile one independently prepared JSON/CNL pair per distinct typed prompt.
    for job in jobs:
        key = qa.sha(job['prompt'])
        if key in compile_cache:
            if not qa.exact_equal(compile_cache[key]['prompt'], job['prompt']):
                raise ValueError('prompt hash collision across regression jobs')
            continue
        cnl = serialize_prompt(job['prompt'], contract_dir=CONTRACT)
        compile_dir = compile_evidence_root / key
        compile_dir.mkdir(exist_ok=False)
        json_request = {'format': 'som.prompt-json.v1', 'prompt': job['prompt']}
        cnl_request = {'format': 'som.cnl.v1', 'source': cnl}
        write_json(compile_dir / 'prompt.json', job['prompt'])
        (compile_dir / 'source.cnl').write_text(cnl, encoding='utf-8')
        json_status, json_response = endpoint(client, '/v1/compile', json_request)
        json_raw = client.last_response_raw
        qa.persist_api_exchange(compile_dir, 'compile-json', json_request, json_status, json_response, json_raw)
        cnl_status, cnl_response = endpoint(client, '/v1/compile', cnl_request)
        cnl_raw = client.last_response_raw
        qa.persist_api_exchange(compile_dir, 'compile-cnl', cnl_request, cnl_status, cnl_response, cnl_raw)
        if isinstance(response_field(json_response, 'bundle'), dict):
            write_json(compile_dir / 'compiled-json.bundle.json', response_field(json_response, 'bundle'))
        if isinstance(response_field(cnl_response, 'bundle'), dict):
            write_json(compile_dir / 'compiled-cnl.bundle.json', response_field(cnl_response, 'bundle'))
        problem: list[str] = []
        for label, status, response in (('json', json_status, json_response), ('cnl', cnl_status, cnl_response)):
            if status != 200 or response_field(response, 'status') != 'ok' or not isinstance(response_field(response, 'bundle'), dict):
                problem.append(f'{label} compile failed: HTTP {status} {response_field(response, "code")}')
        json_bundle = response_field(json_response, 'bundle') if not problem else None
        cnl_bundle = response_field(cnl_response, 'bundle') if not problem else None
        if json_bundle is not None and cnl_bundle is not None:
            if not qa.exact_equal(json_bundle, cnl_bundle):
                problem.append('JSON and CNL compile bundles differ')
            problem.extend(qa.verify_bundle(json_bundle, job['prompt'], assets, policy))
            accepted, rejection = qa.public_python_validate(REPO, job['prompt'])
            if not accepted:
                problem.append(f'public Python validator rejects positive prompt: {rejection}')
        compile_cache[key] = {
            'prompt': copy.deepcopy(job['prompt']),
            'bundle': json_bundle,
            'cnl_sha256': hashlib.sha256(cnl.encode('utf-8')).hexdigest(),
            'json_http': json_status,
            'cnl_http': cnl_status,
            'problems': problem,
            'evidence_dir': str(compile_dir),
        }
        write_json(output / 'compile-progress.json', {
            'version': 'som.compiler-legacy544-compile-progress.v1',
            'completed_unique_prompts': len(compile_cache), 'required_unique_prompts': 540,
            'evidence_dirs': [item['evidence_dir'] for item in compile_cache.values()],
        })

    # Each job gets a full independent bundle assembly and all existing checks.
    from m3_local_fixture import M3Fixture
    try:
        fixture_context = M3Fixture(output / 'fixture')
        with fixture_context as fixture:
            for index, job in enumerate(jobs):
                started = time.monotonic()
                job_dir = output / f'{index:04d}'
                job_dir.mkdir()
                record: dict[str, Any] = {
                    'id': job['id'], 'family': job['family'], 'group': job['group'],
                    'partition': job.get('partition'), 'variant': job['variant'],
                    'behavior_expected': bool(job['behavior']), 'passed': False, 'problems': [],
                    'prompt_sha256': qa.sha(job['prompt']),
                }
                row_interrupted = None
                compiled = compile_cache[record['prompt_sha256']]
                record['cnl_sha256'] = compiled['cnl_sha256']
                record['json_http'] = compiled['json_http']
                record['cnl_http'] = compiled['cnl_http']
                record['compile_evidence_dir'] = compiled['evidence_dir']
                if compiled['problems']:
                    record['problems'].extend(compiled['problems'])
                bundle = compiled['bundle']
                if bundle is not None:
                    try:
                        variant_bundle = independent_bundle(
                            job['prompt'], bundle, job['variant'], blocks, job.get('independent_dsl'))
                        variant_bundle_problems = qa.verify_bundle(
                            variant_bundle, job['prompt'], assets, None)
                        if variant_bundle_problems:
                            record['problems'].extend(
                                f'alternate bundle: {problem}' for problem in variant_bundle_problems)
                        record['variant_plan_semantics'] = 'validated by existing flow.validate_plan' if job['variant'] == 'checked' else 'frozen compiler projection'
                        record['bundle_sha256'] = canonical_hash(variant_bundle)
                        write_json(job_dir / 'variant.bundle.json', variant_bundle)
                        assembler_request = {'bundle': variant_bundle}
                        status, response = endpoint(client, '/v1/assemble', assembler_request)
                        assemble_raw = client.last_response_raw
                        qa.persist_api_exchange(job_dir, 'assemble', assembler_request, status, response, assemble_raw)
                        record['assemble_http'] = status
                        if status != 200 or response_field(response, 'status') != 'ok' or not isinstance(response_field(response, 'files'), dict):
                            record['problems'].append(f'assemble failed: HTTP {status} {response_field(response, "code")}')
                        else:
                            files = response_field(response, 'files')
                            record['product_sha256'] = hashlib.sha256(canonical_product(files).encode()).hexdigest()
                            record['product_file_sha256'] = {
                                name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                                for name, text in sorted(files.items()) if isinstance(text, str)
                            }
                            gold, gold_response_sha, gold_file_hashes = archive_for(job)
                            record['gold_response_sha256'] = gold_response_sha
                            record['archive_byte_parity'] = qa.exact_equal(files, gold)
                            if not record['archive_byte_parity']:
                                record['problems'].append('complete assembled files differ from the frozen archive')
                            product = job_dir / 'product'
                            product.mkdir()
                            write_product(product, files)
                            response_path = job_dir / 'response.json'
                            write_json(response_path, {'status': 'ok', 'files': files})

                            if job['family'] != 'legacy100':
                                from verify_python_quality import verify as verify_quality
                                quality_result = verify_quality(product, job_dir / 'quality')
                                record['quality'] = {
                                    'passed': quality_result.get('passed') is True,
                                    'source_unchanged': quality_result.get('source_unchanged'),
                                    'tool_versions': quality_result.get('tool_versions'),
                                    'checks': {name: item.get('exit_code') for name, item in quality_result.get('checks', {}).items()},
                                }
                                if not record['quality']['passed']:
                                    record['problems'].append('existing strict Python quality check failed')

                            if job['prompt'].get('version') == 'som.prompt.v7':
                                package = job['prompt']['package']
                                idem = {}
                                for name, source in files.items():
                                    if name.endswith('.py'):
                                        formatted = quality.format_source(name, source, formatter, quality_profile, package)
                                        idem[name] = formatted == source
                                record['formatter_idempotent'] = all(idem.values())
                                record['formatter_idempotence_files'] = idem
                                if not record['formatter_idempotent']:
                                    record['problems'].append('pinned native formatter changes a second-pass source')

                            behavior = run_job_behavior(job, job['prompt'], product, response_path, fixture, SCRIPTS)
                            record['behavior'] = behavior
                            if job['behavior'] and (behavior is None or not behavior.get('passed')):
                                record['problems'].append('required existing functional acceptance failed or was unavailable')
                            if not job['behavior'] and behavior is not None:
                                record['problems'].append('behavior checker ran outside the frozen behavior scope')
                    except BaseException as error:
                        record['problems'].append(type(error).__name__ + ': ' + str(error))
                        record['traceback'] = traceback.format_exc()
                        if not isinstance(error, Exception):
                            row_interrupted = error
                record['cleanup'] = cleanup_completed_job(
                    output, job_dir, job_dir / 'product', job_dir / 'quality')
                write_json(job_dir / 'cleanup-receipt.json', record['cleanup'])
                if not record['cleanup']['passed']:
                    record['problems'].append('safe per-job environment cleanup failed')
                record['passed'] = not record['problems']
                record['seconds'] = time.monotonic() - started
                records.append(record)
                interim = {
                    'version': 'som.compiler-legacy544-result.v1',
                    'status': 'running', 'inventory_sha256': expected_inventory_sha,
                    'completed_jobs': len(records), 'required_jobs': len(jobs),
                    'passed_jobs': sum(bool(item['passed']) for item in records),
                    'records': records,
                }
                write_json(output / 'result.pending.json', interim)
                print(json.dumps({'completed': len(records), 'required': len(jobs),
                                  'passed': interim['passed_jobs']}, separators=(',', ':')), flush=True)
                if row_interrupted is not None:
                    raise row_interrupted
    except BaseException as error:
        write_json(output / 'result.interrupted.json', {
            'version': 'som.compiler-legacy544-result.v1', 'status': 'interrupted',
            'completed_jobs': len(records), 'required_jobs': len(jobs), 'records': records,
            'interruption': type(error).__name__ + ': ' + str(error),
        })
        write_json(output / 'run-error.json', {'error': type(error).__name__, 'detail': str(error),
                                               'traceback': traceback.format_exc(), 'records': records})
        raise

    # Ensure historical inputs and frozen contract did not change during checks.
    _, current_inventory = build_jobs(
        Path(inventory['asset_index_oracle']['path']),
        Path(inventory['asset_index_oracle']['provenance_path']),
    )
    current_sha = hashlib.sha256(qa.canonical(current_inventory).encode()).hexdigest()
    input_unchanged = current_sha == expected_inventory_sha
    after_status, after_body, after_raw = candidate_health(client.base_url or '')
    (output / 'health-after.response.raw').write_bytes(after_raw)
    write_json(output / 'health-after.response.json', after_body)
    process_after = candidate_process_binding(Path(identity['candidate_runtime_receipt']),
                                              Path(identity['candidate_binary']), client.base_url or '')
    same_process = all(identity.get('process_before', {}).get(key) == process_after.get(key)
                       for key in ('pid', 'uid', 'lstart', 'argv', 'port', 'binary_sha256',
                                   'source_sha256', 'lsof_listener_fields', 'lsof_executable_fields'))
    health_binding = {
        'expected_health': expected_health,
        'build_receipt_sha256': identity['build_receipt_sha256'],
        'candidate_runtime_receipt_sha256': identity['candidate_runtime_receipt_sha256'],
        'runtime_acceptance_receipt_sha256': identity['runtime_acceptance_receipt_sha256'],
        'health_before_status': before_status, 'health_after_status': after_status,
        'health_before_sha256': hashlib.sha256(before_raw).hexdigest(),
        'health_after_sha256': hashlib.sha256(after_raw).hexdigest(),
        'before_matches_runtime': before_body == expected_health,
        'after_matches_runtime': after_body == expected_health,
        'stable': before_body == after_body,
        'process_before': identity.get('process_before'),
        'process_after': process_after, 'process_stable': same_process,
    }
    identity_ok = (before_status == 200 and after_status == 200 and before_body == expected_health
                   and after_body == expected_health and before_body == after_body
                   and same_process)
    all_passed = input_unchanged and identity_ok and len(records) == len(jobs) and all(item['passed'] for item in records)
    report = {
        'version': 'som.compiler-legacy544-result.v1',
        'status': 'passed' if all_passed else 'failed',
        'inventory_sha256': expected_inventory_sha,
        'input_inventory_unchanged': input_unchanged,
        'candidate_transport': 'HTTP' if client.base_url else 'offline JSONL probe',
        'contract_receipt_chain': inventory['compiler_freeze']['chain'],
        'asset_index_sha256': file_sha(asset_index_path),
        'candidate_binary_sha256': identity['candidate_binary_sha256'],
        'candidate_source_sha256': identity['source_archive_sha256'],
        'candidate_source_json_sha256': identity['source_json_sha256'],
        'candidate_asset_manifest_sha256': identity['asset_manifest_sha256'],
        'runtime_health_binding': health_binding,
        'external_input_hashes': {
            str(path): file_sha(path) for path in (asset_index_path, asset_provenance_path, formatter, quality_profile)
        },
        'old_model_invoked': False,
        'full_behavior_fixture': 'M3Fixture isolated local PostgreSQL and flow mocks',
        'compile_prompt_count': len(compile_cache),
        'compile_pairs': 2 * len(compile_cache),
        'assembly_product_count': len(records),
        'required_job_count': 544,
        'passed_job_count': sum(bool(item['passed']) for item in records),
        'records': records,
    }
    write_json(output / 'result.json', report)
    (output / 'result.pending.json').unlink(missing_ok=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--inventory', type=Path,
                        default=OUTPUT_DIR / 'inventory.json')
    parser.add_argument('--out', type=Path,
                        default=OUTPUT_DIR / 'run')
    parser.add_argument('--http-base')
    parser.add_argument('--probe', help='local offline JSONL API adapter command')
    parser.add_argument('--asset-index', type=Path)
    parser.add_argument('--asset-provenance', type=Path)
    parser.add_argument('--formatter', type=Path)
    parser.add_argument('--candidate-binary', type=Path)
    parser.add_argument('--source-json', type=Path)
    parser.add_argument('--runtime-receipt', type=Path)
    parser.add_argument('--quality-profile', type=Path,
                        default=REPO / 'som-rs/contracts/python-quality-v1/pyproject.toml')
    args = parser.parse_args()

    asset_index_path = args.asset_index.resolve() if args.asset_index else DEFAULT_ASSET_INDEX
    asset_provenance_path = args.asset_provenance.resolve() if args.asset_provenance else DEFAULT_ASSET_PROVENANCE
    jobs, inventory = build_jobs(asset_index_path, asset_provenance_path)
    if args.inventory_only:
        args.inventory = require_new_output(args.inventory, OUTPUT_DIR)
        require_new_output(args.inventory.with_suffix('.ids.json'), OUTPUT_DIR)
        require_new_output(args.inventory.with_suffix('.md'), OUTPUT_DIR)
        write_json(args.inventory, inventory)
        write_json(args.inventory.with_suffix('.ids.json'), {
            'version': 'som.compiler-legacy544-ids.v1',
            'groups': [{k: row[k] for k in ('id', 'family', 'group', 'partition', 'index', 'variant', 'behavior', 'prompt_sha256', 'gold_response_path', 'gold_response_sha256', 'gold_product_sha256')}
                       for row in inventory['groups']],
        })
        args.inventory.with_suffix('.md').write_text(inventory_markdown(inventory), encoding='utf-8')
        print(json.dumps({'inventory': str(args.inventory), 'sha256': hashlib.sha256(qa.canonical(inventory).encode()).hexdigest(),
                          'counts': inventory['counts']}, ensure_ascii=False, indent=2))
        return

    args.inventory = args.inventory.resolve()
    if not args.inventory.is_relative_to(OUTPUT_DIR.resolve()):
        raise ValueError(f'inventory must stay under {OUTPUT_DIR}')
    pinned = json.loads(args.inventory.read_text(encoding='utf-8'))
    if not qa.exact_equal(pinned, inventory):
        raise ValueError('current frozen inputs differ from the reviewed inventory')
    if not args.asset_index or not args.formatter:
        raise ValueError('--asset-index, --asset-provenance and --formatter are required for candidate acceptance')
    if not args.http_base:
        raise ValueError('final candidate identity requires an HTTP candidate endpoint')
    if not args.candidate_binary or not args.source_json or not args.runtime_receipt:
        raise ValueError('--candidate-binary, --source-json and --runtime-receipt are required for candidate acceptance')
    identity, expected_health = candidate_identity(args.candidate_binary.resolve(), args.source_json.resolve(),
                                                   args.runtime_receipt.resolve(), args.http_base, asset_index_path,
                                                   args.source_json.resolve().parent / 'assets')
    client = qa.Client(args.http_base, None)
    try:
        report = run_acceptance(client, pinned, jobs, args.out.resolve(), args.asset_index.resolve(),
                                args.formatter.resolve(), args.quality_profile.resolve(),
                                args.asset_provenance.resolve(), identity, expected_health)
    except BaseException as error:
        output = args.out.resolve()
        if output.is_dir():
            pending_path = output / 'result.pending.json'
            pending = json.loads(pending_path.read_text(encoding='utf-8')) if pending_path.is_file() else {}
            compile_progress_path = output / 'compile-progress.json'
            compile_progress = json.loads(compile_progress_path.read_text(encoding='utf-8')) if compile_progress_path.is_file() else {}
            write_json(output / 'result.interrupted.json', {
                'version': 'som.compiler-legacy544-result.v1', 'status': 'interrupted',
                'completed_jobs': pending.get('completed_jobs', 0), 'required_jobs': 544,
                'completed_unique_prompts': compile_progress.get('completed_unique_prompts', 0),
                'required_unique_prompts': 540,
                'records': pending.get('records', []),
                'interruption': type(error).__name__ + ': ' + str(error),
            })
        raise
    finally:
        client.close()
    print(json.dumps({'status': report['status'], 'out': str(args.out.resolve()),
                      'passed': report['passed_job_count'], 'required': report['required_job_count']}, indent=2))
    raise SystemExit(0 if report['status'] == 'passed' else 2)


if __name__ == '__main__':
    from m3_local_fixture import interruption
    with interruption():
        main()
