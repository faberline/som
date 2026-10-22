#!/usr/bin/env python3
"""Independent audit of a typescript-output-v1 phase 1 acceptance run (issue #65).

Written from the acceptance plans and the raw outputs only. It does not read,
import or run any acceptance tool, and it never uses a tool's summary, judgment,
passed, status, passed_count or gate_statuses field as a verdict.

Sources it reads (every read goes through Reader, which records its sha256):

* Plans: som-rs/contracts/typescript-output-v1/acceptance-plan.json and
  som-rs/contracts/rust-output-v1/acceptance-plan.json (sha256 pinned below).
* Contract data: som-rs/contracts/typescript-output-v1/ (case-inventory,
  negative-cases, target-examples, scope, api.schema, bundle.schema, catalog,
  product-layout, behavior-normalization, toolchain, reference/),
  som-rs/contracts/rust-output-v1/ (negative-cases, case-inventory),
  som-rs/contracts/compiler-v1/examples/.
* Test data (JSON, CNL and prompt files only, never .py): som-rs/tests/compiler-v1/
  cnl-positive-fixtures.json, som-rs/tests/rust-output-v1/cases/.
* Evidence: som-rs/evidence/ receipts (typescript freeze and amendment 001, rust
  amendments 003 to 005), archived copies, typescript-output-v1 and
  rust-output-v1 evidence files.
* Raw outputs: the run directory (header, receipts, logs, reports, response
  files, products, raw parity transcripts), the candidate directory
  (som, build.json, runtime.json, source.json, source.tar, assets/), the
  baseline directory (som, assets/), rust-output-055 run-003,
  public-products-003b and legacy544 run-005.

Never read as a verdict: summary.json, ts-products/summary.json, and the
judgment / summary / passed / status / passed_count / gate_statuses fields.
The scope oracle is reimplemented from scope.json; ts_profile.py is not used.

Acceptance tool files (som-rs/scripts/*_output_acceptance, som-rs/som-targets/qa,
.py files) are never parsed or run: Reader.hash_only hashes their bytes for an
identity check against the run header's tools / tools_before / tools_after maps.

Statuses: passed (re-derived from raw values), attested (the only raw record
is a per-case tool boolean or a tool claim the audit cannot recompute),
post_acceptance (an issue-gate step that by design happens only after
acceptance: the 8765 rehearsal, rollback and the user's approved switch),
unrun (cannot be checked from the raw outputs), failed.

Usage: audit065.py --run RUN_DIR --out NEW_REPORT.json [--repo REPO]
Exit 0 only when no row is failed or unrun.
"""

import argparse
import base64
import collections
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import traceback

