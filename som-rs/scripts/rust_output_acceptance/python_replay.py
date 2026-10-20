#!/usr/bin/env python3
"""Replay the accepted Python regression exchanges against a candidate and compare bytes.

The accepted public run (127 products) and the accepted legacy run (544 jobs)
keep every compile and assemble request and the raw response bytes. This script
sends each recorded request, unchanged, to the candidate and requires the same
HTTP status and byte-identical response. Equal assemble bytes give equal
products, so each job keeps its accepted result. The script also requires that
the accepted run passed every row and that the replayed product matches the
product hashes the accepted run recorded.

A missing exchange, a status or byte difference, and an unrun row are failures.
The candidate must be a QA port; 8765 and 18765 are refused.

--asset-break is the rust-output-v1 amendment 004 mode (compiler-v1 amendment 003, issues #57
and #58), where the Python asset descriptors change and nothing else may:
- a compile exchange must give the same status; an error response the same bytes; a bundle the
  same JSON except bundle.assets and bundle.hashes.payload_sha256, where only the descriptors of
  ASSET_BREAK_DESCRIPTORS may differ and payload_sha256 must be the frozen hash of the new payload;
- the recorded assemble request, whose bundle has the earlier descriptors, must be refused with
  ASSET_HASH_MISMATCH (the recorded compatibility break);
- the same bundle with the candidate's descriptors for its prompt and the payload hash recomputed
  must give the recorded assemble status and response bytes, and the accepted product hashes.

usage: python_replay.py --base-url http://127.0.0.1:PORT --public-run DIR --legacy-run DIR --out NEW.json [--asset-break]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ACCEPTANCE = pathlib.Path(__file__).resolve().parents[1] / "compiler_acceptance"
sys.path.insert(0, str(ACCEPTANCE))
import qa_harness as qa  # type: ignore[import-not-found]  # noqa: E402

FORBIDDEN_PORTS = {8765, 18765}
ASSET_BREAK_DESCRIPTORS = ("backend", "block_library", "catalog", "compiler_contract", "selection_policy")
ASSET_BREAK = False


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def post(base_url: str, path: str, data: bytes) -> tuple[int, bytes]:
    request = urllib.request.Request(base_url + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def request_bytes(directory: pathlib.Path, label: str) -> bytes:
    """The bytes the accepted runner sent: the kept raw file, else its serializer."""
    raw = directory / f"{label}.request.raw"
    if raw.is_file():
        return raw.read_bytes()
    body = json.loads((directory / f"{label}.request.json").read_text(encoding="utf-8"))
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def replay(base_url: str, directory: pathlib.Path, label: str, path: str) -> tuple[dict, bytes | None]:
    row: dict = {"exchange": f"{directory.name}/{label}", "path": path}
    try:
        data = request_bytes(directory, label)
        expected_raw = base64.b64decode((directory / f"{label}.response.raw.b64").read_text(encoding="ascii"))
        expected_status = json.loads((directory / f"{label}.response.json").read_text(encoding="utf-8"))["http_status"]
    except (OSError, ValueError, KeyError) as error:
        row.update(passed=False, problem=f"missing accepted evidence: {type(error).__name__}: {error}")
        return row, None
    if ASSET_BREAK and path == "/v1/assemble":
        return rebased_assemble(base_url, row, data, expected_status, expected_raw)
    status, raw = post(base_url, path, data)
    row.update(request_sha256=sha(data), expected_status=expected_status, status=status,
               expected_response_sha256=sha(expected_raw), response_sha256=sha(raw),
               passed=status == expected_status and raw == expected_raw)
    if ASSET_BREAK and status == expected_status == 200 and raw != expected_raw:
        problem = asset_only_difference(expected_raw, raw)
        row.update(passed=problem is None, asset_break=True)
        if problem is None:
            NEW_ASSETS[json.loads(raw)["bundle"]["hashes"]["prompt_sha256"]] = json.loads(raw)["bundle"]["assets"]
        else:
            row["problem"] = problem
        return row, raw
    if not row["passed"]:
        row["problem"] = "status differs" if status != expected_status else "response bytes differ"
    return row, raw


# The candidate's asset descriptors per prompt_sha256, from the replayed compiles (--asset-break).
NEW_ASSETS: dict[str, dict] = {}


def asset_only_difference(expected_raw: bytes, raw: bytes) -> str | None:
    """None when two compile responses differ only in the admitted asset descriptors."""
    old, new = json.loads(expected_raw), json.loads(raw)
    old_assets, new_assets = old["bundle"].pop("assets"), new["bundle"].pop("assets")
    new_payload = new["bundle"]["hashes"].pop("payload_sha256")
    old["bundle"]["hashes"].pop("payload_sha256")
    if old != new:
        return "compile response differs beyond the asset descriptors"
    changed = {key for key in set(old_assets) | set(new_assets) if old_assets.get(key) != new_assets.get(key)}
    if not changed <= set(ASSET_BREAK_DESCRIPTORS):
        return "asset descriptors outside the admitted set differ: " + ", ".join(sorted(changed))
    payload = {"version": new["bundle"]["version"], "prompt": new["bundle"]["prompt"], "dsl": new["bundle"]["dsl"],
               "assets": new_assets}
    if sha(qa.canonical(payload).encode()) != new_payload:
        return "payload_sha256 is not the hash of the new payload"
    return None


def rebased_assemble(base_url: str, row: dict, data: bytes, expected_status: int,
                     expected_raw: bytes) -> tuple[dict, bytes | None]:
    """The recorded bundle is refused; rebased on the new descriptors it gives the recorded bytes."""
    status, raw = post(base_url, "/v1/assemble", data)
    code = json.loads(raw).get("code") if raw.startswith(b"{") else None
    row.update(request_sha256=sha(data), recorded_bundle_status=status, recorded_bundle_code=code)
    problems = [] if code == "ASSET_HASH_MISMATCH" else ["the recorded bundle is not refused with ASSET_HASH_MISMATCH"]
    body = json.loads(data)
    bundle = body["bundle"]
    assets = NEW_ASSETS.get(bundle["hashes"]["prompt_sha256"])
    if assets is None:
        row.update(passed=False, problem="; ".join(problems + ["no replayed compile gives this prompt's new descriptors"]))
        return row, None
    bundle["assets"] = assets
    payload = {"version": bundle["version"], "prompt": bundle["prompt"], "dsl": bundle["dsl"], "assets": assets}
    bundle["hashes"]["payload_sha256"] = sha(qa.canonical(payload).encode())
    rebased = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    status, raw = post(base_url, "/v1/assemble", rebased)
    row.update(rebased_request_sha256=sha(rebased), expected_status=expected_status, status=status,
               expected_response_sha256=sha(expected_raw), response_sha256=sha(raw), asset_break=True)
    if status != expected_status:
        problems.append("rebased bundle: status differs")
    elif raw != expected_raw:
        problems.append("rebased bundle: response bytes differ")
    row["passed"] = not problems
    if problems:
        row["problem"] = "; ".join(problems)
    return row, raw


def product_matches(raw: bytes | None, record: dict) -> list[str]:
    if raw is None:
        return ["no assembled product"]
    files = json.loads(raw).get("files")
    problems = []
    if not isinstance(files, dict) or sha(qa.canonical(files).encode()) != record["product_sha256"]:
        problems.append("product_sha256 differs from the accepted record")
    file_hashes = record.get("product_file_sha256")
    if file_hashes is not None and isinstance(files, dict):
        if {name: sha(text.encode()) for name, text in sorted(files.items())} != file_hashes:
            problems.append("product file hashes differ from the accepted record")
    return problems


def public_rows(base_url: str, run: pathlib.Path) -> tuple[list[dict], dict]:
    result = json.loads((run / "result.json").read_text())
    records = result["records"]
    directories = sorted(p for p in run.iterdir() if p.is_dir() and p.name.isdigit())
    rows = []
    for index, record in enumerate(records):
        directory = run / f"{index:03d}"
        row = {"id": record["id"], "accepted_passed": record["passed"] is True, "exchanges": []}
        problems = []
        if not directory.is_dir():
            problems.append("no accepted case directory")
        else:
            kept = json.loads((directory / "result.json").read_text())
            if kept.get("id") != record["id"]:
                problems.append("case directory belongs to another case")
            raw = None
            for label in ("compile-cnl", "compile-json", "assemble"):
                exchange, raw = replay(base_url, directory, label, "/v1/assemble" if label == "assemble" else "/v1/compile")
                row["exchanges"].append(exchange)
                if not exchange["passed"]:
                    problems.append(f"{label}: {exchange['problem']}")
            problems += product_matches(raw, record)
        if not row["accepted_passed"]:
            problems.append("accepted run did not pass this case")
        row.update(problems=problems, passed=not problems)
        rows.append(row)
    summary = {"accepted_required": result["required"], "accepted_passed": result["passed"],
               "accepted_complete": result["complete"], "case_directories": len(directories)}
    return rows, summary


def legacy_rows(base_url: str, run: pathlib.Path) -> tuple[list[dict], list[dict], dict]:
    result = json.loads((run / "result.json").read_text())
    compiles = []
    compile_root = run / "compile"
    for directory in sorted(p for p in compile_root.iterdir() if p.is_dir()):
        row = {"prompt_sha256": directory.name, "exchanges": []}
        for label in ("compile-json", "compile-cnl"):
            exchange, _ = replay(base_url, directory, label, "/v1/compile")
            row["exchanges"].append(exchange)
        row["passed"] = all(exchange["passed"] for exchange in row["exchanges"])
        compiles.append(row)
    compiled = {row["prompt_sha256"]: row["passed"] for row in compiles}
    jobs = []
    for index, record in enumerate(result["records"]):
        directory = run / f"{index:04d}"
        row = {"id": record["id"], "family": record["family"], "group": record["group"],
               "accepted_passed": record["passed"] is True}
        problems = []
        if not row["accepted_passed"]:
            problems.append("accepted run did not pass this job")
        request = json.loads((directory / "assemble.request.json").read_text()) if directory.is_dir() else None
        if request is None:
            problems.append("no accepted job directory")
        else:
            hashes = request["bundle"].get("hashes", {})
            if hashes.get("prompt_sha256") != record["prompt_sha256"]:
                problems.append("job directory belongs to another prompt")
            exchange, raw = replay(base_url, directory, "assemble", "/v1/assemble")
            row["exchange"] = exchange
            if not exchange["passed"]:
                problems.append(f"assemble: {exchange['problem']}")
            problems += product_matches(raw, record)
        if compiled.get(record["prompt_sha256"]) is not True:
            problems.append("its prompt compiles did not replay byte-identically")
        row.update(problems=problems, passed=not problems)
        jobs.append(row)
    summary = {"accepted_status": result["status"], "accepted_required": result["required_job_count"],
               "accepted_passed": result["passed_job_count"], "accepted_compile_prompts": result["compile_prompt_count"]}
    return compiles, jobs, summary


def file_sha(path: pathlib.Path) -> str:
    return sha(path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--public-run", type=pathlib.Path, required=True)
    parser.add_argument("--legacy-run", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--asset-break", action="store_true",
                        help="rust-output-v1 amendment 004: only the admitted asset descriptors may change")
    args = parser.parse_args()
    global ASSET_BREAK
    ASSET_BREAK = args.asset_break
    split = urllib.parse.urlsplit(args.base_url)
    if split.hostname not in {"127.0.0.1", "localhost", "::1"} or split.port in FORBIDDEN_PORTS:
        raise SystemExit("use a loopback QA port, not 8765 or 18765")
    if args.out.exists():
        raise SystemExit(f"preserve existing output: {args.out}")
    base_url = args.base_url.rstrip("/")
    started = time.time()
    public, public_summary = public_rows(base_url, args.public_run)
    compiles, jobs, legacy_summary = legacy_rows(base_url, args.legacy_run)
    families: dict[str, dict[str, int]] = {}
    for job in jobs:
        family = families.setdefault(job["family"], {"total": 0, "passed": 0})
        family["total"] += 1
        family["passed"] += job["passed"]
    exchanges = ([e for row in public for e in row["exchanges"]] + [e for row in compiles for e in row["exchanges"]]
                 + [row["exchange"] for row in jobs if "exchange" in row])
    report = {
        "version": "som.rust-output-python-replay.v1",
        "base_url": base_url,
        "mode": ({"asset_break": True, "admitted_descriptors": list(ASSET_BREAK_DESCRIPTORS),
                  "rebased_assembles": sum(1 for row in public for e in row["exchanges"] if e.get("recorded_bundle_code"))
                  + sum(1 for row in jobs if row.get("exchange", {}).get("recorded_bundle_code")),
                  "recorded_bundles_refused": sum(1 for row in public for e in row["exchanges"]
                                                  if e.get("recorded_bundle_code") == "ASSET_HASH_MISMATCH")
                  + sum(1 for row in jobs if row.get("exchange", {}).get("recorded_bundle_code") == "ASSET_HASH_MISMATCH")}
                 if ASSET_BREAK else {"asset_break": False}),
        "inputs": {"public_run": str(args.public_run.resolve()),
                   "public_result_sha256": file_sha(args.public_run / "result.json"),
                   "legacy_run": str(args.legacy_run.resolve()),
                   "legacy_result_sha256": file_sha(args.legacy_run / "result.json")},
        "public": {"required": 127, "total": len(public), "passed": sum(r["passed"] for r in public),
                   "accepted": public_summary, "failed": [r["id"] for r in public if not r["passed"]]},
        "legacy": {"required": 544, "total": len(jobs), "passed": sum(r["passed"] for r in jobs),
                   "families": families, "accepted": legacy_summary,
                   "compile_prompts": len(compiles), "compile_prompts_passed": sum(r["passed"] for r in compiles),
                   "failed": [r["id"] for r in jobs if not r["passed"]]},
        "exchanges": {"total": len(exchanges), "passed": sum(e["passed"] for e in exchanges)},
        "seconds": time.time() - started,
        "public_rows": public, "legacy_compile_rows": compiles, "legacy_rows": jobs,
    }
    report["passed"] = (report["public"]["total"] == report["public"]["passed"] == 127
                        and public_summary["accepted_passed"] == 127
                        and report["legacy"]["total"] == report["legacy"]["passed"] == 544
                        and legacy_summary["accepted_passed"] == 544
                        and report["legacy"]["compile_prompts"] == report["legacy"]["compile_prompts_passed"]
                        and report["exchanges"]["total"] == report["exchanges"]["passed"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({key: report[key] for key in ("passed", "exchanges")}
                     | {"public": f"{report['public']['passed']}/{report['public']['total']}",
                        "legacy": f"{report['legacy']['passed']}/{report['legacy']['total']}",
                        "families": families, "seconds": round(report["seconds"])}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
