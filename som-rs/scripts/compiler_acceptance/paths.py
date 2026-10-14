"""Shared repository and evidence paths for the installed acceptance tools."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def find_repo(source_file: str | Path) -> Path:
    configured = os.environ.get('SOM_REPO')
    if configured:
        root = Path(configured).expanduser().resolve()
        if not (root / 'som-rs').is_dir() or not (root / 'som-core').is_dir():
            raise ValueError(f'SOM_REPO is not a SOM repository: {root}')
        return root
    for parent in Path(source_file).resolve().parents:
        if (parent / 'som-rs').is_dir() and (parent / 'som-core').is_dir():
            return parent
    raise RuntimeError('set SOM_REPO or install this package under som-rs/scripts/compiler_acceptance')


def default_output_root(repo: str | Path) -> Path:
    return Path(repo).resolve() / '.local/rust-compiler-goal/acceptance'


def require_new_output(path: str | Path, output_root: str | Path, *, allow_root: bool = False) -> Path:
    candidate = Path(path).expanduser().resolve()
    root = Path(output_root).resolve()
    if not candidate.is_relative_to(root) or (candidate == root and not allow_root):
        raise ValueError(f'output must stay under {root}: {candidate}')
    if candidate.exists():
        raise FileExistsError(f'preserve existing evidence: {candidate}')
    return candidate
