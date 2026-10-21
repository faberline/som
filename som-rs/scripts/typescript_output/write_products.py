#!/usr/bin/env python3
"""Write assembled TypeScript phase 1 products for target-examples.json and name_cases (issue #75).

Runs the in-process Rust compiler/assembler via `som-targets` without opening any port or
accessing the network, writing each assembled product into `<PRODUCTS_DIR>/<case_id>/` and a
`<PRODUCTS_DIR>/manifest.json` index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
CARGO_BIN = "/opt/homebrew/Cellar/rust/1.96.1/bin/cargo"
RUST_BIN_DIR = "/opt/homebrew/Cellar/rust/1.96.1/bin"


def find_runtime_root(repo: pathlib.Path = REPO) -> pathlib.Path:
    for candidate in (repo, *repo.parents):
        if (candidate / ".local" / "typescript-output").is_dir():
            return candidate
        if (candidate / "som-rs" / "runtime" / "default.json").is_file():
            return candidate
    return repo


def default_prettier_dir() -> pathlib.Path:
    env_val = os.environ.get("SOM_TS_PRETTIER", "").strip()
    if env_val:
        return pathlib.Path(env_val)
    root = find_runtime_root()
    return root / ".local" / "typescript-output" / "prettier-001" / "node_modules" / "prettier"


def check_out_dir_safe(out_dir: pathlib.Path) -> None:
    resolved = out_dir.resolve()
    for part in resolved.parts:
        if part.startswith(("npm-cache-", "prettier-")):
            raise SystemExit(f"refusing to write into protected .local path: {resolved}")
    if resolved.exists():
        if not resolved.is_dir():
            raise SystemExit(f"refusing to write to non-directory path: {resolved}")
        if any(resolved.iterdir()):
            raise SystemExit(f"refusing to write into non-empty directory: {resolved}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("products_dir", nargs="?", type=pathlib.Path, help="new output directory for products")
    parser.add_argument("--out", dest="out_opt", type=pathlib.Path, help="new output directory for products")
    parser.add_argument(
        "--prettier",
        type=pathlib.Path,
        default=None,
        help="path to verified node_modules/prettier (defaults to $SOM_TS_PRETTIER or .local/typescript-output/prettier-001/node_modules/prettier)",
    )
    args = parser.parse_args(argv)

    out_dir = args.out_opt or args.products_dir
    if out_dir is None:
        parser.error("pass PRODUCTS_DIR (positional or --out)")

    check_out_dir_safe(out_dir)
    prettier_dir = (args.prettier or default_prettier_dir()).resolve()
    if not prettier_dir.is_dir():
        raise SystemExit(f"prettier directory does not exist: {prettier_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_toml = SOM_RS / "som-targets" / "Cargo.toml"

    env = dict(os.environ)
    env["SOM_TS_PRETTIER"] = str(prettier_dir)
    env["SOM_TS_WRITE_PRODUCTS_DIR"] = str(out_dir.resolve())
    env["CARGO_NET_OFFLINE"] = "true"
    env["PATH"] = f"{RUST_BIN_DIR}:{env.get('PATH', '/usr/bin:/bin')}"

    cmd = [
        CARGO_BIN,
        "test",
        "--manifest-path",
        str(manifest_toml),
        "--locked",
        "--offline",
        "typescript::tests::target_examples_and_name_cases_compile_to_valid_typescript_bundles",
        "--",
        "--exact",
        "--nocapture",
    ]
    done = subprocess.run(cmd, cwd=SOM_RS / "som-targets", env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        sys.stderr.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"expected manifest.json was not written at {manifest_path}")

    manifest_data: dict[str, Any] = json.loads(manifest_path.read_text())
    products = manifest_data.get("products", [])
    rows: list[dict[str, Any]] = []
    for entry in products:
        p_dir = pathlib.Path(entry["product"])
        files = sorted(
            p.relative_to(p_dir).as_posix()
            for p in p_dir.rglob("*")
            if p.is_file()
        )
        file_hashes = {
            rel: hashlib.sha256((p_dir / rel).read_bytes()).hexdigest()
            for rel in files
        }
        rows.append(
            {
                "id": entry["id"],
                "path": str(p_dir),
                "file_count": len(files),
                "files_sha256": hashlib.sha256(
                    json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            }
        )

    summary = {
        "status": "ok",
        "products_dir": str(out_dir.resolve()),
        "total_products": len(rows),
        "products": rows,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
