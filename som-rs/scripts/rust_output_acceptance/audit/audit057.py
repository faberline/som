#!/usr/bin/env python3
"""Independent audit of an issue #57/#58 acceptance run (rust-output CONTRACT.md s12, compiler-v1 amendment 004).

Written from som-rs/contracts/rust-output-v1/acceptance-plan.json (issue57), CONTRACT.md section 12, the
frozen contract and test data, and the raw outputs of a run only.  It does not import or run any acceptance
tool, does not contact any port and only reads files (git is used read-only to compare the candidate source
with the tools commit).  Every verdict is derived again from recorded raw values (status codes, codes,
hashes, file bytes); the run's own ``passed`` and ``summary`` values are never the verdict.  Where the only
raw record of a sub-check is a per-case boolean, the evidence says "attested".  Anything that cannot be
checked is "unrun".

Frozen compiler-v1 files are judged through the sealed receipt chain COMPILER_RECEIPTS (amendment 003, then
amendment 004, which keeps the M2 library at flow-v7-compact/blocks.json and has the compiler embed
flow-v7-compact-self/blocks.json): an amendment 003 file may differ on disk only when a later sealed, APPROVED
receipt that names the exact previous receipt bytes records the change, with an archived copy of the old bytes.
The candidate's health selection_policy_sha256 and blocks_sha256 are compared with the last receipt of that chain.

Usage:
    audit057.py --run RUN_DIR --out NEW_REPORT.json

Exit status is 0 only when the report has no failed and no unrun row.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VERSION = "som.rust-output-audit-057.v1"
AUDIT_REL = "som-rs/scripts/rust_output_acceptance/audit/audit057.py"
DEFAULT_PORT = 8765
LIST_LIMIT = 15
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
FORBIDDEN_CODES = {"UNSUPPORTED_TARGET", "NOT_IMPLEMENTED", "OUTPUT_QUALITY_FAILED"}
# Health backend descriptor of the accepted #55 candidate (rust-output-acceptance-2026-10-20-phase2.json).
OLD_BACKEND_SHA256 = "1c18257000d2519e85c644d5bb243d5ec7251878efadf4cf27f3bc5b26a2e92b"
ADMITTED_DESCRIPTORS = {"backend", "block_library", "catalog", "compiler_contract", "selection_policy"}
ACC_RUN_REL = ".local/rust-compiler-goal/acceptance/rust-output-055/run-003"
ACC_EVIDENCE_REL = "som-rs/evidence/rust-output-acceptance-2026-10-20-phase2.json"
# compiler-v1 receipt chain, oldest first: amendment 003 (#57/#58 freeze) and every later sealed compiler-v1
# amendment that may supersede one of its frozen files (amendment 004 keeps the M2 block library).
COMPILER_RECEIPTS = ("som-rs/evidence/rust-compiler-contract-amendment-003.json",
                     "som-rs/evidence/rust-compiler-contract-amendment-004.json")
RUST_OUTPUT_RECEIPT = "som-rs/evidence/rust-output-contract-amendment-004.json"
SEALED_STATUS = "amendment_sealed_after_independent_review"
COVERAGE_FROZEN_REL = "som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json"
SELF_FIXTURES_REL = "som-rs/tests/compiler-v1/self-name-fixtures.json"
CNL_FIXTURES_REL = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
TYPED_FIXTURES_REL = "som-rs/tests/compiler-v1/typed-json-fixtures.json"
EXAMPLES_DIR_REL = "som-rs/contracts/compiler-v1/examples"
LEGACY_FAMILIES = {"legacy100": 100, "legacy214": 214, "quality218": 218, "supplement12": 12}
RS_SELF = ("RS07_CNL_FIELD_SELF", "RS14_CNL_V5_FOREIGN_KEY_SELF", "RS16_CNL_V5_FIRST_REFUSAL")
HEAVY_DIRS = {"work", "target", "fixtures", "products", "logs", "receipts"}
NEG_ID = re.compile(r"\b(R[TSP]\d\d_[A-Z0-9_]+)")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ----------------------------------------------------------------- helpers

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_TREE_CACHE: dict[Path, str] = {}


def tree_sha(root: Path) -> str:
    """sha256 over sorted 'relpath NUL sha256(file) LF' records."""
    key = Path(root).resolve()
    if key not in _TREE_CACHE:
        files = []
        for dirpath, _dirs, names in os.walk(key):
            for name in names:
                p = Path(dirpath) / name
                files.append((os.path.relpath(p, key), p))
        h = hashlib.sha256()
        for rel, p in sorted(files):
            h.update(rel.encode() + b"\0" + sha_file(p).encode() + b"\n")
        _TREE_CACHE[key] = h.hexdigest()
    return _TREE_CACHE[key]


def files_sha(root: Path, skip: set[str] | None = None) -> tuple[str, int]:
    """files_sha256 formula: sha256 of the compact sorted JSON map path->text.

    check-results/ and target/ at the top are run artifacts; ``skip`` names extra top-level entries
    (the case row's extra_files, such as uv.lock or a built binary)."""
    skip = set(skip or ()) | {"check-results", "target"}
    texts: dict[str, str] = {}
    for dirpath, _dirs, names in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != "." and rel_dir.split(os.sep)[0] in skip:
            continue
        for name in names:
            p = Path(dirpath) / name
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel in skip:
                continue
            texts[rel] = p.read_bytes().decode("utf-8")
    blob = json.dumps(texts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha_bytes(blob), len(texts)


def compact_shas(obj) -> set[str]:
    """Hashes of the compact JSON serialization of obj, with and without sorted keys."""
    out = set()
    for sk in (True, False):
        out.add(sha_bytes(json.dumps(obj, sort_keys=sk, separators=(",", ":"), ensure_ascii=False).encode()))
    return out


def load_json(path: Path):
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read()), None
    except FileNotFoundError:
        return None, f"{path} missing"
    except (OSError, ValueError) as exc:
        return None, f"{path} unreadable: {exc.__class__.__name__}"


def first(items, limit=LIST_LIMIT):
    items = sorted(map(str, items))
    more = len(items) - limit
    return items[:limit] + ([f"... {more} more"] if more > 0 else [])


_CHAIN_CACHE: dict[Path, dict] = {}


def compiler_chain(repo: Path) -> dict:
    """Walk the compiler-v1 receipt chain COMPILER_RECEIPTS from raw receipt bytes.

    The first receipt's ``files`` is the frozen set.  A later receipt is admitted only when it is sealed
    (status SEALED_STATUS) with an APPROVED independent_review, names the previous receipt and its exact sha256,
    and keeps every frozen path in its own ``files``.  Each admitted ``changes`` entry must start from the hash
    the chain holds for that path (old_sha256), have an archived copy that hashes to it, and end at the new
    receipt's ``files`` hash (new_sha256); any other difference between consecutive ``files`` maps is a problem.
    Returns {"expected": path -> hash the disk must hold, "base": first files map, "superseded": path ->
    [(receipt, old, new, archive)], "last": last admitted receipt, "last_rel", "admitted": [rel], "problems",
    "unrun"}.  Disk hashes are compared by the caller."""
    key = Path(repo).resolve()
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key]
    out = {"expected": {}, "base": {}, "superseded": {}, "last": None, "last_rel": None, "admitted": [],
           "problems": [], "unrun": []}
    _CHAIN_CACHE[key] = out
    base_rel = COMPILER_RECEIPTS[0]
    base, err = load_json(key / base_rel)
    if err or not isinstance((base or {}).get("files"), dict):
        out["unrun"].append(f"{base_rel} unreadable or without files")
        return out
    if base.get("status") != SEALED_STATUS:
        out["problems"].append(f"{base_rel} status is {base.get('status')!r}, not {SEALED_STATUS}")
    if ((base.get("independent_review") or {}).get("final_verdict")) != "APPROVED":
        out["problems"].append(f"{base_rel} has no APPROVED independent_review")
    out["base"] = dict(base["files"])
    out["expected"] = dict(base["files"])
    out["last"], out["last_rel"] = base, base_rel
    out["admitted"].append(base_rel)
    prev_rel, prev = base_rel, base
    for rel in COMPILER_RECEIPTS[1:]:
        rec, err = load_json(key / rel)
        if err or not isinstance((rec or {}).get("files"), dict):
            out["unrun"].append(f"{rel} unreadable or without files")
            return out
        bad = []
        if rec.get("status") != SEALED_STATUS:
            bad.append(f"status is {rec.get('status')!r}, not {SEALED_STATUS}")
        if ((rec.get("independent_review") or {}).get("final_verdict")) != "APPROVED":
            bad.append("no APPROVED independent_review")
        if rec.get("previous_receipt") != prev_rel:
            bad.append(f"previous_receipt is {rec.get('previous_receipt')!r}, not {prev_rel}")
        prev_path = key / prev_rel
        prev_sha = sha_file(prev_path) if prev_path.is_file() else None
        if rec.get("previous_receipt_sha256") != prev_sha:
            bad.append(f"previous_receipt_sha256 {str(rec.get('previous_receipt_sha256'))[:16]} is not the "
                       f"sha256 {str(prev_sha)[:16]} of {prev_rel}")
        dropped = sorted(set(prev["files"]) - set(rec["files"]))
        if dropped:
            bad.append(f"drops frozen paths of {prev_rel}: {first(dropped)}")
        if bad:
            out["problems"].extend(f"{rel}: {b}; its changes are not admitted" for b in bad)
            return out
        changed = {}
        for ch in rec.get("changes") or []:
            path = (ch or {}).get("path")
            old, new = (ch or {}).get("old_sha256"), (ch or {}).get("new_sha256")
            arch = (ch or {}).get("archived_copy")
            msgs = []
            if not path or not HEX64.match(str(old)) or not HEX64.match(str(new)):
                out["problems"].append(f"{rel}: change entry without path, old_sha256 and new_sha256: {ch}")
                continue
            if path in out["expected"] and old != out["expected"][path]:
                msgs.append(f"old_sha256 {old[:16]} is not the chain hash {out['expected'][path][:16]}")
            if rec["files"].get(path) != new:
                msgs.append(f"new_sha256 {new[:16]} is not its files hash {str(rec['files'].get(path))[:16]}")
            ap = key / str(arch or "")
            if not arch or not ap.is_file():
                msgs.append("archived copy missing")
            elif sha_file(ap) != old or ch.get("archived_copy_sha256") != old:
                msgs.append(f"archived copy {arch} does not hash to old_sha256 {old[:16]}")
            if msgs:
                out["problems"].append(f"{rel}: change {path}: " + "; ".join(msgs))
                continue
            changed[path] = new
            out["superseded"].setdefault(path, []).append((rel, old, new, arch))
        for path, want in rec["files"].items():
            if path in changed:
                continue
            if path in out["expected"] and out["expected"][path] != want:
                out["problems"].append(f"{rel}: files hash of {path} differs from {prev_rel} without a "
                                       f"changes entry")
        out["expected"] = {**out["expected"], **rec["files"]}
        out["last"], out["last_rel"] = rec, rel
        out["admitted"].append(rel)
        prev_rel, prev = rel, rec
    return out


def argv_value(argv: list[str], flag: str):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


# --------------------------------------------------------------- reporting

class Row:
    def __init__(self, section: str, index, check: str):
        self.section = section
        self.index = index
        self.check = check
        self.evidence: list[str] = []
        self.problems: list[str] = []
        self.unrun_reasons: list[str] = []

    def ev(self, msg: str):
        self.evidence.append(msg)

    def fail(self, msg: str):
        self.problems.append(msg)

    def unrun(self, msg: str):
        self.unrun_reasons.append(msg)

    @property
    def state(self) -> str:
        if self.problems:
            return "failed"
        if self.unrun_reasons or not self.evidence:
            return "unrun"
        return "passed"

    def as_dict(self) -> dict:
        problems = list(self.problems) + [f"unrun: {m}" for m in self.unrun_reasons]
        if not self.evidence and not self.problems and not self.unrun_reasons:
            problems.append("unrun: no evidence")
        return {"section": self.section, "index": self.index, "check": self.check,
                "state": self.state, "evidence": self.evidence, "problems": problems}


class Tally:
    """Per-case outcome of one sub-check."""

    def __init__(self, label: str):
        self.label = label
        self.ok: set[str] = set()
        self.failed: dict[str, list[str]] = {}
        self.unrun_: dict[str, list[str]] = {}
        self.attested: set[str] = set()

    def good(self, cid):
        self.ok.add(cid)

    def bad(self, cid, msg):
        self.failed.setdefault(cid, []).append(msg)

    def miss(self, cid, msg):
        self.unrun_.setdefault(cid, []).append(msg)

    def attest(self, cid):
        self.attested.add(cid)

    def apply(self, row: Row, expected):
        expected = set(expected)
        if not expected:
            row.unrun(f"{self.label}: no case to check")
            return set()
        failed = set(self.failed) & expected
        unrun = (set(self.unrun_) | (expected - self.ok - set(self.failed))) & expected
        unrun -= failed
        passed = expected - failed - unrun
        msg = f"{self.label}: {len(passed)}/{len(expected)} cases verified"
        att = len(self.attested & passed)
        if att:
            msg += f" ({att} of them include a per-case attested sub-check)"
        row.ev(msg)
        if failed:
            reasons: dict[str, list] = {}
            for cid in failed:
                for m in self.failed[cid]:
                    reasons.setdefault(m, []).append(cid)
            for m, ids in sorted(reasons.items(), key=lambda kv: -len(kv[1]))[:LIST_LIMIT]:
                row.fail(f"{self.label}: {m} [{len(ids)}: {', '.join(first(ids, 8))}]")
        if unrun:
            reasons = {}
            for cid in unrun:
                for m in self.unrun_.get(cid, ["no result"]):
                    reasons.setdefault(m, []).append(cid)
            for m, ids in sorted(reasons.items(), key=lambda kv: -len(kv[1]))[:LIST_LIMIT]:
                row.unrun(f"{self.label}: {m} [{len(ids)}: {', '.join(first(ids, 8))}]")
        return passed


# ------------------------------------------------------------------ context

