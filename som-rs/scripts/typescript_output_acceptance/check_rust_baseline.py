#!/usr/bin/env python3
"""Compare Rust compile responses with the baseline and verify baseline bundle refusal and rebase (#104).

Covers both rust-output-v1 amendment005 checks on a TypeScript phase 1 candidate:
1. Rust compile against the baseline: every case of the rust-output-055 run-003
   inventory (115 phase 1 cases + 584 phase 2 cases), compiled with target: "rust",
   gives the baseline's response except for bundle.assets.backend,
   bundle.assets.target_contract, and bundle.hashes.payload_sha256. RS12 and RS13
   give the baseline's compiler-v1 refusal bytes.
2. Rust bundles of the baseline: the candidate refuses every Rust bundle the
   baseline returns, at POST /v1/assemble, with ASSET_HASH_MISMATCH (HTTP 400).
   The same bundle, rebased on the candidate's backend and target_contract
   descriptors with payload_sha256 recomputed, assembles with HTTP 200 to the
   files_sha256 that run-003 recorded.

usage:
  check_rust_baseline.py --qa URL --baseline URL --run DIR --out DIR [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent


def find_runtime_root(repo: pathlib.Path = REPO) -> pathlib.Path:
    for candidate in (repo, *repo.parents):
        if (candidate / ".local" / "rust-compiler-goal" / "acceptance").is_dir():
            return candidate
        if (candidate / ".local" / "typescript-output").is_dir():
            return candidate
        if (candidate / "som-rs" / "runtime" / "default.json").is_file():
            return candidate
    return repo


def resolve_local_path(path: pathlib.Path, runtime_root: pathlib.Path | None = None) -> pathlib.Path:
    if path.is_absolute() or path.exists():
        return path
    for base in (REPO, runtime_root or find_runtime_root()):
        candidate = base / path
        if candidate.exists():
            return candidate
    return path


RUNTIME_ROOT = find_runtime_root()
LOCAL_ACCEPTANCE = RUNTIME_ROOT / ".local" / "rust-compiler-goal" / "acceptance"
DEFAULT_RUN = LOCAL_ACCEPTANCE / "rust-output-055" / "run-003"

for _p in (
    SOM_RS / "behavior" / "phase2",
    SOM_RS / "scripts" / "compiler_acceptance",
    SOM_RS / "tests" / "rust-output-v1",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import check_phase2 as phase2  # type: ignore[import-not-found]  # noqa: E402
import qa_harness as qa  # type: ignore[import-not-found]  # noqa: E402
import rust_profile  # type: ignore[import-not-found]  # noqa: E402

if not (REPO / ".local").exists() and (RUNTIME_ROOT / ".local").exists():
    phase2.LOCAL = RUNTIME_ROOT / ".local"
    phase2.LEGACY = phase2.LOCAL / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
    phase2.PUBLIC = phase2.LOCAL / "rust-compiler-goal" / "acceptance" / "public-products-003b"
    phase2.RS = phase2.LOCAL / "rust-output-phase2" / "rs-products-001"

_ORIG_PHASE2_CASES = phase2.cases

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
CHECKS = ("compile_equal", "baseline_bundle_refused", "rebased_files")

NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(2)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Frozen canonical JSON sha256 from qa_harness (used by check_phase2 and check_contract)."""
    return qa.sha(value)


def compute_bundle_payload_sha256(bundle: dict[str, Any]) -> str:
    """Compute payload_sha256 over the bundle without 'hashes' using the frozen formula."""
    payload = {key: value for key, value in bundle.items() if key != "hashes"}
    return canonical_sha256(payload)


def validate_url(raw_url: str, flag: str) -> tuple[str, urllib.parse.SplitResult]:
    try:
        split = urllib.parse.urlsplit(raw_url)
    except ValueError as err:
        raise ValueError(f"invalid {flag} URL {raw_url!r}: {err}") from err
    if split.scheme not in {"http", "https"}:
        raise ValueError(f"{flag} URL must use http or https: {raw_url!r}")
    if split.hostname not in LOOPBACK_HOSTS:
        raise ValueError(f"{flag} URL must use a loopback host: {raw_url!r}")
    try:
        port = split.port
    except ValueError as err:
        raise ValueError(f"invalid port in {flag} URL {raw_url!r}: {err}") from err
    if port is None or port <= 0 or port > 65535:
        raise ValueError(f"{flag} URL must specify an explicit valid port: {raw_url!r}")
    if port in FORBIDDEN_PORTS:
        raise ValueError(f"{flag} URL port {port} is forbidden (never use 8765 or 18765)")
    return raw_url.rstrip("/"), split


def same_endpoint(
    qa_url: str,
    qa_split: urllib.parse.SplitResult,
    base_url: str,
    base_split: urllib.parse.SplitResult,
) -> bool:
    if qa_url == base_url:
        return True
    return (
        qa_split.scheme == base_split.scheme
        and qa_split.port == base_split.port
        and qa_split.path.rstrip("/") == base_split.path.rstrip("/")
    )


