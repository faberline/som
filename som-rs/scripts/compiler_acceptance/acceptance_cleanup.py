"""Bounded cleanup for completed SOM acceptance rows.

Call only after the relevant behavior checks have returned. This helper removes
only row-local virtual environments and mypy caches below a new run output.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _environment_manifest(root: Path) -> dict[str, Any]:
    import hashlib
    entries = []
    config = root / 'pyvenv.cfg'
    if config.is_file() and not config.is_symlink():
        entries.append({'path': 'pyvenv.cfg', 'bytes': config.stat().st_size,
                        'sha256': hashlib.sha256(config.read_bytes()).hexdigest()})
    for metadata in sorted(root.rglob('METADATA')):
        if metadata.is_symlink() or not metadata.parent.name.endswith('.dist-info'):
            continue
        content = metadata.read_text(encoding='utf-8', errors='replace').splitlines()
        name = next((line.partition(':')[2].strip() for line in content if line.startswith('Name: ')), None)
        version = next((line.partition(':')[2].strip() for line in content if line.startswith('Version: ')), None)
        entries.append({'path': str(metadata.relative_to(root)), 'name': name, 'version': version,
                        'bytes': metadata.stat().st_size,
                        'sha256': hashlib.sha256(metadata.read_bytes()).hexdigest()})
    return {'entries': entries, 'entry_count': len(entries)}


def _safe_target(output: Path, target: Path) -> bool:
    if output.is_symlink() or not output.is_dir():
        return False
    base = output.absolute()
    try:
        relative = target.relative_to(base)
    except ValueError:
        return False
    if '..' in relative.parts or relative.is_absolute():
        return False
    parts = relative.parts
    # Allowed targets: */product/.venv, */focused-product/.venv, */quality/mypy-cache.
    allowed = ((len(parts) >= 2 and parts[-2:] == ('product', '.venv'))
               or (len(parts) >= 2 and parts[-2:] == ('focused-product', '.venv'))
               or (len(parts) >= 2 and parts[-2:] == ('quality', 'mypy-cache')))
    if not allowed:
        return False
    current = output
    for part in parts:
        current = current / part
        if current.is_symlink():
            return False
    return target.resolve().is_relative_to(output.resolve())


def _mentions(path: Path) -> list[dict[str, str]]:
    result = subprocess.run(['ps', '-ww', '-axo', 'pid=,command='], capture_output=True, text=True,
                            check=False, timeout=10)
    if result.returncode != 0:
        raise RuntimeError('cannot verify process absence: ' + result.stderr[-1000:])
    needle = str(path.resolve())
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and needle in parts[1]:
            rows.append({'pid': parts[0], 'command': parts[1][:500]})
    return rows


def _bytes(path: Path) -> int:
    if path.is_file() and not path.is_symlink():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob('*')
                   if item.is_file() and not item.is_symlink())
    return 0


def cleanup_after_behavior(output: Path, targets: list[Path], *, behavior_complete: bool) -> dict[str, Any]:
    """Clean allowlisted row paths only after all behavior checks have ended."""
    base = output.absolute()
    root = output.resolve()
    record: dict[str, Any] = {'version': 'som.acceptance-owned-cleanup.v1',
                              'behavior_complete': behavior_complete, 'passed': False,
                              'output': str(root), 'targets': [], 'removed_paths': []}
    try:
        if behavior_complete is not True:
            raise ValueError('behavior checks are not marked complete')
        if output.is_symlink() or not output.is_dir():
            raise ValueError('run output must be an existing non-symlink directory')
        unique = []
        for target in targets:
            candidate = target if target.is_absolute() else base / target
            if candidate not in unique:
                unique.append(candidate)
        if not unique:
            raise ValueError('no owned cleanup targets were supplied')
        # Preflight every path and every process before deleting any target.
        for target in unique:
            if not _safe_target(output, target):
                raise ValueError(f'unsafe or non-allowlisted target: {target}')
            live = _mentions(target) if target.exists() else []
            if live:
                raise RuntimeError(f'processes still use cleanup target {target}: {live[:5]}')
            item: dict[str, Any] = {'path': str(target), 'present': target.exists(),
                                    'bytes_before': _bytes(target), 'live_processes': live}
            if target.name == '.venv' and target.exists():
                item['environment_manifest'] = _environment_manifest(target)
            item['free_bytes_before'] = shutil.disk_usage(root).free
            record['targets'].append(item)
        record['free_bytes_before'] = shutil.disk_usage(root).free
        for target, item in zip(unique, record['targets']):
            if not target.exists():
                item['bytes_after'] = 0
                item['free_bytes_after'] = shutil.disk_usage(root).free
                continue
            if not _safe_target(output, target) or _mentions(target):
                raise RuntimeError(f'target safety or process state changed before deletion: {target}')
            shutil.rmtree(target)
            item['removed'] = True
            item['bytes_after'] = _bytes(target)
            item['free_bytes_after'] = shutil.disk_usage(root).free
            if item['bytes_after'] != 0:
                raise RuntimeError(f'target remains after deletion: {target}')
            record['removed_paths'].append(str(target))
        record['free_bytes_after'] = shutil.disk_usage(root).free
        record['passed'] = True
    except Exception as error:
        record['error'] = type(error).__name__ + ': ' + str(error)
        record['free_bytes_after'] = shutil.disk_usage(root).free if root.exists() else None
    return record
