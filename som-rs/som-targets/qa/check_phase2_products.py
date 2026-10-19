#!/usr/bin/env python3
"""Issue #55 checks for Rust output phase 2 on a QA port (acceptance-plan.json phase2).

usage (under som-rs/runtime/acceptance-m3-venv/bin/python, with CARGO_HOME the vendored union):
  check_phase2_products.py run --qa URL --out DIR [--workers N] [--only ID ...] [--resume]
  check_phase2_products.py summarize --out DIR --python-run DIR
  check_phase2_products.py phase1 --qa URL --baseline DIR --record FILE --port N --out FILE

run checks every phase 2 case of som-rs/behavior/phase2/check_phase2.py, and its DIR is also a
check_phase2.py --language rust run directory: the cases/ rows are written by check_phase2's own
fixture_case and suite_case, so check_phase2.py summarize --out DIR reads them.

- bundle_checks: the typed JSON request with target rust, and the CNL request where the case has one
  (public-products-003b compile-cnl.request.json, the CNL RS products' compile.request.raw), are each
  sent twice and every reply is byte-identical; the bundle validates against bundle.schema.json, has
  at most 12 files and 64 blocks per file, every block id starts with rust.; two assembles give the
  same bytes; the files are exactly the product-layout.json phase2 shape the prompt selects, and
  Cargo.toml and Cargo.lock equal the reference files of that shape. A case whose
  phase2/case-inventory.json result is a refusal (RS12, RS13) passes when every reply is a 422
  UNSUPPORTED_TARGET; any other non-200 compile fails.
- product_gates, capability and flow shapes: the case runs on check_phase2.fixture_case with
  phase2_host.RustProduct replaced by GatedRustProduct. Before the checker's first build it runs
  rustfmt --check, cargo build and cargo clippy -D warnings; after it, som_app migrate upgrade on a
  fresh database next to the Python product's own migration on another (uv sync, then its migration
  command), and compares the PostgreSQL schema (check_rust_products.schema, ignoring
  _som_rust_migrations and alembic_version) or the MongoDB collections and indexes (keys in order and
  uniqueness, names not compared). The checker's own cargo test runs, on its fresh database and with
  its identity, go through the tee subcommand, so each run's output is kept: every one exits 0 and
  check_rust_products.test_count holds. Cargo.lock must be unchanged after the checker.
- product_gates, resource shape: check_rust_products.product_gates (phase 1 gates on the QA
  PostgreSQL cluster of check_phase2's worker 0), then check_phase2.suite_case.
- behavior: the check_phase2 row passes, and its rust.files_sha256 and rust.bundle_sha256 equal
  the bundle_checks ones.
- python (summarize): --python-run is the behavior.json python_run directory; its summary.json
  sha256 equals the recorded one, and each case passed there or is a python_defects or
  phase2_refusals entry of phase2/behavior.json.

phase1 starts the accepted phase 1 candidate --baseline (DIR/som serve --assets DIR/assets) on
--port, after its sha256 matches the candidate_before of the record's header.json, and for every
phase 1 acceptance case compiles the typed JSON and CNL requests on both servers. The bundles may
differ only in the som.rust-* asset descriptors and hashes.payload_sha256; the baseline payload and
both files hashes equal the rust-output-050 record (--record, its rust-products.json); the
assembled files are byte-identical. The baseline server is stopped at the end.

Legacy prompts are named by id and hash only.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys


def tee(argv: list[str]) -> int:
    """Run the command after --, keep its output in the log, and pass both streams and the code on."""
    log, rest = pathlib.Path(argv[0]), argv[argv.index("--") + 1:]
    done = subprocess.run(rest, capture_output=True, text=True, check=False)
    log.write_text(json.dumps({"command": rest, "returncode": done.returncode, "stdout": done.stdout,
                               "stderr": done.stderr}) + "\n")
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


if __name__ == "__main__" and sys.argv[1:2] == ["tee"]:
    raise SystemExit(tee(sys.argv[2:]))

import argparse  # noqa: E402
import datetime  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import urllib.request  # noqa: E402
import uuid  # noqa: E402
from typing import Any  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "behavior" / "phase2"))
import check_phase2 as phase2  # noqa: E402
import check_rust_products as products  # noqa: E402
import check_targets as targets  # noqa: E402
import jsonschema  # noqa: E402

CONTRACT = targets.CONTRACT
SHAPES = products.LAYOUT["phase2"]["shapes"]
BEHAVIOR = json.loads((CONTRACT / "phase2" / "behavior.json").read_text())
INVENTORY = {row["id"]: row["phase2_expected"] for row in phase2.read(phase2.INVENTORY)["rows"]}
RUST_RECORDS, PYTHON_RECORDS = ("_som_rust_migrations",), ("alembic_version",)
FORBIDDEN = (":8765", ":18765")


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- bundle checks

def shape(prompt: dict) -> str:
    """The product-layout.json phase2 shape the prompt selects."""
    caps = prompt.get("capabilities")
    if prompt["version"] == "som.prompt.v7":
        return "flow_" + caps["database"]
    if prompt["version"] == "som.prompt.v6" or caps:
        return "capability_" + caps["database"]
    return "resource"


def layout(prompt: dict) -> tuple[set[str], pathlib.Path]:
    name = shape(prompt)
    revision = (prompt.get("migration") or {}).get("revision", "")
    files = {path.replace("<revision>", revision) for path in SHAPES[name]["files"]}
    return files, CONTRACT / ("reference" if name == "resource" else "reference-phase2")


def cnl_request(case: dict) -> dict | None:
    """The case's CNL compile request, or None for a case with the typed JSON form alone."""
    if case["family"] == "public":
        path = phase2.PUBLIC / f"{case['position']:03d}" / "compile-cnl.request.json"
    elif case["family"] == "rs" and case["group"] == "cnl":
        path = phase2.RS / case["id"] / "compile.request.raw"
    else:
        return None
    if not path.exists():
        return None
    request = json.loads(path.read_text())
    if request.get("format") != "som.cnl.v1":
        raise RuntimeError(f"{path.name} is not a som.cnl.v1 request")
    return {"format": request["format"], "source": request["source"], "target": "rust"}


