#!/usr/bin/env python3
"""Build the Rust output phase 2 behavior record (issue #52).

usage (under som-rs/runtime/acceptance-m3-venv/bin/python):
  build_phase2_behavior.py --run DIR [--superseded OLD ...]           write contracts/rust-output-v1/phase2/behavior.json
  build_phase2_behavior.py --run DIR [--superseded OLD ...] --check   fail unless the committed record is this output

DIR is a complete run of som-rs/behavior/phase2/check_phase2.py on the accepted Python products,
summarized with check_phase2.py summarize. The record names the checker of every case, what each
adaptation of adapt.py changes and on how many cases, the run result and every case that did not
pass with its explanation. A failed case without an explanation below stops the build. An earlier
complete run that a suite fix superseded is recorded with its failures and the fix; a complete run the host
slept through is recorded with the sleep window and the flow failures inside it; a complete run that a
harness change superseded is recorded with that change.

The phase 2 Rust reference is the suite oracle of each checker, not a Python product: a Rust
product passes a case when the same checker passes on it. Python products that fail their own
oracle are recorded as python_defects; prompts the phase 2 profile refuses as phase2_refusals.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
REPO = SOM_RS.parent
CONTRACT = SOM_RS / "contracts" / "rust-output-v1"
OUT = CONTRACT / "phase2" / "behavior.json"
INVENTORY = CONTRACT / "phase2" / "case-inventory.json"
PHASE2 = SOM_RS / "behavior" / "phase2"
MANIFEST = PHASE2 / "adapted" / "MANIFEST.json"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PHASE2))
import check_phase2  # type: ignore[import-not-found]  # noqa: E402
import phase2_host  # type: ignore[import-not-found]  # noqa: E402

PYTHON_DEFECT_ISSUE = 57
# A field or relation named self makes the Python product answer 500 on create. The suite oracle
# expects the row; Rust follows the oracle (naming.json W1 and R2).
PYTHON_DEFECTS = {
    "RS07_CNL_FIELD_SELF": "a field named self",
    "RS14_CNL_V5_FOREIGN_KEY_SELF": "a relation whose foreign key field is self",
    "RS16_CNL_V5_FIRST_REFUSAL": "a relation whose foreign key field is self, with a field a__b",
}
PHASE2_REFUSALS = {"RS12_CNL_V3_FILTER_LIMIT": "query_parameter_collision",
                   "RS13_JSON_V3_FILTER_OFFSET": "query_parameter_collision"}
# Suite defects found by a complete run and fixed before the run this record accepts.
SUITE_FIXES = {
    "m2-semantic-05": "EXISTING bound an object key input to the seeded object although the flow puts that key "
                      "itself; phase2_host.existing_bindings now skips keys the flow puts",
    "supplement-gcs-object-read": "the same EXISTING defect",
}
# Complete runs the host slept through. A failure there counts as explained only if it is a flow case that
# finished inside the recorded sleep window; the run is superseded by a run on an awake host.
HOST_SLEEPS = {
    "behavior-python-002": {
        "slept_at": "2026-10-16T11:33:24+00:00",
        "awake_at": "2026-10-16T15:29:07+00:00",
        "evidence": "pmset -g log: 'Entering Sleep state due to Clamshell Sleep' at 2026-10-16 19:33:24 +0800, "
                    "then only DarkWake maintenance windows until 'DarkWake to FullWake' at 23:29:07 +0800",
        "explanation": "the lease, journal and five-second heartbeat checks time out when the host sleeps "
                       "between two observations",
    },
}

# Complete runs whose harness a later amendment changed. Their failures must still be explained; the
# record accepts the run on the pinned harness.
HARNESS_CHANGES = {
    "behavior-python-003": "amendment 003 changed the pinned harness: phase2_host.RustProduct reads SOM_APP_ADDR "
                           "and check_phase2.py gained --language rust; the Python baseline ran again on it",
}

CHECKERS = {
    "phase1_suite": "The phase 1 HTTP suite (som-rs/behavior/suite.py) for v1 to v5 without capabilities: "
                    "CRUD, nulls, validation, pagination, filters, ordering, revisions, relations and the "
                    "error bodies, against its oracle.",
    "verify_all": "verify_composed_services.verify_all, standard: auth accept and reject, PostgreSQL and "
                  "MongoDB persistence and migrations, object put and get, cache, workflow outputs and "
                  "transactions, the start without database configuration and the product's own tests.",
    "verify_all_subset": "verify_all with standard false, as the accepted legacy214 run checked every group "
                         "but services14.",
    "flow": "verify_flow_service.verify, passing on full_m2_acceptance: workflow outputs, step results, "
            "rollback, branches, undo, idempotency, leases, revision conflicts and the provider fault matrix.",
}

# name: (what it changes, adapt.py replacement names, observed marker or None, static predicate or None)
NOT_APPLICABLE = {
    "LEASE": ("the flow has no database operation that a gate can hold",
              "A read-only flow is held at the table it reads; a flow without a database operation records "
              "its lease checks as not applicable.",
              ["LEASE_READ", "LEASE_ROLE", "LEASE_GATE", "READ_GATE", "FIRST_READ"]),
    "PROVIDER_SHAPE": ("the flow puts no object after a database write",
                       "The provider fault matrix needs an object put after a database write.",
                       ["PROVIDER_SHAPE"]),
    "REVISION_ROUTE": ("the product has no PATCH route for the independent edit",
                       "The revision_conflict phase needs a PATCH route for every written resource.",
                       ["REVISION_ROUTE"]),
    "CSV_UNIQUE": ("csv_import_late_conflict_not_applicable_without_unique_child_field",
                   "The late conflict of a CSV import needs a unique child field.",
                   ["CSV_UNIQUE"]),
}
COVERAGE = {
    "PIVOT": ("The branch probes also cover a comparison of two public inputs.", ["PIVOT"]),
    "SUBSET": ("A flow prompt that lists an operation subset is checked by the subset verifier.", ["SUBSET"]),
    "EXISTING": ("An input naming an existing child or object is bound to one seeded through the HTTP API. An "
                 "object key input the flow puts itself is left as the original verifier binds it; that rule was "
                 "added after run 001, where binding such a key failed the legacy m2-semantic-05 and "
                 "supplement-gcs-object-read (superseded_runs). With it, no legacy flow case gets an EXISTING binding.",
                 ["EXISTING_SEED", "EXISTING_DEF", "EXISTING_RETURN", "EXISTING_MAIN", "EXISTING_FAULTS"]),
    "CSV_INPUT": ("The csv argument of workflow.import gets CSV text under any input name.",
                  ["CSV_INPUT_IMPORT", "CSV_INPUT"]),
    "CONFLICT": ("The idempotency conflict body covers every first input type; a flow without inputs has no "
                 "conflict body.", ["CONFLICT"]),
    "DATABASE_ONLY": ("A flow whose only writes are object puts has no database write to compensate.",
                      ["DATABASE_ONLY"]),
}
HOST = ("Install, server command, migrations, the start without database configuration, the product's own "
        "tests, the owned-process check and the route surface go through phase2_host.host_for(root); "
        "PythonProduct runs the original commands.")


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def static_counts(flows: list[dict]) -> dict[str, dict[str, int]]:
    """How many flow cases each coverage adaptation reaches, from the prompts alone."""
    def count(test) -> int:
        return sum(bool(test(prompt)) for prompt in flows)

    def first_input_beyond_original(prompt: dict) -> bool:
        first = prompt["flow"]["inputs"][0]
        if first["type"] in ("boolean", "integer"):
            return False
        if first["type"] in ("parent_input", "child_input"):
            fields = prompt["resources"][first["type"].split("_")[0]]["fields"]
            return not any(f["name"] == "title" for f in fields)
        return True

    return {
        "SUBSET": {"flow_cases": count(lambda p: len(p.get("operations", ())) not in (0, 11))},
        "EXISTING": {"flow_cases": count(phase2_host.existing_bindings)},
        "CSV_INPUT": {"flow_cases": count(lambda p: phase2_host.csv_inputs(p) - {"csv"})},
        "CONFLICT": {"flow_cases_without_inputs": count(lambda p: not p["flow"]["inputs"]),
                     "flow_cases_beyond_original": count(lambda p: p["flow"]["inputs"]
                                                         and first_input_beyond_original(p))},
        "DATABASE_ONLY": {"flow_cases": count(lambda p: {n["op"] for n in phase2_host.flow_nodes(p["flow"]["steps"])
                                                          if n["op"] in phase2_host.DATABASE_WRITES | {"object.put"}}
                                               == {"object.put"})},
        "PIVOT": {},
    }


def superseded_run(old: pathlib.Path) -> dict[str, Any]:
    summary_path = old / "summary.json"
    summary = json.loads(summary_path.read_text())
    if not summary["complete"]:
        raise SystemExit(f"{summary_path}: the superseded run is not complete")
    explained = set(PYTHON_DEFECTS) | set(PHASE2_REFUSALS) | set(SUITE_FIXES)
    sleep = HOST_SLEEPS.get(old.name)
    slept = []
    for case_id in sorted(set(summary["failed"]) - explained):
        record = json.loads((old / "cases" / f"{check_phase2.key(case_id)}.json").read_text())
        if sleep and record["checker"] == "flow" and sleep["slept_at"] <= record["finished_at"] <= sleep["awake_at"]:
            slept.append({"id": case_id, "finished_at": record["finished_at"], "problems": record["problems"]})
        else:
            raise SystemExit(f"{old}: {case_id} failed without an explanation")
    result = {"out": rel(old), "summary_sha256": sha(summary_path), "total": summary["total"],
              "passed": summary["passed"], "failed": summary["failed"],
              "suite_fixes": {i: SUITE_FIXES[i] for i in summary["failed"] if i in SUITE_FIXES}}
    if sleep:
        result["host_sleep"] = {**sleep, "failed_while_asleep": slept}
    if old.name in HARNESS_CHANGES:
        result["harness_change"] = HARNESS_CHANGES[old.name]
    return result


def build(run: pathlib.Path, superseded: list[pathlib.Path]) -> dict[str, Any]:
    summary_path = run / "summary.json"
    summary = json.loads(summary_path.read_text())
    if not summary["complete"]:
        raise SystemExit(f"{summary_path}: the run is not complete, missing {summary['missing'][:5]}")
    inventory = {row["id"]: row for row in json.loads(INVENTORY.read_text())["rows"]}
    every = check_phase2.cases()
    if sorted(c["id"] for c in every) != sorted(inventory):
        raise SystemExit("the run cases differ from case-inventory.json")
    for case_id, digest in summary["case_sha256"].items():
        if sha(run / "cases" / f"{check_phase2.key(case_id)}.json") != digest:
            raise SystemExit(f"{case_id}: the case result differs from summary.json")

    failed = set(summary["failed"])
    explained = set(PYTHON_DEFECTS) | set(PHASE2_REFUSALS)
    if failed - explained:
        raise SystemExit(f"failed cases without an explanation: {sorted(failed - explained)}")
    if explained - failed:
        raise SystemExit(f"explained cases that passed: {sorted(explained - failed)}")
    for case_id, rule in PHASE2_REFUSALS.items():
        if inventory[case_id]["phase2_expected"] != {"result": "refusal", "rule": rule}:
            raise SystemExit(f"{case_id}: the inventory does not expect {rule}")
    refused = {i for i, row in inventory.items() if row["phase2_expected"]["result"] == "refusal"}
    if refused != set(PHASE2_REFUSALS):
        raise SystemExit(f"the inventory refusals {sorted(refused)} differ from {sorted(PHASE2_REFUSALS)}")

    markers: collections.Counter = collections.Counter()
    flows = []
    for case in every:
        text = (run / "cases" / f"{check_phase2.key(case['id'])}.outcome.json").read_text()
        for name, entry in NOT_APPLICABLE.items():
            markers[name] += entry[0] in text
        if case["checker"] == "flow":
            flows.append(check_phase2.load(case)[0])
    static = static_counts(flows)

    defects = {}
    for case_id, cause in PYTHON_DEFECTS.items():
        row = json.loads((run / "cases" / f"{check_phase2.key(case_id)}.json").read_text())
        statuses = collections.Counter(problem for failure in row["first_failures"]
                                       for problem in failure["problems"] if problem.startswith("status "))
        defects[case_id] = {"cause": cause, "issue": PYTHON_DEFECT_ISSUE, "checker": row["checker"],
                            "oracle_mismatches": row["failures"],
                            "first_failures_sampled": len(row["first_failures"]),
                            "first_failure_statuses": dict(sorted(statuses.items())),
                            "rust_reference": "the suite oracle (naming.json W1, R2)"}
    refusals = {}
    for case_id, rule in PHASE2_REFUSALS.items():
        row = json.loads((run / "cases" / f"{check_phase2.key(case_id)}.json").read_text())
        refusals[case_id] = {"rule": rule, "python_product": "does not start: "
                             + ("SyntaxError, duplicate argument" if "duplicate argument" in row["problems"][0]
                                else row["problems"][0][:120]),
                             "rust": "refused at compile (rust_profile_phase2.py); not a behavior case"}

    manifest = json.loads(MANIFEST.read_text())
    passed_products = sum(1 for i in inventory if i not in failed)
    return {
        "version": "som.rust-output-phase2-behavior.v1",
        "issue": 52,
        "rules": [
            "A phase 2 case is checked by the checker its inventory row names, with the adapted verifiers of "
            "som-rs/behavior/phase2/adapted (adapt.py) and the phase2_host language steps.",
            "The Rust reference is the checker's oracle. A Rust product passes a case when its checker passes "
            "and no assembled file changed during the run.",
            "A Python product that fails its own oracle is a python_defect: Rust is still checked against the "
            "oracle and the Python output stays byte-identical.",
            "A case the phase 2 profile refuses is not a behavior case for Rust; Rust refuses its prompt.",
            "Not-applicable records are counted from the outcomes of the Python run; a Rust run must record "
            "the same cases as not applicable.",
        ],
        "sources": {rel(path): sha(path) for path in
                    (INVENTORY, MANIFEST, PHASE2 / "check_phase2.py", PHASE2 / "phase2_host.py",
                     PHASE2 / "adapt.py")},
        "checkers": {name: {"covers": text, **summary["by_checker"].get(name, {"total": 0, "run": 0, "passed": 0})}
                     for name, text in CHECKERS.items()},
        "adaptations": {
            "host_steps": HOST,
            "modules": {name: {"original_sha256": manifest["originals"][f"som-core/scripts/{name}.py"],
                               "adapted_sha256": manifest["adapted"][f"{name}.py"], "replacements": count}
                        for name, count in sorted(manifest["replacements"].items())},
            "not_applicable": {name: {"marker": marker, "what": what, "replacements": parts,
                                      "observed_cases": markers[name]}
                               for name, (marker, what, parts) in NOT_APPLICABLE.items()},
            "coverage": {name: {"what": what, "replacements": parts, **static[name]}
                         for name, (what, parts) in COVERAGE.items()},
        },
        "python_run": {
            "out": rel(run),
            "summary_sha256": sha(summary_path),
            "total": summary["total"], "run": summary["run"], "passed": summary["passed"],
            "failed": summary["failed"],
            "by_source": summary["by_source"], "by_version": summary["by_version"],
        },
        "acceptance": {
            "phase2_products": len(inventory) - len(PHASE2_REFUSALS),
            "passed": passed_products,
            "python_defects": sorted(PYTHON_DEFECTS),
            "statement": f"The phase 2 suite passes on {passed_products} of the {len(inventory)} phase 2 cases. "
                         f"The {len(PYTHON_DEFECTS)} python_defects fail their oracle on the Python product and are "
                         f"tracked in #{PYTHON_DEFECT_ISSUE}; the {len(PHASE2_REFUSALS)} phase2_refusals are refused "
                         "prompts whose Python product does not start. Every legacy job, CNL fixture and public "
                         "example passes.",
        },
        "superseded_runs": [superseded_run(old) for old in superseded],
        "python_defects": defects,
        "phase2_refusals": refusals,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=pathlib.Path, required=True)
    parser.add_argument("--superseded", type=pathlib.Path, nargs="*", default=[])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(args.run.resolve(), [old.resolve() for old in args.superseded]),
                      indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{rel(OUT)} differs from build_phase2_behavior.py output", file=sys.stderr)
            return 1
        print(f"{rel(OUT)} is current")
        return 0
    OUT.write_text(text)
    print(f"wrote {rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
