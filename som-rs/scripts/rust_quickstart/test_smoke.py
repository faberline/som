#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/rust_quickstart/smoke.py and docs/rust-be.md."""

from __future__ import annotations

import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import smoke  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parents[2]
SMOKE_PY = SCRIPT_DIR / "smoke.py"
EXAMPLES_DIR = REPO_ROOT / "som-rs" / "contracts" / "compiler-v1" / "examples"
RUST_BE_DOC = REPO_ROOT / "docs" / "rust-be.md"
GOAL_DOC = REPO_ROOT / "docs" / "goal.md"
ROADMAP_DOC = REPO_ROOT / "docs" / "roadmap.md"
TEST_URL = "http://127.0.0.1:19876"


def make_fake_som(
    directory: Path,
    *,
    omit_on_case: str | None = None,
    omit_item: str | None = None,
    differ_lock_on_case: str | None = None,
    differ_toml_on_case: str | None = None,
    refuse_on_case: str | None = None,
    refuse_mode: str = "target_rule",
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
        if (
            len(argv) != 8
            or argv[0] != "write"
            or argv[2] != "--target"
            or argv[3] != "rust"
            or argv[4] != "--out"
            or argv[6] != "--url"
        ):
            sys.stderr.write(f"unexpected args: {{argv!r}}\\n")
            sys.exit(2)

        source = Path(argv[1])
        out = Path(argv[5])
        url = argv[7]
        if not source.is_file():
            sys.stderr.write(f"missing source: {{source}}\\n")
            sys.exit(1)
        if url != {TEST_URL!r}:
            sys.stderr.write(f"unexpected url: {{url}}\\n")
            sys.exit(1)

        refuse_case = {refuse_on_case!r}
        refuse_mode = {refuse_mode!r}
        if refuse_case is not None and out.name == refuse_case:
            if refuse_mode == "target_rule":
                diagnostic = {{
                    "status": "error",
                    "code": "UNSUPPORTED_TARGET",
                    "detail": "prompt.query.filter_field: query parameter collision",
                    "diagnostics": [
                        {{
                            "code": "UNSUPPORTED_TARGET",
                            "rule": "query_parameter_collision",
                            "prompt_pointer": "/query/filter_field",
                            "cause": "prompt.query.filter_field: query parameter collision",
                        }}
                    ],
                }}
            elif refuse_mode == "compiler_v1_span":
                raw = source.read_bytes()
                idx = raw.find(b"Filter exactly on field ")
                if idx != -1:
                    start = idx + len(b"Filter exactly on field ")
                    end = raw.find(b".", start)
                else:
                    start, end = 0, 0
                diagnostic = {{
                    "status": "error",
                    "code": "INVALID_VALUE",
                    "detail": "prompt.query.filter_field: name collides with a generated Python identifier",
                    "diagnostics": [
                        {{
                            "code": "INVALID_VALUE",
                            "prompt_pointer": "/query/filter_field",
                            "byte_start": start,
                            "byte_end": end,
                            "cause": "prompt.query.filter_field: name collides with a generated Python identifier",
                        }}
                    ],
                }}
            else:
                diagnostic = {{"status": "error", "code": "INVALID_VALUE"}}
            sys.stderr.write(json.dumps(diagnostic) + "\\n")
            sys.exit(2)

        fail_case = {fail_on_case!r}
        if fail_case is not None and out.name == fail_case:
            sys.stderr.write(json.dumps({{"status": "error", "code": "INVALID_SCHEMA"}}) + "\\n")
            sys.exit(2)

        out.mkdir(parents=True, exist_ok=True)
        (out / "src").mkdir(parents=True, exist_ok=True)

        omit_case = {omit_on_case!r}
        omit = {omit_item!r} if (omit_case is None or out.name == omit_case) else None

        toml_text = '[package]\\nname = "som_app"\\nversion = "0.1.0"\\nedition = "2024"\\n'
        if {differ_toml_on_case!r} is not None and out.name == {differ_toml_on_case!r}:
            toml_text += '# modified\\n'

        lock_text = 'version = 4\\n\\n[[package]]\\nname = "som_app"\\nversion = "0.1.0"\\n'
        if {differ_lock_on_case!r} is not None and out.name == {differ_lock_on_case!r}:
            lock_text += '# modified lock\\n'

        files = {{
            "Cargo.toml": toml_text,
            "Cargo.lock": lock_text,
            "README.md": "# som_app\\n",
            "src/main.rs": "fn main() {{}}\\n",
            "src/lib.rs": "pub fn router() {{}}\\n",
        }}
        for rel_path, content in files.items():
            if rel_path == omit:
                continue
            target = out / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        print(json.dumps({{"status": "ok", "out": str(out), "files": len(files), "target": "rust"}}))
        """
    )
    script.write_text(code, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


class RustSmokeScriptTest(unittest.TestCase):
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
            self.assertEqual(summary["refused"], 0)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(summary["refusals"], [])
            self.assertEqual(summary["failures"], [])

    def test_missing_required_file_is_reported_and_fails(self) -> None:
        expected_total = self.expected_example_count()
        for missing_item in (
            "Cargo.toml",
            "Cargo.lock",
            "README.md",
            "src/main.rs",
            "src/lib.rs",
        ):
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
                self.assertEqual(summary["refused"], 0)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(len(summary["failures"]), 1)
                failure = summary["failures"][0]
                self.assertEqual(failure["name"], "v1-cnl")
                self.assertIn(missing_item, failure["missing"])

    def test_differing_cargo_lock_bytes_fails(self) -> None:
        expected_total = self.expected_example_count()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_som = make_fake_som(
                tmp_path,
                differ_lock_on_case="v3-cnl",
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
            self.assertEqual(summary["refused"], 0)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(len(summary["failures"]), 1)
            failure = summary["failures"][0]
            self.assertEqual(failure["name"], "v3-cnl")
            self.assertIn("Cargo.lock", failure["differing_files"])

    def test_refusal_classifier_strictness_and_smoke_handling(self) -> None:
        # 1. The three non-collision filter_field errors must all return False (failed):
        self.assertFalse(
            smoke.is_query_parameter_collision_refusal(
                json.dumps(
                    {
                        "code": "INVALID_VALUE",
                        "detail": 'prompt.query.filter_field: unknown field "done"',
                    }
                )
            )
        )
        self.assertFalse(
            smoke.is_query_parameter_collision_refusal(
                "error at prompt.query.filter_field: must name a boolean field"
            )
        )
        self.assertFalse(
            smoke.is_query_parameter_collision_refusal(
                json.dumps(
                    {
                        "code": "INVALID_VALUE",
                        "diagnostics": [
                            {
                                "prompt_pointer": "/query/filter_field",
                                "cause": "prompt.query.filter_field: name collides with a generated Python identifier",
                            }
                        ],
                    }
                )
            )
        )

        # 2. Case (a): target 422 diagnostic with rule == "query_parameter_collision" -> True
        case_a_stderr = json.dumps(
            {
                "status": "error",
                "code": "UNSUPPORTED_TARGET",
                "diagnostics": [
                    {
                        "code": "UNSUPPORTED_TARGET",
                        "rule": "query_parameter_collision",
                        "prompt_pointer": "/query/filter_field",
                        "cause": "prompt.query.filter_field: query parameter collision",
                    }
                ],
            }
        )
        self.assertTrue(smoke.is_query_parameter_collision_refusal(case_a_stderr))

        # 3. Case (b): compiler-v1 400 INVALID_VALUE at /query/filter_field where source span is limit/offset -> True
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            v3_limit_cnl = tmp_path / "v3_limit.cnl"
            v3_text = (EXAMPLES_DIR / "v3.cnl").read_text(encoding="utf-8").replace(
                "Filter exactly on field completed.",
                "Filter exactly on field limit.",
            )
            v3_limit_cnl.write_text(v3_text, encoding="utf-8")
            raw = v3_limit_cnl.read_bytes()
            start = raw.index(b"Filter exactly on field ") + len(b"Filter exactly on field ")
            end = start + len(b"limit")
            case_b_stderr = json.dumps(
                {
                    "status": "error",
                    "code": "INVALID_VALUE",
                    "detail": "prompt.query.filter_field: name collides with a generated Python identifier",
                    "diagnostics": [
                        {
                            "code": "INVALID_VALUE",
                            "prompt_pointer": "/query/filter_field",
                            "byte_start": start,
                            "byte_end": end,
                            "cause": "prompt.query.filter_field: name collides with a generated Python identifier",
                        }
                    ],
                }
            )
            self.assertTrue(
                smoke.is_query_parameter_collision_refusal(case_b_stderr, v3_limit_cnl)
            )

            # And on the unmodified v3.cnl (where the field at byte_start:byte_end is `completed`),
            # the same compiler-v1 diagnostic is NOT a limit/offset refusal -> False
            v3_orig = EXAMPLES_DIR / "v3.cnl"
            orig_raw = v3_orig.read_bytes()
            orig_start = orig_raw.index(b"Filter exactly on field ") + len(b"Filter exactly on field ")
            orig_end = orig_start + len(b"completed")
            non_limit_stderr = json.dumps(
                {
                    "status": "error",
                    "code": "INVALID_VALUE",
                    "detail": "prompt.query.filter_field: name collides with a generated Python identifier",
                    "diagnostics": [
                        {
                            "code": "INVALID_VALUE",
                            "prompt_pointer": "/query/filter_field",
                            "byte_start": orig_start,
                            "byte_end": orig_end,
                            "cause": "prompt.query.filter_field: name collides with a generated Python identifier",
                        }
                    ],
                }
            )
            self.assertFalse(
                smoke.is_query_parameter_collision_refusal(non_limit_stderr, v3_orig)
            )

    def test_refused_example_is_counted_as_refused_not_failed(self) -> None:
        expected_total = self.expected_example_count()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # Case (a) via CLI on default examples
            fake_som_a = make_fake_som(
                tmp_path,
                refuse_on_case="v3-cnl",
                refuse_mode="target_rule",
            )
            out_dir_a = tmp_path / "out-a"

            proc_a = self.run_cli(
                "--som",
                str(fake_som_a),
                "--url",
                TEST_URL,
                "--out",
                str(out_dir_a),
            )
            self.assertEqual(proc_a.returncode, 0, proc_a.stderr)
            summary_a = json.loads(proc_a.stdout.strip())
            self.assertEqual(summary_a["status"], "ok")
            self.assertEqual(summary_a["total"], expected_total)
            self.assertEqual(summary_a["passed"], expected_total - 1)
            self.assertEqual(summary_a["refused"], 1)
            self.assertEqual(summary_a["failed"], 0)
            self.assertEqual(summary_a["failures"], [])
            self.assertEqual(len(summary_a["refusals"]), 1)
            self.assertEqual(summary_a["refusals"][0]["name"], "v3-cnl")
            self.assertEqual(summary_a["refusals"][0]["rule"], "query_parameter_collision")

            # Case (b) where the filter field is `completed` (not `limit`/`offset`) -> failed: 1, exit 1
            fake_som_b_non_limit = make_fake_som(
                tmp_path,
                refuse_on_case="v3-cnl",
                refuse_mode="compiler_v1_span",
            )
            out_dir_b_fail = tmp_path / "out-b-fail"
            proc_b_fail = self.run_cli(
                "--som",
                str(fake_som_b_non_limit),
                "--url",
                TEST_URL,
                "--out",
                str(out_dir_b_fail),
            )
            self.assertEqual(proc_b_fail.returncode, 1)
            summary_b_fail = json.loads(proc_b_fail.stdout.strip())
            self.assertEqual(summary_b_fail["status"], "error")
            self.assertEqual(summary_b_fail["passed"], expected_total - 1)
            self.assertEqual(summary_b_fail["refused"], 0)
            self.assertEqual(summary_b_fail["failed"], 1)
            self.assertEqual(summary_b_fail["refusals"], [])
            self.assertEqual(len(summary_b_fail["failures"]), 1)
            self.assertEqual(summary_b_fail["failures"][0]["name"], "v3-cnl")

            # Case (b) where the filter field in the source file IS `limit` -> refused: 1, failed: 0, exit 0
            custom_examples = tmp_path / "examples"
            custom_examples.mkdir()
            (custom_examples / "v1.cnl").write_text(
                (EXAMPLES_DIR / "v1.cnl").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (custom_examples / "v3.cnl").write_text(
                (EXAMPLES_DIR / "v3.cnl")
                .read_text(encoding="utf-8")
                .replace("Filter exactly on field completed.", "Filter exactly on field limit."),
                encoding="utf-8",
            )
            out_dir_b_ok = tmp_path / "out-b-ok"
            code_b_ok, summary_b_ok = smoke.run_smoke(
                fake_som_b_non_limit,
                TEST_URL,
                out_dir_b_ok,
                examples_dir=custom_examples,
            )
            self.assertEqual(code_b_ok, 0)
            self.assertEqual(summary_b_ok["status"], "ok")
            self.assertEqual(summary_b_ok["total"], 2)
            self.assertEqual(summary_b_ok["passed"], 1)
            self.assertEqual(summary_b_ok["refused"], 1)
            self.assertEqual(summary_b_ok["failed"], 0)

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

    def test_rust_be_doc_relative_links_resolve_and_sections_exist(self) -> None:
        self.assertTrue(RUST_BE_DOC.is_file(), f"missing {RUST_BE_DOC}")
        doc_text = RUST_BE_DOC.read_text(encoding="utf-8")

        headings = [
            line.strip()
            for line in doc_text.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            headings,
            [
                "## 1. What you get",
                "## 2. Check the toolchain and the crates",
                "## 3. Generate the project",
                "## 4. Build, test and run",
                "## 5. Change it and generate again",
            ],
        )
        self.assertIn("`som write` requires the v0.2.0 default or later.", doc_text)
        self.assertIn("query_parameter_collision", doc_text)
        self.assertIn("som_app", doc_text)

        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        links = link_pattern.findall(doc_text)
        self.assertGreater(len(links), 0)

        for raw_target in links:
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = raw_target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (RUST_BE_DOC.parent / path_part).resolve()
            self.assertTrue(
                resolved.exists(),
                f"Relative link {raw_target!r} in {RUST_BE_DOC} does not exist ({resolved})",
            )

        for doc_path in (GOAL_DOC, ROADMAP_DOC):
            text = doc_path.read_text(encoding="utf-8")
            self.assertIn("(rust-be.md)", text, f"{doc_path} must link rust-be.md")
            target = (doc_path.parent / "rust-be.md").resolve()
            self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