def bundle_checks(qa: str, case: dict, prompt: dict) -> tuple[dict, dict | None]:
    problems: list[str] = []
    requests = [("json", phase2.dump({"format": "som.prompt-json.v1", "target": "rust", "prompt": prompt}))]
    cnl = cnl_request(case)
    if cnl is not None:
        requests.append(("cnl", phase2.dump(cnl)))
    replies = [(form,) + phase2.post(qa, body, "/v1/compile") for form, body in requests for _ in range(2)]
    record: dict[str, Any] = {"forms": [form for form, _ in requests], "shape": shape(prompt),
                              "compile_status": [status for _, status, _ in replies],
                              "compile_sha256": [hashlib.sha256(raw).hexdigest() for _, _, raw in replies]}
    if INVENTORY[case["id"]]["result"] == "refusal":
        for index in range(0, len(replies), 2):
            if replies[index][1:] != replies[index + 1][1:]:
                problems.append(f"two {replies[index][0]} compiles differ in bytes")
        codes = [json.loads(raw).get("code") for _, _, raw in replies]
        if any(status != 422 for _, status, _ in replies) or set(codes) != {"UNSUPPORTED_TARGET"}:
            problems.append(f"refusal compiles gave {record['compile_status']} {sorted(set(map(str, codes)))}")
        record.update(passed=not problems, problems=problems, refusal=True)
        return record, None
    first = replies[0]
    if first[1] != 200:
        code = json.loads(first[2]).get("code") if first[2][:1] == b"{" else None
        record.update(passed=False, problems=[f"json compile gave {first[1]} {code}"])
        return record, None
    for form, status, raw in replies[1:]:
        if (status, raw) != (200, first[2]):
            problems.append(f"a {form} compile differs in bytes from the first JSON compile")
    bundle = json.loads(first[2])["bundle"]
    try:
        products.VALIDATOR.validate(bundle)
    except jsonschema.ValidationError as error:
        problems.append(f"bundle schema: {error.message[:200]}")
    files = bundle["dsl"]["files"]
    if len(files) > 12 or any(len(file["blocks"]) > 64 for file in files):
        problems.append(f"{len(files)} files, at most {max(len(f['blocks']) for f in files)} blocks in one")
    if any(not block["id"].startswith("rust.") for file in files for block in file["blocks"]):
        problems.append("a block id does not start with rust.")
    body = phase2.dump({"bundle": bundle})
    assembled = [phase2.post(qa, body, "/v1/assemble") for _ in range(2)]
    if assembled[0] != assembled[1]:
        problems.append("two assembles differ in bytes")
    record["bundle_sha256"] = phase2.qa.sha(bundle)
    if assembled[0][0] != 200:
        record.update(passed=False, problems=problems + [f"assemble gave {assembled[0][0]}"])
        return record, None
    output = json.loads(assembled[0][1])["files"]
    expected, reference = layout(prompt)
    if set(output) != expected:
        problems.append(f"file set {sorted(set(output) ^ expected)} differs from the {record['shape']} shape")
    for name in ("Cargo.toml", "Cargo.lock"):
        if output.get(name, "").encode() != (reference / name).read_bytes():
            problems.append(f"{name} differs from {reference.name}/{name}")
    record.update(files_sha256=phase2.qa.sha(output), files=len(output),
                  blocks=sum(len(file["blocks"]) for file in files), passed=not problems, problems=problems)
    return record, output


