"""Runtime cleanup fixtures. Run on GitHub, never on the local host."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("react_products_cleanup_collector", HERE / "check_products.py")
collector = importlib.util.module_from_spec(SPEC)
fake_common = types.ModuleType("common")
fake_common.CONTRACT = HERE / "unused-cleanup-contract"
fake_common.REPO = HERE
# These fixtures use files and fake case results. No product helper or service runs.
with mock.patch.dict(sys.modules, {"check_contract": types.ModuleType("check_contract"), "common": fake_common}):
    with mock.patch.object(sys, "path", list(sys.path)):
        SPEC.loader.exec_module(collector)


SOURCE_FILES = (
    "index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
    "src/main.tsx", "src/App.tsx", "src/api.ts", "src/spec.ts", "src/form.ts", "src/styles.css",
    "src/pages/ListPage.tsx", "src/pages/DetailPage.tsx", "src/pages/FormPage.tsx", "src/pages/ErrorView.tsx",
    "src/app.test.tsx", "src/form.test.ts", "src/schema.ts", "README.md",
)


class ProductRuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="som-products-cleanup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.contract = self.root / "contract"
        self.contract.mkdir()
        (self.contract / "toolchain.json").write_text(json.dumps({"vendored_cache": {"path": "unused-cache"}}))
        (self.contract / "api-contract.json").write_text("{}")

    def make_case(self, out=None, cid="case-001"):
        out = self.root / "run" if out is None else out
        case = out / cid
        case.mkdir(parents=True)
        preserved = {}

        def evidence(relative, value):
            path = case / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = value.encode("utf-8") if isinstance(value, str) else value
            path.write_bytes(raw)
            preserved[path] = raw

        for relative in SOURCE_FILES:
            evidence(f"gates/product/{relative}", f"source: {relative}\n")
        for relative in (
            "gates.log", "api.log", "gates/gates.json", "gates/npm-logs/install.log", "api/postgres.log",
            "gates/dist-1/index.html", "gates/dist-1/assets/app.js", "gates/dist-2/index.html",
            "gates/dist-2/assets/app.js", "python/product/main.py", "python/prompt.json",
            "python/compile.response.json", "python/assemble.response.json", "api/raw.response.json",
        ):
            evidence(relative, f"evidence: {relative}\n")
        node_modules = case / "gates/product/node_modules"
        node_modules.mkdir()
        (node_modules / "runtime-package.js").write_text("runtime scratch\n")
        pgdata = case / "api/pgdata"
        pgdata.mkdir()
        (pgdata / "PG_VERSION").write_text("17\n")
        process = {
            "pid": 123456,
            "role": "postgres",
            "expected_cwd": str(pgdata),
            "stop_cwd": str(pgdata),
            "stop_cwd_verified": True,
            "stopped": True,
            "exit_code": 0,
        }
        evidence("api/report.json", json.dumps({"processes": [process], "result": {"steps": []}}))
        return out, case, preserved

    def assert_evidence_unchanged(self, preserved):
        for path, raw in preserved.items():
            self.assertTrue(path.is_file(), str(path))
            self.assertEqual(path.read_bytes(), raw, str(path))

    def test_cleanup_removes_only_owned_runtime_and_keeps_all_evidence(self):
        out, case, preserved = self.make_case()
        outside = self.root / "outside-source"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_bytes(b"keep outside source\n")
        # Installed packages can contain links. Removal must unlink them without visiting their target.
        (case / "gates/product/node_modules/outside-link").symlink_to(outside, target_is_directory=True)
        result = collector.cleanup_case_runtime(out, case.name)
        self.assertIs(result["passed"], True)
        self.assertEqual(result["problems"], [])
        self.assertEqual([item["status"] for item in result["targets"]], ["removed", "removed"])
        self.assertEqual(
            [item["path"] for item in result["targets"]],
            [f"{case.name}/gates/product/node_modules", f"{case.name}/api/pgdata"],
        )
        self.assertFalse((case / "gates/product/node_modules").exists())
        self.assertFalse((case / "api/pgdata").exists())
        self.assertEqual(marker.read_bytes(), b"keep outside source\n")
        self.assert_evidence_unchanged(preserved)

    def test_missing_runtime_directories_need_no_stop_report(self):
        out = self.root / "empty-runtime"
        case = out / "case-001"
        (case / "gates/product").mkdir(parents=True)
        (case / "api").mkdir()
        result = collector.cleanup_case_runtime(out, case.name)
        self.assertIs(result["passed"], True)
        self.assertEqual([item["status"] for item in result["targets"]], ["missing", "missing"])

    def test_symlink_root_case_target_or_ancestor_is_refused(self):
        for index, relative in enumerate((None, "case", "gates", "gates/product", "gates/product/node_modules",
                                          "api", "api/pgdata")):
            with self.subTest(relative=relative):
                out, case, _preserved = self.make_case(self.root / f"links-{index}")
                target = out if relative is None else case if relative == "case" else case / relative
                moved = target.with_name(target.name + "-original")
                target.rename(moved)
                target.symlink_to(moved, target_is_directory=True)
                result = collector.cleanup_case_runtime(out, case.name)
                self.assertIs(result["passed"], False)
                self.assertTrue(result["problems"])
                self.assertTrue(target.is_symlink())
                self.assertTrue(moved.is_dir())

    def test_symlink_above_output_root_is_refused(self):
        parent = self.root / "real-parent"
        out, case, preserved = self.make_case(parent / "run")
        alias = self.root / "parent-alias"
        alias.symlink_to(parent, target_is_directory=True)
        result = collector.cleanup_case_runtime(alias / "run", case.name)
        self.assertIs(result["passed"], False)
        self.assertTrue((out / case.name / "gates/product/node_modules").is_dir())
        self.assertTrue((out / case.name / "api/pgdata").is_dir())
        self.assert_evidence_unchanged(preserved)

    def test_live_or_ambiguous_postgres_is_refused(self):
        changes = (
            {"stopped": False}, {"stopped": 1}, {"stop_cwd_verified": False}, {"stop_cwd_verified": 1},
            {"stop_cwd": "/unowned/pgdata"}, {"expected_cwd": "/unowned/pgdata"},
            {"stop_cwd": None, "stop_cwd_verified": None, "exit_code": 0}, {"role": "python"},
        )
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                out, case, _preserved = self.make_case(self.root / f"ambiguous-{index}")
                report_path = case / "api/report.json"
                report = json.loads(report_path.read_text())
                report["processes"][0].update(change)
                report_path.write_text(json.dumps(report))
                result = collector.cleanup_case_runtime(out, case.name)
                self.assertIs(result["passed"], False)
                self.assertTrue((case / "api/pgdata/PG_VERSION").is_file())
                self.assertEqual(result["targets"][1]["status"], "refused")

    def test_missing_malformed_or_duplicate_postgres_report_is_refused(self):
        for name in ("missing", "malformed", "no-processes", "duplicate", "symlink"):
            with self.subTest(report=name):
                out, case, _preserved = self.make_case(self.root / name)
                report_path = case / "api/report.json"
                if name == "missing":
                    report_path.unlink()
                elif name == "malformed":
                    report_path.write_text("{broken")
                elif name == "no-processes":
                    report_path.write_text("{}")
                elif name == "duplicate":
                    report = json.loads(report_path.read_text())
                    report["processes"] *= 2
                    report_path.write_text(json.dumps(report))
                else:
                    outside_report = self.root / "outside-report.json"
                    report_path.rename(outside_report)
                    report_path.symlink_to(outside_report)
                result = collector.cleanup_case_runtime(out, case.name)
                self.assertIs(result["passed"], False)
                self.assertTrue((case / "api/pgdata/PG_VERSION").is_file())
                self.assertEqual(result["targets"][1]["status"], "refused")

    def test_any_postmaster_pid_blocks_postgres_cleanup(self):
        for name in ("file", "dangling-link"):
            with self.subTest(pid=name):
                out, case, _preserved = self.make_case(self.root / f"pid-{name}")
                pid_file = case / "api/pgdata/postmaster.pid"
                if name == "file":
                    pid_file.write_text("123456\n")
                else:
                    pid_file.symlink_to(self.root / "missing-pid")
                result = collector.cleanup_case_runtime(out, case.name)
                self.assertIs(result["passed"], False)
                self.assertTrue((case / "api/pgdata/PG_VERSION").is_file())
                self.assertTrue(any("postmaster.pid" in item for item in result["problems"]))

    def test_file_targets_and_unsafe_case_names_are_refused(self):
        out, case, _preserved = self.make_case()
        node_modules = case / "gates/product/node_modules"
        shutil.rmtree(node_modules)
        node_modules.write_text("keep unexpected file\n")
        result = collector.cleanup_case_runtime(out, case.name)
        self.assertIs(result["passed"], False)
        self.assertEqual(node_modules.read_text(), "keep unexpected file\n")
        for cid in ("..", "../other", "/other", "parent/child", "parent\\child", ""):
            with self.subTest(cid=cid):
                self.assertIs(collector.cleanup_case_runtime(out, cid)["passed"], False)

    def run_main(self, name, cleanup_runtime=False, cleanup_side_effect=None):
        out = self.root / name
        manifest = self.root / f"{name}-manifest.json"
        entries = [{"id": "case-a"}, {"id": "case-b"}]
        manifest.write_text(json.dumps({"products": entries}))
        preserved = {}

        def fake_case(_url, entry, case_out, _cache, _api):
            _out, _case, evidence = self.make_case(case_out, entry["id"])
            preserved.update(evidence)
            return {"id": entry["id"], "passed": True, "problems": []}

        argv = ["check_products.py", "--url", "http://127.0.0.1:19001", "--manifest", str(manifest),
                "--out", str(out), "--cache", str(self.root / "unused-cache")]
        if cleanup_runtime:
            argv.append("--cleanup-runtime")
        original_cleanup = collector.cleanup_case_runtime
        with (
            mock.patch.object(collector, "CONTRACT", self.contract),
            mock.patch.object(collector, "EXPECTED_PRODUCTS", len(entries)),
            mock.patch.object(collector, "check_case", side_effect=fake_case),
            mock.patch.object(collector, "cleanup_case_runtime", side_effect=cleanup_side_effect,
                              wraps=original_cleanup) as cleanup,
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = collector.main()
        report = json.loads((out / "report.json").read_text())
        return code, report, out, preserved, cleanup

    def test_default_main_keeps_old_rows_and_all_runtime(self):
        code, report, out, preserved, cleanup = self.run_main("default")
        self.assertEqual(code, 0)
        self.assertIs(report["passed"], True)
        cleanup.assert_not_called()
        for row in report["cases"]:
            self.assertNotIn("cleanup_runtime", row)
            self.assertTrue((out / row["id"] / "gates/product/node_modules").is_dir())
            self.assertTrue((out / row["id"] / "api/pgdata").is_dir())
        self.assert_evidence_unchanged(preserved)

    def test_flag_main_cleans_each_completed_case_and_keeps_evidence(self):
        code, report, out, preserved, cleanup = self.run_main("cleanup", cleanup_runtime=True)
        self.assertEqual(code, 0)
        self.assertEqual(cleanup.call_count, 2)
        self.assertIs(report["passed"], True)
        for row in report["cases"]:
            self.assertIs(row["cleanup_runtime"]["passed"], True)
            self.assertFalse((out / row["id"] / "gates/product/node_modules").exists())
            self.assertFalse((out / row["id"] / "api/pgdata").exists())
        self.assert_evidence_unchanged(preserved)

    def test_cleanup_exception_fails_case_and_main_continues(self):
        original_cleanup = collector.cleanup_case_runtime

        def fail_first(out, cid):
            if cid == "case-a":
                raise PermissionError("fixture cleanup denied")
            return original_cleanup(out, cid)

        code, report, out, preserved, cleanup = self.run_main(
            "cleanup-error", cleanup_runtime=True, cleanup_side_effect=fail_first
        )
        self.assertEqual(code, 1)
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(report["failed"], ["case-a"])
        self.assertIs(report["passed"], False)
        self.assertIs(report["cases"][0]["cleanup_runtime"]["passed"], False)
        self.assertTrue(any("fixture cleanup denied" in item for item in report["cases"][0]["problems"]))
        self.assertIs(report["cases"][1]["passed"], True)
        self.assertTrue((out / "case-a/api/pgdata").is_dir())
        self.assertFalse((out / "case-b/api/pgdata").exists())
        self.assert_evidence_unchanged(preserved)


if __name__ == "__main__":
    unittest.main()
