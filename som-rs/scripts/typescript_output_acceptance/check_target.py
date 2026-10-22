#!/usr/bin/env python3
"""Check TypeScript phase 1 target selection and scope over HTTP (issue #98).

Sends compile requests to POST <url>/v1/compile and verifies:
- every target-examples.json example and name case body gives its expected result;
- every negative-cases.json case (TT, TS, TP) gives its expected http_status,
  diagnostic code, pointer and span in the raw request body (or CNL source) per
  the contract's span_model;
- for every public example and name case in case-inventory.json, target: "python"
  gives the same response bytes as the same request without target;
- for every out-of-scope public example, CNL positive fixture, and legacy job in
  case-inventory.json, target: "typescript" gives UNSUPPORTED_TARGET with the
  recorded refusal matching som-rs/tests/typescript-output-v1/ts_profile.py;
- no in-scope public example, version positive fixture, coverage case, name
  case, or legacy job gives UNSUPPORTED_TARGET with target: "typescript".

usage: check_target.py --url URL --out DIR [--legacy-run DIR]
"""
from __future__ import annotations

import argparse
import collections
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
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(TS_TESTS) not in sys.path:
    sys.path.insert(0, str(TS_TESTS))

import build_case_inventory  # type: ignore[import-not-found]  # noqa: E402
import check_python_baseline  # type: ignore[import-not-found]  # noqa: E402
import common  # type: ignore[import-not-found]  # noqa: E402
import ts_profile  # type: ignore[import-not-found]  # noqa: E402

CONTRACT = common.CONTRACT
EXAMPLES = common.EXAMPLES
COVERAGE_CASES = common.COVERAGE_CASES
REPO = common.REPO
rust_cases = common.rust_cases

DEFAULT_LEGACY_RUN = check_python_baseline.DEFAULT_LEGACY_RUN
EXPECTED_LEGACY = 544
EXPECTED_LEGACY_IN_SCOPE = 100

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SPAN_KEYS = ("line", "column", "end_line", "end_column", "byte_start", "byte_end")
TS_BUNDLE_VERSION = "som.dsl-bundle.v2"
PY_BUNDLE_VERSION = "som.dsl-bundle.v1"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compact_json(value: Any) -> str:
    return rust_cases.compact(value)


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


NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_compile(
    base_url: str,
    body: str,
    responses_dir: pathlib.Path,
    file_stem: str,
) -> tuple[int | None, bytes | None, str | None, str | None]:
    """Send POST <base_url>/v1/compile and persist the raw response bytes.

    Returns (http_status, raw_bytes, relative_response_path, error_message).
    When a connection error occurs, http_status and raw_bytes are None and
    error_message describes the failure.
    """
    endpoint = base_url.rstrip("/") + "/v1/compile"
    req = urllib.request.Request(
        endpoint,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with NO_PROXY_OPENER.open(req, timeout=60) as resp:
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


def verify_file_sha(path: pathlib.Path, expected_sha: str, label: str) -> list[str]:
    if not path.is_file():
        return [f"missing source file for {label}: {path}"]
    actual = build_case_inventory.sha(path)
    if actual != expected_sha:
        return [
            f"sha256 mismatch for {label} ({build_case_inventory.rel(path)}): "
            f"got {actual}, expected {expected_sha}"
        ]
    return []


def check_span_in_body(
    body: str,
    form: str,
    diag: dict[str, Any],
    expected_span_text: str | tuple[str, ...] | None = None,
) -> list[str]:
    """Verify diagnostic span coordinates and slice per the contract's span_model."""
    problems: list[str] = []
    for key in SPAN_KEYS:
        if not isinstance(diag.get(key), int):
            problems.append(f"diagnostic missing integer {key}: {diag.get(key)!r}")
    if problems:
        return problems

    pointer = diag.get("prompt_pointer", "")
    envelope = pointer in ("", "/target")
    if form == "cnl" and not envelope:
        try:
            source_text = json.loads(body)["source"]
        except Exception as exc:  # noqa: BLE001
            return [f"cannot parse CNL request JSON: {exc}"]
    else:
        source_text = body

    raw_bytes = source_text.encode("utf-8")
    byte_start = diag["byte_start"]
    byte_end = diag["byte_end"]
    if not (0 <= byte_start <= byte_end <= len(raw_bytes)):
        return [
            f"byte span [{byte_start}, {byte_end}) out of range for length {len(raw_bytes)}"
        ]
    try:
        sliced = raw_bytes[byte_start:byte_end].decode("utf-8")
        prefix = raw_bytes[:byte_start].decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"byte span [{byte_start}, {byte_end}) is not valid UTF-8: {exc}"]

    if expected_span_text is not None:
        allowed = (
            (expected_span_text,)
            if isinstance(expected_span_text, str)
            else expected_span_text
        )
        if sliced not in allowed:
            problems.append(
                f"span text {sliced!r} != expected {expected_span_text!r}"
            )

    char_start = len(prefix)
    char_end = char_start + len(sliced)
    recomputed = rust_cases.location(source_text, char_start, char_end)
    for key in SPAN_KEYS:
        if diag.get(key) != recomputed[key]:
            problems.append(
                f"span {key}: got {diag.get(key)!r}, recomputed {recomputed[key]!r}"
            )

    if (form == "json" or envelope) and pointer:
        try:
            spans = rust_cases.Spans(body)
        except Exception:  # noqa: BLE001
            spans = None
        if spans is not None:
            if diag.get("code") == "INVALID_JSON" and pointer in spans.keys and len(spans.keys[pointer]) > 1:
                expected_tok = rust_cases.location(body, *spans.keys[pointer][1])
                for key in SPAN_KEYS:
                    if diag.get(key) != expected_tok[key]:
                        problems.append(
                            f"duplicate-key span {key}: got {diag.get(key)!r}, expected {expected_tok[key]!r}"
                        )
            elif pointer in spans.values:
                expected_tok = rust_cases.location(body, *spans.values[pointer])
                for key in SPAN_KEYS:
                    if diag.get(key) != expected_tok[key]:
                        problems.append(
                            f"pointer {pointer} span {key}: got {diag.get(key)!r}, expected {expected_tok[key]!r}"
                        )
    return problems


