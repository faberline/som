"""Synthetic wheel producer tests. Execute only on GitHub runners."""
import base64
import ast
import copy
import csv
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import signal
import marshal
import struct
import stat
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")

PRODUCER = Path(__file__).with_name("produce_wheels.py")
p = ModuleType("produce_wheels")
p.__file__, p.__cached__, p.__package__ = str(PRODUCER), None, ""
sys.modules[p.__name__] = p
exec(compile(PRODUCER.read_bytes(), str(PRODUCER), "exec", dont_inherit=True), p.__dict__)

PROJECT = '''[project]
name = "real_synthetic_product"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["requests==2.32.5"]
[dependency-groups]
dev = []
[tool.uv]
package = false
'''
META = b"Metadata-Version: 2.3\nName: requests\nVersion: 2.32.5\nLicense-Expression: Apache-2.0\nRequires-Dist: other>=1; extra == 'optional'\n\nSynthetic metadata.\n"
WHEEL_NAME = "requests-2.32.5-py3-none-any.whl"
WHEEL_URL = "https://files.pythonhosted.org/packages/synthetic/" + WHEEL_NAME
NATIVE = {"python_sha256": "a" * 64, "uv_sha256": "b" * 64, "inventory_sha256": "c" * 64}


def wheel_blob(changes=None, extras=None, omit_record=()):
    files = {"requests/__init__.py": b"# Never imported.\n", "requests-2.32.5.dist-info/METADATA": META,
             "requests-2.32.5.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
             "requests-2.32.5.dist-info/licenses/LICENSE": b"Synthetic public license notice.\n"}
    files.update(changes or {})
    output = io.StringIO(newline="")
    record = csv.writer(output, lineterminator="\n")
    for name, raw in sorted(files.items()):
        if name in omit_record:
            continue
        digest = base64.urlsafe_b64encode(bytes.fromhex(p.sha(raw))).rstrip(b"=").decode()
        record.writerow([name, "sha256=" + digest, len(raw)])
    record.writerow(["requests-2.32.5.dist-info/RECORD", "", ""])
    files["requests-2.32.5.dist-info/RECORD"] = output.getvalue().encode()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
        for info, body in extras or []:
            archive.writestr(info, body)
    return raw.getvalue()


class FakeRunner:
    """Record commands and create data fixtures. Never spawn any child."""
    def __init__(self, out, blob, fail_sync=False):
        self.out, self.blob, self.fail_sync, self.calls = out, blob, fail_sync, []
        self.tools = {"python": out / "qualified-python", "uv": out / "qualified-uv"}
        self.installer_assets = p.installer_assets(Path(__file__).with_name("uv099-installer-authority.json").read_bytes())
        self.closure = None
        self.inject_extra = None
        self.report = {"commands": []}

    def run(self, args, cwd, cache, label, offline=False, venv=None):
        args = [str(value) for value in args]
        self.calls.append({"argv": args, "cwd": cwd, "cache": cache, "label": label, "offline": offline, "venv": venv})
        self.report["commands"].append({"argv": args, "cwd": str(cwd), "cache": str(cache), "label": label, "offline": offline, "exit_code": 0})
        pin = "requests==2.32.5 --hash=sha256:" + p.sha(self.blob) + "\n"
        if args[1] == "lock":
            text = 'version = 1\nrequires-python = ">=3.12, <3.13"\n[[package]]\nname = "real-synthetic-product"\nversion = "0.1.0"\nsource = {virtual = "."}\n'
            text += '[[package]]\nname = "requests"\nversion = "2.32.5"\nsource = {registry = "https://pypi.org/simple"}\n'
            text += 'wheels = [{url = "' + WHEEL_URL + '", hash = "sha256:' + p.sha(self.blob) + '", size = ' + str(len(self.blob)) + '}]\n'
            (cwd / "uv.lock").write_text(text)
        elif args[1] == "export" or args[1:3] == ["pip", "compile"]:
            destination = Path(args[args.index("--output-file") + 1])
            destination.write_text(pin)
        elif args[1] == "sync":
            venv.mkdir()
            name = p.tomllib.loads((cwd / "pyproject.toml").read_text())["project"]["name"]
            expected, contents, _, directories, _ = p.installed_scope(venv, self.closure, self.installer_assets, self.tools["python"], name)
            for directory in sorted(directories, key=lambda item: len(Path(item).parts)):
                (venv / directory).mkdir(parents=True, exist_ok=True)
            for name, value in expected.items():
                target = venv / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if value["type"] == "file":
                    target.write_bytes(contents[name])
                else:
                    target.symlink_to(value["target"])
            if self.inject_extra is not None:
                target = venv / self.inject_extra
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"import unproven_site_hook\n")
            if self.fail_sync:
                raise p.Refusal("synthetic offline sync failure")
        elif args[0] == str(self.tools["python"]) and args[1:4] == ["-I", "-S", "-B"]:
            return json.dumps({"python": "3.12.11", "site": args[-1], "site_disabled": True, "site_in_sys_path": False,
                               "executable": str(self.tools["python"]),
                               "packages": [{"name": "requests", "version": "2.32.5", "metadata_sha256": p.sha(META)}]}).encode()
        return b""


class WheelProducerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.blob = wheel_blob()
        self.assets = p.installer_assets(Path(__file__).with_name("uv099-installer-authority.json").read_bytes())
        for name in p.MODULES:
            self.addCleanup(sys.modules.pop, name, None)

    def row(self):
        return {"id": "product-0000", "pyproject_bytes": PROJECT, "requirements_raw": b"requests==2.32.5\n",
                "requirements": {"requires_python": ">=3.12,<3.13", "runtime": ["requests==2.32.5"], "development": [], "quality_checker": None}}

    def save_wheel(self, blob=None):
        path = self.root / WHEEL_NAME
        path.write_bytes(self.blob if blob is None else blob)
        notices = self.root / "notices"
        notices.mkdir()
        return path, notices

    def test_exact_dependency_bytes_and_native_identity_define_deduplication(self):
        first = p.dependency_key(b"requests==2.32.5\n", NATIVE)
        self.assertEqual(first, p.dependency_key(b"requests==2.32.5\n", NATIVE))
        self.assertNotEqual(first, p.dependency_key(b"requests==2.34.2\n", NATIVE))
        self.assertNotEqual(first, p.dependency_key(b"requests[socks]==2.32.5\n", NATIVE))
        changed = {**NATIVE, "python_sha256": "d" * 64}
        self.assertNotEqual(first, p.dependency_key(b"requests==2.32.5\n", changed))

    def test_hashed_target_lock_keeps_every_transitive_package(self):
        raw = b"requests==2.32.5 \\\n --hash=sha256:" + b"a" * 64 + b"\nother==1.0 --hash=sha256:" + b"b" * 64 + b"\n"
        result = p.locked_requirements(raw)
        self.assertEqual(set(result), {"requests", "other"})
        self.assertEqual(result["other"]["hashes"], ["b" * 64])

    def test_unhashed_broad_marker_url_or_override_lock_refused(self):
        for raw in (b"requests==2.32.5\n", b"requests>=2 --hash=sha256:" + b"a" * 64,
                    b"requests==2.32.5; sys_platform=='linux' --hash=sha256:" + b"a" * 64,
                    b"requests @ https://example.invalid/a.whl --hash=sha256:" + b"a" * 64,
                    b"--override bad.txt\n", b"requests==2.32.5 --hash=md5:abc\n"):
            with self.subTest(raw=raw), self.assertRaises(p.Refusal):
                p.locked_requirements(raw)

    def test_duplicate_and_forbidden_locked_packages_refused(self):
        line = b"requests==2.32.5 --hash=sha256:" + b"a" * 64 + b"\n"
        with self.assertRaises(p.Refusal):
            p.locked_requirements(line + line)
        with self.assertRaises(p.Refusal):
            p.locked_requirements(b"mlx==1.0 --hash=sha256:" + b"a" * 64)

    def test_supported_cpython_abi3_and_pure_tags(self):
        for filename in ("pkg-1.0-cp312-cp312-manylinux_2_35_x86_64.whl", "pkg-1.0-cp39-abi3-manylinux2014_x86_64.whl",
                         "pkg-1.0-py2.py3-none-any.whl", "pkg-1.0-py3-none-manylinux_2_17_x86_64.whl"):
            self.assertIsNotNone(p.wheel_rank(filename))

    def test_wrong_python_abi_os_cpu_or_glibc_refused(self):
        for filename in ("pkg-1.0-cp313-cp313-manylinux_2_35_x86_64.whl", "pkg-1.0-cp311-cp311-manylinux2014_x86_64.whl",
                         "pkg-1.0-py3-none-win_amd64.whl", "pkg-1.0-py3-none-manylinux_2_36_x86_64.whl",
                         "pkg-1.0-py3-none-manylinux2014_aarch64.whl", "pkg-1.0-cp312-cp312t-linux_x86_64.whl"):
            self.assertIsNone(p.wheel_rank(filename))
        for filename in ("../pkg.whl", "pkg\\bad.whl", "pkg.whl", "pkg-1.0-py3-none-any.whl\n"):
            with self.assertRaises(p.Refusal):
                p.wheel_rank(filename)

    def publisher(self):
        return {"info": {"name": "requests", "version": "2.32.5"}, "urls": [
            {"packagetype": "bdist_wheel", "filename": WHEEL_NAME, "url": WHEEL_URL, "size": len(self.blob),
             "digests": {"sha256": p.sha(self.blob)}, "yanked": False}]}

    def test_publisher_selection_requires_both_lock_and_official_hash(self):
        pin = {"version": "2.32.5", "hashes": [p.sha(self.blob)]}
        self.assertEqual(p.selected_wheel(self.publisher(), "requests", pin)["filename"], WHEEL_NAME)
        with self.assertRaises(p.Refusal):
            p.selected_wheel(self.publisher(), "requests", {**pin, "hashes": ["f" * 64]})
        changed = self.publisher()
        changed["urls"][0]["yanked"] = True
        with self.assertRaises(p.Refusal):
            p.selected_wheel(changed, "requests", pin)

    def test_nonpublisher_credentials_ports_fragments_and_query_refused(self):
        for url in ("http://files.pythonhosted.org/a.whl", "https://evil.invalid/a.whl", "https://user:pass@files.pythonhosted.org/a.whl",
                    "https://files.pythonhosted.org:443/a.whl", "https://files.pythonhosted.org/a.whl#hash", "https://files.pythonhosted.org/a.whl?token=x"):
            with self.subTest(url=url), self.assertRaises(p.Refusal):
                p.checked_url(url)
        with self.assertRaises(p.Refusal):
            p.Redirect(False).redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid/a.whl")

    def test_wheel_record_all_members_and_notice_bytes_verified(self):
        path, notices = self.save_wheel()
        audit = p.audit_wheel(path, "requests", "2.32.5", notices)
        self.assertTrue(audit["record_verified"])
        self.assertEqual(audit["metadata_sha256"], p.sha(META))
        self.assertEqual(len(audit["members"]), 5)
        notice = audit["license_notices"][0]
        self.assertEqual((notices / notice["preserved"]).read_bytes(), b"Synthetic public license notice.\n")
        self.assertEqual(notice["sha256"], p.sha((notices / notice["preserved"]).read_bytes()))

    def test_missing_record_member_is_not_accepted_from_archive_hash_alone(self):
        path, notices = self.save_wheel(wheel_blob(omit_record=("requests/__init__.py",)))
        with self.assertRaises(p.Refusal):
            p.audit_wheel(path, "requests", "2.32.5", notices)

    def test_wrong_metadata_identity_and_wheel_tags_refused(self):
        blob = wheel_blob(changes={"requests-2.32.5.dist-info/METADATA": META.replace(b"Name: requests", b"Name: different")})
        path, notices = self.save_wheel(blob)
        with self.assertRaises(p.Refusal):
            p.audit_wheel(path, "requests", "2.32.5", notices)

    def test_traversal_link_and_duplicate_zip_member_refused(self):
        link = zipfile.ZipInfo("requests/link")
        link.create_system, link.external_attr = 3, (stat.S_IFLNK | 0o777) << 16
        fixtures = [[("../outside", b"escape")], [(link, b"outside")], [("requests/__init__.py", b"duplicate")]]
        for index, extras in enumerate(fixtures):
            path = self.root / f"requests-2.32.5-{index}-py3-none-any.whl"
            path.write_bytes(wheel_blob(extras=extras))
            notices = self.root / str(index)
            notices.mkdir()
            with self.assertRaises(p.Refusal):
                p.audit_wheel(path, "requests", "2.32.5", notices)

    def test_expansion_bound_checked_before_member_reads(self):
        path, notices = self.save_wheel()
        with patch.object(p, "MAX_EXPANDED", 1), self.assertRaises(p.Refusal):
            p.audit_wheel(path, "requests", "2.32.5", notices)

    def test_universal_lock_refuses_foreign_source_and_model_dependency(self):
        good = 'version = 1\nrequires-python = ">=3.12, <3.13"\n[[package]]\nname = "product"\nversion = "0.1.0"\nsource = {virtual = "."}\n'
        self.assertEqual(p.uv_lock_inventory(good.encode())["package_records"], 1)
        for addition in ('[[package]]\nname = "bad"\nsource = {registry = "https://evil.invalid"}\n',
                         '[[package]]\nname = "mlx"\nsource = {registry = "https://pypi.org/simple"}\n'):
            with self.assertRaises(p.Refusal):
                p.uv_lock_inventory((good + addition).encode())

    def fake_download(self, url, destination, limit, expected_sha=None, expected_size=None, metadata=False):
        raw = json.dumps(self.publisher()).encode() if metadata else self.blob
        destination.write_bytes(raw)
        self.assertLessEqual(len(raw), limit)
        if expected_sha is not None:
            self.assertEqual(p.sha(raw), expected_sha)
        if expected_size is not None:
            self.assertEqual(len(raw), expected_size)
        return {"url": url, "sha256": p.sha(raw), "size": len(raw)}

    def test_real_project_bytes_and_original_extras_input_drive_offline_closure(self):
        (self.root / "closures").mkdir()
        runner, record = FakeRunner(self.root, self.blob), {}
        with patch.object(p, "download", side_effect=self.fake_download):
            closure = p.resolve_set(runner, self.row(), "a" * 64, record)
        self.assertEqual((closure["root"] / "project/pyproject.toml").read_bytes(), PROJECT.encode())
        self.assertEqual(record["state"], "wheel-closure-qualified")
        self.assertTrue(record["offline_original_closure_verified"])
        last = runner.calls[-1]
        self.assertTrue(last["offline"])
        self.assertIn("--constraint", last["argv"])
        self.assertNotIn("--override", last["argv"])
        self.assertIn(str(closure["root"] / "requirements.in"), last["argv"])
        self.assertIn("--only-binary", last["argv"])

    def test_named_offline_environment_keeps_receipts_and_cleans_only_successful_venv(self):
        (self.root / "environments").mkdir()
        runner, record = FakeRunner(self.root, self.blob), {"id": "product-0000"}
        closure = self.closure_fixture(runner)
        p.provision_named(runner, self.row(), closure, self.root / "probe.py", record)
        self.assertEqual(record["state"], "offline-environment-qualified")
        self.assertFalse((self.root / "environments/product-0000/venv").exists())
        self.assertTrue((self.root / "environments/product-0000/installed-files.json").is_file())
        self.assertTrue((self.root / "environments/product-0000/uv.lock").is_file())
        self.assertEqual(record["product_tests"], "unrun")
        self.assertTrue(all(call["offline"] for call in runner.calls if call["label"].startswith("product-0000")))
        self.assertTrue(any("--frozen" in call["argv"] for call in runner.calls))

    def test_failed_environment_and_logs_are_preserved(self):
        (self.root / "environments").mkdir()
        runner, record = FakeRunner(self.root, self.blob, fail_sync=True), {"id": "product-0000"}
        closure = self.closure_fixture(runner)
        with self.assertRaises(p.Refusal):
            p.provision_named(runner, self.row(), closure, self.root / "probe.py", record)
        self.assertTrue((self.root / "environments/product-0000/venv").is_dir())
        self.assertEqual(json.loads((self.root / "environments/product-0000/receipt.json").read_bytes())["state"], "failed")

    def closure_fixture(self, runner):
        (self.root / "closures").mkdir()
        with patch.object(p, "download", side_effect=self.fake_download):
            closure = p.resolve_set(runner, self.row(), "a" * 64, {})
        runner.closure = closure
        return closure

    def test_changed_frozen_wheel_blocks_named_environment_before_process(self):
        (self.root / "environments").mkdir()
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        (closure["wheels"] / WHEEL_NAME).write_bytes(b"changed")
        count = len(runner.calls)
        with self.assertRaises(p.Refusal):
            p.provision_named(runner, self.row(), closure, self.root / "probe.py", {"id": "product-0000"})
        self.assertEqual(len(runner.calls), count)
        self.assertFalse((self.root / "environments/product-0000").exists())

    def test_installed_code_bytes_are_bound_even_when_metadata_versions_match(self):
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        venv = self.root / "venv"
        self.materialize(venv, closure, runner.tools["python"])
        directory = venv / "lib/python3.12/site-packages"
        inventory = p.installed_inventory(venv, self.root)
        self.assertEqual(p.verify_installed_members(venv, inventory, closure, self.assets, runner.tools["python"], "real_synthetic_product")["source_wheel_members"], 4)
        (directory / "requests/__init__.py").write_text("# changed code; metadata unchanged\n")
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def materialize(self, venv, closure, base, project_name="real_synthetic_product"):
        expected, contents, _, directories, _ = p.installed_scope(venv, closure, self.assets, base, project_name)
        venv.mkdir()
        for directory in sorted(directories, key=lambda item: len(Path(item).parts)):
            (venv / directory).mkdir(parents=True, exist_ok=True)
        for name, value in expected.items():
            target = venv / name
            if value["type"] == "file":
                target.write_bytes(contents[name])
            else:
                target.symlink_to(value["target"])
        return expected, contents

    def test_cleanup_refuses_failed_foreign_or_linked_environment(self):
        outside = self.root / "foreign/venv"
        outside.mkdir(parents=True)
        (outside / "keep").write_text("keep")
        for successful in (False, True):
            with self.assertRaises(p.Refusal):
                p.cleanup_successful_venv(outside, self.root, successful, "a" * 64, outside.parent / "qualification-before-cleanup.json")
        self.assertTrue((outside / "keep").is_file())

    def test_only_observer_gets_declared_observer_project(self):
        with self.assertRaises(p.Refusal):
            p.observer_project(self.row())
        row = {"id": "observer", "pyproject_bytes": None, "requirements": {"runtime": ["requests==2.34.2"]}}
        raw = p.observer_project(row)
        self.assertIn(b"requests==2.34.2", raw)
        self.assertNotIn(b"requests==2.32.5", raw)

    def runner_fixture(self):
        tools = {}
        native = {}
        for name in ("python", "uv"):
            path = self.root / name
            path.write_bytes(b"fake native bytes, never executed " + name.encode())
            tools[name], native[name + "_sha256"] = path, p.file_sha(path)
        for name in ("logs", "home", "tmp"):
            (self.root / name).mkdir()
        report = {"commands": []}
        return p.Runner(self.root, report, tools, native), report

    def test_runner_uses_clean_env_and_records_failure_log_hashes(self):
        runner, report = self.runner_fixture()
        child = SimpleNamespace(pid=999999, wait=lambda timeout: 1, poll=lambda: 1)
        with patch.object(p.subprocess, "Popen", return_value=child) as popen, patch.object(p, "group_exists", return_value=False), patch.dict(os.environ, {"SECRET_SHOULD_NOT_PASS": "private"}):
            with self.assertRaises(p.Refusal):
                runner.run([runner.tools["uv"], "--version"], self.root, self.root / "cache", "synthetic-failure")
        self.assertNotIn("SECRET_SHOULD_NOT_PASS", popen.call_args.kwargs["env"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(report["commands"][0]["exit_code"], 1)
        self.assertEqual(report["commands"][0]["stdout_sha256"], p.sha(b""))

    def test_timeout_stops_only_owned_group_and_keeps_logs(self):
        runner, report = self.runner_fixture()
        waits = iter((subprocess.TimeoutExpired("fake", 1), 0))
        def wait(timeout):
            value = next(waits)
            if isinstance(value, BaseException):
                raise value
            return value
        child = SimpleNamespace(pid=999998, wait=wait, poll=lambda: 0)
        with patch.object(p.subprocess, "Popen", return_value=child), patch.object(p, "group_exists", return_value=False), patch.object(p.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run([runner.tools["uv"], "--version"], self.root, self.root / "cache", "synthetic-timeout")
        kill.assert_called_once_with(999998, signal.SIGTERM)
        self.assertIn("stderr_sha256", report["commands"][0])

    def test_owned_descendants_are_stopped_even_after_parent_exit(self):
        runner, report = self.runner_fixture()
        child = SimpleNamespace(pid=999997, wait=lambda timeout: 0, poll=lambda: 0)
        with patch.object(p.subprocess, "Popen", return_value=child), patch.object(p, "group_exists", side_effect=[True, False]), patch.object(p.os, "killpg") as kill:
            runner.run([runner.tools["uv"], "--version"], self.root, self.root / "cache", "synthetic-descendant")
        kill.assert_called_once_with(999997, signal.SIGTERM)
        self.assertTrue(report["commands"][0]["owned_group_stopped"])

    def test_native_changed_before_child_blocks_process_creation(self):
        runner, _ = self.runner_fixture()
        runner.tools["uv"].write_text("changed")
        with patch.object(p.subprocess, "Popen") as popen, self.assertRaises(p.Refusal):
            runner.run([runner.tools["uv"], "--version"], self.root, self.root / "cache", "synthetic-changed")
        popen.assert_not_called()

    def test_output_overlap_and_symlink_refused_before_mutation(self):
        dirs = [self.root / name for name in ("repo", "inputs", "assembly", "runtime")]
        for path in dirs:
            path.mkdir()
        with self.assertRaises(p.Refusal):
            p.checked_paths(dirs[0], dirs[2] / "output", dirs[1], dirs[2], dirs[3], self.root)
        target = self.root / "link"
        target.symlink_to(dirs[1], target_is_directory=True)
        with self.assertRaises(p.Refusal):
            p.checked_paths(dirs[0], self.root / "new", target, dirs[2], dirs[3], self.root)
        self.assertFalse((self.root / "new").exists())

    def test_duplicate_json_and_nonfinite_numbers_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(p.Refusal):
                p.parse(raw)

    def native_fixture(self):
        root = self.root / "native"
        root.mkdir()
        paths = ["install/python/bin/python3.12", "install/uv/bin/uv"] + [f"install/postgresql/bin/tool{index}" for index in range(39)]
        inventory, admitted = {}, {}
        for name in paths:
            data = ("synthetic native bytes, never executed: " + name).encode()
            inventory[name] = {"kind": "regular_file", "sha256": p.sha(data), "executable_bit": True}
            admitted[name] = {"status": "hash_and_version_rechecked", "version_exit_code": 0,
                              "authority": "controller_audited_saved_artifact", "qualified_for": "later_GitHub_tests_only",
                              "sha256": p.sha(data), "version_stdout": "Python 3.12.11" if "python3.12" in name else "uv 0.9.9 (fixture)" if name.endswith("/uv") else "tool fixture"}
            if name in paths[:2]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o755)
        audit = b"synthetic controller byte audit"
        qualification = {"transport_files": {"installed-files.json": "c" * 64}, "admitted_paths": paths, "install_prefix": str(root)}
        policy = {"native_sha256": {"python": admitted[paths[0]]["sha256"], "uv": admitted[paths[1]]["sha256"]},
                  "native_authority": {"controller_byte_audit_sha256": p.sha(audit), "qualified_inventory_sha256": "c" * 64}}
        receipt = {"version": "som.product-runtime-reuse.v1", "status": "audited_runtime_reinstalled_and_rechecked_for_hosted_tests",
                   "tested_sha": "1" * 40, "qualified_for": "later_GitHub_tests_only", "installed_tree_verified_before": True,
                   "installed_tree_verified_after": True, "product_runtime_ready": False, "product_acceptance_passed": False,
                   "qualification": {"manifest_sha256": p.sha(b"{}"), "audit_sha256": p.sha(audit), "installed_inventory_sha256": "c" * 64},
                   "admitted_executables": admitted}
        (root / "receipt.json").write_text(json.dumps(receipt))
        class Source:
            pins = {p.QUALIFICATION: {"sha256": "d" * 64}}
            def read(self, name):
                return audit if name == p.BYTE_AUDIT else b"{}"
        calls = []
        def verify(*args):
            calls.append(args)
            return {"regular_files": 10102, "aliases": 11, "installed_inventory_sha256": "c" * 64}
        reuse = SimpleNamespace(qualification_data=lambda *args: qualification,
                                read_inventory=lambda *args: inventory, verify_installed_tree=verify)
        return root, Source(), policy, receipt, reuse, calls

    def test_whole_runtime_tree_and_all41_receipt_rows_required_before_child(self):
        root, sources, policy, receipt, reuse, calls = self.native_fixture()
        with patch.object(p, "bound_module", return_value=reuse), patch.object(p.subprocess, "Popen") as popen:
            _, _, _, tools, record = p.admitted_runtime(root, sources, policy, "1" * 40)
        self.assertEqual(set(tools), {"python", "uv"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(record["tree"]["regular_files"], 10102)
        popen.assert_not_called()
        receipt["admitted_executables"]["install/postgresql/bin/tool38"]["version_exit_code"] = 1
        (root / "receipt.json").write_text(json.dumps(receipt))
        with patch.object(p, "bound_module", return_value=reuse), self.assertRaises(p.Refusal):
            p.admitted_runtime(root, sources, policy, "1" * 40)

    def test_reuse_receipt_self_ready_or_wrong_revision_refused(self):
        root, sources, policy, receipt, reuse, _ = self.native_fixture()
        for key, value in (("product_runtime_ready", True), ("tested_sha", "2" * 40), ("installed_tree_verified_after", False)):
            changed = copy.deepcopy(receipt)
            changed[key] = value
            (root / "receipt.json").write_text(json.dumps(changed))
            with patch.object(p, "bound_module", return_value=reuse), self.subTest(key=key), self.assertRaises(p.Refusal):
                p.admitted_runtime(root, sources, policy, "1" * 40)

    def test_runtime_binary_change_cannot_be_hidden_by_reuse_flags(self):
        root, sources, policy, receipt, reuse, _ = self.native_fixture()
        (root / "install/python/bin/python3.12").write_bytes(b"changed")
        with patch.object(p, "bound_module", return_value=reuse), self.assertRaises(p.Refusal):
            p.admitted_runtime(root, sources, policy, "1" * 40)

    def test_source_regular_git_blob_and_working_bytes_must_match(self):
        committed = b"# Synthetic source. Never executed.\n"
        path = self.root / "source.py"
        path.write_bytes(committed)
        oid = hashlib.sha1(b"blob " + str(len(committed)).encode() + b"\0" + committed).hexdigest()
        source = object.__new__(p.Sources)
        source.root, source.revision, source.pins = self.root, "1" * 40, {}
        def fake_git(*args):
            if args[0] == "ls-tree":
                return b"100644 blob " + oid.encode() + b"\tsource.py\0"
            return str(len(committed)).encode() if args[1] == "-s" else committed
        source.git = fake_git
        self.assertEqual(source.read("source.py"), committed)
        path.write_bytes(b"# changed\n")
        with self.assertRaises(p.Refusal):
            source.read("source.py")

    def test_module_dirty_source_is_refused_before_import(self):
        path = self.root / "module.py"
        path.write_bytes(b"raise AssertionError('must never execute')\n")
        source = SimpleNamespace(root=self.root, read=lambda name: b"# committed expected\n")
        with self.assertRaises(p.Refusal):
            p.bound_module(source, "never_import_dirty", "module.py")

    def test_matching_timestamp_pyc_is_ignored_and_only_bound_raw_executes(self):
        name, relative = "som_python_environment_consumer_for_wheels", p.CONSUMER
        path = self.root / relative
        path.parent.mkdir(parents=True)
        raw = b"BOUND_RAW_EXECUTED = True\n"
        path.write_bytes(raw)
        evil = compile("raise AssertionError('unchecked matching PYC executed')", str(path), "exec", dont_inherit=True)
        header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(path.stat().st_mtime), len(raw))
        cached = Path(importlib.util.cache_from_source(str(path)))
        cached.parent.mkdir()
        cached.write_bytes(header + marshal.dumps(evil))
        source = SimpleNamespace(root=self.root, read=lambda relative: raw)
        module = p.bound_module(source, name, relative)
        self.assertTrue(module.BOUND_RAW_EXECUTED)
        self.assertIsNone(module.__cached__)
        self.assertIs(sys.modules[name], module)

    def test_wrong_helper_path_and_preloaded_helper_are_refused(self):
        name = "som_python_environment_consumer_for_wheels"
        source = SimpleNamespace(root=self.root, read=lambda relative: (_ for _ in ()).throw(AssertionError("must refuse before source read")))
        with self.assertRaises(p.Refusal):
            p.bound_module(source, name, "elsewhere.py")
        with patch.dict(sys.modules, {name: ModuleType(name)}), self.assertRaises(p.Refusal):
            p.bound_module(source, name, p.CONSUMER)

    def test_unknown_installed_hooks_code_scripts_metadata_and_record_changes_refused(self):
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        site = "lib/python3.12/site-packages/"
        extras = [site + "surplus.pth", site + "sitecustomize.py", site + "extra.py", "bin/rogue",
                  site + "requests-2.32.5.dist-info/INSTALLER"]
        for index, name in enumerate(extras):
            venv = self.root / f"venv-{index}"
            self.materialize(venv, closure, runner.tools["python"])
            target = venv / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"uv" if name.endswith("INSTALLER") else b"unproven extra bytes")
            with self.subTest(name=name), self.assertRaises(p.Refusal):
                p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")
        venv = self.root / "venv-record"
        self.materialize(venv, closure, runner.tools["python"])
        (venv / site / "requests-2.32.5.dist-info/RECORD").write_bytes(b"forged,sha256=bad,1\n")
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def test_exact_seed_cfg_hook_and_symlink_bytes_are_required(self):
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        venv = self.root / "venv"
        _, contents = self.materialize(venv, closure, runner.tools["python"])
        expected_cfg = f"home = {runner.tools['python'].parent}\nimplementation = CPython\nuv = 0.9.9\nversion_info = 3.12.11\ninclude-system-site-packages = false\nprompt = real-synthetic-product\n"
        self.assertEqual(contents["pyvenv.cfg"], expected_cfg.encode())
        self.assertEqual(contents["lib/python3.12/site-packages/_virtualenv.pth"], b"import _virtualenv")
        self.assertEqual(os.readlink(venv / "bin/python"), str(runner.tools["python"]))
        self.assertTrue(p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")["exact_scope_verified"])
        (venv / "lib/python3.12/site-packages/_virtualenv.pth").write_text("import unproven\n")
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def test_console_wrapper_and_generated_record_follow_exact_uv_source(self):
        self.blob = wheel_blob(changes={"requests-2.32.5.dist-info/entry_points.txt": b"[console_scripts]\nreq-cli = requests.cli:main\n"})
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        venv = self.root / "venv"
        _, contents = self.materialize(venv, closure, runner.tools["python"])
        launcher = (f"#!{venv}/bin/python\n# -*- coding: utf-8 -*-\nimport sys\nfrom requests.cli import main\n"
                    'if __name__ == "__main__":\n    if sys.argv[0].endswith("-script.pyw"):\n        sys.argv[0] = sys.argv[0][:-11]\n'
                    '    elif sys.argv[0].endswith(".exe"):\n        sys.argv[0] = sys.argv[0][:-4]\n    sys.exit(main())\n').encode()
        self.assertEqual(contents["bin/req-cli"], launcher)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(launcher).digest()).rstrip(b"=").decode()
        record = contents["lib/python3.12/site-packages/requests-2.32.5.dist-info/RECORD"]
        self.assertEqual(record.splitlines()[0], f"../../../bin/req-cli,sha256={encoded},{len(launcher)}".encode())
        self.assertEqual(len(list(csv.reader(io.StringIO(record.decode())))), 7)
        proof = p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")
        self.assertEqual(proof["generated_console_scripts"], 1)
        (venv / "bin/req-cli").write_bytes(launcher + b"# extra\n")
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def test_nested_payload_metadata_record_and_data_directory_stay_immutable(self):
        nested_record = "requests/vendor/nested-1.0.dist-info/RECORD"
        nested_metadata = "requests/vendor/nested-1.0.dist-info/METADATA"
        payload = "requests/vendor.data/payload.txt"
        self.blob = wheel_blob(changes={nested_record: b"nested payload record\n", nested_metadata: b"Name: nested\nVersion: 1.0\n", payload: b"payload bytes\n"})
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        self.assertEqual(closure["audits"]["requests"]["dist_info_dir"], "requests-2.32.5.dist-info")
        venv = self.root / "venv"
        expected, _ = self.materialize(venv, closure, runner.tools["python"])
        self.assertIn("lib/python3.12/site-packages/" + payload, expected)
        self.assertNotIn("payload.txt", expected)
        self.assertTrue(p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")["exact_scope_verified"])
        (venv / "lib/python3.12/site-packages" / nested_record).write_bytes(b"changed nested RECORD\n")
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def test_empty_exact_data_directories_relocate_but_nested_payload_data_does_not(self):
        extras = [(zipfile.ZipInfo("requests-2.32.5.data/headers/nested/"), b""),
                  (zipfile.ZipInfo("requests-2.32.5.data/data/empty/"), b""),
                  (zipfile.ZipInfo("requests/vendor.data/empty/"), b"")]
        self.blob = wheel_blob(extras=extras)
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        venv = self.root / "venv"
        self.materialize(venv, closure, runner.tools["python"])
        self.assertTrue((venv / "include/site/python3.12/requests/nested").is_dir())
        self.assertTrue((venv / "empty").is_dir())
        self.assertTrue((venv / "lib/python3.12/site-packages/requests/vendor.data/empty").is_dir())
        self.assertTrue(p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")["exact_scope_verified"])
        (venv / "unknown-empty-directory").mkdir()
        with self.assertRaises(p.Refusal):
            p.verify_installed_members(venv, p.installed_inventory(venv, self.root), closure, self.assets, runner.tools["python"], "real_synthetic_product")

    def test_data_only_probe_never_executes_installed_site_or_pth_hooks(self):
        site = self.root / "untrusted-installed-site"
        metadata = site / "requests-2.32.5.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_bytes(META)
        sentinel = self.root / "HOOK_EXECUTED"
        hook = f"from pathlib import Path; Path({str(sentinel)!r}).write_text('unsafe hook executed')\n"
        (site / "sitecustomize.py").write_text(hook)
        (site / "payload.pth").write_text("import sitecustomize\n")
        probe = self.root / "probe.py"
        probe.write_text(p.PROBE)
        # This regression itself runs only on GitHub. It starts the already
        # admitted test interpreter in no-site mode, never any installed code.
        done = subprocess.run([sys.executable, "-I", "-S", "-B", str(probe), str(site)], check=True, capture_output=True)
        observation = json.loads(done.stdout)
        self.assertTrue(observation["site_disabled"])
        self.assertFalse(observation["site_in_sys_path"])
        self.assertEqual(observation["packages"][0]["name"], "requests")
        self.assertFalse(sentinel.exists())

    def test_direct_base_python_without_no_site_flags_is_refused(self):
        runner, _ = self.runner_fixture()
        with patch.object(p.subprocess, "Popen") as popen, self.assertRaises(p.Refusal):
            runner.run([runner.tools["python"], "-I", "-B", "probe.py"], self.root, self.root / "cache", "bad-site-flags")
        popen.assert_not_called()

    def test_injected_installed_hook_blocks_probe_before_any_python_child(self):
        (self.root / "environments").mkdir()
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        runner.inject_extra = "lib/python3.12/site-packages/payload.pth"
        with self.assertRaises(p.Refusal):
            p.provision_named(runner, self.row(), closure, self.root / "probe.py", {"id": "product-0000"})
        self.assertFalse(any(call["argv"][0] == str(runner.tools["python"]) for call in runner.calls))
        self.assertTrue((self.root / "environments/product-0000/venv/lib/python3.12/site-packages/payload.pth").is_file())

    def test_qualified_hash_and_command_receipts_exist_before_cleanup(self):
        (self.root / "environments").mkdir()
        runner = FakeRunner(self.root, self.blob)
        closure = self.closure_fixture(runner)
        observed = []
        original_cleanup = p.cleanup_successful_venv
        def inspect(venv, out, successful, inventory_sha, qualification_path):
            checkpoint = json.loads(qualification_path.read_bytes())
            self.assertEqual(checkpoint["state"], "offline-environment-qualified")
            self.assertEqual(checkpoint["installed_inventory_sha256"], p.file_sha(venv.parent / "installed-files.json"))
            self.assertEqual(inventory_sha, checkpoint["installed_inventory_sha256"])
            self.assertEqual(checkpoint["installed_metadata_sha256"], p.file_sha(venv.parent / "installed-metadata.json"))
            self.assertEqual(len(checkpoint["command_receipts"]), 3)
            self.assertTrue(venv.is_dir())
            observed.append(True)
            return original_cleanup(venv, out, successful, inventory_sha, qualification_path)
        with patch.object(p, "cleanup_successful_venv", side_effect=inspect):
            p.provision_named(runner, self.row(), closure, self.root / "probe.py", {"id": "product-0000"})
        self.assertEqual(observed, [True])

    def test_actual_main_base_python_version_call_has_no_site_flags(self):
        tree = ast.parse(PRODUCER.read_bytes())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "python_version" for target in node.targets)]
        self.assertEqual(len(calls), 1)
        run = calls[0].value.func.value.func.value
        self.assertIsInstance(run, ast.Call)
        self.assertEqual([node.value for node in run.args[0].elts[1:]], ["-I", "-S", "-B", "--version"])

    def environment_fixture(self):
        inputs, assembly = self.root / "inputs", self.root / "assembly"
        inputs.mkdir()
        (assembly / "assembly").mkdir(parents=True)
        assembly_raw = b'{"synthetic":"assembly"}'
        (assembly / "assembly/manifest.json").write_bytes(assembly_raw)
        products = []
        for index in range(671):
            products.append({"cohort": "python_public" if index < 127 else "python_legacy", "id": f"frozen-{index}",
                             "files_map_sha256": p.sha(str(index).encode()), "requirements": self.row()["requirements"] if index == 0 else None,
                             "pyproject_bytes": PROJECT if index == 0 else None})
        plan = {"observer_requirements": ["requests==2.34.2"]}
        blobs = {p.ENV_PLAN: json.dumps(plan).encode(), p.CONSUMER: b"# synthetic bound consumer source\n",
                 "goldens.json": json.dumps({"cohorts": {"python_public": [{} for _ in range(127)], "python_legacy": [{} for _ in range(544)]}}).encode(),
                 "ts.json": b"{}"}
        sources = SimpleNamespace(read=lambda name: blobs[name])
        q = SimpleNamespace(GOLDENS="goldens.json", TS065="ts.json", inventory=lambda source, document: None,
                            products=lambda *args: products)
        observer = {"id": "observer", "requirements": {"requires_python": ">=3.12,<3.13", "runtime": plan["observer_requirements"], "development": []}, "pyproject_sha256": None}
        product = {"id": "product-0000", "requirements": products[0]["requirements"], "cohort": products[0]["cohort"],
                   "product_id": products[0]["id"], "files_map_sha256": products[0]["files_map_sha256"], "pyproject_sha256": p.sha(PROJECT.encode())}
        for row in (observer, product):
            directory = inputs / "environments" / row["id"]
            directory.mkdir(parents=True)
            raw = ("\n".join(row["requirements"]["runtime"] + row["requirements"]["development"]) + "\n").encode()
            (directory / "requirements.in").write_bytes(raw)
            row["requirements_in_sha256"] = p.sha(raw)
            if row["id"] != "observer":
                (directory / "pyproject.toml").write_bytes(PROJECT.encode())
        receipt = {"version": "som.hosted-python-environment-inventory.v1", "source_revision": "1" * 40,
                   "state": "complete-product-environment-inputs", "environment_input_set_complete": True,
                   "observed_products": 671, "expected_products": 671, "product_runtime_ready": False, "product_acceptance_passed": False,
                   "assembly_manifest_sha256": p.sha(assembly_raw), "environments": [observer, product],
                   "observer_runtime_products": [{key: row[key] for key in ("cohort", "id", "files_map_sha256")} for row in products[1:]],
                   "source_bindings": {name: {"sha256": p.sha(blobs[name])} for name in (p.CONSUMER, p.ENV_PLAN)}}
        (inputs / "receipt.json").write_text(json.dumps(receipt))
        return q, sources, inputs, assembly, receipt

    def test_complete671_bindings_preserve_separate_observer_and_product_inputs(self):
        q, sources, inputs, assembly, receipt = self.environment_fixture()
        rows, old, record = p.environment_inputs(q, sources, inputs, assembly, "1" * 40)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(old), 670)
        self.assertEqual(rows[0]["requirements_raw"], b"requests==2.34.2\n")
        self.assertEqual(rows[1]["requirements_raw"], b"requests==2.32.5\n")
        self.assertEqual(record["environment_receipt_sha256"], p.sha((inputs / "receipt.json").read_bytes()))

    def test_receipt_cannot_hide_partial_scope_or_changed_product_requirements(self):
        q, sources, inputs, assembly, receipt = self.environment_fixture()
        changed = copy.deepcopy(receipt)
        changed["observed_products"] = 670
        (inputs / "receipt.json").write_text(json.dumps(changed))
        with self.assertRaises(p.Refusal):
            p.environment_inputs(q, sources, inputs, assembly, "1" * 40)
        (inputs / "receipt.json").write_text(json.dumps(receipt))
        (inputs / "environments/product-0000/requirements.in").write_text("requests==2.34.2\n")
        with self.assertRaises(p.Refusal):
            p.environment_inputs(q, sources, inputs, assembly, "1" * 40)


if __name__ == "__main__":
    unittest.main()
