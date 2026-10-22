#!/usr/bin/env python3
"""Check RT01-RT07 target-envelope diagnostics over HTTP (issue #105).

Reads every RT case from som-rs/contracts/rust-output-v1/negative-cases.json
(requiring exactly 7), sends each case's body bytes unchanged to POST /v1/compile,
and verifies http_status, expected code, detail/cause, prompt_pointer,
allowed_form, and span located in the raw request body.

usage: check_rt_diagnostics.py --url URL --out DIR
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_target  # noqa: E402
from check_target import (  # noqa: E402
    FORBIDDEN_PORTS,
    LOOPBACK_HOSTS,
    check_diagnostic_reply,
    check_span_in_body,
    make_row,
    post_compile,
    sha_bytes,
    validate_url,
)

RUST_NEGATIVE_CASES_PATH = SOM_RS / "contracts" / "rust-output-v1" / "negative-cases.json"
EXPECTED_RT_COUNT = 7
CONTENT_TYPES_BY_FORM = {
    "json": "application/json",
    "cnl": "application/json",
}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(f"refused: {message}", file=sys.stderr)
        raise SystemExit(2)


def load_rt_cases(path: pathlib.Path | None = None) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """Load RT* cases from som-rs/contracts/rust-output-v1/negative-cases.json."""
    neg_path = path or RUST_NEGATIVE_CASES_PATH
    if not neg_path.is_file():
        return [], None, [f"missing negative-cases.json: {neg_path}"]
    try:
        raw_bytes = neg_path.read_bytes()
        file_sha = sha_bytes(raw_bytes)
        doc = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [], None, [f"cannot read {neg_path}: {type(exc).__name__}: {exc}"]

    all_cases = doc.get("cases")
    if not isinstance(all_cases, list):
        return [], file_sha, [f"{neg_path} missing 'cases' list"]

    rt_cases = [
        case
        for case in all_cases
        if isinstance(case, dict) and str(case.get("id", "")).startswith("RT")
    ]
    problems: list[str] = []
    if len(rt_cases) != EXPECTED_RT_COUNT:
        problems.append(
            f"expected exactly {EXPECTED_RT_COUNT} RT cases in {neg_path}, found {len(rt_cases)}"
        )
    return rt_cases, file_sha, problems


def run_checks(
    base_url: str,
    out_dir: pathlib.Path,
    negative_cases_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    responses_dir = out_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=False)

    rt_cases, neg_sha, load_problems = load_rt_cases(negative_cases_path)
    rows: list[dict[str, Any]] = []

    for case in rt_cases:
        cid = str(case.get("id", ""))
        body = case.get("body", "")
        form = str(case.get("form", "json"))
        expected_http_status = int(case.get("http_status", 400))
        expected_diag = dict(case.get("expected") or {})
        span_text = case.get("span_text")
        content_type = CONTENT_TYPES_BY_FORM.get(form, "application/json")

        expected_desc = {
            "http_status": expected_http_status,
            **expected_diag,
            "span_text": span_text,
        }

        case_problems: list[str] = list(load_problems)
        if form not in CONTENT_TYPES_BY_FORM:
            case_problems.append(f"unsupported case form {form!r} for {cid}")

        status, raw, resp_file, err = post_compile(
            base_url, body, responses_dir, cid
        )
        if err is not None:
            row = make_row(
                cid,
                "rt_diagnostics",
                body,
                None,
                None,
                None,
                expected_desc,
                [],
                not_run_error=err,
            )
            row["form"] = form
            row["content_type"] = content_type
            rows.append(row)
            continue

        assert status is not None and raw is not None
        case_problems.extend(
            check_diagnostic_reply(
                status,
                raw,
                expected_http_status,
                expected_diag,
                body,
                form,
                expected_span_text=span_text,
            )
        )
        # Ensure span is always verified against the raw request body even if prompt_pointer was mutated
        try:
            reply_doc = json.loads(raw)
            diags = reply_doc.get("diagnostics") if isinstance(reply_doc, dict) else None
            if isinstance(diags, list) and len(diags) == 1 and isinstance(diags[0], dict):
                if diags[0].get("prompt_pointer") not in ("", "/target"):
                    for prob in check_span_in_body(body, "json", diags[0], expected_span_text=span_text):
                        if prob not in case_problems:
                            case_problems.append(prob)
        except Exception:  # noqa: BLE001
            pass

        row = make_row(
            cid,
            "rt_diagnostics",
            body,
            status,
            raw,
            resp_file,
            expected_desc,
            case_problems,
        )
        row["form"] = form
        row["content_type"] = content_type
        rows.append(row)

    passed_count = sum(1 for r in rows if r["status"] == "passed" and r["passed"])
    failed_count = sum(1 for r in rows if r["status"] == "failed")
    not_run_count = sum(1 for r in rows if r["status"] == "not_run")

    all_passed = (
        not load_problems
        and len(rows) == EXPECTED_RT_COUNT
        and passed_count == EXPECTED_RT_COUNT
        and failed_count == 0
        and not_run_count == 0
    )

    if all_passed:
        overall_status = "passed"
    elif not_run_count > 0 and failed_count == 0 and not load_problems:
        overall_status = "not_run"
    else:
        overall_status = "failed"

    report: dict[str, Any] = {
        "version": "som.typescript-output-rt-diagnostics.v1",
        "issue": 105,
        "url": base_url,
        "negative_cases_path": str((negative_cases_path or RUST_NEGATIVE_CASES_PATH).resolve()),
        "negative_cases_sha256": neg_sha,
        "expected_rt_count": EXPECTED_RT_COUNT,
        "rt_count": len(rt_cases),
        "problems": load_problems,
        "status": overall_status,
        "passed": all_passed,
        "total": len(rows),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "not_run_count": not_run_count,
        "rows": rows,
        "cases": rows,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="Loopback URL of the candidate som server")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        required=True,
        help="New output directory for report.json and responses/",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    url_err = validate_url(args.url)
    if url_err is not None:
        print(f"refused: {url_err}", file=sys.stderr)
        return 2

    if args.out.exists() or args.out.is_symlink():
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
    raise SystemExit(main())