class Ctx:
    def __init__(self, run: Path, repo: Path):
        self.run = run
        self.repo = repo
        self.contracts = repo / "som-rs" / "contracts" / "rust-output-v1"
        self.acc_run = repo / ACC_RUN_REL
        self.load_errors: list[str] = []
        self.weak: list[str] = []

        self.plan_path = self.contracts / "acceptance-plan.json"
        plan = self._must(self.plan_path) or {}
        self.plan_all = plan
        self.plan = plan.get("issue57") or {}
        self.plan_checks = list(self.plan.get("checks") or [])
        self.neg_path = self.contracts / "negative-cases.json"
        self.neg = self._must(self.neg_path) or {}
        self.tex = self._must(self.contracts / "target-examples.json") or {}
        self.inv1 = self._must(self.contracts / "case-inventory.json") or {}
        self.inv2 = self._must(self.contracts / "phase2" / "case-inventory.json") or {}
        self.behavior = self._must(self.contracts / "phase2" / "behavior.json") or {}
        self.selfx = self._must(repo / SELF_FIXTURES_REL) or {}
        self.cnlpos = self._must(repo / CNL_FIXTURES_REL) or {}
        self.typed = self._must(repo / TYPED_FIXTURES_REL) or {}
        self.frozen_cov = self._must(repo / COVERAGE_FROZEN_REL) or {}
        self.acc_evidence = self._must(repo / ACC_EVIDENCE_REL) or {}

        inv1 = self.inv1
        self.acc1 = ([x["id"] for x in inv1.get("public_examples", []) if x.get("rust") == "in_scope"]
                     + [x["id"] for x in inv1.get("version_positive_fixtures", []) if x.get("rust") == "in_scope"]
                     + [x["id"] for x in inv1.get("coverage_cases", [])]
                     + [x["id"] for x in inv1.get("legacy_in_scope", [])])
        self.coverage = [x["id"] for x in inv1.get("coverage_cases", [])]
        self.rows2 = self.inv2.get("rows", [])
        self.rows2_by_id = {r["id"]: r for r in self.rows2}
        self.refusal_ids = {r["id"] for r in self.rows2
                            if (r.get("phase2_expected") or {}).get("result") == "refusal"}
        self.product_ids = {r["id"] for r in self.rows2} - self.refusal_ids
        self.neg_by_id = {c["id"]: c for c in self.neg.get("cases", [])}
        self.neg_refusal_ids = {cid for cid, c in self.neg_by_id.items()
                                if cid.startswith("RS") and c.get("http_status") == 400}

        # Run files.
        self.header, _ = load_json(run / "header.json")
        self.header = self.header if isinstance(self.header, dict) else {}
        summary, _ = load_json(run / "summary.json")
        self.summary = summary if isinstance(summary, dict) else None
        self.receipts = self._load_receipts()
        self.targets, _ = load_json(run / "targets.json")
        self.cli, _ = load_json(run / "cli.json")
        self.pyctl, _ = load_json(run / "python-controls.json")
        self.validate, _ = load_json(run / "validate-cases.json")
        self.replay, _ = load_json(run / "python-replay.json")
        self.rproducts, _ = load_json(run / "rust-products.json")
        self.runchanged, _ = load_json(run / "rust-unchanged.json")
        self.issue57_name, self.issue57 = self._step_output("issue57")
        self.names_name, self.names = self._step_output("name-collisions")

        # The accepted #55 run (raw data).
        self.acc_header_sha = sha_file(self.acc_run / "header.json") if (self.acc_run / "header.json").is_file() else None
        self.acc_summary_sha = sha_file(self.acc_run / "summary.json") if (self.acc_run / "summary.json").is_file() else None
        self.acc_rproducts, _ = load_json(self.acc_run / "rust-products.json")
        self.acc_targets, _ = load_json(self.acc_run / "targets.json")
        self.acc_pyctl, _ = load_json(self.acc_run / "python-controls.json")
        self.acc_cli, _ = load_json(self.acc_run / "cli.json")

    def _must(self, path: Path):
        obj, err = load_json(path)
        if err:
            self.load_errors.append(err)
        return obj

    def _load_receipts(self):
        out = []
        rdir = self.run / "receipts"
        if not rdir.is_dir():
            return out
        for p in sorted(rdir.glob("*.json")):
            obj, err = load_json(p)
            out.append((p, obj, err))
        return out

    def receipt_for_step(self, step: str):
        for p, obj, _err in self.receipts:
            if isinstance(obj, dict) and obj.get("step") == step:
                return p, obj
        return None, None

    def receipt_for_output(self, name: str):
        for p, obj, _err in self.receipts:
            if isinstance(obj, dict) and name in (obj.get("outputs") or {}):
                return p, obj
        return None, None

    def _step_output(self, step: str):
        """The JSON output of a step, found through its receipt (or by the conventional name)."""
        _p, rec = self.receipt_for_step(step)
        names = [n for n in ((rec or {}).get("outputs") or {}) if n.endswith(".json")]
        for n in names + [f"{step}.json"]:
            obj, _ = load_json(self.run / n)
            if isinstance(obj, dict):
                return n, obj
        return (names[0] if names else f"{step}.json"), None

    def acc_case(self, cid: str):
        """A phase 2 case row of the accepted #55 run, by the id recorded inside it."""
        if not hasattr(self, "_acc_cases"):
            self._acc_cases = {}
            d = self.acc_run / "phase2" / "cases"
            for p in sorted(d.glob("*.json")) if d.is_dir() else []:
                if p.name.endswith(".outcome.json"):
                    continue
                obj, _ = load_json(p)
                if isinstance(obj, dict) and obj.get("id"):
                    if obj["id"] in self._acc_cases:
                        self.load_errors.append(f"duplicate #55 phase 2 case {obj['id']}")
                    self._acc_cases[obj["id"]] = obj
        return self._acc_cases.get(cid)

    @property
    def cand_url(self):
        port = (self.header.get("candidate_before") or {}).get("port")
        return f"http://127.0.0.1:{port}" if port else None


def tie_to_receipt(ctx: Ctx, row: Row, name: str, allow_exit=(0,)) -> dict | None:
    """The artifact is the recorded output of a receipt; returns the receipt."""
    path = ctx.run / name
    if not path.exists():
        row.unrun(f"{name} missing from the run")
        return None
    rp, rec = ctx.receipt_for_output(name)
    if rec is None:
        row.unrun(f"no receipt records {name}")
        return None
    want = rec["outputs"][name]
    got = sha_file(path) if not str(want).startswith("tree:") else "tree:" + tree_sha(path)
    if got != want:
        row.fail(f"{name} hash {got[:16]} differs from receipt {rp.name} ({str(want)[:16]})")
        return None
    if rec.get("exit_code") not in allow_exit:
        row.fail(f"receipt {rp.name} exit code {rec.get('exit_code')}")
        return None
    row.ev(f"{name} sha256 {got} equals receipt {rp.name} (exit {rec.get('exit_code')})")
    return rec


def git(ctx: Ctx, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ctx.repo), *args], capture_output=True)


# ----------------------------------------------------------- run integrity

def check_candidate_source(ctx: Ctx, row: Row, cand: Path, cb: dict):
    head = (ctx.header.get("tools") or {}).get("head")
    commit = cb.get("commit")
    src, err = load_json(cand / "source.json")
    if not head or not commit:
        row.unrun("header records no tools head or candidate commit")
        return
    if err or not isinstance(src, dict) or not isinstance(src.get("files"), dict) or not src.get("roots"):
        row.unrun("candidate source.json with files and roots missing")
        return
    if src.get("commit") != commit:
        row.fail("candidate source.json commit differs from candidate_before commit")
    if commit == head:
        row.ev(f"candidate commit equals the tools head {head}")
    elif git(ctx, "merge-base", "--is-ancestor", commit, head).returncode != 0:
        row.fail(f"candidate commit {commit[:12]} is not an ancestor of the tools head {head[:12]}")
        return
    else:
        diff = git(ctx, "diff", "--name-only", commit, head, "--", *src["roots"])
        changed = diff.stdout.decode().split()
        if diff.returncode != 0:
            row.unrun("git diff between the candidate commit and the tools head failed")
        elif changed:
            row.fail(f"candidate source roots changed between the candidate commit and the tools head: "
                     f"{first(changed)}")
        else:
            row.ev(f"candidate commit {commit[:12]} is an ancestor of the tools head {head[:12]} and no path "
                   f"under its {len(src['roots'])} source roots changed between them")
    bad = []
    for rel, want in sorted(src["files"].items()):
        blob = git(ctx, "show", f"{head}:{rel}")
        if blob.returncode != 0 or sha_bytes(blob.stdout) != want:
            bad.append(rel)
    if bad:
        row.fail(f"candidate source files differ at the tools head: {first(bad)}")
    else:
        row.ev(f"all {len(src['files'])} files of candidate source.json hash the same at the tools head")


def check_one_candidate(ctx: Ctx) -> Row:
    row = Row("run", 0, "One recorded candidate build: header candidate_before equals summary candidate_after, "
              "its files still hash to the recorded values, its health shows the amended descriptors, and every "
              "artifact used that candidate.")
    h = ctx.header
    if "candidate_before" not in h:
        row.unrun("header.json with candidate_before missing")
        return row
    cb = h["candidate_before"]
    health = cb.get("health") or {}
    backend = health.get("backend_sha256")
    if not backend:
        row.fail("candidate_before health records no backend_sha256")
    elif backend == OLD_BACKEND_SHA256:
        row.fail("candidate health backend_sha256 is the pre-amendment backend of the #55 candidate")
    else:
        row.ev(f"candidate health backend_sha256 {backend} (not the #55 backend {OLD_BACKEND_SHA256[:16]})")
    chain = compiler_chain(ctx.repo)
    last_rel = chain["last_rel"] or COMPILER_RECEIPTS[0]
    if chain["unrun"] or chain["problems"]:
        row.ev(f"compiler-v1 receipt chain has problems (judged in run check 5); the hashes below come from "
               f"{last_rel}, the last admitted receipt")
    sel = [v for k, v in chain["expected"].items() if k.endswith("selection-policy.json")]
    if len(sel) == 1:
        if health.get("selection_policy_sha256") == sel[0]:
            row.ev(f"candidate health selection_policy_sha256 equals the selection-policy.json {sel[0][:16]} of "
                   f"{last_rel} (version {health.get('selection_policy_version') or health.get('version')})")
        else:
            row.fail(f"candidate health selection_policy_sha256 {health.get('selection_policy_sha256')} is not "
                     f"the selection-policy.json {sel[0][:16]} of {last_rel}")
    else:
        row.unrun(f"{last_rel} (compiler-v1 receipt chain) records no single selection-policy.json hash")
    # Block library the compiler embeds: compiler-v1 amendment 004 moved it off the M2 path.
    lib = ((chain["last"] or {}).get("libraries") or {}).get("compiler") or {}
    lib_path, lib_sha = lib.get("path"), lib.get("sha256")
    if not lib_path or not HEX64.match(str(lib_sha)):
        row.unrun(f"{last_rel} records no libraries.compiler path and sha256 for the embedded block library")
    else:
        disk = ctx.repo / lib_path
        frozen = ((chain["last"] or {}).get("implementation_at_freeze") or {}).get(lib_path)
        if not disk.is_file() or sha_file(disk) != lib_sha:
            row.fail(f"{lib_path} does not hash to the {last_rel} libraries.compiler sha256 {lib_sha[:16]}")
        elif frozen != lib_sha:
            row.fail(f"{last_rel} implementation_at_freeze does not bind {lib_path} to {lib_sha[:16]}")
        elif health.get("blocks_sha256") != lib_sha:
            row.fail(f"candidate health blocks_sha256 {health.get('blocks_sha256')} is not the compiler block "
                     f"library {lib_path} {lib_sha[:16]} of {last_rel}")
        elif lib.get("blocks") is not None and health.get("block_count") != lib.get("blocks"):
            row.fail(f"candidate health block_count {health.get('block_count')} is not the {lib.get('blocks')} "
                     f"blocks of {lib_path}")
        else:
            row.ev(f"candidate health blocks_sha256 equals the compiler block library {lib_path} {lib_sha[:16]} "
                   f"({health.get('block_count')} blocks), bound by {last_rel} and hashing the same on disk")
    row.ev("health values recorded: " + ", ".join(f"{k} {str(health.get(k))[:16]}" for k in
                                                   ("asset_manifest_sha256", "blocks_sha256",
                                                    "legacy_blocks_sha256", "quality_blocks_sha256")))
    bad = [k for k, v in (cb.get("checks") or {}).items() if v is not True]
    if not cb.get("checks"):
        row.unrun("candidate_before checks missing")
    elif bad:
        row.fail(f"candidate_before checks false: {bad}")
    if cb.get("problems"):
        row.fail(f"candidate_before problems: {len(cb['problems'])}")
    port = cb.get("port")
    if port in (DEFAULT_PORT, 18765):
        row.fail(f"candidate runs on the reserved port {port}")

    cand = Path(str(cb.get("candidate", "")))
    if not cand.is_dir():
        row.fail(f"candidate directory {cand} missing")
    else:
        binary = cand / "som"
        if binary.is_file():
            got = sha_file(binary)
            if got == cb.get("binary_sha256") and binary.stat().st_size == cb.get("binary_bytes"):
                row.ev(f"candidate binary hashes to {got} ({cb.get('binary_bytes')} bytes)")
            else:
                row.fail(f"candidate binary hashes to {got}, recorded {cb.get('binary_sha256')}")
        else:
            row.fail("candidate binary file missing")
        for fname, key in (("build.json", "build_json_sha256"), ("runtime.json", "runtime_json_sha256"),
                           ("source.json", "source_json_sha256"), ("source.tar", "source_tar_sha256")):
            p = cand / fname
            if not p.is_file():
                row.fail(f"candidate {fname} missing")
            elif sha_file(p) != cb.get(key):
                row.fail(f"candidate {fname} hash differs from {key}")
            else:
                row.ev(f"candidate {fname} hash equals {key}")
        if (cand / "assets").is_dir():
            if tree_sha(cand / "assets") == cb.get("assets_tree_sha256"):
                row.ev("candidate assets tree hash equals assets_tree_sha256")
            else:
                row.fail("candidate assets tree hash differs from assets_tree_sha256")
            mani = cand / "assets" / "manifest.json"
            if mani.is_file() and sha_file(mani) != cb.get("asset_manifest_sha256"):
                row.fail("candidate assets/manifest.json differs from asset_manifest_sha256")
        else:
            row.fail("candidate assets directory missing")
        check_candidate_source(ctx, row, cand, cb)

    url = f"http://127.0.0.1:{port}"
    uses = []
    for name, obj, keys in (("targets.json", ctx.targets, ("qa",)), ("cli.json", ctx.cli, ("base_url",)),
                            ("python-controls.json", ctx.pyctl, ("base_url",)),
                            ("python-replay.json", ctx.replay, ("base_url",)),
                            ("rust-products.json", ctx.rproducts, ("qa",)),
                            ("rust-unchanged.json", ctx.runchanged, ("qa",)),
                            (ctx.issue57_name, ctx.issue57, ("qa", "base_url")),
                            (ctx.names_name, ctx.names, ("qa", "base_url"))):
        if isinstance(obj, dict):
            for k in keys:
                if k in obj:
                    uses.append((name, k, obj.get(k)))
    wrong = [f"{n}:{k}={v}" for n, k, v in uses if v != url]
    if wrong:
        row.fail(f"artifacts used another service than the candidate {url}: {wrong}")
    elif uses:
        row.ev(f"{len(uses)} artifact service fields equal {url}")
    if isinstance(ctx.cli, dict):
        if ctx.cli.get("binary_sha256") != cb.get("binary_sha256"):
            row.fail("cli.json binary_sha256 differs from the candidate binary")
        else:
            row.ev("cli.json binary_sha256 equals the candidate binary")
    for p, rec, _err in ctx.receipts:
        if not isinstance(rec, dict):
            continue
        argv = [str(a) for a in rec.get("argv") or []]
        for i, a in enumerate(argv):
            for m in re.findall(r"(?:127\.0\.0\.1|localhost):(\d+)", a):
                if m == str(port):
                    continue
                if m == str(DEFAULT_PORT) and i > 0 and argv[i - 1] == "--reference":
                    continue
                row.fail(f"receipt {p.name} uses port {m} ({argv[i - 1] if i else 'argv0'})")

    summ = ctx.summary
    if not summ or "candidate_after" not in summ:
        row.unrun("summary.json candidate_after missing (run not finished)")
        return row
    ca = summ["candidate_after"]
    keys = sorted(set(cb) | set(ca))
    diffs = [k for k in keys if k not in ("checks", "problems") and cb.get(k) != ca.get(k)]
    if diffs:
        row.fail(f"candidate_after differs from candidate_before in {diffs}")
    else:
        row.ev(f"candidate_after equals candidate_before in {len(keys)} fields "
               f"(pid {cb.get('pid')}, port {port}, binary {str(cb.get('binary_sha256'))[:16]})")
    bad = [k for k, v in (ca.get("checks") or {}).items() if v is not True]
    if bad:
        row.fail(f"candidate_after checks false: {bad}")
    if ca.get("problems"):
        row.fail(f"candidate_after problems: {len(ca['problems'])}")
    return row


