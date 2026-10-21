#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/python_quickstart/smoke.py and docs/python-be.md."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SMOKE_PY = SCRIPT_DIR / "smoke.py"
EXAMPLES_DIR = REPO_ROOT / "som-rs" / "contracts" / "compiler-v1" / "examples"
PYTHON_BE_DOC = REPO_ROOT / "docs" / "python-be.md"
GOAL_DOC = REPO_ROOT / "docs" / "goal.md"
ROADMAP_DOC = REPO_ROOT / "docs" / "roadmap.md"
TEST_URL = "http://127.0.0.1:19876"


def make_fake_som(
    directory: Path,
    *,
    omit_on_case: str | None = None,
    omit_item: str | None = None,
    fail_on_case: str | None = None,
) -> Path:
    script = directory / "fake_som.py"
    code = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        from pathlib import Path
        import sys

        argv = sys.argv[1:]
        if len(argv) != 6 or argv[0] != "write" or argv[2] != "--out" or argv[4] != "--url":
            sys.stderr.write(f"unexpected args: {{argv!r}}\\n")
            sys.exit(2)

        source = Path(argv[1])
        out = Path(argv[3])
        url = argv[5]
        if not source.is_file():
            sys.stderr.write(f"missing source: {{source}}\\n")
            sys.exit(1)
        if url != {TEST_URL!r}:
            sys.stderr.write(f"unexpected url: {{url}}\\n")
            sys.exit(1)

        fail_case = {fail_on_case!r}
        if fail_case is not None and out.name == fail_case:
            sys.stderr.write(json.dumps({{"status": "error", "code": "INVALID_VALUE"}}) + "\\n")
            sys.exit(2)

        out.mkdir(parents=True, exist_ok=True)
        omit_case = {omit_on_case!r}
        omit = {omit_item!r} if (omit_case is None or out.name == omit_case) else None

        if omit != "pyproject.toml":
            (out / "pyproject.toml").write_text('[project]\\nname = "demo"\\n', encoding="utf-8")
        if omit != "README.md":
            (out / "README.md").write_text("# demo service\\n", encoding="utf-8")
        if omit != "tests/":
            tests_dir = out / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_demo.py").write_text("import unittest\\n", encoding="utf-8")

        print(json.dumps({{"status": "ok", "out": str(out), "files": 3, "target": "python"}}))
        """
    )
    script.write_text(code, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


class SmokeScriptTest(unittest.TestCase):
    def expected_example_count(self) -> int:
        cnls = list(EXAMPLES_DIR.glob("*.cnl"))
        prompts = list(EXAMPLES_DIR.glob("*.prompt.json"))
        self.assertGreater(len(cnls), 0)
        self.assertEqual(len(cnls), len(prompts))
        return len(cnls) + len(prompts)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SMOKE_PY), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_passing_smoke_summary_counts(self) -> None:
        expected_total = self.expected_example_count()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_som = make_fake_som(tmp_path)
            out_dir = tmp_path / "fresh-out"

            proc = self.run_cli(
                "--som",
                str(fake_som),
                "--url",
                TEST_URL,
                "--out",
                str(out_dir),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            summary = json.loads(lines[0])
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["total"], expected_total)
            self.assertEqual(summary["passed"], expected_total)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["failures"], [])

    def test_missing_required_file_or_tests_dir_is_reported_and_fails(self) -> None:
        expected_total = self.expected_example_count()
        for missing_item in ("pyproject.toml", "README.md", "tests/"):
            with self.subTest(missing_item=missing_item), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                fake_som = make_fake_som(
                    tmp_path,
                    omit_on_case="v1-cnl",
                    omit_item=missing_item,
                )
                out_dir = tmp_path / "out"

                proc = self.run_cli(
                    "--som",
                    str(fake_som),
                    "--url",
                    TEST_URL,
                    "--out",
                    str(out_dir),
                )
                self.assertEqual(proc.returncode, 1)
                summary = json.loads(proc.stdout.strip())
                self.assertEqual(summary["status"], "error")
                self.assertEqual(summary["total"], expected_total)
                self.assertEqual(summary["passed"], expected_total - 1)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(len(summary["failures"]), 1)
                failure = summary["failures"][0]
                self.assertEqual(failure["name"], "v1-cnl")
                self.assertIn(missing_item, failure["missing"])

    def test_existing_non_empty_out_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_som = make_fake_som(tmp_path)
            existing_dir = tmp_path / "existing-out"
            existing_dir.mkdir()
            (existing_dir / "keep.txt").write_text("do not overwrite\n", encoding="utf-8")

            proc = self.run_cli(
                "--som",
                str(fake_som),
                "--url",
                TEST_URL,
                "--out",
                str(existing_dir),
            )
            self.assertEqual(proc.returncode, 1)
            summary = json.loads(proc.stdout.strip())
            self.assertEqual(summary["status"], "error")
            self.assertIn("Refused --out", summary["error"])
            self.assertEqual(
                (existing_dir / "keep.txt").read_text(encoding="utf-8"),
                "do not overwrite\n",
            )

            existing_file = tmp_path / "existing-file"
            existing_file.write_text("file\n", encoding="utf-8")
            proc_file = self.run_cli(
                "--som",
                str(fake_som),
                "--url",
                TEST_URL,
                "--out",
                str(existing_file),
            )
            self.assertEqual(proc_file.returncode, 1)
            summary_file = json.loads(proc_file.stdout.strip())
            self.assertEqual(summary_file["status"], "error")
            self.assertIn("Refused --out", summary_file["error"])

    def test_python_be_doc_relative_links_resolve_and_v1_example_matches(self) -> None:
        self.assertTrue(PYTHON_BE_DOC.is_file(), f"missing {PYTHON_BE_DOC}")
        doc_text = PYTHON_BE_DOC.read_text(encoding="utf-8")

        v1_text = (EXAMPLES_DIR / "v1.cnl").read_text(encoding="utf-8")
        self.assertIn(v1_text, doc_text)

        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        links = link_pattern.findall(doc_text)
        self.assertGreater(len(links), 0)

        for raw_target in links:
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = raw_target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (PYTHON_BE_DOC.parent / path_part).resolve()
            self.assertTrue(
                resolved.exists(),
                f"Relative link {raw_target!r} in {PYTHON_BE_DOC} does not exist ({resolved})",
            )

        for doc_path in (GOAL_DOC, ROADMAP_DOC):
            text = doc_path.read_text(encoding="utf-8")
            self.assertIn("(python-be.md)", text, f"{doc_path} must link python-be.md")
            target = (doc_path.parent / "python-be.md").resolve()
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
