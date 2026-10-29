"""Hosts a Python product on QA ports and runs api_contract.mjs with a React product's own api.ts against it.

Usage: run_api_contract.py <python product case dir> <react product dir> <work dir (new)>
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

SOM_RS = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOM_RS / "behavior"))

from typescript import host as ts_host  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
PYTHON = str(SOM_RS / "runtime" / "acceptance-m3-venv" / "bin" / "python")


def product_files(root: pathlib.Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def main() -> int:
    args = sys.argv[1:]
    case, react, work = (pathlib.Path(arg).resolve() for arg in args[:3])
    if len(args) == 4 and args[3] == "--linux-qa":
        # Only the source-bound recorder may install this authenticated module.
        adapter = sys.modules.get("linux_api_host")
        qa = getattr(adapter, "ADMITTED_CONTEXT", None)
        if adapter is None or not isinstance(qa, dict):
            raise ValueError("Linux QA context was not admitted")
        with adapter.configured_host(ts_host, qa):
            return run_case(case, react, work, str(qa["python"]))
    return run_case(case, react, work, PYTHON)


def run_case(case: pathlib.Path, react: pathlib.Path, work: pathlib.Path, python: str) -> int:
    work.mkdir(parents=True, exist_ok=False)
    prompt = json.loads((case / "prompt.json").read_text())
    files = product_files(case / "product")
    ports = ts_host.base_hosts.Ports()
    tracker = ts_host.ProcessTracker()
    report: dict = {"case": str(case), "react": str(react)}
    try:
        with ts_host.pg_cluster(work, ports.take(), tracker) as pg:
            db = ts_host.base_hosts.products.fresh_database(pg, "api_contract")
            with ts_host.python_product(work, "python", prompt, files, db, ports.take(), python, tracker) as url:
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
