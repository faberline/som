#!/usr/bin/env python3
"""Build the vendored crate set for the frozen reference locks, offline.

The set is the union of the packages of every reference lock: reference/ (phase 1) and
reference-phase2/ (amendment 003) unless --reference names others. One cargo directory source holds
every version, so phase 1 and phase 2 products build from the same CARGO_HOME.

Every crates.io package that the local cargo cache holds is unpacked after its .crate sha256 matches
the lock checksum, with a file checksum list like cargo vendor writes. A package the cache lacks is
taken from a --base vendor directory when one holds it unpacked, with the lock checksum as its
package checksum and every file matching its .cargo-checksum.json. Any other package is only needed
on another platform (Windows, Redox, WASM, Android, Haiku). It becomes a stub with the manifest the
cached crates.io index records for that exact version, so resolution and the lock stay unchanged. A
host build that compiled a stub would fail, so stubs cannot hide a missing crate.

The script writes a new directory with vendor/, cargo-home/config.toml and
vendor.json, which hashes the whole tree. It needs no network.

usage: vendor_crates.py --out <new directory> [--reference DIR ...] [--base VENDOR_DIR ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import tarfile
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[3]
CONTRACT = REPO / "som-rs/contracts/rust-output-v1"
REFERENCES = [CONTRACT / "reference", CONTRACT / "reference-phase2"]
REGISTRY = pathlib.Path.home() / ".cargo/registry"
INDEX = "index.crates.io-1949cf8c6b5b557f"
CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
STUB = 'compile_error!("vendored stub: this crate is only for another platform");\n'


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_entry(name: str, version: str) -> dict:
    """The crates.io index record for one version, from the sparse index cache."""
    lower = name.lower()
    prefix = {1: "1", 2: "2", 3: f"3/{lower[0]}"}.get(len(lower), f"{lower[:2]}/{lower[2:4]}")
    data = (REGISTRY / "index" / INDEX / ".cache" / prefix / lower).read_bytes()
    parts = data[5:].split(b"\0")
    for key, value in zip(parts[1::2], parts[2::2]):
        if key.decode() == version:
            return json.loads(value)
    raise SystemExit(f"index cache has no {name} {version}")


def toml_value(value: object) -> str:
    return json.dumps(value)


def stub_manifest(entry: dict) -> str:
    """A Cargo.toml with the indexed dependencies and features of one version."""
    lines = ["[package]", f"name = {toml_value(entry['name'])}", f"version = {toml_value(entry['vers'])}",
             'edition = "2015"', "build = false"]
    if entry.get("links"):
        lines.append(f"links = {toml_value(entry['links'])}")
    if entry.get("rust_version"):
        lines.append(f"rust-version = {toml_value(entry['rust_version'])}")
    lines += ["", "[lib]", 'path = "lib.rs"', ""]
    sections: dict[str, list[str]] = {}
    for dep in entry["deps"]:
        kind = {"normal": "dependencies", "dev": "dev-dependencies", "build": "build-dependencies"}[dep.get("kind") or "normal"]
        table = f'target.{toml_value(dep["target"])}.{kind}' if dep.get("target") else kind
        spec = {"version": dep["req"], "optional": dep["optional"], "default-features": dep["default_features"],
                "features": dep["features"]}
        if dep.get("package"):
            spec["package"] = dep["package"]
        body = ", ".join(f"{key} = {toml_value(value)}" for key, value in spec.items())
        sections.setdefault(table, []).append(f"{toml_value(dep['name'])} = {{ {body} }}")
    for table, rows in sections.items():
        lines += [f"[{table}]", *rows, ""]
    features = {**entry.get("features", {}), **entry.get("features2", {})}
    if features:
        lines.append("[features]")
        lines += [f"{toml_value(name)} = {toml_value(values)}" for name, values in sorted(features.items())]
    return "\n".join(lines) + "\n"


def write_checksum(directory: pathlib.Path, package: str) -> None:
    files = {str(path.relative_to(directory)): sha_bytes(path.read_bytes())
             for path in sorted(directory.rglob("*")) if path.is_file()}
    (directory / ".cargo-checksum.json").write_text(json.dumps({"files": files, "package": package}, sort_keys=True))


def unpack(crate: pathlib.Path, destination: pathlib.Path) -> None:
    with tarfile.open(crate, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            relative = pathlib.PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
                raise SystemExit(f"unsafe member {member.name} in {crate.name}")
            target = destination.joinpath(*relative.parts[1:])
            source = archive.extractfile(member)
            if source is None:
                raise SystemExit(f"unreadable member {member.name} in {crate.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def from_base(bases: list[pathlib.Path], name: str, version: str, checksum: str,
              destination: pathlib.Path) -> bool:
    """Copy an unpacked crate of a --base vendor directory; it must be the locked package."""
    for base in bases:
        source = base / f"{name}-{version}"
        listing = source / ".cargo-checksum.json"
        stub = source / "lib.rs"
        if not listing.is_file() or (stub.is_file() and stub.read_text().startswith(STUB)):
            continue
        recorded = json.loads(listing.read_text())
        if recorded.get("package") != checksum:
            continue
        files = {str(path.relative_to(source)): sha_bytes(path.read_bytes())
                 for path in sorted(source.rglob("*")) if path.is_file() and path != listing}
        if files != recorded["files"]:
            raise SystemExit(f"{source} differs from its .cargo-checksum.json")
        shutil.copytree(source, destination, symlinks=False)
        (destination / ".cargo-checksum.json").unlink()
        return True
    return False


def tree_sha(root: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(f"{path.relative_to(root)}\0{sha_bytes(path.read_bytes())}\n".encode())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--reference", type=pathlib.Path, action="append")
    parser.add_argument("--base", type=pathlib.Path, action="append", default=[])
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"preserve existing output: {out}")
    references = [path.resolve() for path in args.reference or REFERENCES]
    bases = [path.resolve() for path in args.base]
    locked: dict[tuple[str, str], str] = {}
    for reference in references:
        for package in tomllib.loads((reference / "Cargo.lock").read_text())["package"]:
            if package.get("source") is None:
                continue
            if package["source"] != CRATES_IO:
                raise SystemExit(f"unexpected source for {package['name']}")
            if locked.setdefault((package["name"], package["version"]), package["checksum"]) != package["checksum"]:
                raise SystemExit(f"the locks disagree on {package['name']} {package['version']}")
    vendor = out / "vendor"
    rows = []
    for (name, version), checksum in sorted(locked.items()):
        directory = vendor / f"{name}-{version}"
        crate = REGISTRY / "cache" / INDEX / f"{name}-{version}.crate"
        if crate.is_file():
            if sha_bytes(crate.read_bytes()) != checksum:
                raise SystemExit(f"{crate.name} differs from the lock checksum")
            unpack(crate, directory)
            kind = "crate"
        elif from_base(bases, name, version, checksum, directory):
            kind = "base"
        else:
            entry = index_entry(name, version)
            if entry["cksum"] != checksum:
                raise SystemExit(f"index checksum for {name} {version} differs from the lock")
            directory.mkdir(parents=True)
            (directory / "Cargo.toml").write_text(stub_manifest(entry))
            (directory / "lib.rs").write_text(STUB)
            kind = "stub"
        write_checksum(directory, checksum)
        rows.append({"name": name, "version": version, "checksum": checksum, "kind": kind,
                     "tree_sha256": tree_sha(directory)})
    home = out / "cargo-home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
        f'[source.vendored-sources]\ndirectory = {json.dumps(str(vendor))}\n\n[net]\noffline = true\n')
    receipt = {"version": "som.rust-output-vendor.v2",
               "references": [{"path": str(reference.relative_to(REPO)) if reference.is_relative_to(REPO)
                               else str(reference),
                               "lock_sha256": sha_bytes((reference / "Cargo.lock").read_bytes()),
                               "manifest_sha256": sha_bytes((reference / "Cargo.toml").read_bytes())}
                              for reference in references],
               "bases": [str(base) for base in bases],
               "lock_packages": len(rows), "packages": rows,
               "crates": sum(row["kind"] == "crate" for row in rows),
               "from_base": [f"{row['name']} {row['version']}" for row in rows if row["kind"] == "base"],
               "stubs": [f"{row['name']} {row['version']}" for row in rows if row["kind"] == "stub"],
               "vendor_tree_sha256": tree_sha(vendor),
               "cargo_config_sha256": sha_bytes((home / "config.toml").read_bytes())}
    (out / "vendor.json").write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({key: receipt[key] for key in ("lock_packages", "crates", "vendor_tree_sha256")}
                     | {"stubs": len(receipt["stubs"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
