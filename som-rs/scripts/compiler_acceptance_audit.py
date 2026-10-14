#!/usr/bin/env python3
"""Fail-closed, read-only audit of SOM Rust compiler candidate 003 receipts.

This tool reads receipts and saved evidence only. It never calls a service,
starts a process, reads model outputs, or changes repository state.
"""
from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import io
import json
import os
import statistics
import tarfile
import tokenize
from pathlib import Path
from typing import Any


REPO_DEFAULT = Path('/Users/chrischeng/faberlines/som')
SCRATCH = Path('/private/tmp/som-rust-final-root-audit')
EXPECTED_BINARY = '3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02'
HISTORICAL_AMENDMENT_001 = {
    'manifest': '774d5195646e5a112b6f96553ae578d6ee57a177f326cb09146b1a29893d0167',
    'installed': '8dbe0f5e4bad8ef6b26f3f51881e8a981daa689de6f58af0686f98a7d480beac',
    'archive': 'd69dab07c3ef3b43c8cef6b49b94740575fcc5c0d256568ebd0b51e457a0f599',
}
HISTORICAL_AMENDMENT_001_FILES = {
    'som-rs/scripts/compiler_acceptance/public_products_corrected.py': '3826a972e88e92a18e5fff9080fa600b2c862ba1cc296e629e9abda7c603e80f',
    'som-rs/scripts/compiler_acceptance/reference_span_prepass.py': '311bd25acb78b43ddf459c1a3fc1a4f4980841b25dd6e25e647dceeb190cd9bb',
    'som-rs/scripts/compiler_acceptance/qualify_prepass.py': '527ea77f8fdeb99e96e06b9e97dbc19cb26a79ebdf3c089495439dde00c47373',
    'som-rs/scripts/compiler_acceptance/qualify_runner_bindings.py': 'eb4effc59e69453f247375956078acd6036ee83aa61fdd32c957df37753cae4b',
    'som-rs/scripts/compiler_acceptance/qualify_formatted_reference.py': 'c256acbe2950f518861d859a612fdd7f375b3460355303b989b0b07a25d12ae2',
    'som-rs/scripts/compiler_acceptance/prepare_amendment.py': '14892c0f109e8061f5c8e3838db43dc37d8088f496abad7b7e021b39d83ce4f8',
}
FAILED_AMENDMENT_002 = {
    'manifest': 'f211e7d4420232a7cf70c55106c299699677ab018a5f5040dda06e4494f05006',
    'installed': '46b61ec2723f9b1294448b8834a759fe5b2604249a21108b9fa5af1edd4d92e7',
    'archive': 'c214445efdbe391a8018a78047db4edd3da21f3c3d37dc4b80b8a16955a46316',
    'controller': '18afb1d60460381669fab37f3cc78a4404db61051f37f7ac53fa2dafc03a0afe',
    'stderr': 'a0f405d11551f0961cd373ea99d52150314b7528787cdcccd98e310b763b02cc',
}
PUBLIC_REFERENCE_FAILURES = {'REF_BRANCH_EXPORT_SAME_TYPE', 'PUBLIC_v7-branch-null'}
PUBLIC_CORRECTED_SUBDIRS = {
    'REF_BRANCH_EXPORT_SAME_TYPE': '118',
    'PUBLIC_v7-branch-null': '126',
}
FREEZE_HASHES = {
    'som-rs/evidence/rust-compiler-contract-freeze-2026-10-13.json': '2ce6e7027687c7e06ee92a4d6c326fd5f00b6e17f83d5bd80db4e82ebb8cee79',
    'som-rs/evidence/rust-compiler-contract-amendment-001.json': '041b33e72d22833091c69ed6407ba979a1b5320e81214aa939e588032af4a797',
    'som-rs/evidence/rust-compiler-contract-amendment-002.json': 'c95b79a89737b2568e7b92529c7b5012c6035bc8e62f830eb010693a4445326d',
}
ASSET_ROLES = {
    'grammar', 'compiler_contract', 'selection_policy', 'catalog', 'block_library',
    'backend', 'formatter', 'literal_policy', 'serializer', 'semantic_contracts',
}
UNRUN_DISCHARGES = {
    'bundle_saved_replay': {
        'runtime': ['saved_bundle_replay_fresh_process'],
        'gaps': [f'fresh_v{v}_saved_bundle_replay' for v in range(1, 8)],
    },
    'equivalent_cnl_json': {'public': ['all_127_compile_pairs']},
    'all_versions': {
        'public': ['all_seven_prompt_versions'],
        'gaps': [f'fresh_v{v}_cross_format_bundle' for v in range(1, 8)],
    },
    'checked_variant': {'legacy': ['m2-alternative-checked', 'supplement-mongodb-child-patch-read-checked', 'supplement-postgresql-child-read-checked']},
    'local_asset_tamper': {'runtime': ['tamper_block']},
    'self_signed_asset_tamper': {'runtime': ['self_signed_block']},
    'missing_asset': {'runtime': ['missing_required_asset']},
    'structural_limits': {'gaps': [f'scope_v{v}_{kind}_limit' for v in (1, 3, 6, 7) for kind in ('file', 'block')]},
    'large_valid_compact_output': {'runtime': ['large_valid_compact_output']},
}
RUNTIME_IDS = {
    'health_contract', 'cli_compile_full_output', 'legacy_generate_cli_full_output',
    'cli_generate_full_response_matches_http', 'cli_bare_bundle_canonical_assemble',
    'explicit_cnl_format_accepted', 'explicit_json_format_accepted', 'explicit_formats_same_bundle',
    'missing_format', 'unsupported_format', 'explicit_cnl_format_missing_source',
    'duplicate_json_key', 'invalid_utf8', 'compile_413', 'assemble_413', 'generate_preserved_413',
    'large_valid_compact_output', 'saved_bundle_replay_fresh_process', 'tamper_block',
    'tamper_formatter', 'tamper_profile', 'tamper_manifest', 'self_signed_block',
    'missing_required_asset', 'loaded_formatter_changed', 'loaded_profile_changed',
    'cli_http_reject_compile', 'cli_http_reject_assemble', 'cli_missing_file_compile',
    'cli_missing_file_assemble', 'cli_invalid_utf8_compile', 'cli_invalid_utf8_assemble',
    'cli_duplicate_bundle_json', 'cli_connection_refused_compile', 'cli_connection_refused_assemble',
    'cli_nonloopback_compile', 'cli_nonloopback_assemble',
}
GAP_IDS = {f'scope_v{v}_{kind}_limit' for v in (1, 3, 6, 7) for kind in ('file', 'block')}
GAP_IDS |= {f'fresh_v{v}_{kind}' for v in range(1, 8) for kind in (
    'cross_format_bundle', 'new_process_compile_bundle_parity', 'saved_bundle_replay')}
