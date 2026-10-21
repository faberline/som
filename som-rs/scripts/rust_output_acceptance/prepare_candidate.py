#!/usr/bin/env python3
"""Build and start one recorded som-targets candidate for the #50 acceptance run.

The candidate directory gets the layout the compiler acceptance tools bind:
source.json, source.tar, build.json, som, assets/ and runtime.json. The build
uses a fresh target directory and the pinned toolchain. The candidate serves on
a QA port and never on 8765 or 18765. Nothing in the repository is written.

With --package-assets FORMATTER the asset package is built from the same commit
by som-compiler-assets (som-rs, pinned toolchain) with that native formatter,
instead of copied from som-rs/compiler-assets/current. A compiler-v1 amendment
that changes the Python asset descriptors needs this (#57).

With --prettier PATH the TypeScript prettier package directory is passed via
SOM_TS_PRETTIER to cargo test, cargo build and som serve, and its resolved path
and tree SHA-256 are recorded in build.json and runtime.json (#97).

usage: prepare_candidate.py --out <new candidate dir> --port <QA port> [--package-assets FORMATTER] [--prettier PATH]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[3]
SOM_RS = REPO / "som-rs"
TARGETS = SOM_RS / "som-targets"
TOOLCHAIN = json.loads((SOM_RS / "contracts/rust-output-v1/toolchain.json").read_text())
BIN_DIR = pathlib.Path(TOOLCHAIN["rust"]["bin_dir"])
SOURCE_ROOTS = ["som-rs/Cargo.toml", "som-rs/Cargo.lock", "som-rs/src", "som-rs/contracts",
                "som-rs/som-targets/Cargo.toml", "som-rs/som-targets/Cargo.lock",
                "som-rs/som-targets/src", "som-rs/som-targets/catalog"]
FORBIDDEN_PORTS = {8765, 18765}


def sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prettier_tree_sha(root: pathlib.Path) -> str:
    """Compute the TypeScript contract tree SHA-256 of a directory (tree_sha.py)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"{rel}\0symlink:{os.readlink(path)}\n".encode())
        elif path.is_file():
            digest.update(f"{rel}\0{sha(path)}\n".encode())
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def tree_state(paths: list[str]) -> dict:
    """HEAD and the status of the source paths. Tools outside them may be new."""
    return {"head": git("rev-parse", "HEAD").strip(),
            "porcelain": git("status", "--porcelain", "--untracked-files=all", "--", *paths)}


def pinned_env(target_dir: pathlib.Path, prettier: pathlib.Path | None = None) -> dict:
    for name in ("rustc", "cargo"):
        if sha(BIN_DIR / name) != TOOLCHAIN["rust"][name]["sha256"]:
            raise SystemExit(f"{name} differs from toolchain.json")
    env = {**os.environ, "PATH": f"{BIN_DIR}:{os.environ['PATH']}", "RUSTC": str(BIN_DIR / "rustc"),
           "CARGO": str(BIN_DIR / "cargo"), "CARGO_TARGET_DIR": str(target_dir), "CARGO_NET_OFFLINE": "true"}
    if prettier is not None:
        env["SOM_TS_PRETTIER"] = str(prettier)
    return env


def timed(command: list[str], env: dict, out: pathlib.Path, index: int, cwd: pathlib.Path = TARGETS) -> dict:
    """Run one build command under /usr/bin/time -l and keep its logs."""
    started = time.monotonic()
    with (out / f"build-{index}.stdout").open("wb") as stdout, (out / f"build-{index}.stderr").open("wb") as stderr:
        code = subprocess.run(["/usr/bin/time", "-l", *command], cwd=cwd, env=env,
                              stdout=stdout, stderr=stderr).returncode
    seconds = time.monotonic() - started
    tail = (out / f"build-{index}.stderr").read_text(errors="replace")
    rss = re.search(r"(\d+)\s+maximum resident set size", tail)
    return {"command": command, "cwd": str(cwd.relative_to(REPO)), "exit_code": code, "seconds": seconds,
            "max_rss_bytes": int(rss.group(1)) if rss else None}


