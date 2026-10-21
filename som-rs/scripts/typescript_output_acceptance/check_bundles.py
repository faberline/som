#!/usr/bin/env python3
"""Check TypeScript phase 1 bundles and assemble refusals A1-A12 over HTTP (issue #99).

Sends compile and assemble requests to <url>/v1/compile and <url>/v1/assemble:
- bundle_checks, for every in-scope acceptance case in case-inventory.json except
  legacy_in_scope (public examples, version_positive fixtures, coverage cases, name cases):
  * CNL and typed JSON requests with target: "typescript" return byte-identical
    som.dsl-bundle.v2 bundles with target typescript (for a single-form case, the
    report records that only one form exists);
  * the bundle validates against bundle.schema.json, has at most 12 files and 64
    blocks per file (and 64 total blocks), and every block id starts with ts. and
    exists in catalog/typescript-catalog.v1.json;
  * every asset descriptor equals the candidate's TypeScript manifest
    (api.schema.json x-manifest), and the six shared descriptors equal the
    candidate's Rust manifest;
  * two compiles and two POST /v1/assemble calls of the same request give the
    same bytes;
  * the assembled files are exactly the product-layout.json set (11 files), and
    package.json, package-lock.json and tsconfig.json equal reference/.
- assemble_refusals: builds each recipe A1-A12 from the bases in acceptance-plan.json,
  rehashes with the frozen formulas unless the recipe says otherwise, and checks
  code, http_status, prompt_pointer, and line/column/byte span in the canonical
  assemble request body.
- Writes <out>/report.json, raw responses under <out>/responses/, and assembled
  products under <out>/products/<case-id>/ with <out>/products/manifest.json.

usage: check_bundles.py --url URL --out DIR
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
TS_TESTS = SOM_RS / "tests" / "typescript-output-v1"
if str(TS_TESTS) not in sys.path:
    sys.path.insert(0, str(TS_TESTS))

import build_case_inventory  # type: ignore[import-not-found]  # noqa: E402
import common  # type: ignore[import-not-found]  # noqa: E402

try:
    from jsonschema import Draft202012Validator  # type: ignore[import-not-found]
    from referencing import Registry, Resource  # type: ignore[import-not-found]

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

CONTRACT = common.CONTRACT
EXAMPLES = common.EXAMPLES
COVERAGE_CASES = common.COVERAGE_CASES
REPO = common.REPO
rust_cases = common.rust_cases

V1_CONTRACT = SOM_RS / "contracts" / "compiler-v1"
RUST_CONTRACT = SOM_RS / "contracts" / "rust-output-v1"
REFERENCE_DIR = CONTRACT / "reference"
CATALOG_PATH = CONTRACT / "catalog" / "typescript-catalog.v1.json"
RECEIPT_PATH = SOM_RS / "evidence" / "typescript-output-contract-amendment-001.json"

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SPAN_KEYS = ("line", "column", "end_line", "end_column", "byte_start", "byte_end")
TS_BUNDLE_VERSION = "som.dsl-bundle.v2"
TS_DSL_VERSION = "som.dsl.typescript.v1"
PY_BUNDLE_VERSION = "som.dsl-bundle.v1"

SHARED_ASSET_KEYS = (
    "grammar",
    "compiler_contract",
    "selection_policy",
    "literal_policy",
    "serializer",
    "semantic_contracts",
)
TS_SPECIFIC_ASSET_KEYS = (
    "target_contract",
    "catalog",
    "block_library",
    "backend",
    "formatter",
)
ALL_ASSET_KEYS = SHARED_ASSET_KEYS + TS_SPECIFIC_ASSET_KEYS

BACKEND_SOURCE_FILES = (
    "som-targets/Cargo.toml",
    "som-targets/src/lib.rs",
    "som-targets/src/main.rs",
    "som-targets/src/profile.rs",
    "som-targets/src/typescript/format.rs",
    "som-targets/src/typescript/mod.rs",
    "som-targets/src/typescript/profile.rs",
    "som-targets/src/typescript/render.rs",
    "som-targets/src/typescript/render/related.rs",
)

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MIGRATION_PATH_RE = re.compile(r"^migrations/0001_[A-Za-z_][A-Za-z0-9_]*\.sql$")

NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compact_json(value: Any) -> str:
    return rust_cases.compact(value)


def canonical_hash(value: Any) -> str:
    return sha_bytes(canonical_json(value).encode("utf-8"))


def rehash_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Recompute prompt_sha256, dsl_sha256, and payload_sha256 with the frozen formulas."""
    out = copy.deepcopy(bundle)
    out.pop("hashes", None)
    payload_obj = {k: out[k] for k in out if k != "hashes"}
    out["hashes"] = {
        "prompt_sha256": canonical_hash(out["prompt"]),
        "dsl_sha256": canonical_hash(out["dsl"]),
        "payload_sha256": canonical_hash(payload_obj),
    }
    return out


