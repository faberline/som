"""Quality boundaries fail closed; legacy rendering remains unchanged."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from som_core import composed, quality
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/scripts"))
from quality_policy import inspect


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.blocks = load_blocks(ROOT / "som-code-python/data/blocks/composed-quality")
        cls.binary = ROOT / "som-rs/models/composed-quality-candidate/ruff"
        cls.row = json.loads((ROOT / "som-core/data/composed/regression.jsonl").read_text().splitlines()[3])
        cls.files = quality.reference(cls.row["prompt"], cls.row["dsl"], cls.blocks, cls.binary)

    def test_final_formatting_is_idempotent(self) -> None:
        for path, source in self.files.items():
            if path.endswith(".py"):
                self.assertEqual(source, quality.format_source(path, source, self.binary,
                                                               package=self.row["prompt"]["package"]))

    def test_missing_annotations_and_unused_variable_fail(self) -> None:
        for source in ("def bad(value):\n    return value\n",
                       "def bad(value: int) -> int:\n    unused = 1\n    return value\n"):
            with self.assertRaises(V1Error) as raised:
                quality.format_source("app/app.py", source, self.binary)
            self.assertEqual(raised.exception.code, "OUTPUT_QUALITY_FAILED")

    def test_missing_security_transaction_and_dependency_blocks_fail(self) -> None:
        catalog = json.loads(quality.CATALOG.read_text())
        for missing in ("auth", "head", "batch", "database"):
            dsl = deepcopy(self.row["dsl"])
            for file in dsl["files"]:
                file["blocks"] = [block for block in file["blocks"] if block["name"] != missing]
            with self.assertRaises(V1Error):
                composed.validate_plan(self.row["prompt"], dsl, catalog)

    def test_unapproved_suppressions_and_weakened_config_fail(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="som-quality-negative-") as directory:
            root = Path(directory)
            for name, source in self.files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            files = [name for name in self.files if name.endswith(".py")]
            self.assertTrue(inspect(root, files)["passed"])
            path = root / (self.row["prompt"]["package"] + "/app.py")
            path.write_text("# mypy: ignore-errors\n" + path.read_text())
            self.assertFalse(inspect(root, files)["passed"])
            path.write_text(self.files[str(path.relative_to(root))])
            config = root / "pyproject.toml"
            config.write_text(config.read_text().replace("strict = true", "strict = false"))
            self.assertFalse(inspect(root, files)["passed"])


if __name__ == "__main__":
    unittest.main()
