#!/usr/bin/env python3
"""Independent audit of an issue #55 Rust output acceptance run.

Written from som-rs/contracts/rust-output-v1/acceptance-plan.json and the raw
outputs of a run only.  It does not import or run any acceptance tool, does
not contact any port and only reads files.  Every verdict is derived again from
the recorded raw values (status codes, hashes, file bytes); the run's own
``passed`` and ``summary`` judgments are never the verdict.  Where the only
raw record of a sub-check is a per-case boolean of a case row, the evidence
says "attested" for it.  Anything that cannot be checked is "unrun".

Usage:
    audit055.py --run RUN_DIR --out NEW_REPORT.json

Exit status is 0 only when the report has no failed and no unrun row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

VERSION = "som.rust-output-audit-055.v1"
HEALTH_BACKEND_SHA256 = "1c18257000d2519e85c644d5bb243d5ec7251878efadf4cf27f3bc5b26a2e92b"
HEALTH_ASSET_MANIFEST_SHA256 = "872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e"
DEFAULT_PORT = 8765
REFUSAL_RULE = "query_parameter_collision"
AUDIT_REL = "som-rs/scripts/rust_output_acceptance/audit/audit055.py"
FORBIDDEN_CODES = {"NOT_IMPLEMENTED", "OUTPUT_QUALITY_FAILED"}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
LIST_LIMIT = 15
RUST_ASSET_KEYS = {"backend", "block_library", "catalog", "formatter", "target_contract"}
PHASE1_ALLOWED_HASH_POINTERS = {"/hashes/payload_sha256"}
HEAVY_DIRS = {"work", "gates-work", "target", "cases", "gates", "logs", "receipts",
              "products", "raw", "fixtures", "check-results"}
SHAPE_FAMILY = {"resource": "resource", "capability_postgresql": "capability",
                "capability_mongodb": "capability", "flow_postgresql": "flow",
                "flow_mongodb": "flow"}
TEST_RESULT = re.compile(r"test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; "
                         r"(\d+) measured; (\d+) filtered out")


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
    """sha256 over sorted 'relpath NUL sha256(file) LF' records (derived from run receipts)."""
    key = Path(root).resolve()
    if key not in _TREE_CACHE:
        _TREE_CACHE[key] = _tree_sha(key)
    return _TREE_CACHE[key]


def _tree_sha(root: Path) -> str:
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = Path(dirpath) / name
            files.append((os.path.relpath(p, root), p))
    h = hashlib.sha256()
    for rel, p in sorted(files):
        h.update(rel.encode() + b"\0" + sha_file(p).encode() + b"\n")
    return h.hexdigest()


def files_sha(root: Path) -> tuple[str, dict[str, str]]:
    """files_sha256 formula of the product rows: sha256 of the compact sorted JSON map path->text.

    check-results/ and target/ at the top are run artifacts, not assembled files.
    """
    texts: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for dirpath, _dirs, names in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir.split(os.sep)[0] in ("check-results", "target"):
            continue
        for name in names:
            p = Path(dirpath) / name
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            data = p.read_bytes()
            hashes[rel] = sha_bytes(data)
            texts[rel] = data.decode("utf-8")
    blob = json.dumps(texts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sha_bytes(blob), hashes


def load_json(path: Path):
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read()), None
    except FileNotFoundError:
        return None, f"{path} missing"
    except (OSError, ValueError) as exc:
        return None, f"{path} unreadable: {exc.__class__.__name__}"


def norm(case_id: str) -> str:
    return case_id.replace("-", "_")


def norm_lower(case_id: str) -> str:
    return case_id.lower().replace("-", "_")


def gate_passed(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return value.get("passed") is True
    return False


def first(items, limit=LIST_LIMIT):
    items = sorted(items)
    more = len(items) - limit
    return items[:limit] + ([f"... {more} more"] if more > 0 else [])


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

    def apply(self, row: Row, expected: list[str] | set[str]):
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
            reasons = {}
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
    def __init__(self, run: Path, repo: Path, phase1_run: Path):
        self.run = run
        self.repo = repo
        self.contracts = repo / "som-rs" / "contracts" / "rust-output-v1"
        self.phase1_run = phase1_run
        self.load_errors: list[str] = []

        self.plan_path = self.contracts / "acceptance-plan.json"
        self.plan = self._must(self.plan_path)
        self.inv1 = self._must(self.contracts / "case-inventory.json")
        self.inv2 = self._must(self.contracts / "phase2" / "case-inventory.json")
        self.behavior = self._must(self.contracts / "phase2" / "behavior.json")
        self.neg_path = self.contracts / "negative-cases.json"
        self.neg = self._must(self.neg_path)
        self.tex_path = self.contracts / "target-examples.json"
        self.tex = self._must(self.contracts / "target-examples.json")
        self.layout = self._must(self.contracts / "product-layout.json")

        # Case sets from the contracts.
        inv1 = self.inv1 or {}
        self.acc1 = ([x["id"] for x in inv1.get("public_examples", []) if x.get("rust") == "in_scope"]
                     + [x["id"] for x in inv1.get("version_positive_fixtures", []) if x.get("rust") == "in_scope"]
                     + [x["id"] for x in inv1.get("coverage_cases", [])]
                     + [x["id"] for x in inv1.get("legacy_in_scope", [])])
        self.legacy100 = [x["id"] for x in inv1.get("legacy_in_scope", [])]
        self.coverage = [x["id"] for x in inv1.get("coverage_cases", [])]
        self.rows2 = (self.inv2 or {}).get("rows", [])
        self.rows2_by_id = {r["id"]: r for r in self.rows2}
        self.ids2 = [r["id"] for r in self.rows2]
        self.refusal_ids = {r["id"] for r in self.rows2
                            if (r.get("phase2_expected") or {}).get("result") == "refusal"}
        self.product_ids = [i for i in self.ids2 if i not in self.refusal_ids]
        self.legacy444 = [r["id"] for r in self.rows2 if r.get("kind") == "legacy"]
        self.neg_by_id = {c["id"]: c for c in (self.neg or {}).get("cases", [])}

        # Run root files.
        self.header, _ = load_json(run / "header.json")
        summary, _ = load_json(run / "summary.json")
        self.summary = summary if isinstance(summary, dict) and ("steps" in summary or "plan_checks" in summary) else None
        self.receipts = self._load_receipts()
        self.targets, _ = load_json(run / "targets.json")
        self.cli, _ = load_json(run / "cli.json")
        self.pyctl, _ = load_json(run / "python-controls.json")
        self.validate, _ = load_json(run / "validate-cases.json")
        self.replay, _ = load_json(run / "python-replay.json")
        self.parity, _ = load_json(run / "behavior" / "parity" / "report-parity.json")

        self.products_checks = []   # (path, obj) with version som.rust-output-product-checks*
        self.phase1_unchanged = []  # (path, obj)
        self.products_summaries = []  # (path, obj)
        self.p2_dirs = {"rust": [], "python": []}
        self._discover()
        self.p2 = self._load_phase2("rust")
        self.py2_run = self._load_phase2("python")

        # Accepted phase 1 evidence (rust-output-050).
        self.acc_products, _ = load_json(phase1_run / "rust-products.json")
        self.acc_pyctl, _ = load_json(phase1_run / "python-controls.json")
        self.acc_validate, _ = load_json(phase1_run / "validate-cases.json")
        self.acc_targets, _ = load_json(phase1_run / "targets.json")
        self.acc_replay, _ = load_json(phase1_run / "python-replay.json")
        self.acc_header, _ = load_json(phase1_run / "header.json")

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

    def _discover(self):
        roots = [self.run]
        for child in sorted(self.run.iterdir()) if self.run.is_dir() else []:
            if child.is_dir() and child.name not in HEAVY_DIRS:
                roots.append(child)
                for grand in sorted(child.iterdir()):
                    if grand.is_dir() and grand.name not in HEAVY_DIRS:
                        roots.append(grand)
        seen = set()
        for d in roots:
            for p in sorted(d.glob("*.json")):
                if p in seen:
                    continue
                seen.add(p)
                if p.name == "products-summary.json":
                    obj, _ = load_json(p)
                    self.products_summaries.append((p, obj))
                    continue
                try:
                    if p.stat().st_size > 64 << 20:
                        continue
                except OSError:
                    continue
                obj, _ = load_json(p)
                if not isinstance(obj, dict):
                    continue
                ver = str(obj.get("version", ""))
                if ver.startswith("som.rust-output-product-checks"):
                    self.products_checks.append((p, obj))
                elif ver.startswith("som.rust-output-phase1-unchanged"):
                    self.phase1_unchanged.append((p, obj))
                elif p.name == "summary.json" and ver == "som.rust-output-phase2-rust-behavior.v1":
                    self.p2_dirs["rust"].append(p.parent)
                elif p.name == "summary.json" and ver == "som.rust-output-phase2-python-behavior.v1":
                    self.p2_dirs["python"].append(p.parent)

    def _load_phase2(self, language: str):
        res = {"dirs": [], "summaries": [], "cases": {}, "gates": {}, "dupes": set(), "errors": []}
        for d in self.p2_dirs[language]:
            summ, err = load_json(d / "summary.json")
            res["dirs"].append(d)
            res["summaries"].append((d, summ, sha_file(d / "summary.json")))
            for sub, key in (("cases", "cases"), ("gates", "gates")):
                sdir = d / sub
                if not sdir.is_dir():
                    continue
                for p in sorted(sdir.glob("*.json")):
                    if p.name.endswith(".outcome.json"):
                        continue
                    obj, err = load_json(p)
                    if err or not isinstance(obj, dict) or "id" not in obj:
                        res["errors"].append(f"{p.name}: unreadable or without id")
                        continue
                    cid = obj["id"]
                    if cid in res[key]:
                        res["dupes"].add(cid)
                    res[key][cid] = (p, obj, d)
        return res

    def receipt_for_output(self, name: str):
        for p, obj, err in self.receipts:
            if isinstance(obj, dict) and name in (obj.get("outputs") or {}):
                return p, obj
        return None, None


def tie_to_receipt(ctx: Ctx, row: Row, name: str) -> bool:
    """The artifact is the recorded output of a receipt with exit status 0."""
    path = ctx.run / name
    if not path.exists():
        row.unrun(f"{name} missing from the run")
        return False
    rp, rec = ctx.receipt_for_output(name)
    if rec is None:
        row.unrun(f"no receipt records {name}")
        return False
    want = rec["outputs"][name]
    got = sha_file(path) if not str(want).startswith("tree:") else "tree:" + tree_sha(path)
    if got != want:
        row.fail(f"{name} hash {got[:16]} differs from receipt {rp.name} ({str(want)[:16]})")
        return False
    if rec.get("exit_code") != 0:
        row.fail(f"receipt {rp.name} exit code {rec.get('exit_code')}")
        return False
    row.ev(f"{name} sha256 {got} equals receipt {rp.name}, exit 0")
    return True


def git(ctx: Ctx, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ctx.repo), *args], capture_output=True)


def check_candidate_source(ctx: Ctx, row: Row, cand: Path, cb: dict):
    """The tools ran at a later commit than the candidate only if that commit builds the same candidate:
    the candidate commit is an ancestor of the tools head, no path under the candidate's source roots
    changed between them, and every file the candidate recorded in source.json hashes the same at the head."""
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


# ----------------------------------------------------------- run integrity

def check_one_candidate(ctx: Ctx) -> Row:
    row = Row("run", 0, "One recorded candidate build: header candidate_before equals summary "
              "candidate_after, health hashes are the expected ones, and the candidate files still hash "
              "to the recorded values.")
    h = ctx.header
    if not isinstance(h, dict) or "candidate_before" not in h:
        row.unrun("header.json with candidate_before missing")
        return row
    cb = h["candidate_before"]
    health = cb.get("health") or {}
    if health.get("backend_sha256") == HEALTH_BACKEND_SHA256:
        row.ev(f"candidate_before health backend_sha256 {HEALTH_BACKEND_SHA256}")
    else:
        row.fail(f"candidate_before health backend_sha256 {health.get('backend_sha256')}")
    if health.get("asset_manifest_sha256") == HEALTH_ASSET_MANIFEST_SHA256:
        row.ev(f"candidate_before health asset_manifest_sha256 {HEALTH_ASSET_MANIFEST_SHA256}")
    else:
        row.fail(f"candidate_before health asset_manifest_sha256 {health.get('asset_manifest_sha256')}")
    bad = [k for k, v in (cb.get("checks") or {}).items() if v is not True]
    if not cb.get("checks"):
        row.unrun("candidate_before checks missing")
    elif bad:
        row.fail(f"candidate_before checks false: {bad}")
    if cb.get("problems"):
        row.fail(f"candidate_before problems: {len(cb['problems'])}")
    port = cb.get("port")
    if port == DEFAULT_PORT:
        row.fail("candidate runs on the default port 8765")

    # Files of the candidate, hashed now.
    cand = Path(str(cb.get("candidate", "")))
    if not cand.is_dir():
        row.fail(f"candidate directory {cand} missing")
    else:
        binary = cand / "som"
        if not binary.is_file():
            sized = [p for p in cand.iterdir() if p.is_file() and p.stat().st_size == cb.get("binary_bytes")]
            binary = sized[0] if len(sized) == 1 else binary
        if binary.is_file():
            got = sha_file(binary)
            if got == cb.get("binary_sha256") and binary.stat().st_size == cb.get("binary_bytes"):
                row.ev(f"candidate binary {binary.name} hashes to {got} ({cb.get('binary_bytes')} bytes)")
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

    # Every service the run used is the candidate (or the read-only reference / baseline).
    url = f"http://127.0.0.1:{port}"
    uses = []
    for name, obj, keys in (("targets.json", ctx.targets, ("qa",)), ("cli.json", ctx.cli, ("base_url",)),
                            ("python-controls.json", ctx.pyctl, ("base_url",)),
                            ("python-replay.json", ctx.replay, ("base_url",))):
        if isinstance(obj, dict):
            for k in keys:
                uses.append((name, k, obj.get(k)))
    for p, obj in ctx.products_checks + ctx.phase1_unchanged:
        uses.append((p.name, "qa", obj.get("qa")))
    for d in ctx.p2["dirs"] + ctx.py2_run["dirs"]:
        for fname in ("run.json", "summary.json"):
            rj, _ = load_json(d / fname)
            if isinstance(rj, dict) and "qa" in rj:
                uses.append((f"{d.name}/{fname}", "qa", rj.get("qa")))
    wrong = [f"{n}:{k}={v}" for n, k, v in uses if v is not None and v != url]
    if wrong:
        row.fail(f"artifacts used another service than the candidate {url}: {wrong}")
    elif uses:
        row.ev(f"{len(uses)} artifact service fields equal {url}")
    if isinstance(ctx.cli, dict):
        if ctx.cli.get("binary_sha256") != cb.get("binary_sha256"):
            row.fail("cli.json binary_sha256 differs from the candidate binary")
        else:
            row.ev("cli.json binary_sha256 equals the candidate binary")
    allowed_ports = {str(port), str(DEFAULT_PORT), str(h.get("baseline_port"))}
    for p, rec, err in ctx.receipts:
        if not isinstance(rec, dict):
            continue
        argv = [str(a) for a in rec.get("argv") or []]
        for i, a in enumerate(argv):
            for m in re.findall(r"127\.0\.0\.1:(\d+)", a):
                if m not in allowed_ports:
                    row.fail(f"receipt {p.name} uses port {m}")
                if m == str(DEFAULT_PORT) and i > 0 and argv[i - 1] in ("--qa", "--base-url", "--url"):
                    row.fail(f"receipt {p.name} uses 8765 as the service under test")
    for p, obj in ctx.phase1_unchanged:
        base = obj.get("baseline") or {}
        acc_bin = ((ctx.acc_header or {}).get("candidate_before") or {}).get("binary_sha256")
        if acc_bin and base.get("binary_sha256") != acc_bin:
            # The baseline binary is the phase 1 compiler; record but compare with the 050 run.
            row.ev(f"{p.name} baseline binary {str(base.get('binary_sha256'))[:16]} is not the rust-output-050 "
                   f"candidate binary {acc_bin[:16]} (baseline identity checked by its record hash only)")
        if str(base.get("port")) == str(port):
            row.fail(f"{p.name} baseline port equals the candidate port")

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
    return row


def check_default_unchanged(ctx: Ctx) -> Row:
    row = Row("run", 1, "The default service on 8765 is unchanged: header default_before equals summary default_after.")
    before = (ctx.header or {}).get("default_before")
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


def check_tools_unchanged(ctx: Ctx) -> Row:
    row = Row("run", 2, "The acceptance tools and contracts recorded in the header still hash to the recorded "
              "values, and the summary records the same tools.")
    tools = (ctx.header or {}).get("tools")
    if not isinstance(tools, dict) or not tools.get("files"):
        row.unrun("header tools missing")
        return row
    # The auditor is not a tool of the run: it is updated and committed after the run ends, so its own
    # file is left out of this comparison. Every other recorded file is compared strictly.
    files = {k: v for k, v in tools["files"].items() if k != AUDIT_REL}
    if len(files) != len(tools["files"]):
        row.ev(f"excluded the auditor's own file {AUDIT_REL} from the tool comparison (header sha256 "
               f"{tools['files'][AUDIT_REL]}); it is not a run tool")
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
        row.ev(f"{len(files)} tool and contract files hash to the header values now")
    rel_plan = "som-rs/contracts/rust-output-v1/acceptance-plan.json"
    if tools["files"].get(rel_plan) not in (None, sha_file(ctx.plan_path)):
        row.fail("the audited acceptance plan is not the one the run used")
    if tools.get("porcelain"):
        row.fail("the header records a dirty worktree")
    summ = ctx.summary
    if not summ:
        row.unrun("summary.json missing (run not finished)")
        return row
    if summ.get("plan_sha256") and summ["plan_sha256"] != sha_file(ctx.plan_path):
        row.fail("summary plan_sha256 differs from the plan file")
    after = summ.get("tools_after") or summ.get("tools")
    if isinstance(after, dict):
        after_files = {k: v for k, v in (after.get("files") or {}).items() if k != AUDIT_REL}
        if after_files != files or after.get("head") != tools.get("head"):
            row.fail("summary tools differ from header tools")
        else:
            row.ev("summary tools equal header tools (the auditor's own file excluded)")
    else:
        row.ev("summary records no tools after the run; the files were hashed again by this audit")
    return row


def check_receipts(ctx: Ctx) -> Row:
    row = Row("run", 3, "Every step receipt is kept: its outputs and logs hash to the recorded values, its exit "
              "code is 0, and the summary lists every receipt by hash.")
    if not ctx.receipts:
        row.unrun("no receipts directory or no receipts")
        return row
    names = {}
    env = (ctx.header or {}).get("env_delta")
    for p, rec, err in ctx.receipts:
        if err or not isinstance(rec, dict):
            row.fail(f"receipt {p.name} unreadable")
            continue
        names[rec.get("step")] = (p, rec)
        if rec.get("exit_code") != 0:
            row.fail(f"receipt {p.name} exit code {rec.get('exit_code')}")
        for out, want in (rec.get("outputs") or {}).items():
            target = ctx.run / out
            if not target.exists():
                row.fail(f"receipt {p.name} output {out} missing")
                continue
            got = "tree:" + tree_sha(target) if str(want).startswith("tree:") else sha_file(target)
            if got != want:
                row.fail(f"receipt {p.name} output {out} hash differs")
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
    row.ev(f"{len(ctx.receipts)} receipts checked: outputs, logs and exit codes")
    summ = ctx.summary
    if not summ:
        row.unrun("summary.json missing (run not finished); receipt list not sealed")
        return row
    if summ.get("header_sha256") != sha_file(ctx.run / "header.json"):
        row.fail("summary header_sha256 differs from header.json")
    else:
        row.ev("summary header_sha256 equals header.json")
    steps = summ.get("steps") or []
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
        if s.get("exit_code") != rec.get("exit_code"):
            row.fail(f"summary exit code of {s.get('step')} differs from the receipt")
    extra = set(names) - listed
    if extra:
        row.fail(f"receipts not listed in the summary: {sorted(map(str, extra))}")
    if steps:
        row.ev(f"summary lists {len(steps)} steps by receipt hash")
    else:
        row.unrun("summary lists no steps")
    if summ.get("blockers"):
        row.fail(f"summary records {len(summ['blockers'])} blockers")
    return row


def check_case_receipts(ctx: Ctx) -> tuple[Row, dict]:
    """Phase 2 per-case receipts: every inventory row has a row file, an outcome and a gate file."""
    row = Row("run", 4, "Every phase 2 case receipt is kept: the 584 inventory rows each have a case row, an "
              "outcome and a gate file whose hashes chain to the phase 2 summary.")
    info = {"chain_ok": set()}
    inv = ctx.inv2 or {}
    rows2 = ctx.rows2
    kinds = {}
    for r in rows2:
        kinds[r.get("kind")] = kinds.get(r.get("kind"), 0) + 1
    if len(rows2) != 584 or len(set(ctx.ids2)) != 584:
        row.fail(f"phase2 inventory has {len(rows2)} rows, {len(set(ctx.ids2))} distinct ids")
    else:
        row.ev(f"phase2 inventory: 584 distinct ids, kinds {dict(sorted(kinds.items()))}")
    if ctx.refusal_ids != {"RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"}:
        row.fail(f"inventory refusal rows are {sorted(ctx.refusal_ids)}")
    p2 = ctx.p2
    if not p2["dirs"]:
        row.unrun("no phase 2 Rust behavior directory in the run")
        return row, info
    row.ev(f"phase 2 Rust directories: {[str(d.relative_to(ctx.run)) if d != ctx.run else '.' for d in p2['dirs']]}")
    p2_tied(ctx, row)
    if p2["dupes"]:
        row.fail(f"case ids recorded twice: {first(p2['dupes'])}")
    if p2["errors"]:
        row.fail(f"unreadable case files: {first(p2['errors'])}")
    case_sha = {}
    for d, summ, _h in p2["summaries"]:
        if isinstance(summ, dict):
            for cid, h in (summ.get("case_sha256") or {}).items():
                if cid in case_sha and case_sha[cid] != h:
                    row.fail(f"case {cid} recorded with two hashes")
                case_sha[cid] = h
            if summ.get("language") != "rust":
                row.fail(f"{d.name}/summary.json language {summ.get('language')}")
    extra = set(case_sha) - set(ctx.ids2)
    if extra:
        row.fail(f"summary hashes cases outside the inventory: {first(extra)}")
    t = Tally("case receipt chain")
    for cid in ctx.ids2:
        c = p2["cases"].get(cid)
        g = p2["gates"].get(cid)
        if c is None or g is None:
            t.miss(cid, "case row or gate file missing")
            continue
        cpath, cobj, cdir = c
        gpath, gobj, _ = g
        ch = sha_file(cpath)
        ok = True
        if cid not in case_sha:
            t.miss(cid, "case not hashed in a phase 2 summary")
            ok = False
        elif case_sha[cid] != ch:
            t.bad(cid, "case row hash differs from the summary")
            ok = False
        outp = cpath.with_name(cpath.stem + ".outcome.json")
        if cobj.get("outcome_sha256"):
            if not outp.is_file():
                t.bad(cid, "outcome file missing")
                ok = False
            elif sha_file(outp) != cobj["outcome_sha256"]:
                t.bad(cid, "outcome hash differs from the case row")
                ok = False
        elif cid not in ctx.refusal_ids:
            t.bad(cid, "case row records no outcome")
            ok = False
        beh = gobj.get("behavior") or {}
        if cid not in ctx.refusal_ids or beh:
            if beh.get("row_sha256") != ch:
                t.bad(cid, "gate behavior.row_sha256 differs from the case row")
                ok = False
        inv_row = ctx.rows2_by_id[cid]
        for k in ("prompt_sha256", "product_sha256", "prompt_version", "checker"):
            if cobj.get(k) != inv_row.get(k) or gobj.get(k, inv_row.get(k)) != inv_row.get(k):
                t.bad(cid, f"{k} differs from the inventory")
                ok = False
        if ok:
            t.good(cid)
            info["chain_ok"].add(cid)
    t.apply(row, ctx.ids2)
    return row, info


def check_gate_hashes(ctx: Ctx) -> Row:
    row = Row("run", 5, "products-summary.json gate_sha256 records every phase 2 gate file by hash.")
    if not ctx.products_summaries:
        row.unrun("products-summary.json missing from the run")
        return row
    gate_sha = {}
    for p, obj in ctx.products_summaries:
        tie_any(ctx, row, p)
        found = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "gate_sha256" and isinstance(v, dict):
                        found.append(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(obj)
        if not found:
            row.fail(f"{p.relative_to(ctx.run)} has no gate_sha256 map")
        for m in found:
            gate_sha.update(m)
        row.ev(f"{p.relative_to(ctx.run)} sha256 {sha_file(p)}")
    t = Tally("gate hash")
    for cid in ctx.ids2:
        g = ctx.p2["gates"].get(cid)
        want = gate_sha.get(cid, gate_sha.get(norm(cid), gate_sha.get(norm(cid) + ".json")))
        if g is None:
            t.miss(cid, "gate file missing")
        elif want is None:
            t.miss(cid, "not in gate_sha256")
        elif sha_file(g[0]) != want:
            t.bad(cid, "gate file hash differs from gate_sha256")
        else:
            t.good(cid)
    t.apply(row, ctx.ids2)
    return row


def load_python_phase2(ctx: Ctx, row: Row):
    """Python phase 2 behavior results: the frozen run named by behavior.json, and the run's own if any."""
    results = {}
    pr = (ctx.behavior or {}).get("python_run") or {}
    out = pr.get("out")
    if not out:
        row.unrun("behavior.json python_run.out missing")
        return results
    d = ctx.repo / out
    sp = d / "summary.json"
    if not sp.is_file():
        row.unrun(f"python run summary {out}/summary.json missing")
        return results
    if sha_file(sp) != pr.get("summary_sha256"):
        row.fail("python run summary hash differs from behavior.json python_run.summary_sha256")
        return results
    row.ev(f"{out}/summary.json sha256 equals behavior.json python_run {pr.get('summary_sha256')}")
    summ, _ = load_json(sp)
    case_sha = (summ or {}).get("case_sha256") or {}
    if set(case_sha) != set(ctx.ids2):
        row.fail(f"python run hashes {len(case_sha)} cases, not the 584 inventory ids")
    rows = {}
    bad_hash = []
    for p in sorted((d / "cases").glob("*.json")):
        if p.name.endswith(".outcome.json"):
            continue
        obj, err = load_json(p)
        if not isinstance(obj, dict):
            continue
        cid = obj.get("id")
        if case_sha.get(cid) != sha_file(p):
            bad_hash.append(cid)
            continue
        if obj.get("outcome_sha256"):
            op = p.with_name(p.stem + ".outcome.json")
            if not op.is_file() or sha_file(op) != obj["outcome_sha256"]:
                bad_hash.append(cid)
                continue
        rows[cid] = obj
    if bad_hash:
        row.fail(f"python case rows with a broken hash chain: {first(bad_hash)}")
    if len(rows) == 584 and not bad_hash:
        row.ev("584 python case rows chain to the python run summary")
    failed = sorted(c for c, o in rows.items() if o.get("passed") is not True)
    if failed != sorted(pr.get("failed") or []):
        row.fail(f"python failures {failed} differ from behavior.json python_run.failed")
    else:
        row.ev(f"python failures recomputed from the case rows: {failed}")
    for c, o in rows.items():
        results[c] = o
    # The run's own python phase 2 run, if it has one.
    for dd, summ2, h in ctx.py2_run["summaries"]:
        row.ev(f"run has its own python phase 2 run {dd.name} (summary {h[:16]})")
    for cid, (p, o, dd) in ctx.py2_run["cases"].items():
        prev = results.get(cid)
        if prev is not None and bool(prev.get("passed")) != bool(o.get("passed")):
            row.fail(f"python result of {cid} differs between the frozen run and the run's own")
    return results