def check_default_unchanged(ctx: Ctx) -> Row:
    row = Row("run", 1, "The default service on 8765 is unchanged: header default_before equals summary default_after.")
    before = ctx.header.get("default_before")
    if not isinstance(before, dict):
        row.unrun("header default_before missing")
        return row
    if before.get("healthz_status") != 200:
        row.fail(f"default_before healthz_status {before.get('healthz_status')}")
    after = (ctx.summary or {}).get("default_after")
    if not isinstance(after, dict):
        row.unrun("summary default_after missing (run not finished)")
        return row
    if after != before:
        row.fail(f"default_after differs in {[k for k in set(before) | set(after) if before.get(k) != after.get(k)]}")
    else:
        row.ev(f"default_after equals default_before (healthz {before.get('healthz_sha256')}, "
               f"listeners {before.get('listeners')})")
    return row


def porcelain_lines(text) -> list[str]:
    if isinstance(text, list):
        lines = [str(x) for x in text]
    else:
        lines = str(text or "").splitlines()
    out = []
    for ln in lines:
        if not ln.strip():
            continue
        path = ln[3:] if len(ln) > 3 else ln
        if path.strip() in (AUDIT_REL, os.path.dirname(AUDIT_REL) + "/"):
            continue
        out.append(ln)
    return out


def check_tools_unchanged(ctx: Ctx) -> Row:
    row = Row("run", 2, "The acceptance tools and contracts recorded in the header still hash to the recorded "
              "values, the worktree was clean, and the summary records the same tools.")
    tools = ctx.header.get("tools")
    if not isinstance(tools, dict) or not tools.get("files"):
        row.unrun("header tools missing")
        return row
    files = {k: v for k, v in tools["files"].items() if k != AUDIT_REL}
    changed, missing = [], []
    for rel, want in files.items():
        p = ctx.repo / rel
        if not p.is_file():
            missing.append(rel)
        elif sha_file(p) != want:
            changed.append(rel)
    if missing:
        row.fail(f"tool files missing: {first(missing)}")
    if changed:
        row.fail(f"tool files changed since the header: {first(changed)}")
    if not missing and not changed:
        row.ev(f"{len(files)} tool and contract files hash to the header values now (auditor's own file excluded)")
    rel_plan = "som-rs/contracts/rust-output-v1/acceptance-plan.json"
    if tools["files"].get(rel_plan) is None:
        row.unrun("the header does not record the acceptance plan hash")
    elif tools["files"][rel_plan] != sha_file(ctx.plan_path):
        row.fail("the audited acceptance plan is not the one the run used")
    else:
        row.ev(f"the run used the audited acceptance plan {sha_file(ctx.plan_path)}")
    dirty = porcelain_lines(tools.get("porcelain"))
    if dirty:
        row.fail(f"the header records a dirty worktree: {first(dirty)}")
    else:
        row.ev(f"header porcelain clean (apart from the auditor's own file) at head {tools.get('head')}")
    summ = ctx.summary
    if not summ:
        row.unrun("summary.json missing (run not finished)")
        return row
    if summ.get("plan_sha256") and summ["plan_sha256"] != sha_file(ctx.plan_path):
        row.fail("summary plan_sha256 differs from the plan file")
    after = summ.get("tools_after") or summ.get("tools")
    if isinstance(after, dict):
        after_files = {k: v for k, v in (after.get("files") or {}).items() if k != AUDIT_REL}
        if after_files and (after_files != files or after.get("head") != tools.get("head")):
            row.fail("summary tools differ from header tools")
        elif after_files:
            row.ev("summary tools equal header tools (the auditor's own file excluded)")
        if porcelain_lines(after.get("porcelain")):
            row.fail(f"summary records a dirty worktree after the run: {first(porcelain_lines(after.get('porcelain')))}")
    else:
        row.ev("summary records no tools after the run; the files were hashed again by this audit")
    return row


def check_receipts(ctx: Ctx) -> Row:
    row = Row("run", 3, "Every step receipt is kept: its outputs and logs hash to the recorded values, its env_delta "
              "is the header's, and the summary lists every receipt by hash with the header hash.")
    if not ctx.receipts:
        row.unrun("no receipts directory or no receipts")
        return row
    names = {}
    env = ctx.header.get("env_delta")
    nout = 0
    for p, rec, err in ctx.receipts:
        if err or not isinstance(rec, dict):
            row.fail(f"receipt {p.name} unreadable")
            continue
        names[rec.get("step")] = (p, rec)
        for out, want in (rec.get("outputs") or {}).items():
            target = ctx.run / out
            nout += 1
            if not target.exists():
                row.fail(f"receipt {p.name} output {out} missing")
                continue
            got = "tree:" + tree_sha(target) if str(want).startswith("tree:") else sha_file(target)
            if got != want:
                row.fail(f"receipt {p.name} output {out} hash differs from the recorded value")
        for stream in ("stdout", "stderr"):
            rel = rec.get(stream)
            if rel is None:
                row.fail(f"receipt {p.name} records no {stream} log")
                continue
            lp = ctx.run / rel
            if not lp.is_file():
                row.fail(f"receipt {p.name} {stream} log missing")
            elif sha_file(lp) != rec.get(f"{stream}_sha256"):
                row.fail(f"receipt {p.name} {stream} log hash differs")
        if env and rec.get("env_delta") not in (None, env):
            row.fail(f"receipt {p.name} env_delta differs from the header")
    row.ev(f"{len(ctx.receipts)} receipts checked: {nout} recorded outputs and both logs of each hash to the "
           f"recorded values")
    summ = ctx.summary
    if not summ:
        row.unrun("summary.json missing (run not finished); receipt list not sealed")
        return row
    if "header_sha256" in summ:
        if summ.get("header_sha256") != sha_file(ctx.run / "header.json"):
            row.fail("summary header_sha256 differs from header.json")
        else:
            row.ev("summary header_sha256 equals header.json")
    steps = summ.get("steps") or []
    if isinstance(steps, dict):
        # The summary lists each step with a passed flag only: compare it with the receipt.
        listed = set(steps)
        for step, flag in steps.items():
            found = names.get(step)
            if not found:
                row.fail(f"summary step {step} has no receipt")
                continue
            code = found[1].get("exit_code")
            # The runner may judge rust-products by the plan (exit 2 with the parity blocker only); the exit
            # codes themselves are judged by the exit-code row, so either reading of that one flag is accepted.
            tolerated = step == "rust-products" and code == 2 and parity_only_blocker(ctx.rproducts)
            if flag is not (code == 0) and not tolerated:
                row.fail(f"summary flag of {step} disagrees with its receipt exit code")
        extra = set(names) - listed
        if extra:
            row.fail(f"receipts not listed in the summary: {sorted(map(str, extra))}")
        if steps:
            row.ev(f"summary lists the same {len(steps)} steps as the receipts, with flags matching their exit codes")
        else:
            row.unrun("summary lists no steps")
        rs = summ.get("receipts_sha256")
        if isinstance(rs, dict) and "header_sha256" in summ:
            files = {p.name: p for p, _r, _e in ctx.receipts}
            if set(rs) != set(files):
                row.fail(f"summary receipts_sha256 lists {sorted(rs)}, the receipts directory holds {sorted(files)}")
            bad = [n for n, h in rs.items() if n in files and sha_file(files[n]) != h]
            if bad:
                row.fail(f"receipt files hash differently from summary receipts_sha256: {bad}")
            elif set(rs) == set(files):
                row.ev(f"summary receipts_sha256 seals all {len(rs)} receipt files by hash")
        else:
            ctx.weak.append("run integrity: summary.json records no header or receipt hashes, so the receipts are "
                            "not sealed by the summary; receipts were checked against their own recorded hashes only")
        return row
    listed = set()
    for s in steps:
        listed.add(s.get("step"))
        found = names.get(s.get("step"))
        if not found:
            row.fail(f"summary step {s.get('step')} has no receipt")
            continue
        p, rec = found
        if s.get("receipt_sha256") != sha_file(p):
            row.fail(f"summary receipt_sha256 of {s.get('step')} differs from {p.name}")
        if "exit_code" in s and s.get("exit_code") != rec.get("exit_code"):
            row.fail(f"summary exit code of {s.get('step')} differs from the receipt")
    extra = set(names) - listed
    if extra:
        row.fail(f"receipts not listed in the summary: {sorted(map(str, extra))}")
    if steps:
        row.ev(f"summary lists {len(steps)} steps by receipt hash")
    else:
        row.unrun("summary lists no steps")
    return row


def parity_only_blocker(rp: dict) -> bool:
    summ = (rp or {}).get("summary") or {}
    blocked = summ.get("blocked") or []
    return (summ.get("failed") == [] and len(blocked) == 1 and "parity" in str(blocked[0]).lower()
            and (summ.get("parity") or {}).get("status") == "not_run")


def check_exit_codes(ctx: Ctx) -> Row:
    row = Row("run", 4, "Every step exit code is 0 (rust-products may exit 2 only when behavior parity, which this "
              "plan does not run, is its single blocker), and every plan step ran.")
    if not ctx.receipts:
        row.unrun("no receipts")
        return row
    seen = []
    for p, rec, _err in ctx.receipts:
        if not isinstance(rec, dict):
            continue
        seen.append(rec.get("step"))
        code = rec.get("exit_code")
        if code == 0:
            continue
        if rec.get("step") == "rust-products" and code == 2 and parity_only_blocker(ctx.rproducts):
            row.ev(f"receipt {p.name} exits 2; rust-products.json records failed [] and only the parity blocker "
                   f"(parity is not part of the issue57 plan), so the exit is tolerated")
            ctx.weak.append("rust-products step exits 2: behavior parity not run (by plan design); "
                            "product gates 0/0 (--no-gates)")
            continue
        row.fail(f"receipt {p.name} (step {rec.get('step')}) exit code {code}")
    row.ev(f"{len(seen)} receipts: {', '.join(map(str, seen))}")
    needed = {"contract", "python-replay", "python-controls", "validate-cases", "targets", "cli", "rust-products",
              "rust-unchanged", "issue57", "name-collisions"}
    missing = needed - set(seen)
    if missing:
        row.unrun(f"steps without a receipt: {sorted(missing)}")
    summ = ctx.summary
    if summ is not None:
        tolerated = {"step rust-products failed"} if parity_only_blocker(ctx.rproducts) else set()
        other = [b for b in summ.get("blockers") or [] if str(b) not in tolerated]
        if other:
            row.fail(f"summary records blockers: {first(map(str, other))}")
        elif summ.get("blockers"):
            row.ev(f"summary blockers {summ['blockers']} come only from the rust-products parity exit")
    return row


