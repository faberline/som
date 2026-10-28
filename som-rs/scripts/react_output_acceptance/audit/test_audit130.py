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
        save_json(self.repo / "som-rs/tests/compiler-v1/cnl-positive-fixtures.json", {"cases": []})
        save_json(self.repo / "som-rs/tests/rust-output-v1/cases/index.json", {"cases": []})
        # This fake repository declares no public-product or historical-job
        # source universe. Its one public example still has both real forms.
        inventory["sources"].pop("legacy_inventory", None)
        inventory["sources"].pop("public_products", None)
        for key, relative in (
            ("cnl_positive_fixtures", "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"),
            ("coverage_index", "som-rs/tests/rust-output-v1/cases/index.json"),
        ):
            inventory["sources"][key] = {relative: sha_file(self.repo / relative)}
        save_json(self.contract / "case-inventory.json", inventory)
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
        python_doc = {"status": "ok", "bundle": {"version": "som.dsl-bundle.v1"}}
        for example in self.docs["target-examples.json"]["examples"]:
            request = json.loads(example["body"])
            form = "cnl" if request["format"] == "som.cnl.v1" else "json"
            pair_id = self.cid + f"-{form}-python-equivalence"
            request["target"] = "python"
            pair = self._raw_row("react-target", "python_equivalence", pair_id,
                                 canonical(request).decode(), python_doc,
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

    def test_incomplete_legacy_fixture_blocks_acceptance_and_tools_are_only_hashed(self):
        code, report, _, error = self.run_audit()
        self.assertEqual(code, 1, error or json.dumps(report, indent=2))
        self.assertEqual(report["version"], "som.react-output-audit-130.v1")
        self.assertEqual(report["run"], self.fixture.run.relative_to(self.fixture.repo).as_posix())
        self.assertEqual(report["plan_sha256"], sha_file(self.fixture.contract / "acceptance-plan.json"))
        self.assertEqual(report["totals"]["failed"], 0)
        self.assertGreater(report["totals"]["unrun"], 0)
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

    def _typescript_baseline_raw_pair(self):
        """Retain both request forms and every HTTP body in a reduced run."""
        inventory = read_json(SOURCE_REPO / "som-rs/contracts/typescript-output-v1/case-inventory.json")
        case = copy.deepcopy(inventory["public_examples"][0])
        inventory = {"sources": inventory["sources"], "public_examples": [case],
                     "version_positive_fixtures": [], "coverage_cases": [],
                     "name_cases": [], "legacy_in_scope": []}
        save_json(self.fixture.repo / "som-rs/contracts/typescript-output-v1/case-inventory.json", inventory)
        root = self.fixture.run / "ts-baseline"
        source = (self.fixture.repo / case["cnl"]).read_text()
        bodies = {"json": {"format": "som.prompt-json.v1", "prompt": self.fixture.prompt, "target": "typescript"},
                  "cnl": {"format": "som.cnl.v1", "source": source, "target": "typescript"}}
        bundles = {}
        for side in ("qa", "baseline"):
            bundle = copy.deepcopy(self.fixture.bundle)
            bundle["target"] = "typescript"
            bundle["dsl"]["version"] = "som.dsl.typescript.v1"
            bundle["assets"]["backend"] = {"id": f"fixture.typescript.{side}", "sha256": sha_bytes(side.encode())}
            bundle["assets"]["target_contract"] = {"id": f"fixture.contract.{side}", "sha256": sha_bytes(side.encode())}
            bundle["hashes"]["payload_sha256"] = sha_bytes(canonical(bundle))
            bundles[side] = bundle

        def retain(record, file_key, hash_key, relative, document):
            path = root / relative
            save_json(path, document)
            record[file_key], record[hash_key] = relative, sha_file(path)

        requests = []
        files = {"index.ts": "export const fixture = 'same bytes';\n", "unicode.txt": "測試\r\n"}
        for index, (form, body) in enumerate(bodies.items()):
            record = {"form": form, "qa_status": 200, "baseline_status": 200,
                      "qa_assemble_status": 200, "baseline_assemble_status": 200,
                      "differing_members": [], "passed": True,
                      "files_sha256": {name: sha_bytes(text.encode()) for name, text in files.items()},
                      "baseline_files_sha256": {name: sha_bytes(text.encode()) for name, text in files.items()}}
            stem = f"{case['id']}/{index}-{form}"
            retain(record, "request_file", "body_sha256", f"requests/{stem}.compile.json", body)
            for side in ("qa", "baseline"):
                retain(record, f"{side}_response_file", f"{side}_sha256", f"responses/{stem}.{side}.compile.json",
                       {"status": "ok", "bundle": bundles[side]})
                retain(record, f"{side}_assemble_request_file", f"{side}_assemble_request_sha256",
                       f"requests/{stem}.{side}.assemble.json", {"bundle": bundles[side]})
                retain(record, f"{side}_assemble_response_file", f"{side}_assemble_sha256",
                       f"responses/{stem}.{side}.assemble.json", {"status": "ok", "files": files})
            requests.append(record)
        row = {"id": case["id"], "group": "public_examples", "status": "passed", "passed": True,
               "problems": [], "requests": requests}
        self.fixture.reports["ts-baseline"] = {"version": "som.react-output-typescript-baseline.v1",
            "passed": True, "status": "passed", "cases": [row]}
        plan = copy.deepcopy(self.fixture.plan)
        plan["typescript_unchanged"]["checks"] = ["Compare every TypeScript product with the baseline."]
        self.fixture.replace_plan(plan)
        self.fixture.refresh_receipts()
        return row

    def _typescript_baseline_result(self):
        self._typescript_audit_count = getattr(self, "_typescript_audit_count", 0) + 1
        out = self.fixture.output.with_name(f"typescript-{self._typescript_audit_count}.json")
        code, report, _, _ = self.run_audit(out=out)
        return code, next(row for row in report["rows"] if row["id"] == "typescript_unchanged/1")

    def test_typescript_raw_compile_and_assemble_pair_is_independently_passed(self):
        self._typescript_baseline_raw_pair()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1, row)  # Other legacy fixture gates lack raw operational evidence.
        self.assertEqual(row["status"], "passed", row)
        self.assertNotIn("attestations", row)

    def test_typescript_raw_compile_drift_fails_with_all_hashes_refreshed(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        path = self.fixture.run / "ts-baseline" / record["baseline_response_file"]
        document = read_json(path)
        document["unexpected_member"] = "raw drift"
        save_json(path, document)
        record["baseline_sha256"] = sha_file(path)
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("outside the three allowed", row["reason"])

    def test_typescript_raw_compile_comparison_keeps_exact_json_types(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        for side, value in (("qa", 1), ("baseline", True)):
            path = self.fixture.run / "ts-baseline" / record[f"{side}_response_file"]
            document = read_json(path)
            document["type_check"] = value
            save_json(path, document)
            record[f"{side}_sha256"] = sha_file(path)
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("outside the three allowed", row["reason"])

    def test_typescript_raw_file_drift_fails_with_both_file_maps_refreshed(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        path = self.fixture.run / "ts-baseline" / record["baseline_assemble_response_file"]
        document = read_json(path)
        document["files"]["unicode.txt"] = "測試\n"
        save_json(path, document)
        record["baseline_assemble_sha256"] = sha_file(path)
        record["baseline_files_sha256"] = {name: sha_bytes(text.encode()) for name, text in document["files"].items()}
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("names or bytes differ", row["reason"])

    def test_typescript_missing_raw_exchange_is_unrun(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        fields = ("request_file", "qa_response_file", "baseline_response_file",
                  "qa_assemble_request_file", "baseline_assemble_request_file",
                  "qa_assemble_response_file", "baseline_assemble_response_file")
        for field in fields:
            with self.subTest(field=field):
                relative = record.pop(field)
                self.fixture.refresh_receipts()
                code, row = self._typescript_baseline_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "unrun", row)
                record[field] = relative
        path = self.fixture.run / "ts-baseline" / record["qa_response_file"]
        path.unlink()
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "unrun", row)

    def test_typescript_raw_hash_drift_is_failed(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        path = self.fixture.run / "ts-baseline" / record["qa_response_file"]
        path.write_bytes(path.read_bytes() + b"\n")
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("sha256 differs", row["reason"])

    def test_typescript_compile_request_must_match_target_and_public_source(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        path = self.fixture.run / "ts-baseline" / record["request_file"]
        original = read_json(path)
        for mutation in ("target", "prompt", "format"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(original)
                if mutation == "target":
                    document["target"] = "react"
                elif mutation == "prompt":
                    document["prompt"]["resource"]["fields"][0]["name"] = "substituted_name"
                else:
                    document["format"] = "som.cnl.v1"
                save_json(path, document)
                record["body_sha256"] = sha_file(path)
                self.fixture.refresh_receipts()
                code, row = self._typescript_baseline_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "failed", row)

    def test_typescript_assemble_request_must_use_its_own_compiled_bundle(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        root = self.fixture.run / "ts-baseline"
        path = root / record["qa_assemble_request_file"]
        save_json(path, read_json(root / record["baseline_assemble_request_file"]))
        record["qa_assemble_request_sha256"] = sha_file(path)
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("its own compiled bundle", row["reason"])

    def test_typescript_exchange_statuses_must_all_be_http_200(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        for field in ("qa_status", "baseline_status", "qa_assemble_status", "baseline_assemble_status"):
            with self.subTest(field=field):
                record[field] = 201
                self.fixture.refresh_receipts()
                code, row = self._typescript_baseline_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "failed", row)
                record[field] = 200

    def test_typescript_matching_recorded_file_hashes_must_match_raw_bytes(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        record["files_sha256"]["index.ts"] = "e" * 64
        record["baseline_files_sha256"]["index.ts"] = "e" * 64
        self.fixture.refresh_receipts()
        code, row = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("hashes differ from raw bytes", row["reason"])

    def test_typescript_raw_exchange_cannot_reference_another_run(self):
        record = self._typescript_baseline_raw_pair()["requests"][0]
        original = record["qa_response_file"]
        outside = self.fixture.run.parent / "other-run/ts-baseline/response.json"
        outside.parent.mkdir(parents=True)
        shutil.copyfile(self.fixture.run / "ts-baseline" / original, outside)
        for relative in (str(outside), "../../other-run/ts-baseline/response.json"):
            with self.subTest(relative=relative):
                record["qa_response_file"] = relative
                self.fixture.refresh_receipts()
                code, row = self._typescript_baseline_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "failed", row)

    def test_typescript_public_source_requires_both_forms(self):
        row = self._typescript_baseline_raw_pair()
        row["requests"] = row["requests"][:1]
        self.fixture.refresh_receipts()
        code, result = self._typescript_baseline_result()
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "unrun", result)
        self.assertIn("request forms", result["reason"])

    def test_raw_product_exit_failure_overrides_passed_aggregates(self):
        gates = read_json(self.fixture.gates_path)
        gates["gates"][1]["exit_code"] = 1
        save_json(self.fixture.gates_path, gates)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertTrue(any(row["status"] == "failed" and "product_gates" in row["id"] for row in report["rows"]))

    def test_agreeing_cli_hashes_must_match_frozen_request_bytes(self):
        row = self.fixture.reports["react-target"]["cli"]["rows"][0]
        row["request_sha256"] = "e" * 64
        row["expected_request_sha256"] = "e" * 64
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        result = next(row for row in report["rows"] if row["id"] == "target_selection_checks/4")
        self.assertEqual(result["status"], "failed")

    def test_reported_exit_without_raw_os_evidence_is_unrun(self):
        api = read_json(self.fixture.api_path)
        api["processes"][0].update(stopped=True, exit_code=0, stop_cwd_verified=None)
        save_json(self.fixture.api_path, api)
        self.fixture.refresh_receipts()
        code, report, _, error = self.run_audit()
        self.assertEqual(code, 1, error or json.dumps(report, indent=2))
        result = next(row for row in report["rows"] if row["id"] == f"product_gates/{self.fixture.cid}/7")
        self.assertEqual(result["status"], "unrun")

    def test_active_process_stop_without_owner_snapshot_fails(self):
        api = read_json(self.fixture.api_path)
        api["processes"][0].update(stopped=True, exit_code=None, stop_cwd_verified=False)
        save_json(self.fixture.api_path, api)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        result = next(row for row in report["rows"] if row["id"] == f"product_gates/{self.fixture.cid}/7")
        self.assertEqual(result["status"], "failed")

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

    def test_omitted_out_of_scope_cnl_fixture_has_its_own_unrun_row(self):
        relative = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
        prompt = read_json(self.fixture.repo / "som-rs/contracts/compiler-v1/examples/v5.prompt.json")
        source = (self.fixture.repo / "som-rs/contracts/compiler-v1/examples/v5.cnl").read_text()
        case = {"id": "OOS_FIXTURE", "category": "boundary_positive",
                "expected": {"status": "accept"}, "expected_prompt": prompt, "cnl": source,
                "prompt_sha256": sha_bytes(canonical(prompt)), "cnl_sha256": sha_bytes(source.encode())}
        save_json(self.fixture.repo / relative, {"cases": [case]})
        inventory = self.fixture.docs["case-inventory.json"]
        inventory["sources"]["cnl_positive_fixtures"] = {relative: sha_file(self.fixture.repo / relative)}
        save_json(self.fixture.contract / "case-inventory.json", inventory)
        # Supply its required Python pair. Only its React scope result is absent.
        row_id = case["id"] + "-cnl-python-equivalence"
        body = canonical({"format": "som.cnl.v1", "source": source, "target": "python"}).decode()
        python_response = {"status": "ok", "bundle": {"version": "som.dsl-bundle.v1", "prompt": prompt}}
        row = self.fixture._raw_row("react-target", "python_equivalence", row_id, body, python_response,
                                    stem=f"python_equivalence__{row_id}__python")
        save_json(self.fixture.run / "react-target/responses" / f"python_equivalence__{row_id}__no_target.json",
                  python_response)
        self.fixture.reports["react-target"]["cases"].append(row)
        self.fixture.replace_plan(self.fixture.plan)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertEqual(report["totals"]["failed"], 0, json.dumps(report, indent=2))
        scope = next(row for row in report["rows"] if row["id"] == "scope_case/OOS_FIXTURE")
        self.assertEqual(scope["status"], "unrun")
        python = next(row for row in report["rows"] if row["id"] == f"python_pair/{row_id}")
        self.assertEqual(python["status"], "passed")
        self.assert_totals(report)

    def test_omitted_out_of_scope_legacy_job_has_its_own_unrun_row(self):
        prompt = read_json(self.fixture.repo / "som-rs/contracts/compiler-v1/examples/v5.prompt.json")
        directory = self.fixture.repo / ".local/legacy-universe/run"
        record = {"id": "OOS_LEGACY", "prompt_sha256": sha_bytes(canonical(prompt))}
        save_json(directory / "result.json", {"records": [record]})
        pinned = directory.parent / "inventory-003.json"
        save_json(pinned, {"groups": [record]})
        save_json(directory / "0000/compile-json.request.json", {"format": "som.prompt-json.v1", "prompt": prompt})
        relative = pinned.relative_to(self.fixture.repo).as_posix()
        inventory = self.fixture.docs["case-inventory.json"]
        inventory["sources"]["legacy_inventory"] = {
            relative: sha_file(pinned),
            "run": directory.relative_to(self.fixture.repo).as_posix(),
        }
        save_json(self.fixture.contract / "case-inventory.json", inventory)
        self.fixture.replace_plan(self.fixture.plan)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertEqual(report["totals"]["failed"], 0, json.dumps(report, indent=2))
        scope = next(row for row in report["rows"] if row["id"] == "scope_case/OOS_LEGACY")
        self.assertEqual(scope["status"], "unrun")
        self.assert_totals(report)

    def test_response_from_another_run_cannot_supply_this_case(self):
        row = self.fixture.reports["react-target"]["cases"][0]
        source = self.fixture.run / "react-target" / row["response_file"]
        other = self.fixture.repo / ".local/another-run/react-target/responses/copied.json"
        other.parent.mkdir(parents=True)
        shutil.copyfile(source, other)
        row["response_file"] = str(other)
        row["response_sha256"] = sha_file(other)
        self.fixture.refresh_receipts()
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        target = next(row for row in report["rows"]
                      if row["id"] == "target_case/target_examples/example-v1-cnl-react")
        self.assertEqual(target["status"], "failed")

    def _python_baseline_source_universes(self, public_ids):
        """Declare source names and prompts before writing mutable result.records."""
        public = self.fixture.repo / ".local/baseline-public"
        legacy = self.fixture.repo / ".local/baseline-legacy/run"
        fixture_path = self.fixture.repo / "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
        source_cases = read_json(SOURCE_REPO / "som-rs/tests/compiler-v1/cnl-positive-fixtures.json")["cases"]
        first, second = copy.deepcopy(source_cases[0]), copy.deepcopy(source_cases[0])
        first["id"], second["id"] = "PUBLIC_FIXTURE_A", "PUBLIC_FIXTURE_B"
        save_json(fixture_path, {"cases": [first, second]})
        index_path = self.fixture.repo / "som-rs/contracts/compiler-v1/examples/index.json"
        index = read_json(index_path)
        index["cases"] = [index["cases"][0]]
        save_json(index_path, index)
        prompts = {first["id"]: first["expected_prompt"], second["id"]: second["expected_prompt"],
                   "PUBLIC_v1": self.fixture.prompt, "legacy-fixture": self.fixture.prompt}
        for position, cid in enumerate((first["id"], second["id"], "PUBLIC_v1")):
            save_json(public / f"{position:03d}/prompt.json", prompts[cid])
            save_json(public / f"{position:03d}/compile-json.request.json",
                      {"format": "som.prompt-json.v1", "prompt": prompts[cid]})
        save_json(public / "result.json", {"records": [{"id": cid, "prompt_sha256": sha_bytes(canonical(prompts[cid]))}
                                                         for cid in public_ids]})
        legacy_record = {"id": "legacy-fixture", "prompt_sha256": sha_bytes(canonical(self.fixture.prompt))}
        save_json(legacy / "result.json", {"records": [legacy_record]})
        save_json(legacy / "0000/compile-json.request.json", {"format": "som.prompt-json.v1", "prompt": self.fixture.prompt})
        pinned = legacy.parent / "inventory-003.json"
        save_json(pinned, {"groups": [legacy_record]})
        inventory = self.fixture.docs["case-inventory.json"]
        inventory["sources"]["public_products"] = {"directory": public.relative_to(self.fixture.repo).as_posix()}
        inventory["sources"]["legacy_inventory"] = {
            pinned.relative_to(self.fixture.repo).as_posix(): sha_file(pinned),
            "run": legacy.relative_to(self.fixture.repo).as_posix(),
        }
        inventory["sources"]["cnl_positive_fixtures"] = {
            fixture_path.relative_to(self.fixture.repo).as_posix(): sha_file(fixture_path)}
        inventory["sources"]["examples"][index_path.relative_to(self.fixture.repo).as_posix()] = sha_file(index_path)
        save_json(self.fixture.contract / "case-inventory.json", inventory)
        rows = []
        for source, ids in (("public", sorted(set(public_ids))), ("legacy", ["legacy-fixture"])):
            for cid in ids:
                for variant in ("none", "python"):
                    request = {"format": "som.prompt-json.v1", "prompt": prompts[cid]}
                    if variant == "python":
                        request["target"] = "python"
                    request_sha = sha_bytes(canonical(request))
                    rows.append({"source": source, "id": cid, "variant": variant,
                                 "compile_request_sha256": request_sha,
                                 "qa_compile_request_sha256": request_sha,
                                 "baseline_compile_request_sha256": request_sha})
        return rows

    def test_complete_small_python_baseline_source_inventory_is_accepted(self):
        rows = self._python_baseline_source_universes(["PUBLIC_FIXTURE_A", "PUBLIC_FIXTURE_B", "PUBLIC_v1"])
        auditor = audit130.Audit(self.fixture.repo, self.fixture.run)
        auditor.read_contracts()
        auditor.baseline_inventory("python", rows)

    def test_duplicate_or_truncated_public_source_cannot_reduce_python_baseline(self):
        for public_ids in (["PUBLIC_FIXTURE_A"] * 3, ["PUBLIC_FIXTURE_A"]):
            with self.subTest(public_ids=public_ids):
                rows = self._python_baseline_source_universes(public_ids)
                auditor = audit130.Audit(self.fixture.repo, self.fixture.run)
                auditor.read_contracts()
                with self.assertRaises(audit130.EvidenceError):
                    auditor.baseline_inventory("python", rows)

    def test_public_baseline_same_version_prompt_substitution_is_rejected(self):
        ids = ["PUBLIC_FIXTURE_A", "PUBLIC_FIXTURE_B", "PUBLIC_v1"]
        rows = self._python_baseline_source_universes(ids)
        directory = self.fixture.repo / ".local/baseline-public"
        prompt = read_json(directory / "000/prompt.json")
        prompt["resource"]["fields"][0]["name"] = "substituted_title"
        save_json(directory / "000/prompt.json", prompt)
        save_json(directory / "000/compile-json.request.json", {"format": "som.prompt-json.v1", "prompt": prompt})
        result = read_json(directory / "result.json")
        result["records"][0]["prompt_sha256"] = sha_bytes(canonical(prompt))
        save_json(directory / "result.json", result)
        for row in rows:
            if row["source"] == "public" and row["id"] == ids[0]:
                request = {"format": "som.prompt-json.v1", "prompt": prompt}
                if row["variant"] == "python":
                    request["target"] = "python"
                digest = sha_bytes(canonical(request))
                for field in ("compile_request_sha256", "qa_compile_request_sha256", "baseline_compile_request_sha256"):
                    row[field] = digest
        auditor = audit130.Audit(self.fixture.repo, self.fixture.run)
        auditor.read_contracts()
        with self.assertRaises(audit130.EvidenceError):
            auditor.baseline_inventory("python", rows)

    def test_agreeing_python_baseline_request_hashes_must_match_source(self):
        rows = self._python_baseline_source_universes(["PUBLIC_FIXTURE_A", "PUBLIC_FIXTURE_B", "PUBLIC_v1"])
        for field in ("compile_request_sha256", "qa_compile_request_sha256", "baseline_compile_request_sha256"):
            rows[0][field] = "e" * 64
        auditor = audit130.Audit(self.fixture.repo, self.fixture.run)
        auditor.read_contracts()
        with self.assertRaises(audit130.EvidenceError):
            auditor.baseline_inventory("python", rows)

    def test_mutable_rust_phase1_record_cannot_drop_frozen_baseline_cases(self):
        frozen_ids = ["RUST_FROZEN_A", "RUST_FROZEN_B"]
        save_json(self.fixture.repo / "som-rs/contracts/rust-output-v1/case-inventory.json", {
            "public_examples": [{"id": cid, "rust": "in_scope"} for cid in frozen_ids],
            "version_positive_fixtures": [], "coverage_cases": [], "legacy_in_scope": [],
            "counts": {"acceptance_total": 2},
        })
        phase2_id = "RUST_PHASE2"
        save_json(self.fixture.repo / "som-rs/contracts/rust-output-v1/phase2/case-inventory.json",
                  {"rows": [{"id": phase2_id, "phase2_expected": {"result": "product"}}]})
        record_path = self.fixture.repo / ".local/rust-compiler-goal/acceptance/rust-output-055/run-003/rust-products.json"
        for mutable_ids in ([], [frozen_ids[0]], [frozen_ids[0], frozen_ids[0]]):
            with self.subTest(mutable_ids=mutable_ids):
                save_json(record_path, {"cases": [{"id": cid} for cid in mutable_ids]})
                rows = [{"phase": phase, "id": cid, "check": check}
                        for phase, ids in (("phase1", sorted(set(mutable_ids))), ("phase2", [phase2_id]))
                        for cid in ids for check in ("compile_equal", "baseline_bundle_refused", "rebased_files")]
                auditor = audit130.Audit(self.fixture.repo, self.fixture.run)
                auditor.read_contracts()
                with self.assertRaises(audit130.EvidenceError):
                    auditor.baseline_inventory("rust", rows)

    def test_changed_identity_metadata_with_stale_run_hash_fails(self):
        path = self.fixture.run / "identity.json"
        identity = read_json(path)
        health_before = copy.deepcopy(identity["candidate"]["healthz"])
        identity["metadata_note"] = "changed after run.json was written"
        save_json(path, identity)
        # Keep run.identity_sha256 unchanged to isolate its stale identity binding.
        self.assertEqual(identity["candidate"]["healthz"], health_before)
        code, report, _, _ = self.run_audit()
        self.assertEqual(code, 1)
        self.assertTrue(any(row["status"] == "failed" and "identity" in row["id"]
                            for row in report["rows"]), json.dumps(report, indent=2))

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

    def _absent_protected_port(self):
        point = {"url": "http://127.0.0.1:8765/healthz", "status": None,
                 "body": None, "raw": None, "sha256": None,
                 "error": "ConnectionRefusedError: synthetic connection refused",
                 "error_kind": "connection_refused", "error_type": "ConnectionRefusedError",
                 "error_errno": 111}
        role = {"url": "http://127.0.0.1:8765", "expected_state": "absent",
                "healthz": {"before": copy.deepcopy(point), "after": copy.deepcopy(point),
                            "unchanged": True}}
        self.fixture.identity["8765"] = role
        return role

    def _protected_port_result(self):
        save_json(self.fixture.run / "identity.json", self.fixture.identity)
        self.fixture.refresh_receipts()
        self._protected_port_audit_count = getattr(self, "_protected_port_audit_count", 0) + 1
        out = self.fixture.output.with_name(f"protected-port-{self._protected_port_audit_count}.json")
        code, report, _, _ = self.run_audit(out=out)
        return code, next(row for row in report["rows"] if row["id"] == "integrity/health/8765")

    def test_explicit_absent_protected_port_passes_from_both_refusal_snapshots(self):
        role = self._absent_protected_port()
        # The audit derives the result instead of using the runner's verdict.
        role["healthz"]["unchanged"] = False
        for number, error_type in ((61, "ConnectionRefusedError"), (111, "OSError")):
            with self.subTest(number=number, error_type=error_type):
                for point in (role["healthz"]["before"], role["healthz"]["after"]):
                    point["error_errno"], point["error_type"] = number, error_type
                code, row = self._protected_port_result()
                self.assertEqual(code, 1, row)  # Other operational fixture evidence is incomplete.
                self.assertEqual(row["status"], "passed", row)
        for point in (role["healthz"]["before"], role["healthz"]["after"]):
            del point["url"]
        code, row = self._protected_port_result()
        self.assertEqual(code, 1, row)
        self.assertEqual(row["status"], "passed", row)

    def test_absent_protected_port_becoming_http_200_fails(self):
        role = self._absent_protected_port()
        raw = '{"status":"ok"}'
        role["healthz"]["after"] = {"url": "http://127.0.0.1:8765/healthz", "status": 200,
            "body": json.loads(raw), "raw": raw, "sha256": sha_bytes(raw.encode()), "error": None}
        code, row = self._protected_port_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)

    def test_absent_protected_port_timeout_is_not_connection_refusal(self):
        role = self._absent_protected_port()
        role["healthz"]["after"].update(error_kind="timeout", error_type="TimeoutError",
                                        error_errno=None, error="TimeoutError: synthetic timeout")
        code, row = self._protected_port_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("connection refusal", row["reason"])

    def test_absent_protected_port_cannot_use_only_a_forged_unchanged_boolean(self):
        role = self._absent_protected_port()
        for point in (role["healthz"]["before"], role["healthz"]["after"]):
            for key in ("error_kind", "error_errno", "error_type"):
                del point[key]
        code, row = self._protected_port_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("connection refusal", row["reason"])

    def test_healthy_protected_port_mode_rejects_connection_refusal(self):
        role = self._absent_protected_port()
        del role["expected_state"]
        code, row = self._protected_port_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("HTTP status is not 200", row["reason"])

    def test_absent_protected_port_requires_the_exact_loopback_url(self):
        for place, value in (("role", "http://example.invalid:8765"),
                             ("before", "http://127.0.0.1:8766/healthz"),
                             ("after", "http://localhost:8765/healthz"),
                             ("after", None)):
            with self.subTest(place=place, value=value):
                role = self._absent_protected_port()
                target = role if place == "role" else role["healthz"][place]
                target["url"] = value
                code, row = self._protected_port_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "failed", row)
                self.assertIn("URL differs", row["reason"])

    def test_absent_protected_port_requires_exact_error_and_null_response_fields(self):
        mutations = (("error_errno", True), ("error_errno", 111.0), ("error_errno", 110),
                     ("error_type", "TimeoutError"), ("error", ""), ("status", 200),
                     ("body", {}), ("raw", ""), ("sha256", "0" * 64))
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                role = self._absent_protected_port()
                role["healthz"]["after"][field] = value
                code, row = self._protected_port_result()
                self.assertEqual(code, 1)
                self.assertEqual(row["status"], "failed", row)
        role = self._absent_protected_port()
        del role["healthz"]["after"]["raw"]
        code, row = self._protected_port_result()
        self.assertEqual(code, 1)
        self.assertEqual(row["status"], "failed", row)

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
