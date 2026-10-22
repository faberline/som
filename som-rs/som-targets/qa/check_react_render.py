#!/usr/bin/env python3
"""Compare the Rust React renderer with the reference renderer on all 129 acceptance cases (issue #127).

Usage: check_react_render.py <work dir (new)>

For each case of contracts/react-output-v1/case-inventory.json in the acceptance set, the
script builds the compile request bodies with target react (CNL where a CNL source exists,
and typed JSON), runs the som-targets react_render example on them, and checks that:
- every body of a case compiles to the same React bundle;
- the bundle prompt is the case's typed prompt;
- assemble returns the 19 files of the product layout;
- the 17 fixed files equal contracts/react-output-v1/reference byte for byte;
- src/schema.ts and README.md equal tests/react-output-v1/render_reference.py byte for byte.

The legacy prompts are read from .local and only written below the work dir. The report,
<work>/report.json, names cases by id and hashes only. Exit 0 when every case passes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_TARGETS = HERE.parent
SOM_RS = SOM_TARGETS.parent
REPO = SOM_RS.parent
CONTRACT = SOM_RS / "contracts" / "react-output-v1"
REFERENCE = CONTRACT / "reference"
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
COVERAGE = SOM_RS / "tests" / "rust-output-v1" / "cases" / "index.json"
ASSETS = SOM_RS / "compiler-assets" / "current"
GENERATED = ("README.md", "src/schema.ts")

_spec = importlib.util.spec_from_file_location(
    "render_reference", SOM_RS / "tests" / "react-output-v1" / "render_reference.py")
assert _spec and _spec.loader
render_reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_reference)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_body(prompt: dict[str, Any]) -> str:
    return compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "react"})


def cnl_body(source: str) -> str:
    return compact({"format": "som.cnl.v1", "source": source, "target": "react"})


def checked(path: pathlib.Path, expected: str) -> bytes:
    data = path.read_bytes()
    if sha(data) != expected:
        raise SystemExit(f"{path} does not match its inventory hash")
    return data


def build_cases(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """The acceptance cases: id, request bodies and the typed prompt the bundle must carry."""
    cases = []
    for row in inventory["public_examples"]:
        if row["react"] != "in_scope":
            continue
        prompt = json.loads(checked(REPO / row["prompt"], row["prompt_file_sha256"]))
        source = checked(REPO / row["cnl"], row["cnl_sha256"]).decode()
        cases.append({"id": row["id"], "kind": "public_example", "prompt": prompt,
                      "bodies": [cnl_body(source), json_body(prompt)]})

    fixtures = {case["id"]: case for case in json.loads(FIXTURES.read_text())["cases"]}
    for row in inventory["version_positive_fixtures"]:
        if row["react"] != "in_scope":
            continue
        case = fixtures[row["id"]]
        if sha(case["cnl"].encode()) != row["cnl_sha256"]:
            raise SystemExit(f"{row['id']}: CNL does not match its inventory hash")
        cases.append({"id": row["id"], "kind": "version_positive", "prompt": case["expected_prompt"],
                      "bodies": [cnl_body(case["cnl"]), json_body(case["expected_prompt"])]})

    coverage = {case["id"]: case for case in json.loads(COVERAGE.read_text())["cases"]}
    for row in inventory["coverage_cases"]:
        if row["react"] != "in_scope":
            continue
        case = coverage[row["id"]]
        prompt = json.loads(checked(COVERAGE.parent / case["prompt"], row["prompt_file_sha256"]))
        source = checked(COVERAGE.parent / case["cnl"], row["cnl_sha256"]).decode()
        cases.append({"id": row["id"], "kind": "coverage", "prompt": prompt,
                      "bodies": [cnl_body(source), json_body(prompt)]})

    names = {case["id"]: case for case in json.loads((CONTRACT / "target-examples.json").read_text())["name_cases"]}
    for row in inventory["name_cases"]:
        body = names[row["id"]]["body"]
        if sha(body.encode()) != row["body_sha256"]:
            raise SystemExit(f"{row['id']}: body does not match its inventory hash")
        # A name case has one request body; its typed prompt is the bundle prompt.
        cases.append({"id": row["id"], "kind": "name_case", "prompt": None, "bodies": [body]})

    products = REPO / inventory["sources"]["public_products"]["directory"]
    for row in inventory["public_products_in_scope"]:
        directory = products / row["index"]
        prompt = json.loads(checked(directory / "prompt.json", row["prompt_file_sha256"]))
        bodies = [json_body(prompt)]
        if (directory / "source.cnl").is_file():
            bodies.insert(0, cnl_body((directory / "source.cnl").read_text()))
        cases.append({"id": row["id"], "kind": "public_product", "prompt": prompt, "bodies": bodies})

    legacy = inventory["sources"]["legacy_inventory"]
    run = REPO / legacy["run"]
    for row in inventory["legacy_in_scope"]:
        request = json.loads((run / f"{row['inventory_index']:04d}" / "assemble.request.json").read_text())
        cases.append({"id": row["id"], "kind": "legacy", "prompt": request["bundle"]["prompt"],
                      "prompt_sha256": row["prompt_sha256"], "bodies": [json_body(request["bundle"]["prompt"])]})
    return cases


def check_case(case: dict[str, Any], result: dict[str, Any], out: pathlib.Path) -> list[str]:
    problems = []
    if result.get("status") != "ok":
        return [f"render failed: {result.get('error')}"]
    if len(set(result["bundle_sha256"])) != 1 or len(result["bundle_sha256"]) != len(case["bodies"]):
        problems.append("the request bodies gave different bundles")
    prompt = json.loads((out / case["id"] / "prompt.json").read_text())
    if case["prompt"] is not None and prompt != case["prompt"]:
        problems.append("the bundle prompt is not the case prompt")
    if "prompt_sha256" in case and case["prompt_sha256"] not in (result["prompt_sha256"], sha(compact(prompt).encode())):
        problems.append("the bundle prompt does not match the legacy inventory hash")
    reference = {str(path.relative_to(REFERENCE)): path.read_bytes()
                 for path in sorted(REFERENCE.rglob("*")) if path.is_file()}
    expected = dict(reference)
    expected.update({name: text.encode() for name, text in render_reference.render(prompt).items()})
    files = out / case["id"] / "files"
    rendered = {str(path.relative_to(files)): path.read_bytes() for path in sorted(files.rglob("*")) if path.is_file()}
    if sorted(rendered) != sorted(expected):
        problems.append(f"file set differs: {sorted(set(rendered) ^ set(expected))}")
    for name in sorted(set(rendered) & set(expected)):
        if rendered[name] != expected[name]:
            problems.append(f"{name} differs from the reference renderer")
        if sha(rendered[name]) != result["files"].get(name):
            problems.append(f"{name} differs from the reported hash")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    work = pathlib.Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=False)
    inventory = json.loads((CONTRACT / "case-inventory.json").read_text())
    cases = build_cases(inventory)
    total = inventory["counts"]["acceptance_total"]
    if len(cases) != total:
        raise SystemExit(f"built {len(cases)} cases, the inventory names {total}")
    (work / "cases.json").write_text(json.dumps({"cases": [{"id": c["id"], "bodies": c["bodies"]} for c in cases]}))
    out = work / "render"
    env = dict(os.environ)
    env.setdefault("CARGO_TARGET_DIR", str(REPO / ".local" / "react-output" / "cargo-target-001"))
    run = subprocess.run(["cargo", "run", "--offline", "--quiet", "--example", "react_render", "--",
                          str(ASSETS), str(work / "cases.json"), str(out)],
                         cwd=SOM_TARGETS, env=env, capture_output=True, text=True, check=False)
    (work / "react_render.log").write_text(run.stdout + run.stderr)
    results = {row["id"]: row for row in json.loads((out / "results.json").read_text())["cases"]}

    rows = []
    for case in cases:
        result = results.get(case["id"], {"status": "missing"})
        problems = check_case(case, result, out)
        rows.append({"id": case["id"], "kind": case["kind"], "forms": len(case["bodies"]),
                     "passed": not problems, "problems": problems,
                     "prompt_sha256": result.get("prompt_sha256"),
                     "bundle_sha256": (result.get("bundle_sha256") or [None])[0],
                     "generated_sha256": {name: result.get("files", {}).get(name) for name in GENERATED}})
    failed = [row for row in rows if not row["passed"]]
    report = {
        "issue": 127,
        "tool": "som-rs/som-targets/qa/check_react_render.py",
        "renderer": "som-rs/som-targets/examples/react_render.rs",
        "reference": "som-rs/tests/react-output-v1/render_reference.py",
        "acceptance_total": total,
        "passed": len(rows) - len(failed),
        "failed": len(failed),
        "react_render_exit": run.returncode,
        "cases": rows,
    }
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                      "failures": [{"id": r["id"], "problems": r["problems"]} for r in failed][:10]}, indent=2))
    return 0 if not failed and run.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