GAP_IDS |= {'redirect_cli_compile', 'redirect_cli_assemble'}
EXPECTED_GROUP_COUNTS = {
    'disjoint_groups': {'legacy100': 100, 'legacy214': 214, 'quality218': 218, 'supplement12': 12},
    'legacy214_subgroups': {'services14': 14, 'compositions120': 120, 'blind80': 80},
    'quality218_partitions': {'formal114': 114, 'dev12': 12, 'maximum-matrix': 90, 'alternative-plans': 2},
    'overlapping_quality_scopes': {'formal114': 114, 'dev12': 12, 'quality218': 218, 'behavior126': 126, 'quality_parity_only92': 92},
    'supplement_behavior': 12, 'job_count': 544, 'unique_prompt_count': 540,
    'duplicate_prompt_group_count': 3, 'assembly_count': 544, 'compile_pair_count': 540,
}
CHECKED_VARIANTS = {
    'm2-alternative-checked',
    'supplement-mongodb-child-patch-read-checked',
    'supplement-postgresql-child-read-checked',
}
EXPECTED_M2_PASS = 'REF_NULLABLE_OPTIONAL_FIELD'
EXPECTED_M2_FAILURES = {
    'PUBLIC_v7-branch-null': ('AssertionError', '{"detail":[{"type":"extra_forbidden","loc":["body","parent","title"],"msg":"Extra inputs are not permitted"}]}'),
    'PUBLIC_v7-empty-branch': ('AssertionError', '{"detail":[{"type":"extra_forbidden","loc":["body","parent","title"],"msg":"Extra inputs are not permitted"}]}'),
    'PUBLIC_v7-empty-field-segments': ('KeyError', "''"),
    'REF_BRANCH_EXPORT_SAME_TYPE': ('AssertionError', 'unresolved branch probes: choose'),
    **{f'REF_COMPARE_{op}': ('AssertionError', 'unresolved branch probes: compare') for op in ('EQ', 'NE', 'LT', 'LE', 'GT', 'GE')},
}


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if type(value) in (int, float, str):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if isinstance(value, list):
        return '[' + ','.join(canonical(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(canonical(key) + ':' + canonical(value[key]) for key in sorted(value)) + '}'
    raise TypeError(type(value).__name__)


def exact_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(exact_equal(a, b) for a, b in zip(left, right))
    if type(left) is float and left == 0.0:
        import math
        return math.copysign(1.0, left) == math.copysign(1.0, right)
    return left == right


class Audit:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.unrun: list[dict[str, str]] = []

    def add(self, name: str, state: str, detail: str = '') -> None:
        self.checks.append({'name': name, 'state': state, 'detail': detail})
        if state == 'UNRUN':
            self.unrun.append({'check': name, 'reason': detail})

    def require(self, name: str, ok: bool, detail: str = '') -> None:
        self.add(name, 'PASS' if ok else 'FAIL', detail)

    def missing(self, name: str, path: Path) -> None:
        self.add(name, 'UNRUN', f'missing required receipt: {path}')

    def json(self, name: str, path: Path) -> Any | None:
        if not path.is_file():
            self.missing(name, path)
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception as error:
            self.add(name, 'FAIL', f'{type(error).__name__}: {error}')
            return None

    @property
    def passed(self) -> bool:
        return all(row['state'] == 'PASS' for row in self.checks) and not self.unrun


def freeze_chain(audit: Audit, repo: Path) -> dict[str, Any] | None:
    docs: dict[str, Any] = {}
    for rel, expected in FREEZE_HASHES.items():
        path = repo / rel
        if not path.is_file():
            audit.missing('freeze.' + rel, path)
            return None
        audit.require('freeze.hash.' + rel, file_sha(path) == expected, file_sha(path))
        docs[rel] = json.loads(path.read_text(encoding='utf-8'))
    initial, amend1, amend2 = (docs[k] for k in FREEZE_HASHES)
    audit.require('freeze.initial_before_implementation', initial.get('status') == 'contract_frozen_before_implementation' and initial.get('implementation_started_before_freeze') is False)
    audit.require('freeze.amendment_chain',
                  amend1.get('previous_receipt_sha256') == FREEZE_HASHES[next(iter(FREEZE_HASHES))]
                  and amend2.get('previous_receipt_sha256') == FREEZE_HASHES[list(FREEZE_HASHES)[1]])
    audit.require('freeze.amendment_001_scope', amend1.get('amendment', {}).get('cases_changed') is False and amend1.get('amendment', {}).get('quality_gates_changed') is False)
    audit.require('freeze.amendment_002_scope', amend2.get('implementation_already_in_progress') is True
                  and amend2.get('changed_paths') == ['som-rs/tests/compiler-v1/cnl-negative-cases.json']
                  and amend2.get('cases') == {'positive': 119, 'typed': 40, 'cnl_negative': 15, 'canonical': 11})
    base, middle, latest = initial.get('files', {}), amend1.get('files', {}), amend2.get('files', {})
    audit.require('freeze.42_file_inventory', len(base) == len(middle) == len(latest) == 42 and set(base) == set(middle) == set(latest))
    diff1 = {path for path in base if base.get(path) != middle.get(path)}
    diff2 = {path for path in middle if middle.get(path) != latest.get(path)}
    audit.require('freeze.archive_change_scope', diff1 == {'som-rs/contracts/compiler-v1/selection-policy.json'}
                  and diff2 == {'som-rs/tests/compiler-v1/cnl-negative-cases.json'}, f'{sorted(diff1)}; {sorted(diff2)}')
    mismatches = [path for path, digest in latest.items() if not (repo / path).is_file() or file_sha(repo / path) != digest]
    audit.require('freeze.latest_hashes', not mismatches, ','.join(mismatches[:12]))
    archive1 = amend1.get('amendment', {}).get('original_policy_archive', '')
    archive2 = amend2.get('archived_fixture', '')
    audit.require('freeze.archives', bool(archive1) and (repo / archive1).is_file()
                  and file_sha(repo / archive1) == base.get('som-rs/contracts/compiler-v1/selection-policy.json')
                  and bool(archive2) and (repo / archive2).is_file()
                  and file_sha(repo / archive2) == amend2.get('archived_fixture_sha256')
                  == middle.get('som-rs/tests/compiler-v1/cnl-negative-cases.json'))
    return {
        'version': 'som.compiler-contract-chain-check.v1',
        'initial_sha256': FREEZE_HASHES[list(FREEZE_HASHES)[0]],
        'amendment_sha256': [FREEZE_HASHES[list(FREEZE_HASHES)[1]], FREEZE_HASHES[list(FREEZE_HASHES)[2]]],
        'latest_file_count': len(latest), 'latest_hashes_match': not mismatches,
        'preimplementation_basis': 'initial receipt only; amendment002 records implementation already in progress',
    }


def verify_candidate(audit: Audit, repo: Path, candidate: Path, acceptance: Path) -> dict[str, Any]:
    src_path = candidate / 'source.json'
    src = audit.json('candidate.source_receipt', src_path) or {}
    src_tar = candidate / 'source.tar'
    binary = candidate / 'som'
    source_hash = file_sha(src_path) if src_path.is_file() else ''
    binary_hash = file_sha(binary) if binary.is_file() else ''
    audit.require('candidate.source_map_count_73', src.get('version') == 'som.compiler-candidate-source.v1' and len(src.get('files', {})) == 73,
                  str(len(src.get('files', {}))))
    audit.require('candidate.binary_sha256', binary.is_file() and binary_hash == EXPECTED_BINARY, binary_hash)
    audit.require('candidate.source_archive', src_tar.is_file() and src.get('archive_sha256') == (file_sha(src_tar) if src_tar.is_file() else None))
    source_mismatches = []
    for rel, digest in src.get('files', {}).items():
        path = repo / rel
        if not path.is_file() or file_sha(path) != digest:
            source_mismatches.append(rel)
    audit.require('candidate.live_source_73', len(src.get('files', {})) == 73 and not source_mismatches,
                  ','.join(source_mismatches[:12]))

    tools_path = candidate / 'acceptance-tools.json'
    tools_doc = audit.json('candidate.acceptance_tools_receipt', tools_path) or {}
    tools_tar = candidate / 'acceptance-tools.tar'
    tools_map = tools_doc.get('files', {})
    audit.require('candidate.acceptance_tools_57_count', len(tools_map) == 57, str(len(tools_map)))
    audit.require('candidate.acceptance_tools_archive', tools_tar.is_file()
                  and tools_doc.get('archive_sha256') == (file_sha(tools_tar) if tools_tar.is_file() else None))
    audit.require('candidate.acceptance_tools_binary_binding', tools_doc.get('candidate_binary_sha256') == binary_hash)
    audit.require('candidate.acceptance_tools_policy', tools_doc.get('case_gates_weakened') is False
                  and tools_doc.get('historical_verifier_failures_remain_failed') is True)
    tool_mismatches = []
    for rel, digest in tools_map.items():
        path = repo / rel
        if not path.is_file() or file_sha(path) != digest:
            tool_mismatches.append(rel)
    audit.require('candidate.live_acceptance_tools_57', len(tools_map) == 57 and not tool_mismatches,
                  ','.join(tool_mismatches[:12]))

    build_path = candidate / 'build.json'
    build = audit.json('candidate.build_receipt', build_path) or {}
    audit.require('candidate.build_receipt_bound', build.get('complete') is True and build.get('source_unchanged') is True
                  and build.get('source_sha256') == source_hash and build.get('binary_sha256') == binary_hash
                  and bool(build.get('commands')) and all(row.get('exit_code') == 0 for row in build.get('commands', [])))
    runtime_path = candidate / 'runtime.json'
    runtime = audit.json('candidate.runtime_metadata', runtime_path) or {}
    audit.require('candidate.runtime_bound', runtime.get('binary_sha256') == binary_hash
                  and runtime.get('source_sha256') == source_hash and runtime.get('accepted') is False
                  and runtime.get('health', {}).get('purpose') == 'som.rust-compiler.v1'
                  and runtime.get('health', {}).get('model_loaded') is False)

    manifest_path = candidate / 'assets/manifest.json'
    manifest = audit.json('candidate.asset_manifest', manifest_path) or {}
    expected_payloads = {'blocks.json', 'legacy-blocks.json', 'quality-blocks.json', 'pyproject.toml', 'quality.json', 'ruff'}
    payloads = manifest.get('files', {})
    audit.require('candidate.asset_payload_exact_six', set(payloads) == expected_payloads and len(payloads) == 6,
                  ','.join(sorted(payloads)))
    payload_bad = []
    for rel, digest in payloads.items():
        path = candidate / 'assets' / rel
        if not path.is_file() or file_sha(path) != digest:
            payload_bad.append(rel)
    audit.require('candidate.asset_payload_hashes', len(payloads) == 6 and not payload_bad, ','.join(payload_bad))
    backend_files = manifest.get('backend', {}).get('files', {})
    asset_index_path = acceptance / 'per-version-asset-index-003.json'
    provenance_path = acceptance / 'per-version-asset-index-provenance-003.json'
    index = audit.json('candidate.independent_asset_index', asset_index_path) or {}
    provenance = audit.json('candidate.asset_index_provenance', provenance_path) or {}
    index_hash = file_sha(asset_index_path) if asset_index_path.is_file() else ''
    manifest_hash = file_sha(manifest_path) if manifest_path.is_file() else ''
    generator_path = repo / 'som-rs/scripts/compiler_acceptance/asset_index_generator.py'
    audit.require('candidate.asset_provenance_binding', provenance.get('generator_sha256')
                  == (file_sha(generator_path) if generator_path.is_file() else None)
                  and provenance.get('package_manifest_sha256') == manifest_hash
                  and provenance.get('package_manifest_matches_independent_sources') is True
                  and not provenance.get('package_manifest_mismatch_paths')
                  and not provenance.get('package_manifest_mismatch_fields')
                  and verify_embedded_freeze(provenance.get('receipt_chain')))
    # Backend inputs span Rust sources, contracts, catalogs, raw block libraries,
    # and approved tools. The independent provenance map records each input hash.
    source_map = provenance.get('source_hashes', {}).get('backend_source_map', {})
    audit.require('candidate.backend_source_26', len(backend_files) == 26
                  and backend_files == source_map, f'package={len(backend_files)} independent={len(source_map)}')
    versions = index.get('by_prompt_version', {})
    expected_versions = {f'som.prompt.v{i}' for i in range(1, 8)}
    audit.require('candidate.asset_roles_exact_7', set(versions) == expected_versions and len(versions) == 7
                  and all(set(row) == ASSET_ROLES for row in versions.values()))
    backend_digest = hashlib.sha256(canonical(manifest.get('backend')).encode('utf-8')).hexdigest()
    audit.require('candidate.asset_index_backend_bound',
                  {row.get('backend', {}).get('sha256') for row in versions.values()} == {backend_digest}
                  and provenance.get('source_hashes', {}).get('backend_source_map') == backend_files)
    return {
        'source_json': src, 'source_json_path': src_path, 'source_json_sha256': source_hash,
        'source_archive_path': src_tar, 'source_archive_sha256': src.get('archive_sha256'),
        'binary_path': binary, 'binary_sha256': binary_hash,
        'build_path': build_path, 'build_sha256': file_sha(build_path) if build_path.is_file() else None,
        'runtime_path': runtime_path, 'runtime_sha256': file_sha(runtime_path) if runtime_path.is_file() else None,
        'runtime': runtime, 'manifest': manifest, 'manifest_path': manifest_path,
        'manifest_sha256': manifest_hash, 'asset_index': index, 'asset_index_path': asset_index_path,
        'asset_index_sha256': index_hash, 'provenance': provenance,
    }


def verify_receipt_binding(audit: Audit, label: str, doc: dict[str, Any] | None,
                           candidate: dict[str, Any]) -> bool:
    if doc is None:
        return False
    expected = candidate['binary_sha256']
    got = doc.get('candidate_binary_sha256') or doc.get('candidate', {}).get('binary_sha256')
    if got is None:
        audit.add(label + '.candidate_binary_binding', 'UNRUN', 'receipt has no candidate binary SHA256')
        ok = False
    else:
        ok = got == expected
        audit.require(label + '.candidate_binary_binding', ok, str(got))
    asset_got = doc.get('asset_index_sha256') or doc.get('asset_index', {}).get('sha256')
    if asset_got is None:
        audit.add(label + '.asset_index_binding', 'UNRUN', 'receipt has no independent asset-index SHA256')
        ok = False
    else:
        asset_ok = asset_got == candidate['asset_index_sha256']
        audit.require(label + '.asset_index_binding', asset_ok, str(asset_got))
        ok = ok and asset_ok
    return ok


def verify_counted_receipt(audit: Audit, label: str, path: Path, candidate: dict[str, Any],
                           expected_ids: set[str], expected_count: int,
                           rows_key: str = 'checks') -> dict[str, Any] | None:
    doc = audit.json(label + '.receipt', path)
    if doc is None:
        return None
    rows = doc.get(rows_key)
    if not isinstance(rows, list):
        audit.require(label + '.rows_are_list', False, rows_key)
        rows = []
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    complete = (doc.get('status') in {'passed', 'pass'} and doc.get('passed') is True
                and doc.get('unrun') in (None, []) and len(rows) == expected_count
                and len(set(ids)) == expected_count and set(ids) == expected_ids
                and all(row.get('passed') is True for row in rows))
    audit.require(label + '.exact_passed_inventory', complete,
                  f"status={doc.get('status')} rows={len(rows)}/{expected_count} IDs={len(set(ids))}/{expected_count}")
    verify_receipt_binding(audit, label, doc, candidate)
    return doc


def verify_runtime_gaps(audit: Audit, acc: Path, candidate: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    runtime = verify_counted_receipt(audit, 'runtime', acc / 'runtime-003/result.json', candidate, RUNTIME_IDS, 37)
    gaps = verify_counted_receipt(audit, 'gaps', acc / 'gaps-003/result.json', candidate, GAP_IDS, 31)
    if runtime:
        check_health(audit, 'runtime', runtime, candidate)
        rows = {row.get('id'): row for row in runtime.get('checks', [])}
        large = rows.get('large_valid_compact_output', {})
        audit.require('runtime.large_compact_exact', large.get('passed') is True
                      and large.get('literal_exact') is True
                      and large.get('formatted_utf8_output_bytes', 0) > 65536
                      and large.get('literal_expected', {}).get('text_chars') == 22000)
        audit.require('runtime.saved_replay', rows.get('saved_bundle_replay_fresh_process', {}).get('passed') is True)
    if gaps:
        ids = {row.get('id'): row for row in gaps.get('checks', [])}
        expected = {f'fresh_v{v}_{kind}' for v in range(1, 8) for kind in ('cross_format_bundle', 'new_process_compile_bundle_parity', 'saved_bundle_replay')}
        expected |= {f'scope_v{v}_{kind}_limit' for v in (1, 3, 6, 7) for kind in ('file', 'block')}
        expected |= {'redirect_cli_compile', 'redirect_cli_assemble'}
        audit.require('gaps.all_fresh_versions_and_limits', expected == GAP_IDS and all(ids.get(key, {}).get('passed') is True for key in expected))
    return runtime, gaps


def check_health(audit: Audit, label: str, doc: dict[str, Any], candidate: dict[str, Any]) -> None:
    rows = {row.get('id'): row for row in doc.get('checks', []) if isinstance(row, dict)}
    health = rows.get('health_contract', {}).get('actual', {})
    expected = candidate['runtime'].get('health', {})
    audit.require(label + '.health_matches_candidate', health == expected
                  and health.get('backend_sha256') == hashlib.sha256(canonical(candidate['manifest'].get('backend')).encode()).hexdigest()
                  and health.get('model_loaded') is False and health.get('purpose') == 'som.rust-compiler.v1')


def verify_freeze_embedded(audit: Audit, label: str, value: Any) -> None:
    if not isinstance(value, dict):
        audit.add(label + '.freeze_chain', 'UNRUN', 'receipt does not embed its freeze-chain verification')
        return
    audit.require(label + '.freeze_status', value.get('status') == 'passed')
    audit.require(label + '.initial_claim', value.get('initial_preimplementation_freeze', {}).get('status')
                  == 'contract_frozen_before_implementation'
                  and value.get('initial_preimplementation_freeze', {}).get('implementation_started_before_freeze') is False)
    hashes = [value.get('initial_preimplementation_freeze', {}).get('receipt_sha256')]
    hashes += [row.get('receipt_sha256') for row in value.get('amendments', [])]
    wanted = list(FREEZE_HASHES.values())
    audit.require(label + '.freeze_chain', hashes == wanted and value.get('latest_bound_file_count') == 42
                  and value.get('latest_hashes_match') is True
                  and value.get('preimplementation_claim_basis') == 'initial receipt only; amendment002 explicitly records implementation already in progress')


def verify_embedded_freeze(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    hashes = [value.get('initial_preimplementation_freeze', {}).get('receipt_sha256')]
    hashes += [row.get('receipt_sha256') for row in value.get('amendments', [])]
    return (value.get('status') == 'passed' and hashes == list(FREEZE_HASHES.values())
            and value.get('latest_bound_file_count') == 42 and value.get('latest_hashes_match') is True
            and value.get('preimplementation_claim_basis') == 'initial receipt only; amendment002 explicitly records implementation already in progress')


def load_fixture_ids(repo: Path, rel: str, list_key: str = 'cases') -> list[str]:
    data = json.loads((repo / rel).read_text(encoding='utf-8'))
    return [item.get('id') for item in data.get(list_key, [])]


def verify_saved_exchange(audit: Audit, label: str, directory: Path, prefix: str,
                          expected_status: int | None = None) -> bool:
    request_path = directory / f'{prefix}.request.json'
    response_path = directory / f'{prefix}.response.json'
    raw_b64_path = directory / f'{prefix}.response.raw.b64'
    if not all(path.is_file() for path in (request_path, response_path, raw_b64_path)):
        audit.add(label, 'UNRUN', 'saved request/response wrapper/raw body is incomplete')
        return False
    try:
        wrapper = json.loads(response_path.read_text(encoding='utf-8'))
        raw = base64.b64decode(''.join(raw_b64_path.read_text(encoding='ascii').split()), validate=True)
        status = wrapper.get('http_status')
        body = wrapper.get('body')
        same = json.loads(raw) == body and (expected_status is None or status == expected_status)
        audit.require(label, same, f'HTTP {status}; raw_bytes={len(raw)}; raw_sha256={hashlib.sha256(raw).hexdigest()}')
        return same
    except Exception as error:
        audit.require(label, False, f'{type(error).__name__}: {error}')
        return False


def expected_frozen_argv(repo: Path, candidate: dict[str, Any], acc: Path) -> list[str]:
    runtime = candidate['runtime']
    original = json.loads((acc / 'frozen-003.json').read_text(encoding='utf-8'))
    seed_rel = original.get('seed_bundle', {}).get('path')
    if not isinstance(seed_rel, str):
        return []
    return [
        str(repo / 'som-rs/runtime/acceptance-m3-venv/bin/python'),
        str(repo / 'som-rs/scripts/compiler_acceptance/qa_harness.py'),
        '--repo', str(repo), '--http-base', f"http://127.0.0.1:{runtime.get('port')}",
        '--seed-bundle', str(repo / seed_rel),
        '--asset-index', str(candidate['asset_index_path']),
        '--out', str(acc / 'frozen-003b.json'),
    ]


def valid_process_identity(value: Any, candidate: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    runtime = candidate['runtime']
    pid, port = runtime.get('pid'), runtime.get('port')
    binary = str(candidate['binary_path'].resolve())
    if (type(pid) is not int or type(port) is not int or value.get('pid') != pid
            or value.get('uid') != os.getuid() or not isinstance(value.get('lstart'), str) or not value['lstart']
            or value.get('port') != port or value.get('argv') != runtime.get('argv')
            or value.get('binary_sha256') != candidate['binary_sha256']
            or value.get('source_sha256') != candidate['source_json_sha256']):
        return False
    listener = value.get('lsof_listener_fields', [])
    pids = {row[1:] for row in listener if isinstance(row, str) and row.startswith('p')}
    names = [row[1:] for row in listener if isinstance(row, str) and row.startswith('n')]
    if pids != {str(pid)} or not names or any(
            not (f'127.0.0.1:{port}' in name or f'[::1]:{port}' in name) for name in names):
        return False
    executable = value.get('lsof_executable_fields', [])
    return f'n{binary}' in executable and any(row == f'n{binary}' for row in executable if isinstance(row, str))


def frozen_exit_ok(exit_code: Any, report: Any) -> bool:
    if not isinstance(report, dict):
        return False
    unrun = report.get('unrun', [])
    names = {row.get('case') for row in unrun if isinstance(row, dict)}
    return (exit_code == 2 and report.get('completed') is False
            and len(unrun) == 9 and names == set(UNRUN_DISCHARGES))


def exact_argv_matches(actual: Any, expected: list[str]) -> bool:
    return isinstance(actual, list) and actual == expected


def verify_frozen_003b_report(audit: Audit, report: dict[str, Any], repo: Path,
                              candidate: dict[str, Any], path: Path) -> bool:
    sections = report.get('sections', {})
    fixtures = {
        'positive_cnl_json': ('som-rs/tests/compiler-v1/cnl-positive-fixtures.json', 119, 'cases'),
        'typed_oracle': ('som-rs/tests/compiler-v1/typed-json-oracle-cases.json', 40, 'cases'),
        'cnl_negative': ('som-rs/tests/compiler-v1/cnl-negative-cases.json', 15, 'cases'),
        'canonical_vectors': ('som-rs/contracts/compiler-v1/canonical-vectors.json', 11, 'vectors'),
    }
    good = True
    for name, (rel, count, key) in fixtures.items():
        expected = load_fixture_ids(repo, rel, key)
        section = sections.get(name, {})
        rows = section.get('records', [])
        ids = [row.get('id') for row in rows if isinstance(row, dict)]
        ok = (section.get('passed') is True and section.get('count') == count
              and len(rows) == count and ids == expected
              and all(row.get('passed') is True for row in rows))
        audit.require(f'frozen_003b.{name}_exact_and_passed', ok, f'{len(rows)}/{count}')
        good &= ok
    original = json.loads((path.parent / 'frozen-003.json').read_text(encoding='utf-8'))
    expected_unrun = {row.get('case') for row in original.get('unrun', [])}
    unrun = report.get('unrun', [])
    got_unrun = {row.get('case') for row in unrun if isinstance(row, dict)}
    unrun_ok = (len(unrun) == 9 and got_unrun == expected_unrun == set(UNRUN_DISCHARGES)
                and all(row.get('section') == 'bundle_tampering' for row in unrun))
    audit.require('frozen_003b.same_exact_nine_unrun', unrun_ok,
                  'supplemental rerun preserves the original nine deferred IDs')
    good &= unrun_ok
    bundle_cases = json.loads((repo / 'som-rs/contracts/compiler-v1/bundle-cases.json').read_text())['cases']
    expected_bundle = [row['id'] for row in bundle_cases
                       if row.get('expected') not in ('accept', 'startup refusal')
                       and row['id'] not in UNRUN_DISCHARGES]
    bundle = sections.get('bundle_tampering', {})
    rows = bundle.get('records', [])
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    bundle_ok = (bundle.get('passed') is True and bundle.get('count') == 14
                 and ids == expected_bundle and len(rows) == 14
                 and all(row.get('passed') is True for row in rows))
    audit.require('frozen_003b.bundle_cases_exact_and_passed', bundle_ok)
    good &= bundle_ok
    audit.require('frozen_003b.report_not_complete', report.get('completed') is False)
    good &= report.get('completed') is False
    verify_freeze_embedded(audit, 'frozen_003b', report.get('freeze'))
    good &= verify_embedded_freeze(report.get('freeze'))
    audit.require('frozen_003b.asset_index_bound', report.get('asset_index', {}).get('sha256') == candidate['asset_index_sha256']
                  and report.get('asset_index', {}).get('path') == str(candidate['asset_index_path'])
                  and report.get('seed_bundle', {}).get('path') == str(repo / original.get('seed_bundle', {}).get('path', ''))
                  and report.get('seed_bundle', {}).get('matched_frozen_prompt') is True
                  and report.get('seed_bundle', {}).get('problems') == [])
    good &= (report.get('asset_index', {}).get('sha256') == candidate['asset_index_sha256']
             and report.get('asset_index', {}).get('path') == str(candidate['asset_index_path'])
             and report.get('seed_bundle', {}).get('path') == str(repo / original.get('seed_bundle', {}).get('path', ''))
             and report.get('seed_bundle', {}).get('matched_frozen_prompt') is True
             and report.get('seed_bundle', {}).get('problems') == [])
    evidence_bad = []
    for section_name, prefix_map in (
        ('positive_cnl_json', ('compile-json', 'compile-cnl')),
        ('typed_oracle', ('compile-json',)),
        ('cnl_negative', ('compile-cnl',)),
        ('bundle_tampering', ('assemble',)),
    ):
        for row in sections.get(section_name, {}).get('records', []):
            evidence_dir = row.get('response_evidence_dir')
            if not evidence_dir:
                evidence_bad.append(f'{section_name}:{row.get("id")}:no_dir')
                continue
            for prefix in prefix_map:
                if not verify_saved_exchange(audit, f'frozen_003b.evidence.{section_name}.{row.get("id")}.{prefix}',
                                             Path(evidence_dir), prefix):
                    evidence_bad.append(f'{section_name}:{row.get("id")}:{prefix}')
    audit.require('frozen_003b.saved_http_evidence', not evidence_bad, ','.join(evidence_bad[:12]))
    good &= not evidence_bad
    return good


def verify_frozen_attribution(audit: Audit, path: Path | None, repo: Path, acc: Path,
                              candidate: dict[str, Any]) -> bool:
    if path is None:
        return False
    if not path.is_file():
        audit.add('frozen_003b.attribution_receipt', 'UNRUN',
                  'optional attribution sidecar has not been supplied')
        return False
    attribution = audit.json('frozen_003b.attribution_receipt', path)
    if not isinstance(attribution, dict):
        return False
    report_path = acc / 'frozen-003b.json'
    report = audit.json('frozen_003b.report', report_path)
    if not isinstance(report, dict):
        return False
    audit.require('frozen_003b.attribution_version', attribution.get('version') == 'som.frozen-candidate-attribution.v1')
    audit.require('frozen_003b.attribution_cwd', attribution.get('cwd') == str(repo))
    expected_argv = expected_frozen_argv(repo, candidate, acc)
    audit.require('frozen_003b.exact_argv', bool(expected_argv) and exact_argv_matches(attribution.get('argv'), expected_argv),
                  str(attribution.get('argv')))
    audit.require('frozen_003b.expected_exit_2_with_exact_nine_unrun', frozen_exit_ok(attribution.get('exit_code'), report))

    def bound(name: str, expected_path: Path) -> bool:
        item = attribution.get(name, {})
        expected_path = expected_path.resolve()
        ok = (item.get('path') == str(expected_path) and expected_path.is_file()
              and item.get('sha256') == file_sha(expected_path))
        audit.require(f'frozen_003b.{name}_binding', ok, str(item.get('path')))
        return ok

    bound_paths = {
        'report': report_path,
        'wrapper': Path('/private/tmp/som-frozen-attribution003.py'),
        'candidate_binary': candidate['binary_path'],
        'candidate_source': candidate['source_json_path'],
        'candidate_build': candidate['build_path'],
        'candidate_runtime': candidate['runtime_path'],
        'acceptance_tools': candidate['source_json_path'].parent / 'acceptance-tools.json',
        'qa_harness': repo / 'som-rs/scripts/compiler_acceptance/qa_harness.py',
        'seed_bundle': repo / json.loads((acc / 'frozen-003.json').read_text())['seed_bundle']['path'],
        'asset_index': candidate['asset_index_path'],
    }
    bindings = [bound(name, value) for name, value in bound_paths.items()]
    source_doc = candidate['source_json']
    tools_doc = audit.json('frozen_003b.acceptance_tools_receipt', bound_paths['acceptance_tools']) or {}
    source_map = source_doc.get('files', {})
    tool_map = tools_doc.get('files', {})
    audit.require('frozen_003b.source_73_still_bound', len(source_map) == 73
                  and all((repo / rel).is_file() and file_sha(repo / rel) == digest
                          for rel, digest in source_map.items()))
    audit.require('frozen_003b.tools_57_still_bound', len(tool_map) == 57
                  and all((repo / rel).is_file() and file_sha(repo / rel) == digest
                          for rel, digest in tool_map.items()))
    audit.require('frozen_003b.before_after_files_checked', attribution.get('source_and_tools_checked_before_after') is True)
    process_before, process_after = attribution.get('process_before'), attribution.get('process_after')
    proc_ok = (process_before == process_after and valid_process_identity(process_before, candidate))
    audit.require('frozen_003b.candidate_process_identity', proc_ok)
    expected_health = candidate['runtime'].get('health')
    health_ok = (attribution.get('health_before_status') == 200 and attribution.get('health_after_status') == 200
                 and attribution.get('health_before') == expected_health
                 and attribution.get('health_after') == expected_health
                 and attribution.get('identity_stable') is True)
    audit.require('frozen_003b.full_health_before_after', health_ok)
    for field in ('health_before_raw_sha256', 'health_after_raw_sha256'):
        value = attribution.get(field, '')
        audit.require('frozen_003b.' + field, isinstance(value, str) and len(value) == 64
                      and all(ch in '0123456789abcdef' for ch in value))
    report_hash_ok = (attribution.get('report', {}).get('path') == str(report_path.resolve())
                      and attribution.get('report', {}).get('sha256') == file_sha(report_path))
    audit.require('frozen_003b.report_sha256', report_hash_ok)
    # stdout proves the expected CLI exit summary, while the sidecar preserves
    # the exact process argv and return code.
    stdout_path = acc / 'frozen-003b.stdout'
    stderr_path = acc / 'frozen-003b.stderr'
    stdout_ok = False
    if stdout_path.is_file() and stderr_path.is_file():
        try:
            output = json.loads(stdout_path.read_text(encoding='utf-8'))
            stdout_ok = (output == {'completed': False, 'out': str(report_path), 'unrun': 9}
                         and stderr_path.stat().st_size == 0)
        except Exception:
            stdout_ok = False
    audit.require('frozen_003b.cli_exit_evidence', stdout_ok)
    report_ok = verify_frozen_003b_report(audit, report, repo, candidate, report_path)
    return all(bindings) and proc_ok and health_ok and report_hash_ok and stdout_ok and report_ok


def verify_frozen(audit: Audit, repo: Path, acc: Path, candidate: dict[str, Any],
                  runtime: dict[str, Any] | None, gaps: dict[str, Any] | None,
                  diagnostics_path: Path, public_path: Path, legacy_path: Path) -> dict[str, Any] | None:
    path = acc / 'frozen-003.json'
    fr = audit.json('frozen.receipt', path)
    if fr is None:
        return None
    verify_freeze_embedded(audit, 'frozen', fr.get('freeze'))
    audit.require('frozen.asset_index_binding', fr.get('asset_index', {}).get('sha256') == candidate['asset_index_sha256'])
    audit.require('frozen.seed_is_independent', fr.get('seed_bundle', {}).get('matched_frozen_prompt') is True
                  and fr.get('seed_bundle', {}).get('problems') == [])
    if fr.get('candidate_binary_sha256') is None:
        audit.add('frozen.binary_attribution', 'UNRUN',
                  'receipt records HTTP mode and asset index but does not bind the responding process binary')
    else:
        audit.require('frozen.binary_attribution', fr.get('candidate_binary_sha256') == candidate['binary_sha256'])
    sections = fr.get('sections', {})
    section_fixture = {
        'positive_cnl_json': ('som-rs/tests/compiler-v1/cnl-positive-fixtures.json', 119),
        'typed_oracle': ('som-rs/tests/compiler-v1/typed-json-oracle-cases.json', 40),
        'cnl_negative': ('som-rs/tests/compiler-v1/cnl-negative-cases.json', 15),
        'canonical_vectors': ('som-rs/contracts/compiler-v1/canonical-vectors.json', 11),
    }
    for section, (rel, count) in section_fixture.items():
        rows = sections.get(section, {}).get('records', [])
        expected_ids = load_fixture_ids(repo, rel, 'vectors' if section == 'canonical_vectors' else 'cases')
        got_ids = [row.get('id') for row in rows]
        audit.require(f'frozen.{section}.exact_and_passed', sections.get(section, {}).get('passed') is True
                      and sections.get(section, {}).get('count') == count and len(rows) == count
                      and got_ids == expected_ids and all(row.get('passed') is True for row in rows),
                      f'{len(rows)}/{count}, matching ordered fixture IDs={got_ids == expected_ids}')
    contract_cases = json.loads((repo / 'som-rs/contracts/compiler-v1/bundle-cases.json').read_text(encoding='utf-8'))['cases']
    unrun_rows = fr.get('unrun', [])
    unrun_names = [row.get('case') for row in unrun_rows if isinstance(row, dict)]
    audit.require('frozen.exact_nine_unrun', len(unrun_rows) == 9 and set(unrun_names) == set(UNRUN_DISCHARGES)
                  and all(row.get('section') == 'bundle_tampering' for row in unrun_rows))
    bundle = sections.get('bundle_tampering', {})
    bundle_rows = bundle.get('records', [])
    expected_run_ids = {case['id'] for case in contract_cases
                        if case.get('expected') not in ('accept', 'startup refusal')
                        and case['id'] not in UNRUN_DISCHARGES}
    got_bundle_ids = [row.get('id') for row in bundle_rows]
    audit.require('frozen.bundle_14_cases', bundle.get('passed') is True and bundle.get('count') == 14
                  and len(bundle_rows) == 14 and set(got_bundle_ids) == expected_run_ids
                  and all(row.get('passed') is True for row in bundle_rows))
    audit.require('frozen.completed_remains_false', fr.get('completed') is False,
                  'nine cases are deferred to dedicated receipts; frozen bundle section is not a full standalone pass')
    response_bad = []
    for section in ('positive_cnl_json', 'typed_oracle', 'cnl_negative', 'bundle_tampering'):
        for row in sections.get(section, {}).get('records', []):
            evidence = row.get('response_evidence_dir')
            if not evidence:
                response_bad.append(f'{section}:{row.get("id")}:no_evidence_dir')
                continue
            d = Path(evidence)
            prefixes = {'positive_cnl_json': ('compile-json', 'compile-cnl'),
                        'typed_oracle': ('compile-json',), 'cnl_negative': ('compile-cnl',),
                        'bundle_tampering': ('assemble',)}[section]
            if any(not verify_saved_exchange(audit, f'frozen.evidence.{section}.{row.get("id")}.{prefix}', d, prefix)
                   for prefix in prefixes):
                response_bad.append(f'{section}:{row.get("id")}')
    audit.require('frozen.all_saved_http_evidence', not response_bad, ','.join(response_bad[:12]))
    # Each deferred scope has a separate frozen gate. A missing receipt remains UNRUN.
    rmap = {row.get('id'): row for row in (runtime or {}).get('checks', [])}
    gmap = {row.get('id'): row for row in (gaps or {}).get('checks', [])}
    for case, mapping in UNRUN_DISCHARGES.items():
        good = True
        details = []
        for group, ids in mapping.items():
            if group == 'runtime':
                good &= runtime is not None and all(rmap.get(i, {}).get('passed') is True for i in ids)
                details.extend('runtime/' + i for i in ids)
            elif group == 'gaps':
                good &= gaps is not None and all(gmap.get(i, {}).get('passed') is True for i in ids)
                details.extend('gaps/' + i for i in ids)
            elif group == 'public':
                good &= public_path.is_file()
                details.extend('public/' + i for i in ids)
            elif group == 'legacy':
                good &= legacy_path.is_file()
                details.extend('legacy/' + i for i in ids)
        # These three spans need the whole downstream receipt, not just its
        # existence. Resolve them after all dedicated checks have run.
        if case in {'equivalent_cnl_json', 'all_versions', 'checked_variant'}:
            audit.add('discharge.' + case, 'UNRUN', 'dedicated receipt has not yet been fully validated')
        elif good:
            audit.require('discharge.' + case, True, ' and '.join(details))
        elif any(group == 'runtime' and runtime is None or group == 'gaps' and gaps is None
                 or group == 'public' and not public_path.is_file()
                 or group == 'legacy' and not legacy_path.is_file()
                 for group in mapping):
            audit.add('discharge.' + case, 'UNRUN', 'required dedicated receipt is missing')
        else:
            audit.require('discharge.' + case, False, 'required dedicated check failed: ' + ' and '.join(details))
    return fr


def verify_diagnostics(audit: Audit, path: Path, candidate: dict[str, Any]) -> dict[str, Any] | None:
    d = audit.json('diagnostics.receipt', path)
    if d is None:
        return None
    rows = d.get('results', [])
    ids = [row.get('id') for row in rows if isinstance(row, dict)]
    audit.require('diagnostics.101_complete', d.get('status') == 'pass' and d.get('complete') is True
                  and d.get('passed') == 101 and d.get('failed') == 0 and d.get('unrun_ids') == []
                  and len(rows) == 101 and len(set(ids)) == 101 and set(ids) == set(d.get('required_ids', []))
                  and all(row.get('passed') is True for row in rows))
    verify_receipt_binding(audit, 'diagnostics', d, candidate)
    audit.require('diagnostics.package_binding', d.get('package_manifest_sha256') == candidate['manifest_sha256'])
    verify_freeze_embedded(audit, 'diagnostics', d.get('freeze_receipts'))
    input_mismatches = []
    for raw_path, digest in d.get('input_hashes', {}).items():
        p = Path(raw_path)
        if not p.is_file() or file_sha(p) != digest:
            input_mismatches.append(raw_path)
    audit.require('diagnostics.inputs_unchanged', bool(d.get('input_hashes')) and not input_mismatches,
                  ','.join(input_mismatches[:8]))
    evidence_bad = []
    for row in rows:
        ident = row.get('id')
        request = path.parent / f'{ident}.request.raw'
        response = path.parent / f'{ident}.response.raw'
        if not request.is_file() or not response.is_file():
            evidence_bad.append(f'{ident}:missing')
            continue
        if (file_sha(request) != row.get('request_sha256')
                or file_sha(response) != row.get('response_sha256')
                or request.stat().st_size != row.get('request_bytes')):
            evidence_bad.append(f'{ident}:hash_or_size')
        if type(row.get('http_status')) is not int or row.get('passed') is not True:
            evidence_bad.append(f'{ident}:status_or_expected_result')
    audit.require('diagnostics.all_raw_http_evidence', not evidence_bad,
                  ','.join(evidence_bad[:12]))
    return d


def verify_performance(audit: Audit, path: Path, candidate: dict[str, Any]) -> dict[str, Any] | None:
    d = audit.json('performance.receipt', path)
    if d is None:
        return None
    who = d.get('candidate', {})
    bindings = (who.get('candidate_binary_sha256') == candidate['binary_sha256']
                and who.get('source_archive_sha256') == candidate['source_archive_sha256']
                and who.get('source_json_sha256') == candidate['source_json_sha256']
                and who.get('asset_index_sha256') == candidate['asset_index_sha256']
                and who.get('asset_manifest_sha256') == candidate['manifest_sha256'])
    audit.require('performance.candidate_assets_binding', bindings)
    before, after = d.get('identity_before'), d.get('identity_after')
    identity_ok = (d.get('identity_stable') is True and isinstance(before, dict) and isinstance(after, dict)
                   and before == after and before.get('binary_sha256') == candidate['binary_sha256']
                   and before.get('source_sha256') == candidate['source_json_sha256'])
    audit.require('performance.process_identity_before_after', identity_ok)
    observations = d.get('observations', [])
    expected_ops = {'compile-cnl', 'compile-json', 'assemble-saved-bundle', 'generate'}
    keys = [(row.get('case'), row.get('repeat'), row.get('operation')) for row in observations]
    expected_keys = {(f'v{v}', f'repeat-{repeat:02d}', op)
                     for v in range(1, 8) for repeat in range(1, 4) for op in expected_ops}
    exact_rows = len(observations) == 84 and set(keys) == expected_keys and len(set(keys)) == 84
    complete = exact_rows and d.get('observation_count') == 84 and d.get('all_requests_completed') is True
    audit.require('performance.84_observations', complete, f'{len(observations)}/84')
    http_ok = all(row.get('http_status') == 200 for row in observations)
    audit.require('performance.http_200', len(observations) == 84 and http_ok)
    timing_ok = all(type(row.get('elapsed_ms')) in (int, float) and row['elapsed_ms'] >= 0
                    and type(row.get('request_bytes')) is int and type(row.get('response_bytes')) is int
                    for row in observations)
    audit.require('performance.complete_response_timing_bytes', timing_ok and len(observations) == 84)
    rss_ok = (d.get('rss_samples_complete') is True and len(observations) == 84
              and all(row.get('rss_after_response', {}).get('available') is True
                      and type(row.get('rss_after_response', {}).get('rss_kib')) is int
                      for row in observations))
    audit.require('performance.rss_sampled_not_peak', rss_ok,
                  str(d.get('rss_scope', '')))
    raw_bad = []
    for row in observations:
        for path_field, sha_field, size_field in (
            ('request_path', 'request_sha256', 'request_bytes'),
            ('response_path', 'response_sha256', 'response_bytes'),
        ):
            p = Path(row.get(path_field, ''))
            if not p.is_file() or file_sha(p) != row.get(sha_field) or p.stat().st_size != row.get(size_field):
                raw_bad.append(f"{row.get('case')}/{row.get('repeat')}/{row.get('operation')}/{path_field}")
        header_path = Path(row.get('response_headers_path', ''))
        if not header_path.is_file():
            raw_bad.append(f"{row.get('case')}/{row.get('repeat')}/{row.get('operation')}/headers")
    audit.require('performance.raw_request_response_evidence', not raw_bad, ','.join(raw_bad[:12]))
    comparisons = d.get('comparisons', [])
    cmp_keys = [(row.get('case'), row.get('repeat'), row.get('check')) for row in comparisons]
    expected_checks = {'cnl_json_complete_bundle', 'repeat_bundle_stability',
                       'repeat_assembled_files_stability', 'assembled_generate_full_files'}
    wanted_cmp = {(f'v{v}', repeat, check) for v in range(1, 8) for repeat in range(1, 4) for check in expected_checks}
    cmp_ok = (len(comparisons) == 84 and len(set(cmp_keys)) == 84 and set(cmp_keys) == wanted_cmp
              and all(row.get('equal') is True and not row.get('json_bundle_validation_problems') for row in comparisons))
    audit.require('performance.typed_bundle_and_full_file_comparisons', cmp_ok)
    audit.require('performance.no_threshold_gate', d.get('performance_thresholds') in (None, {})
                  and 'no latency or memory thresholds' in d.get('purpose', '').lower())
    return d


def expected_public_inventory(repo: Path) -> list[tuple[str, str]]:
    fixture = json.loads((repo / 'som-rs/tests/compiler-v1/cnl-positive-fixtures.json').read_text(encoding='utf-8'))
    rows = [(row['id'], row['expected_prompt']['version']) for row in fixture['cases']]
    example_dir = repo / 'som-rs/contracts/compiler-v1/examples'
    index = json.loads((example_dir / 'index.json').read_text(encoding='utf-8'))
    rows += [('PUBLIC_' + row['id'], json.loads((example_dir / row['prompt']).read_text(encoding='utf-8'))['version']) for row in index['cases']]
    return rows


def verify_public(audit: Audit, repo: Path, path: Path, candidate: dict[str, Any],
                 runtime_path: Path) -> dict[str, Any] | None:
    d = audit.json('public.receipt', path)
    if d is None:
        return None
    rows = d.get('records', [])
    inventory = expected_public_inventory(repo)
    got_ids = [row.get('id') for row in rows]
    audit.require('public.127_exact_inventory', len(rows) == 127 and got_ids == [row[0] for row in inventory]
                  and d.get('required') == 127 and d.get('complete') is True and d.get('passed') == 127
                  and all(row.get('passed') is True for row in rows))
    audit.require('public.candidate_binding', d.get('candidate_binary_sha256') == candidate['binary_sha256']
                  and d.get('candidate_source_sha256') == candidate['source_archive_sha256']
                  and d.get('candidate_source_json_sha256') == candidate['source_json_sha256']
                  and d.get('candidate_asset_manifest_sha256') == candidate['manifest_sha256']
                  and d.get('asset_index_sha256') == candidate['asset_index_sha256'])
    audit.require('public.build_runtime_receipt_hashes', d.get('candidate_build_receipt_sha256') == file_sha(candidate['build_path'])
                  and d.get('candidate_runtime_receipt_sha256') == file_sha(candidate['runtime_path'])
                  and d.get('runtime_health_binding', {}).get('runtime_receipt_sha256') == file_sha(runtime_path) if runtime_path.is_file() else False)
    hb = d.get('runtime_health_binding', {})
    before, after = hb.get('process_before', {}), hb.get('process_after', {})
    same_process = (isinstance(before, dict) and before == after and before.get('binary_sha256') == candidate['binary_sha256']
                    and before.get('source_sha256') == candidate['source_json_sha256']
                    and type(before.get('pid')) is int and type(before.get('uid')) is int
                    and isinstance(before.get('argv'), list) and bool(before.get('argv'))
                    and bool(before.get('lsof_listener_fields')) and bool(before.get('lsof_executable_fields')))
    audit.require('public.process_and_health_binding', hb.get('health_before_matches_runtime') is True
                  and hb.get('health_after_matches_runtime') is True and hb.get('stable_health') is True and same_process)
    audit.require('public.freeze_chain', d.get('freeze_verification', {}).get('latest_hashes_match') is True
                  and d.get('freeze_verification', {}).get('latest_bound_file_count') == 42)
    versions = {version for _, version in inventory}
    audit.require('public.all_seven_versions', versions == {f'som.prompt.v{i}' for i in range(1, 8)})
    audit.require('public.independent_dsl_and_compile_pairs', all(row.get('independent_dsl_equal') is True for row in rows))
    strict = [row for row, (_, version) in zip(rows, inventory) if version in {'som.prompt.v6', 'som.prompt.v7'}]
    audit.require('public.strict_quality_scope', bool(strict) and all(row.get('quality', {}).get('passed') is True for row in strict),
                  f'{sum(row.get("quality", {}).get("passed") is True for row in strict)}/{len(strict)} v6/v7 products')
    exceptions = [row for row in rows if row.get('complete_file_parity') is None]
    audit.require('public.reference_failure_exceptions_preserved', len(exceptions) == 2
                  and {row.get('id') for row in exceptions} == {'REF_BRANCH_EXPORT_SAME_TYPE', 'PUBLIC_v7-branch-null'}
                  and all(row.get('complete_normalized_ast_equal') is True
                          and row.get('separate_live_behavior_required') is True for row in exceptions))
    audit.require('public.nonexception_full_file_parity', public_nonexception_parity_ok(rows))
    evidence_bad = []
    for index, row in enumerate(rows):
        directory = path.parent / f'{index:03d}'
        for prefix in ('compile-json', 'compile-cnl', 'assemble'):
            if not verify_saved_exchange(audit, f'public.raw.{index:03d}.{prefix}', directory, prefix, 200):
                evidence_bad.append(f'{index:03d}:{prefix}')
    audit.require('public.all_raw_http_exchange_evidence', not evidence_bad, ','.join(evidence_bad[:12]))
    cleanup_bad = []
    for index in range(len(rows)):
        cleanup_path = path.parent / f'{index:03d}/quality-cache-cleanup.json'
        if not cleanup_path.is_file():
            cleanup_bad.append(f'{index:03d}:missing')
            continue
        c = json.loads(cleanup_path.read_text(encoding='utf-8'))
        if c.get('passed') is not True or c.get('bytes_after') != 0:
            cleanup_bad.append(f'{index:03d}:failed')
    audit.require('public.127_cleanup_receipts', not cleanup_bad, ','.join(cleanup_bad[:12]))
    return d


def json_map_file(path: Path) -> dict[str, str] | None:
    """Read a saved filename-to-source map, refusing malformed structures."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in value.items()):
            return None
        return value
    except Exception:
        return None


def replay_raw_span_proof(raw_files: dict[str, str], corrected_files: dict[str, str],
                          proof: dict[str, Any]) -> tuple[bool, str]:
    """Independently verify exact-span edits and full-module AST preservation.

    The proof is data only. This function does not import the amendment helper.
    It checks the source bytes, hashes, selected AST nodes, outside bytes, and
    that the corrected module differs only by the admitted assignment IfExp.
    """
    try:
        if (proof.get('version') != 'som.public-reference-span-prepass.v2'
                or proof.get('candidate_input') is not False or proof.get('ruff_location_used') is not False):
            return False, 'candidate/diagnostic input flag is not false'
        entries = proof.get('files')
        if not isinstance(entries, dict) or set(entries) != set(raw_files) or set(corrected_files) != set(raw_files):
            return False, 'proof file inventory differs'
        edit_total = 0
        for name, raw_text in raw_files.items():
            old = raw_text.encode('utf-8')
            new_text = corrected_files[name]
            new = new_text.encode('utf-8')
            entry = entries[name]
            if hashlib.sha256(old).hexdigest() != entry.get('raw_sha256'):
                return False, f'{name}: raw digest differs'
            if hashlib.sha256(new).hexdigest() != entry.get('corrected_sha256'):
                return False, f'{name}: corrected digest differs'
            edits = entry.get('edits')
            if not isinstance(edits, list):
                return False, f'{name}: missing edit list'
            if not name.endswith('.py'):
                if edits or old != new or entry.get('outside_bytes_equal') is not True:
                    return False, f'{name}: non-Python content changed'
                continue
            raw_tree = ast.parse(raw_text, type_comments=True)
            corrected_tree = ast.parse(new_text, type_comments=True)
            selected: dict[tuple[int, int, int, int], ast.If] = {}
            all_if = { (n.lineno, n.col_offset, n.end_lineno, n.end_col_offset): n
                       for n in ast.walk(raw_tree) if isinstance(n, ast.If) }
            previous_end = 0
            expected_bytes = old
            for edit in edits:
                if not isinstance(edit, dict):
                    return False, f'{name}: malformed edit'
                span = edit.get('raw_ast_span')
                if not isinstance(span, list) or len(span) != 4 or any(type(x) is not int for x in span):
                    return False, f'{name}: malformed AST span'
                key = tuple(span)
                node = all_if.get(key)
                if node is None or key in selected or len(node.body) != 1 or len(node.orelse) != 1:
                    return False, f'{name}: span does not select one unique If node'
                left, right = node.body[0], node.orelse[0]
                if (not isinstance(left, ast.Assign) or not isinstance(right, ast.Assign)
                        or len(left.targets) != 1 or len(right.targets) != 1
                        or not isinstance(left.targets[0], ast.Name) or not isinstance(right.targets[0], ast.Name)
                        or left.targets[0].id != right.targets[0].id
                        or left.type_comment is not None or right.type_comment is not None):
                    return False, f'{name}: edit is outside the admitted single-name assignment shape'
                if entry.get('type_comments_parsed') is not True:
                    return False, f'{name}: AST was not parsed with type comments'
                rows = old.splitlines(keepends=True)
                offsets = [0]
                for row_bytes in rows:
                    offsets.append(offsets[-1] + len(row_bytes))
                start = offsets[node.lineno - 1] + node.col_offset
                end = offsets[node.end_lineno - 1] + node.end_col_offset
                if (start != edit.get('start_byte') or end != edit.get('end_byte') or start < previous_end
                        or not 0 <= start <= end <= len(old)):
                    return False, f'{name}: byte span differs or overlaps'
                original_span = old[start:end]
                replacement = edit.get('replacement')
                if not isinstance(replacement, str):
                    return False, f'{name}: replacement is missing'
                replacement_bytes = replacement.encode('utf-8')
                if (hashlib.sha256(original_span).hexdigest() != edit.get('original_span_sha256')
                        or hashlib.sha256(replacement_bytes).hexdigest() != edit.get('replacement_sha256')):
                    return False, f'{name}: span hash differs'
                if original_span.count(b'\n') != replacement_bytes.count(b'\n'):
                    return False, f'{name}: edit changes later source line coordinates'
                if (not original_span.startswith((b'if ', b'if\t', b'if('))
                        or b'\\\n' in original_span or b'\\\r\n' in original_span):
                    return False, f'{name}: raw statement token is not safely rewritable'
                if any(tok.type == tokenize.COMMENT
                       for tok in tokenize.generate_tokens(io.StringIO(raw_text).readline)
                       if node.lineno <= tok.start[0] <= node.end_lineno):
                    return False, f'{name}: rewritten span contains a comment'
                expected_expression = ast.unparse(ast.IfExp(test=node.test, body=left.value, orelse=right.value))
                expected_replacement = f'{left.targets[0].id} = ({expected_expression})'
                line_padding = original_span.count(b'\n') - expected_replacement.encode('utf-8').count(b'\n')
                if line_padding < 0:
                    return False, f'{name}: replacement would shift later line coordinates'
                newline = '\r\n' if b'\r\n' in original_span else '\n'
                expected_replacement = expected_replacement[:-1] + newline * line_padding + ')'
                if replacement != expected_replacement:
                    return False, f'{name}: replacement differs from the frozen IfExp-only unparse rule'
                expected_bytes = expected_bytes[:start] + replacement_bytes + expected_bytes[end:]
                selected[key] = node
                previous_end = end
            if expected_bytes != new:
                return False, f'{name}: changed bytes exist outside recorded spans'

            class ReplaceAdmitted(ast.NodeTransformer):
                def visit_If(self, node: ast.If) -> ast.AST:
                    key = (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
                    if key in selected:
                        a, b = node.body[0], node.orelse[0]
                        return ast.copy_location(ast.Assign(
                            targets=copy.deepcopy(a.targets),
                            value=ast.IfExp(test=copy.deepcopy(node.test), body=copy.deepcopy(a.value),
                                            orelse=copy.deepcopy(b.value))), node)
                    return self.generic_visit(node)

            expected_tree = ReplaceAdmitted().visit(copy.deepcopy(raw_tree))
            if ast.dump(expected_tree, include_attributes=False) != ast.dump(corrected_tree, include_attributes=False):
                return False, f'{name}: whole-module AST differs outside recorded rewrite'
            ast_digest = hashlib.sha256(ast.dump(corrected_tree, include_attributes=False).encode()).hexdigest()
            if (entry.get('whole_ast_equal') is not True or entry.get('outside_bytes_equal') is not True
                    or entry.get('whole_ast_sha256') != ast_digest):
                return False, f'{name}: proof claims are inconsistent'
            edit_total += len(edits)
        if edit_total != proof.get('edit_count') or edit_total < 1:
            return False, 'edit count differs or no rewrite was proved'
        binding = proof.get('original_diagnostic_binding')
        if not isinstance(binding, dict) or binding.get('raw_selection_independent_of_diagnostic_location') is not True:
            return False, 'original diagnostic binding is missing or weak'
        return True, 'raw spans, outside bytes and full AST independently verified'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def verify_original_public_preserved(audit: Audit, repo: Path, candidate: dict[str, Any],
                                     acc: Path) -> dict[str, Any] | None:
    path = acc / 'public-products-003/result.json'
    old = audit.json('public.original_003_receipt', path)
    if old is None:
        return None
    expected = expected_public_inventory(repo)
    rows = old.get('records', [])
    ids = [row.get('id') for row in rows]
    failures = [row for row in rows if row.get('passed') is not True]
    failure_rows = {row.get('id'): row for row in failures}
    fail_ok = (set(failure_rows) == PUBLIC_REFERENCE_FAILURES
               and all(row.get('reference_formatter_status') == 'failed'
                       and row.get('complete_file_parity') is None
                       and row.get('complete_normalized_ast_equal') is False
                       for row in failures))
    audit.require('public.original_003_preserved_failed', len(rows) == 127 and ids == [r[0] for r in expected]
                  and old.get('required') == 127 and old.get('passed') == 125
                  and old.get('complete') is False and len(failures) == 2 and fail_ok
                  and file_sha(path) == '215653d30f2430a809b5c7d14e852d563b1328e68b329db61b42be8bd96fbb99')
    audit.require('public.original_003_candidate_binding',
                  old.get('candidate_binary_sha256') == candidate['binary_sha256']
                  and old.get('candidate_source_json_sha256') == candidate['source_json_sha256']
                  and old.get('candidate_source_sha256') == candidate['source_archive_sha256'])
    return old


def corrected_failure_retained(row: dict[str, Any], old_row: dict[str, Any] | None) -> bool:
    return (isinstance(old_row, dict)
            and row.get('reference_formatter_status') == 'failed'
            and row.get('complete_file_parity') is None
            and row.get('complete_normalized_ast_equal') is False
            and row.get('reference_formatter_error') == old_row.get('reference_formatter_error'))


def verify_formatter_receipt(audit: Audit, repo: Path, acc: Path, candidate: dict[str, Any],
                             manifest: dict[str, Any] | None, path: Path | None,
                             expected_sha256: str | None) -> bool:
    if path is None or not path.is_file() or not expected_sha256 or file_sha(path) != expected_sha256:
        audit.require('public.formatter_002_receipt_binding', False, 'missing receipt or exact hash pin')
        return False
    doc = audit.json('public.formatter_002_receipt', path)
    if not isinstance(doc, dict):
        return False
    expected_amendment_sha = file_sha(Path(manifest['_path'])) if manifest and manifest.get('_path') else None
    old = audit_json(acc / 'public-products-003/result.json') or {}
    old_rows = {row.get('id'): row for row in old.get('records', [])}
    manifest_failures = manifest.get('original_failures', {}) if manifest else {}
    rows = {row.get('id'): row for row in doc.get('records', [])}
    formatter_dependency = manifest.get('dependencies', {}).get('formatter', {}) if manifest else {}
    formatter_path = Path(formatter_dependency.get('path', ''))
    scope_ok = (doc.get('version') == 'som.public-reference-formatter-qualification.v1'
                and doc.get('complete') is True
                and doc.get('original_formatter_failure_count') == 2
                and doc.get('corrected_reference_count') == 2
                and doc.get('amendment_manifest_sha256') == expected_amendment_sha
                and formatter_path.is_file() and file_sha(formatter_path) == formatter_dependency.get('sha256')
                and doc.get('formatter_sha256') == formatter_dependency.get('sha256')
                and set(rows) == PUBLIC_REFERENCE_FAILURES and len(doc.get('records', [])) == 2)
    problems = []
    if scope_ok:
        for case_id, row in rows.items():
            binding = manifest_failures.get(case_id, {})
            raw_path = Path(binding.get('raw_files', {}).get('path', ''))
            response_path = Path(binding.get('assemble_response', {}).get('path', ''))
            case_dir = path.parent / case_id
            raw = json_map_file(raw_path) if raw_path.is_file() else None
            corrected = json_map_file(case_dir / 'corrected-raw-files.json')
            formatted = json_map_file(case_dir / 'corrected-formatted-files.json')
            proof_path = case_dir / 'exact-span-proof.json'
            proof = audit_json(proof_path) if proof_path.is_file() else None
            saved = audit_json(response_path) if response_path.is_file() else None
            original_row = old_rows.get(case_id)
            if (not corrected_failure_retained(row, original_row)
                    or row.get('passed') is not True
                    or row.get('corrected_reference_full_file_parity') is not True
                    or row.get('original_reference_check_passed') is not False
                    or row.get('strict_quality_passed') is not True
                    or audit_json(case_dir / 'result.json') != row
                    or raw is None or corrected is None or formatted is None or not isinstance(proof, dict)
                    or not response_path.is_file()
                    or file_sha(raw_path) != binding.get('raw_files', {}).get('sha256')
                    or file_sha(response_path) != binding.get('assemble_response', {}).get('sha256')
                    or not isinstance(saved, dict) or saved.get('http_status') != 200
                    or not isinstance(saved.get('body', {}).get('files'), dict)
                    or formatted != saved.get('body', {}).get('files')):
                problems.append(case_id + ': receipt, bindings, or saved product differs')
                continue
            good, detail = replay_raw_span_proof(raw, corrected, proof)
            if not good:
                problems.append(case_id + ': ' + detail)
                continue
            binding_proof = proof.get('original_diagnostic_binding', {})
            try:
                filename, sep, detail_json = original_row.get('reference_formatter_error', {}).get('detail', '').partition(': ')
                diagnostic = json.loads(detail_json)[0]
            except Exception:
                problems.append(case_id + ': original diagnostic parse failed')
                continue
            proof_entry = proof.get('files', {}).get(filename, {})
            spans = [edit.get('raw_ast_span') for edit in proof_entry.get('edits', [])]
            if (binding_proof.get('file') != filename or not sep
                    or binding_proof.get('diagnostic') != diagnostic
                    or binding_proof.get('raw_ast_span') not in spans
                    or binding_proof.get('diagnostic_coordinate_space') != 'original formatter output; never raw source'
                    or binding_proof.get('raw_selection_independent_of_diagnostic_location') is not True):
                problems.append(case_id + ': exact original diagnostic is not bound to raw proof')
                continue
            actual = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in saved['body']['files'].items()}
            focused_binding = row.get('focused_behavior_binding', {})
            focused_doc = audit_json(Path(manifest.get('focused_summary', {}).get('path', '')))
            focused_row = next((r for r in focused_doc.get('cases', []) if r.get('id') == case_id), {}) if focused_doc else {}
            focused_hashes = focused_row.get('focused_behavior', {}).get('outcome', {}).get('source_sha256_before')
            if focused_binding.get('passed') is not True or focused_binding.get('full_file_sha256') != actual or actual != focused_hashes:
                problems.append(case_id + ': focused behavior does not bind exact saved product')
    audit.require('public.formatter_002_receipt_binding', scope_ok and not problems,
                  ', '.join(problems))
    return scope_ok and not problems


def verify_amendment_v2(audit: Audit, repo: Path, manifest_path: Path | None,
                        installed_path: Path | None, archive_path: Path | None,
                        expected_hashes: dict[str, str], candidate: dict[str, Any]) -> dict[str, Any] | None:
    if not manifest_path or not installed_path or not archive_path:
        audit.require('public.amendment_v2_files_present', False, 'v2 amendment evidence paths are required')
        return None
    manifest = audit.json('public.amendment_v2_manifest', manifest_path)
    installed = audit.json('public.amendment_v2_installed', installed_path)
    if manifest is None or installed is None or not archive_path.is_file():
        audit.require('public.amendment_v2_files_present', False)
        return None
    manifest['_path'] = str(manifest_path.resolve())
    audit.require('public.amendment_v2_hash_pins',
                  all(expected_hashes.get(k) and file_sha(p) == expected_hashes[k]
                      for k, p in (('manifest', manifest_path), ('installed', installed_path), ('archive', archive_path))),
                  json.dumps({k: file_sha(p) if p.is_file() else None for k, p in (
                      ('manifest', manifest_path), ('installed', installed_path), ('archive', archive_path))}, sort_keys=True))
    installed_manifest = installed.get('manifest', {})
    installed_ok = (installed_manifest.get('path') == str(manifest_path.resolve())
                    and installed_manifest.get('sha256') == file_sha(manifest_path)
                    and installed.get('archive_sha256') == file_sha(archive_path)
                    and installed.get('production_changed') is False
                    and installed.get('original_qa57_changed') is False
                    and installed.get('original_public_failure_preserved') is True
                    and installed.get('candidate_binary_sha256') == candidate['binary_sha256']
                    and installed.get('candidate_source_sha256') == candidate['source_json_sha256'])
    qa_receipt = candidate['binary_path'].parent / 'acceptance-tools.json'
    installed_ok &= installed.get('original_qa57_sha256') == file_sha(qa_receipt)
    audit.require('public.amendment_v2_install_receipt_binding', installed_ok)
    files = manifest.get('additive_files')
    installed_files = installed.get('files')
    if not isinstance(files, dict) or not isinstance(installed_files, dict):
        files, installed_files = {}, {}
    actual_files: dict[str, str] = {}
    normalized_files = {f'som-rs/scripts/compiler_acceptance/{name}': digest for name, digest in files.items()}
    for rel, declared in normalized_files.items():
        target = repo / rel
        actual_files[rel] = file_sha(target) if target.is_file() else ''
    set_match = set(normalized_files) == set(installed_files) and len(files) >= 6
    digest_match = all(actual_files.get(k) == v == installed_files.get(k) for k, v in normalized_files.items())
    audit.require('public.amendment_v2_installed_sources',
                  set_match and digest_match and manifest.get('production_changed') is False
                  and manifest.get('original_qa57_changed') is False,
                  json.dumps({'manifest_files': files, 'installed_files': installed_files, 'actual_files': actual_files}, sort_keys=True))
    archive_items: dict[str, str] = {}
    try:
        with tarfile.open(archive_path, 'r') as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    raise ValueError('archive contains non-file member')
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('archive member has no data')
                archive_items[member.name] = hashlib.sha256(stream.read()).hexdigest()
    except Exception as exc:
        audit.require('public.amendment_v2_archive', False, f'{type(exc).__name__}: {exc}')
    else:
        expected_members = normalized_files
        audit.require('public.amendment_v2_archive', archive_items == expected_members,
                      json.dumps({'expected': expected_members, 'actual': archive_items}, sort_keys=True))
    audit.require('public.amendment_v2_manifest_scope',
                  manifest.get('version') in {'som.public-reference-qa-amendment.v2', 'som.public-reference-qa-amendment.v1'}
                  and manifest.get('production_changed') is False
                  and manifest.get('original_qa57_changed') is False
                  and manifest.get('original_public_run', {}).get('sha256') ==
                      '215653d30f2430a809b5c7d14e852d563b1328e68b329db61b42be8bd96fbb99',
                  str(manifest.get('version')))
    original_entry = manifest.get('original_public_run', {})
    original_path = Path(original_entry.get('path', ''))
    original_doc = audit_json(original_path)
    audit.require('public.amendment_v2_original_run_binding',
                  original_path.is_file() and file_sha(original_path) == original_entry.get('sha256')
                  and isinstance(original_doc, dict) and original_doc.get('passed') == 125
                  and original_doc.get('complete') is False and original_doc.get('required') == 127)
    original_failures = manifest.get('original_failures', {})
    raw_bindings_ok = isinstance(original_failures, dict) and set(original_failures) == PUBLIC_REFERENCE_FAILURES
    if raw_bindings_ok:
        for case_id, binding in original_failures.items():
            row_path = Path(binding.get('result', {}).get('path', ''))
            raw_path = Path(binding.get('raw_files', {}).get('path', ''))
            assemble_path = Path(binding.get('assemble_response', {}).get('path', ''))
            expected_row = next((r for r in original_doc.get('records', []) if r.get('id') == case_id), None) if original_doc else None
            raw_bindings_ok &= (row_path.is_file() and raw_path.is_file() and assemble_path.is_file()
                                and file_sha(row_path) == binding.get('result', {}).get('sha256')
                                and file_sha(raw_path) == binding.get('raw_files', {}).get('sha256')
                                and file_sha(assemble_path) == binding.get('assemble_response', {}).get('sha256')
                                and audit_json(row_path) == expected_row)
    audit.require('public.amendment_v2_original_case_artifacts', bool(raw_bindings_ok))
    candidate_bound = True
    candidate_entries = {
        'candidate_source': ('source_json_sha256', candidate['source_json_sha256']),
        'candidate_build': ('build_receipt_sha256', file_sha(candidate['build_path'])),
        'candidate_runtime': ('candidate_runtime_receipt_sha256', file_sha(candidate['runtime_path'])),
        'candidate_binary': ('candidate_binary_sha256', candidate['binary_sha256']),
        'candidate_archive': ('source_archive_sha256', candidate['source_archive_sha256']),
    }
    for key, (manifest_key, expected) in candidate_entries.items():
        entry = manifest.get(key, {})
        bound = Path(entry.get('path', ''))
        candidate_bound &= (bound.is_file() and file_sha(bound) == entry.get('sha256') == expected)
    audit.require('public.amendment_v2_candidate_sources', candidate_bound)
    return manifest


def verify_failed_amendment_001(audit: Audit, acc: Path) -> None:
    root = acc / 'public-reference-amendment-001'
    paths = {'manifest': root / 'manifest.json', 'installed': root / 'installed.json',
             'archive': root / 'additive-tools.tar',
             'formatter': acc / 'public-reference-formatter-001/result.json'}
    hashes = {'manifest': HISTORICAL_AMENDMENT_001['manifest'],
              'installed': HISTORICAL_AMENDMENT_001['installed'],
              'archive': HISTORICAL_AMENDMENT_001['archive'],
              'formatter': 'da8c41823426f22bfbd09772279fa15e177e2a2e75cedc060da0314c7a9fd235'}
    ok = all(p.is_file() and file_sha(p) == hashes[k] for k, p in paths.items())
    manifest = audit_json(paths['manifest']) or {}
    installed = audit_json(paths['installed']) or {}
    manifest_files = manifest.get('additive_files', {})
    normalized_expected = {f'som-rs/scripts/compiler_acceptance/{Path(name).name}': sha
                           for name, sha in HISTORICAL_AMENDMENT_001_FILES.items()}
    source_ok = manifest_files == {Path(name).name: sha for name, sha in HISTORICAL_AMENDMENT_001_FILES.items()}
    source_ok &= installed.get('files') == normalized_expected
    archive_files = {}
    try:
        with tarfile.open(paths['archive'], 'r') as archive:
            for member in archive.getmembers():
                stream = archive.extractfile(member)
                if not member.isfile() or stream is None:
                    raise ValueError('unsafe archive member')
                archive_files[member.name] = hashlib.sha256(stream.read()).hexdigest()
    except Exception:
        archive_files = {}
    source_ok &= archive_files == normalized_expected
    formatter = audit_json(paths['formatter']) or {}
    rows = formatter.get('records', [])
    format_failed = (formatter.get('complete') is False
                     and formatter.get('original_formatter_failure_count') == 2
                     and formatter.get('corrected_reference_count') == 0
                     and {r.get('id') for r in rows} == PUBLIC_REFERENCE_FAILURES
                     and all(r.get('passed') is False and r.get('reference_formatter_status') == 'failed'
                             and r.get('complete_file_parity') is None
                             and r.get('complete_normalized_ast_equal') is False
                             and r.get('corrected_reference_full_file_parity') is False for r in rows))
    audit.require('public.historical_amendment_001_failure_preserved', ok and source_ok and format_failed)


def verify_failed_amendment_002_startup(audit: Audit, acc: Path) -> None:
    root = acc / 'public-reference-amendment-002'
    controller = acc / 'controller-003c/result.json'
    stderr = acc / 'controller-003c/public127.stderr'
    manifest_path, installed_path, archive_path = (root / 'manifest.json', root / 'installed.json',
                                                    root / 'additive-tools.tar')
    hashes_ok = all(path.is_file() and file_sha(path) == digest for path, digest in (
        (manifest_path, FAILED_AMENDMENT_002['manifest']),
        (installed_path, FAILED_AMENDMENT_002['installed']),
        (archive_path, FAILED_AMENDMENT_002['archive']),
        (controller, FAILED_AMENDMENT_002['controller']),
        (stderr, FAILED_AMENDMENT_002['stderr'])))
    manifest = audit_json(manifest_path) or {}
    installed = audit_json(installed_path) or {}
    files = manifest.get('additive_files', {})
    installed_files = installed.get('files', {})
    archive_files: dict[str, str] = {}
    try:
        with tarfile.open(archive_path, 'r') as archive:
            for member in archive.getmembers():
                data = archive.extractfile(member)
                if not member.isfile() or data is None:
                    raise ValueError('unsafe archive member')
                archive_files[member.name] = hashlib.sha256(data.read()).hexdigest()
    except Exception:
        archive_files = {}
    expected_files = {f'som-rs/scripts/compiler_acceptance/{name}': digest for name, digest in files.items()}
    install_ok = (len(files) == 6 and installed_files == expected_files == archive_files
                  and installed.get('manifest', {}).get('sha256') == FAILED_AMENDMENT_002['manifest']
                  and installed.get('archive_sha256') == FAILED_AMENDMENT_002['archive']
                  and installed.get('original_public_failure_preserved') is True
                  and files.get('public_products_corrected.py') == '3826a972e88e92a18e5fff9080fa600b2c862ba1cc296e629e9abda7c603e80f')
    controller_doc = audit_json(controller) or {}
    jobs = controller_doc.get('jobs', [])
    job = next((row for row in jobs if row.get('name') == 'public127'), {})
    argv = job.get('argv', [])
    stderr_text = stderr.read_text(encoding='utf-8', errors='replace') if stderr.is_file() else ''
    failed_as_recorded = (controller_doc.get('version') == 'som.acceptance-controller.v1'
                          and controller_doc.get('phase') == 'public_gate_failed'
                          and controller_doc.get('accepted') is False and len(jobs) == 1
                          and job.get('exit_code') == 1
                          and any('public_products_corrected.py' in arg for arg in argv)
                          and any('public-reference-amendment-002/manifest.json' in arg for arg in argv)
                          and 'UnboundLocalError: cannot access local variable' in stderr_text
                          and "where it is not associated with a value" in stderr_text)
    audit.require('public.failed_amendment_002_startup_preserved',
                  hashes_ok and install_ok and failed_as_recorded)


def verify_public_v2(audit: Audit, repo: Path, path: Path, candidate: dict[str, Any],
                     runtime_path: Path, manifest: dict[str, Any] | None,
                     formatter_path: Path | None, formatter_sha256: str | None) -> dict[str, Any] | None:
    d = audit.json('public.v2_receipt', path)
    if d is None:
        return None
    rows = d.get('records', [])
    inventory = expected_public_inventory(repo)
    ids = [row.get('id') for row in rows]
    audit.require('public.v2_exact_127_complete', len(rows) == 127 and ids == [r[0] for r in inventory]
                  and d.get('version') == 'som.compiler-public-products.v2' and d.get('required') == 127
                  and d.get('passed') == 127 and d.get('complete') is True
                  and all(row.get('passed') is True for row in rows))
    audit.require('public.v2_candidate_binding', d.get('candidate_binary_sha256') == candidate['binary_sha256']
                  and d.get('candidate_source_sha256') == candidate['source_archive_sha256']
                  and d.get('candidate_source_json_sha256') == candidate['source_json_sha256']
                  and d.get('candidate_asset_manifest_sha256') == candidate['manifest_sha256']
                  and d.get('asset_index_sha256') == candidate['asset_index_sha256']
                  and d.get('candidate_build_receipt_sha256') == file_sha(candidate['build_path'])
                  and d.get('candidate_runtime_receipt_sha256') == file_sha(candidate['runtime_path']))
    audit.require('public.v2_amendment_binding', manifest is not None
                  and d.get('amendment_manifest_sha256') == (file_sha(Path(manifest.get('_path', ''))) if manifest.get('_path') else None)
                  and d.get('original_public_run_passed') == 125 and d.get('original_public_run_complete') is False)
    hb = d.get('runtime_health_binding', {})
    before, after = hb.get('process_before', {}), hb.get('process_after', {})
    process_ok = (isinstance(before, dict) and before == after
                  and before.get('binary_sha256') == candidate['binary_sha256']
                  and before.get('source_sha256') == candidate['source_json_sha256']
                  and type(before.get('pid')) is int and type(before.get('uid')) is int
                  and isinstance(before.get('argv'), list) and bool(before.get('argv'))
                  and bool(before.get('lsof_listener_fields')) and bool(before.get('lsof_executable_fields')))
    audit.require('public.v2_process_health_binding', hb.get('health_before_matches_runtime') is True
                  and hb.get('health_after_matches_runtime') is True and hb.get('stable_health') is True and process_ok
                  and hb.get('runtime_receipt_sha256') == file_sha(runtime_path))
    audit.require('public.v2_freeze_chain', d.get('freeze_verification', {}).get('latest_hashes_match') is True
                  and d.get('freeze_verification', {}).get('latest_bound_file_count') == 42)
    audit.require('public.v2_pairs_and_quality', all(r.get('independent_dsl_equal') is True for r in rows)
                  and all(row.get('quality', {}).get('passed') is True
                          for row, (_, version) in zip(rows, inventory) if version in {'som.prompt.v6', 'som.prompt.v7'}))
    exception_rows = {row.get('id'): row for row in rows if row.get('id') in PUBLIC_REFERENCE_FAILURES}
    old_result = audit_json(repo / '.local/rust-compiler-goal/acceptance/public-products-003/result.json') or {}
    old_rows = {row.get('id'): row for row in old_result.get('records', [])}
    old_exception_ok = len(exception_rows) == 2 and all(
        corrected_failure_retained(row, old_rows.get(case_id))
        for case_id, row in exception_rows.items())
    audit.require('public.v2_old_failures_preserved', old_exception_ok)
    nonexception = [row for row in rows if row.get('id') not in PUBLIC_REFERENCE_FAILURES]
    audit.require('public.v2_nonexception_byte_parity', len(nonexception) == 125
                  and all(row.get('complete_file_parity') is True for row in nonexception))
    amendment_hash = d.get('amendment_manifest_sha256')
    audit.require('public.v2_formatter_qualification_path', formatter_path is not None and formatter_path.is_file()
                  and bool(formatter_sha256) and file_sha(formatter_path) == formatter_sha256)
    formatter = audit.json('public.v2_formatter_qualification', formatter_path) if formatter_path else None
    formatter_rows = {row.get('id'): row for row in formatter.get('records', [])} if formatter else {}
    fmt_ok = (formatter is not None and formatter.get('complete') is True
              and formatter.get('original_formatter_failure_count') == 2
              and formatter.get('corrected_reference_count') == 2
              and formatter.get('amendment_manifest_sha256') == amendment_hash
              and set(formatter_rows) == PUBLIC_REFERENCE_FAILURES
              and all(row.get('passed') is True and row.get('corrected_reference_full_file_parity') is True
                      and row.get('reference_formatter_status') == 'failed'
                      and row.get('complete_file_parity') is None
                      and row.get('complete_normalized_ast_equal') is False
                      and row.get('original_reference_check_passed') is False
                      and row.get('reference_formatter_error') == old_rows.get(case_id, {}).get('reference_formatter_error')
                      for case_id, row in formatter_rows.items()))
    if fmt_ok:
        for case_id in PUBLIC_REFERENCE_FAILURES:
            row_dir = formatter_path.parent / case_id
            formatted_map = json_map_file(row_dir / 'corrected-formatted-files.json')
            case_receipt = audit_json(row_dir / 'result.json')
            result_index = ids.index(case_id)
            expected_map = json_map_file(path.parent / f'{result_index:03d}' / 'reference.corrected-expected-files.json')
            fmt_ok &= (formatted_map is not None and formatted_map == expected_map
                       and case_receipt == formatter_rows[case_id])
    audit.require('public.v2_formatter_qualification_passed', fmt_ok)
    focused = manifest.get('focused_summary') if manifest else None
    focused_path = Path(focused.get('path', '')) if isinstance(focused, dict) else Path()
    focused_doc = audit_json(focused_path) if isinstance(focused, dict) else None
    focused_hash_ok = (focused_path.is_file() and file_sha(focused_path) == focused.get('sha256')) if isinstance(focused, dict) else False
    focused_rows = {row.get('id'): row for row in focused_doc.get('cases', [])} if focused_doc else {}
    focused_ok = len(focused_rows) == 11 and all(
        focused_rows.get(cid, {}).get('compile_equivalence') is True
        and focused_rows.get(cid, {}).get('strict_quality_passed') is True
        and focused_rows.get(cid, {}).get('independent_focused_behavior_passed') is True
        for cid in PUBLIC_REFERENCE_FAILURES)
    audit.require('public.v2_focused_binding', focused_hash_ok and focused_ok
                  and all(exception_rows.get(cid, {}).get('focused_behavior_binding', {}).get('passed') is True
                          for cid in PUBLIC_REFERENCE_FAILURES))
    proof_failures = []
    manifest_failures = manifest.get('original_failures', {}) if manifest else {}
    for index, row in enumerate(rows):
        if row.get('id') not in PUBLIC_REFERENCE_FAILURES:
            continue
        directory = path.parent / f'{index:03d}'
        raw = json_map_file(directory / 'reference.raw-files.json')
        corrected = json_map_file(directory / 'reference.corrected-raw-files.json')
        proof_path = directory / 'reference.exact-span-proof.json'
        expected = json_map_file(directory / 'reference.corrected-expected-files.json')
        exchange = audit_json(directory / 'assemble.response.json') or {}
        product = exchange.get('body', {}).get('files') if isinstance(exchange, dict) else None
        proof = audit_json(proof_path) if proof_path.is_file() else None
        raw_entry = manifest_failures.get(row.get('id'), {}).get('raw_files', {})
        original_raw_path = Path(raw_entry.get('path', ''))
        original_raw_map = json_map_file(original_raw_path) if original_raw_path.is_file() else None
        original_raw_bound = (original_raw_map is not None and raw == original_raw_map
                              and file_sha(original_raw_path) == raw_entry.get('sha256'))
        if (raw is None or not original_raw_bound or corrected is None or expected is None or not isinstance(proof, dict)
                or not isinstance(product, dict) or expected != product
                or not row.get('corrected_reference_full_file_parity') is True
                or row.get('corrected_reference_formatter_status') != 'passed'
                or row.get('corrected_reference_proof_sha256') != file_sha(proof_path)
                or row.get('corrected_reference_raw_sha256') != file_sha(directory / 'reference.corrected-raw-files.json')
                or row.get('corrected_reference_expected_sha256') != file_sha(directory / 'reference.corrected-expected-files.json')):
            proof_failures.append(f'{index}:{row.get("id")}:missing-or-unbound-files')
            continue
        good, detail = replay_raw_span_proof(raw, corrected, proof)
        binding = proof.get('original_diagnostic_binding', {})
        raw_detail = old_rows.get(row['id'], {}).get('reference_formatter_error', {}).get('detail', '')
        if not good or binding.get('diagnostic') is None or binding.get('file') not in raw:
            proof_failures.append(f'{index}:{row.get("id")}:{detail}')
            continue
        edit_spans = [item.get('raw_ast_span') for item in proof.get('files', {}).get(binding.get('file'), {}).get('edits', [])]
        if (binding.get('diagnostic_coordinate_space') != 'original formatter output; never raw source'
                or binding.get('raw_selection_independent_of_diagnostic_location') is not True
                or binding.get('raw_ast_span') not in edit_spans):
            proof_failures.append(f'{index}:{row.get("id")}:diagnostic-coordinate-claim')
        try:
            detail_name, _, detail_json = raw_detail.partition(': ')
            diagnostic = json.loads(detail_json)[0]
            if detail_name != binding.get('file') or diagnostic != binding.get('diagnostic'):
                proof_failures.append(f'{index}:{row.get("id")}:original-diagnostic-mismatch')
        except Exception:
            proof_failures.append(f'{index}:{row.get("id")}:original-diagnostic-invalid')
        actual_hashes = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in product.items()}
        if row.get('focused_behavior_binding', {}).get('full_file_sha256') != actual_hashes:
            proof_failures.append(f'{index}:{row.get("id")}:focused-full-product-mismatch')
    audit.require('public.v2_independent_prepass_and_product_replay', not proof_failures,
                  ','.join(proof_failures))
    exchange_bad = []
    cleanup_bad = []
    for index, _row in enumerate(rows):
        directory = path.parent / f'{index:03d}'
        for prefix in ('compile-json', 'compile-cnl', 'assemble'):
            if not verify_saved_exchange(audit, f'public.v2.raw.{index:03d}.{prefix}', directory, prefix, 200):
                exchange_bad.append(f'{index}:{prefix}')
        cleanup_path = directory / 'quality-cache-cleanup.json'
        cleanup = audit_json(cleanup_path) if cleanup_path.is_file() else None
        if not cleanup or cleanup.get('passed') is not True or cleanup.get('bytes_after') != 0:
            cleanup_bad.append(str(index))
    audit.require('public.v2_all_raw_http_exchanges', not exchange_bad, ','.join(exchange_bad[:15]))
    audit.require('public.v2_cleanup_receipts', not cleanup_bad, ','.join(cleanup_bad[:15]))
    return d


def verify_renderer(audit: Audit, path: Path, candidate: dict[str, Any]) -> dict[str, Any] | None:
    d = audit.json('renderer.receipt', path)
    if d is None:
        return None
    rows = d.get('cases', [])
    ids = [row.get('id') for row in rows]
    expected_ids = set(EXPECTED_M2_FAILURES) | {EXPECTED_M2_PASS}
    candidate_block = d.get('candidate', {})
    candidate_after = d.get('candidate_after', {})
    preparation_path = path.parent / 'preparation.json'
    preparation = audit.json('renderer.preparation', preparation_path) or {}
    prepared_cases = preparation.get('cases', [])
    expected_order = [row.get('id') for row in prepared_cases]
    audit.require('renderer.exact_11_unique_case_inventory', len(expected_order) == 11
                  and expected_order == ids and len(set(ids)) == 11 and set(ids) == expected_ids)
    process_before = candidate_block.get('process', {})
    process_after = candidate_after.get('process', {})
    process_ok = (process_before == process_after and valid_process_identity(process_before, candidate))
    runtime_health = candidate['runtime'].get('health')
    output = path.parent
    identity_file = audit.json('renderer.candidate_identity_file', output / 'candidate-identity.json') or {}
    identity_after_file = audit.json('renderer.candidate_identity_after_file', output / 'candidate-identity-after.json') or {}
    expected_identity_fields = {
        'binary': str(candidate['binary_path']),
        'binary_sha256': candidate['binary_sha256'],
        'pid': candidate['runtime'].get('pid'),
        'base_url': f"http://127.0.0.1:{candidate['runtime'].get('port')}",
        'health': runtime_health,
        'source_json_sha256': candidate['source_json_sha256'],
        'source_archive_sha256': candidate['source_archive_sha256'],
        'build_sha256': file_sha(candidate['build_path']),
        'runtime_receipt_sha256': file_sha(candidate['runtime_path']),
    }
    identity_ok = (candidate_block == identity_file and candidate_after == identity_after_file
                   and all(candidate_block.get(key) == value and candidate_after.get(key) == value
                           for key, value in expected_identity_fields.items())
                   and candidate_block.get('binary') == str(candidate['binary_path'])
                   and candidate_after.get('binary') == str(candidate['binary_path'])
                   and candidate_block.get('binary_sha256') == candidate['binary_sha256']
                   and candidate_after.get('binary_sha256') == candidate['binary_sha256']
                   and candidate_block.get('pid') == candidate['runtime'].get('pid')
                   and candidate_after.get('pid') == candidate['runtime'].get('pid')
                   and candidate_block.get('base_url') == f"http://127.0.0.1:{candidate['runtime'].get('port')}"
                   and candidate_after.get('base_url') == candidate_block.get('base_url')
                   and candidate_block.get('health') == runtime_health
                   and candidate_after.get('health') == runtime_health
                   and candidate_block.get('source_json_sha256') == candidate['source_json_sha256']
                   and candidate_after.get('source_json_sha256') == candidate['source_json_sha256']
                   and candidate_block.get('source_archive_sha256') == candidate['source_archive_sha256']
                   and candidate_after.get('source_archive_sha256') == candidate['source_archive_sha256']
                   and candidate_block.get('build_sha256') == file_sha(candidate['build_path'])
                   and candidate_after.get('build_sha256') == file_sha(candidate['build_path'])
                   and candidate_block.get('runtime_receipt_sha256') == file_sha(candidate['runtime_path'])
                   and candidate_after.get('runtime_receipt_sha256') == file_sha(candidate['runtime_path'])
                   and candidate_block.get('health_raw_sha256') == candidate_after.get('health_raw_sha256')
                   and isinstance(candidate_block.get('health_raw_sha256'), str)
                   and len(candidate_block.get('health_raw_sha256', '')) == 64)
    audit.require('renderer.candidate_identity', identity_ok and process_ok
                  and d.get('candidate_identity_stable') is True)
    row_checks = []
    raw_evidence_bad = []
    artifact_bad = []
    artifact_path = output / 'artifact-hashes.json'
    artifact_hashes = audit.json('renderer.artifact_hash_manifest', artifact_path) or {}
    for row in rows:
        ident = row.get('id')
        case_dir = output / str(ident)
        case_path = case_dir / 'case-result.json'
        case_record = audit.json(f'renderer.case_record.{ident}', case_path) or {}
        compile_record = row.get('compile_assemble', {})
        quality = row.get('quality', {})
        focused = row.get('focused_behavior', {})
        case_ok = renderer_case_record_ok(row, case_record)
        row_checks.append(case_ok)
        source_hashes = compile_record.get('outcome', {}).get('file_sha256', {})
        quality_hashes = quality.get('outcome', {}).get('source_sha256', {})
        if not source_hashes or any(source_hashes.get(name) != digest for name, digest in quality_hashes.items()):
            artifact_bad.append(f'{ident}:quality_source_hashes')
        for product_name in ('product', 'focused-product'):
            for relative, digest in source_hashes.items():
                product_path = case_dir / product_name / relative
                if not product_path.is_file() or file_sha(product_path) != digest:
                    artifact_bad.append(f'{ident}:{product_name}/{relative}')
        for filename, value in (
            ('compile-assemble.json', compile_record), ('strict-quality.json', quality),
            ('unchanged-full-m2.json', row.get('existing_behavior', {})),
            ('independent-focused-behavior.json', focused),
            ('cleanup-receipt.json', row.get('cleanup', {})),
        ):
            file_path = case_dir / filename
            if not file_path.is_file() or audit_json(file_path) != value:
                artifact_bad.append(f'{ident}:{filename}:receipt_mismatch')
        expected_hash_files = [case_path, case_dir / 'compile-assemble.json', case_dir / 'strict-quality.json',
                               case_dir / 'unchanged-full-m2.json', case_dir / 'independent-focused-behavior.json',
                               case_dir / 'cleanup-receipt.json']
        for evidence_file in expected_hash_files:
            relative = str(evidence_file.relative_to(output))
            if artifact_hashes.get(relative) != (file_sha(evidence_file) if evidence_file.is_file() else None):
                artifact_bad.append(f'{ident}:artifact-hash:{relative}')
        for prefix in ('compile-json', 'assemble-json', 'compile-cnl', 'assemble-cnl'):
            if not verify_saved_exchange(audit, f'renderer.raw.{ident}.{prefix}', case_dir, prefix, 200):
                raw_evidence_bad.append(f'{ident}:{prefix}')
    audit.require('renderer.all_11_case_receipts_pass', len(row_checks) == 11 and all(row_checks))
    audit.require('renderer.compile_quality_behavior_artifacts_match', not artifact_bad, ','.join(artifact_bad[:12]))
    audit.require('renderer.all_44_http_exchanges', not raw_evidence_bad, ','.join(raw_evidence_bad[:12]))
    # Every path in the runner manifest must be safe, present, and still match.
    manifest_bad = []
    for relative, digest in artifact_hashes.items():
        rel = Path(relative)
        full = output / rel
        if rel.is_absolute() or '..' in rel.parts or not full.is_file() or full.is_symlink() or file_sha(full) != digest:
            manifest_bad.append(relative)
    audit.require('renderer.complete_artifact_hash_verification', bool(artifact_hashes) and not manifest_bad,
                  ','.join(manifest_bad[:12]))
    cleanup_bad = []
    for row in rows:
        ident = row.get('id')
        cleanup_path = output / str(ident) / 'cleanup-receipt.json'
        cleanup = audit.json(f'renderer.cleanup.{ident}', cleanup_path) or {}
        expected_targets = {
            str((output / str(ident) / 'product/.venv').resolve()),
            str((output / str(ident) / 'focused-product/.venv').resolve()),
            str((output / str(ident) / 'quality/mypy-cache').resolve()),
        }
        targets = cleanup.get('targets', [])
        if not renderer_cleanup_ok(cleanup, output, ident):
            cleanup_bad.append(str(ident))
    audit.require('renderer.exact_3_owned_cleanup_targets_per_case', not cleanup_bad, ','.join(cleanup_bad[:12]))
    audit.require('renderer.scoped_11_pass', d.get('scoped_required_passed') is True and len(rows) == 11
                  and len(set(ids)) == 11 and set(ids) == expected_ids
                  and all(row_checks) and d.get('compile_pairs_passed') == 11 and d.get('strict_quality_passed') == 11
                  and d.get('independent_focused_behavior_passed') == 11
                  and d.get('cleanup_passed') is True and not cleanup_bad and d.get('fixture_failure') is None
                  and d.get('model_run') is False and d.get('production_changes') == [])
    pass_rows = [row for row in rows if row.get('unchanged_full_m2_acceptance') is True]
    fail_rows = [row for row in rows if row.get('unchanged_full_m2_acceptance') is False]
    audit.require('renderer.unchanged_m2_one_pass_ten_exact_failures',
                  len(pass_rows) == 1 and [row.get('id') for row in pass_rows] == [EXPECTED_M2_PASS]
                  and len(fail_rows) == 10 and {row.get('id') for row in fail_rows} == set(EXPECTED_M2_FAILURES)
                  and d.get('unchanged_full_m2_passed') == 1
                  and d.get('full_m2_pass_claim') == 'Only exact unchanged verifier true outcomes count; fallback never converts a limit or failure into this pass.')
    scope = d.get('unchanged_m2_scope', [])
    scope_map = {row.get('id'): row for row in scope}
    exact_failures = True
    for ident, (kind, message) in EXPECTED_M2_FAILURES.items():
        row = scope_map.get(ident, {})
        behavior = next((case.get('existing_behavior', {}) for case in rows if case.get('id') == ident), {})
        exact_failures &= (row.get('full_m2_status') == 'FAIL' and row.get('known_checker_limit_matched') is True
                           and row.get('expected_error_type') == kind and row.get('expected_error') == message
                           and behavior.get('completed') is False and behavior.get('error_type') == kind
                           and behavior.get('error') == message)
    exact_failures &= scope_map.get(EXPECTED_M2_PASS, {}).get('full_m2_status') == 'PASS'
    exact_failures &= scope_map.get(EXPECTED_M2_PASS, {}).get('required_full_m2_pass') is True
    audit.require('renderer.exact_historical_errors', len(scope_map) == 11 and exact_failures,
                  'old full-M2 failures remain failures; focused checks do not replace them')
    audit.require('renderer.full_m2_not_reported_complete', d.get('unchanged_full_m2_passed') != 11
                  and d.get('scoped_required_passed') is not False)
    return d


def verify_legacy_inventory(audit: Audit, path: Path, candidate: dict[str, Any]) -> dict[str, Any] | None:
    inv = audit.json('legacy.inventory', path)
    if inv is None:
        return None
    groups = inv.get('groups', [])
    counts = inv.get('counts', {})
    group_ids = [row.get('id') for row in groups]
    checked = {row.get('id') for row in groups if row.get('variant') == 'checked'}
    audit.require('legacy.inventory_544_metadata', counts == EXPECTED_GROUP_COUNTS and len(groups) == 544
                  and len(set(group_ids)) == 544 and len(inv.get('duplicate_prompt_groups', [])) == 3)
    audit.require('legacy.inventory_checked_variants', checked == CHECKED_VARIANTS, ','.join(sorted(checked)))
    oracle = inv.get('asset_index_oracle', {})
    audit.require('legacy.inventory_asset_index_binding', oracle.get('sha256') == candidate['asset_index_sha256']
                  and oracle.get('package_manifest_matches_independent_sources') is True)
    audit.require('legacy.inventory_freeze_binding', inv.get('compiler_freeze', {}).get('amended_contract_files_match') is True
                  and inv.get('compiler_freeze', {}).get('amended_contract_file_count') == 42)
    # This audit intentionally reads inventory metadata only. It does not open the
    # historical expected DSL, response bodies, or corpus source files.
    return inv


def verify_legacy_result(audit: Audit, path: Path, inv_path: Path, inventory: dict[str, Any] | None,
                         candidate: dict[str, Any], runtime_path: Path) -> dict[str, Any] | None:
    d = audit.json('legacy.result', path)
    if d is None:
        return None
    rows = d.get('records', [])
    inventory_rows = inventory.get('groups', []) if inventory else []
    expected_ids = [row.get('id') for row in inventory_rows]
    audit.require('legacy.544_all_jobs_passed', d.get('status') in {'passed', 'pass'}
                  and d.get('required_job_count') == 544 and d.get('passed_job_count') == 544
                  and len(rows) == 544 and [row.get('id') for row in rows] == expected_ids
                  and all(row.get('passed') is True and row.get('archive_byte_parity') is True for row in rows))
    audit.require('legacy.complete_job_accounting', d.get('compile_prompt_count') == 540
                  and d.get('compile_pairs') == 1080 and d.get('assembly_product_count') == 544
                  and d.get('input_inventory_unchanged') is True and d.get('candidate_transport') == 'HTTP'
                  and d.get('old_model_invoked') is False)
    audit.require('legacy.candidate_source_asset_binding', d.get('candidate_binary_sha256') == candidate['binary_sha256']
                  and d.get('candidate_source_sha256') == candidate['source_archive_sha256']
                  and d.get('candidate_source_json_sha256') == candidate['source_json_sha256']
                  and d.get('candidate_asset_manifest_sha256') == candidate['manifest_sha256']
                  and d.get('asset_index_sha256') == candidate['asset_index_sha256'])
    expected_inventory_sha = hashlib.sha256(canonical(inventory).encode('utf-8')).hexdigest() if inventory else None
    audit.require('legacy.inventory_content_hash_binding', d.get('inventory_sha256') == expected_inventory_sha)
    hb = d.get('runtime_health_binding', {})
    runtime_acceptance = json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.is_file() else {}
    health_rows = [row for row in runtime_acceptance.get('checks', []) if row.get('id') == 'health_contract']
    expected_health = health_rows[0].get('actual') if len(health_rows) == 1 else None
    runtime_health_path = runtime_path.parent / 'http/health.json'
    saved_runtime_health = audit.json('legacy.runtime_acceptance_saved_health', runtime_health_path)
    candidate_health = candidate.get('runtime', {}).get('health')
    before_path = path.parent / 'health-before.response.json'
    after_path = path.parent / 'health-after.response.json'
    before_health = audit.json('legacy.health_before_response', before_path)
    after_health = audit.json('legacy.health_after_response', after_path)
    before_raw_path = path.parent / 'health-before.response.raw'
    after_raw_path = path.parent / 'health-after.response.raw'
    health_files_ok = legacy_health_values_ok(
        expected_health, saved_runtime_health, candidate_health, before_health, after_health,
        before_raw_path, after_raw_path, hb)
    audit.require('legacy.saved_health_evidence_matches_candidate',
                  runtime_acceptance.get('status') == 'passed' and len(health_rows) == 1
                  and health_rows[0].get('passed') is True and health_files_ok)
    before, after = hb.get('process_before', {}), hb.get('process_after', {})
    audit.require('legacy.runtime_health_process_binding', hb.get('health_before_status') == 200
                  and hb.get('health_after_status') == 200 and hb.get('before_matches_runtime') is True
                  and hb.get('after_matches_runtime') is True and hb.get('stable') is True
                  and hb.get('expected_health') == expected_health
                  and before == after and valid_process_identity(before, candidate)
                  and valid_process_identity(after, candidate) and hb.get('process_stable') is True)
    audit.require('legacy.receipt_file_bindings', hb.get('build_receipt_sha256') == file_sha(candidate['build_path'])
                  and hb.get('candidate_runtime_receipt_sha256') == file_sha(candidate['runtime_path'])
                  and hb.get('runtime_acceptance_receipt_sha256') == file_sha(runtime_path) if runtime_path.is_file() else False)
    # Validate retained per-job evidence using paths and hashes only. Do not open
    # or display historical gold answers or formal prompt contents.
    run_root = path.parent
    missing: list[str] = []
    bad_raw: list[str] = []
    checked_ids = set()
    compile_dirs = set()
    v100_logs = True
    for index, row in enumerate(rows):
        checked_ids.add(row.get('id')) if row.get('variant') == 'checked' else None
        directory = run_root / f'{index:04d}'
        cdir = Path(row.get('compile_evidence_dir', ''))
        if not cdir.is_absolute():
            cdir = run_root / cdir
        compile_dirs.add(str(cdir.resolve()))
        expected_files = [cdir / 'compile-json.request.json', cdir / 'compile-json.response.json', cdir / 'compile-json.response.raw.b64',
                          cdir / 'compile-cnl.request.json', cdir / 'compile-cnl.response.json', cdir / 'compile-cnl.response.raw.b64',
                          directory / 'variant.bundle.json', directory / 'assemble.request.json', directory / 'assemble.response.json',
                          directory / 'assemble.response.raw.b64']
        if any(not item.is_file() for item in expected_files):
            missing.append(row.get('id', str(index)))
        else:
            for prefix in ('compile-json', 'compile-cnl'):
                try:
                    wrapper = json.loads((cdir / f'{prefix}.response.json').read_text(encoding='utf-8'))
                    raw = base64.b64decode((cdir / f'{prefix}.response.raw.b64').read_text(encoding='ascii'), validate=True)
                    if wrapper.get('http_status') != 200 or json.loads(raw) != wrapper.get('body'):
                        bad_raw.append(f'{row.get("id")}:{prefix}')
                except Exception:
                    bad_raw.append(f'{row.get("id")}:{prefix}:decode')
            try:
                wrapper = json.loads((directory / 'assemble.response.json').read_text(encoding='utf-8'))
                raw = base64.b64decode((directory / 'assemble.response.raw.b64').read_text(encoding='ascii'), validate=True)
                if wrapper.get('http_status') != 200 or json.loads(raw) != wrapper.get('body'):
                    bad_raw.append(f'{row.get("id")}:assemble')
            except Exception:
                bad_raw.append(f'{row.get("id")}:assemble:decode')
        if row.get('family') == 'legacy100':
            v100_logs &= ((directory / 'legacy100-verifier.stdout.txt').is_file()
                          and (directory / 'legacy100-verifier.stderr.txt').is_file())
    audit.require('legacy.all_544_requests_responses', not missing and not bad_raw,
                  f'missing={len(missing)} bad_raw={len(bad_raw)}')
    audit.require('legacy.540_unique_compile_pairs', len(compile_dirs) == 540)
    audit.require('legacy.100_stdout_stderr', sum(row.get('family') == 'legacy100' for row in rows) == 100 and v100_logs)
    audit.require('legacy.checked_variant_exact_three', checked_ids == CHECKED_VARIANTS)
    qrows = [row for row in rows if row.get('family') != 'legacy100']
    b_rows = [row for row in rows if row.get('behavior_expected') is True]
    audit.require('legacy.quality_444', len(qrows) == 444 and all(row.get('quality', {}).get('passed') is True for row in qrows))
    audit.require('legacy.behavior_452', len(b_rows) == 452 and all(row.get('behavior', {}).get('passed') is True for row in b_rows))
    cleanup_bad = []
    for index in range(len(rows)):
        cp = run_root / f'{index:04d}/cleanup-receipt.json'
        if not cp.is_file():
            cleanup_bad.append(f'{index:04d}:missing')
            continue
        c = json.loads(cp.read_text(encoding='utf-8'))
        targets = c.get('targets', [])
        if c.get('passed') is not True or len(targets) != 2 or any(t.get('bytes_after') != 0 for t in targets):
            cleanup_bad.append(f'{index:04d}:failed')
    audit.require('legacy.544_cleanup_receipts', not cleanup_bad, ','.join(cleanup_bad[:12]))
    return d


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    goal = repo / '.local/rust-compiler-goal'
    acc = goal / 'acceptance'
    candidate_dir = args.candidate.resolve()
    audit = Audit()
    freeze_chain(audit, repo)
    candidate = verify_candidate(audit, repo, candidate_dir, acc)
    runtime, gaps = verify_runtime_gaps(audit, acc, candidate)
    diag = verify_diagnostics(audit, args.diagnostics, candidate)
    performance = verify_performance(audit, args.performance, candidate)
    verify_original_public_preserved(audit, repo, candidate, acc)
    verify_failed_amendment_001(audit, acc)
    verify_failed_amendment_002_startup(audit, acc)
    expected_amendment_hashes = {
        'manifest': args.amendment_manifest_sha256,
        'installed': args.amendment_installed_sha256,
        'archive': args.amendment_archive_sha256,
    }
    amendment = verify_amendment_v2(audit, repo, args.amendment_manifest,
                                    args.amendment_installed, args.amendment_archive,
                                    expected_amendment_hashes, candidate)
    verify_formatter_receipt(audit, repo, acc, candidate, amendment,
                             args.formatter_qualification,
                             args.formatter_qualification_sha256)
    public = verify_public_v2(audit, repo, args.public, candidate, args.runtime,
                              amendment, args.formatter_qualification,
                              args.formatter_qualification_sha256)
    renderer = verify_renderer(audit, args.renderer, candidate)
    inventory = verify_legacy_inventory(audit, args.legacy_inventory, candidate)
    legacy = verify_legacy_result(audit, args.legacy_result, args.legacy_inventory, inventory, candidate, args.runtime)
    frozen = verify_frozen(audit, repo, acc, candidate, runtime, gaps, args.diagnostics, args.public, args.legacy_result)
    frozen_003b_attributed = verify_frozen_attribution(audit, args.frozen_attribution, repo, acc, candidate)
    if frozen_003b_attributed:
        for row in audit.checks:
            if row['name'] == 'frozen.binary_attribution' and row['state'] == 'UNRUN':
                row['state'] = 'PASS'
                row['detail'] = 'separate frozen-003b rerun is directly bound; original frozen-003 receipt remains unchanged'
        audit.unrun = [row for row in audit.unrun if row['check'] != 'frozen.binary_attribution']
    # Resolve the three dedicated frozen unrun cases only after their full receipts pass.
    public_checks = [row for row in audit.checks if row['name'].startswith('public.')]
    legacy_checks = [row for row in audit.checks if row['name'].startswith('legacy.') and not row['name'].startswith('legacy.inventory')]
    public_ok = public is not None and bool(public_checks) and all(row['state'] == 'PASS' for row in public_checks)
    legacy_ok = legacy is not None and bool(legacy_checks) and all(row['state'] == 'PASS' for row in legacy_checks)
    for case, gate in (('equivalent_cnl_json', public_ok), ('all_versions', public_ok), ('checked_variant', legacy_ok)):
        matches = [row for row in audit.checks if row['name'] == 'discharge.' + case]
        if matches:
            # Replace provisional status from the frozen section with full dedicated receipt result.
            matches[0]['state'] = 'PASS' if gate else ('UNRUN' if not public_path_exists(case, args) else 'FAIL')
            matches[0]['detail'] += '; dedicated receipt validated' if gate else '; dedicated receipt missing or failed'
            if matches[0]['state'] == 'UNRUN' and not any(row['check'] == matches[0]['name'] for row in audit.unrun):
                audit.unrun.append({'check': matches[0]['name'], 'reason': matches[0]['detail']})
            elif matches[0]['state'] != 'UNRUN':
                audit.unrun = [row for row in audit.unrun if row['check'] != matches[0]['name']]
    performance_summary: dict[str, Any] | None = None
    if performance is not None:
        obs = performance.get('observations', [])
        by_operation: dict[str, list[dict[str, Any]]] = {}
        for row in obs:
            by_operation.setdefault(row.get('operation', 'unknown'), []).append(row)
        if len(obs) == 84 and all(type(row.get('elapsed_ms')) in (int, float)
                                  and type(row.get('rss_after_response', {}).get('rss_kib')) is int for row in obs):
            performance_summary = {
                'kind': 'observation_only_no_threshold',
                'median_elapsed_ms': {op: round(statistics.median(row['elapsed_ms'] for row in rows), 3)
                                      for op, rows in sorted(by_operation.items())},
                'service_rss_snapshot_mib_range': [
                    round(min(row['rss_after_response']['rss_kib'] for row in obs) / 1024, 2),
                    round(max(row['rss_after_response']['rss_kib'] for row in obs) / 1024, 2)],
                'rss_scope': performance.get('rss_scope'),
            }
    return {
        'version': 'som.compiler-candidate003-independent-audit.v1',
        'candidate': str(candidate_dir), 'candidate_binary_sha256': candidate['binary_sha256'],
        'source_file_count': len(candidate['source_json'].get('files', {})),
        'acceptance_tool_file_count': len((audit_json(candidate_dir / 'acceptance-tools.json') or {}).get('files', {})),
        'checks': audit.checks, 'unrun': audit.unrun,
        'passed': audit.passed,
        'performance_observation_summary': performance_summary,
        'summary': {
            'pass': sum(row['state'] == 'PASS' for row in audit.checks),
            'fail': sum(row['state'] == 'FAIL' for row in audit.checks),
            'unrun': sum(row['state'] == 'UNRUN' for row in audit.checks),
        },
        'scope_note': 'No service calls, model/data lookups, production writes, tracker changes, or Git changes were made by this audit.',
        'limitations': [
            'Performance observations are evidence only. This auditor applies no latency or memory threshold.',
            'Renderer scoped 11-case acceptance is separate from the unchanged full-M2 checker; only REF_NULLABLE_OPTIONAL_FIELD may pass full-M2, and the other ten expected historical failures remain failures.',
            'Missing required receipts remain UNRUN and prevent an overall pass.',
        ],
    }


def public_path_exists(case: str, args: argparse.Namespace) -> bool:
    if case in {'equivalent_cnl_json', 'all_versions'}:
        return args.public.is_file()
    if case == 'checked_variant':
        return args.legacy_result.is_file()
    return False


def audit_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def public_nonexception_parity_ok(rows: list[dict[str, Any]]) -> bool:
    exceptions = {'REF_BRANCH_EXPORT_SAME_TYPE', 'PUBLIC_v7-branch-null'}
    return all(row.get('complete_file_parity') is True for row in rows if row.get('id') not in exceptions)


def renderer_case_record_ok(row: dict[str, Any], case_record: dict[str, Any]) -> bool:
    compile_record = row.get('compile_assemble', {})
    quality = row.get('quality', {})
    focused = row.get('focused_behavior', {})
    return (row.get('compile_equivalence') is True
            and compile_record.get('completed') is True
            and compile_record.get('outcome', {}).get('passed') is True
            and row.get('strict_quality_passed') is True
            and row.get('quality_status') == 'PASS'
            and quality.get('completed') is True
            and quality.get('outcome', {}).get('passed') is True
            and quality.get('outcome', {}).get('source_unchanged') is True
            and row.get('independent_focused_behavior_passed') is True
            and row.get('independent_focused_behavior_status') == 'PASS'
            and focused.get('completed') is True
            and focused.get('outcome', {}).get('passed') is True
            and case_record == row)


def renderer_cleanup_ok(cleanup: dict[str, Any], output: Path, ident: str) -> bool:
    expected_targets = {
        str((output / ident / 'product/.venv').resolve()),
        str((output / ident / 'focused-product/.venv').resolve()),
        str((output / ident / 'quality/mypy-cache').resolve()),
    }
    targets = cleanup.get('targets', [])
    target_paths = {target.get('path') for target in targets}
    absent_ok = all(not Path(target).exists() and not Path(target).is_symlink() for target in expected_targets)
    return (cleanup.get('passed') is True and cleanup.get('behavior_complete') is True
            and cleanup.get('output') == str(output.resolve()) and len(targets) == 3
            and target_paths == expected_targets
            and all(target.get('bytes_after') == 0 and not target.get('live_processes') for target in targets)
            and absent_ok)


def legacy_health_values_ok(expected: Any, saved_runtime: Any, candidate: Any,
                            before: Any, after: Any, before_raw: Path, after_raw: Path,
                            binding: dict[str, Any]) -> bool:
    if (expected is None or saved_runtime != expected or candidate != expected or before != expected or after != expected
            or not before_raw.is_file() or not after_raw.is_file()):
        return False
    try:
        before_bytes, after_bytes = before_raw.read_bytes(), after_raw.read_bytes()
        return (file_sha(before_raw) == binding.get('health_before_sha256')
                and file_sha(after_raw) == binding.get('health_after_sha256')
                and before_bytes == after_bytes and json.loads(before_bytes) == expected
                and json.loads(after_bytes) == expected)
    except Exception:
        return False


def self_test() -> dict[str, Any]:
    tests = []
    def test(name: str, passed: bool) -> None:
        tests.append({'name': name, 'passed': passed})
    test('missing_receipt_is_unrun', missing_gate_state(None) == 'UNRUN')
    incomplete = Audit()
    incomplete.missing('required.receipt', Path('/missing/receipt.json'))
    test('missing_receipt_blocks_overall_pass', not incomplete.passed)
    test('wrong_binary_binding_fails', not receipt_binary_matches({'candidate_binary_sha256': '0' * 64}, EXPECTED_BINARY))
    good_renderer = {'cases': [{'id': EXPECTED_M2_PASS, 'unchanged_full_m2_acceptance': True}]
                     + [{'id': case, 'unchanged_full_m2_acceptance': False} for case in EXPECTED_M2_FAILURES],
                     'unchanged_full_m2_passed': 1}
    test('renderer_one_pass_ten_failures', renderer_scope_ok(good_renderer))
    bad_renderer = dict(good_renderer, unchanged_full_m2_passed=11,
                        cases=[dict(row, unchanged_full_m2_acceptance=True) for row in good_renderer['cases']])
    test('renderer_fullpass_is_rejected', not renderer_scope_ok(bad_renderer))
    good_row = {'id': 'case', 'compile_equivalence': True,
                'compile_assemble': {'completed': True, 'outcome': {'passed': True}},
                'strict_quality_passed': True, 'quality_status': 'PASS',
                'quality': {'completed': True, 'outcome': {'passed': True, 'source_unchanged': True}},
                'independent_focused_behavior_passed': True,
                'independent_focused_behavior_status': 'PASS',
                'focused_behavior': {'completed': True, 'outcome': {'passed': True}}}
    test('renderer_case_requires_all_three_independent_gates', renderer_case_record_ok(good_row, good_row))
    test('renderer_case_rejects_missing_focused_gate', not renderer_case_record_ok(
        dict(good_row, independent_focused_behavior_passed=False),
        dict(good_row, independent_focused_behavior_passed=False)))
    test('public_nonexception_parity_rejects_false', not public_nonexception_parity_ok([
        {'id': 'ordinary', 'complete_file_parity': False}]))
    test('public_parity_exception_is_retained', public_nonexception_parity_ok([
        {'id': 'PUBLIC_v7-branch-null', 'complete_file_parity': None}]))
    old_error = {'code': 'OUTPUT_QUALITY_FAILED', 'detail': 'flows.py: [SIM108]'}
    old_failure = {'reference_formatter_status': 'failed', 'complete_file_parity': None,
                   'complete_normalized_ast_equal': False, 'reference_formatter_error': old_error}
    test('public_v2_retains_exact_historical_failure', corrected_failure_retained(dict(old_failure), old_failure))
    test('public_v2_rejects_changed_historical_failure', not corrected_failure_retained(
        dict(old_failure, reference_formatter_error={'code': 'changed'}), old_failure))
    raw_source = 'def f(x):\n    if x:\n        y = 1\n    else:\n        y = 2\n    return y\n'
    raw_bytes = raw_source.encode('utf-8')
    tree = ast.parse(raw_source, type_comments=True)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If))
    raw_rows = raw_bytes.splitlines(keepends=True)
    offsets = [0]
    for raw_line in raw_rows:
        offsets.append(offsets[-1] + len(raw_line))
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    replacement = 'y = (1 if x else 2\n\n\n)'
    corrected_bytes = raw_bytes[:start] + replacement.encode() + raw_bytes[end:]
    corrected_source = corrected_bytes.decode()
    corrected_ast_hash = hashlib.sha256(ast.dump(ast.parse(corrected_source, type_comments=True),
                                                include_attributes=False).encode()).hexdigest()
    edit = {'raw_ast_span': [node.lineno, node.col_offset, node.end_lineno, node.end_col_offset],
            'start_byte': start, 'end_byte': end,
            'original_span_sha256': hashlib.sha256(raw_bytes[start:end]).hexdigest(),
            'replacement': replacement,
            'replacement_sha256': hashlib.sha256(replacement.encode()).hexdigest()}
    proof = {'version': 'som.public-reference-span-prepass.v2',
             'candidate_input': False, 'ruff_location_used': False, 'edit_count': 1,
             'files': {'sample.py': {'raw_sha256': hashlib.sha256(raw_bytes).hexdigest(),
                                     'corrected_sha256': hashlib.sha256(corrected_bytes).hexdigest(),
                                     'whole_ast_equal': True, 'outside_bytes_equal': True,
                                     'whole_ast_sha256': corrected_ast_hash, 'type_comments_parsed': True,
                                     'edits': [edit]}},
             'original_diagnostic_binding': {'file': 'sample.py', 'diagnostic': {'code': 'SIM108'},
                                             'diagnostic_coordinate_space': 'original formatter output; never raw source',
                                             'raw_selection_independent_of_diagnostic_location': True,
                                             'raw_ast_span': edit['raw_ast_span']}}
    raw_map, corrected_map = {'sample.py': raw_source}, {'sample.py': corrected_source}
    test('public_v2_exact_raw_prepass_replay', replay_raw_span_proof(raw_map, corrected_map, proof)[0])
    forged = copy.deepcopy(proof)
    forged['files']['sample.py']['edits'][0]['start_byte'] += 1
    test('public_v2_forged_proof_offset_rejected', not replay_raw_span_proof(raw_map, corrected_map, forged)[0])
    bad_corrected = {'sample.py': corrected_source.replace('return y', 'return 7')}
    test('public_v2_bad_corrected_bytes_rejected', not replay_raw_span_proof(raw_map, bad_corrected, proof)[0])
    missing_amendment = Audit()
    verify_amendment_v2(missing_amendment, Path('/repo'), None, None, None,
                        {'manifest': None, 'installed': None, 'archive': None}, {})
    test('public_v2_missing_amendment_fails_closed',
         bool(missing_amendment.checks) and all(row['state'] == 'FAIL' for row in missing_amendment.checks))
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / 'run'
        ident = 'case'
        targets = [str((output / ident / 'product/.venv').resolve()),
                   str((output / ident / 'focused-product/.venv').resolve()),
                   str((output / ident / 'quality/mypy-cache').resolve())]
        good_cleanup = {'passed': True, 'behavior_complete': True, 'output': str(output.resolve()),
                        'targets': [{'path': target, 'bytes_after': 0, 'live_processes': []} for target in targets]}
        test('renderer_cleanup_requires_exact_three_absent_targets', renderer_cleanup_ok(good_cleanup, output, ident))
        bad_cleanup = dict(good_cleanup, targets=good_cleanup['targets'][:2])
        test('renderer_cleanup_rejects_missing_target', not renderer_cleanup_ok(bad_cleanup, output, ident))
        before_raw, after_raw = Path(temporary) / 'before', Path(temporary) / 'after'
        health = {'status': 'ok', 'purpose': 'som.rust-compiler.v1', 'model_loaded': False}
        raw = json.dumps(health, sort_keys=True).encode()
        before_raw.write_bytes(raw)
        after_raw.write_bytes(raw)
        binding = {'health_before_sha256': hashlib.sha256(raw).hexdigest(),
                   'health_after_sha256': hashlib.sha256(raw).hexdigest()}
        test('legacy_health_requires_saved_matches', legacy_health_values_ok(
            health, health, health, health, health, before_raw, after_raw, binding))
        test('legacy_health_rejects_changed_saved_response', not legacy_health_values_ok(
            health, health, health, health, health, before_raw, after_raw,
            dict(binding, health_after_sha256='0' * 64)))
    good_perf = {'observation_count': 84, 'all_requests_completed': True,
                 'rss_samples_complete': True, 'successful_http_responses': 84,
                 'comparison_passed': True, 'performance_thresholds': None}
    test('performance_complete_is_observational', performance_shape_ok(good_perf))
    test('performance_missing_rss_fails', not performance_shape_ok(dict(good_perf, rss_samples_complete=False)))
    test('missing_legacy_jobs_fails', not legacy_count_ok({'records': []}))
    test('legacy_exact_544_passes_shape', legacy_count_ok({'records': [{'passed': True}] * 544}))
    test('legacy_missing_one_is_rejected', not legacy_count_ok({'records': [{'passed': True}] * 543}))
    fake_candidate = {'runtime': {'pid': 5, 'port': 9, 'argv': ['/x/som', 'serve']},
                      'binary_path': Path('/x/som'), 'binary_sha256': 'a' * 64,
                      'source_json_sha256': 'b' * 64}
    fake_identity = {'pid': 5, 'uid': os.getuid(), 'lstart': 'start', 'argv': ['/x/som', 'serve'],
                     'port': 9, 'binary_sha256': 'a' * 64, 'source_sha256': 'b' * 64,
                     'lsof_listener_fields': ['p5', 'n127.0.0.1:9'],
                     'lsof_executable_fields': ['p5', 'n/x/som']}
    test('attribution_process_exact_identity_passes', valid_process_identity(fake_identity, fake_candidate))
    test('attribution_wrong_process_rejected', not valid_process_identity(dict(fake_identity, pid=6), fake_candidate))
    expected_test_argv = ['python', 'qa.py', '--repo', '/som']
    test('attribution_exact_argv_accepts_match', exact_argv_matches(expected_test_argv, expected_test_argv))
    test('attribution_exact_argv_rejects_change', not exact_argv_matches(expected_test_argv + ['--wrong'], expected_test_argv))
    sample_report = {'completed': False, 'unrun': [{'case': name} for name in UNRUN_DISCHARGES]}
    test('attribution_exit_two_with_nine_unrun', frozen_exit_ok(2, sample_report))
    test('attribution_wrong_exit_rejected', not frozen_exit_ok(0, sample_report))
    test('attribution_missing_deferred_case_rejected', not frozen_exit_ok(2, {'completed': False, 'unrun': [{'case': name} for name in list(UNRUN_DISCHARGES)[:-1]]}))
    return {'version': 'som.compiler-audit-self-test.v1', 'passed': all(row['passed'] for row in tests), 'tests': tests}


def missing_gate_state(receipt: Any) -> str:
    return 'UNRUN' if receipt is None else 'PASS'


def receipt_binary_matches(receipt: dict[str, Any], expected: str) -> bool:
    return receipt.get('candidate_binary_sha256') == expected


def renderer_scope_ok(doc: dict[str, Any]) -> bool:
    rows = doc.get('cases', [])
    ids = [row.get('id') for row in rows]
    passes = [row for row in rows if row.get('unchanged_full_m2_acceptance') is True]
    failures = [row for row in rows if row.get('unchanged_full_m2_acceptance') is False]
    return (len(rows) == 11 and set(ids) == set(EXPECTED_M2_FAILURES) | {EXPECTED_M2_PASS}
            and len(set(ids)) == 11 and [row.get('id') for row in passes] == [EXPECTED_M2_PASS]
            and len(failures) == 10 and {row.get('id') for row in failures} == set(EXPECTED_M2_FAILURES)
            and doc.get('unchanged_full_m2_passed') == 1)


def performance_shape_ok(doc: dict[str, Any]) -> bool:
    return (doc.get('observation_count') == 84 and doc.get('all_requests_completed') is True
            and doc.get('rss_samples_complete') is True and doc.get('successful_http_responses') == 84
            and doc.get('comparison_passed') is True and doc.get('performance_thresholds') is None)


def legacy_count_ok(doc: dict[str, Any]) -> bool:
    rows = doc.get('records', [])
    return len(rows) == 544 and all(row.get('passed') is True for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--validate-reference-amendment', action='store_true',
                        help='read-only validation of a pinned amendment and formatter receipt only')
    parser.add_argument('--repo', type=Path, default=Path(os.environ.get('SOM_REPO', str(REPO_DEFAULT))))
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path, default=SCRATCH / 'candidate-003-audit.json')
    parser.add_argument('--diagnostics', type=Path)
    parser.add_argument('--performance', type=Path)
    parser.add_argument('--public', type=Path)
    parser.add_argument('--amendment-manifest', type=Path)
    parser.add_argument('--amendment-installed', type=Path)
    parser.add_argument('--amendment-archive', type=Path)
    parser.add_argument('--amendment-manifest-sha256')
    parser.add_argument('--amendment-installed-sha256')
    parser.add_argument('--amendment-archive-sha256')
    parser.add_argument('--formatter-qualification', type=Path)
    parser.add_argument('--formatter-qualification-sha256')
    parser.add_argument('--renderer', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--frozen-attribution', type=Path,
                        help='optional sidecar that binds the fresh frozen-003b run to candidate 003')
    parser.add_argument('--legacy-inventory', type=Path)
    parser.add_argument('--legacy-result', type=Path)
    a = parser.parse_args()
    if a.self_test:
        result = self_test()
        print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 1
    repo = a.repo.resolve()
    goal = repo / '.local/rust-compiler-goal'
    acc = goal / 'acceptance'
    candidate = a.candidate or goal / 'candidate-003'
    if a.validate_reference_amendment:
        required = (a.amendment_manifest, a.amendment_installed, a.amendment_archive,
                    a.amendment_manifest_sha256, a.amendment_installed_sha256,
                    a.amendment_archive_sha256, a.formatter_qualification,
                    a.formatter_qualification_sha256)
        if any(value is None for value in required):
            parser.error('--validate-reference-amendment requires explicit amendment and formatter paths plus SHA256 pins')
        output = a.output.resolve()
        if not output.is_relative_to(SCRATCH) or output.exists():
            parser.error(f'output must be a new path under {SCRATCH}')
        audit = Audit()
        verify_failed_amendment_001(audit, acc)
        verify_failed_amendment_002_startup(audit, acc)
        verified_candidate = verify_candidate(audit, repo, candidate.resolve(), acc)
        manifest = verify_amendment_v2(audit, repo, a.amendment_manifest,
                                       a.amendment_installed, a.amendment_archive,
                                       {'manifest': a.amendment_manifest_sha256,
                                        'installed': a.amendment_installed_sha256,
                                        'archive': a.amendment_archive_sha256},
                                       verified_candidate)
        verify_formatter_receipt(audit, repo, acc, verified_candidate, manifest,
                                 a.formatter_qualification,
                                 a.formatter_qualification_sha256)
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {'version': 'som.public-reference-amendment-preflight.v1',
                  'passed': audit.passed, 'checks': audit.checks,
                  'scope_note': 'Read-only receipt, archive, source, failure-preservation and formatter-proof checks; no compiler or service calls.'}
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({'output': str(output), 'passed': report['passed'],
                          'checks': len(audit.checks), 'failed': sum(c['state'] == 'FAIL' for c in audit.checks)}))
        return 0 if report['passed'] else 1
    a.candidate = candidate
    a.diagnostics = a.diagnostics or acc / 'diagnostic-003/results.json'
    a.performance = a.performance or acc / 'performance-003/result.json'
    a.public = a.public or acc / 'public-products-003b/result.json'
    a.renderer = a.renderer or acc / 'renderer_behavior/run-003/summary.json'
    a.runtime = a.runtime or acc / 'runtime-003/result.json'
    if a.frozen_attribution is None:
        default_attribution = acc / 'frozen-attribution-003b.json'
        if default_attribution.is_file():
            a.frozen_attribution = default_attribution
    a.legacy_inventory = a.legacy_inventory or acc / 'legacy544/inventory-003.json'
    a.legacy_result = a.legacy_result or acc / 'legacy544/run-003/result.json'
    output = a.output.resolve()
    if not output.is_relative_to(SCRATCH) or output.exists():
        parser.error(f'output must be a new path under {SCRATCH}')
    report = run_audit(a)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'passed': report['passed'], **report['summary']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