def check_frozen(ctx: Ctx) -> Row:
    row = Row("run", 5, "The frozen amendment evidence still hashes to its recorded files: rust-output amendment 004, "
              "and compiler-v1 amendment 003 through its sealed receipt chain (a frozen file may differ only when "
              "a later sealed, independently approved compiler-v1 amendment such as 004 records the change from "
              "its frozen hash, with an archived copy, to the hash on disk); and the accepted #55 run is the one "
              "its evidence names.")
    # rust-output amendment 004: every frozen file hashes as recorded.
    ev, err = load_json(ctx.repo / RUST_OUTPUT_RECEIPT)
    if err or not isinstance((ev or {}).get("files"), dict):
        row.unrun(f"{RUST_OUTPUT_RECEIPT} unreadable or without files")
    else:
        bad = [f for f, want in ev["files"].items()
               if not (ctx.repo / f).is_file() or sha_file(ctx.repo / f) != want]
        if bad:
            row.fail(f"{RUST_OUTPUT_RECEIPT}: files changed since the freeze: {first(bad)}")
        else:
            row.ev(f"{RUST_OUTPUT_RECEIPT}: {len(ev['files'])} frozen files hash to their recorded values")
    # compiler-v1 amendment 003 and its sealed successors.
    chain = compiler_chain(ctx.repo)
    for msg in chain["unrun"]:
        row.unrun(msg)
    for msg in chain["problems"]:
        row.fail(msg)
    if chain["base"]:
        base_rel = COMPILER_RECEIPTS[0]
        same, superseded, bad = 0, [], []
        for f, want in chain["base"].items():
            p = ctx.repo / f
            got = sha_file(p) if p.is_file() else None
            if got == want:
                same += 1
            elif f in chain["superseded"] and got == chain["expected"].get(f):
                superseded.append(f)
            else:
                bad.append(f)
        if bad:
            row.fail(f"{base_rel}: files changed since the freeze without a sealed later amendment: {first(bad)}")
        for f in superseded:
            steps = "; ".join(f"{r}: {o[:16]} -> {n[:16]}, archived copy {a} hashes to {o[:16]}"
                              for r, o, n, a in chain["superseded"][f])
            row.ev(f"{base_rel}: {f} superseded by a sealed amendment ({steps}); disk hashes to "
                   f"{chain['expected'][f][:16]}")
        row.ev(f"{base_rel}: {same} of {len(chain['base'])} frozen files hash to their recorded values, "
               f"{len(superseded)} superseded")
        later = [r for r in chain["admitted"] if r != base_rel]
        if later:
            stale = [f for f, want in chain["expected"].items()
                     if not (ctx.repo / f).is_file() or sha_file(ctx.repo / f) != want]
            if stale:
                row.fail(f"{chain['last_rel']}: files differ from the receipt chain: {first(stale)}")
            else:
                row.ev(f"{', '.join(later)}: sealed with an APPROVED independent_review and the exact previous "
                       f"receipt sha256; all {len(chain['last']['files'])} files of {chain['last_rel']} hash to "
                       f"their recorded values")
    lo = ctx.acc_evidence.get("local_only") or {}
    if not lo:
        row.unrun(f"{ACC_EVIDENCE_REL} records no local_only run identity")
    else:
        if str(lo.get("run", "")).rstrip("/") != ACC_RUN_REL:
            row.fail(f"accepted evidence names run {lo.get('run')}, not {ACC_RUN_REL}")
        if lo.get("header_sha256") != ctx.acc_header_sha or lo.get("summary_sha256") != ctx.acc_summary_sha:
            row.fail("rust-output-055 run-003 header/summary hashes differ from the accepted evidence")
        else:
            row.ev(f"rust-output-055 run-003 header {ctx.acc_header_sha[:16]} and summary {ctx.acc_summary_sha[:16]} "
                   f"equal the accepted evidence local_only values")
    return row


# --------------------------------------------------------------- check 0

def check_replay(ctx: Ctx) -> Row:
    row = Row("plan", 0, ctx.plan_checks[0] if ctx.plan_checks else "python_replay --asset-break")
    rp = ctx.replay
    rec = tie_to_receipt(ctx, row, "python-replay.json")
    if not isinstance(rp, dict):
        row.unrun("python-replay.json unreadable")
        return row
    argv = [str(a) for a in (rec or {}).get("argv") or []]
    inputs = rp.get("inputs") or {}
    pub = Path(str(inputs.get("public_run", "")))
    leg = Path(str(inputs.get("legacy_run", "")))
    if rec is not None:
        if "--asset-break" not in argv:
            row.fail("python_replay ran without --asset-break")
        if argv_value(argv, "--public-run") != str(pub) or argv_value(argv, "--legacy-run") != str(leg):
            row.fail("replay inputs differ from the receipt argv")
    if pub.name != "public-products-003b" or leg.parts[-2:] != ("legacy544", "run-005"):
        row.fail(f"replay inputs are not public-products-003b and legacy544 run-005: {pub}, {leg}")
    mode = rp.get("mode") or {}
    if set(mode.get("admitted_descriptors") or []) != ADMITTED_DESCRIPTORS or mode.get("asset_break") is not True:
        row.fail(f"replay mode {mode} does not admit exactly {sorted(ADMITTED_DESCRIPTORS)} with asset_break")
    else:
        row.ev(f"replay admitted descriptors {sorted(ADMITTED_DESCRIPTORS)} with asset_break")
    pres, err1 = load_json(pub / "result.json")
    lres, err2 = load_json(leg / "result.json")
    if err1 or err2:
        row.unrun(f"accepted result.json unreadable: {err1 or err2}")
        return row
    if sha_file(pub / "result.json") != inputs.get("public_result_sha256") or \
            sha_file(leg / "result.json") != inputs.get("legacy_result_sha256"):
        row.fail("accepted result.json hashes differ from the replay inputs")
    else:
        row.ev("public-products-003b and legacy544 run-005 result.json hash to the replay inputs")
    precs = pres.get("records") or []
    lrecs = lres.get("records") or []
    if not (pres.get("complete") is True and len(precs) == 127 and all(r.get("passed") is True for r in precs)):
        row.fail("public-products-003b is not a complete record of 127 passed products")
    if not (lres.get("status") == "passed" and len(lrecs) == 544 and all(r.get("passed") is True for r in lrecs)):
        row.fail("legacy544 run-005 is not a record of 544 passed jobs")

    mapping: dict[str, set] = {}
    total = 0
    nreq = Counter()

    def raw_expected(path: Path, e: dict, cid: str, t: Tally) -> bool:
        if not path.is_file():
            t.miss(cid, "accepted raw response missing")
            return False
        if sha_bytes(base64.b64decode(path.read_bytes())) != e.get("expected_response_sha256"):
            t.bad(cid, "expected_response_sha256 is not the accepted raw response")
            return False
        return True

    def judge(e: dict, kind: str, cid: str, t: Tally) -> bool:
        ok = True
        if not (e.get("status") == e.get("expected_status") == 200):
            t.bad(cid, f"{kind} status {e.get('status')} expected {e.get('expected_status')}")
            ok = False
        if kind.startswith("compile"):
            if not HEX64.match(str(e.get("response_sha256"))):
                t.bad(cid, f"{kind} response hash missing")
                ok = False
            elif e.get("response_sha256") == e.get("expected_response_sha256"):
                t.bad(cid, f"{kind} response equals the pre-amendment response although the descriptors changed")
                ok = False
            else:
                mapping.setdefault(e["expected_response_sha256"], set()).add(e["response_sha256"])
        else:
            if e.get("response_sha256") != e.get("expected_response_sha256"):
                t.bad(cid, "rebased assemble response differs from the accepted response")
                ok = False
            if e.get("recorded_bundle_status") != 400 or e.get("recorded_bundle_code") != "ASSET_HASH_MISMATCH":
                t.bad(cid, f"recorded bundle gave {e.get('recorded_bundle_status')} {e.get('recorded_bundle_code')}")
                ok = False
            if not HEX64.match(str(e.get("rebased_request_sha256"))) or \
                    e.get("rebased_request_sha256") == e.get("request_sha256"):
                t.bad(cid, "no distinct rebased request recorded")
                ok = False
        return ok

    # Public products.
    tp = Tally("public products (compile-cnl, compile-json, assemble)")
    rows = rp.get("public_rows") or []
    ids = [r.get("id") for r in rows]
    want_ids = {r.get("id") for r in precs}
    if len(rows) != 127 or set(ids) != want_ids or len(set(ids)) != 127:
        row.fail(f"public rows {len(rows)} do not cover the 127 accepted products exactly")
    for r in rows:
        cid = r.get("id")
        kinds = sorted(str(e.get("exchange", "")).split("/")[-1] for e in r.get("exchanges") or [])
        if kinds != ["assemble", "compile-cnl", "compile-json"]:
            tp.bad(cid, f"exchanges {kinds}")
            continue
        ok = True
        for e in r["exchanges"]:
            total += 1
            d, kind = e["exchange"].split("/")
            dres, _ = load_json(pub / d / "result.json")
            if (dres or {}).get("id") != cid:
                tp.bad(cid, "exchange directory belongs to another product")
                ok = False
                continue
            ok &= raw_expected(pub / d / f"{kind}.response.raw.b64", e, cid, tp)
            reqp = pub / d / f"{kind}.request.raw"
            if reqp.is_file() and sha_file(reqp) == e.get("request_sha256"):
                nreq["public"] += 1
            else:
                tp.bad(cid, f"{kind} request hash is not the accepted raw request")
                ok = False
            ok &= judge(e, kind, cid, tp)
        if ok:
            tp.good(cid)
    tp.apply(row, want_ids)

    # Legacy assemble jobs.
    tl = Tally("legacy jobs (assemble)")
    lrows = rp.get("legacy_rows") or []
    lids = {r.get("id") for r in lrecs}
    fam = Counter(r.get("family") for r in lrows)
    if len(lrows) != 544 or {r.get("id") for r in lrows} != lids:
        row.fail(f"legacy rows {len(lrows)} do not cover the 544 accepted jobs exactly")
    if dict(fam) != LEGACY_FAMILIES:
        row.fail(f"legacy families {dict(fam)} differ from {LEGACY_FAMILIES}")
    else:
        row.ev(f"legacy families {dict(fam)}")
    for r in lrows:
        cid = r.get("id")
        e = r.get("exchange") or {}
        total += 1
        d, _, kind = str(e.get("exchange", "")).partition("/")
        if kind != "assemble" or not d.isdigit() or int(d) >= len(lrecs) or lrecs[int(d)].get("id") != cid:
            tl.bad(cid, "exchange directory does not belong to the job")
            continue
        if lrecs[int(d)].get("family") != r.get("family"):
            tl.bad(cid, "family differs from the accepted record")
        ok = raw_expected(leg / d / "assemble.response.raw.b64", e, cid, tl)
        req, _ = load_json(leg / d / "assemble.request.json")
        if req is not None and e.get("request_sha256") in compact_shas(req):
            nreq["legacy"] += 1
        else:
            tl.bad(cid, "assemble request hash is not the accepted request")
            ok = False
        ok &= judge(e, "assemble", cid, tl)
        if ok:
            tl.good(cid)
    tl.apply(row, lids)

    # Legacy compile prompts.
    tc = Tally("legacy compile prompts (compile-json, compile-cnl)")
    crows = rp.get("legacy_compile_rows") or []
    prompts = {r.get("prompt_sha256") for r in lrecs}
    if len(crows) != 540 or {r.get("prompt_sha256") for r in crows} != prompts or len(prompts) != 540:
        row.fail(f"legacy compile rows {len(crows)} do not cover the {len(prompts)} accepted prompts exactly")
    replay_compile: dict[str, dict] = {}
    for r in crows:
        ps = r.get("prompt_sha256")
        kinds = sorted(str(e.get("exchange", "")).split("/")[-1] for e in r.get("exchanges") or [])
        if kinds != ["compile-cnl", "compile-json"]:
            tc.bad(ps, f"exchanges {kinds}")
            continue
        ok = True
        for e in r["exchanges"]:
            total += 1
            kind = e["exchange"].split("/")[-1]
            replay_compile.setdefault(ps, {})[kind] = e.get("response_sha256")
            base = leg / "compile" / ps
            ok &= raw_expected(base / f"{kind}.response.raw.b64", e, ps, tc)
            req, _ = load_json(base / f"{kind}.request.json")
            if req is not None and e.get("request_sha256") in compact_shas(req):
                nreq["legacy_compile"] += 1
            else:
                tc.bad(ps, f"{kind} request hash is not the accepted request")
                ok = False
            ok &= judge(e, kind, ps, tc)
        if ok:
            tc.good(ps)
    tc.apply(row, prompts)

    if total != 2005 or (rp.get("exchanges") or {}).get("total") != total:
        row.fail(f"{total} exchanges checked; the plan's sets give 127x3 + 544 + 540x2 = 2005 "
                 f"(replay records {(rp.get('exchanges') or {}).get('total')})")
    else:
        row.ev("2005 exchanges = 127x3 + 544 + 540x2, each expected hash recomputed from the accepted raw bytes")
    row.ev(f"request hashes recomputed from the accepted requests: {dict(nreq)}")
    nonfunc = [k for k, v in mapping.items() if len(v) != 1]
    inv: dict[str, set] = {}
    for k, v in mapping.items():
        for x in v:
            inv.setdefault(x, set()).add(k)
    noninj = [k for k, v in inv.items() if len(v) != 1]
    if nonfunc:
        row.fail(f"{len(nonfunc)} accepted compile responses map to more than one new response")
    if noninj:
        row.fail(f"{len(noninj)} new compile responses come from more than one accepted response")
    if not nonfunc and not noninj:
        row.ev(f"compile responses: {len(mapping)} distinct accepted responses map one-to-one onto new responses "
               f"(compile-json and compile-cnl stay equal wherever they were equal)")
    row.ev("assembled products: every rebased assemble response is byte-identical to the accepted response, "
           "and every recorded bundle is refused with ASSET_HASH_MISMATCH 400")
    row.ev("attested: that a new compile response differs from the accepted one only in bundle.assets "
           "(backend, block_library, catalog, compiler_contract, selection_policy) and hashes.payload_sha256; "
           "the new responses are not kept in the run")
    ctx.weak.append("check 0: compile-response equivalence except the admitted descriptors is attested by "
                    "python_replay rows (new compile responses are not kept); corroborated by one-to-one hash "
                    "mapping, byte-identical assemble responses and targets.json Python file hashes")

    # Cross-tool: targets.json legacy Python replies equal the replay compile responses.
    if isinstance(ctx.targets, dict):
        agree = disagree = 0
        for r in ctx.targets.get("source_forms") or []:
            if not str(r.get("group", "")).startswith("legacy"):
                continue
            ex = replay_compile.get(r.get("prompt_sha256")) or {}
            if ex and r.get("python_reply_sha256") == ex.get("compile-json") == ex.get("compile-cnl"):
                agree += 1
            else:
                disagree += 1
        if disagree:
            row.fail(f"{disagree} legacy targets.json Python replies differ from the replay compile responses")
        elif agree:
            row.ev(f"cross-tool: {agree} legacy targets.json Python replies equal the replay compile-json and "
                   f"compile-cnl responses of the same prompt")
    return row


