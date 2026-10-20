#!/usr/bin/env python3
"""Create a per-prompt-version asset oracle from approved public sources.

No candidate bundle is read. The installed package manifest is checked against
independently hashed repository sources, then used only as a mismatch detector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path: sys.path.insert(0, str(CODE_DIR))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
ROOT = find_repo(__file__)
OUTPUT_ROOT = default_output_root(ROOT)
import qa_harness as qa  # noqa: E402
RECEIPT_PATHS = {
    'initial': ROOT / 'som-rs/evidence/rust-compiler-contract-freeze-2026-10-13.json',
    'amendment001': ROOT / 'som-rs/evidence/rust-compiler-contract-amendment-001.json',
    'amendment002': ROOT / 'som-rs/evidence/rust-compiler-contract-amendment-002.json',
}
EXPECTED_RECEIPTS = {
    'initial': '2ce6e7027687c7e06ee92a4d6c326fd5f00b6e17f83d5bd80db4e82ebb8cee79',
    'amendment001': '041b33e72d22833091c69ed6407ba979a1b5320e81214aa939e588032af4a797',
    'amendment002': 'c95b79a89737b2568e7b92529c7b5012c6035bc8e62f830eb010693a4445326d',
}


def raw_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return raw_hash(path.read_bytes())


def canonical_hash(value: Any) -> str:
    return raw_hash(qa.canonical(value).encode('utf-8'))


def descriptor(identity: str, data: bytes) -> dict[str, str]:
    return {'id': identity, 'sha256': raw_hash(data)}


def described(identity: str, value: Any) -> dict[str, str]:
    return descriptor(identity, qa.canonical(value).encode('utf-8'))


def block_library_descriptor(path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    raw = path.read_bytes()
    document = json.loads(raw)
    if not isinstance(document, list):
        raise ValueError(f'{path}: block source must be a JSON array')
    rows: dict[str, Any] = {}
    for block in document:
        if not isinstance(block, dict) or not isinstance(block.get('id'), str):
            raise ValueError(f'{path}: invalid block identity')
        identity = block['id']
        if identity in rows:
            raise ValueError(f'{path}: duplicate block id {identity}')
        rows[identity] = {
            'version': block['version'],
            'source_sha256': canonical_hash(block),
        }
    library = {
        'version': 'som.compiler-block-library.v1',
        'file_sha256': raw_hash(raw),
        'blocks': rows,
    }
    return described('som.compiler-block-library.v1', library), library


def source_backend_manifest() -> dict[str, Any]:
    backend_sources = {
        'src/lib.rs': ROOT / 'som-rs/src/lib.rs',
        'src/cnl.rs': ROOT / 'som-rs/src/cnl.rs',
        'src/cnl_semantics.rs': ROOT / 'som-rs/src/cnl_semantics.rs',
        'src/planner.rs': ROOT / 'som-rs/src/planner.rs',
        'src/compiler.rs': ROOT / 'som-rs/src/compiler.rs',
        'src/strict_json.rs': ROOT / 'som-rs/src/strict_json.rs',
        'src/main.rs': ROOT / 'som-rs/src/main.rs',
        'src/related.rs': ROOT / 'som-rs/src/related.rs',
        'src/business.rs': ROOT / 'som-rs/src/business.rs',
        'src/composed.rs': ROOT / 'som-rs/src/composed.rs',
        'src/flow.rs': ROOT / 'som-rs/src/flow.rs',
        'src/flow_compact.rs': ROOT / 'som-rs/src/flow_compact.rs',
        'src/quality.rs': ROOT / 'som-rs/src/quality.rs',
        'Cargo.toml': ROOT / 'som-rs/Cargo.toml',
        'Cargo.lock': ROOT / 'som-rs/Cargo.lock',
        'contracts/related-business.v1.md': ROOT / 'som-rs/contracts/related-business.v1.md',
        'contracts/related-flow.v1.md': ROOT / 'som-rs/contracts/related-flow.v1.md',
        'contracts/python-literal-encoding.v1.json': ROOT / 'som-rs/contracts/python-literal-encoding.v1.json',
        'contracts/python-quality-v1/pyproject.toml': ROOT / 'som-rs/contracts/python-quality-v1/pyproject.toml',
        'block-contracts/composed.json': ROOT / 'som-code-python/data/block-contracts/composed.json',
        'block-contracts/composed-quality.json': ROOT / 'som-code-python/data/block-contracts/composed-quality.json',
        'block-contracts/flow-v7.json': ROOT / 'som-code-python/data/block-contracts/flow-v7.json',
        'block-contracts/flow-v7-compact.json': ROOT / 'som-code-python/data/block-contracts/flow-v7-compact.json',
        'flow_compact/forward_support.py.tpl': ROOT / 'som-core/block_templates/flow_compact/forward_support.py.tpl',
        'flow_compact/engine.py.tpl': ROOT / 'som-core/block_templates/flow_compact/engine.py.tpl',
    }
    diagnostics_path = ROOT / 'som-rs/src/json_diagnostics.rs'
    if diagnostics_path.is_file():
        backend_sources['src/json_diagnostics.rs'] = diagnostics_path
    return {
        'version': 'som.compiler-backend.v1',
        'files': {key: file_hash(path) for key, path in sorted(backend_sources.items())},
    }


def formatter_pin(compiler_source: bytes) -> str:
    match = re.search(rb'const FORMATTER_SHA256: &str = "([0-9a-f]{64})";', compiler_source)
    if match is None:
        raise ValueError('compiler.rs does not expose the pinned formatter digest')
    return match.group(1).decode('ascii')


def verify_package_files(package: Path, formatter_path: Path, profile_path: Path,
                         source_paths: dict[str, Path], quality_config: bytes) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for package_name, source_path in source_paths.items():
        expected = source_path.read_bytes()
        actual = (package / package_name).read_bytes()
        checks.append({'asset': package_name, 'matches_source_bytes': actual == expected,
                       'source_sha256': raw_hash(expected), 'package_sha256': raw_hash(actual)})
    formatter_bytes = formatter_path.read_bytes()
    packaged_formatter = (package / 'ruff').read_bytes()
    checks.append({'asset': 'ruff', 'matches_source_bytes': packaged_formatter == formatter_bytes,
                   'source_sha256': raw_hash(formatter_bytes),
                   'package_sha256': raw_hash(packaged_formatter)})
    profile_bytes = profile_path.read_bytes()
    packaged_profile = (package / 'pyproject.toml').read_bytes()
    checks.append({'asset': 'pyproject.toml', 'matches_source_bytes': packaged_profile == profile_bytes,
                   'source_sha256': raw_hash(profile_bytes),
                   'package_sha256': raw_hash(packaged_profile)})
    config_bytes = (package / 'quality.json').read_bytes()
    checks.append({'asset': 'quality.json', 'matches_independent_config': config_bytes == quality_config,
                   'source_sha256': raw_hash(quality_config),
                   'package_sha256': raw_hash(config_bytes)})
    failed = [row for row in checks if not row.get('matches_source_bytes', row.get('matches_independent_config'))]
    return {'checks': checks, 'all_package_files_match': not failed,
            'mismatches': failed,
            'interpretation': 'package comparison is diagnostic; source-derived index remains authoritative'}


def build_asset_index(package: Path, formatter_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    # Bind the acceptance inputs to the complete latest auditable receipt chain.
    freeze = qa.verify_freeze(ROOT)
    for name, path in RECEIPT_PATHS.items():
        actual = file_hash(path)
        if actual != EXPECTED_RECEIPTS[name]:
            raise ValueError(f'{name} receipt SHA mismatch: {actual}')

    compiler_path = ROOT / 'som-rs/src/compiler.rs'
    compiler_bytes = compiler_path.read_bytes()
    contract_dir = ROOT / 'som-rs/contracts/compiler-v1'
    policy_path = contract_dir / 'selection-policy.json'
    grammar_path = contract_dir / 'grammar.ebnf'
    contract_path = contract_dir / 'CONTRACT.md'
    serializer_path = contract_dir / 'serializer-policy.json'
    literal_path = contract_dir / 'literal-policy.json'
    business_contract = ROOT / 'som-rs/contracts/related-business.v1.md'
    flow_contract = ROOT / 'som-rs/contracts/related-flow.v1.md'
    legacy_path = ROOT / 'som-code-python/data/blocks/v5/blocks.json'
    quality_blocks_path = ROOT / 'som-code-python/data/blocks/composed-quality/blocks.json'
    current_path = ROOT / 'som-code-python/data/blocks/flow-v7-compact-self/blocks.json'
    legacy_catalog_path = policy_path
    quality_catalog_path = ROOT / 'som-code-python/data/block-contracts/composed-quality.json'
    flow_catalog_path = ROOT / 'som-code-python/data/block-contracts/flow-v7-compact.json'
    profile_path = ROOT / 'som-rs/contracts/python-quality-v1/pyproject.toml'
    source_paths = {
        'blocks.json': current_path,
        'legacy-blocks.json': legacy_path,
        'quality-blocks.json': quality_blocks_path,
    }

    formatter_bytes = formatter_path.read_bytes()
    formatter_sha = raw_hash(formatter_bytes)
    source_pin = formatter_pin(compiler_bytes)
    if formatter_sha != source_pin:
        raise ValueError(f'formatter file SHA {formatter_sha} differs from compiler source pin {source_pin}')
    profile_bytes = profile_path.read_bytes()
    formatter_config = {
        'id': 'som.python-quality.v1',
        'formatter_version': 'ruff 0.16.6',
        'formatter_sha256': formatter_sha,
        'profile_sha256': raw_hash(profile_bytes),
    }
    quality_config = (qa.canonical(formatter_config) + '\n').encode('utf-8')
    package_check = verify_package_files(package, formatter_path, profile_path, source_paths, quality_config)

    backend = source_backend_manifest()
    backend_hashes = backend['files']
    compiler_source_manifest = qa.load_json(contract_dir / 'backend-source-manifest.json')
    historical_sources = compiler_source_manifest.get('sources')
    if not isinstance(historical_sources, dict):
        raise ValueError('frozen backend source manifest is malformed')
    normalized_historical_sources = {
        key.removeprefix('som-rs/').removeprefix('som-core/block_templates/')
        .replace('som-rs/src/', 'src/').replace('som-rs/', ''): value
        for key, value in historical_sources.items()
    }
    if not set(normalized_historical_sources).issubset(backend_hashes):
        raise ValueError('frozen backend source manifest is not a subset of the compiled backend file group')
    manifest = {
        'version': 'som.compiler-assets.v1',
        'purpose': 'som.rust-compiler.v1',
        'backend': backend,
        'files': {
            'blocks.json': file_hash(current_path),
            'legacy-blocks.json': file_hash(legacy_path),
            'quality-blocks.json': file_hash(quality_blocks_path),
            'quality.json': raw_hash(quality_config),
            'ruff': formatter_sha,
            'pyproject.toml': raw_hash(profile_bytes),
        },
    }
    packaged_manifest_raw = (package / 'manifest.json').read_bytes()
    packaged_manifest = json.loads(packaged_manifest_raw)
    package_manifest_matches = packaged_manifest == manifest and packaged_manifest_raw == (qa.canonical(manifest) + '\n').encode('utf-8')
    manifest_path_mismatches = []
    if isinstance(packaged_manifest, dict):
        manifest_path_mismatches = [key for key in sorted(set(packaged_manifest) | set(manifest))
                                     if packaged_manifest.get(key) != manifest.get(key)]

    common = {
        'grammar': descriptor('som.cnl.v1', grammar_path.read_bytes()),
        'compiler_contract': descriptor('som.compiler-contract.v1', contract_path.read_bytes()),
        'selection_policy': descriptor('som.compiler-selection-policy.v1.1', policy_path.read_bytes()),
        'backend': described('som.compiler-backend.v1', backend),
        'literal_policy': descriptor('som.compiler-literals.v1', literal_path.read_bytes()),
        'serializer': descriptor('som.canonical-json.v1', serializer_path.read_bytes()),
    }
    catalog_inputs = {
        'legacy': (legacy_catalog_path, 'som.compiler-catalog.legacy.v1'),
        'quality': (quality_catalog_path, 'som.compiler-catalog.quality.v1'),
        'flow': (flow_catalog_path, 'som.compiler-catalog.flow.v1'),
    }
    block_inputs = {
        'legacy': (legacy_path, 'legacy-blocks.json'),
        'quality': (quality_blocks_path, 'quality-blocks.json'),
        'flow': (current_path, 'blocks.json'),
    }
    catalog_descriptors: dict[str, dict[str, str]] = {}
    for kind, (catalog_path, identity) in catalog_inputs.items():
        catalog_bytes = catalog_path.read_bytes()
        catalog_manifest = {
            'version': identity,
            'catalog_sha256': raw_hash(catalog_bytes),
            'selection_policy_sha256': file_hash(policy_path),
        }
        catalog_descriptors[kind] = described(identity, catalog_manifest)
    block_descriptors: dict[str, dict[str, str]] = {}
    block_manifests: dict[str, Any] = {}
    for kind, (block_path, _package_name) in block_inputs.items():
        block_descriptors[kind], block_manifests[kind] = block_library_descriptor(block_path)
    render_only = described('som.compiler-render-only.v1', {
        'version': 'som.compiler-render-only.v1', 'formatter': None,
    })
    formatter_descriptor = described('som.compiler-formatter.v1', formatter_config)

    semantic_business = descriptor('related-business.v1', business_contract.read_bytes())
    semantic_flow = descriptor('related-flow.v1', flow_contract.read_bytes())
    by_version: dict[str, Any] = {}
    for version in range(1, 8):
        version_name = f'som.prompt.v{version}'
        catalog_kind = 'flow' if version == 7 else 'quality' if version == 6 else 'legacy'
        block_kind = catalog_kind
        formatted = version in (6, 7)
        semantics = []
        if version >= 5:
            semantics.append(semantic_business)
        if version == 7:
            semantics.append(semantic_flow)
        # Rust uses BTreeMap iteration, so semantic descriptors are key-sorted.
        semantics.sort(key=lambda item: item['id'])
        by_version[version_name] = {
            **common,
            'catalog': catalog_descriptors[catalog_kind],
            'block_library': block_descriptors[block_kind],
            'formatter': formatter_descriptor if formatted else render_only,
            'semantic_contracts': semantics,
        }

    index = {'version': 'som.compiler-asset-index.v1', 'by_prompt_version': by_version}
    provenance = {
        'version': 'som.compiler-asset-index-provenance.v1',
        'receipt_chain': freeze,
        'generator_sha256': file_hash(Path(__file__).resolve()),
        'package_manifest_sha256': raw_hash(packaged_manifest_raw),
        'package_manifest_matches_independent_sources': package_manifest_matches,
        'package_manifest_mismatch_paths': sorted(
            key for key in set(packaged_manifest.get('backend', {}).get('files', {})) | set(backend_hashes)
            if packaged_manifest.get('backend', {}).get('files', {}).get(key) != backend_hashes.get(key)
        ) if isinstance(packaged_manifest, dict) else ['manifest.json:invalid-json-object'],
        'package_manifest_mismatch_fields': manifest_path_mismatches,
        'package_files': package_check,
        'source_hashes': {
            'rust_compiler_source': file_hash(compiler_path),
            'legacy_blocks': file_hash(legacy_path),
            'quality_blocks': file_hash(quality_blocks_path),
            'flow_blocks': file_hash(current_path),
            'legacy_catalog': file_hash(legacy_catalog_path),
            'quality_catalog': file_hash(quality_catalog_path),
            'flow_catalog': file_hash(flow_catalog_path),
            'grammar': file_hash(grammar_path),
            'contract': file_hash(contract_path),
            'selection_policy': file_hash(policy_path),
            'serializer_policy': file_hash(serializer_path),
            'literal_policy': file_hash(literal_path),
            'related_business_contract': file_hash(business_contract),
            'related_flow_contract': file_hash(flow_contract),
            'quality_profile': file_hash(profile_path),
            'native_ruff': formatter_sha,
            'backend_source_map': backend_hashes,
            'embedded_backend_inputs': {
                'related_business_contract': file_hash(business_contract),
                'related_flow_contract': file_hash(flow_contract),
                'python_literal_encoding': file_hash(ROOT / 'som-rs/contracts/python-literal-encoding.v1.json'),
                'python_quality_profile': file_hash(profile_path),
                'composed_catalog': file_hash(ROOT / 'som-code-python/data/block-contracts/composed.json'),
                'composed_quality_catalog': file_hash(quality_catalog_path),
                'flow_catalog': file_hash(ROOT / 'som-code-python/data/block-contracts/flow-v7.json'),
                'compact_flow_catalog': file_hash(flow_catalog_path),
            },
            'historical_backend_source_manifest': file_hash(contract_dir / 'backend-source-manifest.json'),
        },
        'historical_backend_source_map': historical_sources,
        'roles_by_version': {name: sorted(value) for name, value in by_version.items()},
        'block_manifest_hashes': {name: canonical_hash(value) for name, value in block_manifests.items()},
    }
    return index, provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-assets', type=Path, required=True)
    parser.add_argument('--formatter', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=OUTPUT_ROOT / 'per-version-asset-index-final.json')
    parser.add_argument('--provenance', type=Path, default=OUTPUT_ROOT / 'per-version-asset-index-provenance-final.json')
    args = parser.parse_args()
    args.output = require_new_output(args.output, OUTPUT_ROOT)
    args.provenance = require_new_output(args.provenance, OUTPUT_ROOT)
    if args.output == args.provenance:
        raise ValueError('--output and --provenance must use different paths')
    index, provenance = build_asset_index(args.package_assets.resolve(), args.formatter.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.provenance.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'asset_index': str(args.output),
        'asset_index_sha256': file_hash(args.output),
        'provenance': str(args.provenance),
        'versions': len(index['by_prompt_version']),
        'roles_per_version': {name: len(value) for name, value in index['by_prompt_version'].items()},
        'package_manifest_matches_independent_sources': provenance['package_manifest_matches_independent_sources'],
        'package_manifest_mismatch_paths': provenance['package_manifest_mismatch_paths'],
        'freeze_chain_verified': provenance['receipt_chain']['status'],
    }, indent=2))


if __name__ == '__main__':
    main()
