"""Save the final partial handoff, including preserved work and open gates."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "som-rs/evals/m2-flow-2026-10-02/r1"
M1 = ROOT / "som-rs/evals/m1-closeout-2026-10-02/r1"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_sections(content):
    starts = list(re.finditer(r"(?m)^diff --git a/([^ ]+) b/[^\n]+\n", content))
    return {match[1]: content[match.start():starts[index + 1].start() if index + 1 < len(starts) else len(content)]
            for index, match in enumerate(starts)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    snapshot = read(EVAL / "snapshot-002/result.json")
    tracker = read(EVAL / "tracker-001/result.json")
    cleanup = read(EVAL / "cleanup-001/result.json")
    assert snapshot["m1_passed"] and not snapshot["m2_passed"]
    assert tracker["passed"] and cleanup["passed"]
    changed = [name for name, value in snapshot["protected_asset_sha256"].items() if digest(ROOT / name) != value]
    assert not changed
    baseline = read(M1 / "baseline/baseline.json")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == baseline["git_head"]
    current = subprocess.check_output(["git", "diff", "--binary"], cwd=ROOT, text=True)
    original = (M1 / "closeout-002/supplement/tracked-wip.patch").read_text()
    before, after = patch_sections(original), patch_sections(current)
    authored_docs = {"README.md", "som-core/STATUS.md"}
    preserved = {name: before[name] == after.get(name) for name in before if name not in authored_docs}
    assert all(preserved.values()), preserved
    (args.output / "tracked-work.patch").write_text(current)
    (args.output / "git-status.txt").write_bytes(subprocess.check_output(["git", "status", "--short"], cwd=ROOT))
    paths = [ROOT / name for name in (
        "README.md", "som-core/STATUS.md", "som-rs/M1_CLOSEOUT.md", "som-rs/M2_FLOW_WORK.md",
        "som-core/scripts/record_m2_tracker.py", "som-core/scripts/stop_milestone_fixtures.py")]
    paths.append(Path(__file__).resolve())
    paths += [args.output / "tracked-work.patch", args.output / "git-status.txt"]
    paths += [EVAL / name for name in ("snapshot-002/result.json", "tracker-001/result.json", "cleanup-001/result.json")]
    with tarfile.open(args.output / "handoff.tar.gz", "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    result = {
        "version": "som.milestone-handoff.v1", "m1_passed": True, "m2_passed": False,
        "milestones": {"M1": "closed", "M2": "open"}, "issues": {"1-3": "closed", "4-6": "open"},
        "protected_assets_changed": changed, "unrelated_tracked_work_preserved": preserved,
        "task_document_changes": sorted(authored_docs), "git_head": head,
        "current_unchanged": True, "paid_agent_runs": 0, "test_weights_created": False,
        "source_archive_sha256": snapshot["source_archive_sha256"],
        "handoff_archive_sha256": digest(args.output / "handoff.tar.gz"),
        "files": {str(path.relative_to(ROOT)): digest(path) for path in paths},
        "first_unmet_gate": snapshot["formal_artifact_budget"],
        "next": "reduce standalone source size; then complete quality, Metal smoke and independent flow runtime acceptance",
        "passed_preservation_and_recording": True,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("m1_passed", "m2_passed", "protected_assets_changed", "unrelated_tracked_work_preserved", "handoff_archive_sha256")}))


if __name__ == "__main__":
    main()