# --------------------------------------------------------------- check 1

def check_controls(ctx: Ctx) -> Row:
    row = Row("plan", 1, ctx.plan_checks[1] if len(ctx.plan_checks) > 1 else "python controls")
    pc = ctx.pyctl
    tie_to_receipt(ctx, row, "python-controls.json")
    if not isinstance(pc, dict):
        row.unrun("python-controls.json unreadable")
        return row
    if pc.get("negative_cases_sha256") != sha_file(ctx.neg_path):
        row.fail("python-controls ran on another negative-cases.json")
    res = {r.get("id"): r for r in pc.get("results") or []}
    t = Tally("python controls")
    for cid, case in ctx.neg_by_id.items():
        r = res.get(cid)
        if r is None:
            t.miss(cid, "no control result")
            continue
        text = str(case.get("python_control_expected", ""))
        want = 200 if text.startswith("HTTP 200") else case.get("http_status")
        ok = True
        if r.get("control_status") != want:
            t.bad(cid, f"control status {r.get('control_status')}, expected {want}")
            ok = False
        if want == 200 and not HEX64.match(str(r.get("payload_sha256"))):
            t.bad(cid, "200 control without a payload hash")
            ok = False
        if want != 200:
            t.attest(cid)  # the diagnostic code/detail of the control is only a per-case boolean
        if "case_body_status" in r:
            if r.get("case_body_status") != case.get("http_status"):
                t.bad(cid, f"case body status {r.get('case_body_status')}, expected {case.get('http_status')}")
                ok = False
            t.attest(cid)
        if "rust_profile_matches" in r:
            t.attest(cid)
        if ok:
            t.good(cid)
    extra = set(res) - set(ctx.neg_by_id)
    if extra:
        row.fail(f"control results for unknown cases: {first(extra)}")
    t.apply(row, set(ctx.neg_by_id))
    ndiag = sum(1 for c in ctx.neg_by_id.values() if not str(c.get("python_control_expected", "")).startswith("HTTP 200"))
    row.ev(f"attested: the diagnostic code/detail of the {ndiag} non-200 controls (the run keeps only the status)")
    # Payload consistency against the accepted #55 run.
    old = {r.get("id"): r for r in (ctx.acc_pyctl or {}).get("results") or []}
    m: dict[str, set] = {}
    for cid, r in res.items():
        o = old.get(cid) or {}
        if r.get("control_status") == 200 and o.get("control_status") == 200:
            m.setdefault(o.get("payload_sha256"), set()).add(r.get("payload_sha256"))
    inv: dict[str, set] = {}
    for k, v in m.items():
        for x in v:
            inv.setdefault(x, set()).add(k)
    if not m:
        row.unrun("no accepted #55 control payloads to compare")
    elif any(len(v) != 1 for v in m.values()) or any(len(v) != 1 for v in inv.values()):
        row.fail("control payloads do not map one-to-one onto the accepted #55 payloads")
    else:
        row.ev(f"control payloads map one-to-one onto the #55 payloads ({len(m)} distinct)")
    return row


# --------------------------------------------------------------- check 2

def check_validate(ctx: Ctx) -> Row:
    row = Row("plan", 2, ctx.plan_checks[2] if len(ctx.plan_checks) > 2 else "validate_cases")
    v = ctx.validate
    tie_to_receipt(ctx, row, "validate-cases.json")
    if not isinstance(v, dict):
        row.unrun("validate-cases.json unreadable")
        return row
    frozen = {c.get("id"): c for c in ctx.frozen_cov.get("cases") or []}
    got = {c.get("id"): c for c in v.get("cases") or []}
    if set(frozen) != set(ctx.coverage):
        row.fail("frozen coverage evidence does not cover the inventory coverage cases")
    t = Tally("coverage cases")
    for cid in ctx.coverage:
        c, f = got.get(cid), frozen.get(cid)
        if c is None or f is None:
            t.miss(cid, "no result or no frozen record")
            continue
        ok = True
        for k in ("json_status", "cnl_status", "assemble_status"):
            if c.get(k) != 200:
                t.bad(cid, f"{k} {c.get(k)}")
                ok = False
        if c.get("prompt_sha256") != f.get("prompt_sha256"):
            t.bad(cid, "prompt hash differs from the frozen evidence")
            ok = False
        if not c.get("python_files") or c.get("python_files") != f.get("python_files"):
            t.bad(cid, "Python file hashes differ from the frozen evidence")
            ok = False
        if c.get("same_bundle") is not True:
            t.bad(cid, "JSON and CNL bundles differ")
            ok = False
        else:
            t.attest(cid)
        if ok:
            t.good(cid)
    t.apply(row, set(ctx.coverage))
    nf = sum(len(c.get("python_files") or {}) for c in got.values())
    row.ev(f"{nf} Python file hashes equal {COVERAGE_FROZEN_REL}; payload hashes differ only through the "
           f"admitted descriptors ({sum(1 for k in got if (got[k].get('payload_sha256') != (frozen.get(k) or {}).get('payload_sha256')))} changed)")
    return row


# --------------------------------------------------------------- check 3

def expected_cli(ctx: Ctx, r: dict):
    """(status, exit, code_or_bundle_version) the contract gives for one CLI row, or None if unknown."""
    inp, tgt = str(r.get("input", "")), r.get("target")
    m = NEG_ID.search(inp)
    if "unknown" in inp:
        return 400, 2, "INVALID_SCHEMA"
    if m:
        cid = m.group(1)
        case = ctx.neg_by_id.get(cid)
        if case is None:
            return None
        if cid.startswith("RS") and cid not in ctx.neg_refusal_ids:
            return 200, 0, "som.dsl-bundle.v2"
        return case.get("http_status"), 2, (case.get("expected") or {}).get("code")
    if inp.startswith("cov-") or any(inp.startswith(c) for c in ctx.coverage):
        return (200, 0, "som.dsl-bundle.v2") if tgt == "rust" else (200, 0, "som.dsl-bundle.v1")
    return None


def check_targets_cli(ctx: Ctx) -> Row:
    row = Row("plan", 3, ctx.plan_checks[3] if len(ctx.plan_checks) > 3 else "targets and cli")
    t = ctx.targets
    rec = tie_to_receipt(ctx, row, "targets.json")
    if not isinstance(t, dict):
        row.unrun("targets.json unreadable")
    else:
        argv = [str(a) for a in (rec or {}).get("argv") or []]
        if rec is not None and (argv_value(argv, "--refusals") != "phase2" or "--asset-break" not in argv):
            row.fail("check_targets did not run with --refusals phase2 --asset-break")
        bad_in = [k for k, v in (t.get("inputs") or {}).items()
                  if not (ctx.repo / k).is_file() or sha_file(ctx.repo / k) != v]
        if not t.get("inputs"):
            row.unrun("targets.json records no inputs")
        elif bad_in:
            row.fail(f"targets inputs differ from the files now: {first(bad_in)}")
        else:
            row.ev(f"targets.json {len(t['inputs'])} input files hash to the current contract and fixture files")
        # Negative cases.
        tn = Tally("target negative cases")
        neg = {r.get("id"): r for r in t.get("negative_cases") or []}
        for cid, case in ctx.neg_by_id.items():
            r = neg.get(cid)
            if r is None:
                tn.miss(cid, "no result")
                continue
            if cid.startswith("RS") and cid not in ctx.neg_refusal_ids:
                want, ph = 200, "rust_bundle"
            elif cid in ctx.neg_refusal_ids:
                want, ph = case.get("http_status"), "compiler_v1_refusal"
            else:
                want, ph = case.get("http_status"), None
            ok = r.get("status") == want and (ph is None or r.get("phase2") == ph)
            if ph and not HEX64.match(str(r.get("reply_sha256"))):
                ok = False
            if ok:
                tn.good(cid)
            else:
                tn.bad(cid, f"status {r.get('status')} {r.get('phase2')}, expected {want} {ph}")
        tn.apply(row, set(ctx.neg_by_id))
        # RS reply partition equal to the accepted run's.
        old = {r.get("id"): r for r in (ctx.acc_targets or {}).get("negative_cases") or []}

        def part(rows):
            g: dict[str, set] = {}
            for cid, r in rows.items():
                if cid.startswith("RS") and cid not in ctx.neg_refusal_ids and r.get("reply_sha256"):
                    g.setdefault(r["reply_sha256"], set()).add(cid)
            return sorted(sorted(v) for v in g.values())
        if old:
            if part(neg) != part(old):
                row.fail("RS reply groups differ from the accepted #55 run")
            else:
                row.ev("RS Rust replies group exactly as in the accepted #55 run")
        # Target examples.
        te = {r.get("id"): r for r in t.get("target_examples") or []}
        want_ids = [x.get("id") for x in ctx.tex.get("examples") or []]
        tt = Tally("target examples")
        for cid in want_ids:
            r = te.get(cid)
            if r is None:
                tt.miss(cid, "no result")
                continue
            outcome = "python_identical" if cid.endswith("-python") else "rust_bundle"
            pair = te.get(cid.replace("-cnl-", "-json-")) or {}
            if r.get("status") != 200 or r.get("outcome") != outcome:
                tt.bad(cid, f"status {r.get('status')} outcome {r.get('outcome')}")
            elif "-cnl-" in cid and pair.get("reply_sha256") != r.get("reply_sha256"):
                tt.bad(cid, "CNL and typed JSON replies differ")
            else:
                tt.good(cid)
        tt.apply(row, set(want_ids))
        # Source forms.
        sf = t.get("source_forms") or []
        oldsf = {(r.get("id"), r.get("form")): r for r in (ctx.acc_targets or {}).get("source_forms") or []}
        groups = Counter(str(r.get("group", "")).split(":")[0] + "|" + str(r.get("form")) for r in sf)
        n_cnl = int(ctx.cnlpos.get("case_count") or 0)
        want_groups = {"public_examples|cnl": len(ctx.inv1.get("public_examples", [])),
                       "public_examples|json": len(ctx.inv1.get("public_examples", [])),
                       "cnl_positive_fixtures|cnl": n_cnl, "cnl_positive_fixtures|json": n_cnl,
                       "coverage_cases|cnl": len(ctx.coverage), "coverage_cases|json": len(ctx.coverage),
                       "legacy|json": 544}
        got_groups = {k: v for k, v in groups.items() if not k.startswith("compiler_v1_rejections")}
        nrej = sum(v for k, v in groups.items() if k.startswith("compiler_v1_rejections"))
        if got_groups != want_groups:
            row.fail(f"source form counts {dict(got_groups)} differ from {want_groups}")
        else:
            row.ev(f"source forms: {sum(got_groups.values())} in-scope forms ({want_groups}) and {nrej} "
                   f"compiler-v1 rejections")
        ts = Tally("source forms")
        keys = set()
        by_id: dict[str, dict] = {}
        for r in sf:
            key = f"{r.get('id')}|{r.get('form')}"
            keys.add(key)
            o = oldsf.get((r.get("id"), r.get("form")))
            if str(r.get("group", "")).startswith("compiler_v1_rejections"):
                if r.get("python_status") in (400, 422) and r.get("python_status") == r.get("rust_status") and \
                        o is not None and o.get("python_status") == r.get("python_status"):
                    ts.good(key)
                else:
                    ts.bad(key, "compiler-v1 rejection differs between targets or from #55")
                continue
            if r.get("python_status") != 200 or r.get("rust_status") != 200:
                ts.bad(key, f"statuses {r.get('python_status')}/{r.get('rust_status')}")
                continue
            if o is None or not r.get("python_files_sha256") or r.get("python_files_sha256") != o.get("python_files_sha256"):
                ts.bad(key, "Python files differ from the accepted #55 run")
                continue
            if not HEX64.match(str(r.get("rust_reply_sha256"))):
                ts.bad(key, "no Rust reply")
                continue
            by_id.setdefault(str(r.get("id")), {})[r.get("form")] = r.get("rust_reply_sha256")
            ts.good(key)
        ts.apply(row, keys)
        if len(keys) != sum(want_groups.values()) + nrej or nrej != 7:
            row.fail(f"{len(keys)} source forms with {nrej} rejections; expected {sum(want_groups.values())} + 7")
        split = [i for i, v in by_id.items() if len(v) == 2 and len(set(v.values())) != 1]
        if split:
            row.fail(f"{len(split)} prompts give different Rust replies from CNL and typed JSON: {first(split)}")
        else:
            row.ev(f"{sum(1 for v in by_id.values() if len(v) == 2)} prompts give the same Rust reply from CNL and typed JSON")
        row.ev("all in-scope source forms keep the accepted #55 Python file hashes")

    # CLI.
    c = ctx.cli
    tie_to_receipt(ctx, row, "cli.json")
    if not isinstance(c, dict):
        row.unrun("cli.json unreadable")
        return row
    if c.get("refusals_profile") != "phase2":
        row.fail(f"cli refusals_profile {c.get('refusals_profile')}")
    if (c.get("inputs") or {}).get("negative_cases_sha256") != sha_file(ctx.neg_path):
        row.fail("cli ran on another negative-cases.json")
    tc = Tally("cli rows")
    rows = c.get("rows") or []
    keys = set()
    stdout_by: dict[tuple, str] = {}
    for r in rows:
        key = f"{r.get('input')}|{r.get('target')}"
        keys.add(key)
        exp = expected_cli(ctx, r)
        if exp is None:
            tc.miss(key, "row not derivable from the contracts")
            continue
        st, ex, tag = exp
        got_tag = r.get("bundle_version") if st == 200 else r.get("code")
        if (r.get("http_status"), r.get("exit_code"), got_tag) != (st, ex, tag):
            tc.bad(key, f"got {r.get('http_status')}/{r.get('exit_code')}/{got_tag}, expected {st}/{ex}/{tag}")
            continue
        if ex == 0 and (r.get("stderr_sha256") != EMPTY_SHA256 or r.get("stdout_sha256") == EMPTY_SHA256):
            tc.bad(key, "success with empty stdout or non-empty stderr")
            continue
        if ex == 2 and (r.get("stdout_sha256") != EMPTY_SHA256 or r.get("stderr_sha256") == EMPTY_SHA256):
            tc.bad(key, "refusal with stdout or without stderr")
            continue
        stdout_by[(r.get("input"), r.get("target"))] = r.get("stdout_sha256")
        tc.good(key)
    tc.apply(row, keys)
    pairs = [(i, s) for (i, tg), s in stdout_by.items() if tg is None]
    badp = [i for i, s in pairs if stdout_by.get((i, "python")) != s]
    if badp or not pairs:
        row.fail(f"CLI without --target differs from --target python: {first(badp)}" if badp
                 else "no CLI default-target row")
    else:
        row.ev(f"{len(pairs)} CLI default-target outputs equal --target python byte for byte")
    oldrows = {(r.get("input"), r.get("target")): r for r in (ctx.acc_cli or {}).get("rows") or []}
    diff = []
    for r in rows:
        o = oldrows.get((r.get("input"), r.get("target")))
        m = NEG_ID.search(str(r.get("input", "")))
        if m and m.group(1) in ctx.neg_refusal_ids:
            continue
        if o is None or (o.get("http_status"), o.get("exit_code"), o.get("code")) != \
                (r.get("http_status"), r.get("exit_code"), r.get("code")):
            diff.append(f"{r.get('input')}|{r.get('target')}")
    if len(rows) != len(oldrows) or diff:
        row.fail(f"CLI rows differ from the accepted #55 run in status/exit/code: {first(diff)}")
    else:
        row.ev(f"{len(rows)} CLI rows keep the #55 status, exit code and code (RS12/RS13 rows excepted)")
    return row