def check_diagnostic_reply(
    status: int,
    raw: bytes,
    expected_http_status: int,
    expected_diag: dict[str, Any],
    body: str,
    form: str,
    expected_span_text: str | tuple[str, ...] | None = None,
) -> list[str]:
    problems: list[str] = []
    if status != expected_http_status:
        problems.append(f"http_status {status} != expected {expected_http_status}")
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
    if reply.get("code") != expected_diag["code"]:
        problems.append(
            f"reply.code {reply.get('code')!r} != expected {expected_diag['code']!r}"
        )
    if reply.get("detail") != expected_diag["cause"]:
        problems.append(
            f"reply.detail {reply.get('detail')!r} != expected {expected_diag['cause']!r}"
        )
    diagnostics = reply.get("diagnostics")
    if not isinstance(diagnostics, list) or len(diagnostics) != 1:
        problems.append(
            f"expected 1 diagnostic, got {len(diagnostics) if isinstance(diagnostics, list) else type(diagnostics).__name__}"
        )
        return problems
    diag = diagnostics[0]
    if not isinstance(diag, dict):
        return [*problems, "diagnostic is not an object"]
    for key, expected_val in expected_diag.items():
        if diag.get(key) != expected_val:
            problems.append(
                f"diagnostic.{key}: got {diag.get(key)!r} != expected {expected_val!r}"
            )
    problems.extend(check_span_in_body(body, form, diag, expected_span_text))
    return problems


def check_ts_bundle_reply(status: int, raw: bytes) -> tuple[list[str], dict[str, Any] | None]:
    problems: list[str] = []
    try:
        reply = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [f"invalid JSON response: {exc}"], None
    if not isinstance(reply, dict):
        return ["response is not a JSON object"], None
    if reply.get("code") == "UNSUPPORTED_TARGET":
        problems.append(
            f"unexpected UNSUPPORTED_TARGET: {reply.get('detail', '')}"
        )
    if status != 200:
        problems.append(f"http_status {status} != expected 200")
        return problems, reply
    if reply.get("status") != "ok":
        problems.append(f"reply.status {reply.get('status')!r} != 'ok'")
    bundle = reply.get("bundle")
    if not isinstance(bundle, dict):
        problems.append("missing bundle object in response")
        return problems, reply
    if bundle.get("version") != TS_BUNDLE_VERSION:
        problems.append(
            f"bundle.version {bundle.get('version')!r} != {TS_BUNDLE_VERSION!r}"
        )
    if bundle.get("target") != "typescript":
        problems.append(
            f"bundle.target {bundle.get('target')!r} != 'typescript'"
        )
    return problems, reply


def check_py_bundle_reply(status: int, raw: bytes) -> list[str]:
    problems: list[str] = []
    if status != 200:
        problems.append(f"http_status {status} != expected 200")
        return problems
    try:
        reply = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [f"invalid JSON response: {exc}"]
    if not isinstance(reply, dict) or reply.get("status") != "ok":
        problems.append(f"expected status 'ok', got {reply.get('status') if isinstance(reply, dict) else reply!r}")
        return problems
    bundle = reply.get("bundle")
    if not isinstance(bundle, dict) or bundle.get("version") != PY_BUNDLE_VERSION:
        problems.append(
            f"bundle.version {bundle.get('version') if isinstance(bundle, dict) else None!r} != {PY_BUNDLE_VERSION!r}"
        )
    return problems


def make_row(
    case_id: str,
    section: str,
    request_body: str,
    http_status: int | None,
    raw_response: bytes | None,
    response_file: str | None,
    expected: Any,
    problems: list[str],
    not_run_error: str | None = None,
) -> dict[str, Any]:
    req_sha = sha_bytes(request_body.encode("utf-8"))
    if not_run_error is not None:
        return {
            "id": case_id,
            "section": section,
            "status": "not_run",
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
        "request_sha256": req_sha,
        "http_status": http_status,
        "response_sha256": sha_bytes(raw_response) if raw_response is not None else None,
        "response_file": response_file,
        "expected": expected,
        "passed": passed,
        "reason": "ok" if passed else "; ".join(problems),
    }


