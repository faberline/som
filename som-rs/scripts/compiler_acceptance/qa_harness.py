#!/usr/bin/env python3
"""Independent, scratch-only acceptance runner for SOM compiler-v1.

The runner checks the original preimplementation freeze and its two explicit
amendments. Candidate responses are observations, never acceptance oracles.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import pathlib
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.parse
from typing import Any

sys.dont_write_bytecode = True

CODE_DIR = pathlib.Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path: sys.path.insert(0, str(CODE_DIR))
from paths import default_output_root, find_repo, require_new_output  # noqa: E402
DEFAULT_REPO = find_repo(__file__)
OUTPUT_ROOT = default_output_root(DEFAULT_REPO)
RECEIPT_REL = pathlib.Path("som-rs/evidence/rust-compiler-contract-freeze-2026-10-13.json")
RECEIPT_SHA256 = "2ce6e7027687c7e06ee92a4d6c326fd5f00b6e17f83d5bd80db4e82ebb8cee79"
AMENDMENT_001_REL = pathlib.Path("som-rs/evidence/rust-compiler-contract-amendment-001.json")
AMENDMENT_001_SHA256 = "041b33e72d22833091c69ed6407ba979a1b5320e81214aa939e588032af4a797"
AMENDMENT_002_REL = pathlib.Path("som-rs/evidence/rust-compiler-contract-amendment-002.json")
AMENDMENT_002_SHA256 = "c95b79a89737b2568e7b92529c7b5012c6035bc8e62f830eb010693a4445326d"
CONTRACT_REL = pathlib.Path("som-rs/contracts/compiler-v1")
TESTS_REL = pathlib.Path("som-rs/tests/compiler-v1")


def canonical(value: Any) -> str:
    """Implement the frozen canonical JSON rules for fixture/hash checking."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
        s = repr(value)
        if "e" in s:
            a, b = s.split("e", 1)
            sign = "-" if b.startswith("-") else "+" if b.startswith("+") else ""
            digits = b.lstrip("+-")
            if sign == "-" and len(digits) < 2:
                digits = digits.zfill(2)
            s = a + "e" + sign + digits
        return s
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(x) for x in value) + "]"
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise TypeError("JSON object keys must be strings")
        return "{" + ",".join(canonical(k) + ":" + canonical(value[k]) for k in sorted(value)) + "}"
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def exact_equal(a: Any, b: Any) -> bool:
    """Compare JSON recursively while keeping bool/int and int/float distinct."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(exact_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(exact_equal(x, y) for x, y in zip(a, b))
    if type(a) is float and a == 0.0:
        return math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


ASSET_ROLES = {
    "grammar", "compiler_contract", "selection_policy", "catalog",
    "block_library", "backend", "formatter", "literal_policy",
    "serializer", "semantic_contracts",
}
ASSET_INDEX_VERSION = "som.compiler-asset-index.v1"


def validate_asset_index_document(asset_index: Any) -> list[str]:
    """Require all seven exact per-version role maps before candidate checks."""
    errors: list[str] = []
    if not isinstance(asset_index, dict):
        return ["asset index root must be an object"]
    if set(asset_index) != {"version", "by_prompt_version"}:
        errors.append("asset index must contain exactly version and by_prompt_version")
    if asset_index.get("version") != ASSET_INDEX_VERSION:
        errors.append(f"asset index version must be {ASSET_INDEX_VERSION}")
    by_version = asset_index.get("by_prompt_version")
    wanted_versions = {f"som.prompt.v{version}" for version in range(1, 8)}
    if not isinstance(by_version, dict):
        return errors + ["by_prompt_version must be an object"]
    if set(by_version) != wanted_versions:
        errors.append(f"asset index versions differ: missing={sorted(wanted_versions - set(by_version))} extra={sorted(set(by_version) - wanted_versions)}")
    for version, role_map in by_version.items():
        if not isinstance(role_map, dict):
            errors.append(f"{version}: role map must be an object")
            continue
        if set(role_map) != ASSET_ROLES:
            errors.append(f"{version}: role set differs: missing={sorted(ASSET_ROLES - set(role_map))} extra={sorted(set(role_map) - ASSET_ROLES)}")
        for role, descriptor in role_map.items():
            if role == "semantic_contracts":
                if not isinstance(descriptor, list):
                    errors.append(f"{version}: semantic_contracts must be an array")
                    continue
                if any(not isinstance(row, dict) or set(row) != {"id", "sha256"} for row in descriptor):
                    errors.append(f"{version}: semantic_contracts descriptors must contain exactly id and sha256")
                ids = [row.get("id") for row in descriptor if isinstance(row, dict)]
                if ids != sorted(ids) or len(ids) != len(set(ids)):
                    errors.append(f"{version}: semantic_contracts must be uniquely sorted by id")
                for row in descriptor:
                    if not isinstance(row, dict):
                        continue
                    digest = row.get("sha256")
                    if (not isinstance(row.get("id"), str) or not isinstance(digest, str)
                            or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
                        errors.append(f"{version}: invalid semantic contract descriptor")
            elif not isinstance(descriptor, dict) or set(descriptor) != {"id", "sha256"}:
                errors.append(f"{version}.{role}: descriptor must contain exactly id and sha256")
            elif (not isinstance(descriptor.get("id"), str)
                  or not isinstance(descriptor.get("sha256"), str)
                  or len(descriptor["sha256"]) != 64
                  or any(char not in "0123456789abcdef" for char in descriptor["sha256"])):
                errors.append(f"{version}.{role}: invalid descriptor identity or SHA-256")
    return errors


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def persist_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"preserve existing QA evidence: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def persist_api_exchange(directory: pathlib.Path, label: str, request: Any,
                         status: int, response: Any, raw_response: bytes | None = None) -> None:
    persist_json(directory / f"{label}.request.json", request)
    persist_json(directory / f"{label}.response.json", {
        "http_status": status,
        "body": response,
    })
    if raw_response is not None:
        raw_path=directory/f"{label}.response.raw.b64"
        if raw_path.exists():raise FileExistsError(f"preserve existing QA evidence: {raw_path}")
        raw_path.write_text(base64.b64encode(raw_response).decode("ascii")+"\n",encoding="ascii")


def verify_freeze(repo: pathlib.Path) -> dict[str, Any]:
    def read_receipt(relative: pathlib.Path, expected_sha: str, label: str) -> tuple[dict[str, Any], str]:
        path = repo / relative
        raw = path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != expected_sha:
            raise ValueError(f"{label} receipt SHA mismatch: expected {expected_sha}, got {actual_sha}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{label} receipt root is not an object")
        return value, actual_sha

    initial, initial_sha = read_receipt(RECEIPT_REL, RECEIPT_SHA256, "initial")
    amend1, amend1_sha = read_receipt(AMENDMENT_001_REL, AMENDMENT_001_SHA256, "amendment001")
    amend2, amend2_sha = read_receipt(AMENDMENT_002_REL, AMENDMENT_002_SHA256, "amendment002")

    if initial.get("status") != "contract_frozen_before_implementation" or initial.get("implementation_started_before_freeze") is not False:
        raise ValueError("initial receipt no longer proves the original pre-implementation freeze")
    if initial.get("version") != "som.compiler-contract-freeze.v1":
        raise ValueError("initial receipt version changed")
    if amend1.get("previous_receipt") != str(RECEIPT_REL) or amend1.get("previous_receipt_sha256") != initial_sha:
        raise ValueError("amendment001 does not bind the initial receipt")
    if amend1.get("status") != "contract_amendment_frozen_before_planner_correction":
        raise ValueError("amendment001 status changed")
    if amend2.get("previous_receipt") != str(AMENDMENT_001_REL) or amend2.get("previous_receipt_sha256") != amend1_sha:
        raise ValueError("amendment002 does not bind amendment001")
    if amend2.get("status") != "fixture_precondition_correction_before_acceptance" or amend2.get("implementation_already_in_progress") is not True:
        raise ValueError("amendment002 must remain explicitly post-implementation-start")
    if (initial.get("baseline_commit") != amend1.get("baseline_commit")
            or initial.get("baseline_commit") != amend2.get("baseline_commit")
            or initial.get("issue") != amend1.get("issue")
            or initial.get("issue") != amend2.get("issue")):
        raise ValueError("receipt chain changes baseline commit or issue")

    initial_files = initial.get("files")
    amend1_files = amend1.get("files")
    latest_files = amend2.get("files")
    if not all(isinstance(item, dict) for item in (initial_files, amend1_files, latest_files)):
        raise ValueError("receipt chain is missing file hash maps")
    if not (len(initial_files) == len(amend1_files) == len(latest_files) == 42):
        raise ValueError("expected 42 bound files in each receipt generation")
    if set(initial_files) != set(amend1_files) or set(initial_files) != set(latest_files):
        raise ValueError("receipt generations do not bind the same 42 paths")

    diff_initial_amend1 = {path for path in initial_files if initial_files[path] != amend1_files[path]}
    diff_amend1_amend2 = {path for path in amend1_files if amend1_files[path] != latest_files[path]}
    policy_path = "som-rs/contracts/compiler-v1/selection-policy.json"
    cnl_negative_path = "som-rs/tests/compiler-v1/cnl-negative-cases.json"
    if diff_initial_amend1 != {policy_path}:
        raise ValueError(f"amendment001 changed unexpected frozen paths: {sorted(diff_initial_amend1)}")
    if amend1.get("amendment", {}).get("cases_changed") is not False or amend1.get("amendment", {}).get("quality_gates_changed") is not False:
        raise ValueError("amendment001 must preserve case count and quality gates")
    if diff_amend1_amend2 != {cnl_negative_path} or amend2.get("changed_paths") != [cnl_negative_path]:
        raise ValueError(f"amendment002 changed unexpected frozen paths: {sorted(diff_amend1_amend2)}")
    if amend2.get("cases") != {"positive": 119, "typed": 40, "cnl_negative": 15, "canonical": 11}:
        raise ValueError("amendment002 changed frozen acceptance case counts")
    qualification = initial.get("qualification", {})
    if qualification.get("positive_cases_python_and_rust") != 119 or qualification.get("typed_cases_python_and_rust") != 40 or qualification.get("independent_cnl_refusals_with_reviewed_spans") != 15 or qualification.get("rust_canonical_vectors") != 11:
        raise ValueError("initial qualification counts differ from the amended acceptance counts")

    mismatches = []
    for rel, expected in latest_files.items():
        path = repo / rel
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    if mismatches:
        raise ValueError(f"latest amended contract files differ: {mismatches}")

    def check_archive(path_field: str, hash_field: str, source_path: str, expected_initial_path: str) -> dict[str, Any]:
        relative = amend2.get(path_field) if path_field in amend2 else amend1.get("amendment", {}).get(path_field)
        expected = amend2.get(hash_field) if path_field in amend2 else initial_files.get(expected_initial_path)
        if relative != source_path:
            raise ValueError(f"unexpected historical archive path for {path_field}: {relative}")
        archive = repo / relative
        actual = hashlib.sha256(archive.read_bytes()).hexdigest() if archive.is_file() else "MISSING"
        if actual != expected:
            raise ValueError(f"historical archive hash mismatch for {relative}: {actual}")
        return {"path": relative, "sha256": actual, "matches_original": True}

    policy_archive = check_archive(
        "original_policy_archive", "unused", "som-rs/evidence/rust-compiler-contract-archive/selection-policy-v1.json", policy_path)
    fixture_archive = check_archive(
        "archived_fixture", "archived_fixture_sha256", "som-rs/evidence/rust-compiler-contract-archive/cnl-negative-cases-before-amendment-002.json", cnl_negative_path)

    return {
        "version": "som.compiler-contract-chain-check.v1",
        "status": "passed",
        "initial_preimplementation_freeze": {
            "receipt_sha256": initial_sha,
            "status": initial["status"],
            "implementation_started_before_freeze": False,
            "bound_file_count": 42,
        },
        "amendments": [
            {"id": 1, "receipt_sha256": amend1_sha, "previous_receipt_sha256": initial_sha,
             "status": amend1["status"], "changed_frozen_paths": sorted(diff_initial_amend1),
             "cases_changed": False, "quality_gates_changed": False, "archive": policy_archive},
            {"id": 2, "receipt_sha256": amend2_sha, "previous_receipt_sha256": amend1_sha,
             "status": amend2["status"], "implementation_already_in_progress": True,
             "changed_frozen_paths": sorted(diff_amend1_amend2), "case_counts_unchanged": True,
             "archive": fixture_archive},
        ],
        "latest_bound_file_count": 42,
        "latest_hashes_match": True,
        "preimplementation_claim_basis": "initial receipt only; amendment002 explicitly records implementation already in progress",
    }


class Client:
    """HTTP transport or stable JSONL adapter.

    JSONL adapter line: {"method":"POST","path":"/v1/compile","body":...}
    Response line: {"http_status":200,"body":...}. For raw malformed JSON use
    body_raw instead of body. The adapter must call the same app handlers as HTTP.
    """
    def __init__(self, base_url: str | None, probe: str | None):
        if bool(base_url) == bool(probe):
            raise ValueError("choose exactly one of --http-base or --probe")
        self.base_url = base_url.rstrip("/") if base_url else None
        self.proc = None
        self.last_response_raw: bytes | None = None
        if probe:
            self.proc = subprocess.Popen(shlex.split(probe), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1)
        if self.base_url:
            host=urllib.parse.urlsplit(self.base_url).hostname
            if host not in {"127.0.0.1","localhost","::1"}:
                raise ValueError("HTTP base URL must use a loopback host")

    def call(self, path: str, body: Any = None, body_raw: str | None = None) -> tuple[int, dict[str, Any]]:
        request: dict[str, Any] = {"method": "POST", "path": path}
        if body_raw is not None:
            request["body_raw"] = body_raw
        else:
            request["body"] = body
        if self.base_url:
            data = body_raw.encode("utf-8") if body_raw is not None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            req = urllib.request.Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, fp, code, msg, headers, newurl):
                    return None
            opener = urllib.request.build_opener(NoRedirect)
            try:
                with opener.open(req, timeout=60) as response:
                    raw, status = response.read(), response.status
            except urllib.error.HTTPError as e:
                raw, status = e.read(), e.code
            self.last_response_raw=raw
            return status, self._decoded_body(raw)
        assert self.proc and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise RuntimeError(f"probe ended without response: {stderr[-2000:]}")
        response = json.loads(line)
        if set(response) != {"http_status", "body"}:
            raise ValueError("probe response must contain only http_status and body")
        decoded = response["body"]
        self.last_response_raw=line.encode("utf-8")
        if not isinstance(decoded, dict):
            decoded = {"_non_object_json": decoded}
        return int(response["http_status"]), decoded

    @staticmethod
    def _decoded_body(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"_raw_invalid_json_base64": base64.b64encode(raw).decode("ascii")}
        if not isinstance(value, dict):
            return {"_non_object_json": value}
        return value

    def close(self) -> None:
        if self.proc:
            if self.proc.stdin:
                self.proc.stdin.close()
            rc = self.proc.wait(timeout=15)
            if rc:
                stderr = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"probe exited {rc}: {stderr[-2000:]}")


def error_code(status: int, body: dict[str, Any]) -> str | None:
    return body.get("code") if body.get("status") == "error" else None


def verify_bundle(bundle: Any, expected_prompt: dict[str, Any] | None = None,
                  asset_index: dict[str, Any] | None = None,
                  selection_policy: dict[str, Any] | None = None) -> list[str]:
    problems = []
    if not isinstance(bundle, dict) or set(bundle) != {"version", "prompt", "dsl", "assets", "hashes"}:
        return ["bundle root shape"]
    if expected_prompt is not None and not exact_equal(bundle.get("prompt"), expected_prompt):
        problems.append("typed prompt differs from frozen expected prompt")
    hashes = bundle.get("hashes", {})
    if hashes.get("prompt_sha256") != sha(bundle.get("prompt")):
        problems.append("prompt_sha256 mismatch")
    if hashes.get("dsl_sha256") != sha(bundle.get("dsl")):
        problems.append("dsl_sha256 mismatch")
    payload = {k: bundle[k] for k in ("version", "prompt", "dsl", "assets")}
    if hashes.get("payload_sha256") != sha(payload):
        problems.append("payload_sha256 mismatch")
    if not isinstance(bundle.get("assets"), dict):
        problems.append("assets is not an object")
    elif asset_index is not None:
        index_errors = validate_asset_index_document(asset_index)
        problems.extend(f"asset index: {error}" for error in index_errors)
        prompt = expected_prompt if expected_prompt is not None else bundle.get("prompt")
        version = prompt.get("version") if isinstance(prompt, dict) else None
        expected_roles = asset_index.get("by_prompt_version", {}).get(version) if not index_errors else None
        if not isinstance(expected_roles, dict):
            problems.append(f"no exact asset-index role map for prompt version {version!r}")
        else:
            actual_roles = bundle["assets"]
            if set(actual_roles) != set(expected_roles):
                problems.append(
                    "asset role set differs from independent per-version index: "
                    f"missing={sorted(set(expected_roles) - set(actual_roles))} "
                    f"extra={sorted(set(actual_roles) - set(expected_roles))}")
            for role in sorted(set(actual_roles) & set(expected_roles)):
                if not exact_equal(actual_roles[role], expected_roles[role]):
                    problems.append(f"asset descriptor differs from independent per-version index: {role}")
    if expected_prompt is not None and selection_policy is not None:
        problems.extend(semantic_bundle_checks(bundle, expected_prompt, selection_policy))
    return problems


def semantic_bundle_checks(bundle: dict[str, Any], prompt: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Check family, whole-tree limits, ordered requested routes and frozen v7 node selection."""
    dsl=bundle["dsl"]; version=int(prompt["version"].rsplit("v",1)[1])
    wanted={1:"som.dsl.v1",2:"som.dsl.v1",3:"som.dsl.v2",4:"som.dsl.v2",5:"som.dsl.v2",6:"som.dsl.v2",7:"som.dsl.v3"}[version]
    errors=[]
    if dsl.get("version")!=wanted: errors.append(f"DSL family {dsl.get('version')} != {wanted}")
    files=dsl.get("files",[])
    file_limit=8 if version in (1,2) else 12
    if not 1<=len(files)<=file_limit: errors.append("DSL file count outside frozen family limit")
    block_counts=[len(f.get("blocks",[])) for f in files]
    if any(n>64 for n in block_counts): errors.append("a DSL file exceeds 64 blocks")
    if version in (1,2):
        if sum(block_counts)>64: errors.append("DSL v1 total exceeds frozen 64-block limit")
    elif sum(block_counts)>64:
        errors.append("DSL v2/v3 whole tree exceeds 64 blocks")
    if version in (6,7):
        family = "composed-quality" if version == 6 else "flow-v7-compact"
        catalog_path = DEFAULT_REPO / "som-code-python/data/block-contracts" / f"{family}.json"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"cannot load public {family} block contract: {exc}")
            catalog = {}
        slots = catalog.get("slots", {})
        block_contracts = catalog.get("blocks", {})
        app_slot = slots.get("app") if isinstance(slots, dict) else None
        if not isinstance(app_slot, str) or not isinstance(block_contracts, dict):
            errors.append(f"public {family} contract has no app slot or block metadata")
        else:
            app_paths = {app_slot}
            package = prompt.get("package")
            if isinstance(package, str):
                app_paths.add(app_slot.replace("{package}", package))
            app_files = [f for f in files if isinstance(f, dict) and f.get("path") in app_paths]
            if len(app_files) != 1:
                errors.append("expected one file at the public catalog app slot for ordered operation check")
            else:
                found = []
                for block in app_files[0].get("blocks", []):
                    identity = block.get("id") if isinstance(block, dict) else None
                    meta = block_contracts.get(identity) if isinstance(identity, str) else None
                    if not isinstance(meta, dict):
                        errors.append(f"app-slot block has no public operation metadata: {identity!r}")
                        continue
                    if meta.get("slot") != "app":
                        errors.append(f"block metadata assigns {identity!r} outside the app slot")
                        continue
                    operation = meta.get("operation")
                    if operation is not None:
                        if (not isinstance(operation, list) or len(operation) != 2
                                or not all(isinstance(part, str) for part in operation)):
                            errors.append(f"invalid public operation metadata for {identity!r}")
                        else:
                            found.append(tuple(operation))
                wanted = [(o.get("scope"), o.get("action")) for o in prompt.get("operations", [])]
                if found != wanted:
                    errors.append("DSL requested operations missing, duplicated, or reordered")
    if version==7:
        primary=policy["compact_flow"]["flow_primary_providers"]
        caps=prompt["capabilities"]
        def choose(operation: str, undo: str | None = None) -> str:
            family="neutral" if operation=="if" else "storage" if operation.startswith("object.") else "database"
            if operation=="undo":
                family=current_family[0]
                choice=caps["storage"] if family=="storage" else caps["database"]
            elif family=="storage": choice=caps["storage"]
            elif family=="database": choice=caps["database"]
            else: choice=None
            matches=[r for r in primary if r["operation"]==operation and r.get("inverse")==undo and r["provider_family"]==family and r.get("provider_choice")==choice and all(caps.get(k)==v for k,v in r.get("capabilities",{}).items())]
            if len(matches)!=1: raise ValueError(f"frozen primary selection is ambiguous for {operation}/{undo}/{family}/{choice}")
            return matches[0]["id"]
        def transform(node: dict[str, Any]) -> dict[str, Any]:
            op=node["op"]
            if op=="transaction":
                ident=choose("transaction")
                return {"id":ident,"name":node["name"],"steps":[transform(x) for x in node["steps"]]}
            if op=="if":
                return {"id":choose("if"),"name":node["name"],"condition":copy.deepcopy(node["condition"]),"then":[transform(x) for x in node["then"]],"else":[transform(x) for x in node["else"]],"exports":copy.deepcopy(node["exports"])}
            family="storage" if op.startswith("object.") else "database"
            current_family[:] = [family]
            undo_id=choose("undo",node["undo"]) if node["undo"] is not None else None
            return {"id":choose(op),"name":node["name"],"args":copy.deepcopy(node["args"]),"undo":undo_id}
        current_family=["database"]
        try:
            expected_flow={"name":prompt["flow"]["name"],"steps":[transform(x) for x in prompt["flow"]["steps"]],"result":copy.deepcopy(prompt["flow"]["result"])}
            if not exact_equal(dsl.get("flow"),expected_flow): errors.append("DSL v7 flow differs from frozen typed-node projection and primary selection")
        except Exception as e:
            errors.append(f"could not apply frozen flow selection rules: {e}")
    return errors