# --------------------------------------------------------------- check 4

def check_rust_products(ctx: Ctx) -> Row:
    row = Row("plan", 4, ctx.plan_checks[4] if len(ctx.plan_checks) > 4 else "rust products")
    rp = ctx.rproducts
    rec = tie_to_receipt(ctx, row, "rust-products.json", allow_exit=(0, 2))
    if not isinstance(rp, dict):
        row.unrun("rust-products.json unreadable")
        return row
    argv = [str(a) for a in (rec or {}).get("argv") or []]
    if rec is not None and argv_value(argv, "--refusals") != "phase2":
        row.fail("check_rust_products did not run with --refusals phase2")
    if rp.get("refusal_plan") != "phase2":
        row.fail(f"refusal_plan {rp.get('refusal_plan')}")
    if rec is not None and rec.get("exit_code") == 2:
        if parity_only_blocker(rp):
            row.ev("exit 2 with failed [] and the parity blocker only (parity is not part of the issue57 plan)")
        else:
            row.fail("exit 2 with more than the parity blocker")
    summ = rp.get("summary") or {}
    if summ.get("failed"):
        row.fail(f"rust-products failed: {first(map(str, summ['failed']))}")
    bad_in = [k for k, v in (rp.get("inputs") or {}).items()
              if not (ctx.repo / k).is_file() or sha_file(ctx.repo / k) != v]
    if bad_in:
        row.fail(f"rust-products inputs differ from the files now: {first(bad_in)}")
    cases = {c.get("id"): c for c in rp.get("cases") or []}
    t = Tally("bundle checks")
    for cid in ctx.acc1:
        c = cases.get(cid)
        if c is None:
            t.miss(cid, "no case row")
        elif (c.get("bundle_checks") or {}).get("passed") is not True or (c.get("bundle_checks") or {}).get("problems"):
            t.bad(cid, "bundle checks failed")
        elif not HEX64.match(str(c.get("files_sha256"))):
            t.bad(cid, "no files hash")
        else:
            t.good(cid)
            t.attest(cid)
    if len(cases) != len(ctx.acc1) or set(cases) != set(ctx.acc1):
        row.fail(f"{len(cases)} case rows for {len(ctx.acc1)} phase 1 acceptance cases")
    t.apply(row, set(ctx.acc1))
    ctx.weak.append("check 4: per-case bundle_checks are attested booleans of check_rust_products")
    # Assemble refusals, from the plan recipes and phase 2 / amendment 004 replacements.
    recipes = {r["id"]: r for r in (ctx.plan_all.get("assemble_refusals") or {}).get("recipes") or []}
    p2 = (ctx.plan_all.get("phase2") or {}).get("assemble_refusals") or {}
    a6 = str(p2.get("A6_amendment_004", ""))
    if not ("INVALID_VALUE 400" in a6 and "/bundle/prompt/query/filter_field" in a6):
        row.unrun("plan phase2.assemble_refusals.A6_amendment_004 does not state INVALID_VALUE 400 at "
                  "/bundle/prompt/query/filter_field")
    expected = {}
    for rid in ("A1", "A2", "A3", "A4", "A5", "A7", "A9"):
        r = recipes.get(rid)
        if r:
            expected[(rid, "example-v1")] = (r.get("http_status"), r.get("code"), r.get("prompt_pointer"))
    expected[("A6", "example-v1")] = (400, "INVALID_VALUE", "/bundle/prompt/query/filter_field")
    expected[("A8", "example-v1")] = (200, None, None)
    for rid in ("A1", "A2", "A4", "A5", "A7", "A9"):
        if (rid, "example-v1") in expected:
            expected[(rid, "PUBLIC_v6")] = expected[(rid, "example-v1")]
    if len(expected) != 15:
        row.unrun(f"only {len(expected)} assemble refusal expectations derivable from the plan")
    got = {}
    for r in rp.get("assemble_refusals") or []:
        rid = str(r.get("id", "")).split("_")[0]
        got[(rid, r.get("base"))] = r
    ta = Tally("assemble refusals")
    old = {(str(r.get("id", "")).split("_")[0], r.get("base")): r
           for r in (ctx.acc_rproducts or {}).get("assemble_refusals") or []}
    for key, (st, code, ptr) in expected.items():
        k = f"{key[0]}@{key[1]}"
        r = got.get(key)
        if r is None:
            ta.miss(k, "no row")
            continue
        if key[0] == "A8":
            fs, rs = r.get("files_sha256"), r.get("renamed_files_sha256")
            o = old.get(key) or {}
            if r.get("status") != 200 or not HEX64.match(str(fs)) or fs != rs:
                ta.bad(k, "A8 not 200 with files equal to the renamed prompt's files")
            elif o.get("files_sha256") != fs:
                ta.bad(k, "A8 files differ from the accepted #55 A8 files")
            else:
                ta.good(k)
            continue
        if (r.get("status"), r.get("code"), r.get("prompt_pointer")) != (st, code, ptr):
            ta.bad(k, f"got {r.get('status')} {r.get('code')} {r.get('prompt_pointer')}, expected {st} {code} {ptr}")
        else:
            ta.good(k)
    if set(got) - set(expected):
        row.fail(f"unexpected assemble refusal rows: {first(f'{a}@{b}' for a, b in set(got) - set(expected))}")
    ta.apply(row, {f"{a}@{b}" for a, b in expected})
    if (got.get(("A8", "example-v1")) or {}).get("files_sha256"):
        row.ev("A8 files equal the accepted #55 A8 files (Rust products unchanged)")
    ctx.weak.append("check 4: A8's renamed_files_sha256 is computed by the tool; it is corroborated only by "
                    "equality with the #55 A8 files")
    return row


# --------------------------------------------------------------- check 5

