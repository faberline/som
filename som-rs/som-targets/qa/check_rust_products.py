#!/usr/bin/env python3
"""Issue #48 and #49 checks for Rust output phase 1 on a QA port.

Gate: bundle_checks, assemble_refusals and product_gates for every v1 to v5
acceptance case (contracts/rust-output-v1/acceptance-plan.json).

- bundle_checks: the CNL and JSON requests with target rust give byte-identical
  som.dsl-bundle.v2 bundles; each bundle validates against bundle.schema.json,
  has at most 12 files and 64 blocks, and every block id starts with rust.;
  two compiles and two assembles give the same bytes; the files are exactly the
  product-layout.json file set, and Cargo.toml and Cargo.lock equal the
  reference files.
- assemble_refusals: recipes A1 to A9 give their frozen code, status and
  pointer, and the span selects the pointer target in the canonical request.
  With --refusals phase2 (the default), acceptance-plan.json phase2 applies:
  A6 uses the RS13 typed prompt and expects its compiler-v1 refusal
  (amendment 004), A8 expects 200 with the files of the renamed prompt's own compile
  and assemble, and A1, A2, A4, A5, A7 and A9 also run on the PUBLIC_v6 base.
- product_gates: rustfmt --check, cargo build, clippy with warnings denied and
  the product tests on a fresh database, with Cargo.lock unchanged; schema
  equivalence with the Python migration for the same prompt
  (behavior-normalization.json). Every product test runs and passes, none is
  ignored, and each D1 to D5 marker comment sits in a test and is followed by
  a check. The commands run the toolchain.json binaries after their sha256
  checks.
- parity: --parity takes report-parity.json from som-rs/behavior/check_behavior.py
  (issue #46). Each checked case needs a passing parity row with no strict
  D1 to D5 failure, made from the same Rust files (files_sha256). Without
  --parity, parity is reported as not run, which blocks acceptance: the script
  then exits 2 instead of 0.

Run it with a Python that has the pinned alembic, SQLAlchemy, psycopg and
jsonschema packages. Legacy prompts are named by id and hash only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any

import jsonschema
import psycopg
import referencing
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_targets as targets  # noqa: E402

CONTRACT = targets.CONTRACT
REFERENCE = CONTRACT / "reference"
TOOLCHAIN = json.loads((CONTRACT / "toolchain.json").read_text())
CASE_INVENTORY = json.loads((CONTRACT / "case-inventory.json").read_text())
LAYOUT = json.loads((CONTRACT / "product-layout.json").read_text())
SCHEMA = json.loads((CONTRACT / "bundle.schema.json").read_text())
PLAN = json.loads((CONTRACT / "acceptance-plan.json").read_text())
PROMPT_SCHEMA = json.loads((targets.SOM_RS / "contracts" / "compiler-v1" / "compiler-prompt.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, registry=referencing.Registry().with_resource(
    PROMPT_SCHEMA["$id"], referencing.Resource.from_contents(PROMPT_SCHEMA)))
# Migration records: the Python product's, the sqlx migrator's (phase 1) and the hand-written
# migrator's (phase 2, naming.json W3). Each side ignores only its own, so a prompt table with
# one of these names is still compared.
PYTHON_RECORDS = ("alembic_version",)
RUST_RECORDS = {True: ("_som_rust_migrations",), False: ("_sqlx_migrations",)}


def divergence_markers(prompt: dict) -> list[str]:
    """The D markers the product tests must carry. D1 needs a field Pydantic can coerce
    (anything but str). D2 needs a str or int field. D4 needs list query parameters,
    which v1 does not have. A v5 prompt counts the fields of both resources."""
    resources = prompt["resources"].values() if "resources" in prompt else [prompt["resource"]]
    types = {field["type"] for resource in resources for field in resource["fields"]}
    markers = ["D3", "D5"]
    if types & {"str", "int"}:
        markers.append("D2")
    if types - {"str"}:
        markers.append("D1")
    if prompt["version"] != "som.prompt.v1":
        markers.append("D4")
    return sorted(markers)


PARITY_NOT_RUN = {"status": "not_run",
                  "reason": "No --parity report from som-rs/behavior/check_behavior.py parity (issue #46)."}


def parity(path: pathlib.Path | None, rows: list[dict]) -> tuple[dict, list[str]]:
    """The parity summary and the failed parity ids; each row gets its own parity status."""
    if path is None:
        for row in rows:
            row["parity"] = {"status": "not_run"}
        return PARITY_NOT_RUN, []
    data = path.read_bytes()
    report = json.loads(data)
    summary = report["summary"]
    found = {row["id"]: row for row in report["rows"]}
    failed = []
    if summary.get("mode") != "parity" or not summary.get("complete"):
        failed.append("parity: the report is not a complete parity run")
    for row in rows:
        other = found.get(row["id"])
        problems = []
        if other is None:
            problems.append("no parity row")
        else:
            if not other.get("passed") or other.get("strict_failures"):
                problems.append("parity failed")
            if other.get("rust_files_sha256") != row.get("files_sha256"):
                problems.append("parity ran other Rust files")
        row["parity"] = {"status": "failed" if problems else "passed", **({"problems": problems} if problems else {})}
        if problems:
            failed.append(f"parity:{row['id']}")
    return {"status": "failed" if failed else "passed", "issue": 46, "report_sha256": targets.sha(data),
            "recorded_at": report.get("recorded_at"), "cases": len(rows),
            "steps": summary.get("steps"), "strict_steps": summary.get("strict_steps")}, failed


BIN_DIR = pathlib.Path(TOOLCHAIN["rust"]["bin_dir"])
if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
    import full_product_context as hosted_products
    BIN_DIR = hosted_products.rust_bin()
PINNED_BINARIES = ("rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver")


def pinned_toolchain() -> dict[str, str]:
    """The environment that runs the pinned binaries. Each must match its toolchain.json sha256."""
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        return hosted_products.rust_environment()
    for name in PINNED_BINARIES:
        path = BIN_DIR / name
        if targets.sha(path.read_bytes()) != TOOLCHAIN["rust"][name]["sha256"]:
            raise SystemExit(f"{path} does not match the toolchain.json sha256")
    return {"PATH": f"{BIN_DIR}:{os.environ['PATH']}", "RUSTC": str(BIN_DIR / "rustc"),
            "CARGO": str(BIN_DIR / "cargo"), "RUSTFMT": str(BIN_DIR / "rustfmt"),
            "CARGO_NET_OFFLINE": "true"}


def compile_body(case: dict, form: str) -> str:
    if form == "cnl":
        return targets.dump({"format": "som.cnl.v1", "source": case["cnl"], "target": "rust"})
    return targets.dump({"format": "som.prompt-json.v1", "prompt": case["prompt"], "target": "rust"})


def acceptance_cases() -> list[dict]:
    """Every v1 to v5 case the frozen Rust profile admits."""
    return [case for case in targets.sources()
            if not case.get("rejection") and targets.rust_profile.check(case["prompt"]) is None]


# ---------------------------------------------------------------- bundle checks

def bundle_checks(qa: str, case: dict) -> tuple[list[str], dict | None, dict | None]:
    problems: list[str] = []
    replies = []
    forms = ["json"] + (["cnl"] if case["cnl"] is not None else [])
    for form in forms:
        for _ in range(2):
            replies.append((form,) + targets.post(qa, compile_body(case, form)))
    first = replies[0]
    if first[1] != 200:
        return [f"compile {first[0]} gave {first[1]}"], None, None
    # The reply is {"status":"ok","bundle":...}, so equal reply bytes are equal bundle bytes.
    for form, status, raw in replies[1:]:
        if (status, raw) != (200, first[2]):
            problems.append(f"a {form} compile differs in bytes from the first JSON compile")
    bundle = json.loads(first[2])["bundle"]
    try:
        VALIDATOR.validate(bundle)
    except jsonschema.ValidationError as error:
        problems.append(f"bundle schema: {error.message[:200]}")
    files = bundle["dsl"]["files"]
    blocks = [block for file in files for block in file["blocks"]]
    if len(files) > 12 or len(blocks) > 64:
        problems.append(f"{len(files)} files and {len(blocks)} blocks")
    if any(not block["id"].startswith("rust.") for block in blocks):
        problems.append("a block id does not start with rust.")
    body = targets.dump({"bundle": bundle})
    first_files = targets.post(qa, body, "/v1/assemble")
    second_files = targets.post(qa, body, "/v1/assemble")
    if first_files != second_files:
        problems.append("two assembles differ in bytes")
    if first_files[0] != 200:
        return problems + [f"assemble gave {first_files[0]}: {first_files[1][:300]!r}"], bundle, None
    output = json.loads(first_files[1])["files"]
    expected = {row["path"] for row in LAYOUT["files"] if "<" not in row["path"]}
    migrations = [path for path in output if path.startswith("migrations/")]
    revision = case["prompt"]["migration"]["revision"]
    if set(output) - set(migrations) != expected or migrations != [f"migrations/0001_{revision}.sql"]:
        problems.append(f"file set {sorted(output)}")
    for name in ("Cargo.toml", "Cargo.lock"):
        if output.get(name, "").encode() != (REFERENCE / name).read_bytes():
            problems.append(f"{name} differs from the reference")
    return problems, bundle, output


# ---------------------------------------------------------------- assemble refusals

def h(value: Any) -> str:
    """sha256 of som.canonical-json.v1 bytes. The recipe values have no floats."""
    return targets.sha(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())


def rehash(bundle: dict) -> dict:
    """Recompute the three hashes with the frozen formulas, as the recipes require."""
    bundle = {key: value for key, value in bundle.items() if key != "hashes"}
    bundle["hashes"] = {"prompt_sha256": h(bundle["prompt"]), "dsl_sha256": h(bundle["dsl"]),
                        "payload_sha256": h(bundle)}
    return bundle


def rename_field(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: rename_field(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [rename_field(item, old, new) for item in value]
    return new if value == old else value


RUST_ASSETS = ("catalog", "block_library", "backend", "formatter")


def python_compile(qa: str, prompt: dict) -> dict:
    return json.loads(targets.post(qa, targets.dump({"format": "som.prompt-json.v1", "prompt": prompt}))[1])["bundle"]


def with_shared(bundle: dict, prompt: dict, python: dict) -> dict:
    """The bundle with another prompt and that prompt's shared compiler-v1 descriptors, keeping
    the Rust descriptors and the Rust DSL, rehashed."""
    bundle = copy.deepcopy(bundle)
    bundle["prompt"] = prompt
    for key in python["assets"]:
        if key not in RUST_ASSETS:
            bundle["assets"][key] = python["assets"][key]
    return rehash(bundle)


def base_recipes(base: dict, python: dict) -> dict[str, dict]:
    """A1, A2, A4, A5, A7 and A9 on a Rust bundle, with python the Python bundle of its prompt."""
    out: dict[str, dict] = {}
    a1 = copy.deepcopy(base)
    a1["assets"]["formatter"]["sha256"] = targets.sha(b"a different rustfmt binary")
    out["A1"] = rehash(a1)
    a2 = copy.deepcopy(base)
    for key in RUST_ASSETS:
        a2["assets"][key] = python["assets"][key]
    out["A2"] = rehash(a2)
    a4 = copy.deepcopy(base)
    a4["target"] = "python"
    out["A4"] = rehash(a4)
    a5 = copy.deepcopy(base)
    a5["dsl"]["files"][0]["blocks"][0]["id"] = "orm.base"
    out["A5"] = rehash(a5)
    a7 = copy.deepcopy(base)
    digest = a7["hashes"]["payload_sha256"]
    a7["hashes"]["payload_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
    out["A7"] = a7
    # README.md copies are added until there are 13 files, one more than any layout allows.
    a9 = copy.deepcopy(base)
    while len(a9["dsl"]["files"]) < 13:
        a9["dsl"]["files"].append({**copy.deepcopy(a9["dsl"]["files"][-1]), "path": "README.md"})
    out["A9"] = rehash(a9)
    return out


def renamed(base: dict) -> tuple[dict, dict]:
    """The A8 bundle and the prompt it renames: the first field becomes Title."""
    old = base["prompt"]["resource"]["fields"][0]["name"]
    a8 = copy.deepcopy(base)
    a8["prompt"] = rename_field(a8["prompt"], old, "Title")
    a8["dsl"] = rename_field(a8["dsl"], old, "Title")
    return rehash(a8), a8["prompt"]


def recipes(qa: str, base: dict, v5_base: dict) -> dict[str, dict]:
    """Phase 1: each recipe's request bundle, built from the example-v1 Rust bundle, and for A6
    from the example-v5 Rust bundle, as the plan says. A3 is rehashed like the others."""
    v1 = json.loads((targets.EXAMPLES / "v1.prompt.json").read_text())
    v6 = json.loads((targets.EXAMPLES / "v6.prompt.json").read_text())
    python = python_compile(qa, v1)
    out = base_recipes(base, python)
    out["A3"] = rehash({**python, "target": "rust"})
    out["A6"] = with_shared(v5_base, v6, python_compile(qa, v6))
    out["A8"] = renamed(base)[0]
    return out


def refusal_row(qa: str, label: str, bundle: dict, code: str, http_status: int, pointer: str,
                base: str | None = None) -> dict:
    body = targets.dump({"bundle": bundle})
    status, raw = targets.post(qa, body, "/v1/assemble")
    reply = json.loads(raw)
    row: dict[str, Any] = {"id": label, **({"base": base} if base else {}), "status": status,
                           "code": reply.get("code")}
    problems = []
    if status != http_status:
        problems.append(f"status {status} != {http_status}")
    diagnostics = reply.get("diagnostics", [])
    if reply.get("code") != code or len(diagnostics) != 1:
        problems.append(f"code {reply.get('code')} with {len(diagnostics)} diagnostics")
    else:
        diagnostic = diagnostics[0]
        row["prompt_pointer"] = diagnostic.get("prompt_pointer")
        if diagnostic.get("prompt_pointer") != pointer:
            problems.append(f"pointer {diagnostic.get('prompt_pointer')}")
        span = body.encode()[diagnostic["byte_start"]:diagnostic["byte_end"]].decode()
        if span != targets.dump(targets.resolve(json.loads(body), pointer)):
            problems.append("span is not the pointer target")
    return {**row, "passed": not problems, "problems": problems}


def assemble_refusals(qa: str, base: dict, v5_base: dict) -> list[dict]:
    """The phase 1 plan's recipes A1 to A9 on their phase 1 bases."""
    built = recipes(qa, base, v5_base)
    return [refusal_row(qa, recipe["id"], built[recipe["id"]], recipe["code"], recipe["http_status"],
                        recipe["prompt_pointer"]) for recipe in PLAN["assemble_refusals"]["recipes"]]


