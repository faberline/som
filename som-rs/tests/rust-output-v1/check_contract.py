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
import os
import stat
import types
import subprocess
import sys
import tomllib
from typing import Any, Callable


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REPO = ROOT.parent
CONTRACT = ROOT / "contracts" / "rust-output-v1"
V1 = ROOT / "contracts" / "compiler-v1"
EVIDENCE = ROOT / "evidence" / "rust-output-v1"


def requested_qa_mode():
    selected = []
    for index, value in enumerate(sys.argv[1:], start=1):
        if value == '--qa-mode':
            if index + 1 >= len(sys.argv):
                raise ValueError('explicit QA mode value required')
            selected.append(sys.argv[index + 1])
        elif value.startswith('--qa-mode='):
            selected.append(value.split('=', 1)[1])
    if len(selected) > 1 or (selected and selected[0] not in {'historical', 'native-qa-v1'}):
        raise ValueError('one known QA mode required')
    return selected[0] if selected else 'historical'


def checked_native_qa_source():
    """Check exact raw source authority before importing local QA helpers."""
    relative = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
    path = REPO / relative
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('linked native QA authority source')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024**2:
            raise ValueError('regular bounded authority source required')
        raw = stream.read(1024**2 + 1)
    if hashlib.sha256(raw).hexdigest() != '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1':
        raise ValueError('raw native QA authority SHA differs before execution')
    def git(*arguments):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'},
            stderr=subprocess.DEVNULL, timeout=30)
    revision = git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    def source(name):
        if name.startswith('/') or '..' in name.split('/') or '\\' in name:
            raise ValueError('fixed relative QA source required')
        entry = git('ls-tree', '-z', revision, '--', name)
        if entry.count(b'\0') != 1 or not entry.endswith(b'\t' + name.encode() + b'\0') or not entry.startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('regular committed QA source required')
        file = REPO / name
        if any(item.is_symlink() for item in (file, *file.parents)):
            raise ValueError('linked QA source refused')
        with os.fdopen(os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024**2:
                raise ValueError('regular bounded single-link QA source required')
            body = stream.read(64 * 1024**2 + 1)
        if len(body) > 64 * 1024**2 or body != git('cat-file', 'blob', revision + ':' + name):
            raise ValueError('working QA source differs from exact Git bytes')
        return body
    if source(relative) != raw:
        raise ValueError('authority source changed')
    module = types.ModuleType('_som_native_qa_authority_before_checker')
    module.__file__, module.__cached__, module.__package__ = str(path), None, ''
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    paths = git('ls-tree', '-r', '--name-only', revision).decode().splitlines()
    authority = module.verify(source, 'native-qa-v1', paths, helper_revision=revision, candidate_revision=revision)
    module.install_raw_finder(source, REPO, authority)
    return authority


QA_MODE = requested_qa_mode()
NATIVE_QA_SOURCE_AUTHORITY = checked_native_qa_source() if QA_MODE == 'native-qa-v1' else None
from referencing import Registry, Resource
from jsonschema import Draft202012Validator

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
    if QA_MODE == 'native-qa-v1':
        from full_product_context import native_contract_host
        tools = native_contract_host()
        report.check('native host has exact six Rust and three PostgreSQL tools',
                     set(tools) == {'rustc', 'cargo', 'rustfmt', 'cargo-fmt', 'cargo-clippy',
                                    'clippy-driver', 'postgres', 'initdb', 'pg_ctl'})
        return
    toolchain = load(CONTRACT / "toolchain.json")
    rust_bin = pathlib.Path(toolchain["rust"]["bin_dir"])
    for name in ["rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver"]:
        path = rust_bin / name
        report.check(f"host {name} hash", path.exists() and sha(path) == toolchain["rust"][name]["sha256"])
    pg_bin = pathlib.Path(toolchain["postgresql"]["bin_dir"])
    for name in ["postgres", "initdb", "pg_ctl"]:
        path = (pg_bin / name).resolve()
        report.check(f"host {name} hash", path.exists() and sha(path) == toolchain["postgresql"][name]["sha256"])


def native_acceptance_order(receipt: dict, receipt_path: pathlib.Path) -> bool:
    """Admit only this reviewed prospective native amendment with truthful order."""
    order = receipt.get("implementation_order")
    review = receipt.get("independent_review")
    acceptance = receipt.get("acceptance")
    return (receipt.get("version") == 'som.rust-output-contract-amendment.v1'
            and receipt_path.name == 'rust-output-contract-amendment-008.json'
            and type(receipt.get("amendment")) is int and receipt["amendment"] == 8
            and receipt.get("previous_receipt") == 'som-rs/evidence/rust-output-contract-amendment-007.json'
            and receipt.get("previous_receipt_sha256") == '225b98c2bc01e79c5c6e13ad17f93adda68717498d70fa7577e65930813f9144'
            and receipt.get("implementation_already_in_progress") is True
            and receipt.get("changed_paths") == ['som-rs/tests/rust-output-v1/check_contract.py']
            and receipt.get("added_paths") == ['som-rs/contracts/rust-output-v1/native-tools-compatibility.json']
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
        report.check(f"receipt {rel}", path.exists() and sha(path) == digest)
    present = {str(path.relative_to(REPO)) for root in (CONTRACT, HERE, EVIDENCE) for path in root.rglob("*")
               if path.is_file() and "__pycache__" not in path.parts}
    report.check("receipt lists every contract, test and evidence file", present == set(receipt["files"]),
                 json.dumps(sorted(present ^ set(receipt["files"]))))
    target = hashlib.sha256(canonical(receipt["files"]).encode()).hexdigest()
    report.check("receipt target_contract hash is H(files)", receipt["target_contract"]["sha256"] == target)
    if "amendment" in receipt or receipt_path.name == 'rust-output-contract-amendment-008.json':
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
                     and (native_acceptance_order(receipt, receipt_path)
                          if receipt.get("amendment") == 8 or receipt_path.name == 'rust-output-contract-amendment-008.json'
                          else receipt["implementation_already_in_progress"] is False))
    else:
        report.check("receipt was sealed before implementation",
                     receipt["status"] == "contract_frozen_before_implementation"
                     and receipt["implementation_started_before_freeze"] is False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=pathlib.Path)
    parser.add_argument("--qa-mode", choices=("historical", "native-qa-v1"), default="historical")
    parser.add_argument("--host", action="store_true")
    args = parser.parse_args()
    report = Report()
    if args.receipt and args.receipt.name == 'rust-output-contract-amendment-008.json':
        report.check('native numbered receipt requires explicit source mode',
                     args.qa_mode == 'native-qa-v1' and NATIVE_QA_SOURCE_AUTHORITY is not None)
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