def pointer_parts(ptr: str) -> list[str]:
    return [x.replace("~1", "/").replace("~0", "~") for x in ptr.lstrip("/").split("/")] if ptr else []


def patch_json(doc: Any, ops: list[dict[str, Any]]) -> Any:
    doc = copy.deepcopy(doc)
    for op in ops:
        parts = pointer_parts(op["path"]); parent = doc
        for part in parts[:-1]: parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = parts[-1] if parts else None
        if op["op"] == "test":
            cur = parent[int(key)] if isinstance(parent, list) else parent[key]
            if not exact_equal(cur, op["value"]): raise ValueError("JSON Patch test failed")
        elif op["op"] == "replace":
            if isinstance(parent, list): parent[int(key)] = copy.deepcopy(op["value"])
            else:
                if key not in parent: raise KeyError(op["path"])
                parent[key] = copy.deepcopy(op["value"])
        elif op["op"] == "add":
            if isinstance(parent, list):
                if key == "-": parent.append(copy.deepcopy(op["value"]))
                else: parent.insert(int(key), copy.deepcopy(op["value"]))
            else: parent[key] = copy.deepcopy(op["value"])
        elif op["op"] == "remove":
            if isinstance(parent, list): del parent[int(key)]
            else: del parent[key]
        else: raise ValueError(f"unsupported frozen JSON Patch op {op['op']}")
    return doc


