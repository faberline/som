#!/usr/bin/env python3
"""Build the hand-authored Rust phase 1 coverage prompts and their CNL.

The prompts are written here, not sampled from a corpus. CNL is produced by
the independent compiler-v1 serializer, so the CNL and JSON forms describe the
same typed prompt. Run with --check to confirm the saved files are current.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONTRACT_DIR = ROOT / "contracts" / "compiler-v1"
EXAMPLES = CONTRACT_DIR / "examples"
CASES = HERE / "cases"

sys.path.insert(0, str(ROOT / "tests" / "compiler-v1"))
from serializer import serialize_prompt  # type: ignore[import-not-found]  # noqa: E402


_ABSENT: object = object()


def field(name: str, type_: str, required: bool, unique: bool = False, default: object = _ABSENT) -> dict:
    out = {"name": name, "type": type_, "required": required, "unique": unique}
    if default is not _ABSENT:
        out["default"] = default
    return out


def example(name):
    return json.loads((EXAMPLES / f"{name}.prompt.json").read_text())


def standalone(base, version_example, package, name, path, table, fields, revision, query=None):
    prompt = copy.deepcopy(example(version_example))
    prompt["package"] = package
    prompt["resource"] = {"fields": fields, "name": name, "path": path, "table": table}
    prompt["migration"] = {"down_revision": None, "revision": revision}
    if query is not None:
        prompt["query"] = query
    assert prompt["version"] == base
    return prompt


def related(package, parent, child, foreign_key, revision):
    prompt = copy.deepcopy(example("v5"))
    prompt["package"] = package
    prompt["resources"] = {"parent": parent, "child": child}
    prompt["relation"] = {"foreign_key": foreign_key, "on_delete": "restrict"}
    prompt["migration"] = {"revision": revision, "down_revision": None}
    return prompt


def resource(name, table, path, fields, filter_field, date_field, max_limit):
    return {"name": name, "table": table, "path": path, "fields": fields,
            "query": {"filter_field": filter_field, "date_field": date_field, "max_limit": max_limit}}


def cases():
    yield "cov-v1-types", "Every v1 field type with required, optional and unique fields.", standalone(
        "som.prompt.v1", "v1", "inventory", "Part", "/parts", "parts", [
            field("sku", "str", True, True),
            field("bin", "int", True, True),
            field("weight", "float", True),
            field("fragile", "bool", True),
            field("note", "str", False),
            field("count", "int", False),
            field("ratio", "float", False),
            field("checked", "bool", False),
        ], "cov1000")
    yield "cov-v2-types", "v2 with the largest page bound and every field type.", standalone(
        "som.prompt.v2", "v2", "catalog", "Product", "/products", "products", [
            field("category", "str", True),
            field("code", "str", True, True),
            field("price", "float", True),
            field("stock", "int", False),
            field("active", "bool", False),
        ], "cov2000", {"filter_field": "category", "max_limit": 100})
    yield "cov-v3-types", "v3 with int, float and unique fields beside the bool filter.", standalone(
        "som.prompt.v3", "v3", "tickets", "Ticket", "/tickets", "tickets", [
            field("subject", "str", True, True),
            field("code", "int", True, True),
            field("priority", "int", True),
            field("estimate", "float", False, default=None),
            field("note", "str", False, default=None),
            field("closed", "bool", False, default=False),
        ], "cov3000", {"filter_field": "closed", "max_limit": 50})
    yield "cov-v4-types", "v4 with int, float and unique fields beside the bool and date filters.", standalone(
        "som.prompt.v4", "v4", "events", "Event", "/events", "events", [
            field("name", "str", True),
            field("capacity", "int", True, True),
            field("price", "float", False, default=None),
            field("public", "bool", False, default=False),
            field("starts_on", "date", False, default=None),
        ], "cov4000", {"date_field": "starts_on", "filter_field": "public", "max_limit": 20})
    yield "cov-v5-min", "v5 with one field per resource, no filters and another foreign key name.", related(
        "boards",
        resource("Board", "boards", "/boards", [field("title", "str", True)], None, None, 20),
        resource("Card", "cards", "/cards", [field("label", "str", True)], None, None, 20),
        "board_id", "cov5000")
    yield "cov-v5-max", "v5 with eight fields per resource, every type, unique values and filters on both sides.", related(
        "billing",
        resource("Account", "accounts", "/accounts", [
            field("code", "str", True, True),
            field("name", "str", True),
            field("tier", "int", True),
            field("balance", "float", False, default=None),
            field("active", "bool", False, default=False),
            field("opened_on", "date", False, default=None),
            field("quota", "int", False, default=None),
            field("notes", "str", False, default=None),
        ], "active", "opened_on", 100),
        resource("Invoice", "invoices", "/invoices", [
            field("number", "int", True, True),
            field("memo", "str", True),
            field("amount", "float", True),
            field("paid", "bool", False, default=False),
            field("due_on", "date", False, default=None),
            field("reference", "str", False, default=None),
            field("discount", "float", False, default=None),
            field("lines", "int", False, default=None),
        ], "paid", "due_on", 50),
        "account_id", "cov5100")


def rendered():
    statement_map = json.loads((CONTRACT_DIR / "field-statement-map.json").read_text())
    out = {}
    index = []
    for case_id, purpose, prompt in cases():
        prompt_text = json.dumps(prompt, indent=2, ensure_ascii=False) + "\n"
        cnl = serialize_prompt(prompt, statement_map)
        out[f"{case_id}.prompt.json"] = prompt_text
        out[f"{case_id}.cnl"] = cnl
        index.append({
            "id": case_id,
            "purpose": purpose,
            "prompt_version": prompt["version"],
            "prompt": f"{case_id}.prompt.json",
            "cnl": f"{case_id}.cnl",
            "prompt_file_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
            "cnl_sha256": hashlib.sha256(cnl.encode()).hexdigest(),
        })
    out["index.json"] = json.dumps({"version": "som.rust-output-coverage-cases.v1", "cases": index},
                                   indent=2, ensure_ascii=False) + "\n"
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = rendered()
    stale = []
    for name, text in files.items():
        path = CASES / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
    if stale:
        print("stale: " + ", ".join(stale))
        return 1
    print(("checked " if args.check else "wrote ") + str(len(files)) + " files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