# ---------------------------------------------------------------- gated Rust product

def outcome(code: int, log: str = "") -> dict:
    return {"passed": code == 0, **({"log": log[-3000:]} if code else {})}


class Gate:
    """The product gates of the case a fixture worker is running."""

    current: "Gate | None" = None

    def __init__(self, case: dict, prompt: dict, python_files: dict, fixture: dict, work: pathlib.Path) -> None:
        self.case, self.prompt, self.python_files, self.fixture, self.work = case, prompt, python_files, fixture, work
        self.gates: dict[str, dict] = {}
        self.prepared = False
        self.test_logs: list[pathlib.Path] = []
        self.work.mkdir(parents=True, exist_ok=True)

    def toolchain_env(self, host: Any, env: dict) -> dict:
        return {**host.cargo_env(env), **products.pinned_toolchain(), "CARGO_TARGET_DIR": str(host.target_dir)}

    def before_build(self, host: Any, env: dict) -> None:
        root, cargo = host.root, str(products.BIN_DIR / "cargo")
        toolchain = self.toolchain_env(host, env)
        if not os.environ.get("CARGO_HOME"):
            self.gates["cargo_home"] = outcome(1, "CARGO_HOME is not set to the vendored union")
        rs = sorted(str(p.relative_to(root)) for p in root.rglob("*.rs"))
        # --config-path /dev/null keeps rustfmt.toml files above the product out, as in the service.
        self.gates["rustfmt_check"] = outcome(*products.run(
            [str(products.BIN_DIR / "rustfmt"), "--edition", "2024", "--config-path", "/dev/null", "--check", *rs],
            root, toolchain))
        self.gates["cargo_build"] = outcome(*products.run([cargo, "build", "--locked", "--offline", "-q"],
                                                          root, toolchain))
        self.gates["cargo_clippy"] = outcome(*products.run(
            [cargo, "clippy", "--locked", "--offline", "-q", "--all-targets", "--", "-D", "warnings"], root, toolchain))

    def after_build(self, host: Any, env: dict) -> None:
        name = "index_equivalence" if self.prompt["capabilities"]["database"] == "mongodb" else "schema_equivalence"
        try:
            self.gates[name] = self.storage(host, env)
        except Exception as error:  # noqa: BLE001
            self.gates[name] = outcome(1, f"{type(error).__name__}: {error}\n{traceback.format_exc()[-2000:]}")
        try:
            self.gates["missing_setting"] = self.missing_setting(host, env)
        except Exception as error:  # noqa: BLE001
            self.gates["missing_setting"] = outcome(1, f"{type(error).__name__}: {error}")

    def missing_setting(self, host: Any, env: dict) -> dict:
        mongo = self.prompt["capabilities"]["database"] == "mongodb"
        return missing_setting(host.root, self.toolchain_env(host, env), env, mongo)

    def storage(self, host: Any, env: dict) -> dict:
        """som_app migrate upgrade and the Python product's migration, each on a fresh database."""
        from business_database_checks import Mongo, Postgres
        from phase2_host import PythonProduct
        from verify_related import run
        mongo = self.prompt["capabilities"]["database"] == "mongodb"
        backend = Mongo if mongo else Postgres
        stamp = uuid.uuid4().hex[:12]
        rust, python = (backend(self.prompt, f"som_test_gate_{side}_{stamp}", self.fixture) for side in "rp")
        root = self.work / "python"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir()
        phase2.write_product(root, self.python_files)
        rust.create(run)
        python.create(run)
        try:
            run([host.binary, "migrate", "upgrade"], cwd=host.root, env={**env, **rust.env})
            product = PythonProduct(root)
            product.prepare(env)
            run(product.migration_command(python, True), cwd=root, env={**env, **python.env}, timeout=600)
            if mongo:
                found = {"rust": self.indexes(rust.db), "python": self.indexes(python.db)}
            else:
                found = {"rust": products.schema("postgresql:///" + rust.name, RUST_RECORDS),
                         "python": products.schema("postgresql:///" + python.name, PYTHON_RECORDS)}
        finally:
            for side in (rust, python):
                try:
                    side.close(run)
                except Exception:  # noqa: BLE001
                    pass
            if (root / ".venv").is_dir():
                shutil.rmtree(root / ".venv")
        equal = found["rust"] == found["python"]
        return {"passed": equal, **({} if equal else {"differences": self.differences(found)}),
                "compared": sorted(found["rust"]),
                "sha256": {side: hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
                           for side, value in found.items()}}

    @staticmethod
    def differences(found: dict) -> list[dict]:
        """Each table or collection, and each of its parts, where the two products differ."""
        out = []
        for name in sorted(set(found["rust"]) | set(found["python"])):
            rust, python = found["rust"].get(name), found["python"].get(name)
            if isinstance(rust, dict) and isinstance(python, dict):
                out += [{"table": name, "part": part, "rust": rust.get(part), "python": python.get(part)}
                        for part in sorted(set(rust) | set(python)) if rust.get(part) != python.get(part)]
            elif rust != python:
                out.append({"table": name, "rust": rust, "python": python})
        return json.loads(json.dumps(out, default=str))[:40]

    @staticmethod
    def indexes(db: Any) -> dict:
        """Each collection's indexes as [keys in order, unique], names left out."""
        return {name: sorted([[list(pair) for pair in index["key"].items()], bool(index.get("unique"))]
                             for index in db[name].list_indexes())
                for name in sorted(db.list_collection_names()) if not name.startswith("system.")}

    def test_log(self) -> pathlib.Path:
        path = self.work / f"cargo-test-{len(self.test_logs) + 1}.json"
        self.test_logs.append(path)
        return path

    def finish(self, product: pathlib.Path, files: dict | None) -> None:
        runs = [json.loads(path.read_text()) for path in self.test_logs if path.exists()]
        problems = []
        for index, test in enumerate(runs, 1):
            if test["returncode"]:
                problems.append(f"cargo test run {index} exited {test['returncode']}")
            else:
                code, why = products.test_count(product, test["stdout"] + test["stderr"])
                if code:
                    problems.append(f"cargo test run {index}: {why}")
        if not runs:
            problems.append("the checker ran no product test")
        self.gates["cargo_test"] = {"passed": not problems, "runs": len(runs),
                                    **({"log": "; ".join(problems)} if problems else {})}
        if files is not None and (product / "Cargo.lock").exists():
            same = (product / "Cargo.lock").read_text(encoding="utf-8") == files["Cargo.lock"]
            self.gates["lock_unchanged"] = outcome(0 if same else 1, "Cargo.lock changed")
        else:
            self.gates["lock_unchanged"] = outcome(1, "no product")


