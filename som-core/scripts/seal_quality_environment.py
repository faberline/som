"""Hash the actual check environment without recording host secrets."""

import base64
import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "som-rs/evals/composed-quality-2026-10-01/environment.json")
    destination = parser.parse_args().output
    if destination.exists():
        raise ValueError("preserve previous environment seal")
    distributions = []
    mismatches = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        record = distribution.read_text("RECORD")
        hashes = {}
        if record:
            for name, expected, _ in csv.reader(io.StringIO(record)):
                if expected.startswith("sha256="):
                    path = Path(distribution.locate_file(name))
                    digest = hashlib.sha256(path.read_bytes()).digest()
                    hashes[name] = digest.hex()
                    if base64.urlsafe_b64encode(digest).decode().rstrip("=") != expected.split("=", 1)[1]:
                        mismatches.append({"distribution": distribution.metadata["Name"], "path": name})
        distributions.append({"name": distribution.metadata["Name"], "version": distribution.version,
                              "record_sha256": hashlib.sha256((record or "").encode()).hexdigest(),
                              "files_sha256": hashes})
    result = {"python": platform.python_version(), "platform": platform.platform(),
              "executable": sys.executable, "distributions": distributions,
              "mismatches": mismatches, "passed": not mismatches}
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"distributions": len(distributions), "mismatches": len(mismatches)}))
    raise SystemExit(bool(mismatches))


if __name__ == "__main__":
    main()
