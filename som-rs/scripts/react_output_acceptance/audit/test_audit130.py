"""Independent-audit fixtures. Run this suite on GitHub, never on the local host."""

from __future__ import annotations

from collections import Counter
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SOURCE_REPO = HERE.parents[3]
SPEC = importlib.util.spec_from_file_location("independent_react_audit130", HERE / "audit130.py")
audit130 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit130)

STEPS = (
    "contract", "react-target", "react-python-controls", "python-baseline",
    "rust-baseline", "rust-python-controls", "rt-diagnostics", "ts-target",
    "ts-baseline", "ts-python-controls", "bundles", "products",
)
CONTROL_STEPS = {"react-python-controls", "rust-python-controls", "ts-python-controls"}
CONTRACT_REL = Path("som-rs/contracts/react-output-v1")
TOOL_REL = Path("som-rs/scripts/react_output_acceptance/run_acceptance.py")
STATUSES = {"passed", "attested", "post_acceptance", "unrun", "failed"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    return sha_bytes(path.read_bytes())


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def read_json(path):
    return json.loads(path.read_bytes())


class RecordedRun:
    """A tiny run with the same on-disk schemas as checks #128, #129 and #136.

    Contract case counts are reduced in this temporary repository only.
    No acceptance helper is imported and no recorded command is executed.
    """

    def __init__(self, repo):
        self.repo = repo
        self.repo.mkdir(parents=True)
        self.run = repo / ".local/react-output/run-fixture"
        self.run.mkdir(parents=True)
        self.contract = repo / CONTRACT_REL
        self.output = repo / ".local/audits/report.json"
        self.prompt = read_json(SOURCE_REPO / "som-rs/contracts/compiler-v1/examples/v1.prompt.json")
        self.cid = "example-v1"
        self.plan = read_json(SOURCE_REPO / CONTRACT_REL / "acceptance-plan.json")
        # The pass fixture proves a reduced immutable fake plan, not the full
        # real acceptance plan. Separate tests enable unsupported categories
        # and require an honest unrun result when their raw evidence is absent.
        self.plan["assemble_refusals"] = {}
        for family in ("python_unchanged", "rust_unchanged", "typescript_unchanged"):
            self.plan[family]["checks"] = []
        self.plan["issue_gates"] = {key: self.plan["issue_gates"][key]
                                    for key in ("phase1-renderer", "release")}
        self.docs = {}
        self.reports = {}
        self._contracts()
        self.refresh_contract_receipts()
        self._products()
        self._target_and_bundle_evidence()
        self._other_step_evidence()
        self._identity()
        self.refresh_contract_receipts()
        self.refresh_receipts()

    def _contracts(self):
        for name in ("api-contract.json", "scope.json", "toolchain.json", "api.schema.json", "bundle.schema.json"):
            self.docs[name] = read_json(SOURCE_REPO / CONTRACT_REL / name)
        layout = read_json(SOURCE_REPO / CONTRACT_REL / "product-layout.json")
        keep = {"package.json", "package-lock.json", "src/schema.ts", "README.md"}
        layout["files"] = [row for row in layout["files"] if row["path"] in keep]
        layout.update(file_count=4, fixed_file_count=2, generated_file_count=2)
        self.docs["product-layout.json"] = layout
        inventory = read_json(SOURCE_REPO / CONTRACT_REL / "case-inventory.json")
        inventory["public_examples"] = [copy.deepcopy(inventory["public_examples"][0])]
        for key in ("version_positive_fixtures", "coverage_cases", "name_cases", "public_products_in_scope", "legacy_in_scope"):
            inventory[key] = []
        inventory["counts"] = {
            "acceptance_total": 1, "public_examples_in_scope": 1,
            "version_positive_in_scope": 0, "coverage_cases_in_scope": 0,
            "name_cases": 0, "public_products_in_scope": 0, "legacy_in_scope": 0,
        }
        self.docs["case-inventory.json"] = inventory
        negatives = read_json(SOURCE_REPO / CONTRACT_REL / "negative-cases.json")
        negatives["cases"] = [next(copy.deepcopy(row) for row in negatives["cases"] if row["id"].startswith(prefix))
                              for prefix in ("RR", "RS", "RP")]
        self.docs["negative-cases.json"] = negatives
        targets = read_json(SOURCE_REPO / CONTRACT_REL / "target-examples.json")
        targets["examples"] = copy.deepcopy(targets["examples"][:2])
        targets["name_cases"] = []
        self.docs["target-examples.json"] = targets
        self.docs["acceptance-plan.json"] = self.plan
        for name, doc in self.docs.items():
            save_json(self.contract / name, doc)
        self.catalog = {"version": "som.react-catalog.v1", "dsl_version": "som.dsl.react.v1",
                        "files": [{"path": row["path"], "blocks": [{
                            "id": "react.fixture." + str(index), "name": "fixture",
                            "versions": ["som.prompt.v1"], "params": {}, "refs": [],
                        }]} for index, row in enumerate(layout["files"])]}
        save_json(self.contract / "catalog/react-catalog.v1.json", self.catalog)
        # Public text and JSON data are templates; no Python tool is copied or run.
        examples = SOURCE_REPO / "som-rs/contracts/compiler-v1/examples"
        shutil.copytree(examples, self.repo / "som-rs/contracts/compiler-v1/examples")
        for path in (SOURCE_REPO / "som-rs/contracts/compiler-v1").glob("*.schema.json"):
            shutil.copyfile(path, self.repo / "som-rs/contracts/compiler-v1" / path.name)
        save_json(self.repo / "som-rs/tests/compiler-v1/cnl-positive-fixtures.json", {"fixtures": [], "version_positive": []})
        save_json(self.repo / "som-rs/tests/rust-output-v1/cases/index.json", {"cases": []})
        for name in ("package.json", "package-lock.json"):
            target = self.contract / "reference" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n")

    def _products(self):
        self.files = {"package.json": "{}\n", "package-lock.json": "{}\n",
                      "src/schema.ts": "export const fixture = 1;\n", "README.md": "Fixture\n"}
        self.product = self.run / "bundles/products" / self.cid
        work = self.run / "products" / self.cid
        gates_dir = work / "gates"
        for root in (self.product, gates_dir / "product"):
            for name, content in self.files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        hashes = {name: sha_bytes(content.encode()) for name, content in self.files.items()}
        dist_hashes = {"index.html": sha_bytes(b"fixture build\n")}
        for index in (1, 2):
            dist = gates_dir / f"dist-{index}"
            dist.mkdir(parents=True)
            (dist / "index.html").write_bytes(b"fixture build\n")
        gate_rows = []
        commands = self.docs["toolchain.json"]["commands"]
        for name in ("install", "typecheck", "format_check", "test", "build-1", "build-2"):
            command = commands[name] if not name.startswith("build-") else commands["build"].replace(
                "<dist dir outside the product>", str(gates_dir / f"dist-{name[-1]}"))
            (gates_dir / f"{name}.log").write_text("fixture command exited 0\n")
            gate_rows.append({"step": name, "command": command, "exit_code": 0, "log": f"{name}.log"})
        self.gates_path = gates_dir / "gates.json"
        save_json(self.gates_path, {
            "version": "som.react-output-gates.v1", "source": str(self.product),
            "product_files": hashes, "product_files_after": hashes,
            "gates": gate_rows, "environment": {"npm_config_offline": "true"},
            "builds_identical": True, "dist_files": dist_hashes,
            "lock_sha256_before": hashes["package-lock.json"], "lock_sha256_after": hashes["package-lock.json"],
            "lock_unchanged": True, "product_unchanged": True,
            "vendored_cache_tree_sha256_before": "c" * 64,
            "vendored_cache_tree_sha256_after": "c" * 64,
            "prettier_tree_sha256": "d" * 64, "passed": True,
        })
        facts = {"query": False, "filter": False, "before": False,
                 "not_nullable": True, "required": True, "unique": True}
        skip = {"list filter", "list before", "list page", "list limit too large"}
        api = self.docs["api-contract.json"]
        api_steps = [{"name": row["name"], "ok": None, "skipped": api["skip_reasons"][row["name"]]}
                     if row["name"] in skip else {"name": row["name"], "ok": True}
                     for row in api["steps"]]
        self.api_path = work / "api/report.json"
        process = {"pid": 1234, "role": "fixture", "expected_cwd": str(work), "initial_cwd": str(work),
                   "initial_cwd_verified": True, "command": ["never-executed"],
                   "stopped": True, "stop_cwd_verified": True, "exit_code": 0}
        save_json(self.api_path, {"case": str(work / "python"), "react": str(self.product), "exit_code": 0,
                                  "result": {"resource": "Item", "failed": 0, "facts": facts, "steps": api_steps},
                                  "processes": [process]})
        python_files = {"app.py": "# fixture Python output\n"}
        python_dir = work / "python"
        save_json(python_dir / "prompt.json", self.prompt)
        save_json(python_dir / "compile.response.json", {"status": "ok", "bundle": {"version": "som.dsl-bundle.v1"}})
        save_json(python_dir / "assemble.response.json", {"status": "ok", "files": python_files})
        for name, body in python_files.items():
            (python_dir / "product").mkdir(parents=True, exist_ok=True)
            (python_dir / "product" / name).write_text(body)
        save_json(self.run / "bundles/products/manifest.json", {"products": [{
            "id": self.cid, "group": "public_examples", "product": str(self.product), "prompt": self.prompt,
            "python_request": canonical({"format": "som.prompt-json.v1", "prompt": self.prompt}).decode(),
        }]})
        row = {"id": self.cid, "group": "public_examples", "prompt_version": "som.prompt.v1",
               "exit_codes": {"gates": 0, "api_contract": 0},
               "gates": {gate["step"]: 0 for gate in gate_rows}, "builds_identical": True,
               "lock_unchanged": True, "product_unchanged": True, "vendored_cache_unchanged": True,
               "product_files": hashes, "dist_files_sha256": sha_bytes(json.dumps(dist_hashes, sort_keys=True).encode()),
               "python_product_files": {key: sha_bytes(value.encode()) for key, value in python_files.items()},
               "api_facts": facts, "api_steps": api_steps, "processes_stopped": True,
               "problems": [], "passed": True}
        self.reports["products"] = {"version": "som.react-output-product-gates.v1", "case_count": 1,
                                     "complete": True, "failed": [], "passed": True, "cases": [row]}

    def _raw_row(self, step, section, cid, body, response, status=200, stem=None, **extra):
        raw = self.run / step / "responses" / f"{stem or section + '__' + cid}.json"
        save_json(raw, response)
        return {"id": cid, "section": section, "http_status": status,
                "request_sha256": sha_bytes(body.encode()), "response_sha256": sha_file(raw),
                "response_file": str(raw.relative_to(self.run / step)), "expected": "recorded contract result",
                "status": "passed", "passed": True, "reason": "ok", **extra}

    def _target_and_bundle_evidence(self):
        shared = ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer", "semantic_contracts")
        self.assets = {name: {"id": "fixture." + name, "sha256": "a" * 64}
                       for name in (*shared, "target_contract", "catalog", "block_library", "backend", "formatter")}
        self.assets["semantic_contracts"] = []
        self.assets["target_contract"] = {"id": "som.react-output-contract.v1",
                                          "sha256": sha_bytes(canonical(self.contract_files))}
        self.assets["catalog"] = {"id": "som.react-catalog.v1",
                                  "sha256": sha_file(self.contract / "catalog/react-catalog.v1.json")}
        formatter = self.docs["toolchain.json"]["formatter"]
        self.assets["formatter"] = {key: formatter[key] for key in ("id", "sha256")}
        dsl = {"version": "som.dsl.react.v1", "files": [
            {"path": row["path"], "blocks": [{key: block[key] for key in ("id", "name", "params", "refs")}
                                              for block in row["blocks"]]} for row in self.catalog["files"]
        ]}
        self.bundle = {"version": "som.dsl-bundle.v2", "target": "react", "prompt": self.prompt,
                       "dsl": dsl, "assets": self.assets, "hashes": {"prompt_sha256": sha_bytes(canonical(self.prompt)),
                       "dsl_sha256": sha_bytes(canonical(dsl))}}
        self.bundle["hashes"]["payload_sha256"] = sha_bytes(canonical({key: value for key, value in self.bundle.items()
                                                                       if key != "hashes"}))
        compile_doc = {"status": "ok", "bundle": self.bundle}
        target_rows = []
        for example in self.docs["target-examples.json"]["examples"]:
            target_rows.append(self._raw_row("react-target", "target_examples", example["id"], example["body"], compile_doc))
            target_rows.append(self._raw_row("react-target", "in_scope", example["id"], example["body"], compile_doc))
        for case in self.docs["negative-cases.json"]["cases"]:
            response = {"status": "error", "code": case["expected"]["code"],
                        "detail": case["expected"]["cause"], "diagnostics": [case["expected"]]}
            row = self._raw_row("react-target", "negative_cases", case["id"], case["body"], response, case["http_status"])
            row["expected"] = copy.deepcopy(case["expected"])
            target_rows.append(row)
        pair_id = self.cid + "-json-python-equivalence"
        python_doc = {"status": "ok", "bundle": {"version": "som.dsl-bundle.v1"}}
        pair = self._raw_row("react-target", "python_equivalence", pair_id, "{}", python_doc,
                             stem="python_equivalence__" + pair_id + "__python")
        save_json(self.run / "react-target/responses" / f"python_equivalence__{pair_id}__no_target.json", python_doc)
        target_rows.append(pair)
        cli = []
        for case in [*self.docs["target-examples.json"]["examples"], *self.docs["negative-cases.json"]["cases"]]:
            body = json.loads(case["body"])
            if body.get("format") != "som.cnl.v1" or set(body) != {"format", "source", "target"}:
                continue
            refused = isinstance(case["expected"], dict)
            cli.append({"id": case["id"], "target": body["target"], "exit_code": 2 if refused else 0,
                        "http_status": case["http_status"] if refused else 200,
                        "code": case["expected"]["code"] if refused else None,
                        "request_sha256": sha_bytes(case["body"].encode()),
                        "expected_request_sha256": sha_bytes(case["body"].encode()),
                        "stdout_sha256": sha_bytes(canonical(self.bundle)),
                        "expected_stdout_sha256": sha_bytes(canonical(self.bundle)),
                        "passed": True, "problems": []})
        self.reports["react-target"] = {"version": "som.react-output-target-checks.v1", "passed": True,
                                         "status": "passed", "cases": target_rows,
                                         "cli": {"rows": cli, "total": len(cli), "refusals": len(cli) - 1, "passed": True}}
        for form in ("json", "cnl"):
            for attempt in (1, 2):
                save_json(self.run / "bundles/responses" / f"bundle_checks__{self.cid}__compile_{form}_{attempt}.json", compile_doc)
        for attempt in (1, 2):
            save_json(self.run / "bundles/responses" / f"bundle_checks__{self.cid}__assemble_{attempt}.json",
                      {"status": "ok", "files": self.files})
        rust_bundle = copy.deepcopy(self.bundle)
        rust_bundle["target"] = "rust"
        save_json(self.run / "bundles/responses" / f"bundle_checks__{self.cid}__rust_manifest.json", {"status": "ok", "bundle": rust_bundle})
        brow = self._raw_row("bundles", "bundle_checks", self.cid, self.docs["target-examples.json"]["examples"][0]["body"], {"status": "ok", "files": self.files},
                             stem=f"bundle_checks__{self.cid}__assemble_1", forms=["json", "cnl"], single_form=None)
        self.reports["bundles"] = {"version": "som.react-output-bundle-checks.v1", "passed": True,
                                    "status": "passed", "manifest": "products/manifest.json", "cases": [brow]}

    def _other_step_evidence(self):
        for step in CONTROL_STEPS:
            self.reports[step] = {"passed": 1, "failed": [], "results": [{"id": "fixture-control", "passed": True,
                                       "control_status": 200, "payload_sha256": "a" * 64}]}
        response_sha = sha_bytes(canonical({"status": "ok", "fixture": "unchanged"}))
        pyrows = []
        for variant in ("none", "python"):
            row = {"id": "fixture-baseline", "source": "public", "variant": variant,
                   "passed": True, "status": "passed", "reason": None}
            for operation in ("compile", "assemble"):
                for side in ("qa", "baseline"):
                    row[f"{side}_{operation}_status"] = 200
                    row[f"{side}_{operation}_response_sha256"] = response_sha
                row[operation] = {"qa_status": 200, "baseline_status": 200,
                                  "qa_response_sha256": response_sha,
                                  "baseline_response_sha256": response_sha, "passed": True}
            pyrows.append(row)
        self.reports["python-baseline"] = {"version": "som.python-baseline-checks.v1", "rows": pyrows,
                                            "passed": True, "status": "passed"}
        self.reports["rust-baseline"] = {"version": "som.rust-baseline-checks.v1", "passed": True,
            "status": "passed", "rows": [{"id": "fixture-baseline", "phase": "phase1", "check": "compile",
                "qa_status": 200, "baseline_status": 200, "qa_response_sha256": response_sha,
                "baseline_response_sha256": response_sha, "passed": True, "status": "passed"}]}
        self.reports["ts-baseline"] = {"version": "som.react-output-typescript-baseline.v1", "passed": True,
            "status": "passed", "cases": [{"id": "fixture-baseline", "group": "public_examples", "status": "passed",
                "problems": [], "requests": [{"body_sha256": sha_bytes(b"{}"), "qa_status": 200,
                    "baseline_status": 200, "qa_sha256": response_sha, "baseline_sha256": response_sha,
                    "differing_members": [], "qa_assemble_status": 200, "baseline_assemble_status": 200,
                    "files_sha256": {"index.ts": sha_bytes(b"fixture\n")}}]}]}
        for step in ("rt-diagnostics", "ts-target"):
            self.reports[step] = {"passed": True, "status": "passed", "cases": []}

    def _identity(self):
        tool = self.repo / TOOL_REL
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_bytes(b'raise RuntimeError("acceptance tools must never be imported")\n# opaque byte: \xff\n')
        tools = {TOOL_REL.as_posix(): sha_file(tool)}
        binary = self.repo / ".local/candidate/som"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fake candidate; never executable\n")
        binary_sha = sha_file(binary)
        build = {"complete": True, "binary_sha256": binary_sha,
                 "tree_before": {"head": "a" * 40, "porcelain": ""}}
        runtime = {"binary_sha256": binary_sha, "port": 18970}
        save_json(binary.parent / "build.json", build)
        save_json(binary.parent / "runtime.json", runtime)
        def health(role):
            raw = json.dumps({"status": "ok", "role": role}, sort_keys=True)
            value = {"status": 200, "body": json.loads(raw), "raw": raw, "sha256": sha_bytes(raw.encode())}
            return {"before": copy.deepcopy(value), "after": copy.deepcopy(value), "unchanged": True}
        self.identity = {"candidate": {"dir": str(binary.parent), "build": build, "runtime": runtime,
                          "build_sha256": sha_file(binary.parent / "build.json"),
                          "runtime_sha256": sha_file(binary.parent / "runtime.json"),
                          "binary_sha256": binary_sha, "binary_sha256_after": binary_sha, "healthz": health("candidate")},
                         "baseline": {"healthz": health("baseline")}, "8765": {"healthz": health("default")},
                         "git_head": {"before": "a" * 40, "after": "a" * 40, "unchanged": True},
                         "react_output_acceptance_files": {"before": tools, "after": copy.deepcopy(tools), "unchanged": True},
                         "passed": True, "problems": []}
        save_json(self.run / "identity.json", self.identity)

    def refresh_contract_receipts(self):
        files = {str(path.relative_to(self.repo)): sha_file(path) for path in self.contract.rglob("*") if path.is_file()}
        self.contract_files = files
        receipt = {"version": "som.react-output-contract-freeze.v1", "status": "contract_frozen_before_implementation",
                   "files": files, "independent_review": {"reviewed": True}}
        save_json(self.repo / "som-rs/evidence/react-output-contract-freeze-2026-10-22.json", receipt)

    def replace_plan(self, plan):
        """Keep source and raw bundle hashes consistent when a test changes its fake plan."""
        self.plan = copy.deepcopy(plan)
        save_json(self.contract / "acceptance-plan.json", self.plan)
        self.refresh_contract_receipts()
        self.assets["target_contract"]["sha256"] = sha_bytes(canonical(self.contract_files))
        self.bundle["hashes"]["payload_sha256"] = sha_bytes(canonical({key: value for key, value in self.bundle.items()
                                                                       if key != "hashes"}))
        for path in (self.run / "bundles/responses").glob("*__compile_*.json"):
            save_json(path, {"status": "ok", "bundle": self.bundle})
        rust = copy.deepcopy(self.bundle)
        rust["target"] = "rust"
        save_json(self.run / "bundles/responses" / f"bundle_checks__{self.cid}__rust_manifest.json",
                  {"status": "ok", "bundle": rust})
        for row in self.reports["react-target"]["cases"]:
            if row["section"] in ("target_examples", "in_scope"):
                path = self.run / "react-target" / row["response_file"]
                save_json(path, {"status": "ok", "bundle": self.bundle})
                row["response_sha256"] = sha_file(path)
        self.refresh_receipts()

    def report_path(self, step):
        if step == "contract":
            return self.run / "logs/01-contract.stdout"
        if step in CONTROL_STEPS:
            return self.run / f"{step}.json"
        return self.run / step / "report.json"

    def refresh_receipts(self):
        steps = []
        for index, step in enumerate(STEPS, 1):
            stdout = self.run / "logs" / f"{index:02d}-{step}.stdout"
            stderr = self.run / "logs" / f"{index:02d}-{step}.stderr"
            stdout.parent.mkdir(parents=True, exist_ok=True)
            if not stdout.exists():
                stdout.write_text("1 passed, 0 failed\n" if step == "contract" else "fixture log\n")
            if not stderr.exists():
                stderr.write_text("")
            report = self.report_path(step)
            if step != "contract":
                save_json(report, self.reports[step])
            receipt = {"step": step, "index": index, "argv": ["not-executed", "fixture-step"], "cwd": str(self.repo),
                       "started_at": "2026-10-28T00:00:00+00:00", "ended_at": "2026-10-28T00:00:01+00:00",
                       "seconds": 1.0, "exit_code": 0, "stdout": str(stdout), "stdout_sha256": sha_file(stdout),
                       "stderr": str(stderr), "stderr_sha256": sha_file(stderr), "report": str(report),
                       "report_sha256": sha_file(report), "judgment": "passed", "passed": True, "reason": "claimed pass"}
            receipt_path = self.run / "receipts" / f"{index:02d}-{step}.json"
            save_json(receipt_path, receipt)
            steps.append({"step": step, "index": index, "judgment": "passed", "passed": True,
                          "receipt": str(receipt_path), "receipt_sha256": sha_file(receipt_path),
                          "report": str(report), "report_sha256": sha_file(report), "exit_code": 0})
        save_json(self.run / "run.json", {"version": "som.react-output-acceptance-run.v1", "steps": steps,
                                          "identity": str(self.run / "identity.json"),
                                          "identity_sha256": sha_file(self.run / "identity.json"),
                                          "passed": True, "status": "passed", "failed": [], "not_run": []})


class Audit130Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="som-audit130-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = RecordedRun(Path(self.temp.name).resolve() / "repo")

    def run_audit(self, out=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = audit130.main(["--repo", str(self.fixture.repo), "--run", str(self.fixture.run),
                                  "--out", str(out or self.fixture.output)])
        output = out or self.fixture.output
        report = read_json(output) if code != 2 and output.is_file() else None
        return code, report, stdout.getvalue(), stderr.getvalue()

    def assert_totals(self, report):
        counted = Counter(row["status"] for row in report["rows"])
        self.assertEqual(set(report["totals"]), STATUSES)
        self.assertEqual(report["totals"], {status: counted[status] for status in STATUSES})

    def test_supported_fixture_plan_passes_and_tools_are_only_hashed(self):
        code, report, _, error = self.run_audit()
        self.assertEqual(code, 0, error or json.dumps(report, indent=2))
        self.assertEqual(report["version"], "som.react-output-audit-130.v1")
        self.assertEqual(report["run"], self.fixture.run.relative_to(self.fixture.repo).as_posix())
        self.assertEqual(report["plan_sha256"], sha_file(self.fixture.contract / "acceptance-plan.json"))
        self.assertEqual(report["totals"]["failed"], 0)
        self.assertEqual(report["totals"]["unrun"], 0)
        self.assertGreater(report["totals"]["post_acceptance"], 0)
        self.assertGreater(report["totals"]["attested"], 0)
        self.assert_totals(report)
        self.assertEqual(report["files"][TOOL_REL.as_posix()], sha_file(self.fixture.repo / TOOL_REL))
        self.assertEqual(report["files"][(CONTRACT_REL / "acceptance-plan.json").as_posix()], report["plan_sha256"])

    def test_raw_refusal_failure_overrides_all_claimed_passes(self):
        row = next(row for row in self.fixture.reports["react-target"]["cases"] if row["section"] == "negative_cases")
        raw = self.fixture.run / "react-target" / row["response_file"]
        doc = read_json(raw)
        doc["diagnostics"][0]["code"] = "WRONG_RAW_CODE"
        save_json(raw, doc)
        row["response_sha256"] = sha_file(raw)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)
        self.assert_totals(report)

    def test_remaining_real_plan_categories_report_missing_evidence(self):
        # A real-schema baseline report does not retain every comparison byte.
        # Enabling the full plan must not turn that absence into a false pass.
        plan = read_json(SOURCE_REPO / CONTRACT_REL / "acceptance-plan.json")
        self.fixture.replace_plan(plan)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["unrun"], 0)
        ids = {row["id"] for row in report["rows"]}
        for category in ("target_selection_checks", "scope_checks", "bundle_checks"):
            for index in range(1, len(plan[category]) + 1):
                self.assertIn(f"{category}/{index}", ids)
        for category in ("python_unchanged", "rust_unchanged", "typescript_unchanged"):
            for index in range(1, len(plan[category]["checks"]) + 1):
                self.assertIn(f"{category}/{index}", ids)
        for recipe in plan["assemble_refusals"]["recipes"]:
            self.assertIn(f"assemble_refusals/recipes/{recipe['id']}", ids)
        for key in plan["assemble_refusals"]:
            if key != "recipes":
                self.assertIn(f"assemble_refusals/{key}", ids)
        for key in plan["issue_gates"]:
            self.assertIn(f"issue_gates/{key}", ids)
        self.assert_totals(report)

    def test_status_only_typescript_baseline_is_not_attestation(self):
        plan = copy.deepcopy(self.fixture.plan)
        plan["typescript_unchanged"]["checks"] = ["Compare every TypeScript product with the baseline."]
        self.fixture.replace_plan(plan)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        row = next(row for row in report["rows"] if row["id"] == "typescript_unchanged/1")
        self.assertEqual(row["status"], "unrun")

    def test_raw_product_exit_failure_overrides_passed_aggregates(self):
        gates = read_json(self.fixture.gates_path)
        gates["gates"][1]["exit_code"] = 1
        save_json(self.fixture.gates_path, gates)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertTrue(any(row["status"] == "failed" and "product_gates" in row["id"] for row in report["rows"]))

    def test_missing_expected_inventory_case_is_unrun(self):
        self.fixture.reports["bundles"]["cases"] = [row for row in self.fixture.reports["bundles"]["cases"]
                                                       if row["section"] != "bundle_checks"]
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["unrun"], 0)
        self.assert_totals(report)

    def test_case_prefix_does_not_satisfy_exact_inventory_id(self):
        self.fixture.reports["bundles"]["cases"][0]["id"] = "example-v10"
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["unrun"], 0)

    def test_duplicate_case_ids_cannot_replace_complete_results(self):
        rows = self.fixture.reports["bundles"]["cases"]
        rows.append(copy.deepcopy(rows[0]))
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_changed_tool_hash_fails(self):
        tool = self.fixture.repo / TOOL_REL
        tool.write_bytes(tool.read_bytes() + b"# changed after run\n")
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_health_hash_drift_fails(self):
        self.fixture.identity["candidate"]["healthz"]["after"]["sha256"] = "0" * 64
        save_json(self.fixture.run / "identity.json", self.fixture.identity)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_health_content_drift_cannot_hide_behind_unchanged_hash(self):
        self.fixture.identity["8765"]["healthz"]["after"]["raw"] = '{"different":"raw body"}'
        save_json(self.fixture.run / "identity.json", self.fixture.identity)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_log_checksum_mismatch_fails(self):
        log = self.fixture.run / "logs/02-react-target.stdout"
        log.write_text("tampered after receipt\n")
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_report_checksum_mismatch_fails(self):
        report_path = self.fixture.report_path("react-target")
        report_path.write_text(report_path.read_text() + "\n")
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_step_order_mismatch_fails(self):
        path = self.fixture.run / "run.json"
        doc = read_json(path)
        doc["steps"][0], doc["steps"][1] = doc["steps"][1], doc["steps"][0]
        save_json(path, doc)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"], 0)

    def test_missing_raw_product_evidence_is_unrun(self):
        self.fixture.gates_path.unlink()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["unrun"], 0)

    def test_malformed_raw_product_evidence_cannot_pass(self):
        self.fixture.gates_path.write_text("{broken")
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertGreater(report["totals"]["failed"] + report["totals"]["unrun"], 0)

    def test_existing_output_is_refused_without_overwrite(self):
        self.fixture.output.parent.mkdir(parents=True)
        self.fixture.output.write_text("preserve this existing output\n")
        before = self.fixture.output.read_bytes()
        code, _, _, error = self.run_audit()
        self.assertEqual(code, 2, error)
        self.assertEqual(self.fixture.output.read_bytes(), before)

    def test_output_traversal_is_refused(self):
        output = self.fixture.repo / ".." / "escape.json"
        code, _, _, error = self.run_audit(output)
        self.assertEqual(code, 2, error)
        self.assertFalse(output.exists())

    def test_symlink_output_parent_is_refused(self):
        target = self.fixture.repo / ".local/actual-output"
        target.mkdir(parents=True)
        link = self.fixture.repo / ".local/linked-output"
        link.symlink_to(target, target_is_directory=True)
        code, _, _, error = self.run_audit(link / "report.json")
        self.assertEqual(code, 2, error)
        self.assertFalse((target / "report.json").exists())

    def test_raw_evidence_cannot_escape_run_through_symlink(self):
        target = self.fixture.repo / "outside-evidence.json"
        shutil.copyfile(self.fixture.gates_path, target)
        self.fixture.gates_path.unlink()
        self.fixture.gates_path.symlink_to(target)
        code, report, _, _ = self.run_audit()
        self.assertIn(code, (1, 2))
        if report is not None:
            self.assertGreater(report["totals"]["failed"] + report["totals"]["unrun"], 0)

    def test_schema_prefix_position_rejects_wrong_const(self):
        schema = {"$id": "fixture.tuple", "type": "array", "minItems": 2, "maxItems": 2,
                  "prefixItems": [{"const": "resource"}, {"type": "integer"}]}
        validator = audit130.Schema(audit130.Reader(self.fixture.repo), [schema])
        validator.validate(["resource", 4], schema)
        with self.assertRaises(audit130.EvidenceError):
            validator.validate(["flow", 4], schema)
        with self.assertRaises(audit130.EvidenceError):
            validator.validate(["resource", "four"], schema)

    def test_schema_field_default_obeys_required_and_optional_conditions(self):
        schema = {"$id": "fixture.field", "type": "object", "required": ["required"],
                  "properties": {"required": {"type": "boolean"}},
                  "if": {"properties": {"required": {"const": True}}, "required": ["required"]},
                  "then": {"not": {"required": ["default"]}},
                  "else": {"properties": {"default": {"type": "string"}}}}
        validator = audit130.Schema(audit130.Reader(self.fixture.repo), [schema])
        validator.validate({"required": True}, schema)
        validator.validate({"required": False, "default": "optional"}, schema)
        with self.assertRaises(audit130.EvidenceError):
            validator.validate({"required": True, "default": "not permitted"}, schema)
        with self.assertRaises(audit130.EvidenceError):
            validator.validate({"required": False, "default": 7}, schema)

    def test_schema_property_names_rejects_an_illegal_wire_name(self):
        schema = {"$id": "fixture.names", "type": "object",
                  "propertyNames": {"pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}}
        validator = audit130.Schema(audit130.Reader(self.fixture.repo), [schema])
        validator.validate({"title": "valid", "_other": 1}, schema)
        with self.assertRaises(audit130.EvidenceError):
            validator.validate({"bad-name": "must not pass"}, schema)


if __name__ == "__main__":
    unittest.main()
