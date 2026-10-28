#!/usr/bin/env python3
"""Audit a React acceptance run from frozen contracts and retained raw evidence.

No acceptance tool is imported, parsed or run. Tool files are hashed only.
Aggregate verdicts are ignored. Operational checks require retained raw bytes.
Only human review and approval metadata can be attested. Missing evidence is
unrun. A clean committed checkout is supplied by the caller; this audit checks
its bytes against the run identity, not the internal Git object database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_rules import render_files  # noqa: E402
from refusal_rules import request_for  # noqa: E402
from api_raw import validate as validate_api, ApiRawError  # noqa: E402
from baseline_raw import validate_python, validate_rust, validate_controls, verify_location, BaselineRawError  # noqa: E402


VERSION = "som.react-output-audit-130.v1"
CONTRACT = "som-rs/contracts/react-output-v1"
EVIDENCE = "som-rs/evidence"
TOOLS = "som-rs/scripts/react_output_acceptance"
STEPS = (
    "contract", "react-target", "react-python-controls", "python-baseline",
    "rust-baseline", "rust-python-controls", "rt-diagnostics", "ts-target",
    "ts-baseline", "ts-python-controls", "bundles", "products",
)
STATUSES = ("passed", "attested", "post_acceptance", "unrun", "failed")
HEX = re.compile(r"[0-9a-f]{64}\Z")
SHARED = ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer", "semantic_contracts")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceError(ValueError):
    def __init__(self, message: str, status: str = "failed"):
        super().__init__(message)
        self.status = status


def need(condition: Any, message: str, status: str = "failed") -> None:
    if not condition:
        raise EvidenceError(message, status)


def object_value(value: Any, label: str) -> dict:
    need(isinstance(value, dict), f"{label} is not an object")
    return value


def list_value(value: Any, label: str) -> list:
    need(isinstance(value, list), f"{label} is not an array")
    return value


def true_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


class Reader:
    """All content reads go through this recorder, including hash-only reads."""
    def __init__(self, repo: Path):
        self.repo = repo.resolve(strict=True)
        self.files: dict[str, str] = {}
        self.missing: set[str] = set()
        self.accesses: list[str] = []

    def path(self, value: str | Path, base: Path | None = None) -> Path:
        need(isinstance(value, (str, Path)), "evidence path is missing or invalid")
        path = Path(value)
        need(".." not in path.parts, f"path traversal is refused: {value}")
        if not path.is_absolute():
            path = (base or self.repo) / path
        try:
            relative = path.relative_to(self.repo)
        except ValueError as exc:
            raise EvidenceError(f"evidence escapes repository: {value}") from exc
        current = self.repo
        for component in relative.parts:
            current /= component
            need(not current.is_symlink(), f"symlink evidence is refused: {value}")
        return path

    def bytes(self, value: str | Path, base: Path | None = None, *, hash_only: bool = False) -> bytes:
        path = self.path(value, base)
        rel = path.relative_to(self.repo).as_posix()
        if not hash_only:
            need(path.suffix != ".py", f"acceptance tool may only be hashed: {rel}")
        if not path.is_file():
            self.missing.add(rel)
            raise EvidenceError(f"missing raw file: {rel}", "unrun")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise EvidenceError(f"cannot read {rel}: {exc.__class__.__name__}") from exc
        value = sha(raw)
        need(rel not in self.files or self.files[rel] == value, f"file changed while auditing: {rel}")
        self.files[rel] = value
        self.accesses.append(rel)
        return raw

    def note(self, value: str | Path) -> None:
        self.accesses.append(self.path(value).relative_to(self.repo).as_posix())

    def hash(self, value: str | Path, base: Path | None = None) -> str:
        return sha(self.bytes(value, base, hash_only=True))

    def json(self, value: str | Path, base: Path | None = None) -> Any:
        raw = self.bytes(value, base)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EvidenceError(f"invalid JSON: {value}") from exc

    def tree(self, value: str | Path, *, exclude_modules: bool = False) -> dict[str, str]:
        root = self.path(value)
        need(root.is_dir(), f"missing raw directory: {value}", "unrun")
        result = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if exclude_modules and "node_modules" in relative.parts:
                continue
            self.path(path)
            if path.is_file():
                result[relative.as_posix()] = self.hash(path)
        return result


class Schema:
    """The structural JSON Schema vocabulary used by the frozen local schemas."""
    def __init__(self, reader: Reader, documents: list[dict]):
        self.reader = reader
        self.documents = {doc["$id"]: doc for doc in documents if isinstance(doc.get("$id"), str)}

    def validate(self, value: Any, schema: Any, root: dict | None = None, depth: int = 0) -> None:
        need(depth < 100, "schema nesting exceeds audit limit")
        if schema is True:
            return
        need(schema is not False, "schema rejects the value")
        schema = object_value(schema, "schema")
        root = root or schema
        supported = {
            "$schema", "$id", "$ref", "$defs", "$anchor", "$comment", "title", "description",
            "default", "examples", "deprecated", "readOnly", "writeOnly", "format", "contentEncoding", "contentMediaType",
            "type", "const", "enum", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
            "properties", "patternProperties", "propertyNames", "required", "additionalProperties",
            "minProperties", "maxProperties", "dependentRequired", "dependentSchemas",
            "items", "prefixItems", "minItems", "maxItems", "uniqueItems", "contains", "minContains", "maxContains",
            "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        }
        unknown = [key for key in schema if key not in supported and not key.startswith("x-")]
        need(not unknown, f"unsupported schema assertion keywords: {unknown}", "unrun")
        ref = schema.get("$ref")
        if ref is not None:
            need(isinstance(ref, str), "invalid schema reference")
            name, _, pointer = ref.partition("#")
            selected = root if not name else self.documents.get(name)
            need(selected is not None, f"unavailable local schema reference: {ref}", "unrun")
            target = selected
            if pointer:
                for key in pointer.lstrip("/").split("/"):
                    target = target[key.replace("~1", "/").replace("~0", "~")]
            self.validate(value, target, selected, depth + 1)
        if "const" in schema:
            need(canonical(value) == canonical(schema["const"]), "schema const differs")
        if "enum" in schema:
            need(canonical(value) in [canonical(item) for item in schema["enum"]], "value is outside schema enum")
        kinds = schema.get("type")
        if kinds is not None:
            kinds = kinds if isinstance(kinds, list) else [kinds]
            matches = {
                "object": isinstance(value, dict), "array": isinstance(value, list),
                "string": isinstance(value, str), "integer": true_integer(value),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "boolean": isinstance(value, bool), "null": value is None,
            }
            need(any(matches.get(kind, False) for kind in kinds), "schema type differs")
        for keyword in ("allOf", "anyOf", "oneOf"):
            if keyword not in schema:
                continue
            outcomes = []
            for branch in schema[keyword]:
                try:
                    self.validate(value, branch, root, depth + 1)
                    outcomes.append(True)
                except EvidenceError as exc:
                    if exc.status == "unrun":
                        raise
                    outcomes.append(False)
            need(all(outcomes) if keyword == "allOf" else sum(outcomes) == 1 if keyword == "oneOf" else any(outcomes), f"schema {keyword} differs")
        if "not" in schema:
            try:
                self.validate(value, schema["not"], root, depth + 1)
            except EvidenceError as exc:
                if exc.status == "unrun":
                    raise
            else:
                raise EvidenceError("schema not rejects the value")
        if "if" in schema:
            condition = True
            try:
                self.validate(value, schema["if"], root, depth + 1)
            except EvidenceError as exc:
                if exc.status == "unrun":
                    raise
                condition = False
            branch = "then" if condition else "else"
            if branch in schema:
                self.validate(value, schema[branch], root, depth + 1)
        if isinstance(value, dict):
            need(all(key in value for key in schema.get("required", [])), "schema required member is missing")
            need(len(value) >= schema.get("minProperties", 0), "schema minProperties differs")
            need(len(value) <= schema.get("maxProperties", len(value)), "schema maxProperties differs")
            properties = schema.get("properties", {})
            for key, item in value.items():
                if "propertyNames" in schema:
                    self.validate(key, schema["propertyNames"], root, depth + 1)
                matched = False
                if key in properties:
                    self.validate(item, properties[key], root, depth + 1)
                    matched = True
                for pattern, property_schema in schema.get("patternProperties", {}).items():
                    if re.search(pattern, key):
                        self.validate(item, property_schema, root, depth + 1)
                        matched = True
                if not matched:
                    extra = schema.get("additionalProperties", True)
                    if extra is not True:
                        self.validate(item, extra, root, depth + 1)
            for key, required in schema.get("dependentRequired", {}).items():
                if key in value:
                    need(all(member in value for member in required), "schema dependentRequired differs")
            for key, dependent in schema.get("dependentSchemas", {}).items():
                if key in value:
                    self.validate(value, dependent, root, depth + 1)
        if isinstance(value, list):
            need(len(value) >= schema.get("minItems", 0), "schema minItems differs")
            need(len(value) <= schema.get("maxItems", len(value)), "schema maxItems differs")
            if schema.get("uniqueItems"):
                need(len({canonical(item) for item in value}) == len(value), "schema uniqueItems differs")
            prefixes = schema.get("prefixItems", [])
            for index, item in enumerate(value):
                if index < len(prefixes):
                    self.validate(item, prefixes[index], root, depth + 1)
                    continue
                if "items" in schema:
                    self.validate(item, schema["items"], root, depth + 1)
            if "contains" in schema:
                count = 0
                for item in value:
                    try:
                        self.validate(item, schema["contains"], root, depth + 1)
                        count += 1
                    except EvidenceError as exc:
                        if exc.status == "unrun":
                            raise
                need(schema.get("minContains", 1) <= count <= schema.get("maxContains", len(value)), "schema contains count differs")
        if isinstance(value, str):
            need(len(value) >= schema.get("minLength", 0), "schema minLength differs")
            need(len(value) <= schema.get("maxLength", len(value)), "schema maxLength differs")
            if "pattern" in schema:
                need(re.search(schema["pattern"], value) is not None, "schema pattern differs")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            for key, compare in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b), ("exclusiveMinimum", lambda a, b: a > b), ("exclusiveMaximum", lambda a, b: a < b)):
                if key in schema:
                    need(compare(value, schema[key]), f"schema {key} differs")


class Audit:
    def __init__(self, repo: Path, run: Path):
        self.reader = Reader(repo)
        self.repo = self.reader.repo
        self.run = self.reader.path(run)
        self.rows: list[dict] = []
        self.reports: dict[str, dict] = {}
        self.contracts: dict[str, dict] = {}
        self.inventory: dict = {}
        self.acceptance: list[dict] = []
        self.manifest: dict[str, dict] = {}
        self.latest_receipt: dict = {}
        self.attestations: list[dict] = []
        self.active_report = ""
        self.baseline_requests: dict[tuple[str, str, str], str] = {}
        self.ts_baseline_inventory: dict = {}
        self.ts_baseline_cases: dict[str, tuple[str, dict]] = {}

    def row(self, row_id: str, action: Callable[[], Any], *, description: str = "") -> dict:
        before = len(self.reader.accesses)
        attestation_start = len(self.attestations)
        try:
            result = action()
            status, reason = result if isinstance(result, tuple) else ("passed", "raw values match")
        except EvidenceError as exc:
            status, reason = exc.status, str(exc)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, re.error) as exc:
            status, reason = "failed", f"malformed raw evidence: {exc.__class__.__name__}: {exc}"
        need(status in STATUSES, "invalid audit row status")
        item = {"id": row_id, "status": status, "reason": reason, "evidence": sorted(set(self.reader.accesses[before:]))}
        if len(self.attestations) > attestation_start:
            item["attestations"] = self.attestations[attestation_start:]
        if description:
            item["check"] = description
        self.rows.append(item)
        return item

    def combine(self, prefix: str) -> tuple[str, str]:
        selected = [row for row in self.rows if row["id"].startswith(prefix)]
        need(selected, f"no raw results for {prefix}", "unrun")
        for status in ("failed", "unrun", "attested"):
            if any(row["status"] == status for row in selected):
                return status, f"derived from {len(selected)} named evidence rows; see {prefix}"
        return "passed", f"derived from {len(selected)} named evidence rows"

    def read_contracts(self) -> None:
        for name in ("acceptance-plan.json", "case-inventory.json", "negative-cases.json", "target-examples.json", "api-contract.json", "scope.json", "toolchain.json", "product-layout.json", "bundle.schema.json", "api.schema.json", "catalog/react-catalog.v1.json"):
            self.contracts[name] = object_value(self.reader.json(f"{CONTRACT}/{name}"), name)
        self.inventory = self.contracts["case-inventory.json"]
        for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases", "public_products_in_scope", "legacy_in_scope"):
            for case in list_value(self.inventory.get(group), f"inventory {group}"):
                case = object_value(case, "inventory case")
                if group in ("public_examples", "version_positive_fixtures", "coverage_cases") and case.get("react") != "in_scope":
                    continue
                need(isinstance(case.get("id"), str) and case["id"], "inventory case has no id")
                need(re.fullmatch(r"[A-Za-z0-9_.-]+", case["id"]) and case["id"] not in (".", ".."), "unsafe inventory case id")
                self.acceptance.append({**case, "group": group})
        ids = [case["id"] for case in self.acceptance]
        need(ids and len(set(ids)) == len(ids), "acceptance inventory is empty or repeats an id")

    def get_report(self, step: str) -> dict:
        filename = f"{step}.json" if step.endswith("python-controls") else f"{step}/report.json"
        self.active_report = (self.run / filename).relative_to(self.repo).as_posix()
        if step not in self.reports:
            self.reports[step] = object_value(self.reader.json(self.run / filename), f"{step} report")
        else:
            self.reader.note(self.run / filename)
        return self.reports[step]

    def cases(self, step: str, key: str = "cases") -> list[dict]:
        rows = list_value(self.get_report(step).get(key), f"{step} {key}")
        need(all(isinstance(row, dict) for row in rows), f"malformed {step} case row")
        return rows

    def response(self, step: str, row: dict) -> tuple[bytes, dict]:
        need(row.get("response_file") is not None, "missing raw response file", "unrun")
        path = self.reader.path(row["response_file"], self.run / step)
        need(path.is_relative_to(self.run / step), "raw response is outside this step of the recorded run")
        raw = self.reader.bytes(path)
        need(row.get("response_sha256") == sha(raw), "raw response sha256 differs")
        try:
            doc = object_value(json.loads(raw), "raw HTTP response")
        except (ValueError, UnicodeDecodeError) as exc:
            raise EvidenceError("invalid JSON in raw HTTP response") from exc
        return raw, doc

    def find(self, step: str, case_id: str, section: str | None = None) -> dict:
        matches = [row for row in self.cases(step) if row.get("id") == case_id and (section is None or row.get("section") == section)]
        need(matches, f"missing named case {step}/{case_id}", "unrun")
        need(len(matches) == 1, f"duplicate named case {step}/{case_id}")
        return matches[0]

    def identities(self) -> None:
        identity = object_value(self.reader.json(self.run / "identity.json"), "identity")
        for role in ("candidate", "baseline", "8765"):
            def health(role: str = role) -> None:
                data = object_value(identity.get(role), role)
                pair = object_value(data.get("healthz"), f"{role} healthz")
                before, after = object_value(pair.get("before"), "health before"), object_value(pair.get("after"), "health after")
                if role == "8765" and "expected_state" in data:
                    need(data["expected_state"] == "absent", "8765 expected state is invalid")
                    base_url = "http://127.0.0.1:8765"
                    health_url = f"{base_url}/healthz"
                    if "url" in data:
                        need(data["url"] == base_url, "8765 protected URL differs")
                    urls = []
                    for point in (before, after):
                        url = point.get("url", health_url if data.get("url") == base_url else None)
                        need(url == health_url, "8765 absent health URL differs")
                        urls.append(url)
                        need(point.get("error_kind") == "connection_refused", "8765 absence has no connection refusal")
                        number = point.get("error_errno")
                        need(true_integer(number) and number in (61, 111), "8765 absence has no ECONNREFUSED error number")
                        need(point.get("error_type") in ("ConnectionRefusedError", "OSError"), "8765 absence error type differs")
                        need(isinstance(point.get("error"), str) and point["error"].strip(), "8765 absence error is missing")
                        need(all(key in point and point[key] is None for key in ("status", "body", "raw", "sha256")), "8765 absent health snapshot contains an HTTP response")
                    need(urls[0] == urls[1], "8765 absent health URL changed")
                    return
                for point in (before, after):
                    need(point.get("status") == 200, f"{role} health HTTP status is not 200")
                    need(isinstance(point.get("raw"), str), f"{role} raw health body missing", "unrun")
                    need(sha(point["raw"].encode()) == point.get("sha256"), f"{role} raw health body hash differs")
                need(before["raw"] == after["raw"] and before["sha256"] == after["sha256"], f"{role} health body changed")
            self.row(f"integrity/health/{role}", health)
        def binary() -> None:
            candidate = object_value(identity.get("candidate"), "candidate identity")
            value = candidate.get("binary_sha256")
            need(isinstance(value, str) and HEX.fullmatch(value), "candidate binary hash is missing")
            need(value == candidate.get("binary_sha256_after"), "candidate binary changed")
            if "dir" in candidate:
                directory = self.reader.path(candidate["dir"])
                need(self.reader.hash(directory / "som") == value, "current candidate binary hash differs")
                for key in ("build", "runtime"):
                    doc = object_value(self.reader.json(directory / f"{key}.json"), f"candidate {key}")
                    need(self.reader.hash(directory / f"{key}.json") == candidate.get(f"{key}_sha256"), f"candidate {key} metadata hash differs")
                    need(doc == candidate.get(key) and doc.get("binary_sha256") == value, f"candidate {key} metadata changed")
        self.row("integrity/candidate", binary)
        def git_identity() -> None:
            head = object_value(identity.get("git_head"), "git head identity")
            need(isinstance(head.get("before"), str) and re.fullmatch(r"[0-9a-f]{40}", head["before"]), "missing full HEAD identity")
            need(head["before"] == head.get("after"), "HEAD identity changed")
        self.row("integrity/source-head", git_identity)
        def tool_identity() -> None:
            maps = object_value(identity.get("react_output_acceptance_files"), "tool identity")
            before, after = object_value(maps.get("before"), "tools before"), object_value(maps.get("after"), "tools after")
            need(before and before == after, "tool maps are empty or changed")
            root = self.reader.path(TOOLS)
            actual = {}
            for path in sorted(root.rglob("*")):
                self.reader.path(path)
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    actual[path.relative_to(self.repo).as_posix()] = self.reader.hash(path)
            need(before == actual, "tool file set or source hashes differ from recorded identity")
        self.row("integrity/tools", tool_identity)

    def receipts(self) -> None:
        run_doc = object_value(self.reader.json(self.run / "run.json"), "run record")
        def identity_binding() -> None:
            path = self.reader.path(run_doc.get("identity"), self.run)
            need(path == self.run / "identity.json", "run identity path differs from this recorded run")
            need(self.reader.hash(path) == run_doc.get("identity_sha256"), "run identity sha256 differs")
        self.row("integrity/identity-binding", identity_binding)
        steps = list_value(run_doc.get("steps"), "run steps")
        self.row("integrity/step-order", lambda: need([step.get("step") for step in steps] == list(STEPS), "step order differs from the 12 required steps"))
        for index, name in enumerate(STEPS, 1):
            def check(index: int = index, name: str = name) -> None:
                matches = [step for step in steps if isinstance(step, dict) and step.get("step") == name]
                need(len(matches) == 1, f"missing or repeated step {name}", "unrun" if not matches else "failed")
                step = matches[0]
                receipt_path = self.reader.path(step.get("receipt"), self.run)
                need(receipt_path == self.run / "receipts" / f"{index:02d}-{name}.json", f"{name} receipt is outside the recorded step")
                need(self.reader.hash(receipt_path) == step.get("receipt_sha256"), f"{name} receipt hash differs")
                receipt = object_value(self.reader.json(receipt_path), f"{name} receipt")
                need(receipt.get("step") == name and receipt.get("index") == index, f"{name} receipt identity differs")
                need(isinstance(receipt.get("argv"), list) and receipt["argv"], f"{name} argv missing")
                need(self.reader.path(receipt.get("cwd")) == self.repo, f"{name} cwd differs")
                for field in ("stdout", "stderr", "report"):
                    path = self.reader.path(receipt.get(field), self.run)
                    expected = self.run / "logs" / f"{index:02d}-{name}.{field}" if field != "report" else self.run / "logs/01-contract.stdout" if name == "contract" else self.run / (f"{name}.json" if name.endswith("python-controls") else f"{name}/report.json")
                    need(path == expected, f"{name} {field} path is outside the recorded step")
                    need(self.reader.hash(path) == receipt.get(f"{field}_sha256"), f"{name} {field} hash differs")
                if "report_sha256" in step:
                    need(step["report_sha256"] == receipt.get("report_sha256"), f"{name} run/receipt report hash differs")
                need(true_integer(receipt.get("exit_code")), f"{name} did not run", "unrun")
                need(receipt["exit_code"] == 0, f"{name} process exited {receipt['exit_code']}")
            self.row(f"integrity/receipt/{name}", check)

    def sealed_contract(self) -> tuple[str, str]:
        folder = self.reader.path(EVIDENCE)
        freeze = folder / "react-output-contract-freeze-2026-10-22.json"
        receipts = [freeze] + sorted(folder.glob("react-output-contract-amendment-*.json"))
        previous = None
        for path in receipts:
            doc = object_value(self.reader.json(path), "sealed receipt")
            expected_status = "contract_frozen_before_implementation" if path == freeze else "amendment_sealed_after_independent_review"
            need(doc.get("status") == expected_status, f"contract receipt is not sealed: {path.name}")
            files = object_value(doc.get("files"), "sealed receipt files")
            need(files, "sealed receipt files map is empty")
            review = doc.get("independent_review")
            need(isinstance(review, dict) and review, "independent contract review is missing", "unrun")
            if previous is not None:
                need(doc.get("previous_receipt_sha256") == self.reader.hash(previous), "amendment receipt chain differs")
            previous = path
            self.latest_receipt = doc
        for path, expected in self.latest_receipt["files"].items():
            need(self.reader.hash(path) == expected, f"sealed contract file hash differs: {path}")
        return "attested", "sealed file hashes and receipt chain match; human approval and independent review are recorded attestations"

    def diagnostic(self, row: dict, expected: dict, status: int, step: str, body: str | None = None) -> None:
        _, doc = self.response(step, row)
        need(row.get("http_status") == status, "HTTP status differs from frozen diagnostic")
        need(doc.get("code") == expected.get("code"), "raw diagnostic code differs")
        diagnostics = list_value(doc.get("diagnostics"), "diagnostics")
        need(len(diagnostics) == 1, "expected exactly one diagnostic")
        actual = object_value(diagnostics[0], "diagnostic")
        for key, value in expected.items():
            need(actual.get(key) == value, f"raw diagnostic {key} differs")
        if "cause" in expected:
            need(doc.get("detail") == expected["cause"], "raw diagnostic detail differs")
        if body is not None:
            need(row.get("request_sha256") == sha(body.encode()), "request hash differs from frozen request")
            source = body
            request = json.loads(body)
            if request.get("format") == "som.cnl.v1" and actual.get("prompt_pointer", "") not in ("", "/target"):
                source = request["source"]
                need(isinstance(source, str), "CNL source missing from request")
            start, end = actual.get("byte_start"), actual.get("byte_end")
            need(true_integer(start) and true_integer(end) and 0 <= start <= end <= len(source.encode()), "diagnostic byte span is invalid")
            # UTF-8 boundaries and line/column coordinates are independently checked.
            prefix = source.encode()[:start].decode()
            suffix = source.encode()[:end].decode()
            for name, text in (("", prefix), ("end_", suffix)):
                line = text.count("\n") + 1
                column = len(text.rsplit("\n", 1)[-1]) + 1
                need(actual.get(f"{name}line") == line and actual.get(f"{name}column") == column, "diagnostic line/column span differs")

    def target_cases(self) -> None:
        targets = self.contracts["target-examples.json"]
        for section in ("examples", "name_cases"):
            report_section = "target_examples" if section == "examples" else section
            for case in targets.get(section, []):
                def check(case: dict = case, report_section: str = report_section) -> None:
                    row = self.find("react-target", case["id"], report_section)
                    _, response = self.response("react-target", row)
                    body = json.loads(case["body"])
                    need(row.get("request_sha256") == sha(case["body"].encode()), "target request hash differs")
                    if isinstance(case.get("expected"), dict):
                        self.diagnostic(row, case["expected"], case["http_status"], "react-target", case["body"])
                    else:
                        need(row.get("http_status") == 200, "target example HTTP status is not 200")
                        bundle = object_value(response.get("bundle"), "target example bundle")
                        target = body.get("target", "python")
                        need(bundle.get("target", "python") == target, "raw target example target differs")
                        need(bundle.get("version") == ("som.dsl-bundle.v1" if target == "python" else "som.dsl-bundle.v2"), "raw target example bundle version differs")
                self.row(f"target_case/{report_section}/{case['id']}", check)
        for case in self.contracts["negative-cases.json"].get("cases", []):
            self.row(f"negative_case/{case['id']}", lambda case=case: self.diagnostic(self.find("react-target", case["id"], "negative_cases"), case["expected"], case["http_status"], "react-target", case["body"]))

    def python_pairs(self) -> None:
        expected: set[str] = set()
        for case in self.inventory.get("public_examples", []):
            expected.add(f"{case['id']}-json-python-equivalence")
            if case.get("cnl") and case.get("cnl_sha256"):
                expected.add(f"{case['id']}-cnl-python-equivalence")
        for case in self.inventory.get("name_cases", []):
            expected.add(f"{case['id']}-python-equivalence")
        fixtures = object_value(self.reader.json("som-rs/tests/compiler-v1/cnl-positive-fixtures.json"), "CNL positive fixtures")
        version_positive = {case["id"] for case in self.inventory.get("version_positive_fixtures", [])}
        for case in fixtures.get("cases", []):
            expected.add(f"{case['id']}-cnl-python-equivalence")
            if case["id"] in version_positive:
                expected.add(f"{case['id']}-json-python-equivalence")
        coverage = object_value(self.reader.json("som-rs/tests/rust-output-v1/cases/index.json"), "coverage case index")
        for case in coverage.get("cases", []):
            expected |= {f"{case['id']}-cnl-python-equivalence", f"{case['id']}-json-python-equivalence"}
        for case in self.inventory.get("public_products_in_scope", []):
            expected.add(f"{case['id']}-json-python-equivalence")
            directory = self.inventory["sources"]["public_products"]["directory"]
            source = self.reader.path(Path(directory) / case["index"] / "source.cnl")
            if source.is_file():
                self.reader.hash(source)
                expected.add(f"{case['id']}-cnl-python-equivalence")
        for case in self.inventory.get("legacy_in_scope", []):
            expected.add(f"{case['id']}-json-python-equivalence")
        rows = self.cases("react-target")
        for row_id in sorted(expected):
            def check(row_id: str = row_id) -> None:
                found = [row for row in rows if row.get("section") == "python_equivalence" and row.get("id") == row_id]
                need(len(found) == 1, f"missing Python equivalence {row_id}", "unrun" if not found else "failed")
                self.response("react-target", found[0])
                base = self.run / "react-target" / "responses" / f"python_equivalence__{safe_id(row_id)}"
                left = self.reader.bytes(str(base) + "__python.json")
                right = self.reader.bytes(str(base) + "__no_target.json")
                need(found[0].get("http_status") == 200 and left == right, "Python target and omitted-target response bytes differ")
            self.row(f"python_pair/{row_id}", check)

    def cli(self) -> tuple[str, str]:
        rows = list_value(object_value(self.get_report("react-target").get("cli"), "CLI report").get("rows"), "CLI rows")
        expected = []
        for case in self.contracts["target-examples.json"].get("examples", []):
            body = json.loads(case["body"])
            if body.get("format") == "som.cnl.v1":
                expected.append((case, body, False))
        for case in self.contracts["negative-cases.json"].get("cases", []):
            body = json.loads(case["body"])
            if body.get("format") == "som.cnl.v1" and isinstance(body.get("target"), str) and set(body) == {"format", "source", "target"}:
                expected.append((case, body, True))
        need(expected, "no named CLI cases in raw evidence", "unrun")
        for case, body, refusal in expected:
            found = [row for row in rows if row.get("id") == case["id"] or row.get("input") == case["id"]]
            need(len(found) == 1, f"missing CLI case {case['id']}", "unrun" if not found else "failed")
            row = found[0]
            need(row.get("target") == body.get("target"), "CLI target changed")
            captured_request = {"format": "som.cnl.v1", "source": body["source"]}
            if body.get("target") is not None:
                captured_request["target"] = body["target"]
            expected_hash = sha(json.dumps(captured_request, ensure_ascii=False, separators=(",", ":")).encode())
            need(row.get("expected_request_sha256") == row.get("request_sha256") == expected_hash, "CLI captured request hash differs from the independently derived frozen request")
            need(true_integer(row.get("exit_code")) and row["exit_code"] == (2 if refusal else 0), "CLI exit code differs")
            if refusal:
                need(row.get("code") == case["expected"]["code"] and row.get("http_status") == case["http_status"], "CLI refusal differs")
            def raw(name: str) -> bytes:
                value = row.get(f"{name}_file")
                need(isinstance(value, str) and value, f"missing CLI raw {name}", "unrun")
                path = self.reader.path(value, self.run / "react-target")
                need(path.is_relative_to(self.run / "react-target/cli-inputs"), "CLI raw path leaves this run")
                data = self.reader.bytes(path)
                need(row.get(f"{name}_sha256") == sha(data), f"CLI raw {name} hash differs")
                return data
            request = raw("request")
            need(request == json.dumps(captured_request, ensure_ascii=False, separators=(",", ":")).encode(), "CLI raw forwarded request differs")
            need(true_integer(row.get("capture_count")) and row["capture_count"] == 1
                 and row.get("request_route") == "/v1/compile", "CLI forwarding inventory differs")
            reply = object_value(json.loads(raw("http_response")), "CLI HTTP reply")
            stdout, stderr = raw("stdout"), raw("stderr")
            stream = stderr if refusal else stdout
            need(json.loads(stream) == (reply if refusal else reply.get("bundle")), "CLI output differs from raw HTTP reply")
        return "passed", "exact forwarded request, raw streams, HTTP reply and exit codes match"

    def scope_cases(self) -> None:
        for case in self.inventory.get("public_examples", []):
            def public(case: dict = case) -> None:
                prompt = object_value(self.pinned_json(case["prompt"], case["prompt_file_sha256"]), "public example prompt")
                forms = {"json": {"format": "som.prompt-json.v1", "prompt": prompt, "target": "react"}}
                if case.get("cnl"):
                    raw = self.reader.bytes(case["cnl"])
                    need(sha(raw) == case.get("cnl_sha256"), "public CNL source hash differs")
                    forms["cnl"] = {"format": "som.cnl.v1", "source": raw.decode(), "target": "react"}
                self.scope_case(case["id"], prompt, forms, case.get("react"))
            self.row(f"scope_case/{case['id']}", public)

        def fixtures() -> None:
            path = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
            pins = self.inventory.get("sources", {}).get("cnl_positive_fixtures", {})
            document = object_value(self.pinned_json(path, pins.get(path)), "positive fixture inventory")
            cases = list_value(document.get("cases"), "all positive CNL fixtures")
            ids = [case["id"] for case in cases]
            need(len(set(ids)) == len(ids), "positive fixture inventory repeats an id")
            for case in cases:
                def check(case: dict = case) -> None:
                    prompt = object_value(case.get("expected_prompt"), "fixture prompt")
                    need(case.get("prompt_sha256") == sha(canonical(prompt)), "fixture prompt hash differs")
                    need(case.get("cnl_sha256") == sha(case["cnl"].encode()), "fixture CNL hash differs")
                    forms = {"cnl": {"format": "som.cnl.v1", "source": case["cnl"], "target": "react"}}
                    if case.get("category") == "version_positive":
                        forms["json"] = {"format": "som.prompt-json.v1", "prompt": prompt, "target": "react"}
                    self.scope_case(case["id"], prompt, forms)
                self.row(f"scope_case/{case['id']}", check)
        self.row("scope_case/source-fixtures", fixtures)

        def coverage() -> None:
            path = "som-rs/tests/rust-output-v1/cases/index.json"
            pins = self.inventory.get("sources", {}).get("coverage_index", {})
            document = object_value(self.pinned_json(path, pins.get(path)), "coverage source inventory")
            inventory = {case["id"]: case for case in self.inventory.get("coverage_cases", [])}
            for case in list_value(document.get("cases"), "all coverage cases"):
                def check(case: dict = case) -> None:
                    need(case["id"] in inventory, "coverage case absent from frozen inventory")
                    pin = inventory[case["id"]]
                    base = Path("som-rs/tests/rust-output-v1/cases")
                    prompt = self.pinned_json(base / case["prompt"], pin["prompt_file_sha256"])
                    raw = self.reader.bytes(base / case["cnl"])
                    need(sha(raw) == pin["cnl_sha256"], "coverage CNL source hash differs")
                    forms = {"json": {"format": "som.prompt-json.v1", "prompt": prompt, "target": "react"}, "cnl": {"format": "som.cnl.v1", "source": raw.decode(), "target": "react"}}
                    self.scope_case(case["id"], prompt, forms, pin.get("react"))
                self.row(f"scope_case/{case['id']}", check)
        self.row("scope_case/source-coverage", coverage)

        for case in self.inventory.get("name_cases", []):
            def name_case(case: dict = case) -> None:
                row = self.find("react-target", case["id"], "name_cases")
                _, response = self.response("react-target", row)
                prompt = object_value(response.get("bundle", {}).get("prompt"), "name-case raw prompt")
                need(row.get("request_sha256") == case.get("body_sha256"), "name case request hash differs")
                need(self.scope_oracle(prompt) is None and row.get("http_status") == 200, "name case is outside the frozen profile")
            self.row(f"scope_case/{case['id']}", name_case)

        public_source = self.inventory.get("sources", {}).get("public_products")
        if public_source is not None:
            self.row("scope_case/source-public-products", lambda: self.public_product_scope(public_source))
        elif self.inventory.get("public_products_in_scope"):
            self.row("scope_case/source-public-products", lambda: need(False, "public product source universe missing", "unrun"))

        source = self.inventory.get("sources", {}).get("legacy_inventory")
        if source is not None:
            self.row("scope_case/source-legacy", lambda: self.legacy_scope(source))
        elif self.inventory.get("legacy_in_scope"):
            self.row("scope_case/source-legacy", lambda: need(False, "legacy source universe missing", "unrun"))

    def pinned_json(self, path: str | Path, expected: Any) -> Any:
        raw = self.reader.bytes(path)
        need(isinstance(expected, str) and HEX.fullmatch(expected), f"source pin missing: {path}", "unrun")
        need(sha(raw) == expected, f"frozen source hash differs: {path}")
        return json.loads(raw)

    def scope_oracle(self, prompt: dict) -> dict | None:
        scope = self.contracts["scope.json"]
        version = prompt["version"]
        rule, pointer, values = None, None, {}
        if version not in scope["prompt_versions"]["in_scope"]:
            rule, pointer = "prompt_version", "/version"
            values = {"version": version.removeprefix("som.prompt.v"), "family": scope["prompt_versions"]["out_of_scope"][version]}
        elif any(segment == "" for segment in prompt["resource"]["path"].split("/")[1:]):
            rule, pointer, values = "path_empty_segment", "/resource/path", {"path": prompt["resource"]["path"]}
        else:
            for index, field in enumerate(prompt["resource"]["fields"]):
                if field["name"] in scope["identifiers"]["reserved_names"]:
                    rule, pointer, values = "identifier_reserved", f"/resource/fields/{index}/name", {"name": field["name"]}
                    break
        if rule is None:
            return None
        refusal = scope["refusals"][rule]
        return {"rule": rule, "code": refusal["code"], "pointer": pointer, "cause": refusal["cause"].format(**values), "allowed_form": refusal["allowed_form"], "http_status": refusal["http_status"]}

    def scope_case(self, cid: str, prompt: dict, forms: dict[str, dict], inventory_rule: str | None = None, *, legacy: bool = False) -> None:
        expected = self.scope_oracle(prompt)
        rule = "in_scope" if expected is None else expected["rule"]
        if inventory_rule is not None:
            need(rule == inventory_rule, f"{cid} frozen selection differs from independent profile")
        raw_replies = []
        for form, request in forms.items():
            row_id = cid if legacy else f"{cid}-{form}-react"
            section = "legacy" if legacy else "in_scope" if expected is None else "out_of_scope"
            row = self.find("react-target", row_id, section)
            raw, response = self.response("react-target", row)
            body = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
            need(row.get("request_sha256") == sha(body.encode()), f"{cid} {form} scope request differs from frozen source")
            if expected is None:
                need(row.get("http_status") == 200, f"in-scope case {cid} was refused")
                bundle = object_value(response.get("bundle"), "in-scope raw bundle")
                need(bundle.get("target") == "react" and bundle.get("prompt") == prompt, f"{cid} admitted bundle target or prompt differs")
                raw_replies.append(raw)
            else:
                diagnostic = {key: expected[key] for key in ("code", "cause", "allowed_form")}
                diagnostic["prompt_pointer"] = expected["pointer"] if form == "cnl" else "/prompt" + expected["pointer"]
                self.diagnostic(row, diagnostic, expected["http_status"], "react-target", body)
        need(not raw_replies or all(raw == raw_replies[0] for raw in raw_replies), f"{cid} CNL and JSON admitted response bytes differ")

    def legacy_scope(self, source: dict) -> None:
        # The source inventory pins the complete legacy universe, including all
        # out-of-scope jobs. The selected 100 rows alone cannot prove this gate.
        pinned_cases = []
        for path, expected in source.items():
            if isinstance(expected, str) and HEX.fullmatch(expected):
                pinned = object_value(self.pinned_json(path, expected), "pinned complete legacy inventory")
                pinned_cases.extend(list_value(pinned.get("groups"), "pinned legacy groups"))
        directory = self.reader.path(source["run"])
        result = object_value(self.reader.json(directory / "result.json"), "complete legacy result inventory")
        records = list_value(result.get("records"), "complete legacy records")
        ids = [str(record.get("id", f"legacy-{index:04d}")) for index, record in enumerate(records)]
        need(records and len(set(ids)) == len(ids), "complete legacy universe is empty or repeats an id")
        need(pinned_cases, "complete legacy source has no pinned named inventory", "unrun")
        need(ids == [case["id"] for case in pinned_cases], "legacy result drops, changes or reorders the pinned complete named inventory")
        selected = {case["id"]: case for case in self.inventory.get("legacy_in_scope", [])}
        need(set(selected) <= set(ids), "selected legacy cases absent from complete source universe")
        for index, (cid, record) in enumerate(zip(ids, records)):
            def check(index: int = index, cid: str = cid, record: dict = record) -> None:
                candidates = []
                if record.get("prompt_sha256"):
                    candidates.append(directory / "compile" / str(record["prompt_sha256"]))
                candidates.append(directory / f"{index:04d}")
                request = None
                for candidate in candidates:
                    for filename in ("compile-json.request.json", "compile.request.json"):
                        path = self.reader.path(candidate / filename)
                        if path.is_file():
                            request = object_value(self.reader.json(path), "legacy compile input")
                            break
                    if request is not None:
                        break
                need(request is not None, f"legacy raw compile input missing: {cid}", "unrun")
                need("target" not in request, "accepted legacy input already contains a target")
                prompt = object_value(request.get("prompt"), "legacy prompt")
                if record.get("prompt_sha256"):
                    compact = json.dumps(prompt, ensure_ascii=False, separators=(",", ":")).encode()
                    need(record["prompt_sha256"] in (sha(canonical(prompt)), sha(compact)), "legacy recorded prompt hash differs")
                need(record.get("prompt_sha256") == pinned_cases[index].get("prompt_sha256"), "legacy result prompt identity differs from pinned complete inventory")
                selected_case = selected.get(cid)
                if selected_case is not None:
                    need(selected_case.get("inventory_index") == index and selected_case.get("prompt_sha256") == record.get("prompt_sha256"), "selected legacy identity differs")
                actual_rule = "in_scope" if self.scope_oracle(prompt) is None else self.scope_oracle(prompt)["rule"]
                need((actual_rule == "in_scope") == (cid in selected), "complete legacy source profile differs from frozen selected inventory")
                self.scope_case(cid, prompt, {"json": {**request, "target": "react"}}, actual_rule, legacy=True)
            self.row(f"scope_case/{cid}", check)

    def public_product_scope(self, source: dict) -> None:
        directory = self.reader.path(source["directory"])
        result = object_value(self.reader.json(directory / "result.json"), "complete public product source inventory")
        records = list_value(result.get("records"), "complete public source records")
        frozen_sources = self.public_sources()
        need([record.get("id") for record in records] == [case["id"] for case in frozen_sources], "public source ids/order differ from immutable fixture/example identities")
        histogram = list_value(self.inventory.get("counts", {}).get("public_products"), "frozen complete public product histogram")
        expected = {(entry["prompt_version"], entry["react"]): entry["count"] for entry in histogram}
        need(len(records) == sum(expected.values()), "complete public source record count differs from frozen universe")
        selected = {case["id"]: case for case in self.inventory.get("public_products_in_scope", [])}
        observed: dict[tuple[str, str], int] = {}
        for index, record in enumerate(records):
            cid = f"public-product-{index:03d}"
            def check(index: int = index, cid: str = cid) -> None:
                base = directory / f"{index:03d}"
                raw = self.reader.bytes(base / "prompt.json")
                prompt = object_value(json.loads(raw), "public product prompt")
                need(canonical(prompt) == canonical(frozen_sources[index]["prompt"]), "public source prompt differs from immutable fixture/example prompt")
                refusal = self.scope_oracle(prompt)
                rule = "in_scope" if refusal is None else refusal["rule"]
                key = (prompt["version"], rule)
                observed[key] = observed.get(key, 0) + 1
                need((cid in selected) == (rule == "in_scope"), "public product profile differs from selected frozen inventory")
                if cid in selected:
                    need(selected[cid].get("index") == f"{index:03d}" and selected[cid].get("prompt_file_sha256") == sha(raw), "selected public prompt identity differs")
                forms = {"json": {"format": "som.prompt-json.v1", "prompt": prompt, "target": "react"}}
                cnl = self.reader.path(base / "source.cnl")
                actual_cnl = self.reader.bytes(cnl).decode()
                need(actual_cnl == frozen_sources[index]["cnl"], "public CNL source differs from immutable fixture/example source")
                forms["cnl"] = {"format": "som.cnl.v1", "source": actual_cnl, "target": "react"}
                if refusal is None:
                    self.scope_case(cid, prompt, forms, rule)
                else:
                    # The frozen writer emits dedicated product rows only for
                    # admitted products. Refusals use the original fixture or
                    # example identity and that source's declared forms.
                    source_case = frozen_sources[index]
                    source_forms = {form: forms[form] for form in source_case["scope_forms"]}
                    if "json" in source_forms:
                        source_forms["json"] = {"format": "som.prompt-json.v1", "prompt": source_case["prompt"], "target": "react"}
                    self.scope_case(source_case["scope_id"], source_case["prompt"], source_forms, rule)
            self.row(f"scope_case/{cid}", check)
        need(observed == expected, "complete public source profile histogram differs from frozen contract")

    def public_sources(self) -> list[dict]:
        """Rebuild public-products input identities from immutable contract data."""
        sources = self.inventory.get("sources", {})
        fixture_path = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json"
        fixture_pins = sources.get("cnl_positive_fixtures", {})
        fixtures = object_value(self.pinned_json(fixture_path, fixture_pins.get(fixture_path)), "frozen public fixture inputs")
        cases = []
        for case in list_value(fixtures.get("cases"), "frozen public fixture cases"):
            prompt = object_value(case.get("expected_prompt"), "frozen public fixture prompt")
            need(isinstance(case.get("id"), str) and isinstance(case.get("cnl"), str), "frozen public fixture identity/source missing")
            need(case.get("prompt_sha256") == sha(canonical(prompt)), "frozen public fixture prompt hash differs")
            need(case.get("cnl_sha256") == sha(case["cnl"].encode()), "frozen public fixture CNL hash differs")
            cases.append({"id": case["id"], "prompt": prompt, "cnl": case["cnl"],
                          "scope_id": case["id"], "scope_forms": ("json", "cnl") if case.get("category") == "version_positive" else ("cnl",)})
        examples = Path("som-rs/contracts/compiler-v1/examples")
        pins = sources.get("examples", {})
        index_path = examples / "index.json"
        index = object_value(self.pinned_json(index_path, pins.get(index_path.as_posix())), "frozen public example index")
        for case in list_value(index.get("cases"), "frozen public examples"):
            prompt_path = examples / case["prompt"]
            cnl_path = examples / case["cnl"]
            prompt = object_value(self.pinned_json(prompt_path, pins.get(prompt_path.as_posix())), "frozen public example prompt")
            raw_cnl = self.reader.bytes(cnl_path)
            need(sha(raw_cnl) == pins.get(cnl_path.as_posix()), "frozen public example CNL hash differs")
            cases.append({"id": "PUBLIC_" + case["id"], "prompt": prompt, "cnl": raw_cnl.decode(),
                          "scope_id": "example-" + case["id"], "scope_forms": ("json", "cnl")})
        ids = [case["id"] for case in cases]
        need(cases and len(set(ids)) == len(ids), "immutable public input ids are empty or repeated")
        scope_ids = [case["scope_id"] for case in cases]
        need(len(set(scope_ids)) == len(scope_ids), "immutable public scope source ids are ambiguous")
        return cases

    def bundle_data(self, case_id: str) -> tuple[dict, dict, dict]:
        row = self.find("bundles", case_id, "bundle_checks")
        self.response("bundles", row)
        need(case_id in self.manifest, f"missing manifest product {case_id}", "unrun")
        manifest = self.manifest[case_id]
        base = self.run / "bundles" / "responses" / f"bundle_checks__{safe_id(case_id)}"
        forms = row.get("forms")
        if forms is None:
            forms = row.get("expected", {}).get("forms")
        if forms is None:
            forms = ["json", "cnl"]
        need(isinstance(forms, list) and forms and set(forms) <= {"json", "cnl"}, "invalid bundle request forms")
        replies = [self.reader.bytes(str(base) + f"__compile_{form}_{attempt}.json") for form in forms for attempt in (1, 2)]
        assembled = [self.reader.bytes(str(base) + f"__assemble_{attempt}.json") for attempt in (1, 2)]
        need(all(raw == replies[0] for raw in replies), "compile response bytes differ between forms or repeats")
        need(assembled[0] == assembled[1], "repeated assemble response bytes differ")
        bundle = object_value(object_value(json.loads(replies[0]), "compile reply").get("bundle"), "React bundle")
        files = object_value(object_value(json.loads(assembled[0]), "assemble reply").get("files"), "assembled files")
        need(row.get("http_status") == 200, "bundle assemble HTTP status is not 200")
        need(bundle.get("prompt") == manifest.get("prompt"), "manifest prompt differs from raw bundle")
        return bundle, files, row

    def bundles(self) -> None:
        manifest_doc = object_value(self.reader.json(self.run / "bundles/products/manifest.json"), "products manifest")
        entries = list_value(manifest_doc.get("products"), "manifest products")
        ids = [entry.get("id") for entry in entries]
        need(len(set(ids)) == len(ids), "manifest repeats product id")
        self.manifest = {entry["id"]: entry for entry in entries}
        expected = {case["id"] for case in self.acceptance}
        self.row("bundle_integrity/inventory", lambda: need(set(ids) == expected, "manifest inventory differs from complete named acceptance inventory", "unrun" if expected - set(ids) else "failed"))
        documents = [self.contracts["bundle.schema.json"]]
        compiler = self.reader.path("som-rs/contracts/compiler-v1")
        for path in sorted(compiler.glob("*.schema.json")):
            documents.append(object_value(self.reader.json(path), "compiler schema"))
        schema = Schema(self.reader, documents)
        for case in self.acceptance:
            cid = case["id"]
            def check(cid: str = cid) -> tuple[str, str]:
                bundle, files, row = self.bundle_data(cid)
                need(bundle.get("version") == "som.dsl-bundle.v2" and bundle.get("target") == "react", "bundle envelope differs")
                schema.validate(bundle, self.contracts["bundle.schema.json"])
                dsl = object_value(bundle.get("dsl"), "bundle DSL")
                paths = [entry.get("path") for entry in list_value(dsl.get("files"), "DSL files")]
                expected_paths = {entry["path"] for entry in self.contracts["product-layout.json"]["files"]}
                need(set(paths) == expected_paths and len(paths) == len(set(paths)), "DSL product paths differ")
                blocks = [block for entry in dsl["files"] for block in entry["blocks"]]
                catalog = {block["id"] for entry in self.contracts["catalog/react-catalog.v1.json"]["files"] for block in entry["blocks"]}
                need(len(paths) <= 19 and 0 < len(blocks) <= 64, "bundle file or total block budget differs")
                need(all(isinstance(block.get("id"), str) and block["id"].startswith("react.") and block["id"] in catalog for block in blocks), "bundle contains an unknown React block")
                hashes = object_value(bundle.get("hashes"), "bundle hashes")
                need(hashes.get("prompt_sha256") == sha(canonical(bundle["prompt"])), "prompt canonical hash differs")
                need(hashes.get("dsl_sha256") == sha(canonical(dsl)), "DSL canonical hash differs")
                need(hashes.get("payload_sha256") == sha(canonical({key: value for key, value in bundle.items() if key != "hashes"})), "payload canonical hash differs")
                need(set(files) == expected_paths and all(isinstance(value, str) for value in files.values()), "assembled file set differs")
                product = self.reader.path(self.manifest[cid]["product"])
                need(product == self.run / "bundles/products" / cid, "product path is outside its recorded run case")
                need(self.reader.tree(product, exclude_modules=True) == {path: sha(text.encode()) for path, text in files.items()}, "product files differ from raw assembly")
                for entry in self.contracts["product-layout.json"]["files"]:
                    if entry.get("kind") == "fixed":
                        need(files[entry["path"]].encode() == self.reader.bytes(f"{CONTRACT}/reference/{entry['path']}"), "fixed product file differs from frozen reference")
                base = self.run / "bundles/responses" / f"bundle_checks__{safe_id(cid)}__rust_manifest.json"
                rust = object_value(self.reader.json(base), "Rust manifest reply").get("bundle", {})
                assets = object_value(bundle.get("assets"), "React assets")
                need(all(key in assets and assets[key] == rust.get("assets", {}).get(key) for key in SHARED), "shared asset descriptor differs from Rust manifest")
                if self.latest_receipt:
                    need(assets.get("target_contract") == {"id": "som.react-output-contract.v1", "sha256": sha(canonical(self.latest_receipt["files"]))}, "React target contract descriptor differs")
                formatter = self.contracts["toolchain.json"].get("formatter")
                if isinstance(formatter, dict):
                    need(assets.get("formatter") == {"id": formatter["id"], "sha256": formatter["sha256"]}, "formatter descriptor differs")
                need(assets.get("catalog") == {"id": "som.react-catalog.v1", "sha256": self.reader.hash(f"{CONTRACT}/catalog/react-catalog.v1.json")}, "React catalog descriptor differs")
                fixed_order = ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
                    "src/main.tsx", "src/App.tsx", "src/api.ts", "src/spec.ts", "src/form.ts", "src/styles.css",
                    "src/pages/ListPage.tsx", "src/pages/DetailPage.tsx", "src/pages/FormPage.tsx", "src/pages/ErrorView.tsx",
                    "src/app.test.tsx", "src/form.test.ts")
                library = self.reader.bytes("som-rs/som-targets/src/react/render.rs") + b"".join(
                    self.reader.bytes(f"{CONTRACT}/reference/{name}") for name in fixed_order)
                need(assets.get("block_library") == {"id": "som.react-block-library.v1", "sha256": sha(library)}, "React block library descriptor differs")
                backend = {f"som-targets/{name}": self.reader.hash(f"som-rs/som-targets/{name}")
                           for name in ("Cargo.toml", "src/lib.rs", "src/main.rs", "src/profile.rs")}
                backend.update({f"som-targets/src/react/{name}": value for name, value in
                                self.reader.tree(self.reader.path("som-rs/som-targets/src/react")).items()})
                need(assets.get("backend") == {"id": "som.react-backend.v1", "sha256": sha(canonical(
                    {"version": "som.react-backend.v1", "files": backend}))}, "React backend descriptor differs")
                for name, expected_text in render_files(bundle["prompt"]).items():
                    need(files.get(name) == expected_text, f"generated {name} differs from independent frozen rendering rules")
                return "passed", "raw bundle, product bytes, generated files and all descriptors recomputed"
            self.row(f"bundle_case/{cid}", check)
        recipes = self.contracts["acceptance-plan.json"].get("assemble_refusals", {}).get("recipes", [])
        for recipe in recipes:
            def check(recipe: dict = recipe) -> tuple[str, str]:
                row = self.find("bundles", recipe["id"], "assemble_refusals")
                def base(name: str) -> dict:
                    return object_value(self.reader.json(self.run / "bundles/responses" / name), "assemble base reply")["bundle"]
                react = base("bundle_checks__example-v1__compile_json_1.json")
                rust = base("bundle_checks__example-v1__rust_manifest.json")
                typescript = base("assemble_refusals__base_typescript_v1.json")
                python = base("assemble_refusals__base_python_v1.json")
                v5 = base("assemble_refusals__base_python_v5.json")
                prompt = self.reader.json("som-rs/contracts/compiler-v1/examples/v5.prompt.json")
                need(v5["prompt"] == prompt, "v5 refusal base prompt differs from frozen example")
                raw = request_for(recipe["id"], react, typescript, rust, python, prompt, v5["assets"])
                retained = self.reader.bytes(self.run / "bundles/responses" / f"assemble_refusals__{recipe['id']}.request.raw")
                need(retained == raw, "assemble mutation differs from independently rebuilt frozen recipe")
                self.diagnostic(row, {key: recipe[key] for key in ("code", "prompt_pointer")}, recipe["http_status"], "bundles", raw.decode())
                _, response = self.response("bundles", row)
                try:
                    verify_location(raw, response["diagnostics"][0])
                except BaselineRawError as error:
                    raise EvidenceError(str(error), error.status) from error
                return "passed", "exact mutation, hashes, raw request and refusal span recomputed"
            self.row(f"assemble_refusals/recipes/{recipe['id']}", check)

    def product_gate(self, cid: str, gate: int) -> tuple[str, str] | None:
        need(cid in self.manifest, f"missing product manifest case {cid}", "unrun")
        product_row = self.find("products", cid)
        work = self.run / "products" / cid
        gates = object_value(self.reader.json(work / "gates/gates.json"), "raw gates")
        commands = object_value(self.contracts["toolchain.json"].get("commands"), "frozen commands")
        if gate <= 4 or gate == 5:
            names = ("install", "typecheck", "format_check", "test")
            required = [names[gate - 1]] if gate <= 4 else ["build-1", "build-2"]
            gate_rows = list_value(gates.get("gates"), "raw command gates")
            need([row.get("step") for row in gate_rows] == [*names, "build-1", "build-2"], "product command order differs")
            for name in required:
                rows = [row for row in gate_rows if row.get("step") == name]
                need(len(rows) == 1, f"missing command gate {name}", "unrun")
                row = rows[0]
                expected = commands["build"].replace("<dist dir outside the product>", str(work / "gates" / f"dist-{name[-1]}")) if name.startswith("build-") else commands[name]
                need(row.get("command") == expected, f"{name} command differs from frozen toolchain")
                need(true_integer(row.get("exit_code")) and row["exit_code"] == 0, f"{name} exited with a failure")
                self.reader.bytes(row.get("log"), work / "gates")
            codes = object_value(product_row.get("exit_codes"), "product process exits")
            need(codes.get("gates") == 0, "product gate process exit is not zero")
            if gate == 1:
                environment = object_value(gates.get("environment"), "gate environment")
                need(environment.get("npm_config_offline") == "true", "npm offline environment missing")
                need("--offline" in required_command(gate_rows, "install") and "--ignore-scripts" in required_command(gate_rows, "install"), "install is not offline with scripts disabled")
            if gate == 5:
                left = self.reader.tree(work / "gates/dist-1")
                right = self.reader.tree(work / "gates/dist-2")
                need(left and left == right and left == gates.get("dist_files"), "raw build dist trees differ or are empty")
            return None
        if gate == 6:
            original = self.reader.tree(self.reader.path(self.manifest[cid]["product"]), exclude_modules=True)
            after = self.reader.tree(work / "gates/product", exclude_modules=True)
            need(original and original == after == gates.get("product_files") == gates.get("product_files_after"), "product file bytes or inventory changed during gates")
            lock = after.get("package-lock.json")
            need(lock is not None and lock == gates.get("lock_sha256_before") == gates.get("lock_sha256_after"), "package lock hashes differ")
            before, after_cache = gates.get("vendored_cache_tree_sha256_before"), gates.get("vendored_cache_tree_sha256_after")
            need(isinstance(before, str) and HEX.fullmatch(before) and before == after_cache, "vendored cache before/after tree hashes differ or are absent")
            snapshots = [object_value(self.reader.json(work / f"cache-{name}.json"), "raw cache file snapshot") for name in ("before", "after")]
            frozen = self.contracts["toolchain.json"]["vendored_cache"]
            current = self.reader.tree(self.reader.path(frozen["path"]))
            digest = sha(b"".join(f"{name}\0{current[name]}\n".encode() for name in sorted(current, key=Path)))
            for snapshot in snapshots:
                need(snapshot.get("version") == "som.m6-cache-snapshot.v1" and snapshot.get("files") == current, "cache file snapshot differs from current frozen bytes")
                need(snapshot.get("tree_sha256") == digest == frozen["tree_sha256"] == before == after_cache, "cache tree hash differs from frozen input")
                need(type(snapshot.get("file_count")) is int and snapshot["file_count"] == frozen["tree_files"] == len(current)
                     and type(snapshot.get("symlink_count")) is int and snapshot["symlink_count"] == frozen["tree_symlinks"] == 0, "cache file inventory differs")
            return "passed", "product, lock and before/after cache file maps match retained frozen bytes"
        api = object_value(self.reader.json(work / "api/report.json"), "raw API report")
        result = object_value(api.get("result"), "raw API result")
        contract = self.contracts["api-contract.json"]
        steps = list_value(result.get("steps"), "raw API steps")
        need([step.get("name") for step in steps] == [step["name"] for step in contract["steps"]], "API step names/order differ from complete frozen inventory")
        prompt = object_value(self.manifest[cid].get("prompt"), "product prompt")
        fields = object_value(prompt.get("resource"), "prompt resource").get("fields")
        fields = list_value(fields, "prompt fields")
        query = prompt.get("version") != "som.prompt.v1"
        facts = {"query": query, "filter": query, "before": query and prompt.get("query", {}).get("date_field") is not None, "not_nullable": any(field.get("required") or field.get("default") is not None for field in fields), "required": any(field.get("required") for field in fields), "unique": any(field.get("unique") for field in fields)}
        need(result.get("facts") == facts, "API facts differ from prompt")
        conditions = {"list filter": "filter", "list before": "before", "list page": "query", "list limit too large": "query", "patch null on a field that is not nullable": "not_nullable", "create missing a required field": "required", "create unique conflict": "unique"}
        for index, (step, planned) in enumerate(zip(steps, contract["steps"])):
            name = planned["name"]
            applicable = facts[conditions[name]] if name in conditions else True
            if applicable:
                need(step.get("ok") is True and not step.get("skipped"), f"API step failed or was skipped: {name}")
            else:
                need(step.get("ok") is None and step.get("skipped") == contract["skip_reasons"][name], f"API conditional skip differs: {name}")
        need(api.get("exit_code") == 0 and product_row.get("exit_codes", {}).get("api_contract") == 0, "API command exited with failure")
        processes = list_value(api.get("processes"), "API owned processes")
        need(processes and all(process.get("stopped") is True for process in processes), "API process cleanup is missing or failed")
        for index, process in enumerate(processes):
            need(true_integer(process.get("pid")) and process["pid"] > 0, "owned process id missing")
            need(isinstance(process.get("command"), list) and process["command"], "owned process command missing")
            need(isinstance(process.get("expected_cwd"), str), "owned process expected cwd missing")
            if process.get("initial_cwd_verified") is True:
                need(process.get("initial_cwd") == process["expected_cwd"], "owned process initial cwd differs")
            else:
                need(process.get("initial_cwd") is None, "owned process started in a different cwd")
            stop_verified = process.get("stop_cwd_verified")
            need(stop_verified is True or (stop_verified is None and true_integer(process.get("exit_code"))), "active process stop ownership is unverified")
            self.reader.path(process["expected_cwd"])
        try:
            validate_api(self.reader, work / "api-raw", work / "api/report.json", prompt, contract,
                         self.reader.path(self.manifest[cid]["product"]))
        except ApiRawError as error:
            raise EvidenceError(str(error), error.status) from error
        return "passed", "all applicable API exchanges and OS process ownership/cleanup recomputed from raw evidence"

    def products(self) -> None:
        rows = self.cases("products")
        ids = [row.get("id") for row in rows]
        expected = {case["id"] for case in self.acceptance}
        self.row("product_integrity/inventory", lambda: need(len(set(ids)) == len(ids) and set(ids) == expected, "product report does not cover complete named inventory", "unrun" if expected - set(ids) else "failed"))
        gates = self.contracts["acceptance-plan.json"]["product_gates"]["each_acceptance_case"]
        for case in self.acceptance:
            for index, description in enumerate(gates, 1):
                self.row(f"product_gates/{case['id']}/{index}", lambda cid=case["id"], index=index: self.product_gate(cid, index), description=description)

    def controls(self, language: str) -> tuple[str, str]:
        step = f"{language}-python-controls"
        report = self.get_report(step)
        results = list_value(report.get("results"), "Python controls results")
        contract_name = {"react": "react-output-v1", "rust": "rust-output-v1", "ts": "typescript-output-v1"}[language]
        negatives = object_value(self.reader.json(f"som-rs/contracts/{contract_name}/negative-cases.json"), "control negative contract").get("cases", [])
        expectations = {case["id"]: case for case in negatives}
        if language in ("react", "ts"):
            target = object_value(self.reader.json(f"som-rs/contracts/{contract_name}/target-examples.json"), "control name cases")
            expectations.update({case["id"]: case for case in target.get("name_cases", [])})
        expected = set(expectations)
        ids = [row.get("id") for row in results]
        need(len(set(ids)) == len(ids) and set(ids) == expected, "Python controls named case inventory differs", "unrun" if expected - set(ids) else "failed")
        try:
            validate_controls(self.reader, self.run / f"{step}-raw", report, language)
        except BaselineRawError as error:
            raise EvidenceError(str(error), error.status) from error
        return "passed", "all named control request/reply bytes, diagnostics and sealed historical payload pins match"

    def baseline(self, language: str) -> tuple[str, str]:
        step = f"{language}-baseline"
        key = "rows" if language in ("python", "rust") else "cases"
        rows = self.cases(step, key)
        need(rows, f"missing {language} baseline case results", "unrun")
        self.baseline_inventory(language, rows)
        if language in ("python", "rust"):
            try:
                validator = validate_python if language == "python" else validate_rust
                validator(self.reader, self.run / f"{step}-raw", self.get_report(step))
            except BaselineRawError as error:
                raise EvidenceError(str(error), error.status) from error
            return "passed", "complete source-bound compile and assembly raw bytes match accepted references"
        seen = set()
        for row in rows:
            identity = (row.get("source"), row.get("phase"), row.get("id"), row.get("variant"), row.get("check"))
            need(identity not in seen, "baseline case result is repeated")
            seen.add(identity)
            self.typescript_baseline_case(row)
        return "passed", "raw TypeScript compile replies and product file bytes match"

    def typescript_raw(self, record: dict, file_key: str, hash_key: str) -> dict:
        """Read one hash-bound exchange from this run's TypeScript step only."""
        value = record.get(file_key)
        need(value is not None, f"missing TypeScript raw {file_key}", "unrun")
        need(isinstance(value, str) and value and not Path(value).is_absolute(), "TypeScript raw path must be relative to this step")
        root = self.run / "ts-baseline"
        path = self.reader.path(value, root)
        need(path.is_relative_to(root), "TypeScript raw exchange is outside this step of the recorded run")
        raw = self.reader.bytes(path)
        need(record.get(hash_key) == sha(raw), f"TypeScript raw {hash_key} differs")
        try:
            document = object_value(json.loads(raw), "TypeScript raw exchange")
            canonical(document)
            return document
        except (ValueError, UnicodeDecodeError) as exc:
            raise EvidenceError("invalid JSON in TypeScript raw exchange") from exc

    def typescript_baseline_inputs(self, cid: str) -> tuple[dict, dict | None, str | None]:
        """Derive request forms from frozen data without loading a collector."""
        group, case = self.ts_baseline_cases[cid]
        sources = self.ts_baseline_inventory.get("sources", {})
        prompt, cnl = None, None
        if group == "public_examples":
            prompt = object_value(self.pinned_json(case["prompt"], case.get("prompt_file_sha256")), "TypeScript public prompt")
            if case.get("cnl"):
                raw = self.reader.bytes(case["cnl"])
                need(sha(raw) == case.get("cnl_sha256"), "TypeScript public CNL source hash differs")
                cnl = raw.decode()
        elif group in ("version_positive_fixtures", "coverage_cases"):
            fixture = group == "version_positive_fixtures"
            path = "som-rs/tests/compiler-v1/cnl-positive-fixtures.json" if fixture else "som-rs/tests/rust-output-v1/cases/index.json"
            pins = sources.get("cnl_positive_fixtures" if fixture else "coverage_index", {})
            document = object_value(self.pinned_json(path, pins.get(path)), "TypeScript source inventory")
            matches = [item for item in list_value(document.get("cases"), "TypeScript source cases") if item.get("id") == cid]
            need(len(matches) == 1, "TypeScript source case is missing or repeated", "unrun" if not matches else "failed")
            source = matches[0]
            if fixture:
                prompt = object_value(source.get("expected_prompt"), "TypeScript fixture prompt")
                cnl = source.get("cnl")
                need(sha(canonical(prompt)) == case.get("prompt_sha256"), "TypeScript fixture prompt hash differs")
            else:
                base = Path("som-rs/tests/rust-output-v1/cases")
                prompt = object_value(self.pinned_json(base / source["prompt"], case.get("prompt_file_sha256")), "TypeScript coverage prompt")
                cnl = self.reader.bytes(base / source["cnl"]).decode()
            need(isinstance(cnl, str) and sha(cnl.encode()) == case.get("cnl_sha256"), "TypeScript CNL source hash differs")
        elif group == "name_cases":
            document = object_value(self.reader.json("som-rs/contracts/typescript-output-v1/target-examples.json"), "TypeScript name examples")
            matches = [item for item in list_value(document.get("name_cases"), "TypeScript name cases") if item.get("id") == cid]
            need(len(matches) == 1, "TypeScript name source is missing or repeated", "unrun" if not matches else "failed")
            source = matches[0]
            need(isinstance(source.get("body"), str) and sha(source["body"].encode()) == case.get("body_sha256"), "TypeScript name source request hash differs")
            body = object_value(json.loads(source["body"]), "TypeScript name request")
            form = case.get("form")
            need(source.get("form") == form and form in ("json", "cnl"), "TypeScript name source form differs")
            forms = {form: body}
            if form == "cnl":
                forms["json"] = None
            return forms, body.get("prompt"), None
        elif group == "legacy_in_scope":
            # The frozen legacy inventory keeps prompt hashes, not private prompts.
            return {"json": None, "cnl": None}, None, case.get("prompt_sha256")
        else:
            raise EvidenceError(f"no independent TypeScript source rule for {group}", "unrun")
        forms = {"json": {"format": "som.prompt-json.v1", "prompt": prompt, "target": "typescript"}}
        if cnl is not None:
            forms["cnl"] = {"format": "som.cnl.v1", "source": cnl, "target": "typescript"}
        return forms, prompt, None

    def typescript_baseline_case(self, row: dict) -> None:
        cid = row["id"]
        group, _ = self.ts_baseline_cases[cid]
        need(row.get("group") == group, "TypeScript baseline source group differs")
        need(row.get("requests") is not None, "TypeScript baseline requests missing", "unrun")
        requests = list_value(row.get("requests"), "TypeScript baseline requests")
        need(requests, "TypeScript baseline requests missing", "unrun")
        forms, expected_prompt, prompt_hash = self.typescript_baseline_inputs(cid)
        actual_forms = [record.get("form") for record in requests if isinstance(record, dict)]
        need(len(actual_forms) == len(requests) and len(set(actual_forms)) == len(actual_forms), "TypeScript request forms are malformed or repeated")
        need(set(actual_forms) == set(forms), "TypeScript baseline request forms differ from source", "unrun" if set(forms) - set(actual_forms) else "failed")
        previous_bundles = {}
        for record in requests:
            form = record["form"]
            body = self.typescript_raw(record, "request_file", "body_sha256")
            need(body.get("target") == "typescript", "TypeScript compile request target differs")
            member, format_name = ("prompt", "som.prompt-json.v1") if form == "json" else ("source", "som.cnl.v1")
            need(set(body) == {"format", member, "target"} and body.get("format") == format_name, "TypeScript compile request form differs")
            need(isinstance(body[member], dict if form == "json" else str), "TypeScript compile input type differs")
            if forms[form] is not None:
                need(canonical(body) == canonical(forms[form]), "TypeScript compile request differs from immutable source")
            bundles, normalized, file_maps = {}, {}, {}
            for side in ("qa", "baseline"):
                reply = self.typescript_raw(record, f"{side}_response_file", f"{side}_sha256")
                status = record.get(f"{side}_status")
                need(true_integer(status) and status == 200, "TypeScript baseline compile status differs")
                need(reply.get("status") == "ok", "TypeScript compile response is not successful")
                bundle = object_value(reply.get("bundle"), "TypeScript compiled bundle")
                need(bundle.get("target") == "typescript", "TypeScript compiled bundle target differs")
                prompt = object_value(bundle.get("prompt"), "TypeScript compiled prompt")
                if expected_prompt is not None:
                    need(canonical(prompt) == canonical(expected_prompt), "TypeScript compiled prompt differs from immutable source")
                if prompt_hash is not None:
                    need(isinstance(prompt_hash, str) and HEX.fullmatch(prompt_hash) and sha(canonical(prompt)) == prompt_hash, "TypeScript legacy compiled prompt differs from frozen hash")
                if form == "json":
                    need(canonical(body["prompt"]) == canonical(prompt), "TypeScript JSON request differs from its compiled prompt")
                normalized[side] = json.loads(canonical(reply))
                for section, key in (("assets", "backend"), ("assets", "target_contract"), ("hashes", "payload_sha256")):
                    members = object_value(normalized[side]["bundle"].get(section), f"TypeScript bundle {section}")
                    if key in members:
                        members[key] = None
                bundles[side] = bundle
                if side in previous_bundles:
                    need(canonical(bundle) == previous_bundles[side], "TypeScript request forms produce different bundles")
                assemble_body = self.typescript_raw(record, f"{side}_assemble_request_file", f"{side}_assemble_request_sha256")
                need(canonical(assemble_body) == canonical({"bundle": bundle}), "TypeScript assemble request differs from its own compiled bundle")
                assembled = self.typescript_raw(record, f"{side}_assemble_response_file", f"{side}_assemble_sha256")
                status = record.get(f"{side}_assemble_status")
                need(true_integer(status) and status == 200, "TypeScript baseline assemble status differs")
                need(assembled.get("status") == "ok", "TypeScript assemble response is not successful")
                files = object_value(assembled.get("files"), "TypeScript product files")
                need(files and all(isinstance(name, str) and isinstance(content, str) for name, content in files.items()), "TypeScript product files are not a nonempty string map")
                hashes = {name: sha(content.encode()) for name, content in files.items()}
                hash_key = "files_sha256" if side == "qa" else "baseline_files_sha256"
                need(canonical(record.get(hash_key)) == canonical(hashes), f"TypeScript {side} product file hashes differ from raw bytes")
                file_maps[side] = {name: content.encode() for name, content in files.items()}
            need(canonical(normalized["qa"]) == canonical(normalized["baseline"]), "TypeScript compile replies differ outside the three allowed members")
            need(file_maps["qa"] == file_maps["baseline"], "TypeScript baseline product file names or bytes differ")
            previous_bundles = {side: canonical(bundle) for side, bundle in bundles.items()}

    def baseline_inventory(self, language: str, rows: list[dict]) -> None:
        if language == "python":
            expected = set()
            sources = self.inventory.get("sources", {})
            public = sources.get("public_products", {}).get("directory", ".local/rust-compiler-goal/acceptance/public-products-003b")
            legacy = sources.get("legacy_inventory", {}).get("run", ".local/rust-compiler-goal/acceptance/legacy544/run-005")
            public_sources = self.public_sources()
            for source, directory, width in (("public", public, 3), ("legacy", legacy, 4)):
                document = object_value(self.reader.json(Path(directory) / "result.json"), f"accepted {source} raw inventory")
                records = list_value(document.get("records"), f"accepted {source} records")
                need(records, f"accepted {source} inventory empty", "unrun")
                record_ids = [str(record.get("id", f"{source}-{index:0{width}d}")) for index, record in enumerate(records)]
                need(len(set(record_ids)) == len(record_ids), f"accepted {source} source ids repeat")
                if source == "public":
                    need(record_ids == [case["id"] for case in public_sources], "public baseline source ids/order differ from immutable fixture/example identities")
                for index, record in enumerate(records):
                    cid = str(record.get("id", f"{source}-{index:0{width}d}"))
                    expected |= {(source, cid, "none"), (source, cid, "python")}
                    if source == "public":
                        base = self.reader.path(Path(directory) / f"{index:03d}")
                        prompt = object_value(self.reader.json(base / "prompt.json"), "public baseline source prompt")
                        need(prompt == public_sources[index]["prompt"], "public baseline source prompt differs from immutable fixture/example input")
                        request_path = base / "compile-json.request.json"
                        if not self.reader.path(request_path).is_file():
                            request_path = base / "compile.request.json"
                        request = object_value(self.reader.json(request_path), "public baseline compile input")
                        need(request == {"format": "som.prompt-json.v1", "prompt": public_sources[index]["prompt"]}, "public baseline compile input differs from immutable prompt")
                        raw_path = request_path.with_suffix(".raw")
                        default_raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
                        if self.reader.path(raw_path).is_file():
                            default_raw = self.reader.bytes(raw_path)
                            need(json.loads(default_raw) == request, "public baseline raw request differs from immutable input")
                        python_request = {**request, "target": "python"}
                        self.baseline_requests[(source, cid, "none")] = sha(default_raw)
                        self.baseline_requests[(source, cid, "python")] = sha(json.dumps(python_request, ensure_ascii=False, separators=(",", ":")).encode())
            actual = {(row.get("source"), row.get("id"), row.get("variant")) for row in rows}
        elif language == "rust":
            expected = set()
            phase1 = object_value(self.reader.json("som-rs/contracts/rust-output-v1/case-inventory.json"), "frozen Rust phase1 inventory")
            phase2 = object_value(self.reader.json("som-rs/contracts/rust-output-v1/phase2/case-inventory.json"), "Rust phase2 inventory")
            phase1_cases = []
            for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "legacy_in_scope"):
                for case in list_value(phase1.get(group), f"frozen Rust phase1 {group}"):
                    if group != "legacy_in_scope" and case.get("rust") != "in_scope":
                        continue
                    phase1_cases.append(case)
            phase1_ids = [case["id"] for case in phase1_cases]
            need(phase1_ids and len(set(phase1_ids)) == len(phase1_ids), "frozen Rust phase1 ids are empty or repeated")
            for phase, cases in (("phase1", phase1_cases), ("phase2", phase2.get("rows"))):
                for case in list_value(cases, f"Rust {phase} cases"):
                    cid = case["id"]
                    checks = ("compile_equal",) if cid.startswith(("RS12", "RS13")) else ("compile_equal", "baseline_bundle_refused", "rebased_files")
                    expected |= {(phase, cid, check) for check in checks}
            actual = {(row.get("phase"), row.get("id"), row.get("check")) for row in rows}
        else:
            expected = set()
            inventory = object_value(self.reader.json("som-rs/contracts/typescript-output-v1/case-inventory.json"), "TypeScript baseline inventory")
            self.ts_baseline_inventory = inventory
            self.ts_baseline_cases = {}
            for group in ("public_examples", "version_positive_fixtures", "coverage_cases", "name_cases", "public_products_in_scope", "legacy_in_scope"):
                for case in list_value(inventory.get(group, []), f"TypeScript {group}"):
                    if group in ("public_examples", "version_positive_fixtures", "coverage_cases") and case.get("typescript") != "in_scope":
                        continue
                    need(case["id"] not in expected, "TypeScript frozen baseline inventory repeats an id")
                    expected.add(case["id"])
                    self.ts_baseline_cases[case["id"]] = (group, case)
            actual = {row.get("id") for row in rows}
        need(expected, f"{language} complete baseline inventory is empty", "unrun")
        need(actual == expected, f"{language} baseline named inventory differs: {len(expected - actual)} missing, {len(actual - expected)} unexpected", "unrun" if expected - actual else "failed")
        if language == "python":
            for row in rows:
                key = (row.get("source"), row.get("id"), row.get("variant"))
                if key in self.baseline_requests:
                    need(row.get("qa_compile_request_sha256") == row.get("baseline_compile_request_sha256") == self.baseline_requests[key], "Python baseline request hash differs from source-bound immutable input")

    def plan_rows(self) -> None:
        plan = self.contracts["acceptance-plan.json"]
        target_checks = [lambda: self.combine("target_case/"), lambda: self.combine("negative_case/RR"), lambda: self.combine("python_pair/"), self.cli]
        scope_checks = [lambda: self.negative_scope_checks(), lambda: self.combine("scope_case/"), lambda: self.combine("scope_case/"), lambda: self.combine("scope_case/")]
        for category, actions in (("target_selection_checks", target_checks), ("scope_checks", scope_checks)):
            for index, description in enumerate(plan.get(category, []), 1):
                action = actions[index - 1] if index <= len(actions) else lambda: (_ for _ in ()).throw(EvidenceError("unknown plan check has no independent audit implementation", "unrun"))
                self.row(f"{category}/{index}", action, description=description)
        for index, description in enumerate(plan.get("bundle_checks", []), 1):
            self.row(f"bundle_checks/{index}", lambda: self.combine("bundle_case/"), description=description)
        for key, description in plan.get("assemble_refusals", {}).items():
            self.row(f"assemble_refusals/{key}", lambda: self.combine("assemble_refusals/recipes/"), description=str(description))
        actions = {
            "python_unchanged": [lambda: self.baseline("python"), lambda: self.controls("react"), lambda: self.other_controls()],
            "rust_unchanged": [lambda: self.baseline("rust"), lambda: self.rt_diagnostics()],
            "typescript_unchanged": [lambda: self.baseline("ts"), lambda: self.ts_diagnostics()],
        }
        for category, checks in actions.items():
            for index, description in enumerate(plan.get(category, {}).get("checks", []), 1):
                action = checks[index - 1] if index <= len(checks) else lambda: (_ for _ in ()).throw(EvidenceError("unknown unchanged check", "unrun"))
                self.row(f"{category}/{index}", action, description=description)
        for key, description in plan.get("issue_gates", {}).items():
            def check(key: str = key) -> tuple[str, str]:
                if key == "release":
                    return "post_acceptance", "rehearsal, rollback, default switch, tag and release follow independent acceptance and human approval"
                if key == "phase1-contract":
                    return self.combine("contract/")
                if key == "phase1-target-amendments":
                    return self.target_amendments()
                if key == "phase1-renderer":
                    return self.combine("bundle_case/")
                prefixes = ("target_selection_checks/", "scope_checks/", "python_unchanged/", "rust_unchanged/", "typescript_unchanged/") if key == "phase1-target" else ("bundle_checks/", "assemble_refusals/", "product_gates/") if key == "phase1-products" else ("integrity/", "contract/", "target_selection_checks/", "scope_checks/", "bundle_checks/", "assemble_refusals/", "product_gates/", "python_unchanged/", "rust_unchanged/", "typescript_unchanged/") if key == "phase1-acceptance" else ()
                need(prefixes, f"unknown issue gate {key}", "unrun")
                results = [self.combine(prefix) for prefix in prefixes]
                for status in ("failed", "unrun", "attested"):
                    if any(result[0] == status for result in results):
                        return status, "derived from the named raw-evidence check rows"
                return "passed", "derived from the named raw-evidence check rows"
            self.row(f"issue_gates/{key}", check, description=description)

    def other_controls(self) -> tuple[str, str]:
        results = [self.controls(language) for language in ("rust", "ts")]
        return ("attested", "some refusal controls retain only per-case booleans") if any(result[0] == "attested" for result in results) else ("passed", "both prior-language control inventories match")

    def negative_scope_checks(self) -> tuple[str, str]:
        selected = [row for row in self.rows if row["id"].startswith(("negative_case/RS", "negative_case/RP"))]
        need(selected, "scope negative cases missing", "unrun")
        for status in ("failed", "unrun", "attested"):
            if any(row["status"] == status for row in selected):
                return status, "derived from all named RS and RP diagnostics"
        return "passed", "all named RS and RP raw diagnostics match"

    def rt_diagnostics(self) -> tuple[str, str]:
        cases = object_value(self.reader.json("som-rs/contracts/rust-output-v1/negative-cases.json"), "Rust negative cases")["cases"]
        selected = [case for case in cases if case["id"].startswith("RT")]
        need(selected, "RT inventory missing", "unrun")
        for case in selected:
            self.diagnostic(self.find("rt-diagnostics", case["id"]), case["expected"], case["http_status"], "rt-diagnostics", case["body"])
        return "passed", "every named RT raw diagnostic matches the amended contract"

    def ts_diagnostics(self) -> tuple[str, str]:
        document = object_value(self.reader.json("som-rs/contracts/typescript-output-v1/negative-cases.json"), "TypeScript negative cases")
        for case in document["cases"]:
            self.diagnostic(self.find("ts-target", case["id"], "negative_cases"), case["expected"], case["http_status"], "ts-target", case["body"])
        return "passed", "every named TT/TS/TP raw diagnostic matches the amended contract"

    def target_amendments(self) -> tuple[str, str]:
        for language, number in (("rust", "006"), ("typescript", "002")):
            doc = object_value(self.reader.json(f"{EVIDENCE}/{language}-output-contract-amendment-{number}.json"), "target amendment")
            need(isinstance(doc.get("independent_review"), dict) and doc["independent_review"], "target amendment independent review missing", "unrun")
            files = object_value(doc.get("files"), "target amendment files")
            need(files, "target amendment file map empty")
            for path, expected in files.items():
                need(self.reader.hash(path) == expected, "target amendment file hash differs")
        return "attested", "target amendment file hashes checked; review and human approval are recorded attestations"

    def run_audit(self) -> dict:
        self.row("contract/data", self.read_contracts)
        if self.contracts and "acceptance-plan.json" in self.contracts:
            self.row("contract/sealed", self.sealed_contract)
            for name, action in (("identity", self.identities), ("receipts", self.receipts), ("target", self.target_cases), ("python-pairs", self.python_pairs), ("scope", self.scope_cases), ("bundles", self.bundles), ("products", self.products)):
                self.row(f"audit_stage/{name}", action)
            self.row("audit_stage/plan", self.plan_rows)
        totals = dict.fromkeys(STATUSES, 0)
        for row in self.rows:
            totals[row["status"]] += 1
        return {"version": VERSION, "run": self.run.relative_to(self.repo).as_posix(), "plan_sha256": self.reader.files.get(f"{CONTRACT}/acceptance-plan.json"), "rows": self.rows, "totals": totals, "files": dict(sorted(self.reader.files.items())), "missing_files": sorted(self.reader.missing), "limitations": ["Current source bytes are checked against recorded before/after identities. The caller supplies a clean committed checkout; Git object storage is not independently inspected.", "Only human review and approval metadata is attested. Operational checks require raw evidence. Aggregate tool verdicts are never acceptance evidence."]}


def required_command(rows: list[dict], name: str) -> str:
    return next(row["command"] for row in rows if row["step"] == name)


def run_audit(repo: Path, run: Path) -> dict:
    return Audit(repo, run).run_audit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve(strict=True)
        reader = Reader(repo)
        run = reader.path(args.run)
        out = args.out if args.out.is_absolute() else repo / args.out
        need(".." not in out.parts, "output traversal is refused")
        for path in (out, *out.parents):
            need(not path.is_symlink(), "symlink output is refused")
        need(not out.exists(), "--out already exists")
        need(out != run and not out.is_relative_to(run), "audit output must be outside the immutable run directory")
        report = run_audit(repo, run)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"report": str(out), "totals": report["totals"]}))
        return 0 if report["totals"]["failed"] == 0 and report["totals"]["unrun"] == 0 else 1
    except (EvidenceError, OSError, ValueError, TypeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