def scan_codes(obj, out: Counter, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "code" and isinstance(v, str) and v in FORBIDDEN_CODES:
                out[v] += 1
            scan_codes(v, out, path + "/" + str(k))
    elif isinstance(obj, list):
        for v in obj:
            scan_codes(v, out, path)


def check_no_unsupported(ctx: Ctx) -> Row:
    row = Row("plan", 5, ctx.plan_checks[5] if len(ctx.plan_checks) > 5 else "no UNSUPPORTED_TARGET")
    n = 0
    t = ctx.targets if isinstance(ctx.targets, dict) else None
    if t is None:
        row.unrun("targets.json unreadable")
    else:
        bad = [r.get("id") for r in t.get("negative_cases") or []
               if str(r.get("id", "")).startswith("RS") and r.get("status") not in (200, 400)]
        bad += [f"{r.get('id')}|{r.get('form')}" for r in t.get("source_forms") or []
                if r.get("rust_status") not in (200,) and not str(r.get("group", "")).startswith("compiler_v1_rejections")]
        bad += [r.get("id") for r in t.get("target_examples") or [] if r.get("status") != 200]
        n += len(t.get("negative_cases") or []) + len(t.get("source_forms") or []) + len(t.get("target_examples") or [])
        if bad:
            row.fail(f"targets.json Rust results that are not a bundle or a compiler-v1 refusal: {first(bad)}")
        refused = sorted(r.get("id") for r in t.get("negative_cases") or [] if r.get("status") == 400
                         and str(r.get("id", "")).startswith("RS"))
        if set(refused) != ctx.neg_refusal_ids:
            row.fail(f"RS cases refused: {refused}, expected {sorted(ctx.neg_refusal_ids)}")
    ru = ctx.runchanged if isinstance(ctx.runchanged, dict) else None
    if ru is None:
        row.unrun("rust-unchanged.json unreadable")
    else:
        cases = {c.get("id"): c for c in (ru.get("phase2") or {}).get("cases") or []}
        if set(cases) != set(ctx.rows2_by_id):
            row.fail(f"rust-unchanged phase 2 covers {len(cases)} cases, inventory {len(ctx.rows2_by_id)}")
        bad = []
        for cid, c in cases.items():
            rr = c.get("rust") or {}
            if cid in ctx.refusal_ids:
                if rr.get("compile_status") != 400 or (rr.get("refusal") or {}).get("code") != "INVALID_VALUE":
                    bad.append(cid)
            elif rr.get("compile_status") != 200:
                bad.append(cid)
        n += len(cases)
        if bad:
            row.fail(f"phase 2 Rust compiles that are not 200 (or INVALID_VALUE 400 for RS12/RS13): {first(bad)}")
        else:
            row.ev(f"{len(cases)} phase 2 Rust compiles: {len(cases) - len(ctx.refusal_ids)} give 200 and "
                   f"{sorted(ctx.refusal_ids)} give INVALID_VALUE 400")
    if isinstance(ctx.cli, dict):
        rust_rows = [r for r in ctx.cli.get("rows") or [] if r.get("target") == "rust"]
        n += len(rust_rows)
        bad = [r.get("input") for r in rust_rows if r.get("code") in FORBIDDEN_CODES]
        if bad:
            row.fail(f"CLI target rust rows with a forbidden code: {first(bad)}")
    # Global scan of every JSON result of the run for a forbidden code value.
    found = Counter()
    nfiles = 0
    for dirpath, dirs, names in os.walk(ctx.run):
        dirs[:] = [d for d in dirs if d not in ("work", "target", "fixtures", "products")]
        for name in names:
            if not name.endswith(".json"):
                continue
            p = Path(dirpath) / name
            if p.stat().st_size > (64 << 20):
                continue
            obj, err = load_json(p)
            if err:
                continue
            nfiles += 1
            scan_codes(obj, found)
    if found:
        row.fail(f"forbidden codes in run results: {dict(found)}")
    else:
        row.ev(f"no 'code' value UNSUPPORTED_TARGET, NOT_IMPLEMENTED or OUTPUT_QUALITY_FAILED in {nfiles} JSON "
               f"files of the run (work, target, fixtures and products directories excepted)")
    if n:
        row.ev(f"{n} Rust-target results inspected (targets.json, rust-unchanged.json, cli.json)")
    return row


# --------------------------------------------------------------- check 6

def check_rust_unchanged(ctx: Ctx) -> Row:
    row = Row("plan", 6, ctx.plan_checks[6] if len(ctx.plan_checks) > 6 else "rust unchanged")
    ru = ctx.runchanged
    rec = tie_to_receipt(ctx, row, "rust-unchanged.json")
    if not isinstance(ru, dict):
        row.unrun("rust-unchanged.json unreadable")
        return row
    argv = [str(a) for a in (rec or {}).get("argv") or []]
    if rec is not None:
        if argv_value(argv, "--run") != str(ctx.acc_run):
            row.fail(f"check_rust_unchanged compared with {argv_value(argv, '--run')}, not {ctx.acc_run}")
        if argv_value(argv, "--phase1") != str(ctx.run / "rust-products.json"):
            row.fail("phase 1 report is not this run's rust-products.json")
    # Phase 1: compare the #55 record with this run's report myself.
    acc = {c.get("id"): c.get("files_sha256") for c in (ctx.acc_rproducts or {}).get("cases") or []}
    now = {c.get("id"): c.get("files_sha256") for c in (ctx.rproducts or {}).get("cases") or []}
    t1 = Tally("phase 1 Rust products")
    for cid in ctx.acc1:
        if not acc.get(cid) or not now.get(cid):
            t1.miss(cid, "no recorded or current files hash")
        elif acc[cid] != now[cid]:
            t1.bad(cid, "files differ from rust-output-055 run-003")
        else:
            t1.good(cid)
    t1.apply(row, set(ctx.acc1))
    p1 = ru.get("phase1") or {}
    rp1 = {c.get("id"): c.get("files_sha256") for c in p1.get("cases") or []}
    inc = [cid for cid, v in rp1.items() if not isinstance(v, list) or v != [acc.get(cid), now.get(cid)]]
    if inc or len(rp1) != len(ctx.acc1):
        row.fail(f"rust-unchanged phase 1 rows disagree with the two reports: {first(inc)}")
    # Phase 2: compare the #55 case rows with the reported current hashes.
    p2 = ru.get("phase2") or {}
    rows = {c.get("id"): c for c in p2.get("cases") or []}
    cdir = ctx.acc_run / "phase2" / "cases"
    t2 = Tally("phase 2 Rust products")
    for cid in ctx.product_ids:
        rec055 = ctx.acc_case(cid)
        recorded = ((rec055 or {}).get("rust") or {}).get("files_sha256")
        r = rows.get(cid) or {}
        pair = r.get("files_sha256")
        if not recorded:
            t2.miss(cid, "no #55 recorded files hash")
        elif not isinstance(pair, list) or len(pair) != 2:
            t2.miss(cid, "no current files hash")
        elif pair[0] != recorded:
            t2.bad(cid, "rust-unchanged recorded hash is not the #55 case row hash")
        elif pair[1] != recorded or (r.get("rust") or {}).get("files_sha256") != recorded:
            t2.bad(cid, "files differ from rust-output-055 run-003")
        else:
            t2.good(cid)
            t2.attest(cid)
    t2.apply(row, ctx.product_ids)
    tr = Tally("RS12/RS13 compiler-v1 refusals")
    for cid in ctx.refusal_ids:
        exp = (ctx.rows2_by_id.get(cid) or {}).get("phase2_expected") or {}
        rr = (rows.get(cid) or {}).get("rust") or {}
        ref = rr.get("refusal") or {}
        ok = (rr.get("compile_status") == exp.get("http_status") and ref.get("code") == exp.get("code")
              and ref.get("prompt_pointer") == "/prompt" + str(exp.get("pointer"))
              and (exp.get("detail") is None or ref.get("detail") == exp.get("detail")))
        if not rows.get(cid):
            tr.miss(cid, "no row")
        elif ok:
            tr.good(cid)
        else:
            tr.bad(cid, f"got {rr.get('compile_status')} {ref.get('code')} {ref.get('prompt_pointer')}, expected "
                        f"{exp.get('http_status')} {exp.get('code')} /prompt{exp.get('pointer')}")
    tr.apply(row, ctx.refusal_ids)
    if set(rows) != set(ctx.rows2_by_id):
        row.fail(f"rust-unchanged phase 2 covers {len(rows)} of {len(ctx.rows2_by_id)} inventory cases")
    else:
        row.ev(f"phase 2: {len(ctx.product_ids)} products + {len(ctx.refusal_ids)} refusals = {len(rows)} inventory cases")
    # Cross-tool: issue57 Rust rows for RS07/RS14/RS16.
    cross = []
    for cid in RS_SELF:
        rr, _ = load_json(ctx.run / "issue57" / "rust" / "cases" / f"{cid}.json")
        rec055 = ctx.acc_case(cid)
        a = ((rr or {}).get("rust") or (rr or {}).get("issue57") or {}).get("files_sha256")
        b = ((rec055 or {}).get("rust") or {}).get("files_sha256")
        cross.append(bool(a) and a == b)
    if all(cross):
        row.ev("cross-tool: check_issue57 Rust rows of RS07, RS14 and RS16 give the #55 files hashes")
    else:
        row.fail("check_issue57 Rust rows of RS07/RS14/RS16 differ from the #55 files hashes")
    ctx.weak.append("check 6: current phase 2 Rust file hashes are reported by check_rust_unchanged (products not "
                    "kept in the run); phase 1 current hashes come from rust-products.json")
    return row


# --------------------------------------------------------------- check 7

def issue57_rows(ctx: Ctx, lang: str):
    out = {}
    d = ctx.run / "issue57" / lang / "cases"
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            if p.name.endswith(".outcome.json"):
                continue
            obj, err = load_json(p)
            if isinstance(obj, dict) and obj.get("id"):
                out[obj["id"]] = (p, obj)
    return out


def check_issue57(ctx: Ctx) -> Row:
    row = Row("plan", 7, ctx.plan_checks[7] if len(ctx.plan_checks) > 7 else "check_issue57")
    rep = ctx.issue57
    rec = tie_to_receipt(ctx, row, ctx.issue57_name)
    if not isinstance(rep, dict):
        row.unrun(f"{ctx.issue57_name} unreadable")
    elif rep.get("fixtures_sha256") not in (None, sha_file(ctx.repo / SELF_FIXTURES_REL)):
        row.fail("check_issue57 ran on another self-name-fixtures.json")
    sx = ctx.selfx
    pos = {c["id"]: c for c in sx.get("positive") or []}
    negs = {c["id"]: c for c in sx.get("negative") or []}
    accs = {c["id"]: c for c in sx.get("accepted") or []}
    counts = sx.get("counts") or {}
    if (len(pos), len(negs), len(accs)) != (15, 13, 3) or \
            (counts.get("positive"), counts.get("negative"), counts.get("accepted")) not in ((15, 13, 3), (None, None, None)):
        row.fail(f"self-name fixtures hold {len(pos)}/{len(negs)}/{len(accs)} cases, the contract says 15/13/3")
    inv2 = ctx.rows2_by_id
    defects = ctx.behavior.get("python_defects") or {}
    expected_ids = set(pos) | set(RS_SELF)
    gates: dict[tuple, set] = {}
    for lang in ("python", "rust"):
        rows = issue57_rows(ctx, lang)
        t = Tally(f"{lang} self-name products")
        for cid in expected_ids:
            if cid not in rows:
                t.miss(cid, "no case row")
                continue
            p, r = rows[cid]
            info = r.get("issue57") or {}
            if lang == "rust":
                info = {**info, **(r.get("rust") or {})}
            ok = True
            want_prompt = (pos.get(cid) or {}).get("prompt_sha256") or (inv2.get(cid) or {}).get("prompt_sha256")
            if r.get("prompt_sha256") != want_prompt:
                t.bad(cid, "prompt hash differs from the fixture/inventory")
                ok = False
            if info.get("compile_status") != 200:
                t.bad(cid, f"compile status {info.get('compile_status')}")
                ok = False
            if "cnl_same_bundle" in info and info["cnl_same_bundle"] is not True:
                t.bad(cid, "typed JSON and CNL compile to different bundles")
                ok = False
            elif info.get("cnl_same_bundle") is True:
                t.attest(cid)
            else:
                t.miss(cid, "no record that the typed JSON and CNL forms compile to the same bundle "
                            "(the row has no cnl_same_bundle)")
                ok = False
            if lang == "python" or "variants" in info:
                want_v = (pos.get(cid) or {}).get("python_variants") if cid in pos else None
                got_v = info.get("variants")
                if cid in pos and sorted(got_v or []) != sorted(want_v or []):
                    t.bad(cid, f"variants {got_v}, recorded {want_v}")
                    ok = False
                elif cid not in pos:
                    shared = [k for k, v in pos.items() if v.get("prompt_sha256") == r.get("prompt_sha256")]
                    if shared:
                        if sorted(got_v or []) != sorted(pos[shared[0]].get("python_variants") or []):
                            t.bad(cid, "variants differ from the fixture with the same prompt")
                            ok = False
                    elif not got_v or not all(str(x).endswith(".self") for x in got_v):
                        t.bad(cid, f"variants {got_v} are not self variants")
                        ok = False
                    else:
                        t.attest(cid)
            if r.get("passed") is not True or r.get("checker_passed") is not True or r.get("problems"):
                t.bad(cid, "checker did not pass")
                ok = False
            if cid in defects and r.get("checker") != defects[cid].get("checker"):
                t.bad(cid, f"checker {r.get('checker')}, behavior.json records {defects[cid].get('checker')}")
                ok = False
            if not HEX64.match(str(r.get("product_sha256"))) or r.get("product_sha256") != info.get("files_sha256"):
                t.bad(cid, "product hash differs from the assembled files hash")
                ok = False
            # Outcome file.
            op = p.with_name(f"{cid}.outcome.json")
            if r.get("checker") == "phase1_suite":
                out, err = load_json(op)
                if err:
                    t.miss(cid, "outcome file missing")
                    ok = False
                elif sha_file(op) != r.get("outcome_sha256"):
                    t.bad(cid, "outcome file hash differs from the row")
                    ok = False
                else:
                    fails = out.get("failures")
                    if fails or r.get("failures") != 0 or len(out.get("transcript") or []) != r.get("steps") or \
                            not r.get("steps"):
                        t.bad(cid, "outcome records failures or a transcript of another length")
                        ok = False
            else:
                out, err = load_json(op)
                if err or sha_file(op) != r.get("outcome_sha256"):
                    t.miss(cid, "outcome file missing or hash differs")
                    ok = False
                sc = r.get("surface_checks") or []
                if any(not isinstance(x, dict) or x.get("equal") is not True
                       or sorted(map(tuple, x.get("documented") or [])) != sorted(map(tuple, x.get("probed") or []))
                       for x in sc):
                    t.bad(cid, "documented and probed surfaces differ")
                    ok = False
                # The product of a verify_all/flow case is kept: hash it again and read its process events.
                prod = ctx.run / "issue57" / lang / "work" / cid / "product"
                if prod.is_dir():
                    try:
                        got_files, _n = files_sha(prod, set(r.get("extra_files") or []))
                    except (OSError, UnicodeDecodeError):
                        got_files = None
                    if got_files != info.get("files_sha256"):
                        t.bad(cid, "kept product files hash differs from the row")
                        ok = False
                    ev_ok, ev_msg, kinds = product_events(prod, lang)
                    if not ev_ok:
                        t.bad(cid, ev_msg)
                        ok = False
                    gates[(lang, cid)] = kinds
                else:
                    t.miss(cid, "product directory not kept")
                    ok = False
            if lang == "rust":
                if cid in RS_SELF:
                    rec055 = ctx.acc_case(cid)
                    if ((rec055 or {}).get("rust") or {}).get("files_sha256") != info.get("files_sha256"):
                        t.bad(cid, "Rust files differ from the #55 product")
                        ok = False
            if ok:
                t.good(cid)
        t.apply(row, expected_ids)
        extra = set(rows) - expected_ids
        if extra:
            row.fail(f"{lang}: unexpected case rows {first(extra)}")
    ctx.weak.append("check 7: the selected variant blocks of RS07 and RS16 are recorded by check_issue57 only "
                    "(no contract records them; RS14 is compared with the fixture of the same prompt); typed JSON "
                    "vs CNL same-bundle is a per-case boolean (cnl_same_bundle)")
    # Negative and accepted fixtures.
    if isinstance(rep, dict):
        check_issue57_fixtures(ctx, row, rep, negs, accs)
        check_issue57_gates(ctx, row, expected_ids, gates)
    else:
        row.unrun("negative and accepted fixture results unavailable")
    return row


def product_events(prod: Path, lang: str) -> tuple[bool, str, set]:
    """Process events of the checker for a kept product: every cargo command exits 0.

    Returns (ok, problem, kinds) where kinds names the cargo subcommands that ran and exited 0."""
    evdirs = sorted((prod / "check-results").glob("evidence-*")) if (prod / "check-results").is_dir() else []
    kinds: set[str] = set()
    if not evdirs:
        return (lang != "rust"), "no checker evidence kept", kinds
    for d in evdirs:
        p = d / "events.jsonl"
        if not p.is_file():
            return False, "checker evidence without events", kinds
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                return False, "unreadable checker event", kinds
            if e.get("kind") != "process":
                continue
            args = [str(a) for a in e.get("args") or []]
            idx = [i for i, a in enumerate(args) if a.endswith("/cargo") or a == "cargo" or a.endswith("/cargo-clippy")]
            if not idx:
                continue
            sub = args[idx[0] + 1:]
            name = "clippy" if args[idx[0]].endswith("cargo-clippy") or sub[:1] == ["clippy"] else (sub[:1] or [""])[0]
            if e.get("exit_code") != 0:
                return False, f"cargo {name} exit {e.get('exit_code')}", kinds
            if name == "clippy" and not ("-D" in sub and "warnings" in sub or "-Dwarnings" in sub
                                         or "--deny=warnings" in sub):
                name = "clippy-without-deny"
            kinds.add(name)
    return True, "", kinds


def check_issue57_fixtures(ctx: Ctx, row: Row, rep: dict, negs: dict, accs: dict):
    rows = rep.get("negative") or []
    t = Tally("negative and accepted fixtures (json, json-rust, cnl)")
    expected_keys = {f"{cid}|{form}" for cid in list(negs) + list(accs) for form in ("json", "json-rust", "cnl")}
    seen = set()
    for r in rows:
        key = f"{r.get('id')}|{r.get('form')}"
        if key not in expected_keys or key in seen:
            t.bad(key, "unexpected or duplicate fixture row")
            continue
        seen.add(key)
        cid, form = r.get("id"), r.get("form")
        got = r.get("got") or {}
        if cid in negs:
            fx = negs[cid].get("expected") or {}
            want = {"status": fx.get("status"), "code": fx.get("code"), "detail": fx.get("detail"),
                    "prompt_pointer": fx.get("cnl_prompt_pointer" if form == "cnl" else "json_prompt_pointer")}
            if None in (want["status"], want["code"], want["prompt_pointer"]):
                t.miss(key, "fixture records no complete expectation")
                continue
            mism = [k for k, v in want.items() if got.get(k) != v]
            if mism:
                t.bad(key, f"result differs from the fixture expectation in {mism}")
            elif got.get("status") not in (400, 422):
                t.bad(key, f"negative fixture status {got.get('status')}")
            else:
                t.good(key)
        else:
            if got.get("status") != 200:
                t.bad(key, f"accepted fixture status {got.get('status')}")
            else:
                t.good(key)
    t.apply(row, expected_keys)
    row.ev("negative fixtures compared field by field (status, code, detail, form-specific prompt pointer) with "
           "self-name-fixtures.json; accepted fixtures give 200 in all three forms")


def check_issue57_gates(ctx: Ctx, row: Row, ids: set, gates: dict):
    """Rust build, clippy with warnings denied and tests.

    Read from issue57/rust/gates/<id>.json with its logs and the gated product directory, and for verify_all/flow
    products also from the checker process events."""
    gdir = ctx.run / "issue57" / "rust" / "gates"
    rows = issue57_rows(ctx, "rust")
    tc_ = load_json(ctx.repo / "som-rs/contracts/rust-output-v1/toolchain.json")[0] or {}
    cmds = tc_.get("commands") or {}

    def argv_of(cmd: str) -> list:
        parts = str(cmd).split()
        return parts[1:] if parts[:1] == ["cargo"] else []

    want_argv = {"cargo_build": argv_of(cmds.get("build")), "cargo_clippy": argv_of(cmds.get("clippy")),
                 "cargo_test": argv_of(cmds.get("test"))}
    diag = re.compile(r"^(warning|error)(\[[A-Za-z0-9_:-]+\])?:")
    result = re.compile(r"^test result: (\w+)\. (\d+) passed; (\d+) failed;")
    tr = Tally("Rust gate records tie to the checked product (same files hash, Cargo.lock unchanged)")
    tb = Tally("Rust cargo build --locked --offline (exit 0, log hash kept)")
    tc = Tally("Rust cargo clippy --all-targets -- -D warnings (exit 0, no diagnostics in the log)")
    tt = Tally("Rust cargo test (gate log for phase1_suite, checker events for verify_all/flow)")
    tests_per_case = {}
    for cid in sorted(ids):
        gp = gdir / f"{cid}.json"
        g, err = load_json(gp)
        if err or not isinstance(g, dict):
            for t in (tr, tb, tc, tt):
                t.miss(cid, "no gate record")
            continue
        info = {}
        if cid in rows:
            r = rows[cid][1]
            info = {**(r.get("issue57") or {}), **(r.get("rust") or {})}
            checker = r.get("checker")
        else:
            checker = None
        gi = g.get("issue57") or {}
        prod = gdir / cid
        tie_ok = True
        if g.get("id") != cid or g.get("checker") != checker:
            tr.bad(cid, "gate record id or checker differs from the case row")
            tie_ok = False
        elif any(gi.get(k) != info.get(k) for k in ("files_sha256", "bundle_sha256", "compile_status", "files")):
            tr.bad(cid, "gate record issue57 block differs from the case row")
            tie_ok = False
        elif not prod.is_dir():
            tr.miss(cid, "gated product directory not kept")
            tie_ok = False
        else:
            try:
                got_files, _n = files_sha(prod, set())
            except (OSError, UnicodeDecodeError):
                got_files = None
            if got_files != info.get("files_sha256"):
                tr.bad(cid, "gated product files (after the gates) hash differently from the checked product")
                tie_ok = False
        lk = (g.get("gates") or {}).get("lock_unchanged") or {}
        if tie_ok and lk.get("exit_code") != 0:
            tr.bad(cid, "lock_unchanged gate did not exit 0")
            tie_ok = False
        if tie_ok:
            tr.good(cid)
        gg = g.get("gates") or {}

        def gate_log(name: str):
            e = gg.get(name)
            if not isinstance(e, dict):
                return None, "not recorded"
            if e.get("exit_code") != 0:
                return None, f"exit {e.get('exit_code')}"
            if want_argv.get(name) and e.get("argv") != want_argv[name]:
                return None, f"argv {e.get('argv')} differs from toolchain.json commands"
            lp = gdir / str(e.get("log"))
            if not e.get("log") or not lp.is_file():
                return None, "log missing"
            if sha_file(lp) != e.get("log_sha256"):
                return None, "log hash differs from the record"
            return lp.read_text(errors="replace"), ""

        text, why = gate_log("cargo_build")
        if text is None:
            tb.bad(cid, why) if why != "not recorded" else tb.miss(cid, why)
        elif "Finished" not in text or any(diag.match(l.strip()) for l in text.splitlines()):
            tb.bad(cid, "build log has no Finished line or has a diagnostic")
        else:
            tb.good(cid)
        text, why = gate_log("cargo_clippy")
        if text is None:
            tc.bad(cid, why) if why != "not recorded" else tc.miss(cid, why)
        elif "Finished" not in text or any(diag.match(l.strip()) for l in text.splitlines()):
            tc.bad(cid, "clippy log has no Finished line or has a diagnostic")
        else:
            tc.good(cid)
        if checker == "phase1_suite":
            text, why = gate_log("cargo_test")
            if text is None:
                tt.bad(cid, why) if why != "not recorded" else tt.miss(cid, why)
                continue
            res = [result.match(l.strip()) for l in text.splitlines() if l.strip().startswith("test result:")]
            passed = sum(int(m.group(2)) for m in res if m)
            failed = sum(int(m.group(3)) for m in res if m)
            if not res or not all(res) or any(m.group(1) != "ok" for m in res) or failed or passed == 0 \
                    or "FAILED" in text or "panicked" in text:
                tt.bad(cid, f"test log: {passed} passed, {failed} failed, {len(res)} result lines")
            elif ((gg.get("test_count") or {}).get("exit_code")) != 0:
                tt.bad(cid, "test_count gate did not exit 0")
            else:
                tests_per_case[cid] = passed
                tt.good(cid)
        else:
            kinds = gates.get(("rust", cid))
            if kinds is None:
                tt.miss(cid, "no checker process events kept")
            elif {"build", "test"} <= kinds:
                tt.good(cid)
            else:
                tt.bad(cid, f"cargo build and test not both in the checker events ({sorted(kinds)})")
    for t in (tr, tb, tc, tt):
        t.apply(row, ids)
    if tests_per_case:
        row.ev(f"phase1_suite cargo test logs: {len(tests_per_case)} cases, {min(tests_per_case.values())}.."
               f"{max(tests_per_case.values())} tests passed per case, 0 failed")
    # Toolchain: cargo's own rustc probe cache in the gates target directory.
    rust = (tc_.get("rust") or {})
    ri, err = load_json(ctx.run / "issue57" / "rust" / "target-gates" / ".rustc_info.json")
    outs = [str((v or {}).get("stdout") or "") for v in ((ri or {}).get("outputs") or {}).values()] if not err else []
    want_ver = (rust.get("rustc") or {}).get("version")
    want_commit = (rust.get("rustc") or {}).get("commit_hash")
    sysroot = str(Path(str(rust.get("bin_dir") or "")).parent)
    host = tc_.get("host")
    if not outs:
        row.unrun("no rustc probe cache in the gates target directory: toolchain not shown")
    elif not want_ver or not any(o.startswith(want_ver) and f"commit-hash: {want_commit}" in o
                                 and f"host: {host}" in o for o in outs):
        row.fail("gates target directory was built by another rustc than toolchain.json pins")
    elif not any(sysroot in o.splitlines() for o in outs):
        row.fail("gates target directory rustc sysroot differs from the toolchain bin_dir")
    else:
        row.ev(f"gates target directory rustc probe: {want_ver}, commit {want_commit[:9]}, host {host}, sysroot "
               f"{sysroot} (toolchain.json)")
    # The report's own gate map and worker exit codes must agree (they are not the verdict).
    pr = ((ctx.issue57 or {}).get("positive") or {}).get("rust") or {}
    gm = pr.get("gates")
    if not isinstance(gm, dict) or set(gm) != ids or not all(v is True for v in gm.values()):
        row.fail("issue57 summary positive.rust.gates does not list every case as passed")
    wx = pr.get("worker_exit_codes")
    if not isinstance(wx, list) or len(wx) != 3 or any(x != 0 for x in wx):
        row.fail(f"issue57 Rust worker exit codes {wx}, expected three zeros (suite, fixture, gates)")
    log = ctx.run / "issue57" / "rust" / "worker-gates.log"
    oks = {l.split(" gates:")[0].strip() for l in log.read_text(errors="replace").splitlines()
           if l.strip().endswith("gates: ok")} if log.is_file() else set()
    if oks != ids:
        row.fail(f"worker-gates.log reports ok for {len(oks)} cases, expected {len(ids)}")
    else:
        row.ev("issue57 summary gate map, three Rust worker exit codes and worker-gates.log agree (18 cases ok)")
    ctx.weak.append("check 7: cargo test of phase1_suite products ran on a fresh database by the gate record's "
                    "account; the logs show the tests passed but not which database they used")


# --------------------------------------------------------------- check 8

def check_names(ctx: Ctx) -> Row:
    row = Row("plan", 8, ctx.plan_checks[8] if len(ctx.plan_checks) > 8 else "name collisions")
    rep = ctx.names
    tie_to_receipt(ctx, row, ctx.names_name)
    if not isinstance(rep, dict):
        row.unrun(f"{ctx.names_name} unreadable")
        return row
    ex_dir = ctx.repo / EXAMPLES_DIR_REL
    n_examples = len(list(ex_dir.glob("*.prompt.json"))) + len(list(ex_dir.glob("*.cnl")))
    want = {"example": n_examples, "cnl_fixture": 2 * int(ctx.cnlpos.get("case_count") or 0),
            "typed_fixture": len(ctx.typed.get("fixtures") or {}), "public": 127, "legacy": 544}
    rows = rep.get("rows") or []
    groups = Counter(r.get("group") for r in rows)
    if dict(groups) != want:
        row.fail(f"name-collision rows per group {dict(groups)}, expected {want}")
    else:
        row.ev(f"name-collision rows per group {want}")
    t = Tally("prompts not refused by python_name_collisions")
    keys = set()
    for i, r in enumerate(rows):
        key = f"{r.get('group')}|{r.get('name')}"
        if key in keys:
            row.fail(f"duplicate row {key}")
        keys.add(key)
        if r.get("refused_by_python_name_collisions") is not False:
            t.bad(key, "refused by python_name_collisions or not recorded")
        elif r.get("status") != 200:
            t.bad(key, f"status {r.get('status')} {r.get('code')}")
        else:
            t.good(key)
    t.apply(row, keys)
    # The names must be exactly the plan's prompt sets.
    exp = {f"example|{f.name}" for f in ex_dir.iterdir() if f.name.endswith((".prompt.json", ".cnl"))}
    exp |= {f"cnl_fixture|{c.get('id')} {form}" for c in ctx.cnlpos.get("cases") or [] for form in ("json", "cnl")}
    exp |= {f"typed_fixture|{k}" for k in ctx.typed.get("fixtures") or {}}
    inputs = (ctx.replay or {}).get("inputs") or {}
    pub = Path(str(inputs.get("public_run") or ctx.repo / ".local/rust-compiler-goal/public-products-003b"))
    leg = Path(str(inputs.get("legacy_run") or ctx.repo / ".local/rust-compiler-goal/acceptance/legacy544/run-005"))
    pdirs = sorted(d.name for d in pub.iterdir() if d.is_dir() and (d / "result.json").is_file()) if pub.is_dir() else []
    lres, _ = load_json(leg / "result.json")
    exp |= {f"public|{d}" for d in pdirs}
    exp |= {f"legacy|{i:04d} {r.get('id')}" for i, r in enumerate((lres or {}).get("records") or [])}
    if len(pdirs) != 127 or len((lres or {}).get("records") or []) != 544:
        row.unrun("accepted public/legacy runs not readable for the expected name sets")
    elif keys != exp:
        row.fail(f"names differ from the plan sets: missing {first(exp - keys, 8)}, extra {first(keys - exp, 8)}")
    else:
        row.ev(f"the {len(keys)} names are exactly the compiler-v1 example files, the CNL positive fixtures in "
               f"json and cnl, the typed fixtures, the 127 public product directories and the 544 legacy jobs")
    ctx.weak.append("check 8: name-collisions.json records names and statuses only (no prompt hashes or reply "
                    "hashes), so which prompt bytes were sent is attested by the tool")
    return row


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    args = ap.parse_args(argv)
    out = args.out.resolve()
    if out.exists():
        print(f"refusing to overwrite {out}", file=sys.stderr)
        return 2
    run = args.run.resolve()
    if not run.is_dir():
        print(f"no run directory {run}", file=sys.stderr)
        return 2
    ctx = Ctx(run, args.repo.resolve())
    rows: list[Row] = []
    for fn in (check_one_candidate, check_default_unchanged, check_tools_unchanged, check_receipts,
               check_exit_codes, check_frozen, check_replay, check_controls, check_validate, check_targets_cli,
               check_rust_products, check_no_unsupported, check_rust_unchanged, check_issue57, check_names):
        try:
            rows.append(fn(ctx))
        except Exception as exc:  # an audit bug must not pass silently
            r = Row("error", fn.__name__, f"{fn.__name__} raised")
            r.fail(f"{exc.__class__.__name__}: {exc}")
            rows.append(r)
    audit = Row("plan", 9, ctx.plan_checks[9] if len(ctx.plan_checks) > 9 else "independent audit")
    audit.ev(f"this report, written by {AUDIT_REL} (sha256 {sha_file(Path(__file__))}) from the plan and the raw "
             f"outputs only")
    rows.append(audit)
    if ctx.load_errors:
        r = Row("run", 6, "Every contract and fixture file the audit needs is readable.")
        for e in ctx.load_errors:
            r.fail(e)
        rows.append(r)
    if len(ctx.plan_checks) != 10:
        r = Row("run", 7, "The issue57 plan has exactly 10 checks.")
        r.fail(f"the plan has {len(ctx.plan_checks)} checks")
        rows.append(r)
    dicts = [r.as_dict() for r in rows]
    totals = Counter(d["state"] for d in dicts)
    status = "passed" if not totals.get("failed") and not totals.get("unrun") else "failed"
    report = {
        "version": VERSION,
        "run": str(run),
        "status": status,
        "totals": {"passed": totals.get("passed", 0), "failed": totals.get("failed", 0),
                   "unrun": totals.get("unrun", 0)},
        "plan_sha256": sha_file(ctx.plan_path),
        "audit_sha256": sha_file(Path(__file__)),
        "accepted_run": str(ctx.acc_run),
        "weak_evidence": sorted(set(ctx.weak)),
        "rows": dicts,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps({"status": status, "totals": report["totals"], "out": str(out)}))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
