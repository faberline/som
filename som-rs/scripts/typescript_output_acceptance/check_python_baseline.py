#!/usr/bin/env python3
"""Compare Python compile and assemble responses between a QA candidate and baseline (#103).

Every typed prompt of the 127 public products (public-products-003b) and the
544 legacy jobs (legacy544/run-005), sent without target and with
target: "python", must give a compile response equal byte for byte to the
baseline compiler's response. Assembling that bundle must also give response
bytes equal to the baseline's.

usage:
  check_python_baseline.py --qa URL --baseline URL --out DIR
    [--public-run DIR] [--legacy-run DIR] [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
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
DEFAULT_PUBLIC_RUN = LOCAL_ACCEPTANCE / "public-products-003b"
DEFAULT_LEGACY_RUN = LOCAL_ACCEPTANCE / "legacy544" / "run-005"

FORBIDDEN_PORTS = {8765, 18765}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

EXPECTED_PUBLIC = 127
EXPECTED_LEGACY = 544
VARIANTS = ("none", "python")

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
    qa_path = qa_split.path.rstrip("/")
    base_path = base_split.path.rstrip("/")
    return (
        qa_split.scheme == base_split.scheme
        and qa_split.port == base_split.port
        and qa_path == base_path
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
        return error.code, error.read()


def _load_request_file(directory: pathlib.Path, label: str = "compile-json") -> tuple[dict[str, Any], bytes | None]:
    """Read <label>.request.json (and optional <label>.request.raw) like python_replay.py."""
    req_json = directory / f"{label}.request.json"
    if not req_json.is_file() and label == "compile-json":
        fallback = directory / "compile.request.json"
        if fallback.is_file():
            req_json = fallback
            label = "compile"
    text = req_json.read_text(encoding="utf-8")
    body = json.loads(text)
    if not isinstance(body, dict):
        raise ValueError(f"{req_json} is not a JSON object")
    raw_file = directory / f"{label}.request.raw"
    raw_bytes = raw_file.read_bytes() if raw_file.is_file() else None
    return body, raw_bytes


def read_public_inputs(run: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read public-products run inputs following python_replay.public_rows layout."""
    result_path = run / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    records = result.get("records")
    if not isinstance(records, list):
        raise ValueError(f"{result_path} missing 'records' list")
    inputs: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        case_id = str(record.get("id", f"public-{index:03d}"))
        directory = run / f"{index:03d}"
        entry: dict[str, Any] = {
            "id": case_id,
            "source": "public",
            "index": index,
            "directory": directory,
            "body": None,
            "raw_bytes": None,
            "not_run_reason": None,
        }
        if not directory.is_dir():
            entry["not_run_reason"] = f"missing case directory: {directory}"
            inputs.append(entry)
            continue
        case_result_path = directory / "result.json"
        if case_result_path.is_file():
            try:
                kept = json.loads(case_result_path.read_text(encoding="utf-8"))
                if isinstance(kept, dict) and "id" in kept and kept.get("id") != case_id:
                    entry["not_run_reason"] = (
                        f"case directory {directory.name} belongs to {kept.get('id')!r}, expected {case_id!r}"
                    )
                    inputs.append(entry)
                    continue
            except (OSError, ValueError) as err:
                entry["not_run_reason"] = f"invalid case result.json in {directory.name}: {type(err).__name__}: {err}"
                inputs.append(entry)
                continue
        try:
            body, raw_bytes = _load_request_file(directory, "compile-json")
            entry["body"] = body
            entry["raw_bytes"] = raw_bytes
        except (OSError, ValueError) as err:
            entry["not_run_reason"] = f"cannot read compile request in {directory.name}: {type(err).__name__}: {err}"
        inputs.append(entry)
    meta = {
        "result_sha256": sha_file(result_path),
        "accepted_required": result.get("required"),
        "accepted_passed": result.get("passed"),
        "records_count": len(records),
    }
    return inputs, meta


