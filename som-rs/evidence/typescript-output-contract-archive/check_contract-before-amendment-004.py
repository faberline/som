#!/usr/bin/env python3
"""Check the TypeScript output contract files for consistency.

It needs no compiler and no network. It validates the schemas and the frozen
request bodies, runs the scope reference check on the examples and the cases,
reruns every case builder with --check, and compares the catalog, the product
layout, the bundle schema, the reference files and toolchain.json with each
other. ts_profile is checked against the typed JSON cases, the public examples
and the coverage cases; CNL cases are checked by span only. The checks of the
amendment 001 fields check the sealed values: the reference lockfile against
toolchain.json lock_packages, the formatter sha256 by its rule, and the
reference verification evidence. The vendored cache and prettier tree hashes
are recomputed only when those local directories exist.
--files prints the SHA-256 of every contract, tool and evidence file, the
files map a receipt seals. --receipt also checks every file hash in a freeze
receipt or an amendment receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Callable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from common import CONTRACT, EXAMPLES, HERE, REPO, SOM_RS, ts_profile
from tree_sha import tree_sha

V1 = SOM_RS / "contracts" / "compiler-v1"
RUST = SOM_RS / "contracts" / "rust-output-v1"
COVERAGE = SOM_RS / "tests" / "rust-output-v1" / "cases"
EVIDENCE = SOM_RS / "evidence" / "typescript-output-v1"

JSON_FILES = ["acceptance-plan.json", "api.schema.json", "behavior-normalization.json", "bundle.schema.json",
              "case-inventory.json", "negative-cases.json", "product-layout.json", "scope.json", "target-examples.json",
              "toolchain.json", "catalog/typescript-catalog.v1.json", "reference/package.json",
              "reference/package-lock.json", "reference/tsconfig.json"]
BUILDERS = ["build_negative_cases.py", "build_target_examples.py", "build_case_inventory.py"]
IN_SCOPE = {f"som.prompt.v{v}" for v in range(1, 6)}
MIGRATION = re.compile(r"^migrations/0001_[A-Za-z_][A-Za-z0-9_]*\.sql$")


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def h(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


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
                          (RUST / "api.schema.json", []), (RUST / "bundle.schema.json", []),
                          (CONTRACT / "api.schema.json", []), (CONTRACT / "bundle.schema.json", [])]:
        schema = load(path)
        resource = Resource.from_contents(schema)
        resources.append((schema["$id"], resource))
        resources.extend((alias, resource) for alias in aliases)
    return Registry().with_resources(resources)


def check_schemas(report: Report) -> Callable[[str], Draft202012Validator]:
    for name in ["api.schema.json", "bundle.schema.json"]:
        try:
            Draft202012Validator.check_schema(load(CONTRACT / name))
            report.check(f"schema {name} is a valid 2020-12 schema", True)
        except Exception as err:  # noqa: BLE001
            report.check(f"schema {name} is a valid 2020-12 schema", False, str(err)[:200])
    reg = registry()
    return lambda ref: Draft202012Validator({"$ref": ref}, registry=reg)


def layout_paths() -> list[str]:
    return [entry["path"] for entry in load(CONTRACT / "product-layout.json")["files"]]


def sample_bundle(prompt: dict[str, Any]) -> dict[str, Any]:
    paths = [p.replace("<revision>", "rev0000") for p in layout_paths()]
    files = [{"path": path, "blocks": [{"id": "ts.sample", "name": "sample", "params": {}, "refs": []}]}
             for path in paths]
    descriptor = {"id": "sample", "sha256": "0" * 64}
    assets: dict[str, Any] = {key: dict(descriptor) for key in load(CONTRACT / "api.schema.json")["x-manifest"]
                              ["asset_keys"] if key != "semantic_contracts"}
    assets["semantic_contracts"] = [dict(descriptor)]
    bundle = {"version": "som.dsl-bundle.v2", "target": "typescript", "prompt": prompt,
              "dsl": {"version": "som.dsl.typescript.v1", "files": files}, "assets": assets}
    bundle["hashes"] = {"prompt_sha256": h(prompt), "dsl_sha256": h(bundle["dsl"]),
                        "payload_sha256": h({k: bundle[k] for k in ("version", "target", "prompt", "dsl", "assets")})}
    return bundle


def check_bundle_schema(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    prompt = load(EXAMPLES / "v1.prompt.json")
    ts = schema("som.dsl-bundle.v2.typescript")
    bundle = sample_bundle(prompt)
    report.check("sample TypeScript bundle with the 11 layout files is valid", ts.is_valid(bundle),
                 "; ".join(e.message for e in ts.iter_errors(bundle))[:300])
    report.check("sample TypeScript bundle is not a v1 bundle", not schema("som.dsl-bundle.v1").is_valid(bundle))
    report.check("sample TypeScript bundle is not a Rust bundle", not schema("som.dsl-bundle.v2").is_valid(bundle))
    success = schema("som.compiler-api.v1.typescript-target#/$defs/compile_success")
    report.check("compile success admits a TypeScript bundle", success.is_valid({"status": "ok", "bundle": bundle}))
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("target python", lambda b: b.update(target="python")),
        ("target rust", lambda b: b.update(target="rust")),
        ("missing target", lambda b: b.pop("target")),
        ("Rust DSL version", lambda b: b["dsl"].update(version="som.dsl.rust.v1")),
        ("13 files", lambda b: b["dsl"]["files"].extend([b["dsl"]["files"][-1]] * 2)),
        ("unknown path", lambda b: b["dsl"]["files"][0].update(path="src/extra.ts")),
        ("Rust path", lambda b: b["dsl"]["files"][0].update(path="Cargo.toml")),
        ("bad migration name", lambda b: b["dsl"]["files"][8].update(path="migrations/rev0000.sql")),
        ("65 blocks in a file", lambda b: b["dsl"]["files"][0].update(blocks=b["dsl"]["files"][0]["blocks"] * 65)),
        ("missing formatter asset", lambda b: b["assets"].pop("formatter")),
        ("extra member", lambda b: b.update(extra=1)),
    ]
    for label, mutate in mutations:
        bad = json.loads(json.dumps(bundle))
        mutate(bad)
        report.check(f"TypeScript bundle schema refuses {label}", not ts.is_valid(bad))
    twelve = json.loads(json.dumps(bundle))
    twelve["dsl"]["files"].append(twelve["dsl"]["files"][-1])
    report.check("TypeScript bundle schema admits 12 files", ts.is_valid(twelve))


def check_layout(report: Report) -> None:
    layout = load(CONTRACT / "product-layout.json")
    schema = load(CONTRACT / "bundle.schema.json")
    path_schema = schema["$defs"]["typescript_dsl"]["properties"]["files"]["items"]["properties"]["path"]["anyOf"]
    enum = set(path_schema[0]["enum"])
    paths = layout_paths()
    fixed = {p for p in paths if not p.startswith("migrations/")}
    report.check("layout file_count equals its file list", layout["file_count"] == len(paths) == 11)
    report.check("bundle schema path enum equals the layout's fixed files", enum == fixed, str(enum ^ fixed))
    report.check("bundle schema migration pattern matches the layout migration",
                 path_schema[1]["pattern"] == MIGRATION.pattern
                 and MIGRATION.match("migrations/0001_rev0000.sql") is not None)
    report.check("layout total block limit is 64", layout["limits"]["max_total_blocks"] == 64)
    report.check("layout limits equal the bundle schema limits",
                 layout["limits"]["max_files"] == schema["$defs"]["typescript_dsl"]["properties"]["files"]["maxItems"]
                 and layout["limits"]["max_blocks_per_file"] == schema["$defs"]["typescript_dsl"]["properties"]
                 ["files"]["items"]["properties"]["blocks"]["maxItems"])
    report.check("schema equivalence excludes the TypeScript migrator table",
                 layout["excluded_from_schema_equivalence"] == load(CONTRACT / "scope.json")["identifiers"]
                 ["reserved_tables"])
    behavior = load(CONTRACT / "behavior-normalization.json")
    report.check("behavior normalization excludes alembic_version and _som_ts_migrations",
                 "alembic_version" in behavior["schema_equivalence"]["query"]
                 and "_som_ts_migrations" in behavior["schema_equivalence"]["query"])
    ids = [d["id"] for d in behavior["divergences"]]
    report.check("divergence ids are D1 to D7", ids == [f"D{n}" for n in range(1, 8)], str(ids))


def check_catalog(report: Report) -> None:
    catalog = load(CONTRACT / "catalog" / "typescript-catalog.v1.json")
    paths = layout_paths()
    report.check("catalog DSL version is som.dsl.typescript.v1", catalog["dsl_version"] == "som.dsl.typescript.v1")
    catalog_paths = [f["path"].replace("{migration.revision}", "<revision>") for f in catalog["files"]]
    report.check("catalog file paths equal the layout in order", catalog_paths == paths, str(catalog_paths))
    blocks = [b for f in catalog["files"] for b in f["blocks"]]
    report.check("every catalog block id starts with ts.", all(b["id"].startswith("ts.") for b in blocks))
    report.check("catalog block ids are unique", len({b["id"] for b in blocks}) == len(blocks))
    report.check("every catalog block names in-scope versions only",
                 all(b["versions"] and set(b["versions"]) <= IN_SCOPE for b in blocks))
    for version in sorted(IN_SCOPE):
        files = [f for f in catalog["files"] if any(version in b["versions"] for b in f["blocks"])]
        count = max((sum(version in b["versions"] for b in f["blocks"]) for f in catalog["files"]), default=0)
        total = sum(version in b["versions"] for b in blocks)
        report.check(f"{version} plan has every layout file and at most 64 blocks per file and in total",
                     len(files) == len(paths) and count <= 64 and total <= 64,
                     f"{len(files)} files, {count} blocks in a file, {total} in total")


def check_reference(report: Report) -> None:
    toolchain = load(CONTRACT / "toolchain.json")
    package = load(CONTRACT / "reference" / "package.json")
    tsconfig = load(CONTRACT / "reference" / "tsconfig.json")
    report.check("reference package is som-app", package["name"] == "som-app" and package.get("private") is True)
    report.check("reference package is an ES module", package.get("type") == "module")
    report.check("reference engines pin the toolchain Node",
                 "v" + package["engines"]["node"] == toolchain["node"]["version"])
    report.check("reference dependencies equal toolchain.json",
                 package["dependencies"] == toolchain["packages"]["dependencies"])
    report.check("reference devDependencies equal toolchain.json",
                 package["devDependencies"] == toolchain["packages"]["dev_dependencies"])
    report.check("reference package has no prettier key", "prettier" not in package)
    report.check("toolchain formatter is the pinned prettier",
                 toolchain["formatter"]["package"] == f"prettier {toolchain['packages']['dev_dependencies']['prettier']}")
    options = tsconfig["compilerOptions"]
    for key in ["strict", "noEmit", "erasableSyntaxOnly", "verbatimModuleSyntax", "allowImportingTsExtensions"]:
        report.check(f"reference tsconfig sets {key}", options.get(key) is True)
    check_lockfile(report, toolchain, package)
    formatter = toolchain["formatter"]
    node = toolchain["node"]
    report.check("formatter sha256 follows its rule",
                 formatter["sha256"] == h({"node": node["sha256"], "libnode": node["libnode"]["sha256"],
                                           "prettier_tree_sha256": formatter["prettier_tree_sha256"]}))
    check_reference_verification(report, toolchain)
    cache = REPO / toolchain["vendored_cache"]["path"]
    if cache.is_dir():
        got = tree_sha(cache)
        report.check("vendored cache tree hash equals toolchain.json",
                     got == (toolchain["vendored_cache"]["tree_sha256"], toolchain["vendored_cache"]["tree_files"],
                             toolchain["vendored_cache"]["tree_symlinks"]), str(got))
    else:
        print(f"NOTE vendored cache {cache.relative_to(REPO)} is absent: its tree hash was not recomputed")


def check_lockfile(report: Report, toolchain: dict[str, Any], package: dict[str, Any]) -> None:
    lock = load(CONTRACT / "reference" / "package-lock.json")
    report.check("reference lockfile is lockfileVersion 3 for som-app",
                 lock.get("lockfileVersion") == 3 and lock.get("name") == "som-app"
                 and lock.get("version") == package["version"] and lock.get("requires") is True)
    root = lock["packages"][""]
    report.check("lockfile root equals reference/package.json",
                 root.get("name") == package["name"] and root.get("version") == package["version"]
                 and root.get("dependencies") == package["dependencies"]
                 and root.get("devDependencies") == package["devDependencies"]
                 and root.get("engines") == package["engines"])
    entries = [{"path": path, "name": path.removeprefix("node_modules/"), "version": entry["version"],
                "resolved": entry["resolved"], "integrity": entry["integrity"], **({"dev": True} if entry.get("dev")
                                                                                   else {})}
               for path, entry in lock["packages"].items() if path != ""]
    report.check("lockfile packages equal toolchain.json lock_packages",
                 entries == toolchain["packages"]["lock_packages"])
    report.check("no lockfile package has an install script",
                 not any(entry.get("hasInstallScript") for entry in lock["packages"].values()))
    report.check("every lockfile package resolves to registry.npmjs.org with sha512 integrity",
                 all(e["resolved"].startswith("https://registry.npmjs.org/") and e["integrity"].startswith("sha512-")
                     for e in entries))
    direct = {**toolchain["packages"]["dependencies"], **toolchain["packages"]["dev_dependencies"]}
    versions = {e["name"]: e["version"] for e in entries}
    report.check("every pinned package has its pinned version in the lockfile",
                 all(versions.get(name) == version for name, version in direct.items()))
    report.check("lockfile dev flags follow dependencies and devDependencies",
                 all(("dev" in e) == (e["name"] in toolchain["packages"]["dev_dependencies"])
                     for e in entries if e["name"] in direct))


def check_reference_verification(report: Report, toolchain: dict[str, Any]) -> None:
    verification = toolchain["reference_verification"]
    report.check("reference verification passed", verification["status"] == "passed")
    evidence = load(REPO / verification["evidence"])
    product = REPO / evidence["product"]["copy"]
    files = {str(path.relative_to(product)) for path in product.rglob("*") if path.is_file()}
    layout = {p.replace("<revision>", "rev0000") for p in layout_paths()}
    report.check("reference product has the 11 layout files", files == layout, str(sorted(files ^ layout)))
    report.check("reference product file hashes equal the evidence",
                 all(hashlib.sha256((product / rel).read_bytes()).hexdigest() == digest
                     for rel, digest in evidence["product"]["files"].items()) and set(evidence["product"]["files"])
                 == files)
    for name in ["package.json", "package-lock.json", "tsconfig.json"]:
        report.check(f"reference product {name} is the reference bytes",
                     (product / name).read_bytes() == (CONTRACT / "reference" / name).read_bytes())
    steps = [(g["step"], g["exit_code"]) for g in evidence["gates"]]
    report.check("reference gates install, typecheck, format_check and test exit 0",
                 steps == [("install", 0), ("typecheck", 0), ("format_check", 0), ("test", 0)], str(steps))
    report.check("reference gate commands equal toolchain.json commands",
                 all(g["command"] == toolchain["commands"][g["step"]] for g in evidence["gates"]),
                 str([g["command"] for g in evidence["gates"]]))
    report.check("reference lockfile unchanged by the commands",
                 evidence["lock_unchanged"] is True and evidence["lock_sha256_before"] == evidence["lock_sha256_after"]
                 == hashlib.sha256((CONTRACT / "reference" / "package-lock.json").read_bytes()).hexdigest())
    report.check("reference run smoke passed", evidence["run_smoke"]["passed"] is True
                 and evidence["run_smoke"]["sigterm_exit_code"] == 0)
    report.check("reference evidence passed", evidence["passed"] is True)
    report.check("reference evidence prettier tree equals toolchain.json",
                 evidence["prettier_tree_sha256"] == toolchain["formatter"]["prettier_tree_sha256"])
    report.check("reference evidence cache tree equals toolchain.json",
                 evidence["vendored_cache_tree_sha256_after"] == toolchain["vendored_cache"]["tree_sha256"])
    prettier = REPO / evidence["prettier_tree_path"]
    if prettier.is_dir():
        report.check("prettier tree hash equals toolchain.json",
                     tree_sha(prettier)[0] == toolchain["formatter"]["prettier_tree_sha256"])
    else:
        print(f"NOTE {evidence['prettier_tree_path']} is absent: the prettier tree hash was not recomputed")


def check_negative_cases(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    cases = load(CONTRACT / "negative-cases.json")["cases"]
    ts_request = schema("som.compiler-api.v1.typescript-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    error = schema("som.compiler-api.v1#/$defs/compiler_error")
    scope = load(CONTRACT / "scope.json")
    known = {entry["code"] for entry in scope["refusals"].values()}
    ids = [case["id"] for case in cases]
    report.check("negative case ids are unique", len(ids) == len(set(ids)))
    forms = {rule: entry["allowed_form"] for rule, entry in scope["refusals"].items()}
    report.check("refusal allowed forms are distinct", len(set(forms.values())) == len(forms))
    for rule, form in forms.items():
        report.check(f"refusal {rule} has a TS case",
                     any(c["id"].startswith("TS") and c["expected"]["allowed_form"] == form for c in cases))
    for case in cases:
        cid = case["id"]
        control = json.loads(case["python_control_body"])
        report.check(f"{cid} control has no target", "target" not in control)
        report.check(f"{cid} control is a compiler-v1 request", v1_request.is_valid(control))
        expected = case["expected"]
        report.check(f"{cid} expected diagnostic has the compiler-v1 shape",
                     error.is_valid({"status": "error", "code": expected["code"], "detail": "x",
                                     "diagnostics": [expected]}))
        if cid.startswith(("TS", "TP")) and case["form"] == "cnl":
            text = json.loads(case["body"])["source"].encode()
        else:
            text = case["body"].encode()
        span = text[expected["byte_start"]:expected["byte_end"]].decode()
        report.check(f"{cid} byte span holds span_text", span == case["span_text"], repr(span))
        if cid == "TT07_JSON_TARGET_DUPLICATE":
            continue
        body = json.loads(case["body"])
        if cid.startswith("TT"):
            report.check(f"{cid} body fails the TypeScript-target schema", not ts_request.is_valid(body))
            continue
        report.check(f"{cid} body has target typescript", body.get("target") == "typescript")
        report.check(f"{cid} body passes the TypeScript-target schema", ts_request.is_valid(body))
        if cid.startswith("TS") and expected["code"] == "UNSUPPORTED_TARGET":
            report.check(f"{cid} code is a scope refusal", expected["code"] in known and case["http_status"] == 422)
            if body["format"] == "som.prompt-json.v1":
                got = ts_profile.check(body["prompt"])
                ok = got is not None and "/prompt" + got["pointer"] == expected["prompt_pointer"] \
                    and got["cause"] == expected["cause"] and got["allowed_form"] == expected["allowed_form"]
                report.check(f"{cid} matches ts_profile", ok, json.dumps(got))


def check_target_examples(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    ts_request = schema("som.compiler-api.v1.typescript-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    data = load(CONTRACT / "target-examples.json")
    examples = data["examples"]
    report.check("target examples cover CNL and JSON with typescript and python",
                 {(e["id"].split("-")[-2], e["target"]) for e in examples}
                 == {("cnl", "typescript"), ("json", "typescript"), ("cnl", "python"), ("json", "python")})
    for example in examples + data["name_cases"]:
        body = json.loads(example["body"])
        report.check(f"target example {example['id']} passes the TypeScript-target schema",
                     ts_request.is_valid(body) and body["target"] == example["target"])
        if example["target"] == "typescript" and body["format"] == "som.prompt-json.v1":
            report.check(f"target example {example['id']} is in scope", ts_profile.check(body["prompt"]) is None)
        if "same_as_body" in example:
            same = json.loads(example["same_as_body"])
            report.check(f"target example {example['id']} control is a compiler-v1 request",
                         v1_request.is_valid(same) and {**same, "target": "python"} == body)
        if "python_control_body" in example:
            control = json.loads(example["python_control_body"])
            report.check(f"name case {example['id']} control is the body without target",
                         v1_request.is_valid(control) and {**control, "target": "typescript"} == body)


def check_profile(report: Report) -> None:
    for path in sorted(EXAMPLES.glob("*.prompt.json")):
        prompt = load(path)
        got = ts_profile.check(prompt)
        if prompt["version"] in IN_SCOPE:
            report.check(f"example {path.name} is in scope", got is None, json.dumps(got))
        else:
            report.check(f"example {path.name} is refused by prompt_version",
                         got is not None and got["rule"] == "prompt_version")
    for case in load(COVERAGE / "index.json")["cases"]:
        report.check(f"coverage case {case['id']} is in scope",
                     ts_profile.check(load(COVERAGE / case["prompt"])) is None)
    scope = load(CONTRACT / "scope.json")
    report.check("reserved names are __proto__, constructor and prototype",
                 scope["identifiers"]["reserved_names"] == ["__proto__", "constructor", "prototype"])


def check_plan(report: Report) -> None:
    plan = load(CONTRACT / "acceptance-plan.json")
    recipes = [r["id"] for r in plan["assemble_refusals"]["recipes"]]
    report.check("assemble recipes are A1 to A12", recipes == [f"A{n}" for n in range(1, 13)], str(recipes))
    inventory = load(CONTRACT / "case-inventory.json")["counts"]
    report.check("acceptance total is 125", inventory["acceptance_total"] == 125)
    for key in ["case_inventory", "negative_cases", "target_examples"]:
        report.check(f"acceptance plan {key} exists", (CONTRACT / plan[key]).is_file())


def check_builders(report: Report) -> None:
    for builder in BUILDERS:
        run = subprocess.run([sys.executable, str(HERE / builder), "--check"], capture_output=True, text=True,
                             cwd=REPO)
        report.check(f"{builder} --check", run.returncode == 0, (run.stdout + run.stderr).strip()[-300:])


def contract_files() -> list[pathlib.Path]:
    roots = [CONTRACT, HERE, EVIDENCE]
    return sorted({path for root in roots if root.is_dir() for path in root.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts})


def check_receipt(report: Report, receipt_path: pathlib.Path) -> None:
    receipt = load(receipt_path)
    for rel, digest in receipt["files"].items():
        path = REPO / rel
        report.check(f"receipt {rel}", path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest)
    present = {str(path.relative_to(REPO)) for path in contract_files()}
    report.check("receipt lists every contract, tool and evidence file", present == set(receipt["files"]),
                 json.dumps(sorted(present ^ set(receipt["files"]))))
    report.check("receipt target_contract hash is H(files)", receipt["target_contract"]["sha256"] == h(receipt["files"]))
    if "amendment" in receipt:
        previous = REPO / receipt["previous_receipt"]
        report.check("amendment receipt names its previous receipt by hash",
                     previous.is_file() and hashlib.sha256(previous.read_bytes()).hexdigest()
                     == receipt["previous_receipt_sha256"])
        report.check("amendment previous target hash is the previous receipt's",
                     receipt["target_contract"]["previous_sha256"] == load(previous)["target_contract"]["sha256"])
        old = load(previous)["files"]
        changed = sorted(k for k in receipt["files"] if k in old and receipt["files"][k] != old[k])
        added = sorted(k for k in receipt["files"] if k not in old)
        report.check("amendment changed_paths and added_paths equal the files map diff",
                     receipt["changed_paths"] == changed and receipt["added_paths"] == added
                     and set(old) <= set(receipt["files"]))
        report.check("amendment changes record each changed path",
                     [c["path"] for c in receipt["changes"]] == changed)
        for change in receipt["changes"]:
            archived = REPO / change["archived_copy"]
            report.check(f"amendment change {change['path']} hashes",
                         change["old_sha256"] == old.get(change["path"]) == change["archived_copy_sha256"]
                         and archived.is_file() and hashlib.sha256(archived.read_bytes()).hexdigest()
                         == change["old_sha256"] and change["new_sha256"] == receipt["files"][change["path"]])
        report.check("amendment was sealed after independent review",
                     receipt["status"] == "amendment_sealed_after_independent_review"
                     and receipt["implementation_already_in_progress"] is False)
    else:
        report.check("receipt was sealed before implementation",
                     receipt["status"] == "contract_frozen_before_implementation"
                     and receipt["implementation_started_before_freeze"] is False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", action="store_true", help="print the SHA-256 of every contract file")
    parser.add_argument("--receipt", type=pathlib.Path, help="also check the files map of a receipt")
    args = parser.parse_args()
    report = Report()
    for name in JSON_FILES:
        try:
            load(CONTRACT / name)
            report.check(f"{name} parses", True)
        except Exception as err:  # noqa: BLE001
            report.check(f"{name} parses", False, str(err)[:200])
    schema = check_schemas(report)
    check_bundle_schema(report, schema)
    check_layout(report)
    check_catalog(report)
    check_reference(report)
    check_negative_cases(report, schema)
    check_target_examples(report, schema)
    check_profile(report)
    check_plan(report)
    check_builders(report)
    if args.receipt:
        check_receipt(report, args.receipt)
    if args.files:
        for path in contract_files():
            print(hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(REPO))
    for line in report.failed:
        print("FAIL", line)
    print(f"{len(report.passed)} passed, {len(report.failed)} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
