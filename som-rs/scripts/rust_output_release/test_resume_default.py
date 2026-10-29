"""Resume fixtures use fake owners and never start a process or contact a port."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
FIXTURES = importlib.util.spec_from_file_location("resume_profile_fixtures", HERE / "test_compiler_profile.py")
profile = importlib.util.module_from_spec(FIXTURES)
FIXTURES.loader.exec_module(profile)


def link_state(path):
    path = Path(path)
    if path.is_symlink():
        return {"kind": "symlink", "target": str(path.readlink())}
    profile.require(not path.exists(), "unexpected non-link pointer")
    return {"kind": "absent"}


class FakeRun:
    def __init__(self, fixture, output, cwd):
        self.fixture, self.output, self.cwd = fixture, output, cwd
        self.children, self.owners, self.evidence_errors = [], {}, []
        self.events, self.canaries, self.health_checks, self.stopped = [], [], [], []
        self.owner = None
        if fixture.incomplete_evidence:
            self.evidence_errors.append({"error": "fake raw evidence write failed"})

    def save(self, name, raw):
        (self.output / name).write_bytes(raw)

    def event(self, stage, **fields):
        self.events.append({"stage": stage, **fields})
        (self.output / "journal.json").write_text(json.dumps(self.events))

    def start(self, argv, health):
        self.owner = {"pid": 7777, "argv": argv[:], "port": self.fixture.port, "cwd": self.cwd,
                      "binary_sha256": self.fixture.config["previous"]["binary"]["sha256"], "start": "fake fresh start"}
        self.fixture.new_alive = True
        self.owners[self.owner["pid"]] = self.owner
        return copy.deepcopy(self.owner)

    def health(self, port, expected):
        self.health_checks.append((port, copy.deepcopy(expected)))
        return copy.deepcopy(expected)

    def canary(self, port, binaries, cases, label):
        self.canaries.append((port, binaries, cases, label))
        if self.fixture.change_receipt:
            self.fixture.default.write_bytes(self.fixture.external_bytes)
        if self.fixture.fail_canary:
            raise RuntimeError("fake canary failure")

    def stop_child(self, owner):
        profile.require(owner == self.owner, "refuse stopping an unverified fake owner")
        profile.require(self.fixture.identity(owner["pid"], owner["argv"], owner["port"]) == owner,
                        "fake owner identity changed")
        self.stopped.append(owner["pid"])
        self.fixture.new_alive = False


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-resume-fixture-")
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve()
        modules = profile.isolated_modules()
        self.rc, self.switch = modules["common"], modules["switch"]
        self.rehearse = modules["rehearse"]
        spec = importlib.util.spec_from_file_location("fixture_resume_default", HERE / "resume_default.py")
        self.resume = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"release_common": self.rc, "switch": self.switch}):
            spec.loader.exec_module(self.resume)
        self.repo = self.temp / "repo"
        self.fixture = profile.Fixture(self.repo, self.rc)
        self.config = self.fixture.config
        self.root = self.repo / ".local/rust-compiler-goal/acceptance/resume-fixture/root"
        self.port = 18940
        self.default = self.root / "som-rs/runtime/default.json"
        previous = self.config["previous"]
        self.command = [previous["binary"]["path"], "serve", "--assets", previous["assets"]["path"], "--port", str(self.port)]
        self.old_owner = {"pid": 5432, "argv": self.command, "cwd": str(self.repo), "start": "fake stale start",
                          "binary_sha256": previous["binary"]["sha256"], "port": self.port}
        self.current = {"version": "som.default-runtime.v2", "accepted": True, "pid": 5432,
                        "command": self.command, "identity": self.old_owner,
                        "health": self.fixture.runtimes["previous"]["health"]}
        self.fixture.write(self.default, self.current)
        (self.root / "som-rs/runtime/current").symlink_to(str(Path(self.command[0]).parent))
        assets_pointer = self.root / "som-rs/compiler-assets/current"
        assets_pointer.parent.mkdir(parents=True)
        assets_pointer.symlink_to(self.command[3])
        self.snapshot = self.default.read_bytes()
        self.output = self.root.parent / "resume"
        self.approval_path = self.temp / "approval.json"
        self.old_alive = self.new_alive = self.fail_canary = self.change_receipt = self.fail_after_commit = False
        self.incomplete_evidence = self.owner_drift = False
        self.external_bytes = b"external receipt bytes: must not be overwritten\n"
        self.runs, self.port_checks = [], []
        self.atomic_calls = 0
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.rc, "REPO", self.repo).start()
        mock.patch.object(self.switch, "SANDBOX_PARENT", self.repo / ".local/rust-compiler-goal/acceptance").start()
        mock.patch.object(self.rc, "link_state", side_effect=link_state).start()
        mock.patch.object(self.rc, "alive", side_effect=self.alive).start()
        mock.patch.object(self.rc, "port_free", side_effect=self.port_free).start()
        mock.patch.object(self.rc, "identity", side_effect=self.identity).start()
        mock.patch.object(self.rc, "atomic_bytes", side_effect=self.atomic).start()
        mock.patch.object(self.rc, "Release", side_effect=self.make_run).start()
        mock.patch.dict(self.rc.os.environ, {}, clear=True).start()
        mock.patch.object(self.resume.signal, "signal").start()

    def alive(self, pid):
        return self.old_alive if pid == 5432 else self.new_alive if pid == 7777 else False

    def port_free(self, port):
        self.assertEqual(port, self.port)
        self.port_checks.append(port)

    def identity(self, pid, argv, port):
        self.assertEqual((pid, port), (7777, self.port))
        self.assertEqual(argv, self.command)
        owner = copy.deepcopy(self.runs[-1].owner)
        if self.owner_drift:
            owner["start"] = "a different fake process start"
        return owner

    def atomic(self, path, raw):
        self.assertIn(path, (self.default, self.output / "result.json"))
        path.write_bytes(raw)
        if path == self.default:
            self.atomic_calls += 1
            if self.fail_after_commit and self.atomic_calls == 1:
                raise RuntimeError("fake failure after receipt commit")

    def make_run(self, output, cwd):
        run = FakeRun(self, output, cwd)
        self.runs.append(run)
        return run

    def execute(self):
        return self.resume.execute(self.config, self.root, self.port, self.output, None, True)

    def assert_no_launch(self):
        self.assertTrue(all(run.owner is None for run in self.runs))
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_live_old_pid_refuses_without_start_or_signal(self):
        self.old_alive = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_no_launch()

    def test_occupied_port_refuses_without_start(self):
        with mock.patch.object(self.rc, "port_free", side_effect=RuntimeError("fake occupied port")):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assert_no_launch()

    def test_changed_previous_binding_refuses_before_start(self):
        self.config["previous"]["binary"]["sha256"] = "0" * 64
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_no_launch()

    def test_pointer_not_bound_to_stale_receipt_refuses_before_start(self):
        pointer = self.root / "som-rs/compiler-assets/current"
        pointer.unlink()
        pointer.symlink_to(self.config["candidate"]["assets"]["path"])
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_no_launch()
        self.assertEqual(str(pointer.readlink()), self.config["candidate"]["assets"]["path"])

    def test_pointer_change_after_state_read_is_not_overwritten_or_launched(self):
        pointer = self.root / "som-rs/compiler-assets/current"
        factory = self.make_run

        def changed(output, cwd):
            pointer.unlink()
            pointer.symlink_to(self.config["candidate"]["assets"]["path"])
            return factory(output, cwd)

        with mock.patch.object(self.rc, "Release", side_effect=changed):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assert_no_launch()
        self.assertEqual(str(pointer.readlink()), self.config["candidate"]["assets"]["path"])

    def test_bound_binary_change_after_state_read_refuses_before_start(self):
        factory = self.make_run

        def changed(output, cwd):
            Path(self.config["previous"]["binary"]["path"]).write_bytes(b"externally changed binary bytes\n")
            return factory(output, cwd)

        with mock.patch.object(self.rc, "Release", side_effect=changed):
            with self.assertRaises(RuntimeError):
                self.execute()
        self.assert_no_launch()

    def test_resume_uses_fresh_identity_and_all_previous_canaries(self):
        self.execute()
        saved = json.loads(self.default.read_bytes())
        self.assertEqual(saved["pid"], 7777)
        self.assertEqual(saved["identity"], self.runs[0].owner)
        self.assertEqual(saved["command"], self.command)
        self.assertIs(saved["accepted"], True)
        self.assertTrue(self.runs[0].health_checks)
        self.assertTrue(self.runs[0].canaries)
        for _port, _binaries, cases, _label in self.runs[0].canaries:
            self.assertEqual(cases, self.rc.previous_cases(self.config))
            self.assertEqual(len(cases), 6)
        result = json.loads((self.output / "result.json").read_bytes())
        self.assertIs(result["passed"], True)
        self.assertEqual(result["stale_snapshot_sha256"], self.rc.sha_bytes(self.snapshot))
        self.assertEqual(self.runs[0].events[-1]["stage"], "resume_verified")
        self.assertEqual(self.runs[0].stopped, [])

    def test_failed_canary_stops_only_new_owner_and_keeps_stale_receipt(self):
        self.fail_canary = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.runs[0].stopped, [7777])
        self.assertFalse(self.new_alive)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_failure_after_commit_restores_exact_stale_bytes(self):
        self.fail_after_commit = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.runs[0].stopped, [7777])
        self.assertFalse(self.new_alive)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_external_receipt_change_is_never_overwritten(self):
        self.change_receipt = self.fail_canary = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.runs[0].stopped, [7777])
        self.assertEqual(self.default.read_bytes(), self.external_bytes)

    def test_plan_is_read_only_and_does_not_start_an_owner(self):
        result = self.resume.plan(self.config, self.root, self.port, True)
        self.assertEqual(result["mutations"], 0)
        self.assertEqual(result["old_pid"], 5432)
        self.assertIs(result["old_pid_alive"], False)
        self.assertEqual(self.runs, [])
        self.assertEqual(self.atomic_calls, 0)
        self.assertEqual(self.default.read_bytes(), self.snapshot)

    def test_approval_pins_action_source_configuration_snapshot_and_quote(self):
        approval = {"action": "resume-default-8765-compiler", "approved": True,
                    "candidate_binary_sha256": self.config["candidate"]["binary"]["sha256"],
                    "script_sha256": self.rc.digest(self.resume.__file__), "common_sha256": self.rc.digest(self.rc.__file__),
                    "prerequisites_sha256": self.rc.receipt_sha(self.config),
                    "default_sha256": self.rc.sha_bytes(self.snapshot), "quote": "Resume this stopped compiler."}
        self.fixture.write(self.approval_path, approval)
        self.assertEqual(self.resume.check_approval(self.approval_path, self.config, self.snapshot), approval)
        for field in approval:
            with self.subTest(field=field):
                changed = dict(approval)
                changed[field] = (1 if field == "approved" else "   " if field == "quote"
                                  else "restore-default-8765-compiler" if field == "action" else "0" * 64)
                self.fixture.write(self.approval_path, changed)
                with self.assertRaises(RuntimeError):
                    self.resume.check_approval(self.approval_path, self.config, self.snapshot)
        self.assertEqual(self.runs, [])

    def test_incomplete_evidence_blocks_pass_and_cleans_up_owned_child(self):
        self.incomplete_evidence = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.runs[0].stopped, [7777])
        self.assertEqual(self.default.read_bytes(), self.snapshot)
        result = json.loads((self.output / "result.json").read_bytes())
        self.assertIs(result["passed"], False)
        self.assertTrue(result["evidence_errors"])

    def test_changed_owner_identity_is_not_signalled(self):
        self.owner_drift = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual(self.runs[0].stopped, [])
        self.assertEqual(self.default.read_bytes(), self.snapshot)
        result = json.loads((self.output / "result.json").read_bytes())
        self.assertIs(result["passed"], False)
        self.assertTrue(result["cleanup_errors"])

    def startup_wait_timeout(self, poll_status):
        handle = mock.Mock()
        handle.pid = 8888
        handle.poll.return_value = poll_status
        handle.wait.side_effect = subprocess.TimeoutExpired('fake startup cleanup wait', 20)
        factory = self.make_run

        def unadmitted(output, cwd):
            run = factory(output, cwd)
            def start(_argv, _health):
                run.children.append(handle)
                # Simulate frozen Run.start's own cleanup attempt timing out
                # before it admitted any process identity.
                handle.terminate()
                handle.wait(timeout=20)
            run.start = start
            return run

        with mock.patch.object(self.rc, 'Release', side_effect=unadmitted):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.execute()
        self.assertEqual(self.runs[0].owners, {})
        self.assertEqual(self.runs[0].stopped, [])
        handle.poll.assert_called_once_with()
        handle.terminate.assert_called_once_with()
        handle.wait.assert_called_once_with(timeout=20)
        handle.kill.assert_not_called()
        handle.send_signal.assert_not_called()
        self.assertEqual(self.default.read_bytes(), self.snapshot)
        result = json.loads((self.output / 'result.json').read_bytes())
        self.assertIs(result['passed'], False)
        return result

    def test_unadmitted_live_handle_requires_operator_after_start_cleanup_timeout(self):
        result = self.startup_wait_timeout(None)
        self.assertIs(result['stale_snapshot_restored'], False)
        self.assertTrue(any('child handle 8888 remains alive; requires operator' in error
                            for error in result['cleanup_errors']))

    def test_reaped_unadmitted_handle_allows_stale_restore_after_start_timeout(self):
        result = self.startup_wait_timeout(0)
        self.assertIs(result['stale_snapshot_restored'], True)
        self.assertEqual(result['cleanup_errors'], [])

    def test_hosted_offline_seed_refuses_pid_zero_before_alive_probe(self):
        seed = self.rehearse.rehearsal_seed(self.config)
        output = self.temp / "offline-zero"
        done = subprocess.CompletedProcess(["fake-python"], 0, stdout="0\n", stderr="")
        hosted = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "macOS"}
        with mock.patch.dict(self.rc.os.environ, hosted, clear=True), \
             mock.patch.object(self.rehearse.subprocess, "run", return_value=done) as called, \
             mock.patch.object(self.rc, "alive", side_effect=AssertionError("PID zero must not be probed")) as alive:
            with self.assertRaisesRegex(RuntimeError, "seed PID"):
                self.rehearse.offline_sandbox(output, seed, self.port, "f" * 64)
        called.assert_called_once()
        alive.assert_not_called()
        self.assertFalse((output / "root/som-rs/runtime/default.json").exists())

    def test_offline_seed_refuses_nonhosted_environment_before_child_or_port_probe(self):
        seed = self.rehearse.rehearsal_seed(self.config)
        output = self.temp / "offline-local"
        with mock.patch.object(self.rehearse.subprocess, "run", side_effect=profile.forbidden) as child, \
             mock.patch.object(self.rc, "port_free", side_effect=profile.forbidden) as port:
            with self.assertRaisesRegex(RuntimeError, "hosted GitHub macOS"):
                self.rehearse.offline_sandbox(output, seed, self.port, "f" * 64)
        child.assert_not_called()
        port.assert_not_called()
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
