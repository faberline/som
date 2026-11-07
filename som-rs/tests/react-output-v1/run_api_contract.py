"""Hosts a Python product on QA ports and runs api_contract.mjs with a React product's own api.ts against it.

Usage: run_api_contract.py <python product case dir> <react product dir> <work dir (new)>
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

# Native mode uses fixed source bindings. A globals flag grants no authority.
_NATIVE_API = '__som_native_active_controls__' in globals()
if _NATIVE_API:
    from _som_native_active_controls_binding import controls as _native_controls
    from types import FunctionType as _NativeFunction, ModuleType as _NativeModule
    _native_controls.verify_installed()
    if (_native_controls is not __som_native_active_controls__
            or _native_controls.role_sources.get('entry.m6.api-contract') != 'som-rs/tests/react-output-v1/run_api_contract.py'
            or __file__ != str(_native_controls.root / 'som-rs/tests/react-output-v1/run_api_contract.py')
            or __name__ != '__main__'
            or type(__som_native_API_subprocess__) is not _NativeModule
            or type(__som_native_API_tools__) is not _NativeFunction):
        raise ValueError('actual fixed native API host/runner bindings required')
    _native_facade = _NativeModule.__getattribute__(__som_native_API_subprocess__, '__dict__')
    if (_native_facade.get('__file__') != str(_native_controls.root /
            _native_controls.role_sources['m6.api.recording.subprocess'])
            or sys.modules.get(_native_facade.get('__name__')) is not __som_native_API_subprocess__
            or __som_native_API_tools__.__globals__.get('__file__') != str(_native_controls.root /
                _native_controls.role_sources['m6.api.case.consumer'])):
        raise ValueError('fixed stable native API facade/getter source differs')
    _NATIVE_API_TOOLS = __som_native_API_tools__()
    _native_keys = ('python', 'node', 'npm_cli', 'cache', 'pg_bin', 'lsof', 'ps', 'site')
    if (type(_NATIVE_API_TOOLS) is not dict or not all(type(key) is str for key in _NATIVE_API_TOOLS)
            or not all(key in _NATIVE_API_TOOLS and type(_NATIVE_API_TOOLS[key]) is str
                       and _NATIVE_API_TOOLS[key] for key in _native_keys)):
        raise ValueError('actual source-owned native API tool data required')
    for _native_key in _native_keys:
        _native_path = pathlib.Path(_NATIVE_API_TOOLS[_native_key])
        if not _native_path.is_absolute() or '..' in _native_path.parts:
            raise ValueError('absolute exact native API tool path required')
    if (_NATIVE_API_TOOLS['lsof'] != '/usr/bin/lsof'
            or _NATIVE_API_TOOLS['ps'] != '/bin/ps'):
        raise ValueError('fixed Linux lsof/ps paths required')
    # Only this module's alias changes. The real stdlib module stays installed.
    subprocess = __som_native_API_subprocess__
    _native_controls.verify_installed()
else:
    if '_som_native_active_controls' in sys.modules:
        raise ValueError('native API host/runner has no fixed binding')


SOM_RS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOM_RS / "behavior"))

from typescript import host as ts_host  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
if _NATIVE_API:
    PYTHON = _NATIVE_API_TOOLS['python']
else:
    PYTHON = str(SOM_RS / "runtime" / "acceptance-m3-venv" / "bin" / "python")


def product_files(root: pathlib.Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def main() -> int:
    case, react, work = (pathlib.Path(arg).resolve() for arg in sys.argv[1:4])
    work.mkdir(parents=True, exist_ok=False)
    prompt = json.loads((case / "prompt.json").read_text())
    files = product_files(case / "product")
    ports = ts_host.base_hosts.Ports()
    tracker = ts_host.ProcessTracker()
    report: dict = {"case": str(case), "react": str(react)}
    try:
        with ts_host.pg_cluster(work, ports.take(), tracker) as pg:
            db = ts_host.base_hosts.products.fresh_database(pg, "api_contract")
            with ts_host.python_product(work, "python", prompt, files, db, ports.take(), PYTHON, tracker) as url:
                done = subprocess.run(
                    [ts_host.NODE_BIN, str(HERE / "api_contract.mjs"), str(react), url, str(case / "prompt.json")],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                report["exit_code"] = done.returncode
                report["stderr"] = done.stderr[-2000:]
                report["result"] = json.loads(done.stdout) if done.stdout.strip() else None
    finally:
        report["processes"] = tracker.records
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"exit_code": report.get("exit_code"), "steps": report.get("result", {}) and report["result"]["steps"]}, indent=1))
    stopped = all(record["stopped"] for record in tracker.records)
    print(f"all_processes_stopped={stopped}")
    return 0 if report.get("exit_code") == 0 and stopped else 1


if __name__ == "__main__":
    sys.exit(main())
