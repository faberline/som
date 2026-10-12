"""Verify the explicit v7 gate through HTTP and the file-free Rust CLI."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    prompt = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl").read_text().splitlines()[0])["prompt"]
    prompt_file = args.output / "prompt.json"
    prompt_file.write_text(json.dumps(prompt, indent=2) + "\n")
    project = args.output / "empty-project"
    project.mkdir()
    binary = ROOT / "som-rs/target/flow-m2/debug/som"
    with httpx.Client(base_url=args.url, timeout=65) as client:
        health = client.get("/healthz").json()
        response = client.post("/v1/generate", json=prompt)
    command = [str(binary), "generate", "--url", args.url, str(prompt_file)]
    cli = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False, timeout=65)
    (args.output / "cli.stdout").write_text(cli.stdout)
    (args.output / "cli.stderr").write_text(cli.stderr)
    output = json.loads(cli.stdout)
    body = response.json()
    files = sorted(str(path.relative_to(project)) for path in project.rglob("*"))
    result = {
        "stage": "generation_gate; not accepted flow inference",
        "http_status": response.status_code,
        "response": body,
        "health": health,
        "cli_command": command,
        "cli_exit_code": cli.returncode,
        "cli_matches_http": output == body,
        "writes_project_files": bool(files),
        "project_entries": files,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt_file.read_bytes()).hexdigest(),
        "passed": response.status_code == 500 and body.get("code") == "MODEL_UNAVAILABLE"
                  and output == body and cli.returncode == 2 and not files,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("http_status", "cli_exit_code", "writes_project_files", "passed")}))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()
