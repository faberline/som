"""Synthetic runtime qualification checks. Execute on GitHub only."""

import copy
import hashlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import call, patch

SPEC = importlib.util.spec_from_file_location("qualify_runtime", Path(__file__).with_name("qualify_runtime.py"))
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(files, links=()):
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w:gz") as output:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o755 if name.endswith("/configure") else 0o644
            output.addfile(member, io.BytesIO(data))
        for name, target in links:
            member = tarfile.TarInfo(name)
            member.type, member.linkname = tarfile.SYMTYPE, target
            output.addfile(member)
    return blob.getvalue()


def git_oid(body):
    return hashlib.sha1(b"blob " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()


class GitOnlyRunner:
    """Fake Git metadata. Any builder or native process call fails the fixture."""

    def __init__(self, out, report, committed):
        self.out, self.report, self.committed = out, report, dict(committed)
        self.revision, self.mode, self.corrupt_blob, self.top = "1" * 40, b"100644", False, None

    def run(self, *args, **kwargs):
        raise AssertionError("builder or native process must not run")

    def run_bytes(self, argv, label, limit):
        argv = [str(value) for value in argv]
        if argv[1:3] != ["--no-replace-objects", "-C"]:
            raise AssertionError("Git replacement objects must be disabled")
        operation = argv[4]
        if operation == "rev-parse":
            body = (self.top or argv[3]).encode() + b"\n" if argv[-1] == "--show-toplevel" else self.revision.encode() + b"\n"
        elif operation == "ls-tree":
            relative = argv[-1]
            body = self.mode + b" blob " + git_oid(self.committed[relative]).encode() + b"\t" + relative.encode() + b"\0"
        elif operation == "cat-file":
            selected = next(value for value in self.committed.values() if git_oid(value) == argv[-1])
            body = str(len(selected)).encode() + b"\n" if argv[-2] == "-s" else selected
            if self.corrupt_blob and argv[-2] == "blob":
                body = b"x" * len(body)
        else:
            raise AssertionError("unexpected Git metadata call")
        if len(body) > limit:
            raise AssertionError("fixture output exceeds bound")
        self.report["commands"].append({"argv": argv, "exit_code": 0, "stdout_sha256": digest(body)})
        return body


class RuntimeQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.manifest = self.repo / q.PINS_REL
        self.script = self.repo / q.SCRIPT_REL
        self.manifest.parent.mkdir(parents=True)
        self.script.write_bytes(b"# synthetic builder fixture, never executed\n")
        self.out = self.root / "run"
        self.blobs, rows = {}, []
        for name, version in q.EXPECTED.items():
            root = name + "-fixture"
            kind = "source" if name in {"python", "postgresql", "redis"} else "licenses" if name == "uv-licenses" else "binary"
            files = {root + "/LICENSE": b"license"}
            row = {"name": name, "version": version, "root": root, "kind": kind,
                   "url": "https://www.python.org/fixture", "licenses": {root + "/LICENSE": digest(b"license")}}
            if kind == "source":
                files[root + "/configure"] = b"fixture configure, never executed"
            if kind == "binary":
                executable = "mongod" if name == "mongodb" else "uv"
                body = b"\x7fELF fixture " + executable.encode()
                files[root + ("/bin/" if name == "mongodb" else "/") + executable] = body
                row["executables"] = {root + ("/bin/" if name == "mongodb" else "/") + executable: {"path": "bin/" + executable, "sha256": digest(body)}}
            blob = archive(files)
            row["sha256"] = digest(blob)
            self.blobs[name] = blob
            rows.append(row)
        self.data = {"version": "som.native-product-runtime-source-pins.v1", "archives": rows}
        self.manifest.write_text(json.dumps(self.data))
        self.committed = {q.SCRIPT_REL: self.script.read_bytes(), q.PINS_REL: self.manifest.read_bytes()}
        self.environment = patch.dict(os.environ, {"RUNNER_TEMP": str(self.root), "GITHUB_ACTIONS": "true"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def member(self, name):
        return next(row for row in self.data["archives"] if row["name"] == name)

    def stored_archive(self, name):
        path = self.root / (name + ".tar")
        path.write_bytes(self.blobs[name])
        return path

    def test_complete_manifest_and_exact_binary_member_paths(self):
        self.assertEqual(q.manifest_data(self.manifest.read_bytes()), self.data)
        row = self.member("uv")
        record = next(iter(row["executables"].values()))
        record["path"] = "bin/imposter"
        self.manifest.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(q.Refusal, "executable pin"):
            q.manifest_data(self.manifest.read_bytes())

    def test_manifest_duplicate_keys_and_traversal_are_refused(self):
        self.manifest.write_text('{"version":"a","version":"b"}')
        with self.assertRaisesRegex(q.Refusal, "duplicate"):
            q.manifest_data(self.manifest.read_bytes())
        self.member("python")["licenses"]["python-fixture/../outside"] = "0" * 64
        self.manifest.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(q.Refusal, "unsafe"):
            q.manifest_data(self.manifest.read_bytes())

    def test_manifest_requires_exact_versions_and_all_components(self):
        for mutation in ("missing", "version", "duplicate"):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(self.data)
                if mutation == "missing":
                    value["archives"].pop()
                elif mutation == "version":
                    value["archives"][0]["version"] = "latest"
                else:
                    value["archives"][-1] = copy.deepcopy(value["archives"][0])
                self.manifest.write_text(json.dumps(value))
                with self.assertRaises(q.Refusal):
                    q.manifest_data(self.manifest.read_bytes())

    def test_existing_outside_and_traversal_outputs_refused(self):
        existing = self.root / "existing"
        existing.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(self.repo, target_is_directory=True)
        for out in (existing, self.repo / "new", self.root / "../escape", linked / "new", self.root.parent / "outside"):
            with self.subTest(out=out):
                with self.assertRaises(q.Refusal):
                    q.checked_output(out, self.repo)

    def test_new_owned_output_is_allowed(self):
        q.checked_output(self.out, self.repo)
        self.assertFalse(self.out.exists())

    def test_verified_sources_and_licenses_are_preserved(self):
        self.out.mkdir()
        row = self.member("python")
        record = q.admit_archive(row, self.stored_archive("python"), self.out)
        self.assertEqual((self.out / "source/python/configure").read_bytes(), b"fixture configure, never executed")
        self.assertEqual((self.out / "licenses/python/python-fixture/LICENSE").read_bytes(), b"license")
        self.assertEqual(record["licenses"], row["licenses"])

    def test_archive_and_license_changes_are_refused_without_execution(self):
        self.out.mkdir()
        path = self.stored_archive("python")
        path.write_bytes(path.read_bytes() + b"changed")
        with patch.object(q.tarfile, "open") as opened:
            with self.assertRaisesRegex(q.Refusal, "archive"):
                q.admit_archive(self.member("python"), path, self.out)
        opened.assert_not_called()
        path.write_bytes(self.blobs["python"])
        row = copy.deepcopy(self.member("python"))
        row["licenses"][next(iter(row["licenses"]))] = "0" * 64
        with self.assertRaisesRegex(q.Refusal, "license"):
            q.admit_archive(row, path, self.out)

    def test_unsafe_paths_and_selected_symlinks_are_refused(self):
        cases = [
            ({"../escape": b"bad"}, ()),
            ({"uv-fixture/LICENSE": b"license"}, (("uv-fixture/uv", "LICENSE"),)),
        ]
        self.out.mkdir()
        for index, (files, links) in enumerate(cases):
            row = copy.deepcopy(self.member("uv"))
            data = archive(files, links)
            row["sha256"] = digest(data)
            path = self.root / f"unsafe-{index}.tar"
            path.write_bytes(data)
            with self.assertRaises(q.Refusal):
                q.admit_archive(row, path, self.out)

    def test_escaping_source_symlink_is_refused(self):
        row = copy.deepcopy(self.member("python"))
        blob = archive({"python-fixture/LICENSE": b"license"}, (("python-fixture/link", "../../outside"),))
        row["sha256"] = digest(blob)
        path = self.root / "source-link.tar"
        path.write_bytes(blob)
        self.out.mkdir()
        with self.assertRaisesRegex(q.Refusal, "escapes"):
            q.admit_archive(row, path, self.out)

    def test_safe_source_symlink_is_preserved_and_chain_escape_is_refused(self):
        row = copy.deepcopy(self.member("python"))
        self.out.mkdir()
        blob = archive({"python-fixture/LICENSE": b"license", "python-fixture/a": b"value"},
                       (("python-fixture/alias", "a"),))
        row["sha256"] = digest(blob)
        path = self.root / "safe-link.tar"
        path.write_bytes(blob)
        q.admit_archive(row, path, self.out)
        self.assertEqual((self.out / "source/python/alias").read_bytes(), b"value")
        other = self.root / "second-run"
        other.mkdir()
        q.write_new(other / "source/outside", b"sentinel")
        blob = archive({"python-fixture/LICENSE": b"license", "python-fixture/outside": b"inside"},
                       (("python-fixture/link", "."), ("python-fixture/escape", "link/../outside")))
        row["sha256"] = digest(blob)
        path = self.root / "chained-link.tar"
        path.write_bytes(blob)
        with self.assertRaises(q.Refusal):
            q.admit_archive(row, path, other)

    def test_duplicate_archive_paths_and_hardlinks_are_refused(self):
        self.out.mkdir()
        for hardlink in (False, True):
            row = copy.deepcopy(self.member("uv"))
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as output:
                for _ in range(1 if hardlink else 2):
                    member = tarfile.TarInfo("uv-fixture/LICENSE")
                    member.size = len(b"license")
                    output.addfile(member, io.BytesIO(b"license"))
                member = tarfile.TarInfo("uv-fixture/uv")
                member.type, member.linkname = tarfile.LNKTYPE, "uv-fixture/LICENSE"
                output.addfile(member)
            row["sha256"] = digest(stream.getvalue())
            path = self.root / f"link-{hardlink}.tar"
            path.write_bytes(stream.getvalue())
            with self.assertRaises(q.Refusal):
                q.admit_archive(row, path, self.out)

    def test_prebuilt_executable_hash_and_size_limits_are_checked(self):
        row = copy.deepcopy(self.member("uv"))
        row["executables"][next(iter(row["executables"]))]["sha256"] = "0" * 64
        self.out.mkdir()
        with self.assertRaisesRegex(q.Refusal, "binary member"):
            q.admit_archive(row, self.stored_archive("uv"), self.out)
        with patch.object(q, "MAX_MEMBER", 2):
            with self.assertRaisesRegex(q.Refusal, "limit"):
                q.admit_archive(self.member("python"), self.stored_archive("python"), self.out)

    def test_tar_metadata_stream_and_parser_reads_are_bounded(self):
        self.out.mkdir()
        row = copy.deepcopy(self.member("python"))
        with patch.object(q, "MAX_EXPANDED", 100):
            with self.assertRaisesRegex(q.Refusal, "expanded tar"):
                q.admit_archive(row, self.stored_archive("python"), self.out)
        with patch.object(q, "MAX_MEMBERS", 1):
            with self.assertRaisesRegex(q.Refusal, "member count"):
                q.admit_archive(row, self.stored_archive("python"), self.out)
        member = tarfile.TarInfo("python-fixture/PaxHeader")
        member.type, member.size = tarfile.XHDTYPE, q.MAX_EXPANDED * 4
        blob = gzip.compress(member.tobuf() + b"\0" * 1024)
        row["sha256"] = digest(blob)
        path = self.root / "oversized-pax.tar.gz"
        path.write_bytes(blob)
        with self.assertRaisesRegex(q.Refusal, "tar parser read"):
            q.admit_archive(row, path, self.out)
        self.assertFalse((self.out / "source").exists())

    def test_untrusted_environment_hash_cannot_change_source_pin(self):
        self.out.mkdir()
        path = self.stored_archive("python")
        path.write_bytes(b"replacement")
        with patch.dict(os.environ, {"PYTHON_SHA256": digest(b"replacement")}):
            with self.assertRaisesRegex(q.Refusal, "archive"):
                q.admit_archive(self.member("python"), path, self.out)

    def test_download_is_bounded_and_uses_the_committed_hash(self):
        row = self.member("uv")
        class Response(io.BytesIO):
            def geturl(self):
                return row["url"]
        for suffix, limit in (("good", q.MAX_ARCHIVE), ("too-large", 2)):
            with self.subTest(suffix=suffix), patch.object(q, "build_opener") as opener, patch.object(q, "MAX_ARCHIVE", limit):
                opener.return_value.open.return_value = Response(self.blobs["uv"])
                destination = self.root / suffix
                if suffix == "good":
                    self.assertEqual(q.download(row, destination)["sha256"], row["sha256"])
                else:
                    with self.assertRaisesRegex(q.Refusal, "limit"):
                        q.download(row, destination)

    def test_fixed_files_bind_to_full_git_blobs_and_keep_committed_pin_bytes(self):
        report = {"commands": []}
        runner = GitOnlyRunner(self.out, report, self.committed)
        with patch.object(q, "__file__", str(self.script)):
            script = q.checked_source_paths(self.repo, self.manifest)
            blobs = q.committed_sources(runner, self.repo, "1" * 40, script, self.manifest)
        self.assertEqual(blobs, self.committed)
        self.manifest.write_bytes(b'{"dirty":true}')
        self.assertEqual(q.manifest_data(blobs[q.PINS_REL]), self.data)
        for relative, body in self.committed.items():
            binding = report["source_bindings"]["files"][relative]
            self.assertTrue(binding["working_bytes_match"])
            self.assertEqual(binding["git_blob_oid"], git_oid(body))
            self.assertEqual(binding["committed_sha256"], digest(body))

    def assert_dirty_source_refused(self, path):
        path.write_bytes(path.read_bytes() + b"\n ")
        report = {}
        def make_runner(out, receipt):
            report.update(receipt=receipt)
            return GitOnlyRunner(out, receipt, self.committed)
        with patch.object(q, "__file__", str(self.script)), patch.object(q, "host"), patch.object(q.os, "chdir"), patch.object(q, "Runner", side_effect=make_runner), patch.object(q, "download") as download:
            with self.assertRaisesRegex(q.Refusal, "working source differs"):
                q.qualify(self.repo, self.manifest, self.out, "1" * 40)
        download.assert_not_called()
        relative = path.relative_to(self.repo).as_posix()
        self.assertFalse(report["receipt"]["source_bindings"]["files"][relative]["working_bytes_match"])
        self.assertEqual(report["receipt"]["trusted_sources"], {})

    def test_dirty_manifest_never_selects_archive_pins(self):
        self.assert_dirty_source_refused(self.manifest)

    def test_dirty_running_script_is_refused_before_archives_or_builders(self):
        self.assert_dirty_source_refused(self.script)

    def test_different_manifest_path_is_refused_even_with_identical_bytes(self):
        other = self.repo / "another-pins.json"
        other.write_bytes(self.committed[q.PINS_REL])
        with patch.object(q, "__file__", str(self.script)), patch.object(q, "host"), patch.object(q, "Runner") as runner:
            with self.assertRaisesRegex(q.Refusal, "fixed committed source-pins"):
                q.qualify(self.repo, other, self.out, "1" * 40)
        runner.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_different_script_path_is_refused_even_with_identical_bytes(self):
        other = self.repo / "another-builder.py"
        other.write_bytes(self.committed[q.SCRIPT_REL])
        with patch.object(q, "__file__", str(other)), patch.object(q, "host"), patch.object(q, "Runner") as runner:
            with self.assertRaisesRegex(q.Refusal, "fixed committed qualify_runtime"):
                q.qualify(self.repo, self.manifest, self.out, "1" * 40)
        runner.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_wrong_head_nonregular_git_entry_and_corrupt_blob_are_refused(self):
        for mutation in ("head", "root", "mode", "blob"):
            with self.subTest(mutation=mutation):
                runner = GitOnlyRunner(self.out, {"commands": []}, self.committed)
                if mutation == "head":
                    runner.revision = "2" * 40
                elif mutation == "root":
                    runner.top = str(self.root / "another-root")
                elif mutation == "mode":
                    runner.mode = b"120000"
                else:
                    runner.corrupt_blob = True
                with self.assertRaises(q.Refusal):
                    q.committed_sources(runner, self.repo, "1" * 40, self.script, self.manifest)

    def test_all_archives_must_pass_before_any_builder_or_native_process(self):
        self.member("mongodb")["licenses"]["mongodb-fixture/LICENSE"] = "0" * 64
        self.manifest.write_text(json.dumps(self.data))
        committed = dict(self.committed)
        committed[q.PINS_REL] = self.manifest.read_bytes()
        def fake_download(row, destination):
            destination.write_bytes(self.blobs[row["name"]])
            return {"sha256": row["sha256"]}
        def make_runner(out, receipt):
            return GitOnlyRunner(out, receipt, committed)
        with patch.object(q, "__file__", str(self.script)), patch.object(q, "host"), patch.object(q, "download", side_effect=fake_download), patch.object(q.os, "chdir"), patch.object(q, "Runner", side_effect=make_runner):
            with self.assertRaisesRegex(q.Refusal, "license"):
                q.qualify(self.repo, self.manifest, self.out, "1" * 40)
        receipt = json.loads((self.out / "receipt.json").read_text())
        self.assertFalse(receipt["product_runtime_ready"])
        self.assertTrue(receipt["source_bindings"]["files"][q.PINS_REL]["working_bytes_match"])
        self.assertTrue(all(command["argv"][4] in {"rev-parse", "ls-tree", "cat-file"} for command in receipt["commands"]))

    def test_admitted_symlink_or_wrong_hash_is_never_executed(self):
        self.out.mkdir()
        tool = self.out / "tool"
        tool.write_bytes(b"\x7fELF fixture")
        tool.chmod(0o755)
        link = self.out / "link"
        link.symlink_to(tool)
        class Runner:
            def run(self, *args, **kwargs):
                raise AssertionError("must not execute")
        runner = Runner()
        runner.out, runner.report = self.out, {"admitted_executables": {}, "commands": []}
        with self.assertRaisesRegex(q.Refusal, "symlink"):
            q.probe(runner, link, "uv")
        with self.assertRaisesRegex(q.Refusal, "trusted member"):
            q.probe(runner, tool, "uv", "0" * 64)

    def test_exact_version_tokens_with_mocked_native_runs(self):
        self.out.mkdir()
        tool = self.out / "uv"
        tool.write_bytes(b"\x7fELF fixture")
        tool.chmod(0o755)
        expected = digest(tool.read_bytes())
        class Runner:
            def __init__(self, out, text):
                self.out, self.text = out, text
                self.report = {"commands": [], "admitted_executables": {}}
            def run(self, argv, *args, **kwargs):
                self.report["commands"].append({"exit_code": 0})
                return self.text if argv[-1] == "--version" else ""
        for text in ("uv 0.9.9 (fixture)\n", "uv 0.9.90\n", "uv 0.9.9.1\n", "uv 0.9.9dev\n"):
            runner = Runner(self.out, text)
            with self.subTest(text=text):
                if text.startswith("uv 0.9.9 "):
                    record = q.probe(runner, tool, "uv", expected)
                    self.assertEqual(record["version_exit_code"], 0)
                    self.assertEqual(record["sha256"], expected)
                    self.assertEqual(record["admitted_for"], "qualification_commands_only")
                else:
                    with self.assertRaisesRegex(q.Refusal, "wrong observed version"):
                        q.probe(runner, tool, "uv", expected)

    def test_timeout_signals_only_the_started_process_group_and_keeps_log_hashes(self):
        self.out.mkdir()
        report = {"commands": []}
        runner = q.Runner(self.out, report)
        class Process:
            pid, returncode, calls = 424242, None, 0
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["fixture"], timeout)
                self.returncode = -15
                return self.returncode
        with patch.object(q.subprocess, "Popen", return_value=Process()), patch.object(q.os, "killpg", side_effect=[None, ProcessLookupError()]) as send:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run(["/never-executed"], "timeout", timeout=1)
        self.assertEqual(send.call_args_list, [call(424242, signal.SIGTERM), call(424242, 0)])
        self.assertEqual(report["commands"][0]["exit_code"], -15)
        self.assertEqual(report["commands"][0]["stdout_sha256"], digest(b""))
        self.assertEqual(report["commands"][0]["stderr_sha256"], digest(b""))

    def test_leader_exit_does_not_hide_a_remaining_owned_child_group(self):
        self.out.mkdir()
        report = {"commands": []}
        runner = q.Runner(self.out, report)
        class Process:
            pid, returncode, calls = 424243, None, 0
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["fixture"], timeout)
                self.returncode = -15
                return self.returncode
        with patch.object(q.subprocess, "Popen", return_value=Process()), patch.object(q.os, "killpg", side_effect=[None, None, None, ProcessLookupError()]) as send:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run(["/never-executed"], "remaining-child", timeout=1)
        self.assertEqual(send.call_args_list, [call(424243, signal.SIGTERM), call(424243, 0),
                                               call(424243, signal.SIGKILL), call(424243, 0)])
        self.assertTrue(report["commands"][0]["cleanup"]["group_gone"])
        self.assertEqual(report["commands"][0]["cleanup"]["signals"], [int(signal.SIGTERM), int(signal.SIGKILL)])

    def test_inventory_does_not_admit_or_execute_aliases(self):
        self.out.mkdir()
        installed = self.out / "install"
        installed.mkdir()
        binary = installed / "python3.12"
        binary.write_bytes(b"\x7fELF fixture")
        binary.chmod(0o755)
        (installed / "python3").symlink_to("python3.12")
        result = q.inventory(installed, self.out)
        self.assertEqual(result["install/python3"]["kind"], "alias_not_admitted")
        self.assertFalse(result["install/python3"]["qualification_probe_executed"])
        self.assertFalse(result["install/python3"]["runtime_admitted"])
        self.assertEqual(result["install/python3.12"]["sha256"], digest(b"\x7fELF fixture"))


if __name__ == "__main__":
    unittest.main()
