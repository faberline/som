#!/usr/bin/env python3
"""Unit tests for som-rs/scripts/typescript_quickstart/smoke.py and docs/typescript-be.md."""

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
TS_REFERENCE_DIR = REPO_ROOT / "som-rs" / "contracts" / "typescript-output-v1" / "reference"
TS_ACCEPTANCE_PLAN = REPO_ROOT / "som-rs" / "contracts" / "typescript-output-v1" / "acceptance-plan.json"
TS_BE_DOC = REPO_ROOT / "docs" / "typescript-be.md"
GOAL_DOC = REPO_ROOT / "docs" / "goal.md"
ROADMAP_DOC = REPO_ROOT / "docs" / "roadmap.md"
TEST_URL = "http://127.0.0.1:19876"


def check_doc_asset_hash_mismatch_status(doc_text: str, plan_path: Path = TS_ACCEPTANCE_PLAN) -> list[str]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    recipes = plan.get("assemble_refusals", {}).get("recipes", [])
    statuses = {
        int(r["http_status"])
        for r in recipes
        if isinstance(r, dict) and r.get("code") == "ASSET_HASH_MISMATCH"
    }
    if len(statuses) != 1:
        return [f"expected exactly one ASSET_HASH_MISMATCH http_status in {plan_path}, got {statuses!r}"]
    expected_status = str(next(iter(statuses)))

    problems: list[str] = []
    matching_lines = [line for line in doc_text.splitlines() if "ASSET_HASH_MISMATCH" in line]
    if not matching_lines:
        return ["missing ASSET_HASH_MISMATCH mention in doc"]

    http_status_re = re.compile(r"\bHTTP\s+`?([0-9]{3})`?|\bstatus\s+`?([1-5][0-9]{2})`?", re.IGNORECASE)
    found_expected = False
    for line in matching_lines:
        for match in http_status_re.finditer(line):
            code_str = match.group(1) or match.group(2)
            if code_str != expected_status:
                problems.append(
                    f"ASSET_HASH_MISMATCH paired with HTTP status {code_str} instead of {expected_status}: {line!r}"
                )
            else:
                found_expected = True
    if not found_expected:
        problems.append(
            f"ASSET_HASH_MISMATCH must be paired with HTTP {expected_status} from acceptance-plan.json assemble_refusals"
        )
    if "acceptance-plan.json" not in doc_text or "assemble_refusals" not in doc_text:
        problems.append("doc must cite acceptance-plan.json assemble_refusals for ASSET_HASH_MISMATCH")
    return problems