def apply_cnl_recipe(base: str, case: dict[str, Any]) -> str:
    lines = base.splitlines()
    if "replace_line" in case:
        lines[case["replace_line"] - 1] = case["with"]
    for edit in case.get("replace_lines", []):
        lines[edit["line"] - 1] = edit["with"]
    if "insert_before_line" in case:
        row = case["insert_before_line"]
        lines.insert(row["line"] - 1, row["text"])
    return "\n".join(lines) + "\n"


def check_diagnostic(source: str, case: dict[str, Any], body: dict[str, Any]) -> list[str]:
    problems = []
    e = case["expected"]
    if body.get("code") != case["code"]:
        problems.append(f"code {body.get('code')} != {case['code']}")
    ds = body.get("diagnostics")
    if not isinstance(ds, list) or len(ds) != 1:
        return problems + ["expected exactly one diagnostic"]
    d = ds[0]
    for k in ("line", "column", "end_line", "end_column", "byte_start", "byte_end", "prompt_pointer"):
        if d.get(k) != e.get(k): problems.append(f"diagnostic {k} {d.get(k)!r} != {e.get(k)!r}")
    if d.get("code") != case["code"]: problems.append("diagnostic code mismatch")
    raw = source.encode("utf-8")
    if raw[e["byte_start"]:e["byte_end"]].decode("utf-8") != case["span_text"]:
        problems.append("frozen byte span no longer selects span_text")
    pre = raw[:e["byte_start"]].decode("utf-8")
    line = pre.count("\n") + 1; col = len(pre.rsplit("\n", 1)[-1]) + 1
    if (line, col) != (e["line"], e["column"]): problems.append("frozen scalar line/column inconsistent with byte span")
    return problems