def gated_product_class() -> type:
    import phase2_host

    class GatedRustProduct(phase2_host.RustProduct):
        """RustProduct with the product gates of Gate.current around its first build."""

        def prepare(self, env: dict) -> None:
            gate = Gate.current
            if gate is None or gate.prepared:
                return super().prepare(env)
            gate.prepared = True
            gate.before_build(self, env)
            super().prepare(env)
            gate.after_build(self, env)

        def test_command(self) -> list[str]:
            command = super().test_command()
            if Gate.current is None:
                return command
            return [sys.executable, str(pathlib.Path(__file__).resolve()), "tee", str(Gate.current.test_log()),
                    "--", *command]

    return GatedRustProduct


# ---------------------------------------------------------------- one case

def gate_path(out: pathlib.Path, case: dict) -> pathlib.Path:
    return out / "gates" / f"{phase2.key(case['id'])}.json"


def behavior_record(out: pathlib.Path, case: dict, bundle: dict) -> dict:
    path = out / "cases" / f"{phase2.key(case['id'])}.json"
    if not path.exists():
        return {"passed": False, "problems": ["no check_phase2 row"]}
    row = phase2.read(path)
    rust = row.get("rust") or {}
    problems = [] if row["passed"] else ["the check_phase2 row did not pass"] + row.get("problems", [])[:3]
    if bundle.get("files_sha256") and rust.get("files_sha256") != bundle["files_sha256"]:
        problems.append("the checker's files differ from the bundle_checks files")
    if bundle.get("bundle_sha256") and rust.get("bundle_sha256") != bundle["bundle_sha256"]:
        problems.append("the checker's bundle differs from the bundle_checks bundle")
    return {"passed": not problems, "problems": problems, "checker": case["checker"],
            "changed_files": row.get("changed_files", []), "row_sha256": file_sha(path)}


def write_gate(out: pathlib.Path, case: dict, record: dict, started: float) -> None:
    gates = record.get("product_gates") or {}
    record["passed"] = (record["bundle_checks"]["passed"] and record["behavior"]["passed"]
                        and all(g["passed"] for g in gates.values()))
    record["seconds"] = round(time.monotonic() - started, 1)
    record["finished_at"] = now()
    gate_path(out, case).write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    print(f"{case['id']}: gates {'ok' if record['passed'] else 'FAIL'} {record['seconds']}s",
          file=sys.stderr, flush=True)


