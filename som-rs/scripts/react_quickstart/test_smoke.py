#!/usr/bin/env python3
"""Test the React quickstart with a fake CLI and the public examples.

The fake CLI writes files or returns a refusal. It has no network code.
Protected-port URLs are passed only to the pure URL check function.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from urllib.parse import unquote, urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SMOKE_PY = SCRIPT_DIR / "smoke.py"
EXAMPLES_DIR = REPO_ROOT / "som-rs" / "contracts" / "compiler-v1" / "examples"
CONTRACT_DIR = REPO_ROOT / "som-rs" / "contracts" / "react-output-v1"
REFERENCE_DIR = CONTRACT_DIR / "reference"
REACT_FE_DOC = REPO_ROOT / "docs" / "react-fe.md"
TEST_URL = "http://127.0.0.1:19876"

spec = importlib.util.spec_from_file_location("react_quickstart_smoke", SMOKE_PY)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot load {SMOKE_PY}")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def public_sources() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.cnl")) + sorted(EXAMPLES_DIR.glob("*.prompt.json"))


def case_name(source: Path) -> str:
    if source.name.endswith(".prompt.json"):
        return source.name.removesuffix(".prompt.json") + "-prompt"
    return source.stem + "-cnl"


def prompt_version(source: Path) -> int:
    if source.name.endswith(".prompt.json"):
        version = json.loads(source.read_bytes())["version"]
        match = re.fullmatch(r"som\.prompt\.v([0-9]+)", version)
    else:
        match = re.search(
            r"^Use prompt version ([0-9]+)\.$",
            source.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    if match is None:
        raise AssertionError(f"missing public prompt version: {source}")
    return int(match.group(1))


def layout_files() -> tuple[list[str], list[str]]:
    layout = json.loads((CONTRACT_DIR / "product-layout.json").read_bytes())
    files = [row["path"] for row in layout["files"]]
    fixed = [row["path"] for row in layout["files"] if row["kind"] == "fixed"]
    return files, fixed


def make_fake_som(directory: Path, **changes: object) -> Path:
    """Write a fake that accepts only the real public source paths."""
    config: dict[str, object] = {
        "examples": str(EXAMPLES_DIR),
        "reference": str(REFERENCE_DIR),
        "layout": str(CONTRACT_DIR / "product-layout.json"),
        "url": TEST_URL,
        "omit": {},
        "differ": {},
        "refuse": {},
        "succeed_out_of_scope": [],
        "extra": {},
    }
    config.update(changes)
    script = directory / "fake_som.py"
    script.with_suffix(".json").write_text(json.dumps(config), encoding="utf-8")
    script.write_text(
        textwrap.dedent(
            r'''
            #!/usr/bin/env python3
            import json
            from pathlib import Path
            import re
            import sys

            config_path = Path(__file__).with_suffix(".json")
            config = json.loads(config_path.read_bytes())
            argv = sys.argv[1:]
            if (
                len(argv) != 8
                or argv[0] != "write"
                or argv[2:4] != ["--target", "react"]
                or argv[4] != "--out"
                or argv[6] != "--url"
            ):
                sys.stderr.write(f"unexpected args: {argv!r}\n")
                sys.exit(2)

            source = Path(argv[1])
            out = Path(argv[5])
            if source.parent.resolve() != Path(config["examples"]).resolve() or not source.is_file():
                sys.stderr.write(f"not a public source: {source}\n")
                sys.exit(1)
            if argv[7] != config["url"]:
                sys.stderr.write(f"unexpected URL: {argv[7]}\n")
                sys.exit(1)

            if source.name.endswith(".prompt.json"):
                name = source.name.removesuffix(".prompt.json") + "-prompt"
                version = int(json.loads(source.read_bytes())["version"].rsplit("v", 1)[1])
            else:
                name = source.stem + "-cnl"
                version = int(re.search(r"^Use prompt version ([0-9]+)\.$", source.read_text(), re.M)[1])
            if out.name != name:
                sys.stderr.write(f"unexpected output name: {out}\n")
                sys.exit(1)

            with config_path.with_suffix(".calls.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(argv) + "\n")

            override = config["refuse"].get(name)
            if override is not None or (version in {5, 6, 7} and name not in config["succeed_out_of_scope"]):
                code = override or "UNSUPPORTED_TARGET"
                diagnostic = {
                    "status": "error",
                    "code": code,
                    "detail": "React output phase 1 does not cover this prompt.",
                    "diagnostics": [{
                        "code": code,
                        "prompt_pointer": "/version",
                        "cause": "React output phase 1 does not cover this prompt.",
                        "allowed_form": "Use prompt version 1 through 4 for target react.",
                    }],
                }
                sys.stderr.write(json.dumps(diagnostic) + "\n")
                sys.exit(2)

            reference = Path(config["reference"])
            layout = json.loads(Path(config["layout"]).read_bytes())
            out.mkdir(parents=True, exist_ok=True)
            written = 0
            for item in layout["files"]:
                rel = item["path"]
                if rel in config["omit"].get(name, []):
                    continue
                if item["kind"] == "fixed":
                    content = (reference / rel).read_bytes()
                elif rel == "src/schema.ts":
                    content = b"// fake schema for the layout test\n"
                else:
                    content = b"# fake React product\n"
                mutation = config["differ"].get(name, {}).get(rel)
                if mutation == "append":
                    content += b"\n"
                elif mutation == "crlf":
                    content = content.replace(b"\n", b"\r\n")
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                written += 1
            for rel in config["extra"].get(name, []):
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"unexpected file\n")
                written += 1
            print(json.dumps({"status": "ok", "out": str(out), "files": written, "target": "react"}))
            '''
        ).lstrip("\n"),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def fake_calls(fake_som: Path) -> list[list[str]]:
    log = fake_som.with_suffix(".calls.jsonl")
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def file_bytes(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


class ReactSmokeScriptTest(unittest.TestCase):
    def expected_cases(self) -> dict[str, tuple[Path, int]]:
        sources = public_sources()
        self.assertEqual(len(sources), 16)
        cases = {case_name(source): (source, prompt_version(source)) for source in sources}
        self.assertEqual(len(cases), 16)
        self.assertEqual(sum(version in {1, 2, 3, 4} for _, version in cases.values()), 8)
        self.assertEqual(sum(version in {5, 6, 7} for _, version in cases.values()), 8)
        self.assertEqual(
            set(cases),
            {f"v{version}-{form}" for version in range(1, 8) for form in ("cnl", "prompt")}
            | {"v7-branch-null-cnl", "v7-branch-null-prompt"},
        )
        return cases

    def run_cli(self, fake_som: Path, out: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        proc = subprocess.run(
            [sys.executable, str(SMOKE_PY), "--som", str(fake_som), "--url", TEST_URL, "--out", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"stdout={proc.stdout!r}; stderr={proc.stderr!r}")
        return proc, json.loads(lines[0])

    def assert_counts(self, summary: dict, *, written: int, refused: int, failed: int) -> None:
        self.assertEqual(summary["total"], 16)
        self.assertEqual(summary["written"], written)
        self.assertEqual(summary["passed"], written)
        self.assertEqual(summary["refused"], refused)
        self.assertEqual(summary["failed"], failed)
        self.assertEqual(written + refused + failed, 16)
        self.assertEqual(len(summary["examples"]), 16)
        self.assertEqual(summary["rows"], summary["examples"])
        self.assertEqual(len(summary["refusals"]), refused)
        self.assertEqual(len(summary["failures"]), failed)
        self.assertEqual(summary["status"], "error" if failed else "ok")

    def test_pass_path_and_summary_counts(self) -> None:
        cases = self.expected_cases()
        required, fixed = layout_files()
        self.assertEqual(len(required), 19)
        self.assertEqual(len(fixed), 17)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_som(root)
            out = root / "fresh-out"
            proc, summary = self.run_cli(fake, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assert_counts(summary, written=8, refused=8, failed=0)
            self.assertEqual(summary["out"], str(out))
            self.assertEqual(summary["failures"], [])
            rows = {row["name"]: row for row in summary["examples"]}
            self.assertEqual(set(rows), set(cases))
            for name, (source, version) in cases.items():
                with self.subTest(name=name):
                    row = rows[name]
                    self.assertEqual(row["source"], str(source))
                    self.assertEqual(row["version"], version)
                    if version <= 4:
                        self.assertEqual(row["status"], "written")
                        self.assertEqual(row["returncode"], 0)
                        product = file_bytes(out / name)
                        self.assertEqual(set(product), set(required))
                        for rel in fixed:
                            self.assertEqual(product[rel], (REFERENCE_DIR / rel).read_bytes())
                    else:
                        self.assertEqual(row["status"], "refused")
                        self.assertEqual(row["returncode"], 2)
                        self.assertEqual(row["code"], "UNSUPPORTED_TARGET")
                        self.assertFalse((out / name).exists())
            calls = fake_calls(fake)
            self.assertEqual(len(calls), 16)
            self.assertEqual({Path(argv[1]) for argv in calls}, set(public_sources()))
            self.assertTrue(all(argv[2:4] == ["--target", "react"] and argv[7] == TEST_URL for argv in calls))

    def test_each_missing_product_file_is_reported(self) -> None:
        cases = self.expected_cases()
        names = [name for name, (_, version) in cases.items() if version <= 4]
        required, _ = layout_files()
        # Each batch uses the real 16 public examples. No private test prompt is needed.
        for offset in range(0, len(required), len(names)):
            missing = dict(zip(names, required[offset : offset + len(names)]))
            with self.subTest(files=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake = make_fake_som(root, omit={name: [rel] for name, rel in missing.items()})
                proc, summary = self.run_cli(fake, root / "out")
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assert_counts(summary, written=8 - len(missing), refused=8, failed=len(missing))
                failures = {row["name"]: row for row in summary["failures"]}
                self.assertEqual(set(failures), set(missing))
                for name, rel in missing.items():
                    self.assertEqual(failures[name]["status"], "failed")
                    self.assertEqual(failures[name]["returncode"], 0)
                    self.assertIn(rel, failures[name]["missing"])

    def test_different_package_lock_bytes_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_som(root, differ={"v2-cnl": {"package-lock.json": "append"}})
            proc, summary = self.run_cli(fake, root / "out")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assert_counts(summary, written=7, refused=8, failed=1)
            failure = summary["failures"][0]
            self.assertEqual(failure["name"], "v2-cnl")
            self.assertIn("package-lock.json", failure["differing_files"])

    def test_every_fixed_file_uses_raw_bytes_without_crlf_normalization(self) -> None:
        cases = self.expected_cases()
        names = [name for name, (_, version) in cases.items() if version <= 4]
        _, fixed = layout_files()
        for rel in fixed:
            raw = (REFERENCE_DIR / rel).read_bytes()
            self.assertIn(b"\n", raw, rel)
            self.assertNotIn(b"\r\n", raw, rel)
            self.assertNotEqual(raw, raw.replace(b"\n", b"\r\n"), rel)
        for offset in range(0, len(fixed), len(names)):
            differing = dict(zip(names, fixed[offset : offset + len(names)]))
            with self.subTest(files=differing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake = make_fake_som(root, differ={name: {rel: "crlf"} for name, rel in differing.items()})
                proc, summary = self.run_cli(fake, root / "out")
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assert_counts(summary, written=8 - len(differing), refused=8, failed=len(differing))
                failures = {row["name"]: row for row in summary["failures"]}
                self.assertEqual(set(failures), set(differing))
                for name, rel in differing.items():
                    self.assertIn(rel, failures[name]["differing_files"])

    def test_unexpected_product_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_som(root, extra={"v1-cnl": ["stale.txt"]})
            proc, summary = self.run_cli(fake, root / "out")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assert_counts(summary, written=7, refused=8, failed=1)
            failure = summary["failures"][0]
            self.assertEqual(failure["name"], "v1-cnl")
            self.assertIn("stale.txt", failure["unexpected"])

    def test_v5_refusal_is_expected_but_v1_refusal_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_som(root, refuse={"v1-cnl": "UNSUPPORTED_TARGET"})
            proc, summary = self.run_cli(fake, root / "out")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assert_counts(summary, written=7, refused=8, failed=1)
            rows = {row["name"]: row for row in summary["examples"]}
            for form in ("cnl", "prompt"):
                self.assertEqual(rows[f"v5-{form}"]["status"], "refused")
                self.assertEqual(rows[f"v5-{form}"]["returncode"], 2)
            self.assertEqual(rows["v1-cnl"]["status"], "failed")
            self.assertEqual(rows["v1-cnl"]["returncode"], 2)
            self.assertEqual(summary["failures"][0]["name"], "v1-cnl")

    def test_v5_requires_exit_2_with_unsupported_target(self) -> None:
        for changes in (
            {"refuse": {"v5-cnl": "INVALID_SCHEMA"}},
            {"succeed_out_of_scope": ["v5-cnl"]},
        ):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake = make_fake_som(root, **changes)
                proc, summary = self.run_cli(fake, root / "out")
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assert_counts(summary, written=8, refused=7, failed=1)
                self.assertEqual(summary["failures"][0]["name"], "v5-cnl")

    def test_nonempty_output_with_stale_product_is_refused_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_som(root, omit={"v1-cnl": ["src/schema.ts"]})
            out = root / "existing-out"
            stale = out / "v1-cnl"
            required, fixed = layout_files()
            for rel in required:
                target = stale / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((REFERENCE_DIR / rel).read_bytes() if rel in fixed else b"stale\r\n")
            (out / "keep.txt").write_bytes(b"do not overwrite\r\n")
            before = file_bytes(out)
            proc, summary = self.run_cli(fake, out)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(summary["status"], "refused")
            self.assertIn("Refused --out", summary["error"])
            for field in ("total", "written", "passed", "refused", "failed"):
                self.assertEqual(summary[field], 0)
            for field in ("examples", "rows", "refusals", "failures"):
                self.assertEqual(summary[field], [])
            self.assertEqual(file_bytes(out), before)
            self.assertEqual(fake_calls(fake), [])

    def test_protected_ports_and_exact_default_url_exception_use_only_pure_checks(self) -> None:
        default_url = "http://127.0.0.1:8765"
        self.assertEqual(smoke.DEFAULT_URL, default_url)
        self.assertIsNone(smoke.validate_url(default_url))
        self.assertIsNone(smoke.validate_url(TEST_URL))
        for url in (
            "http://127.0.0.1:18765",
            "https://127.0.0.1:18765/",
            "http://localhost:8765",
            "http://[::1]:8765",
            "http://127.0.0.1:08765",
            "https://127.0.0.1:8765",
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/api",
            "http://user:secret@127.0.0.1:8765",
            "http://127.0.0.1:8765?query=value",
            "http://127.0.0.1:8765#fragment",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(smoke.validate_url(url))

    def test_react_quickstart_relative_links_resolve(self) -> None:
        self.assertTrue(REACT_FE_DOC.is_file(), f"missing {REACT_FE_DOC}")
        doc = REACT_FE_DOC.read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", doc)
        self.assertGreater(len(links), 0)
        for raw_target in links:
            target = raw_target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (REACT_FE_DOC.parent / unquote(parsed.path)).resolve()
            self.assertTrue(resolved.is_file(), f"missing relative file link {raw_target!r}: {resolved}")
        for name in ("goal.md", "roadmap.md"):
            path = REPO_ROOT / "docs" / name
            self.assertIn("(react-fe.md)", path.read_text(encoding="utf-8"), f"{path} must link react-fe.md")


if __name__ == "__main__":
    unittest.main()
