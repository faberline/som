"""Stop only verified processes created for this milestone evaluation."""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import httpx

OWNED = {
    43260: "som-rs/target/composed-quality/release/som serve --model som-rs/models/composed-quality-candidate --blocks som-code-python/data/blocks/composed-quality/blocks.json --port 8770",
    12576: "som-rs/target/flow-m2/debug/som serve --model som-rs/models/composed-quality-candidate --blocks som-code-python/data/blocks/composed-quality/blocks.json --port 8774",
    33258: "som-rs/target/flow-m2/debug/som serve --model som-rs/models/composed-quality-candidate --blocks som-code-python/data/blocks/composed-quality/blocks.json --port 8775",
    43028: "/private/tmp/som-business-eval-venv/bin/python som-core/scripts/local_business_services.py --root /private/tmp/som-m1-fixture-2026-10-02-b",
    60711: "/private/tmp/som-business-eval-venv/bin/python som-core/scripts/local_business_services.py --root /private/tmp/som-m1-fixture-2026-10-02-c",
}


def command_for(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False)
    return result.stdout.strip()


def default_health():
    try:
        response = httpx.get("http://127.0.0.1:8765/healthz", timeout=5)
        return {"http_status": response.status_code, "response": response.json()}
    except httpx.HTTPError as error:
        return {"unavailable": type(error).__name__}


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.2):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = default_health()
    results = []
    for pid, expected in OWNED.items():
        actual = command_for(pid)
        row = {"pid": pid, "expected_command": expected, "observed_command": actual}
        if actual == expected:
            os.kill(pid, signal.SIGTERM)
            row["signal"] = "SIGTERM"
        elif actual:
            row["signal"] = "none; PID changed; process preserved"
        else:
            row["signal"] = "none; already stopped"
        results.append(row)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and any(command_for(pid) == expected for pid, expected in OWNED.items()):
        time.sleep(.2)
    for row in results:
        row["stopped"] = not command_for(row["pid"])
    ports = {}
    for name in ("b", "c"):
        manifest = json.loads(Path("/private/tmp/som-m1-fixture-2026-10-02-" + name + "/manifest.json").read_text())
        for kind, port in manifest["ports"].items():
            ports[name + ":" + kind] = {"port": port, "open": port_open(port)}
    after = default_health()
    result = {"processes": results, "fixture_ports": ports,
              "default_before": before, "default_after": after,
              "default_unchanged": before == after, "deleted_files": [],
              "passed": all(row["stopped"] for row in results)
                        and not any(row["open"] for row in ports.values()) and before == after}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stopped": sum(row["stopped"] for row in results),
                      "fixture_ports_closed": not any(row["open"] for row in ports.values()),
                      "default_unchanged": before == after, "passed": result["passed"]}))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
