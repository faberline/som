"""Synthetic environment-input checks. Run on GitHub only."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise RuntimeError("GitHub tests only")

SPEC = importlib.util.spec_from_file_location("qualify_python", Path(__file__).with_name("qualify_python.py"))
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)

PROJECT = '''[project]
name = "synthetic_product"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["requests==2.32.5", "sqlalchemy[asyncio]==2.0.44", "psycopg[binary]==3.3.6"]
[dependency-groups]
dev = ["httpx==0.28.1"]
[tool.uv]
package = false
'''


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def fixture(self, files=None):
        if files is None:
            files = {"pyproject.toml": PROJECT, "product.py": "# Synthetic public fixture. Never imported.\n"}
        golden = {"id": "synthetic", "index": 0, "prompt_sha256": "3" * 64,
                  "files_map_sha256": q.files_digest(files), "file_count": len(files),
                  "raw_assemble_response_sha256": q.sha(encoded({"status": "ok", "files": files})),
                  "accepted_per_file_hashes": [{"path_sha256": q.sha(name.encode()), "content_sha256": q.sha(text.encode())}
                                               for name, text in sorted(files.items())]}
        observations, variants = [], []
        for index, variant in enumerate(("none", "python")):
            product = f"assembly/observations/{index:04d}/product"
            directory = self.root / product
            directory.mkdir(parents=True)
            listing = []
            for name, text in files.items():
                (directory / name).write_bytes(text.encode())
                listing.append({"path": name, "sha256": q.sha(text.encode()), "size": len(text.encode())})
            record = {"state": "passed", "product_dir": product, "actual_files_map_sha256": golden["files_map_sha256"], "files": listing,
                      "target": "python", "cohort": "python_public", "source": "public", "id": "synthetic", "index": 0,
                      "compile_variant": variant, "mode": "compile-default", "observation_index": index}
            for kind in ("request", "response"):
                path = f"assembly/observations/{index:04d}/assemble.{kind}.raw"
                body = encoded({"status": "ok", "files": files}) if kind == "response" else encoded({"synthetic": kind, "variant": variant})
                (self.root / path).write_bytes(body)
                record[kind + "_path"], record[kind + "_sha256"] = path, q.sha(body)
            observations.append(copy.deepcopy(record))
            variants.append({**record, "compile_variant": variant, "compile_row_index": index, "observation_index": index})
        entry = {"cohort": "python_public", "id": "synthetic", "index": 0, "prompt_sha256": golden["prompt_sha256"],
                 "state": "passed", "product_dir": variants[0]["product_dir"],
                 "expected_files_map_sha256": golden["files_map_sha256"], "compile_variants": variants}
        document = {"version": "som.native-assembly-experiment.v1", "tested_sha": "1" * 40,
                    "state": "experimental-unsealed", "product_acceptance_passed": False, "assembly_experiment_complete": True,
                    "input_receipts": {"goldens_raw_sha256": "a" * 64, "ts065_raw_sha256": "b" * 64},
                    "products": {"python": [entry]}, "observations": observations}
        goldens = {"cohorts": {"python_public": [golden], "python_legacy": []}}
        return document, goldens

    def consume(self, document, goldens):
        return q.products(self.root, document, goldens, "1" * 40, "a" * 64, "b" * 64)

    def test_preserves_each_constraint_extra_order_and_group(self):
        record = q.requirements(PROJECT.encode())
        self.assertEqual(record["runtime"], ["requests==2.32.5", "sqlalchemy[asyncio]==2.0.44", "psycopg[binary]==3.3.6"])
        self.assertEqual(record["development"], ["httpx==0.28.1"])

    def test_refuses_unpinned_or_external_requirements(self):
        for value in ("requests>=2", "requests", "requests @ https://example.invalid/a.whl", "mlx==1.0"):
            with self.subTest(value=value), self.assertRaises(q.Refusal):
                q.requirements(PROJECT.replace("requests==2.32.5", value).encode())

    def test_refuses_build_or_resolution_overrides(self):
        for text in (PROJECT + '\n[build-system]\nrequires = ["setuptools"]\n',
                     PROJECT + '\n[tool.uv.sources]\nrequests = {path = "../outside"}\n',
                     PROJECT.replace('dev = ["httpx==0.28.1"]', 'dev = [{include-group = "other"}]'),
                     PROJECT.replace('package = false', 'package = true')):
            with self.subTest(text=text), self.assertRaises(q.Refusal):
                q.requirements(text.encode())

    def test_quality_checker_uses_owning_product_tools(self):
        project = PROJECT.replace('dev = ["httpx==0.28.1"]', 'dev = ["httpx==0.28.1", "ruff==0.16.6", "mypy==2.3.1", "pylint==4.0.9", "isort==9.0.2"]')
        project += '\n[tool.som.python-quality]\ncontract = "som.python-quality.v1"\n[tool.som.python-quality.tools]\nruff = "0.16.6"\nmypy = "2.3.1"\npylint = "4.0.9"\nisort = "9.0.2"\n'
        checker = q.requirements(project.encode())["quality_checker"]
        self.assertEqual(checker["interpreter"], "this-environment/venv/bin/python")
        self.assertEqual(checker["tool_versions"]["mypy"], "2.3.1")
        with self.assertRaises(q.Refusal):
            q.requirements(project.replace('"mypy==2.3.1", ', '').encode())

    def test_full_scope_consumes_both_real_variants(self):
        document, goldens = self.fixture()
        rows = self.consume(document, goldens)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pyproject_bytes"], PROJECT)
        self.assertEqual(rows[0]["requirements"]["runtime"][0], "requests==2.32.5")

    def test_old_product_keeps_observer_route_without_fake_project(self):
        document, goldens = self.fixture({"old_product.py": "# Synthetic old product. Never imported.\n"})
        rows = self.consume(document, goldens)
        self.assertIsNone(rows[0]["requirements"])
        self.assertIsNone(rows[0]["pyproject_bytes"])

    def test_real_source_inventory_preserves_separate_requirement_sets(self):
        root = Path(__file__).resolve().parents[4]
        plan = q.parse((Path(__file__).with_name("environment-plan.json")).read_bytes())
        class ReadOnlySource:
            def read(self, name):
                return q.read(root / q.relative(name))
        result = q.inventory(ReadOnlySource(), plan)
        self.assertEqual(len(result["templates"]), 8)
        self.assertEqual(len(result["direct_runtime_spellings"]), 17)
        self.assertEqual(len(result["direct_development_spellings"]), 8)
        self.assertIn("requests==2.34.2", result["observer_requirements"])
        self.assertNotIn("requests==2.32.5", result["observer_requirements"])
        changed = copy.deepcopy(plan)
        changed["source_sha256"][plan["template_sources"][0]] = "0" * 64
        with self.assertRaises(q.Refusal):
            q.inventory(ReadOnlySource(), changed)

    def test_no_partial_or_self_accepted_input(self):
        document, goldens = self.fixture()
        for key, value in (("assembly_experiment_complete", False), ("product_acceptance_passed", True), ("tested_sha", "2" * 40)):
            changed = copy.deepcopy(document)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(q.Refusal):
                self.consume(changed, goldens)

    def test_missing_duplicate_or_reordered_frozen_slot_refused(self):
        document, goldens = self.fixture()
        for mutate in (lambda d: d["products"]["python"].clear(),
                       lambda d: d["products"]["python"].append(copy.deepcopy(d["products"]["python"][0])),
                       lambda d: d["products"]["python"][0].update(index=1)):
            changed = copy.deepcopy(document)
            mutate(changed)
            with self.assertRaises(q.Refusal):
                self.consume(changed, goldens)

    def test_missing_or_reused_variant_receipt_refused(self):
        document, goldens = self.fixture()
        changed = copy.deepcopy(document)
        changed["products"]["python"][0]["compile_variants"][1]["observation_index"] = 0
        with self.assertRaises(q.Refusal):
            self.consume(changed, goldens)
        document["products"]["python"][0]["compile_variants"].pop()
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)

    def test_modified_unselected_variant_blocks_whole_inventory(self):
        document, goldens = self.fixture()
        (self.root / document["products"]["python"][0]["compile_variants"][1]["product_dir"] / "product.py").write_text("# changed\n")
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)

    def test_exchange_bytes_are_reread(self):
        document, goldens = self.fixture()
        (self.root / document["observations"][0]["response_path"]).write_bytes(b"changed")
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)

    def test_rehashed_changed_raw_reply_does_not_replace_frozen_pin(self):
        document, goldens = self.fixture()
        observation = document["observations"][0]
        path = self.root / observation["response_path"]
        path.write_bytes(path.read_bytes() + b" ")
        observation["response_sha256"] = document["products"]["python"][0]["compile_variants"][0]["response_sha256"] = q.sha(path.read_bytes())
        with self.assertRaisesRegex(q.Refusal, "frozen raw product reply"):
            self.consume(document, goldens)

    def test_raw_reply_must_contain_the_saved_complete_product(self):
        document, goldens = self.fixture()
        wrong = encoded({"status": "ok", "files": {"other.py": "# different\n"}})
        goldens["cohorts"]["python_public"][0]["raw_assemble_response_sha256"] = q.sha(wrong)
        for observation, variant in zip(document["observations"], document["products"]["python"][0]["compile_variants"]):
            (self.root / observation["response_path"]).write_bytes(wrong)
            observation["response_sha256"] = variant["response_sha256"] = q.sha(wrong)
        with self.assertRaisesRegex(q.Refusal, "raw reply differs"):
            self.consume(document, goldens)

    def test_named_observation_and_variant_cannot_be_relabelled(self):
        document, goldens = self.fixture()
        for field, value in (("id", "other"), ("cohort", "python_legacy"), ("source", "legacy"),
                             ("compile_variant", "none"), ("index", 1), ("observation_index", 0), ("target", "rust")):
            changed = copy.deepcopy(document)
            changed["observations"][1][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(q.Refusal, "named identity"):
                self.consume(changed, goldens)

    def test_physical_observation_directory_must_match_its_index(self):
        document, goldens = self.fixture()
        variant = document["products"]["python"][0]["compile_variants"][1]
        variant["product_dir"] = document["observations"][1]["product_dir"] = document["observations"][0]["product_dir"]
        with self.assertRaisesRegex(q.Refusal, "physical proof"):
            self.consume(document, goldens)

    def test_checked_mode_is_bound_to_frozen_product_variant(self):
        document, goldens = self.fixture()
        document["observations"][0]["mode"] = "checked-dsl"
        with self.assertRaisesRegex(q.Refusal, "named identity"):
            self.consume(document, goldens)

    def test_extra_nonregular_product_member_is_refused(self):
        document, goldens = self.fixture()
        directory = self.root / document["products"]["python"][0]["product_dir"]
        os.mkfifo(directory / "extra-pipe")
        with self.assertRaisesRegex(q.Refusal, "nonregular product member"):
            self.consume(document, goldens)

    def test_output_inside_assembly_is_refused_before_creation(self):
        document, _goldens = self.fixture()
        directory = self.root / document["products"]["python"][0]["product_dir"]
        out = directory / "new-output"
        with self.assertRaisesRegex(q.Refusal, "outside assembly"):
            q.checked_output(self.root / "repo", out, self.root, self.root)
        self.assertFalse(out.exists())

    def test_complete_map_does_not_replace_per_file_authority(self):
        document, goldens = self.fixture()
        goldens["cohorts"]["python_public"][0]["accepted_per_file_hashes"][0]["content_sha256"] = "f" * 64
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)

    def test_extra_file_and_symlink_refused(self):
        document, goldens = self.fixture()
        directory = self.root / document["products"]["python"][0]["product_dir"]
        (directory / "unlisted").write_text("extra")
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)
        (directory / "unlisted").unlink()
        (directory / "unlisted").symlink_to(directory / "product.py")
        with self.assertRaises(q.Refusal):
            self.consume(document, goldens)

    def test_output_keeps_product_bytes_and_isolates_environment_paths(self):
        needs = q.requirements(PROJECT.encode())
        first = q.write_environment(self.root, "product-0001", needs, PROJECT)
        second = q.write_environment(self.root, "product-0002", needs, PROJECT)
        self.assertEqual((self.root / "product-0001/pyproject.toml").read_bytes(), PROJECT.encode())
        self.assertNotEqual(first["cache"], second["cache"])
        self.assertNotEqual(first["venv"], second["venv"])
        self.assertNotEqual(first["wheelhouse"], second["wheelhouse"])
        self.assertIn("sqlalchemy[asyncio]==2.0.44", (self.root / "product-0001/requirements.in").read_text())
        with self.assertRaises(FileExistsError):
            q.write_environment(self.root, "product-0001", needs, PROJECT)

    def test_unsafe_paths_and_duplicate_json_keys_refused(self):
        for value in ("../outside", "/outside", "a//b", "a\\b", "a/./b", "a\nb"):
            with self.subTest(value=value), self.assertRaises(q.Refusal):
                q.relative(value)
        with self.assertRaises(q.Refusal):
            q.parse(b'{"a":1,"a":2}')

    def test_dirty_source_refused_without_running_any_project_code(self):
        path = self.root / "source.py"
        committed = b"# fixture, never executed\n"
        path.write_bytes(committed)
        oid = hashlib.sha1(b"blob " + str(len(committed)).encode() + b"\0" + committed).hexdigest()
        source = object.__new__(q.Sources)
        source.root, source.revision, source.pins = self.root, "1" * 40, {}
        def fake_git(*args):
            if args[0] == "ls-tree":
                return b"100644 blob " + oid.encode() + b"\tsource.py\0"
            return str(len(committed)).encode() if args[1] == "-s" else committed
        source.git = fake_git
        self.assertEqual(source.read("source.py"), committed)
        path.write_text("# changed\n")
        with self.assertRaises(q.Refusal):
            source.read("source.py")


if __name__ == "__main__":
    unittest.main()
