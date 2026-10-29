#!/usr/bin/env python3
"""Run the React product gates of toolchain.json on a fresh copy of a product and write the gate evidence.

Usage: run_gates.py <product dir> <work dir (new)> [--cache <vendored cache>]
                    [--qa-profile <sealed Linux QA profile>]

The product is copied to <work>/product without node_modules. The gates run in
toolchain.json commands order: install, typecheck, format_check, test, then
build twice into <work>/dist-1 and <work>/dist-2. Every command runs offline
against the vendored cache with npm logs under <work>/npm-logs. Writes
<work>/gates.json and prints one line per gate. Exits 0 when every gate passes.
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from common import CONTRACT, REPO
from tree_sha import tree_sha
import linux_qa_profile

TOOLCHAIN = json.loads((CONTRACT / "toolchain.json").read_text())
NODE = TOOLCHAIN["node"]["binary"]
NPM_CLI = str(pathlib.Path(TOOLCHAIN["npm"]["root"]) / "bin" / "npm-cli.js")
STEPS = ["install", "typecheck", "format_check", "test"]


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def argv(command: str, node: str | None = None, npm_cli: str | None = None) -> list[str]:
    node = NODE if node is None else node
    npm_cli = NPM_CLI if npm_cli is None else npm_cli
    words = command.split()
    if words[0] == "npm":
        return [node, npm_cli, *words[1:]]
    assert words[0] == "node", command
    return [node, *words[1:]]


def same_tree(a: pathlib.Path, b: pathlib.Path) -> bool:
    left = sorted(p.relative_to(a).as_posix() for p in a.rglob("*"))
    right = sorted(p.relative_to(b).as_posix() for p in b.rglob("*"))
    if left != right:
        return False
    return all(filecmp.cmp(a / rel, b / rel, shallow=False) for rel in left if (a / rel).is_file())


def product_files(product: pathlib.Path) -> dict[str, str]:
    """Every product file outside node_modules, by sha256."""
    return {p.relative_to(product).as_posix(): sha(p) for p in sorted(product.rglob("*"))
            if p.is_file() and "node_modules" not in p.relative_to(product).parts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("product", type=pathlib.Path)
    parser.add_argument("work", type=pathlib.Path)
    parser.add_argument("--cache", type=pathlib.Path)
    parser.add_argument("--qa-profile", type=pathlib.Path)
    args = parser.parse_args()
    qa = None
    node, npm_cli = NODE, NPM_CLI
    if args.qa_profile is not None:
        if args.cache is not None:
            parser.error("--cache cannot override the Linux QA profile")
        qa = linux_qa_profile.validate_profile(args.qa_profile)
        node, npm_cli, cache = qa['node'], qa['npm_cli'], qa['cache']
    else:
        cache = args.cache if args.cache is not None else REPO / TOOLCHAIN["vendored_cache"]["path"]
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    product = work / "product"
    shutil.copytree(args.product, product, ignore=shutil.ignore_patterns("node_modules"))
    files = product_files(product)
    userconfig = work / "empty-npmrc"
    userconfig.write_text("")
    env = {"PATH": f"{pathlib.Path(node).parent}:/usr/bin:/bin", "HOME": str(work),
           "npm_config_cache": str(cache.resolve()), "npm_config_offline": "true",
           "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_update_notifier": "false",
           "npm_config_logs_dir": str(work / "npm-logs"), "npm_config_userconfig": str(userconfig)}
    lock_before = sha(product / "package-lock.json")
    manifest_before = sha(product / "package.json")
    cache_before = tree_sha(cache)[0]
    gates: list[dict[str, Any]] = []

    def run(step: str, command: str) -> int:
        log = work / f"{step}.log"
        with log.open("w") as out:
            code = subprocess.run(argv(command, node, npm_cli), cwd=product, env=env, stdout=out, stderr=subprocess.STDOUT,
                                  timeout=600, check=False).returncode
        gates.append({"step": step, "command": command, "exit_code": code, "log": log.name})
        print(f"{step}={code}")
        return code

    for step in STEPS:
        run(step, TOOLCHAIN["commands"][step])
    template = TOOLCHAIN["commands"]["build"]
    for index in (1, 2):
        run(f"build-{index}", template.replace("<dist dir outside the product>", str(work / f"dist-{index}")))
    identical = (work / "dist-1").is_dir() and (work / "dist-2").is_dir() and same_tree(work / "dist-1",
                                                                                        work / "dist-2")
    print(f"builds_identical={identical}")
    lock_after = sha(product / "package-lock.json")
    manifest_after = sha(product / "package.json")
    lock_unchanged = lock_before == lock_after and manifest_before == manifest_after
    print(f"lock_unchanged={lock_unchanged}")
    files_after = product_files(product)
    product_unchanged = files_after == files
    print(f"product_unchanged={product_unchanged}")
    cache_after = tree_sha(cache)[0]
    prettier = tree_sha(product / "node_modules" / "prettier")[0] if (product / "node_modules" / "prettier").is_dir() \
        else None
    passed = (all(g["exit_code"] == 0 for g in gates) and identical and lock_unchanged and product_unchanged
              and cache_before == cache_after)
    qa_reason = None
    if qa is not None:
        try:
            linux_qa_profile.recheck(qa)
            linux_qa_profile.need(linux_qa_profile.safe_tree(product / "node_modules" / "prettier", False)
                                  == linux_qa_profile.PRETTIER_TREE, 'QA prettier differs')
        except (linux_qa_profile.ProfileError, OSError) as exc:
            qa_reason = str(exc)
            passed = False
    dist = {p.relative_to(work / "dist-1").as_posix(): sha(p) for p in sorted((work / "dist-1").rglob("*"))
            if p.is_file()} if (work / "dist-1").is_dir() else {}
    report = {"version": "som.react-output-gates.v1", "source": str(args.product), "product_files": files,
              "product_files_after": files_after, "product_unchanged": product_unchanged,
              "environment": {k: v for k, v in env.items() if k != "HOME"}, "gates": gates,
              "builds_identical": identical, "dist_files": dist, "lock_sha256_before": lock_before,
              "lock_sha256_after": lock_after, "lock_unchanged": lock_unchanged,
              "vendored_cache_tree_sha256_before": cache_before, "vendored_cache_tree_sha256_after": cache_after,
              "prettier_tree_sha256": prettier, "passed": passed}
    if qa is not None:
        report["qa_profile"] = {"sha256": qa["profile_sha256"], "node": node, "npm_cli": npm_cli,
                                "cache": str(cache), "recheck_passed": qa_reason is None,
                                "failure_reason": qa_reason}
    (work / "gates.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"passed={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
