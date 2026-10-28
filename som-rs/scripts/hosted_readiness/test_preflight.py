"""Fixture-only checks. Run these in GitHub Actions, not on the local host."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("hosted_preflight", Path(__file__).with_name("preflight.py"))
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.source = self.repo / "inventory.json"
        self.source.write_text(json.dumps({"cases": [{"id": "first"}, {"id": "second"}]}))
        self.asset = self.repo / "asset.txt"
        self.asset.write_text("public fixture asset\n")
        self.data = {
            "version": preflight.VERSION,
            "source_commit": "a" * 40,
            "source_files": {"inventory.json": sha(self.source)},
            "inventories": [{"name": "fixture", "path": "inventory.json", "pointer": "/cases", "id_key": "id", "ids": ["first", "second"]}],
            "runner": {"host": "fixture-host"},
            "assets": [{"name": "public", "path": "asset.txt", "sha256": sha(self.asset)}],
            "blockers": [],
        }
        self.manifest = self.repo / "manifest.json"
        self.out = self.repo / ".local/hosted-readiness/report.json"

    def run_cli(self, data=None, extra=(), out=None):
        self.manifest.write_text(json.dumps(self.data if data is None else data))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(preflight, "host_identity", return_value="fixture-host"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = preflight.main(["--repo", str(self.repo), "--manifest", "manifest.json", "--out", str(out or self.out), *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def report(self):
        return json.loads(self.out.read_text())

    def test_ready_is_not_acceptance(self):
        code, stdout, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.report()["status"], "ready")
        self.assertFalse(self.report()["acceptance_passed"])
        self.assertEqual(self.report()["product_suites"], "not_run")
        self.assertEqual(self.report()["source_commit"], "a" * 40)
        self.assertEqual(self.report()["manifest_sha256"], sha(self.manifest))
        self.assertEqual(self.report()["tool_sha256"], sha(Path(preflight.__file__)))
        self.assertEqual(json.loads(stdout)["status"], "ready")

    def test_all_missing_requirements_are_recorded(self):
        self.source.unlink()
        self.asset.unlink()
        code, _, _ = self.run_cli()
        self.assertEqual(code, 1)
        report = self.report()
        self.assertEqual(report["source_files"][0]["status"], "blocked")
        self.assertEqual(report["inventories"][0]["status"], "blocked")
        self.assertEqual(report["assets"][0]["status"], "blocked")

    def test_hash_mismatch(self):
        self.asset.write_text("changed")
        code, _, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(self.report()["assets"][0]["reason"], "sha256 mismatch")

    def test_source_hash_mismatch(self):
        self.source.write_text(self.source.read_text() + "\n")
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.report()["source_files"][0]["reason"], "sha256 mismatch")

    def test_unsupported_platform(self):
        self.data["runner"]["host"] = "aarch64-apple-darwin"
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.report()["runner"]["status"], "blocked")

    def test_inventory_missing_substituted_and_reordered(self):
        for ids in (["first"], ["first", "replacement"], ["second", "first"]):
            with self.subTest(ids=ids):
                if self.out.exists():
                    self.out.unlink()
                self.source.write_text(json.dumps({"cases": [{"id": value} for value in ids]}))
                self.data["source_files"]["inventory.json"] = sha(self.source)
                self.assertEqual(self.run_cli()[0], 1)
                self.assertEqual(self.report()["inventories"][0]["status"], "blocked")

    def test_inventory_malformed_reports_blocked(self):
        for body in ({"cases": [None]}, {"cases": {}}, {}, {"cases": [{"different": "key"}]}):
            with self.subTest(body=body):
                if self.out.exists():
                    self.out.unlink()
                self.source.write_text(json.dumps(body))
                self.data["source_files"]["inventory.json"] = sha(self.source)
                self.assertEqual(self.run_cli()[0], 1)
                self.assertEqual(self.report()["inventories"][0]["status"], "blocked")

    def test_private_asset_is_never_read(self):
        self.data["assets"] = [{"name": "private", "blocked_reason": "No approved hosted source"}]
        self.data["blockers"] = ["Runner decision pending"]
        with patch.object(preflight, "digest", side_effect=lambda path: sha(path)) as reader:
            self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(
            [call.args[0] for call in reader.call_args_list],
            [self.source, self.manifest, Path(preflight.__file__)],
        )
        self.assertEqual(self.report()["assets"][0]["reason"], "No approved hosted source")

    def test_duplicate_inventory_and_asset_names_refused(self):
        for field in ("inventories", "assets"):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data[field].append(copy.deepcopy(data[field][0]))
                self.assertEqual(self.run_cli(data)[0], 2)
                self.assertFalse(self.out.exists())

    def test_private_asset_cannot_include_readable_path(self):
        self.data["assets"] = [{"name": "private", "blocked_reason": "private", "path": "secret"}]
        self.assertEqual(self.run_cli()[0], 2)
        self.assertFalse(self.out.exists())

    def test_protected_and_invalid_ports(self):
        for port in ("8765", "18765", "0", "65536", "-1"):
            with self.subTest(port=port):
                self.assertEqual(self.run_cli(extra=("--port", port))[0], 2)
                self.assertFalse(self.out.exists())

    def test_unsafe_source_and_asset_paths(self):
        for field in ("source", "asset"):
            for value in ("../escape", "/etc/passwd", "path/../escape", "./asset", "a\\b", "a//b"):
                with self.subTest(field=field, path=value):
                    data = copy.deepcopy(self.data)
                    if field == "source":
                        data["source_files"] = {value: "0" * 64}
                    else:
                        data["assets"][0]["path"] = value
                    self.assertEqual(self.run_cli(data)[0], 2)
                    self.assertFalse(self.out.exists())

    def test_source_and_asset_symlinks_refused(self):
        for name in ("inventory.json", "asset.txt"):
            with self.subTest(name=name):
                target = self.repo / name
                body = target.read_text()
                target.unlink()
                target.symlink_to(self.repo / "manifest.json")
                self.assertEqual(self.run_cli()[0], 2)
                target.unlink()
                target.write_text(body)

    def test_symlink_directory_refused(self):
        (self.repo / "linked").symlink_to(self.repo, target_is_directory=True)
        self.data["assets"][0]["path"] = "linked/asset.txt"
        self.assertEqual(self.run_cli()[0], 2)

    def test_output_source_path_existing_output_and_traversal_refused(self):
        for out in (self.repo / "new-source.json", self.repo / "asset.txt", self.repo / ".local/hosted-readiness/../escape.json"):
            with self.subTest(out=out):
                self.assertEqual(self.run_cli(out=out)[0], 2)
        self.assertEqual(self.run_cli()[0], 0)
        previous = self.out.read_bytes()
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.out.read_bytes(), previous)

    def test_output_symlink_refused(self):
        (self.repo / ".local").symlink_to(self.repo, target_is_directory=True)
        self.assertEqual(self.run_cli()[0], 2)

    def test_new_output_outside_repo(self):
        out = self.repo.parent / "report.json"
        self.assertEqual(self.run_cli(out=out)[0], 0)
        self.assertTrue(out.exists())

    def test_malformed_manifest_types_refused(self):
        variants = [None, [], "text", {}, {**self.data, "source_commit": 2}, {**self.data, "source_files": []}, {**self.data, "inventories": [None]}, {**self.data, "runner": []}, {**self.data, "assets": [None]}, {**self.data, "blockers": "text"}]
        for data in variants:
            with self.subTest(data=data):
                self.manifest.write_text(json.dumps(data))
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = preflight.main(["--repo", str(self.repo), "--manifest", "manifest.json", "--out", str(self.out)])
                self.assertEqual(code, 2)
                self.assertFalse(self.out.exists())

    def test_malformed_json_refused(self):
        self.manifest.write_text("{broken")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(preflight.main(["--repo", str(self.repo), "--manifest", "manifest.json", "--out", str(self.out)]), 2)
        self.assertFalse(self.out.exists())

    def test_inventory_must_be_source_bound_and_ids_unique(self):
        for change in ({"path": "other.json"}, {"ids": ["first", "first"]}, {"pointer": 3}, {"id_key": None}, {"ids": []}):
            with self.subTest(change=change):
                data = copy.deepcopy(self.data)
                data["inventories"][0].update(change)
                self.assertEqual(self.run_cli(data)[0], 2)

    def test_manifest_symlink_and_outside_path_refused(self):
        self.manifest.symlink_to(self.asset)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(preflight.main(["--repo", str(self.repo), "--manifest", "manifest.json", "--out", str(self.out)]), 2)
            self.assertEqual(preflight.main(["--repo", str(self.repo), "--manifest", str(self.repo.parent / "manifest.json"), "--out", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
