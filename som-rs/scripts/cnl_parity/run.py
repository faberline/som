"""Replay a fixed CNL corpus against retained and candidate SOM source trees."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from compare import check_original, compare
from corpus import build
from http_cli import check as check_http_cli


HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1] / "som-targets/tests/cnl_parity.rs"


def tool_identity(command):
    path = Path(shutil.which(str(command)) or command).resolve()
    if path.is_dir():
        files = {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
                 for file in sorted(path.rglob("*")) if file.is_file()}
        package = json.loads((path / "package.json").read_bytes())
        return {"path": str(path), "version": package["version"], "files": files}
    result = subprocess.run([str(path), "--version"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=15)
    assert result.returncode == 0, result.stdout.decode(errors="replace")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version": result.stdout.decode().strip()}


def run_one(label, root, output, corpus, args, baseline_bundles=None):
    environment = os.environ.copy()
    environment.update({
        "CARGO_BUILD_JOBS": environment.get("CARGO_BUILD_JOBS", "2"),
        "RUST_TEST_THREADS": environment.get("RUST_TEST_THREADS", "2"),
        "CARGO_TARGET_DIR": str(args.target_dir / label),
        "SOM_PARITY_CORPUS": str(corpus),
        "SOM_PARITY_REPORT": str(output / f"{label}.json"),
    })
    for name in ("SOM_PARITY_CASE_PREFIX", "SOM_PARITY_PARSE_ONLY"):
        environment.pop(name, None)
    if args.cargo_home:
        environment["CARGO_HOME"] = str(args.cargo_home)
    if args.parse_only:
        environment["SOM_PARITY_PARSE_ONLY"] = "1"
    else:
        environment.update({
            "SOM_PARITY_ASSETS": str(output / f"{label}-assets"),
            "SOM_PARITY_RUFF": str(args.ruff),
            "SOM_TS_PRETTIER": str(args.prettier),
        })
    manifest = root / "som-rs/som-targets/Cargo.toml"
    # Keep the compiler source snapshot intact. This temporary test is identical
    # for both builds and is removed even if cargo fails.
    test = manifest.parent / "tests/cnl_parity_acceptance.rs"
    if test.exists():
        raise FileExistsError(f"Refuse to overwrite {test}")
    test.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HARNESS, test)
    command = [args.cargo, "test", "--locked", "--manifest-path", str(manifest),
               "--test", "cnl_parity_acceptance", "--", "--ignored"]
    if args.offline:
        command.insert(3, "--offline")
    try:
        with (output / f"{label}.log").open("wb") as log:
            result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
    finally:
        test.unlink()
    (output / f"{label}-command.json").write_text(json.dumps({
        "argv": command, "returncode": result.returncode,
        "source_root": str(root), "target_dir": environment["CARGO_TARGET_DIR"],
    }, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(f"{label} failed; read {output / f'{label}.log'}")
    if not args.parse_only:
        build_command = [args.cargo, "build", "--locked", "--manifest-path", str(manifest),
                         "--bin", "som"]
        if args.offline:
            build_command.insert(3, "--offline")
        with (output / f"{label}-build.log").open("wb") as log:
            subprocess.run(build_command, env=environment, stdout=log, stderr=subprocess.STDOUT,
                           check=True)
        binary = args.target_dir / label / "debug/som"
        check_http_cli(binary, output / f"{label}-assets", json.loads(corpus.read_bytes()),
                       json.loads((output / f"{label}.json").read_bytes()),
                       output / f"{label}-http-cli", environment, baseline_bundles)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--corpus", type=Path, help="Use an already frozen corpus")
    parser.add_argument("--baseline-report", type=Path, help="Reuse an immutable baseline report")
    parser.add_argument("--baseline-bundles", type=Path,
                        help="Saved old compiler bundles; required with --baseline-report for a full run")
    parser.add_argument("--original-report", type=Path,
                        help="Require the original 178 records to match this retained report")
    parser.add_argument("--parse-only", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cargo", default=shutil.which("cargo") or "cargo")
    parser.add_argument("--cargo-home", type=Path)
    parser.add_argument("--target-dir", type=Path)
    parser.add_argument("--ruff", type=Path, default=os.environ.get("SOM_PARITY_RUFF"))
    parser.add_argument("--prettier", type=Path, default=os.environ.get("SOM_TS_PRETTIER"),
                        help="Directory of the frozen Prettier package")
    args = parser.parse_args()
    args.baseline = args.baseline.resolve()
    args.candidate = args.candidate.resolve()
    if args.baseline == args.candidate:
        parser.error("baseline and candidate must be distinct source trees")
    if not args.parse_only and (not args.ruff or not args.prettier):
        parser.error("full comparison requires --ruff and --prettier")
    if not args.parse_only and args.baseline_report and not args.baseline_bundles:
        parser.error("full comparison with --baseline-report requires --baseline-bundles")
    baseline_bundles = args.baseline_bundles.resolve() if args.baseline_bundles else None
    if not args.parse_only and baseline_bundles:
        for target in ("python", "rust", "typescript", "react"):
            if not (baseline_bundles / f"{target}.bundle.json").is_file():
                parser.error(f"baseline bundle is missing: {baseline_bundles / f'{target}.bundle.json'}")
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="som-cnl-parity-"))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    args.target_dir = (args.target_dir or output / "target").resolve()
    print(output, flush=True)
    tool_commands = {"cargo": args.cargo, "rustc": "rustc"}
    if not args.parse_only:
        tool_commands.update({"ruff": args.ruff, "prettier": args.prettier,
                              "rustfmt": "rustfmt", "node": "node"})
    (output / "tools.json").write_text(json.dumps({
        "tools": {name: tool_identity(command) for name, command in tool_commands.items()},
        "harness_sha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }, indent=2, sort_keys=True) + "\n")
    corpus_path = output / "corpus.json"
    if args.corpus:
        shutil.copyfile(args.corpus, corpus_path)
    else:
        corpus_path.write_text(json.dumps(build(args.baseline / "som-rs"),
                                         ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    corpus = json.loads(corpus_path.read_bytes())
    if args.baseline_report:
        shutil.copyfile(args.baseline_report, output / "baseline.json")
    else:
        run_one("baseline", args.baseline, output, corpus_path, args)
        if not args.parse_only and baseline_bundles is None:
            baseline_bundles = output / "baseline-http-cli"
    run_one("candidate", args.candidate, output, corpus_path, args, baseline_bundles)
    before = json.loads((output / "baseline.json").read_bytes())
    after = json.loads((output / "candidate.json").read_bytes())
    summary = compare(corpus, before, after, args.parse_only)
    if args.original_report:
        original_bytes = args.original_report.read_bytes()
        retained = json.loads(original_bytes)
        original_differences = {
            "baseline": check_original(corpus, before, retained, args.parse_only),
            "candidate": check_original(corpus, after, retained, args.parse_only),
        }
        summary["original_reference_differences"] = original_differences
        summary["original_reference_sha256"] = hashlib.sha256(original_bytes).hexdigest()
        summary["passed"] &= not any(original_differences.values())
    summary["sha256"] = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in ("corpus.json", "baseline.json", "candidate.json")
    }
    (output / "comparison.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"Compared {summary['expected_records']} records; "
          f"{len(summary['differences'])} differences; "
          f"{len(summary['unexpected_outcomes'])} unexpected outcomes.")
    for difference in summary["differences"][:20]:
        print(difference)
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