def check_python_run(ctx: Ctx) -> tuple[Row, dict]:
    row = Row("run", 6, "The Python phase 2 behavior run hashes to behavior.json python_run and its case rows chain to it.")
    res = load_python_phase2(ctx, row)
    return row, res


# --------------------------------------------------------- python_unchanged

def exchange_ok(e) -> bool:
    return (e.get("response_sha256") == e.get("expected_response_sha256") and e.get("expected_response_sha256")
            and e.get("status") == e.get("expected_status"))


def check_pu0(ctx: Ctx) -> Row:
    plan = ctx.plan["python_unchanged"]["checks"]
    row = Row("python_unchanged", 0, plan[0])
    if not tie_to_receipt(ctx, row, "python-replay.json"):
        return row
    rp = ctx.replay
    rows = rp.get("public_rows") or []
    ids = [r.get("id") for r in rows]
    if len(rows) != 127 or len(set(ids)) != 127:
        row.fail(f"{len(rows)} public rows, {len(set(ids))} distinct")
    bad = [r.get("id") for r in rows if not r.get("exchanges") or not all(exchange_ok(e) for e in r["exchanges"])
           or r.get("accepted_passed") is not True]
    if bad:
        row.fail(f"public products whose replies differ from the accepted bytes: {first(bad)}")
    n = sum(len(r.get("exchanges") or []) for r in rows)
    row.ev(f"{len(rows) - len(bad)}/127 public products: {n} compile and assemble replies equal the accepted response hashes and statuses")
    acc = {r.get("id"): r for r in (ctx.acc_replay or {}).get("public_rows") or []}
    if acc:
        drift = [r["id"] for r in rows if r.get("id") in acc and
                 [e.get("expected_response_sha256") for e in r.get("exchanges", [])] !=
                 [e.get("expected_response_sha256") for e in acc[r["id"]].get("exchanges", [])]]
        if drift or set(acc) != set(ids):
            row.fail(f"accepted public responses differ from the rust-output-050 record: {first(drift)}")
        else:
            row.ev("accepted response hashes equal those of the rust-output-050 record")
    if (ctx.acc_replay or {}).get("inputs", {}).get("public_result_sha256") not in (None, rp.get("inputs", {}).get("public_result_sha256")):
        row.fail("public accepted result hash differs from the rust-output-050 record")
    return row


