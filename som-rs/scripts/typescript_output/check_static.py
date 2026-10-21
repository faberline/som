#!/usr/bin/env python3
"""Run offline static gates (install, typecheck, format_check, lock_unchanged) on TypeScript products (issue #75).

Executes the exact commands in `som-rs/contracts/typescript-output-v1/toolchain.json` against the
read-only vendored npm cache and verifies that neither the product files outside `node_modules`
nor the vendored npm cache are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
TS_CONTRACT = SOM_RS / "contracts" / "typescript-output-v1"
TS_REFERENCE = TS_CONTRACT / "reference"
TS_TESTS = SOM_RS / "tests" / "typescript-output-v1"

if str(TS_TESTS) not in sys.path:
    sys.path.insert(0, str(TS_TESTS))

from tree_sha import tree_sha  # noqa: E402

TOOLCHAIN = json.loads((TS_CONTRACT / "toolchain.json").read_text())
LAYOUT = json.loads((TS_CONTRACT / "product-layout.json").read_text())
NODE_BIN = TOOLCHAIN["node"]["binary"]
NPM_INVOCATION = TOOLCHAIN["npm"]["invocation"].split()
PG_BIN_DIR = pathlib.Path(TOOLCHAIN["postgresql"]["bin_dir"])
REFERENCE_HASHES = {
    name: hashlib.sha256((TS_REFERENCE / name).read_bytes()).hexdigest()
    for name in ("package.json", "package-lock.json", "tsconfig.json")
}
STATIC_GATES = ("install", "typecheck", "format_check", "lock_unchanged")


def file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_runtime_root(repo: pathlib.Path = REPO) -> pathlib.Path:
    for candidate in (repo, *repo.parents):
        if (candidate / ".local" / "typescript-output").is_dir():
            return candidate
        if (candidate / "som-rs" / "runtime" / "default.json").is_file():
            return candidate
    return repo


def resolve_local_path(path: pathlib.Path, runtime_root: pathlib.Path | None = None) -> pathlib.Path:
    if path.is_absolute() or path.exists():
        return path
    root = runtime_root or find_runtime_root()
    candidate = root / path
    if candidate.exists():
        return candidate
    return path


def default_cache_dir() -> pathlib.Path:
    root = find_runtime_root()
    return root / ".local" / "typescript-output" / "npm-cache-001"


def verify_toolchain(cache_dir: pathlib.Path) -> dict[str, Any]:
    problems: list[str] = []
    node_cfg = TOOLCHAIN["node"]
    node_path = pathlib.Path(node_cfg["binary"])
    if not node_path.is_file() or file_sha256(node_path) != node_cfg["sha256"]:
        problems.append(f"node binary hash mismatch at {node_path}")

    libnode_path = pathlib.Path(node_cfg["libnode"]["path"])
    if not libnode_path.is_file() or file_sha256(libnode_path) != node_cfg["libnode"]["sha256"]:
        problems.append(f"libnode hash mismatch at {libnode_path}")

    for dylib, spec in node_cfg["homebrew_libraries"].items():
        resolved = pathlib.Path(spec["resolved"])
        if not resolved.is_file() or file_sha256(resolved) != spec["sha256"]:
            problems.append(f"Homebrew dylib hash mismatch for {dylib} ({resolved})")

    npm_cfg = TOOLCHAIN["npm"]
    npm_root = pathlib.Path(npm_cfg["root"])
    if not npm_root.is_dir():
        problems.append(f"npm root missing at {npm_root}")
    else:
        npm_sha, npm_files, npm_links = tree_sha(npm_root)
        if (npm_sha, npm_files, npm_links) != (
            npm_cfg["tree_sha256"],
            npm_cfg["tree_files"],
            npm_cfg["tree_symlinks"],
        ):
            problems.append(
                f"npm tree mismatch: got {(npm_sha, npm_files, npm_links)}, "
                f"expected {(npm_cfg['tree_sha256'], npm_cfg['tree_files'], npm_cfg['tree_symlinks'])}"
            )

    if not cache_dir.is_dir():
        problems.append(f"vendored npm cache directory does not exist: {cache_dir}")
        cache_info: dict[str, Any] = {}
    else:
        c_sha, c_files, c_links = tree_sha(cache_dir)
        cache_info = {
            "path": str(cache_dir),
            "tree_sha256": c_sha,
            "tree_files": c_files,
            "tree_symlinks": c_links,
        }
        vc = TOOLCHAIN["vendored_cache"]
        if cache_dir.name == pathlib.Path(vc["path"]).name:
            if (c_sha, c_files, c_links) != (vc["tree_sha256"], vc["tree_files"], vc["tree_symlinks"]):
                problems.append(
                    f"vendored cache tree mismatch: got {(c_sha, c_files, c_links)}, "
                    f"expected {(vc['tree_sha256'], vc['tree_files'], vc['tree_symlinks'])}"
                )

    if problems:
        raise RuntimeError("toolchain verification failed: " + "; ".join(problems))
    return {
        "node_version": node_cfg["version"],
        "node_sha256": node_cfg["sha256"],
        "libnode_sha256": node_cfg["libnode"]["sha256"],
        "npm_version": npm_cfg["version"],
        "npm_tree_sha256": npm_cfg["tree_sha256"],
        "cache": cache_info,
    }


def toolchain_env(cache_dir: pathlib.Path, scratch_dir: pathlib.Path) -> dict[str, str]:
    logs_dir = scratch_dir / "npm-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    empty_npmrc = scratch_dir / "empty-npmrc"
    if not empty_npmrc.exists():
        empty_npmrc.write_text("")

    env = dict(os.environ)
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "SOM_APP_ADDR"):
        env.pop(key, None)

    node_bin_dir = str(pathlib.Path(NODE_BIN).parent)
    env.update(
        {
            "PATH": f"{node_bin_dir}:/usr/bin:/bin",
            "npm_config_cache": str(cache_dir.resolve()),
            "npm_config_offline": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "npm_config_logs_dir": str(logs_dir.resolve()),
            "npm_config_userconfig": str(empty_npmrc.resolve()),
            "TZ": "UTC",
            "LC_ALL": "C",
        }
    )
    return env


def copy_product(source_dir: pathlib.Path, dest_dir: pathlib.Path) -> dict[str, str]:
    dest_dir.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}
    for path in sorted(source_dir.rglob("*")):
        rel = path.relative_to(source_dir)
        if "node_modules" in rel.parts or ".git" in rel.parts:
            continue
        target = dest_dir / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            target.write_bytes(data)
            hashes[rel.as_posix()] = hashlib.sha256(data).hexdigest()
    return hashes


def snapshot_outside_node_modules(root: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if "node_modules" in rel.parts:
            continue
        if path.is_file():
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def discover_products(products_dir: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    manifest_path = products_dir / "manifest.json"
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text())
        entries = data["products"] if isinstance(data, dict) and "products" in data else data
        return [(entry["id"], pathlib.Path(entry.get("product") or entry["path"])) for entry in entries]
    if (products_dir / "package.json").is_file():
        return [(products_dir.name, products_dir)]
    found: list[tuple[str, pathlib.Path]] = []
    for child in sorted(products_dir.iterdir()):
        if child.is_dir() and (child / "package.json").is_file():
            found.append((child.name, child))
    return found


def run_cmd(command: list[str], cwd: pathlib.Path, env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300.0, check=False)
    duration_ms = round((time.monotonic() - started) * 1000)
    return {
        "command": command,
        "exit_code": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "duration_ms": duration_ms,
    }


def check_one_product(
    case_id: str,
    product_dir: pathlib.Path,
    cache_dir: pathlib.Path,
    scratch_root: pathlib.Path,
) -> dict[str, Any]:
    work_product = scratch_root / "products" / case_id
    env_scratch = scratch_root / "env" / case_id
    env_scratch.mkdir(parents=True, exist_ok=True)

    before_files = copy_product(product_dir, work_product)
    env = toolchain_env(cache_dir, env_scratch)

    # 1. install
    install_cmd = [*NPM_INVOCATION, "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"]
    install_res = run_cmd(install_cmd, work_product, env)
    install_ok = install_res["exit_code"] == 0

    # 2. typecheck
    tsc_cmd = [NODE_BIN, "node_modules/typescript/bin/tsc", "-p", "tsconfig.json"]
    if install_ok:
        tsc_res = run_cmd(tsc_cmd, work_product, env)
        tsc_out = (tsc_res["stdout"] + tsc_res["stderr"]).strip()
        typecheck_ok = tsc_res["exit_code"] == 0 and tsc_out == ""
    else:
        tsc_res = {"command": tsc_cmd, "exit_code": None, "stdout": "", "stderr": "not_run", "duration_ms": 0}
        typecheck_ok = False

    # 3. format_check
    ts_files = [
        rel
        for rel in ("src/main.ts", "src/app.ts", "src/schemas.ts", "src/db.ts", "src/migrate.ts", "test/api.test.ts")
        if (work_product / rel).is_file()
    ]
    prettier_cmd = [
        NODE_BIN,
        "node_modules/prettier/bin/prettier.cjs",
        "--check",
        "--no-config",
        "--no-editorconfig",
        *ts_files,
    ]
    if install_ok:
        fmt_res = run_cmd(prettier_cmd, work_product, env)
        format_ok = fmt_res["exit_code"] == 0
    else:
        fmt_res = {"command": prettier_cmd, "exit_code": None, "stdout": "", "stderr": "not_run", "duration_ms": 0}
        format_ok = False

    # 4. lock_unchanged
    after_files = snapshot_outside_node_modules(work_product)
    source_after_files = snapshot_outside_node_modules(product_dir)
    lock_problems: list[str] = []
    if before_files != after_files:
        lock_problems.append("files outside node_modules changed during commands")
    if before_files != source_after_files:
        lock_problems.append("source product directory changed")
    for ref_name, ref_sha in REFERENCE_HASHES.items():
        if after_files.get(ref_name) != ref_sha:
            lock_problems.append(f"{ref_name} differs from reference/{ref_name}")
    lock_ok = not lock_problems

    gates = {
        "install": {
            "passed": install_ok,
            "exit_code": install_res["exit_code"],
            "duration_ms": install_res["duration_ms"],
            "output": (install_res["stdout"] + install_res["stderr"]).strip()[-1500:],
        },
        "typecheck": {
            "passed": typecheck_ok,
            "exit_code": tsc_res["exit_code"],
            "duration_ms": tsc_res["duration_ms"],
            "output": (tsc_res["stdout"] + tsc_res["stderr"]).strip()[-1500:],
        },
        "format_check": {
            "passed": format_ok,
            "exit_code": fmt_res["exit_code"],
            "duration_ms": fmt_res["duration_ms"],
            "output": (fmt_res["stdout"] + fmt_res["stderr"]).strip()[-1500:],
        },
        "lock_unchanged": {
            "passed": lock_ok,
            "exit_code": 0 if lock_ok else 1,
            "problems": lock_problems,
        },
    }
    passed = all(gates[g]["passed"] for g in STATIC_GATES)
    return {
        "id": case_id,
        "passed": passed,
        "gate_statuses": {g: ("passed" if gates[g]["passed"] else "failed") for g in STATIC_GATES},
        "gates": gates,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("products_dir", nargs="?", type=pathlib.Path, help="directory containing generated products")
    parser.add_argument("--products", dest="products_opt", type=pathlib.Path, help="directory containing generated products")
    parser.add_argument(
        "--cache",
        type=pathlib.Path,
        default=None,
        help="path to vendored npm cache (defaults to .local/typescript-output/npm-cache-001)",
    )
    parser.add_argument("--out", type=pathlib.Path, default=None, help="optional path to write JSON report")
    args = parser.parse_args(argv)

    products_dir = args.products_opt or args.products_dir
    if products_dir is None:
        parser.error("pass PRODUCTS_DIR (positional or --products)")
    if not products_dir.is_dir():
        raise SystemExit(f"products directory does not exist: {products_dir}")

    cache_dir = resolve_local_path(args.cache or default_cache_dir())
    toolchain_info = verify_toolchain(cache_dir)
    cache_before = dict(toolchain_info["cache"])

    items = discover_products(products_dir)
    if not items:
        raise SystemExit(f"no TypeScript products found in {products_dir}")

    started = time.time()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="som-ts-static-", ignore_cleanup_errors=True) as tmp:
        scratch = pathlib.Path(tmp)
        for case_id, prod_path in items:
            rows.append(check_one_product(case_id, prod_path, cache_dir, scratch))

    cache_sha_after, cache_files_after, cache_links_after = tree_sha(cache_dir)
    cache_unchanged = (
        cache_sha_after == cache_before.get("tree_sha256")
        and cache_files_after == cache_before.get("tree_files")
        and cache_links_after == cache_before.get("tree_symlinks")
    )
    if not cache_unchanged:
        raise RuntimeError(f"vendored npm cache at {cache_dir} was modified during static checks")

    failed = [r["id"] for r in rows if not r["passed"]]
    exit_code = 0 if (rows and not failed and cache_unchanged) else 1
    report = {
        "version": "som.typescript-output-static-checks.v1",
        "issue": 75,
        "products_dir": str(products_dir.resolve()),
        "cache": {
            **cache_before,
            "tree_sha256_after": cache_sha_after,
            "unchanged": cache_unchanged,
        },
        "summary": {
            "total_products": len(rows),
            "passed_products": sum(1 for r in rows if r["passed"]),
            "failed_products": failed,
            "all_passed": exit_code == 0,
            "exit_code": exit_code,
            "seconds": round(time.time() - started, 3),
        },
        "products": rows,
    }
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    if failed:
        for r in rows:
            if not r["passed"]:
                sys.stderr.write(f"FAILED {r['id']}: {json.dumps(r['gates'], ensure_ascii=False)}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
