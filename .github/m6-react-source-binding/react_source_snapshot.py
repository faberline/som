"""GitHub-only historical React source snapshot experiment.

This fixed f59 experiment is not a production policy, verifier, or acceptance
authority. It binds only a historical source subset and frozen macOS formatter
metadata. It does not establish runtime tool identity or product correctness.
The pins cannot be supplied or changed by a caller. New contract/source commits
require a separately reviewed derivation, not a change to these observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
from typing import Any

BASE = "f59e28d910ff4adb16e3becfdc82bd2978ca84fa"
OWNER = "som-rs/som-targets/src/react/mod.rs"
OWNER_SHA = "ff5dd83564d170a56072a9c27d9a3fb83a52c9909492e6eea74a046030c00af1"
RECEIPTS = (
    ("som-rs/evidence/react-output-contract-freeze-2026-10-22.json", "d427096f8ceca6742f0ad0ee6e3c867e726fa90038f0bb4028fe8ecd4233ed95"),
    ("som-rs/evidence/react-output-contract-amendment-001.json", "468b8b146dd39ffdc16a70285d46631adc89a62d30fafc5197dc5c6e8335a3bb"),
    ("som-rs/evidence/react-output-contract-amendment-002.json", "eb4e120c2a573dc06cf068841ec7b0b0f0e2349d74ae2bff5df7dcade41fe962"),
)
ALIASES = {
    "som-targets/Cargo.toml": "som-rs/som-targets/Cargo.toml",
    "som-targets/src/lib.rs": "som-rs/som-targets/src/lib.rs",
    "som-targets/src/main.rs": "som-rs/som-targets/src/main.rs",
    "som-targets/src/profile.rs": "som-rs/som-targets/src/profile.rs",
    "som-targets/src/react/mod.rs": OWNER,
    "som-targets/src/react/profile.rs": "som-rs/som-targets/src/react/profile.rs",
    "som-targets/src/react/render.rs": "som-rs/som-targets/src/react/render.rs",
}
EXPECTED_FILES = {
    "som-targets/Cargo.toml": "df9be9cf80bc1e0d300c9b0aaf28c7e146e22796bb26ca3ab22ec759eae4e79b",
    "som-targets/src/lib.rs": "812e0025e930869acb216527fe48461cc0fe694853d1c7be14b3d1e330fadba7",
    "som-targets/src/main.rs": "6f521d6d7f3fa7b074de861da4ce5c15934d312d8ce77464f47cf0c3e9863fe2",
    "som-targets/src/profile.rs": "193f2efd0c5c1dcc276a4b2bdd0c3dd8a0dbab7033b26cc2d7fa5ac930ff1082",
    "som-targets/src/react/mod.rs": OWNER_SHA,
    "som-targets/src/react/profile.rs": "f1f2770cfd08055106a01c03a963a09a71857cd105c2afd056daa9369a899fca",
    "som-targets/src/react/render.rs": "d9d3eca8371cb7c6ef8d16c386e289ac343a3d4beeb03d990e586be7623afe4d",
}
BACKEND_SHA = "834ff20ea7e7b4f54009a38e960b1423c4dac42ac88f7092532f8c125c4bb1d7"
TARGET_SHA = "d48cbd2a790554bab14a38bd6092e5975e82027c0a6da1ef46d8ff7affdbf638"
FORMATTER_SHA = "636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8"
PRETTIER_SHA = "f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299"
TOOLCHAIN = "som-rs/contracts/react-output-v1/toolchain.json"
CONTRACT_ROOTS = ("som-rs/contracts/react-output-v1", "som-rs/tests/react-output-v1",
                  "som-rs/evidence/react-output-v1")
PROTECTED = (*CONTRACT_ROOTS, OWNER,
             *ALIASES.values(), *(path for path, _ in RECEIPTS),
             "som-rs/evidence/react-output-contract-amendment-*.json")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class SnapshotRefused(ValueError):
    """Historical source evidence is missing or inconsistent."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SnapshotRefused(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    # Applied here only to maps of fixed ASCII paths and SHA strings.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        _need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(SnapshotRefused("invalid JSON constant")))
    except (UnicodeError, ValueError) as error:
        raise SnapshotRefused("invalid JSON") from error
    _need(type(value) is dict, "JSON root must be an object")
    return value


def _path(value: str) -> str:
    _need(type(value) is str and bool(value) and "\\" not in value and "\0" not in value, "invalid path")
    p = PurePosixPath(value)
    _need(not p.is_absolute() and all(part not in ("", ".", "..") for part in value.split("/")), "unsafe path")
    return value