def canonical_vector_check(vectors: dict[str, Any]) -> dict[str, Any]:
    records=[]
    for v in vectors["vectors"]:
        value=json.loads(v["input_json"])
        got=canonical(value); digest=hashlib.sha256(got.encode()).hexdigest()
        records.append({"id":v["id"],"expected":v["canonical"],"actual":got,"expected_sha256":v["sha256"],"actual_sha256":digest,"passed":got==v["canonical"] and digest==v["sha256"]})
    return {"count":len(records),"passed":all(x["passed"] for x in records),"candidate_endpoint":None,"records":records}


def public_python_validate(repo: pathlib.Path, prompt: dict[str, Any]) -> tuple[bool, str | None]:
    src=str(repo/"som-core/src")
    if src not in sys.path: sys.path.insert(0,src)
    from som_core import v1, related, composed, flow
    version=int(prompt["version"].rsplit("v",1)[1])
    fn={1:v1.validate_prompt,2:v1.validate_prompt,3:v1.validate_prompt,4:v1.validate_prompt,
        5:related.validate,6:composed.validate,7:flow.validate}.get(version,v1.validate_prompt)
    try:
        fn(prompt)
        return True,None
    except Exception as e:
        return False,getattr(e,"code",None) or type(e).__name__


def mutate_bundle(bundle: dict[str, Any], case_id: str, contract_dir: pathlib.Path) -> tuple[dict[str, Any] | None, str | None]:
    b=copy.deepcopy(bundle)
    def recalc():
        b["hashes"]={"prompt_sha256":sha(b["prompt"]),"dsl_sha256":sha(b["dsl"]),"payload_sha256":sha({k:b[k] for k in ("version","prompt","dsl","assets")})}
    if case_id=="extra_bundle_key": b["surprise"]=True
    elif case_id=="missing_prompt": b.pop("prompt",None)
    elif case_id=="prompt_hash": b["prompt"]["package"] += "_tampered"
    elif case_id=="dsl_hash":
        blocks=[x for f in b["dsl"].get("files",[]) for x in f.get("blocks",[])]
        if not blocks:return None,"seed has no blocks"
        blocks[0]["name"] += "x"
    elif case_id=="payload_hash": b["hashes"]["payload_sha256"]="0"*64
    elif case_id in ("asset_hash","unknown_asset_id"):
        for role in sorted(b["assets"]):
            value=b["assets"][role]
            if isinstance(value,dict) and "id" in value and "sha256" in value:
                if case_id=="asset_hash": value["sha256"]="0"*64
                else: value["id"] += ".unknown"
                break
        else:return None,"seed has no simple asset descriptor"
        recalc()
    elif case_id=="unsupported_version": b["version"]="som.dsl-bundle.v999"; recalc()
    elif case_id=="rehashed_wrong_binding":
        # A prompt-binding reference belongs to block params. Runtime input
        # references belong to the flow's meaning and trigger composition checks.
        version=b.get("prompt",{}).get("version")
        if version not in ("som.prompt.v6","som.prompt.v7"):
            return None,"typed binding mutation requires an approved v6/v7 seed"
        catalog_name="flow-v7-compact" if version=="som.prompt.v7" else "composed-quality"
        catalog=load_json(contract_dir.parents[2]/"som-code-python/data/block-contracts"/(catalog_name+".json"))
        found=False
        for file in b["dsl"].get("files",[]):
            for block in file.get("blocks",[]):
                meta=catalog["blocks"].get(block.get("id"))
                if not isinstance(meta,dict) or block.get("params")!=meta.get("bindings"):
                    return None,"seed block params do not match its approved catalog"
                params=block["params"]
                keys=sorted(params,key=lambda key:(key!="name",key))
                for key in keys:
                    ref=params[key]
                    if isinstance(ref,dict) and set(ref)=={"$ref"} and isinstance(ref["$ref"],str) and key in meta.get("params",{}):
                        ref["$ref"]="unknown_binding"; found=True; break
                if found: break
            if found: break
        if not found:return None,"seed DSL has no approved typed params.$ref binding"
        recalc()
    elif case_id in ("rehashed_missing_route","rehashed_reordered_routes"):
        ops=b.get("prompt",{}).get("operations",[])
        if len(ops)<2:return None,"seed prompt lacks two selected operations"
        version=b.get("prompt",{}).get("version")
        if version not in ("som.prompt.v6","som.prompt.v7"):
            return None,"route mutation requires an approved v6/v7 seed"
        catalog_name="flow-v7-compact" if version=="som.prompt.v7" else "composed-quality"
        catalog=load_json(contract_dir.parents[2]/"som-code-python/data/block-contracts"/(catalog_name+".json"))
        app_files=[file for file in b["dsl"].get("files",[]) if file.get("path")==catalog["slots"]["app"]]
        if len(app_files)!=1:return None,"seed must have one approved app file"
        arr=app_files[0].get("blocks",[])
        locations=[]
        for op in ops[:2]:
            hits=[i for i,block in enumerate(arr) if catalog["blocks"].get(block.get("id"),{}).get("operation")==[op.get("scope"),op.get("action")]]
            if len(hits)!=1:return None,"cannot identify one actual catalog block per requested operation"
            locations.append(hits[0])
        if locations[0]==locations[1]:return None,"requested route blocks must be distinct"
        if case_id=="rehashed_missing_route":
            i=locations[0]; removed_name=arr[i]["name"]
            tails=[block for block in arr if catalog["blocks"].get(block.get("id"),{}).get("provides")=="app:tail"]
            if len(tails)!=1:return None,"seed must have one approved router-close block"
            tail=tails[0]
            ref={"symbol":removed_name} if version=="som.prompt.v7" else removed_name
            if tail.get("refs",[]).count(ref)!=1:return None,"router close must reference removed route exactly once"
            if any(block is not tail and block is not arr[i] and ref in block.get("refs",[]) for block in arr):
                return None,"removed route has another remaining dependency; preserve it"
            # Remove only this route and its router-close edge. Keep all other
            # refs unchanged so the failure tests missing operations, not refs.
            tail["refs"]=[item for item in tail["refs"] if item!=ref]
            del arr[i]
        else:
            i,j=locations
            # Whole-block swap keeps each selected block's own bindings/refs.
            arr[i],arr[j]=arr[j],arr[i]
        recalc()
    elif case_id=="rehashed_missing_undo":
        def remove_undo(x):
            if isinstance(x,dict):
                if "undo" in x and x["undo"] is not None: x["undo"]=None; return True
                return any(remove_undo(v) for v in x.values())
            if isinstance(x,list): return any(remove_undo(v) for v in x)
            return False
        if not remove_undo(b["dsl"]):return None,"seed DSL has no nonnull undo"
        recalc()
    else:
        return None,"case needs asset-root mutation or startup restart"
    return b,None