def run_checks(
    base_url: str,
    out_dir: pathlib.Path,
    legacy_run: pathlib.Path = DEFAULT_LEGACY_RUN,
    *,
    expected_legacy: int | None = None,
    expected_legacy_in_scope: int | None = None,
) -> dict[str, Any]:
    responses_dir = out_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=False)

    inventory = json.loads(build_case_inventory.OUT.read_text(encoding="utf-8"))
    target_examples_doc = json.loads(
        (CONTRACT / "target-examples.json").read_text(encoding="utf-8")
    )
    negative_cases_doc = json.loads(
        (CONTRACT / "negative-cases.json").read_text(encoding="utf-8")
    )
    scope = ts_profile.load_scope()

    examples_sources: dict[str, str] = inventory["sources"]["examples"]
    fixtures_sources: dict[str, str] = inventory["sources"]["cnl_positive_fixtures"]
    coverage_sources: dict[str, str] = inventory["sources"]["coverage_index"]

    public_by_id = {row["id"]: row for row in inventory["public_examples"]}
    vp_by_id = {row["id"]: row for row in inventory["version_positive_fixtures"]}
    cov_by_id = {row["id"]: row for row in inventory["coverage_cases"]}
    name_by_id = {row["id"]: row for row in inventory["name_cases"]}

    rows: list[dict[str, Any]] = []

    # 1. target-examples.json: examples and name_cases
    example_rows_by_source: dict[str, list[tuple[dict[str, Any], bytes | None]]] = {}
    for item in target_examples_doc["examples"]:
        cid = item["id"]
        src_rel = item["source"]
        src_path = REPO / src_rel
        problems: list[str] = []
        if src_rel in examples_sources:
            problems.extend(verify_file_sha(src_path, examples_sources[src_rel], cid))
        else:
            base_id = cid.removesuffix("-cnl-typescript").removesuffix("-json-typescript")
            cov_row = cov_by_id.get(base_id)
            if cov_row is not None:
                key = "cnl_sha256" if src_rel.endswith(".cnl") else "prompt_file_sha256"
                problems.extend(verify_file_sha(src_path, cov_row[key], cid))

        status, raw, resp_file, err = post_compile(
            base_url, item["body"], responses_dir, f"target_examples__{cid}"
        )
        if err is not None:
            rows.append(
                make_row(
                    cid,
                    "target_examples",
                    item["body"],
                    None,
                    None,
                    None,
                    item["expected"],
                    [],
                    not_run_error=err,
                )
            )
            continue

        assert status is not None and raw is not None
        if item["target"] == "python":
            problems.extend(check_py_bundle_reply(status, raw))
            same_status, same_raw, _, same_err = post_compile(
                base_url,
                item["same_as_body"],
                responses_dir,
                f"target_examples__{cid}__without_target",
            )
            if same_err is not None:
                rows.append(
                    make_row(
                        cid,
                        "target_examples",
                        item["body"],
                        None,
                        None,
                        None,
                        item["expected"],
                        [],
                        not_run_error=same_err,
                    )
                )
                continue
            assert same_status is not None and same_raw is not None
            problems.extend(check_py_bundle_reply(same_status, same_raw))
            if raw != same_raw:
                problems.append(
                    "target python response bytes differ from response without target"
                )
            rows.append(
                make_row(
                    cid,
                    "target_examples",
                    item["body"],
                    status,
                    raw,
                    resp_file,
                    item["expected"],
                    problems,
                )
            )
        else:
            more_problems, _ = check_ts_bundle_reply(status, raw)
            problems.extend(more_problems)
            row = make_row(
                cid,
                "target_examples",
                item["body"],
                status,
                raw,
                resp_file,
                item["expected"],
                problems,
            )
            rows.append(row)
            base_key = src_rel.removesuffix(".cnl").removesuffix(".prompt.json")
            example_rows_by_source.setdefault(base_key, []).append((row, raw))

    for base_key, group in example_rows_by_source.items():
        if len(group) == 2:
            (row_a, raw_a), (row_b, raw_b) = group
            if (
                row_a["passed"]
                and row_b["passed"]
                and raw_a is not None
                and raw_b is not None
                and raw_a != raw_b
            ):
                msg = f"CNL and JSON forms of {base_key} gave different TypeScript bundle bytes"
                for r in (row_a, row_b):
                    r["passed"] = False
                    r["status"] = "failed"
                    r["reason"] = msg if r["reason"] == "ok" else f"{r['reason']}; {msg}"

    for item in target_examples_doc["name_cases"]:
        cid = item["id"]
        problems = []
        inv_name = name_by_id.get(cid)
        if inv_name is None:
            problems.append(f"{cid} missing from case-inventory.json name_cases")
        else:
            actual_body_sha = sha_bytes(item["body"].encode("utf-8"))
            if actual_body_sha != inv_name["body_sha256"]:
                problems.append(
                    f"body_sha256 mismatch for {cid}: got {actual_body_sha}, expected {inv_name['body_sha256']}"
                )
        base_rel = f"som-rs/contracts/compiler-v1/examples/{item['base_example']}"
        if base_rel in examples_sources:
            problems.extend(
                verify_file_sha(REPO / base_rel, examples_sources[base_rel], cid)
            )

        status, raw, resp_file, err = post_compile(
            base_url, item["body"], responses_dir, f"name_cases__{cid}"
        )
        if err is not None:
            rows.append(
                make_row(
                    cid,
                    "name_cases",
                    item["body"],
                    None,
                    None,
                    None,
                    item["expected"],
                    [],
                    not_run_error=err,
                )
            )
            continue
        assert status is not None and raw is not None
        more_problems, _ = check_ts_bundle_reply(status, raw)
        problems.extend(more_problems)
        rows.append(
            make_row(
                cid,
                "name_cases",
                item["body"],
                status,
                raw,
                resp_file,
                item["expected"],
                problems,
            )
        )

    # 2. negative-cases.json: TT, TS, TP
    for case in negative_cases_doc["cases"]:
        cid = case["id"]
        problems = []
        base_rel = f"som-rs/contracts/compiler-v1/examples/{case['base_example']}"
        if base_rel in examples_sources:
            problems.extend(
                verify_file_sha(REPO / base_rel, examples_sources[base_rel], cid)
            )

        status, raw, resp_file, err = post_compile(
            base_url, case["body"], responses_dir, f"negative_cases__{cid}"
        )
        expected_desc = {
            "http_status": case["http_status"],
            **case["expected"],
            "span_text": case["span_text"],
        }
        if err is not None:
            rows.append(
                make_row(
                    cid,
                    "negative_cases",
                    case["body"],
                    None,
                    None,
                    None,
                    expected_desc,
                    [],
                    not_run_error=err,
                )
            )
            continue
        assert status is not None and raw is not None
        problems.extend(
            check_diagnostic_reply(
                status,
                raw,
                case["http_status"],
                case["expected"],
                case["body"],
                case["form"],
                expected_span_text=case["span_text"],
            )
        )
        rows.append(
            make_row(
                cid,
                "negative_cases",
                case["body"],
                status,
                raw,
                resp_file,
                expected_desc,
                problems,
            )
        )

    # 3. python_equivalence: every public example (cnl & json) and name case in case-inventory.json
    for pub in inventory["public_examples"]:
        pub_id = pub["id"]
        prompt_path = REPO / pub["prompt"]
        cnl_path = REPO / pub["cnl"] if pub.get("cnl") else None

        prompt_problems = verify_file_sha(
            prompt_path, pub["prompt_file_sha256"], f"{pub_id}:json"
        )
        if pub["prompt"] in examples_sources:
            prompt_problems.extend(
                verify_file_sha(
                    prompt_path, examples_sources[pub["prompt"]], f"{pub_id}:sources.examples"
                )
            )
        prompt_obj = json.loads(prompt_path.read_text(encoding="utf-8")) if prompt_path.is_file() else {}
        json_no_target = compact_json({"format": "som.prompt-json.v1", "prompt": prompt_obj})
        json_py_target = compact_json(
            {"format": "som.prompt-json.v1", "prompt": prompt_obj, "target": "python"}
        )
        rows.append(
            _check_python_pair(
                base_url,
                responses_dir,
                f"{pub_id}-json-python-equivalence",
                json_py_target,
                json_no_target,
                list(prompt_problems),
            )
        )

        if cnl_path is not None and pub.get("cnl_sha256"):
            cnl_problems = verify_file_sha(
                cnl_path, pub["cnl_sha256"], f"{pub_id}:cnl"
            )
            if pub["cnl"] in examples_sources:
                cnl_problems.extend(
                    verify_file_sha(
                        cnl_path, examples_sources[pub["cnl"]], f"{pub_id}:sources.examples"
                    )
                )
            cnl_text = cnl_path.read_text(encoding="utf-8") if cnl_path.is_file() else ""
            cnl_no_target = compact_json({"format": "som.cnl.v1", "source": cnl_text})
            cnl_py_target = compact_json(
                {"format": "som.cnl.v1", "source": cnl_text, "target": "python"}
            )
            rows.append(
                _check_python_pair(
                    base_url,
                    responses_dir,
                    f"{pub_id}-cnl-python-equivalence",
                    cnl_py_target,
                    cnl_no_target,
                    list(cnl_problems),
                )
            )

    for item in target_examples_doc["name_cases"]:
        cid = item["id"]
        problems = []
        inv_name = name_by_id.get(cid)
        if inv_name is None:
            problems.append(f"{cid} missing from case-inventory.json name_cases")
        else:
            actual_body_sha = sha_bytes(item["body"].encode("utf-8"))
            if actual_body_sha != inv_name["body_sha256"]:
                problems.append(
                    f"body_sha256 mismatch for {cid}: got {actual_body_sha}, expected {inv_name['body_sha256']}"
                )
        base_rel = f"som-rs/contracts/compiler-v1/examples/{item['base_example']}"
        if base_rel in examples_sources:
            problems.extend(
                verify_file_sha(REPO / base_rel, examples_sources[base_rel], cid)
            )
        no_target_body = item["python_control_body"]
        no_target_obj = json.loads(no_target_body)
        py_target_body = compact_json({**no_target_obj, "target": "python"})
        rows.append(
            _check_python_pair(
                base_url,
                responses_dir,
                f"{cid}-python-equivalence",
                py_target_body,
                no_target_body,
                problems,
            )
        )

    # 4 & 5. Scope checks across public_examples, cnl-positive-fixtures.json, coverage_cases, and name_cases
    # 4a / 5a: public_examples with target: "typescript"
    for pub in inventory["public_examples"]:
        pub_id = pub["id"]
        expected_rule = pub["typescript"]
        prompt_path = REPO / pub["prompt"]
        cnl_path = REPO / pub["cnl"] if pub.get("cnl") else None

        base_problems = verify_file_sha(
            prompt_path, pub["prompt_file_sha256"], f"{pub_id}:prompt"
        )
        if cnl_path is not None and pub.get("cnl_sha256"):
            base_problems.extend(
                verify_file_sha(cnl_path, pub["cnl_sha256"], f"{pub_id}:cnl")
            )
        prompt_obj = (
            json.loads(prompt_path.read_text(encoding="utf-8"))
            if prompt_path.is_file()
            else {}
        )
        cnl_text = (
            cnl_path.read_text(encoding="utf-8")
            if cnl_path is not None and cnl_path.is_file()
            else None
        )
        _check_scope_forms(
            base_url,
            responses_dir,
            rows,
            pub_id,
            prompt_obj,
            cnl_text,
            expected_rule,
            scope,
            base_problems,
        )

    # 4b / 5b: cnl-positive-fixtures.json (both version_positive and out-of-scope capability/flow fixtures)
    fixtures_rel = build_case_inventory.rel(build_case_inventory.FIXTURES)
    fixtures_file_problems = verify_file_sha(
        build_case_inventory.FIXTURES,
        fixtures_sources.get(fixtures_rel, ""),
        "cnl_positive_fixtures",
    )
    fixtures_doc = (
        json.loads(build_case_inventory.FIXTURES.read_text(encoding="utf-8"))
        if build_case_inventory.FIXTURES.is_file()
        else {"cases": []}
    )
    expected_cat_rules: dict[tuple[str, str], int] = {
        (entry["category"], entry["typescript"]): entry["count"]
        for entry in inventory["counts"]["cnl_positive_fixtures"]
    }
    actual_cat_counts: collections.Counter[tuple[str, str]] = collections.Counter()

    for fix in fixtures_doc.get("cases", []):
        fid = fix["id"]
        category = fix["category"]
        prompt_obj = fix["expected_prompt"]
        cnl_text = fix["cnl"]
        fix_problems = list(fixtures_file_problems)

        actual_prompt_sha = sha_bytes(canonical_json(prompt_obj).encode("utf-8"))
        actual_cnl_sha = sha_bytes(cnl_text.encode("utf-8"))
        if actual_prompt_sha != fix.get("prompt_sha256"):
            fix_problems.append(
                f"prompt_sha256 mismatch for {fid}: got {actual_prompt_sha}, expected {fix.get('prompt_sha256')}"
            )
        if actual_cnl_sha != fix.get("cnl_sha256"):
            fix_problems.append(
                f"cnl_sha256 mismatch for {fid}: got {actual_cnl_sha}, expected {fix.get('cnl_sha256')}"
            )

        profile_rule = build_case_inventory.classify(prompt_obj, scope)
        actual_cat_counts[(category, profile_rule)] += 1

        if category == "version_positive":
            vp_row = vp_by_id.get(fid)
            if vp_row is None:
                fix_problems.append(
                    f"{fid} missing from case-inventory.json version_positive_fixtures"
                )
                expected_rule = profile_rule
            else:
                expected_rule = vp_row["typescript"]
                if vp_row["prompt_sha256"] != actual_prompt_sha:
                    fix_problems.append(
                        f"inventory prompt_sha256 mismatch for {fid}: got {actual_prompt_sha}, expected {vp_row['prompt_sha256']}"
                    )
                if vp_row["cnl_sha256"] != actual_cnl_sha:
                    fix_problems.append(
                        f"inventory cnl_sha256 mismatch for {fid}: got {actual_cnl_sha}, expected {vp_row['cnl_sha256']}"
                    )
            _check_scope_forms(
                base_url,
                responses_dir,
                rows,
                fid,
                prompt_obj,
                cnl_text,
                expected_rule,
                scope,
                fix_problems,
            )
        else:
            matching_rules = [
                r for (cat, r) in expected_cat_rules if cat == category
            ]
            expected_rule = matching_rules[0] if len(matching_rules) == 1 else profile_rule
            _check_scope_forms(
                base_url,
                responses_dir,
                rows,
                fid,
                prompt_obj,
                cnl_text,
                expected_rule,
                scope,
                fix_problems,
                forms=("cnl",),
            )

    if dict(actual_cat_counts) != expected_cat_rules and rows:
        rows[-1]["passed"] = False
        rows[-1]["status"] = "failed"
        rows[-1]["reason"] = (
            f"cnl_positive_fixtures category/rule counts {dict(actual_cat_counts)} "
            f"!= expected {expected_cat_rules}"
        )

    # 5c: coverage_cases with target: "typescript"
    cov_rel = build_case_inventory.rel(build_case_inventory.COVERAGE)
    cov_index_problems = verify_file_sha(
        build_case_inventory.COVERAGE,
        coverage_sources.get(cov_rel, ""),
        "coverage_index",
    )
    cov_doc = (
        json.loads(build_case_inventory.COVERAGE.read_text(encoding="utf-8"))
        if build_case_inventory.COVERAGE.is_file()
        else {"cases": []}
    )
    for cov in cov_doc.get("cases", []):
        cid = cov["id"]
        cov_problems = list(cov_index_problems)
        inv_cov = cov_by_id.get(cid)
        prompt_path = COVERAGE_CASES / cov["prompt"]
        cnl_path = COVERAGE_CASES / cov["cnl"]
        if inv_cov is None:
            cov_problems.append(f"{cid} missing from case-inventory.json coverage_cases")
            expected_rule = "in_scope"
        else:
            expected_rule = inv_cov["typescript"]
            cov_problems.extend(
                verify_file_sha(prompt_path, inv_cov["prompt_file_sha256"], f"{cid}:prompt")
            )
            cov_problems.extend(
                verify_file_sha(cnl_path, inv_cov["cnl_sha256"], f"{cid}:cnl")
            )
        prompt_obj = (
            json.loads(prompt_path.read_text(encoding="utf-8"))
            if prompt_path.is_file()
            else {}
        )
        cnl_text = cnl_path.read_text(encoding="utf-8") if cnl_path.is_file() else ""
        _check_scope_forms(
            base_url,
            responses_dir,
            rows,
            cid,
            prompt_obj,
            cnl_text,
            expected_rule,
            scope,
            cov_problems,
        )

    # 6. Legacy 544 jobs (run-005) with target: "typescript"
    legacy_run_resolved = check_python_baseline.resolve_local_path(pathlib.Path(legacy_run))
    legacy_problems = _check_legacy_jobs(
        base_url,
        responses_dir,
        rows,
        legacy_run_resolved,
        inventory,
        scope,
        expected_legacy=expected_legacy,
        expected_legacy_in_scope=expected_legacy_in_scope,
    )

    all_passed = bool(rows) and not legacy_problems and all(r["passed"] for r in rows)
    report: dict[str, Any] = {
        "version": "som.typescript-output-target-checks.v1",
        "issue": 98,
        "url": base_url,
        "legacy_run": str(legacy_run_resolved.resolve()),
        "status": "passed" if all_passed else "failed",
        "passed": all_passed,
        "total": len(rows),
        "passed_count": sum(1 for r in rows if r["passed"]),
        "failed_count": sum(1 for r in rows if not r["passed"]),
        "not_run_count": sum(1 for r in rows if r["status"] == "not_run"),
        "problems": legacy_problems,
        "cases": rows,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def _check_legacy_jobs(
    base_url: str,
    responses_dir: pathlib.Path,
    rows: list[dict[str, Any]],
    legacy_run: pathlib.Path,
    inventory: dict[str, Any],
    scope: dict[str, Any],
    *,
    expected_legacy: int | None = None,
    expected_legacy_in_scope: int | None = None,
) -> list[str]:
    legacy_problems: list[str] = []
    try:
        legacy_inputs, legacy_meta = check_python_baseline.read_legacy_inputs(legacy_run)
    except (OSError, ValueError) as err:
        legacy_inputs = []
        legacy_meta = {}
        legacy_problems.append(
            f"cannot read legacy run {legacy_run}: {type(err).__name__}: {err}"
        )

    legacy_records: list[dict[str, Any]] = []
    result_path = legacy_run / "result.json"
    if result_path.is_file():
        try:
            raw_res = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(raw_res, dict) and isinstance(raw_res.get("records"), list):
                legacy_records = raw_res["records"]
        except (OSError, ValueError):
            legacy_records = []

    in_scope_list: list[dict[str, Any]] = inventory.get("legacy_in_scope", [])
    in_scope_by_key: dict[tuple[int, str], dict[str, Any]] = {
        (int(entry["inventory_index"]), str(entry["prompt_sha256"])): entry
        for entry in in_scope_list
    }
    in_scope_by_index: dict[int, dict[str, Any]] = {
        int(entry["inventory_index"]): entry for entry in in_scope_list
    }
    in_scope_by_sha: dict[str, list[dict[str, Any]]] = {}
    for entry in in_scope_list:
        in_scope_by_sha.setdefault(str(entry["prompt_sha256"]), []).append(entry)

    inv_legacy_counts_list: list[dict[str, Any]] = inventory.get("counts", {}).get("legacy", [])
    inv_in_scope_count = int(
        inventory.get("counts", {}).get("legacy_in_scope", len(in_scope_list))
    )

    exp_legacy = EXPECTED_LEGACY if expected_legacy is None else expected_legacy
    exp_in_scope = (
        EXPECTED_LEGACY_IN_SCOPE
        if expected_legacy_in_scope is None
        else expected_legacy_in_scope
    )

    actual_legacy_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    in_scope_count = 0
    legacy_rows: list[dict[str, Any]] = []

    for item in legacy_inputs:
        job_id = str(item["id"])
        idx = int(item["index"])
        rec = (
            legacy_records[idx]
            if idx < len(legacy_records) and isinstance(legacy_records[idx], dict)
            else {}
        )
        group = rec.get("group")
        if not group:
            if idx in in_scope_by_index:
                group = in_scope_by_index[idx].get("group")
            elif "-" in job_id:
                group = job_id.rsplit("-", 1)[0]

        if item.get("not_run_reason") is not None or item.get("body") is None:
            rec_sha = str(item.get("prompt_sha256") or "")
            is_in_scope = (idx, rec_sha) in in_scope_by_key
            expected_desc: Any = (
                "HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript (never UNSUPPORTED_TARGET)."
                if is_in_scope
                else {"http_status": 422, "code": "UNSUPPORTED_TARGET"}
            )
            not_run_row = {
                "id": job_id,
                "section": "legacy",
                "inventory_index": idx,
                "group": group,
                "in_scope": is_in_scope,
                "prompt_sha256": item.get("prompt_sha256"),
                "status": "not_run",
                "request_sha256": None,
                "http_status": None,
                "response_sha256": None,
                "response_file": None,
                "expected": expected_desc,
                "passed": False,
                "reason": item.get("not_run_reason") or f"missing compile request for {job_id}",
            }
            rows.append(not_run_row)
            legacy_rows.append(not_run_row)
            continue

        req_obj = item["body"]
        job_problems: list[str] = []
        if "target" in req_obj:
            job_problems.append(
                f"recorded compile request already contains a 'target' key ({req_obj['target']!r})"
            )
        prompt_obj = req_obj.get("prompt")
        if not isinstance(prompt_obj, dict):
            job_problems.append(
                f"recorded compile request for {job_id} missing 'prompt' object"
            )
            prompt_obj = {}

        canon_sha = sha_bytes(canonical_json(prompt_obj).encode("utf-8")) if prompt_obj else ""
        compact_sha = sha_bytes(compact_json(prompt_obj).encode("utf-8")) if prompt_obj else ""
        recorded_sha = item.get("prompt_sha256")
        if recorded_sha is not None:
            prompt_sha = str(recorded_sha)
            if prompt_obj and prompt_sha not in (canon_sha, compact_sha):
                job_problems.append(
                    f"prompt_sha256 mismatch for {job_id}: prompt hashes to {canon_sha}, record has {prompt_sha}"
                )
        else:
            prompt_sha = canon_sha

        in_scope_entry = in_scope_by_key.get((idx, prompt_sha))
        if (
            in_scope_entry is not None
            and prompt_obj
            and in_scope_entry["prompt_sha256"] not in (canon_sha, compact_sha)
        ):
            in_scope_entry = None
        is_in_scope = in_scope_entry is not None

        if idx in in_scope_by_index and not is_in_scope:
            job_problems.append(
                f"legacy_in_scope prompt_sha256 mismatch at inventory_index {idx}: "
                f"got {prompt_sha}, expected {in_scope_by_index[idx]['prompt_sha256']}"
            )
        elif prompt_sha in in_scope_by_sha and not is_in_scope:
            job_problems.append(
                f"legacy_in_scope inventory_index mismatch for prompt {prompt_sha}: got {idx}"
            )

        found = ts_profile.check(prompt_obj, scope) if prompt_obj else None
        actual_rule = "in_scope" if found is None else found["rule"]
        prompt_version = str(prompt_obj.get("version", "")) if prompt_obj else ""
        if group is not None and prompt_version:
            actual_legacy_counts[(str(group), prompt_version, actual_rule)] += 1

        if is_in_scope:
            in_scope_count += 1
            if actual_rule != "in_scope":
                job_problems.append(
                    f"ts_profile.check rule {actual_rule!r} != 'in_scope' for in-scope legacy job {job_id}"
                )
            expected_desc = (
                "HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript (never UNSUPPORTED_TARGET)."
            )
        else:
            if found is None:
                job_problems.append(
                    f"expected out-of-scope refusal for {job_id}, but ts_profile.check returned None"
                )
                expected_desc = {"http_status": 422, "code": "UNSUPPORTED_TARGET"}
            else:
                pointer = "/prompt" + found["pointer"]
                expected_desc = {
                    "http_status": 422,
                    "rule": found["rule"],
                    "code": "UNSUPPORTED_TARGET",
                    "prompt_pointer": pointer,
                    "cause": found["cause"],
                    "allowed_form": found["allowed_form"],
                }

        ts_body = compact_json({**req_obj, "target": "typescript"})
        status, raw, resp_file, err = post_compile(
            base_url,
            ts_body,
            responses_dir,
            f"legacy__{idx:04d}__{job_id}",
        )
        if err is not None:
            row = make_row(
                job_id,
                "legacy",
                ts_body,
                None,
                None,
                None,
                expected_desc,
                [],
                not_run_error=err,
            )
            row["inventory_index"] = idx
            row["group"] = group
            row["in_scope"] = is_in_scope
            row["prompt_sha256"] = prompt_sha
            rows.append(row)
            legacy_rows.append(row)
            continue

        assert status is not None and raw is not None
        if is_in_scope:
            more_problems, _ = check_ts_bundle_reply(status, raw)
            job_problems.extend(more_problems)
        else:
            if found is not None:
                pointer = "/prompt" + found["pointer"]
                expected_diag = {
                    "code": "UNSUPPORTED_TARGET",
                    "prompt_pointer": pointer,
                    "cause": found["cause"],
                    "allowed_form": found["allowed_form"],
                }
                leaf = resolve_pointer(prompt_obj, found["pointer"])
                expected_span = compact_json(leaf)
                job_problems.extend(
                    check_diagnostic_reply(
                        status,
                        raw,
                        422,
                        expected_diag,
                        ts_body,
                        "json",
                        expected_span_text=expected_span,
                    )
                )
            else:
                if status != 422:
                    job_problems.append(f"http_status {status} != expected 422")

        row = make_row(
            job_id,
            "legacy",
            ts_body,
            status,
            raw,
            resp_file,
            expected_desc,
            job_problems,
        )
        row["inventory_index"] = idx
        row["group"] = group
        row["in_scope"] = is_in_scope
        row["prompt_sha256"] = prompt_sha
        rows.append(row)
        legacy_rows.append(row)

    if len(legacy_inputs) != exp_legacy:
        legacy_problems.append(
            f"legacy job count is {len(legacy_inputs)}, expected {exp_legacy}"
        )
    elif legacy_meta.get("accepted_required") not in (None, exp_legacy):
        legacy_problems.append(
            f"legacy result.json required_job_count is {legacy_meta.get('accepted_required')}, expected {exp_legacy}"
        )

    if in_scope_count != exp_in_scope:
        legacy_problems.append(
            f"legacy in-scope job count is {in_scope_count}, expected {exp_in_scope}"
        )
    elif in_scope_count != inv_in_scope_count or in_scope_count != len(in_scope_list):
        legacy_problems.append(
            f"legacy in-scope job count is {in_scope_count}, case-inventory.json expects {inv_in_scope_count} ({len(in_scope_list)} entries)"
        )

    expected_legacy_counts: dict[tuple[str, str, str], int] = {
        (str(entry["group"]), str(entry["prompt_version"]), str(entry["typescript"])): int(
            entry["count"]
        )
        for entry in inv_legacy_counts_list
    }
    expected_legacy_refusal_counts: dict[tuple[str, str, str], int] = {
        k: v for k, v in expected_legacy_counts.items() if k[2] != "in_scope"
    }
    actual_legacy_refusal_counts: dict[tuple[str, str, str], int] = {
        k: v for k, v in actual_legacy_counts.items() if k[2] != "in_scope"
    }
    if actual_legacy_refusal_counts != expected_legacy_refusal_counts:
        legacy_problems.append(
            f"legacy group refusal counts {actual_legacy_refusal_counts} != expected {expected_legacy_refusal_counts}"
        )
    elif dict(actual_legacy_counts) != expected_legacy_counts:
        legacy_problems.append(
            f"legacy group counts {dict(actual_legacy_counts)} != expected {expected_legacy_counts}"
        )

    if (
        legacy_problems
        and legacy_rows
        and all(r["status"] == "passed" for r in legacy_rows)
    ):
        msg = "; ".join(legacy_problems)
        last = legacy_rows[-1]
        last["passed"] = False
        last["status"] = "failed"
        last["reason"] = msg if last["reason"] == "ok" else f"{last['reason']}; {msg}"

    return legacy_problems


def _check_python_pair(
    base_url: str,
    responses_dir: pathlib.Path,
    row_id: str,
    py_body: str,
    no_target_body: str,
    problems: list[str],
) -> dict[str, Any]:
    expected = "HTTP 200 with the same response bytes as the same request without target."
    py_status, py_raw, py_file, py_err = post_compile(
        base_url, py_body, responses_dir, f"python_equivalence__{row_id}__python"
    )
    if py_err is not None:
        return make_row(
            row_id,
            "python_equivalence",
            py_body,
            None,
            None,
            None,
            expected,
            [],
            not_run_error=py_err,
        )
    plain_status, plain_raw, _, plain_err = post_compile(
        base_url,
        no_target_body,
        responses_dir,
        f"python_equivalence__{row_id}__no_target",
    )
    if plain_err is not None:
        return make_row(
            row_id,
            "python_equivalence",
            py_body,
            None,
            None,
            None,
            expected,
            [],
            not_run_error=plain_err,
        )
    assert py_status is not None and py_raw is not None
    assert plain_status is not None and plain_raw is not None
    problems.extend(check_py_bundle_reply(py_status, py_raw))
    problems.extend(check_py_bundle_reply(plain_status, plain_raw))
    if py_raw != plain_raw:
        problems.append(
            "target python response bytes differ from response without target"
        )
    return make_row(
        row_id,
        "python_equivalence",
        py_body,
        py_status,
        py_raw,
        py_file,
        expected,
        problems,
    )


def _check_scope_forms(
    base_url: str,
    responses_dir: pathlib.Path,
    rows: list[dict[str, Any]],
    base_id: str,
    prompt_obj: dict[str, Any],
    cnl_text: str | None,
    expected_rule: str,
    scope: dict[str, Any],
    base_problems: list[str],
    forms: tuple[str, ...] = ("cnl", "json"),
) -> None:
    found = ts_profile.check(prompt_obj, scope) if prompt_obj else None
    actual_rule = "in_scope" if found is None else found["rule"]
    rule_problems = list(base_problems)
    if actual_rule != expected_rule:
        rule_problems.append(
            f"ts_profile.check rule {actual_rule!r} != case-inventory.json {expected_rule!r}"
        )

    section = "in_scope" if expected_rule == "in_scope" else "out_of_scope"
    pair_results: list[tuple[dict[str, Any], bytes | None]] = []

    for form in forms:
        if form == "cnl":
            if cnl_text is None:
                continue
            body = compact_json(
                {"format": "som.cnl.v1", "source": cnl_text, "target": "typescript"}
            )
        else:
            body = compact_json(
                {
                    "format": "som.prompt-json.v1",
                    "prompt": prompt_obj,
                    "target": "typescript",
                }
            )
        row_id = f"{base_id}-{form}-typescript"
        status, raw, resp_file, err = post_compile(
            base_url, body, responses_dir, f"{section}__{row_id}"
        )
        if expected_rule == "in_scope":
            expected_desc: Any = (
                "HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript (never UNSUPPORTED_TARGET)."
            )
        else:
            pointer = (
                found["pointer"]
                if (found and form == "cnl")
                else ("/prompt" + found["pointer"] if found else "")
            )
            expected_desc = {
                "http_status": 422,
                "rule": expected_rule,
                "code": "UNSUPPORTED_TARGET",
                "prompt_pointer": pointer,
                "cause": found["cause"] if found else "",
                "allowed_form": found["allowed_form"] if found else "",
            }

        if err is not None:
            rows.append(
                make_row(
                    row_id,
                    section,
                    body,
                    None,
                    None,
                    None,
                    expected_desc,
                    [],
                    not_run_error=err,
                )
            )
            continue

        assert status is not None and raw is not None
        problems = list(rule_problems)
        if expected_rule == "in_scope":
            more, _ = check_ts_bundle_reply(status, raw)
            problems.extend(more)
            row = make_row(
                row_id, section, body, status, raw, resp_file, expected_desc, problems
            )
            rows.append(row)
            pair_results.append((row, raw))
        else:
            if found is None:
                problems.append(
                    f"expected out-of-scope refusal {expected_rule!r}, but ts_profile returned None"
                )
            else:
                pointer = (
                    found["pointer"]
                    if form == "cnl"
                    else "/prompt" + found["pointer"]
                )
                expected_diag = {
                    "code": "UNSUPPORTED_TARGET",
                    "prompt_pointer": pointer,
                    "cause": found["cause"],
                    "allowed_form": found["allowed_form"],
                }
                leaf = resolve_pointer(prompt_obj, found["pointer"])
                if form == "json":
                    expected_span: str | tuple[str, ...] = compact_json(leaf)
                else:
                    text = (
                        leaf.removeprefix("som.prompt.v")
                        if found["rule"] == "prompt_version"
                        else str(leaf)
                    )
                    expected_span = (text, compact_json(text))
                problems.extend(
                    check_diagnostic_reply(
                        status,
                        raw,
                        422,
                        expected_diag,
                        body,
                        form,
                        expected_span_text=expected_span,
                    )
                )
            rows.append(
                make_row(
                    row_id,
                    section,
                    body,
                    status,
                    raw,
                    resp_file,
                    expected_desc,
                    problems,
                )
            )

    if expected_rule == "in_scope" and len(pair_results) == 2:
        (row_a, raw_a), (row_b, raw_b) = pair_results
        if (
            row_a["passed"]
            and row_b["passed"]
            and raw_a is not None
            and raw_b is not None
            and raw_a != raw_b
        ):
            msg = f"CNL and JSON forms of {base_id} gave different TypeScript bundle bytes"
            for r in (row_a, row_b):
                r["passed"] = False
                r["status"] = "failed"
                r["reason"] = msg if r["reason"] == "ok" else f"{r['reason']}; {msg}"


def main(
    argv: list[str] | None = None,
    *,
    expected_legacy: int | None = None,
    expected_legacy_in_scope: int | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="Loopback URL of the candidate som server")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="New output directory for report.json and responses/")
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
        expected_legacy=expected_legacy,
        expected_legacy_in_scope=expected_legacy_in_scope,
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
