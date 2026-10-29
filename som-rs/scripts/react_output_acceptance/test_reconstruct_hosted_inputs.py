"""Fake unit inputs only. Tests replace HTTP and Git; no compiler is executed."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("m6_reconstruct", Path(__file__).with_name("reconstruct_hosted_inputs.py"))
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Fixture:
    """A small fake Rust universe exercises the copy rules without acceptance data."""
    def __init__(self, root):
        self.repo = root / "repo"
        self.repo.mkdir()
        self.out = self.repo / ".local/m6-hosted/acceptance/reconstructed-inputs"
        self.out.parent.mkdir(parents=True)
        self.requests, self.prompts, self.name_hashes = {}, {}, {}
        self.calls = []
        self.git_calls = []
        self.bad_name = None
        self.reorder_name = None
        self.change_health = False
        self.health_calls = 0
        self.health = {"status": "ok", "fake_identity": "public-baseline"}
        self.binary = b"fake baseline bytes, never executed"
        self.binary_sha = helper.sha(self.binary)
        self.write(".local/m6-hosted/baseline/som", self.binary, 0o755)
        self.write(".local/m6-hosted/baseline/runtime.json", encode({"health": self.health}))
        cases, evidence = [], []
        for index in range(1, 10):
            identity = f"RN{index:02d}_FAKE"
            prompt = {"zeta": index, "alpha": "fake private é", "version": "fake.prompt.v1"}
            request = {"format": "som.prompt-json.v1", "prompt": prompt} if index % 2 else {"format": "som.cnl.v1", "source": f"Fake public CNL {index}."}
            body = encode(request).decode()
            self.requests[body.encode()] = identity
            self.prompts[identity] = prompt
            pretty = (json.dumps(prompt, indent=2) + "\n").encode()
            self.name_hashes[identity] = helper.sha(pretty)
            cases.append({"id": identity, "python_control_body": body})
            evidence.append({"id": identity, "prompt_file_sha256": helper.sha(pretty)})
        self.write("som-rs/contracts/react-output-v1/target-examples.json", encode({"name_cases": cases}))
        self.evidence_path = "som-rs/evidence/react-output-v1/name-cases-fake.json"
        self.write("som-rs/contracts/react-output-v1/toolchain.json", encode({"reference_verification": {"name_cases_evidence": self.evidence_path}}))
        self.write(self.evidence_path, encode({"run": {"work_dir": helper.NAME_ROOT}, "cases": evidence}))

        # All ten named files are copied. Two fake refusal rows have outcomes.
        self.phase2_ids = ["legacy-fake", "public-fake", "RS12_CNL_FAKE", "RS13_JSON_FAKE"]
        self.write("som-rs/contracts/rust-output-v1/phase2/case-inventory.json", encode({"rows": [{"id": identity} for identity in self.phase2_ids]}))
        self.source_files = {}
        phase1 = {"cases": [{"id": f"phase1-fake-{index}", "files_sha256": "f" * 64} for index in range(3)]}
        self.source_files["rust-products.json"] = json.dumps(phase1, indent=1).encode() + b"\n"
        hashes = {}
        for identity in self.phase2_ids:
            key = identity.replace("-", "_")
            raw = json.dumps({"id": identity, "rust": {"files_sha256": "f" * 64}}, indent=1).encode() + b"\n"
            self.source_files[f"phase2/cases/{key}.json"] = raw
            self.source_files[f"phase2/cases/{key}.outcome.json"] = b"fake exact outcome bytes\x00\xff\n"
            hashes[identity] = helper.sha(raw)
        self.source_files["phase2/summary.json"] = encode({"case_sha256": hashes}) + b"\n"
        for name, raw in self.source_files.items():
            self.write(f"{helper.RUST_SOURCE}/{name}", raw)

        self.originals = {}
        metadata = []
        for index in range(5):
            name = f"{helper.LEGACY_ROOT}/{index:04d}/assemble.request.json"
            raw = (json.dumps({"bundle": {"prompt": {"zeta": index, "alpha": "fake original"}}}, indent=2) + "\n").encode()
            self.originals[name] = raw
            self.write(name, raw)
            metadata.append({"path": name, "sha256": helper.sha(raw), "bytes": len(raw), "mode": 0o644})
        manifest = encode({"version": "som.m6-sealed-inputs-manifest.v1", "files": metadata})
        index = encode({"version": "som.m6-input-index.v1", "packages": [{"archive": "package-000.sealed", "manifest": "package-000.manifest.json", "manifest_sha256": helper.sha(manifest)}]})
        self.blobs = {"inputs-index.json": index, "package-000.manifest.json": manifest}
        self.commit = "2" * 40
        self.write("som-rs/scripts/react_output_acceptance/hosted_inputs.json", encode({"version": "som.m6-hosted-inputs.v1", "commit": self.commit, "index_sha256": helper.sha(index)}))

    def write(self, name, raw, mode=0o644):
        filename = self.repo / name
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_bytes(raw)
        filename.chmod(mode)
        return filename

    def git(self, args, **kwargs):
        self.git_calls.append(args)
        assert args[:5] == ["git", "--no-pager", "-C", str(self.repo), "show"]
        assert kwargs["shell"] is False and kwargs["stdout"] == subprocess.PIPE
        assert "M6_INPUTS_KEYRING" not in kwargs["env"] and "M6_INPUTS_KEY" not in kwargs["env"]
        commit, filename = args[5].split(":", 1)
        assert commit == self.commit
        return subprocess.CompletedProcess(args, 0, self.blobs[filename], b"")

    def http(self, route, body=None):
        self.calls.append((route, body))
        if route == "/healthz":
            self.health_calls += 1
            value = {**self.health, "changed": True} if self.change_health and self.health_calls > 1 else self.health
            return 200, encode(value)
        assert route == "/v1/compile" and body in self.requests
        identity = self.requests[body]
        prompt = dict(self.prompts[identity])
        if identity == self.bad_name:
            prompt["alpha"] = "fake wrong output that must never be printed"
        if identity == self.reorder_name:
            prompt = dict(reversed(list(prompt.items())))
        return 200, encode({"status": "ok", "bundle": {"prompt": prompt}}) + b"\n"

    @contextlib.contextmanager
    def patches(self, hosted=True):
        with mock.patch.multiple(helper, PHASE1_COUNT=3, PHASE2_COUNT=4, RUST_FILE_COUNT=10, LEGACY_COUNT=5, BASELINE_SHA=self.binary_sha), \
             mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true" if hosted else "false", "RUNNER_ENVIRONMENT": "github-hosted", "M6_INPUTS_KEYRING": "fake private keyring"}, clear=True), \
             mock.patch.object(helper.subprocess, "run", side_effect=self.git), \
             mock.patch.object(helper, "baseline_call", side_effect=self.http):
            yield

    def run(self, **kwargs):
        with self.patches():
            return helper.reconstruct(kwargs.get("repo", self.repo), kwargs.get("baseline", helper.BASELINE_URL), kwargs.get("out", self.out))


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-m6-reconstruction-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = Fixture(self.root)

    def assert_no_name_prompts(self):
        self.assertFalse((self.fixture.repo / helper.NAME_ROOT).exists())

    def test_real_count_contract_and_exact_physical_fake_copy(self):
        self.assertEqual((helper.NAME_COUNT, helper.PHASE1_COUNT, helper.PHASE2_COUNT, helper.RUST_FILE_COUNT, helper.LEGACY_COUNT), (9, 115, 584, 1170, 544))
        receipt = self.fixture.run()
        self.assertEqual(receipt["version"], "som.m6-hosted-reconstruction.v1")
        self.assertEqual(receipt["stage"], "input_reconstruction")
        self.assertEqual((receipt["status"], receipt["passed"], receipt["product_suites"]), ("passed", True, "not_run"))
        self.assertEqual(receipt["legacy_assemble_requests"], "not_reconstructed")
        self.assertEqual((receipt["name_prompt_count"], receipt["rust_file_count"], receipt["rust_phase1_case_count"], receipt["rust_phase2_case_count"]), (9, 10, 3, 4))
        self.assertEqual(receipt["rust_reference"]["destination"], "rust-baseline")
        self.assertEqual(receipt["rust_reference"]["source_tree_sha256"], receipt["rust_reference"]["copy_tree_sha256"])
        self.assertEqual(json.loads((self.fixture.out / "receipt.json").read_bytes()), receipt)
        for name, raw in self.fixture.source_files.items():
            source = self.fixture.repo / helper.RUST_SOURCE / name
            copied = self.fixture.out / "rust-baseline" / name
            self.assertEqual(copied.read_bytes(), raw)
            self.assertEqual(source.read_bytes(), raw)
            self.assertNotEqual((source.stat().st_dev, source.stat().st_ino), (copied.stat().st_dev, copied.stat().st_ino))
        self.assertEqual({record["path"] for record in receipt["rust_reference"]["files"]}, set(self.fixture.source_files))
        for name, raw in self.fixture.originals.items():
            self.assertEqual((self.fixture.repo / name).read_bytes(), raw)
        for record in receipt["name_prompts"]:
            prompt = self.fixture.repo / record["path"]
            self.assertEqual(helper.sha(prompt.read_bytes()), self.fixture.name_hashes[record["id"]])
            self.assertIn(b"\\u00e9", prompt.read_bytes())
            self.assertEqual(helper.sha((self.fixture.out / record["request_file"]).read_bytes()), record["request_sha256"])
            self.assertEqual(helper.sha((self.fixture.out / record["response_file"]).read_bytes()), record["response_sha256"])
        self.assertEqual(len([route for route, _ in self.fixture.calls if route == "/v1/compile"]), 9)
        self.assertNotIn(str(self.root), json.dumps(receipt))

    def test_original_assemble_requests_are_required_and_never_synthesized(self):
        filename = next(iter(self.fixture.originals))
        (self.fixture.repo / filename).unlink()
        with self.assertRaisesRegex(helper.ReconstructionError, f"^{helper.REFUSAL}$"):
            self.fixture.run()
        self.assertFalse((self.fixture.repo / filename).exists())
        self.assertFalse(self.fixture.out.exists())
        self.assertEqual(self.fixture.calls, [])
        self.assert_no_name_prompts()

    def test_original_byte_change_and_public_manifest_hash_change_are_refused(self):
        filename = next(iter(self.fixture.originals))
        self.fixture.write(filename, self.fixture.originals[filename] + b" ")
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.fixture.write(filename, self.fixture.originals[filename])
        self.fixture.blobs["package-000.manifest.json"] += b" "
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertEqual(self.fixture.calls, [])
        self.assert_no_name_prompts()

    def test_wrong_name_bytes_and_wrong_key_order_do_not_publish_any_name_prompt(self):
        for attribute in ("bad_name", "reorder_name"):
            with self.subTest(attribute=attribute):
                setattr(self.fixture, attribute, "RN09_FAKE")
                with self.assertRaises(helper.ReconstructionError):
                    self.fixture.run()
                self.assert_no_name_prompts()
                receipt = json.loads((self.fixture.out / "receipt.json").read_bytes())
                self.assertEqual((receipt["passed"], receipt["status"], receipt["failed_stage"]), (False, "failed", "name_prompts"))
                self.assertTrue((self.fixture.out / "name-cases/RN09_FAKE/compile.response.json").is_file())
                import shutil
                shutil.rmtree(self.fixture.out)
                setattr(self.fixture, attribute, None)

    def test_changed_baseline_health_does_not_publish_name_prompts(self):
        self.fixture.change_health = True
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assert_no_name_prompts()
        self.assertFalse((self.fixture.out / "rust-baseline").exists())
        self.assertFalse(json.loads((self.fixture.out / "receipt.json").read_bytes())["passed"])

    def test_missing_refusal_outcome_extra_record_and_summary_hash_mismatch_are_refused(self):
        source = self.fixture.repo / helper.RUST_SOURCE
        missing = "phase2/cases/RS12_CNL_FAKE.outcome.json"
        (source / missing).unlink()
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.fixture.write(f"{helper.RUST_SOURCE}/{missing}", self.fixture.source_files[missing])
        extra = self.fixture.write(f"{helper.RUST_SOURCE}/phase2/cases/unexpected.json", b"fake extra")
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        extra.unlink()
        summary = json.loads(self.fixture.source_files["phase2/summary.json"])
        summary["case_sha256"][self.fixture.phase2_ids[0]] = "0" * 64
        self.fixture.write(f"{helper.RUST_SOURCE}/phase2/summary.json", encode(summary))
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertFalse(self.fixture.out.exists())
        self.assertEqual(self.fixture.calls, [])

    def test_symlink_source_and_name_parent_are_refused_without_following(self):
        source = self.fixture.repo / helper.RUST_SOURCE / "rust-products.json"
        target = self.root / "outside.json"
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        source.unlink()
        self.fixture.write(f"{helper.RUST_SOURCE}/rust-products.json", self.fixture.source_files["rust-products.json"])
        name_root = self.fixture.repo / helper.NAME_ROOT
        name_root.parent.mkdir(parents=True, exist_ok=True)
        name_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertFalse(self.fixture.out.exists())
        self.assertEqual(target.read_bytes(), self.fixture.source_files["rust-products.json"])
        self.assertFalse((self.root / "RN01_FAKE").exists())

    def test_existing_prompt_is_never_replaced(self):
        name = f"{helper.NAME_ROOT}/RN01_FAKE/prompt.json"
        self.fixture.write(name, b"fake existing bytes")
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertEqual((self.fixture.repo / name).read_bytes(), b"fake existing bytes")
        self.assertFalse(self.fixture.out.exists())

    def test_host_url_output_and_baseline_hash_guards(self):
        with self.fixture.patches(hosted=False):
            with self.assertRaises(helper.ReconstructionError):
                helper.reconstruct(self.fixture.repo, helper.BASELINE_URL, self.fixture.out)
        for baseline in ["http://127.0.0.1:18919", "http://example.invalid:18917", helper.BASELINE_URL + "/"]:
            with self.assertRaises(helper.ReconstructionError):
                self.fixture.run(baseline=baseline)
        self.fixture.out.mkdir()
        sentinel = self.fixture.out / "keep"
        sentinel.write_bytes(b"fake keep")
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertEqual(sentinel.read_bytes(), b"fake keep")
        sentinel.unlink()
        self.fixture.out.rmdir()
        self.fixture.write(".local/m6-hosted/baseline/som", b"wrong fake baseline")
        with self.assertRaises(helper.ReconstructionError):
            self.fixture.run()
        self.assertEqual(self.fixture.calls, [])
        self.assert_no_name_prompts()

    def test_cli_prints_counts_only_and_failure_is_generic(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["--repo", str(self.fixture.repo), "--baseline", helper.BASELINE_URL, "--out", str(self.fixture.out)]
        with self.fixture.patches(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = helper.main(args)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["name_prompt_count"], 9)
        self.assertEqual(stderr.getvalue(), "")
        for private in [str(self.root), "fake private", "fake private keyring"]:
            self.assertNotIn(private, stdout.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.fixture.patches(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = helper.main(args)
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), helper.REFUSAL + "\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(helper.main(["--private", "fake secret"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), helper.REFUSAL + "\n")


if __name__ == "__main__":
    unittest.main()