TEST_SETTINGS = {"DATABASE_URL", "TEST_DATABASE_URL", "MONGODB_URL", "MONGODB_DATABASE", "SOM_TEST_DATABASE_NAME"}


def missing_setting(root: pathlib.Path, toolchain: dict, env: dict, mongo: bool) -> dict:
    """product_gates[3]: cargo test fails, naming the setting, when a test database setting is missing."""
    settings = ("MONGODB_URL", "SOM_TEST_DATABASE_NAME") if mongo else ("TEST_DATABASE_URL",)
    base = {k: v for k, v in toolchain.items() if k not in TEST_SETTINGS}
    command = [str(products.BIN_DIR / "cargo"), "test", "--locked", "--offline", "-q", "--", "--test-threads=1"]
    runs, problems = {}, []
    for setting in settings:
        kept = {k: env[k] for k in settings if k != setting and k in env}
        code, log = products.run(command, root, {**base, **kept}, timeout=600)
        runs[setting] = code
        if code == 0:
            problems.append(f"cargo test passed without {setting}")
        elif setting not in log:
            problems.append(f"cargo test failed without {setting} but does not name it")
    return {"passed": not problems, "exit_codes": runs, **({"log": "; ".join(problems)} if problems else {})}


def head(case: dict) -> dict:
    return {key: case[key] for key in ("id", "source", "family", "group", "position", "prompt_version",
                                       "checker", "standard", "prompt_sha256", "product_sha256")}


def fixture_case(out: pathlib.Path, case: dict, fixture: dict, qa: str, index: int) -> None:
    started = time.monotonic()
    record: dict[str, Any] = {**head(case), "expected": INVENTORY[case["id"]]}
    files = None
    try:
        prompt, python_files = phase2.load(case)
        record["bundle_checks"], files = bundle_checks(qa, case, prompt)
        if files is not None:
            Gate.current = Gate(case, prompt, python_files, fixture, out / "gates-work" / phase2.key(case["id"]))
    except Exception as error:  # noqa: BLE001
        record["bundle_checks"] = {"passed": False, "problems": [f"{type(error).__name__}: {error}"[:2000]]}
    try:
        phase2.fixture_case(out, case, fixture, "rust", qa, out / "target" / f"w{index}")
    finally:
        gate, Gate.current = Gate.current, None
    if gate is not None:
        gate.finish(out / "work" / phase2.key(case["id"]) / "product", files)
        record["product_gates"] = gate.gates
    elif files is not None or INVENTORY[case["id"]]["result"] != "refusal":
        record["product_gates"] = {"run": outcome(1, "no product to gate")}
    record["behavior"] = behavior_record(out, case, record["bundle_checks"])
    write_gate(out, case, record, started)


def suite_case(out: pathlib.Path, case: dict, host: Any, qa: str, pg: str) -> None:
    started = time.monotonic()
    record: dict[str, Any] = {**head(case), "expected": INVENTORY[case["id"]]}
    try:
        prompt, _ = phase2.load(case)
        record["bundle_checks"], files = bundle_checks(qa, case, prompt)
        if files is not None:
            name = phase2.key(case["id"]).lower()[:24]
            work = out / "gates-work" / f"suite-{uuid.uuid4().hex[:8]}"
            target = str(out / "target" / "w0-gates")
            gates = products.product_gates(qa, {"id": name, "prompt": prompt}, files, work, pg, target)
            record["product_gates"] = gates["gates"]
            # products.product_gates writes the product to work / its key; the phase 1 shapes are PostgreSQL.
            root = work / re.sub(r"[^a-z0-9]+", "_", name)
            toolchain = {**os.environ, **products.pinned_toolchain(), "CARGO_TARGET_DIR": target}
            try:
                record["product_gates"]["missing_setting"] = missing_setting(root, toolchain, {}, False)
            except Exception as error:  # noqa: BLE001
                record["product_gates"]["missing_setting"] = {"passed": False,
                                                              "log": f"{type(error).__name__}: {error}"}
            record["tests_passed"] = gates["tests_passed"]
        elif INVENTORY[case["id"]]["result"] != "refusal":
            record["product_gates"] = {"run": outcome(1, "no product to gate")}
    except Exception as error:  # noqa: BLE001
        record.setdefault("bundle_checks", {"passed": False, "problems": []})
        record["product_gates"] = {"run": outcome(1, f"{type(error).__name__}: {error}\n{traceback.format_exc()}")}
    phase2.suite_case(out, case, host, "rust")
    record["behavior"] = behavior_record(out, case, record["bundle_checks"])
    write_gate(out, case, record, started)


