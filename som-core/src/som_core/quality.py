"""Reference quality assembly using the pinned native formatter only."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from som_core import composed
from som_core.v1 import V1Error, assemble, resolve_prompt_refs

ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "som-rs/contracts/python-quality-v1/pyproject.toml"
CATALOG = ROOT / "som-code-python/data/block-contracts/composed-quality.json"
VERSION = "som.python-quality.v1"
RUFF_VERSION = "ruff 0.16.6"


def identifiers(source: str) -> set[str]:
    """Collect names outside Python strings and comments (same as Rust)."""
    result: set[str] = set()
    index = 0
    while index < len(source):
        char = source[index]
        if char == "#":
            stop = source.find("\n", index)
            index = len(source) if stop < 0 else stop + 1
        elif char in {"'", '"'}:
            marker = char * (3 if source[index:index + 3] == char * 3 else 1)
            index += len(marker)
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                elif source.startswith(marker, index):
                    index += len(marker)
                    break
                else:
                    index += 1
        elif char.isascii() and (char.isalpha() or char == "_"):
            stop = index + 1
            while stop < len(source) and source[stop].isascii() and (
                source[stop].isalnum() or source[stop] == "_"
            ):
                stop += 1
            result.add(source[index:stop])
            index = stop
        else:
            index += 1
    return result


def assemble_imports(source: str) -> str:
    """Emit only metadata imports whose bindings occur in the block bodies."""
    lines = source.splitlines(keepends=True)
    split = 0
    for line in lines:
        if line.startswith(("import ", "from ")) or not line.strip():
            split += 1
        else:
            break
    body = "".join(lines[split:])
    used = identifiers(body)
    imports: list[str] = []
    for line in lines[:split]:
        text = line.strip()
        if not text:
            continue
        text, marker, comment = text.partition(" #")
        text = text.rstrip()
        suffix = " #" + comment if marker else ""
        prefix, items = (text.split(" import ", 1) if text.startswith("from ")
                         else ("", text.removeprefix("import ")))
        kept = []
        for item in items.split(", "):
            name = item.split(" as ")[-1].split(".")[0]
            if name in used:
                kept.append(item)
        if kept:
            imports.append((prefix + " import " if prefix else "import ")
                           + ", ".join(kept) + suffix)
    return ('"""Generated service module. See the service contract."""\n\n'
            + "\n".join(imports) + "\n\n" + body)


def format_source(path: str, source: str, binary: Path,
                  profile: Path = PROFILE, package: str = "") -> str:
    """Sort imports, format, then reject every remaining lint violation."""
    common = ["--config", str(profile), "--no-cache", "--stdin-filename", path]
    if package:
        common.extend(["--config", "lint.isort.known-first-party=" + json.dumps([package])])
    commands = [
        [str(binary), "check", *common, "--select", "I", "--fix", "-"],
        [str(binary), "format", *common, "-"],
        [str(binary), "check", *common, "--output-format", "json", "-"],
    ]
    for index, command in enumerate(commands):
        try:
            result = subprocess.run(command, input=source, text=True,
                                    capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise V1Error("OUTPUT_QUALITY_FAILED", str(error)) from error
        if result.returncode:
            raise V1Error("OUTPUT_QUALITY_FAILED",
                          path + ": " + result.stdout + result.stderr)
        if index < 2:
            source = result.stdout
    return source


def finalize(files: dict[str, str], binary: Path,
             profile: Path = PROFILE, *,
             byte_limit: int | None = 65536) -> dict[str, str]:
    """Check final size after native formatting."""
    package = re.search(r'^name = "([a-zA-Z0-9_]+)"', files["pyproject.toml"], re.M)
    result = {path: format_source(path, assemble_imports(source), binary, profile,
                                 package[1] if package else "")
              if path.endswith(".py") else source
              for path, source in files.items()}
    if len(result) > 12 or (byte_limit is not None and
                           sum(len(s.encode()) for s in result.values()) > byte_limit):
        raise V1Error("SCOPE_TOO_LARGE", "final quality output exceeds limits")
    return result


def reference(prompt: dict, dsl: dict, blocks: dict,
              binary: Path) -> dict[str, str]:
    """Preserve the selected plan and use the quality contracts."""
    composed.validate_plan(prompt, dsl, json.loads(CATALOG.read_text()))
    return finalize(assemble(resolve_prompt_refs(prompt, dsl), blocks), binary)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
