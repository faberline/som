#!/usr/bin/env python3
"""Provision hash-checked public tools on a GitHub Linux runner."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

HOST = "x86_64-unknown-linux-gnu"
REQUIRED = {"ruff", "rustfmt", "node", "prettier"}
PUBLISHERS = {"files.pythonhosted.org", "static.rust-lang.org", "nodejs.org", "registry.npmjs.org"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024


class Refusal(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Refusal(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def member_path(name: object) -> str:
    require(isinstance(name, str) and name, "archive path must be a string")
    name = name.removesuffix("/")
    require(
        name and not PurePosixPath(name).is_absolute() and "\\" not in name
        and all(part not in {"", ".", ".."} for part in name.split("/"))
        and not any(c in name for c in "\r\n\0"),
        "unsafe archive member path",
    )
    return name


def no_symlinks(path: Path) -> None:
    require(all(not item.is_symlink() for item in (path, *path.parents)), "symlink path refused")


def public_url(value: object) -> str:
    require(isinstance(value, str), "artifact URL must be a string")
    parsed = urlsplit(value)
    require(
        parsed.scheme == "https" and parsed.hostname in PUBLISHERS
        and not parsed.username and not parsed.password and parsed.port is None
        and not parsed.query and not parsed.fragment,
        "artifact URL must name an approved HTTPS publisher",
    )
    return value


def validate_manifest(data: object) -> dict:
    require(isinstance(data, dict) and data.get("version") == "som.native-tools.v1", "unsupported manifest")
    rows = data.get("tools")
    require(isinstance(rows, list) and len(rows) == len(REQUIRED), "all four native tool records are required")
    tools = {}
    for row in rows:
        require(isinstance(row, dict), "tool record must be an object")
        name = row.get("tool")
        require(isinstance(name, str) and name in REQUIRED and name not in tools, "unknown or duplicate tool")
        require(isinstance(row.get("version"), str) and row["version"], "tool version is required")
        require(row.get("host") == (HOST if name != "prettier" else "platform-independent JavaScript"), "wrong native artifact host")
        public_url(row.get("url"))
        require(isinstance(row.get("archive_sha256"), str) and SHA.fullmatch(row["archive_sha256"]), "invalid archive digest")
        licenses = row.get("licenses")
        require(isinstance(licenses, dict) and licenses, "license inventory is required")
        for path, sha in licenses.items():
            member_path(path)
            require(isinstance(sha, str) and SHA.fullmatch(sha), "invalid license digest")
        if name == "prettier":
            require(isinstance(row.get("tree_sha256"), str) and SHA.fullmatch(row["tree_sha256"]), "Prettier tree digest is required")
        else:
            member_path(row.get("executable_member"))
            require(isinstance(row.get("executable_sha256"), str) and SHA.fullmatch(row["executable_sha256"]), "invalid executable digest")
        tools[name] = row
    return tools


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(row: dict) -> bytes:
    request = Request(public_url(row["url"]), headers={"User-Agent": "som-native-provisioning/1"})
    with build_opener(PublicRedirect()).open(request, timeout=30) as response:
        public_url(response.geturl())
        data = response.read(MAX_ARCHIVE + 1)
    require(len(data) <= MAX_ARCHIVE, "artifact exceeds download limit")
    require(digest(data) == row["archive_sha256"], f"{row['tool']} archive digest mismatch")
    return data


def archive_members(blob: bytes, zipped: bool):
    """Validate every path before returning readers. Never use extract/extractall."""
    archive = zipfile.ZipFile(io.BytesIO(blob)) if zipped else tarfile.open(fileobj=io.BytesIO(blob), mode="r:*")
    entries = {}
    expanded = 0
    for entry in archive.infolist() if zipped else archive.getmembers():
        name = member_path(entry.filename if zipped else entry.name)
        require(name not in entries, "duplicate archive member")
        size = entry.file_size if zipped else entry.size
        require(0 <= size <= MAX_FILE, "archive member exceeds size limit")
        expanded += size
        require(expanded <= MAX_EXPANDED, "archive exceeds expanded size limit")
        directory = entry.is_dir() if zipped else entry.isdir()
        if zipped:
            mode = entry.external_attr >> 16
            regular = not directory and (stat.S_IFMT(mode) in (0, stat.S_IFREG))
        else:
            regular = entry.isfile()
        entries[name] = (entry, directory, regular)
    return archive, entries


def write_new(path: Path, data: bytes, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    if executable:
        path.chmod(0o755)


def unpack(row: dict, blob: bytes, out: Path) -> dict:
    name = row["tool"]
    archive, entries = archive_members(blob, name == "ruff")
    selected = set(row["licenses"])
    if name != "prettier":
        selected.add(row["executable_member"])
    require(selected <= set(entries), f"{name} archive is missing a pinned member")
    license_rows, executable_sha = {}, None
    try:
        for path, (entry, directory, regular) in entries.items():
            wanted = name == "prettier" or path in selected
            if not wanted:
                # Official Node archives contain unused npm links. They are never extracted.
                continue
            require(directory or regular, "selected archive links and special files are refused")
            if directory:
                require(path not in selected, "pinned archive member is a directory")
                continue
            if name == "prettier":
                require(path.startswith("package/"), "Prettier member must be under package/")
                relative = member_path(path[len("package/"):])
                destination = out / "prettier" / relative
            elif path == row["executable_member"]:
                destination = out / "bin" / name
            else:
                destination = out / "licenses" / name / path
            reader = archive.open(entry) if name == "ruff" else archive.extractfile(entry)
            with reader:
                data = reader.read(MAX_FILE + 1)
            require(len(data) == (entry.file_size if name == "ruff" else entry.size), "archive member size mismatch")
            actual = digest(data)
            if path in row["licenses"]:
                require(actual == row["licenses"][path], f"{name} license digest mismatch")
                license_rows[path] = actual
            if name != "prettier" and path == row["executable_member"]:
                require(actual == row["executable_sha256"], f"{name} executable digest mismatch")
                executable_sha = actual
                if name == "rustfmt":
                    continue  # The supplied rustup binary is used; verify its archive bytes too.
            write_new(destination, data, name in {"ruff", "node"} and path == row.get("executable_member"))
    finally:
        archive.close()
    require(set(license_rows) == set(row["licenses"]), f"{name} license inventory incomplete")
    if name == "prettier":
        files = sorted((out / "prettier").rglob("*"))
        lines = []
        for file in files:
            require(not file.is_symlink() and (file.is_file() or file.is_dir()), "unsafe Prettier tree")
            if file.is_file():
                lines.append(f"{file.relative_to(out / 'prettier').as_posix()}\0{digest(file.read_bytes())}\n")
        actual = digest("".join(lines).encode())
        require(actual == row["tree_sha256"], "Prettier tree digest mismatch")
        executable_sha = actual
    return {"archive_sha256": digest(blob), "executable_sha256": executable_sha, "licenses": license_rows}


def version_record(command: list[str], name: str, expected: str) -> dict:
    result = subprocess.run(command, cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, capture_output=True, text=True, timeout=15)
    row = {"argv": command, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}
    text = result.stdout.strip()
    valid = text == {"ruff": f"ruff {expected}", "node": f"v{expected}", "prettier": expected}.get(name)
    if name == "rustfmt":
        tokens = text.split()
        valid = len(tokens) >= 2 and tokens[0] == "rustfmt" and tokens[1].split("-")[0] == expected
    row["version_matches"] = valid and result.returncode == 0
    return row


def provision(repo: Path, manifest: Path, out: Path, rustfmt: Path) -> dict:
    require(os.environ.get("GITHUB_ACTIONS") == "true", "provision only on GitHub runners")
    require(platform.system() == "Linux" and platform.machine().lower() in {"x86_64", "amd64"}, "unsupported native host")
    require(out.is_absolute() and ".." not in out.parts and not any(c in str(out) for c in "\r\n\0"), "unsafe output directory")
    no_symlinks(out)
    require(out != repo and repo not in out.parents and not out.exists() and out.parent.is_dir(), "output must be a new directory outside the repository")
    require(manifest.is_absolute() and repo in manifest.parents and ".." not in manifest.parts, "manifest must be inside the repository")
    no_symlinks(manifest)
    require(rustfmt.is_absolute() and ".." not in rustfmt.parts and not any(c in str(rustfmt) for c in "\r\n\0"), "unsafe rustfmt path")
    no_symlinks(rustfmt)
    require(rustfmt.is_file(), "rustfmt must be a regular file")
    data = manifest.read_bytes()
    tools = validate_manifest(json.loads(data))
    require(digest(rustfmt.read_bytes()) == tools["rustfmt"]["executable_sha256"], "installed rustfmt digest mismatch")
    out.mkdir(mode=0o700)
    report = {"version": "som.native-provisioning.v1", "manifest_sha256": digest(data), "host": HOST, "status": "failed", "tools": {}, "product_acceptance_passed": False}
    try:
        for name in ("ruff", "rustfmt", "node", "prettier"):
            blob = download(tools[name])
            # Also verify mocked/replaced download adapters at the trust boundary.
            require(len(blob) <= MAX_ARCHIVE and digest(blob) == tools[name]["archive_sha256"], f"{name} archive digest mismatch")
            report["tools"][name] = unpack(tools[name], blob, out)
        paths = {"SOM_RUFF": out / "bin/ruff", "SOM_RUSTFMT": rustfmt, "SOM_TS_NODE": out / "bin/node", "SOM_TS_PRETTIER": out / "prettier"}
        commands = {
            "ruff": [str(paths["SOM_RUFF"]), "--version"],
            "rustfmt": [str(rustfmt), "--version"],
            "node": [str(paths["SOM_TS_NODE"]), "--version"],
            "prettier": [str(paths["SOM_TS_NODE"]), str(paths["SOM_TS_PRETTIER"] / "bin/prettier.cjs"), "--version"],
        }
        for name, command in commands.items():
            record = version_record(command, name, tools[name]["version"])
            report["tools"][name]["version"] = record
            require(record["version_matches"], f"{name} version command failed or differs")
        lines = []
        for name, path in paths.items():
            require(not any(c in str(path) for c in "\r\n\0"), "unsafe environment value")
            lines.append(f"{name}={path}\n")
        write_new(out / "github.env", "".join(lines).encode())
        report["status"] = "ready"
        return report
    except Exception as error:
        report["reason"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_new(out / "receipt.json", (json.dumps(report, indent=2) + "\n").encode())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--manifest", type=Path, default=Path("som-rs/contracts/native-tools-v1/artifacts.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rustfmt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve(strict=True)
        manifest = args.manifest if args.manifest.is_absolute() else repo / args.manifest
        provision(repo, manifest, args.out, args.rustfmt)
        print(json.dumps({"status": "ready", "receipt": str(args.out / "receipt.json"), "github_env": str(args.out / "github.env"), "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1 if args.out.is_dir() and (args.out / "receipt.json").exists() else 2


if __name__ == "__main__":
    sys.exit(main())