def check_pu1(ctx: Ctx) -> Row:
    plan = ctx.plan["python_unchanged"]["checks"]
    row = Row("python_unchanged", 1, plan[1])
    if not tie_to_receipt(ctx, row, "python-replay.json"):
        return row
    rp = ctx.replay
    rows = rp.get("legacy_rows") or []
    ids = {r.get("id") for r in rows}
    want = set(ctx.legacy100) | set(ctx.legacy444)
    if len(rows) != 544 or ids != want or len(want) != 544:
        row.fail(f"legacy rows {len(rows)} ({len(ids)} distinct) do not equal the 544 legacy jobs of the inventories")
    bad = [r.get("id") for r in rows if not exchange_ok(r.get("exchange") or {}) or r.get("accepted_passed") is not True]
    if bad:
        row.fail(f"legacy jobs whose assemble reply differs from the accepted bytes: {first(bad)}")
    comp = rp.get("legacy_compile_rows") or []
    cbad = [r.get("prompt_sha256") for r in comp if not r.get("exchanges") or not all(exchange_ok(e) for e in r["exchanges"])]
    if cbad:
        row.fail(f"legacy compile prompts whose replies differ: {len(cbad)}")
    row.ev(f"{len(rows) - len(bad)}/544 legacy jobs keep the accepted assemble reply; "
           f"{len(comp) - len(cbad)}/{len(comp)} distinct legacy prompts keep the accepted compile replies "
           f"({sum(len(r.get('exchanges') or []) for r in comp)} exchanges)")
    if (ctx.acc_replay or {}).get("inputs", {}).get("legacy_result_sha256") not in (None, rp.get("inputs", {}).get("legacy_result_sha256")):
        row.fail("legacy accepted result hash differs from the rust-output-050 record")
    return row


def check_pu2(ctx: Ctx) -> Row:
    plan = ctx.plan["python_unchanged"]["checks"]
    row = Row("python_unchanged", 2, plan[2])
    inv1 = ctx.inv1 or {}
    l100 = inv1.get("legacy_in_scope") or []
    if len(l100) != 100 or any(x.get("group") != "legacy100" for x in l100):
        row.fail("case-inventory.json legacy_in_scope is not the 100 legacy100 jobs")
    else:
        row.ev("case-inventory.json legacy_in_scope: 100 legacy100 jobs")
    fam = {}
    for r in ctx.rows2:
        if r.get("kind") == "legacy":
            fam.setdefault(r.get("family"), set()).add(r["id"])
    if len(fam.get("legacy214", ())) != 214:
        row.fail(f"phase2 inventory legacy214 has {len(fam.get('legacy214', ()))} jobs")
    else:
        row.ev("phase2 case-inventory.json legacy214: 214 jobs")
    if not tie_to_receipt(ctx, row, "python-replay.json"):
        return row
    by_fam = {}
    for r in ctx.replay.get("legacy_rows") or []:
        by_fam.setdefault(r.get("family"), set()).add(r.get("id"))
    if by_fam.get("legacy100") != set(ctx.legacy100):
        row.fail("python replay legacy100 family is not the inventory legacy100 set")
    if by_fam.get("legacy214") != fam.get("legacy214"):
        row.fail("python replay legacy214 family is not the inventory legacy214 set")
    if by_fam.get("legacy100") == set(ctx.legacy100) and by_fam.get("legacy214") == fam.get("legacy214"):
        row.ev("python replay legacy100 and legacy214 id sets equal the inventories")
    if tie_to_receipt(ctx, row, "targets.json"):
        groups = {}
        for s in ctx.targets.get("source_forms") or []:
            if str(s.get("group", "")).startswith("legacy:"):
                groups.setdefault(s["group"][7:], set()).add(s["id"])
        inv_groups = {}
        for r in ctx.rows2:
            if r.get("family") == "legacy214":
                inv_groups.setdefault(r.get("group"), set()).add(r["id"])
        inv_groups["legacy100"] = set(ctx.legacy100)
        bad = [g for g, s in inv_groups.items() if groups.get(g) != s]
        if bad:
            row.fail(f"targets.json legacy groups differ from the inventories: {bad}")
        else:
            row.ev(f"targets.json legacy groups {sorted(inv_groups)} equal the inventory sets")
    return row


def check_pu3(ctx: Ctx) -> Row:
    plan = ctx.plan["python_unchanged"]["checks"]
    row = Row("python_unchanged", 3, plan[3])
    if not tie_to_receipt(ctx, row, "python-controls.json"):
        return row
    pc = ctx.pyctl
    if pc.get("negative_cases_sha256") != sha_file(ctx.neg_path):
        row.fail("python-controls.json used another negative-cases.json")
    res = {r.get("id"): r for r in pc.get("results") or []}
    acc = {r.get("id"): r for r in (ctx.acc_pyctl or {}).get("results") or []}
    if set(res) != set(ctx.neg_by_id):
        row.fail(f"control results cover {len(res)} of {len(ctx.neg_by_id)} negative cases")
    t = Tally("python control")
    for cid, case in ctx.neg_by_id.items():
        r = res.get(cid)
        if r is None:
            t.miss(cid, "no control result")
            continue
        exp = str(case.get("python_control_expected", ""))
        if exp.startswith("HTTP 200"):
            if r.get("control_status") != 200:
                t.bad(cid, f"control status {r.get('control_status')}, expected 200")
            elif not r.get("payload_sha256"):
                t.bad(cid, "no payload hash recorded")
            elif cid in acc and acc[cid].get("payload_sha256") != r.get("payload_sha256"):
                t.bad(cid, "control payload differs from the accepted rust-output-050 control")
            elif cid not in acc:
                t.miss(cid, "no accepted control payload to compare")
            else:
                t.good(cid)
        elif exp.startswith("the same diagnostic"):
            if r.get("control_status") != case.get("http_status"):
                t.bad(cid, f"control status {r.get('control_status')}, expected {case.get('http_status')}")
            elif r.get("passed") is not True:
                t.bad(cid, "control row not passed")
            else:
                t.good(cid)
                t.attest(cid)
        else:
            t.miss(cid, "python_control_expected not understood")
    t.apply(row, list(ctx.neg_by_id))
    row.ev("the diagnostic body of 'the same diagnostic' controls is not recorded; status recomputed, body attested per case")
    return row


