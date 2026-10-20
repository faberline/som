#!/usr/bin/env python3
"""Check the frozen Rust output contract files for consistency.

It needs no compiler and no network. It validates the schemas and the frozen
request bodies, runs the scope reference check on the in-scope and refused
examples, reruns every case builder with --check, compares the reference Cargo
files with toolchain.json, and checks the recorded evidence. With --receipt it
also checks every file hash in a freeze receipt. With --host it checks the
toolchain binary hashes on this machine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import Any, Callable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REPO = ROOT.parent
CONTRACT = ROOT / "contracts" / "rust-output-v1"
V1 = ROOT / "contracts" / "compiler-v1"
EVIDENCE = ROOT / "evidence" / "rust-output-v1"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts" / "compiler_acceptance"))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402
from qa_harness import canonical  # type: ignore[import-not-found]  # noqa: E402

JSON_FILES = ["acceptance-plan.json", "api.schema.json", "behavior-normalization.json", "bundle.schema.json",
              "case-inventory.json", "negative-cases.json", "product-layout.json", "scope.json", "target-examples.json",
              "toolchain.json"]
BUILDERS = ["build_cases.py", "build_negative_cases.py", "build_case_inventory.py", "build_target_examples.py"]


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def h(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passed.append(name)
        else:
            self.failed.append(f"{name}: {detail}" if detail else name)


def registry() -> Registry:
    resources = []
    for path, aliases in [(V1 / "api.schema.json", []), (V1 / "bundle.schema.json", ["bundle.schema.json"]),
                          (V1 / "compiler-prompt.schema.json", ["compiler-prompt.schema.json"]),
                          (CONTRACT / "api.schema.json", []), (CONTRACT / "bundle.schema.json", [])]:
        schema = load(path)
        resource = Resource.from_contents(schema)
        resources.append((schema["$id"], resource))
        resources.extend((alias, resource) for alias in aliases)
    return Registry().with_resources(resources)


def validator(reg: Registry, ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": ref}, registry=reg)


def check_schemas(report: Report) -> Callable[[str], Draft202012Validator]:
    for name in ["api.schema.json", "bundle.schema.json"]:
        try:
            Draft202012Validator.check_schema(load(CONTRACT / name))
            report.check(f"schema {name} is a valid 2020-12 schema", True)
        except Exception as err:  # noqa: BLE001
            report.check(f"schema {name} is a valid 2020-12 schema", False, str(err)[:200])
    reg = registry()
    return lambda ref: validator(reg, ref)


def sample_bundle(prompt: dict[str, Any]) -> dict[str, Any]:
    files = [{"path": path, "blocks": [{"id": "rust.sample", "name": "sample", "params": {}, "refs": []}]}
             for path in ["Cargo.toml", "Cargo.lock", "src/main.rs", "src/lib.rs", "src/models.rs", "src/schemas.rs",
                          "src/api.rs", "src/db.rs", "migrations/0001_rev0000.sql", "tests/api.rs", "README.md"]]
    descriptor = {"id": "sample", "sha256": "0" * 64}
    assets: dict[str, Any] = {key: dict(descriptor) for key in ["grammar", "compiler_contract", "selection_policy", "literal_policy",
                                                "serializer", "target_contract", "catalog", "block_library",
                                                "backend", "formatter"]}
    assets["semantic_contracts"] = [dict(descriptor)]
    bundle = {"version": "som.dsl-bundle.v2", "target": "rust", "prompt": prompt,
              "dsl": {"version": "som.dsl.rust.v1", "files": files}, "assets": assets}
    bundle["hashes"] = {"prompt_sha256": h(prompt), "dsl_sha256": h(bundle["dsl"]),
                        "payload_sha256": h({k: bundle[k] for k in ("version", "target", "prompt", "dsl", "assets")})}
    return bundle


def check_bundle_schema(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    prompt = load(V1 / "examples" / "v1.prompt.json")
    v2 = schema("som.dsl-bundle.v2")
    v1 = schema("som.dsl-bundle.v1")
    bundle = sample_bundle(prompt)
    report.check("sample Rust bundle with 11 files is valid v2", v2.is_valid(bundle))
    report.check("sample Rust bundle is not a v1 bundle", not v1.is_valid(bundle))
    success = schema("som.compiler-api.v1.rust-target#/$defs/compile_success")
    report.check("compile success admits a v2 bundle", success.is_valid({"status": "ok", "bundle": bundle}))
    assemble = schema("som.compiler-api.v1.rust-target#/$defs/assemble_request")
    report.check("assemble request admits a v2 bundle", assemble.is_valid({"bundle": bundle}))
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("target python", lambda b: b.update(target="python")),
        ("missing target", lambda b: b.pop("target")),
        ("13 files", lambda b: b["dsl"]["files"].extend([b["dsl"]["files"][-1]] * 2)),
        ("unknown path", lambda b: b["dsl"]["files"][0].update(path="src/extra.rs")),
        ("bad migration name", lambda b: b["dsl"]["files"][8].update(path="migrations/rev0000.sql")),
        ("65 blocks in a file", lambda b: b["dsl"]["files"][0].update(blocks=b["dsl"]["files"][0]["blocks"] * 65)),
        ("missing formatter asset", lambda b: b["assets"].pop("formatter")),
        ("extra member", lambda b: b.update(extra=1)),
    ]
    for label, mutate in mutations:
        bad = json.loads(json.dumps(bundle))
        mutate(bad)
        report.check(f"v2 schema refuses {label}", not v2.is_valid(bad))
    twelve = json.loads(json.dumps(bundle))
    twelve["dsl"]["files"].append(twelve["dsl"]["files"][-1])
    report.check("v2 schema admits 12 files", v2.is_valid(twelve))


def check_negative_cases(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    cases = load(CONTRACT / "negative-cases.json")["cases"]
    rust_request = schema("som.compiler-api.v1.rust-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    error = schema("som.compiler-api.v1#/$defs/compiler_error")
    scope = load(CONTRACT / "scope.json")
    known = {entry["code"] for entry in scope["refusals"].values()}
    ids = [case["id"] for case in cases]
    report.check("negative case ids are unique", len(ids) == len(set(ids)))
    forms = {rule: entry["allowed_form"] for rule, entry in scope["refusals"].items()}
    report.check("refusal allowed forms are distinct", len(set(forms.values())) == len(forms))
    # Amendment 004: these phase 1 refusals moved to compiler-v1 python_name_collisions, for every target.
    moved = load(CONTRACT / "phase2" / "naming.json")["phase1_refusals_moved_to_compiler_v1"]
    for rule, form in forms.items():
        if rule in moved:
            report.check(f"refusal {rule} moved to compiler-v1 has no Rust RS case",
                         not any(c["id"].startswith("RS") and c["expected"]["allowed_form"] == form for c in cases))
            continue
        report.check(f"refusal {rule} has an RS case",
                     any(c["id"].startswith("RS") and c["expected"]["allowed_form"] == form for c in cases))
    for case in cases:
        cid = case["id"]
        control = json.loads(case["python_control_body"])
        report.check(f"{cid} control has no target", "target" not in control)
        report.check(f"{cid} control is a compiler-v1 request", v1_request.is_valid(control))
        report.check(f"{cid} control is a Rust-target request", rust_request.is_valid(control))
        expected = case["expected"]
        report.check(f"{cid} expected diagnostic has the compiler-v1 shape",
                     error.is_valid({"status": "error", "code": expected["code"], "detail": "x",
                                     "diagnostics": [expected]}))
        span = case["body"].encode()[expected["byte_start"]:expected["byte_end"]].decode()
        if not cid.startswith(("RS", "RP")) or case.get("form") == "json":
            report.check(f"{cid} byte span holds span_text", span == case["span_text"], repr(span))
        else:
            source = json.loads(case["body"])["source"].encode()
            got_span = source[expected["byte_start"]:expected["byte_end"]].decode()
            report.check(f"{cid} CNL byte span holds span_text", got_span == case["span_text"], repr(got_span))
        if cid == "RT07_JSON_TARGET_DUPLICATE":
            continue
        body = json.loads(case["body"])
        if cid.startswith("RT"):
            report.check(f"{cid} body fails the Rust-target schema", not rust_request.is_valid(body))
            continue
        report.check(f"{cid} body has target rust", body.get("target") == "rust")
        report.check(f"{cid} body passes the Rust-target schema", rust_request.is_valid(body))
        if cid.startswith("RS") and expected["code"] == "INVALID_VALUE":
            # Amendment 004: compiler-v1 refuses the prompt for every target; the phase 1 profile still names
            # it with a moved rule, and the control gives the same diagnostic.
            report.check(f"{cid} is a compiler-v1 name collision refusal",
                         case["http_status"] == 400 and expected["cause"].endswith(
                             ": name collides with a generated Python identifier")
                         and case["python_control_expected"].startswith("the same diagnostic"))
            if body["format"] == "som.prompt-json.v1":
                got = rust_profile.check(body["prompt"])
                report.check(f"{cid} is a moved phase 1 refusal", got is not None and got["rule"] in moved
                             and "/prompt" + got["pointer"] == expected["prompt_pointer"], json.dumps(got))
            continue
        if cid.startswith("RS"):
            report.check(f"{cid} code is a scope refusal", expected["code"] in known and case["http_status"] == 422)
            if body["format"] == "som.prompt-json.v1":
                got = rust_profile.check(body["prompt"])
                ok = got is not None and "/prompt" + got["pointer"] == expected["prompt_pointer"] \
                    and got["cause"] == expected["cause"] and got["allowed_form"] == expected["allowed_form"]
                report.check(f"{cid} matches rust_profile", ok, json.dumps(got))


def check_target_examples(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    rust_request = schema("som.compiler-api.v1.rust-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    examples = load(CONTRACT / "target-examples.json")["examples"]
    report.check("target examples cover CNL and JSON with rust and python",
                 {(e["id"].split("-")[-2], e["target"]) for e in examples}
                 == {("cnl", "rust"), ("json", "rust"), ("cnl", "python"), ("json", "python")})
    for example in examples:
        body = json.loads(example["body"])
        report.check(f"target example {example['id']} passes the Rust-target schema",
                     rust_request.is_valid(body) and body["target"] == example["target"])
        if example["target"] == "rust" and body["format"] == "som.prompt-json.v1":
            report.check(f"target example {example['id']} is in scope", rust_profile.check(body["prompt"]) is None)
        if "same_as_body" in example:
            same = json.loads(example["same_as_body"])
            report.check(f"target example {example['id']} control is a compiler-v1 request",
                         v1_request.is_valid(same) and {**same, "target": "python"} == body)


def check_profile(report: Report) -> None:
    examples = load(V1 / "examples" / "index.json")
    names = sorted(p.name for p in (V1 / "examples").glob("*.prompt.json"))
    for name in names:
        prompt = load(V1 / "examples" / name)
        got = rust_profile.check(prompt)
        in_scope = prompt["version"] in {"som.prompt.v1", "som.prompt.v2", "som.prompt.v3", "som.prompt.v4",
                                         "som.prompt.v5"}
        if in_scope:
            report.check(f"example {name} is in scope", got is None, json.dumps(got))
            report.check(f"example {name} has no capabilities", "capabilities" not in prompt)
        else:
            report.check(f"example {name} is refused by prompt_version",
                         got is not None and got["rule"] == "prompt_version")
    report.check("examples index parses", isinstance(examples, (dict, list)))
    index = load(HERE / "cases" / "index.json")
    for case in index["cases"]:
        prompt = load(HERE / "cases" / f"{case['id']}.prompt.json")
        report.check(f"coverage case {case['id']} is in scope", rust_profile.check(prompt) is None)
        report.check(f"coverage case {case['id']} has no capabilities", "capabilities" not in prompt)


def check_builders(report: Report) -> None:
    for builder in BUILDERS:
        run = subprocess.run([sys.executable, str(HERE / builder), "--check"], capture_output=True, text=True,
                             cwd=REPO)
        report.check(f"{builder} --check", run.returncode == 0, (run.stdout + run.stderr).strip()[-300:])


def check_reference(report: Report) -> None:
    toolchain = load(CONTRACT / "toolchain.json")
    manifest = tomllib.loads((CONTRACT / "reference" / "Cargo.toml").read_text())
    report.check("reference package is som_app", manifest["package"]["name"] == "som_app")
    report.check("reference edition is the toolchain edition",
                 manifest["package"].get("edition") == toolchain["rust"]["edition"])
    for section, key in [("dependencies", "dependencies"), ("dev-dependencies", "dev_dependencies")]:
        frozen = toolchain["crates"][key]
        actual = manifest.get(section, {})
        report.check(f"reference {section} names equal toolchain.json", sorted(actual) == sorted(frozen),
                     f"{sorted(actual)} != {sorted(frozen)}")
        for crate, spec in frozen.items():
            got = actual.get(crate, {})
            got = {"version": got} if isinstance(got, str) else got
            ok = got.get("version") == spec["version"] \
                and sorted(got.get("features", [])) == sorted(spec.get("features", [])) \
                and got.get("default-features", True) == spec.get("default_features", True)
            report.check(f"reference {crate} pin equals toolchain.json", ok, json.dumps(got))
    lock = CONTRACT / "reference" / "Cargo.lock"
    lock_sha = sha(lock)
    report.check("Cargo.lock hash is the verified hash",
                 any(lock_sha in line for line in toolchain["reference_verification"]["results"]), lock_sha)
    packages = lock.read_text().count("[[package]]")
    report.check("Cargo.lock package count", packages == toolchain["crates"]["lock_packages"], str(packages))


def check_layout(report: Report) -> None:
    layout = load(CONTRACT / "product-layout.json")
    bundle = load(CONTRACT / "bundle.schema.json")
    paths = [f["path"] for f in layout["files"]]
    report.check("layout file_count", layout["file_count"] == len(paths) == len(set(paths)))
    fixed = bundle["$defs"]["rust_dsl"]["properties"]["files"]["items"]["properties"]["path"]["anyOf"][0]["enum"]
    report.check("bundle schema fixed paths equal layout", sorted(fixed + ["migrations/0001_<revision>.sql"])
                 == sorted(paths))
    report.check("bundle file limit covers the layout",
                 bundle["$defs"]["rust_dsl"]["properties"]["files"]["maxItems"] >= len(paths))


def check_plan(report: Report) -> None:
    plan = load(CONTRACT / "acceptance-plan.json")
    recipes = plan["assemble_refusals"]["recipes"]
    ids = [r["id"] for r in recipes]
    report.check("assemble recipe ids are A1 to A9", ids == [f"A{i}" for i in range(1, 10)])
    for recipe in recipes:
        report.check(f"recipe {recipe['id']} pointer is in the bundle", recipe["prompt_pointer"].startswith("/bundle"))
    report.check("issue gates cover #46 to #51", sorted(plan["issue_gates"]) == [f"#{n}" for n in range(46, 52)])
    inventory = load(CONTRACT / "case-inventory.json")
    counts = inventory["counts"]
    total = counts["public_examples_in_scope"] + counts["version_positive_in_scope"] + counts["coverage_cases"] \
        + counts["legacy_in_scope"]
    report.check("inventory acceptance total", total == counts["acceptance_total"], str(total))
    scope = load(CONTRACT / "scope.json")
    for rule, entry in scope["refusals"].items():
        report.check(f"refusal {rule} names the Python fallback", "omit target to generate Python" in
                     entry["allowed_form"])
        report.check(f"refusal {rule} is UNSUPPORTED_TARGET 422",
                     entry["code"] == "UNSUPPORTED_TARGET" and entry["http_status"] == 422)


def check_evidence(report: Report) -> None:
    # The latest controls run: amendment 004 changed negative-cases.json after the 2026-10-15 run.
    controls = load(sorted(EVIDENCE.glob("negative-python-controls-*.json"))[-1])
    report.check("Python controls ran on the current negative cases",
                 controls["negative_cases_sha256"] == sha(CONTRACT / "negative-cases.json"))
    report.check("Python controls all passed", controls["failed"] == [] and controls["passed"] == 32)
    cnl_scope = [c["id"] for c in load(CONTRACT / "negative-cases.json")["cases"]
                 if c["id"].startswith("RS") and c["form"] == "cnl" and c["expected"]["code"] != "INVALID_VALUE"]
    rows = {r["id"]: r for r in controls["results"]}
    report.check("every CNL scope case's compiled prompt gives its frozen refusal",
                 all(rows[cid].get("rust_profile_matches") is True for cid in cnl_scope), json.dumps(cnl_scope))
    coverage = load(EVIDENCE / "coverage-cases-python-2026-10-15.json")
    report.check("coverage cases all passed on Python", coverage["failed"] == [] and coverage["passed"] == 6)


def check_host(report: Report) -> None:
    toolchain = load(CONTRACT / "toolchain.json")
    rust_bin = pathlib.Path(toolchain["rust"]["bin_dir"])
    for name in ["rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver"]:
        path = rust_bin / name
        report.check(f"host {name} hash", path.exists() and sha(path) == toolchain["rust"][name]["sha256"])
    pg_bin = pathlib.Path(toolchain["postgresql"]["bin_dir"])
    for name in ["postgres", "initdb", "pg_ctl"]:
        path = (pg_bin / name).resolve()
        report.check(f"host {name} hash", path.exists() and sha(path) == toolchain["postgresql"][name]["sha256"])


def check_receipt(report: Report, receipt_path: pathlib.Path) -> None:
    receipt = load(receipt_path)
    for rel, digest in receipt["files"].items():
        path = REPO / rel
        report.check(f"receipt {rel}", path.exists() and sha(path) == digest)
    present = {str(path.relative_to(REPO)) for root in (CONTRACT, HERE, EVIDENCE) for path in root.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts}
    report.check("receipt lists every contract, test and evidence file", present == set(receipt["files"]),
                 json.dumps(sorted(present ^ set(receipt["files"]))))
    target = hashlib.sha256(canonical(receipt["files"]).encode()).hexdigest()
    report.check("receipt target_contract hash is H(files)", receipt["target_contract"]["sha256"] == target)
    report.check("receipt was sealed before implementation",
                 receipt["status"] == "contract_frozen_before_implementation"
                 and receipt["implementation_started_before_freeze"] is False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--host", action="store_true")
    args = parser.parse_args()
    report = Report()
    for name in JSON_FILES:
        try:
            load(CONTRACT / name)
            report.check(f"{name} parses", True)
        except Exception as err:  # noqa: BLE001
            report.check(f"{name} parses", False, str(err))
    schema = check_schemas(report)
    check_bundle_schema(report, schema)
    check_negative_cases(report, schema)
    check_target_examples(report, schema)
    check_profile(report)
    check_builders(report)
    check_reference(report)
    check_layout(report)
    check_plan(report)
    check_evidence(report)
    if args.host:
        check_host(report)
    if args.receipt:
        check_receipt(report, args.receipt)
    print(json.dumps({"passed": len(report.passed), "failed": report.failed}, indent=2))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
