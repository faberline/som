"""Unit tests for som-rs/scripts/rust_vendor/check_vendor.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parent / "check_vendor.py"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_sha(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(
                f"{path.relative_to(root)}\0{_sha_bytes(path.read_bytes())}\n".encode(
                    "utf-8"
                )
            )
    return digest.hexdigest()


def _build_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    ref1 = tmp_path / "ref1"
    ref2 = tmp_path / "ref2"
    ref1.mkdir()
    ref2.mkdir()

    pkg_alpha = ("alpha", "0.1.0", "a" * 64)
    pkg_beta = ("beta", "0.2.0", "b" * 64)

    lock1_text = (
        'version = 4\n\n'
        '[[package]]\nname = "som_app"\nversion = "0.1.0"\n\n'
        f'[[package]]\nname = "{pkg_alpha[0]}"\nversion = "{pkg_alpha[1]}"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{pkg_alpha[2]}"\n'
    )
    lock2_text = (
        'version = 4\n\n'
        f'[[package]]\nname = "{pkg_alpha[0]}"\nversion = "{pkg_alpha[1]}"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{pkg_alpha[2]}"\n\n'
        f'[[package]]\nname = "{pkg_beta[0]}"\nversion = "{pkg_beta[1]}"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        f'checksum = "{pkg_beta[2]}"\n'
    )
    (ref1 / "Cargo.lock").write_text(lock1_text, encoding="utf-8")
    (ref2 / "Cargo.lock").write_text(lock2_text, encoding="utf-8")

    vendor_dir = tmp_path / "vendor-bundle"
    vendor_root = vendor_dir / "vendor"
    vendor_root.mkdir(parents=True)

    rows = []
    for name, version, checksum in (pkg_alpha, pkg_beta):
        cdir = vendor_root / f"{name}-{version}"
        src_dir = cdir / "src"
        src_dir.mkdir(parents=True)
        cargo_toml = f'[package]\nname = "{name}"\nversion = "{version}"\n'.encode("utf-8")
        lib_rs = f"pub fn {name}() -> &'static str {{ \"{version}\" }}\n".encode("utf-8")
        (cdir / "Cargo.toml").write_bytes(cargo_toml)
        (src_dir / "lib.rs").write_bytes(lib_rs)
        files = {
            "Cargo.toml": _sha_bytes(cargo_toml),
            "src/lib.rs": _sha_bytes(lib_rs),
        }
        (cdir / ".cargo-checksum.json").write_text(
            json.dumps({"files": files, "package": checksum}, sort_keys=True),
            encoding="utf-8",
        )
        rows.append(
            {
                "name": name,
                "version": version,
                "checksum": checksum,
                "kind": "crate",
                "tree_sha256": _tree_sha(cdir),
            }
        )

    cargo_home = vendor_dir / "cargo-home"
    cargo_home.mkdir()
    config_toml = (
        '[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
        f'[source.vendored-sources]\ndirectory = {json.dumps(str(vendor_root))}\n\n'
        "[net]\noffline = true\n"
    )
    config_path = cargo_home / "config.toml"
    config_path.write_text(config_toml, encoding="utf-8")

    receipt = {
        "version": "som.rust-output-vendor.v2",
        "references": [
            {
                "path": str(ref1),
                "lock_sha256": _sha_bytes((ref1 / "Cargo.lock").read_bytes()),
            },
            {
                "path": str(ref2),
                "lock_sha256": _sha_bytes((ref2 / "Cargo.lock").read_bytes()),
            },
        ],
        "lock_packages": 2,
        "packages": rows,
        "vendor_tree_sha256": _tree_sha(vendor_root),
        "cargo_config_sha256": _sha_bytes(config_path.read_bytes()),
    }
    (vendor_dir / "vendor.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return vendor_dir, ref1, ref2


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_all_ok(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "ok alpha-0.1.0",
        "ok beta-0.2.0",
        "ok vendor.json",
        "ok config.toml",
        "summary ok=2 problems=0",
    ]

    json_result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
        "--json",
    )
    assert json_result.returncode == 0
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is True
    assert payload["summary"] == {"ok": 2, "problems": 0}


def test_missing_package_directory(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    missing_dir = vendor_dir / "vendor/beta-0.2.0"
    shutil.rmtree(missing_dir)

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"missing beta-0.2.0 {missing_dir}" in lines
    assert lines[-1] == "summary ok=1 problems=1"


def test_modified_file_content(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    lib_path = vendor_dir / "vendor/alpha-0.1.0/src/lib.rs"
    old_sha = _sha_bytes(lib_path.read_bytes())
    tampered = b"pub fn alpha() -> &'static str { \"tampered\" }\n"
    lib_path.write_bytes(tampered)
    new_sha = _sha_bytes(tampered)

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"mismatch alpha-0.1.0 src/lib.rs {old_sha} {new_sha}" in lines
    assert lines[-1] == "summary ok=1 problems=2"


def test_extra_unlisted_file_and_directory(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    extra_file = vendor_dir / "vendor/alpha-0.1.0/extra.txt"
    extra_file.write_text("unlisted\n", encoding="utf-8")
    extra_dir = vendor_dir / "vendor/rogue-9.9.9"
    extra_dir.mkdir()

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"extra {extra_file}" in lines
    assert f"extra {extra_dir}" in lines
    assert lines[-1] == "summary ok=1 problems=3"


def test_checksum_package_mismatch(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    cfile = vendor_dir / "vendor/alpha-0.1.0/.cargo-checksum.json"
    cdata = json.loads(cfile.read_text(encoding="utf-8"))
    wrong_pkg = "f" * 64
    cdata["package"] = wrong_pkg
    cfile.write_text(json.dumps(cdata, sort_keys=True), encoding="utf-8")

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"mismatch alpha-0.1.0 package {'a' * 64} {wrong_pkg}" in lines


def test_vendor_json_tree_sha256_mismatch(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    vjson = vendor_dir / "vendor.json"
    vdata = json.loads(vjson.read_text(encoding="utf-8"))
    actual_tree = vdata["packages"][0]["tree_sha256"]
    bad_tree = "0" * 64
    vdata["packages"][0]["tree_sha256"] = bad_tree
    vjson.write_text(json.dumps(vdata), encoding="utf-8")

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"mismatch alpha-0.1.0 tree_sha256 {bad_tree} {actual_tree}" in lines
    assert lines[-1] == "summary ok=1 problems=1"


def test_config_toml_wrong_directory(tmp_path: Path) -> None:
    vendor_dir, ref1, ref2 = _build_fixture(tmp_path)
    config_path = vendor_dir / "cargo-home/config.toml"
    wrong_dir = tmp_path / "wrong-vendor"
    config_path.write_text(
        '[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
        f'[source.vendored-sources]\ndirectory = {json.dumps(str(wrong_dir))}\n\n'
        "[net]\noffline = true\n",
        encoding="utf-8",
    )

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(ref2),
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert f"mismatch config.toml directory {vendor_dir / 'vendor'} {wrong_dir}" in lines


def test_unreadable_cargo_lock_exits_2(tmp_path: Path) -> None:
    vendor_dir, ref1, _ = _build_fixture(tmp_path)
    missing_ref = tmp_path / "missing-ref"

    result = _run(
        "--vendor",
        str(vendor_dir),
        "--reference",
        str(ref1),
        "--reference",
        str(missing_ref),
    )
    assert result.returncode == 2
