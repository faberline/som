#!/usr/bin/env python3
"""Issue #57 and #58 fixtures: names that the generated Python code cannot carry, and `self`.

usage: self_name_fixtures.py --write   (writes self-name-fixtures.json; refuses to overwrite)
       self_name_fixtures.py --check   (the saved file equals a fresh build)

Every prompt is a public example or a CNL positive fixture with exact string values renamed
everywhere in the prompt. The file has three lists:
  positive: a field, foreign key or query field named `self`; compiler-v1 accepts it, and a
            Python bundle selects exactly python_variants (the `.self` variant block ids).
  negative: a name listed in selection-policy.json python_name_collisions for the version and
            database; compiler-v1 refuses typed JSON and CNL with status 400, INVALID_VALUE,
            the policy detail and the slot's prompt pointer.
  accepted: the same names where the policy lists no row for the version, database and roles.
The expected values come from this table and the policy text, not from a compiler.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
CONTRACT = HERE.parents[1] / "contracts" / "compiler-v1"
OUT = HERE / "self-name-fixtures.json"
sys.path.insert(0, str(HERE))
from serializer import serialize_prompt  # noqa: E402

DETAIL = "name collides with a generated Python identifier"
CNL_ALLOWED = "Rename it; this name shares a scope with an identifier of the generated Python code."
SQL = "orm.base.self"
MONGO = "orm.mongo.base.self"
MONGO_V6 = "orm.composed.mongo.base.self"
FLOW_SQL = "compact.service.flow.store.postgresql.self"


def read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def example(name: str) -> dict:
    return read(CONTRACT / "examples" / f"{name}.prompt.json")


FIXTURES = {c["id"]: c["expected_prompt"] for c in read(HERE / "cnl-positive-fixtures.json")["cases"]}
V5C = FIXTURES["PAIR_V5_RELATED_BUSINESS"]
V6M = FIXTURES["CAP_MONGODB_JWT_NONE_JSON_EXPORT"]


def rename(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {k: rename(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rename(v, old, new) for v in value]
    return new if value == old else value


def with_caps(prompt: dict, **caps: str) -> dict:
    prompt = copy.deepcopy(prompt)
    prompt["capabilities"].update(caps)
    return prompt


def unique_child(prompt: dict) -> dict:
    """The transaction check injects a late duplicate on a unique child field."""
    prompt = copy.deepcopy(prompt)
    prompt["resources"]["child"]["fields"][0]["unique"] = True
    return prompt


def renamed(prompt: dict, old: str, new: str) -> dict:
    result = rename(prompt, old, new)
    if json.dumps(result) == json.dumps(prompt):
        raise SystemExit(f"{old} does not occur")
    return result


# (id, prompt, the name renamed to self, expected python_variants)
POSITIVE = [
    ("SELF_V1_FIELD", example("v1"), "title", [SQL]),
    ("SELF_V3_FILTER_FIELD", example("v3"), "completed", [SQL]),
    ("SELF_V5_PARENT_FIELD", example("v5"), "name", [SQL]),
    ("SELF_V5_CHILD_FOREIGN_KEY", example("v5"), "project_id", [SQL]),
    ("SELF_V5_CAPS_PG_CHILD_FIELD", V5C, V5C["resources"]["child"]["fields"][0]["name"], [SQL]),
    ("SELF_V5_CAPS_MONGODB_CHILD_FIELD", unique_child(with_caps(V5C, database="mongodb")),
     V5C["resources"]["child"]["fields"][0]["name"], [MONGO]),
    ("SELF_V5_CAPS_MONGODB_FOREIGN_KEY", unique_child(with_caps(V5C, database="mongodb")),
     V5C["relation"]["foreign_key"], [MONGO]),
    ("SELF_V6_PG_PARENT_FIELD", example("v6"), "name", [SQL]),
    ("SELF_V6_PG_FOREIGN_KEY", example("v6"), "project_id", [SQL]),
    ("SELF_V6_MONGODB_CHILD_FIELD", V6M, V6M["resources"]["child"]["fields"][0]["name"], [MONGO_V6]),
    ("SELF_V6_MONGODB_FOREIGN_KEY", V6M, V6M["relation"]["foreign_key"], [MONGO_V6]),
    ("SELF_V7_PG_FOREIGN_KEY", example("v7"), "project_id", [FLOW_SQL]),
    ("SELF_V7_PG_FILTER_FIELD", example("v7"), "completed", [FLOW_SQL]),
    ("SELF_V7_MONGODB_CHILD_FIELD", with_caps(example("v7"), database="mongodb"), "title", []),
    ("SELF_V7_MONGODB_FOREIGN_KEY", with_caps(example("v7"), database="mongodb"), "project_id", []),
]

# (id, prompt, old name, new name, refused slot path or None when accepted)
NAMED = [
    ("NAME_V1_FIELD_MAPPED_COLUMN", example("v1"), "title", "mapped_column", "resource.fields[0].name"),
    ("NAME_V2_RESOURCE_BASE", example("v2"), "Item", "Base", "resource.name"),
    ("NAME_V3_FILTER_FIELD_LIMIT", example("v3"), "completed", "limit", "query.filter_field"),
    ("NAME_V3_FILTER_FIELD_OFFSET", example("v3"), "completed", "offset", "query.filter_field"),
    ("NAME_V4_FIELD_DATE", example("v4"), "description", "date", "resource.fields[2].name"),
    ("NAME_V5_PG_RESOURCE_DECLARATIVEBASE", example("v5"), "Project", "DeclarativeBase", "resources.parent.name"),
    ("NAME_V5_MONGODB_RESOURCE_DOCUMENT", with_caps(V5C, database="mongodb"),
     V5C["resources"]["parent"]["name"], "Document", "resources.parent.name"),
    ("NAME_V5_PG_FOREIGN_KEY_HASHLIB", example("v5"), "project_id", "hashlib", "relation.foreign_key"),
    ("NAME_V6_PG_FIELD_STR", example("v6"), "name", "str", "resources.parent.fields[0].name"),
    ("NAME_V6_MONGODB_RESOURCE_FASTAPI", V6M, V6M["resources"]["parent"]["name"], "FastAPI", "resources.parent.name"),
    ("NAME_V7_PG_RESOURCE_SCHEMA", example("v7"), "Todo", "SCHEMA", "resources.child.name"),
    ("NAME_V7_PG_FOREIGN_KEY_INT", example("v7"), "project_id", "int", "relation.foreign_key"),
    ("NAME_V7_MONGODB_FIELD_SET", with_caps(example("v7"), database="mongodb"), "title", "set",
     "resources.child.fields[0].name"),
    ("ACCEPT_V1_FIELD_DATE", example("v1"), "title", "date", None),
    ("ACCEPT_V5_PG_RESOURCE_DOCUMENT", example("v5"), "Project", "Document", None),
    ("ACCEPT_V7_MONGODB_RESOURCE_SCHEMA", with_caps(example("v7"), database="mongodb"), "Todo", "SCHEMA", None),
]


def pointer(path: str) -> str:
    return "/" + path.replace("[", ".").replace("]", "").replace(".", "/")


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def build() -> dict:
    statement_map = read(CONTRACT / "field-statement-map.json")
    positive = []
    for case_id, base, old, variants in POSITIVE:
        prompt = renamed(base, old, "self")
        positive.append({"id": case_id, "renamed": old, "prompt": prompt, "prompt_sha256": sha(prompt),
                         "cnl": serialize_prompt(prompt, statement_map), "python_variants": variants})
    negative, accepted = [], []
    for case_id, base, old, new, path in NAMED:
        prompt = renamed(base, old, new)
        row = {"id": case_id, "renamed": [old, new], "prompt": prompt, "prompt_sha256": sha(prompt),
               "cnl": serialize_prompt(prompt, statement_map)}
        if path is None:
            accepted.append(row)
            continue
        row["expected"] = {
            "status": 400, "code": "INVALID_VALUE", "detail": f"prompt.{path}: {DETAIL}",
            "json_prompt_pointer": "/prompt" + pointer(path), "cnl_prompt_pointer": pointer(path),
            "cnl_allowed_form": CNL_ALLOWED,
        }
        negative.append(row)
    return {
        "format": "som.compiler-v1.self-name-fixtures.v1",
        "issues": [57, 58],
        "generator": "som-rs/tests/compiler-v1/self_name_fixtures.py",
        "policy": "som-rs/contracts/compiler-v1/selection-policy.json python_name_collisions",
        "counts": {"positive": len(positive), "negative": len(negative), "accepted": len(accepted)},
        "positive": positive,
        "negative": negative,
        "accepted": accepted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=1, ensure_ascii=False) + "\n"
    if args.write:
        if OUT.exists():
            raise SystemExit(f"{OUT} exists")
        OUT.write_text(text)
        print(hashlib.sha256(text.encode()).hexdigest(), OUT)
        return 0
    same = OUT.read_text() == text
    print("ok" if same else "differs", OUT)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