def phase2_refusals(qa: str, base: dict) -> list[dict]:
    """acceptance-plan.json phase2.assemble_refusals: the phase 1 recipes on the example-v1 base
    except A6 (A6_amendment_004) and A8, which phase 2 replaces, and A1, A2, A4, A5, A7 and A9
    again on the PUBLIC_v6 Rust bundle as base with the same code, status and pointer."""
    frozen = {recipe["id"]: recipe for recipe in PLAN["assemble_refusals"]["recipes"]}
    v1 = json.loads((targets.EXAMPLES / "v1.prompt.json").read_text())
    v6 = json.loads((targets.EXAMPLES / "v6.prompt.json").read_text())
    python = python_compile(qa, v1)
    built = base_recipes(base, python)
    built["A3"] = rehash({**python, "target": "rust"})
    rows = [refusal_row(qa, key, built[key], frozen[key]["code"], frozen[key]["http_status"],
                        frozen[key]["prompt_pointer"], "example-v1") for key in sorted(built)]

    # A6 (amendment 004): the RS13 typed prompt with the shared descriptors of the example-v3
    # Python compile. compiler-v1 refuses it for every target, so assemble gives the RS13 code
    # and status at the prompt pointer under /bundle.
    rs13 = next(case for case in json.loads((CONTRACT / "negative-cases.json").read_text())["cases"]
                if case["id"].startswith("RS13"))
    v3 = json.loads((targets.EXAMPLES / "v3.prompt.json").read_text())
    rows.append(refusal_row(qa, "A6", with_shared(base, json.loads(rs13["body"])["prompt"], python_compile(qa, v3)),
                            rs13["expected"]["code"], rs13["http_status"],
                            "/bundle" + rs13["expected"]["prompt_pointer"], "example-v1"))

    # A8: 200, and the files equal the compile and assemble of the renamed prompt with target rust.
    a8, prompt = renamed(base)
    status, raw = targets.post(qa, targets.dump({"bundle": a8}), "/v1/assemble")
    problems = [] if status == 200 else [f"status {status}"]
    a8_row: dict[str, Any] = {"id": "A8", "base": "example-v1", "status": status}
    if status == 200:
        a8_row["files_sha256"] = targets.sha(targets.dump(json.loads(raw)["files"]).encode())
    compiled = targets.post(qa, targets.dump({"format": "som.prompt-json.v1", "prompt": prompt, "target": "rust"}))
    if compiled[0] != 200:
        problems.append(f"compile of the renamed prompt gave {compiled[0]}")
    elif status == 200:
        expected = targets.post(qa, targets.dump({"bundle": json.loads(compiled[1])["bundle"]}), "/v1/assemble")
        if expected[0] == 200:
            a8_row["renamed_files_sha256"] = targets.sha(targets.dump(json.loads(expected[1])["files"]).encode())
        if expected != (status, raw):
            problems.append("the files differ from the compile and assemble of the renamed prompt")
    rows.append({**a8_row, "passed": not problems, "problems": problems})

    v6_base = json.loads(targets.post(qa, targets.dump(
        {"format": "som.prompt-json.v1", "prompt": v6, "target": "rust"}))[1])["bundle"]
    for key, bundle in sorted(base_recipes(v6_base, python_compile(qa, v6)).items()):
        rows.append(refusal_row(qa, f"{key}_PUBLIC_v6", bundle, frozen[key]["code"], frozen[key]["http_status"],
                                frozen[key]["prompt_pointer"], "PUBLIC_v6"))
    return rows