def read_legacy_inputs(run: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read legacy544 run inputs following python_replay.legacy_rows layout."""
    result_path = run / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    records = result.get("records")
    if not isinstance(records, list):
        raise ValueError(f"{result_path} missing 'records' list")
    compile_root = run / "compile"
    request_cache: dict[pathlib.Path, tuple[dict[str, Any], bytes | None]] = {}
    inputs: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        job_id = str(record.get("id", f"legacy-{index:04d}"))
        prompt_sha = record.get("prompt_sha256")
        job_dir = run / f"{index:04d}"
        compile_dir = (compile_root / str(prompt_sha)) if prompt_sha else job_dir
        entry: dict[str, Any] = {
            "id": job_id,
            "source": "legacy",
            "index": index,
            "prompt_sha256": prompt_sha,
            "directory": compile_dir,
            "body": None,
            "raw_bytes": None,
            "not_run_reason": None,
        }

        candidate_dirs: list[pathlib.Path] = []
        if prompt_sha:
            candidate_dirs.append(compile_root / str(prompt_sha))
        candidate_dirs.append(job_dir)

        loaded = False
        last_err: Exception | None = None
        for cand_dir in candidate_dirs:
            if not cand_dir.is_dir():
                continue
            try:
                if cand_dir in request_cache:
                    body, raw_bytes = request_cache[cand_dir]
                else:
                    body, raw_bytes = _load_request_file(cand_dir, "compile-json")
                    request_cache[cand_dir] = (body, raw_bytes)
                entry["directory"] = cand_dir
                entry["body"] = body
                entry["raw_bytes"] = raw_bytes
                loaded = True
                break
            except (OSError, ValueError) as err:
                last_err = err

        if not loaded:
            if last_err is not None:
                entry["not_run_reason"] = (
                    f"cannot read compile request for {job_id}: {type(last_err).__name__}: {last_err}"
                )
            else:
                entry["not_run_reason"] = f"missing compile request directory for {job_id}"
        inputs.append(entry)
    meta = {
        "result_sha256": sha_file(result_path),
        "accepted_required": result.get("required_job_count"),
        "accepted_passed": result.get("passed_job_count"),
        "records_count": len(records),
    }
    return inputs, meta


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _empty_row(item: dict[str, Any], variant: str, status: str, reason: str | None) -> dict[str, Any]:
    return {
        "id": item["id"],
        "source": item["source"],
        "variant": variant,
        "passed": status == "passed",
        "status": status,
        "reason": reason,
        "compile_request_sha256": None,
        "qa_compile_request_sha256": None,
        "baseline_compile_request_sha256": None,
        "qa_compile_status": None,
        "baseline_compile_status": None,
        "qa_compile_response_sha256": None,
        "baseline_compile_response_sha256": None,
        "qa_assemble_request_sha256": None,
        "baseline_assemble_request_sha256": None,
        "qa_assemble_status": None,
        "baseline_assemble_status": None,
        "qa_assemble_response_sha256": None,
        "baseline_assemble_response_sha256": None,
        "compile": {
            "request_sha256": None,
            "qa_request_sha256": None,
            "baseline_request_sha256": None,
            "qa_status": None,
            "baseline_status": None,
            "qa_response_sha256": None,
            "baseline_response_sha256": None,
            "passed": False,
        },
        "assemble": {
            "qa_request_sha256": None,
            "baseline_request_sha256": None,
            "qa_status": None,
            "baseline_status": None,
            "qa_response_sha256": None,
            "baseline_response_sha256": None,
            "passed": False,
        },
        "qa": {
            "compile_request_sha256": None,
            "compile_status": None,
            "compile_response_sha256": None,
            "assemble_request_sha256": None,
            "assemble_status": None,
            "assemble_response_sha256": None,
        },
        "baseline": {
            "compile_request_sha256": None,
            "compile_status": None,
            "compile_response_sha256": None,
            "assemble_request_sha256": None,
            "assemble_status": None,
            "assemble_response_sha256": None,
        },
    }


def evaluate_variant(
    qa_url: str,
    baseline_url: str,
    item: dict[str, Any],
    variant: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Run one (input, variant) check and return (row_dict, raw_responses_if_failed)."""
    if item.get("not_run_reason") is not None or item.get("body") is None:
        reason = item.get("not_run_reason") or "recorded compile request not found"
        return _empty_row(item, variant, "not_run", reason), {}

    body: dict[str, Any] = item["body"]
    if "target" in body:
        reason = f"recorded compile request already contains a 'target' key ({body['target']!r})"
        return _empty_row(item, variant, "failed", reason), {}

    if variant == "none":
        raw_bytes: bytes | None = item.get("raw_bytes")
        if raw_bytes is not None:
            try:
                parsed_raw = json.loads(raw_bytes.decode("utf-8"))
                compile_req_bytes = raw_bytes if parsed_raw == body else compact_json_bytes(body)
            except (UnicodeDecodeError, ValueError):
                compile_req_bytes = compact_json_bytes(body)
        else:
            compile_req_bytes = compact_json_bytes(body)
    elif variant == "python":
        py_body = {**body, "target": "python"}
        compile_req_bytes = compact_json_bytes(py_body)
    else:
        raise ValueError(f"unknown variant: {variant!r}")

    compile_req_sha = sha_bytes(compile_req_bytes)
    row = _empty_row(item, variant, "failed", None)
    row["compile_request_sha256"] = compile_req_sha
    row["qa_compile_request_sha256"] = compile_req_sha
    row["baseline_compile_request_sha256"] = compile_req_sha
    row["compile"]["request_sha256"] = compile_req_sha
    row["compile"]["qa_request_sha256"] = compile_req_sha
    row["compile"]["baseline_request_sha256"] = compile_req_sha
    row["qa"]["compile_request_sha256"] = compile_req_sha
    row["baseline"]["compile_request_sha256"] = compile_req_sha

    raw_blobs: dict[str, bytes] = {}
    reasons: list[str] = []

    try:
        qa_comp_status, qa_comp_raw = post_raw(qa_url, "/v1/compile", compile_req_bytes)
    except OSError as err:
        row["reason"] = f"qa compile request failed: {type(err).__name__}: {err}"
        return row, raw_blobs

    raw_blobs["qa.compile.raw"] = qa_comp_raw
    qa_comp_sha = sha_bytes(qa_comp_raw)
    row["qa_compile_status"] = qa_comp_status
    row["qa_compile_response_sha256"] = qa_comp_sha
    row["compile"]["qa_status"] = qa_comp_status
    row["compile"]["qa_response_sha256"] = qa_comp_sha
    row["qa"]["compile_status"] = qa_comp_status
    row["qa"]["compile_response_sha256"] = qa_comp_sha

    try:
        base_comp_status, base_comp_raw = post_raw(baseline_url, "/v1/compile", compile_req_bytes)
    except OSError as err:
        row["reason"] = f"baseline compile request failed: {type(err).__name__}: {err}"
        return row, raw_blobs

    raw_blobs["baseline.compile.raw"] = base_comp_raw
    base_comp_sha = sha_bytes(base_comp_raw)
    row["baseline_compile_status"] = base_comp_status
    row["baseline_compile_response_sha256"] = base_comp_sha
    row["compile"]["baseline_status"] = base_comp_status
    row["compile"]["baseline_response_sha256"] = base_comp_sha
    row["baseline"]["compile_status"] = base_comp_status
    row["baseline"]["compile_response_sha256"] = base_comp_sha

    compile_passed = qa_comp_status == base_comp_status and qa_comp_raw == base_comp_raw
    row["compile"]["passed"] = compile_passed
    if qa_comp_status != base_comp_status:
        reasons.append(f"compile HTTP status differs: qa={qa_comp_status}, baseline={base_comp_status}")
    elif qa_comp_raw != base_comp_raw:
        reasons.append("compile response bytes differ")

    assemble_passed = False
    if qa_comp_status == 200 and base_comp_status == 200:
        qa_bundle = None
        base_bundle = None
        try:
            qa_doc = json.loads(qa_comp_raw.decode("utf-8"))
            if isinstance(qa_doc, dict) and isinstance(qa_doc.get("bundle"), dict):
                qa_bundle = qa_doc["bundle"]
            else:
                reasons.append("qa compile 200 response missing bundle object")
        except (UnicodeDecodeError, ValueError) as err:
            reasons.append(f"qa compile 200 response is not valid JSON: {err}")

        try:
            base_doc = json.loads(base_comp_raw.decode("utf-8"))
            if isinstance(base_doc, dict) and isinstance(base_doc.get("bundle"), dict):
                base_bundle = base_doc["bundle"]
            else:
                reasons.append("baseline compile 200 response missing bundle object")
        except (UnicodeDecodeError, ValueError) as err:
            reasons.append(f"baseline compile 200 response is not valid JSON: {err}")

        if qa_bundle is not None and base_bundle is not None:
            qa_asm_req = compact_json_bytes({"bundle": qa_bundle})
            base_asm_req = compact_json_bytes({"bundle": base_bundle})
            qa_asm_req_sha = sha_bytes(qa_asm_req)
            base_asm_req_sha = sha_bytes(base_asm_req)

            row["qa_assemble_request_sha256"] = qa_asm_req_sha
            row["baseline_assemble_request_sha256"] = base_asm_req_sha
            row["assemble"]["qa_request_sha256"] = qa_asm_req_sha
            row["assemble"]["baseline_request_sha256"] = base_asm_req_sha
            row["qa"]["assemble_request_sha256"] = qa_asm_req_sha
            row["baseline"]["assemble_request_sha256"] = base_asm_req_sha

            try:
                qa_asm_status, qa_asm_raw = post_raw(qa_url, "/v1/assemble", qa_asm_req)
            except OSError as err:
                reasons.append(f"qa assemble request failed: {type(err).__name__}: {err}")
                row["reason"] = "; ".join(reasons)
                return row, raw_blobs

            raw_blobs["qa.assemble.raw"] = qa_asm_raw
            qa_asm_sha = sha_bytes(qa_asm_raw)
            row["qa_assemble_status"] = qa_asm_status
            row["qa_assemble_response_sha256"] = qa_asm_sha
            row["assemble"]["qa_status"] = qa_asm_status
            row["assemble"]["qa_response_sha256"] = qa_asm_sha
            row["qa"]["assemble_status"] = qa_asm_status
            row["qa"]["assemble_response_sha256"] = qa_asm_sha

            try:
                base_asm_status, base_asm_raw = post_raw(baseline_url, "/v1/assemble", base_asm_req)
            except OSError as err:
                reasons.append(f"baseline assemble request failed: {type(err).__name__}: {err}")
                row["reason"] = "; ".join(reasons)
                return row, raw_blobs

            raw_blobs["baseline.assemble.raw"] = base_asm_raw
            base_asm_sha = sha_bytes(base_asm_raw)
            row["baseline_assemble_status"] = base_asm_status
            row["baseline_assemble_response_sha256"] = base_asm_sha
            row["assemble"]["baseline_status"] = base_asm_status
            row["assemble"]["baseline_response_sha256"] = base_asm_sha
            row["baseline"]["assemble_status"] = base_asm_status
            row["baseline"]["assemble_response_sha256"] = base_asm_sha

            assemble_passed = (
                qa_asm_status == 200
                and base_asm_status == 200
                and qa_asm_raw == base_asm_raw
            )
            row["assemble"]["passed"] = assemble_passed
            if qa_asm_status != base_asm_status:
                reasons.append(
                    f"assemble HTTP status differs: qa={qa_asm_status}, baseline={base_asm_status}"
                )
            elif qa_asm_status != 200:
                reasons.append(f"assemble HTTP status is {qa_asm_status}, expected 200")
            elif qa_asm_raw != base_asm_raw:
                reasons.append("assemble response bytes differ")
    else:
        assemble_passed = compile_passed
        row["assemble"]["passed"] = assemble_passed

    if compile_passed and assemble_passed and not reasons:
        row["passed"] = True
        row["status"] = "passed"
        row["reason"] = None
        return row, {}

    row["passed"] = False
    row["status"] = "failed"
    row["reason"] = "; ".join(reasons) if reasons else "check failed"
    return row, raw_blobs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _ArgumentParser(
        description=(
            "Compare Python compile and assemble responses (without target and with target 'python') "
            "between --qa and --baseline across the 127 public products and 544 legacy jobs."
        )
    )
    parser.add_argument("--qa", required=True, help="Loopback URL of the QA candidate server")
    parser.add_argument("--baseline", required=True, help="Loopback URL of the baseline server")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="New output directory")
    parser.add_argument(
        "--public-run",
        type=pathlib.Path,
        default=DEFAULT_PUBLIC_RUN,
        help="Path to the recorded public-products run (default: %(default)s)",
    )
    parser.add_argument(
        "--legacy-run",
        type=pathlib.Path,
        default=DEFAULT_LEGACY_RUN,
        help="Path to the recorded legacy544 run (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent HTTP workers (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    expected_public: int | None = None,
    expected_legacy: int | None = None,
) -> int:
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
    public_run: pathlib.Path = resolve_local_path(args.public_run)
    legacy_run: pathlib.Path = resolve_local_path(args.legacy_run)

    exp_public = EXPECTED_PUBLIC if expected_public is None else expected_public
    exp_legacy = EXPECTED_LEGACY if expected_legacy is None else expected_legacy
    exp_total_rows = (exp_public + exp_legacy) * len(VARIANTS)

    run_problems: list[str] = []
    try:
        public_inputs, public_meta = read_public_inputs(public_run)
    except (OSError, ValueError) as err:
        public_inputs = []
        public_meta = {
            "result_sha256": None,
            "accepted_required": None,
            "accepted_passed": None,
            "records_count": 0,
        }
        run_problems.append(f"cannot read public run {public_run}: {type(err).__name__}: {err}")

    try:
        legacy_inputs, legacy_meta = read_legacy_inputs(legacy_run)
    except (OSError, ValueError) as err:
        legacy_inputs = []
        legacy_meta = {
            "result_sha256": None,
            "accepted_required": None,
            "accepted_passed": None,
            "records_count": 0,
        }
        run_problems.append(f"cannot read legacy run {legacy_run}: {type(err).__name__}: {err}")

    tasks: list[tuple[dict[str, Any], str]] = []
    for item in public_inputs:
        for variant in VARIANTS:
            tasks.append((item, variant))
    for item in legacy_inputs:
        for variant in VARIANTS:
            tasks.append((item, variant))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(
            pool.map(
                lambda pair: evaluate_variant(qa_url, baseline_url, pair[0], pair[1]),
                tasks,
            )
        )

    out_dir.mkdir(parents=True, exist_ok=False)
    responses_dir = out_dir / "responses"

    rows: list[dict[str, Any]] = []
    for (item, variant), (row, raw_blobs) in zip(tasks, results):
        if not row["passed"] and raw_blobs:
            responses_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{_safe_filename(item['source'])}_{item['index']:04d}_{_safe_filename(item['id'])}_{variant}"
            saved_files: dict[str, str] = {}
            for suffix, blob in sorted(raw_blobs.items()):
                rel_path = f"responses/{prefix}.{suffix}"
                (out_dir / rel_path).write_bytes(blob)
                saved_files[suffix] = rel_path
            row["saved_responses"] = saved_files
        rows.append(row)

    public_rows_list = [r for r in rows if r["source"] == "public"]
    legacy_rows_list = [r for r in rows if r["source"] == "legacy"]
    none_rows_list = [r for r in rows if r["variant"] == "none"]
    python_rows_list = [r for r in rows if r["variant"] == "python"]

    passed_rows = sum(1 for r in rows if r["status"] == "passed" and r["passed"])
    failed_rows = sum(1 for r in rows if r["status"] == "failed")
    not_run_rows = sum(1 for r in rows if r["status"] == "not_run")

    pub_req = public_meta.get("accepted_required")
    leg_req = legacy_meta.get("accepted_required")
    pub_count_ok = len(public_inputs) == exp_public and (pub_req is None or pub_req == exp_public)
    leg_count_ok = len(legacy_inputs) == exp_legacy and (leg_req is None or leg_req == exp_legacy)
    total_count_ok = len(rows) == exp_total_rows
    row_count_matches = pub_count_ok and leg_count_ok and total_count_ok

    if len(public_inputs) != exp_public:
        run_problems.append(
            f"public input count is {len(public_inputs)}, expected {exp_public}"
        )
    elif pub_req is not None and pub_req != exp_public:
        run_problems.append(
            f"public result.json required count is {pub_req}, expected {exp_public}"
        )

    if len(legacy_inputs) != exp_legacy:
        run_problems.append(
            f"legacy input count is {len(legacy_inputs)}, expected {exp_legacy}"
        )
    elif leg_req is not None and leg_req != exp_legacy:
        run_problems.append(
            f"legacy result.json required_job_count is {leg_req}, expected {exp_legacy}"
        )

    if len(rows) != exp_total_rows:
        run_problems.append(
            f"total row count is {len(rows)}, expected {exp_total_rows} (({exp_public} + {exp_legacy}) * 2)"
        )

    overall_passed = (
        row_count_matches
        and not run_problems
        and passed_rows == exp_total_rows
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
        "version": "som.typescript-output-python-baseline.v1",
        "qa": qa_url,
        "qa_url": qa_url,
        "baseline": baseline_url,
        "baseline_url": baseline_url,
        "public_run": str(public_run.resolve()),
        "legacy_run": str(legacy_run.resolve()),
        "public_result_sha256": public_meta["result_sha256"],
        "legacy_result_sha256": legacy_meta["result_sha256"],
        "inputs": {
            "public_run": str(public_run.resolve()),
            "public_result_sha256": public_meta["result_sha256"],
            "legacy_run": str(legacy_run.resolve()),
            "legacy_result_sha256": legacy_meta["result_sha256"],
        },
        "expected_public": exp_public,
        "expected_legacy": exp_legacy,
        "expected_total": exp_total_rows,
        "expected_rows": exp_total_rows,
        "total": len(rows),
        "total_rows": len(rows),
        "passed_count": passed_rows,
        "passed_rows": passed_rows,
        "failed_count": failed_rows,
        "failed_rows": failed_rows,
        "not_run_count": not_run_rows,
        "not_run_rows": not_run_rows,
        "row_count_matches": row_count_matches,
        "totals": {
            "expected_public": exp_public,
            "public_inputs": len(public_inputs),
            "expected_legacy": exp_legacy,
            "legacy_inputs": len(legacy_inputs),
            "expected_rows": exp_total_rows,
            "total_rows": len(rows),
            "passed": passed_rows,
            "failed": failed_rows,
            "not_run": not_run_rows,
            "row_count_matches": row_count_matches,
            "by_source": {
                "public": {
                    "expected_rows": exp_public * len(VARIANTS),
                    "total": len(public_rows_list),
                    "passed": sum(1 for r in public_rows_list if r["passed"]),
                    "failed": sum(1 for r in public_rows_list if r["status"] == "failed"),
                    "not_run": sum(1 for r in public_rows_list if r["status"] == "not_run"),
                },
                "legacy": {
                    "expected_rows": exp_legacy * len(VARIANTS),
                    "total": len(legacy_rows_list),
                    "passed": sum(1 for r in legacy_rows_list if r["passed"]),
                    "failed": sum(1 for r in legacy_rows_list if r["status"] == "failed"),
                    "not_run": sum(1 for r in legacy_rows_list if r["status"] == "not_run"),
                },
            },
            "by_variant": {
                "none": {
                    "expected_rows": exp_public + exp_legacy,
                    "total": len(none_rows_list),
                    "passed": sum(1 for r in none_rows_list if r["passed"]),
                    "failed": sum(1 for r in none_rows_list if r["status"] == "failed"),
                    "not_run": sum(1 for r in none_rows_list if r["status"] == "not_run"),
                },
                "python": {
                    "expected_rows": exp_public + exp_legacy,
                    "total": len(python_rows_list),
                    "passed": sum(1 for r in python_rows_list if r["passed"]),
                    "failed": sum(1 for r in python_rows_list if r["status"] == "failed"),
                    "not_run": sum(1 for r in python_rows_list if r["status"] == "not_run"),
                },
            },
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
