"""Synthetic owned compile driver fixtures. Run only on GitHub. No real server."""
import io
import json
import os
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import run_experimental_native_compile as d


class DriverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo, self.python, self.rust = [self.root / name for name in ["repo", "python", "rust"]]
        for directory in [self.repo, self.python, self.rust]:
            directory.mkdir()

    def test_python_encoding_matches_only_complete_independent_request_pin(self):
        prompt = {"z": {"second": 2, "first": 1}, "a": [1, False, 1.0]}
        raw = (json.dumps({"format": "som.prompt-json.v1", "prompt": prompt}, indent=2) + "\n").encode()
        self.assertEqual(d.python_request(raw, prompt, "none", d.sha(raw)), ("original-verified-raw", raw))
        compact = d.compact({"format": "som.prompt-json.v1", "prompt": prompt})
        self.assertEqual(d.python_request(raw, prompt, "none", d.sha(compact))[1], compact)
        sorted_raw = d.compact({"format": "som.prompt-json.v1", "prompt": d.sorted_objects(prompt)})
        self.assertEqual(d.python_request(raw, prompt, "none", d.sha(sorted_raw))[0], "compact-recursive-sorted-prompt-keys")
        explicit = d.compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "python"})
        self.assertEqual(list(d.parse(d.python_request(raw, prompt, "python", d.sha(explicit))[1])), ["format", "prompt", "target"])
        with self.assertRaisesRegex(d.Refusal, "no allowed encoding"):
            d.python_request(raw, prompt, "none", "0" * 64)

    def full_plan_fixture(self):
        by_cohort, accepted, checked_files = {}, [], {}
        for cohort, source, count in [("python_public", "public", 127), ("python_legacy", "legacy", 544)]:
            entries = {}
            for index in range(count):
                identity = source + "-" + str(index)
                prompt = {"version": "som.prompt.v7", "z": {"b": index, "a": True}, "a": source}
                raw = d.compact({"format": "som.prompt-json.v1", "prompt": prompt})
                path = f"{cohort}/{index:04d}/compile-json.request.raw"
                file = self.python / path
                file.parent.mkdir(parents=True)
                file.write_bytes(raw)
                locator = {"path": path, "raw_sha256": d.sha(raw)}
                entries[identity] = {"prompt": prompt, "locators": {"compile-json": locator}}
                checked_files[path] = d.sha(raw)
                for variant in ["none", "python"]:
                    body = {"format": "som.prompt-json.v1", "prompt": prompt}
                    if variant == "python":
                        body["target"] = "python"
                    encoded = d.compact(body)
                    accepted.append(("python", {"source": source, "id": identity, "variant": variant,
                                                "row_index": len(accepted), "qa_compile_request_sha256": d.sha(encoded),
                                                "qa_compile_status": 200, "qa_compile_response_sha256": "1" * 64}))
            by_cohort[cohort] = entries
        cohorts = {}
        for cohort, phase, count in [("rust_phase1", "phase1", 115), ("rust_phase2", "phase2", 584)]:
            entries = []
            for index in range(count):
                identity = phase + "-" + str(index)
                prompt = {"version": "som.prompt.v5", "public_fixture": index}
                raw = d.compact({"format": "som.prompt-json.v1", "target": "rust", "prompt": prompt})
                path = f"{cohort}/{index:04d}/baseline-json.request.raw"
                file = self.rust / path
                file.parent.mkdir(parents=True)
                file.write_bytes(raw)
                provenance = {"artifact": d.experiment.BASELINE, "artifact_raw_sha256": d.experiment.BASELINE_SHA256,
                              "phase": phase, "id": identity, "check": "compile_equal", "accepted_qa_request_sha256": d.sha(raw)}
                entries.append({"id": identity, "index": index, "expected_prompt_sha256": d.value_sha(prompt),
                                "baseline_json_request": {"path": path, "raw_sha256": d.sha(raw), "provenance": provenance}})
                accepted.append(("rust", {"phase": phase, "id": identity, "row_index": len(accepted),
                                          "qa_request_sha256": d.sha(raw), "qa_status": 200, "qa_response_sha256": "2" * 64}))
            cohorts[cohort] = entries
        manifest = {"version": "som.public-rust-input-completion.v1", "tested_sha": "a" * 40,
                    "product_acceptance_passed": False, "paired_request_set_ready": True,
                    "golden_export_sha256": d.mapper.GOLDENS_SHA256,
                    "baseline_artifact": {"path": d.experiment.BASELINE, "raw_sha256": d.experiment.BASELINE_SHA256},
                    "cohorts": cohorts}
        (self.rust / "manifest.json").write_text(json.dumps(manifest))
        def read(path, expected):
            raw = (self.python / path).read_bytes()
            d.require(d.sha(raw) == expected, "synthetic verified input changed")
            return raw
        reused = SimpleNamespace(by_cohort=by_cohort, manifest_sha256="f" * 64, read=read, verify=lambda: None)
        return accepted, reused

    def jobs(self):
        accepted, reused = self.full_plan_fixture()
        with patch.object(d.mapper, "load_goldens", return_value={}), patch.object(d.mapper, "Reused", return_value=reused):
            jobs, _record = d.prepare_jobs(self.repo, self.python, self.rust, accepted, "a" * 40)
        return jobs, accepted, reused

    def test_full_2041_row_plan_and_continue_after_one_exact_reply_failure(self):
        jobs, _accepted, _reused = self.jobs()
        self.assertEqual(len(jobs), 2041)
        self.assertEqual({target: sum(job["target"] == target for job in jobs) for target in d.EXPECTED}, d.EXPECTED)
        self.assertTrue(all("input_error" not in job and d.sha(job["raw"]) == job["expected_request_sha256"] for job in jobs))
        calls = []
        def verify(target, identity, status, raw):
            calls.append((target, identity))
            if len(calls) == 1000:
                raise ValueError("synthetic exact-byte failure")
            return SimpleNamespace(report={"state": "experimental-unsealed", "product_acceptance": False, "current_raw_sha256": d.sha(raw)})
        checker = SimpleNamespace(verify_response=verify)
        service = SimpleNamespace(call=lambda route, raw: (200, b'{"synthetic":true}', {"owned": True}))
        out = self.root / "results"
        out.mkdir()
        records = d.run_rows(jobs, checker, service, out)
        self.assertEqual(len(records), 2041)
        self.assertEqual(len(calls), 2041)
        self.assertEqual(sum(row["state"] == "passed" for row in records), 2040)
        self.assertEqual(records[999]["state"], "failed")
        self.assertEqual((out / "rows/0999/response.raw").read_bytes(), b'{"synthetic":true}')
        self.assertTrue((out / "rows/2040/result.json").is_file())

    def test_rust_rehashed_locator_cannot_change_accepted_request_bytes(self):
        accepted, reused = self.full_plan_fixture()
        file = self.rust / "rust_phase2/0000/baseline-json.request.raw"
        file.write_bytes((json.dumps(json.loads(file.read_bytes()), indent=2) + "\n").encode())
        manifest = self.rust / "manifest.json"
        report = json.loads(manifest.read_bytes())
        report["cohorts"]["rust_phase2"][0]["baseline_json_request"]["raw_sha256"] = d.sha(file.read_bytes())
        manifest.write_text(json.dumps(report))
        with patch.object(d.mapper, "load_goldens", return_value={}), patch.object(d.mapper, "Reused", return_value=reused):
            jobs, _ = d.prepare_jobs(self.repo, self.python, self.rust, accepted, "a" * 40)
        self.assertEqual(sum("input_error" in row for row in jobs), 1)
        self.assertNotIn("raw", jobs[1342 + 115])

    def test_missing_or_reordered_completed_scope_refuses(self):
        accepted, reused = self.full_plan_fixture()
        file = self.rust / "manifest.json"
        report = json.loads(file.read_bytes())
        report["cohorts"]["rust_phase2"].pop()
        file.write_text(json.dumps(report))
        with patch.object(d.mapper, "load_goldens", return_value={}), patch.object(d.mapper, "Reused", return_value=reused):
            with self.assertRaisesRegex(d.Refusal, "row order differs"):
                d.prepare_jobs(self.repo, self.python, self.rust, accepted, "a" * 40)

    def test_planning_interruption_propagates_instead_of_becoming_an_input_error(self):
        accepted, reused = self.full_plan_fixture()
        with patch.object(d.mapper, "load_goldens", return_value={}), patch.object(d.mapper, "Reused", return_value=reused), patch.object(d, "python_request", side_effect=d.DriverInterrupted("cancel")):
            with self.assertRaises(d.DriverInterrupted):
                d.prepare_jobs(self.repo, self.python, self.rust, accepted, "a" * 40)

    def test_readiness_wait_does_not_swallow_interrupted_oserror(self):
        service = object.__new__(d.OwnedService)
        service.out = self.root
        service.argv, service.environment = ["native", "serve"], {"PATH": "/usr/bin:/bin"}
        service.ownership = Mock(side_effect=d.DriverInterrupted("cancel"))
        process = SimpleNamespace(pid=42, poll=lambda: None)
        with patch.object(d.subprocess, "Popen", return_value=process), patch.object(d, "process_stamp", return_value={"start_ticks": 123}):
            try:
                with self.assertRaises(d.DriverInterrupted):
                    service.start()
            finally:
                service.log.close()

    def proc_fixture(self):
        proc = self.root / "proc"
        (proc / "net").mkdir(parents=True)
        (proc / "42/fd").mkdir(parents=True)
        fields = ["0"] * 20
        fields[0], fields[1], fields[2], fields[3], fields[19] = "S", "1", "42", "42", "123"
        (proc / "42/stat").write_text("42 (owned service) " + " ".join(fields))
        executable = self.root / "binary"
        executable.write_bytes(b"synthetic ELF identity")
        (proc / "42/exe").symlink_to(executable)
        (proc / "42/fd/3").symlink_to("socket:[777]")
        (proc / "net/tcp").write_text("header\n0: 0100007F:4A1A 00000000:0000 0A 0 0 0 0 0 777\n")
        (proc / "net/tcp6").write_text("header\n")
        return proc, d.file_identity(executable)

    def test_owned_socket_needs_exact_pid_executable_inode_and_loopback_listener(self):
        proc, binary = self.proc_fixture()
        stamp = d.process_stamp(42, proc)
        receipt = d.socket_owner(42, 18970, stamp, binary, proc)
        self.assertEqual(receipt["listener"]["inode"], "777")
        (proc / "net/tcp").write_text("header\n0: 00000000:4A1A 00000000:0000 0A 0 0 0 0 0 777\n")
        with self.assertRaisesRegex(d.Refusal, "exact loopback"):
            d.socket_owner(42, 18970, stamp, binary, proc)

    def test_forbidden_or_occupied_port_never_launches_or_touches_listener(self):
        for port in [8765, 18765, 0, 65536]:
            with patch.object(d.sys, "platform", "linux"), patch.object(d, "listeners") as found, patch.object(d.subprocess, "Popen") as popen:
                with self.assertRaises(d.Refusal):
                    d.OwnedService(self.root / "unused", self.root, {}, port, self.root)
                found.assert_not_called()
                popen.assert_not_called()
        with patch.object(d.sys, "platform", "linux"), patch.object(d, "listeners", return_value=[{"occupied": True}]), patch.object(d.socket, "socket") as sock:
            with self.assertRaisesRegex(d.Refusal, "occupied"):
                d.OwnedService(self.root / "unused", self.root, {}, 18970, self.root)
            sock.assert_not_called()

    def test_no_http_request_occurs_without_fresh_socket_ownership_proof(self):
        service = object.__new__(d.OwnedService)
        service.port, service.opener = 18970, Mock()
        service.ownership = Mock(side_effect=d.Refusal("owned socket absent"))
        with self.assertRaisesRegex(d.Refusal, "owned socket absent"):
            service.call("/v1/compile", b'{"format":"som.prompt-json.v1"}')
        service.opener.open.assert_not_called()

    def test_cleanup_signals_only_original_owned_group_and_refuses_reused_pid(self):
        process = SimpleNamespace(pid=42, returncode=None)
        process.poll = lambda: process.returncode
        process.wait = lambda timeout: setattr(process, "returncode", -15)
        service = object.__new__(d.OwnedService)
        service.process, service.log, service.stamp = process, None, {"start_ticks": 123}
        stamp = {"start_ticks": 123, "pgrp": 42, "session": 42}
        with patch.object(d, "process_stamp", return_value=stamp), patch.object(d.os, "killpg") as kill, patch.object(Path, "iterdir", return_value=iter([])):
            receipt = service.close()
        self.assertTrue(receipt["cleanup_passed"])
        kill.assert_called_once_with(42, signal.SIGTERM)
        process.returncode = None
        with patch.object(d, "process_stamp", return_value={**stamp, "start_ticks": 999}), patch.object(d.os, "killpg") as kill:
            receipt = service.close()
        self.assertFalse(receipt["cleanup_passed"])
        kill.assert_not_called()

    def test_source_snapshot_rechecks_bytes_instead_of_cached_metadata(self):
        file = self.repo / "source.rs"
        file.write_bytes(b"same")
        repository = SimpleNamespace(root=self.repo, pins={"source.rs": d.sha(b"same")}, commit="a" * 40,
                                     git=lambda *args: ("a" * 40 + "\n").encode())
        checker = SimpleNamespace(repo=repository)
        self.assertEqual(d.source_snapshot(checker)["source.rs"], d.sha(b"same"))
        info = file.stat()
        file.write_bytes(b"edit")
        os.utime(file, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaisesRegex(d.Refusal, "source changed"):
            d.source_snapshot(checker)

    def test_asset_health_hash_is_canonical_while_raw_tree_hash_remains_separate(self):
        assets = self.root / "assets"
        assets.mkdir()
        file = assets / "manifest.json"
        value = {"z": [1, False, 1.0], "a": "public fixture"}
        file.write_bytes(d.compact(value))
        first_raw = d.file_identity(file)["sha256"]
        first_canonical = d.asset_manifest_canonical_sha(assets)
        file.write_text(json.dumps(value, indent=4) + "\n")
        self.assertNotEqual(first_raw, d.file_identity(file)["sha256"])
        self.assertEqual(first_canonical, d.asset_manifest_canonical_sha(assets))
        self.assertEqual(first_canonical, d.experiment.value_sha(value))

    def lifecycle_fixture(self, *, interrupt=False, break_row_record=False):
        raw = b'{}'
        identity = {"source": "public", "id": "public-fixture", "variant": "none"}
        accepted = [("python", {**identity, "row_index": 0, "qa_compile_request_sha256": d.sha(raw)})]
        keys = [("python", "public", "public-fixture", "none")]
        job = {"target": "python", "identity": identity, "accepted_row_index": 0,
               "expected_request_sha256": d.sha(raw), "raw": raw}
        tools = {key: self.root / name for key, name in d.TOOLS.items()}
        assets, out = self.root / "assets", self.root / "experiment"
        flat = {"sha256": "f" * 64, "size": 1, "mode": 0o755, "device": 1, "inode": 1}
        tree_sha = d.sha(("bin/prettier.cjs\0" + flat["sha256"] + "\n").encode())
        artifact = {"tools": [{"tool": name,
                               "host": "platform-independent JavaScript" if name == "prettier" else d.experiment.HOST,
                               "tree_sha256" if name == "prettier" else "executable_sha256": tree_sha if name == "prettier" else flat["sha256"]}
                              for name in d.TOOLS.values()]}
        reads = []
        def repo_read(path):
            reads.append(path)
            if path == d.DRIVER:
                return Path(d.__file__).read_bytes()
            if path == d.experiment.ARTIFACTS:
                return d.compact(artifact)
            return b"synthetic committed source"
        repository = SimpleNamespace(commit="a" * 40, read=repo_read)
        checker = SimpleNamespace(repo=repository, rows={keys[0]: None}, after={"python_backend": "b" * 64},
                                  source_record=lambda: {"synthetic": True},
                                  verify_response=lambda *args: SimpleNamespace(report={"product_acceptance": False}))
        service = Mock()
        service.argv, service.environment = ["native-target-aware", "serve"], {"PATH": "/usr/bin:/bin"}
        service.close.return_value = {"cleanup_passed": True, "owned_pid": 42}
        if interrupt:
            def interrupted_start():
                # Invoke the installed handler. No OS signal or real child runs.
                d.signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            service.start.side_effect = interrupted_start
        else:
            service.start.return_value = {"owned": True}
        health = {"status": "ok", "model_loaded": False, "blocks_loaded": True,
                  "backend_sha256": "b" * 64, "asset_manifest_sha256": "e" * 64,
                  "formatter_sha256": "f" * 64}
        service.call.side_effect = lambda route, raw=None: (200, d.compact(health) if route == "/healthz" else b"{}", {"owned": True})
        real_dump, failed = d.dump, []
        def dump(path, value):
            if break_row_record and path.name == "result.json" and not failed:
                failed.append(True)
                raise OSError("synthetic row write failure")
            real_dump(path, value)
        with ExitStack() as stack:
            stack.enter_context(patch.object(d, "baseline", return_value=({}, accepted, keys)))
            factory = stack.enter_context(patch.object(d.experiment, "ExperimentalChecker", return_value=checker))
            stack.enter_context(patch.object(d, "prepare_jobs", return_value=([job], {})))
            stack.enter_context(patch.object(d, "source_snapshot", return_value={"source": "c" * 64}))
            stack.enter_context(patch.object(d, "file_identity", return_value=flat))
            stack.enter_context(patch.object(d, "tree_identity", side_effect=lambda path: {"manifest.json" if path == assets else "bin/prettier.cjs": flat}))
            stack.enter_context(patch.object(d, "asset_manifest_canonical_sha", return_value="e" * 64))
            stack.enter_context(patch.object(d, "OwnedService", return_value=service))
            stack.enter_context(patch.object(d, "dump", side_effect=dump))
            popen = stack.enter_context(patch.object(d.subprocess, "Popen"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            report = d.run(self.repo, self.python, self.rust, self.root / "native", assets, tools, 18970, out, "a" * 40)
            factory.assert_called_once_with(self.repo)
            popen.assert_not_called()
        service.close.assert_called_once()
        self.assertTrue((out / "manifest.json").is_file())
        self.assertTrue(report["cleanup"]["cleanup_passed"])
        self.assertFalse(report["experiment_complete"])
        self.assertFalse(report["product_acceptance_passed"])
        self.assertIn("som-rs/som-targets/src/main.rs", reads)
        self.assertNotIn("som-rs/src/main.rs", reads)
        return report

    def test_signal_cancellation_closes_owned_service_and_keeps_manifest(self):
        previous = signal.getsignal(signal.SIGTERM)
        report = self.lifecycle_fixture(interrupt=True)
        self.assertEqual(report["interrupted_signal"], signal.SIGTERM)
        self.assertEqual(report["setup_error"], "DriverInterrupted")
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["state"], "unrun")

    def test_midrow_recording_failure_still_cleans_up_and_records_failed_row(self):
        report = self.lifecycle_fixture(break_row_record=True)
        self.assertEqual(report["setup_error"], "OSError")
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["state"], "recording_failed")


if __name__ == "__main__":
    unittest.main()
