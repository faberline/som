"""Fake orchestration checks. Execute on GitHub only; no product suite runs here."""
from __future__ import annotations

import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("m6_hosted_driver", Path(__file__).with_name("run_hosted_acceptance.py"))
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Fixture:
    """All commands, process identities and HTTP replies are fake."""
    def __init__(self, root):
        self.repo = root / "repo"
        self.frozen = self.repo / ".local/m6-hosted/baseline"
        self.frozen.mkdir(parents=True)
        (self.frozen / "som").write_bytes(b"fake pinned baseline")
        self.pin = driver.sha(self.frozen / "som")
        save(self.frozen / "runtime.json", {"health": {"status": "ok"}})
        save(self.repo / "som-rs/contracts/react-output-v1/acceptance-plan.json", {"baseline": self.pin})
        (self.repo / ".local/m6-hosted/prettier").mkdir()
        self.out = self.repo / ".local/m6-hosted/check"
        self.calls = []
        self.acceptance_exit = self.audit_exit = self.prepare_exit = 0
        self.bad_cwd = self.changed_head = self.omit_run = False
        self.run_overrides, self.audit_overrides = {}, {}

    def run(self, command, **kwargs):
        self.calls.append(command)
        assert kwargs["shell"] is False
        assert "M6_INPUTS_KEYRING" not in kwargs["env"] and "GH_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        kwargs["stdout"].write(b"private raw output\n")
        kwargs["stderr"].write(b"private raw error\n")
        code = 0
        if command[0] == "git":
            kwargs["stdout"].seek(0)
            kwargs["stdout"].truncate()
            changed = self.changed_head and kwargs["stdout"].name.endswith("head-after.stdout")
            kwargs["stdout"].write((("b" if changed else "a") * 40 + "\n").encode())
        elif command[0] == "ps":
            kwargs["stdout"].seek(0)
            kwargs["stdout"].truncate()
            kwargs["stdout"].write((" ".join(self.runtime["argv"]) + "\n").encode())
        elif command[0] == "lsof":
            kwargs["stdout"].seek(0)
            kwargs["stdout"].truncate()
            cwd = self.repo if self.bad_cwd else self.out / "candidate"
            kwargs["stdout"].write(f"p4321\nfcwd\nn{cwd}\n".encode())
        elif command[1].endswith("prepare_candidate.py"):
            code = self.prepare_exit
            if not code:
                candidate = self.out / "candidate"
                candidate.mkdir()
                (candidate / "som").write_bytes(b"fake candidate")
                pin = driver.sha(candidate / "som")
                self.runtime = {"pid": 4321, "port": 18919, "binary_sha256": pin, "accepted": False,
                    "argv": [str(candidate / "som"), "serve", "--assets", str(candidate / "assets"), "--port", "18919"]}
                save(candidate / "runtime.json", self.runtime)
                save(candidate / "build.json", {"complete": True, "source_unchanged": True,
                    "binary_sha256": pin, "tree_before": {"head": "a" * 40}})
        elif command[1].endswith("run_acceptance.py"):
            code = self.acceptance_exit
            if not self.omit_run:
                products = self.out / "run/products/report.json"
                save(products, {"fake": True})
                save(self.out / "run/run.json", {"version": "som.react-output-acceptance-run.v1",
                    "passed": code == 0, "identity_passed": True, "failed": [], "problems": [], "not_run": [], "steps": [
                    {"step": name, "judgment": "passed", "report": str(products) if name == "products" else None}
                    for name in driver.STEPS], **self.run_overrides})
        elif command[1].endswith("audit130.py"):
            code = self.audit_exit
            save(self.out / "audit.json", {"version": "som.react-output-audit-130.v1",
                "run": (self.out / "run").relative_to(self.repo).as_posix(),
                "totals": {"failed": int(code != 0), "unrun": 0}, **self.audit_overrides})
        else:
            raise AssertionError("Unexpected subprocess")
        return subprocess.CompletedProcess(command, code)


class HostedDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = f = Fixture(Path(self.temp.name))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in (("REPO", f.repo), ("BASELINE_SHA", f.pin)):
            self.stack.enter_context(mock.patch.object(driver, target, value))
        self.host = self.stack.enter_context(mock.patch.object(driver, "require_hosted"))
        self.free = self.stack.enter_context(mock.patch.object(driver, "port_free", return_value=True))
        self.health = self.stack.enter_context(mock.patch.object(driver, "wait_health"))
        self.run = self.stack.enter_context(mock.patch.object(driver.subprocess, "run", side_effect=f.run))
        self.start = self.stack.enter_context(mock.patch.object(driver.subprocess, "Popen"))
        self.stack.enter_context(mock.patch.object(driver.os, "getpgid", return_value=4321))
        self.kill = self.stack.enter_context(mock.patch.object(driver.os, "killpg", side_effect=[None, ProcessLookupError()]))
        self.stack.enter_context(mock.patch.dict(os.environ, {"M6_INPUTS_KEYRING": "private-key", "GH_TOKEN": "private-token",
                                                               "PYTHONDONTWRITEBYTECODE": "0"}))

    def invoke(self):
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            result = driver.main(["--out", str(self.fixture.out)])
        self.assertNotIn("private", console.getvalue())
        path = self.fixture.out / "summary.json"
        return result, json.loads(path.read_text()) if path.exists() else None

    def test_host_refusal_has_no_process_or_output(self):
        self.host.side_effect = ValueError("refused")
        self.assertEqual(self.invoke(), (1, None))
        self.run.assert_not_called()
        self.start.assert_not_called()

    def test_actual_host_guard_refuses_self_hosted(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_OS": "macOS"}):
            with self.assertRaises(ValueError):
                ORIGINAL_HOST()

    def test_existing_output_is_preserved(self):
        save(self.fixture.out / "summary.json", {"preserve": True})
        self.assertEqual(self.invoke(), (1, {"preserve": True}))
        self.run.assert_not_called()
        self.start.assert_not_called()
        self.kill.assert_not_called()

    def test_wrong_baseline_hash_stops_before_first_command(self):
        (self.fixture.frozen / "som").write_bytes(b"wrong baseline")
        code, summary = self.invoke()
        self.assertEqual((code, summary["failed_stage"], summary["product_suites"]), (1, "inputs", "not_run"))
        self.run.assert_not_called()
        self.start.assert_not_called()

    def test_full_fake_run_keeps_raw_logs_and_fixed_commands(self):
        code, summary = self.invoke()
        self.assertEqual((code, summary["passed"], summary["step_count"]), (0, True, 12))
        self.assertEqual(summary["cleanup"]["candidate"]["ownership_verified"], True)
        self.assertEqual(self.start.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual((self.fixture.out / "acceptance.stdout").read_bytes(), b"private raw output\n")
        command = next(command for command in self.fixture.calls if len(command) > 1 and command[1].endswith("run_acceptance.py"))
        self.assertIn("--cleanup-products-runtime", command)
        self.assertEqual(command[command.index("--baseline") + 1], "http://127.0.0.1:18917")
        audit = next(command for command in self.fixture.calls if len(command) > 1 and command[1].endswith("audit130.py"))
        self.assertEqual(audit[audit.index("--out") + 1], str(self.fixture.out / "audit.json"))

    def test_failed_acceptance_still_runs_independent_audit(self):
        self.fixture.acceptance_exit = 1
        code, summary = self.invoke()
        self.assertEqual((code, summary["failed_stage"], summary["audit_exit_code"]), (1, "acceptance", 0))
        self.assertEqual(summary["product_suites"], "recorded")

    def test_wrong_audit_version_is_refused(self):
        self.fixture.audit_overrides = {"version": "unrelated.audit.v1"}
        self.assertEqual(self.invoke()[0], 1)

    def test_audit_for_another_run_is_refused(self):
        self.fixture.audit_overrides = {"run": ".local/m6-hosted/another-run/run"}
        self.assertEqual(self.invoke()[0], 1)

    def test_boolean_audit_counts_are_refused(self):
        for name in ("failed", "unrun"):
            with self.subTest(name=name):
                self.fixture.out = self.fixture.repo / f".local/m6-hosted/check-{name}"
                self.fixture.audit_overrides = {"totals": {"failed": 0, "unrun": 0, name: False}}
                self.kill.side_effect = [None, ProcessLookupError()]
                self.assertEqual(self.invoke()[0], 1)

    def test_invalid_run_metadata_is_refused(self):
        for field, value in (("version", "unrelated.run.v1"), ("identity_passed", False),
                             ("failed", ["products"]), ("problems", ["identity changed"])):
            with self.subTest(field=field):
                self.fixture.out = self.fixture.repo / f".local/m6-hosted/check-{field}"
                self.fixture.run_overrides = {field: value}
                self.kill.side_effect = [None, ProcessLookupError()]
                self.assertEqual(self.invoke()[0], 1)

    def test_failed_prepare_records_suites_as_unrun(self):
        self.fixture.prepare_exit = 1
        code, summary = self.invoke()
        self.assertEqual((code, summary["failed_stage"], summary["product_suites"]), (1, "candidate", "not_run"))
        self.assertFalse(any(len(command) > 1 and command[1].endswith("run_acceptance.py") for command in self.fixture.calls))

    def test_cleanup_refuses_process_with_wrong_working_directory(self):
        self.fixture.bad_cwd = True
        code, summary = self.invoke()
        self.assertEqual((code, summary["failed_stage"]), (1, "cleanup-or-head"))
        self.assertFalse(summary["cleanup"]["candidate"]["ownership_verified"])
        self.kill.assert_not_called()
        self.start.return_value.terminate.assert_called_once()

    def test_occupied_qa_port_stops_before_baseline(self):
        self.free.return_value = False
        code, summary = self.invoke()
        self.assertEqual((code, summary["failed_stage"]), (1, "ports"))
        self.start.assert_not_called()
        self.health.assert_not_called()
        self.kill.assert_not_called()

    def test_head_change_blocks_a_good_run(self):
        self.fixture.changed_head = True
        self.assertEqual(self.invoke()[0], 1)


ORIGINAL_HOST = driver.require_hosted


class TransportGuardTests(unittest.TestCase):
    def test_only_connection_refused_counts_as_an_unused_port(self):
        with mock.patch.object(driver.socket, "create_connection", side_effect=OSError(errno.ECONNREFUSED, "fake")):
            self.assertTrue(driver.port_free(18917))
        with mock.patch.object(driver.socket, "create_connection", side_effect=TimeoutError()):
            self.assertFalse(driver.port_free(18917))
        with mock.patch.object(driver.socket, "create_connection"):
            self.assertFalse(driver.port_free(18917))

    def test_healthy_service_with_wrong_identity_is_refused(self):
        with mock.patch.object(driver, "OPENER") as opener:
            response = opener.open.return_value.__enter__.return_value
            response.status, response.read.return_value = 200, b'{"status":"other"}'
            with self.assertRaises(ValueError):
                driver.wait_health(mock.Mock(poll=lambda: None), {"status": "frozen"})


if __name__ == "__main__":
    ORIGINAL_HOST()
    unittest.main()
