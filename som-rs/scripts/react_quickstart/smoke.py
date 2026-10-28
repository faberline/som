#!/usr/bin/env python3
"""Smoke-check React output for every public compiler-v1 CNL and JSON prompt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
import urllib.parse

SOM_RS_DIR = Path(__file__).resolve().parents[2]
DEFAULT_EXAMPLES_DIR = SOM_RS_DIR / "contracts" / "compiler-v1" / "examples"
REACT_CONTRACT_DIR = SOM_RS_DIR / "contracts" / "react-output-v1"
DEFAULT_REFERENCE_DIR = REACT_CONTRACT_DIR / "reference"
DEFAULT_URL = "http://127.0.0.1:8765"

FORBIDDEN_PORTS = {8765, 18765}
IN_SCOPE_VERSIONS = {1, 2, 3, 4}
OUT_OF_SCOPE_VERSIONS = {5, 6, 7}
REFUSAL_CODE = "UNSUPPORTED_TARGET"

# Keep these paths in the order of react-output-v1/product-layout.json.
REFERENCE_FILES = (
    "index.html",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
    "src/main.tsx",
    "src/App.tsx",
    "src/api.ts",
    "src/spec.ts",
    "src/form.ts",
    "src/styles.css",
    "src/pages/ListPage.tsx",
    "src/pages/DetailPage.tsx",
    "src/pages/FormPage.tsx",
    "src/pages/ErrorView.tsx",
    "src/app.test.tsx",
    "src/form.test.ts",
)
REQUIRED_FILES = REFERENCE_FILES + ("src/schema.ts", "README.md")
# Preserve the helper name used by the TypeScript quickstart.
REQUIRED_FIXED_FILES = REQUIRED_FILES

CNL_VERSION_RE = re.compile(r"^\s*Use\s+prompt\s+version\s+([0-9]+)\s*\.", re.MULTILINE)
JSON_VERSION_RE = re.compile(r"^som\.prompt\.v([0-9]+)$")
STEM_VERSION_RE = re.compile(r"^v([0-9]+)(?:$|[-_])")


def example_sources(examples_dir: Path) -> list[Path]:
    return sorted(examples_dir.glob("*.cnl")) + sorted(examples_dir.glob("*.prompt.json"))


def case_name_for(source: Path) -> str:
    if source.name.endswith(".prompt.json"):
        return f"{source.name[:-len('.prompt.json')]}-prompt"
    if source.name.endswith(".cnl"):
        return f"{source.name[:-len('.cnl')]}-cnl"
    return source.name.replace(".", "-")


def detect_prompt_version(source: Path) -> int | None:
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""

    if source.name.endswith(".cnl") and text:
        match = CNL_VERSION_RE.search(text)
        if match is not None:
            return int(match.group(1))
    elif source.name.endswith(".json") and text:
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            raw_ver = doc.get("version")
            if not isinstance(raw_ver, str) and isinstance(doc.get("prompt"), dict):
                raw_ver = doc["prompt"].get("version")
            if isinstance(raw_ver, str):
                match = JSON_VERSION_RE.fullmatch(raw_ver)
                if match is not None:
                    return int(match.group(1))

    stem = (
        source.name[:-len(".prompt.json")]
        if source.name.endswith(".prompt.json")
        else source.stem
    )
    match = STEM_VERSION_RE.match(stem)
    return int(match.group(1)) if match is not None else None


def validate_url(url: str) -> str | None:
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        return "Refused --url: whitespace and control characters are not allowed"
    if "\\" in url:
        return "Refused --url: backslashes are not allowed"
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        return f"Refused --url: {exc}"
    if parsed.scheme not in ("http", "https") or not hostname:
        return "Refused --url: must be an http:// or https:// URL"
    if parsed.username is not None or parsed.password is not None:
        return "Refused --url: credentials are not allowed"
    if parsed.path not in ("", "/") or parsed.params:
        return "Refused --url: must be a service base URL without a path"
    if "?" in url or "#" in url:
        return "Refused --url: query parameters and fragments are not allowed"
    if port in FORBIDDEN_PORTS and url != DEFAULT_URL:
        return (
            "Refused --url: ports 8765 and 18765 are forbidden, "
            f"except for the exact CLI default {DEFAULT_URL}"
        )
    return None


def validate_out_dir(out: Path) -> str | None:
    if out.is_symlink():
        return f"Refused --out {out}: symlinks are not allowed"
    if out.exists():
        if not out.is_dir():
            return f"Refused --out {out}: path exists and is not a directory"
        try:
            if any(out.iterdir()):
                return f"Refused --out {out}: directory is not empty"
        except OSError as exc:
            return f"Refused --out {out}: {exc}"
    return None


def check_product_layout(target_dir: Path) -> tuple[list[str], list[str]]:
    """Return missing files and unexpected entries without following symlinks."""
    if target_dir.is_symlink() or not target_dir.is_dir():
        return ["<output directory>"], []

    expected = set(REQUIRED_FILES)
    expected_dirs = {
        parent.as_posix()
        for rel_name in REQUIRED_FILES
        for parent in Path(rel_name).parents
        if parent != Path(".")
    }
    files: set[str] = set()
    unexpected: list[str] = []
    pending = [target_dir]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir())
        except OSError:
            unexpected.append(f"<unreadable directory: {directory.relative_to(target_dir)}>")
            continue
        for candidate in children:
            rel_name = candidate.relative_to(target_dir).as_posix()
            if candidate.is_symlink():
                unexpected.append(rel_name)
            elif candidate.is_dir():
                if rel_name in expected_dirs:
                    pending.append(candidate)
                else:
                    unexpected.append(f"{rel_name}/")
            elif candidate.is_file():
                files.add(rel_name)
            else:
                unexpected.append(rel_name)

    missing = [rel_name for rel_name in REQUIRED_FILES if rel_name not in files]
    unexpected.extend(files - expected)
    return missing, sorted(unexpected)


def check_generated_dir(target_dir: Path) -> list[str]:
    return check_product_layout(target_dir)[0]


def check_reference_byte_identity(
    target_dir: Path,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
) -> list[str]:
    differing: list[str] = []
    for rel_name in REFERENCE_FILES:
        gen_path = target_dir / rel_name
        ref_path = reference_dir / rel_name
        try:
            if gen_path.is_symlink() or not gen_path.is_file():
                differing.append(rel_name)
                continue
            if gen_path.read_bytes() != ref_path.read_bytes():
                differing.append(rel_name)
        except OSError:
            differing.append(rel_name)
    return differing


def is_unsupported_target_refusal(returncode: int, stderr: str) -> bool:
    if returncode != 2:
        return False
    try:
        payload = json.loads(stderr.strip())
    except ValueError:
        return False
    if not isinstance(payload, dict) or payload.get("code") != REFUSAL_CODE:
        return False
    diagnostics = payload.get("diagnostics")
    if diagnostics is not None:
        if not isinstance(diagnostics, list) or not diagnostics:
            return False
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict) or diagnostic.get("code") != REFUSAL_CODE:
                return False
    return True


def _make_refusal_summary(out_dir: Path, error: str) -> dict[str, Any]:
    return {
        "status": "refused",
        "error": error,
        "out": str(out_dir),
        "total": 0,
        "written": 0,
        "passed": 0,
        "refused": 0,
        "failed": 0,
        "examples": [],
        "rows": [],
        "refusals": [],
        "failures": [],
    }


def run_smoke(
    som_bin: Path,
    url: str,
    out_dir: Path,
    examples_dir: Path = DEFAULT_EXAMPLES_DIR,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
) -> tuple[int, dict[str, Any]]:
    url_error = validate_url(url)
    if url_error is not None:
        return 2, _make_refusal_summary(out_dir, url_error)

    out_error = validate_out_dir(out_dir)
    if out_error is not None:
        return 2, _make_refusal_summary(out_dir, out_error)

    sources = example_sources(examples_dir)
    if not sources:
        return 2, _make_refusal_summary(
            out_dir,
            f"No *.cnl or *.prompt.json examples found in {examples_dir}",
        )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 2, _make_refusal_summary(out_dir, f"Cannot create --out {out_dir}: {exc}")

    rows: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    written = 0

    for source in sources:
        name = case_name_for(source)
        version = detect_prompt_version(source)
        target_dir = out_dir / name
        cmd = [
            str(som_bin),
            "write",
            str(source),
            "--target",
            "react",
            "--out",
            str(target_dir),
            "--url",
            url,
        ]
        row: dict[str, Any] = {
            "name": name,
            "source": str(source),
            "version": version,
            "status": "failed",
            "returncode": None,
        }
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except (OSError, UnicodeError) as exc:
            row["reason"] = f"failed to execute {som_bin}: {exc}"
            rows.append(row)
            failures.append(row)
            continue

        row["returncode"] = proc.returncode
        stderr_text = proc.stderr.strip()
        if version in OUT_OF_SCOPE_VERSIONS:
            if is_unsupported_target_refusal(proc.returncode, stderr_text):
                row["status"] = "refused"
                row["code"] = REFUSAL_CODE
                refusals.append(row)
            else:
                row["reason"] = (
                    f"expected exit 2 with {REFUSAL_CODE} for out-of-scope version"
                )
                row["stderr"] = stderr_text
                failures.append(row)
            rows.append(row)
            continue

        if version not in IN_SCOPE_VERSIONS:
            row["reason"] = f"unknown prompt version {version!r}"
            row["stderr"] = stderr_text
        elif proc.returncode != 0:
            row["reason"] = "expected a written React product for in-scope version"
            row["stderr"] = stderr_text
        else:
            missing, unexpected = check_product_layout(target_dir)
            if missing or unexpected:
                row["reason"] = "generated product layout does not match the React contract"
                if missing:
                    row["missing"] = missing
                if unexpected:
                    row["unexpected"] = unexpected
            else:
                differing = check_reference_byte_identity(target_dir, reference_dir)
                if differing:
                    row["reason"] = "fixed product bytes differ from the React reference"
                    row["differing_files"] = differing
                else:
                    row["status"] = "written"
                    written += 1

        rows.append(row)
        if row["status"] == "failed":
            failures.append(row)

    failed = len(failures)
    summary: dict[str, Any] = {
        "status": "ok" if failed == 0 else "error",
        "out": str(out_dir),
        "total": len(sources),
        "written": written,
        "passed": written,
        "refused": len(refusals),
        "failed": failed,
        "examples": rows,
        "rows": rows,
        "refusals": refusals,
        "failures": failures,
    }
    return (0 if failed == 0 else 1), summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run `som write --target react` across compiler-v1 CNL and JSON prompt examples. "
            "Check v1-v4 products and v5-v7 UNSUPPORTED_TARGET refusals."
        )
    )
    parser.add_argument("--som", type=Path, required=True, help="Path to the som CLI binary.")
    parser.add_argument(
        "--url",
        required=True,
        help=(
            "Base URL of the SOM HTTP service. Ports 8765 and 18765 are refused "
            f"except for the exact CLI default {DEFAULT_URL}."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New or empty directory where generated projects will be written.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code, summary = run_smoke(args.som, args.url, args.out)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