def worker(out: pathlib.Path, index: int, claims: str, ids: list[str], qa: str) -> int:
    wanted = set(ids)
    todo = [case for case in phase2.cases() if case["id"] in wanted]
    if index == 0:
        sys.path.insert(0, str(phase2.HERE.parent))
        import tempfile

        import hosts
        with tempfile.TemporaryDirectory(prefix="som-phase2-", dir=out / "fixtures",
                                         ignore_cleanup_errors=True) as scratch:
            work = pathlib.Path(scratch)
            ports = hosts.Ports()
            with hosts.cluster(work, ports.take()) as pg:
                host = hosts.Host(qa, pg, work / "products", work / "target", sys.executable)
                host.ports = ports
                for case in todo:
                    if phase2.claim(out, claims, case):
                        suite_case(out, case, host, qa, pg)
        return 0
    import phase2_host
    phase2_host.RustProduct = gated_product_class()
    from m3_local_fixture import M3Fixture
    with M3Fixture(out / "fixtures" / f"w{index}-{claims}") as fixture:
        for case in todo:
            if phase2.claim(out, claims, case):
                fixture_case(out, case, fixture, qa, index)
    return 0


# ---------------------------------------------------------------- run and summarize

def run(args: argparse.Namespace) -> int:
    out = args.out.resolve()
    every = phase2.cases()
    unknown = set(args.only or []) - {case["id"] for case in every}
    if unknown:
        raise SystemExit(f"unknown ids: {sorted(unknown)}")
    if out.exists() and not args.resume:
        raise SystemExit(f"{out} exists; use a new directory or --resume")
    if not os.environ.get("CARGO_HOME"):
        raise SystemExit("set CARGO_HOME to the vendored union of toolchain.json phase2_crates")
    for name in ("cases", "work", "fixtures", "gates", "gates-work"):
        (out / name).mkdir(parents=True, exist_ok=True)
    settings = {"language": "rust", "qa": args.qa}
    if (out / "run.json").exists() and phase2.read(out / "run.json") != settings:
        raise SystemExit("--resume must keep the candidate of the run")
    (out / "run.json").write_text(json.dumps(settings, indent=1) + "\n")
    done = {path.stem for path in (out / "gates").glob("*.json")}
    todo = [c for c in every if (not args.only or c["id"] in args.only) and phase2.key(c["id"]) not in done]
    claims = "claims-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    (out / claims).mkdir()
    suite_ids = [c["id"] for c in todo if c["checker"] == "phase1_suite"]
    fixture_ids = [c["id"] for c in todo if c["checker"] != "phase1_suite"]
    plan = ([(0, suite_ids)] if suite_ids else []) + [
        (index, fixture_ids) for index in range(1, min(args.workers, len(fixture_ids)) + 1)]
    print(f"{len(todo)} cases: {len(suite_ids)} on the phase 1 suite, {len(fixture_ids)} on "
          f"{len(plan) - bool(suite_ids)} fixture workers", flush=True)
    env = {**os.environ, **phase2.WORKER_ENV}
    processes = []
    for index, ids in plan:
        log = (out / f"worker-{index}-{claims}.log").open("w")
        argv = [sys.executable, str(pathlib.Path(__file__).resolve()), "worker", "--out", str(out),
                "--index", str(index), "--claims", claims, "--ids", json.dumps(ids), "--qa", args.qa]
        processes.append(subprocess.Popen(argv, stdout=log, stderr=log, env=env))
    codes = [process.wait() for process in processes]
    print("worker exit codes", codes, flush=True)
    phase2.summarize(out)
    return max(codes, default=0)


def python_gate(python_run: pathlib.Path) -> tuple[dict, dict[str, bool]]:
    recorded = BEHAVIOR["python_run"]
    summary_path = python_run / "summary.json"
    problems = []
    if file_sha(summary_path) != recorded["summary_sha256"]:
        problems.append("the Python run summary differs from phase2/behavior.json python_run")
    summary = phase2.read(summary_path)
    explained = set(BEHAVIOR["python_defects"]) | set(BEHAVIOR["phase2_refusals"])
    verdict = {}
    for case_id, sha in summary["case_sha256"].items():
        path = python_run / "cases" / f"{phase2.key(case_id)}.json"
        if file_sha(path) != sha:
            problems.append(f"{case_id}: the Python row differs from its summary hash")
        verdict[case_id] = bool(phase2.read(path)["passed"]) or case_id in explained
    return ({"out": python_run.as_posix(), "summary_sha256": file_sha(summary_path), "problems": problems,
             "passed": sum(verdict.values()), "explained": sorted(explained & set(verdict)),
             "total": len(verdict)}, verdict)