def post_raw(base_url: str, route: str, data: bytes, timeout: float = 120.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        base_url + route,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with NO_PROXY_OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


_STATIC_PROMPT_CACHE: dict[str, dict[str, Any]] | None = None
_LEGACY_INDEX_CACHE: dict[str, tuple[int, str | None]] | None = None
_LOADED_PROMPT_CACHE: dict[str, dict[str, Any]] = {}


def _lookup_prompt_by_id(case_id: str) -> dict[str, Any] | None:
    """Resolve a typed prompt by case_id lazily across phase 1 and phase 2 inventories."""
    if case_id in _LOADED_PROMPT_CACHE:
        return _LOADED_PROMPT_CACHE[case_id]

    global _STATIC_PROMPT_CACHE, _LEGACY_INDEX_CACHE
    if _STATIC_PROMPT_CACHE is None:
        prompts: dict[str, dict[str, Any]] = {}
        examples_dir = SOM_RS / "contracts" / "compiler-v1" / "examples"
        for name in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v7-branch-null"):
            pfile = examples_dir / f"{name}.prompt.json"
            if pfile.is_file():
                prompts[f"example-{name}"] = phase2.read(pfile)

        fixtures_path = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
        if fixtures_path.is_file():
            for case in phase2.read(fixtures_path).get("cases", []):
                prompts[case["id"]] = case["expected_prompt"]

        coverage_path = SOM_RS / "tests" / "rust-output-v1" / "cases" / "index.json"
        if coverage_path.is_file():
            for case in phase2.read(coverage_path).get("cases", []):
                pfile = coverage_path.parent / case["prompt"]
                if pfile.is_file():
                    prompts[case["id"]] = phase2.read(pfile)

        _STATIC_PROMPT_CACHE = prompts

    if case_id in _STATIC_PROMPT_CACHE:
        _LOADED_PROMPT_CACHE[case_id] = _STATIC_PROMPT_CACHE[case_id]
        return _STATIC_PROMPT_CACHE[case_id]

    # Check RS cases (e.g. "RS01", "rs/RS12")
    rs_short = case_id.split("/")[-1] if case_id.startswith("rs/") else case_id
    if rs_short.startswith("RS"):
        rs_prompt_path = RUNTIME_ROOT / ".local" / "rust-output-phase2" / "rs-products-001" / rs_short / "prompt.json"
        if rs_prompt_path.is_file():
            loaded = phase2.read(rs_prompt_path)
            _LOADED_PROMPT_CACHE[case_id] = loaded
            return loaded

    # Check PUBLIC_v6 / PUBLIC_v7 / PUBLIC_v7_branch_null
    if case_id.startswith("PUBLIC_"):
        suffix = case_id[len("PUBLIC_") :].replace("_", "-")
        pub_example = f"example-{suffix}"
        if pub_example in _STATIC_PROMPT_CACHE:
            _LOADED_PROMPT_CACHE[case_id] = _STATIC_PROMPT_CACHE[pub_example]
            return _STATIC_PROMPT_CACHE[pub_example]

    # Check legacy544 lazily by case_id
    legacy_root = RUNTIME_ROOT / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544"
    if _LEGACY_INDEX_CACHE is None:
        _LEGACY_INDEX_CACHE = {}
        inv_path = legacy_root / "inventory-003.json"
        if inv_path.is_file():
            inv = phase2.read(inv_path)
            for index, group in enumerate(inv.get("groups", [])):
                _LEGACY_INDEX_CACHE[group["id"]] = (index, group.get("prompt_sha256"))

    if case_id in _LEGACY_INDEX_CACHE:
        index, psha = _LEGACY_INDEX_CACHE[case_id]
        run5_root = legacy_root / "run-005"
        comp_req = run5_root / "compile" / str(psha) / "compile-json.request.json" if psha else None
        if comp_req is not None and comp_req.is_file():
            loaded = phase2.read(comp_req)["prompt"]
            _LOADED_PROMPT_CACHE[case_id] = loaded
            return loaded
        asm_req = run5_root / f"{index:04d}" / "assemble.request.json"
        if asm_req.is_file():
            loaded = phase2.read(asm_req)["bundle"]["prompt"]
            _LOADED_PROMPT_CACHE[case_id] = loaded
            return loaded
        var_bundle = run5_root / f"{index:04d}" / "variant.bundle.json"
        if var_bundle.is_file():
            loaded = phase2.read(var_bundle)["prompt"]
            _LOADED_PROMPT_CACHE[case_id] = loaded
            return loaded

    return None


def _is_refusal_record(rec: dict[str, Any]) -> bool:
    expected = rec.get("expected")
    if expected == "refusal" or (isinstance(expected, dict) and expected.get("result") == "refusal"):
        return True
    if rec.get("refusal") is not None and "files_sha256" not in rec:
        return True
    comp_status = rec.get("compile_status")
    if isinstance(comp_status, int) and comp_status != 200 and "files_sha256" not in rec:
        return True
    return False


def load_phase1_cases(run: pathlib.Path) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """Read phase 1 cases from <run>/rust-products.json like check_rust_unchanged.py."""
    rp_path = run / "rust-products.json"
    if not rp_path.is_file():
        return (
            [
                {
                    "id": "phase1_rust_products",
                    "phase": "phase1",
                    "prompt": None,
                    "files_sha256": None,
                    "is_refusal": False,
                    "not_run_reason": f"missing {rp_path}",
                }
            ],
            None,
            [f"missing {rp_path}"],
        )

    try:
        rp_sha = sha_file(rp_path)
        doc = phase2.read(rp_path)
        raw_cases = doc.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"{rp_path} missing 'cases' list")
    except (OSError, ValueError) as err:
        reason = f"cannot read {rp_path}: {type(err).__name__}: {err}"
        return (
            [
                {
                    "id": "phase1_rust_products",
                    "phase": "phase1",
                    "prompt": None,
                    "files_sha256": None,
                    "is_refusal": False,
                    "not_run_reason": reason,
                }
            ],
            None,
            [reason],
        )

    items: list[dict[str, Any]] = []
    for index, rec in enumerate(raw_cases):
        if not isinstance(rec, dict):
            items.append(
                {
                    "id": f"phase1-{index}",
                    "phase": "phase1",
                    "prompt": None,
                    "files_sha256": None,
                    "is_refusal": False,
                    "not_run_reason": f"invalid phase 1 record at index {index}",
                }
            )
            continue
        case_id = str(rec.get("id") or f"phase1-{index}")
        files_sha = rec.get("files_sha256")
        is_refusal = _is_refusal_record(rec)
        prompt = rec.get("prompt") if isinstance(rec.get("prompt"), dict) else _lookup_prompt_by_id(case_id)
        not_run_reason: str | None = None
        if not is_refusal and (not isinstance(files_sha, str) or not files_sha):
            not_run_reason = f"missing recorded files_sha256 for phase 1 case {case_id}"
        elif not isinstance(prompt, dict):
            not_run_reason = f"missing typed prompt for phase 1 case {case_id}"
        items.append(
            {
                "id": case_id,
                "phase": "phase1",
                "prompt": prompt,
                "files_sha256": files_sha if isinstance(files_sha, str) else None,
                "is_refusal": is_refusal,
                "not_run_reason": not_run_reason,
            }
        )
    return items, rp_sha, []


