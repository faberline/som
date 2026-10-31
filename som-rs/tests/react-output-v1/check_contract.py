#!/usr/bin/env python3
"""Check the React output contract files for consistency.

It needs no compiler and no network. It validates the schemas and the frozen
request bodies, runs the scope reference check on the examples and the cases,
reruns every case builder with --check, and compares the catalog, the product
layout, the bundle schema, the reference files, toolchain.json, the API
contract and the acceptance plan with each other. react_profile is checked
against the typed JSON cases, the public examples and the coverage cases; CNL
cases are checked by span only. The pre-freeze evidence is checked against the
reference bytes: every fixed file of every verified product equals its
reference file. The vendored cache tree hash and the presence of each cached
tarball are recomputed only when the cache directory exists.
--files prints the SHA-256 of every contract, tool and evidence file, the
files map a receipt seals. --receipt also checks every file hash in a freeze
receipt or an amendment receipt.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import sys
from typing import Any, Callable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from common import CONTRACT, COVERAGE_CASES, EVIDENCE, EXAMPLES, HERE, REPO, SOM_RS, react_profile
from tree_sha import tree_sha

V1 = SOM_RS / "contracts" / "compiler-v1"
RUST = SOM_RS / "contracts" / "rust-output-v1"
TS = SOM_RS / "contracts" / "typescript-output-v1"

JSON_FILES = ["acceptance-plan.json", "api-contract.json", "api.schema.json", "bundle.schema.json",
              "case-inventory.json", "negative-cases.json", "product-layout.json", "scope.json", "target-examples.json",
              "toolchain.json", "catalog/react-catalog.v1.json", "reference/package.json",
              "reference/package-lock.json", "reference/tsconfig.json"]
BUILDERS = ["build_negative_cases.py", "build_target_examples.py", "build_case_inventory.py"]
IN_SCOPE = {f"som.prompt.v{v}" for v in range(1, 5)}
GATE_STEPS = ["install", "typecheck", "format_check", "test", "build-1", "build-2"]
LOCK_FLAGS = ["dev", "optional", "devOptional", "hasInstallScript", "os", "cpu", "libc"]


def load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def h(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
                          (TS / "api.schema.json", []), (TS / "bundle.schema.json", []),
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
    files = [{"path": path, "blocks": [{"id": "react.sample", "name": "sample", "params": {}, "refs": []}]}
             for path in layout_paths()]
    descriptor = {"id": "sample", "sha256": "0" * 64}
    assets: dict[str, Any] = {key: dict(descriptor) for key in load(CONTRACT / "api.schema.json")["x-manifest"]
                              ["asset_keys"] if key != "semantic_contracts"}
    assets["semantic_contracts"] = [dict(descriptor)]
    bundle = {"version": "som.dsl-bundle.v2", "target": "react", "prompt": prompt,
              "dsl": {"version": "som.dsl.react.v1", "files": files}, "assets": assets}
    bundle["hashes"] = {"prompt_sha256": h(prompt), "dsl_sha256": h(bundle["dsl"]),
                        "payload_sha256": h({k: bundle[k] for k in ("version", "target", "prompt", "dsl", "assets")})}
    return bundle


def check_bundle_schema(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    prompt = load(EXAMPLES / "v1.prompt.json")
    react = schema("som.dsl-bundle.v2.react")
    bundle = sample_bundle(prompt)
    report.check("sample React bundle with the 19 layout files is valid", react.is_valid(bundle),
                 "; ".join(e.message for e in react.iter_errors(bundle))[:300])
    report.check("sample React bundle is not a v1 bundle", not schema("som.dsl-bundle.v1").is_valid(bundle))
    report.check("sample React bundle is not a Rust bundle", not schema("som.dsl-bundle.v2").is_valid(bundle))
    report.check("sample React bundle is not a TypeScript bundle",
                 not schema("som.dsl-bundle.v2.typescript").is_valid(bundle))
    success = schema("som.compiler-api.v1.react-target#/$defs/compile_success")
    report.check("compile success admits a React bundle", success.is_valid({"status": "ok", "bundle": bundle}))
    assemble = schema("som.compiler-api.v1.react-target#/$defs/assemble_request")
    report.check("assemble request admits a React bundle", assemble.is_valid({"bundle": bundle}),
                 "; ".join(e.message for e in assemble.iter_errors({"bundle": bundle}))[:300])
    mutations: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        ("target python", lambda b: b.update(target="python")),
        ("target rust", lambda b: b.update(target="rust")),
        ("target typescript", lambda b: b.update(target="typescript")),
        ("missing target", lambda b: b.pop("target")),
        ("TypeScript DSL version", lambda b: b["dsl"].update(version="som.dsl.typescript.v1")),
        ("20 files", lambda b: b["dsl"]["files"].append(b["dsl"]["files"][-1])),
        ("unknown path", lambda b: b["dsl"]["files"][0].update(path="src/extra.ts")),
        ("TypeScript path", lambda b: b["dsl"]["files"][0].update(path="src/app.ts")),
        ("65 blocks in a file", lambda b: b["dsl"]["files"][0].update(blocks=b["dsl"]["files"][0]["blocks"] * 65)),
        ("a file without blocks", lambda b: b["dsl"]["files"][0].update(blocks=[])),
        ("missing formatter asset", lambda b: b["assets"].pop("formatter")),
        ("extra member", lambda b: b.update(extra=1)),
    ]
    for label, mutate in mutations:
        bad = json.loads(json.dumps(bundle))
        mutate(bad)
        report.check(f"React bundle schema refuses {label}", not react.is_valid(bad))


def check_layout(report: Report) -> None:
    layout = load(CONTRACT / "product-layout.json")
    schema = load(CONTRACT / "bundle.schema.json")
    files_schema = schema["$defs"]["react_dsl"]["properties"]["files"]
    enum = files_schema["items"]["properties"]["path"]["enum"]
    paths = layout_paths()
    fixed = [entry["path"] for entry in layout["files"] if entry["kind"] == "fixed"]
    generated = [entry["path"] for entry in layout["files"] if entry["kind"] == "generated"]
    report.check("layout counts equal its file list",
                 layout["file_count"] == len(paths) == 19 and layout["fixed_file_count"] == len(fixed) == 17
                 and layout["generated_file_count"] == len(generated) == 2 and len(set(paths)) == len(paths))
    report.check("generated files are src/schema.ts and README.md", generated == ["src/schema.ts", "README.md"])
    report.check("bundle schema path enum equals the layout files in order", enum == paths, str(enum))
    limits = layout["limits"]
    report.check("layout limits equal the bundle schema limits",
                 limits["max_files"] == files_schema["maxItems"] == 19
                 and limits["max_blocks_per_file"] == files_schema["items"]["properties"]["blocks"]["maxItems"] == 64
                 and files_schema["items"]["properties"]["blocks"]["minItems"] == 1)
    report.check("layout total block limit is 64", limits["max_total_blocks"] == 64)
    report.check("layout renderer reference is render_reference.py",
                 layout["renderer"]["reference"] == "som-rs/tests/react-output-v1/render_reference.py"
                 and (HERE / "render_reference.py").is_file())
    for path in fixed:
        report.check(f"layout fixed file {path} has reference bytes", (CONTRACT / "reference" / path).is_file())
    present = sorted(str(p.relative_to(CONTRACT / "reference")) for p in (CONTRACT / "reference").rglob("*")
                     if p.is_file())
    report.check("reference holds exactly the fixed files", present == sorted(fixed), str(present))


def check_catalog(report: Report) -> None:
    catalog = load(CONTRACT / "catalog" / "react-catalog.v1.json")
    paths = layout_paths()
    fixed = {e["path"] for e in load(CONTRACT / "product-layout.json")["files"] if e["kind"] == "fixed"}
    report.check("catalog DSL version is som.dsl.react.v1", catalog["dsl_version"] == "som.dsl.react.v1")
    report.check("catalog file paths equal the layout in order", [f["path"] for f in catalog["files"]] == paths)
    blocks = [b for f in catalog["files"] for b in f["blocks"]]
    report.check("every catalog block id starts with react.", all(b["id"].startswith("react.") for b in blocks))
    report.check("catalog block ids are unique", len({b["id"] for b in blocks}) == len(blocks))
    report.check("every catalog block names in-scope versions only",
                 all(b["versions"] and set(b["versions"]) <= IN_SCOPE for b in blocks))
    report.check("every fixed file is one block",
                 all(len(f["blocks"]) == 1 for f in catalog["files"] if f["path"] in fixed))
    for version in sorted(IN_SCOPE):
        files = [f for f in catalog["files"] if any(version in b["versions"] for b in f["blocks"])]
        count = max(sum(version in b["versions"] for b in f["blocks"]) for f in catalog["files"])
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
    report.check("reference tsconfig compiles JSX with react-jsx", options.get("jsx") == "react-jsx")
    check_lockfile(report, toolchain, package)
    formatter = toolchain["formatter"]
    node = toolchain["node"]
    report.check("formatter sha256 follows its rule",
                 formatter["sha256"] == h({"node": node["sha256"], "libnode": node["libnode"]["sha256"],
                                           "prettier_tree_sha256": formatter["prettier_tree_sha256"]}))
    cache = REPO / toolchain["vendored_cache"]["path"]
    if cache.is_dir():
        got = tree_sha(cache)
        report.check("vendored cache tree hash equals toolchain.json",
                     got == (toolchain["vendored_cache"]["tree_sha256"], toolchain["vendored_cache"]["tree_files"],
                             toolchain["vendored_cache"]["tree_symlinks"]), str(got))
        for entry in toolchain["packages"]["lock_packages"]:
            digest = base64.b64decode(entry["integrity"].removeprefix("sha512-")).hex()
            held = (cache / "_cacache" / "content-v2" / "sha512" / digest[:2] / digest[2:4] / digest[4:]).is_file()
            report.check(f"vendored cache holds {entry['path']} as recorded", held == entry["in_vendored_cache"])
    else:
        print(f"NOTE vendored cache {cache.relative_to(REPO)} is absent: its tree hash was not recomputed")


def check_lockfile(report: Report, toolchain: dict[str, Any], package: dict[str, Any]) -> None:
    lock = load(CONTRACT / "reference" / "package-lock.json")
    packages = toolchain["packages"]
    report.check("reference lockfile is lockfileVersion 3 for som-app",
                 lock.get("lockfileVersion") == 3 and lock.get("name") == "som-app"
                 and lock.get("version") == package["version"] and lock.get("requires") is True)
    root = lock["packages"][""]
    report.check("lockfile root equals reference/package.json",
                 root.get("name") == package["name"] and root.get("version") == package["version"]
                 and root.get("dependencies") == package["dependencies"]
                 and root.get("devDependencies") == package["devDependencies"])
    entries = [{"path": path, "name": path.rsplit("node_modules/", 1)[1], "version": entry["version"],
                "resolved": entry["resolved"], "integrity": entry["integrity"],
                **{flag: entry[flag] for flag in LOCK_FLAGS if flag in entry}}
               for path, entry in lock["packages"].items() if path != ""]
    recorded = [{k: v for k, v in e.items() if k != "in_vendored_cache"} for e in packages["lock_packages"]]
    report.check("lockfile packages equal toolchain.json lock_packages", entries == recorded)
    report.check("lock package counts equal toolchain.json",
                 len(entries) == packages["lock_package_count"]
                 and sum(e["in_vendored_cache"] for e in packages["lock_packages"])
                 == packages["lock_packages_in_vendored_cache"])
    report.check("fsevents is the only lockfile package with an install script",
                 [e["name"] for e in entries if e.get("hasInstallScript")] == ["fsevents"])
    report.check("every lockfile package resolves to registry.npmjs.org with sha512 integrity",
                 all(e["resolved"].startswith("https://registry.npmjs.org/") and e["integrity"].startswith("sha512-")
                     for e in entries))
    report.check("every lockfile package not in the vendored cache is optional",
                 all(e.get("optional") is True for e in packages["lock_packages"] if not e["in_vendored_cache"]))
    direct = {**packages["dependencies"], **packages["dev_dependencies"]}
    versions = {e["path"].removeprefix("node_modules/"): e["version"] for e in entries}
    report.check("every pinned package has its pinned version in the lockfile",
                 all(versions.get(name) == version for name, version in direct.items()))
    report.check("lockfile dev flags follow dependencies and devDependencies",
                 all(("dev" in e or "devOptional" in e) == (e["name"] in packages["dev_dependencies"])
                     for e in entries if e["path"].removeprefix("node_modules/") in direct))


def check_evidence(report: Report) -> None:
    toolchain = load(CONTRACT / "toolchain.json")
    verification = toolchain["reference_verification"]
    report.check("reference verification passed", verification["status"] == "passed")
    commands = toolchain["commands"]
    fixed = {e["path"] for e in load(CONTRACT / "product-layout.json")["files"] if e["kind"] == "fixed"}
    paths = set(layout_paths())
    evidence = load(REPO / verification["evidence"])
    files = evidence["product_files"]
    report.check("reference product has the 19 layout files", set(files) == paths, str(sorted(set(files) ^ paths)))
    report.check("reference product fixed files are the reference bytes",
                 all(files[p] == sha(CONTRACT / "reference" / p) for p in fixed),
                 str([p for p in sorted(fixed) if files.get(p) != sha(CONTRACT / "reference" / p)]))
    steps = [(g["step"], g["exit_code"]) for g in evidence["gates"]]
    report.check("reference gates exit 0 in order", steps == [(s, 0) for s in GATE_STEPS], str(steps))
    report.check("reference gate commands equal toolchain.json commands",
                 all(g["command"] == commands[g["step"].split("-")[0]] or
                     (g["step"].startswith("build") and g["command"].startswith(commands["build"].split(" --outDir")[0]))
                     for g in evidence["gates"]), str([g["command"] for g in evidence["gates"]]))
    report.check("reference builds identical, lock and cache unchanged",
                 evidence["builds_identical"] is True and evidence["lock_unchanged"] is True
                 and evidence["vendored_cache_tree_sha256_before"] == evidence["vendored_cache_tree_sha256_after"]
                 == toolchain["vendored_cache"]["tree_sha256"])
    report.check("reference evidence prettier tree equals toolchain.json",
                 evidence["prettier_tree_sha256"] == toolchain["formatter"]["prettier_tree_sha256"])
    report.check("reference product files outside node_modules unchanged by the gates",
                 evidence["product_unchanged"] is True and evidence["product_files_after"] == files)
    api = load(CONTRACT / "api-contract.json")
    conditional = [s["name"] for s in api["steps"] if "when" in s]
    report.check("API contract conditional steps are the seven with a when fact",
                 conditional == list(WHEN_FACT) and list(api["skip_reasons"]) == conditional, str(conditional))
    got = evidence["api_contract"]
    report.check("reference API contract ran every step and passed",
                 got["exit_code"] == 0 and got["processes_stopped"] is True
                 and api_steps_ok(got["steps"], got["facts"], api) is None,
                 str(api_steps_ok(got["steps"], got["facts"], api)))
    report.check("reference evidence passed", evidence["passed"] is True)
    reference_fixed = fixed_files_sha256({p: sha(CONTRACT / "reference" / p) for p in fixed})
    report.check("reference evidence fixed files hash to the reference", fixed_files_sha256(files) == reference_fixed)

    public = load(REPO / verification["public_products_evidence"])
    report.check("public products evidence passed for 8 products",
                 public["passed"] is True and len(public["cases"]) == 8
                 and all(c["passed"] is True for c in public["cases"]))
    public_dir = REPO / public["public_products"]
    for case in public["cases"]:
        label = f"public product {case['index']}"
        generated = case["generated_files"]
        report.check(f"{label} ran every gate and the API contract",
                     case["exit_codes"] == {"render": 0, "gates": 0, "api_contract": 0}
                     and case["gates"] == {s: 0 for s in GATE_STEPS}
                     and case["builds_identical"] is True and case["lock_unchanged"] is True
                     and case["product_unchanged"] is True
                     and case["vendored_cache_unchanged"] is True and case["processes_stopped"] is True
                     and set(generated) == paths - fixed, json.dumps({k: case[k] for k in ("exit_codes", "gates")}))
        report.check(f"{label} fixed files are the reference files", case["fixed_files_sha256"] == reference_fixed)
        problem = api_steps_ok(case["api_steps"], case["api_facts"], api)
        report.check(f"{label} API steps follow the skip rule", problem is None, str(problem))
        prompt_path = public_dir / case["index"] / "prompt.json"
        check_prompt(report, label, prompt_path, case, case["api_facts"])
    inventory = load(CONTRACT / "case-inventory.json")
    indexes = sorted(str(p["index"]) for p in inventory["public_products_in_scope"])
    report.check("public products evidence covers the inventory's in-scope products",
                 sorted(c["index"] for c in public["cases"]) == indexes, str(indexes))

    names = load(REPO / verification["name_cases_evidence"])
    ids = [c["id"] for c in load(CONTRACT / "target-examples.json")["name_cases"]]
    report.check("name cases evidence passed for every name case",
                 names["passed"] is True and [c["id"] for c in names["cases"]] == ids
                 and all(c["passed"] is True and c["builds_identical"] is True and c["lock_unchanged"] is True
                         and c["product_unchanged"] is True and c["vendored_cache_unchanged"] is True
                         and c["render_exit"] == 0 and c["gates_exit"] == 0
                         and c["gates"] == {s: 0 for s in GATE_STEPS} for c in names["cases"]))
    for case in names["cases"]:
        report.check(f"name case {case['id']} fixed files are the reference files",
                     case["fixed_files_sha256"] == reference_fixed and set(case["generated_files"]) == paths - fixed)
        check_prompt(report, f"name case {case['id']}", REPO / names["run"]["work_dir"] / case["id"] / "prompt.json",
                     case, None)
    controls = load(EVIDENCE / "python-controls-2026-10-22.json")
    count = len(load(CONTRACT / "negative-cases.json")["cases"]) + len(ids)
    report.check("Python controls evidence passed for every negative case and name case",
                 controls["passed"] == count and not controls["failed"] and len(controls["results"]) == count)
    report.check("Python controls evidence records a payload_sha256 for every HTTP 200 control",
                 all(len(r.get("payload_sha256") or "") == 64 for r in controls["results"]
                     if r["control_status"] == 200))
    report.check("Python controls evidence names the current case files",
                 controls["sources_sha256"] == {n: sha(CONTRACT / n) for n in ["negative-cases.json",
                                                                               "target-examples.json"]})


# The fact each conditional API step needs (api-contract.json when), as the driver and spec_facts name it.
WHEN_FACT = {"list filter": "filter", "list before": "before", "list page": "query", "list limit too large": "query",
             "patch null on a field that is not nullable": "not_nullable",
             "create missing a required field": "required", "create unique conflict": "unique"}


def fixed_files_sha256(files: dict[str, str]) -> str:
    return h({path: digest for path, digest in files.items() if path not in ("README.md", "src/schema.ts")})


def spec_facts(prompt: dict[str, Any]) -> dict[str, bool]:
    """The API driver facts for a prompt, from render_reference.spec."""
    import render_reference  # type: ignore[import-not-found]
    s = render_reference.spec(prompt)
    query = s["query"]
    return {"query": query is not None, "filter": query is not None and query["filter"] is not None,
            "before": query is not None and query["before"] is not None,
            "not_nullable": any(not f["nullable"] for f in s["fields"]),
            "required": any(f["required"] for f in s["fields"]), "unique": any(f["unique"] for f in s["fields"])}


def api_steps_ok(steps: list[dict[str, Any]], facts: dict[str, bool], api: dict[str, Any]) -> str | None:
    """None when the steps are the 20 contract steps, none failed, and exactly the inapplicable ones skipped."""
    if [s["name"] for s in steps] != [s["name"] for s in api["steps"]]:
        return f"step names {[s['name'] for s in steps]}"
    for step in steps:
        fact = WHEN_FACT.get(step["name"])
        applicable = fact is None or facts[fact] is True
        if applicable and step["ok"] is not True:
            return f"{step['name']} ok {step['ok']}"
        if not applicable and (step["ok"] is not None or step.get("skipped") != api["skip_reasons"][step["name"]]):
            return f"{step['name']} should be skipped: {step}"
    return None


# --require-prompts turns an absent .local prompt into a failure instead of a NOTE.
REQUIRE_PROMPTS = False


def check_prompt(report: Report, label: str, prompt_path: pathlib.Path, case: dict[str, Any],
                 facts: dict[str, bool] | None) -> None:
    """Checks a case against its .local prompt when that file is present; a NOTE otherwise."""
    if not prompt_path.is_file():
        if REQUIRE_PROMPTS:
            report.check(f"{label} prompt present", False, f"{prompt_path.relative_to(REPO)} is absent")
            return
        print(f"NOTE {label}: {prompt_path.relative_to(REPO)} is absent; prompt checks not run")
        return
    import render_reference  # type: ignore[import-not-found]
    prompt = load(prompt_path)
    report.check(f"{label} prompt file hash", sha(prompt_path) == case["prompt_file_sha256"])
    rendered = render_reference.render(prompt)
    report.check(f"{label} generated files equal render_reference.py",
                 case["generated_files"] == {n: hashlib.sha256(t.encode()).hexdigest() for n, t in rendered.items()})
    if facts is not None:
        report.check(f"{label} API facts follow from the prompt", facts == spec_facts(prompt),
                     f"{facts} != {spec_facts(prompt)}")


def check_contract_text(report: Report) -> None:
    """CONTRACT.md states the numbers and texts the other files hold."""
    text = (CONTRACT / "CONTRACT.md").read_text()
    invalid = load(CONTRACT / "api.schema.json")["x-target-selection"]["invalid"]
    toolchain = load(CONTRACT / "toolchain.json")
    counts = load(CONTRACT / "case-inventory.json")["counts"]
    cache = toolchain["vendored_cache"]
    packages = toolchain["packages"]
    recipes = load(CONTRACT / "acceptance-plan.json")["assemble_refusals"]["recipes"]
    negative = load(CONTRACT / "negative-cases.json")["cases"]
    names = load(CONTRACT / "target-examples.json")["name_cases"]
    steps = load(CONTRACT / "api-contract.json")["steps"]
    controls = load(EVIDENCE / "python-controls-2026-10-22.json")
    wanted = {
        "four-target cause": f'cause: "{invalid["cause"]}"',
        "four-target allowed form": f'allowed form: "{invalid["allowed_form"]}"',
        "negative case count": f"has {len(negative)} cases",
        "acceptance case count": f"has {counts['acceptance_total']} acceptance cases",
        "assemble recipes": f"A1 to A{len(recipes)}",
        "API step count": f"It has {len(steps)} steps",
        "conditional API steps": f"{sum('when' in s for s in steps)} steps have a `when` condition",
        "Python controls": f"{controls['passed']} passed, {len(controls['failed'])} failed",
        "name case count": f"{len(names)} name cases",
        "vendored cache tree": f"`{cache['tree_sha256']}` ({cache['tree_files']} files)",
        "layout file count": f"{len(layout_paths())} files",
    }
    total, cached = packages["lock_package_count"], packages["lock_packages_in_vendored_cache"]
    wanted["lock package counts"] = (f"with {total} packages. {cached} are in the vendored cache; the other "
                                     f"{total - cached} are optional")
    for name, needle in wanted.items():
        report.check(f"CONTRACT.md states the {name}", needle in text, needle)


def check_negative_cases(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    cases = load(CONTRACT / "negative-cases.json")["cases"]
    react_request = schema("som.compiler-api.v1.react-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    error = schema("som.compiler-api.v1#/$defs/compiler_error")
    scope = load(CONTRACT / "scope.json")
    selection = load(CONTRACT / "api.schema.json")["x-target-selection"]["invalid"]
    ids = [case["id"] for case in cases]
    report.check("negative case ids are unique", len(ids) == len(set(ids)))
    report.check("negative cases are RR01-RR08, RS01-RS18 and RP01-RP04",
                 [i[:4] for i in ids] == [f"RR{n:02}" for n in range(1, 9)] + [f"RS{n:02}" for n in range(1, 19)]
                 + [f"RP{n:02}" for n in range(1, 5)], str(ids))
    forms = {rule: entry["allowed_form"] for rule, entry in scope["refusals"].items()}
    report.check("refusal allowed forms are distinct", len(set(forms.values())) == len(forms))
    for rule, form in forms.items():
        report.check(f"refusal {rule} has an RS case",
                     any(c["id"].startswith("RS") and c["expected"]["allowed_form"] == form for c in cases))
    for case in cases:
        cid = case["id"]
        control = json.loads(case["python_control_body"])
        report.check(f"{cid} control has no target", "target" not in control)
        report.check(f"{cid} control is a compiler-v1 request", v1_request.is_valid(control))
        expected = case["expected"]
        report.check(f"{cid} expected diagnostic has the compiler-v1 shape",
                     error.is_valid({"status": "error", "code": expected["code"], "detail": "x",
                                     "diagnostics": [expected]}))
        if cid.startswith(("RS", "RP")) and case["form"] == "cnl":
            text = json.loads(case["body"])["source"].encode()
        else:
            text = case["body"].encode()
        span = text[expected["byte_start"]:expected["byte_end"]].decode()
        report.check(f"{cid} byte span holds span_text", span == case["span_text"], repr(span))
        if cid.startswith("RR") and cid != "RR08_JSON_TARGET_DUPLICATE":
            report.check(f"{cid} has the four-target texts",
                         expected["code"] == selection["code"] and case["http_status"] == selection["http_status"]
                         and expected["cause"] == selection["cause"]
                         and expected["allowed_form"] == selection["allowed_form"])
        if cid == "RR08_JSON_TARGET_DUPLICATE":
            continue
        body = json.loads(case["body"])
        if cid.startswith("RR"):
            report.check(f"{cid} body fails the React-target schema", not react_request.is_valid(body))
            continue
        report.check(f"{cid} body has target react", body.get("target") == "react")
        report.check(f"{cid} body passes the React-target schema", react_request.is_valid(body))
        if expected["code"] == "UNSUPPORTED_TARGET":
            report.check(f"{cid} is a scope refusal", case["http_status"] == 422 and cid.startswith("RS"))
            if body["format"] == "som.prompt-json.v1":
                got = react_profile.check(body["prompt"])
                ok = got is not None and "/prompt" + got["pointer"] == expected["prompt_pointer"] \
                    and got["cause"] == expected["cause"] and got["allowed_form"] == expected["allowed_form"]
                report.check(f"{cid} matches react_profile", ok, json.dumps(got))


def check_target_examples(report: Report, schema: Callable[[str], Draft202012Validator]) -> None:
    react_request = schema("som.compiler-api.v1.react-target#/$defs/compile_request")
    v1_request = schema("som.compiler-api.v1#/$defs/compile_request")
    data = load(CONTRACT / "target-examples.json")
    examples = data["examples"]
    report.check("target examples cover CNL and JSON with react and python",
                 {(e["id"].split("-")[-2], e["target"]) for e in examples}
                 == {("cnl", "react"), ("json", "react"), ("cnl", "python"), ("json", "python")})
    for example in examples + data["name_cases"]:
        body = json.loads(example["body"])
        report.check(f"target example {example['id']} passes the React-target schema",
                     react_request.is_valid(body) and body["target"] == example["target"])
        if example["target"] == "react" and body["format"] == "som.prompt-json.v1":
            report.check(f"target example {example['id']} is in scope", react_profile.check(body["prompt"]) is None)
        if "same_as_body" in example:
            same = json.loads(example["same_as_body"])
            report.check(f"target example {example['id']} control is a compiler-v1 request",
                         v1_request.is_valid(same) and {**same, "target": "python"} == body)
        if "python_control_body" in example:
            control = json.loads(example["python_control_body"])
            report.check(f"name case {example['id']} control is the body without target",
                         v1_request.is_valid(control) and {**control, "target": "react"} == body)


def check_profile(report: Report) -> None:
    for path in sorted(EXAMPLES.glob("*.prompt.json")):
        prompt = load(path)
        got = react_profile.check(prompt)
        if prompt["version"] in IN_SCOPE:
            report.check(f"example {path.name} is in scope", got is None, json.dumps(got))
        else:
            report.check(f"example {path.name} is refused by prompt_version",
                         got is not None and got["rule"] == "prompt_version")
    for case in load(COVERAGE_CASES / "index.json")["cases"]:
        prompt = load(COVERAGE_CASES / case["prompt"])
        got = react_profile.check(prompt)
        expected_rule = None if prompt["version"] in IN_SCOPE else "prompt_version"
        report.check(f"coverage case {case['id']} profile result",
                     (got or {}).get("rule") == expected_rule, json.dumps(got))
    scope = load(CONTRACT / "scope.json")
    report.check("reserved names are __proto__, constructor and prototype",
                 scope["identifiers"]["reserved_names"] == ["__proto__", "constructor", "prototype"])
    report.check("in-scope prompt versions are v1 to v4", set(scope["prompt_versions"]["in_scope"]) == IN_SCOPE)


def check_plan(report: Report) -> None:
    plan = load(CONTRACT / "acceptance-plan.json")
    recipes = [r["id"] for r in plan["assemble_refusals"]["recipes"]]
    report.check("assemble recipes are A1 to A16", recipes == [f"A{n}" for n in range(1, 17)], str(recipes))
    report.check("every assemble recipe has a code, a status and a pointer",
                 all(r["code"] and r["http_status"] in (400, 422) and r["prompt_pointer"].startswith("/bundle")
                     for r in plan["assemble_refusals"]["recipes"]))
    counts = load(CONTRACT / "case-inventory.json")["counts"]
    report.check("acceptance total is 129", counts["acceptance_total"] == 129 == sum(
        counts[k] for k in ["public_examples_in_scope", "version_positive_in_scope", "coverage_cases_in_scope",
                            "name_cases", "legacy_in_scope"]) + counts["public_products_in_scope"])
    for key in ["case_inventory", "negative_cases", "target_examples", "api_contract"]:
        report.check(f"acceptance plan {key} exists", (CONTRACT / plan[key]).is_file())
    for rel in plan["pre_freeze_verification"]["evidence"]:
        report.check(f"pre-freeze evidence {rel.split(':')[0]} exists", (REPO / rel.split(":")[0]).is_file())
    report.check("issue gates include the target amendments and the release",
                 {"phase1-contract", "phase1-target-amendments", "release"} <= set(plan["issue_gates"]))
    api = load(CONTRACT / "api-contract.json")
    names = [s["name"] for s in api["steps"]]
    report.check("API contract has 20 named steps", len(names) == 20 == len(set(names)), str(len(names)))
    report.check("API contract driver exists", (HERE / "api_contract.mjs").is_file()
                 and (HERE / "run_api_contract.py").is_file())


def check_builders(report: Report) -> None:
    for builder in BUILDERS:
        run = subprocess.run([sys.executable, str(HERE / builder), "--check"], capture_output=True, text=True,
                             cwd=REPO)
        report.check(f"{builder} --check", run.returncode == 0, (run.stdout + run.stderr).strip()[-300:])


def contract_files() -> list[pathlib.Path]:
    roots = [CONTRACT, HERE, EVIDENCE]
    return sorted({path for root in roots if root.is_dir() for path in root.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts})


def native_acceptance_order(receipt: dict, receipt_path: pathlib.Path) -> bool:
    """Admit only this reviewed prospective native amendment with truthful order."""
    order = receipt.get("implementation_order")
    review = receipt.get("independent_review")
    acceptance = receipt.get("acceptance")
    return (receipt.get("version") == 'som.react-output-contract-amendment.v1'
            and receipt_path.name == 'react-output-contract-amendment-003.json'
            and type(receipt.get("amendment")) is int and receipt["amendment"] == 3
            and receipt.get("previous_receipt") == 'som-rs/evidence/react-output-contract-amendment-002.json'
            and receipt.get("previous_receipt_sha256") == 'eb4e120c2a573dc06cf068841ec7b0b0f0e2349d74ae2bff5df7dcade41fe962'
            and receipt.get("implementation_already_in_progress") is True
            and receipt.get("changed_paths") == ['som-rs/tests/react-output-v1/check_contract.py', 'som-rs/tests/react-output-v1/common.py', 'som-rs/tests/react-output-v1/run_api_contract.py', 'som-rs/tests/react-output-v1/run_gates.py']
            and receipt.get("added_paths") == ['som-rs/tests/react-output-v1/linux_qa_profile.py', 'som-rs/tests/react-output-v1/test_native_api_host_controls.py']
            and type(order) is dict and order == {'version': 'som.native-acceptance-chronology.v1', 'implementation_already_existed': True, 'prior_work_relabelled_as_preimplementation': False, 'scope': 'native_tools_and_metadata_only', 'acceptance_rule': 'prospective_full_same_candidate_gates', 'historical_receipt_bytes_changed': False, 'historical_results_promoted_to_current_acceptance': False}
            and all(type(order.get(key)) is type(value) for key, value in {'version': 'som.native-acceptance-chronology.v1', 'implementation_already_existed': True, 'prior_work_relabelled_as_preimplementation': False, 'scope': 'native_tools_and_metadata_only', 'acceptance_rule': 'prospective_full_same_candidate_gates', 'historical_receipt_bytes_changed': False, 'historical_results_promoted_to_current_acceptance': False}.items())
            and type(review) is dict and review.get("final_verdict") == "APPROVED"
            and review.get("scope") == "native_policy_validator_and_exact_qa_source_maps"
            and review.get("receipt_files_sha256") == h(receipt.get("files"))
            == receipt.get("target_contract", {}).get("sha256")
            and type(acceptance) is dict and acceptance.get("status") == "not_run"
            and acceptance.get("product_acceptance_passed") is False)


def check_receipt(report: Report, receipt_path: pathlib.Path) -> None:
    receipt = load(receipt_path)
    for rel, digest in receipt["files"].items():
        path = REPO / rel
        report.check(f"receipt {rel}", path.is_file() and sha(path) == digest)
    present = {str(path.relative_to(REPO)) for path in contract_files()}
    report.check("receipt lists every contract, tool and evidence file", present == set(receipt["files"]),
                 json.dumps(sorted(present ^ set(receipt["files"]))))
    report.check("receipt target_contract hash is H(files)", receipt["target_contract"]["sha256"] == h(receipt["files"]))
    if "amendment" in receipt or receipt_path.name == 'react-output-contract-amendment-003.json':
        previous = REPO / receipt["previous_receipt"]
        report.check("amendment receipt names its previous receipt by hash",
                     previous.is_file() and sha(previous) == receipt["previous_receipt_sha256"])
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
                         and archived.is_file() and sha(archived) == change["old_sha256"]
                         and change["new_sha256"] == receipt["files"][change["path"]])
        report.check("amendment was sealed after independent review",
                     receipt["status"] == "amendment_sealed_after_independent_review"
                     and (native_acceptance_order(receipt, receipt_path)
                          if receipt.get("amendment") == 3 or receipt_path.name == 'react-output-contract-amendment-003.json'
                          else receipt["implementation_already_in_progress"] is False))
    else:
        report.check("receipt was sealed before implementation",
                     receipt["status"] == "contract_frozen_before_implementation"
                     and receipt["implementation_started_before_freeze"] is False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", action="store_true", help="print the SHA-256 of every contract file")
    parser.add_argument("--receipt", type=pathlib.Path, help="also check the files map of a receipt")
    parser.add_argument("--require-prompts", action="store_true", help="fail when a .local prompt is absent")
    args = parser.parse_args()
    global REQUIRE_PROMPTS
    REQUIRE_PROMPTS = args.require_prompts
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
    check_evidence(report)
    check_contract_text(report)
    check_negative_cases(report, schema)
    check_target_examples(report, schema)
    check_profile(report)
    check_plan(report)
    check_builders(report)
    if args.receipt:
        check_receipt(report, args.receipt)
    if args.files:
        for path in contract_files():
            print(sha(path), path.relative_to(REPO))
    for line in report.failed:
        print("FAIL", line)
    print(f"{len(report.passed)} passed, {len(report.failed)} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