# ---------------------------------------------------------------- product gates

def run(command: list[str], cwd: pathlib.Path, env: dict, timeout: int = 1800) -> tuple[int, str]:
    """The exit code and the whole output."""
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        return hosted_products.context().capture(command, cwd, env, timeout)
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stdout + done.stderr


TEST_RESULT = re.compile(r"test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                         r"(\d+) measured; (\d+) filtered out")


def test_count(root: pathlib.Path, output: str) -> tuple[int, str]:
    """Every test attribute in the product ran and passed, with none ignored or filtered."""
    sources = [path.read_text() for path in root.rglob("*.rs")]
    expected = sum(len(re.findall(r"#\[(?:tokio::)?test\]", text)) for text in sources)
    if any("#[ignore" in text for text in sources):
        return 1, "a product test is ignored"
    results = [tuple(map(int, row[1:])) for row in TEST_RESULT.findall(output)]
    passed = sum(row[0] for row in results)
    others = sum(sum(row[1:]) for row in results)
    if not expected or passed != expected or others:
        return 1, f"{passed} of {expected} tests passed, {others} failed, ignored, measured or filtered"
    return 0, ""


def marker_problems(tests: str, markers: list[str]) -> list[str]:
    """Each marker must be a comment inside a test function, followed by a check before the
    next comment. The refused helper must itself assert."""
    lines = tests.splitlines()
    helper = re.search(r"async fn refused\(.*?\n\}", tests, re.S)
    problems = [] if helper and "assert" in helper.group(0) else ["the refused helper does not assert"]
    for marker in markers:
        found = False
        for index, line in enumerate(lines):
            if not re.search(rf"^\s*//[^\n]*\b{marker}\b", line):
                continue
            start = max(i for i in range(index + 1) if re.match(r"(async )?fn ", lines[i]) or i == 0)
            in_test = start > 0 and lines[start - 1].strip() in ("#[tokio::test]", "#[test]")
            following = []
            for later in lines[index + 1:]:
                if later.lstrip().startswith("//") or later.startswith("}"):
                    break
                following.append(later)
            if in_test and re.search(r"\bassert\w*!|\brefused\(", "\n".join(following)):
                found = True
                break
        if not found:
            problems.append(f"{marker} has no check in a test")
    return problems


def fresh_database(pg: str, name: str) -> str:
    with psycopg.connect(f"postgresql://postgres@{pg}/postgres", autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    return f"postgresql://postgres@{pg}/{name}"


def write(root: pathlib.Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def schema(url: str, excluded: tuple[str, ...]) -> dict:
    """The compared parts of schema public (behavior-normalization.json schema_equivalence)."""
    out: dict[str, Any] = {}
    with psycopg.connect(url) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE' ORDER BY table_name") if row[0] not in excluded]
        for table in tables:
            columns = []
            for name, data_type, length, nullable, default in connection.execute(
                    "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s "
                    "ORDER BY ordinal_position", (table,)):
                if default and default.startswith("nextval("):
                    default = f"nextval('{table}_{name}_seq'::regclass)"
                columns.append([name, data_type, length, nullable, default])
            constraints = connection.execute(
                "SELECT c.contype, ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i) "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n ORDER BY k.i), "
                "c.confrelid::regclass::text, ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, i) "
                "JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n ORDER BY k.i), c.confdeltype "
                "FROM pg_constraint c WHERE c.conrelid = %s::regclass", (f'public."{table}"',)).fetchall()
            indexes = connection.execute(
                "SELECT i.indisunique, ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(n, p) "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n ORDER BY k.p) "
                "FROM pg_index i WHERE i.indrelid = %s::regclass", (f'public."{table}"',)).fetchall()
            out[table] = {
                "columns": columns,
                "primary_key": [list(row[1]) for row in constraints if row[0] == "p"],
                "unique": sorted(sorted(row[1]) for row in constraints if row[0] == "u"),
                "foreign_keys": sorted([list(row[1]), row[2], list(row[3]), row[4]]
                                       for row in constraints if row[0] == "f"),
                "indexes": sorted([list(row[1]), row[0]] for row in indexes),
            }
    return out


def python_schema(qa: str, case: dict, pg: str, name: str) -> dict:
    """Apply the Python product's Alembic revision to a fresh database and read its schema."""
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT") and hosted_products.recorded_transport():
        files = hosted_products.recorded_files(case["prompt"], "python")
    else:
        status, raw = targets.post(qa, targets.dump({"format": "som.prompt-json.v1", "prompt": case["prompt"]}))
        files_status, files_raw = targets.post(qa, targets.dump({"bundle": json.loads(raw)["bundle"]}), "/v1/assemble")
        if status != 200 or files_status != 200:
            raise RuntimeError(f"Python compile {status}, assemble {files_status}")
        files = json.loads(files_raw)["files"]
    revisions = [path for path in files if path.startswith("alembic/versions/")]
    if len(revisions) != 1:
        raise RuntimeError(f"{len(revisions)} Python revisions")
    spec = importlib.util.spec_from_loader("som_revision", loader=None)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    url = fresh_database(pg, name)
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                exec(compile(files[revisions[0]], revisions[0], "exec"), module.__dict__)  # noqa: S102
                module.upgrade()
    finally:
        engine.dispose()
    return schema(url, PYTHON_RECORDS)


def product_gates(qa: str, case: dict, files: dict, work: pathlib.Path, pg: str, target_dir: str) -> dict:
    key = re.sub(r"[^a-z0-9]+", "_", case["id"].lower())
    stamp = time.strftime("%H%M%S") + f"{time.time_ns() % 1000000:06d}"
    root = work / key
    write(root, files)
    env = {**os.environ, **pinned_toolchain(), "CARGO_TARGET_DIR": target_dir}
    gates: dict[str, Any] = {}
    lock = (root / "Cargo.lock").read_bytes()
    rs = sorted(str(p.relative_to(root)) for p in root.rglob("*.rs"))
    cargo = str(BIN_DIR / "cargo")
    # --config-path /dev/null keeps rustfmt.toml files above the work directory out, as in the service.
    gates["rustfmt_check"] = run([str(BIN_DIR / "rustfmt"), "--edition", "2024", "--config-path", "/dev/null",
                                  "--check", *rs], root, env)
    gates["cargo_build"] = run([cargo, "build", "--locked", "--offline", "-q"], root, env)
    gates["cargo_clippy"] = run([cargo, "clippy", "--locked", "--offline", "-q", "--all-targets",
                                 "--", "-D", "warnings"], root, env)
    rust_db = f"r48_{key}_{stamp}"
    url = fresh_database(pg, rust_db)
    gates["cargo_test"] = run([cargo, "test", "--locked", "--offline", "-q", "--", "--test-threads=1"],
                              root, {**env, "TEST_DATABASE_URL": url})
    tested = [int(row[1]) for row in TEST_RESULT.findall(gates["cargo_test"][1])]
    gates["test_count"] = test_count(root, gates["cargo_test"][1])
    gates["lock_unchanged"] = (0 if (root / "Cargo.lock").read_bytes() == lock else 1, "")
    # A comment line names each divergence it checks, as in "// D1 and D2: ...".
    missing = marker_problems((root / "tests" / "api.rs").read_text(), divergence_markers(case["prompt"]))
    gates["divergences_d1_d5"] = (1 if missing else 0, "; ".join(missing))
    try:
        rust = schema(url, RUST_RECORDS["_som_rust_migrations" in files["src/lib.rs"]])
        python = python_schema(qa, case, pg, f"p48_{key}_{stamp}")
        equal = rust == python
        gates["schema_equivalence"] = (0 if equal else 1,
                                       "" if equal else json.dumps({"rust": rust, "python": python})[:2500])
    except Exception as error:  # noqa: BLE001
        gates["schema_equivalence"] = (1, f"{type(error).__name__}: {error}"[:1000])
    passed = all(code == 0 for code, _ in gates.values())
    return {"passed": passed, "tests_passed": sum(tested),
            "gates": {name: {"passed": code == 0, **({"log": log[-3000:]} if code else {})}
                      for name, (code, log) in gates.items()}}


# ---------------------------------------------------------------- main

def check_case(qa: str, case: dict, work: pathlib.Path, pg: str, target_dir: str, gates: bool) -> dict:
    row: dict[str, Any] = {"id": case["id"], "group": case["group"], "version": case["prompt"]["version"],
                           **{key: case[key] for key in ("prompt_sha256", "prompt_file_sha256") if key in case}}
    problems, bundle, files = bundle_checks(qa, case)
    row["bundle_checks"] = {"passed": not problems, "problems": problems}
    if bundle is not None:
        row["bundle_payload_sha256"] = bundle["hashes"]["payload_sha256"]
        row["blocks"] = sum(len(file["blocks"]) for file in bundle["dsl"]["files"])
    if files is not None:
        row["files_sha256"] = targets.sha(targets.dump(files).encode())
        if gates:
            row["product_gates"] = product_gates(qa, case, files, work, pg, target_dir)
    row["passed"] = not problems and files is not None and (not gates or row["product_gates"]["passed"])
    print(f"{case['id']}: {'ok' if row['passed'] else 'FAIL'}", file=sys.stderr, flush=True)
    return row


def input_source_key(path: pathlib.Path) -> str:
    if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
        ctx = hosted_products.context()
        if ctx.qa_mode == 'native-qa-v1' and 'typescript_regression_inputs' in ctx.data:
            bindings = ctx.cache.get('typescript_replay_input_bindings', ctx.data['typescript_regression_inputs'])
            inventory = pathlib.Path(bindings['roots']['legacy_run']) / 'inventory-003.json'
            if path.absolute() == inventory.absolute():
                profile = json.loads(ctx.sources.read('som-rs/scripts/hosted_readiness/inputs/typescript-regression-inputs-v1.json'))
                raw = path.read_bytes(); info = path.stat()
                expected = profile['roles']['legacy_run']['files']['inventory-003.json']
                if {'sha256': targets.sha(raw), 'size': len(raw), 'mode': info.st_mode & 0o7777} != expected:
                    raise ValueError('explicit legacy inventory differs from its current source profile')
                return 'typescript_regression_inputs/legacy_run/inventory-003.json'
    return path.relative_to(targets.REPO).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", required=True, help="QA compiler base URL")
    parser.add_argument("--pg", required=True, help="host:port of the QA PostgreSQL 17 cluster, user postgres")
    parser.add_argument("--work", required=True, type=pathlib.Path, help="new directory for the products")
    parser.add_argument("--target-dir", required=True, help="shared CARGO_TARGET_DIR")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--only", nargs="*", help="case ids to check")
    parser.add_argument("--no-gates", action="store_true", help="bundle checks and refusals only")
    parser.add_argument("--refusals", choices=("phase1", "phase2"), default="phase2",
                        help="phase2 replaces A6 and A8 and adds the PUBLIC_v6 base (acceptance-plan.json phase2)")
    parser.add_argument("--parity", type=pathlib.Path, help="report-parity.json of som-rs/behavior/check_behavior.py")
    args = parser.parse_args()
    if str(args.qa).rstrip("/").endswith((":8765", ":18765")):
        parser.error("QA checks never use ports 8765 or 18765")

    cases = acceptance_cases()
    if len(cases) != CASE_INVENTORY["counts"]["acceptance_total"]:
        parser.error(f"{len(cases)} cases differ from the frozen acceptance_total")
    unknown = sorted(set(args.only or []) - {case["id"] for case in cases})
    if unknown:
        parser.error(f"unknown case ids {unknown}")
    pinned_toolchain()
    selected = [case for case in cases if not args.only or case["id"] in args.only]
    args.work.mkdir(parents=True, exist_ok=False)
    base, v5_base = (json.loads(targets.post(args.qa, compile_body(
        next(case for case in cases if case["id"] == name), "json"))[1])["bundle"]
        for name in ("example-v1", "example-v5"))
    refusals = phase2_refusals(args.qa, base) if args.refusals == "phase2" else assemble_refusals(args.qa, base, v5_base)
    # cargo serializes on the shared target directory, so products run one at a time.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        rows = list(pool.map(lambda case: check_case(args.qa, case, args.work, args.pg, args.target_dir,
                                                     not args.no_gates), selected))
    by_version: dict[str, int] = {}
    for case in cases:
        by_version[case["prompt"]["version"]] = by_version.get(case["prompt"]["version"], 0) + 1
    failed = [r["id"] for r in refusals if not r["passed"]] + [r["id"] for r in rows if not r["passed"]]
    parity_summary, parity_failed = parity(args.parity, rows)
    failed += parity_failed
    blocked = [] if args.parity else ["behavior parity: not run, pass --parity with a check_behavior.py report"]
    report = {
        "version": "som.rust-output-product-checks.v1",
        "issue": 49,
        "qa": args.qa,
        "refusal_plan": args.refusals,
        "inputs": {input_source_key(p): targets.sha(p.read_bytes()) for p in
                   [CONTRACT / "acceptance-plan.json", CONTRACT / "case-inventory.json",
                    CONTRACT / "bundle.schema.json", CONTRACT / "product-layout.json",
                    CONTRACT / "behavior-normalization.json", CONTRACT / "scope.json",
                    CONTRACT / "toolchain.json",
                    targets.SOM_RS / "evidence" / "rust-output-contract-amendment-003.json",
                    targets.SOM_RS / "evidence" / "rust-output-contract-amendment-004.json",
                    targets.SOM_RS / "evidence" / "rust-output-contract-amendment-005.json",
                    targets.SOM_RS / "evidence" / "rust-output-contract-amendment-006.json",
                    REFERENCE / "Cargo.toml", REFERENCE / "Cargo.lock",
                    targets.TESTS / "rust_profile.py", targets.TESTS / "rust_profile_phase2.py",
                    CONTRACT / "negative-cases.json", targets.FIXTURES, targets.COVERAGE,
                    targets.LEGACY / "inventory-003.json"]},
        "summary": {
            "acceptance_cases": {"total": len(cases), "by_version": dict(sorted(by_version.items())),
                                 "checked": len(rows),
                                 "frozen_acceptance_total": CASE_INVENTORY["counts"]["acceptance_total"]},
            "bundle_checks": {"passed": sum(r["bundle_checks"]["passed"] for r in rows), "total": len(rows)},
            "assemble_refusals": {"passed": sum(r["passed"] for r in refusals), "total": len(refusals)},
            "product_gates": {"passed": sum(r.get("product_gates", {}).get("passed", False) for r in rows),
                              "total": 0 if args.no_gates else len(rows)},
            "distinct_products": len({r.get("files_sha256") for r in rows} - {None}),
            "parity": parity_summary,
            "failed": failed,
            "blocked": blocked,
        },
        "assemble_refusals": refusals,
        "cases": rows,
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    # A failure exits 1. Without one, an unrun parity suite still blocks acceptance: exit 2.
    return 1 if failed else 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