def rename_field_in_value(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {k: rename_field_in_value(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_field_in_value(item, old, new) for item in value]
    return new if value == old else value


def sanitize_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def validate_url(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        return f"invalid URL {url!r}: {exc}"
    if parsed.scheme not in ("http", "https"):
        return f"unsupported URL scheme in {url!r}; expected http or https"
    host = parsed.hostname
    if host not in LOOPBACK_HOSTS:
        return f"refusing non-loopback host {host!r} in {url!r}"
    try:
        port = parsed.port
    except ValueError as exc:
        return f"invalid port in {url!r}: {exc}"
    if port is None:
        return f"URL {url!r} must specify an explicit QA port"
    if port in FORBIDDEN_PORTS:
        return f"refusing reserved port {port} in {url!r}"
    return None


def resolve_pointer(value: Any, pointer: str) -> Any:
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def post_json(
    base_url: str,
    route: str,
    body: str,
    responses_dir: pathlib.Path,
    file_stem: str,
) -> tuple[int | None, bytes | None, str | None, str | None]:
    endpoint = base_url.rstrip("/") + route
    req = urllib.request.Request(
        endpoint,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with NO_PROXY_OPENER.open(req, timeout=120) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as err:
        try:
            status = err.code
            raw = err.read()
        finally:
            err.close()
    except Exception as exc:  # noqa: BLE001
        return None, None, None, f"connection error: {type(exc).__name__}: {exc}"

    rel_name = f"responses/{sanitize_filename(file_stem)}.json"
    out_path = responses_dir.parent / rel_name
    out_path.write_bytes(raw)
    return status, raw, rel_name, None


def compute_expected_ts_specific_descriptors() -> dict[str, dict[str, str]]:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    toolchain = json.loads((CONTRACT / "toolchain.json").read_text(encoding="utf-8"))
    catalog_bytes = CATALOG_PATH.read_bytes()
    block_lib_bytes = (
        (SOM_RS / "som-targets" / "src" / "typescript" / "render.rs").read_bytes()
        + (SOM_RS / "som-targets" / "src" / "typescript" / "render" / "related.rs").read_bytes()
    )
    backend_files_map = {
        rel: sha_bytes((SOM_RS / rel).read_bytes()) for rel in BACKEND_SOURCE_FILES
    }
    return {
        "target_contract": {
            "id": "som.typescript-output-contract.v1",
            "sha256": canonical_hash(receipt["files"]),
        },
        "catalog": {
            "id": "som.typescript-catalog.v1",
            "sha256": sha_bytes(catalog_bytes),
        },
        "block_library": {
            "id": "som.typescript-block-library.v1",
            "sha256": sha_bytes(block_lib_bytes),
        },
        "backend": {
            "id": "som.typescript-backend.v1",
            "sha256": canonical_hash(
                {"version": "som.typescript-backend.v1", "files": backend_files_map}
            ),
        },
        "formatter": {
            "id": toolchain["formatter"]["id"],
            "sha256": toolchain["formatter"]["sha256"],
        },
    }


def compute_expected_shared_descriptors(prompt: dict[str, Any]) -> dict[str, Any]:
    version = prompt.get("version", "")
    semantic: list[dict[str, str]] = []
    if version in ("som.prompt.v5", "som.prompt.v6", "som.prompt.v7"):
        semantic.append(
            {
                "id": "related-business.v1",
                "sha256": sha_bytes(
                    (SOM_RS / "contracts" / "related-business.v1.md").read_bytes()
                ),
            }
        )
    if version == "som.prompt.v7":
        semantic.append(
            {
                "id": "related-flow.v1",
                "sha256": sha_bytes(
                    (SOM_RS / "contracts" / "related-flow.v1.md").read_bytes()
                ),
            }
        )
    return {
        "grammar": {
            "id": "som.cnl.v1",
            "sha256": sha_bytes((V1_CONTRACT / "grammar.ebnf").read_bytes()),
        },
        "compiler_contract": {
            "id": "som.compiler-contract.v1",
            "sha256": sha_bytes((V1_CONTRACT / "CONTRACT.md").read_bytes()),
        },
        "selection_policy": {
            "id": "som.compiler-selection-policy.v1.2",
            "sha256": sha_bytes((V1_CONTRACT / "selection-policy.json").read_bytes()),
        },
        "literal_policy": {
            "id": "som.compiler-literals.v1",
            "sha256": sha_bytes((V1_CONTRACT / "literal-policy.json").read_bytes()),
        },
        "serializer": {
            "id": "som.canonical-json.v1",
            "sha256": sha_bytes((V1_CONTRACT / "serializer-policy.json").read_bytes()),
        },
        "semantic_contracts": semantic,
    }


def _build_jsonschema_validator() -> Any | None:
    if not HAS_JSONSCHEMA:
        return None
    resources = []
    for path, aliases in [
        (V1_CONTRACT / "api.schema.json", []),
        (V1_CONTRACT / "bundle.schema.json", ["bundle.schema.json"]),
        (V1_CONTRACT / "compiler-prompt.schema.json", ["compiler-prompt.schema.json"]),
        (RUST_CONTRACT / "api.schema.json", []),
        (RUST_CONTRACT / "bundle.schema.json", []),
        (CONTRACT / "api.schema.json", []),
        (CONTRACT / "bundle.schema.json", []),
    ]:
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        resources.append((schema["$id"], resource))
        resources.extend((alias, resource) for alias in aliases)
    reg = Registry().with_resources(resources)
    bundle_schema = json.loads((CONTRACT / "bundle.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(bundle_schema, registry=reg)


JSONSCHEMA_VALIDATOR = _build_jsonschema_validator()


def validate_bundle_structure(
    bundle: Any,
    fixed_layout_paths: set[str],
    catalog_block_ids: set[str],
) -> list[str]:
    """Validate bundle against bundle.schema.json, file/block limits, and catalog block ids."""
    problems: list[str] = []
    if JSONSCHEMA_VALIDATOR is not None:
        for err in JSONSCHEMA_VALIDATOR.iter_errors(bundle):
            problems.append(f"bundle.schema.json: {err.message[:200]}")

    if not isinstance(bundle, dict):
        return ["bundle is not a JSON object"]

    expected_top = {"version", "target", "prompt", "dsl", "assets", "hashes"}
    if set(bundle) != expected_top:
        problems.append(f"bundle keys {sorted(bundle)} != {sorted(expected_top)}")
    if bundle.get("version") != TS_BUNDLE_VERSION:
        problems.append(f"bundle.version {bundle.get('version')!r} != {TS_BUNDLE_VERSION!r}")
    if bundle.get("target") != "typescript":
        problems.append(f"bundle.target {bundle.get('target')!r} != 'typescript'")
    if not isinstance(bundle.get("prompt"), dict):
        problems.append("bundle.prompt is not an object")

    dsl = bundle.get("dsl")
    if not isinstance(dsl, dict):
        problems.append("bundle.dsl is not an object")
    else:
        if set(dsl) != {"version", "files"}:
            problems.append(f"bundle.dsl keys {sorted(dsl)} != ['files', 'version']")
        if dsl.get("version") != TS_DSL_VERSION:
            problems.append(f"bundle.dsl.version {dsl.get('version')!r} != {TS_DSL_VERSION!r}")
        files = dsl.get("files")
        if not isinstance(files, list) or not (1 <= len(files) <= 12):
            problems.append(
                f"bundle.dsl.files count {len(files) if isinstance(files, list) else type(files).__name__} outside 1..12"
            )
        else:
            total_blocks = 0
            for f_idx, file_entry in enumerate(files):
                if not isinstance(file_entry, dict) or set(file_entry) != {"path", "blocks"}:
                    problems.append(f"dsl.files[{f_idx}] invalid keys")
                    continue
                fpath = file_entry.get("path")
                if not isinstance(fpath, str) or (
                    fpath not in fixed_layout_paths and not MIGRATION_PATH_RE.match(fpath)
                ):
                    problems.append(f"dsl.files[{f_idx}].path {fpath!r} not in product-layout.json")
                blocks = file_entry.get("blocks")
                if not isinstance(blocks, list) or not (1 <= len(blocks) <= 64):
                    problems.append(
                        f"dsl.files[{f_idx}].blocks count {len(blocks) if isinstance(blocks, list) else type(blocks).__name__} outside 1..64"
                    )
                    continue
                total_blocks += len(blocks)
                for b_idx, block in enumerate(blocks):
                    if not isinstance(block, dict) or set(block) != {"id", "name", "params", "refs"}:
                        problems.append(f"dsl.files[{f_idx}].blocks[{b_idx}] invalid keys")
                        continue
                    bid = block.get("id")
                    if not isinstance(bid, str) or not bid.startswith("ts."):
                        problems.append(
                            f"block id {bid!r} at dsl.files[{f_idx}].blocks[{b_idx}] does not start with 'ts.'"
                        )
                    elif bid not in catalog_block_ids:
                        problems.append(
                            f"block id {bid!r} at dsl.files[{f_idx}].blocks[{b_idx}] not in typescript-catalog.v1.json"
                        )
                    bname = block.get("name")
                    if not isinstance(bname, str) or not IDENT_RE.match(bname):
                        problems.append(f"block name {bname!r} is not a valid identifier")
                    if not isinstance(block.get("params"), dict):
                        problems.append(f"block params at {bid!r} is not an object")
                    refs = block.get("refs")
                    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                        problems.append(f"block refs at {bid!r} is not a string array")
            if total_blocks > 64:
                problems.append(f"total blocks across files {total_blocks} > 64")

    assets = bundle.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(ALL_ASSET_KEYS):
        problems.append(
            f"bundle.assets keys {sorted(assets) if isinstance(assets, dict) else type(assets).__name__} != expected 11 keys"
        )
    else:
        for key in ALL_ASSET_KEYS:
            val = assets[key]
            if key == "semantic_contracts":
                if not isinstance(val, list):
                    problems.append("assets.semantic_contracts is not an array")
                else:
                    for idx, desc in enumerate(val):
                        if not _valid_descriptor(desc):
                            problems.append(f"assets.semantic_contracts[{idx}] invalid descriptor")
            else:
                if not _valid_descriptor(val):
                    problems.append(f"assets.{key} invalid descriptor")

    hashes = bundle.get("hashes")
    if not isinstance(hashes, dict) or set(hashes) != {
        "prompt_sha256",
        "dsl_sha256",
        "payload_sha256",
    }:
        problems.append("bundle.hashes missing required sha256 keys")
    else:
        for k, v in hashes.items():
            if not isinstance(v, str) or not HEX64_RE.match(v):
                problems.append(f"bundle.hashes.{k} is not 64 lowercase hex digits")
        if isinstance(bundle.get("prompt"), dict) and isinstance(dsl, dict) and isinstance(assets, dict):
            recomputed = rehash_bundle(bundle)["hashes"]
            for k in ("prompt_sha256", "dsl_sha256", "payload_sha256"):
                if hashes.get(k) != recomputed[k]:
                    problems.append(
                        f"bundle.hashes.{k} mismatch: got {hashes.get(k)}, expected {recomputed[k]}"
                    )
    return problems


def _valid_descriptor(desc: Any) -> bool:
    return (
        isinstance(desc, dict)
        and set(desc) == {"id", "sha256"}
        and isinstance(desc.get("id"), str)
        and bool(desc["id"])
        and isinstance(desc.get("sha256"), str)
        and bool(HEX64_RE.match(desc["sha256"]))
    )


def collect_in_scope_cases(inventory: dict[str, Any], target_examples_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect the 25 non-legacy in-scope acceptance cases in case-inventory.json."""
    cases: list[dict[str, Any]] = []
    examples_sources = inventory["sources"]["examples"]
    fixtures_sources = inventory["sources"]["cnl_positive_fixtures"]
    coverage_sources = inventory["sources"]["coverage_index"]

    # 1. public_examples
    for row in inventory["public_examples"]:
        if row["typescript"] != "in_scope":
            continue
        cid = row["id"]
        prompt_path = REPO / row["prompt"]
        cnl_path = REPO / row["cnl"]
        file_problems = _verify_sha(prompt_path, row["prompt_file_sha256"], f"{cid}:prompt")
        file_problems.extend(_verify_sha(cnl_path, row["cnl_sha256"], f"{cid}:cnl"))
        if row["prompt"] in examples_sources:
            file_problems.extend(
                _verify_sha(prompt_path, examples_sources[row["prompt"]], f"{cid}:sources.examples")
            )
        if row["cnl"] in examples_sources:
            file_problems.extend(
                _verify_sha(cnl_path, examples_sources[row["cnl"]], f"{cid}:sources.examples")
            )
        prompt_obj = json.loads(prompt_path.read_text(encoding="utf-8"))
        cnl_text = cnl_path.read_text(encoding="utf-8")
        cases.append(
            {
                "id": cid,
                "group": "public_examples",
                "prompt": prompt_obj,
                "requests": [
                    (
                        "json",
                        compact_json(
                            {
                                "format": "som.prompt-json.v1",
                                "prompt": prompt_obj,
                                "target": "typescript",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "typescript",
                            }
                        ),
                    ),
                ],
                "single_form": None,
                "file_problems": file_problems,
            }
        )

    # 2. version_positive_fixtures
    fixtures_rel = build_case_inventory.rel(build_case_inventory.FIXTURES)
    fixtures_file_problems = _verify_sha(
        build_case_inventory.FIXTURES,
        fixtures_sources.get(fixtures_rel, ""),
        "cnl_positive_fixtures",
    )
    fixtures_doc = json.loads(build_case_inventory.FIXTURES.read_text(encoding="utf-8"))
    fixtures_by_id = {c["id"]: c for c in fixtures_doc["cases"]}
    for row in inventory["version_positive_fixtures"]:
        if row["typescript"] != "in_scope":
            continue
        cid = row["id"]
        fix = fixtures_by_id[cid]
        prompt_obj = fix["expected_prompt"]
        cnl_text = fix["cnl"]
        file_problems = list(fixtures_file_problems)
        if canonical_hash(prompt_obj) != row["prompt_sha256"]:
            file_problems.append(f"prompt_sha256 mismatch for {cid}")
        if sha_bytes(cnl_text.encode("utf-8")) != row["cnl_sha256"]:
            file_problems.append(f"cnl_sha256 mismatch for {cid}")
        cases.append(
            {
                "id": cid,
                "group": "version_positive_fixtures",
                "prompt": prompt_obj,
                "requests": [
                    (
                        "json",
                        compact_json(
                            {
                                "format": "som.prompt-json.v1",
                                "prompt": prompt_obj,
                                "target": "typescript",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "typescript",
                            }
                        ),
                    ),
                ],
                "single_form": None,
                "file_problems": file_problems,
            }
        )

    # 3. coverage_cases
    cov_rel = build_case_inventory.rel(build_case_inventory.COVERAGE)
    cov_index_problems = _verify_sha(
        build_case_inventory.COVERAGE,
        coverage_sources.get(cov_rel, ""),
        "coverage_index",
    )
    cov_doc = json.loads(build_case_inventory.COVERAGE.read_text(encoding="utf-8"))
    cov_by_id = {c["id"]: c for c in cov_doc["cases"]}
    for row in inventory["coverage_cases"]:
        if row["typescript"] != "in_scope":
            continue
        cid = row["id"]
        cov = cov_by_id[cid]
        prompt_path = COVERAGE_CASES / cov["prompt"]
        cnl_path = COVERAGE_CASES / cov["cnl"]
        file_problems = list(cov_index_problems)
        file_problems.extend(_verify_sha(prompt_path, row["prompt_file_sha256"], f"{cid}:prompt"))
        file_problems.extend(_verify_sha(cnl_path, row["cnl_sha256"], f"{cid}:cnl"))
        prompt_obj = json.loads(prompt_path.read_text(encoding="utf-8"))
        cnl_text = cnl_path.read_text(encoding="utf-8")
        cases.append(
            {
                "id": cid,
                "group": "coverage_cases",
                "prompt": prompt_obj,
                "requests": [
                    (
                        "json",
                        compact_json(
                            {
                                "format": "som.prompt-json.v1",
                                "prompt": prompt_obj,
                                "target": "typescript",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "typescript",
                            }
                        ),
                    ),
                ],
                "single_form": None,
                "file_problems": file_problems,
            }
        )

    # 4. name_cases (only one form exists per name_case in target-examples.json)
    te_name_by_id = {c["id"]: c for c in target_examples_doc["name_cases"]}
    for row in inventory["name_cases"]:
        cid = row["id"]
        item = te_name_by_id[cid]
        form = item["form"]
        body = item["body"]
        file_problems = []
        if sha_bytes(body.encode("utf-8")) != row["body_sha256"]:
            file_problems.append(f"body_sha256 mismatch for {cid}")
        base_rel = f"som-rs/contracts/compiler-v1/examples/{row['base_example']}"
        if base_rel in examples_sources:
            file_problems.extend(
                _verify_sha(REPO / base_rel, examples_sources[base_rel], f"{cid}:base_example")
            )
        parsed_body = json.loads(body)
        prompt_obj = parsed_body.get("prompt")
        cases.append(
            {
                "id": cid,
                "group": "name_cases",
                "prompt": prompt_obj,
                "requests": [(form, body)],
                "single_form": f"only {form} form exists in target-examples.json name_cases",
                "file_problems": file_problems,
            }
        )

    return cases


def _verify_sha(path: pathlib.Path, expected_sha: str, label: str) -> list[str]:
    if not path.is_file():
        return [f"missing source file for {label}: {path}"]
    actual = build_case_inventory.sha(path)
    if actual != expected_sha:
        return [
            f"sha256 mismatch for {label} ({build_case_inventory.rel(path)}): got {actual}, expected {expected_sha}"
        ]
    return []


def build_refusal_recipes(
    base_ts_v1: dict[str, Any],
    base_ts_v5: dict[str, Any],
    base_rust_v1: dict[str, Any],
    base_py_v1: dict[str, Any],
    v6_prompt: dict[str, Any],
    v6_shared_assets: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build all 12 mutated bundles for assemble_refusals recipes A1-A12."""
    out: dict[str, dict[str, Any]] = {}

    # A1: Replace assets.formatter.sha256 with 64 zero digits.
    a1 = copy.deepcopy(base_ts_v1)
    a1["assets"]["formatter"]["sha256"] = "0" * 64
    out["A1"] = rehash_bundle(a1)

    # A2: Replace target_contract, catalog, block_library, backend and formatter with the Rust descriptors from the candidate's Rust manifest.
    a2 = copy.deepcopy(base_ts_v1)
    for key in TS_SPECIFIC_ASSET_KEYS:
        a2["assets"][key] = copy.deepcopy(base_rust_v1["assets"][key])
    out["A2"] = rehash_bundle(a2)

    # A3: Add target typescript to a valid som.dsl-bundle.v1 Python bundle.
    a3 = copy.deepcopy(base_py_v1)
    a3["target"] = "typescript"
    out["A3"] = rehash_bundle(a3)

    # A4: Set target to python in a TypeScript bundle.
    a4 = copy.deepcopy(base_ts_v1)
    a4["target"] = "python"
    out["A4"] = rehash_bundle(a4)

    # A5: Replace the first block id with the Python block id orm.base.
    a5 = copy.deepcopy(base_ts_v1)
    a5["dsl"]["files"][0]["blocks"][0]["id"] = "orm.base"
    out["A5"] = rehash_bundle(a5)

    # A6: Replace the prompt with the public v6 example prompt. Recompute the shared compiler-v1 descriptors for the v6 prompt, keep the TypeScript descriptors and the TypeScript DSL, and rehash.
    a6 = copy.deepcopy(base_ts_v5)
    a6["prompt"] = copy.deepcopy(v6_prompt)
    for key in SHARED_ASSET_KEYS:
        a6["assets"][key] = copy.deepcopy(v6_shared_assets[key])
    out["A6"] = rehash_bundle(a6)

    # A7: Change one hex digit of payload_sha256. Do not rehash.
    a7 = copy.deepcopy(base_ts_v1)
    old_payload = a7["hashes"]["payload_sha256"]
    flipped = ("1" if old_payload[0] == "0" else "0") + old_payload[1:]
    a7["hashes"]["payload_sha256"] = flipped
    out["A7"] = a7

    # A8: Rename the first prompt field to __proto__ in the prompt and in every DSL param that names it.
    a8 = copy.deepcopy(base_ts_v1)
    old_field = a8["prompt"]["resource"]["fields"][0]["name"]
    a8["prompt"] = rename_field_in_value(a8["prompt"], old_field, "__proto__")
    for f_entry in a8["dsl"]["files"]:
        for blk in f_entry["blocks"]:
            blk["params"] = rename_field_in_value(blk["params"], old_field, "__proto__")
    out["A8"] = rehash_bundle(a8)

    # A9: Add a 12th and a 13th file entry, each a copy of the last file.
    a9 = copy.deepcopy(base_ts_v1)
    last_file = copy.deepcopy(a9["dsl"]["files"][-1])
    while len(a9["dsl"]["files"]) < 13:
        a9["dsl"]["files"].append(copy.deepcopy(last_file))
    out["A9"] = rehash_bundle(a9)

    # A10: Set target to rust in a TypeScript bundle.
    a10 = copy.deepcopy(base_ts_v1)
    a10["target"] = "rust"
    out["A10"] = rehash_bundle(a10)

    # A11: Set target to typescript in a Rust bundle.
    a11 = copy.deepcopy(base_rust_v1)
    a11["target"] = "typescript"
    out["A11"] = rehash_bundle(a11)

    # A12: Replace the first block id with the first block id of the candidate's Rust bundle for the same prompt.
    a12 = copy.deepcopy(base_ts_v1)
    a12["dsl"]["files"][0]["blocks"][0]["id"] = copy.deepcopy(
        base_rust_v1["dsl"]["files"][0]["blocks"][0]["id"]
    )
    out["A12"] = rehash_bundle(a12)

    return out


def check_refusal_response(
    status: int,
    raw: bytes,
    body: str,
    recipe: dict[str, Any],
) -> list[str]:
    problems: list[str] = []
    exp_status = recipe["http_status"]
    exp_code = recipe["code"]
    exp_ptr = recipe["prompt_pointer"]

    if status != exp_status:
        problems.append(f"http_status {status} != expected {exp_status}")
    try:
        reply = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [*problems, f"invalid JSON response: {exc}"]
    if not isinstance(reply, dict):
        return [*problems, "response is not a JSON object"]
    if set(reply) != {"status", "code", "detail", "diagnostics"}:
        problems.append(f"unexpected reply keys {sorted(reply)}")
    if reply.get("status") != "error":
        problems.append(f"reply.status {reply.get('status')!r} != 'error'")
    if reply.get("code") != exp_code:
        problems.append(f"reply.code {reply.get('code')!r} != expected {exp_code!r}")

    diagnostics = reply.get("diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) != 1:
        problems.append(
            f"expected 1 diagnostic, got {len(diagnostics) if isinstance(diagnostics, list) else type(diagnostics).__name__}"
        )
        return problems
    diag = diagnostics[0]
    if not isinstance(diag, dict):
        return [*problems, "diagnostic is not an object"]
    if diag.get("code") != exp_code:
        problems.append(f"diagnostic.code {diag.get('code')!r} != expected {exp_code!r}")
    if diag.get("prompt_pointer") != exp_ptr:
        problems.append(
            f"diagnostic.prompt_pointer {diag.get('prompt_pointer')!r} != expected {exp_ptr!r}"
        )

    for key in SPAN_KEYS:
        if not isinstance(diag.get(key), int):
            problems.append(f"diagnostic missing integer {key}: {diag.get(key)!r}")
    if any(not isinstance(diag.get(key), int) for key in SPAN_KEYS):
        return problems

    spans = rust_cases.Spans(body)
    if exp_ptr not in spans.values:
        problems.append(f"pointer {exp_ptr!r} not found in canonical assemble request body")
        return problems
    expected_loc = rust_cases.location(body, *spans.values[exp_ptr])
    for key in SPAN_KEYS:
        if diag.get(key) != expected_loc[key]:
            problems.append(
                f"span {key}: got {diag.get(key)!r} != expected {expected_loc[key]!r}"
            )
    raw_bytes = body.encode("utf-8")
    b_start = diag["byte_start"]
    b_end = diag["byte_end"]
    if 0 <= b_start <= b_end <= len(raw_bytes):
        sliced = raw_bytes[b_start:b_end].decode("utf-8", errors="replace")
        expected_slice = canonical_json(resolve_pointer(json.loads(body), exp_ptr))
        if sliced != expected_slice:
            problems.append(f"span slice {sliced!r} != pointer target {expected_slice!r}")
    else:
        problems.append(f"byte span [{b_start}, {b_end}) out of range")
    return problems


def run_checks(base_url: str, out_dir: pathlib.Path) -> dict[str, Any]:
    responses_dir = out_dir / "responses"
    products_dir = out_dir / "products"
    responses_dir.mkdir(parents=True, exist_ok=False)
    products_dir.mkdir(parents=True, exist_ok=False)

    inventory = json.loads(build_case_inventory.OUT.read_text(encoding="utf-8"))
    target_examples_doc = json.loads(
        (CONTRACT / "target-examples.json").read_text(encoding="utf-8")
    )
    acceptance_plan_doc = json.loads(
        (CONTRACT / "acceptance-plan.json").read_text(encoding="utf-8")
    )
    layout_doc = json.loads(
        (CONTRACT / "product-layout.json").read_text(encoding="utf-8")
    )
    catalog_doc = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    fixed_layout_paths = {
        entry["path"]
        for entry in layout_doc["files"]
        if "<" not in entry["path"]
    }
    catalog_block_ids = {
        blk["id"]
        for f_entry in catalog_doc["files"]
        for blk in f_entry["blocks"]
    }
    reference_bytes = {
        name: (REFERENCE_DIR / name).read_bytes()
        for name in ("package.json", "package-lock.json", "tsconfig.json")
    }
    expected_ts_specific = compute_expected_ts_specific_descriptors()

    in_scope_cases = collect_in_scope_cases(inventory, target_examples_doc)
    rows: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []
    compiled_bundles_by_id: dict[str, dict[str, Any]] = {}
    rust_bundles_by_id: dict[str, dict[str, Any]] = {}

    # 1. bundle_checks across all 25 non-legacy in-scope cases
    for case in in_scope_cases:
        cid = case["id"]
        forms = [f for f, _ in case["requests"]]
        first_req_body = case["requests"][0][1]
        expected_summary = {
            "section": "bundle_checks",
            "forms": forms,
            "single_form": case["single_form"],
            "bundle_version": TS_BUNDLE_VERSION,
            "target": "typescript",
            "file_count": 11,
        }
        problems = list(case["file_problems"])

        compile_replies: list[tuple[str, int, bytes, str]] = []
        not_run_err: str | None = None
        for form, req_body in case["requests"]:
            for attempt in (1, 2):
                st, raw, rfile, err = post_json(
                    base_url,
                    "/v1/compile",
                    req_body,
                    responses_dir,
                    f"bundle_checks__{cid}__compile_{form}_{attempt}",
                )
                if err is not None:
                    not_run_err = err
                    break
                assert st is not None and raw is not None and rfile is not None
                compile_replies.append((f"{form}#{attempt}", st, raw, rfile))
            if not_run_err is not None:
                break

        if not_run_err is not None:
            rows.append(
                _make_row(
                    cid,
                    "bundle_checks",
                    first_req_body,
                    None,
                    None,
                    None,
                    expected_summary,
                    [],
                    not_run_error=not_run_err,
                    forms=forms,
                    single_form=case["single_form"],
                )
            )
            continue

        first_label, first_status, first_raw, first_rfile = compile_replies[0]
        if first_status != 200:
            problems.append(f"compile {first_label} returned HTTP {first_status} != 200")
            rows.append(
                _make_row(
                    cid,
                    "bundle_checks",
                    first_req_body,
                    first_status,
                    first_raw,
                    first_rfile,
                    expected_summary,
                    problems,
                    forms=forms,
                    single_form=case["single_form"],
                )
            )
            continue

        for label, st, raw, _ in compile_replies[1:]:
            if st != 200:
                problems.append(f"compile {label} returned HTTP {st} != 200")
            elif raw != first_raw:
                if label.split("#")[0] == first_label.split("#")[0]:
                    problems.append(
                        f"nondeterministic compile: {label} bytes differ from {first_label}"
                    )
                else:
                    problems.append(
                        f"CNL and JSON compile bytes differ ({label} vs {first_label})"
                    )

        try:
            compile_doc = json.loads(first_raw)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"invalid compile JSON: {exc}")
            rows.append(
                _make_row(
                    cid,
                    "bundle_checks",
                    first_req_body,
                    first_status,
                    first_raw,
                    first_rfile,
                    expected_summary,
                    problems,
                    forms=forms,
                    single_form=case["single_form"],
                )
            )
            continue

        if not isinstance(compile_doc, dict) or compile_doc.get("status") != "ok":
            problems.append(f"compile reply status {compile_doc.get('status') if isinstance(compile_doc, dict) else compile_doc!r} != 'ok'")
        bundle = compile_doc.get("bundle") if isinstance(compile_doc, dict) else None
        problems.extend(
            validate_bundle_structure(bundle, fixed_layout_paths, catalog_block_ids)
        )

        if isinstance(bundle, dict) and isinstance(bundle.get("prompt"), dict) and isinstance(bundle.get("assets"), dict):
            compiled_bundles_by_id[cid] = bundle
            prompt_obj = bundle["prompt"]
            expected_shared = compute_expected_shared_descriptors(prompt_obj)
            expected_manifest = {**expected_shared, **expected_ts_specific}
            for akey, expected_desc in expected_manifest.items():
                if bundle["assets"].get(akey) != expected_desc:
                    problems.append(
                        f"assets.{akey}: got {bundle['assets'].get(akey)!r} != expected TypeScript manifest {expected_desc!r}"
                    )

            # Query candidate's Rust compile for the same prompt to verify shared descriptors
            rust_req = compact_json(
                {
                    "format": "som.prompt-json.v1",
                    "prompt": prompt_obj,
                    "target": "rust",
                }
            )
            r_status, r_raw, _, r_err = post_json(
                base_url,
                "/v1/compile",
                rust_req,
                responses_dir,
                f"bundle_checks__{cid}__rust_manifest",
            )
            if r_err is not None:
                rows.append(
                    _make_row(
                        cid,
                        "bundle_checks",
                        first_req_body,
                        None,
                        None,
                        None,
                        expected_summary,
                        [],
                        not_run_error=r_err,
                        forms=forms,
                        single_form=case["single_form"],
                    )
                )
                continue
            assert r_status is not None and r_raw is not None
            if r_status != 200:
                problems.append(f"Rust manifest compile returned HTTP {r_status} != 200")
            else:
                try:
                    r_doc = json.loads(r_raw)
                    r_bundle = r_doc["bundle"]
                    rust_bundles_by_id[cid] = r_bundle
                    for skey in SHARED_ASSET_KEYS:
                        if bundle["assets"].get(skey) != r_bundle["assets"].get(skey):
                            problems.append(
                                f"shared descriptor {skey} differs from candidate Rust manifest"
                            )
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"invalid Rust compile JSON: {exc}")

            # Two POST /v1/assemble calls
            assemble_body = canonical_json({"bundle": bundle})
            a1_status, a1_raw, a1_file, a1_err = post_json(
                base_url,
                "/v1/assemble",
                assemble_body,
                responses_dir,
                f"bundle_checks__{cid}__assemble_1",
            )
            if a1_err is not None:
                rows.append(
                    _make_row(
                        cid,
                        "bundle_checks",
                        first_req_body,
                        None,
                        None,
                        None,
                        expected_summary,
                        [],
                        not_run_error=a1_err,
                        forms=forms,
                        single_form=case["single_form"],
                    )
                )
                continue
            a2_status, a2_raw, _, a2_err = post_json(
                base_url,
                "/v1/assemble",
                assemble_body,
                responses_dir,
                f"bundle_checks__{cid}__assemble_2",
            )
            if a2_err is not None:
                rows.append(
                    _make_row(
                        cid,
                        "bundle_checks",
                        first_req_body,
                        None,
                        None,
                        None,
                        expected_summary,
                        [],
                        not_run_error=a2_err,
                        forms=forms,
                        single_form=case["single_form"],
                    )
                )
                continue
            assert a1_status is not None and a1_raw is not None and a1_file is not None
            assert a2_status is not None and a2_raw is not None
            if a1_status != 200:
                problems.append(f"assemble #1 returned HTTP {a1_status} != 200")
            if a2_status != 200:
                problems.append(f"assemble #2 returned HTTP {a2_status} != 200")
            if (a1_status, a1_raw) != (a2_status, a2_raw):
                problems.append("nondeterministic assemble: two POST /v1/assemble responses differ in bytes")

            if a1_status == 200:
                try:
                    assembled_doc = json.loads(a1_raw)
                    files_map = assembled_doc["files"]
                    if not isinstance(files_map, dict):
                        problems.append("assemble response files is not an object")
                    else:
                        revision = prompt_obj.get("migration", {}).get("revision", "")
                        expected_migration = f"migrations/0001_{revision}.sql"
                        expected_file_set = set(fixed_layout_paths) | {expected_migration}
                        if set(files_map) != expected_file_set or len(files_map) != 11:
                            problems.append(
                                f"assembled file set {sorted(files_map)} != expected 11 product-layout.json files {sorted(expected_file_set)}"
                            )
                        for ref_name, ref_content in reference_bytes.items():
                            actual_content = files_map.get(ref_name)
                            if not isinstance(actual_content, str) or actual_content.encode("utf-8") != ref_content:
                                problems.append(
                                    f"assembled {ref_name} bytes differ from reference/{ref_name}"
                                )

                        # Write assembled product to <out>/products/<case-id>/
                        case_product_dir = products_dir / cid
                        case_product_dir.mkdir(parents=True, exist_ok=True)
                        for rel_path, content in files_map.items():
                            if isinstance(content, str):
                                target_file = case_product_dir / rel_path
                                target_file.parent.mkdir(parents=True, exist_ok=True)
                                target_file.write_text(content, encoding="utf-8")
                        manifest_entries.append(
                            {
                                "id": cid,
                                "product": str(case_product_dir.resolve()),
                                "prompt": prompt_obj,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"invalid assemble JSON: {exc}")

            rows.append(
                _make_row(
                    cid,
                    "bundle_checks",
                    first_req_body,
                    a1_status,
                    a1_raw,
                    a1_file,
                    expected_summary,
                    problems,
                    forms=forms,
                    single_form=case["single_form"],
                )
            )
        else:
            rows.append(
                _make_row(
                    cid,
                    "bundle_checks",
                    first_req_body,
                    first_status,
                    first_raw,
                    first_rfile,
                    expected_summary,
                    problems,
                    forms=forms,
                    single_form=case["single_form"],
                )
            )

    (products_dir / "manifest.json").write_text(
        json.dumps({"products": manifest_entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 2. assemble_refusals: A1-A12
    v1_prompt = json.loads((EXAMPLES / "v1.prompt.json").read_text(encoding="utf-8"))
    v6_prompt = json.loads((EXAMPLES / "v6.prompt.json").read_text(encoding="utf-8"))

    # Compile Python example-v1 for A3 and Python example-v6 for A6 shared descriptors
    py_v1_req = compact_json({"format": "som.prompt-json.v1", "prompt": v1_prompt})
    py_v1_status, py_v1_raw, _, py_v1_err = post_json(
        base_url, "/v1/compile", py_v1_req, responses_dir, "assemble_refusals__base_python_v1"
    )
    py_v6_req = compact_json({"format": "som.prompt-json.v1", "prompt": v6_prompt})
    py_v6_status, py_v6_raw, _, py_v6_err = post_json(
        base_url, "/v1/compile", py_v6_req, responses_dir, "assemble_refusals__base_python_v6"
    )

    base_ts_v1 = compiled_bundles_by_id.get("example-v1")
    base_ts_v5 = compiled_bundles_by_id.get("example-v5")
    base_rust_v1 = rust_bundles_by_id.get("example-v1")
    base_py_v1 = (
        json.loads(py_v1_raw)["bundle"]
        if (py_v1_err is None and py_v1_status == 200 and py_v1_raw is not None)
        else None
    )
    v6_shared_assets = (
        json.loads(py_v6_raw)["bundle"]["assets"]
        if (py_v6_err is None and py_v6_status == 200 and py_v6_raw is not None)
        else compute_expected_shared_descriptors(v6_prompt)
    )

    recipes_list: list[dict[str, Any]] = acceptance_plan_doc["assemble_refusals"]["recipes"]
    if (
        base_ts_v1 is None
        or base_ts_v5 is None
        or base_rust_v1 is None
        or base_py_v1 is None
    ):
        missing_err = (
            py_v1_err
            or py_v6_err
            or "cannot build A1-A12 recipes because base example-v1 / example-v5 bundles failed to compile"
        )
        for recipe in recipes_list:
            rid = recipe["id"]
            rows.append(
                _make_row(
                    rid,
                    "assemble_refusals",
                    "",
                    None,
                    None,
                    None,
                    {
                        "http_status": recipe["http_status"],
                        "code": recipe["code"],
                        "prompt_pointer": recipe["prompt_pointer"],
                        "change": recipe["change"],
                    },
                    [],
                    not_run_error=missing_err,
                )
            )
    else:
        built_recipes = build_refusal_recipes(
            base_ts_v1,
            base_ts_v5,
            base_rust_v1,
            base_py_v1,
            v6_prompt,
            v6_shared_assets,
        )
        for recipe in recipes_list:
            rid = recipe["id"]
            mutated_bundle = built_recipes[rid]
            req_body = canonical_json({"bundle": mutated_bundle})
            expected_desc = {
                "http_status": recipe["http_status"],
                "code": recipe["code"],
                "prompt_pointer": recipe["prompt_pointer"],
                "change": recipe["change"],
            }
            st, raw, rfile, err = post_json(
                base_url,
                "/v1/assemble",
                req_body,
                responses_dir,
                f"assemble_refusals__{rid}",
            )
            if err is not None:
                rows.append(
                    _make_row(
                        rid,
                        "assemble_refusals",
                        req_body,
                        None,
                        None,
                        None,
                        expected_desc,
                        [],
                        not_run_error=err,
                    )
                )
                continue
            assert st is not None and raw is not None and rfile is not None
            r_problems = check_refusal_response(st, raw, req_body, recipe)
            rows.append(
                _make_row(
                    rid,
                    "assemble_refusals",
                    req_body,
                    st,
                    raw,
                    rfile,
                    expected_desc,
                    r_problems,
                )
            )

    all_passed = bool(rows) and all(r["passed"] for r in rows)
    report = {
        "version": "som.typescript-output-bundle-checks.v1",
        "issue": 99,
        "url": base_url,
        "legacy_in_scope": (
            "out_of_scope: legacy_in_scope jobs are out of scope for check_bundles.py"
        ),
        "manifest": str((products_dir / "manifest.json").relative_to(out_dir)),
        "status": "passed" if all_passed else "failed",
        "passed": all_passed,
        "total": len(rows),
        "passed_count": sum(1 for r in rows if r["passed"]),
        "failed_count": sum(1 for r in rows if not r["passed"]),
        "not_run_count": sum(1 for r in rows if r["status"] == "not_run"),
        "cases": rows,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def _make_row(
    case_id: str,
    section: str,
    request_body: str,
    http_status: int | None,
    raw_response: bytes | None,
    response_file: str | None,
    expected: Any,
    problems: list[str],
    not_run_error: str | None = None,
    forms: list[str] | None = None,
    single_form: str | None = None,
) -> dict[str, Any]:
    req_sha = sha_bytes(request_body.encode("utf-8"))
    extra: dict[str, Any] = {}
    if forms is not None:
        extra["forms"] = forms
        extra["single_form"] = single_form
    if not_run_error is not None:
        return {
            "id": case_id,
            "section": section,
            "status": "not_run",
            **extra,
            "request_sha256": req_sha,
            "http_status": None,
            "response_sha256": None,
            "response_file": None,
            "expected": expected,
            "passed": False,
            "reason": not_run_error,
        }
    passed = len(problems) == 0
    return {
        "id": case_id,
        "section": section,
        "status": "passed" if passed else "failed",
        **extra,
        "request_sha256": req_sha,
        "http_status": http_status,
        "response_sha256": sha_bytes(raw_response) if raw_response is not None else None,
        "response_file": response_file,
        "expected": expected,
        "passed": passed,
        "reason": "ok" if passed else "; ".join(problems),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="Loopback URL of the candidate som server")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        required=True,
        help="New output directory for report.json, responses/, and products/",
    )
    args = parser.parse_args(argv)

    url_err = validate_url(args.url)
    if url_err is not None:
        print(f"refused: {url_err}", file=sys.stderr)
        return 2

    if args.out.exists():
        print(f"refused: output directory already exists: {args.out}", file=sys.stderr)
        return 2

    report = run_checks(args.url, args.out)
    print(
        json.dumps(
            {
                "status": report["status"],
                "passed": report["passed"],
                "total": report["total"],
                "passed_count": report["passed_count"],
                "failed_count": report["failed_count"],
                "not_run_count": report["not_run_count"],
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
