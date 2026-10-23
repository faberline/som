#!/usr/bin/env python3
"""Check React phase 1 bundles and assemble refusals A1-A16 over HTTP (issue #129).

react-output-v1 acceptance-plan.json bundle_checks and assemble_refusals.
Ported from som-rs/scripts/typescript_output_acceptance/check_bundles.py.

Sends compile and assemble requests to <url>/v1/compile and <url>/v1/assemble:
- bundle_checks, for every acceptance case in case-inventory.json (in-scope
  public examples, version positive fixtures, coverage cases, name cases,
  in-scope public products and in-scope legacy jobs):
  * CNL and typed JSON requests with target: "react" return byte-identical
    som.dsl-bundle.v2 bundles with target react (for a single-form case, the
    report records that only one form exists);
  * the bundle validates against bundle.schema.json, has at most 19 files and
    1-64 blocks per file (and at most 64 blocks in total), and every block id
    starts with react. and exists in catalog/react-catalog.v1.json;
  * every asset descriptor equals the candidate's React manifest
    (api.schema.json x-manifest), and the six shared descriptors equal the
    candidate's Rust manifest;
  * two compiles and two POST /v1/assemble calls of the same request give the
    same bytes;
  * the assembled files are exactly the product-layout.json set (19 files); the
    17 fixed files equal reference/, and src/schema.ts and README.md equal
    tests/react-output-v1/render_reference.py for the prompt.
- assemble_refusals: builds each recipe A1-A16 from the bases in acceptance-plan.json,
  rehashes with the frozen formulas unless the recipe says otherwise, and checks
  code, http_status, prompt_pointer, and line/column/byte span in the canonical
  assemble request body.
- Writes <out>/report.json, raw responses under <out>/responses/, and assembled
  products under <out>/products/<case-id>/ with <out>/products/manifest.json. Each
  manifest entry also records python_request: the case's compile request without
  target, which gives the Python service the React product calls.

usage: check_bundles.py --url URL --out DIR [--legacy-run DIR]
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
REACT_TESTS = SOM_RS / "tests" / "react-output-v1"
TS_ACCEPTANCE = SOM_RS / "scripts" / "typescript_output_acceptance"
if str(TS_ACCEPTANCE) not in sys.path:
    sys.path.append(str(TS_ACCEPTANCE))
if str(REACT_TESTS) not in sys.path:
    sys.path.insert(0, str(REACT_TESTS))

import build_case_inventory  # type: ignore[import-not-found]  # noqa: E402
import check_python_baseline  # type: ignore[import-not-found]  # noqa: E402
import common  # type: ignore[import-not-found]  # noqa: E402
import render_reference  # type: ignore[import-not-found]  # noqa: E402

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
CATALOG_PATH = CONTRACT / "catalog" / "react-catalog.v1.json"
RECEIPT_PATH = SOM_RS / "evidence" / "react-output-contract-amendment-002.json"

DEFAULT_LEGACY_RUN = check_python_baseline.DEFAULT_LEGACY_RUN
EXPECTED_LEGACY_IN_SCOPE = 100
EXPECTED_MANIFEST_PRODUCTS = 129

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SPAN_KEYS = ("line", "column", "end_line", "end_column", "byte_start", "byte_end")
REACT_BUNDLE_VERSION = "som.dsl-bundle.v2"
REACT_DSL_VERSION = "som.dsl.react.v1"
PY_BUNDLE_VERSION = "som.dsl-bundle.v1"

SHARED_ASSET_KEYS = (
    "grammar",
    "compiler_contract",
    "selection_policy",
    "literal_policy",
    "serializer",
    "semantic_contracts",
)
REACT_SPECIFIC_ASSET_KEYS = (
    "target_contract",
    "catalog",
    "block_library",
    "backend",
    "formatter",
)
ALL_ASSET_KEYS = SHARED_ASSET_KEYS + REACT_SPECIFIC_ASSET_KEYS

BACKEND_SOURCE_FILES = (
    "som-targets/Cargo.toml",
    "som-targets/src/lib.rs",
    "som-targets/src/main.rs",
    "som-targets/src/profile.rs",
    "som-targets/src/react/mod.rs",
    "som-targets/src/react/profile.rs",
    "som-targets/src/react/render.rs",
)
MAX_FILES = 19
MAX_BLOCKS = 64
FILE_COUNT = 19
FIXED_FILES = (
    "index.html",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
    "src/main.tsx",
    "src/App.tsx",
    "src/api.ts",
    "src/spec.ts",
    "src/form.ts",
    "src/styles.css",
    "src/pages/ListPage.tsx",
    "src/pages/DetailPage.tsx",
    "src/pages/FormPage.tsx",
    "src/pages/ErrorView.tsx",
    "src/app.test.tsx",
    "src/form.test.ts",
)
GENERATED_FILES = ("src/schema.ts", "README.md")

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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


def compute_expected_react_specific_descriptors() -> dict[str, dict[str, str]]:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    toolchain = json.loads((CONTRACT / "toolchain.json").read_text(encoding="utf-8"))
    catalog_bytes = CATALOG_PATH.read_bytes()
    block_lib_bytes = (SOM_RS / "som-targets" / "src" / "react" / "render.rs").read_bytes() + b"".join(
        (REFERENCE_DIR / name).read_bytes() for name in FIXED_FILES
    )
    backend_files_map = {
        rel: sha_bytes((SOM_RS / rel).read_bytes()) for rel in BACKEND_SOURCE_FILES
    }
    return {
        "target_contract": {
            "id": "som.react-output-contract.v1",
            "sha256": canonical_hash(receipt["files"]),
        },
        "catalog": {
            "id": "som.react-catalog.v1",
            "sha256": sha_bytes(catalog_bytes),
        },
        "block_library": {
            "id": "som.react-block-library.v1",
            "sha256": sha_bytes(block_lib_bytes),
        },
        "backend": {
            "id": "som.react-backend.v1",
            "sha256": canonical_hash(
                {"version": "som.react-backend.v1", "files": backend_files_map}
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
    if bundle.get("version") != REACT_BUNDLE_VERSION:
        problems.append(f"bundle.version {bundle.get('version')!r} != {REACT_BUNDLE_VERSION!r}")
    if bundle.get("target") != "react":
        problems.append(f"bundle.target {bundle.get('target')!r} != 'react'")
    if not isinstance(bundle.get("prompt"), dict):
        problems.append("bundle.prompt is not an object")

    dsl = bundle.get("dsl")
    if not isinstance(dsl, dict):
        problems.append("bundle.dsl is not an object")
    else:
        if set(dsl) != {"version", "files"}:
            problems.append(f"bundle.dsl keys {sorted(dsl)} != ['files', 'version']")
        if dsl.get("version") != REACT_DSL_VERSION:
            problems.append(f"bundle.dsl.version {dsl.get('version')!r} != {REACT_DSL_VERSION!r}")
        files = dsl.get("files")
        if not isinstance(files, list) or not (1 <= len(files) <= MAX_FILES):
            problems.append(
                f"bundle.dsl.files count {len(files) if isinstance(files, list) else type(files).__name__} outside 1..{MAX_FILES}"
            )
        else:
            total_blocks = 0
            for f_idx, file_entry in enumerate(files):
                if not isinstance(file_entry, dict) or set(file_entry) != {"path", "blocks"}:
                    problems.append(f"dsl.files[{f_idx}] invalid keys")
                    continue
                fpath = file_entry.get("path")
                if not isinstance(fpath, str) or (
                    fpath not in fixed_layout_paths
                ):
                    problems.append(f"dsl.files[{f_idx}].path {fpath!r} not in product-layout.json")
                blocks = file_entry.get("blocks")
                if not isinstance(blocks, list) or not (1 <= len(blocks) <= MAX_BLOCKS):
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
                    if not isinstance(bid, str) or not bid.startswith("react."):
                        problems.append(
                            f"block id {bid!r} at dsl.files[{f_idx}].blocks[{b_idx}] does not start with 'react.'"
                        )
                    elif bid not in catalog_block_ids:
                        problems.append(
                            f"block id {bid!r} at dsl.files[{f_idx}].blocks[{b_idx}] not in react-catalog.v1.json"
                        )
                    bname = block.get("name")
                    if not isinstance(bname, str) or not IDENT_RE.match(bname):
                        problems.append(f"block name {bname!r} is not a valid identifier")
                    if not isinstance(block.get("params"), dict):
                        problems.append(f"block params at {bid!r} is not an object")
                    refs = block.get("refs")
                    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                        problems.append(f"block refs at {bid!r} is not a string array")
            if total_blocks > MAX_BLOCKS:
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
    """Collect the 29 non-legacy acceptance cases in case-inventory.json."""
    cases: list[dict[str, Any]] = []
    examples_sources = inventory["sources"]["examples"]
    fixtures_sources = inventory["sources"]["cnl_positive_fixtures"]
    coverage_sources = inventory["sources"]["coverage_index"]

    # 1. public_examples
    for row in inventory["public_examples"]:
        if row["react"] != "in_scope":
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
                                "target": "react",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "react",
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
        if row["react"] != "in_scope":
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
                                "target": "react",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "react",
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
        if row["react"] != "in_scope":
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
                                "target": "react",
                            }
                        ),
                    ),
                    (
                        "cnl",
                        compact_json(
                            {
                                "format": "som.cnl.v1",
                                "source": cnl_text,
                                "target": "react",
                            }
                        ),
                    ),
                ],
                "single_form": None,
                "file_problems": file_problems,
            }
        )

    # 4. name_cases (CNL-form cases also compile their bundle.prompt as som.prompt-json.v1)
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
        derive_json = form == "cnl"
        cases.append(
            {
                "id": cid,
                "group": "name_cases",
                "prompt": prompt_obj,
                "requests": [(form, body)],
                "derive_json_from_cnl_bundle": derive_json,
                "single_form": (
                    None
                    if derive_json
                    else f"only {form} form exists in target-examples.json name_cases"
                ),
                "file_problems": file_problems,
            }
        )

    # 5. public_products_in_scope (prompt.json, and source.cnl when the product has one)
    products_root = REPO / inventory["sources"]["public_products"]["directory"]
    for row in inventory["public_products_in_scope"]:
        cid = row["id"]
        case_dir = products_root / row["index"]
        prompt_path = case_dir / "prompt.json"
        cnl_path = case_dir / "source.cnl"
        file_problems = _verify_sha(prompt_path, row["prompt_file_sha256"], f"{cid}:prompt")
        prompt_obj = json.loads(prompt_path.read_text(encoding="utf-8"))
        if prompt_obj.get("version") != row["prompt_version"]:
            file_problems.append(f"prompt_version mismatch for {cid}")
        requests = [
            (
                "json",
                compact_json(
                    {
                        "format": "som.prompt-json.v1",
                        "prompt": prompt_obj,
                        "target": "react",
                    }
                ),
            )
        ]
        if cnl_path.is_file():
            requests.append(
                (
                    "cnl",
                    compact_json(
                        {
                            "format": "som.cnl.v1",
                            "source": cnl_path.read_text(encoding="utf-8"),
                            "target": "react",
                        }
                    ),
                )
            )
        cases.append(
            {
                "id": cid,
                "group": "public_products_in_scope",
                "prompt": prompt_obj,
                "requests": requests,
                "single_form": (
                    None if cnl_path.is_file() else "only json form exists for this public product"
                ),
                "file_problems": file_problems,
            }
        )

    return cases


def collect_legacy_in_scope_cases(
    inventory: dict[str, Any],
    legacy_run: pathlib.Path,
    expected_legacy_in_scope: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Select the 100 legacy_in_scope jobs by inventory_index and prompt_sha256."""
    legacy_problems: list[str] = []
    in_scope_list: list[dict[str, Any]] = inventory.get("legacy_in_scope", [])
    in_scope_by_key: dict[tuple[int, str], dict[str, Any]] = {
        (int(entry["inventory_index"]), str(entry["prompt_sha256"])): entry
        for entry in in_scope_list
    }

    try:
        result_path = legacy_run / "result.json"
        result_doc = json.loads(result_path.read_text(encoding="utf-8"))
        records = result_doc.get("records")
        if not isinstance(records, list):
            raise ValueError(f"{result_path} missing 'records' list")
        legacy_inputs, _ = check_python_baseline.read_legacy_inputs(legacy_run)
    except (OSError, ValueError) as err:
        err_msg = f"cannot read legacy run {legacy_run}: {type(err).__name__}: {err}"
        legacy_problems.append(err_msg)
        not_run_cases = [
            {
                "id": str(entry["id"]),
                "group": "legacy_in_scope",
                "inventory_index": int(entry["inventory_index"]),
                "prompt": None,
                "requests": [],
                "derive_json_from_cnl_bundle": False,
                "single_form": None,
                "file_problems": [],
                "not_run_error": err_msg,
            }
            for entry in in_scope_list
        ]
        return not_run_cases, legacy_problems

    selected_cases: list[dict[str, Any]] = []
    for item in legacy_inputs:
        idx = int(item["index"])
        rec_sha = str(item.get("prompt_sha256") or "")
        if (
            not rec_sha
            and isinstance(item.get("body"), dict)
            and isinstance(item["body"].get("prompt"), dict)
        ):
            rec_sha = canonical_hash(item["body"]["prompt"])

        inv_entry = in_scope_by_key.get((idx, rec_sha))
        if inv_entry is None:
            continue

        cid = str(inv_entry["id"])
        if item.get("not_run_reason") is not None or item.get("body") is None:
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": (
                        item.get("not_run_reason")
                        or f"missing compile request for {cid}"
                    ),
                }
            )
            continue

        compile_dir = pathlib.Path(item["directory"])
        record = (
            records[idx]
            if 0 <= idx < len(records) and isinstance(records[idx], dict)
            else {}
        )
        cnl_sha256 = record.get("cnl_sha256")
        if not isinstance(cnl_sha256, str) or not cnl_sha256.strip():
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": (
                        f"missing or invalid cnl_sha256 in result.json records[{idx}] for {cid}"
                    ),
                }
            )
            continue

        cnl_req_path = compile_dir / "compile-cnl.request.json"
        if not cnl_req_path.is_file():
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": f"missing compile-cnl.request.json for {cid}: {cnl_req_path}",
                }
            )
            continue

        try:
            cnl_req = json.loads(cnl_req_path.read_text(encoding="utf-8"))
            if not isinstance(cnl_req, dict) or not isinstance(
                cnl_req.get("source"), str
            ):
                raise ValueError(
                    f"{cnl_req_path} must be a JSON object with a string 'source'"
                )
        except (OSError, UnicodeDecodeError, ValueError) as err:
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": (
                        f"unreadable compile-cnl.request.json for {cid}: {type(err).__name__}: {err}"
                    ),
                }
            )
            continue

        source_cnl_path = compile_dir / "source.cnl"
        if not source_cnl_path.is_file():
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": f"missing source.cnl for {cid}: {source_cnl_path}",
                }
            )
            continue

        try:
            source_cnl_bytes = source_cnl_path.read_bytes()
        except OSError as err:
            selected_cases.append(
                {
                    "id": cid,
                    "group": "legacy_in_scope",
                    "inventory_index": idx,
                    "prompt": None,
                    "requests": [],
                    "derive_json_from_cnl_bundle": False,
                    "single_form": None,
                    "file_problems": [],
                    "not_run_error": (
                        f"unreadable source.cnl for {cid}: {type(err).__name__}: {err}"
                    ),
                }
            )
            continue

        req_obj = item["body"]
        file_problems: list[str] = []
        if "target" in req_obj:
            file_problems.append(
                f"recorded compile request for {cid} already contains a 'target' key ({req_obj['target']!r})"
            )
        prompt_obj = req_obj.get("prompt")
        if not isinstance(prompt_obj, dict):
            file_problems.append(f"recorded compile request for {cid} missing 'prompt' object")
            prompt_obj = {}
        else:
            canon_sha = canonical_hash(prompt_obj)
            compact_sha = sha_bytes(compact_json(prompt_obj).encode("utf-8"))
            if inv_entry["prompt_sha256"] not in (canon_sha, compact_sha):
                file_problems.append(
                    f"prompt_sha256 mismatch for {cid}: got {canon_sha}, expected {inv_entry['prompt_sha256']}"
                )

        if cnl_req.get("format") != "som.cnl.v1":
            file_problems.append(
                f"recorded CNL compile request for {cid} has format {cnl_req.get('format')!r} != 'som.cnl.v1'"
            )
        if "target" in cnl_req:
            file_problems.append(
                f"recorded CNL compile request for {cid} already contains a 'target' key ({cnl_req['target']!r})"
            )
        cnl_source_bytes = cnl_req["source"].encode("utf-8")
        actual_cnl_sha = sha_bytes(cnl_source_bytes)
        if actual_cnl_sha != cnl_sha256:
            file_problems.append(
                f"cnl_sha256 mismatch for {cid}: got {actual_cnl_sha}, expected {cnl_sha256}"
            )
        if source_cnl_bytes != cnl_source_bytes:
            file_problems.append(
                f"source.cnl bytes differ from compile-cnl.request.json source for {cid}"
            )

        react_req_body = compact_json({**req_obj, "target": "react"})
        react_cnl_req_body = compact_json({**cnl_req, "target": "react"})
        selected_cases.append(
            {
                "id": cid,
                "group": "legacy_in_scope",
                "inventory_index": idx,
                "prompt": prompt_obj,
                "requests": [
                    ("json", react_req_body),
                    ("cnl", react_cnl_req_body),
                ],
                "derive_json_from_cnl_bundle": False,
                "single_form": None,
                "file_problems": file_problems,
                "not_run_error": None,
            }
        )

    if len(in_scope_list) != expected_legacy_in_scope:
        legacy_problems.append(
            f"case-inventory.json legacy_in_scope count is {len(in_scope_list)}, expected {expected_legacy_in_scope}"
        )
    if len(selected_cases) != expected_legacy_in_scope:
        legacy_problems.append(
            f"selected legacy_in_scope job count is {len(selected_cases)}, expected {expected_legacy_in_scope}"
        )

    return selected_cases, legacy_problems


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
    base_react_v1: dict[str, Any],
    base_ts_v1: dict[str, Any],
    base_rust_v1: dict[str, Any],
    base_py_v1: dict[str, Any],
    v5_prompt: dict[str, Any],
    v5_shared_assets: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build all 16 mutated bundles for assemble_refusals recipes A1-A16."""
    out: dict[str, dict[str, Any]] = {}

    # A1: Replace assets.formatter.sha256 with 64 zero digits.
    a1 = copy.deepcopy(base_react_v1)
    a1["assets"]["formatter"]["sha256"] = "0" * 64
    out["A1"] = rehash_bundle(a1)

    # A2: Replace target_contract, catalog, block_library, backend and formatter with the TypeScript descriptors from the candidate's TypeScript manifest.
    a2 = copy.deepcopy(base_react_v1)
    for key in REACT_SPECIFIC_ASSET_KEYS:
        a2["assets"][key] = copy.deepcopy(base_ts_v1["assets"][key])
    out["A2"] = rehash_bundle(a2)

    # A3: Add target react to a valid som.dsl-bundle.v1 Python bundle.
    a3 = copy.deepcopy(base_py_v1)
    a3["target"] = "react"
    out["A3"] = rehash_bundle(a3)

    # A4: Set target to python in a React bundle.
    a4 = copy.deepcopy(base_react_v1)
    a4["target"] = "python"
    out["A4"] = rehash_bundle(a4)

    # A5: Replace the first block id with the Python block id orm.base.
    a5 = copy.deepcopy(base_react_v1)
    a5["dsl"]["files"][0]["blocks"][0]["id"] = "orm.base"
    out["A5"] = rehash_bundle(a5)

    # A6: Replace the prompt with the public v5 example prompt. Recompute the shared compiler-v1 descriptors for the v5 prompt, keep the React descriptors and the React DSL, and rehash.
    a6 = copy.deepcopy(base_react_v1)
    a6["prompt"] = copy.deepcopy(v5_prompt)
    for key in SHARED_ASSET_KEYS:
        a6["assets"][key] = copy.deepcopy(v5_shared_assets[key])
    out["A6"] = rehash_bundle(a6)

    # A7: Change one hex digit of payload_sha256. Do not rehash.
    a7 = copy.deepcopy(base_react_v1)
    old_payload = a7["hashes"]["payload_sha256"]
    flipped = ("1" if old_payload[0] == "0" else "0") + old_payload[1:]
    a7["hashes"]["payload_sha256"] = flipped
    out["A7"] = a7

    # A8: Rename the first prompt field to __proto__ in the prompt and in every DSL param that names it.
    a8 = copy.deepcopy(base_react_v1)
    old_field = a8["prompt"]["resource"]["fields"][0]["name"]
    a8["prompt"] = rename_field_in_value(a8["prompt"], old_field, "__proto__")
    for f_entry in a8["dsl"]["files"]:
        for blk in f_entry["blocks"]:
            blk["params"] = rename_field_in_value(blk["params"], old_field, "__proto__")
    out["A8"] = rehash_bundle(a8)

    # A9: Add a 20th file entry, a copy of the last file.
    a9 = copy.deepcopy(base_react_v1)
    last_file = copy.deepcopy(a9["dsl"]["files"][-1])
    while len(a9["dsl"]["files"]) < MAX_FILES + 1:
        a9["dsl"]["files"].append(copy.deepcopy(last_file))
    out["A9"] = rehash_bundle(a9)

    # A10: Set target to rust in a React bundle.
    a10 = copy.deepcopy(base_react_v1)
    a10["target"] = "rust"
    out["A10"] = rehash_bundle(a10)

    # A11: Set target to typescript in a React bundle.
    a11 = copy.deepcopy(base_react_v1)
    a11["target"] = "typescript"
    out["A11"] = rehash_bundle(a11)

    # A12: Set target to react in a TypeScript bundle.
    a12 = copy.deepcopy(base_ts_v1)
    a12["target"] = "react"
    out["A12"] = rehash_bundle(a12)

    # A13: Set target to react in a Rust bundle.
    a13 = copy.deepcopy(base_rust_v1)
    a13["target"] = "react"
    out["A13"] = rehash_bundle(a13)

    # A14: Replace the first block id with the first block id of the candidate's TypeScript bundle for the same prompt.
    a14 = copy.deepcopy(base_react_v1)
    a14["dsl"]["files"][0]["blocks"][0]["id"] = copy.deepcopy(
        base_ts_v1["dsl"]["files"][0]["blocks"][0]["id"]
    )
    out["A14"] = rehash_bundle(a14)

    # A15: Append copies of the last block of the last file until the bundle has 65 blocks in total, keeping that file at 64 blocks or fewer; when it would pass 64, append to the file before it.
    a15 = copy.deepcopy(base_react_v1)
    a15_files = a15["dsl"]["files"]
    last_block = copy.deepcopy(a15_files[-1]["blocks"][-1])
    target_idx = len(a15_files) - 1
    while sum(len(f_entry["blocks"]) for f_entry in a15_files) < MAX_BLOCKS + 1:
        if len(a15_files[target_idx]["blocks"]) >= MAX_BLOCKS:
            target_idx -= 1
        a15_files[target_idx]["blocks"].append(copy.deepcopy(last_block))
    out["A15"] = rehash_bundle(a15)

    # A16: Swap the path members of the first two files, keeping their blocks.
    a16 = copy.deepcopy(base_react_v1)
    a16_files = a16["dsl"]["files"]
    a16_files[0]["path"], a16_files[1]["path"] = a16_files[1]["path"], a16_files[0]["path"]
    out["A16"] = rehash_bundle(a16)

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


def _check_bundle_case(
    base_url: str,
    responses_dir: pathlib.Path,
    products_dir: pathlib.Path,
    case: dict[str, Any],
    fixed_layout_paths: set[str],
    catalog_block_ids: set[str],
    reference_bytes: dict[str, bytes],
    expected_react_specific: dict[str, dict[str, str]],
    manifest_entries: list[dict[str, Any]],
    compiled_bundles_by_id: dict[str, dict[str, Any]],
    rust_bundles_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    cid = case["id"]
    forms = [f for f, _ in case["requests"]] or (
        ["json", "cnl"] if case["single_form"] is None else ["json"]
    )
    if case.get("derive_json_from_cnl_bundle") and "json" not in forms:
        forms = [*forms, "json"]
    first_req_body = case["requests"][0][1] if case["requests"] else None
    expected_summary = {
        "section": "bundle_checks",
        "forms": forms,
        "single_form": case["single_form"],
        "bundle_version": REACT_BUNDLE_VERSION,
        "target": "react",
        "file_count": FILE_COUNT,
    }
    if case.get("not_run_error") is not None:
        return _make_row(
            cid,
            "bundle_checks",
            first_req_body,
            None,
            None,
            None,
            expected_summary,
            [],
            not_run_error=case["not_run_error"],
            forms=forms,
            single_form=case["single_form"],
        )

    assert first_req_body is not None
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

    if (
        not_run_err is None
        and case.get("derive_json_from_cnl_bundle")
        and compile_replies
        and compile_replies[0][1] == 200
    ):
        try:
            cnl_doc = json.loads(compile_replies[0][2])
        except Exception:  # noqa: BLE001
            cnl_doc = None
        cnl_bundle = cnl_doc.get("bundle") if isinstance(cnl_doc, dict) else None
        derived_prompt = (
            cnl_bundle.get("prompt") if isinstance(cnl_bundle, dict) else None
        )
        if isinstance(derived_prompt, dict):
            derived_json_body = compact_json(
                {
                    "format": "som.prompt-json.v1",
                    "prompt": derived_prompt,
                    "target": "react",
                }
            )
            for attempt in (1, 2):
                st, raw, rfile, err = post_json(
                    base_url,
                    "/v1/compile",
                    derived_json_body,
                    responses_dir,
                    f"bundle_checks__{cid}__compile_json_{attempt}",
                )
                if err is not None:
                    not_run_err = err
                    break
                assert st is not None and raw is not None and rfile is not None
                compile_replies.append((f"json#{attempt}", st, raw, rfile))

    if not_run_err is not None:
        return _make_row(
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

    first_label, first_status, first_raw, first_rfile = compile_replies[0]
    if first_status != 200:
        problems.append(f"compile {first_label} returned HTTP {first_status} != 200")
        return _make_row(
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

    by_form_first: dict[str, tuple[str, bytes]] = {}
    for label, _, raw, _ in compile_replies:
        fname = label.split("#")[0]
        if fname not in by_form_first:
            by_form_first[fname] = (label, raw)

    for label, st, raw, _ in compile_replies[1:]:
        fname = label.split("#")[0]
        if st != 200:
            problems.append(f"compile {label} returned HTTP {st} != 200")
        elif label != by_form_first[fname][0] and raw != by_form_first[fname][1]:
            problems.append(
                f"nondeterministic compile: {label} bytes differ from {by_form_first[fname][0]}"
            )
        elif raw != first_raw:
            problems.append(
                f"CNL and JSON compile bytes differ ({label} vs {first_label})"
            )

    try:
        compile_doc = json.loads(first_raw)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"invalid compile JSON: {exc}")
        return _make_row(
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

    if not isinstance(compile_doc, dict) or compile_doc.get("status") != "ok":
        problems.append(
            f"compile reply status {compile_doc.get('status') if isinstance(compile_doc, dict) else compile_doc!r} != 'ok'"
        )
    bundle = compile_doc.get("bundle") if isinstance(compile_doc, dict) else None
    problems.extend(
        validate_bundle_structure(bundle, fixed_layout_paths, catalog_block_ids)
    )

    if (
        isinstance(bundle, dict)
        and isinstance(bundle.get("prompt"), dict)
        and isinstance(bundle.get("assets"), dict)
    ):
        compiled_bundles_by_id[cid] = bundle
        prompt_obj = bundle["prompt"]
        expected_shared = compute_expected_shared_descriptors(prompt_obj)
        expected_manifest = {**expected_shared, **expected_react_specific}
        for akey, expected_desc in expected_manifest.items():
            if bundle["assets"].get(akey) != expected_desc:
                problems.append(
                    f"assets.{akey}: got {bundle['assets'].get(akey)!r} != expected React manifest {expected_desc!r}"
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
            return _make_row(
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
            return _make_row(
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
        a2_status, a2_raw, _, a2_err = post_json(
            base_url,
            "/v1/assemble",
            assemble_body,
            responses_dir,
            f"bundle_checks__{cid}__assemble_2",
        )
        if a2_err is not None:
            return _make_row(
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
                    expected_file_set = set(fixed_layout_paths)
                    if set(files_map) != expected_file_set or len(files_map) != FILE_COUNT:
                        problems.append(
                            f"assembled file set {sorted(files_map)} != expected {FILE_COUNT} product-layout.json files {sorted(expected_file_set)}"
                        )
                    for ref_name, ref_content in reference_bytes.items():
                        actual_content = files_map.get(ref_name)
                        if not isinstance(actual_content, str) or actual_content.encode("utf-8") != ref_content:
                            problems.append(
                                f"assembled {ref_name} bytes differ from reference/{ref_name}"
                            )
                    try:
                        generated = render_reference.render(prompt_obj)
                    except Exception as exc:  # noqa: BLE001
                        generated = {}
                        problems.append(f"render_reference.py failed: {type(exc).__name__}: {exc}")
                    for gen_name in GENERATED_FILES:
                        if gen_name not in generated:
                            problems.append(f"render_reference.py gave no {gen_name}")
                        elif files_map.get(gen_name) != generated[gen_name]:
                            problems.append(
                                f"assembled {gen_name} differs from render_reference.py"
                            )

                    # Write assembled product to <out>/products/<case-id>/
                    case_product_dir = products_dir / cid
                    case_product_dir.mkdir(parents=True, exist_ok=True)
                    for rel_path, content in files_map.items():
                        if isinstance(content, str) and rel_path in expected_file_set:
                            target_file = case_product_dir / rel_path
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            target_file.write_text(content, encoding="utf-8")
                    request_doc = json.loads(first_req_body)
                    manifest_entries.append(
                        {
                            "id": cid,
                            "group": case["group"],
                            "product": str(case_product_dir.resolve()),
                            "prompt": prompt_obj,
                            "python_request": compact_json(
                                {k: v for k, v in request_doc.items() if k != "target"}
                            ),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                problems.append(f"invalid assemble JSON: {exc}")

        return _make_row(
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

    return _make_row(
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


def run_checks(
    base_url: str,
    out_dir: pathlib.Path,
    legacy_run: pathlib.Path = DEFAULT_LEGACY_RUN,
    *,
    expected_legacy_in_scope: int | None = None,
    expected_manifest_products: int | None = None,
) -> dict[str, Any]:
    responses_dir = out_dir / "responses"
    products_dir = out_dir / "products"
    responses_dir.mkdir(parents=True, exist_ok=False)
    products_dir.mkdir(parents=True, exist_ok=False)

    exp_legacy_in_scope = (
        EXPECTED_LEGACY_IN_SCOPE
        if expected_legacy_in_scope is None
        else expected_legacy_in_scope
    )
    exp_manifest_products = (
        EXPECTED_MANIFEST_PRODUCTS
        if expected_manifest_products is None
        else expected_manifest_products
    )

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
        for name in FIXED_FILES
    }
    expected_react_specific = compute_expected_react_specific_descriptors()

    in_scope_cases = collect_in_scope_cases(inventory, target_examples_doc)
    legacy_run_resolved = check_python_baseline.resolve_local_path(
        pathlib.Path(legacy_run)
    )
    legacy_cases, report_problems = collect_legacy_in_scope_cases(
        inventory, legacy_run_resolved, exp_legacy_in_scope
    )

    non_legacy_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    refusal_rows: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []
    compiled_bundles_by_id: dict[str, dict[str, Any]] = {}
    rust_bundles_by_id: dict[str, dict[str, Any]] = {}

    # 1a. bundle_checks across all 29 non-legacy acceptance cases
    for case in in_scope_cases:
        non_legacy_rows.append(
            _check_bundle_case(
                base_url,
                responses_dir,
                products_dir,
                case,
                fixed_layout_paths,
                catalog_block_ids,
                reference_bytes,
                expected_react_specific,
                manifest_entries,
                compiled_bundles_by_id,
                rust_bundles_by_id,
            )
        )

    # 1b. bundle_checks across the 100 legacy_in_scope jobs
    for case in legacy_cases:
        legacy_row = _check_bundle_case(
            base_url,
            responses_dir,
            products_dir,
            case,
            fixed_layout_paths,
            catalog_block_ids,
            reference_bytes,
            expected_react_specific,
            manifest_entries,
            compiled_bundles_by_id,
            rust_bundles_by_id,
        )
        if "inventory_index" in case:
            legacy_row["inventory_index"] = case["inventory_index"]
        legacy_rows.append(legacy_row)

    (products_dir / "manifest.json").write_text(
        json.dumps({"products": manifest_entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if len(manifest_entries) != exp_manifest_products:
        report_problems.append(
            f"products/manifest.json product count is {len(manifest_entries)}, expected {exp_manifest_products}"
        )

    # 2. assemble_refusals: A1-A16
    v1_prompt = json.loads((EXAMPLES / "v1.prompt.json").read_text(encoding="utf-8"))
    v5_prompt = json.loads((EXAMPLES / "v5.prompt.json").read_text(encoding="utf-8"))

    # Compile Python example-v1 for A3, Python example-v5 for the A6 shared descriptors,
    # and TypeScript example-v1 for A2, A12 and A14.
    py_v1_req = compact_json({"format": "som.prompt-json.v1", "prompt": v1_prompt})
    py_v1_status, py_v1_raw, _, py_v1_err = post_json(
        base_url, "/v1/compile", py_v1_req, responses_dir, "assemble_refusals__base_python_v1"
    )
    py_v5_req = compact_json({"format": "som.prompt-json.v1", "prompt": v5_prompt})
    py_v5_status, py_v5_raw, _, py_v5_err = post_json(
        base_url, "/v1/compile", py_v5_req, responses_dir, "assemble_refusals__base_python_v5"
    )
    ts_v1_req = compact_json(
        {"format": "som.prompt-json.v1", "prompt": v1_prompt, "target": "typescript"}
    )
    ts_v1_status, ts_v1_raw, _, ts_v1_err = post_json(
        base_url, "/v1/compile", ts_v1_req, responses_dir, "assemble_refusals__base_typescript_v1"
    )

    base_react_v1 = compiled_bundles_by_id.get("example-v1")
    base_rust_v1 = rust_bundles_by_id.get("example-v1")
    base_ts_v1 = (
        json.loads(ts_v1_raw)["bundle"]
        if (ts_v1_err is None and ts_v1_status == 200 and ts_v1_raw is not None)
        else None
    )
    base_py_v1 = (
        json.loads(py_v1_raw)["bundle"]
        if (py_v1_err is None and py_v1_status == 200 and py_v1_raw is not None)
        else None
    )
    v5_shared_assets = (
        json.loads(py_v5_raw)["bundle"]["assets"]
        if (py_v5_err is None and py_v5_status == 200 and py_v5_raw is not None)
        else None
    )

    recipes_list: list[dict[str, Any]] = acceptance_plan_doc["assemble_refusals"]["recipes"]
    if (
        base_react_v1 is None
        or base_ts_v1 is None
        or base_rust_v1 is None
        or base_py_v1 is None
        or v5_shared_assets is None
    ):
        missing_err = (
            py_v1_err
            or py_v5_err
            or ts_v1_err
            or "cannot build A1-A16 recipes because a base example-v1 or example-v5 bundle failed to compile"
        )
        for recipe in recipes_list:
            rid = recipe["id"]
            refusal_rows.append(
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
            base_react_v1,
            base_ts_v1,
            base_rust_v1,
            base_py_v1,
            v5_prompt,
            v5_shared_assets,
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
                refusal_rows.append(
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
            refusal_rows.append(
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

    rows = [*non_legacy_rows, *refusal_rows, *legacy_rows]
    if report_problems and rows and all(r["passed"] for r in rows):
        msg = "; ".join(report_problems)
        last = rows[-1]
        last["passed"] = False
        last["status"] = "failed"
        last["reason"] = msg if last["reason"] == "ok" else f"{last['reason']}; {msg}"

    all_passed = bool(rows) and not report_problems and all(r["passed"] for r in rows)
    report = {
        "version": "som.react-output-bundle-checks.v1",
        "issue": 129,
        "url": base_url,
        "legacy_run": str(legacy_run_resolved.resolve()),
        "manifest": str((products_dir / "manifest.json").relative_to(out_dir)),
        "status": "passed" if all_passed else "failed",
        "passed": all_passed,
        "total": len(rows),
        "passed_count": sum(1 for r in rows if r["passed"]),
        "failed_count": sum(1 for r in rows if not r["passed"]),
        "not_run_count": sum(1 for r in rows if r["status"] == "not_run"),
        "problems": report_problems,
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
    request_body: str | None,
    http_status: int | None,
    raw_response: bytes | None,
    response_file: str | None,
    expected: Any,
    problems: list[str],
    not_run_error: str | None = None,
    forms: list[str] | None = None,
    single_form: str | None = None,
) -> dict[str, Any]:
    req_sha = (
        sha_bytes(request_body.encode("utf-8"))
        if isinstance(request_body, str) and request_body
        else None
    )
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


def main(
    argv: list[str] | None = None,
    *,
    expected_legacy_in_scope: int | None = None,
    expected_manifest_products: int | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="Loopback URL of the candidate som server")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        required=True,
        help="New output directory for report.json, responses/, and products/",
    )
    parser.add_argument(
        "--legacy-run",
        type=pathlib.Path,
        default=DEFAULT_LEGACY_RUN,
        help="Path to the recorded legacy544 run (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    url_err = validate_url(args.url)
    if url_err is not None:
        print(f"refused: {url_err}", file=sys.stderr)
        return 2

    if args.out.exists():
        print(f"refused: output directory already exists: {args.out}", file=sys.stderr)
        return 2

    report = run_checks(
        args.url,
        args.out,
        args.legacy_run,
        expected_legacy_in_scope=expected_legacy_in_scope,
        expected_manifest_products=expected_manifest_products,
    )
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