def _discover_phase2_case_specs(run: pathlib.Path) -> list[dict[str, Any]]:
    """Return phase 2 case specs using phase2.cases() or fake run directory records."""
    if phase2.cases is not _ORIG_PHASE2_CASES:
        return list(phase2.cases())
    try:
        if run.resolve() == DEFAULT_RUN.resolve():
            return list(phase2.cases())
    except OSError:
        pass

    cases_dir = run / "phase2" / "cases"
    if not cases_dir.is_dir():
        return list(phase2.cases())

    record_files = sorted(
        p for p in cases_dir.glob("*.json") if not p.name.endswith(".outcome.json")
    )
    summary_ids: list[str] = []
    for sname, skey in (("summary.json", "case_sha256"), ("products-summary.json", "gate_sha256")):
        spath = run / "phase2" / sname
        if spath.is_file():
            try:
                sdoc = phase2.read(spath)
                if isinstance(sdoc.get(skey), dict):
                    for cid in sdoc[skey]:
                        if cid not in summary_ids:
                            summary_ids.append(cid)
            except (OSError, ValueError):
                pass

    if not record_files and not summary_ids:
        return list(phase2.cases())

    specs: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for cid in summary_ids:
        k = phase2.key(cid)
        seen_keys.add(k)
        rpath = cases_dir / f"{k}.json"
        rec_prompt: dict[str, Any] | None = None
        prompt_sha: str | None = None
        if rpath.is_file():
            try:
                rdoc = phase2.read(rpath)
                if isinstance(rdoc.get("prompt"), dict):
                    rec_prompt = rdoc["prompt"]
                prompt_sha = rdoc.get("prompt_sha256")
            except (OSError, ValueError):
                pass
        if rec_prompt is None:
            rec_prompt = _lookup_prompt_by_id(cid)
        if prompt_sha is None and isinstance(rec_prompt, dict):
            prompt_sha = canonical_sha256(rec_prompt)
        specs.append(
            {
                "id": cid,
                "prompt_sha256": prompt_sha,
                "load_prompt": (lambda p=rec_prompt: p),
            }
        )

    for rpath in record_files:
        stem = rpath.stem
        if stem in seen_keys:
            continue
        seen_keys.add(stem)
        try:
            rdoc = phase2.read(rpath)
        except (OSError, ValueError):
            rdoc = {}
        cid = str(rdoc.get("id") or stem)
        rec_prompt = rdoc.get("prompt") if isinstance(rdoc.get("prompt"), dict) else _lookup_prompt_by_id(cid)
        prompt_sha = rdoc.get("prompt_sha256")
        if prompt_sha is None and isinstance(rec_prompt, dict):
            prompt_sha = canonical_sha256(rec_prompt)
        specs.append(
            {
                "id": cid,
                "prompt_sha256": prompt_sha,
                "load_prompt": (lambda p=rec_prompt: p),
            }
        )
    return specs


