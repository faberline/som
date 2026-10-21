#!/usr/bin/env python3
"""Unit tests for prepare_candidate.py --prettier support (issue #97)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO = SCRIPT_DIR.parents[2]
TREE_SHA_DIR = REPO / "som-rs" / "tests" / "typescript-output-v1"
for p in (SCRIPT_DIR, TREE_SHA_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import prepare_candidate  # noqa: E402
import tree_sha  # noqa: E402


FAKE_SOM_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, pathlib, sys
    log_path = os.environ.get("FAKE_STEP_LOG")
    if log_path:
        with pathlib.Path(log_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "step": "som " + (sys.argv[1] if len(sys.argv) > 1 else ""),
                "argv": sys.argv[1:],
                "som_ts_prettier": os.environ.get("SOM_TS_PRETTIER"),
            }) + "\\n")
    """
)

FAKE_CARGO_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, pathlib, stat, sys
    log_path = os.environ.get("FAKE_STEP_LOG")
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    if log_path:
        with pathlib.Path(log_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "step": "cargo " + sub,
                "argv": sys.argv[1:],
                "som_ts_prettier": os.environ.get("SOM_TS_PRETTIER"),
            }) + "\\n")
    if sub == "build":
        target_dir = pathlib.Path(os.environ["CARGO_TARGET_DIR"]) / "release"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "som.d").write_text("target/release/som: \\n", encoding="utf-8")
        som_bin = target_dir / "som"
        som_bin.write_text(os.environ["FAKE_SOM_TEMPLATE"], encoding="utf-8")
        som_bin.chmod(som_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    """
)


class PrepareCandidatePrettierTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()

        rustc = self.bin_dir / "rustc"
        rustc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        rustc.chmod(rustc.stat().st_mode | stat.S_IXUSR)

        cargo = self.bin_dir / "cargo"
        cargo.write_text(FAKE_CARGO_SCRIPT, encoding="utf-8")
        cargo.chmod(cargo.stat().st_mode | stat.S_IXUSR)

        self.toolchain = {
            "rust": {
                "bin_dir": str(self.bin_dir),
                "rustc": {"sha256": prepare_candidate.sha(rustc)},
                "cargo": {"sha256": prepare_candidate.sha(cargo)},
            }
        }
        self.step_log = self.tmp / "steps.jsonl"

        self.som_rs_dir = self.tmp / "som-rs"
        assets_dir = self.som_rs_dir / "compiler-assets" / "current"
        assets_dir.mkdir(parents=True)
        (assets_dir / "manifest.json").write_text('{"assets":[]}\n', encoding="utf-8")

        self.prettier_dir = self.tmp / "prettier-pkg" / "node_modules" / "prettier"
        (self.prettier_dir / "bin").mkdir(parents=True)
        (self.prettier_dir / "package.json").write_text(
            '{"name":"prettier","version":"3.9.10"}\n', encoding="utf-8"
        )
        (self.prettier_dir / "bin" / "prettier.cjs").write_text(
            "// fake prettier\n", encoding="utf-8"
        )
        os.symlink("bin/prettier.cjs", self.prettier_dir / "index-link.cjs")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _read_steps(self) -> list[dict]:
        if not self.step_log.is_file():
            return []
        return [
            json.loads(line)
            for line in self.step_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _run_candidate(
        self,
        argv: list[str],
        extra_env: dict[str, str | None] | None = None,
    ) -> tuple[int, str, str]:
        env_patch = {
            "FAKE_STEP_LOG": str(self.step_log),
            "FAKE_SOM_TEMPLATE": FAKE_SOM_SCRIPT,
        }
        if extra_env:
            for k, v in extra_env.items():
                if v is not None:
                    env_patch[k] = v

        def fake_wait_health(port: int, process: subprocess.Popen) -> dict:
            process.wait(timeout=5)
            return {"status": "ok", "port": port}

        def fake_timed(
            command: list[str],
            env: dict,
            out: pathlib.Path,
            index: int,
            cwd: pathlib.Path = prepare_candidate.TARGETS,
        ) -> dict:
            with (
                (out / f"build-{index}.stdout").open("wb") as stdout,
                (out / f"build-{index}.stderr").open("wb") as stderr,
            ):
                code = subprocess.run(
                    command, cwd=cwd, env=env, stdout=stdout, stderr=stderr
                ).returncode
            return {
                "command": command,
                "cwd": str(cwd.relative_to(prepare_candidate.REPO)),
                "exit_code": code,
                "seconds": 0.01,
                "max_rss_bytes": 1024,
            }

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            mock.patch.object(prepare_candidate, "BIN_DIR", self.bin_dir),
            mock.patch.object(prepare_candidate, "SOM_RS", self.som_rs_dir),
            mock.patch.object(prepare_candidate, "TOOLCHAIN", self.toolchain),
            mock.patch.object(
                prepare_candidate, "SOURCE_ROOTS", ["som-rs/Cargo.toml"]
            ),
            mock.patch.object(prepare_candidate, "timed", side_effect=fake_timed),
            mock.patch.object(
                prepare_candidate, "wait_health", side_effect=fake_wait_health
            ),
            mock.patch.dict(os.environ, env_patch, clear=False),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            if extra_env and "SOM_TS_PRETTIER" in extra_env and extra_env["SOM_TS_PRETTIER"] is None:
                os.environ.pop("SOM_TS_PRETTIER", None)
            rc = prepare_candidate.main(argv)
        return rc, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_missing_prettier_path_exits_2_before_any_build(self) -> None:
        missing = self.tmp / "does-not-exist"
        out_dir = self.tmp / "candidate-missing"
        with (
            mock.patch.object(prepare_candidate, "tree_state") as mock_tree,
            mock.patch.object(prepare_candidate, "timed") as mock_timed,
            mock.patch.object(subprocess, "Popen") as mock_popen,
        ):
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                rc = prepare_candidate.main(
                    [
                        "--out",
                        str(out_dir),
                        "--port",
                        "18991",
                        "--prettier",
                        str(missing),
                    ]
                )
        self.assertEqual(rc, 2)
        self.assertIn("prettier not found", stderr_buf.getvalue())
        self.assertFalse(out_dir.exists())
        mock_tree.assert_not_called()
        mock_timed.assert_not_called()
        mock_popen.assert_not_called()
        self.assertEqual(self._read_steps(), [])

        cli_proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "prepare_candidate.py"),
                "--out",
                str(out_dir),
                "--port",
                "18991",
                "--prettier",
                str(missing),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(cli_proc.returncode, 2)
        self.assertFalse(out_dir.exists())

    def test_with_prettier_sets_env_across_all_three_steps_and_records_hash(self) -> None:
        out_dir = self.tmp / "candidate-with-prettier"
        relative_prettier = self.prettier_dir / ".." / "prettier"
        expected_resolved = str(self.prettier_dir.resolve())
        expected_sha, _, _ = tree_sha.tree_sha(self.prettier_dir.resolve())
        before_entries = sorted(
            p.relative_to(self.prettier_dir).as_posix()
            for p in self.prettier_dir.rglob("*")
        )

        rc, _, stderr = self._run_candidate(
            [
                "--out",
                str(out_dir),
                "--port",
                "18992",
                "--prettier",
                str(relative_prettier),
            ],
            extra_env={"SOM_TS_PRETTIER": None},
        )
        self.assertEqual(rc, 0, msg=stderr)

        steps = self._read_steps()
        self.assertEqual(
            [s["step"] for s in steps],
            ["cargo test", "cargo build", "som serve"],
        )
        for step in steps:
            self.assertEqual(step["som_ts_prettier"], expected_resolved)

        build = json.loads((out_dir / "build.json").read_text(encoding="utf-8"))
        runtime = json.loads((out_dir / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(build["som_ts_prettier"], expected_resolved)
        self.assertEqual(build["prettier_tree_sha256"], expected_sha)
        self.assertEqual(runtime["som_ts_prettier"], expected_resolved)
        self.assertEqual(runtime["prettier_tree_sha256"], expected_sha)

        after_entries = sorted(
            p.relative_to(self.prettier_dir).as_posix()
            for p in self.prettier_dir.rglob("*")
        )
        after_sha, _, _ = tree_sha.tree_sha(self.prettier_dir.resolve())
        self.assertEqual(after_entries, before_entries)
        self.assertEqual(after_sha, expected_sha)

    def test_without_prettier_unset_env_leaves_steps_and_records_none(self) -> None:
        out_dir = self.tmp / "candidate-no-prettier"
        rc, _, stderr = self._run_candidate(
            ["--out", str(out_dir), "--port", "18993"],
            extra_env={"SOM_TS_PRETTIER": None},
        )
        self.assertEqual(rc, 0, msg=stderr)

        steps = self._read_steps()
        self.assertEqual(
            [s["step"] for s in steps],
            ["cargo test", "cargo build", "som serve"],
        )
        for step in steps:
            self.assertIsNone(step["som_ts_prettier"])

        build = json.loads((out_dir / "build.json").read_text(encoding="utf-8"))
        runtime = json.loads((out_dir / "runtime.json").read_text(encoding="utf-8"))
        self.assertIsNone(build["som_ts_prettier"])
        self.assertIsNone(build["prettier_tree_sha256"])
        self.assertIsNone(runtime["som_ts_prettier"])
        self.assertIsNone(runtime["prettier_tree_sha256"])

    def test_without_prettier_inherits_existing_env_value(self) -> None:
        out_dir = self.tmp / "candidate-inherited-prettier"
        inherited_value = str(self.prettier_dir)
        expected_sha, _, _ = tree_sha.tree_sha(self.prettier_dir.resolve())

        rc, _, stderr = self._run_candidate(
            ["--out", str(out_dir), "--port", "18994"],
            extra_env={"SOM_TS_PRETTIER": inherited_value},
        )
        self.assertEqual(rc, 0, msg=stderr)

        steps = self._read_steps()
        self.assertEqual(
            [s["step"] for s in steps],
            ["cargo test", "cargo build", "som serve"],
        )
        for step in steps:
            self.assertEqual(step["som_ts_prettier"], inherited_value)

        build = json.loads((out_dir / "build.json").read_text(encoding="utf-8"))
        runtime = json.loads((out_dir / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(build["som_ts_prettier"], inherited_value)
        self.assertEqual(build["prettier_tree_sha256"], expected_sha)
        self.assertEqual(runtime["som_ts_prettier"], inherited_value)
        self.assertEqual(runtime["prettier_tree_sha256"], expected_sha)


if __name__ == "__main__":
    unittest.main()
