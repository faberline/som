"""One bounded real-Codex delivery probe; preserve each attempt."""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "som-rs/evals/composable-2026-10-01"
work = Path("/private/tmp/som-composable-transport-probe-2026-10-01")
attempt = KIT / f"transport-probe-{len(list(KIT.glob('transport-probe-*')))+1:03d}"
attempt.mkdir()
prompt = (work / "instruction.txt").read_text()
(attempt / "instruction.txt").write_text(prompt)
started = time.time()
with (attempt / "events.jsonl").open("w") as log, (attempt / "stderr.log").open("w") as err:
    process = subprocess.Popen(["codex", "exec", "-C", str(work), "--skip-git-repo-check", "--json", "--model", "gpt-6-astra", "--sandbox", "read-only", "-c", "model_reasoning_effort=high", "-"], stdin=subprocess.PIPE, stdout=log, stderr=err, text=True)
    try:
        process.communicate(prompt, timeout=240)
    except subprocess.TimeoutExpired:
        process.terminate(); process.wait(timeout=15)
(attempt / "metadata.json").write_text(json.dumps({"exit_code": process.returncode, "seconds": time.time()-started, "directory": str(work)}, indent=2)+"\n")
print(json.dumps({"attempt": str(attempt), "exit_code": process.returncode}))
