#!/usr/bin/env python3
"""Provision the frozen macOS toolchain on a disposable hosted GitHub runner.

This command never runs product suites. It preserves bottle file bytes and
requires the frozen file hashes before it reports successful provisioning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PREFIX = Path("/opt/homebrew")
HEX = re.compile(r"^[0-9a-f]{64}$")
FORMULA = re.compile(r"^[a-z0-9][a-z0-9+._@-]*$")
VERSION = re.compile(r"^[0-9][A-Za-z0-9.+-]*$")
RUST_BINARIES = ("rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver")
FULL_BOTTLE_COUNT = 33
PYTHON_BINARY = "/opt/homebrew/opt/python@3.12/bin/python3.12"
PYTHON_RUNTIME_PROBE = (
    "import _decimal, _lzma, ensurepip, sqlite3, ssl, venv; from pathlib import Path; "
    "assert any((Path(ensurepip.__file__).parent / '_bundled').glob('pip-*.whl')); "
    "print('python-runtime-ok')"
)
CONTRACTS = (
    "som-rs/contracts/rust-output-v1/toolchain.json",
    "som-rs/contracts/react-output-v1/toolchain.json",
    "som-rs/contracts/typescript-output-v1/toolchain.json",
)


class ProvisionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def need(condition: Any, code: str, message: str) -> None:
    if not condition:
        raise ProvisionError(code, message)


def require_github_runner(environ: dict[str, str] | None = None, *,
                          system: str | None = None, machine: str | None = None,
                          macos_version: str | None = None) -> None:
    env = os.environ if environ is None else environ
    need(env.get("GITHUB_ACTIONS") == "true"
         and env.get("RUNNER_ENVIRONMENT") == "github-hosted"
         and env.get("RUNNER_OS") == "macOS",
         "runner_refused", "Provisioning requires a hosted macOS GitHub Actions runner.")
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    macos_version = platform.mac_ver()[0] if macos_version is None else macos_version
    need(system == "Darwin" and machine == "arm64" and macos_version.split(".")[0] == "26",
         "runner_refused", "The frozen binaries require an arm64 macOS 26 runner.")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = links = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"{relative}\0symlink:{os.readlink(path)}\n".encode())
            links += 1
        elif path.is_file():
            digest.update(f"{relative}\0{file_sha256(path)}\n".encode())
            files += 1
    return digest.hexdigest(), files, links


def blob_repository(candidate: dict[str, Any]) -> str:
    formula = candidate.get("formula")
    digest = candidate.get("archive_sha256")
    need(isinstance(formula, str) and FORMULA.fullmatch(formula)
         and isinstance(digest, str) and HEX.fullmatch(digest),
         "manifest_invalid", "A bottle identity is invalid.")
    repository = formula.replace("@", "/")
    expected = f"https://ghcr.io/v2/homebrew/core/{repository}/blobs/sha256:{digest}"
    need(candidate.get("url") == expected, "manifest_invalid", "A bottle URL is not its official digest URL.")
    return repository


def validate_manifest(document: dict[str, Any], *, expected_count: int | None = FULL_BOTTLE_COUNT) -> list[dict[str, Any]]:
    need(document.get("version") == "som.react-output-macos-bottles.v1",
         "manifest_invalid", "The bottle manifest version is invalid.")
    entries = document.get("bottles")
    need(isinstance(entries, list) and entries and document.get("bottle_count") == len(entries),
         "manifest_invalid", "The bottle count is invalid.")
    need(expected_count is None or len(entries) == expected_count,
         "manifest_invalid", "The full bottle inventory is incomplete.")
    seen = set()
    for entry in entries:
        need(isinstance(entry, dict), "manifest_invalid", "A bottle record is invalid.")
        blob_repository(entry)
        formula, version = entry["formula"], entry.get("version")
        revision, rebuild = entry.get("revision"), entry.get("bottle_rebuild")
        need(formula not in seen and isinstance(version, str) and VERSION.fullmatch(version)
             and type(revision) is int and revision >= 0 and type(rebuild) is int and rebuild >= 0,
             "manifest_invalid", "A bottle version or revision is invalid.")
        need(entry.get("target") in ("arm64_tahoe", "arm64_sequoia", "arm64_sonoma"),
             "manifest_invalid", "A bottle target is invalid.")
        seen.add(formula)
        package_version = version + (f"_{revision}" if revision else "")
        need(entry.get("expected_keg_directory") == f"/opt/homebrew/Cellar/{formula}/{package_version}"
             and entry.get("opt_symlink") == f"/opt/homebrew/opt/{formula}",
             "manifest_invalid", "A bottle destination differs from its exact keg.")
        for alternative in entry.get("alternatives", []):
            need(isinstance(alternative, dict), "manifest_invalid", "An alternative bottle is invalid.")
            need(not set(alternative) - {"bottle_rebuild", "target", "url", "archive_sha256",
                                         "archive_size_bytes", "source_evidence", "status"},
                 "manifest_invalid", "An alternative changes the bottle identity or destination.")
            blob_repository({**entry, **alternative})
    need(all(dependency in seen for entry in entries
             for dependency in entry.get("non_system_dynamic_dependencies", [])),
         "manifest_invalid", "A dynamic library dependency is missing.")
    if expected_count is not None:
        python = document.get("python")
        need("python@3.12" in seen and isinstance(python, dict)
             and python.get("version") == "3.12.11" and python.get("binary") == PYTHON_BINARY,
             "manifest_invalid", "The complete Python bottle configuration is missing.")
    return entries


class PublicBottleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected is not None and urllib.parse.urlsplit(request.full_url).hostname != urllib.parse.urlsplit(new_url).hostname:
            redirected.remove_header("Authorization")
        return redirected


def download_archive(candidate: dict[str, Any], destination: Path, *, opener=None) -> dict[str, Any]:
    repository = blob_repository(candidate)
    if opener is None:
        opener = urllib.request.build_opener(PublicBottleRedirect())
    scope = f"repository:homebrew/core/{repository}:pull"
    token_url = "https://ghcr.io/token?" + urllib.parse.urlencode({"service": "ghcr.io", "scope": scope})
    try:
        with opener.open(urllib.request.Request(token_url), timeout=60) as response:
            token_data = json.loads(response.read(1024 * 1024))
        token = token_data.get("token") if isinstance(token_data, dict) else None
        need(isinstance(token, str) and token, "download_failed", "The public bottle token is unavailable.")
        request = urllib.request.Request(candidate["url"], headers={"Authorization": f"Bearer {token}"})
        digest = hashlib.sha256()
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with opener.open(request, timeout=120) as response, destination.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except ProvisionError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        # HTTP errors can contain signed redirect URLs or headers. Do not retain them.
        raise ProvisionError("download_failed", "An official bottle download failed.") from None
    actual = digest.hexdigest()
    need(actual == candidate["archive_sha256"], "archive_hash_mismatch", "A downloaded bottle hash differs.")
    expected_size = candidate.get("archive_size_bytes")
    need(expected_size is None or size == expected_size,
         "archive_size_mismatch", "A downloaded bottle size differs.")
    return {"archive_sha256": actual, "archive_size_bytes": size}


def inside(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def extract_archive(archive: Path, staging: Path, entry: dict[str, Any]) -> Path:
    """Validate all members, then extract files before creating safe symlinks."""
    relative_keg = PurePosixPath(entry["expected_keg_directory"]).relative_to("/opt/homebrew/Cellar")
    final_keg = PurePosixPath(entry["expected_keg_directory"])
    need(not staging.exists(), "stage_exists", "A bottle staging directory already exists.")
    try:
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            paths: dict[PurePosixPath, tarfile.TarInfo] = {}
            for member in members:
                path = PurePosixPath(member.name)
                need(not path.is_absolute() and ".." not in path.parts and path.parts,
                     "archive_path_refused", "A bottle archive path is unsafe.")
                ancestor = path in relative_keg.parents and path != PurePosixPath(".")
                need(inside(path, relative_keg) or (ancestor and member.isdir()),
                     "archive_path_refused", "A bottle archive path is outside its exact keg.")
                need(path not in paths and (member.isdir() or member.isfile() or member.issym()),
                     "archive_member_refused", "A bottle member is repeated, a hard link or a special file.")
                paths[path] = member
                if member.issym():
                    target = PurePosixPath(member.linkname)
                    if target.is_absolute():
                        allowed = inside(PurePosixPath(posixpath.normpath(str(target))), final_keg)
                    else:
                        resolved = PurePosixPath(posixpath.normpath(str(path.parent / target)))
                        allowed = inside(resolved, relative_keg)
                    need(allowed, "archive_link_refused", "A bottle symlink leaves its exact keg.")
            links = {path for path, member in paths.items() if member.issym()}
            need(not any(parent in links for path in paths for parent in path.parents),
                 "archive_link_refused", "A bottle symlink is a parent of another member.")
            staging.mkdir(parents=True)
            directories = []
            for path, member in paths.items():
                target = staging.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    directories.append((target, member.mode))
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    need(source is not None, "archive_member_refused", "A bottle file has no content.")
                    with source, target.open("xb") as stream:
                        shutil.copyfileobj(source, stream)
                    target.chmod(member.mode & 0o777)
            for path in links:
                target = staging.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(paths[path].linkname)
            for directory, mode in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
                directory.chmod(mode & 0o777)
    except ProvisionError:
        raise
    except (OSError, tarfile.TarError, ValueError):
        raise ProvisionError("archive_extract_failed", "A bottle could not be staged.") from None
    result = staging.joinpath(*relative_keg.parts)
    need(result.is_dir() and not result.is_symlink(), "archive_keg_missing", "A bottle has no expected keg directory.")
    return result


def load_contracts(repo: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for relative in CONTRACTS:
        try:
            value = json.loads((repo / relative).read_bytes())
        except (OSError, ValueError):
            raise ProvisionError("contract_invalid", "A frozen toolchain contract cannot be read.") from None
        need(isinstance(value, dict), "contract_invalid", "A frozen toolchain contract is invalid.")
        result[relative] = value
    return result


def pin_specs(contracts: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    pins = []
    for relative, document in contracts.items():
        rust = document.get("rust")
        if isinstance(rust, dict):
            for name in RUST_BINARIES:
                pins.append({"label": f"rust/{name}", "path": str(PurePosixPath(rust["bin_dir"]) / name),
                             "sha256": rust[name]["sha256"]})
        node = document.get("node")
        if isinstance(node, dict):
            pins.extend(({"label": "node/binary", "path": node["binary"], "sha256": node["sha256"]},
                         {"label": "node/libnode", "path": node["libnode"]["path"], "sha256": node["libnode"]["sha256"]}))
            for link, library in node["homebrew_libraries"].items():
                label = f"node/{library['formula']}/{PurePosixPath(link).name}"
                pins.extend(({"label": label + "/resolved", "path": library["resolved"], "sha256": library["sha256"]},
                             {"label": label + "/opt", "path": link, "sha256": library["sha256"]}))
        pg = document.get("postgresql")
        if isinstance(pg, dict):
            for name in ("postgres", "initdb", "pg_ctl"):
                pins.append({"label": f"postgresql/{name}", "path": str(PurePosixPath(pg["bin_dir"]) / name),
                             "sha256": pg[name]["sha256"]})
    unique = {}
    for pin in pins:
        need(PurePosixPath(pin["path"]).is_relative_to("/opt/homebrew")
             and ".." not in PurePosixPath(pin["path"]).parts and HEX.fullmatch(pin["sha256"]),
             "contract_invalid", "A frozen file pin is invalid.")
        key = pin["path"]
        need(key not in unique or unique[key]["sha256"] == pin["sha256"],
             "contract_invalid", "Frozen contracts disagree about a file hash.")
        unique[key] = pin
    return list(unique.values())


def hash_check(pin: dict[str, str], actual_path: Path) -> dict[str, Any]:
    try:
        actual = file_sha256(actual_path) if actual_path.is_file() else None
    except OSError:
        actual = None
    return {"label": pin["label"], "path": pin["path"], "expected_sha256": pin["sha256"],
            "actual_sha256": actual, "status": "passed" if actual == pin["sha256"] else "failed"}


def stage_package(entry: dict[str, Any], out: Path, pins: list[dict[str, str]],
                  record: dict[str, Any], *, downloader=download_archive) -> tuple[dict[str, Any], Path]:
    keg = PurePosixPath(entry["expected_keg_directory"])
    relevant = [pin for pin in pins if PurePosixPath(pin["path"]).is_relative_to(keg)]
    # PostgreSQL's frozen paths go through opt; map them to the staged keg too.
    opt = PurePosixPath(entry["opt_symlink"])
    relevant += [pin for pin in pins if PurePosixPath(pin["path"]).is_relative_to(opt)]
    candidates = [entry, *({**entry, **alternative} for alternative in entry.get("alternatives", []))]
    for index, candidate in enumerate(candidates):
        attempt = {"archive_sha256": candidate["archive_sha256"], "target": candidate["target"],
                   "bottle_rebuild": candidate["bottle_rebuild"], "status": "started", "hash_checks": []}
        record["attempts"].append(attempt)
        archive = out / "archives" / f"{entry['formula']}-{index}.tar.gz"
        try:
            attempt["download"] = downloader(candidate, archive)
            # An injected downloader must not bypass the trust boundary.
            need(file_sha256(archive) == candidate["archive_sha256"],
                 "archive_hash_mismatch", "A bottle hash differs before extraction.")
            staged = extract_archive(archive, out / "stage" / f"{entry['formula']}-{index}", entry)
            for pin in relevant:
                path = PurePosixPath(pin["path"])
                relative = path.relative_to(keg if path.is_relative_to(keg) else opt)
                attempt["hash_checks"].append(hash_check(pin, staged.joinpath(*relative.parts)))
        except ProvisionError as error:
            attempt.update(status="failed", error={"code": error.code, "message": str(error)})
            raise
        if all(check["status"] == "passed" for check in attempt["hash_checks"]):
            attempt["status"] = "passed"
            record["selected_archive_sha256"] = candidate["archive_sha256"]
            record["selected_target"] = candidate["target"]
            record["selected_bottle_rebuild"] = candidate["bottle_rebuild"]
            return candidate, staged
        attempt["status"] = "frozen_hash_mismatch"
    raise ProvisionError("frozen_bottle_bytes_mismatch",
                         "Staged bottle bytes differ from frozen files. Historical relocation remains unverified.")


def remove_exact(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def replace_link(link: Path, target: Path, *, allow_directory: bool = False) -> None:
    need(allow_directory or link.is_symlink() or not link.exists(),
         "link_path_refused", "An opt link path is an existing directory.")
    remove_exact(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)


def install_closure(selections: list[tuple[dict[str, Any], Path]], prefix: Path = PREFIX) -> None:
    if prefix == PREFIX:
        require_github_runner()
    for entry, staged in selections:
        relative = PurePosixPath(entry["expected_keg_directory"]).relative_to("/opt/homebrew")
        destination = prefix.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        remove_exact(destination)
        shutil.copytree(staged, destination, symlinks=True)
        replace_link(prefix / "opt" / entry["formula"], destination)
    node = next((entry for entry, _ in selections if entry["formula"] == "node"), None)
    if node is not None:
        keg = prefix / "Cellar" / "node" / (node["version"] + (f"_{node['revision']}" if node["revision"] else ""))
        source, destination = keg / "libexec/lib/node_modules/npm", prefix / "lib/node_modules/npm"
        need(source.is_dir(), "npm_missing", "The Node bottle has no npm package.")
        remove_exact(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
        (destination / "npmrc").write_bytes(b"prefix = /opt/homebrew\n")
    pg = next((entry for entry, _ in selections if entry["formula"] == "postgresql@17"), None)
    if pg is not None:
        keg = prefix / "Cellar" / "postgresql@17" / (pg["version"] + (f"_{pg['revision']}" if pg["revision"] else ""))
        for relative in ("share/postgresql", "lib/postgresql"):
            target = keg / relative
            need(target.is_dir(), "postgresql_data_missing", "The PostgreSQL bottle lacks required data or libraries.")
            parent = "share" if relative.startswith("share/") else "lib"
            replace_link(prefix / parent / "postgresql@17", target, allow_directory=True)
    python = next((entry for entry, _ in selections if entry["formula"] == "python@3.12"), None)
    if python is not None:
        package = python["version"] + (f"_{python['revision']}" if python["revision"] else "")
        framework = prefix / "Cellar/python@3.12" / package / "Frameworks/Python.framework/Versions/3.12"
        need(framework.is_dir(), "python_framework_missing", "The Python bottle has no expected framework.")
        replace_link(prefix / "Frameworks/Python.framework/Versions/3.12", framework, allow_directory=True)


def verify_python(spec: dict[str, Any], contracts: dict[str, dict[str, Any]],
                  prefix: Path = PREFIX, *, command_runner=None) -> dict[str, Any]:
    """Run only on GitHub; fake runners and a temporary prefix support unit fixtures."""
    if prefix == PREFIX:
        require_github_runner()
    versions = {document["python"]["version"] for document in contracts.values()
                if isinstance(document.get("python"), dict)}
    need(spec.get("binary") == PYTHON_BINARY and spec.get("version") == "3.12.11"
         and versions == {"3.12.11"}, "contract_invalid", "The Python version pin is invalid.")
    binary = prefix / "opt/python@3.12/bin/python3.12"
    need(binary.is_file(), "python_binary_missing", "The installed Python executable is missing.")
    command_runner = subprocess.run if command_runner is None else command_runner
    result = {"status": "failed", "binary": PYTHON_BINARY, "expected_version": "Python 3.12.11",
              "binary_sha256": None, "venv_creation": "not_run", "checks": []}
    commands = (("version", [str(binary), "--version"], "Python 3.12.11"),
                ("stdlib_and_bundled_ensurepip", [str(binary), "-I", "-c", PYTHON_RUNTIME_PROBE], "python-runtime-ok"))
    for label, command, expected in commands:
        try:
            done = command_runner(command, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            result["checks"].append({"label": label, "status": "failed", "reason": "Python check could not run."})
            return result
        output = (done.stdout or done.stderr or "").strip()
        passed = done.returncode == 0 and output == expected
        check = {"label": label, "status": "passed" if passed else "failed", "exit_code": done.returncode}
        if label == "version" and re.fullmatch(r"Python [0-9]+\.[0-9]+\.[0-9]+", output):
            check["actual_version"] = output
        result["checks"].append(check)
        if not passed:
            return result
    result["status"] = "passed"
    return result


def verify_installed(contracts: dict[str, dict[str, Any]], pins: list[dict[str, str]],
                     prefix: Path = PREFIX) -> list[dict[str, Any]]:
    checks = [hash_check(pin, prefix.joinpath(*PurePosixPath(pin["path"]).relative_to("/opt/homebrew").parts))
              for pin in pins]
    for document in contracts.values():
        npm = document.get("npm")
        if isinstance(npm, dict):
            root = prefix.joinpath(*PurePosixPath(npm["root"]).relative_to("/opt/homebrew").parts)
            actual = tree_sha256(root) if root.is_dir() else None
            expected = (npm["tree_sha256"], npm["tree_files"], npm["tree_symlinks"])
            checks.append({"label": "npm/tree", "path": npm["root"], "expected": list(expected),
                           "actual": list(actual) if actual is not None else None,
                           "status": "passed" if actual == expected else "failed"})
    return checks


def provision(document: dict[str, Any], contracts: dict[str, dict[str, Any]], out: Path,
              report: dict[str, Any], *, prefix: Path = PREFIX, downloader=download_archive,
              expected_count: int | None = FULL_BOTTLE_COUNT, python_runner=None) -> None:
    if prefix == PREFIX:
        require_github_runner()
    entries = validate_manifest(document, expected_count=expected_count)
    pins = pin_specs(contracts)
    selections = []
    report["stage"] = "download_and_stage"
    for entry in entries:
        record = {key: entry[key] for key in ("formula", "version", "revision", "expected_keg_directory")}
        record["attempts"] = []
        report["packages"].append(record)
        selections.append(stage_package(entry, out, pins, record, downloader=downloader))
    report["stage"] = "install"
    install_closure(selections, prefix)
    report["stage"] = "frozen_hashes"
    report["hash_checks"] = verify_installed(contracts, pins, prefix)
    need(report["hash_checks"] and all(check["status"] == "passed" for check in report["hash_checks"]),
         "installed_frozen_hash_mismatch", "Installed files differ from the frozen toolchain.")
    if isinstance(document.get("python"), dict):
        report["stage"] = "python_runtime"
        report["python"] = verify_python(document["python"], contracts, prefix, command_runner=python_runner)
        need(report["python"]["status"] == "passed", "python_runtime_mismatch",
             "The installed Python version or bundled standard library check failed.")
    report.update(status="passed", stage="complete")


def new_report() -> dict[str, Any]:
    return {"version": "som.react-output-macos-provision.v1", "status": "failed",
            "product_suites": "not_run", "stage": "guard", "packages": [], "hash_checks": [],
            "python": {"status": "not_run", "venv_creation": "not_run"},
            "relocation": "No binary relocation or signing is performed. Frozen file hashes decide whether raw bottle bytes are usable."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="A new provisioning log directory")
    parser.add_argument("--manifest", type=Path, default=HERE / "macos_bottles.json")
    args = parser.parse_args(argv)
    report = new_report()
    output = None
    code = 1
    try:
        require_github_runner()
        need(not args.out.exists() and not args.out.is_symlink(),
             "output_exists", "The provisioning output directory already exists.")
        args.out.mkdir(parents=True)
        output = args.out / "report.json"
        document = json.loads(args.manifest.read_bytes())
        need(isinstance(document, dict), "manifest_invalid", "The bottle manifest is invalid.")
        contracts = load_contracts(REPO)
        report["manifest_sha256"] = file_sha256(args.manifest)
        report["contract_sha256"] = {relative: file_sha256(REPO / relative) for relative in CONTRACTS}
        provision(document, contracts, args.out, report)
        code = 0
    except ProvisionError as error:
        report["error"] = {"code": error.code, "message": str(error)}
    except Exception as error:
        report["error"] = {"code": "provision_failed", "message": "Provisioning failed. No product suite ran.",
                           "exception_type": type(error).__name__}
    finally:
        if output is not None:
            try:
                output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except OSError:
                code = 1
                report.update(status="failed", error={"code": "report_write_failed",
                                                      "message": "The provisioning report could not be written."})
    print(json.dumps({"status": report["status"], "stage": report["stage"],
                      "code": report.get("error", {}).get("code"),
                      "packages": len(report["packages"]), "product_suites": report["product_suites"]}))
    return code


if __name__ == "__main__":
    sys.exit(main())
