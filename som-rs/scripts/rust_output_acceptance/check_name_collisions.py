#!/usr/bin/env python3
"""Check that python_name_collisions refuses no accepted prompt (issue #58).

acceptance-plan.json issue57 (amendment 004): no public, legacy, fixture or example prompt is
refused by python_name_collisions. Each prompt below is compiled on the candidate; a reply whose
detail ends with the policy detail fails the check. Other outcomes are recorded and not judged here
(python_replay.py judges the public and legacy responses):

- the compiler-v1 examples, typed JSON and CNL (contracts/compiler-v1/examples);
- the 119 CNL positive fixtures, typed JSON and CNL;
- the 7 typed JSON fixtures;
- the 127 public prompts (public-products-003b) and the 544 legacy prompts (legacy544 run-005).

usage: check_name_collisions.py --qa URL --out OUT.json [--workers N]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import urllib.error
import urllib.request
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
TESTS = SOM_RS / "tests" / "compiler-v1"
PUBLIC = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "public-products-003b"
LEGACY = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544" / "run-005"
DETAIL = "name collides with a generated Python identifier"
FORBIDDEN = (":8765", ":18765")
EXPECTED = {"example": 16, "cnl_fixture": 238, "typed_fixture": 7, "public": 127, "legacy": 544}


def read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def post(qa: str, body: dict) -> tuple[int, Any]:
    request = urllib.request.Request(qa.rstrip("/") + "/v1/compile", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def typed(prompt: dict) -> dict:
    return {"format": "som.prompt-json.v1", "prompt": prompt}


def requests() -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    for path in sorted(EXAMPLES.glob("*.prompt.json")):
        name = path.name.removesuffix(".prompt.json")
        out.append(("example", f"{name}.prompt.json", typed(read(path))))
        out.append(("example", f"{name}.cnl", {"format": "som.cnl.v1", "source": (EXAMPLES / f"{name}.cnl").read_text()}))
    for case in read(TESTS / "cnl-positive-fixtures.json")["cases"]:
        out.append(("cnl_fixture", f"{case['id']} json", typed(case["expected_prompt"])))
        out.append(("cnl_fixture", f"{case['id']} cnl", {"format": "som.cnl.v1", "source": case["cnl"]}))
    for name, prompt in read(TESTS / "typed-json-fixtures.json")["fixtures"].items():
        out.append(("typed_fixture", name, typed(prompt)))
    for directory in sorted(PUBLIC.glob("[0-9][0-9][0-9]")):
        out.append(("public", directory.name, typed(read(directory / "prompt.json"))))
    for index, record in enumerate(read(LEGACY / "result.json")["records"]):
        prompt = read(LEGACY / f"{index:04d}" / "variant.bundle.json")["prompt"]
        out.append(("legacy", f"{index:04d} {record['id']}", typed(prompt)))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.qa.rstrip("/").endswith(FORBIDDEN):
        parser.error("QA checks never use ports 8765 or 18765")
    if args.out.exists():
        parser.error(f"{args.out} exists")
    items = requests()
    counts = {group: sum(1 for g, _, _ in items if g == group) for group in EXPECTED}
    if counts != EXPECTED:
        raise SystemExit(f"prompt counts {counts}, expected {EXPECTED}")

    def one(item: tuple[str, str, dict]) -> dict:
        group, name, body = item
        status, reply = post(args.qa, body)
        detail = reply.get("detail") or ""
        return {"group": group, "name": name, "status": status, "code": reply.get("code"),
                "refused_by_python_name_collisions": detail.endswith(DETAIL)}

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(one, items))
    failed = [f"{r['group']} {r['name']}" for r in rows if r["refused_by_python_name_collisions"]]
    summary = {group: {"total": counts[group],
                       "compiled": sum(1 for r in rows if r["group"] == group and r["status"] == 200),
                       "other_status": sorted({r["status"] for r in rows if r["group"] == group and r["status"] != 200})}
               for group in EXPECTED}
    report = {"version": "som.python-name-collisions-check.v1", "issue": 58, "qa": args.qa,
              "detail": DETAIL, "groups": summary, "failed": failed, "passed": not failed, "rows": rows}
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({"passed": report["passed"], "groups": summary, "failed": failed}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