def dep_info_files(dep_file: pathlib.Path) -> list[str]:
    """Repository files the compiler read, from the cargo dep-info of the binary."""
    text = dep_file.read_text().replace("\\\n", " ")
    first = text.splitlines()[0]
    _, _, deps = first.partition(": ")
    files = set()
    for raw in re.split(r"(?<!\\) ", deps):
        raw = raw.replace("\\ ", " ").strip()
        if not raw:
            continue
        path = pathlib.Path(os.path.normpath(raw))
        if path.is_relative_to(REPO) and not path.is_relative_to(dep_file.parents[2]):
            files.add(str(path.relative_to(REPO)))
    return sorted(files)


def wait_health(port: int, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"candidate exited with {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                return json.loads(response.read())
        except OSError:
            time.sleep(0.1)
    raise SystemExit("candidate did not become healthy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--package-assets", type=pathlib.Path, metavar="FORMATTER")
    parser.add_argument("--prettier", type=pathlib.Path, metavar="PATH")
    args = parser.parse_args(argv)
    prettier: pathlib.Path | None = None
    if args.prettier is not None:
        if not args.prettier.is_dir():
            print(f"prettier not found: {args.prettier}", file=sys.stderr)
            return 2
        prettier = args.prettier.resolve()
    formatter = args.package_assets.resolve() if args.package_assets else None
    if formatter and not formatter.is_file():
        raise SystemExit(f"formatter not found: {formatter}")
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"preserve existing candidate: {out}")
    if args.port in FORBIDDEN_PORTS:
        raise SystemExit("8765 and 18765 are not QA ports")
    if subprocess.run(["lsof", "-nP", f"-iTCP:{args.port}", "-sTCP:LISTEN", "-t"], capture_output=True).stdout:
        raise SystemExit(f"port {args.port} is in use")
    before = tree_state(SOURCE_ROOTS)
    if before["porcelain"]:
        raise SystemExit("source roots are not clean")
    out.mkdir(parents=True)
    target_dir = out / "target"
    env = pinned_env(target_dir, prettier)
    som_ts_prettier = str(prettier) if prettier is not None else os.environ.get("SOM_TS_PRETTIER")
    prettier_dir = prettier if prettier is not None else (pathlib.Path(som_ts_prettier) if som_ts_prettier else None)
    prettier_sha256 = prettier_tree_sha(prettier_dir.resolve()) if prettier_dir and prettier_dir.is_dir() else None

    commands = [
        [str(BIN_DIR / "cargo"), "test", "--locked", "--offline"],
        [str(BIN_DIR / "cargo"), "build", "--release", "--locked", "--offline", "--bin", "som"],
    ]
    rows = []
    for index, command in enumerate(commands):
        rows.append(timed(command, env, out, index))
        if rows[-1]["exit_code"] != 0:
            break
    packager = None
    if formatter and len(rows) == len(commands) and rows[-1]["exit_code"] == 0:
        packager_env = {**env, "CARGO_TARGET_DIR": str(out / "target-assets")}
        command = [str(BIN_DIR / "cargo"), "build", "--release", "--locked", "--offline", "--bin", "som-compiler-assets"]
        commands.append(command)
        rows.append(timed(command, packager_env, out, len(rows), SOM_RS))
        packager = out / "target-assets/release/som-compiler-assets"
    complete = len(rows) == len(commands) and all(row["exit_code"] == 0 for row in rows)
    built = target_dir / "release/som"
    read_files = dep_info_files(target_dir / "release/som.d") if complete else []
    before_reads = {"head": before["head"], "porcelain": git("status", "--porcelain", "--", *read_files)} if read_files else None
    after = tree_state(SOURCE_ROOTS)
    tracked = set(git("ls-files", "-z", "--", *SOURCE_ROOTS).split("\0")) - {""}
    untracked_reads = sorted(set(read_files) - set(git("ls-files", "-z", "--", *read_files).split("\0")))
    files = {relative: sha(REPO / relative) for relative in sorted(tracked | set(read_files))}
    source = {"version": "som.compiler-candidate-source.v1",
              "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
              "commit": before["head"], "roots": SOURCE_ROOTS, "dep_info_files": read_files,
              "files": files}
    tar = out / "source.tar"
    subprocess.run(["git", "archive", "--format=tar", "-o", str(tar), before["head"], "--", *files],
                   cwd=REPO, check=True)
    source["archive_sha256"] = sha(tar)
    (out / "source.json").write_text(json.dumps(source, indent=1, sort_keys=True) + "\n")

    unchanged = (before == after and not untracked_reads and not (before_reads or {}).get("porcelain")
                 and all(sha(REPO / relative) == digest for relative, digest in files.items()))
    binary = out / "som"
    if complete:
        shutil.copy2(built, binary)
    if formatter:
        assets = formatter
        if complete:
            packaged = subprocess.run([str(packager), "--destination", str(out / "assets"), "--formatter", str(formatter)],
                                      cwd=REPO, capture_output=True)
            (out / "assets-package.stdout").write_bytes(packaged.stdout)
            (out / "assets-package.stderr").write_bytes(packaged.stderr)
            complete = packaged.returncode == 0
        if not complete:
            print(json.dumps({"complete": False, "stage": "assets"}))
            return 1
    else:
        assets = (SOM_RS / "compiler-assets/current").resolve()
        shutil.copytree(assets, out / "assets", symlinks=False)
    build = {"version": "som.rust-output-candidate-build.v1", "commands": rows, "complete": complete,
             "toolchain": {name: TOOLCHAIN["rust"][name]["sha256"] for name in ("rustc", "cargo")},
             "tree_before": before, "tree_after": after, "read_files_status": before_reads, "untracked_reads": untracked_reads,
             "source_unchanged": unchanged, "source_sha256": sha(out / "source.json"),
             "binary_sha256": sha(binary) if complete else None,
             "binary_bytes": binary.stat().st_size if complete else None,
             "assets_from": str(assets), "asset_manifest_sha256": sha(out / "assets/manifest.json"),
             "assets_packaged": None if not formatter else {
                 "packager_sha256": sha(packager), "formatter": str(formatter), "formatter_sha256": sha(formatter),
                 "files": {p.name: sha(p) for p in sorted((out / "assets").iterdir()) if p.is_file()}},
             "som_ts_prettier": som_ts_prettier,
             "prettier_tree_sha256": prettier_sha256}
    (out / "build.json").write_text(json.dumps(build, indent=1, sort_keys=True) + "\n")
    if not (complete and unchanged):
        print(json.dumps({"complete": complete, "source_unchanged": unchanged,
                          "untracked_reads": untracked_reads}))
        return 1

    serve_env = {**os.environ, "SOM_TS_PRETTIER": str(prettier)} if prettier is not None else dict(os.environ)
    argv = [str(binary), "serve", "--assets", str(out / "assets"), "--port", str(args.port)]
    with (out / "server.log").open("ab") as log:
        process = subprocess.Popen(argv, cwd=out, env=serve_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    started_at = datetime.datetime.now(datetime.UTC).isoformat()
    health = wait_health(args.port, process)
    runtime = {"pid": process.pid, "argv": argv, "port": args.port, "binary_sha256": build["binary_sha256"],
               "source_sha256": build["source_sha256"], "som_ts_prettier": som_ts_prettier,
               "prettier_tree_sha256": prettier_sha256, "started_at": started_at, "accepted": False,
               "health": health}
    (out / "runtime.json").write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pid": process.pid, "port": args.port, "binary_sha256": build["binary_sha256"],
                      "source_files": len(files), "seconds": [row["seconds"] for row in rows]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