def _factory_aliases(owner_bytes: bytes) -> dict[str, str]:
    """Parse the exact pinned static Rust BACKEND_FILES literal, failing closed."""
    _need(_sha(owner_bytes) == OWNER_SHA, "owner source differs")
    text = owner_bytes.decode("utf-8", "strict")
    start = 'const BACKEND_FILES: &[(&str, &str)] = &['
    end = '\n];'
    _need(text.count(start) == 1, "factory boundary differs")
    tail = text.split(start, 1)[1]
    _need(tail.count(end) >= 1, "factory end missing")
    body = tail.split(end, 1)[0]
    tuple_re = re.compile(r'\(\s*"([^"\\]+)"\s*,\s*include_str!\(\s*"([^"\\]+)"\s*\)\s*,?\s*\)')
    parsed: dict[str, str] = {}
    for match in tuple_re.finditer(body):
        key, include = match.groups()
        _need(key not in parsed and not include.startswith("/"), "duplicate or absolute include")
        resolved = os.path.normpath(os.path.join(os.path.dirname(OWNER), include)).replace(os.sep, "/")
        _path(resolved)
        parsed[key] = resolved
    residue = tuple_re.sub("", body)
    _need(not re.sub(r"[\s,]", "", residue), "unparsed factory entry")
    _need(parsed == ALIASES, "factory aliases differ")
    return parsed