def make_fake_som(
    directory: Path,
    *,
    omit_on_case: str | None = None,
    omit_item: str | None = None,
    differ_file_on_case: str | None = None,
    differ_file_name: str = "package-lock.json",
    refuse_on_case: str | None = None,
    refuse_code: str = "UNSUPPORTED_TARGET",
    succeed_on_out_of_scope_case: str | None = None,
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
            or argv[3] != "typescript"
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

        out_of_scope_prefixes = ("v6-", "v7-")
        is_out_of_scope = out.name.startswith(out_of_scope_prefixes)
        refuse_case = {refuse_on_case!r}
        refuse_code = {refuse_code!r}
        succeed_oos = {succeed_on_out_of_scope_case!r}

        should_refuse = (
            (is_out_of_scope and out.name != succeed_oos)
            or (refuse_case is not None and out.name == refuse_case)
        )
        if should_refuse:
            code_val = refuse_code if (refuse_case is not None and out.name == refuse_case) else "UNSUPPORTED_TARGET"
            diagnostic = {{
                "status": "error",
                "code": code_val,
                "detail": "TypeScript output phase 1 does not cover this prompt.",
                "diagnostics": [
                    {{
                        "code": code_val,
                        "prompt_pointer": "/version",
                        "cause": "TypeScript output phase 1 does not cover this prompt.",
                        "allowed_form": "Use prompt version 1 through 5 for target typescript.",
                    }}
                ],
            }}
            sys.stderr.write(json.dumps(diagnostic) + "\\n")
            sys.exit(2)

        ref_dir = Path({str(TS_REFERENCE_DIR)!r})
        pkg_bytes = (ref_dir / "package.json").read_bytes()
        lock_bytes = (ref_dir / "package-lock.json").read_bytes()
        tsconfig_bytes = (ref_dir / "tsconfig.json").read_bytes()

        differ_case = {differ_file_on_case!r}
        differ_name = {differ_file_name!r}
        if differ_case is not None and out.name == differ_case:
            if differ_name == "package.json":
                pkg_bytes = pkg_bytes + b"\\n"
            elif differ_name == "package-lock.json":
                lock_bytes = lock_bytes + b"\\n"
            elif differ_name == "tsconfig.json":
                tsconfig_bytes = tsconfig_bytes + b"\\n"

        omit_case = {omit_on_case!r}
        omit = {omit_item!r} if (omit_case is None or out.name == omit_case) else None

        files_bytes = {{
            "package.json": pkg_bytes,
            "package-lock.json": lock_bytes,
            "tsconfig.json": tsconfig_bytes,
            "src/main.ts": b"// main.ts\\n",
            "src/app.ts": b"// app.ts\\n",
            "src/schemas.ts": b"// schemas.ts\\n",
            "src/db.ts": b"// db.ts\\n",
            "src/migrate.ts": b"// migrate.ts\\n",
            "migrations/0001_rev0000.sql": b"-- revision: rev0000, down_revision: none\\n",
            "test/api.test.ts": b"// api.test.ts\\n",
            "README.md": b"# items\\n",
        }}
        out.mkdir(parents=True, exist_ok=True)
        written_count = 0
        for rel_path, content in files_bytes.items():
            if rel_path == omit or (omit == "migrations/0001_*.sql" and rel_path.startswith("migrations/")):
                continue
            target = out / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            written_count += 1

        print(json.dumps({{"status": "ok", "out": str(out), "files": written_count, "target": "typescript"}}))
        """
    )
    script.write_text(code, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


class TypeScriptSmokeScriptTest(unittest.TestCase):
    def expected_counts(self) -> tuple[int, int, int]:
        cnls = sorted(EXAMPLES_DIR.glob("*.cnl"))
        prompts = sorted(EXAMPLES_DIR.glob("*.prompt.json"))
        self.assertGreater(len(cnls), 0)
        self.assertEqual(len(cnls), len(prompts))
        sources = cnls + prompts
        in_scope = sum(1 for s in sources if smoke.detect_prompt_version(s) in {1, 2, 3, 4, 5})
        out_of_scope = sum(1 for s in sources if smoke.detect_prompt_version(s) in {6, 7})
        self.assertEqual(in_scope, 10)
        self.assertEqual(out_of_scope, 6)
        self.assertEqual(len(sources), 16)
        return len(sources), in_scope, out_of_scope

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SMOKE_PY), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_pass_path_and_summary_counts(self) -> None:
        expected_total, expected_written, expected_refused = self.expected_counts()
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
            self.assertEqual(summary["written"], expected_written)
            self.assertEqual(summary["refused"], expected_refused)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(len(summary["examples"]), expected_total)
            self.assertEqual(len(summary["refusals"]), expected_refused)
            self.assertEqual(summary["failures"], [])

            by_name = {row["name"]: row for row in summary["examples"]}
            for ver in range(1, 6):
                for suffix in ("cnl", "prompt"):
                    case_name = f"v{ver}-{suffix}"
                    self.assertEqual(by_name[case_name]["status"], "written")
                    self.assertEqual(by_name[case_name]["returncode"], 0)
            for stem in ("v6", "v7", "v7-branch-null"):
                for suffix in ("cnl", "prompt"):
                    case_name = f"{stem}-{suffix}"
                    self.assertEqual(by_name[case_name]["status"], "refused")
                    self.assertEqual(by_name[case_name]["returncode"], 2)
                    self.assertEqual(by_name[case_name]["code"], "UNSUPPORTED_TARGET")

    def test_missing_product_file_is_reported_and_fails(self) -> None:
        expected_total, expected_written, expected_refused = self.expected_counts()
        all_items = (
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "src/main.ts",
            "src/app.ts",
            "src/schemas.ts",
            "src/db.ts",
            "src/migrate.ts",
            "migrations/0001_*.sql",
            "test/api.test.ts",
            "README.md",
        )
        with tempfile.TemporaryDirectory() as shared_tmp:
            shared_path = Path(shared_tmp)
            small_examples = shared_path / "examples"
            small_examples.mkdir()
            for fname in ("v1.cnl", "v6.cnl"):
                (small_examples / fname).write_bytes((EXAMPLES_DIR / fname).read_bytes())

            for missing_item in all_items:
                with self.subTest(missing_item=missing_item), tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    fake_som = make_fake_som(
                        tmp_path,
                        omit_on_case="v1-cnl",
                        omit_item=missing_item,
                    )
                    out_dir = tmp_path / "out"

                    if missing_item in ("package.json", "migrations/0001_*.sql"):
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
                        self.assertEqual(summary["written"], expected_written - 1)
                        self.assertEqual(summary["refused"], expected_refused)
                        self.assertEqual(summary["failed"], 1)
                    else:
                        code, summary = smoke.run_smoke(
                            fake_som,
                            TEST_URL,
                            out_dir,
                            examples_dir=small_examples,
                        )
                        self.assertEqual(code, 1)
                        self.assertEqual(summary["status"], "error")
                        self.assertEqual(summary["total"], 2)
                        self.assertEqual(summary["written"], 0)
                        self.assertEqual(summary["refused"], 1)
                        self.assertEqual(summary["failed"], 1)

                    self.assertEqual(len(summary["failures"]), 1)
                    failure = summary["failures"][0]
                    self.assertEqual(failure["name"], "v1-cnl")
                    self.assertEqual(failure["status"], "failed")
                    self.assertIn(missing_item, failure["missing"])

    def test_package_lock_differing_from_reference_fails(self) -> None:
        expected_total, expected_written, expected_refused = self.expected_counts()
        for ref_file in ("package-lock.json", "package.json", "tsconfig.json"):
            with self.subTest(ref_file=ref_file), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                fake_som = make_fake_som(
                    tmp_path,
                    differ_file_on_case="v2-cnl",
                    differ_file_name=ref_file,
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
                self.assertEqual(summary["written"], expected_written - 1)
                self.assertEqual(summary["refused"], expected_refused)
                self.assertEqual(summary["failed"], 1)
                self.assertEqual(len(summary["failures"]), 1)
                failure = summary["failures"][0]
                self.assertEqual(failure["name"], "v2-cnl")
                self.assertEqual(failure["status"], "failed")
                self.assertIn(ref_file, failure["differing_files"])

    def test_v6_refused_counted_as_refused_and_v1_refused_counted_as_failed(self) -> None:
        expected_total, expected_written, expected_refused = self.expected_counts()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # When v1-cnl is also refused with exit 2 UNSUPPORTED_TARGET while v6-cnl is refused:
            # v6-cnl is counted in `refused`, and v1-cnl is counted in `failed`.
            fake_som = make_fake_som(
                tmp_path,
                refuse_on_case="v1-cnl",
                refuse_code="UNSUPPORTED_TARGET",
            )
            out_dir = tmp_path / "out-v1-refused"

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
            self.assertEqual(summary["written"], expected_written - 1)
            self.assertEqual(summary["refused"], expected_refused)
            self.assertEqual(summary["failed"], 1)

            by_name = {row["name"]: row for row in summary["examples"]}
            self.assertEqual(by_name["v6-cnl"]["status"], "refused")
            self.assertEqual(by_name["v6-cnl"]["returncode"], 2)
            self.assertEqual(by_name["v1-cnl"]["status"], "failed")
            self.assertEqual(by_name["v1-cnl"]["returncode"], 2)
            self.assertEqual(len(summary["failures"]), 1)
            self.assertEqual(summary["failures"][0]["name"], "v1-cnl")

            # Also verify that if a v6 example fails with a non-UNSUPPORTED_TARGET code or exits 0,
            # it is counted as `failed`, not `refused`.
            fake_som_bad_v6_code = make_fake_som(
                tmp_path,
                refuse_on_case="v6-cnl",
                refuse_code="INVALID_SCHEMA",
            )
            out_dir_bad_v6 = tmp_path / "out-v6-bad-code"
            proc_bad_v6 = self.run_cli(
                "--som",
                str(fake_som_bad_v6_code),
                "--url",
                TEST_URL,
                "--out",
                str(out_dir_bad_v6),
            )
            self.assertEqual(proc_bad_v6.returncode, 1)
            summary_bad_v6 = json.loads(proc_bad_v6.stdout.strip())
            self.assertEqual(summary_bad_v6["written"], expected_written)
            self.assertEqual(summary_bad_v6["refused"], expected_refused - 1)
            self.assertEqual(summary_bad_v6["failed"], 1)
            self.assertEqual(summary_bad_v6["failures"][0]["name"], "v6-cnl")

            fake_som_v6_exit0 = make_fake_som(
                tmp_path,
                succeed_on_out_of_scope_case="v6-cnl",
            )
            out_dir_v6_exit0 = tmp_path / "out-v6-exit0"
            proc_v6_exit0 = self.run_cli(
                "--som",
                str(fake_som_v6_exit0),
                "--url",
                TEST_URL,
                "--out",
                str(out_dir_v6_exit0),
            )
            self.assertEqual(proc_v6_exit0.returncode, 1)
            summary_v6_exit0 = json.loads(proc_v6_exit0.stdout.strip())
            self.assertEqual(summary_v6_exit0["written"], expected_written)
            self.assertEqual(summary_v6_exit0["refused"], expected_refused - 1)
            self.assertEqual(summary_v6_exit0["failed"], 1)
            self.assertEqual(summary_v6_exit0["failures"][0]["name"], "v6-cnl")

    def test_existing_non_empty_out_and_forbidden_ports_are_refused(self) -> None:
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
            self.assertEqual(proc.returncode, 2)
            summary = json.loads(proc.stdout.strip())
            self.assertIn("Refused --out", summary["error"])
            self.assertEqual(
                (existing_dir / "keep.txt").read_text(encoding="utf-8"),
                "do not overwrite\n",
            )

            for bad_port in (8765, 18765):
                unused_out = tmp_path / f"unused-{bad_port}"
                proc_port = self.run_cli(
                    "--som",
                    str(fake_som),
                    "--url",
                    f"http://127.0.0.1:{bad_port}",
                    "--out",
                    str(unused_out),
                )
                self.assertEqual(proc_port.returncode, 2)
                port_summary = json.loads(proc_port.stdout.strip())
                self.assertIn("8765 and 18765 are forbidden", port_summary["error"])
                self.assertFalse(unused_out.exists())

    def test_typescript_be_doc_relative_links_resolve_and_sections_exist(self) -> None:
        self.assertTrue(TS_BE_DOC.is_file(), f"missing {TS_BE_DOC}")
        doc_text = TS_BE_DOC.read_text(encoding="utf-8")

        headings = [
            line.strip()
            for line in doc_text.splitlines()
            if line.startswith("## ")
        ]
        self.assertEqual(
            headings,
            [
                "## 1. What you get",
                "## 2. Check the toolchain and the npm cache",
                "## 3. Generate the project",
                "## 4. Install, check, test and run",
                "## 5. Change it and generate again",
            ],
        )

        # Verify required facts from the contract and commands from toolchain.json
        for required_snippet in (
            "som-app",
            "process.features.typescript",
            "prompt_version",
            "v5_capabilities",
            "path_empty_segment",
            "path_reserved",
            "table_reserved",
            "identifier_reserved",
            ".local/typescript-output/npm-cache-001",
            "11ae1ed59d4e17888d3b6e3b864cdfdbc850b11752cae2735d13ccb5f71eb1e9",
            "npm_config_cache=",
            "npm_config_offline=true",
            "`som write --target typescript` needs the v0.5.0 default; until then, point `--url` at a candidate on a QA port.",
            "npm ci --offline --ignore-scripts --no-audit --no-fund",
            "node node_modules/typescript/bin/tsc -p tsconfig.json",
            "node node_modules/prettier/bin/prettier.cjs --check --no-config --no-editorconfig src/main.ts src/app.ts src/schemas.ts src/db.ts src/migrate.ts test/api.test.ts",
            "node --test --test-concurrency=1 test/api.test.ts",
            "node src/main.ts",
            "DATABASE_URL",
            "SOM_APP_ADDR",
            "127.0.0.1:8000",
            "src/migrate.ts",
            "ASSET_HASH_MISMATCH",
        ):
            self.assertIn(required_snippet, doc_text)

        link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        links = link_pattern.findall(doc_text)
        self.assertGreater(len(links), 0)

        for raw_target in links:
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = raw_target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (TS_BE_DOC.parent / path_part).resolve()
            self.assertTrue(
                resolved.exists(),
                f"Relative link {raw_target!r} in {TS_BE_DOC} does not exist ({resolved})",
            )

        for doc_path in (GOAL_DOC, ROADMAP_DOC):
            text = doc_path.read_text(encoding="utf-8")
            self.assertIn("(typescript-be.md)", text, f"{doc_path} must link typescript-be.md")
            target = (doc_path.parent / "typescript-be.md").resolve()
            self.assertTrue(target.is_file())

        self.assertEqual(check_doc_asset_hash_mismatch_status(doc_text), [])
        wrong_status_doc = doc_text.replace("HTTP `400`", "HTTP `422`")
        self.assertNotEqual(check_doc_asset_hash_mismatch_status(wrong_status_doc), [])
        missing_cite_doc = doc_text.replace("assemble_refusals", "other_section")
        self.assertNotEqual(check_doc_asset_hash_mismatch_status(missing_cite_doc), [])


if __name__ == "__main__":
    unittest.main()