def summarize(args: argparse.Namespace) -> int:
    out = args.out.resolve()
    every = phase2.cases()
    python, python_ok = python_gate(args.python_run.resolve())
    rows, missing, failed = {}, [], []
    gate_counts: dict[str, dict[str, int]] = {}
    for case in every:
        path = gate_path(out, case)
        if not path.exists():
            missing.append(case["id"])
            continue
        row = phase2.read(path)
        row["python"] = {"passed": python_ok.get(case["id"], False)}
        rows[case["id"]] = (row, file_sha(path))
        for name, gate in [("bundle_checks", row["bundle_checks"]), ("behavior", row["behavior"]),
                           ("python", row["python"]), *(row.get("product_gates") or {}).items()]:
            count = gate_counts.setdefault(name, {"passed": 0, "total": 0})
            count["total"] += 1
            count["passed"] += bool(gate["passed"])
        if not (row["passed"] and row["python"]["passed"]):
            failed.append(case["id"])
    refusals = sorted(i for i, (row, _) in rows.items() if row["bundle_checks"].get("refusal"))
    expected_refusals = sorted(i for i, e in INVENTORY.items() if e["result"] == "refusal")
    behavior_summary = out / "summary.json"
    report = {
        "version": "som.rust-output-phase2-product-checks.v1",
        "issue": 55,
        "qa": phase2.read(out / "run.json")["qa"],
        "total": len(every), "run": len(rows), "passed": len(rows) - len(failed),
        "complete": not missing, "failed": failed, "missing": missing,
        "refusals": {"found": refusals, "expected": expected_refusals, "passed": refusals == expected_refusals},
        "distinct_products": len({row["bundle_checks"].get("files_sha256") for row, _ in rows.values()} - {None}),
        "gates": dict(sorted(gate_counts.items())),
        "by_shape": {},
        "behavior_summary_sha256": file_sha(behavior_summary) if behavior_summary.exists() else None,
        "python": python,
        "inputs": {p.relative_to(targets.REPO).as_posix(): file_sha(p) for p in [
            CONTRACT / "acceptance-plan.json", CONTRACT / "product-layout.json", CONTRACT / "bundle.schema.json",
            CONTRACT / "toolchain.json", CONTRACT / "phase2" / "case-inventory.json",
            CONTRACT / "phase2" / "behavior.json", CONTRACT / "reference" / "Cargo.lock",
            CONTRACT / "reference-phase2" / "Cargo.toml", CONTRACT / "reference-phase2" / "Cargo.lock",
            pathlib.Path(__file__).resolve(), phase2.HERE / "check_phase2.py", phase2.HERE / "phase2_host.py",
            HERE / "check_rust_products.py"]},
        "gate_sha256": {i: sha for i, (_, sha) in sorted(rows.items())},
    }
    for row, _ in rows.values():
        item = report["by_shape"].setdefault(row["bundle_checks"].get("shape", "none"), {"total": 0, "passed": 0})
        item["total"] += 1
        item["passed"] += bool(row["passed"] and row["python"]["passed"])
    ok = (report["complete"] and not failed and report["refusals"]["passed"] and not python["problems"])
    report["accepted"] = ok
    (out / "products-summary.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("total", "run", "passed", "failed", "missing", "refusals", "gates",
                                             "by_shape", "accepted")}, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------- phase 1 unchanged

def health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def bundle_differences(old: Any, new: Any, path: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f"{path}/{key}")
            else:
                out += bundle_differences(old[key], new[key], f"{path}/{key}")
        return out
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [d for i, (a, b) in enumerate(zip(old, new)) for d in bundle_differences(a, b, f"{path}/{i}")]
    return [] if old == new else [path]


def allowed(path: str, old: dict, new: dict) -> bool:
    if path == "/hashes/payload_sha256":
        return True
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "assets":
        ids = [side["assets"].get(parts[2], {}) for side in (old, new)]
        return all(isinstance(item, dict) and str(item.get("id", "")).startswith("som.rust-") for item in ids)
    return False