def _git(repo: Path, *args: str) -> bytes:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(["/usr/bin/git", *args], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise SnapshotRefused("Git source check failed") from error
    return result.stdout


def _bound_file(repo: Path, path: str) -> bytes:
    """Match a regular working-tree file to its pinned f59 Git blob."""
    path = _path(path)
    node = repo
    for part in path.split("/"):
        node = node / part
        _need(not node.is_symlink(), "source path contains a symlink")
    _need(node.is_file(), "source file missing")
    actual = node.read_bytes()
    committed = _git(repo, "show", f"{BASE}:{path}")
    _need(actual == committed, "working file differs from f59 Git blob")
    return committed


def _scope_files(repo: Path) -> set[str]:
    """Enumerate the receipt's three roots without following symlinks."""
    found: set[str] = set()
    for root in CONTRACT_ROOTS:
        path = repo / root
        _need(path.is_dir() and not path.is_symlink(), "contract root missing or linked")
        for base, dirs, files in os.walk(path, followlinks=False):
            base_path = Path(base)
            _need("__pycache__" not in dirs, "cached files in contract scope")
            for name in dirs:
                _need(not (base_path / name).is_symlink(), "linked contract directory")
            for name in files:
                entry = base_path / name
                _need(not entry.is_symlink() and entry.is_file(), "non-regular contract file")
                found.add(entry.relative_to(repo).as_posix())
    return found


def _receipt_files(repo: Path) -> set[str]:
    """Reject extra receipt files rather than claiming a later contract."""
    root = repo / "som-rs/evidence"
    _need(root.is_dir() and not root.is_symlink(), "receipt directory missing or linked")
    paths: set[str] = set()
    for entry in root.glob("react-output-contract-amendment-*.json"):
        _need(not entry.is_symlink() and entry.is_file(), "non-regular amendment receipt")
        paths.add(entry.relative_to(repo).as_posix())
    return paths


def _formatter(tool: dict[str, Any]) -> str:
    """Compute the frozen macOS reference descriptor; never a Linux tool proof."""
    node, formatter = tool.get("node", {}), tool.get("formatter", {})
    _need(type(node) is dict and type(formatter) is dict and type(node.get("libnode")) is dict, "formatter profile missing")
    _need(tool.get("host") == "aarch64-apple-darwin", "historical formatter host differs")
    _need(formatter.get("id") == "som.react-formatter.prettier-3.9.10.v1"
          and formatter.get("prettier_tree_sha256") == PRETTIER_SHA, "formatter profile differs")
    payload = {"node": node.get("sha256"), "libnode": node["libnode"].get("sha256"),
               "prettier_tree_sha256": formatter["prettier_tree_sha256"]}
    _need(all(type(value) is str and HEX.fullmatch(value) for value in payload.values()), "formatter input digest invalid")
    formatter_sha = _sha(_canonical(payload))
    _need(formatter.get("sha256") == formatter_sha == FORMATTER_SHA, "formatter derivation differs")
    return formatter_sha


@dataclass(frozen=True, slots=True)
class SnapshotProof:
    label: str
    base_source: str
    checkout_head: str
    backend_sha256: str
    target_sha256: str
    formatter_sha256: str
    receipt_file_count: int
    acceptance_granted: bool = field(default=False, init=False)


def inspect_snapshot(repo: Path) -> SnapshotProof:
    """Inspect the fixed source subset on a GH runner, never grant acceptance."""
    _need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
          and os.environ.get("GITHUB_REPOSITORY") == "faberline/som", "GitHub runner identity missing")
    _need(os.environ.get("RUNNER_OS") == "Linux" and os.environ.get("RUNNER_ARCH") == "X64"
          and platform.system() == "Linux" and platform.machine() == "x86_64", "unsupported observation host")
    _need(isinstance(repo, Path) and repo.is_absolute() and repo.resolve() == repo and repo.is_dir(), "invalid checkout root")
    expected_head = os.environ.get("GITHUB_SHA", "")
    _need(re.fullmatch(r"[0-9a-f]{40}", expected_head) is not None, "GitHub head missing")
    _need(_git(repo, "rev-parse", "HEAD").strip().decode("ascii") == expected_head, "checkout head differs")
    _git(repo, "merge-base", "--is-ancestor", BASE, expected_head)
    _need(not _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *PROTECTED).strip(),
          "protected source has working changes")
    _need(_receipt_files(repo) == {path for path, _ in RECEIPTS[1:]}, "amendment receipt set differs")

    owner = _bound_file(repo, OWNER)
    aliases = _factory_aliases(owner)
    files = {key: _sha(_bound_file(repo, path)) for key, path in aliases.items()}
    _need(files == EXPECTED_FILES, "backend source vector differs")
    backend = _sha(_canonical({"version": "som.react-backend.v1", "files": files}))
    _need(backend == BACKEND_SHA, "backend derivation differs")

    chain = []
    for path, expected in RECEIPTS:
        raw = _bound_file(repo, path)
        _need(_sha(raw) == expected, "receipt raw hash differs")
        chain.append(_json(raw))
    _need(chain[0].get("status") == "contract_frozen_before_implementation", "freeze state differs")
    for index in (1, 2):
        current, previous = chain[index], chain[index - 1]
        _need(current.get("status") == "amendment_sealed_after_independent_review" and current.get("amendment") == index,
              "amendment state differs")
        _need(current.get("previous_receipt") == RECEIPTS[index - 1][0]
              and current.get("previous_receipt_sha256") == RECEIPTS[index - 1][1], "receipt chain differs")
        _need(current.get("target_contract", {}).get("previous_sha256") == previous.get("target_contract", {}).get("sha256"),
              "previous target identity differs")
    files_map = chain[-1].get("files")
    _need(type(files_map) is dict and len(files_map) == 54, "active files map scope differs")
    _need(_scope_files(repo) == set(files_map), "receipt file set differs")
    for path, digest in files_map.items():
        _need(type(digest) is str and HEX.fullmatch(digest) is not None, "bad file digest")
        _need(_sha(_bound_file(repo, path)) == digest, "receipt member differs")
    target = _sha(_canonical(files_map))
    _need(chain[-1].get("target_contract", {}).get("id") == "som.react-output-contract.v1"
          and chain[-1]["target_contract"].get("sha256") == target == TARGET_SHA, "target map differs")
    _need(f'"{TARGET_SHA}"' in owner.decode("utf-8"), "source target constant differs")

    formatter_sha = _formatter(_json(_bound_file(repo, TOOLCHAIN)))
    # Recheck the complete subset and HEAD after derivation. These checks bound
    # ordinary changes, but do not prove an atomic snapshot against a malicious
    # concurrent writer that changes and restores a path between reads.
    paths = set(files_map) | set(aliases.values()) | {path for path, _ in RECEIPTS}
    for path in sorted(paths):
        _bound_file(repo, path)
    _need(_scope_files(repo) == set(files_map), "receipt file set changed")
    _need(_receipt_files(repo) == {path for path, _ in RECEIPTS[1:]}, "amendment receipt set changed")
    _need(not _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *PROTECTED).strip(),
          "protected source changed during observation")
    _need(_git(repo, "rev-parse", "HEAD").strip().decode("ascii") == expected_head,
          "checkout head changed during observation")
    return SnapshotProof("HISTORICAL SOURCE SUBSET / NO ACCEPTANCE", BASE, expected_head, backend, target, formatter_sha, len(files_map))
