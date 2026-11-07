"""Synthetic complete Rust tool qualification tests. Execute only on GitHub."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[4]
subject_path = HERE / "qualify_rust_tools.py"
s = ModuleType("synthetic_qualify_rust_tools")
s.__file__, s.__cached__, s.__package__ = str(subject_path), None, ""
sys.modules[s.__name__] = s
exec(compile(subject_path.read_bytes(), str(subject_path), "exec", dont_inherit=True), s.__dict__)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ArchiveAndScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "out"
        self.out.mkdir()
        for name in ("install", "downloads", "licenses", "publisher-records"):
            (self.out / name).mkdir()
        self.bodies = {"fixture/cargo/bin/cargo": b"\x7fELF\x02\x01\x01 synthetic bytes",
                       "fixture/cargo/lib/runtime.so": b"complete runtime library",
                       "fixture/LICENSE": b"public synthetic notice",
                       "fixture/install.sh": b"never execute this script"}
        self.manifest = "file:bin/cargo\nfile:lib/runtime.so\n"
        self.bodies["fixture/cargo/manifest.in"] = self.manifest.encode()
        self.directories = {"fixture": 0o755, "fixture/cargo": 0o755,
                            "fixture/cargo/bin": 0o755, "fixture/cargo/lib": 0o755}
        self.row = {"member_root": "fixture", "component_dir": "cargo", "install_manifest_text": self.manifest,
                    "all_regular_files": {name: {"bytes": len(body), "sha256": sha(body),
                                                 "mode": 0o755 if name.endswith(("/cargo", "install.sh")) else 0o644}
                                          for name, body in self.bodies.items()},
                    "all_aliases": {}, "directories": self.directories, "licenses": ["fixture/LICENSE"]}

    def archive(self, bodies=None, additions=()):
        path = self.out / "downloads/cargo.tar.xz"
        with tarfile.open(path, "w:xz") as archive:
            for name, mode in sorted(self.directories.items()):
                info = tarfile.TarInfo(name)
                info.type, info.mode = tarfile.DIRTYPE, mode
                archive.addfile(info)
            for name, body in sorted((bodies or self.bodies).items()):
                info = tarfile.TarInfo(name)
                info.mode, info.size = self.row["all_regular_files"].get(name, {}).get("mode", 0o644), len(body)
                archive.addfile(info, io.BytesIO(body))
            for info, body in additions:
                archive.addfile(info, io.BytesIO(body) if body is not None else None)
        self.row.update(publisher_sha256=sha(path.read_bytes()), archive_bytes=path.stat().st_size)
        return path

    def install(self):
        path = self.archive()
        profile = {"components": {"cargo": self.row}}
        expected = s.expected_install(profile)
        with patch.object(s.subprocess, "Popen", side_effect=AssertionError("installer must not execute a program")):
            result = s.admit_component("cargo", self.row, path, self.out, expected)
        return profile, expected, result

    def test_complete_manifest_installs_runtime_and_keeps_script_as_data(self):
        profile, expected, result = self.install()
        self.assertEqual(set(expected), {"bin/cargo", "lib/runtime.so"})
        self.assertEqual(s.verify_install_tree(self.out, profile)["files"], expected)
        self.assertEqual(len(result["regular_files"]), len(self.bodies))
        script = self.out / "publisher-records/cargo/fixture/install.sh"
        self.assertEqual(script.read_bytes(), self.bodies["fixture/install.sh"])
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o644)
        self.assertEqual((self.out / "licenses/cargo/fixture/LICENSE").read_bytes(), self.bodies["fixture/LICENSE"])

    def test_manifest_cannot_omit_runtime_scope(self):
        self.row["install_manifest_text"] = "file:bin/cargo\n"
        with self.assertRaises(s.Refusal):
            s.expected_install({"components": {"cargo": self.row}})

    def test_duplicate_manifest_destination_is_refused(self):
        self.row["install_manifest_text"] += "file:bin/cargo\n"
        with self.assertRaises(s.Refusal):
            s.expected_install({"components": {"cargo": self.row}})

    def test_manifest_parent_traversal_is_refused(self):
        self.row["install_manifest_text"] = "file:../outside\n"
        with self.assertRaises(s.Refusal):
            s.expected_install({"components": {"cargo": self.row}})

    def test_archive_unknown_file_is_refused_before_install(self):
        bodies = dict(self.bodies, **{"fixture/unknown": b"extra"})
        path = self.archive(bodies)
        with self.assertRaises(s.Refusal):
            s.admit_component("cargo", self.row, path, self.out, s.expected_install({"components": {"cargo": self.row}}))
        self.assertEqual(list((self.out / "install").iterdir()), [])

    def test_archive_duplicate_member_is_refused(self):
        duplicate = tarfile.TarInfo("fixture/LICENSE")
        duplicate.size = 1
        path = self.archive(additions=[(duplicate, b"x")])
        with self.assertRaises(s.Refusal):
            s.admit_component("cargo", self.row, path, self.out, {})

    def test_archive_links_and_special_members_are_refused(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                info = tarfile.TarInfo("fixture/link")
                info.type, info.linkname = kind, "cargo/bin/cargo"
                path = self.archive(additions=[(info, None)])
                with self.assertRaises(s.Refusal):
                    s.admit_component("cargo", self.row, path, self.out, {})

    def test_archive_member_body_hash_is_required(self):
        bodies = dict(self.bodies)
        bodies["fixture/cargo/bin/cargo"] = b"x" * len(bodies["fixture/cargo/bin/cargo"])
        path = self.archive(bodies)
        with self.assertRaises(s.Refusal):
            s.admit_component("cargo", self.row, path, self.out, s.expected_install({"components": {"cargo": self.row}}))

    def test_changed_whole_archive_is_refused(self):
        path = self.archive()
        with path.open("ab") as stream:
            stream.write(b"unexpected")
        with self.assertRaises(s.Refusal):
            s.admit_component("cargo", self.row, path, self.out, {})

    def test_installed_tree_requires_runtime_file(self):
        profile, _, _ = self.install()
        (self.out / "install/lib/runtime.so").unlink()
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)

    def test_installed_tree_refuses_unknown_file_or_empty_directory(self):
        profile, _, _ = self.install()
        extra = self.out / "install/unlisted"
        extra.write_bytes(b"extra")
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)
        extra.unlink()
        extra.mkdir()
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)

    def test_installed_tree_refuses_changed_mode_and_body(self):
        profile, _, _ = self.install()
        file = self.out / "install/bin/cargo"
        file.chmod(0o644)
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)
        file.chmod(0o755)
        file.write_bytes(b"x" * file.stat().st_size)
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)

    def test_installed_tree_refuses_symlink_and_hardlink(self):
        profile, _, _ = self.install()
        file = self.out / "install/bin/cargo"
        other = self.root / "same-bytes"
        other.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(other)
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)
        file.unlink()
        os.link(other, file)
        file.chmod(0o755)
        with self.assertRaises(s.Refusal):
            s.verify_install_tree(self.out, profile)

    def test_identical_source_owned_overlap_is_explicit(self):
        second = copy.deepcopy(self.row)
        second["component_dir"] = "clippy-preview"
        second["all_regular_files"] = {name.replace("fixture/cargo/", "fixture/clippy-preview/"): pin
                                       for name, pin in second["all_regular_files"].items()}
        expected = s.expected_install({"components": {"cargo": self.row, "clippy-preview": second}})
        self.assertEqual(expected["bin/cargo"]["components"], ["cargo", "clippy-preview"])
        second["all_regular_files"]["fixture/clippy-preview/bin/cargo"]["sha256"] = "0" * 64
        with self.assertRaises(s.Refusal):
            s.expected_install({"components": {"cargo": self.row, "clippy-preview": second}})

    def test_conflicting_existing_install_merge_is_refused(self):
        path = self.archive()
        file = self.out / "install/bin/cargo"
        file.parent.mkdir()
        file.write_bytes(b"different")
        with self.assertRaises(s.Refusal):
            s.admit_component("cargo", self.row, path, self.out, s.expected_install({"components": {"cargo": self.row}}))

    def test_six_tool_launch_check_rehashes_each_actual_file(self):
        bodies = {"fixture/cargo/bin/" + name: b"\x7fELF\x02\x01\x01 " + name.encode() for name in s.NAMES}
        manifest = "".join("file:bin/" + name + "\n" for name in sorted(s.NAMES))
        bodies["fixture/cargo/manifest.in"] = manifest.encode()
        row = {"member_root": "fixture", "component_dir": "cargo", "install_manifest_text": manifest,
               "all_regular_files": {name: {"bytes": len(raw), "sha256": sha(raw), "mode": 0o755 if "/bin/" in name else 0o644}
                                     for name, raw in bodies.items()}}
        profile = {"components": {"cargo": row}, "executables": {name: {"path": "bin/" + name} for name in s.NAMES}}
        (self.out / "install/bin").mkdir()
        for name in s.NAMES:
            path = self.out / "install/bin" / name
            path.write_bytes(bodies["fixture/cargo/bin/" + name])
            path.chmod(0o755)
        tools = s.verify_tools(self.out, profile)
        self.assertEqual(set(tools), s.NAMES)
        tools["clippy-driver"].write_bytes(b"changed")
        with self.assertRaises(s.Refusal):
            s.verify_tools(self.out, profile)


class SourceAndProfileTests(unittest.TestCase):
    def profile(self):
        return json.loads((REPO / s.PROFILE).read_bytes())

    def test_actual_full_profile_has_all_components_and_six_tools(self):
        profile = s.profile_data((REPO / s.PROFILE).read_bytes())
        self.assertEqual(set(profile["executables"]), s.NAMES)
        self.assertEqual(sum(len(r["all_regular_files"]) for r in profile["components"].values()), 215)
        self.assertTrue(any(name.startswith("lib/rustlib/") for name in s.expected_install(profile)))

    def test_missing_component_or_tool_cannot_pass(self):
        for key, name in (("components", "rust-std"), ("executables", "clippy-driver")):
            profile = self.profile()
            del profile[key][name]
            with self.assertRaises(s.Refusal):
                s.profile_data(s.raw_json(profile))

    def test_profile_refuses_alias_wrong_host_and_unsafe_publisher(self):
        for change in (lambda d: d.update(host="other"),
                       lambda d: d["components"]["rustc"].update(all_aliases={"linked": "other"}),
                       lambda d: d["components"]["rustc"].update(url="https://example.invalid/tool.tar.xz")):
            profile = self.profile()
            change(profile)
            with self.assertRaises(s.Refusal):
                s.profile_data(s.raw_json(profile))

    def test_unknown_required_tool_member_cannot_match_two_missing_values(self):
        profile = self.profile()
        profile["executables"]["cargo"]["archive_member"] = "not-recorded"
        with self.assertRaises(s.Refusal):
            s.profile_data(s.raw_json(profile))

    def test_bad_publisher_is_refused_before_network(self):
        with patch.object(s, "build_opener", side_effect=AssertionError("network must not start")):
            with self.assertRaises(s.Refusal):
                s.download("https://example.invalid/tool", Path("unused"), "0" * 64, 1)

    def test_duplicate_profile_json_members_are_refused(self):
        with self.assertRaises(s.Refusal):
            s.profile_data(b'{"version":"one","version":"two"}')

    def test_nonfinite_profile_json_constants_are_refused(self):
        for value in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(s.Refusal):
                    s.parse(b'{"ignored":' + value + b'}')

    def test_version_prefix_has_a_word_boundary(self):
        pin = {"version_prefix": "clippy 0.1.96"}
        self.assertTrue(s.version_matches("clippy 0.1.96 (source)\n", pin))
        self.assertFalse(s.version_matches("clippy 0.1.960 (source)\n", pin))
        with self.assertRaises(s.Refusal):
            s.version_matches("clippy\x00 0.1.96", pin)

    def test_bootstrap_uses_actual_source_instead_of_matching_stale_pyc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / s.SUPPORT
            path.parent.mkdir(parents=True)
            raw = (REPO / s.SUPPORT).read_bytes()
            path.write_bytes(b"raise RuntimeError('stale PYC executed')\n".ljust(len(raw), b" "))
            os.utime(path, (1700000000, 1700000000))
            py_compile.compile(str(path), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            path.write_bytes(raw)
            os.utime(path, (1700000000, 1700000000))
            with patch.dict(sys.modules):
                sys.modules.pop("_som_rust_product_runtime_support", None)
                module = s.bootstrap(path)
                self.assertTrue(hasattr(module, "Refusal"))
                self.assertIsNone(module.__cached__)

    def test_dirty_or_linked_bootstrap_is_refused_before_compile(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / s.SUPPORT
            path.parent.mkdir(parents=True)
            path.write_bytes(b"untrusted bytes")
            with patch("builtins.compile", side_effect=AssertionError("dirty source reached compile")):
                with self.assertRaises(RuntimeError):
                    s.bootstrap(path)
            source = path.with_name("real.py")
            source.write_bytes((REPO / s.SUPPORT).read_bytes())
            path.unlink()
            path.symlink_to(source)
            with self.assertRaises(RuntimeError):
                s.bootstrap(path)

    def test_unadmitted_bootstrap_namespace_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "namespace"):
            s.bootstrap(REPO / s.SUPPORT)


class OwnedCommandTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        out = Path(temporary.name).resolve()
        report = {"commands": []}
        return s.Runner(out, report), report

    def test_every_success_waits_then_checks_owned_process_group(self):
        runner, report = self.fixture()
        events = []
        class FakeProcess:
            pid = 987654321
            def wait(self, timeout):
                events.append("wait")
            def poll(self):
                return 0
        def popen(argv, **kwargs):
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            kwargs["stdout"].write(b"synthetic output\n")
            return FakeProcess()
        def stop(process):
            self.assertEqual(events, ["wait"])
            events.append("cleanup")
            return {"group_gone": True, "signals": []}
        with patch.object(s.subprocess, "Popen", side_effect=popen), patch.object(s.q, "stop_owned", side_effect=stop):
            self.assertEqual(runner.run(["synthetic"], "synthetic"), "synthetic output\n")
        self.assertEqual(events, ["wait", "cleanup"])
        self.assertTrue(report["commands"][0]["wait_complete"])
        self.assertIsNone(s.q.LIVE)

    def test_missing_group_cleanup_cannot_pass(self):
        runner, report = self.fixture()
        class FakeProcess:
            pid = 987654321
            def wait(self, timeout):
                return 0
            def poll(self):
                return 0
        with patch.object(s.subprocess, "Popen", return_value=FakeProcess()), patch.object(s.q, "stop_owned", return_value={"group_gone": False}):
            with self.assertRaises(s.Refusal):
                runner.run(["synthetic"], "unknown-cleanup")
        self.assertFalse(report["commands"][0]["cleanup"]["group_gone"])

    def test_source_failure_keeps_false_flags_and_never_starts_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            out = root / "som-rust-product-tools-qualification"
            argv = [str(subject_path), "--repo", str(REPO), "--source-revision", "1" * 40, "--out", str(out)]
            with patch.dict(os.environ, {"RUNNER_TEMP": str(root)}), patch.object(sys, "argv", argv), \
                    patch.object(s.q, "host"), patch.object(s, "source_bindings", side_effect=s.Refusal("source mismatch")), \
                    patch.object(s, "download", side_effect=AssertionError("download before source admission")):
                self.assertEqual(s.main(), 1)
            receipt = json.loads((out / "receipt.json").read_bytes())
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(receipt["product_acceptance_passed"])
            self.assertFalse(receipt["product_runtime_ready"])
            self.assertFalse((out / "github.env").exists())


class GitSourceBindingTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name).resolve()
        blobs = {s.SCRIPT: subject_path.read_bytes(), s.SUPPORT: (REPO / s.SUPPORT).read_bytes(),
                 s.PROFILE: (REPO / s.PROFILE).read_bytes()}
        for name, body in blobs.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        class FakeGit:
            head = "1" * 40
            def run_bytes(self, argv, label, limit):
                argv = list(map(str, argv))
                if argv[-2:] == ["rev-parse", "HEAD^{commit}"]:
                    return (self.head + "\n").encode()
                if argv[-2:] == ["rev-parse", "--show-toplevel"]:
                    return os.fsencode(repo) + b"\n"
                if "ls-tree" in argv:
                    name = argv[-1]
                    body = blobs[name]
                    oid = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
                    return ("100644 blob " + oid + "\t" + name).encode() + b"\0"
                if "cat-file" in argv:
                    oid = argv[-1]
                    rows = [body for body in blobs.values()
                            if hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest() == oid]
                    if len(rows) != 1:
                        raise AssertionError("unrecognized synthetic Git blob")
                    return str(len(rows[0])).encode() + b"\n" if argv[-2] == "-s" else rows[0]
                raise AssertionError("unexpected synthetic Git metadata command")
        return repo, blobs, FakeGit()

    def call(self, repo, runner):
        with patch.object(s, "__file__", str(repo / s.SCRIPT)), patch.object(s.q, "regular", return_value="0" * 64):
            return s.source_bindings(runner, repo, "1" * 40)

    def test_three_regular_working_sources_must_match_exact_git_blobs(self):
        repo, blobs, runner = self.fixture()
        actual, records = self.call(repo, runner)
        self.assertEqual(actual, blobs)
        self.assertEqual(set(records), {s.SCRIPT, s.SUPPORT, s.PROFILE})
        self.assertEqual(records[s.SUPPORT]["sha256"], s.SUPPORT_SHA)

    def test_wrong_declared_head_is_refused(self):
        repo, _, runner = self.fixture()
        runner.head = "2" * 40
        with self.assertRaises(s.Refusal):
            self.call(repo, runner)

    def test_dirty_or_linked_profile_is_refused(self):
        repo, _, runner = self.fixture()
        path = repo / s.PROFILE
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaises(s.Refusal):
            self.call(repo, runner)
        other = repo / "same-profile.json"
        other.write_bytes(original)
        path.unlink()
        path.symlink_to(other)
        with self.assertRaises(s.Refusal):
            self.call(repo, runner)

    def test_unreviewed_git_support_cannot_supply_code(self):
        repo, blobs, runner = self.fixture()
        blobs[s.SUPPORT] = b"different Git-bound support"
        (repo / s.SUPPORT).write_bytes(blobs[s.SUPPORT])
        with self.assertRaises(s.Refusal):
            self.call(repo, runner)

    def test_incomplete_receipt_cannot_enter_runtime_inventory_check(self):
        with patch.object(s, "verify_install_tree", side_effect=AssertionError("incomplete receipt reached runtime")):
            with self.assertRaises(s.Refusal):
                s.verify_record(Path("unused"), {}, "1" * 40,
                                {"version": "som.rust-product-tool-qualification.v1", "status": "incomplete"})


if __name__ == "__main__":
    unittest.main()
