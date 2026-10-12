"""Preserve existing services and run read-only quality checks."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    destination: Path = args.output
    destination.mkdir(parents=True, exist_ok=False)
    profile = ROOT / "som-rs/contracts/python-quality-v1/pyproject.toml"
    shutil.copy2(profile, destination / profile.name)
    old = ROOT / "som-rs/evals/composable-2026-10-01"
    services = json.loads((old / "rust-services.json").read_text())["results"]
    record: dict[str, object] = {
        "profile_sha256": digest(profile),
        "original_evidence_sha256": digest(old / "archive/evidence.tar.gz"),
        "git_status": subprocess.check_output(
            ["git", "status", "--short"], cwd=ROOT, text=True
        ),
        "services": [],
    }
    results: list[dict[str, object]] = []
    for service in services:
        source = Path(service["directory"])
        saved = destination / service["package"]
        saved.mkdir()
        hashes: dict[str, str] = {}
        for relative in service["source_sha256"]:
            original = source / relative
            if digest(original) != service["source_sha256"][relative]:
                raise ValueError(f"sealed source changed: {original}")
            target = saved / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            hashes[relative] = digest(original)
        sources = sorted(name for name in hashes if name.endswith(".py"))
        checks = {
            "ruff": [str(args.python.parent / "ruff"), "check", "--config", str(profile),
                     "--output-format", "json", *sources],
            "format": [str(args.python.parent / "ruff"), "format", "--check", "--config",
                       str(profile), *sources],
            "mypy": [str(args.python), "-m", "mypy", "--config-file",
                     str(profile), "--cache-dir", str(saved / ".mypy-cache"),
                     *sources],
            "pylint": [str(args.python), "-m", "pylint", "--rcfile",
                       str(profile), "--persistent=no", "--output-format=json2", *sources],
            "isort": [str(args.python), "-m", "isort", "--check-only",
                      "--settings-path", str(profile), *sources],
        }
        outcomes: dict[str, object] = {}
        for name, command in checks.items():
            start = time.monotonic()
            completed = subprocess.run(
                command, cwd=saved, capture_output=True, text=True,
                timeout=300, check=False,
            )
            (saved / f"{name}.stdout").write_text(completed.stdout)
            (saved / f"{name}.stderr").write_text(completed.stderr)
            outcomes[name] = {
                "command": command, "exit_code": completed.returncode,
                "seconds": time.monotonic() - start,
                "stdout_sha256": digest(saved / f"{name}.stdout"),
                "stderr_sha256": digest(saved / f"{name}.stderr"),
            }
        results.append({"package": service["package"],
                        "source_sha256": hashes, "checks": outcomes})
        record["services"] = results
        (destination / "baseline.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        print(json.dumps({"package": service["package"],
                          "checks": {k: v["exit_code"]
                                     for k, v in outcomes.items()}}), flush=True)


if __name__ == "__main__":
    main()
