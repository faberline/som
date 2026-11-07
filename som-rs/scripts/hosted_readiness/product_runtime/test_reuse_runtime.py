"""Synthetic runtime reuse tests. No real network or native process runs."""
import copy
import hashlib
import io
import json
import os
import py_compile
import struct
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import patch

SUBJECT = Path(__file__).with_name("reuse_runtime.py")
r = ModuleType("reuse_runtime")
r.__file__, r.__cached__ = str(SUBJECT), None
sys.modules[r.__name__] = r
exec(compile(SUBJECT.read_bytes(), str(SUBJECT), "exec", dont_inherit=True), r.__dict__)


def stale_cache(path, source, stale):
    """Create a valid timestamp cache whose body disagrees with the source bytes."""
    if len(stale) > len(source):
        raise AssertionError("fixture cache body must fit the source size")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(stale.ljust(len(source), b" "))
    os.utime(path, (1700000000, 1700000000))
    cache = Path(py_compile.compile(str(path), doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
    path.write_bytes(source)
    os.utime(path, (1700000000, 1700000000))
    if cache.read_bytes()[8:16] != struct.pack("<II", 1700000000, len(source)):
        raise AssertionError("fixture is not a valid matching timestamp/size cache")
    return cache


def raw(value):
    return (json.dumps(value, indent=2) + "\n").encode()


class RuntimeReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.transport, self.out = self.root / "transport", self.root / "installed"
        self.transport.mkdir()
        self.out.mkdir()
        self.prefix, self.revision = "/qualified-fixture/som-product-runtime-qualification", "a" * 40
        python, uv = "install/python/bin/python3.12", "install/uv/bin/uv"
        self.bodies = {python: b"\x7fELF fixture python", uv: b"\x7fELF fixture uv",
                       "install/python/lib/data.txt": b"qualified data"}
        self.inventory = {name: {"kind": "regular_file", "sha256": r.digest(body),
                                "executable_bit": "/bin/" in name} for name, body in self.bodies.items()}
        self.alias = "install/python/bin/python"
        self.inventory[self.alias] = {"kind": "alias_not_admitted", "target": "python3.12", "sha256": r.digest(b"python3.12"),
                                      "qualification_probe_executed": False, "runtime_admitted": False}
        pins = {"version": "som.native-product-runtime-source-pins.v1", "archives": []}
        self.receipt = {"version": "som.product-runtime-qualification.v1",
                        "status": "qualification_commands_completed_awaiting_independent_review",
                        "source_revision": self.revision, "product_runtime_ready": False, "services": "not_started",
                        "trusted_sources": {}, "commands": [], "admitted_executables": {}, "python_modules": '{"fixture":true}\n'}
        self.files = dict(self.bodies)
        self.files[self.alias] = self.bodies[python]
        for name, version in r.q.EXPECTED.items():
            archive_root = name + "-fixture"
            kind = "source" if name in {"python", "postgresql", "redis"} else "licenses" if name == "uv-licenses" else "binary"
            licenses = {} if name == "uv" else {archive_root + "/LICENSE": r.digest(b"fixture license")}
            item = {"name": name, "version": version, "kind": kind, "root": archive_root,
                    "url": "https://www.python.org/fixture", "sha256": r.digest(name.encode()), "archive_bytes": 1, "licenses": licenses}
            if kind == "binary":
                executable = "mongod" if name == "mongodb" else "uv"
                member = archive_root + ("/bin/" if name == "mongodb" else "/") + executable
                item["executables"] = {member: {"path": "bin/" + executable, "sha256": r.digest(b"fixture binary")}}
            pins["archives"].append(item)
            self.receipt["trusted_sources"][name] = {"sha256": item["sha256"], "bytes": 1, "admission": {"licenses": licenses}}
            for member, digest in licenses.items():
                self.files["licenses/" + name + "/" + member] = b"fixture license"
        for index, relative in enumerate((python, uv)):
            stdout = ("Python fixture\n" if relative == python else "uv fixture\n")
            log = f"logs/{index:03d}-version"
            command = {"argv": [self.prefix + "/" + relative, "--version"], "exit_code": 0,
                       "stdout": self.prefix + "/" + log + ".stdout", "stderr": self.prefix + "/" + log + ".stderr",
                       "stdout_sha256": r.digest(stdout.encode()), "stderr_sha256": r.digest(b"")}
            self.receipt["commands"].append(command)
            self.files[log + ".stdout"], self.files[log + ".stderr"] = stdout.encode(), b""
            self.receipt["admitted_executables"][relative] = {"path": self.prefix + "/" + relative,
                "sha256": self.inventory[relative]["sha256"], "status": "qualification_commands_passed_not_independently_accepted",
                "admitted_for": "qualification_commands_only", "version_exit_code": 0, "version_stdout": stdout, "version_command_index": index}
        self.pins_raw = raw(pins)
        self.receipt["manifest_sha256"] = r.digest(self.pins_raw)
        self.files.update({"receipt.json": raw(self.receipt), "installed-files.json": raw(self.inventory),
                           "source-pins.json": self.pins_raw, "build-intermediates.json": b"{}\n"})
        self.qualification = {"version": "som.product-runtime-artifact-qualification.v1", "repository": "faberline/som",
            "run_id": 42, "artifact_id": 99, "artifact_name": "product-runtime-first-build-qualification",
            "artifact_sha256": "b" * 64, "source_revision": self.revision, "install_prefix": self.prefix,
            "qualified_for": "later_GitHub_tests_only", "product_runtime_ready": False, "product_acceptance_passed": False,
            "transport_files": {name: r.digest(self.files[name]) for name in r.META_FILES},
            "counts": {"regular_installed_files": 3, "aliases": 1, "admitted_executables": 2, "commands": 2, "license_files": 5},
            "aliases": {self.alias: "python3.12"}, "admitted_paths": [python, uv]}
        self.audit = {"version": "som.product-runtime-first-build-saved-byte-audit.v1", "qualification_evidence_verified": True, "product_runtime_ready": False, "product_acceptance_passed": False,
            "tested_sha": self.revision, "artifact_id": 99, "artifact_github_digest": "b" * 64,
            "run_url": "https://github.com/faberline/som/actions/runs/42",
            "receipt_sha256": self.qualification["transport_files"]["receipt.json"],
            "installed_inventory_sha256": self.qualification["transport_files"]["installed-files.json"],
            "regular_installed_files": 3, "transport_dereferenced_aliases": 1, "admitted_elf_version_observations": 2,
            "commands_zero_exit_and_log_hashes_verified": 2, "license_files": 5, "source_archive_records": 6}
        self.audit_raw = raw(self.audit)
        self.qualification["audit"] = {"path": r.AUDIT, "sha256": r.digest(self.audit_raw)}
        self.pins = pins
        for name, body in self.files.items():
            r.q.write_new(self.transport / name, body)

    def install(self):
        files = r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)
        r.install_files(files, self.inventory, self.pins, self.qualification, self.out)
        return files


    def test_bootstrap_ignores_valid_matching_timestamp_cache(self):
        support = self.root / "bootstrap" / r.SUPPORT
        source = Path(r.q.__file__).read_bytes()
        cache = stale_cache(support, source, b"raise AssertionError('unchecked cached support executed')\n")
        with patch.dict(sys.modules):
            sys.modules.pop(r.SUPPORT_MODULE, None)
            module = r.bootstrap_support(support)
        self.assertEqual(module.__file__, str(support))
        self.assertIsNone(module.__cached__)
        self.assertEqual(module.EXPECTED, r.q.EXPECTED)
        self.assertTrue(cache.is_file())

    def test_bootstrap_rejects_dirty_support_before_compile(self):
        support = self.root / "bootstrap" / r.SUPPORT
        support.parent.mkdir(parents=True)
        support.write_bytes(b"raise AssertionError('dirty support executed')\n")
        with patch("builtins.compile", side_effect=AssertionError("dirty support reached compile")):
            with self.assertRaisesRegex(RuntimeError, "support SHA"):
                r.bootstrap_support(support)

    def test_bootstrap_rejects_linked_or_copied_support_paths(self):
        support = self.root / "bootstrap" / r.SUPPORT
        support.parent.mkdir(parents=True)
        target = self.root / "original-support.py"
        target.write_bytes(Path(r.q.__file__).read_bytes())
        support.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "linked"):
            r.bootstrap_support(support)
        support.unlink()
        os.link(target, support)
        with self.assertRaisesRegex(RuntimeError, "linked"):
            r.bootstrap_support(support)
        with self.assertRaisesRegex(RuntimeError, "fixed bootstrap"):
            r.bootstrap_support(target)

    def test_bootstrap_refuses_preloaded_unbound_module(self):
        support = self.root / "bootstrap" / r.SUPPORT
        support.parent.mkdir(parents=True)
        support.write_bytes(Path(r.q.__file__).read_bytes())
        with patch.dict(sys.modules, {r.SUPPORT_MODULE: ModuleType(r.SUPPORT_MODULE)}):
            with self.assertRaisesRegex(RuntimeError, "already loaded"):
                r.bootstrap_support(support)

    def test_qualification_uses_exact_audit_and_source_pin_bytes(self):
        value = r.qualification_data(raw(self.qualification), self.audit_raw, self.pins_raw)
        self.assertEqual(value["artifact_id"], 99)
        for audit_raw, pins_raw in ((self.audit_raw + b" ", self.pins_raw), (self.audit_raw, self.pins_raw + b" ")):
            with self.assertRaises(r.Refusal):
                r.qualification_data(raw(self.qualification), audit_raw, pins_raw)

    def test_audit_cannot_grant_full_product_readiness(self):
        changed = copy.deepcopy(self.qualification)
        changed["product_runtime_ready"] = True
        with self.assertRaisesRegex(r.Refusal, "scope"):
            r.qualification_data(raw(changed), self.audit_raw, self.pins_raw)

    def test_inventory_rejects_unknown_modes_and_admitted_aliases(self):
        changed = copy.deepcopy(self.inventory)
        changed["install/python/bin/python3.12"]["executable_bit"] = "yes"
        with self.assertRaisesRegex(r.Refusal, "mode"):
            r.validate_inventory(changed, self.qualification)
        changed = copy.deepcopy(self.qualification)
        changed["admitted_paths"] = [self.alias, "install/uv/bin/uv"]
        with self.assertRaisesRegex(r.Refusal, "admitted executable"):
            r.validate_inventory(self.inventory, changed)

    def test_unknown_or_escaping_alias_is_refused(self):
        for target in ("../../outside", "unknown"):
            changed = copy.deepcopy(self.inventory)
            changed[self.alias]["target"] = target
            changed[self.alias]["sha256"] = r.digest(target.encode())
            with self.assertRaisesRegex(r.Refusal, "alias"):
                r.validate_inventory(changed, self.qualification)

    def test_transport_extra_file_or_empty_directory_is_refused(self):
        extra = self.transport / "job.log"
        extra.write_bytes(b"not in the artifact contract")
        with self.assertRaisesRegex(r.Refusal, "extra"):
            r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)
        extra.unlink()
        (self.transport / "unexpected-empty").mkdir()
        with self.assertRaisesRegex(r.Refusal, "extra"):
            r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)

    def test_tampered_regular_and_dereferenced_alias_bytes_are_refused(self):
        for name in ("install/uv/bin/uv", self.alias):
            file = self.transport / name
            original = file.read_bytes()
            file.write_bytes(original + b"changed")
            with self.assertRaisesRegex(r.Refusal, "hash"):
                r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)
            file.write_bytes(original)

    def test_transport_symlinks_and_hardlinks_are_refused(self):
        file = self.transport / "install/uv/bin/uv"
        file.unlink()
        file.symlink_to(self.transport / "install/python/bin/python3.12")
        with self.assertRaisesRegex(r.Refusal, "symlink"):
            r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)
        file.unlink()
        os.link(self.transport / "install/python/bin/python3.12", file)
        with self.assertRaisesRegex(r.Refusal, "linked"):
            r.verify_transport(self.transport, self.inventory, self.receipt, self.pins, self.qualification)

    def test_only_recorded_links_and_modes_are_restored(self):
        self.install()
        result = r.verify_installed_tree(self.out, self.inventory, self.qualification)
        self.assertEqual(result["regular_files"], 3)
        self.assertEqual(result["aliases"], 1)
        self.assertTrue((self.out / self.alias).is_symlink())
        self.assertEqual(os.readlink(self.out / self.alias), "python3.12")
        self.assertEqual(stat.S_IMODE((self.out / "install/uv/bin/uv").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.out / "install/python/lib/data.txt").stat().st_mode), 0o644)
        r.verify_evidence(self.out, r.transport_expectations(self.receipt, self.inventory, self.pins, self.qualification))

    def test_changed_installed_mode_hash_or_link_is_refused(self):
        self.install()
        file = self.out / "install/uv/bin/uv"
        file.chmod(0o700)
        with self.assertRaisesRegex(r.Refusal, "mode"):
            r.verify_installed_tree(self.out, self.inventory, self.qualification)
        file.chmod(0o755)
        file.write_bytes(b"changed")
        with self.assertRaisesRegex(r.Refusal, "hash"):
            r.verify_installed_tree(self.out, self.inventory, self.qualification)

    def test_source_command_failure_and_wrong_version_log_are_refused(self):
        receipt = copy.deepcopy(self.receipt)
        receipt["commands"][0]["exit_code"] = 1
        with self.assertRaisesRegex(r.Refusal, "failed"):
            r.transport_expectations(receipt, self.inventory, self.pins, self.qualification)
        receipt = copy.deepcopy(self.receipt)
        receipt["admitted_executables"]["install/python/bin/python3.12"]["version_stdout"] = "forged"
        with self.assertRaisesRegex(r.Refusal, "version log"):
            r.transport_expectations(receipt, self.inventory, self.pins, self.qualification)

    def test_mocked_origin_requires_exact_pinned_run_digest_and_source(self):
        metadata = {"id": 99, "name": self.qualification["artifact_name"], "expired": False, "digest": "sha256:" + "b" * 64,
                    "workflow_run": {"id": 42, "head_sha": self.revision},
                    "url": "https://api.github.com/repos/faberline/som/actions/artifacts/99"}
        for field in ("digest", "run"):
            changed = copy.deepcopy(metadata)
            if field == "digest":
                changed["digest"] = "sha256:" + "c" * 64
            else:
                changed["workflow_run"]["id"] = 43
            with patch.object(r, "build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(raw(changed))
                with self.assertRaisesRegex(r.Refusal, "metadata differs"):
                    r.verify_origin(self.qualification, self.out)
        with patch.object(r, "build_opener") as opener, patch.dict(os.environ, {"SOM_RUNTIME_ACTIONS_TOKEN": "synthetic-not-a-secret"}):
            opener.return_value.open.return_value = io.BytesIO(raw(metadata))
            result = r.verify_origin(self.qualification, self.out)
        self.assertEqual(result["artifact_id"], 99)
        self.assertNotIn("synthetic-not-a-secret", json.dumps(result))

    def test_mocked_native_checks_use_only_admitted_paths_and_explicit_python_B(self):
        self.install()
        original = self.receipt
        class Runner:
            def __init__(self, out):
                self.out = out
                self.report = {"commands": [], "admitted_executables": {}}
                self.argv = []
            def run(self, argv, label):
                self.argv.append([str(value) for value in argv])
                self.report["commands"].append({"exit_code": 0, "stderr_sha256": r.digest(b"")})
                if label == "recheck-python-core-modules":
                    return original["python_modules"]
                relative = Path(argv[0]).relative_to(self.out).as_posix()
                return original["admitted_executables"][relative]["version_stdout"]
        runner = Runner(self.out)
        r.recheck_tools(runner, self.receipt, self.qualification)
        self.assertEqual(len(runner.report["admitted_executables"]), 2)
        self.assertEqual(runner.argv[-1][1:4], ["-I", "-B", "-c"])
        self.assertTrue(all(Path(argv[0]).relative_to(self.out).as_posix() != self.alias for argv in runner.argv))
        self.assertEqual(r.verify_installed_tree(self.out, self.inventory, self.qualification)["aliases"], 1)

    def test_bad_qualified_hash_never_reaches_a_native_command(self):
        self.install()
        (self.out / "install/python/bin/python3.12").write_bytes(b"\x7fELF changed")
        class Runner:
            def run(self, *args, **kwargs):
                raise AssertionError("must not run a native tool")
        runner = Runner()
        runner.out, runner.report = self.out, {"commands": [], "admitted_executables": {}}
        with self.assertRaisesRegex(r.Refusal, "ELF hash"):
            r.recheck_tools(runner, self.receipt, self.qualification)

    def test_copy_rehashes_transport_bytes_and_preserves_original_evidence(self):
        source = self.transport / "source-pins.json"
        source.write_bytes(b"changed after verification")
        destination = self.out / "copied"
        with self.assertRaisesRegex(r.Refusal, "changed during copy"):
            r.copy_checked(source, destination, self.qualification["transport_files"]["source-pins.json"])

    def test_fixed_git_sources_reject_dirty_qualification(self):
        repo = self.root / "repo"
        files = {name: ("fixture " + name).encode() for name in (r.SCRIPT, r.SUPPORT, r.QUALIFICATION, r.AUDIT, r.PINS)}
        for name, body in files.items():
            r.q.write_new(repo / name, body)
        class Git:
            report = {}
            def run_bytes(self, argv, label, limit):
                args = [str(value) for value in argv]
                if "--no-replace-objects" not in args:
                    raise AssertionError("replacement object authority allowed")
                operation = args[5]
                if operation == "rev-parse":
                    return (str(repo).encode() if args[-1] == "--show-toplevel" else b"a" * 40) + b"\n"
                if operation == "ls-tree":
                    name = args[-1]
                    body = files[name]
                    oid = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
                    return b"100644 blob " + oid.encode() + b"\t" + name.encode() + b"\0"
                body = next(value for value in files.values() if hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest() == args[-1])
                return str(len(body)).encode() + b"\n" if args[-2] == "-s" else body
        with patch.object(r, "__file__", str(repo / r.SCRIPT)), patch.object(r.q, "__file__", str(repo / r.SUPPORT)):
            self.assertEqual(r.bind_sources(Git(), repo, "a" * 40), files)
            (repo / r.QUALIFICATION).write_bytes(files[r.QUALIFICATION] + b" dirty")
            with self.assertRaisesRegex(r.Refusal, "working source"):
                r.bind_sources(Git(), repo, "a" * 40)

    def test_copied_installer_path_is_not_source_authority(self):
        class NeverGit:
            def run_bytes(self, *args):
                raise AssertionError("must reject path before Git")
        with patch.object(r, "__file__", str(self.root / "copied.py")):
            with self.assertRaisesRegex(r.Refusal, "fixed installer"):
                r.bind_sources(NeverGit(), self.root, "a" * 40)

    def test_mocked_wrong_version_is_refused(self):
        self.install()
        class Runner:
            def run(self, argv, label):
                self.report["commands"].append({"exit_code": 0, "stderr_sha256": r.digest(b"")})
                return "wrong native version\n"
        runner = Runner()
        runner.out, runner.report = self.out, {"commands": [], "admitted_executables": {}}
        with self.assertRaisesRegex(r.Refusal, "version or bytes"):
            r.recheck_tools(runner, self.receipt, self.qualification)

    def test_json_duplicate_keys_are_refused(self):
        with self.assertRaises(r.Refusal):
            r.parse(b'{"path":1,"path":2}')


if __name__ == "__main__":
    unittest.main()