def load_phase2_cases(
    run: pathlib.Path,
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Read phase 2 cases and <run>/phase2/cases/<key>.json records like check_rust_unchanged.py."""
    problems: list[str] = []
    try:
        specs = _discover_phase2_case_specs(run)
    except Exception as err:  # noqa: BLE001
        reason = f"cannot load phase 2 case list: {type(err).__name__}: {err}"
        return (
            [
                {
                    "id": "phase2_cases",
                    "phase": "phase2",
                    "prompt": None,
                    "files_sha256": None,
                    "is_refusal": False,
                    "not_run_reason": reason,
                }
            ],
            {},
            [reason],
        )

    items: list[dict[str, Any]] = []
    record_hashes: dict[str, str] = {}
    cases_dir = run / "phase2" / "cases"

    for spec in specs:
        case_id = str(spec["id"])
        rec_path = cases_dir / f"{phase2.key(case_id)}.json"
        prompt: dict[str, Any] | None = None
        not_run_reason: str | None = None
        files_sha: str | None = None
        is_refusal = False

        if not rec_path.is_file():
            not_run_reason = f"missing phase 2 case record: {rec_path}"
        else:
            try:
                record_hashes[case_id] = sha_file(rec_path)
                rec_doc = phase2.read(rec_path)
                rust_rec = rec_doc.get("rust")
                if not isinstance(rust_rec, dict):
                    not_run_reason = f"missing 'rust' object in {rec_path.name}"
                else:
                    if _is_refusal_record(rust_rec):
                        is_refusal = True
                    else:
                        raw_fsha = rust_rec.get("files_sha256")
                        if isinstance(raw_fsha, str) and raw_fsha:
                            files_sha = raw_fsha
                        else:
                            not_run_reason = f"missing recorded files_sha256 in {rec_path.name}"
            except (OSError, ValueError) as err:
                not_run_reason = f"cannot read {rec_path.name}: {type(err).__name__}: {err}"

        if not_run_reason is None:
            try:
                loaded_prompt = spec["load_prompt"]()
                if not isinstance(loaded_prompt, dict):
                    not_run_reason = f"missing typed prompt for phase 2 case {case_id}"
                else:
                    exp_psha = spec.get("prompt_sha256")
                    if exp_psha and canonical_sha256(loaded_prompt) != exp_psha:
                        not_run_reason = f"prompt hash differs from the run record for {case_id}"
                    else:
                        prompt = loaded_prompt
            except Exception as err:  # noqa: BLE001
                not_run_reason = f"cannot load typed prompt for {case_id}: {type(err).__name__}: {err}"

        items.append(
            {
                "id": case_id,
                "phase": "phase2",
                "prompt": prompt,
                "files_sha256": files_sha,
                "is_refusal": is_refusal,
                "not_run_reason": not_run_reason,
            }
        )

    return items, record_hashes, problems


def bundle_differences(old: Any, new: Any, path: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for k in sorted(set(old) | set(new)):
            sub = f"{path}/{k}"
            if k not in old or k not in new:
                out.append(sub)
            else:
                out.extend(bundle_differences(old[k], new[k], sub))
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [
            d
            for idx, (a, b) in enumerate(zip(old, new))
            for d in bundle_differences(a, b, f"{path}/{idx}")
        ]
    return [] if old == new else [path or "/"]


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _make_row(
    case_id: str,
    phase_name: str,
    check_name: str,
    *,
    request_sha256: str | None = None,
    qa_request_sha256: str | None = None,
    baseline_request_sha256: str | None = None,
    response_sha256: str | None = None,
    qa_response_sha256: str | None = None,
    baseline_response_sha256: str | None = None,
    qa_status: int | None = None,
    baseline_status: int | None = None,
    expected: Any = None,
    passed: bool = False,
    status: str = "not_run",
    reason: str | None = None,
    refusal_path: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": case_id,
        "phase": phase_name,
        "check": check_name,
        "request_sha256": request_sha256,
        "qa_request_sha256": qa_request_sha256 if qa_request_sha256 is not None else request_sha256,
        "baseline_request_sha256": (
            baseline_request_sha256 if baseline_request_sha256 is not None else request_sha256
        ),
        "response_sha256": response_sha256 if response_sha256 is not None else qa_response_sha256,
        "qa_response_sha256": qa_response_sha256 if qa_response_sha256 is not None else response_sha256,
        "baseline_response_sha256": baseline_response_sha256,
        "qa_status": qa_status,
        "baseline_status": baseline_status,
        "expected": expected,
        "passed": passed,
        "status": status,
        "reason": reason,
    }
    if refusal_path:
        row["refusal_path"] = True
    return row


def native_digest_context():
    if not os.environ.get('SOM_FULL_PRODUCT_CONTEXT'):
        return None
    import full_product_context as hosted
    ctx = hosted.context()
    if ctx.qa_mode != 'native-qa-v1':
        return None
    path = 'som-rs/scripts/hosted_readiness/typescript_native_digests.py'
    if path not in ctx.qa_source_authority['admitted_repository_python_sources']:
        raise ValueError('native baseline digest adapter is outside exact source admission')
    return ctx, ctx.module('typescript_regression_native_digests', path)


def evaluate_case(
    qa_url: str,
    baseline_url: str,
    item: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, bytes]]], bool]:
    """Run all checks for one case. Returns (rows, failed_raw_blobs_per_row, took_refusal_path)."""
    native = native_digest_context()
    native_pair_error = None
    native_pair_proof = None
    case_id: str = item["id"]
    phase_name: str = item["phase"]
    is_refusal: bool = bool(item.get("is_refusal"))
    files_sha: str | None = item.get("files_sha256")

    if item.get("not_run_reason") is not None or item.get("prompt") is None:
        reason = item.get("not_run_reason") or "missing case record or typed prompt"
        not_run_rows: list[dict[str, Any]] = []
        checks_to_emit = ("compile_equal",) if is_refusal else CHECKS
        for check_name in checks_to_emit:
            not_run_rows.append(
                _make_row(
                    case_id,
                    phase_name,
                    check_name,
                    expected="refusal" if is_refusal else files_sha,
                    passed=False,
                    status="not_run",
                    reason=reason,
                )
            )
        return not_run_rows, [("", {}) for _ in not_run_rows], False

    prompt: dict[str, Any] = item["prompt"]
    compile_req_bytes = compact_json_bytes(
        {"format": "som.prompt-json.v1", "target": "rust", "prompt": prompt}
    )
    compile_req_sha = sha_bytes(compile_req_bytes)

    try:
        qa_comp_status, qa_comp_raw = post_raw(qa_url, "/v1/compile", compile_req_bytes)
    except OSError as err:
        reason = f"qa compile request failed: {type(err).__name__}: {err}"
        rows_err = [
            _make_row(
                case_id,
                phase_name,
                chk,
                request_sha256=compile_req_sha if chk == "compile_equal" else None,
                expected="refusal" if is_refusal else files_sha,
                passed=False,
                status="failed",
                reason=reason,
            )
            for chk in (("compile_equal",) if is_refusal else CHECKS)
        ]
        return rows_err, [("", {}) for _ in rows_err], False

    try:
        base_comp_status, base_comp_raw = post_raw(baseline_url, "/v1/compile", compile_req_bytes)
    except OSError as err:
        reason = f"baseline compile request failed: {type(err).__name__}: {err}"
        rows_err = [
            _make_row(
                case_id,
                phase_name,
                chk,
                request_sha256=compile_req_sha if chk == "compile_equal" else None,
                qa_response_sha256=sha_bytes(qa_comp_raw) if chk == "compile_equal" else None,
                qa_status=qa_comp_status if chk == "compile_equal" else None,
                expected="refusal" if is_refusal else files_sha,
                passed=False,
                status="failed",
                reason=reason,
            )
            for chk in (("compile_equal",) if is_refusal else CHECKS)
        ]
        return rows_err, [("compile_equal", {"qa.compile.raw": qa_comp_raw})] + [("", {}) for _ in rows_err[1:]], False

    qa_comp_sha = sha_bytes(qa_comp_raw)
    base_comp_sha = sha_bytes(base_comp_raw)

    # Non-200 refusal path (RS12 / RS13)
    if is_refusal or base_comp_status != 200:
        refusal_ok = (
            base_comp_status != 200
            and qa_comp_status == base_comp_status
            and qa_comp_raw == base_comp_raw
        )
        if base_comp_status == 200 or qa_comp_status == 200:
            reason = (
                f"expected non-200 refusal compile response on both sides, "
                f"got qa={qa_comp_status}, baseline={base_comp_status}"
            )
        elif qa_comp_status != base_comp_status:
            reason = f"refusal compile HTTP status differs: qa={qa_comp_status}, baseline={base_comp_status}"
        elif qa_comp_raw != base_comp_raw:
            reason = "refusal compile response bytes differ"
        else:
            reason = None

        row = _make_row(
            case_id,
            phase_name,
            "compile_equal",
            request_sha256=compile_req_sha,
            response_sha256=qa_comp_sha,
            qa_response_sha256=qa_comp_sha,
            baseline_response_sha256=base_comp_sha,
            qa_status=qa_comp_status,
            baseline_status=base_comp_status,
            expected="refusal_bytes_equal",
            passed=refusal_ok,
            status="passed" if refusal_ok else "failed",
            reason=reason,
            refusal_path=True,
        )
        blobs = (
            {}
            if refusal_ok and native is None
            else {"compile.request.raw": compile_req_bytes, "qa.compile.raw": qa_comp_raw, "baseline.compile.raw": base_comp_raw}
        )
        return [row], [("compile_equal", blobs)], True

    # Product case path: requires 200 compile on both sides
    rows_out: list[dict[str, Any]] = []
    blobs_out: list[tuple[str, dict[str, bytes]]] = []

    comp_reasons: list[str] = []
    qa_bundle: dict[str, Any] | None = None
    base_bundle: dict[str, Any] | None = None

    if qa_comp_status != 200 or base_comp_status != 200:
        comp_reasons.append(
            f"expected compile HTTP 200 on both sides, got qa={qa_comp_status}, baseline={base_comp_status}"
        )
    else:
        try:
            qa_doc = json.loads(qa_comp_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            qa_doc = None
            comp_reasons.append(f"qa compile response is not valid JSON: {err}")

        try:
            base_doc = json.loads(base_comp_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            base_doc = None
            comp_reasons.append(f"baseline compile response is not valid JSON: {err}")

        if isinstance(qa_doc, dict) and isinstance(base_doc, dict):
            if qa_doc.get("status") != "ok" or not isinstance(qa_doc.get("bundle"), dict):
                comp_reasons.append("qa compile 200 response missing bundle")
            else:
                qa_bundle = qa_doc["bundle"]
            if base_doc.get("status") != "ok" or not isinstance(base_doc.get("bundle"), dict):
                comp_reasons.append("baseline compile 200 response missing bundle")
            else:
                base_bundle = base_doc["bundle"]

            qa_top_extra = {k: v for k, v in qa_doc.items() if k != "bundle"}
            base_top_extra = {k: v for k, v in base_doc.items() if k != "bundle"}
            if qa_top_extra != base_top_extra:
                comp_reasons.append("compile top-level envelope differs outside bundle")
        else:
            if qa_doc is not None and not isinstance(qa_doc, dict):
                comp_reasons.append("qa compile response is not a JSON object")
            if base_doc is not None and not isinstance(base_doc, dict):
                comp_reasons.append("baseline compile response is not a JSON object")

        if qa_bundle is not None and base_bundle is not None:
            qa_copy = copy.deepcopy(qa_bundle)
            base_copy = copy.deepcopy(base_bundle)

            qa_assets = qa_copy.get("assets")
            base_assets = base_copy.get("assets")
            qa_hashes = qa_copy.get("hashes")
            base_hashes = base_copy.get("hashes")

            if not isinstance(qa_assets, dict) or not isinstance(base_assets, dict):
                comp_reasons.append("bundle.assets is missing or not an object")
            else:
                if native is not None:
                    # Validate complete raw bytes and source-owned SHA leaves first.
                    try:
                        native_pair_proof = native[1].compare(native[0], 'rust', base_comp_raw, qa_comp_raw)
                        qa_assets['formatter']['sha256'] = None
                        base_assets['formatter']['sha256'] = None
                    except ValueError as error:
                        native_pair_error = str(error)
                        comp_reasons.append('native source digest proof refused: ' + str(error))
                for allowed_asset in ("backend", "target_contract"):
                    if allowed_asset not in qa_assets or allowed_asset not in base_assets:
                        comp_reasons.append(f"bundle.assets.{allowed_asset} is missing")
                    qa_assets.pop(allowed_asset, None)
                    base_assets.pop(allowed_asset, None)

            if not isinstance(qa_hashes, dict) or not isinstance(base_hashes, dict):
                comp_reasons.append("bundle.hashes is missing or not an object")
            else:
                if "payload_sha256" not in qa_hashes or "payload_sha256" not in base_hashes:
                    comp_reasons.append("bundle.hashes.payload_sha256 is missing")
                qa_hashes.pop("payload_sha256", None)
                base_hashes.pop("payload_sha256", None)

            diffs = bundle_differences(base_copy, qa_copy)
            if diffs:
                comp_reasons.append(
                    "bundle differs outside allowed fields (assets.backend, assets.target_contract, hashes.payload_sha256): "
                    + ", ".join(diffs)
                )

            expected_payload_sha = compute_bundle_payload_sha256(qa_bundle)
            actual_payload_sha = (qa_bundle.get("hashes") or {}).get("payload_sha256")
            if actual_payload_sha != expected_payload_sha:
                comp_reasons.append(
                    f"candidate payload_sha256 {actual_payload_sha!r} != frozen payload hash {expected_payload_sha!r}"
                )

    comp_passed = not comp_reasons
    comp_row = _make_row(
        case_id,
        phase_name,
        "compile_equal",
        request_sha256=compile_req_sha,
        response_sha256=qa_comp_sha,
        qa_response_sha256=qa_comp_sha,
        baseline_response_sha256=base_comp_sha,
        qa_status=qa_comp_status,
        baseline_status=base_comp_status,
        expected="bundle_equal_except_backend_target_contract_payload_sha256",
        passed=comp_passed,
        status="passed" if comp_passed else "failed",
        reason=None if comp_passed else "; ".join(comp_reasons),
    )
    if native is not None:
        comp_row['expected_role'] = 'original-judge-label'
        comp_row['comparison_mode'] = 'native-qa-v1:exact-source-owned-digest-SHA-leaves'
        comp_row['native_digest_proof'] = native_pair_proof
    rows_out.append(comp_row)
    blobs_out.append(
        (
            "compile_equal",
            {}
            if comp_passed and native is None
            else {"compile.request.raw": compile_req_bytes, "qa.compile.raw": qa_comp_raw, "baseline.compile.raw": base_comp_raw},
        )
    )

    if qa_bundle is None or base_bundle is None or native_pair_error is not None:
        for chk_name, exp_val in (
            ("baseline_bundle_refused", {"http_status": 400, "code": "ASSET_HASH_MISMATCH"}),
            ("rebased_files", {"http_status": 200, "files_sha256": files_sha}),
        ):
            rows_out.append(
                _make_row(
                    case_id,
                    phase_name,
                    chk_name,
                    expected=exp_val,
                    passed=False,
                    status="failed",
                    reason="skipped assemble check because compile did not return valid bundles on both sides",
                )
            )
            blobs_out.append((chk_name, {}))
        return rows_out, blobs_out, False

    # Check 2: baseline_bundle_refused on candidate
    base_asm_req_bytes = compact_json_bytes({"bundle": base_bundle})
    base_asm_req_sha = sha_bytes(base_asm_req_bytes)
    try:
        refuse_status, refuse_raw = post_raw(qa_url, "/v1/assemble", base_asm_req_bytes)
        refuse_resp_sha = sha_bytes(refuse_raw)
        try:
            refuse_doc = json.loads(refuse_raw.decode("utf-8"))
            refuse_code = refuse_doc.get("code") if isinstance(refuse_doc, dict) else None
        except (UnicodeDecodeError, ValueError):
            refuse_code = None

        refuse_passed = refuse_status == 400 and refuse_code == "ASSET_HASH_MISMATCH"
        refuse_reason = (
            None
            if refuse_passed
            else (
                f"expected 400 ASSET_HASH_MISMATCH for baseline bundle on candidate, "
                f"got status={refuse_status} code={refuse_code!r}"
            )
        )
        refuse_blobs = {} if refuse_passed and native is None else {
            "baseline.assemble.request.raw": base_asm_req_bytes, "qa.baseline_bundle_assemble.raw": refuse_raw}
    except OSError as err:
        refuse_status = None
        refuse_resp_sha = None
        refuse_passed = False
        refuse_reason = f"qa assemble request failed for baseline bundle: {type(err).__name__}: {err}"
        refuse_blobs = {}

    rows_out.append(
        _make_row(
            case_id,
            phase_name,
            "baseline_bundle_refused",
            request_sha256=base_asm_req_sha,
            response_sha256=refuse_resp_sha,
            qa_response_sha256=refuse_resp_sha,
            qa_status=refuse_status,
            expected={"http_status": 400, "code": "ASSET_HASH_MISMATCH"},
            passed=refuse_passed,
            status="passed" if refuse_passed else "failed",
            reason=refuse_reason,
        )
    )
    blobs_out.append(("baseline_bundle_refused", refuse_blobs))

    # Check 3: rebased_files on candidate
    rebased_bundle = copy.deepcopy(base_bundle)
    if isinstance(rebased_bundle.get("assets"), dict) and isinstance(qa_bundle.get("assets"), dict):
        if "backend" in qa_bundle["assets"]:
            rebased_bundle["assets"]["backend"] = copy.deepcopy(qa_bundle["assets"]["backend"])
        if "target_contract" in qa_bundle["assets"]:
            rebased_bundle["assets"]["target_contract"] = copy.deepcopy(
                qa_bundle["assets"]["target_contract"]
            )
    if native is not None:
        expected_native_rebase, _ = native[1].rebase(native[0], 'rust', base_comp_raw, qa_comp_raw)
        rebased_bundle['assets']['formatter']['sha256'] = expected_native_rebase['assets']['formatter']['sha256']
    if isinstance(rebased_bundle.get("hashes"), dict):
        rebased_bundle["hashes"]["payload_sha256"] = compute_bundle_payload_sha256(rebased_bundle)
    if native is not None and rebased_bundle != expected_native_rebase:
        raise ValueError('native Rust rebase differs outside exact source-owned digest leaves')

    rebased_req_bytes = compact_json_bytes({"bundle": rebased_bundle})
    rebased_req_sha = sha_bytes(rebased_req_bytes)

    try:
        reb_status, reb_raw = post_raw(qa_url, "/v1/assemble", rebased_req_bytes)
        reb_resp_sha = sha_bytes(reb_raw)
        reb_reasons: list[str] = []
        actual_files_sha: str | None = None
        if reb_status != 200:
            reb_reasons.append(f"rebased bundle assemble returned HTTP {reb_status}, expected 200")
        else:
            try:
                reb_doc = json.loads(reb_raw.decode("utf-8"))
                files_obj = reb_doc.get("files") if isinstance(reb_doc, dict) else None
                if not isinstance(files_obj, dict):
                    reb_reasons.append("rebased bundle assemble 200 response missing 'files' object")
                else:
                    actual_files_sha = canonical_sha256(files_obj)
                    if actual_files_sha != files_sha:
                        reb_reasons.append(
                            f"rebased assembled files_sha256 {actual_files_sha!r} != recorded {files_sha!r}"
                        )
            except (UnicodeDecodeError, ValueError) as err:
                reb_reasons.append(f"rebased bundle assemble response is not valid JSON: {err}")

        reb_passed = not reb_reasons
        reb_reason = None if reb_passed else "; ".join(reb_reasons)
        reb_blobs = {} if reb_passed and native is None else {
            "rebased.assemble.request.raw": rebased_req_bytes, "qa.rebased_assemble.raw": reb_raw}
    except OSError as err:
        reb_status = None
        reb_resp_sha = None
        actual_files_sha = None
        reb_passed = False
        reb_reason = f"qa assemble request failed for rebased bundle: {type(err).__name__}: {err}"
        reb_blobs = {}

    reb_row = _make_row(
        case_id,
        phase_name,
        "rebased_files",
        request_sha256=rebased_req_sha,
        response_sha256=reb_resp_sha,
        qa_response_sha256=reb_resp_sha,
        qa_status=reb_status,
        expected={"http_status": 200, "files_sha256": files_sha},
        passed=reb_passed,
        status="passed" if reb_passed else "failed",
        reason=reb_reason,
    )
    reb_row["files_sha256"] = actual_files_sha
    reb_row["recorded_files_sha256"] = files_sha
    rows_out.append(reb_row)
    blobs_out.append(("rebased_files", reb_blobs))

    return rows_out, blobs_out, False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(
        description=(
            "Compare Rust compile responses between --qa and --baseline across run-003 "
            "and verify baseline bundle refusal (ASSET_HASH_MISMATCH) and rebased assembly."
        )
    )
    parser.add_argument("--qa", required=True, help="Loopback URL of the QA candidate server")
    parser.add_argument("--baseline", required=True, help="Loopback URL of the baseline server")
    parser.add_argument(
        "--run",
        type=pathlib.Path,
        default=DEFAULT_RUN,
        help="Path to the accepted rust-output-055/run-003 directory (default: %(default)s)",
    )
    parser.add_argument("--out", type=pathlib.Path, required=True, help="New output directory")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent HTTP workers (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        qa_url, qa_split = validate_url(args.qa, "--qa")
        baseline_url, base_split = validate_url(args.baseline, "--baseline")
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if same_endpoint(qa_url, qa_split, baseline_url, base_split):
        print("error: --qa and --baseline must not point to the same URL/endpoint", file=sys.stderr)
        return 2

    if args.workers < 1:
        print("error: --workers must be >= 1", file=sys.stderr)
        return 2

    out_dir: pathlib.Path = args.out
    if out_dir.exists() or out_dir.is_symlink():
        print(f"error: preserve existing output path: {out_dir}", file=sys.stderr)
        return 2

    started = time.time()
    run_dir = resolve_local_path(args.run)

    phase1_items, rp_sha, p1_problems = load_phase1_cases(run_dir)
    phase2_items, p2_record_hashes, p2_problems = load_phase2_cases(run_dir)
    run_problems = p1_problems + p2_problems

    all_items = phase1_items + phase2_items

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        case_results = list(
            pool.map(lambda item: evaluate_case(qa_url, baseline_url, item), all_items)
        )

    out_dir.mkdir(parents=True, exist_ok=False)
    responses_dir = out_dir / "responses"

    rows: list[dict[str, Any]] = []
    refusal_cases: list[str] = []

    for item, (case_rows, case_blobs, took_refusal) in zip(all_items, case_results):
        if took_refusal:
            refusal_cases.append(item["id"])
        for row, (chk_name, raw_blobs) in zip(case_rows, case_blobs):
            if (not row["passed"] or native_digest_context() is not None) and raw_blobs:
                responses_dir.mkdir(parents=True, exist_ok=True)
                prefix = f"{_safe_filename(row['phase'])}_{_safe_filename(row['id'])}_{_safe_filename(chk_name)}"
                saved_files: dict[str, str] = {}
                for suffix, blob in sorted(raw_blobs.items()):
                    rel_path = f"responses/{prefix}.{suffix}"
                    (out_dir / rel_path).write_bytes(blob)
                    saved_files[suffix] = rel_path
                row["saved_responses"] = saved_files
            rows.append(row)

    passed_rows = sum(1 for r in rows if r["status"] == "passed" and r["passed"])
    failed_rows = sum(1 for r in rows if r["status"] == "failed")
    not_run_rows = sum(1 for r in rows if r["status"] == "not_run")

    by_check: dict[str, dict[str, int]] = {}
    for chk in CHECKS:
        chk_rows = [r for r in rows if r["check"] == chk]
        by_check[chk] = {
            "total": len(chk_rows),
            "passed": sum(1 for r in chk_rows if r["status"] == "passed" and r["passed"]),
            "failed": sum(1 for r in chk_rows if r["status"] == "failed"),
            "not_run": sum(1 for r in chk_rows if r["status"] == "not_run"),
        }

    by_phase: dict[str, dict[str, int]] = {}
    for ph in ("phase1", "phase2"):
        ph_rows = [r for r in rows if r["phase"] == ph]
        by_phase[ph] = {
            "cases": len(phase1_items if ph == "phase1" else phase2_items),
            "total_rows": len(ph_rows),
            "passed": sum(1 for r in ph_rows if r["status"] == "passed" and r["passed"]),
            "failed": sum(1 for r in ph_rows if r["status"] == "failed"),
            "not_run": sum(1 for r in ph_rows if r["status"] == "not_run"),
        }

    p2_records_digest = hashlib.sha256(
        "".join(f"{cid}\0{sha}\n" for cid, sha in sorted(p2_record_hashes.items())).encode("utf-8")
    ).hexdigest()

    overall_passed = (
        len(rows) > 0
        and not run_problems
        and passed_rows == len(rows)
        and failed_rows == 0
        and not_run_rows == 0
    )

    if overall_passed:
        overall_status = "passed"
    elif failed_rows > 0 or run_problems:
        overall_status = "failed"
    else:
        overall_status = "not_run"

    report = {
        "version": "som.typescript-output-rust-baseline.v1",
        "qa": qa_url,
        "qa_url": qa_url,
        "baseline": baseline_url,
        "baseline_url": baseline_url,
        "run": str(run_dir.resolve()),
        "rust_products_sha256": rp_sha,
        "phase2_cases_sha256": p2_records_digest,
        "records_sha256": {
            "rust_products_json": rp_sha,
            "phase2_cases_digest": p2_records_digest,
            "phase2_case_files": p2_record_hashes,
        },
        "refusal_cases": sorted(refusal_cases),
        "rs12_rs13_cases": sorted(refusal_cases),
        "total": len(rows),
        "total_rows": len(rows),
        "passed_count": passed_rows,
        "passed_rows": passed_rows,
        "failed_count": failed_rows,
        "failed_rows": failed_rows,
        "not_run_count": not_run_rows,
        "not_run_rows": not_run_rows,
        "totals": {
            "phase1_cases": len(phase1_items),
            "phase2_cases": len(phase2_items),
            "total_cases": len(all_items),
            "refusal_cases": len(refusal_cases),
            "total_rows": len(rows),
            "passed": passed_rows,
            "failed": failed_rows,
            "not_run": not_run_rows,
            "by_check": by_check,
            "by_phase": by_phase,
        },
        "problems": run_problems,
        "status": overall_status,
        "passed": overall_passed,
        "seconds": time.time() - started,
        "rows": rows,
    }

    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if overall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
