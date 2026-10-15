#!/usr/bin/env python3
"""Issue #47 and #48 checks for the target-aware compiler on a QA port.

Gate: target_selection_checks, scope_checks and python_unchanged.

- Every frozen negative case gives exactly its frozen status and diagnostic,
  and its span selects the frozen span_text.
- Every target example gives its expected reply. A v1 to v4 Rust example gives
  a som.dsl-bundle.v2 bundle with target rust, and its CNL and JSON forms give
  the same bytes. A v5 Rust example gives the temporary NOT_IMPLEMENTED
  diagnostic until issue #49.
- For every public example, CNL positive fixture, coverage case and legacy job,
  in each available form:
  - the QA reply without target equals the reference reply byte for byte,
    and its bundle assembles to the same Python file bytes on both;
  - the QA reply with target python equals the QA reply without target;
  - a request compiler-v1 refuses keeps that exact diagnostic with target rust;
  - the QA reply with target rust gives the refusal the frozen reference
    profile (tests/rust-output-v1/rust_profile.py) computes on the compiler's
    own compiled prompt; an in-scope v1 to v4 prompt gives a Rust bundle whose
    CNL and JSON forms are byte-identical, and an in-scope v5 prompt gives
    NOT_IMPLEMENTED.
- The refusal counts equal the frozen case inventory.

The reference compiler gets read-only compile calls only. Legacy prompts are
read from the local acceptance tree. The report names them by id and hash only.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
REPO = SOM_RS.parent
CONTRACT = SOM_RS / "contracts" / "rust-output-v1"
TESTS = SOM_RS / "tests" / "rust-output-v1"
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
COVERAGE = TESTS / "cases" / "index.json"
LEGACY = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544"

sys.path.insert(0, str(TESTS))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402

NOT_IMPLEMENTED = {
    "code": "NOT_IMPLEMENTED",
    "prompt_pointer": "/target",
    "cause": "Rust output for the v5 parent and child resources is not implemented in this build yet. "
             "The prompt is in Rust output phase 1 scope.",
    "allowed_form": "Omit target, or use target python, to generate Python. Rust v1 to v4 prompts are available.",
}
RUST_BUNDLE = "som.dsl-bundle.v2"
SPAN_KEYS = ("line", "column", "end_line", "end_column", "byte_start", "byte_end")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def post(base: str, body: str, route: str = "/v1/compile") -> tuple[int, bytes]:
    request = urllib.request.Request(base.rstrip("/") + route, data=body.encode(),
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def resolve(value: Any, pointer: str) -> Any:
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def one_diagnostic(status: int, raw: bytes, http_status: int, expected: dict) -> list[str]:
    """Problems with a refusal reply against one expected diagnostic."""
    problems = []
    reply = json.loads(raw)
    if status != http_status:
        problems.append(f"status {status} != {http_status}")
    if reply.get("status") != "error" or reply.get("code") != expected["code"]:
        problems.append(f"code {reply.get('code')} != {expected['code']}")
    if reply.get("detail") != expected["cause"]:
        problems.append("detail differs from cause")
    diagnostics = reply.get("diagnostics", [])
    if len(diagnostics) != 1:
        problems.append(f"{len(diagnostics)} diagnostics")
        return problems
    for key, value in expected.items():
        if diagnostics[0].get(key) != value:
            problems.append(f"{key}: {diagnostics[0].get(key)!r} != {value!r}")
    if set(reply) != {"status", "code", "detail", "diagnostics"}:
        problems.append(f"reply members {sorted(reply)}")
    return problems


def located(body: str, form: str, diagnostic: dict) -> str:
    """The text a diagnostic span selects. CNL prompt diagnostics locate the CNL
    source. JSON and envelope diagnostics, target included, locate the raw body."""
    envelope = diagnostic.get("prompt_pointer") in ("", "/target")
    data = (json.loads(body)["source"] if form == "cnl" and not envelope else body).encode()
    return data[diagnostic["byte_start"]:diagnostic["byte_end"]].decode()


# Frozen negative cases and target examples.

def negative_cases(qa: str) -> list[dict]:
    rows = []
    for case in json.loads((CONTRACT / "negative-cases.json").read_text())["cases"]:
        status, raw = post(qa, case["body"])
        problems = one_diagnostic(status, raw, case["http_status"], case["expected"])
        if not problems:
            span = located(case["body"], case["form"], json.loads(raw)["diagnostics"][0])
            if span != case["span_text"]:
                problems.append("span_text differs")
        rows.append({"id": case["id"], "status": status, "passed": not problems, "problems": problems})
    return rows


def target_examples(qa: str) -> list[dict]:
    rows = []
    for example in json.loads((CONTRACT / "target-examples.json").read_text())["examples"]:
        status, raw = post(qa, example["body"])
        problems = []
        if example["target"] == "python":
            plain = json.loads(example["body"])
            del plain["target"]
            control_status, control_raw = post(qa, dump(plain))
            if (status, raw) != (control_status, control_raw) or status != 200:
                problems.append("python reply differs from the reply without target")
            outcome = "python_identical"
        else:
            plain = json.loads(example["body"])
            del plain["target"]
            control_status, control_raw = post(qa, dump(plain))
            version = json.loads(control_raw)["bundle"]["prompt"]["version"] if control_status == 200 else None
            problems, outcome = rust_in_scope(status, raw, example["body"], version)
        rows.append({"id": example["id"], "status": status, "outcome": outcome, "reply_sha256": sha(raw),
                     "passed": not problems, "problems": problems})
    # The CNL and JSON forms of one source give byte-identical Rust bundles. The v5
    # diagnostic spans locate each request body, so those replies differ.
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        twin = by_id.get(row["id"].replace("-cnl-rust", "-json-rust"))
        if (row["id"].endswith("-cnl-rust") and row["outcome"] == "rust_bundle"
                and (twin is None or twin["reply_sha256"] != row["reply_sha256"])):
            row["problems"].append("the CNL and JSON forms give different Rust replies")
            row["passed"] = False
    return rows


def rust_in_scope(status: int, raw: bytes, body: str, version: str | None) -> tuple[list[str], str]:
    """The reply for a prompt in Rust phase 1 scope: a Rust bundle for v1 to v4, NOT_IMPLEMENTED for v5."""
    if version == "som.prompt.v5":
        problems = one_diagnostic(status, raw, 501, NOT_IMPLEMENTED)
        if not problems and located(body, "json" if '"som.prompt-json.v1"' in body else "cnl",
                                    json.loads(raw)["diagnostics"][0]) != '"rust"':
            problems.append("NOT_IMPLEMENTED span is not the target value")
        return problems, "not_implemented_until_49"
    if status != 200:
        return [f"Rust compile gave {status}"], "rust_bundle"
    bundle = json.loads(raw).get("bundle", {})
    if bundle.get("version") != RUST_BUNDLE or bundle.get("target") != "rust":
        return ["the reply is not a Rust som.dsl-bundle.v2 bundle"], "rust_bundle"
    return [], "rust_bundle"


# Every source prompt in each form, with and without target.

def sources() -> list[dict]:
    cases = []
    for name in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v7-branch-null"):
        cnl = EXAMPLES / f"{name}.cnl"
        cases.append({"id": f"example-{name}", "group": "public_examples",
                      "prompt_file_sha256": sha((EXAMPLES / f"{name}.prompt.json").read_bytes()),
                      "prompt": json.loads((EXAMPLES / f"{name}.prompt.json").read_text()),
                      "cnl": cnl.read_text() if cnl.exists() else None})
    for case in json.loads(FIXTURES.read_text())["cases"]:
        cases.append({"id": case["id"], "group": "cnl_positive_fixtures:" + case["category"],
                      "prompt_sha256": case["prompt_sha256"],
                      "prompt": case["expected_prompt"], "cnl": case["cnl"]})
    for case in json.loads(COVERAGE.read_text())["cases"]:
        cases.append({"id": case["id"], "group": "coverage_cases", "prompt_file_sha256": case["prompt_file_sha256"],
                      "prompt": json.loads((COVERAGE.parent / case["prompt"]).read_text()),
                      "cnl": (COVERAGE.parent / case["cnl"]).read_text()})
    inventory = json.loads((LEGACY / "inventory-003.json").read_text())
    for index, group in enumerate(inventory["groups"]):
        request = json.loads((LEGACY / "run-005" / f"{index:04d}" / "assemble.request.json").read_text())
        cases.append({"id": group["id"], "group": "legacy:" + group["group"], "prompt_sha256": group["prompt_sha256"],
                      "prompt": request["bundle"]["prompt"], "cnl": None})
    return cases + rejections()


def rejections() -> list[dict]:
    """Requests compiler-v1 refuses. Their diagnostic must win over every target check."""
    def example(name: str) -> dict:
        return json.loads((EXAMPLES / f"{name}.prompt.json").read_text())

    v1, v4, v5 = example("v1"), example("v4"), example("v5")
    v1_cnl = (EXAMPLES / "v1.cnl").read_text()
    cases = [
        ("version-unknown", {**v1, "version": "som.prompt.v9"}, None),
        ("resource-missing", {k: v for k, v in v1.items() if k != "resource"}, None),
        ("field-type-unknown", {**v1, "resource": {**v1["resource"], "fields": [
            {**v1["resource"]["fields"][0], "type": "uuid"}] + v1["resource"]["fields"][1:]}}, None),
        ("filter-reserved", {**v4, "query": {**v4["query"], "filter_field": "before"}}, None),
        ("relation-missing", {k: v for k, v in v5.items() if k != "relation"}, None),
        ("cnl-version-unknown", v1, v1_cnl.replace("Use prompt version 1.", "Use prompt version 9.")),
        ("cnl-syntax", v1, v1_cnl.replace("End resource.", "End the resource now.")),
    ]
    return [{"id": f"reject-{name}", "group": "compiler_v1_rejections", "rejection": True,
             "prompt": prompt, "cnl": cnl, "json": cnl is None} for name, prompt, cnl in cases]


def requests(case: dict) -> list[tuple[str, dict]]:
    forms = [("json", {"format": "som.prompt-json.v1", "prompt": case["prompt"]})] if case.get("json", True) else []
    if case["cnl"] is not None:
        forms.append(("cnl", {"format": "som.cnl.v1", "source": case["cnl"]}))
    return forms


def check_form(qa: str, reference: str, case: dict, form: str, request: dict) -> dict:
    problems = []
    body = dump(request)
    status, raw = post(qa, body)
    ref_status, ref_raw = post(reference, body)
    if (status, raw) != (ref_status, ref_raw):
        problems.append("reply without target differs from the reference compiler")
    py_status, py_raw = post(qa, dump({**request, "target": "python"}))
    if (py_status, py_raw) != (status, raw):
        problems.append("target python differs from the reply without target")
    rust_body = dump({**request, "target": "rust"})
    rust_status, rust_raw = post(qa, rust_body)
    row = {"id": case["id"], "group": case["group"], "form": form,
           **{key: case[key] for key in ("prompt_sha256", "prompt_file_sha256") if key in case},
           "python_status": status, "python_reply_sha256": sha(raw), "rust_status": rust_status}
    if case.get("rejection") and status == 200:
        problems.append("compiler-v1 accepted a request built to be refused")
    if status != 200:
        # compiler-v1 diagnostics keep precedence over every target check.
        if (rust_status, rust_raw) != (status, raw):
            problems.append("target rust does not keep the compiler-v1 diagnostic")
        row.update(rust="compiler_v1_diagnostic", passed=not problems, problems=problems)
        return row
    # The Python files: the same bundle must assemble to the same bytes on both compilers.
    assemble_body = dump({"bundle": json.loads(raw)["bundle"]})
    files_status, files_raw = post(qa, assemble_body, "/v1/assemble")
    if files_status != 200 or (files_status, files_raw) != post(reference, assemble_body, "/v1/assemble"):
        problems.append("assembled Python files differ from the reference compiler")
    row["python_files_sha256"] = sha(files_raw)
    compiled = json.loads(raw)["bundle"]["prompt"]
    if form == "json" and compiled != case["prompt"]:
        problems.append("compiled prompt differs from the request prompt")
    found = rust_profile.check(compiled)
    diagnostic = json.loads(rust_raw).get("diagnostics", [{}])[0]
    if found is None:
        more, outcome = rust_in_scope(rust_status, rust_raw, rust_body, compiled["version"])
        problems += more
        row.update(rust="in_scope", rust_outcome=outcome, rust_reply_sha256=sha(rust_raw))
    else:
        # JSON pointers name the request member, so they carry the /prompt prefix.
        pointer = found["pointer"] if form == "cnl" else "/prompt" + found["pointer"]
        expected = {"code": "UNSUPPORTED_TARGET", "prompt_pointer": pointer,
                    "cause": found["cause"], "allowed_form": found["allowed_form"]}
        problems += one_diagnostic(rust_status, rust_raw, 422, expected)
        row["rust"] = found["rule"]
        if not problems:
            span = located(rust_body, form, diagnostic)
            leaf = resolve(compiled, found["pointer"])
            if form == "json":
                # The span must be the exact value token, not a truncated parent.
                if span != dump(leaf):
                    problems.append("JSON span is not the refused value token")
            else:
                # The span must be exactly the refused token: the bare word, a quoted
                # path, or the version number.
                row["cnl_span_sha256"] = sha(span.encode())
                text = leaf.removeprefix("som.prompt.v") if found["rule"] == "prompt_version" else str(leaf)
                if span not in (text, dump(text)):
                    problems.append("CNL span is not the refused token")
    row.update(passed=not problems, problems=problems)
    return row


def counts(rows: list[dict]) -> dict:
    tally: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for row in rows:
        tally[(row["group"], row["form"], row["rust"])] += 1
    return {f"{g}|{f}|{r}": n for (g, f, r), n in sorted(tally.items())}


def inventory_matches(rows: list[dict]) -> list[str]:
    """The JSON-form refusal counts must equal the frozen case inventory."""
    inventory = json.loads((CONTRACT / "case-inventory.json").read_text())
    problems = []
    json_rows = [r for r in rows if r["form"] == "json" and r["group"] != "compiler_v1_rejections"]
    want: collections.Counter[tuple[str, str]] = collections.Counter()
    for c in inventory["counts"]["cnl_positive_fixtures"]:
        want[("cnl_positive_fixtures:" + c["category"], c["rust"])] += c["count"]
    for c in inventory["counts"]["legacy"]:
        want[("legacy:" + c["group"], c["rust"])] += c["count"]
    for row in inventory["public_examples"]:
        want[("public_examples", row["rust"])] += 1
    for row in inventory["coverage_cases"]:
        want[("coverage_cases", row["rust"])] += 1
    got = collections.Counter((r["group"], r["rust"]) for r in json_rows)
    if got != want:
        problems.append(f"refusal counts differ: missing {dict(want - got)}, extra {dict(got - want)}")
    # Each CNL form must agree with its JSON form.
    by_json = {r["id"]: r["rust"] for r in json_rows}
    replies = {r["id"]: r.get("rust_reply_sha256") for r in json_rows}
    for row in rows:
        if row["form"] == "cnl" and row["id"] in by_json and by_json[row["id"]] != row["rust"]:
            problems.append(f"{row['id']}: CNL rule {row['rust']} != JSON rule {by_json.get(row['id'])}")
        # In-scope Rust replies must be byte-identical across the two forms.
        if (row["form"] == "cnl" and row.get("rust_outcome") == "rust_bundle" and row["id"] in replies
                and replies[row["id"]] != row["rust_reply_sha256"]):
            problems.append(f"{row['id']}: CNL Rust reply differs from the JSON Rust reply")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", required=True, help="QA compiler base URL")
    parser.add_argument("--reference", required=True, help="reference compiler base URL, read-only")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    negatives = negative_cases(args.qa)
    examples = target_examples(args.qa)
    jobs = [(case, form, request) for case in sources() for form, request in requests(case)]
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(lambda job: check_form(args.qa, args.reference, *job), jobs))
    inventory_problems = inventory_matches(rows)

    failed = ([r["id"] for r in negatives if not r["passed"]] + [r["id"] for r in examples if not r["passed"]]
              + [f"{r['id']}:{r['form']}" for r in rows if not r["passed"]])
    report = {
        "version": "som.rust-output-target-checks.v1",
        "issue": 47,
        "qa": args.qa,
        "reference": args.reference,
        "inputs": {p.relative_to(REPO).as_posix(): sha(p.read_bytes()) for p in
                   [CONTRACT / "negative-cases.json", CONTRACT / "target-examples.json",
                    CONTRACT / "case-inventory.json", CONTRACT / "scope.json", TESTS / "rust_profile.py",
                    FIXTURES, COVERAGE, LEGACY / "inventory-003.json"]},
        "summary": {
            "negative_cases": {"total": len(negatives), "passed": sum(r["passed"] for r in negatives)},
            "target_examples": {"total": len(examples), "passed": sum(r["passed"] for r in examples)},
            "source_forms": {"total": len(rows), "passed": sum(r["passed"] for r in rows)},
            "python_products": {"assembled": sum("python_files_sha256" in r for r in rows),
                                "distinct_file_sets": len({r.get("python_files_sha256") for r in rows} - {None})},
            "compiler_v1_precedence": {"total": sum(r["group"] == "compiler_v1_rejections" for r in rows),
                                       "passed": sum(r["group"] == "compiler_v1_rejections" and r["passed"]
                                                     for r in rows)},
            "inventory_problems": inventory_problems,
            "failed": failed,
        },
        "rule_counts": counts(rows),
        "negative_cases": negatives,
        "target_examples": examples,
        "source_forms": rows,
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False)[:4000])
    return 1 if failed or inventory_problems else 0


if __name__ == "__main__":
    sys.exit(main())
