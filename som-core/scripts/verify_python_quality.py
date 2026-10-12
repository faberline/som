"""Read-only, complete-product quality acceptance with persistent evidence."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

PINS = {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root: Path, evidence: Path) -> dict[str, object]:
    """Check every source file with the same pinned tools and settings."""
    evidence.mkdir(parents=True, exist_ok=False)
    files = sorted(str(path.relative_to(root)) for path in root.rglob("*.py")
                   if not any(part.startswith(".") for part in path.relative_to(root).parts))
    before = {name: sha256(root / name) for name in files + ["pyproject.toml"]}
    versions = {name: importlib.metadata.version(name) for name in PINS}
    if versions != PINS:
        raise ValueError("quality tools differ from pinned versions")
    binary = Path(sys.executable).parent / "ruff"
    from quality_policy import inspect
    policy = inspect(root, files)
    commands = {
        "ruff": [str(binary), "check", "--config", "pyproject.toml", "--no-cache", "--output-format=json", *files],
        "format": [str(binary), "format", "--check", "--config", "pyproject.toml", "--no-cache", *files],
        "mypy": [sys.executable, "-m", "mypy", "--config-file", "pyproject.toml", "--cache-dir", str(evidence / "mypy-cache"), *files],
        "pylint": [sys.executable, "-m", "pylint", "--rcfile", "pyproject.toml", "--persistent=no", "--output-format=json2", *files],
        "isort": [sys.executable, "-m", "isort", "--check-only", "--settings-path", "pyproject.toml", *files],
    }
    checks = {}
    environment = dict(os.environ, PYLINTHOME=str(evidence / "pylint-cache"))
    for name, command in commands.items():
        started = time.monotonic()
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                text=True, check=False, timeout=300)
        stdout, stderr = evidence / (name + ".stdout"), evidence / (name + ".stderr")
        stdout.write_text(result.stdout)
        stderr.write_text(result.stderr)
        checks[name] = {"command": command, "exit_code": result.returncode,
                        "seconds": time.monotonic() - started,
                        "stdout_sha256": sha256(stdout), "stderr_sha256": sha256(stderr)}
    unchanged = before == {name: sha256(root / name) for name in before}
    record = {"source_sha256": before, "source_unchanged": unchanged,
              "tool_versions": versions, "formatter_sha256": sha256(binary),
              "policy": policy, "checks": checks,
              "passed": unchanged and policy["passed"] and all(c["exit_code"] == 0 for c in checks.values())}
    (evidence / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    record = verify(args.root.resolve(), args.evidence.resolve())
    print(json.dumps({"passed": record["passed"], "checks": {k: v["exit_code"] for k, v in record["checks"].items()}}))
    raise SystemExit(not record["passed"])


if __name__ == "__main__":
    main()
