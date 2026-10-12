"""Append-only capture of the exact development baseline before M2 edits."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    roots = ["som-core/src", "som-core/scripts", "som-core/tests", "som-core/data",
             "som-core/block_templates", "som-rs/src", "som-rs/tests", "som-rs/contracts",
             "som-rs/models/composed-quality-candidate", "som-code-python/data/blocks",
             "som-code-python/data/block-contracts"]
    paths = [path for name in roots for path in (ROOT/name).rglob("*")
             if path.is_file() and "__pycache__" not in path.parts]
    paths += [ROOT/name for name in ["som-rs/Cargo.toml", "som-rs/Cargo.lock",
              "som-core/pyproject.toml", "som-core/uv.lock", "som-rs/target/release/som",
              "som-rs/evals/direct-baselines/quality-v1-main-models-high.json",
              "som-rs/evals/fixed-baseline-som-2026-10-01/r1/SUMMARY.md",
              "som-rs/evals/fixed-baseline-som-2026-10-01/r1/final-audit.json"]]
    paths = sorted(set(paths))
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in paths}
    with tarfile.open(args.output/"sources.tar.gz", "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    record = {"version": "som.m1.baseline.v1", "files": hashes,
              "archive_sha256": digest(args.output/"sources.tar.gz"),
              "git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
              "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "platform": platform.platform(), "python": platform.python_version(),
              "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
              "default_model": str((ROOT/"som-rs/models/current").resolve()),
              "default_blocks": str((ROOT/"som-code-python/data/blocks/current").resolve())}
    (args.output/"baseline.json").write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps({"files": len(hashes), "archive_sha256": record["archive_sha256"]}))


if __name__ == "__main__":
    main()