def check_pu4(ctx: Ctx) -> Row:
    plan = ctx.plan["python_unchanged"]["checks"]
    row = Row("python_unchanged", 4, plan[4])
    if not tie_to_receipt(ctx, row, "validate-cases.json"):
        return row
    cases = {c.get("id"): c for c in ctx.validate.get("cases") or []}
    acc = {c.get("id"): c for c in (ctx.acc_validate or {}).get("cases") or []}
    t = Tally("coverage case")
    for cid in ctx.coverage:
        c = cases.get(cid)
        a = acc.get(cid)
        if c is None:
            t.miss(cid, "no validation row")
            continue
        if a is None:
            t.miss(cid, "no accepted evidence row")
            continue
        if (c.get("json_status"), c.get("cnl_status"), c.get("assemble_status")) != (200, 200, 200):
            t.bad(cid, "status not 200")
        elif c.get("same_bundle") is not True:
            t.bad(cid, "CNL and JSON bundles differ")
        elif c.get("payload_sha256") != a.get("payload_sha256"):
            t.bad(cid, "payload_sha256 differs from the frozen evidence")
        elif c.get("python_files") != a.get("python_files") or not c.get("python_files"):
            t.bad(cid, "python file hashes differ from the frozen evidence")
        elif c.get("prompt_sha256") != a.get("prompt_sha256"):
            t.bad(cid, "prompt hash differs")
        else:
            t.good(cid)
    t.apply(row, ctx.coverage)
    row.ev("frozen evidence: rust-output-050 validate-cases.json (the accepted phase 1 run)")
    return row


# -------------------------------------------------- target_selection_checks

def public_expected(ctx: Ctx) -> dict:
    """(id, form) -> accepted no-target compile response hash from python-replay public rows."""
    out = {}
    for r in (ctx.replay or {}).get("public_rows") or []:
        for e in r.get("exchanges") or []:
            kind = str(e.get("exchange", "")).split("/")[-1]
            if kind.startswith("compile-"):
                out[(r.get("id"), kind[8:])] = e.get("expected_response_sha256")
    return out


def example_to_public(cid: str) -> str:
    m = re.fullmatch(r"example-(v\d+(?:-.+)?)", cid)
    return f"PUBLIC_{m.group(1)}" if m else cid


def check_ts0(ctx: Ctx) -> Row:
    row = Row("target_selection_checks", 0, ctx.plan["target_selection_checks"][0])
    if not tie_to_receipt(ctx, row, "targets.json"):
        return row
    if not tie_to_receipt(ctx, row, "python-replay.json"):
        return row
    tx = {r.get("id"): r for r in ctx.targets.get("target_examples") or []}
    pub = public_expected(ctx)
    sf = {(s.get("id"), s.get("form")): s for s in ctx.targets.get("source_forms") or []}
    t = Tally("target example")
    exs = (ctx.tex or {}).get("examples") or []
    for ex in exs:
        cid = ex["id"]
        r = tx.get(cid)
        if r is None:
            t.miss(cid, "no result")
            continue
        m = re.fullmatch(r"(.+)-(cnl|json)-(rust|python)", cid)
        if not m:
            t.miss(cid, "id not understood")
            continue
        src, form, target = m.groups()
        if target != ex.get("target"):
            t.bad(cid, "target differs from the example")
        elif r.get("status") != 200 or not r.get("reply_sha256"):
            t.bad(cid, f"status {r.get('status')}")
        elif target == "rust":
            other = tx.get(f"{src}-{'json' if form == 'cnl' else 'cnl'}-rust")
            if r.get("outcome") != "rust_bundle":
                t.bad(cid, "not a Rust bundle")
            elif other is None or other.get("reply_sha256") != r.get("reply_sha256"):
                t.bad(cid, "CNL and JSON forms give different bundle bytes")
            elif (src, form) in sf and sf[(src, form)].get("rust_reply_sha256") != r.get("reply_sha256"):
                t.bad(cid, "reply differs from the same request in source_forms")
            else:
                t.good(cid)
        else:
            want = pub.get((example_to_public(src), form))
            if want is None:
                t.miss(cid, "no accepted no-target reply to compare")
            elif r.get("reply_sha256") != want:
                t.bad(cid, "target python reply differs from the accepted reply without target")
            else:
                t.good(cid)
    t.apply(row, [e["id"] for e in exs])
    return row


def check_ts1(ctx: Ctx) -> Row:
    row = Row("target_selection_checks", 1, ctx.plan["target_selection_checks"][1])
    if not tie_to_receipt(ctx, row, "targets.json"):
        return row
    if ctx.targets.get("inputs", {}).get("som-rs/contracts/rust-output-v1/negative-cases.json") not in (None, sha_file(ctx.neg_path)):
        row.fail("targets.json used another negative-cases.json")
    neg = {r.get("id"): r for r in ctx.targets.get("negative_cases") or []}
    cli = {}
    for r in (ctx.cli or {}).get("rows") or []:
        cli.setdefault(r.get("input"), []).append(r)
    rt = [c for c in ctx.neg_by_id if c.startswith("RT")]
    t = Tally("RT case")
    for cid in rt:
        r = neg.get(cid)
        case = ctx.neg_by_id[cid]
        if r is None:
            t.miss(cid, "no result")
        elif r.get("status") != case.get("http_status"):
            t.bad(cid, f"status {r.get('status')}, expected {case.get('http_status')}")
        elif r.get("passed") is not True or r.get("problems"):
            t.bad(cid, "row records problems")
        else:
            for c in cli.get(cid, []):
                if c.get("code") != case.get("expected", {}).get("code"):
                    t.bad(cid, "CLI code differs from the expected diagnostic code")
            if cid not in t.failed:
                t.good(cid)
                t.attest(cid)
    t.apply(row, rt)
    row.ev("targets.json records the RT status only; code, span and pointer are attested by the per-case row "
           "(recomputed for the RT cases also run through the CLI)")
    return row


def check_ts2(ctx: Ctx) -> Row:
    row = Row("target_selection_checks", 2, ctx.plan["target_selection_checks"][2])
    if not tie_to_receipt(ctx, row, "targets.json") or not tie_to_receipt(ctx, row, "python-replay.json"):
        return row
    pub = public_expected(ctx)
    legacy = {}
    for r in ctx.replay.get("legacy_compile_rows") or []:
        for e in r.get("exchanges") or []:
            kind = str(e.get("exchange", "")).split("/")[-1]
            if kind.startswith("compile-"):
                legacy[(r.get("prompt_sha256"), kind[8:])] = e.get("expected_response_sha256")
    cli = {}
    for r in (ctx.cli or {}).get("rows") or []:
        cli[(r.get("input"), r.get("target"))] = r
    acc_sf = {(s.get("id"), s.get("form")): s for s in (ctx.acc_targets or {}).get("source_forms") or []}
    sf = ctx.targets.get("source_forms") or []
    by_id = {}
    for s in sf:
        by_id.setdefault(s.get("id"), []).append(s)
    fixtures = {r["id"] for r in ctx.rows2 if r.get("kind") == "cnl_fixture"}
    fixtures |= {x["id"] for x in (ctx.inv1 or {}).get("version_positive_fixtures", []) if x.get("rust") == "in_scope"}
    needed = set(ctx.acc1) | fixtures
    t = Tally("target python equals no target")
    for cid in sorted(needed):
        forms = by_id.get(cid)
        if not forms:
            t.miss(cid, "not in targets.json source_forms")
            continue
        verdict = None
        for s in forms:
            if s.get("python_status") != 200 or not s.get("python_reply_sha256"):
                verdict = ("bad", f"python status {s.get('python_status')}")
                break
            grp = str(s.get("group", ""))
            if grp.startswith("legacy:"):
                want = legacy.get((s.get("prompt_sha256"), s.get("form")))
            elif grp == "coverage_cases":
                none_r, py_r = cli.get((cid, None)), cli.get((cid, "python"))
                acc = acc_sf.get((cid, s.get("form")))
                if none_r and py_r and none_r.get("stdout_sha256") == py_r.get("stdout_sha256") and \
                        none_r.get("exit_code") == 0 and acc and acc.get("python_reply_sha256") == s.get("python_reply_sha256"):
                    want = s.get("python_reply_sha256")
                else:
                    want = None
            else:
                want = pub.get((example_to_public(cid), s.get("form")))
            if want is None:
                verdict = verdict or ("miss", "no accepted no-target reply to compare")
            elif want != s.get("python_reply_sha256"):
                verdict = ("bad", "target python reply differs from the accepted reply without target")
                break
        if verdict is None:
            t.good(cid)
        elif verdict[0] == "bad":
            t.bad(cid, verdict[1])
        else:
            t.miss(cid, verdict[1])
    t.apply(row, needed)
    row.ev(f"{len(set(ctx.acc1))} acceptance cases and {len(fixtures)} CNL positive fixtures; the no-target reference is the "
           "accepted reply in python-replay.json (public products and legacy prompts) and, for the 6 coverage cases, "
           "the CLI run without target plus the rust-output-050 record")
    return row


def check_ts3(ctx: Ctx) -> Row:
    row = Row("target_selection_checks", 3, ctx.plan["target_selection_checks"][3])
    if not tie_to_receipt(ctx, row, "cli.json"):
        return row
    rows = ctx.cli.get("rows") or []
    if not rows:
        row.fail("cli.json has no rows")
        return row
    by = {}
    for r in rows:
        by.setdefault(r.get("input"), {})[r.get("target")] = r
    for cid in ctx.coverage:
        d = by.get(cid, {})
        n, p, rs = d.get(None), d.get("python"), d.get("rust")
        if not (n and p and rs):
            row.unrun(f"CLI rows for {cid} incomplete")
            continue
        if n.get("stdout_sha256") != p.get("stdout_sha256") or n.get("exit_code") != 0 or p.get("exit_code") != 0:
            row.fail(f"CLI {cid}: --target python output differs from no target")
        if rs.get("exit_code") != 0 or rs.get("bundle_version") != "som.dsl-bundle.v2":
            row.fail(f"CLI {cid}: --target rust did not give a v2 bundle")
        if n.get("bundle_version") != "som.dsl-bundle.v1":
            row.fail(f"CLI {cid}: no target did not give a v1 bundle")
    refusals = [r for r in rows if isinstance(r.get("http_status"), int) and r["http_status"] >= 400]
    bad = [r.get("input") for r in refusals if r.get("exit_code") != 2 or r.get("stdout_sha256") != EMPTY_SHA256]
    if bad:
        row.fail(f"CLI refusals without exit status 2: {bad}")
    okrows = [r for r in rows if r.get("http_status") == 200]
    if any(r.get("exit_code") != 0 for r in okrows):
        row.fail("CLI success without exit status 0")
    unknown = [r for r in refusals if r.get("target") not in (None, "python", "rust")]
    if not unknown:
        row.unrun("no CLI row with an unknown target value")
    elif any(r.get("code") != "INVALID_SCHEMA" or r.get("http_status") != 400 for r in unknown):
        row.fail("an unknown --target value was not refused by the service with INVALID_SCHEMA")
    for r in rows:
        case = ctx.neg_by_id.get(r.get("input"))
        if case is None:
            continue
        exp = case.get("expected", {})
        if r.get("input") in ctx.refusal_ids:
            if r.get("code") != "UNSUPPORTED_TARGET" or r.get("exit_code") != 2:
                row.fail(f"CLI {r.get('input')} not refused with UNSUPPORTED_TARGET and exit 2")
        elif r.get("input", "").startswith("RS"):
            if r.get("exit_code") != 0 or r.get("bundle_version") != "som.dsl-bundle.v2":
                row.fail(f"CLI {r.get('input')} did not give a Rust bundle")
        elif r.get("code") != exp.get("code") or r.get("http_status") != case.get("http_status"):
            row.fail(f"CLI {r.get('input')} code {r.get('code')}, expected {exp.get('code')}")
    if not any(r.get("input") in ctx.refusal_ids for r in rows):
        row.unrun("no CLI row runs a phase 2 refusal")
    row.ev(f"{len(rows)} CLI rows: {len(refusals)} refusals all exit 2 with empty stdout; target python output equals no "
           f"target for {len(ctx.coverage)} coverage inputs; {len(unknown)} unknown target values reach the service "
           "unchanged and are refused there (forwarding itself attested by the rows)")
    return row


# ---------------------------------------------------------------- phase 2

