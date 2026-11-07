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
        self.source = self.repo / preflight.REQUIRED_INVENTORIES["phase1-public_examples"][0]
        self.source.parent.mkdir(parents=True)
        records = [{"id": "first"}, {"id": "second"}]
        self.source.write_text(json.dumps({
            group: records for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope")
        }))
        sources = {}
        inventories = []
        for name, (path, pointer) in preflight.REQUIRED_INVENTORIES.items():
            file = self.repo / path
            if not file.exists():
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps({pointer[1:]: records}))
            sources[path] = sha(file)
            inventories.append({"name": name, "path": path, "pointer": pointer, "id_key": "id", "ids": ["first", "second"]})
        self.plan = self.repo / preflight.PLAN_PATH
        plan = {name: {"checks": ["first", "second"]} for name in preflight.GATE_SECTIONS}
        plan["amendment006"]["checks"] = [f"check {index}" for index in range(10)]
        self.plan.write_text(json.dumps(plan))
        sources[preflight.PLAN_PATH] = sha(self.plan)
        for path in (*preflight.COMMAND_SOURCES, *preflight.INDEPENDENT_AUDITS, *(path for tools in preflight.AMENDMENT006_TOOLS for path in tools)):
            file = self.repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("public tool fixture\n")
            sources[path] = sha(file)
        self.asset = self.repo / "asset.txt"
        self.asset.write_text("public fixture asset\n")
        self.data = {
            "version": preflight.VERSION,
            "source_commit": "a" * 40,
            "source_files": sources,
            "inventories": inventories,
            "runner": {"hosts": ["fixture-host"]},
            "assets": [{"name": name, "path": "asset.txt", "sha256": sha(self.asset)} for name in sorted(preflight.REQUIRED_ASSETS)],
            "blockers": [],
            "product_acceptance_enabled": False,
            "gate_plan": {
                **{name: list(steps) for name, steps in preflight.GATE_STEPS.items()},
                "contract_sections": {name: preflight.section_digest(value) for name, value in plan.items()},
                "commands_source": list(preflight.COMMAND_SOURCES),
                "independent_audits": list(preflight.INDEPENDENT_AUDITS),
                "amendment_context": {"amendment006": {"checks": [
                    {"pointer": f"/amendment006/checks/{index}", "requirement": plan["amendment006"]["checks"][index],
                     "tools": list(tools), "execution": "not_run"}
                    for index, tools in enumerate(preflight.AMENDMENT006_TOOLS)
                ]}},
            },
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
        self.data["runner"]["hosts"] = ["unsupported-host"]
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.report()["runner"]["status"], "blocked")

    def test_inventory_missing_substituted_and_reordered(self):
        for ids in (["first"], ["first", "replacement"], ["second", "first"]):
            with self.subTest(ids=ids):
                if self.out.exists():
                    self.out.unlink()
                self.source.write_text(json.dumps({"public_examples": [{"id": value} for value in ids]}))
                self.data["source_files"][self.source.relative_to(self.repo).as_posix()] = sha(self.source)
                self.assertEqual(self.run_cli()[0], 1)
                self.assertEqual(self.report()["inventories"][0]["status"], "blocked")

    def test_inventory_malformed_reports_blocked(self):
        for body in ({"public_examples": [None]}, {"public_examples": {}}, {}, {"public_examples": [{"different": "key"}]}):
            with self.subTest(body=body):
                if self.out.exists():
                    self.out.unlink()
                self.source.write_text(json.dumps(body))
                self.data["source_files"][self.source.relative_to(self.repo).as_posix()] = sha(self.source)
                self.assertEqual(self.run_cli()[0], 1)
                self.assertEqual(self.report()["inventories"][0]["status"], "blocked")

    def test_private_asset_is_never_read(self):
        name = self.data["assets"][0]["name"]
        self.data["assets"][0] = {"name": name, "blocked_reason": "No approved hosted source"}
        self.data["blockers"] = ["Asset route pending"]
        with patch.object(preflight, "digest", side_effect=lambda path: sha(path)) as reader:
            self.assertEqual(self.run_cli()[0], 1)
        self.assertNotIn(self.repo / "private", [call.args[0] for call in reader.call_args_list])
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
        for name in (self.source.relative_to(self.repo).as_posix(), "asset.txt"):
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

    def test_whole_inventory_or_asset_group_cannot_be_omitted(self):
        for field in ("inventories", "assets"):
            for index in range(len(self.data[field])):
                with self.subTest(field=field, index=index):
                    data = copy.deepcopy(self.data)
                    data[field].pop(index)
                    self.assertEqual(self.run_cli(data)[0], 2)
                    self.assertFalse(self.out.exists())

    def test_inventory_cannot_point_at_another_group(self):
        self.data["inventories"][0]["pointer"] = "/coverage_cases"
        self.assertEqual(self.run_cli()[0], 2)

    def test_gate_section_and_step_omissions_refused(self):
        for name in preflight.GATE_SECTIONS:
            with self.subTest(section=name):
                data = copy.deepcopy(self.data)
                del data["gate_plan"]["contract_sections"][name]
                self.assertEqual(self.run_cli(data)[0], 2)
        for name in preflight.GATE_STEPS:
            for change in ([], list(reversed(self.data["gate_plan"][name]))):
                with self.subTest(group=name, change=change):
                    data = copy.deepcopy(self.data)
                    data["gate_plan"][name] = change
                    self.assertEqual(self.run_cli(data)[0], 2)

    def test_changed_current_amendment_remains_blocked_with_new_source_hash(self):
        plan = json.loads(self.plan.read_text())
        plan["amendment006"]["checks"].append("new required regression")
        self.plan.write_text(json.dumps(plan))
        self.data["source_files"][preflight.PLAN_PATH] = sha(self.plan)
        self.assertEqual(self.run_cli()[0], 1)
        row = next(row for row in self.report()["gate_sections"] if row["name"] == "amendment006")
        self.assertEqual(row["reason"], "frozen gate section sha256 mismatch")

    def test_unmapped_future_gate_remains_blocked(self):
        plan = json.loads(self.plan.read_text())
        plan["new_required_gates"] = ["must not disappear"]
        self.plan.write_text(json.dumps(plan))
        self.data["source_files"][preflight.PLAN_PATH] = sha(self.plan)
        self.assertEqual(self.run_cli()[0], 1)
        self.assertTrue(any(row.get("reason") == "unmapped frozen gate section" for row in self.report()["gate_sections"]))

    def test_current_amendment_mapping_cannot_be_omitted_or_substituted(self):
        original = self.data["gate_plan"]["amendment_context"]["amendment006"]["checks"]
        changes = [[], original[:-1], list(reversed(original))]
        for key, value in (("tools", []), ("tools", ["another.py"]), ("execution", "passed"), ("pointer", "/amendment006/checks/1")):
            changed = copy.deepcopy(original)
            changed[0][key] = value
            changes.append(changed)
        for checks in changes:
            with self.subTest(checks=checks):
                data = copy.deepcopy(self.data)
                data["gate_plan"]["amendment_context"]["amendment006"]["checks"] = checks
                self.assertEqual(self.run_cli(data)[0], 2)

    def test_audits_and_command_sources_cannot_be_omitted(self):
        for key in ("independent_audits", "commands_source"):
            with self.subTest(key=key):
                data = copy.deepcopy(self.data)
                data["gate_plan"][key] = []
                self.assertEqual(self.run_cli(data)[0], 2)

    def test_current_amendment_requirement_must_equal_raw_plan(self):
        self.data["gate_plan"]["amendment_context"]["amendment006"]["checks"][0]["requirement"] = "weaker check"
        self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(self.report()["gate_mappings"][0]["status"], "blocked")

    def test_current_amendment_tool_must_be_source_bound(self):
        del self.data["source_files"][preflight.AMENDMENT006_TOOLS[0][0]]
        self.assertEqual(self.run_cli()[0], 2)


    def test_product_acceptance_cannot_be_enabled_by_manifest(self):
        for value in (None, True, 0, "false"):
            with self.subTest(value=value):
                self.data["product_acceptance_enabled"] = value
                self.assertEqual(self.run_cli()[0], 2)


    def test_manifest_symlink_and_outside_path_refused(self):
        self.manifest.symlink_to(self.asset)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(preflight.main(["--repo", str(self.repo), "--manifest", "manifest.json", "--out", str(self.out)]), 2)
            self.assertEqual(preflight.main(["--repo", str(self.repo), "--manifest", str(self.repo.parent / "manifest.json"), "--out", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
