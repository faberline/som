"""Run local CNL acceptance with a new cache and anonymous public sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
BUILD_DIRECTORIES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "target",
    "compiler-assets", "build", "dist",
}
LOCAL_RUST_DIRECTORIES = {"models", "runtime", "evals"}
QUALITY_FIXTURES = ("quality.json", "ruff", "pyproject.toml")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_path(path, omit_vendor=False):
    if any(part in BUILD_DIRECTORIES for part in path.parts):
        return False
    if path.parts[:1] == ("som-rs",) and len(path.parts) > 1:
        if path.parts[1] in LOCAL_RUST_DIRECTORIES:
            return False
        if omit_vendor and path.parts[1] == "vendor":
            return False
    return path.suffix not in {".pyc", ".pyo"}


def source_files(root, omit_vendor=False):
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == root:
        listed = subprocess.check_output([
            "git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z",
        ])
        paths = {Path(os.fsdecode(name)) for name in listed.split(b"\0") if name}
        kind = "git"
    else:
        paths = set()
        for directory, children, files in os.walk(root):
            relative = Path(directory).relative_to(root)
            children[:] = [name for name in children if source_path(relative / name, omit_vendor)]
            paths.update(relative / name for name in files)
        kind = "snapshot"
    return kind, sorted(path for path in paths
                        if source_path(path, omit_vendor) and (root / path).is_file())


def inventory(root, omit_vendor=False):
    _, files = source_files(root, omit_vendor)
    return {str(path): digest(root / path) for path in files}


def snapshot(source, destination):
    kind, files = source_files(source, omit_vendor=True)
    destination.mkdir()
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    return kind


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path,
                        help="Source repository or frozen source copy; built in an isolated snapshot")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--original-report", required=True, type=Path)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--baseline-bundles", type=Path,
                        help="Saved old compiler bundles for a reused baseline report")
    parser.add_argument("--ruff", required=True, type=Path)
    parser.add_argument("--prettier", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if args.baseline == args.candidate:
        parser.error("baseline and candidate must be separate source trees")
    if any(args.output.is_relative_to(root) for root in (args.baseline, args.candidate)):
        parser.error("output must be outside the baseline and candidate source trees")
    for root in (args.baseline, args.candidate):
        if not (root / "som-rs/Cargo.toml").is_file():
            parser.error(f"SOM source manifest is missing under {root}")
    fixture_root = args.baseline / "som-rs/models/composed-quality-candidate"
    for name in QUALITY_FIXTURES:
        if not (fixture_root / name).is_file():
            parser.error(f"baseline quality fixture is missing: {fixture_root / name}")
    args.output.mkdir(parents=True, exist_ok=False)
    print(args.output, flush=True)
    before = {name: inventory(getattr(args, name), omit_vendor=name == "candidate")
              for name in ("baseline", "candidate")}
    (args.output / "source-before.json").write_text(json.dumps(before, sort_keys=True, indent=2) + "\n")
    candidate = args.output / "candidate"
    source_kind = snapshot(args.candidate, candidate)
    snapshot_before = inventory(candidate)
    assert snapshot_before == before["candidate"], "candidate changed while copying its source"
    fixture_target = candidate / "som-rs/models/composed-quality-candidate"
    fixture_target.mkdir(parents=True)
    fixture_hashes = {name: digest(fixture_root / name) for name in QUALITY_FIXTURES}
    for name in QUALITY_FIXTURES:
        shutil.copy2(fixture_root / name, fixture_target / name)
        assert digest(fixture_target / name) == fixture_hashes[name]
    provenance = {
        "baseline": str(args.baseline), "candidate_source": str(args.candidate),
        "candidate_source_kind": source_kind, "candidate_snapshot": str(candidate),
        "snapshot_files": snapshot_before, "quality_fixture_source": str(fixture_root),
        "quality_fixture_files": fixture_hashes,
    }
    (args.output / "source-snapshot.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n")

    # Do not inherit credentials, Git helpers, Cargo config, or registry tokens.
    environment = {name: os.environ[name] for name in ("PATH", "TMPDIR", "LANG")
                   if name in os.environ}
    environment.update({
        "CARGO_HOME": str(args.output / "cargo-home"),
        "CARGO_TARGET_DIR": str(args.output / "target/candidate"),
        "CARGO_BUILD_JOBS": "2", "RUST_TEST_THREADS": "2",
        "CARGO_NET_GIT_FETCH_WITH_CLI": "true", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "", "SOM_TS_PRETTIER": str(args.prettier),
    })
    (args.output / "environment.json").write_text(json.dumps(environment, sort_keys=True, indent=2) + "\n")
    commands = []

    def run(label, command):
        print(label, flush=True)
        with (args.output / f"{label}.log").open("wb") as log:
            result = subprocess.run([str(part) for part in command], env=environment,
                                    cwd=args.output, stdout=log, stderr=subprocess.STDOUT)
        commands.append({"label": label, "argv": [str(part) for part in command],
                         "returncode": result.returncode})
        (args.output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(f"{label} failed; read {args.output / (label + '.log')}")

    manifests = [candidate / "som-rs/Cargo.toml",
                 candidate / "som-rs/som-targets/Cargo.toml"]
    dependency = next(line for line in manifests[0].read_text().splitlines()
                      if line.startswith("faberline-nlp-cnl ="))
    revision = re.search(r'rev = "([0-9a-f]{40})"', dependency).group(1)
    source = f"git+https://github.com/faberline/nlp.git?rev={revision}#{revision}"
    assert 'git = "https://github.com/faberline/nlp.git"' in dependency
    for manifest in manifests:
        lock = manifest.with_name("Cargo.lock").read_text()
        package = next(block for block in lock.split("[[package]]")
                       if 'name = "faberline-nlp-cnl"' in block)
        assert f'source = "{source}"' in package
    rust = subprocess.check_output(["rustc", "-vV"], env=environment).decode()
    host = next(line.split(": ", 1)[1] for line in rust.splitlines() if line.startswith("host: "))
    (args.output / "rustc.txt").write_text(rust)
    assert not Path(environment["CARGO_HOME"]).exists()
    for index, manifest in enumerate(manifests):
        run(f"fetch-{index}", ["cargo", "fetch", "--locked", "--manifest-path", manifest,
                               "--target", host])

    metadata = json.loads(subprocess.check_output([
        "cargo", "metadata", "--locked", "--offline", "--format-version", "1",
        "--filter-platform", host, "--manifest-path", str(manifests[0]),
    ], env=environment, cwd=args.output))
    nlp = next(package for package in metadata["packages"] if package["name"] == "faberline-nlp-cnl")
    assert nlp["source"] == source
    nlp_crate = Path(nlp["manifest_path"]).parent
    head = subprocess.check_output(["git", "-C", str(nlp_crate), "rev-parse", "HEAD"],
                                   env=environment).decode().strip()
    assert head == revision
    # Build this package without the NLP model workspace.
    standalone = args.output / "standalone-cnl"
    shutil.copytree(nlp_crate, standalone)
    run("cnl-lock", ["cargo", "generate-lockfile", "--offline", "--manifest-path",
                     standalone / "Cargo.toml"])
    run("cnl-tests", ["cargo", "test", "--locked", "--offline", "--manifest-path",
                      standalone / "Cargo.toml"])
    assets = candidate / "som-rs/compiler-assets/current"
    run("assets", ["cargo", "run", "--locked", "--offline", "--manifest-path", manifests[0],
                   "--bin", "som-compiler-assets", "--", "--destination", assets,
                   "--formatter", args.ruff])
    backend = json.loads((assets / "manifest.json").read_bytes())["backend"]["files"]
    prefix = "dependencies/faberline-nlp-cnl/"
    embedded = {name[len(prefix):]: value for name, value in backend.items() if name.startswith(prefix)}
    actual = {str(path.relative_to(nlp_crate)): digest(path)
              for path in sorted((nlp_crate / "src").rglob("*")) if path.is_file()}
    actual["Cargo.toml"] = digest(nlp_crate / "Cargo.toml")
    assert embedded == actual, "NLP embedded sources differ from the pinned public source"
    (args.output / "dependency-source.json").write_text(json.dumps({
        "revision": revision, "source": source, "files": actual,
        "standalone_lock_sha256": digest(standalone / "Cargo.lock"),
    }, indent=2, sort_keys=True) + "\n")
    for index, manifest in enumerate(manifests):
        run(f"som-tests-{index}", ["cargo", "test", "--locked", "--offline", "--manifest-path", manifest])

    parity = [sys.executable, HERE / "run.py", "--baseline", args.baseline,
              "--candidate", candidate, "--corpus", args.corpus,
              "--original-report", args.original_report, "--ruff", args.ruff,
              "--prettier", args.prettier, "--cargo-home", environment["CARGO_HOME"],
              "--target-dir", args.output / "target", "--output", args.output / "parity", "--offline"]
    if args.baseline_report:
        parity.extend(["--baseline-report", args.baseline_report])
    if args.baseline_bundles:
        parity.extend(["--baseline-bundles", args.baseline_bundles])
    run("parity", parity)
    for name, files in before.items():
        root = getattr(args, name)
        assert inventory(root, omit_vendor=name == "candidate") == files, \
            f"{name} source changed during acceptance"
    assert inventory(candidate) == snapshot_before, "candidate snapshot sources changed during acceptance"
    for name, sha in fixture_hashes.items():
        assert digest(fixture_root / name) == sha, f"baseline quality fixture changed: {name}"
        assert digest(fixture_target / name) == sha, f"candidate quality fixture changed: {name}"
    result = {"passed": True, "revision": revision,
              "comparison": json.loads((args.output / "parity/comparison.json").read_bytes()),
              "candidate_source": str(args.candidate), "candidate_snapshot": str(candidate),
              "source_unchanged": True, "anonymous_fetch": True,
              "offline_build_and_tests": True}
    (args.output / "acceptance.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("Local CNL acceptance passed.", flush=True)


if __name__ == "__main__":
    main()