def check_refusals(ctx: Ctx) -> Row:
    row = Row("phase2", "refusals", ctx.plan["phase2"]["refusals"])
    p2_tied(ctx, row)
    p2 = ctx.p2
    t = Tally("compile result")
    neg = ctx.neg_by_id
    beh_ref = (ctx.behavior or {}).get("phase2_refusals") or {}
    for cid in ctx.ids2:
        c = p2["cases"].get(cid)
        g = p2["gates"].get(cid)
        if c is None or g is None:
            t.miss(cid, "case row or gate file missing")
            continue
        rust = c[1].get("rust") or {}
        bc = g[1].get("bundle_checks") or {}
        statuses = bc.get("compile_status") or []
        ref = rust.get("refusal")
        if isinstance(ref, dict) and ref.get("code") in FORBIDDEN_CODES:
            t.bad(cid, f"{ref.get('code')} result")
            continue
        if cid in ctx.refusal_ids:
            exp = (neg.get(cid) or {}).get("expected") or {}
            ptrs = {exp.get("prompt_pointer"), "/prompt" + str(exp.get("prompt_pointer"))}
            if rust.get("compile_status") != 422 or not statuses or any(s != 422 for s in statuses):
                t.bad(cid, "not refused with 422 in every form")
            elif not isinstance(ref, dict) or ref.get("code") != "UNSUPPORTED_TARGET":
                t.bad(cid, "refusal code is not UNSUPPORTED_TARGET")
            elif ref.get("detail") != exp.get("cause"):
                t.bad(cid, "refusal detail is not the query_parameter_collision cause")
            elif ref.get("prompt_pointer") not in ptrs:
                t.bad(cid, "refusal pointer differs")
            elif (ctx.rows2_by_id[cid].get("phase2_expected") or {}).get("rule") != REFUSAL_RULE or \
                    (beh_ref.get(cid) or {}).get("rule") != REFUSAL_RULE:
                t.bad(cid, "rule is not query_parameter_collision")
            else:
                t.good(cid)
        else:
            if ref is not None or rust.get("compile_status") != 200 or not statuses or any(s != 200 for s in statuses):
                t.bad(cid, "refused or not compiled with 200 in every form")
            else:
                t.good(cid)
    t.apply(row, ctx.ids2)
    if tie_to_receipt(ctx, row, "targets.json"):
        tn = {r.get("id"): r for r in ctx.targets.get("negative_cases") or []}
        for cid, case in neg.items():
            r = tn.get(cid)
            if r is None:
                row.unrun(f"targets.json has no {cid}")
                continue
            if cid in ctx.refusal_ids:
                if r.get("status") != 422 or r.get("phase2") != REFUSAL_RULE:
                    row.fail(f"targets.json {cid} is not the query_parameter_collision refusal")
                g = p2["gates"].get(cid)
                pc = p2["cases"].get(cid)
                if g and r.get("reply_sha256") in ((g[1].get("bundle_checks") or {}).get("compile_sha256") or []):
                    row.ev(f"targets.json {cid} reply bytes equal the phase 2 compile of its form")
                elif g and pc:
                    # targets.json posts the frozen contract body; phase 2 posts its own serialization of the
                    # same typed prompt, so diagnostic spans and the reply bytes may differ. The prompt itself
                    # must be the same, and phase 2 must refuse it with the same code and rule.
                    try:
                        body = json.loads(case.get("body") or "")
                    except ValueError:
                        body = None
                    prompt = body.get("prompt") if isinstance(body, dict) else None
                    psha = sha_bytes(json.dumps(prompt, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False).encode()) if prompt is not None else None
                    pref = (pc[1].get("rust") or {}).get("refusal") or {}
                    if psha is None or psha != pc[1].get("prompt_sha256"):
                        row.fail(f"targets.json {cid} reply differs from phase 2 and its frozen prompt is not the "
                                 "phase 2 prompt")
                    elif pref.get("code") != "UNSUPPORTED_TARGET" or \
                            (pc[1].get("rust") or {}).get("compile_status") != 422:
                        row.fail(f"targets.json {cid} reply differs from phase 2, which did not refuse it with 422 "
                                 "UNSUPPORTED_TARGET")
                    else:
                        row.ev(f"targets.json {cid}: the frozen body carries the phase 2 prompt (sha256 {psha[:16]}); "
                               "both give 422 UNSUPPORTED_TARGET with rule query_parameter_collision; the reply bytes "
                               "differ with the request serialization")
                else:
                    row.unrun(f"phase 2 has no gate and case row for {cid}")
            elif cid.startswith("RS"):
                if r.get("status") != 200 or r.get("phase2") != "rust_bundle":
                    row.fail(f"targets.json {cid} is not a Rust bundle")
            elif cid.startswith("RP"):
                if r.get("status") != case.get("http_status"):
                    row.fail(f"targets.json {cid} does not keep its compiler-v1 status")
        bad = []
        for s in ctx.targets.get("source_forms") or []:
            if s.get("rust") == "in_scope" and (s.get("rust_status") != 200 or s.get("rust_outcome") != "rust_bundle"):
                bad.append(f"{s.get('id')}/{s.get('form')}")
            if s.get("rust") == "compiler_v1_diagnostic" and s.get("rust_status") != s.get("python_status"):
                bad.append(f"{s.get('id')}/{s.get('form')}")
            if s.get("rust") not in ("in_scope", "compiler_v1_diagnostic"):
                bad.append(f"{s.get('id')}/{s.get('form')}: rust {s.get('rust')}")
        if bad:
            row.fail(f"targets.json source forms not compiled for Rust: {first(bad)}")
        else:
            row.ev(f"targets.json: {len(ctx.targets.get('source_forms') or [])} source forms compile for Rust or keep "
                   "their compiler-v1 diagnostic; RS rows other than RS12 and RS13 give Rust bundles; RP rows keep "
                   "their status")
    return row


def product_dir(ctx: Ctx, cid: str):
    entry = ctx.p2["cases"].get(cid) or ctx.p2["gates"].get(cid)
    if entry is None:
        return None
    d = entry[2]
    p = d / "work" / norm(cid) / "product"
    if p.is_dir():
        return p
    gw = d / "gates-work"
    if gw.is_dir():
        # The phase 1 suite keeps each product under its lower-case id cut to 24 characters.
        names = {norm_lower(cid), norm_lower(cid)[:24]}
        found = [suite / n for suite in sorted(gw.glob("suite-*")) for n in sorted(names) if (suite / n).is_dir()]
        clash = [o for o in ctx.ids2 if o != cid and norm_lower(o)[:24] == norm_lower(cid)[:24]]
        if len(found) == 1 and not clash:
            return found[0]
    return None


def expected_family(case: dict) -> str | None:
    v = str(case.get("prompt_version", ""))
    m = re.fullmatch(r"som\.prompt\.v(\d+)", v)
    if not m:
        return None
    n = int(m.group(1))
    if n <= 4:
        return "resource"
    if n == 5:
        return "capability" if case.get("capabilities") else "resource"
    if n == 6:
        return "capability"
    return "flow"


