"""Fake subprocess checks. Run on GitHub; no process, service, or gate is started."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("record_api_fixture", Path(__file__).with_name("record_api.py"))
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.calls = []

        def run(argv, *args, **kwargs):
            self.calls.append((argv, kwargs))
            text = kwargs.get("text")
            data = "raw reply\n" if text else b"raw reply\n"
            return subprocess.CompletedProcess(argv, 0, data, "" if text else b"")

        self.recorder = record.Recorder(self.out, run)

    def test_fetch_hook_is_limited_to_exact_api_node_child(self):
        self.recorder.run(["node", str(record.PROBE), "product", "http://127.0.0.1:20001", "prompt"], text=True)
        self.recorder.run(["node", "/other/probe.mjs"], text=True)
        api_env = self.calls[0][1]["env"]
        self.assertEqual(api_env["NODE_OPTIONS"], f"--import={record.HOOK}")
        self.assertEqual(api_env["SOM_API_RAW_DIR"], str(self.out))
        self.assertNotIn("env", self.calls[1][1])
        event = self.recorder.events[0]
        self.assertEqual((self.out / event["stdout"]["file"]).read_bytes(), b"raw reply\n")

    def test_existing_node_options_are_refused(self):
        with self.assertRaises(ValueError):
            self.recorder.run(["node", str(record.PROBE)], env={"NODE_OPTIONS": "unowned-hook"})
        self.assertEqual(self.calls, [])

    def test_snapshot_retains_exact_os_commands_and_absence(self):
        with mock.patch.object(record.os, "getpgid", side_effect=ProcessLookupError(3, "fake absent")):
            self.recorder.snapshot(4321, "after-stop", 20001)
        event = self.recorder.events[0]
        self.assertEqual(event["pgid_error_errno"], 3)
        self.assertIsNone(event["pgid"])
        self.assertEqual(event["listener"]["argv"], ["/usr/sbin/lsof", "-nP", "-iTCP:20001", "-sTCP:LISTEN", "-t"])
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all((self.out / event[key]["stdout"]["file"]).is_file() for key in ("ps", "table", "cwd", "listener")))

    def test_postmaster_bytes_are_saved_before_stop(self):
        data = self.out / "pgdata"
        data.mkdir()
        pid = b"4321\n/fake/pgdata\n0\n20002\n"
        (data / "postmaster.pid").write_bytes(pid)
        with mock.patch.object(self.recorder, "snapshot") as snapshot:
            self.recorder.run(["/frozen/pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop"], text=True)
        self.assertEqual((self.out / "postmaster-before-stop.pid").read_bytes(), pid)
        snapshot.assert_called_once_with(4321, "before-pg-stop")
        self.assertEqual([event["kind"] for event in self.recorder.events], ["postmaster", "pg_ctl"])

    def test_failed_observation_is_recorded_without_interrupting_cleanup(self):
        recorder = record.Recorder(self.out, mock.Mock(side_effect=OSError("fake OS failure")))
        event = recorder.command(["/bin/ps", "-axo", "pid=,ppid=,pgid="])
        self.assertIsNone(event["exit_code"])
        self.assertEqual(recorder.errors, ["OSError"])

    def test_write_and_snapshot_failure_cannot_suppress_original_pg_stop(self):
        data = self.out / "pgdata"
        data.mkdir()
        (data / "postmaster.pid").write_bytes(b"4321\n/fake/pgdata\n0\n20002\n")
        argv = ["/frozen/pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop"]
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("fake full disk")), \
             mock.patch.object(self.recorder, "snapshot", side_effect=OSError("fake ps failure")):
            done = self.recorder.run(argv, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(self.calls[0][0], argv)
        self.assertTrue(self.recorder.errors)
        self.assertEqual(self.recorder.events[-1]["kind"], "pg_ctl")

    def test_snapshot_failure_returns_original_lsof_result_to_owner(self):
        argv = ["/usr/sbin/lsof", "-a", "-p", "4321", "-d", "cwd", "-Fn"]
        with mock.patch.object(self.recorder, "snapshot", side_effect=RuntimeError("fake observation failed")):
            done = self.recorder.run(argv, text=True)
        self.assertEqual(done.stdout, "raw reply\n")
        self.assertEqual(self.recorder.errors, ["RuntimeError"])

    def test_runpy_uses_the_frozen_path_and_original_argv(self):
        repo = self.out / "repo"
        runner = repo / "som-rs/tests/react-output-v1/run_api_contract.py"
        probe, hook = runner.with_name("api_contract.mjs"), repo / "hook.mjs"
        product, case, work = repo / ".local/product", repo / ".local/case/python", repo / ".local/case/api"
        for path in (runner, probe, hook, *(product / "src" / name for name in ("api.ts", "form.ts", "schema.ts"))):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake immutable bytes")
        expected = [str(runner), str(case), str(product), str(work)]

        def fake_runpy(path, *, run_name):
            self.assertEqual((path, run_name, record.sys.argv), (str(runner), "__main__", expected))
            work.mkdir(parents=True)
            (work / "report.json").write_text(json.dumps({"processes": []}))
            return {"__file__": path}

        with mock.patch.multiple(record, REPO=repo, RUNNER=runner, PROBE=probe, HOOK=hook), \
             mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "macOS"}), \
             mock.patch.object(record.runpy, "run_path", side_effect=fake_runpy):
            self.assertEqual(record.main([str(case), str(product), str(work)]), 0)
        self.assertEqual(runner.read_bytes(), b"fake immutable bytes")
        self.assertEqual(json.loads((work.parent / "api-raw/receipt.json").read_bytes())["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
