#!/usr/bin/env python3
"""Build the phase 2 construct table of the Rust output contract (issue #52).

Every compiler-v1 construct is listed with the versions that admit it, the issue that owns its Rust
form for each version, and the Rust form. The constructs are read from the compiler-v1 contract:
each prompt pointer of field-statement-map.json, and each enum or const value of
compiler-prompt.schema.json (field types, defaults, sentences, capabilities, operations, flow
inputs, operations, nodes, undo, predicates and literal types).

The script fails when a construct matches no Rust form, when a pointer matches several forms, and
when a form matches no construct. So the table assigns every construct exactly once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
REPO = SOM_RS.parent
COMPILER = SOM_RS / "contracts" / "compiler-v1"
MAP = COMPILER / "field-statement-map.json"
SCHEMA = COMPILER / "compiler-prompt.schema.json"
OUT = SOM_RS / "contracts" / "rust-output-v1" / "phase2" / "constructs.json"

FLOW_POINTERS = ("/flow/", "runtime_reference/")
FLOW_FAMILIES = {"flow_input_type", "flow_operation", "flow_node", "flow_undo", "predicate", "literal_type"}

# Rust forms of the prompt pointers. A key is a pattern over the pointer segments, where a *
# segment matches any one segment; every pointer matches exactly one key.
POINTER_FORMS = {
    "/version": "Selects the product shape and is never a Rust identifier. v1 to v5 without capabilities keep the phase 1 layout of product-layout.json; v5 with capabilities, v6 and v7 use the phase 2 layout of CONTRACT.md section 10.",
    "/package": "Never a Rust identifier: the Cargo package and the binary stay som_app. Every string the Python product derives from the package is the same in Rust (naming.json W5): README.md, the session cookie som_<package>, the login cookie som_<package>_login and the Redis key prefix <REDIS_NAMESPACE>:<package>:.",
    "/contract/*": "Checked by compiler-v1 only. It selects the v6 or v7 product shape and adds nothing else to the product.",
    "/resource/name": "Never a Rust identifier (naming.json R1). Every wire string the Python product derives from it is the same in Rust.",
    "/resources/*/name": "Never a Rust identifier (naming.json R1). Every wire string the Python product derives from it is the same in Rust.",
    "/resource/table": "The PostgreSQL table, verbatim and double-quoted in SQL (naming.json W3).",
    "/resources/*/table": "The PostgreSQL table or the MongoDB collection, verbatim (naming.json W3).",
    "/resource/path": "The route prefix, verbatim (naming.json W2).",
    "/resources/*/path": "The route prefix, verbatim; the child routes nest under <parent path>/{parent_id} as in Python (naming.json W2).",
    "/resource/fields/*/name": "The JSON member and the SQL column, verbatim; the Rust field follows naming.json R2.",
    "/resources/*/fields/*/name": "The JSON member, the SQL column and the MongoDB document field, verbatim; the Rust field follows naming.json R2.",
    "/resource/fields/*/type": "The field_type construct of the same value.",
    "/resources/*/fields/*/type": "The field_type construct of the same value.",
    "/resource/fields/*/required": "Required in create bodies, NOT NULL, and Option<T> when optional, with the Python validation errors.",
    "/resources/*/fields/*/required": "Required in create bodies, NOT NULL (PostgreSQL) or always present (MongoDB), and Option<T> when optional, with the Python validation errors.",
    "/resource/fields/*/unique": "A unique constraint with the Python name and the Python 409 on conflict.",
    "/resources/*/fields/*/unique": "A unique constraint with the Python name (PostgreSQL) or the Python partial unique index on the field's BSON type (MongoDB), and the Python 409 on conflict.",
    "/resource/fields/*/default": "The field_default construct of the same value.",
    "/resources/*/fields/*/default": "The field_default construct of the same value.",
    "/query/*": "The list query parameter of the Python product: same name, parsing, bounds, ordering and index (naming.json W4). A v3 filter field named like a fixed list parameter is refused by compiler-v1 for every target (python_name_collisions, CONTRACT.md section 12).",
    "/resources/*/query/*": "The list query parameter of the Python product: same name, parsing, bounds, ordering and index (PostgreSQL index or MongoDB query index) (naming.json W4).",
    "/relation/foreign_key": "The child column or document field, verbatim; the Rust field follows naming.json R2.",
    "/relation/on_delete": "Restrict: deleting a parent that has children gives the Python status and detail. PostgreSQL uses the Python foreign key; MongoDB checks inside the Python transaction.",
    "/migration/*": "PostgreSQL: the schema of the Python migration (tables, columns, defaults, indexes and constraints; migration bookkeeping tables are not compared). MongoDB: the Python migrate upgrade (indexes) and downgrade (drops the same collections, only on the isolated test database). `som_app migrate upgrade|downgrade` runs them (services.json).",
    "/capabilities/database": "The capability_database construct of the same value.",
    "/capabilities/auth": "The capability_auth construct of the same value.",
    "/capabilities/storage": "The capability_storage construct of the same value.",
    "/capabilities/workflow": "The capability_workflow construct of the same value.",
    "/capabilities/value_field": "The workflow reads this field by its wire name, as the Python product does.",
    "/capabilities/group_field": "The workflow groups by this field by its wire name, as the Python product does.",
    "/logic/*": "The legacy_logic construct of the same sentence.",
    "/logic/*/*": "The related_logic construct of the same scope and sentence.",
    "/tests/0": "The check_sentence construct of the same version.",
    "/operations/*/scope": "The resource of the operation construct: the parent or child routes, or the batch route.",
    "/operations/*/action": "The operation construct of the same action on the resource of its scope.",
    "/flow/name": "The route segment of POST /flows/{name}/runs, verbatim; never a Rust identifier (naming.json R3).",
    "/flow/inputs/*/name": "The JSON member of the run body, verbatim (naming.json W1).",
    "/flow/inputs/*/type": "The flow_input_type construct of the same value.",
    "/flow/steps/*/name": "The step key in the run record and the journal, verbatim; never a Rust identifier (naming.json R3).",
    "/flow/steps/*/op": "The flow_operation or flow_node construct of the same value.",
    "/flow/steps/*/args/*": "The argument value of the operation, resolved from the runtime reference at run time as Python resolves it.",
    "/flow/steps/*/undo": "The flow_undo construct of the same value.",
    "/flow/steps/*/steps": "The transaction body: the flow_node transaction construct.",
    "/flow/steps/*/condition/*": "The predicate construct and its operands, compared with the Python value semantics (types, null and float sign).",
    "/flow/steps/*/then": "The then arm: the flow_node if construct.",
    "/flow/steps/*/else": "The else arm: the flow_node if construct.",
    "/flow/steps/*/exports/*/*": "The branch export: the value of the taken arm, recorded under the export name as Python records it.",
    "/flow/result/*": "A member of the run result, verbatim name, with the Python value encoding.",
    "runtime_reference/*": "A run-time value: input, step field or literal, with the Python resolution, type and encoding (literal_type).",
}

# Rust forms of the enum values, by family and value.
VALUE_FORMS: dict[str, dict[str, str]] = {
    "field_type": {
        "str": "String; VARCHAR(255) in PostgreSQL; the Python BSON string in MongoDB; the Python length limit.",
        "int": "i32 in PostgreSQL products (INTEGER) and the Python integer range in MongoDB products, with the Python bounds errors.",
        "float": "f64; DOUBLE PRECISION in PostgreSQL; the Python BSON double in MongoDB; the float-zero sign as Python keeps it.",
        "bool": "bool; BOOLEAN in PostgreSQL; the Python BSON bool in MongoDB.",
        "date": "chrono::NaiveDate; DATE in PostgreSQL; the Python stored form in MongoDB; ISO 8601 on the wire.",
    },
    "field_default": {
        "null": "No default: optional fields are null when absent, as in Python.",
        "false": "DEFAULT false for the bool field, applied when absent, as in Python.",
    },
    "capability_database": {
        "postgresql": "PostgreSQL through sqlx as in phase 1, with the capability product tables of the Python migration (for v7 also _som_flow_runs and _som_flow_leases with the Python columns, naming.json W6).",
        "mongodb": "MongoDB with the driver chosen by #53. Requires a replica set or mongos as Python does. Collections are the prompt tables; integer ids come from _som_counters and revisions from _som_revision as in Python (naming.json W6). Unique and query indexes, transactions, and errors as Python.",
    },
    "capability_auth": {
        "jwt": "RS256 bearer tokens checked with JWT_PUBLIC_KEY_FILE, JWT_ISSUER and JWT_AUDIENCE; RSA keys of at least 2048 bits; claims sub, iat, exp, iss and aud; role=editor for writes; the Python 401 and 403 answers.",
        "firebase": "Firebase ID tokens for FIREBASE_PROJECT_ID, with FIREBASE_AUTH_EMULATOR_HOST as a test-only loopback override; revoked or disabled users are rejected; the custom role claim editor allows writes; the Python 401 and 403 answers.",
        "google_oauth": "The Python OIDC code flow: /auth/google/login, /auth/google/callback, /auth/me and POST /auth/logout; sessions and one-use login state in Redis under the Python key prefix; the Python cookie names; X-CSRF-Token from /auth/me for writes; GOOGLE_EDITOR_SUBJECTS lists editors; HTTPS redirect outside tests; SOM_TEST_OIDC_ISSUER as a test-only loopback override.",
    },
    "capability_storage": {
        "none": "The /objects routes and answers of the Python product without an object store.",
        "s3": "S3 objects in S3_BUCKET with the SDK credential chain and AWS_REGION (default us-east-1); SOM_TEST_S3_ENDPOINT as a test-only loopback override with path-style addressing. PUT, GET and DELETE /objects/{key}: safe keys of 1 to 80 characters, at most 1 MiB, SHA-256 on PUT, same-body retry safe, a different body gives 409.",
        "gcs": "GCS objects in GCS_BUCKET of GCS_PROJECT with application default credentials; STORAGE_EMULATOR_HOST as a test-only loopback override. The same /objects rules as s3.",
    },
    "capability_workflow": {
        "json_export": "GET <parent path>/{parent_id}/export: up to 1000 children in id order, 413 above; the Python media type, headers and bytes.",
        "gzip_export": "The json_export rules, gzip-encoded with the Python bytes.",
        "csv": "The CSV export with the Python bytes, and POST <parent path>/{parent_id}/import with text/csv: 1 to 20 rows, headers equal to the custom child fields, \\N for null, all rows or none.",
        "decimal_summary": "The Python decimal summary of value_field (grouped by group_field when set), with the Python decimal encoding.",
        "statistics": "The Python statistics of value_field (grouped by group_field when set), with the Python float encoding.",
    },
    "contract": {
        "related-business.v1": "The v6 product shape: the operations construct selects the routes.",
        "related-flow.v1": "The v7 product shape: the v6 shape plus the flow routes POST /flows/{name}/runs (with Idempotency-Key), GET /flow-runs/{run_id} and POST /flow-runs/{run_id}/recover.",
    },
    "operation": {
        "create": "POST on the collection route with the Python body, status and errors.",
        "get": "GET on the item route with the Python 404.",
        "list": "GET on the collection route with the Python query parameters and order.",
        "patch": "PATCH on the item route with the Python partial update and updated_at rule.",
        "delete": "DELETE on the item route with the Python status, 404 and restrict rule.",
        "batch_create": "POST <parent path>/with-children: a parent and 1 to 20 children in one transaction, the {parent, children} wrappers, rollback on any failure.",
    },
    "flow_input_type": {
        "boolean": "A JSON boolean member of the run body.",
        "integer": "A JSON integer member with the Python range.",
        "float": "A JSON number member with the Python float rules.",
        "string": "A JSON string member.",
        "date": "An ISO 8601 date member.",
        "parent_input": "An object with the parent create fields and the Python validation.",
        "child_input": "An object with the child create fields and the Python validation.",
        "parent_patch": "An object with the parent patch fields and the Python validation.",
        "child_patch": "An object with the child patch fields and the Python validation.",
        "children_input": "An array of 1 to 20 child create objects.",
    },
    "flow_operation": {},  # filled from flow_operations below
    "flow_node": {
        "transaction": "A database transaction around its steps with the Python commit and rollback boundaries; at most 2, not nested, as compiler-v1 admits.",
        "if": "A branch: the predicate picks the then or else arm at run time, pure literal comparisons stay folded as compiler-v1 folds them, and exports take the value of the taken arm.",
    },
    "flow_undo": {
        "null": "No undo for the step.",
        "delete_created": "After a later failure, delete what the step created, in the Python journal order, to the Python final state.",
        "restore_patch": "After a later failure, restore the fields the step patched, in the Python journal order, to the Python final state.",
    },
    "predicate": {
        "is_null": "True when the left value is null.",
        "eq": "Equality with the Python type and float-zero rules.",
        "ne": "Inequality with the Python type and float-zero rules.",
        "lt": "Less than, on the types compiler-v1 admits.",
        "le": "At most, on the types compiler-v1 admits.",
        "gt": "Greater than, on the types compiler-v1 admits.",
        "ge": "At least, on the types compiler-v1 admits.",
    },
    "literal_type": {
        "boolean": "A boolean literal.",
        "integer": "An integer literal.",
        "float": "A float literal; the bundle and the generated literal keep the sign of zero, and the PostgreSQL JSONB limit on -0.0 stays recorded.",
        "string": "A string literal, escaped as a Rust string literal.",
        "date": "A date literal.",
        "bytes": "A bytes literal with the Python encoding.",
    },
    "legacy_logic": {},  # one form for every sentence, below
    "related_logic": {},
    "check_sentence": {},
}
LEGACY_LOGIC_FORM = "The route and behavior of the Python product for this sentence (phase 1 for PostgreSQL)."
RELATED_LOGIC_FORM = "The route and behavior of the Python v5 product for this scoped sentence."
CHECK_FORM = "The generated tests in tests/ cover these checks; `cargo test` runs them against TEST_DATABASE_URL and a missing service fails them."


def pointer_matches(pointer: str, pattern: str) -> bool:
    parts, wanted = pointer.split("/"), pattern.split("/")
    return len(parts) == len(wanted) and all(w in ("*", p) for p, w in zip(parts, wanted))


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def owners(versions: list[int], flow: bool, capability_only: bool) -> dict[str, list[str]]:
    """The issue that owns the Rust form, per prompt version."""
    out: dict[str, list[str]] = {}
    for version in versions:
        if version <= 4:
            out[f"v{version}"] = ["phase1"]
        elif version == 5:
            out["v5"] = ["#53"] if capability_only else ["phase1", "#53"]
        elif version == 6:
            out["v6"] = ["#53"]
        else:
            out["v7"] = ["#54"] if flow else ["#53"]
    return out


def schema_versions(schema: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(int(branch["properties"]["version"]["const"].rsplit("v", 1)[1]), branch) for branch in schema["oneOf"]]


def enum_values(schema: dict[str, Any]) -> dict[str, dict[str, set[int]]]:
    """family -> value -> versions, from the schema."""
    found: dict[str, dict[str, set[int]]] = {}

    def add(family: str, value: Any, version: int) -> None:
        key = "null" if value is None else ("false" if value is False else (json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)))
        found.setdefault(family, {}).setdefault(key, set()).add(version)

    defs = schema["$defs"]
    for version, branch in schema_versions(schema):
        props = branch["properties"]
        resources = [props["resource"]] if "resource" in props else [props["resources"]["properties"][r] for r in ("parent", "child")]
        for resource in resources:
            field = resource["properties"]["fields"]["items"]["properties"]
            for value in field["type"]["enum"]:
                add("field_type", value, version)
            for value in field.get("default", {}).get("enum", []):
                add("field_default", value, version)
        for value in props.get("logic", {}).get("items", {}).get("enum", []):
            add("related_logic" if isinstance(value, dict) else "legacy_logic", value, version)
        if "tests" in props:
            add("check_sentence", props["tests"]["prefixItems"][0]["const"], version)
        if "capabilities" in props:
            for name in ("database", "auth", "storage", "workflow"):
                for value in props["capabilities"]["properties"][name]["enum"]:
                    add("capability_" + name, value, version)
        if "contract" in props:
            add("contract", props["contract"]["properties"]["id"]["const"], version)
        if "operations" in props:
            first, batch = props["operations"]["items"]["oneOf"]
            for action in first["properties"]["action"]["enum"]:
                add("operation", action, version)
            add("operation", batch["properties"]["action"]["const"], version)
        if "flow" in props:
            for value in props["flow"]["properties"]["inputs"]["items"]["properties"]["type"]["enum"]:
                add("flow_input_type", value, version)
            step, transaction, branch_node = defs["node"]["oneOf"]
            for value in step["properties"]["op"]["enum"]:
                add("flow_operation", value, version)
            for value in step["properties"]["undo"]["enum"]:
                add("flow_undo", value, version)
            add("flow_node", transaction["properties"]["op"]["const"], version)
            add("flow_node", branch_node["properties"]["op"]["const"], version)
            add("predicate", defs["predicate"]["oneOf"][0]["properties"]["op"]["const"], version)
            for value in defs["predicate"]["oneOf"][1]["properties"]["op"]["enum"]:
                add("predicate", value, version)
            for value in defs["runtime_ref"]["oneOf"][2]["properties"]["type"]["enum"]:
                add("literal_type", value, version)
    return found


def build() -> dict[str, Any]:
    field_map = json.loads(MAP.read_text())
    schema = json.loads(SCHEMA.read_text())
    errors: list[str] = []

    used_patterns: set[str] = set()
    pointer_rows = []
    for entry in field_map["mapping"]:
        pointer = entry["prompt_pointer"]
        matches = [p for p in POINTER_FORMS if pointer_matches(pointer, p)]
        if len(matches) != 1:
            errors.append(f"pointer {pointer} matches {len(matches)} Rust forms: {matches}")
            continue
        used_patterns.add(matches[0])
        flow = pointer.startswith(FLOW_POINTERS)
        pointer_rows.append({"id": "pointer:" + pointer, "pointer": pointer, "statement": entry["statement"],
                             "versions": entry["versions"],
                             "owner": owners(entry["versions"], flow, pointer.startswith("/capabilities/")),
                             "rust_form": POINTER_FORMS[matches[0]]})
    errors += [f"Rust form {p} matches no pointer" for p in POINTER_FORMS if p not in used_patterns]

    operation_forms = VALUE_FORMS["flow_operation"]
    for op, spec in field_map["flow_operations"].items():
        args = ", ".join(f"{k}: {v}" for k, v in spec["args"].items()) or "no arguments"
        operation_forms[op] = (f"The Python operation {op} ({args}) giving {spec['output_type']}, through the same "
                               f"route logic, authorization and errors as the HTTP route; required undo: "
                               f"{spec['required_undo'] or 'none'}.")

    value_rows = []
    found = enum_values(schema)
    for family, values in sorted(found.items()):
        forms = VALUE_FORMS.get(family)
        if forms is None:
            errors.append(f"family {family} has no Rust forms")
            continue
        for value, versions in sorted(values.items()):
            if family == "legacy_logic":
                form = LEGACY_LOGIC_FORM
            elif family == "related_logic":
                form = RELATED_LOGIC_FORM
            elif family == "check_sentence":
                form = CHECK_FORM
            elif family == "operation" and value in forms:
                form = forms[value]
            else:
                form = forms.get(value)
            if not form:
                errors.append(f"{family} {value} has no Rust form")
                continue
            ordered = sorted(versions)
            value_rows.append({"id": f"{family}:{value}", "family": family, "value": value, "versions": ordered,
                               "owner": owners(ordered, family in FLOW_FAMILIES, family.startswith("capability_")),
                               "rust_form": form})
        errors += [f"{family} {v} is not a compiler-v1 value" for v in forms if v not in values]

    # The map's own value lists must agree with the schema.
    for family, listed in (("field_type", field_map["field_types"]), ("flow_input_type", field_map["input_types"]),
                           ("literal_type", field_map["literal_types"]),
                           ("flow_operation", list(field_map["flow_operations"])),
                           ("predicate", list(field_map["predicates"].values()))):
        if set(listed) != set(found[family]):
            errors.append(f"{family}: the field map lists {sorted(listed)}, the schema {sorted(found[family])}")
    if errors:
        raise SystemExit("unassigned or unknown constructs:\n  " + "\n  ".join(errors))

    rows = pointer_rows + value_rows
    unassigned = [r["id"] for r in rows if not r["rust_form"] or not r["owner"]]
    owner_counts: dict[str, int] = {}
    for row in rows:
        for names in row["owner"].values():
            for name in names:
                owner_counts[name] = owner_counts.get(name, 0) + 1
    return {
        "version": "som.rust-output-phase2-constructs.v1",
        "rules": [
            "Every prompt pointer of field-statement-map.json and every enum or const value of compiler-prompt.schema.json is one construct below. The builder fails if any construct has no Rust form, if a pointer matches several forms, or if a form matches no construct.",
            "owner names the issue that delivers the Rust form for each prompt version: phase1 is the delivered phase 1 output (#46 to #51); #53 owns capability products (v5 with capabilities, v6, and the non-flow part of v7); #54 owns v7 flows. A v5 construct outside the capabilities block belongs to phase 1 for a product without capabilities and to #53 for a product with capabilities.",
            "A Rust form that says 'as Python' or 'the Python ...' means the same observable result as the accepted Python product for the same prompt under the phase 2 behavior suite (behavior.json).",
            "The crates and their versions and features for MongoDB, authentication, object storage, workflows and flows are fixed by #53 in amendment 003: toolchain.json phase2_crates and reference-phase2/.",
        ],
        "sources": {rel(MAP): sha(MAP), rel(SCHEMA): sha(SCHEMA)},
        "counts": {"constructs": len(rows), "pointers": len(pointer_rows), "values": len(value_rows),
                   "unassigned": len(unassigned), "owner_rows": dict(sorted(owner_counts.items()))},
        "constructs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("stale: " + rel(OUT))
            return 1
        print("checked phase2/constructs.json")
        return 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text)
    print("wrote phase2/constructs.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