def phase1(args: argparse.Namespace) -> int:
    if any(str(args.qa).rstrip("/").endswith(port) for port in FORBIDDEN) or args.port in (8765, 18765):
        raise SystemExit("QA checks never use ports 8765 or 18765")
    args.baseline, args.record, args.out = args.baseline.resolve(), args.record.resolve(), args.out.resolve()
    record = json.loads(args.record.read_text())
    header = json.loads((args.record.parent / "header.json").read_text())["candidate_before"]
    binary = args.baseline / "som"
    if file_sha(binary) != header["binary_sha256"]:
        raise SystemExit(f"{binary} differs from the record's candidate_before binary")
    accepted = {row["id"]: row for row in record["cases"]}
    cases = products.acceptance_cases()
    if sorted(accepted) != sorted(case["id"] for case in cases):
        raise SystemExit("the phase 1 acceptance cases differ from the record")
    base = f"http://127.0.0.1:{args.port}"
    if health(base):
        raise SystemExit(f"port {args.port} is already serving")
    log = (args.out.parent / (args.out.stem + ".baseline.log")).open("w")
    server = subprocess.Popen([str(binary), "serve", "--assets", str(args.baseline / "assets"),
                               "--port", str(args.port)], stdout=log, stderr=log, cwd=args.out.parent)
    rows = []
    try:
        for _ in range(120):
            if health(base) or server.poll() is not None:
                break
            time.sleep(0.5)
        if not health(base):
            raise SystemExit("the baseline server did not start")
        for case in cases:
            problems: list[str] = []
            forms = ["json"] + (["cnl"] if case["cnl"] is not None else [])
            row: dict[str, Any] = {"id": case["id"], "version": case["prompt"]["version"], "forms": forms}
            for form in forms:
                body = products.compile_body(case, form)
                (old_status, old_raw), (new_status, new_raw) = (targets.post(server_url, body)
                                                                for server_url in (base, args.qa))
                if (old_status, new_status) != (200, 200):
                    problems.append(f"{form} compile gave {old_status} on the baseline, {new_status} here")
                    continue
                old, new = json.loads(old_raw)["bundle"], json.loads(new_raw)["bundle"]
                differences = bundle_differences(old, new)
                problems += [f"{form}: {d} differs" for d in differences if not allowed(d, old, new)]
                if old["hashes"]["payload_sha256"] != accepted[case["id"]]["bundle_payload_sha256"]:
                    problems.append(f"{form}: the baseline payload differs from the record")
                files = []
                for server_url, bundle in ((base, old), (args.qa, new)):
                    status, raw = targets.post(server_url, targets.dump({"bundle": bundle}), "/v1/assemble")
                    files.append(json.loads(raw)["files"] if status == 200 else None)
                if files[0] is None or files[0] != files[1]:
                    problems.append(f"{form}: the assembled files differ")
                elif targets.sha(targets.dump(files[1]).encode()) != accepted[case["id"]]["files_sha256"]:
                    problems.append(f"{form}: the files differ from the record")
                row[form] = {"differences": differences, "payload_sha256": [old["hashes"]["payload_sha256"],
                                                                           new["hashes"]["payload_sha256"]]}
            row.update(passed=not problems, problems=problems)
            rows.append(row)
            print(f"{case['id']}: {'ok' if row['passed'] else 'FAIL'}", file=sys.stderr, flush=True)
    finally:
        server.terminate()
        server.wait(timeout=30)
    failed = [row["id"] for row in rows if not row["passed"]]
    report = {"version": "som.rust-output-phase1-unchanged.v1", "issue": 55, "qa": args.qa,
              "baseline": {"dir": str(args.baseline), "binary_sha256": file_sha(binary), "port": args.port},
              "record": {"path": str(args.record), "sha256": file_sha(args.record)},
              "total": len(rows), "passed": len(rows) - len(failed), "failed": failed, "cases": rows}
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({k: report[k] for k in ("total", "passed", "failed")}, indent=1))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("run")
    p.add_argument("--qa", required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--only", nargs="*")
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("worker")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--claims", required=True)
    p.add_argument("--ids", required=True)
    p.add_argument("--qa", required=True)
    p = sub.add_parser("summarize")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--python-run", type=pathlib.Path, required=True)
    p = sub.add_parser("phase1")
    p.add_argument("--qa", required=True)
    p.add_argument("--baseline", type=pathlib.Path, required=True, help="the rust-output-050 candidate directory")
    p.add_argument("--record", type=pathlib.Path, required=True, help="rust-output-050 run rust-products.json")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if getattr(args, "qa", None) and str(args.qa).rstrip("/").endswith(FORBIDDEN):
        parser.error("QA checks never use ports 8765 or 18765")
    if args.mode in ("run", "worker") and pathlib.Path(sys.prefix).resolve() != phase2.VENV.resolve():
        raise SystemExit(f"run this with {phase2.VENV}/bin/python, the pinned acceptance environment")
    if args.mode == "run":
        return run(args)
    if args.mode == "worker":
        return worker(args.out.resolve(), args.index, args.claims, json.loads(args.ids), args.qa)
    if args.mode == "summarize":
        return summarize(args)
    return phase1(args)


if __name__ == "__main__":
    raise SystemExit(main())