class P2Products:
    """Per-case derivation of bundle_checks and product_gates for the phase 2 product cases."""

    def __init__(self, ctx: Ctx, python_rows: dict):
        self.ctx = ctx
        self.python_rows = python_rows
        self.layout = (ctx.layout or {}).get("phase2", {}).get("shapes", {})
        self.ref_hash = {}
        for sub in ("reference", "reference-phase2"):
            for f in ("Cargo.toml", "Cargo.lock"):
                p = ctx.contracts / sub / f
                if p.is_file():
                    self.ref_hash[(sub, f)] = sha_file(p)
        self.sf_forms = {}
        for s in (ctx.targets or {}).get("source_forms") or []:
            self.sf_forms.setdefault(s.get("id"), set()).add(s.get("form"))
        self.disk = {}
        self.t = {k: Tally(k) for k in (
            "forms", "schema", "determinism", "layout",
            "g0", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8")}
        self.shape = {}
        self.run()

    def run(self):
        for cid in self.ctx.product_ids:
            self.one(cid)

    def one(self, cid: str):
        ctx, t = self.ctx, self.t
        c = ctx.p2["cases"].get(cid)
        g = ctx.p2["gates"].get(cid)
        if c is None or g is None:
            for k in t:
                t[k].miss(cid, "case row or gate file missing")
            return
        case, gate = c[1], g[1]
        rust = case.get("rust") or {}
        bc = gate.get("bundle_checks") or {}
        pg = gate.get("product_gates") or {}
        forms = bc.get("forms") or []
        statuses = bc.get("compile_status") or []
        hashes = bc.get("compile_sha256") or []

        # bundle_checks[0]: CNL and JSON byte-identical; one form runs that form.
        sf_key = example_to_public(cid) if cid.startswith("PUBLIC_") else cid
        want_forms = self.sf_forms.get(sf_key) or self.sf_forms.get(
            cid.replace("PUBLIC_", "example-")) or set()
        if not forms or len(hashes) != 2 * len(forms) or len(statuses) != len(hashes):
            t["forms"].bad(cid, "forms or compile records incomplete")
        elif any(s != 200 for s in statuses) or len(set(hashes)) != 1:
            t["forms"].bad(cid, "forms give different bundle bytes or a non-200 status")
        elif want_forms and not want_forms <= set(forms):
            t["forms"].bad(cid, f"forms {sorted(forms)} miss a form the case has ({sorted(want_forms)})")
        elif rust.get("compile_sha256") not in hashes or rust.get("bundle_sha256") != bc.get("bundle_sha256") \
                or rust.get("files_sha256") != bc.get("files_sha256"):
            t["forms"].bad(cid, "case row and gate record different bundles")
        else:
            t["forms"].good(cid)

        # bundle_checks[1]: schema, limits, rust block ids (schema and prefix attested).
        nfiles, nblocks = bc.get("files"), bc.get("blocks")
        if not isinstance(nfiles, int) or not isinstance(nblocks, int):
            t["schema"].miss(cid, "file and block counts not recorded")
        elif nfiles > 12 or nblocks > 64 * nfiles or nblocks < 1:
            t["schema"].bad(cid, f"{nfiles} files, {nblocks} blocks")
        elif bc.get("passed") is not True or bc.get("problems"):
            t["schema"].bad(cid, "bundle row records problems")
        else:
            t["schema"].good(cid)
            t["schema"].attest(cid)

        # bundle_checks[2]: two compiles per form give the same bytes (assembles attested).
        pairs = [hashes[i:i + 2] for i in range(0, len(hashes), 2)]
        if not pairs or any(len(p) != 2 or p[0] != p[1] for p in pairs):
            t["determinism"].bad(cid, "two compiles give different bytes")
        elif bc.get("passed") is not True:
            t["determinism"].bad(cid, "bundle row not passed")
        else:
            t["determinism"].good(cid)
            t["determinism"].attest(cid)

        # Shape.
        shape = bc.get("shape")
        fam = expected_family(case)
        self.shape[cid] = shape
        spec = self.layout.get(shape)
        if spec is None or SHAPE_FAMILY.get(shape) != fam:
            t["layout"].bad(cid, f"shape {shape} is not the {fam} shape the prompt version selects")
            spec = None
        db = "mongodb" if str(shape).endswith("mongodb") else "postgresql"

        # bundle_checks[3]: file set and Cargo files, from the product on disk.
        pdir = product_dir(ctx, cid)
        disk = None
        if pdir is None:
            t["layout"].miss(cid, "assembled product not kept in the run")
        else:
            fsha, fh = files_sha(pdir)
            disk = fh
            self.disk[cid] = fh
            if fsha != bc.get("files_sha256"):
                t["layout"].bad(cid, "files on disk do not hash to the recorded files_sha256")
            elif spec is not None:
                migr = [f for f in fh if f.startswith("migrations/")]
                want = [f for f in spec.get("files", []) if not f.startswith("migrations/")]
                need_migr = any(f.startswith("migrations/") for f in spec.get("files", []))
                rest = sorted(f for f in fh if not f.startswith("migrations/"))
                sub = "reference" if shape == "resource" else "reference-phase2"
                if rest != sorted(want) or (len(migr) != (1 if need_migr else 0)) or \
                        any(not re.fullmatch(r"migrations/0001_[^/]+\.sql", m) for m in migr):
                    t["layout"].bad(cid, "file set differs from the product-layout shape")
                elif len(fh) != nfiles:
                    t["layout"].bad(cid, "file count differs from the bundle row")
                elif fh.get("Cargo.toml") != self.ref_hash.get((sub, "Cargo.toml")) or \
                        fh.get("Cargo.lock") != self.ref_hash.get((sub, "Cargo.lock")):
                    t["layout"].bad(cid, "Cargo.toml or Cargo.lock differ from the shape reference")
                else:
                    t["layout"].good(cid)

        # product_gates 0, 1, 2: recorded per case only as gate booleans.
        for k, key in (("g0", "rustfmt_check"), ("g1", "cargo_build"), ("g2", "cargo_clippy")):
            if key not in pg:
                t[k].miss(cid, f"{key} not recorded")
            elif not gate_passed(pg[key]):
                t[k].bad(cid, f"{key} failed")
            else:
                t[k].good(cid)
                t[k].attest(cid)

        # product_gates 3: cargo test, fresh database, missing setting.
        self.cargo_test(cid, shape, db, pg, gate, g[2])

        # product_gates 4: Cargo.lock unchanged.
        if "lock_unchanged" not in pg:
            t["g4"].miss(cid, "lock_unchanged not recorded")
        elif not gate_passed(pg["lock_unchanged"]):
            t["g4"].bad(cid, "Cargo.lock changed")
        elif disk is not None and spec is not None and disk.get("Cargo.lock") != self.ref_hash.get(
                ("reference" if shape == "resource" else "reference-phase2", "Cargo.lock")):
            t["g4"].bad(cid, "Cargo.lock on disk differs from the reference")
        else:
            t["g4"].good(cid)
            if disk is None:
                t["g4"].attest(cid)

        # product_gates 5 and 6: schema / index equivalence.
        if db == "postgresql":
            se = pg.get("schema_equivalence")
            if se is None:
                t["g5"].miss(cid, "schema_equivalence not recorded")
            elif not gate_passed(se):
                t["g5"].bad(cid, "schema not equivalent")
            elif isinstance(se, dict) and "sha256" in se:
                sh = se.get("sha256") or {}
                comp = se.get("compared") or []
                if not sh.get("rust") or sh.get("rust") != sh.get("python"):
                    t["g5"].bad(cid, "schema hashes differ")
                elif not comp or set(comp) & {"_som_rust_migrations", "alembic_version"}:
                    t["g5"].bad(cid, "compared tables empty or include migration bookkeeping")
                else:
                    t["g5"].good(cid)
            else:
                t["g5"].good(cid)
                t["g5"].attest(cid)
            if "index_equivalence" in pg:
                t["g6"].bad(cid, "PostgreSQL shape with a MongoDB index gate")
        else:
            ie = pg.get("index_equivalence")
            if ie is None:
                t["g6"].miss(cid, "index_equivalence not recorded")
            elif not gate_passed(ie):
                t["g6"].bad(cid, "indexes not equivalent")
            else:
                sh = (ie.get("sha256") or {}) if isinstance(ie, dict) else {}
                if not sh.get("rust") or sh.get("rust") != sh.get("python") or not ie.get("compared"):
                    t["g6"].bad(cid, "index hashes differ or nothing compared")
                else:
                    t["g6"].good(cid)
            if "schema_equivalence" in pg:
                t["g5"].bad(cid, "MongoDB shape with a PostgreSQL schema gate")

        # product_gates 7: phase 2 behavior suite, no assembled file changed.
        self.behavior_gate(cid, case, gate, c[0], disk)

        # product_gates 8: Python passes or is explained.
        self.python_gate(cid)

    def cargo_test(self, cid, shape, db, pg, gate, pdir_root):
        t = self.t["g3"]
        ct = pg.get("cargo_test")
        if ct is None:
            t.miss(cid, "cargo_test not recorded")
            return
        if not gate_passed(ct):
            t.bad(cid, "cargo test failed")
            return
        if shape == "resource":
            if "test_count" in pg and not gate_passed(pg["test_count"]):
                t.bad(cid, "test count gate failed")
            elif not isinstance(gate.get("tests_passed"), int) or gate["tests_passed"] < 1:
                t.bad(cid, "no passing test count recorded")
            else:
                t.good(cid)
                t.attest(cid)
            return
        ms = pg.get("missing_setting")
        need = ["TEST_DATABASE_URL"] if db == "postgresql" else ["MONGODB_URL", "SOM_TEST_DATABASE_NAME"]
        if not isinstance(ms, dict) or not gate_passed(ms):
            t.bad(cid, "missing-setting check not passed")
            return
        codes = ms.get("exit_codes") or {}
        if any(not isinstance(codes.get(k), int) or codes.get(k) == 0 for k in need):
            t.bad(cid, f"a missing {need} did not fail")
            return
        runs = ct.get("runs") if isinstance(ct, dict) else None
        wd = pdir_root / "gates-work" / norm(cid)
        files = sorted(wd.glob("cargo-test-*.json")) if wd.is_dir() else []
        if not files:
            t.miss(cid, "cargo test receipts not kept")
            return
        if runs is not None and runs != len(files):
            t.bad(cid, "cargo test run count differs from the kept receipts")
            return
        for f in files:
            obj, err = load_json(f)
            if not isinstance(obj, dict):
                t.bad(cid, "cargo test receipt unreadable")
                return
            cmd = [str(a) for a in obj.get("command") or []]
            if obj.get("returncode") != 0:
                t.bad(cid, "cargo test exit status not 0")
                return
            if not ("test" in cmd and "--locked" in cmd and "--offline" in cmd and "--test-threads=1" in cmd):
                t.bad(cid, "cargo test command lacks --locked, --offline or --test-threads=1")
                return
            res = TEST_RESULT.findall(str(obj.get("stdout", "")))
            if not res:
                t.bad(cid, "cargo test output has no test result")
                return
            if any(r[0] != "ok" or int(r[2]) or int(r[3]) or int(r[5]) for r in res):
                t.bad(cid, "cargo test failed, ignored or filtered tests")
                return
            if sum(int(r[1]) for r in res) < 1:
                t.bad(cid, "cargo test ran no test")
                return
        t.good(cid)
        t.attest(cid)  # the fresh database itself is not recorded

    def behavior_gate(self, cid, case, gate, case_path, disk):
        t = self.t["g7"]
        inv = self.ctx.rows2_by_id[cid]
        beh = gate.get("behavior") or {}
        if case.get("language") != "rust":
            t.bad(cid, "case row is not a Rust run")
        elif case.get("passed") is not True or case.get("problems"):
            t.bad(cid, "behavior checker failed")
        elif case.get("checker") != inv.get("checker") or beh.get("checker") not in (None, inv.get("checker")):
            t.bad(cid, "checker differs from the inventory")
        elif case.get("changed_files") or beh.get("changed_files"):
            t.bad(cid, "an assembled file changed")
        elif case.get("checker_passed") is False or beh.get("passed") is not True:
            t.bad(cid, "checker not passed")
        else:
            outp = case_path.with_name(case_path.stem + ".outcome.json")
            outcome, _ = load_json(outp)
            if not isinstance(outcome, dict):
                t.bad(cid, "outcome unreadable")
                return
            flow = outcome.get("flow")
            if inv.get("checker") == "flow" or case.get("checker") == "flow":
                if not isinstance(flow, dict) or flow.get("full_fault_matrix_passed") is not True or flow.get("pending"):
                    t.bad(cid, "flow fault matrix not complete")
                    return
            if disk is not None:
                seen = []

                def walk(o):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if k == "source_sha256" and isinstance(v, dict):
                                seen.append(v)
                            else:
                                walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)
                walk(outcome)
                for m in seen:
                    for f, h in m.items():
                        if f in disk and disk[f] != h:
                            t.bad(cid, "checked source differs from the assembled file")
                            return
            t.good(cid)
            t.attest(cid)

    def python_gate(self, cid):
        t = self.t["g8"]
        beh = self.ctx.behavior or {}
        r = self.python_rows.get(cid)
        if r is None:
            t.miss(cid, "no python result")
        elif r.get("passed") is True:
            t.good(cid)
        elif cid in (beh.get("python_defects") or {}):
            d = beh["python_defects"][cid]
            if d.get("oracle_mismatches") not in (None, r.get("failures")):
                t.bad(cid, "python failure count differs from behavior.json")
            elif d.get("checker") not in (None, r.get("checker")):
                t.bad(cid, "python checker differs from behavior.json")
            else:
                t.good(cid)
        else:
            t.bad(cid, "python failed and behavior.json does not explain it")

    def case_passed_all(self, cid) -> bool:
        return all(cid in tl.ok and cid not in tl.failed and cid not in tl.unrun_ for k, tl in self.t.items()
                   if not (k == "g5" and str(self.shape.get(cid, "")).endswith("mongodb"))
                   and not (k == "g6" and not str(self.shape.get(cid, "")).endswith("mongodb")))


def covering_receipt(ctx: Ctx, target: Path):
    """The receipt whose tree output is target or one of its parents inside the run, with the hash checked."""
    target = target.resolve()
    for p, rec, err in ctx.receipts:
        if not isinstance(rec, dict) or rec.get("exit_code") != 0:
            continue
        for out, want in (rec.get("outputs") or {}).items():
            op = (ctx.run / out).resolve()
            if not str(want).startswith("tree:"):
                continue
            if op == target or op in target.parents:
                if op.is_dir() and "tree:" + tree_sha(op) == want:
                    return p, out, True
                return p, out, False
    return None, None, None


def tie_any(ctx: Ctx, row: Row, path: Path) -> bool:
    """path is a receipt output itself, or lies in a receipt's tree output."""
    rel = str(path.relative_to(ctx.run))
    if ctx.receipt_for_output(rel)[1] is not None:
        return tie_to_receipt(ctx, row, rel)
    p, out, ok = covering_receipt(ctx, path)
    if p is None:
        row.unrun(f"{rel} is not the recorded output of any receipt")
        return False
    if not ok:
        row.fail(f"{rel} lies in {out}, whose tree differs from receipt {p.name}")
        return False
    row.ev(f"{rel} lies in {out}, whose tree hash equals receipt {p.name}")
    return True


def p2_tied(ctx: Ctx, row: Row):
    """Phase 2 Rust results count only from directories that a receipt seals."""
    if not ctx.p2["dirs"]:
        row.unrun("no phase 2 Rust directory in the run")
        return
    cache = ctx.__dict__.setdefault("_p2_tie", {})
    for d in ctx.p2["dirs"]:
        rel = str(d.relative_to(ctx.run)) if d != ctx.run else "."
        if d not in cache:
            cache[d] = covering_receipt(ctx, d)
        p, out, ok = cache[d]
        if p is not None:
            if not ok:
                row.fail(f"phase 2 directory {rel} differs from the tree hash in receipt {p.name} ({out})")
            else:
                row.ev(f"phase 2 directory {rel} tree hash equals receipt {p.name} output {out}")
            continue
        # No receipt seals the whole directory: each file the audit reads from it (summary.json, cases/,
        # gates/) must then be a sealed output itself. Products on disk are tied through files_sha256.
        for part in ("summary.json", "cases", "gates"):
            if not (d / part).exists():
                row.unrun(f"phase 2 directory {rel} has no {part}")
            else:
                tie_any(ctx, row, d / part)


def check_cargo_home(ctx: Ctx, row: Row):
    """CARGO_HOME holds only the vendored union of the two reference lock files."""
    env = ((ctx.header or {}).get("env_delta") or {}).get("CARGO_HOME")
    vend = (ctx.header or {}).get("vendor")
    if not env or not isinstance(vend, dict):
        row.unrun("header records no vendored CARGO_HOME")
        return
    home = Path(env)
    root = home.parent
    if not home.is_dir():
        row.fail(f"CARGO_HOME {home} missing")
        return
    allowed = {"config.toml", ".global-cache", ".package-cache", ".package-cache-mutate", "registry/CACHEDIR.TAG"}
    extra = []
    for dirpath, _d, names in os.walk(home):
        for n in names:
            rel = os.path.relpath(Path(dirpath) / n, home).replace(os.sep, "/")
            if rel not in allowed:
                extra.append(rel)
    if extra:
        row.fail(f"CARGO_HOME holds files other than its config: {first(extra)}")
    cfg = home / "config.toml"
    if not cfg.is_file() or sha_file(cfg) != vend.get("cargo_config_sha256"):
        row.fail("CARGO_HOME config.toml differs from the header cargo_config_sha256")
        return
    text = cfg.read_text()
    vdir = root / "vendor"
    if 'replace-with = "vendored-sources"' not in text or f'directory = "{vdir}"' not in text or \
            "offline = true" not in text:
        row.fail("CARGO_HOME config does not replace crates.io with the vendor directory offline")
    vj = root / "vendor.json"
    if not vj.is_file() or sha_file(vj) != vend.get("vendor_json_sha256"):
        row.fail("vendor.json differs from the header vendor_json_sha256")
        return
    vobj, _ = load_json(vj)
    if (vobj or {}).get("vendor_tree_sha256") != vend.get("vendor_tree_sha256"):
        row.fail("vendor.json vendor_tree_sha256 differs from the header")
    union = set()
    for sub in ("reference", "reference-phase2"):
        lock = ctx.contracts / sub / "Cargo.lock"
        if not lock.is_file():
            row.unrun(f"{sub}/Cargo.lock missing")
            return
        for blk in lock.read_text().split("[[package]]")[1:]:
            n = re.search(r'^name = "(.+)"', blk, re.M)
            v = re.search(r'^version = "(.+)"', blk, re.M)
            if n and v and re.search(r"^source = ", blk, re.M):
                union.add(f"{n.group(1)}-{v.group(1)}")
    names = set(os.listdir(vdir)) if vdir.is_dir() else set()
    pk = {f"{p.get('name')}-{p.get('version')}": p for p in (vobj or {}).get("packages") or []}
    if names != union or set(pk) != union:
        row.fail(f"vendor directory ({len(names)}) is not the union of the reference lock files ({len(union)}): "
                 f"extra {first(names - union, 5)}, missing {first(union - names, 5)}")
    else:
        row.ev(f"CARGO_HOME {home} holds only its offline config; the vendor directory is exactly the {len(union)} "
               "registry packages of reference/Cargo.lock and reference-phase2/Cargo.lock")
    same = sum(1 for k, p in pk.items() if (vdir / k).is_dir() and tree_sha(vdir / k) == p.get("tree_sha256"))
    row.ev(f"{same}/{len(pk)} vendored package trees hash to their vendor.json tree_sha256 with the receipt tree "
           "formula; the others and the vendor_tree_sha256 use a formula the outputs do not define (not recomputed)")
    for p, rec, err in ctx.receipts:
        if isinstance(rec, dict) and (rec.get("env_delta") or {}).get("CARGO_HOME") not in (None, env):
            row.fail(f"receipt {p.name} ran with another CARGO_HOME")


def check_bundle(ctx: Ctx, pp: P2Products) -> list[Row]:
    plan = ctx.plan["phase2"]["bundle_checks"]
    rows = []
    for i, key in enumerate(("forms", "schema", "determinism", "layout")):
        row = Row("phase2.bundle_checks", i, plan[i])
        p2_tied(ctx, row)
        pp.t[key].apply(row, ctx.product_ids)
        if key == "schema":
            row.ev("file count and block count are recomputed limits; schema validity, per-file block limit and the "
                   "rust block prefix are recorded only as the per-case bundle row")
        if key == "determinism":
            row.ev("two compiles per form recomputed from the recorded reply hashes; two assembles are recorded only "
                   "as the per-case bundle row")
        if key == "layout":
            row.ev("file set, migration name and Cargo files recomputed from the kept product files; shape family "
                   "derived from prompt_version and capabilities, database from the gate shape")
        rows.append(row)
    return rows


def check_gates(ctx: Ctx, pp: P2Products) -> list[Row]:
    plan = ctx.plan["phase2"]["product_gates"]
    rows = []
    # A case whose shape is not known yet is expected by both database gates (and is unrun in both).
    known = {c for c in ctx.product_ids if pp.shape.get(c) in SHAPE_FAMILY}
    pg_ids = [c for c in ctx.product_ids if c not in known or not str(pp.shape[c]).endswith("mongodb")]
    mg_ids = [c for c in ctx.product_ids if c not in known or str(pp.shape[c]).endswith("mongodb")]
    for i in range(9):
        row = Row("phase2.product_gates", i, plan[i])
        p2_tied(ctx, row)
        expected = ctx.product_ids
        if i == 5:
            expected = pg_ids
        if i == 6:
            expected = mg_ids
        pp.t[f"g{i}"].apply(row, expected)
        if i == 1:
            check_cargo_home(ctx, row)
        if i == 3:
            row.ev("PostgreSQL and MongoDB shapes: cargo test receipts recomputed (exit 0, flags, no failed/ignored/filtered "
                   "test) and missing settings refused; the fresh database and the resource-shape runs are attested per case")
        if i == 8:
            row.ev("python results from behavior.json python_run, explained failures from behavior.json python_defects; "
                   "RS12 and RS13 are refusals (no product case)")
        rows.append(row)
    return rows


def check_phase1_unchanged(ctx: Ctx) -> Row:
    row = Row("phase2", "phase1_unchanged", ctx.plan["phase2"]["phase1_unchanged"])
    acc1 = list(dict.fromkeys(ctx.acc1))
    if len(acc1) != 115:
        row.fail(f"phase 1 acceptance set has {len(acc1)} cases")
    if not ctx.phase1_unchanged:
        row.unrun("no phase1-unchanged output in the run")
        return row
    p, obj = ctx.phase1_unchanged[0]
    if len(ctx.phase1_unchanged) > 1:
        row.fail("more than one phase1-unchanged output")
    tie_any(ctx, row, p)
    rec = obj.get("record") or {}
    accp = ctx.phase1_run / "rust-products.json"
    if not accp.is_file() or rec.get("sha256") != sha_file(accp):
        row.fail("the record is not the accepted rust-output-050 rust-products.json")
    acc_cases = {c.get("id"): c for c in (ctx.acc_products or {}).get("cases") or []}
    cases = {c.get("id"): c for c in obj.get("cases") or []}
    allowed = {f"/assets/{k}/sha256" for k in RUST_ASSET_KEYS} | PHASE1_ALLOWED_HASH_POINTERS
    t = Tally("bundle differs only in Rust descriptors")
    for cid in acc1:
        c = cases.get(cid)
        a = acc_cases.get(cid)
        if c is None:
            t.miss(cid, "no comparison row")
            continue
        if a is None:
            t.miss(cid, "no accepted record row")
            continue
        forms = c.get("forms") or []
        if not forms:
            t.bad(cid, "no form compared")
            continue
        bad = None
        for f in forms:
            blk = c.get(f) or {}
            diffs = blk.get("differences")
            pay = blk.get("payload_sha256") or []
            if not isinstance(diffs, list) or len(pay) != 2:
                bad = "comparison incomplete"
            elif set(diffs) - allowed:
                bad = f"differs outside the Rust descriptors: {sorted(set(diffs) - allowed)}"
            elif pay[0] != a.get("bundle_payload_sha256"):
                bad = "old payload is not the accepted phase 1 bundle"
            elif (pay[0] == pay[1]) == bool(diffs):
                bad = "payload hash change does not match the recorded differences"
            if bad:
                break
        if bad:
            t.bad(cid, bad)
        else:
            t.good(cid)
    t.apply(row, acc1)
    # Assembled files byte-identical.
    files = Tally("assembled files byte-identical")
    pdirs_ok = False
    if (ctx.run / "products").is_dir():
        rp, out, pdirs_ok = covering_receipt(ctx, ctx.run / "products")
        if pdirs_ok is False:
            row.fail(f"products/ differs from the tree hash in receipt {rp.name}")
        elif pdirs_ok:
            row.ev(f"products/ tree hash equals receipt {rp.name} output {out}")
    run_cases = {}
    for pth, o in ctx.products_checks:
        if not any(c.get("id") in acc_cases for c in o.get("cases") or []):
            continue
        if not tie_any(ctx, row, pth):
            continue
        for c in o.get("cases") or []:
            if c.get("id") in acc_cases:
                run_cases[c.get("id")] = c
    parity = {}
    if ctx.parity is not None and covering_receipt(ctx, ctx.run / "behavior" / "parity" / "report-parity.json")[2]:
        parity = {r.get("id"): r for r in ctx.parity.get("rows") or []}
        row.ev("behavior/parity is sealed by its receipt; its rust_files_sha256 is used where no product record exists")
    for cid in acc1:
        a = acc_cases.get(cid)
        r = run_cases.get(cid)
        pdir = ctx.run / "products" / norm_lower(cid)
        if a is None:
            files.miss(cid, "no accepted record row")
        elif pdirs_ok and pdir.is_dir():
            if files_sha(pdir)[0] != a.get("files_sha256"):
                files.bad(cid, "kept product files differ from the accepted files_sha256")
            elif r is not None and r.get("files_sha256") != a.get("files_sha256"):
                files.bad(cid, "recorded files_sha256 differs from the accepted one")
            else:
                files.good(cid)
        elif r is not None and r.get("files_sha256"):
            if r.get("files_sha256") != a.get("files_sha256"):
                files.bad(cid, "recorded files_sha256 differs from the accepted one")
            else:
                files.good(cid)
                files.attest(cid)
        elif parity.get(cid, {}).get("rust_files_sha256"):
            if parity[cid]["rust_files_sha256"] != a.get("files_sha256"):
                files.bad(cid, "parity product files differ from the accepted files_sha256")
            else:
                files.good(cid)
                files.attest(cid)
        else:
            files.miss(cid, "no files hash for the candidate's assembled files")
    files.apply(row, acc1)
    return row


BASES = {"example-v1": "example-v1", "example_v1": "example-v1", "PUBLIC_v6": "PUBLIC_v6"}
RECIPE_ID = re.compile(r"(A\d+)(?:_(.+))?")


def recipe_key(row: dict):
    """(base, recipe, problem) of one assemble refusal row.

    The recipe is the id without its base suffix ("A1_PUBLIC_v6" -> A1 on PUBLIC_v6). A "base" field
    decides the base; a row without one (the older format) takes the base from the id suffix, and a
    plain recipe id is the example-v1 base. A base field that contradicts the id suffix is a problem.
    """
    rid = str(row.get("id") or "")
    m = RECIPE_ID.fullmatch(rid)
    if not m:
        return None, None, f"unknown recipe id {rid!r}"
    recipe, suffix = m.group(1), m.group(2)
    id_base = None
    if suffix is not None:
        id_base = BASES.get(suffix)
        if id_base is None:
            return None, recipe, f"{rid}: unknown base suffix {suffix!r}"
    if "base" in row:
        b = BASES.get(str(row.get("base")))
        if b is None:
            return None, recipe, f"{rid}: unknown base {row.get('base')!r}"
        if id_base is not None and id_base != b:
            return None, recipe, f"{rid}: base field {b} contradicts the id suffix {id_base}"
        return b, recipe, None
    return (id_base or "example-v1"), recipe, None


def refusal_rows(ctx: Ctx):
    out = {}
    unknown = []
    srcs = []
    files = [(p, o) for p, o in ctx.products_checks if o.get("assemble_refusals")]
    # A phase 2 product check runs recipes on the PUBLIC_v6 base too (a base field or a base id suffix);
    # when one exists, a phase 1 format file (example-v1 only, phase 1 A6 and A8) is not the phase 2 record.
    def is_phase2(o):
        return any(isinstance(r, dict) and ("base" in r or recipe_key(r)[0] == "PUBLIC_v6")
                   for r in o["assemble_refusals"])
    phase2 = [(p, o) for p, o in files if is_phase2(o)]
    for p, o in (phase2 or files):
        rows = o.get("assemble_refusals") or []
        if rows:
            srcs.append(p)
        for r in rows:
            if not isinstance(r, dict):
                unknown.append(f"non-object row in {p.name}")
                continue
            b, recipe, problem = recipe_key(r)
            if problem:
                unknown.append(problem)
                continue
            out.setdefault((b, recipe), []).append((p, r))
    return out, unknown, srcs


def new_format(r: dict) -> bool:
    """A row that names its base also records the pointer (and, for A8, both file hashes)."""
    return "base" in r


def pointer_of(r: dict):
    for k in ("prompt_pointer", "pointer"):
        if k in r:
            return r[k]
    return None


def check_assemble(ctx: Ctx) -> list[Row]:
    ar = ctx.plan["phase2"]["assemble_refusals"]
    recipes = {r["id"]: r for r in ctx.plan["assemble_refusals"]["recipes"]}
    rows_out = []
    got, unknown, srcs = refusal_rows(ctx)

    # rule
    row = Row("phase2.assemble_refusals", "rule", ar["rule"])
    need = [("example-v1", i) for i in ("A1", "A2", "A3", "A4", "A5", "A7", "A9")] + \
           [("PUBLIC_v6", i) for i in ("A1", "A2", "A4", "A5", "A7", "A9")]
    if not srcs:
        row.unrun("no product-checks output with assemble_refusals in the run")
    else:
        for p in srcs:
            tie_any(ctx, row, p)
    if unknown:
        row.fail(f"assemble refusal rows with an unknown base or id: {first(unknown)}")
    att = cmp = 0
    for base, rid in need:
        rec = recipes[rid]
        rs = got.get((base, rid))
        if not rs:
            if srcs:
                row.unrun(f"{rid} on {base} not recorded")
            continue
        for p, r in rs:
            if r.get("status") != rec["http_status"] or r.get("code") != rec["code"]:
                row.fail(f"{rid} on {base}: {r.get('status')} {r.get('code')}, expected {rec['http_status']} {rec['code']}")
            ptr = pointer_of(r)
            if ptr is None:
                # The run's own passed flag is not a verdict: without the pointer the check is unrun.
                if r.get("passed") is not True:
                    row.fail(f"{rid} on {base}: row not passed")
                att += 1
            elif ptr != rec["prompt_pointer"]:
                row.fail(f"{rid} on {base}: pointer {ptr}, expected {rec['prompt_pointer']}")
            else:
                cmp += 1
    if srcs:
        row.ev(f"{len(need)} recipe runs required (A1-A5, A7, A9 on example-v1; A1, A2, A4, A5, A7, A9 on PUBLIC_v6); "
               f"ids read as <recipe> or <recipe>_<base>, a base field taking precedence; "
               f"status and code recomputed against the plan; {cmp} pointers compared with the plan pointer")
        if att:
            row.unrun(f"{att} recipe rows record no prompt_pointer (older format), so their pointers cannot be compared")
    rows_out.append(row)

    # A6 (phase 2 replacement, on the example-v1 base)
    row = Row("phase2.assemble_refusals", "A6", ar["A6"])
    rs = got.get(("example-v1", "A6")) or []
    if not rs:
        row.unrun("A6 on example-v1 not recorded")
    for p in sorted({p for p, _ in rs}):
        tie_any(ctx, row, p)
    for p, r in rs:
        if r.get("status") != 422 or r.get("code") != "UNSUPPORTED_TARGET":
            row.fail(f"A6: {r.get('status')} {r.get('code')}, expected 422 UNSUPPORTED_TARGET")
            continue
        ptr = pointer_of(r)
        rs12 = ctx.p2["cases"].get("RS12_CNL_V3_FILTER_LIMIT")
        ref_ptr = (((rs12 or (None, {}))[1].get("rust") or {}).get("refusal") or {}).get("prompt_pointer")
        if ptr is None:
            if r.get("passed") is not True:
                row.fail("A6 row not passed")
            row.ev("A6: 422 UNSUPPORTED_TARGET recomputed")
            row.unrun("A6: the row records no prompt_pointer, so the RS12 pointer cannot be compared")
        elif not str(ptr).startswith("/bundle/prompt"):
            row.fail(f"A6 pointer {ptr} not under /bundle/prompt")
        elif not ref_ptr:
            row.unrun("RS12 refusal pointer not available to compare the A6 pointer")
        elif ptr != "/bundle" + ref_ptr:
            row.fail(f"A6 pointer {ptr} is not the RS12 typed prompt pointer under /bundle")
        elif not tie_any(ctx, row, rs12[0]):
            row.unrun("the RS12 case record that gives the reference pointer is not sealed by a receipt")
        else:
            row.ev(f"A6: 422 UNSUPPORTED_TARGET at {ptr} (the RS12 pointer under /bundle)")
    rows_out.append(row)

    # A8
    row = Row("phase2.assemble_refusals", "A8", ar["A8"])
    rs = got.get(("example-v1", "A8")) or []
    if not rs:
        row.unrun("A8 on example-v1 not recorded")
    for p in sorted({p for p, _ in rs}):
        tie_any(ctx, row, p)
    for p, r in rs:
        if r.get("status") != 200 or r.get("code") not in (None, ""):
            row.fail(f"A8: {r.get('status')} {r.get('code')}, expected 200")
            continue
        hashes = {k: v for k, v in r.items() if "files_sha256" in k and isinstance(v, str)}
        bad = [k for k, v in hashes.items() if not re.fullmatch(r"[0-9a-f]{64}", v)]
        if bad:
            row.fail(f"A8: {bad} not a sha256 hex digest")
        elif len(hashes) >= 2:
            if len(set(hashes.values())) != 1:
                row.fail(f"A8 files differ from the compile and assemble of the renamed prompt: {sorted(hashes)}")
            else:
                row.ev(f"A8: 200 and {sorted(hashes)} equal ({next(iter(hashes.values()))})")
        else:
            if r.get("passed") is not True:
                row.fail("A8 row not passed")
            row.ev("A8: 200 recomputed")
            row.unrun(f"A8: the row records {sorted(hashes) or 'no'} file hashes; files_sha256 and "
                      "renamed_files_sha256 are both needed to compare the files")
    rows_out.append(row)
    return rows_out


def check_legacy(ctx: Ctx, pp: P2Products) -> Row:
    row = Row("phase2", "legacy_gate", ctx.plan["phase2"]["legacy_gate"])
    acc_cases = {c.get("id"): c for c in (ctx.acc_products or {}).get("cases") or []}
    run_cases = {}
    for p, o in ctx.products_checks:
        for c in o.get("cases") or []:
            run_cases[c.get("id")] = c
    for p, o in ctx.products_checks:
        tie_any(ctx, row, p)
    if ctx.parity is not None:
        tie_any(ctx, row, ctx.run / "behavior" / "parity" / "report-parity.json")
    parity = {r.get("id"): r for r in (ctx.parity or {}).get("rows") or []}
    t1 = Tally("phase 1 legacy job (phase 1 gates and parity)")
    for cid in ctx.legacy100:
        c = run_cases.get(cid)
        if c is None:
            t1.miss(cid, "no phase 1 product row in the run")
            continue
        gates = (c.get("product_gates") or {}).get("gates") or {}
        bad = [k for k, v in gates.items() if not gate_passed(v)]
        need = {"rustfmt_check", "cargo_build", "cargo_clippy", "cargo_test", "lock_unchanged",
                "schema_equivalence", "divergences_d1_d5"}
        if need - set(gates):
            t1.miss(cid, f"gates not recorded: {sorted(need - set(gates))}")
            continue
        if bad or (c.get("bundle_checks") or {}).get("passed") is not True:
            t1.bad(cid, f"phase 1 gate failed: {bad}")
            continue
        a = acc_cases.get(cid)
        if a is not None and a.get("files_sha256") != c.get("files_sha256"):
            t1.bad(cid, "files differ from the accepted phase 1 product")
            continue
        pr = parity.get(cid)
        if pr is None:
            t1.miss(cid, "no parity row")
            continue
        if pr.get("passed") is not True or pr.get("differences") or pr.get("strict_failures") or \
                pr.get("rust_failures") or pr.get("python_failures"):
            t1.bad(cid, "parity failed")
            continue
        if pr.get("rust_files_sha256") != c.get("files_sha256"):
            t1.bad(cid, "parity ran another Rust product")
            continue
        rawd = ctx.run / "behavior" / "parity" / "raw"
        okraw = True
        for pre, key in (("py", "python_raw_sha256"), ("rs", "rust_raw_sha256")):
            rp = rawd / f"{pre}_{norm_lower(cid)}.json"
            if not rp.is_file() or sha_file(rp) != pr.get(key):
                okraw = False
        if not okraw:
            t1.bad(cid, "parity raw record missing or hash differs")
            continue
        t1.good(cid)
        t1.attest(cid)
    t1.apply(row, ctx.legacy100)
    t2 = Tally("phase 2 legacy job (all nine product gates and the bundle checks)")
    for cid in ctx.legacy444:
        if cid in ctx.refusal_ids:
            t2.bad(cid, "refused")
        elif pp.case_passed_all(cid):
            t2.good(cid)
        elif any(cid in tl.failed for tl in pp.t.values()):
            t2.bad(cid, "a gate or bundle check failed")
        else:
            t2.miss(cid, "a gate or bundle check is unrun")
    t2.apply(row, ctx.legacy444)
    if tie_to_receipt(ctx, row, "python-replay.json"):
        lr = {r.get("id"): r for r in ctx.replay.get("legacy_rows") or []}
        bad = [c for c in set(ctx.legacy100) | set(ctx.legacy444) if c not in lr or not exchange_ok(lr[c].get("exchange") or {})]
        if bad:
            row.fail(f"legacy jobs whose Python result is not the accepted one: {first(bad)}")
        else:
            row.ev("544 legacy jobs keep the accepted Python assemble reply")
    return row


# -------------------------------------------------------------- cross-check

def cross_check(ctx: Ctx, rows: list[Row]) -> list[str]:
    notes = []
    summ = ctx.summary
    if not summ:
        return ["run summary.json missing: no run verdicts to cross-check"]
    theirs = {}
    for pc in summ.get("plan_checks") or []:
        theirs[str(pc.get("check"))] = pc.get("state")
    for r in rows:
        s = theirs.get(r.check)
        if s is None:
            continue
        if s != r.state:
            notes.append(f"{r.section}[{r.index}]: run says {s}, audit says {r.state}")
    if summ.get("status") == "passed" and any(r.state != "passed" for r in rows):
        notes.append("run status is passed but the audit has failed or unrun rows")
    return notes


# --------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, type=Path, help="acceptance run directory")
    ap.add_argument("--out", required=True, type=Path, help="new report file (never overwritten)")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4],
                    help="repository root (default: from this file's location)")
    ap.add_argument("--phase1-run", type=Path, default=None,
                    help="accepted phase 1 run (default: .local/rust-compiler-goal/acceptance/rust-output-050/run-001)")
    args = ap.parse_args(argv)
    if args.out.exists():
        print(f"refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    run = args.run.resolve()
    if not run.is_dir():
        print(f"run directory {run} missing", file=sys.stderr)
        return 2
    repo = args.repo.resolve()
    phase1_run = (args.phase1_run or repo / ".local/rust-compiler-goal/acceptance/rust-output-050/run-001").resolve()
    try:
        out_fh = open(args.out, "x", encoding="utf-8")
    except FileExistsError:
        print(f"refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    ctx = Ctx(run, repo, phase1_run)
    rows: list[Row] = []
    if ctx.load_errors or not ctx.plan:
        r = Row("run", "contracts", "The frozen contracts load.")
        for e in ctx.load_errors:
            r.fail(e)
        rows.append(r)
    if ctx.plan:
        rows.append(check_one_candidate(ctx))
        rows.append(check_default_unchanged(ctx))
        rows.append(check_tools_unchanged(ctx))
        rows.append(check_receipts(ctx))
        rows.append(check_case_receipts(ctx)[0])
        rows.append(check_gate_hashes(ctx))
        py_row, py_rows = check_python_run(ctx)
        rows.append(py_row)
        rows += [check_pu0(ctx), check_pu1(ctx), check_pu2(ctx), check_pu3(ctx), check_pu4(ctx)]
        rows += [check_ts0(ctx), check_ts1(ctx), check_ts2(ctx), check_ts3(ctx)]
        rows.append(check_refusals(ctx))
        rows.append(check_phase1_unchanged(ctx))
        pp = P2Products(ctx, py_rows)
        rows += check_bundle(ctx, pp)
        rows += check_assemble(ctx)
        rows += check_gates(ctx, pp)
        rows.append(check_legacy(ctx, pp))

    out_rows = [r.as_dict() for r in rows]
    totals = {"failed": sum(r["state"] == "failed" for r in out_rows),
              "unrun": sum(r["state"] == "unrun" for r in out_rows)}
    report = {
        "version": VERSION,
        "run": str(run),
        "plan_sha256": sha_file(ctx.plan_path) if ctx.plan_path.is_file() else None,
        "audit_sha256": sha_file(Path(__file__).resolve()),
        "phase1_record": str(phase1_run),
        "rows": out_rows,
        "passed": sum(r["state"] == "passed" for r in out_rows),
        "totals": totals,
        "cross_check": cross_check(ctx, rows) if ctx.plan else [],
    }
    with out_fh:
        json.dump(report, out_fh, indent=1, sort_keys=False)
        out_fh.write("\n")
    print(json.dumps({"passed": report["passed"], **totals}))
    return 0 if totals["failed"] == 0 and totals["unrun"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
