"""Reuse exact weights and codec in the separate quality candidate."""

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    source = ROOT / "som-rs/models/composed-candidate"
    target = ROOT / "som-rs/models/composed-quality-candidate"
    if not target.exists():
        shutil.copytree(source, target)
    profile = ROOT / "som-rs/contracts/python-quality-v1"
    binary = Path("/private/tmp/som-python-quality-tools-2026-10-01/bin/ruff").resolve()
    shutil.copy2(binary, target / "ruff")
    for original, name in [(profile / "pyproject.toml", "pyproject.toml"),
                           (profile / "contract.md", "quality-contract.md"),
                           (profile / "exceptions.json", "quality-exceptions.json"),
                           (ROOT / "som-code-python/data/block-contracts/composed-quality.json", "contracts.json")]:
        shutil.copy2(original, target / name)
    config = {"id": "som.python-quality.v1", "formatter_version": "ruff 0.16.6",
              "formatter_sha256": digest(target / "ruff"),
              "profile_sha256": digest(target / "pyproject.toml")}
    (target / "quality.json").write_text(json.dumps(config, indent=2) + "\n")
    manifest = json.loads((source / "manifest.json").read_text())
    for name in ("contracts.json", "quality.json", "ruff", "pyproject.toml",
                 "quality-contract.md", "quality-exceptions.json"):
        manifest["files"][name] = digest(target / name)
    manifest["quality_contract"] = config["id"]
    manifest["blocks_sha256"] = digest(ROOT / "som-code-python/data/blocks/composed-quality/blocks.json")
    manifest["parent_manifest_sha256"] = digest(source / "manifest.json")
    for name in ("model.safetensors", "config.json", "tokenizer.json", "codec.json"):
        if digest(target / name) != digest(source / name):
            raise ValueError("model or token ABI changed: " + name)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"manifest_sha256": digest(target / "manifest.json"),
                      "model_and_codec_unchanged": True,
                      "bytes": sum(p.stat().st_size for p in target.rglob("*") if p.is_file())}))


if __name__ == "__main__":
    main()
