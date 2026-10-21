"""Unit tests for som-rs/scripts/rust_toolchain/check_toolchain.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parent / "check_toolchain.py"
BINARIES = (
    "rustc",
    "cargo",
    "rustfmt",
    "cargo-fmt",
    "cargo-clippy",
    "clippy-driver",
)


def _write_fake_toolchain(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    rust_section: dict[str, object] = {
        "source": "fake rust",
        "bin_dir": str(bin_dir),
    }
    for name in BINARIES:
        payload = f"fake-binary-{name}\n".encode("utf-8")
        binary_path = bin_dir / name
        binary_path.write_bytes(payload)
        binary_path.chmod(0o755)
        digest = hashlib.sha256(payload).hexdigest()
        hashes[name] = digest
        rust_section[name] = {"sha256": digest}
    rust_section["edition"] = "2024"

    toolchain_data = {
        "version": "som.rust-output-toolchain.v1",
        "rust": rust_section,
        "formatter": {
            "binary": "rustfmt",
            "sha256": hashes["rustfmt"],
        },
    }
    toolchain_path = tmp_path / "toolchain.json"
    toolchain_path.write_text(json.dumps(toolchain_data), encoding="utf-8")
    return toolchain_path, bin_dir, hashes


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_all_ok(tmp_path: Path) -> None:
    toolchain_path, _, _ = _write_fake_toolchain(tmp_path)
    result = _run("--toolchain", str(toolchain_path))
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "ok rustc",
        "ok cargo",
        "ok rustfmt",
        "ok cargo-fmt",
        "ok cargo-clippy",
        "ok clippy-driver",
        "ok formatter",
    ]

    json_result = _run("--toolchain", str(toolchain_path), "--json")
    assert json_result.returncode == 0
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is True
    assert [item["status"] for item in payload["items"]] == ["ok"] * 7


def test_missing_binary(tmp_path: Path) -> None:
    toolchain_path, bin_dir, _ = _write_fake_toolchain(tmp_path)
    missing_path = bin_dir / "cargo-fmt"
    missing_path.unlink()

    result = _run("--toolchain", str(toolchain_path))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "ok rustc",
        "ok cargo",
        "ok rustfmt",
        f"missing cargo-fmt {missing_path}",
        "ok cargo-clippy",
        "ok clippy-driver",
        "ok formatter",
    ]


def test_non_executable_binary_is_reported_missing(tmp_path: Path) -> None:
    toolchain_path, bin_dir, _ = _write_fake_toolchain(tmp_path)
    non_exec = bin_dir / "cargo"
    non_exec.chmod(0o644)

    result = _run("--toolchain", str(toolchain_path))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "ok rustc",
        f"missing cargo {non_exec}",
        "ok rustfmt",
        "ok cargo-fmt",
        "ok cargo-clippy",
        "ok clippy-driver",
        "ok formatter",
    ]


def test_hash_mismatch(tmp_path: Path) -> None:
    toolchain_path, bin_dir, hashes = _write_fake_toolchain(tmp_path)
    tampered = b"tampered-rustfmt-binary\n"
    (bin_dir / "rustfmt").write_bytes(tampered)
    actual_sha = hashlib.sha256(tampered).hexdigest()

    result = _run("--toolchain", str(toolchain_path))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "ok rustc",
        "ok cargo",
        f"mismatch rustfmt {hashes['rustfmt']} {actual_sha}",
        "ok cargo-fmt",
        "ok cargo-clippy",
        "ok clippy-driver",
        "ok formatter",
    ]


def test_formatter_sha_mismatch(tmp_path: Path) -> None:
    toolchain_path, _, hashes = _write_fake_toolchain(tmp_path)
    data = json.loads(toolchain_path.read_text(encoding="utf-8"))
    wrong_formatter_sha = "0" * 64
    data["formatter"]["sha256"] = wrong_formatter_sha
    toolchain_path.write_text(json.dumps(data), encoding="utf-8")

    result = _run("--toolchain", str(toolchain_path))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "ok rustc",
        "ok cargo",
        "ok rustfmt",
        "ok cargo-fmt",
        "ok cargo-clippy",
        "ok clippy-driver",
        f"mismatch formatter {hashes['rustfmt']} {wrong_formatter_sha}",
    ]

    json_result = _run("--toolchain", str(toolchain_path), "--json")
    assert json_result.returncode == 1
    payload = json.loads(json_result.stdout)
    assert payload["ok"] is False
    assert payload["items"][-1] == {
        "name": "formatter",
        "status": "mismatch",
        "expected": hashes["rustfmt"],
        "actual": wrong_formatter_sha,
    }


def test_invalid_or_unreadable_toolchain_exits_2(tmp_path: Path) -> None:
    missing_file = tmp_path / "does-not-exist.json"
    assert _run("--toolchain", str(missing_file)).returncode == 2

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    assert _run("--toolchain", str(bad_json)).returncode == 2

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"rust": {"bin_dir": "/tmp"}}), encoding="utf-8")
    assert _run("--toolchain", str(incomplete)).returncode == 2