def run(repo: pathlib.Path, client: Client, seed_bundle: pathlib.Path | None, out: pathlib.Path,
        asset_index_path: pathlib.Path | None = None) -> dict[str, Any]:
    contract=repo/CONTRACT_REL; tests=repo/TESTS_REL
    freeze=verify_freeze(repo)
    positives=load_json(tests/"cnl-positive-fixtures.json")
    typed=load_json(tests/"typed-json-fixtures.json")["fixtures"]
    refusals=load_json(tests/"typed-json-oracle-cases.json")["cases"]
    negatives=load_json(tests/"cnl-negative-cases.json")
    vector_data=load_json(contract/"canonical-vectors.json")
    bundle_cases=load_json(contract/"bundle-cases.json")["cases"]
    selection_policy=load_json(contract/"selection-policy.json")
    if asset_index_path is None:
        raise ValueError("--asset-index is required for exact per-version asset identity checks")
    asset_index=load_json(asset_index_path)
    asset_index_errors=validate_asset_index_document(asset_index)
    if asset_index_errors:
        raise ValueError("invalid independent per-version asset index: " + "; ".join(asset_index_errors))
    output_root=default_output_root(repo)
    out=require_new_output(out, output_root)
    response_root=out.parent/(out.stem+".responses")
    if not response_root.is_relative_to(output_root.resolve()) or response_root.exists():
        raise ValueError(f"response evidence must be new and stay under {output_root}: {response_root}")
    response_root.mkdir(parents=True,exist_ok=False)
    report={"freeze":freeze,"candidate":{"mode":"HTTP" if client.base_url else "JSONL probe"},"sections":{},"unrun":[],
            "response_evidence_dir":str(response_root)}
    report["asset_index"]={
        "path":str(asset_index_path),
        "sha256":hashlib.sha256(asset_index_path.read_bytes()).hexdigest(),
        "versions":7,
        "exact_roles_per_version":True,
    }
    report["sections"]["canonical_vectors"]=canonical_vector_check(vector_data)
    report["sections"]["canonical_vectors"]["note"]="These 11 vectors independently qualify the harness hash implementation. The API has no canonicalize endpoint; candidate canonicalization is checked through every returned bundle hash."

    pos=[]
    for case in positives["cases"]:
        p=case["expected_prompt"]
        cnl=case["cnl"]
        evidence_dir=response_root/"positive"/sha(p)
        evidence_dir.mkdir(parents=True,exist_ok=False)
        persist_json(evidence_dir/"prompt.json",p)
        (evidence_dir/"source.cnl").write_text(cnl,encoding="utf-8")
        json_request={"format":"som.prompt-json.v1","prompt":p}
        cnl_request={"format":"som.cnl.v1","source":cnl}
        validator_ok,validator_error=public_python_validate(repo,p)
        json_status,json_body=client.call("/v1/compile",json_request)
        json_raw=client.last_response_raw
        cnl_status,cnl_body=client.call("/v1/compile",cnl_request)
        cnl_raw=client.last_response_raw
        persist_api_exchange(evidence_dir,"compile-json",json_request,json_status,json_body,json_raw)
        persist_api_exchange(evidence_dir,"compile-cnl",cnl_request,cnl_status,cnl_body,cnl_raw)
        if isinstance(json_body.get("bundle"),dict):persist_json(evidence_dir/"compiled-json.bundle.json",json_body["bundle"])
        if isinstance(cnl_body.get("bundle"),dict):persist_json(evidence_dir/"compiled-cnl.bundle.json",cnl_body["bundle"])
        row={"id":case["id"],"json_http":json_status,"cnl_http":cnl_status,"python_validator_passed":validator_ok,"passed":True,"problems":[]}
        row["response_evidence_dir"]=str(evidence_dir)
        if not validator_ok: row["problems"].append(f"public Python validator rejected positive fixture: {validator_error}")
        for label,status,body in (("json",json_status,json_body),("cnl",cnl_status,cnl_body)):
            if status!=200 or body.get("status")!="ok": row["problems"].append(f"{label} compile not ok"); continue
            bundle=body.get("bundle"); problems=verify_bundle(bundle,p,asset_index,selection_policy); row["problems"].extend(f"{label}: {x}" for x in problems)
        if json_body.get("status")=="ok" and cnl_body.get("status")=="ok":
            if canonical(json_body["bundle"])!=canonical(cnl_body["bundle"]): row["problems"].append("CNL/JSON complete bundles differ")
        row["passed"]=not row["problems"]; pos.append(row)
    report["sections"]["positive_cnl_json"]={"count":len(pos),"passed":all(x["passed"] for x in pos),"records":pos}

    rrows=[]
    for case in refusals:
        base=typed[case["fixture"]]
        prompt=patch_json(base,case.get("patch",[]))
        evidence_dir=response_root/"typed-oracle"/f"{len(rrows):03d}"
        evidence_dir.mkdir(parents=True,exist_ok=False)
        persist_json(evidence_dir/"prompt.json",prompt)
        request={"format":"som.prompt-json.v1","prompt":prompt}
        validator_ok,validator_error=public_python_validate(repo,prompt)
        status,body=client.call("/v1/compile",request)
        raw_response=client.last_response_raw
        persist_api_exchange(evidence_dir,"compile-json",request,status,body,raw_response)
        if isinstance(body.get("bundle"),dict):persist_json(evidence_dir/"compiled.bundle.json",body["bundle"])
        expected=case["expected"]
        if expected["status"]=="accept":
            ok=status==200 and body.get("status")=="ok" and validator_ok
        else:
            code=expected.get("code")
            expected_status=422 if code in {"UNSUPPORTED_VERSION","UNSUPPORTED_CONTRACT","UNSUPPORTED_LOGIC","UNSUPPORTED_TEST","UNSUPPORTED_BLOCK","SCOPE_TOO_LARGE"} else 400
            ok=status==expected_status and body.get("status")=="error" and error_code(status,body)==code and not validator_ok
        rrows.append({"id":case["id"],"expected":expected,"http_status":status,"observed":body.get("status"),"code":body.get("code"),"python_validator_passed":validator_ok,"python_validator_error":validator_error,"response_evidence_dir":str(evidence_dir),"passed":ok})
    report["sections"]["typed_oracle"]={"count":len(rrows),"passed":all(x["passed"] for x in rrows),"records":rrows}

    base_cnl=(contract/"examples/v7.cnl").read_text(encoding="utf-8")
    nrows=[]
    if hashlib.sha256(base_cnl.encode()).hexdigest()!=negatives["base_example"]["sha256"]:
        raise ValueError("frozen CNL negative base example hash mismatch")
    for case in negatives["cases"]:
        source=apply_cnl_recipe(base_cnl,case)
        evidence_dir=response_root/"cnl-negative"/f"{len(nrows):03d}"
        evidence_dir.mkdir(parents=True,exist_ok=False)
        (evidence_dir/"source.cnl").write_text(source,encoding="utf-8")
        request={"format":"som.cnl.v1","source":source}
        status,body=client.call("/v1/compile",request)
        raw_response=client.last_response_raw
        persist_api_exchange(evidence_dir,"compile-cnl",request,status,body,raw_response)
        if isinstance(body.get("bundle"),dict):persist_json(evidence_dir/"compiled.bundle.json",body["bundle"])
        problems=check_diagnostic(source,case,body)
        if status!=case.get("http_status"): problems.append(f"HTTP {status} != {case.get('http_status')}")
        nrows.append({"id":case["id"],"http_status":status,"passed":not problems,"problems":problems,"observed_code":body.get("code"),"response_evidence_dir":str(evidence_dir)})
    report["sections"]["cnl_negative"]={"count":len(nrows),"passed":all(x["passed"] for x in nrows),"records":nrows}

    brows=[]
    seed=load_json(seed_bundle) if seed_bundle else None
    if seed is not None:
        persist_json(response_root/"seed-bundle.json",seed)
        known_prompts=[x["expected_prompt"] for x in positives["cases"]]+list(typed.values())
        known=next((p for p in known_prompts if exact_equal(p,seed.get("prompt"))),None)
        seed_problems=verify_bundle(seed,known,asset_index,selection_policy) if known is not None else ["seed prompt is not one of the independent frozen prompts"]
        report["seed_bundle"]={"path":str(seed_bundle),"matched_frozen_prompt":known is not None,"problems":seed_problems}
        if seed_problems:
            report["unrun"].append({"section":"bundle_tampering","reason":"independent seed bundle failed its frozen prompt, hash, asset, or semantic checks"})
            seed=None
    for case_index, case in enumerate(bundle_cases):
        cid=case["id"]
        evidence_dir=response_root/"bundle-tampering"/f"{case_index:03d}-{cid}"
        evidence_dir.mkdir(parents=True,exist_ok=False)
        if cid=="duplicate_json_key":
            raw='{"bundle":{"version":"som.dsl-bundle.v1","prompt":{},"prompt":{},"dsl":{},"assets":{},"hashes":{}}}'
            status,body=client.call("/v1/assemble",body_raw=raw)
            raw_response=client.last_response_raw
            (evidence_dir/"assemble.request.raw.json").write_text(raw,encoding="utf-8")
            persist_api_exchange(evidence_dir,"assemble",{"body_raw":raw},status,body,raw_response)
            expected=case["expected"]; passed=status==400 and body.get("code")==expected
            brows.append({"id":cid,"expected":expected,"http_status":status,"code":body.get("code"),"response_evidence_dir":str(evidence_dir),"passed":passed}); continue
        if cid=="transport_size":
            compile_raw=json.dumps({"format":"som.cnl.v1","source":"x"},separators=(",",":"))
            compile_raw=compile_raw[:-2]+("x"*262200)+"\"}"
            comp_status,comp_body=client.call("/v1/compile",body_raw=compile_raw)
            comp_raw=client.last_response_raw
            assemble_raw='{"bundle":{}}'+(' '*2097152)
            ass_status,ass_body=client.call("/v1/assemble",body_raw=assemble_raw)
            ass_raw=client.last_response_raw
            (evidence_dir/"compile.request.raw.json").write_text(compile_raw,encoding="utf-8")
            (evidence_dir/"assemble.request.raw.json").write_text(assemble_raw,encoding="utf-8")
            persist_api_exchange(evidence_dir,"compile",{"body_raw":"<see compile.request.raw.json>"},comp_status,comp_body,comp_raw)
            persist_api_exchange(evidence_dir,"assemble",{"body_raw":"<see assemble.request.raw.json>"},ass_status,ass_body,ass_raw)
            passed=comp_status==413 and ass_status==413
            brows.append({"id":cid,"expected":"413 for compile and assemble","compile_http":comp_status,"assemble_http":ass_status,"response_evidence_dir":str(evidence_dir),"passed":passed}); continue
        if cid in ("local_asset_tamper","self_signed_asset_tamper","missing_asset","structural_limits","large_valid_compact_output","bundle_saved_replay","equivalent_cnl_json","all_versions","checked_variant"):
            report["unrun"].append({"section":"bundle_tampering","case":cid,"reason":"requires an isolated asset/startup fixture, a boundary-specific valid fixture, or multiple independently pinned bundles"})
            continue
        if seed is None:
            persist_json(evidence_dir/"unrun.json",{"reason":"requires independently supplied valid seed bundle"})
            report["unrun"].append({"section":"bundle_tampering","case":cid,"reason":"requires independently supplied valid seed bundle; candidate compile output is not used as expected input"})
            continue
        mutated,reason=mutate_bundle(seed,cid,contract)
        if reason:
            persist_json(evidence_dir/"unrun.json",{"reason":reason})
            report["unrun"].append({"section":"bundle_tampering","case":cid,"reason":reason}); continue
        request={"bundle":mutated}
        status,body=client.call("/v1/assemble",request)
        raw_response=client.last_response_raw
        persist_api_exchange(evidence_dir,"assemble",request,status,body,raw_response)
        expected=case["expected"]
        allowed=expected.split(" or ") if " or " in expected else [expected]
        passed=status!=200 and body.get("status")=="error" and body.get("code") in allowed
        brows.append({"id":cid,"expected":expected,"http_status":status,"code":body.get("code"),"response_evidence_dir":str(evidence_dir),"passed":passed})
    report["sections"]["bundle_tampering"]={"count":len(brows),"passed":all(x["passed"] for x in brows) if brows else None,"records":brows}
    report["completed"]=(report["sections"]["canonical_vectors"]["passed"] and all(x.get("passed") is True for x in report["sections"].values() if isinstance(x,dict) and "passed" in x) and not report["unrun"])
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",type=pathlib.Path,default=DEFAULT_REPO)
    ap.add_argument("--http-base")
    ap.add_argument("--probe",help="command implementing the documented persistent JSONL adapter protocol")
    ap.add_argument("--seed-bundle",type=pathlib.Path,help="independent valid bundle input for bundle tampering only; never an expected output")
    ap.add_argument("--asset-index",type=pathlib.Path,help="independently generated som.compiler-asset-index.v1 with exact by_prompt_version role maps")
    ap.add_argument("--out",type=pathlib.Path)
    ap.add_argument("--verify-freeze-only",action="store_true")
    args=ap.parse_args()
    if args.out is None: args.out=default_output_root(args.repo)/"qa/latest-report.json"
    if args.verify_freeze_only:
        print(json.dumps(verify_freeze(args.repo),indent=2)); return
    client=Client(args.http_base,args.probe)
    try:
        report=run(args.repo,client,args.seed_bundle,args.out,args.asset_index)
    finally:
        client.close()
    print(json.dumps({"completed":report["completed"],"out":str(args.out),"unrun":len(report["unrun"])},indent=2))
    raise SystemExit(0 if report["completed"] else 2)


if __name__=="__main__":
    main()