VERSION = "som.typescript-output-audit-065.v2"
AUDIT_SELF_REL = "som-rs/scripts/typescript_output_acceptance/audit/audit065.py"
AUDIT_V1_SHA = "cecccad8d885c4290d272a6ff4afa7a9cbe3c3cdba45906b68df42c1c41207e7"  # committed v1 (audit-001)
TS_PLAN_REL = "som-rs/contracts/typescript-output-v1/acceptance-plan.json"
TS_PLAN_SHA = "318d4ab8f4dc35e5d490f89fe8edf4d4c3eb021068b2c429025f0641927e58cf"
RS_PLAN_REL = "som-rs/contracts/rust-output-v1/acceptance-plan.json"
RS_PLAN_SHA = "a3f2392fef920aa6e4fe42c5aaa923f03369fe2293a92e5ee3be3f9e2ebe83fe"
BASELINE_BIN_SHA = "52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2"
TS_TC_SHA = "a1ed9978d7ddd0123e22a1e388f1a6e889ca63e43752563cd744733b84c103b4"
RS_TC_SHA = "8c25078c84fdec3dea1b4cb15df499d682ccb9b7286d957598f2076e3a685d51"
CAND_REL = ".local/typescript-output/candidate-001"
BASE_REL = ".local/rust-compiler-goal/rust-output-candidate-006"
RUN003_REL = ".local/rust-compiler-goal/acceptance/rust-output-055/run-003"
PP_REL = ".local/rust-compiler-goal/acceptance/public-products-003b"
L544_REL = ".local/rust-compiler-goal/acceptance/legacy544/run-005"
TS_CT = "som-rs/contracts/typescript-output-v1"
RS_CT = "som-rs/contracts/rust-output-v1"
CV1 = "som-rs/contracts/compiler-v1"
EV = "som-rs/evidence"
TS_FREEZE = EV + "/typescript-output-contract-freeze-2026-10-21.json"
TS_AM1 = EV + "/typescript-output-contract-amendment-001.json"
RS_AM3 = EV + "/rust-output-contract-amendment-003.json"
RS_AM4 = EV + "/rust-output-contract-amendment-004.json"
RS_AM5 = EV + "/rust-output-contract-amendment-005.json"
FORBIDDEN = ("som-rs/scripts/", "som-rs/som-targets/qa/", "som-rs/behavior/")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def C(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def H(x):
    return sha(C(x))


def is_tool_path(rel):
    """True for a path the audit must not read (acceptance tools)."""
    rel = rel.replace("\\", "/")
    if rel.endswith(".py") or rel.endswith(".pyc"):
        return True
    return any(rel.startswith(p) or ("/" + p) in rel for p in FORBIDDEN)


class Forbidden(Exception):
    pass


class Reader:
    """Central reader: every byte read is hashed and recorded by path."""

    def __init__(self, repo):
        self.repo = os.path.abspath(repo)
        self.shas = {}
        self._tars = {}

    def p(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.repo, rel)

    def key(self, path):
        ap = os.path.abspath(path)
        if ap.startswith(self.repo + os.sep):
            return os.path.relpath(ap, self.repo)
        return ap

    def bytes(self, rel):
        path = self.p(rel)
        k = self.key(path)
        if is_tool_path(k):
            raise Forbidden(k)
        with open(path, "rb") as f:
            b = f.read()
        self.shas[k] = sha(b)
        return b

    def hash_only(self, rel):
        """Identity check only: sha256 of a file's bytes; the content is not returned or parsed."""
        path = self.p(rel)
        with open(path, "rb") as f:
            s = sha(f.read())
        self.shas[self.key(path)] = s
        return s

    def json(self, rel):
        return json.loads(self.bytes(rel))

    def text(self, rel):
        return self.bytes(rel).decode("utf-8")

    def exists(self, rel):
        return os.path.lexists(self.p(rel))

    def isdir(self, rel):
        return os.path.isdir(self.p(rel))

    def listdir(self, rel):
        return sorted(os.listdir(self.p(rel)))

    def maybe_json(self, rel):
        return self.json(rel) if self.exists(rel) else None

    def tar_member(self, tar_rel, name):
        if tar_rel not in self._tars:
            self._tars[tar_rel] = tarfile.open(fileobj=io.BytesIO(self.bytes(tar_rel)))
        tf = self._tars[tar_rel]
        m = tf.getmember(name)
        f = tf.extractfile(m)
        if f is None:
            raise KeyError(name)
        b = f.read()
        self.shas[self.key(self.p(tar_rel)) + "!" + name] = sha(b)
        return b

    def tar_names(self, tar_rel):
        if tar_rel not in self._tars:
            self._tars[tar_rel] = tarfile.open(fileobj=io.BytesIO(self.bytes(tar_rel)))
        return [(m.name, m.isfile()) for m in self._tars[tar_rel].getmembers()]

    def dir_hash(self, rel):
        """sha256 of "".join(f"{rel}\\0{sha}\\n") over sorted files below rel."""
        root = self.p(rel)
        items = []
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in fn:
                ap = os.path.join(dp, f)
                items.append((os.path.relpath(ap, root), sha(self.bytes(ap))))
        items.sort()
        return sha("".join(f"{r}\0{s}\n" for r, s in items).encode()), len(items)

    def tree_hash(self, rel):
        """Assets tree rule: sorted, a symlink adds rel\\0symlink:<target>\\n."""
        root = self.p(rel)
        items = []
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in fn + [d for d in dn if os.path.islink(os.path.join(dp, d))]:
                ap = os.path.join(dp, f)
                r = os.path.relpath(ap, root)
                if os.path.islink(ap):
                    items.append((r, "symlink:" + os.readlink(ap)))
                else:
                    items.append((r, sha(self.bytes(ap))))
        items.sort()
        return sha("".join(f"{r}\0{s}\n" for r, s in items).encode())


STATUS_ORDER = {"failed": 4, "unrun": 3, "post_acceptance": 2, "attested": 1, "passed": 0}
STATUSES = ("passed", "attested", "post_acceptance", "unrun", "failed")


class Row:
    def __init__(self, plan, section, index, check, rid):
        self.plan, self.section, self.index, self.check, self.id = plan, section, index, check, rid
        self.failed, self.unrun, self.attested, self.evidence = [], [], [], []
        self.post = []

    def post_acceptance(self, m):
        self.post.append(m)

    def fail(self, m):
        self.failed.append(m)

    def miss(self, m):
        self.unrun.append(m)

    def attest(self, m):
        self.attested.append(m)

    def ev(self, m):
        self.evidence.append(m)

    def need(self, cond, m):
        if not cond:
            self.fail(m)
        return bool(cond)

    def eq(self, a, b, m):
        if a != b:
            self.fail(f"{m}: {short(a)} != {short(b)}")
            return False
        return True

    def absorb(self, other, label):
        for m in other.failed:
            self.fail(f"[{label}] {m}")
        for m in other.unrun:
            self.miss(f"[{label}] {m}")
        for m in other.attested:
            self.attest(f"[{label}] {m}")
        for m in other.post:
            self.post_acceptance(f"[{label}] {m}")

    @property
    def status(self):
        if self.failed:
            return "failed"
        if self.unrun:
            return "unrun"
        if self.post:
            return "post_acceptance"
        if self.attested:
            return "attested"
        return "passed"

    def reason(self):
        parts = []
        for name, lst in (("failed", self.failed), ("unrun", self.unrun), ("post_acceptance", self.post), ("attested", self.attested)):
            if lst:
                more = f" (+{len(lst) - 5} more)" if len(lst) > 5 else ""
                parts.append(f"{name}: " + "; ".join(lst[:5]) + more)
        return " | ".join(parts) if parts else "all sub-checks re-derived from raw values"

    def to_json(self):
        return {
            "plan": self.plan, "section": self.section, "index": self.index, "check": self.check,
            "id": self.id, "status": self.status, "evidence": self.evidence,
            "reason": self.reason(),
            "counts": {"failed": len(self.failed), "unrun": len(self.unrun), "post_acceptance": len(self.post),
                       "attested": len(self.attested)},
        }


def short(x, n=80):
    s = x if isinstance(x, str) else json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "..."


class Audit:
    def __init__(self, repo, run):
        self.r = Reader(repo)
        self.repo = self.r.repo
        self.run = os.path.abspath(run)
        self.run_rel = self.r.key(self.run)
        self.rows = []
        self.cache = {}

    # ---------------------------------------------------------------- helpers
    def R(self, rel):
        return os.path.join(self.run_rel, rel)

    def rj(self, rel):
        k = ("run", rel)
        if k not in self.cache:
            self.cache[k] = self.r.json(self.R(rel))
        return self.cache[k]

    def cj(self, rel):
        k = ("repo", rel)
        if k not in self.cache:
            self.cache[k] = self.r.json(rel)
        return self.cache[k]

    def row(self, plan, section, index, check, rid, fn):
        row = Row(plan, section, index, check, rid)
        try:
            fn(row)
        except Forbidden as e:
            row.miss(f"needs a forbidden tool file {e}")
        except Exception as e:  # noqa: BLE001
            row.miss(f"audit exception {type(e).__name__}: {e}; {traceback.format_exc().splitlines()[-3][:160]}")
        self.rows.append(row)
        return row

    def find(self, rid):
        for row in self.rows:
            if row.id == rid:
                return row
        raise KeyError(rid)

    # ----------------------------------------------------------- contract data
    def ts_plan(self):
        return self.cj(TS_PLAN_REL)

    def rs_plan(self):
        return self.cj(RS_PLAN_REL)

    def neg_ts(self):
        return {c["id"]: c for c in self.cj(TS_CT + "/negative-cases.json")["cases"]}

    def neg_rs(self):
        return {c["id"]: c for c in self.cj(RS_CT + "/negative-cases.json")["cases"]}

    def target_examples(self):
        return self.cj(TS_CT + "/target-examples.json")

    def inventory(self):
        return self.cj(TS_CT + "/case-inventory.json")

    def scope(self):
        return self.cj(TS_CT + "/scope.json")

    def fixtures(self):
        return self.cj("som-rs/tests/compiler-v1/cnl-positive-fixtures.json")["cases"]

    def coverage_index(self):
        return self.cj("som-rs/tests/rust-output-v1/cases/index.json")["cases"]

    def example_prompts(self):
        out = {}
        for c in self.cj(CV1 + "/examples/index.json")["cases"]:
            cid = c.get("id")
            p = c.get("prompt") or c.get("prompt_json") or c.get("typed_prompt")
            if isinstance(p, str):
                out[cid] = self.cj(CV1 + "/examples/" + os.path.basename(p))
        return out

    def target_cases(self):
        return self.rj("target/report.json")["cases"]

    def target_body(self, case):
        b = self.r.bytes(self.R("target/" + case["response_file"]))
        return b, json.loads(b)

    def bundles_cases(self, section):
        return [c for c in self.rj("bundles/report.json")["cases"] if c["section"] == section]

    def bresp(self, name):
        p = self.R("bundles/responses/" + name)
        if not self.r.exists(p):
            return None, None
        b = self.r.bytes(p)
        return b, json.loads(b)

    def acceptance_ids(self):
        inv = self.inventory()
        ids = []
        for k in ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases"):
            ids += [x["id"] for x in inv[k] if x.get("typescript", "in_scope") == "in_scope"]
        legacy = [x["id"] for x in inv["legacy_in_scope"]]
        return ids, legacy

    # -------------------------------------------------------------- diagnostics
    def check_diag(self, row, cid, body_text, resp_bytes, resp, http_status, expected, form, span=True):
        """Exact diagnostic plus span located in the raw request body."""
        exp = dict(expected)
        exp_status = exp.pop("http_status", None)
        span_text = exp.pop("span_text", None)
        if exp_status is not None:
            row.eq(http_status, exp_status, f"{cid} http_status")
        if not isinstance(resp, dict) or resp.get("status") != "error":
            row.fail(f"{cid} response is not an error body")
            return
        diags = resp.get("diagnostics") or []
        if len(diags) != 1:
            row.fail(f"{cid} has {len(diags)} diagnostics, expected 1")
            return
        d = diags[0]
        row.eq(resp.get("code"), exp.get("code"), f"{cid} code")
        for k, v in exp.items():
            if k in ("http_status",):
                continue
            row.eq(d.get(k), v, f"{cid} diagnostic.{k}")
        if span and body_text is not None and "byte_start" in exp and exp.get("byte_start") is not None:
            texts = [("request body", body_text)]
            if form == "cnl":
                try:
                    texts.insert(0, ("CNL source", json.loads(body_text)["source"]))
                except (ValueError, KeyError, TypeError):
                    pass
            chosen = None
            for label, tx in texts:
                rb = tx.encode("utf-8")
                if span_text is not None and rb[exp["byte_start"]:exp["byte_end"]].decode("utf-8", "replace") == span_text:
                    chosen = (label, tx)
                    break
            if chosen is None:
                row.fail(f"{cid} span text not found at byte_start..byte_end in the CNL source or the request body")
                return
            raw = chosen[1].encode("utf-8")
            # line and column from the byte offsets
            pre = raw[:exp["byte_start"]].decode("utf-8")
            line = pre.count("\n") + 1
            col_c = len(pre) - (pre.rfind("\n") + 1) + 1
            row.eq(line, exp.get("line"), f"{cid} line from byte_start")
            row.eq(col_c, exp.get("column"), f"{cid} column from byte_start")
            pre2 = raw[:exp["byte_end"]].decode("utf-8")
            line2 = pre2.count("\n") + 1
            col2 = len(pre2) - (pre2.rfind("\n") + 1) + 1
            row.eq(line2, exp.get("end_line"), f"{cid} end_line from byte_end")
            row.eq(col2, exp.get("end_column"), f"{cid} end_column from byte_end")

    # ------------------------------------------------------------- scope oracle
    def oracle(self, prompt, form):
        """Reimplementation of the scope.json profile. Returns None or (rule, pointer, args)."""
        sc = self.scope()
        pre = "/prompt" if form == "json" else ""
        v = prompt.get("version")
        if v not in sc["prompt_versions"]["in_scope"]:
            if v in sc["prompt_versions"]["out_of_scope"]:
                return ("prompt_version", pre + "/version",
                        {"version": v.rsplit("v", 1)[1], "family": sc["prompt_versions"]["out_of_scope"][v]})
            return ("compiler_v1", None, {})
        reserved = set(sc["identifiers"]["reserved_names"])
        tables = set(sc["identifiers"]["reserved_tables"])
        v1_4 = v != "som.prompt.v5"
        if v == "som.prompt.v5":
            caps = prompt.get("capabilities")
            if caps:
                return ("v5_capabilities", pre + "/capabilities/auth", {"auth": caps.get("auth")})
            res = [("/resources/parent", prompt["resources"]["parent"]),
                   ("/resources/child", prompt["resources"]["child"])]
        else:
            res = [("/resource", prompt["resource"])]
        for base, r in res:
            path = r.get("path", "")
            segs = path.split("/")[1:] if path.startswith("/") else path.split("/")
            if any(s == "" for s in segs):
                return ("path_empty_segment", pre + base + "/path", {"path": path})
            if v1_4 and segs and segs[0] == "healthz":
                return ("path_reserved", pre + base + "/path", {"path": path})
            if r.get("table") in tables:
                return ("table_reserved", pre + base + "/table", {"table": r.get("table")})
            for i, f in enumerate(r.get("fields", [])):
                if f.get("name") in reserved:
                    return ("identifier_reserved", pre + base + f"/fields/{i}/name", {"name": f["name"]})
        if v == "som.prompt.v5":
            fk = (prompt.get("relation") or {}).get("foreign_key")
            if fk in reserved:
                return ("identifier_reserved", pre + "/relation/foreign_key", {"name": fk})
        return None

    def oracle_texts(self, rule, args):
        ref = self.scope()["refusals"][rule]
        return ref["code"], ref["http_status"], ref["cause"].format(**args), ref["allowed_form"]

    # ------------------------------------------------------------ legacy jobs
    def legacy_jobs(self):
        """The 544 legacy jobs of legacy544 run-005 (its result.json records), in inventory order."""
        if "legacy_jobs" not in self.cache:
            recs = self.r.json(L544_REL + "/result.json")["records"]
            jobs = []
            for i, rec in enumerate(recs):
                d = f"{L544_REL}/compile/{rec['prompt_sha256']}"
                prompt = self.r.json(d + "/prompt.json")
                jobs.append({"index": i, "rec": rec, "dir": d, "prompt": prompt, "oracle": self.oracle(prompt, "json")})
            self.cache["legacy_jobs"] = jobs
        return self.cache["legacy_jobs"]

    def check_legacy_target(self, row, want):
        """Check the target/report.json legacy section against legacy544 run-005 and the scope oracle.

        want: "out" (jobs the oracle refuses) or "in" (jobs it admits). Returns (n, {(group, version, rule): count}).
        """
        jobs = self.legacy_jobs()
        cases = {c["id"]: c for c in self.target_cases() if c["section"] == "legacy"}
        row.eq(sorted(cases), sorted(j["rec"]["id"] for j in jobs), "target legacy ids = legacy544 run-005 record ids")
        inv_in = {x["id"]: x for x in self.inventory()["legacy_in_scope"]}
        n, groups = 0, collections.Counter()
        for j in jobs:
            rec, o, prompt = j["rec"], j["oracle"], j["prompt"]
            in_scope = o is None
            if (want == "in") != in_scope:
                continue
            cid = rec["id"]
            c = cases.get(cid)
            if c is None:
                row.fail(f"{cid} has no target result")
                continue
            row.eq(H(prompt), rec["prompt_sha256"], f"{cid} prompt hash")
            row.eq((c.get("prompt_sha256"), c.get("inventory_index"), c.get("group")), (rec["prompt_sha256"], j["index"], rec["group"]),
                   f"{cid} prompt hash, inventory index and group")
            req = self.r.json(j["dir"] + "/compile-json.request.json")
            body = json.dumps(dict(req, target="typescript"), separators=(",", ":"), ensure_ascii=False).encode()
            row.eq(c["request_sha256"], sha(body), f"{cid} request = run-005 typed JSON request with target typescript")
            b, rj = self.target_body(c)
            row.eq(sha(b), c["response_sha256"], f"{cid} response sha")
            if in_scope:
                ii = inv_in.get(cid)
                row.need(ii is not None and ii["prompt_sha256"] == rec["prompt_sha256"] and ii["inventory_index"] == j["index"],
                         f"{cid} admitted by the oracle but not the case-inventory legacy_in_scope record")
                bun = rj.get("bundle") or {}
                row.eq((c["http_status"], rj.get("status"), bun.get("version"), bun.get("target")), (200, "ok", "som.dsl-bundle.v2", "typescript"),
                       f"{cid} typescript bundle")
                row.need(rj.get("code") != "UNSUPPORTED_TARGET", f"{cid} UNSUPPORTED_TARGET")
                row.eq(H(bun.get("prompt")), rec["prompt_sha256"], f"{cid} bundle prompt hash")
                rule = "in_scope"
            else:
                row.need(cid not in inv_in, f"{cid} refused by the oracle but listed in legacy_in_scope")
                rule, ptr, args = o
                if rule == "compiler_v1":
                    row.fail(f"{cid}: prompt version unknown to scope.json")
                    continue
                code, st, cause, allowed = self.oracle_texts(rule, args)
                d = rj.get("diagnostics") or [{}]
                row.eq(c["http_status"], st, f"{cid} http")
                row.eq(len(d), 1, f"{cid} one diagnostic")
                row.eq((rj.get("status"), rj.get("code"), d[0].get("prompt_pointer"), d[0].get("cause"), d[0].get("allowed_form")),
                       ("error", code, ptr, cause, allowed), f"{cid} refusal vs oracle")
            groups[(rec["group"], prompt.get("version"), rule)] += 1
            n += 1
        return n, groups

    def legacy_counts(self, want):
        return {(x["group"], x["prompt_version"], x["typescript"]): x["count"] for x in self.inventory()["counts"]["legacy"]
                if (x["typescript"] == "in_scope") == (want == "in")}

    # ======================================================== integrity rows
    def integrity(self):
        A = "audit"

        def plans(row):
            for rel, want in ((TS_PLAN_REL, TS_PLAN_SHA), (RS_PLAN_REL, RS_PLAN_SHA)):
                row.eq(sha(self.r.bytes(rel)), want, f"{rel} sha256")
            h = self.rj("header.json")
            for k in ("acceptance_plans", "acceptance_plans_before", "acceptance_plans_after"):
                v = h.get(k)
                if v is None:
                    row.fail(f"header.{k} missing")
                    continue
                row.eq(v["typescript_output_v1"]["sha256"], TS_PLAN_SHA, f"header.{k} typescript plan")
                row.eq(v["rust_output_v1"]["sha256"], RS_PLAN_SHA, f"header.{k} rust plan")
            row.ev("both plans hash to the pinned values and header records them before and after")

        self.row(A, "integrity", 0, "Plans hashed and recorded in the header before and after", "integrity.plans", plans)

        def candidate(row):
            h = self.rj("header.json")
            cb, ca = h["candidate_before"], h["candidate_after"]
            binb = self.r.bytes(CAND_REL + "/som")
            bsha = sha(binb)
            build = self.r.json(CAND_REL + "/build.json")
            runtime = self.r.json(CAND_REL + "/runtime.json")
            src_bytes = self.r.bytes(CAND_REL + "/source.json")
            src = json.loads(src_bytes)
            cli = self.rj("cli.json")
            for name, v in (("header.candidate_before", cb["binary_sha256"]), ("header.candidate_after", ca["binary_sha256"]),
                            ("build.json", build["binary_sha256"]), ("runtime.json", runtime["binary_sha256"]),
                            ("cli.json", cli["binary_sha256"])):
                row.eq(v, bsha, f"{name} binary_sha256 vs disk")
            row.eq(build["binary_bytes"], len(binb), "build.binary_bytes")
            row.eq(cb["binary_bytes"], len(binb), "header binary_bytes")
            row.eq(cb["build_json_sha256"], sha(self.r.bytes(CAND_REL + "/build.json")), "header build_json_sha256")
            row.eq(ca["build_json_sha256"], cb["build_json_sha256"], "build_json before/after")
            man = sha(self.r.bytes(CAND_REL + "/assets/manifest.json"))
            row.eq(build["asset_manifest_sha256"], man, "build.asset_manifest_sha256 vs assets/manifest.json")
            row.eq(cb["asset_manifest_sha256"], man, "header asset_manifest_sha256")
            row.eq(ca["asset_manifest_sha256"], man, "header after asset_manifest_sha256")
            files = build["assets_packaged"]["files"]
            disk = {n: sha(self.r.bytes(CAND_REL + "/assets/" + n)) for n in self.r.listdir(CAND_REL + "/assets")
                    if not os.path.isdir(self.r.p(CAND_REL + "/assets/" + n))}
            row.eq(disk, files, "assets files vs build.assets_packaged.files")
            tree = self.r.tree_hash(CAND_REL + "/assets")
            row.eq(cb["assets_tree_sha256"], tree, "header assets_tree_sha256 (tree rule)")
            row.eq(ca["assets_tree_sha256"], tree, "header after assets_tree_sha256")
            row.eq(build["source_sha256"], sha(src_bytes), "build.source_sha256 = sha256(source.json bytes)")
            row.eq(src["archive_sha256"], sha(self.r.bytes(CAND_REL + "/source.tar")), "source.archive_sha256 vs source.tar")
            members = dict(self.r.tar_names(CAND_REL + "/source.tar"))
            bad = 0
            missing = 0
            for rel, want in src["files"].items():
                cands = [rel, "som-rs/" + rel]
                name = next((c for c in cands if members.get(c)), None)
                if name is None:
                    missing += 1
                    continue
                if sha(self.r.tar_member(CAND_REL + "/source.tar", name)) != want:
                    bad += 1
            row.need(missing == 0, f"{missing} source.json files absent from source.tar")
            row.need(bad == 0, f"{bad} source.tar members differ from source.json")
            row.ev(f"source.tar: {len(src['files'])} source.json files verified")
            commit = src.get("commit")
            row.need(isinstance(commit, str) and len(commit) == 40, "source.json commit recorded")
            row.eq(cb["commit"], commit, "header candidate commit vs source.json")
            row.eq(ca["commit"], commit, "header after commit")
            for k in ("tree_before", "tree_after"):
                row.eq(build[k]["head"], commit, f"build.{k}.head")
                row.eq(build[k]["porcelain"], "", f"build.{k}.porcelain clean")
            row.eq(runtime["health"], cb["health"], "runtime.json health vs header candidate_before.health")
            row.eq(cb["health"], ca["health"], "candidate health before/after")
            row.ev(f"candidate binary {bsha[:12]}, commit {commit[:12]}, assets tree {tree[:12]}")
            if runtime.get("accepted") is not True:
                row.ev("note: runtime.json accepted is not true (recorded as a raw value)")

        self.row(A, "integrity", 1, "Candidate binary, source and asset hashes agree across records", "integrity.candidate", candidate)

        def baseline(row):
            h = self.rj("header.json")
            b = sha(self.r.bytes(BASE_REL + "/som"))
            row.eq(b, BASELINE_BIN_SHA, "baseline som sha256")
            for k in ("baseline_before", "baseline_after"):
                row.eq(h[k]["binary_sha256"], BASELINE_BIN_SHA, f"header.{k}.binary_sha256")
                row.eq(h[k]["assets_tree_sha256"], self.r.tree_hash(BASE_REL + "/assets"), f"header.{k}.assets_tree_sha256")

        self.row(A, "integrity", 2, "Baseline binary is the pinned rust-output-candidate-006", "integrity.baseline", baseline)

        def receipts(row):
            names = self.r.listdir(self.R("receipts"))
            row.eq(len(names), 13, "receipt count")
            for n in names:
                rc = self.r.json(self.R("receipts/" + n))
                row.eq(rc.get("exit_code"), 0, f"{n} exit_code")
                for s in ("stdout", "stderr"):
                    row.eq(sha(self.r.bytes(self.R(rc[s]))), rc[s + "_sha256"], f"{n} {s} sha")
                for rel, want in rc.get("outputs", {}).items():
                    p = self.R(rel)
                    if not self.r.exists(p):
                        row.fail(f"{n} output {rel} missing")
                    elif self.r.isdir(p):
                        got, nfiles = self.r.dir_hash(p)
                        row.eq(got, want, f"{n} output dir {rel}")
                    else:
                        row.eq(sha(self.r.bytes(p)), want, f"{n} output {rel}")
                if self.r.bytes(self.R(rc["stderr"])) != b"":
                    row.ev(f"note: {n} stderr is not empty")
            row.ev(f"{len(names)} receipts: exit codes, logs and outputs verified")

        self.row(A, "integrity", 3, "Run receipts: exit codes, log and output hashes", "integrity.receipts", receipts)

        def tools(row):
            h = self.rj("header.json")
            t, tb, ta = h["tools"], h.get("tools_before"), h.get("tools_after")
            row.need(tb is not None and ta is not None, "tools_before and tools_after recorded")
            if tb is not None and ta is not None:
                row.eq(tb.get("files"), ta.get("files"), "tools files before/after")
                row.eq(tb.get("head"), ta.get("head"), "tools head before/after")
            row.need(isinstance(t.get("head"), str) and len(t["head"]) == 40, "tools commit recorded")
            row.eq(t.get("porcelain"), "", "tools porcelain clean")
            n_tool = n_ok = n_diff = 0
            for rel, want in t["files"].items():
                if is_tool_path(rel):
                    n_tool += 1
                    continue
                if not self.r.exists(rel):
                    n_diff += 1
                    continue
                if sha(self.r.bytes(rel)) == want:
                    n_ok += 1
                else:
                    n_diff += 1
            row.ev(f"tools head {t['head'][:12]}; non-tool files equal on disk {n_ok}, differing {n_diff}; tool files not read {n_tool}")
            if n_diff:
                row.ev("note: differing non-tool files may reflect commits after the tools head")

        self.row(A, "integrity", 4, "Tools commit recorded and unchanged during the run", "integrity.tools", tools)

        def tool_files(row):
            # identity only: tool bytes are hashed (Reader.hash_only), never parsed, imported or run
            h = self.rj("header.json")
            maps = {k: (h.get(k) or {}).get("files") for k in ("tools", "tools_before", "tools_after")}
            for k, m in maps.items():
                row.need(isinstance(m, dict) and m, f"header.{k}.files recorded")
            if not all(isinstance(m, dict) and m for m in maps.values()):
                return
            row.eq(maps["tools_before"], maps["tools_after"], "tools_before.files = tools_after.files")
            row.eq(maps["tools"], maps["tools_before"], "tools.files = tools_before.files")
            n_tool = n_ok = 0
            for rel, want in sorted(maps["tools"].items()):
                if not self.r.exists(rel):
                    row.fail(f"tool file {rel} missing on disk")
                    continue
                got = self.r.hash_only(rel)
                if is_tool_path(rel):
                    n_tool += 1
                if rel == AUDIT_SELF_REL and got != want:
                    # this script was extended for audit-002 after the run; the run recorded the committed v1
                    row.eq(want, AUDIT_V1_SHA, f"{rel} recorded hash = committed audit v1")
                    row.ev(f"note: {rel} on disk ({got[:12]}) is this audit's edited version; the run recorded v1 {want[:12]}")
                    n_ok += 1
                    continue
                if row.eq(got, want, f"tool file {rel} on disk vs header"):
                    n_ok += 1
            for n in self.r.listdir(self.R("receipts")):
                rc = self.rj("receipts/" + n)
                fm = rc.get("files")
                if isinstance(fm, dict):
                    for rel, want in fm.items():
                        path = next((c for c in (rel, "som-rs/" + rel) if self.r.exists(c)), None)
                        if path is None:
                            row.fail(f"receipt {n} file {rel} missing on disk")
                        else:
                            row.eq(self.r.hash_only(path), want, f"receipt {n} file {rel}")
            row.ev(f"{len(maps['tools'])} tool-root files hashed (identity only; {n_tool} acceptance tool files not parsed): "
                   f"{n_ok} equal the header records; before/after maps equal")

        self.row(A, "integrity", 5, "Acceptance tool file hashes", "integrity.tool_files", tool_files)

        def ts_chain(row):
            fz_b = self.r.bytes(TS_FREEZE)
            am_b = self.r.bytes(TS_AM1)
            fz, am = json.loads(fz_b), json.loads(am_b)
            row.eq(am.get("previous_receipt_sha256"), sha(fz_b), "amendment-001 previous_receipt_sha256")
            row.eq(H(fz["files"]), fz["target_contract"]["sha256"], "freeze target_contract = H(files)")
            row.eq(am["target_contract"]["previous_sha256"], fz["target_contract"]["sha256"], "amendment-001 tc previous")
            row.eq(H(am["files"]), am["target_contract"]["sha256"], "amendment-001 target_contract = H(files)")
            row.eq(am["target_contract"]["sha256"], TS_TC_SHA, "amendment-001 tc = bundle target_contract")
            row.eq(am["files"].get(TS_PLAN_REL, am["files"].get(TS_PLAN_REL.replace("som-rs/", ""))), TS_PLAN_SHA,
                   "TS plan sha in amendment-001 files")
            self._files_on_disk(row, am["files"], "amendment-001")
            for ch in am.get("changes", []):
                arch = ch.get("archived_copy")
                if arch and not is_tool_path(arch):
                    row.eq(sha(self.r.bytes(arch)), ch.get("archived_copy_sha256"), f"archived copy {os.path.basename(arch)}")
                    row.eq(ch.get("archived_copy_sha256"), ch.get("old_sha256"), f"archived copy = old {os.path.basename(arch)}")
            rv = am.get("independent_review", {})
            row.ev(f"freeze status {fz.get('status')}; amendment status {am.get('status')}; review final {rv.get('final_verdict')}, "
                   f"rounds {[x.get('verdict') for x in rv.get('rounds', [])]}")

        self.row(A, "integrity", 6, "TypeScript receipt chain freeze-2026-10-21 -> amendment-001", "integrity.ts_chain", ts_chain)

        def rs_chain(row):
            b3, b4, b5 = self.r.bytes(RS_AM3), self.r.bytes(RS_AM4), self.r.bytes(RS_AM5)
            a3, a4, a5 = json.loads(b3), json.loads(b4), json.loads(b5)
            row.eq(sha(b3), "147a3bf8885e64fb511f9d6973d9b42f608ceda80b13d9fcac504582fdde1b21", "amendment-003 sha")
            row.eq(a4.get("previous_receipt_sha256"), sha(b3), "amendment-004 previous_receipt_sha256")
            row.eq(a5.get("previous_receipt_sha256"), sha(b4), "amendment-005 previous_receipt_sha256")
            row.eq(a4["target_contract"]["previous_sha256"], a3["target_contract"]["sha256"], "004 tc previous")
            row.eq(a5["target_contract"]["previous_sha256"], a4["target_contract"]["sha256"], "005 tc previous")
            for name, a in (("003", a3), ("004", a4), ("005", a5)):
                row.eq(H(a["files"]), a["target_contract"]["sha256"], f"amendment-{name} tc = H(files)")
            row.eq(a5["target_contract"]["sha256"], RS_TC_SHA, "amendment-005 tc = Rust manifest target_contract")
            row.eq(a5["files"].get(RS_PLAN_REL, a5["files"].get(RS_PLAN_REL.replace("som-rs/", ""))), RS_PLAN_SHA,
                   "Rust plan sha in amendment-005 files")
            self._files_on_disk(row, a5["files"], "amendment-005")
            for ch in a5.get("changes", []):
                arch = ch.get("archived_copy")
                if arch and not is_tool_path(arch):
                    row.eq(sha(self.r.bytes(arch)), ch.get("archived_copy_sha256"), f"archived copy {os.path.basename(arch)}")
            rv = a5.get("independent_review", {})
            row.ev(f"amendment-005 review final {rv.get('final_verdict')}; check_contract keys {sorted(a5.get('check_contract', {}))}")

        self.row(A, "integrity", 7, "Rust receipt chain amendment-004 -> amendment-005", "integrity.rust_chain", rs_chain)

        def rp_inputs(row):
            rp = self.rj("rust-products.json")
            n_tool = 0
            for rel, want in rp["inputs"].items():
                if is_tool_path(rel):
                    n_tool += 1
                    if rel == AUDIT_SELF_REL:
                        row.eq(want, AUDIT_V1_SHA, f"rust-products input {rel} = committed audit v1")
                    else:
                        row.eq(self.r.hash_only(rel), want, f"rust-products tool input {rel} (identity hash)")
                    continue
                row.eq(sha(self.r.bytes(rel)), want, f"rust-products input {rel}")
            row.ev(f"{len(rp['inputs'])} inputs verified; {n_tool} of them tool files checked by identity hash only")

        self.row(A, "integrity", 8, "rust-products.json recorded inputs", "integrity.rust_products_inputs", rp_inputs)

    def _files_on_disk(self, row, files, label):
        ok = skipped = 0
        tool_changed = []
        for rel, want in files.items():
            cands = [rel, "som-rs/" + rel]
            path = next((c for c in cands if self.r.exists(c)), None)
            if path is None:
                row.fail(f"{label} file {rel} missing on disk")
                continue
            if is_tool_path(path):
                skipped += 1
                got = AUDIT_V1_SHA if path == AUDIT_SELF_REL else self.r.hash_only(path)
                if got == want:
                    ok += 1
                else:
                    tool_changed.append(rel)
                continue
            if sha(self.r.bytes(path)) == want:
                ok += 1
            else:
                row.fail(f"{label} file {rel} differs on disk")
        row.ev(f"{label} files map: {ok} equal on disk ({skipped} tool files checked by identity hash only)")
        if tool_changed:
            row.ev(f"note: {label}: {len(tool_changed)} tool files changed after the receipt (later commits; the run's own "
                   f"tool identity is integrity.tool_files): {tool_changed[:6]}")

    # ================================================= python_unchanged (TS)
    def python_unchanged(self):
        plan = self.ts_plan()["python_unchanged"]["checks"]

        def pu0(row):
            pb = self.rj("python-baseline/report.json")
            rows = pb["rows"]
            cnt = collections.Counter((x["source"], x["variant"]) for x in rows)
            row.eq(dict(cnt), {("public", "none"): 127, ("public", "python"): 127, ("legacy", "none"): 544, ("legacy", "python"): 544} and
                   {k: v for k, v in {("public", "none"): 127, ("public", "python"): 127, ("legacy", "none"): 544, ("legacy", "python"): 544}.items()},
                   "row coverage by source and variant")
            bad = collections.Counter()
            for x in rows:
                for ph in ("compile", "assemble"):
                    e = x[ph]
                    for k in ("request_sha256", "response_sha256", "status"):
                        if e.get("baseline_" + k) is None or e.get("baseline_" + k) != e.get("qa_" + k):
                            bad[(ph, k)] += 1
            for (ph, k), n in bad.items():
                row.fail(f"{n} rows with {ph} qa_{k} != baseline_{k}")
            pr = self.rj("python-replay.json")
            pub_ids = {x["id"] for x in pr["public_rows"]}
            pb_pub = collections.Counter(x["id"] for x in rows if x["source"] == "public")
            row.eq(set(pb_pub), pub_ids, "python-baseline public ids = python-replay public ids")
            row.need(all(v == 2 for v in pb_pub.values()), "each public id has both variants")
            leg_ids = collections.Counter(x["id"] for x in rows if x["source"] == "legacy")
            row.eq(len(leg_ids), 544, "distinct legacy ids")
            row.eq(set(leg_ids), {x["id"] for x in pr["legacy_rows"]}, "legacy ids = python-replay legacy ids")
            # tie the without-target compile request to the recorded public compile-json request
            dir_of = {}
            for x in pr["public_rows"]:
                for e in x["exchanges"]:
                    dir_of[x["id"]] = e["exchange"].split("/")[0]
            tied = 0
            for x in rows:
                if x["source"] == "public" and x["variant"] == "none":
                    d = dir_of.get(x["id"])
                    rq = self.r.bytes(PP_REL + f"/{d}/compile-json.request.raw") if d else None
                    if rq is not None and sha(rq) == x["compile"]["baseline_request_sha256"]:
                        tied += 1
            row.ev(f"{len(rows)} rows: compile and assemble qa request/response/status equal baseline; "
                   f"{tied}/127 public without-target requests equal the recorded public-products-003b compile-json request bytes")
            if tied != 127:
                row.attest(f"{127 - tied} public without-target requests not tied to recorded request bytes; the baseline request hash is the record")
            row.attest("baseline and candidate response bodies are kept only as hashes; equality is of recorded hashes")

        self.row("typescript-output-v1", "python_unchanged.checks", 0, plan[0], "ts.python_unchanged.0", pu0)

        def pu1(row):
            pr = self.rj("python-replay.json")
            row.eq(len(pr["public_rows"]), 127, "public rows")
            row.eq(len(pr["legacy_rows"]), 544, "legacy rows")
            row.eq(len(pr["legacy_compile_rows"]), 540, "legacy compile prompts")
            asm_bad = asm_ok = comp = tie_bad = tie_ok = brk = 0
            for x in pr["public_rows"]:
                for e in x["exchanges"]:
                    d, kind = e["exchange"].split("/")
                    raw = base64.b64decode(self.r.bytes(PP_REL + f"/{d}/{kind}.response.raw.b64"))
                    if sha(raw) == e["expected_response_sha256"]:
                        tie_ok += 1
                    else:
                        tie_bad += 1
                    rq = self.r.bytes(PP_REL + f"/{d}/{kind}.request.raw")
                    if kind != "assemble" and sha(rq) != e["request_sha256"]:
                        row.fail(f"public {d} {kind} request differs from the recorded request bytes")
                    if kind == "assemble":
                        if e["response_sha256"] == e["expected_response_sha256"] and e["status"] == e["expected_status"]:
                            asm_ok += 1
                        else:
                            asm_bad += 1
                        if e.get("recorded_bundle_status") == 400 and e.get("recorded_bundle_code") == "ASSET_HASH_MISMATCH":
                            brk += 1
                        else:
                            row.fail(f"public {d} recorded bundle not refused with ASSET_HASH_MISMATCH 400")
                    else:
                        if e["status"] != e["expected_status"]:
                            row.fail(f"public {d} {kind} status {e['status']} != {e['expected_status']}")
                        comp += 1
            for x in pr["legacy_rows"]:
                e = x["exchange"]
                d = e["exchange"].split("/")[0]
                raw = base64.b64decode(self.r.bytes(L544_REL + f"/{d}/assemble.response.raw.b64"))
                if sha(raw) == e["expected_response_sha256"]:
                    tie_ok += 1
                else:
                    tie_bad += 1
                if e["response_sha256"] == e["expected_response_sha256"] and e["status"] == e["expected_status"]:
                    asm_ok += 1
                else:
                    asm_bad += 1
                if e.get("recorded_bundle_status") == 400 and e.get("recorded_bundle_code") == "ASSET_HASH_MISMATCH":
                    brk += 1
                else:
                    row.fail(f"legacy {d} recorded bundle not refused with ASSET_HASH_MISMATCH 400")
            for x in pr["legacy_compile_rows"]:
                for e in x["exchanges"]:
                    psha, kind = e["exchange"].split("/")
                    raw = base64.b64decode(self.r.bytes(L544_REL + f"/compile/{psha}/{kind}.response.raw.b64"))
                    if sha(raw) == e["expected_response_sha256"]:
                        tie_ok += 1
                    else:
                        tie_bad += 1
                    if e["status"] != e["expected_status"]:
                        row.fail(f"legacy compile {psha[:12]} {kind} status differs")
                    comp += 1
            row.need(asm_bad == 0, f"{asm_bad} rebased assembles differ from the recorded response")
            row.need(tie_bad == 0, f"{tie_bad} expected hashes differ from the recorded raw responses")
            row.eq(brk, 671, "recorded bundles refused with ASSET_HASH_MISMATCH")
            row.ev(f"rebased assembles equal recorded bytes: {asm_ok}; expected hashes tied to recorded raw responses: {tie_ok}")
            row.attest(f"{comp} compile exchanges: the descriptor-only difference (amendment_004) cannot be recomputed; "
                       "only response hashes are kept")
            vc = self.rj("validate-cases.json")
            evd = self.cj(EV + "/rust-output-v1/coverage-cases-python-2026-10-15.json")
            want = {c["id"]: c["python_files"] for c in evd["cases"]}
            got = {c["id"]: c.get("python_files") for c in vc["cases"]}
            row.eq(got, want, "validate-cases python_files vs frozen evidence")
            for c in vc["cases"]:
                row.need(c.get("json_status") == 200 and c.get("cnl_status") == 200 and c.get("assemble_status") == 200,
                         f"validate-cases {c['id']} statuses")
            row.attest("validate-cases same_bundle is a per-case tool boolean")

        self.row("typescript-output-v1", "python_unchanged.checks", 1, plan[1], "ts.python_unchanged.1", pu1)

        def pu2(row):
            tpc = self.rj("ts-python-controls.json")
            neg = self.neg_ts()
            names = [c["id"] for c in self.target_examples()["name_cases"]]
            ids = [x["id"] for x in tpc["results"]]
            row.eq(sorted(ids), sorted(list(neg) + names), "control ids = negative cases + name cases")
            row.eq(tpc["sources_sha256"].get("negative-cases.json"), sha(self.r.bytes(TS_CT + "/negative-cases.json")), "negative-cases sha")
            row.eq(tpc["sources_sha256"].get("target-examples.json"), sha(self.r.bytes(TS_CT + "/target-examples.json")), "target-examples sha")
            want_st = {}
            for cid, nc in neg.items():
                pce = nc.get("python_control_expected", "")
                want_st[cid] = nc["http_status"] if pce.startswith("the same diagnostic") else (
                    int(re.match(r"HTTP (\d+)", pce).group(1)) if re.match(r"HTTP (\d+)", pce) else None)
            for e in self.target_examples()["name_cases"]:
                m = re.match(r"HTTP (\d+)", e.get("python_control_expected", ""))
                want_st[e["id"]] = int(m.group(1)) if m else None
            for x in tpc["results"]:
                w = want_st.get(x["id"])
                if w is None:
                    row.miss(f"{x['id']} python_control_expected not interpreted")
                else:
                    row.eq(x.get("control_status"), w, f"{x['id']} control_status vs python_control_expected")
            row.attest(f"{len(ids)} controls: bundle equality to the unchanged v1 bundle is a per-case tool boolean")

        self.row("typescript-output-v1", "python_unchanged.checks", 2, plan[2], "ts.python_unchanged.2", pu2)

        def pu3(row):
            rpc = self.rj("rust-python-controls.json")
            neg = self.neg_rs()
            row.eq(sorted(x["id"] for x in rpc["results"]), sorted(neg), "control ids = rust negative cases")
            row.eq(rpc["negative_cases_sha256"], sha(self.r.bytes(RS_CT + "/negative-cases.json")), "rust negative-cases sha")
            exp = {}
            for cid, c in neg.items():
                m = re.match(r"HTTP (\d+)", c.get("python_control_expected", ""))
                if m:
                    exp[cid] = int(m.group(1))
            bad = [x["id"] for x in rpc["results"] if x["id"] in exp and x.get("control_status") != exp[x["id"]]]
            row.need(not bad, f"control_status differs from python_control_expected: {bad}")
            row.attest(f"{len(rpc['results'])} controls: bundle equality is a per-case tool boolean")

        self.row("typescript-output-v1", "python_unchanged.checks", 3, plan[3], "ts.python_unchanged.3", pu3)

    # ================================================== target selection (TS)
    def target_selection(self):
        plan = self.ts_plan()["target_selection_checks"]
        te = self.target_examples()

        def ts0(row):
            cases = {(c["section"], c["id"]): c for c in self.target_cases()}
            n = 0
            pairs = collections.defaultdict(dict)
            for sec, lst in (("target_examples", te["examples"]), ("name_cases", te["name_cases"])):
                for e in lst:
                    c = cases.get((sec, e["id"]))
                    if c is None:
                        row.fail(f"{e['id']} has no result")
                        continue
                    row.eq(c["request_sha256"], sha(e["body"].encode()), f"{e['id']} request bytes are the contract body")
                    b, j = self.target_body(c)
                    row.eq(sha(b), c["response_sha256"], f"{e['id']} response file sha")
                    exp = e["expected"]
                    if exp.startswith("HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript"):
                        row.eq(c["http_status"], 200, f"{e['id']} http")
                        bun = j.get("bundle", {})
                        row.eq((j.get("status"), bun.get("version"), bun.get("target")), ("ok", "som.dsl-bundle.v2", "typescript"),
                               f"{e['id']} bundle version/target")
                        base = e["id"].replace("-cnl-", "-").replace("-json-", "-")
                        pairs[base][e["id"]] = b
                    elif exp.startswith("HTTP 200 with the same response bytes as the same request without target"):
                        nt = self.R("target/" + c["response_file"][:-5] + "__without_target.json")
                        if not self.r.exists(nt):
                            row.fail(f"{e['id']} has no without-target response")
                        else:
                            row.eq(c["http_status"], 200, f"{e['id']} http")
                            row.need(self.r.bytes(nt) == b, f"{e['id']} target python bytes != without target")
                    elif exp.startswith("HTTP 422"):
                        row.eq(c["http_status"], 422, f"{e['id']} http")
                        row.eq(j.get("code"), "UNSUPPORTED_TARGET", f"{e['id']} code")
                    else:
                        row.miss(f"{e['id']} expected text not interpreted: {exp[:60]}")
                    n += 1
            same = sum(1 for v in pairs.values() if len(v) == 2 and len(set(v.values())) == 1)
            diff = [k for k, v in pairs.items() if len(v) == 2 and len(set(v.values())) != 1]
            row.need(not diff, f"CNL and JSON bundles differ for {diff}")
            row.ev(f"{n} example and name-case bodies checked; {same} CNL/JSON pairs byte-identical")

        self.row("typescript-output-v1", "target_selection_checks", 0, plan[0], "ts.target_selection.0", ts0)

        def ts1(row):
            neg = self.neg_ts()
            cases = {c["id"]: c for c in self.target_cases() if c["section"] == "negative_cases"}
            n = 0
            for cid, nc in neg.items():
                if not cid.startswith("TT"):
                    continue
                c = cases.get(cid)
                if c is None:
                    row.fail(f"{cid} has no result")
                    continue
                row.eq(c["request_sha256"], sha(nc["body"].encode()), f"{cid} request bytes")
                b, j = self.target_body(c)
                row.eq(sha(b), c["response_sha256"], f"{cid} response sha")
                exp = dict(nc["expected"], http_status=nc["http_status"], span_text=nc.get("span_text"))
                self.check_diag(row, cid, nc["body"], b, j, c["http_status"], exp, nc["form"])
                n += 1
            row.eq(n, 7, "TT case count")

        self.row("typescript-output-v1", "target_selection_checks", 1, plan[1], "ts.target_selection.1", ts1)

        def ts2(row):
            self.rt_check(row)

        self.row("typescript-output-v1", "target_selection_checks", 2, plan[2], "ts.target_selection.2", ts2)

        def ts3(row):
            # python_equivalence: target python bytes == no target bytes
            pe = [c for c in self.target_cases() if c["section"] == "python_equivalence"]
            ok = 0
            covered = set()
            for c in pe:
                b, j = self.target_body(c)
                row.eq(sha(b), c["response_sha256"], f"{c['id']} response sha")
                nt = c["response_file"].replace("__python.json", "__no_target.json")
                if nt == c["response_file"]:
                    stem = c["response_file"][:-5]
                    nt, py = stem + "__no_target.json", stem + "__python.json"
                    if self.r.exists(self.R("target/" + py)):
                        b = self.r.bytes(self.R("target/" + py))
                if not self.r.exists(self.R("target/" + nt)):
                    row.fail(f"{c['id']} has no without-target response")
                    continue
                b2 = self.r.bytes(self.R("target/" + nt))
                if b == b2:
                    ok += 1
                    covered.add(c["id"].replace("-python-equivalence", ""))
                else:
                    row.fail(f"{c['id']} target python bytes != without target")
            row.ev(f"python_equivalence: {ok}/{len(pe)} byte-equal pairs")
            # python-baseline none vs python
            pb = self.rj("python-baseline/report.json")
            by = collections.defaultdict(dict)
            for x in pb["rows"]:
                by[(x["source"], x["id"])][x["variant"]] = x
            eq_pairs = 0
            for k, v in by.items():
                if "none" in v and "python" in v:
                    if all(v["none"][ph]["qa_response_sha256"] == v["python"][ph]["qa_response_sha256"] for ph in ("compile", "assemble")):
                        eq_pairs += 1
                        covered.add(k[1])
                    else:
                        row.fail(f"{k[1]} python-baseline python response != none response")
            row.ev(f"python-baseline: {eq_pairs} prompt pairs with equal compile and assemble responses")
            inv = self.inventory()
            fixture_ids = {f["id"] for f in self.fixtures()}
            acc, legacy = self.acceptance_ids()
            need = set(acc) | set(legacy) | fixture_ids
            exmap = {x["id"]: x for x in inv["public_examples"]}
            miss = []
            for i in sorted(need):
                alias = {i, i + "-json", i + "-cnl"}
                if exmap.get(i):
                    alias.add(i.replace("example-", "PUBLIC_").replace("example-v", "PUBLIC_v"))
                if not (alias & covered):
                    miss.append(i)
            leg_cov = {x["id"] for x in pb["rows"] if x["source"] == "legacy"}
            row.ev(f"legacy in-scope jobs with a python-baseline none/python pair: {sum(1 for i in legacy if i in leg_cov)} of {len(legacy)}")
            cli = self.rj("cli.json")
            cli_by = collections.defaultdict(dict)
            for x in cli["rows"]:
                cli_by[x["input"]][x["target"]] = x.get("stdout_sha256")
            for i in list(miss):
                v = cli_by.get(i, {})
                if None in v and "python" in v and v[None] == v["python"]:
                    miss.remove(i)
                    row.attest(f"{i}: covered only by CLI stdout equality (target python vs none)")
            if miss:
                row.miss(f"{len(miss)} acceptance cases or CNL fixtures without a python-vs-no-target comparison: {miss[:8]}")
            row.attest("python-baseline compares typed-JSON prompts; the CNL form with target python is compared only for the examples")

        self.row("typescript-output-v1", "target_selection_checks", 3, plan[3], "ts.target_selection.3", ts3)

        def ts4(row):
            self.cli_check(row, ts_only=True)

        self.row("typescript-output-v1", "target_selection_checks", 4, plan[4], "ts.target_selection.4", ts4)

    def rt_check(self, row):
        neg = self.neg_rs()
        rd = self.rj("rt-diagnostics/report.json")
        row.eq(rd["negative_cases_sha256"], sha(self.r.bytes(RS_CT + "/negative-cases.json")), "rust negative-cases sha")
        rows = {c["id"]: c for c in rd.get("rows", rd.get("cases", []))}
        rt = [cid for cid in neg if cid.startswith("RT")]
        row.eq(sorted(rows), sorted(rt), "RT ids")
        for cid in rt:
            nc, c = neg[cid], rows.get(cid)
            if c is None:
                continue
            row.eq(c["request_sha256"], sha(nc["body"].encode()), f"{cid} request bytes")
            b = self.r.bytes(self.R("rt-diagnostics/" + c["response_file"]))
            row.eq(sha(b), c["response_sha256"], f"{cid} response sha")
            exp = dict(nc["expected"], http_status=nc["http_status"], span_text=nc.get("span_text"))
            self.check_diag(row, cid, nc["body"], b, json.loads(b), c["http_status"], exp, nc["form"])
        row.ev(f"{len(rt)} RT cases checked against rust-output-v1 negative-cases.json")

    def cli_check(self, row, ts_only=False):
        cli = self.rj("cli.json")
        row.eq(cli["binary_sha256"], sha(self.r.bytes(CAND_REL + "/som")), "cli binary")
        row.eq(cli["inputs"]["negative_cases_sha256"], sha(self.r.bytes(RS_CT + "/negative-cases.json")), "cli negative cases sha")
        row.eq(cli["inputs"]["coverage_index_sha256"], sha(self.r.bytes("som-rs/tests/rust-output-v1/cases/index.json")), "cli coverage index sha")
        t = cli["typescript"]
        src = self.r.text("som-rs/tests/rust-output-v1/cases/cov-v1-types.cnl")
        forms = {
            "compact": json.dumps({"format": "som.cnl.v1", "source": src, "target": "typescript"}, separators=(",", ":"), ensure_ascii=False),
            "canonical": C({"format": "som.cnl.v1", "source": src, "target": "typescript"}).decode(),
            "spaced": json.dumps({"format": "som.cnl.v1", "source": src, "target": "typescript"}),
            "compact_ascii": json.dumps({"format": "som.cnl.v1", "source": src, "target": "typescript"}, separators=(",", ":")),
        }
        hit = [k for k, v in forms.items() if sha(v.encode()) == t["request_sha256"]]
        if hit:
            row.ev(f"typescript request hash recomputed from the cov-v1-types CNL with target typescript ({hit[0]} JSON)")
        else:
            row.attest("typescript request hash equals the tool's expected hash; the body formula could not be recomputed")
        row.eq(t["request_sha256"], t["expected_request_sha256"], "typescript request = expected")
        row.eq((t["http_status"], t["exit_code"], t.get("bundle_version"), t.get("target")), (200, 0, "som.dsl-bundle.v2", "typescript"),
               "typescript row")
        rows = cli["rows"]
        refusals = [x for x in rows if x.get("code")]
        for x in refusals:
            row.eq(x["exit_code"], 2, f"refusal {x['input']} exit")
            row.eq(x["stdout_sha256"], EMPTY_SHA, f"refusal {x['input']} empty stdout")
            row.need(x["http_status"] in (400, 422), f"refusal {x['input']} http {x['http_status']}")
        ts_ref = [x for x in refusals if x.get("target") == "typescript"]
        if not ts_ref:
            row.miss("no CLI row refuses a request with --target typescript; exit status 2 on a TypeScript refusal is not shown")
        for x in ts_ref:
            row.eq((x["http_status"], x["code"]), (422, "UNSUPPORTED_TARGET"), f"typescript refusal {x['input']} http/code")
        if ts_ref:
            row.ev(f"{len(ts_ref)} CLI typescript refusal row(s) {[x['input'] for x in ts_ref]}: 422 UNSUPPORTED_TARGET, exit 2, empty stdout")
        by = collections.defaultdict(dict)
        for x in rows:
            if not x.get("code"):
                by[x["input"]][x["target"]] = x
        for inp, v in by.items():
            if None in v and "python" in v:
                row.eq(v[None]["stdout_sha256"], v["python"]["stdout_sha256"], f"{inp} python stdout = no target")
                row.eq(v[None].get("bundle_version"), "som.dsl-bundle.v1", f"{inp} no-target bundle v1")
            if "rust" in v:
                row.eq(v["rust"].get("bundle_version"), "som.dsl-bundle.v2", f"{inp} rust bundle v2")
            for x in v.values():
                row.eq(x["exit_code"], 0, f"{inp} {x['target']} exit")
        row.ev(f"{len(rows)} CLI rows; {len(refusals)} refusals all exit 2 with empty stdout")

    # ============================================================ scope (TS)
    def scope_checks(self):
        plan = self.ts_plan()["scope_checks"]

        def sc0(row):
            neg = self.neg_ts()
            cases = {c["id"]: c for c in self.target_cases() if c["section"] == "negative_cases"}
            n = 0
            for cid, nc in neg.items():
                if not cid.startswith(("TS", "TP")):
                    continue
                c = cases.get(cid)
                if c is None:
                    row.fail(f"{cid} has no result")
                    continue
                row.eq(c["request_sha256"], sha(nc["body"].encode()), f"{cid} request bytes")
                b, j = self.target_body(c)
                row.eq(sha(b), c["response_sha256"], f"{cid} response sha")
                exp = dict(nc["expected"], http_status=nc["http_status"], span_text=nc.get("span_text"))
                self.check_diag(row, cid, nc["body"], b, j, c["http_status"], exp, nc["form"])
                n += 1
            row.eq(n, 26, "TS and TP case count")

        self.row("typescript-output-v1", "scope_checks", 0, plan[0], "ts.scope.0", sc0)

        def sc1(row):
            oos = [c for c in self.target_cases() if c["section"] == "out_of_scope"]
            inv = self.inventory()
            fx = {f["id"]: f for f in self.fixtures()}
            ex = self.example_prompts()
            fx_rule = collections.Counter()
            n = 0
            for c in oos:
                base, form = re.match(r"(.+)-(cnl|json)-typescript$", c["id"]).groups()
                prompt = None
                if base in fx:
                    prompt = fx[base]["expected_prompt"]
                elif base.startswith("example-"):
                    prompt = ex.get(base) or ex.get(base.replace("example-", ""))
                if prompt is None:
                    row.miss(f"{c['id']}: typed prompt not found")
                    continue
                o = self.oracle(prompt, form)
                if o is None or o[0] == "compiler_v1":
                    row.fail(f"{c['id']}: oracle admits the prompt but the case is out of scope")
                    continue
                rule, ptr, args = o
                code, st, cause, allowed = self.oracle_texts(rule, args)
                exp = c["expected"]
                row.eq(exp.get("rule"), rule, f"{c['id']} recorded rule vs oracle")
                b, j = self.target_body(c)
                row.eq(sha(b), c["response_sha256"], f"{c['id']} response sha")
                row.eq(c["http_status"], st, f"{c['id']} http")
                d = (j.get("diagnostics") or [{}])
                row.eq(len(d), 1, f"{c['id']} one diagnostic")
                row.eq((j.get("code"), d[0].get("prompt_pointer"), d[0].get("cause"), d[0].get("allowed_form")),
                       (code, ptr, cause, allowed), f"{c['id']} diagnostic vs oracle")
                if base in fx:
                    fx_rule[(fx[base]["category"], rule)] += 1 if form == "cnl" else 0
                n += 1
            want = collections.Counter()
            for x in inv["counts"]["cnl_positive_fixtures"]:
                if x["typescript"] != "in_scope":
                    want[(x["category"], x["typescript"])] = x["count"]
            row.eq({f"{k[0]}|{k[1]}": v for k, v in fx_rule.items() if v}, {f"{k[0]}|{k[1]}": v for k, v in want.items()},
                   "out-of-scope fixture rules (CNL form) vs case-inventory counts")
            exp_ex = [x["id"] for x in inv["public_examples"] if x["typescript"] != "in_scope"]
            got_ex = {re.match(r"(.+)-(cnl|json)-typescript$", c["id"]).group(1) for c in oos if c["id"].startswith("example-")}
            row.eq(sorted(got_ex), sorted(exp_ex), "out-of-scope public examples")
            row.ev(f"{n} out-of-scope cases match the reimplemented scope oracle and the scope.json refusal texts")
            n_leg, groups = self.check_legacy_target(row, "out")
            row.eq({"|".join(k): v for k, v in groups.items()}, {"|".join(k): v for k, v in self.legacy_counts("out").items()},
                   "out-of-scope legacy jobs by group, version and rule vs case-inventory counts")
            row.ev(f"{n_leg} out-of-scope legacy jobs refused with the oracle's UNSUPPORTED_TARGET diagnostic; requests rebuilt "
                   "from legacy544 run-005")

        self.row("typescript-output-v1", "scope_checks", 1, plan[1], "ts.scope.1", sc1)

        def sc2(row):
            inv = self.inventory()
            fx = self.fixtures()
            agree = 0
            # fixtures: oracle vs case-inventory category counts
            got = collections.Counter()
            for f in fx:
                o = self.oracle(f["expected_prompt"], "cnl")
                got[(f["category"], o[0] if o else "in_scope")] += 1
            want = {(x["category"], x["typescript"]): x["count"] for x in inv["counts"]["cnl_positive_fixtures"]}
            row.eq({f"{a}|{b}": v for (a, b), v in got.items()}, {f"{a}|{b}": v for (a, b), v in want.items()},
                   "oracle on 119 CNL fixtures vs case-inventory")
            agree += len(fx)
            ex = self.example_prompts()
            for x in inv["public_examples"]:
                p = ex.get(x["id"]) or ex.get(x["id"].replace("example-", ""))
                if p is None:
                    p = self.cj(x["prompt"])
                o = self.oracle(p, "json")
                row.eq(o[0] if o else "in_scope", x["typescript"], f"oracle on {x['id']}")
                agree += 1
            for x in self.coverage_index():
                p = self.cj("som-rs/tests/rust-output-v1/cases/" + x["prompt"])
                o = self.oracle(p, "json")
                row.eq(o, None, f"oracle admits coverage case {x['id']}")
                agree += 1
            # negative JSON cases with a TypeScript refusal
            for cid, nc in self.neg_ts().items():
                if nc["form"] != "json" or nc["expected"]["code"] != "UNSUPPORTED_TARGET":
                    continue
                body = json.loads(nc["body"])
                o = self.oracle(body["prompt"], "json")
                row.need(o is not None, f"oracle admits {cid}")
                if o:
                    code, st, cause, allowed = self.oracle_texts(o[0], o[2])
                    row.eq((o[1], cause, allowed), (nc["expected"]["prompt_pointer"], nc["expected"]["cause"], nc["expected"]["allowed_form"]),
                           f"oracle on {cid}")
                agree += 1
            for e in self.target_examples()["name_cases"]:
                body = json.loads(e["body"])
                if "prompt" in body:
                    row.eq(self.oracle(body["prompt"], "json"), None, f"oracle admits {e['id']}")
                    agree += 1
            # legacy: oracle on every recorded typed prompt
            leg_in = {x["prompt_sha256"] for x in inv["legacy_in_scope"]}
            ldir = L544_REL + "/compile"
            adm, ref = set(), collections.Counter()
            for d in self.r.listdir(ldir):
                p = self.r.json(f"{ldir}/{d}/prompt.json")
                row.eq(H(p), d, f"legacy prompt hash {d[:12]}")
                o = self.oracle(p, "json")
                if o is None:
                    adm.add(d)
                else:
                    ref[o[0]] += 1
            row.eq(sorted(adm), sorted(leg_in), "oracle admits exactly the case-inventory legacy_in_scope prompts")
            row.ev(f"oracle agrees with contract records on {agree} prompts; legacy prompts: {len(adm)} admitted, refusals {dict(ref)}")
            n_out, _ = self.check_legacy_target(row, "out")
            n_in, _ = self.check_legacy_target(row, "in")
            row.ev(f"candidate on legacy jobs with target typescript equals the oracle: {n_out} refusals, {n_in} admitted")
            # the out-of-scope typed-JSON cases in the target report
            n_js = 0
            for c in self.target_cases():
                if c["section"] == "out_of_scope" and c["id"].endswith("-json-typescript"):
                    n_js += 1
            row.ev(f"{n_js} out-of-scope typed-JSON cases are compared with the oracle in ts.scope.1")

        self.row("typescript-output-v1", "scope_checks", 2, plan[2], "ts.scope.2", sc2)

        def sc3(row):
            ins = [c for c in self.target_cases() if c["section"] == "in_scope"]
            for c in ins:
                b, j = self.target_body(c)
                row.eq(sha(b), c["response_sha256"], f"{c['id']} response sha")
                row.need(c["http_status"] == 200 and j.get("status") == "ok", f"{c['id']} not 200 ok")
                row.need(j.get("code") != "UNSUPPORTED_TARGET", f"{c['id']} UNSUPPORTED_TARGET")
            acc, legacy = self.acceptance_ids()
            got = {re.sub(r"-(cnl|json)-typescript$", "", c["id"]) for c in ins}
            bc = {c["id"] for c in self.bundles_cases("bundle_checks")}
            missing = [i for i in acc + legacy if i not in got and i not in bc]
            row.need(not missing, f"acceptance cases without a typescript compile result: {missing}")
            for c in self.bundles_cases("bundle_checks"):
                for k in ("compile_cnl_1", "compile_json_1"):
                    b, j = self.bresp(f"bundle_checks__{c['id']}__{k}.json")
                    if j is not None and j.get("status") != "ok":
                        row.fail(f"{c['id']} {k} not ok")
            row.ev(f"{len(ins)} in_scope target cases and {len(bc)} bundle cases compile to a bundle")
            n_in, groups = self.check_legacy_target(row, "in")
            row.eq({"|".join(k): v for k, v in groups.items()}, {"|".join(k): v for k, v in self.legacy_counts("in").items()},
                   "in-scope legacy jobs by group and version vs case-inventory counts")
            row.ev(f"{n_in} in-scope legacy jobs compile with target typescript to a som.dsl-bundle.v2 bundle (target report)")

        self.row("typescript-output-v1", "scope_checks", 3, plan[3], "ts.scope.3", sc3)

    # ========================================================= bundle checks
    def bundle_checks(self):
        plan = self.ts_plan()["bundle_checks"]
        acc, legacy = self.acceptance_ids()

        inv_leg = {x["id"]: x for x in self.inventory()["legacy_in_scope"]}

        def cov(row):
            ids = [c["id"] for c in self.bundles_cases("bundle_checks")]
            row.eq(sorted(ids), sorted(acc + legacy), "bundle case ids = every acceptance case (incl. legacy in-scope jobs)")
            row.eq(len(ids), self.inventory()["counts"]["acceptance_total"], "bundle case count = acceptance_total")
            for cid in legacy:
                _, j = self.bresp(f"bundle_checks__{cid}__compile_json_1.json")
                if j is None:
                    row.fail(f"{cid} has no typed JSON compile")
                    continue
                row.eq(H((j.get("bundle") or {}).get("prompt")), inv_leg[cid]["prompt_sha256"], f"{cid} bundle prompt = inventory prompt hash")
            return ids

        def compiles(cid):
            out = {}
            for k in ("compile_cnl_1", "compile_cnl_2", "compile_json_1", "compile_json_2"):
                b, j = self.bresp(f"bundle_checks__{cid}__{k}.json")
                if b is not None:
                    out[k] = (b, j)
            return out

        def bc0(row):
            ids = cov(row)
            single = []
            for cid in ids:
                cs = compiles(cid)
                forms = {k.split("_")[1] for k in cs}
                if len(set(b for b, _ in cs.values())) != 1:
                    row.fail(f"{cid} compile bytes differ across forms or repeats")
                for k, (b, j) in cs.items():
                    bun = j.get("bundle", {})
                    row.eq((bun.get("version"), bun.get("target")), ("som.dsl-bundle.v2", "typescript"), f"{cid} {k}")
                if forms != {"cnl", "json"}:
                    single.append(cid)
            te_cnl = {e["id"] for e in self.target_examples()["name_cases"] if "source" in json.loads(e["body"])}
            recs = {j["rec"]["id"]: j for j in self.legacy_jobs()}
            tcases = {c["id"]: c for c in self.target_cases() if c["section"] == "legacy"}
            no_cnl = leg_cnl_missing = 0
            for cid in single:
                if cid.startswith("TN") and cid not in te_cnl:
                    no_cnl += 1
                    continue  # the name case exists only as typed JSON: no CNL source exists
                if cid in recs:
                    j = recs[cid]
                    src_rel = j["dir"] + "/source.cnl"
                    if self.r.exists(src_rel) and sha(self.r.bytes(src_rel)) == j["rec"].get("cnl_sha256"):
                        leg_cnl_missing += 1
                        continue
                    no_cnl += 1
                    continue
                row.miss(f"{cid}: only one form was compiled although a CNL source exists")
            if leg_cnl_missing:
                row.miss(f"{leg_cnl_missing} legacy in-scope jobs were compiled from typed JSON only, but each has a CNL source "
                         "(legacy544 run-005 source.cnl, sha equal to its record's cnl_sha256); case-inventory selection_rules[2] "
                         "requires the CNL compile where a CNL source exists and byte-identical bundles")
            # the same typed JSON request in the target report gives the same bytes
            same = 0
            for cid in legacy:
                c = tcases.get(cid)
                x, _ = self.bresp(f"bundle_checks__{cid}__compile_json_1.json")
                if c is not None and x is not None:
                    if self.r.bytes(self.R("target/" + c["response_file"])) == x:
                        same += 1
                    else:
                        row.fail(f"{cid} target-report and bundle-check typed JSON compiles differ")
            row.ev(f"{len(ids) - len(single)} cases with byte-identical CNL and JSON bundles; {no_cnl} cases have no CNL source; "
                   f"{same} legacy typed-JSON compiles equal the target-report bytes")

        self.row("typescript-output-v1", "bundle_checks", 0, plan[0], "ts.bundle_checks.0", bc0)

        def bc1(row):
            ids = cov(row)
            schema = self.cj(TS_CT + "/bundle.schema.json")
            cat = self.cj(TS_CT + "/catalog/typescript-catalog.v1.json")
            cat_ids = {b["id"] for f in cat["files"] for b in f["blocks"]}
            v = Validator(schema, {"som.compiler-prompt-family.v1": self.cj(CV1 + "/compiler-prompt.schema.json")})
            for cid in ids:
                for k, (b, j) in compiles(cid).items():
                    if not k.endswith("_1"):
                        continue
                    bun = j["bundle"]
                    errs = v.validate(bun)
                    for e in errs[:3]:
                        (row.miss if e.startswith("unsupported") else row.fail)(f"{cid} schema: {e}")
                    files = bun["dsl"]["files"]
                    row.need(len(files) <= 12, f"{cid} {len(files)} files")
                    for f in files:
                        row.need(len(f["blocks"]) <= 64, f"{cid} {f['path']} has {len(f['blocks'])} blocks")
                        for bl in f["blocks"]:
                            row.need(bl["id"].startswith("ts.") and bl["id"] in cat_ids, f"{cid} block {bl['id']}")
            row.ev(f"{len(ids)} cases validated with an in-audit JSON-schema validator; catalog has {len(cat_ids)} block ids")

        self.row("typescript-output-v1", "bundle_checks", 1, plan[1], "ts.bundle_checks.1", bc1)

        def bc2(row):
            ids = cov(row)
            exp = self.expected_ts_assets(row)
            shared = ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer", "semantic_contracts")
            for cid in ids:
                for k, (b, j) in compiles(cid).items():
                    a = j["bundle"]["assets"]
                    for key, want in exp.items():
                        row.eq(a.get(key), want, f"{cid} {k} assets.{key}")
                    row.eq(sorted(a), sorted(self.cj(TS_CT + "/api.schema.json")["x-manifest"]["asset_keys"]), f"{cid} asset keys")
                _, rm = self.bresp(f"bundle_checks__{cid}__rust_manifest.json")
                if rm is None:
                    row.fail(f"{cid} has no Rust manifest response")
                    continue
                ra = rm["bundle"]["assets"]
                row.eq(ra["target_contract"]["sha256"], RS_TC_SHA, f"{cid} Rust target_contract")
                for key in shared:
                    row.eq(a.get(key), ra.get(key), f"{cid} shared {key} = Rust")
            row.ev("asset descriptors recomputed: catalog bytes, block_library and backend from source.tar, target_contract "
                   "from amendment-001, formatter from toolchain.json")

        self.row("typescript-output-v1", "bundle_checks", 2, plan[2], "ts.bundle_checks.2", bc2)

        def bc3(row):
            ids = cov(row)
            for cid in ids:
                for a, b in (("compile_cnl_1", "compile_cnl_2"), ("compile_json_1", "compile_json_2"), ("assemble_1", "assemble_2")):
                    x, _ = self.bresp(f"bundle_checks__{cid}__{a}.json")
                    y, _ = self.bresp(f"bundle_checks__{cid}__{b}.json")
                    if x is None and y is None:
                        continue
                    row.need(x is not None and x == y, f"{cid} {a} != {b}")
            row.ev(f"{len(ids)} cases: repeated compiles and assembles byte-identical")

        self.row("typescript-output-v1", "bundle_checks", 3, plan[3], "ts.bundle_checks.3", bc3)

        def bc4(row):
            ids = cov(row)
            pl = self.cj(TS_CT + "/product-layout.json")
            paths = sorted(f["path"] for f in pl["files"])
            refs = {n: self.r.bytes(TS_CT + "/reference/" + n) for n in ("package.json", "package-lock.json", "tsconfig.json")}
            tp = {p["id"]: p for p in self.rj("ts-products/report.json")["products"]}
            for cid in ids:
                b, j = self.bresp(f"bundle_checks__{cid}__assemble_1.json")
                files = j["files"]
                migs = [k for k in files if re.fullmatch(r"migrations/0001_[^/]+\.sql", k)]
                row.eq(len(migs), 1, f"{cid} one migrations/0001_<revision>.sql")
                norm = sorted("migrations/0001_<revision>.sql" if k in migs else k for k in files)
                row.eq(norm, paths, f"{cid} file set")
                for m in migs:
                    rev = m[len("migrations/0001_"):-len(".sql")]
                    first = files[m].split("\n", 1)[0]
                    row.need(first.startswith(f"-- revision: {rev}, down_revision: "), f"{cid} migration first line names revision {rev}")
                for n, rb in refs.items():
                    row.need(files.get(n, "").encode() == rb, f"{cid} {n} != reference")
                fs = H({k: sha(v.encode()) for k, v in files.items()})
                if cid in tp:
                    row.eq(tp[cid]["files_sha256"], fs, f"{cid} ts-products files_sha256")
                pdir = self.R(f"bundles/products/{cid}")
                if self.r.isdir(pdir):
                    for k, v in files.items():
                        row.need(self.r.bytes(pdir + "/" + k) == v.encode(), f"{cid} product file {k} differs from assemble")
            mrel = self.R("bundles/products/manifest.json")
            if self.r.exists(mrel):
                man = self.r.json(mrel)["products"]
                row.eq(sorted(m["id"] for m in man), sorted(ids), "products manifest ids = bundle case ids")
                for m in man:
                    row.eq(os.path.abspath(m["product"]), os.path.join(self.run, "bundles", "products", m["id"]), f"{m['id']} manifest product path")
                    _, j = self.bresp(f"bundle_checks__{m['id']}__compile_json_1.json")
                    if j is None:
                        _, j = self.bresp(f"bundle_checks__{m['id']}__compile_cnl_1.json")
                    row.need(j is not None and j["bundle"]["prompt"] == m["prompt"], f"{m['id']} manifest prompt = compiled bundle prompt")
                row.ev(f"products manifest: {len(man)} entries, paths and prompts match the compiled bundles")
            else:
                row.fail("bundles/products/manifest.json missing")
            row.ev(f"{len(ids)} products: {len(paths)} files each; fixed files equal reference/")

        self.row("typescript-output-v1", "bundle_checks", 4, plan[4], "ts.bundle_checks.4", bc4)

    def expected_ts_assets(self, row):
        if "ts_assets" in self.cache:
            return self.cache["ts_assets"]
        tar = CAND_REL + "/source.tar"
        names = dict(self.r.tar_names(tar))

        def member(rel):
            for c in ("som-rs/" + rel, rel):
                if names.get(c):
                    return self.r.tar_member(tar, c)
            raise KeyError(rel)

        mod = member("som-targets/src/typescript/mod.rs").decode()
        cat = self.r.bytes(TS_CT + "/catalog/typescript-catalog.v1.json")
        lib_files = ["som-targets/src/typescript/render.rs", "som-targets/src/typescript/render/related.rs"]
        inc = re.findall(r'include_str!\("([^"]+)"\)', mod)
        row.ev(f"mod.rs include_str entries: {len(inc)}")
        block_library = sha(b"".join(member(f) for f in lib_files))
        backend_files = ["som-targets/Cargo.toml", "som-targets/src/lib.rs", "som-targets/src/main.rs", "som-targets/src/profile.rs",
                         "som-targets/src/typescript/format.rs", "som-targets/src/typescript/mod.rs",
                         "som-targets/src/typescript/profile.rs", "som-targets/src/typescript/render.rs",
                         "som-targets/src/typescript/render/related.rs"]
        backend = H({"version": "som.typescript-backend.v1", "files": {f: sha(member(f)) for f in backend_files}})
        tc = self.cj(TS_CT + "/toolchain.json")
        exp = {
            "catalog": {"id": "som.typescript-catalog.v1", "sha256": sha(cat)},
            "block_library": {"id": "som.typescript-block-library.v1", "sha256": block_library},
            "backend": {"id": "som.typescript-backend.v1", "sha256": backend},
            "target_contract": {"id": "som.typescript-output-contract.v1", "sha256": self.cj(TS_AM1)["target_contract"]["sha256"]},
            "formatter": {"id": tc["formatter"]["id"], "sha256": tc["formatter"]["sha256"]},
        }
        self.cache["ts_assets"] = exp
        return exp

    # ====================================================== assemble refusals
    def assemble_refusals(self):
        ar = self.ts_plan()["assemble_refusals"]
        cases = {c["id"]: c for c in self.bundles_cases("assemble_refusals")}
        for i, rec in enumerate(ar["recipes"]):
            def fn(row, rec=rec):
                c = cases.get(rec["id"])
                if c is None:
                    row.fail("no result")
                    return
                b = self.r.bytes(self.R("bundles/" + c["response_file"]))
                row.eq(sha(b), c["response_sha256"], "response sha")
                j = json.loads(b)
                row.eq(c["http_status"], rec["http_status"], "http status")
                row.eq(j.get("status"), "error", "error body")
                d = j.get("diagnostics") or []
                row.eq(len(d), 1, "one diagnostic")
                row.eq(j.get("code"), rec["code"], "code")
                if d:
                    row.eq(d[0].get("code"), rec["code"], "diagnostic code")
                    row.eq(d[0].get("prompt_pointer"), rec["prompt_pointer"], "pointer")
                row.ev(f"{rec['id']}: {rec['code']} {rec['http_status']} at the plan pointer")
                row.attest("the request body is kept only as a hash: that the recipe change was applied, and the "
                           "line/column/byte span in the canonical assemble request, are tool claims")

            self.row("typescript-output-v1", "assemble_refusals.recipes", i, f"{rec['id']}: {rec['change']}", f"ts.assemble_refusals.{rec['id']}", fn)

    # ========================================================= product gates
    def product_gates(self):
        pg = self.ts_plan()["product_gates"]
        checks = pg["each_acceptance_case"]
        tp = self.rj("ts-products/report.json")
        prods = {p["id"]: p for p in tp["products"]}
        acc, legacy = self.acceptance_ids()
        tc = self.cj(TS_CT + "/toolchain.json")
        ref_lock = sha(self.r.bytes(TS_CT + "/reference/package-lock.json"))
        ref_pkg = sha(self.r.bytes(TS_CT + "/reference/package.json"))

        def each(row, fn):
            row.eq(sorted(prods), sorted(acc + legacy), "product ids = every acceptance case (incl. legacy in-scope jobs)")
            for cid in acc + legacy:
                p = prods.get(cid)
                if p is None:
                    row.fail(f"{cid} has no product record")
                    continue
                g = p["gates"]
                fn(row, cid, g, p)
            row.ev(f"{len(acc) + len(legacy)} products checked ({len(legacy)} legacy in-scope jobs)")

        def g0(row):
            want = tc["commands"]["install"].split()
            cache = tp["cache"]
            row.eq(cache["tree_sha256"], tc["vendored_cache"]["tree_sha256"], "cache tree = toolchain vendored_cache")
            row.eq(cache.get("tree_sha256_after"), cache["tree_sha256"], "cache unchanged after the run")

            def f(row, cid, g, p):
                cmd = g["install"]["command"]
                row.eq(cmd[-len(want) + 1:], want[1:], f"{cid} install argv")
                row.eq(g["install"]["exit_code"], 0, f"{cid} install exit")
            each(row, f)

        def g1(row):
            def f(row, cid, g, p):
                row.eq(g["typecheck"]["exit_code"], 0, f"{cid} typecheck exit")
                row.need("tsc" in " ".join(g["typecheck"].get("command", [])), f"{cid} typecheck command")
            each(row, f)

        def g2(row):
            def f(row, cid, g, p):
                cmd = " ".join(g["format_check"].get("command", []))
                row.eq(g["format_check"]["exit_code"], 0, f"{cid} format exit")
                row.need(all(x in cmd for x in ("--check", "--no-config", "--no-editorconfig")), f"{cid} prettier flags")
            each(row, f)

        def g3(row):
            def f(row, cid, g, p):
                w, wo = g["test"]["with_test_database_url"], g["test"]["without_test_database_url"]
                c = w["counts"]
                row.eq(w["exit_code"], 0, f"{cid} test exit")
                row.need(c["tests"] > 0 and c["pass"] == c["tests"] and c["fail"] == 0 and c["cancelled"] == 0
                         and c["skipped"] == 0 and c["todo"] == 0, f"{cid} test counts {c}")
                row.need(wo["exit_code"] != 0 and wo["counts"]["fail"] > 0, f"{cid} missing database is not a failure")
                row.eq(g["test"].get("command", [])[1:4], ["--test", "--test-concurrency=1", "test/api.test.ts"], f"{cid} test argv")
            each(row, f)

        def g4(row):
            def f(row, cid, g, p):
                lk = g["lock_unchanged"]
                row.eq((lk["package_json_sha256_before"], lk["package_json_sha256_after"]), (ref_pkg, ref_pkg), f"{cid} package.json")
                row.eq((lk["lock_sha256_before"], lk["lock_sha256_after"]), (ref_lock, ref_lock), f"{cid} package-lock.json")
                row.eq((lk["added_outside_node_modules"], lk["removed_outside_node_modules"], lk["changed_outside_node_modules"]),
                       ([], [], []), f"{cid} files outside node_modules")
            each(row, f)

        def g5(row):
            def f(row, cid, g, p):
                se = g["schema_equivalence"]
                row.eq(se["exit_code"], 0, f"{cid} migrate exit")
                row.need(se["compared_tables"], f"{cid} no compared tables")
                row.eq(se["typescript_schema"], se["python_schema"], f"{cid} TypeScript schema = Python schema")
                row.eq(se["differences"], [], f"{cid} differences")
                row.eq(sorted(se["excluded_tables"]), sorted(["alembic_version", "_som_ts_migrations"]), f"{cid} excluded tables")
            each(row, f)
            row.attest("the recorded schemas are the tool's introspection output; the database is not re-read")

        def g6(row):
            def f(row, cid, g, p):
                pa = g["parity"]
                fn = cid.replace("-", "_")
                pyb = self.r.bytes(self.R(f"ts-products/raw/py_{fn}.json"))
                tsb = self.r.bytes(self.R(f"ts-products/raw/ts_{fn}.json"))
                row.eq(sha(pyb), pa["python_raw_sha256"], f"{cid} python raw sha")
                row.eq(sha(tsb), pa["typescript_raw_sha256"], f"{cid} typescript raw sha")
                py, ts = json.loads(pyb), json.loads(tsb)
                row.eq(len(py["raw"]), pa["steps"], f"{cid} python steps")
                tpar = ts["raw"][:pa["steps"]]
                row.eq(len(ts["raw"]), pa["steps"] + g["strict_refusals"]["strict_steps"], f"{cid} typescript parity + strict steps")
                row.eq([x["step"] for x in py["raw"]], [x["step"] for x in tpar], f"{cid} step lists")
                row.eq((py["failures"], ts["failures"]), ([], []), f"{cid} raw failures")
                d = sum(1 for a, b in zip(py["raw"], tpar) if a.get("status") != b.get("status"))
                row.eq(d, 0, f"{cid} parity steps with different HTTP status")
                row.need(not [x for x in py["raw"] + tpar if x.get("status") == 500], f"{cid} has 500 responses")
            each(row, f)
            row.attest("body equality after the frozen normalization (ids, timestamps, D1-D7) is not recomputed; the "
                       "per-product differences count is a tool record")

        def g7(row):
            need = {"D1", "D2", "D3", "D4", "D5", "D7"}
            absent = collections.Counter()

            want_status = {"D1": 422, "D2": 422, "D3": 404, "D4": 422, "D5": 404, "D7": 422}
            not_applicable = collections.Counter()
            label_diff = []

            def f(row, cid, g, p):
                sr = g["strict_refusals"]
                pa = g["parity"]
                ts = json.loads(self.r.bytes(self.R(f"ts-products/raw/ts_{cid.replace('-', '_')}.json")))
                strict = ts["raw"][pa["steps"]:]
                row.eq(len(strict), sr["strict_steps"], f"{cid} strict steps in the raw transcript")
                raw_by = collections.Counter()
                for x in strict:
                    m = re.match(r"(D\d): ", x["step"])
                    if m:
                        raw_by[m.group(1)] += 1
                        w = want_status.get(m.group(1))
                        if w is None:
                            row.fail(f"{cid} strict step for {m.group(1)} has no expected status in this audit")
                        elif x.get("status") != w:
                            row.fail(f"{cid} {x['step'][:60]} status {x.get('status')} != {w}")
                        if m.group(1) == "D5":
                            resp = x.get("response")
                            resp = json.loads(resp) if isinstance(resp, str) else resp
                            row.eq(resp, {"detail": "Not Found"}, f"{cid} D5 body")
                    elif x["step"].startswith("prototype_members: "):
                        raw_by["prototype_members"] += 1
                    elif re.match(r"([a-z_]+: )?D1 to D7 (added|changed) no row$", x["step"]):
                        raw_by["no_row_change"] += 1
                        row.eq(x.get("status"), 200, f"{cid} {x['step']} status")
                    else:
                        row.fail(f"{cid} unclassified strict step {x['step'][:60]}")
                row.eq(sr["failures"], 0, f"{cid} strict failures")
                row.eq(sum(sr["by_divergence"].values()) + sr.get("prototype_members", 0), sr["strict_steps"], f"{cid} strict step count")
                rec = {k: v for k, v in raw_by.items() if k.startswith("D")}
                if rec != sr["by_divergence"]:
                    label_diff.append(f"{cid} raw {dict(sorted(raw_by.items()))} vs recorded {sr['by_divergence']}")
                # applicability from the compiled prompt: D1 needs a non-string body field, D4 a list query parameter
                prompt = None
                for k in ("compile_json_1", "compile_cnl_1"):
                    _, j = self.bresp(f"bundle_checks__{cid}__{k}.json")
                    if j is not None:
                        prompt = j["bundle"]["prompt"]
                        break
                res = [prompt["resource"]] if "resource" in prompt else list(prompt["resources"].values())
                has_nonstr = any(f.get("type") != "str" for r in res for f in r["fields"])
                has_query = any("?" in x["path"] for x in ts["raw"][:pa["steps"]])
                applicable = set(need)
                if not has_nonstr:
                    applicable.discard("D1")
                    not_applicable["D1"] += 1
                # D2 (behavior-normalization): a str longer than 255 code points or an int outside signed 32 bits
                # in a field value; a prompt with no str and no int field has no input that can reach it
                if not any(f.get("type") in ("str", "int") for r in res for f in r["fields"]):
                    applicable.discard("D2")
                    not_applicable["D2"] += 1
                if not has_query and prompt["version"] == "som.prompt.v1":
                    applicable.discard("D4")
                    not_applicable["D4"] += 1
                for dv in sorted(applicable - set(rec)):
                    absent[dv] += 1
            each(row, f)
            for dv, n in sorted(absent.items()):
                row.fail(f"divergence {dv} applies but has no strict step in the raw transcript of {n} products")
            row.ev(f"divergences judged not applicable (string-only fields for D1; no str or int field for D2; "
                   f"v1 prompt without list query for D4): "
                   f"{dict(not_applicable)}")
            if label_diff:
                row.ev(f"note: tool by_divergence differs from the raw step prefixes in {len(label_diff)} products, e.g. {label_diff[0]}")
            row.attest("prototype_members strict steps have no expected status in the contract; their outcome is the tool's record")

        def g8(row):
            def f(row, cid, g, p):
                st = g["startup"]
                for k in ("missing_database_url", "invalid_som_app_addr"):
                    row.eq(st[k]["exit_code"], 2, f"{cid} {k} exit")
                    row.eq(len(st[k]["stderr_lines"]), 1, f"{cid} {k} stderr lines")
                row.eq(st["sigterm"]["exit_code"], 0, f"{cid} SIGTERM exit")
                row.need(st["sigterm"].get("ready") is True, f"{cid} not ready before SIGTERM")
            each(row, f)

        for i, fn in enumerate((g0, g1, g2, g3, g4, g5, g6, g7, g8)):
            self.row("typescript-output-v1", "product_gates.each_acceptance_case", i, checks[i], f"ts.product_gates.{i}", fn)

        def ns(row):
            total = self.inventory()["counts"]["acceptance_total"]
            want = sorted(acc + legacy)
            row.eq(len(want), total, "acceptance ids = acceptance_total")
            row.eq(sorted(prods), want, "every acceptance case has a product record")
            gate_keys = ("install", "typecheck", "format_check", "test", "lock_unchanged", "schema_equivalence", "parity",
                         "strict_refusals", "startup")
            for cid in want:
                p = prods.get(cid)
                if p is None:
                    continue
                miss = [k for k in gate_keys if not isinstance(p["gates"].get(k), dict)]
                row.need(not miss, f"{cid} missing gate records {miss}")
            for i in range(9):
                r = self.find(f"ts.product_gates.{i}")
                if r.status in ("failed", "unrun"):
                    row.fail(f"ts.product_gates.{i} is {r.status}")
            # bundle checks are gates too: a skipped form or check blocks acceptance
            for i in range(5):
                r = self.find(f"ts.bundle_checks.{i}")
                if r.unrun:
                    row.fail(f"ts.bundle_checks.{i} has a skipped check: {r.unrun[0][:160]}")
            row.ev(f"{len(prods)} of {total} acceptance cases have all {len(gate_keys)} gate records")

        self.row("typescript-output-v1", "product_gates", None, pg["no_sampling"], "ts.product_gates.no_sampling", ns)

    # ========================================================= amendment 001
    def amendment_001(self):
        recs = self.ts_plan()["amendment_001"]["records"]
        tc = self.cj(TS_CT + "/toolchain.json")
        lock = self.cj(TS_CT + "/reference/package-lock.json")

        def a0(row):
            row.eq(lock.get("lockfileVersion"), 3, "lockfileVersion")
            row.eq(sha(self.r.bytes(TS_CT + "/reference/package-lock.json")), self.cj(TS_AM1)["files"].get(
                TS_CT + "/reference/package-lock.json", self.cj(TS_AM1)["files"].get("contracts/typescript-output-v1/reference/package-lock.json")),
                "lockfile sha sealed in amendment-001")

        def a1(row):
            pk = {k: v for k, v in lock["packages"].items() if k}
            lp = {x["path"]: x for x in tc["packages"]["lock_packages"]}
            row.eq(sorted(lp), sorted(pk), "lock_packages paths = lockfile packages")
            for k, v in pk.items():
                x = lp.get(k, {})
                row.eq((x.get("version"), x.get("resolved"), x.get("integrity")), (v.get("version"), v.get("resolved"), v.get("integrity")), k)
            row.ev(f"{len(pk)} lockfile packages")

        def a2(row):
            v = tc["vendored_cache"]
            tp = self.rj("ts-products/report.json")
            row.eq(tp["cache"]["tree_sha256"], v["tree_sha256"], "run cache tree = toolchain")
            row.eq(tp["cache"].get("tree_files"), v.get("tree_files"), "tree files")
            am = self.cj(TS_AM1)
            row.need(v["tree_sha256"] in json.dumps(am.get("related_local_paths", {})), "amendment-001 related_local_paths names the tree")
            row.attest("the vendored cache directory is outside the audit's raw inputs; its tree hash is not recomputed")

        def a3(row):
            f = tc["formatter"]
            pt = f.get("prettier_tree_sha256") or self.r.json(CAND_REL + "/build.json").get("prettier_tree_sha256")
            got = H({"node": tc["node"]["sha256"], "libnode": tc["node"]["libnode"]["sha256"], "prettier_tree_sha256": pt})
            row.eq(got, f["sha256"], "formatter sha256 by sha256_rule")
            row.ev(f"rule: {short(f.get('sha256_rule', ''), 120)}")
            row.attest("node, libnode and prettier tree hashes are recorded values; the binaries are not re-hashed")

        def a4(row):
            rv_rel = tc["reference_verification"]["evidence"]
            rv = self.cj(rv_rel)
            row.eq(sha(self.r.bytes(rv_rel)), tc["reference_verification"].get("sha256", sha(self.r.bytes(rv_rel))), "evidence sha")
            files = rv["product"]["files"]
            copy = rv["product"]["copy"]
            for k, want in files.items():
                row.eq(sha(self.r.bytes(copy + "/" + k)), want, f"reference product {k}")
            for n in ("package.json", "package-lock.json", "tsconfig.json"):
                row.eq(files[n], sha(self.r.bytes(TS_CT + "/reference/" + n)), f"reference {n}")
            res = json.dumps(rv, sort_keys=True)
            row.ev(f"evidence keys {sorted(rv)}")
            for gate in ("install", "typecheck", "format_check", "test"):
                row.need(gate in res, f"evidence lacks {gate}")
            row.attest("the reference run's command outcomes are evidence records; they are not re-executed")

        for i, fn in enumerate((a0, a1, a2, a3, a4)):
            self.row("typescript-output-v1", "amendment_001.records", i, recs[i], f"ts.amendment_001.{i}", fn)

    # ================================================== Rust amendment005
    def rust(self):
        a5 = self.rs_plan()["amendment005"]
        ch = a5["checks"]
        P = "rust-output-v1"

        def r0(row):
            self.rt_check(row)

        self.row(P, "amendment005.checks", 0, ch[0], "rust.amendment005.0", r0)

        def r1(row):
            row.absorb(self.find("ts.python_unchanged.3"), "ts.python_unchanged.3")

        self.row(P, "amendment005.checks", 1, ch[1], "rust.amendment005.1", r1)

        def r2(row):
            row.absorb(self.find("ts.python_unchanged.0"), "ts.python_unchanged.0")

        self.row(P, "amendment005.checks", 2, ch[2], "rust.amendment005.2", r2)

        def r3(row):
            row.absorb(self.find("ts.python_unchanged.1"), "ts.python_unchanged.1")

        self.row(P, "amendment005.checks", 3, ch[3], "rust.amendment005.3", r3)

        rb = self.rj("rust-baseline/report.json")
        p1_ids = sorted(c["id"] for c in self.cj(RS_CT + "/case-inventory.json").get("cases", [])) or None
        p2_dir = RUN003_REL + "/phase2/cases"
        p2_files = {}
        for n in self.r.listdir(p2_dir):
            if n.endswith(".json") and not n.endswith(".outcome.json"):
                p2_files[self.r.json(f"{p2_dir}/{n}")["id"]] = n
        p2_ids = sorted(p2_files)
        r3_products = self.r.json(RUN003_REL + "/rust-products.json")
        r3_p1 = {c["id"]: c["files_sha256"] for c in r3_products["cases"]}

        def r3_p2():
            if "r3p2" not in self.cache:
                out = {}
                for i in p2_ids:
                    c = self.r.json(f"{p2_dir}/{p2_files[i]}")
                    out[i] = c.get("rust", {})
                self.cache["r3p2"] = out
            return self.cache["r3p2"]

        def r4(row):
            ce = [x for x in rb["rows"] if x["check"] == "compile_equal"]
            got1 = sorted(x["id"] for x in ce if x["phase"] == "phase1")
            got2 = sorted(x["id"] for x in ce if x["phase"] == "phase2")
            row.eq(got1, sorted(r3_p1), "phase1 ids = run-003 rust-products cases")
            row.eq(got2, p2_ids, "phase2 ids = run-003 phase2 cases")
            refused = {"RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"}
            for x in ce:
                row.eq(x["qa_request_sha256"], x["baseline_request_sha256"], f"{x['id']} request")
                row.eq(x["qa_status"], x["baseline_status"], f"{x['id']} status")
                if x["id"] in refused:
                    row.eq(x["qa_response_sha256"], x["baseline_response_sha256"], f"{x['id']} refusal bytes = baseline")
                    row.eq(x["qa_status"], 400, f"{x['id']} refusal status")
                else:
                    row.eq(x["qa_status"], 200, f"{x['id']} compile status")
            rec = rb.get("records_sha256", {}).get("phase2_case_files", {})
            nrec = 0
            for i in p2_ids:
                if i in rec:
                    row.eq(rec[i], sha(self.r.bytes(f"{p2_dir}/{p2_files[i]}")), f"run-003 phase2 case file {i}")
                    nrec += 1
            row.ev(f"{nrec} run-003 phase2 case files equal the hashes the run recorded")
            row.ev(f"{len(got1)} phase1 + {len(got2)} phase2 compiles: same request and status as the baseline")
            row.attest("descriptor-only response difference (backend, target_contract, payload_sha256) cannot be recomputed: "
                       "response bodies are kept only as hashes")

        self.row(P, "amendment005.checks", 4, ch[4], "rust.amendment005.4", r4)

        def r5(row):
            ru = self.rj("rust-unchanged.json")
            rec_b = self.r.bytes(RUN003_REL + "/rust-products.json")
            row.eq(ru["phase1"]["record"]["sha256"], sha(rec_b), "phase1 record sha")
            row.eq(ru["phase1"]["report"]["sha256"], sha(self.r.bytes(self.R("rust-products.json"))), "phase1 report sha")
            rp = {c["id"]: c["files_sha256"] for c in self.rj("rust-products.json")["cases"]}
            row.eq(sorted(rp), sorted(r3_p1), "phase1 case ids")
            for i, want in r3_p1.items():
                row.eq(rp.get(i), want, f"phase1 {i} files_sha256 vs run-003")
            for c in ru["phase1"]["cases"]:
                row.eq(c["files_sha256"], [r3_p1.get(c["id"])] * 2, f"rust-unchanged phase1 {c['id']}")
            p2 = r3_p2()
            row.eq(sorted(c["id"] for c in ru["phase2"]["cases"]), p2_ids, "phase2 ids")
            n_eq = 0
            for c in ru["phase2"]["cases"]:
                want = p2[c["id"]].get("files_sha256")
                if c["id"] in ru["phase2"]["refusals"]:
                    continue
                if c["files_sha256"] == [want, want] and want:
                    n_eq += 1
                else:
                    row.fail(f"phase2 {c['id']} files_sha256 {short(c['files_sha256'])} vs run-003 {short(want)}")
            row.eq(n_eq, 582, "phase2 byte-identical products")
            row.eq(sorted(ru["phase2"]["refusals"]), ["RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"], "phase2 refusals")
            row.ev(f"phase1 {len(r3_p1)} and phase2 {n_eq} products equal the run-003 files_sha256")
            row.attest("candidate-side files_sha256 values are tool records of the assembled files (product files are not kept)")

        self.row(P, "amendment005.checks", 5, ch[5], "rust.amendment005.5", r5)

        def r6(row):
            ref = [x for x in rb["rows"] if x["check"] == "baseline_bundle_refused"]
            reb = [x for x in rb["rows"] if x["check"] == "rebased_files"]
            row.eq(len(ref), 115 + 582, "refused rows")
            row.eq(len(reb), 115 + 582, "rebased rows")
            for x in ref:
                row.eq(x["qa_status"], 400, f"{x['id']} baseline bundle status")
            p2 = r3_p2()
            for x in reb:
                want = r3_p1.get(x["id"]) if x["phase"] == "phase1" else p2.get(x["id"], {}).get("files_sha256")
                row.eq(x["files_sha256"], want, f"{x['phase']} {x['id']} rebased files vs run-003")
                row.eq(x["qa_status"], 200, f"{x['id']} rebased status")
            row.ev(f"{len(ref)} baseline bundles refused with 400; {len(reb)} rebased bundles give the run-003 files_sha256")
            row.attest("the ASSET_HASH_MISMATCH code of each refusal is a tool record (only response hashes kept)")

        self.row(P, "amendment005.checks", 6, ch[6], "rust.amendment005.6", r6)

        def r7(row):
            plan = self.rs_plan()
            recs = {r["id"]: r for r in plan["assemble_refusals"]["recipes"]}
            got = {a["id"]: a for a in self.rj("rust-products.json")["assemble_refusals"]}
            want_ids = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"] + [f"{i}_PUBLIC_v6" for i in ("A1", "A2", "A4", "A5", "A7", "A9")]
            row.eq(sorted(got), sorted(want_ids), "refusal ids")
            for i in want_ids:
                a = got.get(i)
                if a is None:
                    continue
                base = i.split("_")[0]
                if base == "A6":
                    row.eq((a["status"], a.get("code"), a.get("prompt_pointer")), (400, "INVALID_VALUE", "/bundle/prompt/query/filter_field"), "A6_amendment_004")
                elif base == "A8":
                    row.eq(a["status"], 200, "A8 status")
                    row.need(a.get("files_sha256") and a["files_sha256"] == a.get("renamed_files_sha256"), "A8 files = renamed compile")
                else:
                    r = recs[base]
                    row.eq((a["status"], a.get("code"), a.get("prompt_pointer")), (r["http_status"], r["code"], r["prompt_pointer"]), i)
                row.eq(a.get("base"), "PUBLIC_v6" if i.endswith("_PUBLIC_v6") else "example-v1", f"{i} base")
            row.ev(f"{len(got)} Rust assemble refusals match the phase2 plan")
            row.attest("refusal codes and pointers are recorded fields of the tool report (response bodies not kept)")

        self.row(P, "amendment005.checks", 7, ch[7], "rust.amendment005.7", r7)

        def r8(row):
            self.cli_check(row)

        self.row(P, "amendment005.checks", 8, ch[8], "rust.amendment005.8", r8)

    # ============================================================== extras
    def rust_extras(self):
        P = "rust-output-v1"
        ph = self.rs_plan()["phase2"]

        def a6(row):
            a = {x["id"]: x for x in self.rj("rust-products.json")["assemble_refusals"]}.get("A6")
            row.need(a is not None, "A6 result")
            if a:
                row.eq((a["status"], a.get("code"), a.get("prompt_pointer")), (400, "INVALID_VALUE", "/bundle/prompt/query/filter_field"), "A6")
            row.attest("recorded fields of the tool report; the recipe application is a tool claim")

        self.row(P, "phase2.assemble_refusals", None, ph["assemble_refusals"]["A6_amendment_004"], "rust.phase2.A6_amendment_004", a6)

        def rf(row):
            neg = self.neg_rs()
            for cid in ("RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"):
                row.eq(neg[cid]["expected"]["code"], "INVALID_VALUE", f"{cid} contract code")
            rb = self.rj("rust-baseline/report.json")
            for x in rb["rows"]:
                if x["check"] == "compile_equal" and x["id"] in ("RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"):
                    row.eq(x["qa_status"], 400, f"{x['id']} status")
                    row.eq(x["qa_response_sha256"], x["baseline_response_sha256"], f"{x['id']} bytes = baseline")
                elif x["check"] == "compile_equal":
                    row.need(x["qa_status"] == 200, f"{x['id']} status {x['qa_status']}")
            cli = self.rj("cli.json")
            bad = [x["input"] for x in cli["rows"] if x.get("target") == "rust" and x.get("code") in ("UNSUPPORTED_TARGET", "NOT_IMPLEMENTED", "OUTPUT_QUALITY_FAILED")]
            row.need(not bad, f"CLI rust refusals {bad}")
            rp = self.rj("rust-products.json")
            row.need(not [a for a in rp["assemble_refusals"] if a.get("code") in ("UNSUPPORTED_TARGET", "NOT_IMPLEMENTED", "OUTPUT_QUALITY_FAILED")],
                     "an assemble refusal is UNSUPPORTED_TARGET/NOT_IMPLEMENTED/OUTPUT_QUALITY_FAILED")
            row.ev("all 697 Rust compiles return 200 except RS12/RS13, whose 400 bytes equal the baseline")
            row.attest("the INVALID_VALUE code of RS12/RS13 rests on equality with the baseline's response hash; the body is not kept")

        self.row(P, "phase2", None, ph["refusals_amendment_004"], "rust.phase2.refusals_amendment_004", rf)

        def pu(row):
            row.absorb(self.find("ts.python_unchanged.1"), "ts.python_unchanged.1")
            row.absorb(self.find("ts.python_unchanged.3"), "ts.python_unchanged.3")

        self.row(P, "python_unchanged", None, self.rs_plan()["python_unchanged"]["amendment_004"], "rust.python_unchanged.amendment_004", pu)

    # ======================================================== aggregates
    def aggregates(self):
        tsp = self.ts_plan()

        def ru(row):
            for i in range(9):
                row.absorb(self.find(f"rust.amendment005.{i}"), f"rust.amendment005.{i}")
            row.ev("amendment005 check 9 (independent audit) is absorbed after the audit rows are decided")

        ru_row = self.row("typescript-output-v1", "rust_unchanged", None, tsp["rust_unchanged"]["checks"], "ts.rust_unchanged", ru)

        def ig_contract(row):
            fz = self.cj(TS_FREEZE)
            row.ev(f"freeze status {fz.get('status')}; approval by {fz.get('approval', {}).get('by')}")
            row.absorb(self.find("integrity.ts_chain"), "integrity.ts_chain")
            row.attest("the user's approval and the independent review are receipt records (author reports)")

        def ig_am(row):
            am = self.cj(TS_AM1)
            row.eq(am.get("independent_review", {}).get("final_verdict"), "APPROVED", "review verdict")
            row.eq(am.get("independent_review", {}).get("target_sha256"), TS_TC_SHA, "review target")
            row.absorb(self.find("integrity.ts_chain"), "integrity.ts_chain")
            row.attest("the independent review is a receipt record (author report)")

        def agg(prefixes):
            def fn(row):
                for r in list(self.rows):
                    if r is not row and any(r.id.startswith(p) for p in prefixes):
                        row.absorb(r, r.id)
            return fn

        ig = tsp["issue_gates"]
        self.row("typescript-output-v1", "issue_gates", "phase1-contract", ig["phase1-contract"], "ts.issue_gates.phase1-contract", ig_contract)
        self.row("typescript-output-v1", "issue_gates", "phase1-amendment-001", ig["phase1-amendment-001"], "ts.issue_gates.phase1-amendment-001", ig_am)
        tgt_fn = agg(("ts.target_selection.", "ts.scope.", "ts.python_unchanged.", "ts.rust_unchanged"))
        tgt_row = self.row("typescript-output-v1", "issue_gates", "phase1-target", ig["phase1-target"], "ts.issue_gates.phase1-target", tgt_fn)
        self.row("typescript-output-v1", "issue_gates", "phase1-products", ig["phase1-products"], "ts.issue_gates.phase1-products",
                 agg(("ts.bundle_checks.", "ts.assemble_refusals.", "ts.product_gates.")))

        def run_guard(row):
            h = self.rj("header.json")
            row.eq(h.get("default_before"), h.get("default_after"), "header default_before = default_after (8765 default untouched)")
            row.eq(h.get("candidate_before"), h.get("candidate_after"), "header candidate_before = candidate_after")
            ports = {h.get("baseline_port"), h.get("candidate_before", {}).get("port")}
            row.need(not (ports & {8765, 18765}), f"run ports {sorted(p for p in ports if p)} avoid 8765 and 18765")
            row.ev(f"run ports {sorted(p for p in ports if p)}; candidate binary {h.get('candidate_before', {}).get('binary_sha256', '')[:16]}")

        post_msg = ("the QA-port rehearsal with Python, Rust and TypeScript canaries, the rollback and the user's approved "
                    "switch of the 8765 default happen only after acceptance; not part of an acceptance run")

        def g65_ts(row):
            run_guard(row)
            row.post_acceptance(post_msg)

        self.row("typescript-output-v1", "issue_gates", "#65", ig["#65"], "ts.issue_gates.#65", g65_ts)

        def audit_decide(r, others):
            n_f = sum(1 for x in others if x.status == "failed")
            n_u = sum(1 for x in others if x.status == "unrun")
            bad = [x.id for x in others if x.status in ("failed", "unrun")]
            if bad:
                r.fail(f"this independent audit reports {n_f} failed and {n_u} unrun rows (excluding the audit and #65 rows): {bad[:8]}")
            else:
                r.ev(f"every other row ({len(others)}) is passed, attested or post_acceptance")

        def placeholder(row):
            row.ev("decided after every other row")

        acc_row = self.row("typescript-output-v1", "issue_gates", "phase1-acceptance", ig["phase1-acceptance"], "ts.issue_gates.phase1-acceptance", placeholder)
        a9 = self.row("rust-output-v1", "amendment005.checks", 9, self.rs_plan()["amendment005"]["checks"][9], "rust.amendment005.9", placeholder)
        excl = (acc_row, a9)
        others = [x for x in self.rows if x not in excl and not x.id.endswith("#65")]
        for r in excl:
            r.failed, r.unrun, r.post, r.attested, r.evidence = [], [], [], [], []
            audit_decide(r, others)
        # check 9 completes ts.rust_unchanged, and phase1-target aggregates it
        ru_row.absorb(a9, "rust.amendment005.9")
        tgt_row.failed, tgt_row.unrun, tgt_row.post, tgt_row.attested, tgt_row.evidence = [], [], [], [], []
        tgt_fn(tgt_row)

        def g65_rs(row):
            for i in range(10):
                row.absorb(self.find(f"rust.amendment005.{i}"), f"rust.amendment005.{i}")
            row.absorb(acc_row, "ts.issue_gates.phase1-acceptance")
            h = self.rj("header.json")
            row.ev(f"rust amendment005 checks were run against the same candidate as this TS run "
                   f"(port {h.get('candidate_before', {}).get('port')})")
            run_guard(row)
            row.post_acceptance(post_msg)

        self.row("rust-output-v1", "amendment005.issue_gates", "#65", self.rs_plan()["amendment005"]["issue_gates"]["#65"],
                 "rust.amendment005.issue_gates.#65", g65_rs)

    def run_all(self):
        self.integrity()
        self.python_unchanged()
        self.target_selection()
        self.scope_checks()
        self.bundle_checks()
        self.assemble_refusals()
        self.product_gates()
        self.amendment_001()
        self.rust()
        self.rust_extras()
        self.aggregates()


class Validator:
    """Minimal JSON-schema validator (draft 2020-12 subset); unknown keywords are reported as unsupported."""

    KNOWN = {"$schema", "$id", "$defs", "definitions", "title", "description", "$comment", "type", "properties", "required",
             "additionalProperties", "items", "prefixItems", "enum", "const", "pattern", "minLength", "maxLength", "minItems",
             "maxItems", "minimum", "maximum", "$ref", "oneOf", "anyOf", "allOf", "uniqueItems", "default", "examples",
             "propertyNames", "minProperties", "maxProperties", "patternProperties", "x-order", "format",
             "if", "then", "else", "not"}

    def __init__(self, schema, store=None):
        self.root = schema
        self.cur = schema
        self.store = dict(store or {})
        if isinstance(schema, dict) and "$id" in schema:
            self.store[schema["$id"]] = schema

    def resolve(self, ref):
        base, _, frag = ref.partition("#")
        root = self.cur if base == "" else self.store[base]
        node = root
        for part in frag.split("/"):
            if part:
                node = node[part.replace("~1", "/").replace("~0", "~")]
        return node, root

    def validate(self, inst, s=None, path=""):
        s = self.root if s is None else s
        errs = []
        if s is True or s == {}:
            return errs
        if s is False:
            return [f"{path or '/'} not allowed"]
        for k in s:
            if k not in self.KNOWN and not k.startswith("x-"):
                errs.append(f"unsupported keyword {k} at {path or '/'}")
        if "$ref" in s:
            node, root = self.resolve(s["$ref"])
            saved, self.cur = self.cur, root
            try:
                errs += self.validate(inst, node, path)
            finally:
                self.cur = saved
        if "if" in s:
            if not self.validate(inst, s["if"], path):
                if "then" in s:
                    errs += self.validate(inst, s["then"], path)
            elif "else" in s:
                errs += self.validate(inst, s["else"], path)
        if "not" in s and not self.validate(inst, s["not"], path):
            errs.append(f"{path or '/'} not")
        t = s.get("type")
        if t is not None:
            ts = t if isinstance(t, list) else [t]
            if not any(self._is(inst, x) for x in ts):
                return errs + [f"{path or '/'} type {type(inst).__name__} not {t}"]
        if "const" in s and inst != s["const"]:
            errs.append(f"{path or '/'} const")
        if "enum" in s and inst not in s["enum"]:
            errs.append(f"{path or '/'} enum")
        if isinstance(inst, str):
            if "pattern" in s and not re.search(s["pattern"], inst):
                errs.append(f"{path or '/'} pattern")
            if "minLength" in s and len(inst) < s["minLength"]:
                errs.append(f"{path or '/'} minLength")
            if "maxLength" in s and len(inst) > s["maxLength"]:
                errs.append(f"{path or '/'} maxLength")
        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in s and inst < s["minimum"]:
                errs.append(f"{path or '/'} minimum")
            if "maximum" in s and inst > s["maximum"]:
                errs.append(f"{path or '/'} maximum")
        if isinstance(inst, dict):
            for r in s.get("required", []):
                if r not in inst:
                    errs.append(f"{path}/{r} required")
            props = s.get("properties", {})
            pp = s.get("patternProperties", {})
            for k, v in inst.items():
                matched = False
                if k in props:
                    matched = True
                    errs += self.validate(v, props[k], f"{path}/{k}")
                for pat, sub in pp.items():
                    if re.search(pat, k):
                        matched = True
                        errs += self.validate(v, sub, f"{path}/{k}")
                if not matched and "additionalProperties" in s:
                    ap = s["additionalProperties"]
                    if ap is False:
                        errs.append(f"{path}/{k} additional property")
                    elif isinstance(ap, dict):
                        errs += self.validate(v, ap, f"{path}/{k}")
                if "propertyNames" in s:
                    errs += self.validate(k, s["propertyNames"], f"{path}/{k}(name)")
            if "minProperties" in s and len(inst) < s["minProperties"]:
                errs.append(f"{path} minProperties")
            if "maxProperties" in s and len(inst) > s["maxProperties"]:
                errs.append(f"{path} maxProperties")
        if isinstance(inst, list):
            if "minItems" in s and len(inst) < s["minItems"]:
                errs.append(f"{path} minItems")
            if "maxItems" in s and len(inst) > s["maxItems"]:
                errs.append(f"{path} maxItems")
            pre = s.get("prefixItems", [])
            for i, sub in enumerate(pre[:len(inst)]):
                errs += self.validate(inst[i], sub, f"{path}/{i}")
            if isinstance(s.get("items"), dict):
                for i, v in enumerate(inst[len(pre):], start=len(pre)):
                    errs += self.validate(v, s["items"], f"{path}/{i}")
            if s.get("uniqueItems") and len({C(x) for x in inst}) != len(inst):
                errs.append(f"{path} uniqueItems")
        if "allOf" in s:
            for sub in s["allOf"]:
                errs += self.validate(inst, sub, path)
        if "anyOf" in s:
            if not any(not self.validate(inst, sub, path) for sub in s["anyOf"]):
                errs.append(f"{path or '/'} anyOf")
        if "oneOf" in s:
            n = sum(1 for sub in s["oneOf"] if not self.validate(inst, sub, path))
            if n != 1:
                errs.append(f"{path or '/'} oneOf matched {n}")
        return errs

    @staticmethod
    def _is(x, t):
        return {
            "object": isinstance(x, dict), "array": isinstance(x, list), "string": isinstance(x, str),
            "integer": isinstance(x, int) and not isinstance(x, bool),
            "number": isinstance(x, (int, float)) and not isinstance(x, bool),
            "boolean": isinstance(x, bool), "null": x is None,
        }.get(t, False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))))))
    a = ap.parse_args()
    out = open(a.out, "x", encoding="utf-8")
    au = Audit(a.repo, a.run)
    au.run_all()
    rows = [r.to_json() for r in au.rows]
    totals = collections.Counter(r["status"] for r in rows)
    report = {
        "version": VERSION,
        "run": au.run_rel,
        "plans": {TS_PLAN_REL: sha(au.r.bytes(TS_PLAN_REL)), RS_PLAN_REL: sha(au.r.bytes(RS_PLAN_REL))},
        "totals": {k: totals.get(k, 0) for k in STATUSES} | {"rows": len(rows)},
        "rows": rows,
        "inputs_sha256": dict(sorted(au.r.shas.items())),
    }
    json.dump(report, out, indent=1, ensure_ascii=False, sort_keys=False)
    out.write("\n")
    out.close()
    print(json.dumps(report["totals"]))
    for r in rows:
        if r["status"] != "passed":
            print(f"{r['status']:15} {r['id']}: {r['reason'][:300]}")
    return 0 if totals.get("failed", 0) == 0 and totals.get("unrun", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
