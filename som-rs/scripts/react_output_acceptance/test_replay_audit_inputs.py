"""Synthetic retained bytes only. No compiler, gate, or service is executed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("m6_replay_inputs", Path(__file__).with_name("replay_audit_inputs.py"))
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


class Fixture:
    def __init__(self, repo):
        self.repo = repo
        repo.mkdir()
        self.raw = repo / ".local/m6-hosted/acceptance"
        self.out = repo / ".local/replay"
        self.out.mkdir(parents=True)
        self.prepare_out = self.out / "prepared.json"
        self.audit_path = self.out / "audit.json"
        self.source_path = self.out / "auditor-source.json"
        self.final_out = self.out / "summary.json"
        self.head = helper.SOURCE_HEAD
        self.binary = b"fake candidate: never executed\n"
        self.binary_sha = helper.sha(self.binary)
        self.candidate = self.raw / "candidate"
        self.write(self.candidate / "som", self.binary)
        source = {"version": "som.compiler-candidate-source.v1", "commit": self.head, "files": {}}
        self.write(self.candidate / "source.json", source)
        self.build = {"version": "som.rust-output-candidate-build.v1", "complete": True,
                      "source_unchanged": True, "binary_sha256": self.binary_sha,
                      "source_sha256": self.hash(self.candidate / "source.json"),
                      "tree_before": {"head": self.head, "porcelain": ""},
                      "tree_after": {"head": self.head, "porcelain": ""}}
        self.runtime = {"accepted": False, "binary_sha256": self.binary_sha,
                        "source_sha256": self.build["source_sha256"], "port": 18919}
        self.write(self.candidate / "build.json", self.build)
        self.write(self.candidate / "runtime.json", self.runtime)
        self.identity = {"candidate": {"dir": str(self.candidate), "build": self.build, "runtime": self.runtime,
                          "build_sha256": self.hash(self.candidate / "build.json"),
                          "runtime_sha256": self.hash(self.candidate / "runtime.json"),
                          "binary_sha256": self.binary_sha, "binary_sha256_after": self.binary_sha},
                         "git_head": {"before": self.head, "after": self.head}, "passed": True, "problems": []}
        self.write(self.raw / "run/identity.json", self.identity)
        self.run = {"version": "som.react-output-acceptance-run.v1", "passed": True, "status": "passed",
                    "identity_passed": True, "candidate": str(self.candidate),
                    "identity": str(self.raw / "run/identity.json"),
                    "identity_sha256": self.hash(self.raw / "run/identity.json"),
                    "failed": [], "problems": [], "not_run": [],
                    "steps": [{"step": name, "index": index, "judgment": "passed", "passed": True, "exit_code": 0}
                              for index, name in enumerate(helper.STEPS, 1)]}
        self.write(self.raw / "run/run.json", self.run)
        self.summary = {"version": "som.m6-hosted-acceptance.v1", "passed": False, "status": "failed",
                        "failed_stage": "audit", "acceptance_exit_code": 0, "audit_exit_code": 1,
                        "head_before": self.head, "head_after": self.head,
                        "candidate_binary_sha256": self.binary_sha, "baseline_binary_sha256": helper.BASELINE_SHA,
                        "candidate_assets_match_bootstrap": True, "step_count": 12, "product_suites": "recorded",
                        "cleanup": {"baseline_stopped": True, "candidate": {
                            "binary_sha256": self.binary_sha, "ownership_verified": True, "stopped": True}}}
        cases, frozen, records = [], [], []
        self.prompts = {}
        self.name_root = ".local/react-output/review134-names-001"
        for index in range(1, 10):
            identity = f"RN{index:02d}_FAKE"
            prompt = {"version": "fake.prompt.v1", "zeta": index, "alpha": "public é"}
            self.prompts[identity] = encoded(prompt)
            control = {"format": "som.prompt-json.v1", "prompt": prompt} if index % 2 else {
                "format": "som.cnl.v1", "source": f"Fake public CNL {index}."}
            request = json.dumps(control)
            response = encoded({"status": "ok", "bundle": {"prompt": prompt}})
            directory = f"name-cases/{identity}"
            request_file, response_file = directory + "/compile.request.json", directory + "/compile.response.json"
            self.write(self.raw / "reconstructed-inputs" / request_file, request.encode())
            self.write(self.raw / "reconstructed-inputs" / response_file, response)
            cases.append({"id": identity, "python_control_body": request})
            frozen.append({"id": identity, "prompt_file_sha256": helper.sha(encoded(prompt))})
            records.append({"id": identity, "path": f"{self.name_root}/{identity}/prompt.json",
                            "sha256": helper.sha(encoded(prompt)), "bytes": len(encoded(prompt)),
                            "request_file": request_file, "request_sha256": helper.sha(request.encode()),
                            "response_file": response_file, "response_sha256": helper.sha(response)})
        self.reconstruction = {"version": "som.m6-hosted-reconstruction.v1", "status": "passed", "passed": True,
                               "name_prompt_count": 9, "name_prompts": records,
                               "baseline": {"binary_sha256": helper.BASELINE_SHA}}
        self.receipt_path = self.raw / "reconstructed-inputs/receipt.json"
        self.write(self.receipt_path, self.reconstruction)
        self.receipt_sha = self.hash(self.receipt_path)
        self.summary["reconstructed_inputs_receipt_sha256"] = self.receipt_sha
        self.write(self.raw / "summary.json", self.summary)
        contract = repo / "som-rs/contracts/react-output-v1"
        evidence = "som-rs/evidence/react-output-v1/name-cases-fake.json"
        self.write(contract / "toolchain.json", {"reference_verification": {"name_cases_evidence": evidence}})
        self.write(contract / "target-examples.json", {"name_cases": cases})
        self.write(repo / evidence, {"version": "som.react-output-name-cases.v1", "run": {"work_dir": self.name_root}, "cases": frozen})
        self.audit = {"version": "som.react-output-audit-130.v1", "run": (self.raw / "run").relative_to(repo).as_posix(),
                      "rows": [{"id": "integrity/candidate", "status": "passed"}],
                      "totals": {"failed": 0, "unrun": 0, "passed": 1, "attested": 0, "post_acceptance": 0}}
        self.write(self.audit_path, self.audit)
        auditor_bytes = b"fake copied auditor source; never executed\n"
        self.write(self.out / "audit/api_raw.py", auditor_bytes)
        self.write(self.out / "audit/audit130.py", auditor_bytes)
        self.source = {"version": "som.m6-auditor-source.v1", "commit": "f" * 40,
                       "files": {"audit/api_raw.py": helper.sha(auditor_bytes),
                                 "audit/audit130.py": helper.sha(auditor_bytes)}}
        self.write(self.source_path, self.source)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else encoded(value))

    def hash(self, path):
        return helper.sha(path.read_bytes())

    def prepare(self):
        with mock.patch.multiple(helper, CANDIDATE_SHA=self.binary_sha, RECONSTRUCTION_SHA=self.receipt_sha):
            return helper.prepare(self.repo, self.raw, self.prepare_out)

    def rebind_reconstruction(self):
        self.write(self.receipt_path, self.reconstruction)
        self.receipt_sha = self.hash(self.receipt_path)
        self.summary["reconstructed_inputs_receipt_sha256"] = self.receipt_sha
        self.write(self.raw / "summary.json", self.summary)

    def finalize(self, exit_code=0):
        with mock.patch.multiple(helper, CANDIDATE_SHA=self.binary_sha, RECONSTRUCTION_SHA=self.receipt_sha):
            return helper.finalize(self.repo, self.raw, self.audit_path, self.source_path, self.final_out, exit_code)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-replay-fixture-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Fixture(Path(temporary.name).resolve() / "repo")

    def test_prepare_hydrates_exact_ascii_writer_without_changing_raw(self):
        fixture = self.fixture
        before = {path.relative_to(fixture.raw): path.read_bytes() for path in fixture.raw.rglob("*") if path.is_file()}
        fixture.prepare()
        for identity, expected in fixture.prompts.items():
            self.assertEqual((fixture.repo / fixture.name_root / identity / "prompt.json").read_bytes(), expected)
            self.assertIn(b"\\u00e9", expected)
        self.assertEqual(before, {path.relative_to(fixture.raw): path.read_bytes() for path in fixture.raw.rglob("*") if path.is_file()})

    def test_late_bad_response_refuses_before_publishing_any_name(self):
        fixture = self.fixture
        path = fixture.raw / "reconstructed-inputs" / fixture.reconstruction["name_prompts"][-1]["response_file"]
        path.write_bytes(b'{"status":"ok","bundle":{"prompt":{"wrong":true}}}')
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())

    def test_changed_candidate_bytes_refuse_before_hydration(self):
        fixture = self.fixture
        (fixture.candidate / "som").write_bytes(b"changed candidate bytes: never executed\n")
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())

    def test_all_twelve_steps_must_keep_original_order(self):
        fixture = self.fixture
        fixture.run["steps"][0], fixture.run["steps"][1] = fixture.run["steps"][1], fixture.run["steps"][0]
        fixture.write(fixture.raw / "run/run.json", fixture.run)
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())
        self.assertFalse(fixture.prepare_out.exists())

    def test_existing_different_file_and_symlink_parent_refuse(self):
        fixture = self.fixture
        first = next(iter(fixture.prompts))
        destination = fixture.repo / fixture.name_root / first / "prompt.json"
        fixture.write(destination, b"different original bytes")
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertEqual(destination.read_bytes(), b"different original bytes")
        destination.unlink()
        destination.parent.rmdir()
        destination.parent.symlink_to(fixture.out, target_is_directory=True)
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.out / "prompt.json").exists())

    def test_wrong_acceptance_or_source_cannot_replay(self):
        fixture = self.fixture
        for key, value in (("acceptance_exit_code", 1), ("head_after", "0" * 40), ("failed_stage", "candidate")):
            with self.subTest(key=key):
                changed = {**fixture.summary, key: value}
                fixture.write(fixture.raw / "summary.json", changed)
                with self.assertRaises(ValueError):
                    fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())

    def test_request_must_match_frozen_control_even_with_matching_raw_hash(self):
        fixture = self.fixture
        row = fixture.reconstruction["name_prompts"][0]
        request = b'{"format":"som.prompt-json.v1","prompt":{"wrong":true}}'
        fixture.write(fixture.raw / "reconstructed-inputs" / row["request_file"], request)
        row["request_sha256"] = helper.sha(request)
        fixture.rebind_reconstruction()
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())

    def test_response_must_produce_frozen_prompt_even_with_matching_raw_hash(self):
        fixture = self.fixture
        # RN08 uses CNL, so only the frozen prompt hash can reject this altered
        # response once all recorded request/response/output hashes agree.
        row = fixture.reconstruction["name_prompts"][7]
        prompt = {"wrong": True}
        response = encoded({"status": "ok", "bundle": {"prompt": prompt}})
        fixture.write(fixture.raw / "reconstructed-inputs" / row["response_file"], response)
        row["response_sha256"] = helper.sha(response)
        row["sha256"], row["bytes"] = helper.sha(encoded(prompt)), len(encoded(prompt))
        fixture.rebind_reconstruction()
        with self.assertRaises(ValueError):
            fixture.prepare()
        self.assertFalse((fixture.repo / fixture.name_root).exists())

    def test_finalize_requires_full_new_audit_and_binds_actual_auditor_source(self):
        fixture = self.fixture
        result = fixture.finalize()
        self.assertIs(result["passed"], True)
        self.assertEqual(result["audit_sha256"], fixture.hash(fixture.audit_path))
        self.assertEqual(result["auditor_source_sha256"], fixture.hash(fixture.source_path))
        self.assertEqual(result["auditor_source"], fixture.source)
        self.assertEqual(result["candidate_binary_sha256"], fixture.binary_sha)
        self.assertEqual(json.loads(fixture.final_out.read_bytes()), result)
        self.assertEqual(json.loads((fixture.raw / "summary.json").read_bytes()), fixture.summary)

    def test_finalize_refuses_boolean_zero_wrong_run_and_operational_attestation(self):
        fixture = self.fixture
        for change in ("boolean-zero", "other-run", "operational-attestation", "exit"):
            with self.subTest(change=change):
                changed = json.loads(encoded(fixture.audit))
                if change == "boolean-zero":
                    changed["totals"]["failed"] = False
                elif change == "other-run":
                    changed["run"] = ".local/other/run"
                elif change == "operational-attestation":
                    changed["rows"] = [{"id": "products/example-v1/api", "status": "attested"}]
                fixture.write(fixture.audit_path, changed)
                result = fixture.finalize(1 if change == "exit" else 0)
                self.assertIs(result["passed"], False)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["new_product_suites"], "not_run")
                self.assertEqual(json.loads(fixture.final_out.read_bytes()), result)
                fixture.final_out.unlink()

    def test_finalize_refuses_changed_copied_auditor_bytes(self):
        fixture = self.fixture
        (fixture.out / "audit/api_raw.py").write_bytes(b"changed copied auditor source\n")
        with self.assertRaises(ValueError):
            fixture.finalize()
        self.assertFalse(fixture.final_out.exists())

    def test_cli_refuses_nonhosted_environment_before_git_or_writes(self):
        fixture = self.fixture
        with mock.patch.dict(helper.os.environ, {}, clear=True), \
             mock.patch.object(helper.subprocess, "run", side_effect=AssertionError("Git must not run")), \
             mock.patch("builtins.print"):
            result = helper.main(["prepare", "--repo", str(fixture.repo), "--raw-root", str(fixture.raw),
                                  "--out", str(fixture.prepare_out)])
        self.assertEqual(result, 1)
        self.assertFalse(fixture.prepare_out.exists())


if __name__ == "__main__":
    unittest.main()
