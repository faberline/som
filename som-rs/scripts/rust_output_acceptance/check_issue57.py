#!/usr/bin/env python3
"""Issue #57 behavior checks: prompts with a field or foreign key named self, on a QA port.

usage (under som-rs/runtime/acceptance-m3-venv/bin/python, with CARGO_HOME the vendored union):
  check_issue57.py run --qa URL --out NEW_DIR [--language python|rust ...]
  check_issue57.py worker --qa URL --out DIR --part suite|fixture --language L   (internal)

The cases are the positive cases of som-rs/tests/compiler-v1/self-name-fixtures.json plus the
phase 2 products whose Python output #57 fixes (phase2/behavior.json python_defects: RS07, RS14,
RS16, prompts read from rust-output-phase2/rs-products-001 and hash-checked against its record).

For each case and language the typed prompt is compiled on --qa (target rust for Rust) and
assembled. Python bundles must select exactly the fixture's variant block ids. The product then
runs on the phase 2 checker that check_phase2.checker picks for the prompt: the phase 1 HTTP suite
(check_phase2.suite_case) or verify_all / verify_flow_service (check_phase2.fixture_case), with
check_phase2's own rows written under DIR/cases. A case passes when its row passes.

The fixture prompts are also compiled as CNL: a positive or accepted CNL source gives the same
bundle as its typed prompt. Each negative case, as typed JSON (Python and Rust target) and as
CNL, must give exactly the recorded status, code, detail, diagnostic pointer and CNL allowed form.
Accepted cases compile with status 200 for both targets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
PHASE2 = SOM_RS / "behavior" / "phase2"
sys.path.insert(0, str(PHASE2))
import check_phase2 as cp  # noqa: E402

FIXTURES = SOM_RS / "tests" / "compiler-v1" / "self-name-fixtures.json"
BEHAVIOR = SOM_RS / "contracts" / "rust-output-v1" / "phase2" / "behavior.json"
FORBIDDEN = (":8765", ":18765")


def post(qa: str, route: str, body: dict) -> tuple[int, Any]:
    request = urllib.request.Request(qa.rstrip("/") + route, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def block_ids(dsl: dict) -> list[str]:
    return sorted({b["id"] for f in dsl.get("files", []) for b in f.get("blocks", [])})


def positive_cases() -> list[dict]:
    rows = [{"id": c["id"], "source": "self-name-fixtures", "prompt": c["prompt"], "cnl": c["cnl"],
             "variants": c["python_variants"]} for c in cp.read(FIXTURES)["positive"]]
    record = {r["id"]: r for r in cp.read(cp.RS / "result.json")["cases"]}
    for case_id in sorted(cp.read(BEHAVIOR)["python_defects"]):
        prompt = cp.read(cp.RS / case_id / "prompt.json")
        if cp.qa.sha(prompt) != record[case_id]["prompt_sha256"]:
            raise SystemExit(f"{case_id}: prompt hash differs from rs-products-001")
        rows.append({"id": case_id, "source": "rust-output-phase2/rs-products-001", "prompt": prompt,
                     "cnl": None, "variants": None})
    for row in rows:
        prompt = row["prompt"]
        row.update(family="issue57", group="self", prompt_version=prompt["version"],
                   capabilities=bool(prompt.get("capabilities")))
        row["checker"], row["standard"] = cp.checker(row)
    return rows


def product(qa: str, row: dict, language: str) -> tuple[dict[str, str], dict]:
    body = {"format": "som.prompt-json.v1", "prompt": row["prompt"]}
    if language == "rust":
        body["target"] = "rust"
    status, compiled = post(qa, "/v1/compile", body)
    if status != 200:
        raise RuntimeError(f"compile returned {status}: {str(compiled)[:500]}")
    bundle = compiled["bundle"]
    record = {"compile_status": status, "bundle_sha256": cp.qa.sha(bundle)}
    if row["cnl"] is not None:
        cnl = {**{k: v for k, v in body.items() if k != "prompt"}, "format": "som.cnl.v1", "source": row["cnl"]}
        status, from_cnl = post(qa, "/v1/compile", cnl)
        if status != 200 or from_cnl["bundle"] != bundle:
            raise RuntimeError(f"CNL compile returned {status} or a different bundle")
        record["cnl_same_bundle"] = True
    if language == "python":
        selected = [i for i in block_ids(bundle["dsl"]) if i.endswith(".self")]
        record["variants"] = selected
        if row["variants"] is not None and selected != sorted(row["variants"]):
            raise RuntimeError(f"variant blocks {selected}, expected {sorted(row['variants'])}")
        if row["variants"] is None and not selected and row["prompt_version"] != "som.prompt.v6":
            raise RuntimeError("no self variant block selected")
    status, assembled = post(qa, "/v1/assemble", {"bundle": bundle})
    if status != 200:
        raise RuntimeError(f"assemble returned {status}: {str(assembled)[:500]}")
    files = assembled["files"]
    record.update(files_sha256=cp.qa.sha(files), files=len(files))
    return files, record


def as_case(row: dict, files: dict[str, str], record: dict) -> dict:
    case = {k: v for k, v in row.items() if k not in ("prompt", "cnl", "variants")}
    case.update(prompt_sha256=cp.qa.sha(row["prompt"]), product_sha256=cp.qa.sha(files),
                issue57=record, load_prompt=lambda: row["prompt"], load_files=lambda: files)
    return case


def install() -> None:
    """check_phase2 reads frozen run records; here the product is the one the candidate gives."""
    cp.load = lambda case: (case["load_prompt"](), case["load_files"]())
    cp.rust_files = lambda _qa, case, _prompt, row: (row.setdefault("rust", case["issue57"]),
                                                     case["load_files"]())[1]


def prepared(qa: str, row: dict, language: str, out: pathlib.Path) -> dict | None:
    try:
        files, record = product(qa, row, language)
    except Exception as error:  # noqa: BLE001
        failed = {"id": row["id"], "language": language, "passed": False,
                  "problems": [f"{type(error).__name__}: {error}"[:3000]]}
        (out / "cases" / f"{cp.key(row['id'])}.json").write_text(json.dumps(failed, indent=1) + "\n")
        print(f"{row['id']}: FAIL {failed['problems'][0][:200]}", file=sys.stderr, flush=True)
        return None
    return as_case(row, files, record)


def worker(args: argparse.Namespace) -> int:
    out, qa, language = args.out, args.qa, args.language
    install()
    rows = [r for r in positive_cases() if (r["checker"] == "phase1_suite") == (args.part == "suite")]
    if args.part == "suite":
        sys.path.insert(0, str(SOM_RS / "behavior"))
        import hosts
        with tempfile.TemporaryDirectory(prefix="som-issue57-", dir=out / "fixtures",
                                         ignore_cleanup_errors=True) as scratch:
            work = pathlib.Path(scratch)
            ports = hosts.Ports()
            with hosts.cluster(work, ports.take()) as pg:
                host = hosts.Host(qa, pg, work / "products", work / "target", sys.executable)
                host.ports = ports
                for row in rows:
                    case = prepared(qa, row, language, out)
                    if case is not None:
                        cp.suite_case(out, case, host, language)
        return 0
    from m3_local_fixture import M3Fixture
    with M3Fixture(out / "fixtures" / f"fixture-{language}") as fixture:
        for row in rows:
            case = prepared(qa, row, language, out)
            if case is not None:
                cp.fixture_case(out, case, fixture, language, qa, out / "target")
    return 0


def negatives(qa: str) -> list[dict]:
    results = []
    data = cp.read(FIXTURES)
    for case in data["negative"] + data["accepted"]:
        want = case.get("expected")
        requests = [("json", {"format": "som.prompt-json.v1", "prompt": case["prompt"]}),
                    ("json-rust", {"format": "som.prompt-json.v1", "prompt": case["prompt"], "target": "rust"}),
                    ("cnl", {"format": "som.cnl.v1", "source": case["cnl"]})]
        for form, body in requests:
            status, reply = post(qa, "/v1/compile", body)
            if want is None:
                got, expected = {"status": status}, {"status": 200}
            else:
                diagnostic = (reply.get("diagnostics") or [{}])[0]
                got = {"status": status, "code": reply.get("code"), "detail": reply.get("detail"),
                       "prompt_pointer": diagnostic.get("prompt_pointer")}
                expected = {"status": want["status"], "code": want["code"], "detail": want["detail"],
                            "prompt_pointer": want["cnl_prompt_pointer" if form == "cnl" else "json_prompt_pointer"]}
                if form == "cnl":
                    got["allowed_form"] = diagnostic.get("allowed_form")
                    expected["allowed_form"] = want["cnl_allowed_form"]
            results.append({"id": case["id"], "form": form, "passed": got == expected,
                            "got": got, "expected": expected})
            print(f"{case['id']} {form}: {'ok' if got == expected else 'FAIL'}", file=sys.stderr, flush=True)
    return results


def run(args: argparse.Namespace) -> int:
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; use a new directory")
    for name in ("fixtures", "target"):
        (out / name).mkdir(parents=True)
    summary: dict[str, Any] = {"qa": args.qa, "fixtures_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
                               "languages": args.language, "negative": negatives(args.qa), "positive": {}}
    env = {**os.environ, **cp.WORKER_ENV}
    for language in args.language:
        lane = out / language
        (lane / "cases").mkdir(parents=True)
        (lane / "work").mkdir()
        (lane / "fixtures").mkdir()
        (lane / "target").mkdir()
        codes = []
        for part in ("suite", "fixture"):
            with (lane / f"worker-{part}.log").open("w") as log:
                codes.append(subprocess.run(
                    [sys.executable, str(pathlib.Path(__file__).resolve()), "worker", "--qa", args.qa,
                     "--out", str(lane), "--part", part, "--language", language],
                    stdout=log, stderr=log, env=env, check=False).returncode)
        rows = {}
        for row in positive_cases():
            path = lane / "cases" / f"{cp.key(row['id'])}.json"
            rows[row["id"]] = cp.read(path)["passed"] if path.exists() else "not run"
        summary["positive"][language] = {"worker_exit_codes": codes, "cases": rows}
    passed = (all(r["passed"] for r in summary["negative"])
              and all(v is True for lane in summary["positive"].values() for v in lane["cases"].values())
              and all(c == 0 for lane in summary["positive"].values() for c in lane["worker_exit_codes"]))
    summary["passed"] = passed
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": passed, "positive": {l: v["cases"] for l, v in summary["positive"].items()},
                      "negative_failures": [r["id"] + "/" + r["form"] for r in summary["negative"] if not r["passed"]]},
                     indent=1))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "worker"):
        p = sub.add_parser(name)
        p.add_argument("--qa", required=True)
        p.add_argument("--out", type=pathlib.Path, required=True)
    sub.choices["run"].add_argument("--language", nargs="+", choices=("python", "rust"), default=["python", "rust"])
    sub.choices["worker"].add_argument("--language", choices=("python", "rust"), required=True)
    sub.choices["worker"].add_argument("--part", choices=("suite", "fixture"), required=True)
    args = parser.parse_args()
    if args.qa.rstrip("/").endswith(FORBIDDEN):
        raise SystemExit("QA ports only")
    return run(args) if args.command == "run" else worker(args)


if __name__ == "__main__":
    sys.exit(main())
