"""SOM v1 contracts and the Python reference assembler.

This module is an oracle for the Rust service. It never executes generated code
or writes generated files. Only registered, typed blocks may supply source.
"""

from __future__ import annotations

import json
import keyword
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

PROMPT_VERSION = "som.prompt.v1"
PROMPT_VERSION_2 = "som.prompt.v2"
PROMPT_VERSION_3 = "som.prompt.v3"
PROMPT_VERSION_4 = "som.prompt.v4"
DSL_VERSION = "som.dsl.v1"
DSL_VERSION_2 = "som.dsl.v2"
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
PATH = re.compile(r"[A-Za-z_0-9/.-]+\.py\Z")
PATH_V2 = re.compile(r"[A-Za-z_0-9/.-]+\.(?:py|toml|ini|md)\Z")
TAG = re.compile(r"\{\{([A-Za-z_][A-Za-z_0-9]*)\}\}")
SECTION = re.compile(r"\{\{#fields\}\}([\s\S]*?)\{\{/fields\}\}")
TYPES = frozenset({"str", "int", "bool", "float"})
LOGIC_STEPS = (
    "Create a row. Return 409 for a unique value conflict.",
    "Get a row by id. Return 404 if it does not exist.",
    "List rows in id order.",
    "Update supplied fields. Return 404 if the row does not exist.",
    "Delete a row. Return 404 if it does not exist.",
)
TEST_CASE = "Check CRUD, missing id, and unique conflict when supported."
QUERY_LIST_STEP = (
    "List rows with an optional exact string filter, limit, and offset in id order."
)
LOGIC_STEPS_V2 = (*LOGIC_STEPS[:2], QUERY_LIST_STEP, *LOGIC_STEPS[3:])
TEST_CASE_V2 = "Check CRUD, exact filtering, page bounds, missing id, and unique conflict when supported."
BOOLEAN_LIST_STEP = (
    "List rows with an optional exact boolean filter, limit, and offset in id order."
)
LOGIC_STEPS_V3 = (*LOGIC_STEPS[:2], BOOLEAN_LIST_STEP, *LOGIC_STEPS[3:])
TEST_CASE_V3 = (
    "Check standalone CRUD, boolean filtering, page bounds, defaults, "
    "timestamps, missing id, and migration."
)
DATE_LIST_STEP = (
    "List rows with optional exact boolean and exclusive date-before filters, "
    "limit, and offset in id order."
)
LOGIC_STEPS_V4 = (*LOGIC_STEPS[:2], DATE_LIST_STEP, *LOGIC_STEPS[3:])
TEST_CASE_V4 = (
    "Check standalone CRUD, boolean and date-before filtering, page bounds, "
    "defaults, timestamps, missing id, and migration."
)


class V1Error(ValueError):
    """A versioned prompt, DSL, or block that cannot be accepted."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _object(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise V1Error("INVALID_SCHEMA", f"{where}: expected {sorted(keys)}")
    return value


def _identifier(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or not IDENT.fullmatch(value)
        or keyword.iskeyword(value)
    ):
        raise V1Error("INVALID_VALUE", f"{where}: expected a Python identifier")
    return value


def validate_prompt(prompt: Any) -> dict[str, Any]:
    """Validate the task contract that the flagship, not SOM, decides."""
    if isinstance(prompt, dict) and prompt.get("version") == "som.prompt.v7":
        from .flow import validate
        return validate(prompt)
    if isinstance(prompt, dict) and prompt.get("version") == "som.prompt.v6":
        from .composed import validate
        return validate(prompt)
    if isinstance(prompt, dict) and prompt.get("version") == "som.prompt.v5":
        from .related import validate

        return validate(prompt)
    if not isinstance(prompt, dict) or prompt.get("version") not in {
        PROMPT_VERSION,
        PROMPT_VERSION_2,
        PROMPT_VERSION_3,
        PROMPT_VERSION_4,
    }:
        raise V1Error("UNSUPPORTED_VERSION", "prompt.version is not supported")
    is_date_service = prompt["version"] == PROMPT_VERSION_4
    is_query = prompt["version"] in {
        PROMPT_VERSION_2,
        PROMPT_VERSION_3,
        PROMPT_VERSION_4,
    }
    is_service = prompt["version"] in {PROMPT_VERSION_3, PROMPT_VERSION_4}
    p = _object(
        prompt,
        {"version", "package", "resource", "logic", "tests", "migration"}
        | ({"query"} if is_query else set()),
        "prompt",
    )
    _identifier(p["package"], "prompt.package")
    r = _object(p["resource"], {"name", "table", "path", "fields"}, "prompt.resource")
    _identifier(r["name"], "prompt.resource.name")
    _identifier(r["table"], "prompt.resource.table")
    if not isinstance(r["path"], str) or not re.fullmatch(
        r"/[a-z][a-z0-9_/-]*", r["path"]
    ):
        raise V1Error("INVALID_VALUE", "prompt.resource.path: expected a route path")
    if not isinstance(r["fields"], list) or not r["fields"]:
        raise V1Error(
            "INVALID_VALUE", "prompt.resource.fields: expected non-empty list"
        )
    seen = set()
    for i, field in enumerate(r["fields"]):
        keys = {"name", "type", "required", "unique"}
        if is_service and isinstance(field, dict) and field.get("required") is False:
            keys.add("default")
        f = _object(field, keys, f"field[{i}]")
        name = _identifier(f["name"], f"field[{i}].name")
        reserved = {"id", "metadata", "registry"}
        if is_service:
            reserved |= {"created_at", "updated_at"}
        if name in seen or name in reserved:
            raise V1Error("INVALID_VALUE", f"field[{i}].name: duplicate or reserved")
        seen.add(name)
        if (
            not isinstance(f["type"], str)
            or f["type"] not in (TYPES | {"date"} if is_date_service else TYPES)
            or not isinstance(f["required"], bool)
            or not isinstance(f["unique"], bool)
        ):
            raise V1Error("INVALID_VALUE", f"field[{i}]: invalid type or flags")
        if is_service and not f["required"]:
            default = f["default"]
            if default is not None and not (f["type"] == "bool" and default is False):
                raise V1Error(
                    "INVALID_VALUE", f"field[{i}].default: expected null or false"
                )
    if (
        not isinstance(p["logic"], list)
        or not p["logic"]
        or not all(isinstance(s, str) and s.strip() for s in p["logic"])
    ):
        raise V1Error("INVALID_VALUE", "prompt.logic: expected ordered non-empty steps")
    logic_steps = (
        LOGIC_STEPS_V4
        if is_date_service
        else LOGIC_STEPS_V3
        if is_service
        else LOGIC_STEPS_V2
        if is_query
        else LOGIC_STEPS
    )
    if len(p["logic"]) != 5 or set(p["logic"]) != set(logic_steps):
        raise V1Error(
            "UNSUPPORTED_LOGIC", "prompt.logic: expected each supported step once"
        )
    if (
        not isinstance(p["tests"], list)
        or not p["tests"]
        or not all(isinstance(s, str) and s.strip() for s in p["tests"])
    ):
        raise V1Error("INVALID_VALUE", "prompt.tests: expected non-empty cases")
    test_case = (
        TEST_CASE_V4
        if is_date_service
        else TEST_CASE_V3
        if is_service
        else TEST_CASE_V2
        if is_query
        else TEST_CASE
    )
    if p["tests"] != [test_case]:
        raise V1Error("UNSUPPORTED_TEST", "prompt.tests: unsupported test contract")
    if is_query:
        query = _object(
            p["query"],
            {"filter_field", "max_limit"}
            | ({"date_field"} if is_date_service else set()),
            "prompt.query",
        )
        field = _identifier(query["filter_field"], "prompt.query.filter_field")
        if is_date_service and field in {"before", "limit", "offset", "db"}:
            raise V1Error(
                "INVALID_VALUE", "prompt.query.filter_field: reserved query name"
            )
        if not any(
            item["name"] == field
            and item["type"] == ("bool" if is_service else "str")
            and not item["unique"]
            and (not is_service or item.get("default") is False)
            for item in r["fields"]
        ):
            raise V1Error(
                "INVALID_VALUE",
                "prompt.query.filter_field: expected a supported non-unique filter field",
            )
        limit = query["max_limit"]
        if type(limit) is not int or not 20 <= limit <= 100:
            raise V1Error("INVALID_VALUE", "prompt.query.max_limit: expected 20..100")
        if is_date_service:
            date_field = _identifier(query["date_field"], "prompt.query.date_field")
            date_fields = [item for item in r["fields"] if item["type"] == "date"]
            if (
                len(date_fields) != 1
                or date_fields[0]["name"] != date_field
                or date_fields[0]["required"]
                or date_fields[0]["unique"]
                or date_fields[0]["default"] is not None
            ):
                raise V1Error(
                    "INVALID_VALUE",
                    "prompt.query.date_field: expected the sole optional non-unique date field with null default",
                )
    migration = _object(
        p["migration"], {"revision", "down_revision"}, "prompt.migration"
    )
    _identifier(migration["revision"], "prompt.migration.revision")
    if migration["down_revision"] is not None:
        _identifier(migration["down_revision"], "prompt.migration.down_revision")
    if is_service and migration["down_revision"] is not None:
        raise V1Error("INVALID_VALUE", "prompt.migration.down_revision: expected null")
    return p


def resolve_prompt_refs(prompt: Any, dsl: Any) -> Any:
    """Copy prompt values into typed DSL slots without accepting source text."""
    if isinstance(prompt, dict) and prompt.get("version") == "som.prompt.v7":
        from .flow import resolve
        return resolve(prompt, dsl)
    p = validate_prompt(prompt)
    if p["version"] == "som.prompt.v6":
        from .composed import values as composed_values
        values = composed_values(p)
    elif p["version"] == "som.prompt.v5":
        from .related import values as related_values

        values = related_values(p)
    else:
        values = {
        "package": p["package"],
        "resource.name": p["resource"]["name"],
        "resource.table": p["resource"]["table"],
        "resource.path": p["resource"]["path"],
        "resource.fields": p["resource"]["fields"],
        "migration.revision": p["migration"]["revision"],
        "migration.down_revision": p["migration"]["down_revision"],
        }
    if p["version"] in {PROMPT_VERSION_2, PROMPT_VERSION_3, PROMPT_VERSION_4}:
        values["query.filter_field"] = p["query"]["filter_field"]
        values["query.max_limit"] = p["query"]["max_limit"]
    if p["version"] == PROMPT_VERSION_4:
        values["query.date_field"] = p["query"]["date_field"]
    result = deepcopy(dsl)
    if not isinstance(result, dict) or not isinstance(result.get("files"), list):
        raise V1Error("INVALID_SCHEMA", "dsl.files: expected a list")
    for file in result["files"]:
        if not isinstance(file, dict):
            raise V1Error("INVALID_SCHEMA", "dsl.files: expected file objects")
        if isinstance(file.get("path"), str):
            file["path"] = re.sub(
                r"\{([A-Za-z_.]+)\}",
                lambda match: (
                    str(values[match.group(1)])
                    if match.group(1) in {"package", "migration.revision"}
                    else _bad_ref(match.group(1))
                ),
                file["path"],
            )
        blocks = file.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if isinstance(block, dict) and isinstance(block.get("params"), dict):
                for key, value in block["params"].items():
                    if isinstance(value, dict) and set(value) == {"$ref"}:
                        ref = value["$ref"]
                        if not isinstance(ref, str) or ref not in values:
                            raise V1Error(
                                "INVALID_REF",
                                f"unknown prompt parameter reference {ref}",
                            )
                        block["params"][key] = deepcopy(values[ref])
    return result


def _bad_ref(ref: str) -> str:
    raise V1Error("INVALID_REF", f"unknown path reference {ref}")


def _path(value: Any, where: str, version: str = DSL_VERSION) -> str:
    if (
        not isinstance(value, str)
        or not (PATH_V2 if version == DSL_VERSION_2 else PATH).fullmatch(value)
        or value.startswith("/")
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise V1Error("INVALID_PATH", f"{where}: unsafe relative file path")
    return value


def _literal(value: Any, kind: str, where: str) -> str:
    if kind == "routes_v7":
        from .flow_compact import compile_routes
        return compile_routes(value)
    if kind == "flow_engine":
        from .flow_compact import compile_engine
        return compile_engine(value)
    if kind == "flow_program":
        from .flow_compact import compile_program
        return compile_program(value)
    if kind == "identifier":
        return _identifier(value, where)
    if kind == "string":
        if not isinstance(value, str):
            raise V1Error("INVALID_VALUE", f"{where}: expected string")
        return json.dumps(value, ensure_ascii=False)
    if kind == "nullable_string":
        if value is not None and not isinstance(value, str):
            raise V1Error("INVALID_VALUE", f"{where}: expected string or null")
        return "None" if value is None else json.dumps(value, ensure_ascii=False)
    if kind == "type":
        if not isinstance(value, str) or value not in TYPES:
            raise V1Error("INVALID_VALUE", f"{where}: unsupported type")
        return value
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise V1Error("INVALID_VALUE", f"{where}: expected integer")
        return str(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise V1Error("INVALID_VALUE", f"{where}: expected boolean")
        return str(value)
    raise V1Error("INVALID_BLOCK", f"{where}: unknown parameter type {kind}")


def _fields(value: Any, where: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise V1Error("INVALID_VALUE", f"{where}: expected non-empty fields")
    out = []
    seen = set()
    for i, item in enumerate(value):
        f = _object(item, {"name", "type", "required", "unique"}, f"{where}[{i}]")
        name = _identifier(f["name"], f"{where}[{i}].name")
        if (
            name in seen
            or name in {"id", "metadata", "registry"}
            or not isinstance(f["type"], str)
            or f["type"] not in TYPES
            or not isinstance(f["required"], bool)
            or not isinstance(f["unique"], bool)
        ):
            raise V1Error("INVALID_VALUE", f"{where}[{i}]: invalid field")
        seen.add(name)
        out.append(
            {
                "name": name,
                "name_literal": json.dumps(name),
                "type": f["type"],
                "sql_type": {
                    "str": "sa.String(255)",
                    "int": "sa.Integer",
                    "bool": "sa.Boolean",
                    "float": "sa.Float",
                }[f["type"]],
                "migration_type": {
                    "str": "sa.String(length=255)",
                    "int": "sa.Integer()",
                    "bool": "sa.Boolean()",
                    "float": "sa.Float()",
                }[f["type"]],
                "nullable": str(not f["required"]),
                "unique": str(f["unique"]),
                "create_type": f["type"] if f["required"] else f"{f['type']} | None",
                "create_default": "" if f["required"] else " = None",
                "patch_type": f"{f['type']} | None",
                "sample": {
                    "str": '"sample"',
                    "int": "3",
                    "bool": "True",
                    "float": "1.5",
                }[f["type"]],
            }
        )
    return out


def _fields_service(value: Any, where: str, allow_date: bool) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise V1Error("INVALID_VALUE", f"{where}: expected non-empty fields")
    out = []
    names = set()
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise V1Error("INVALID_SCHEMA", f"{where}[{i}]: expected field object")
        required = item.get("required")
        keys = {"name", "type", "required", "unique"}
        if required is False:
            keys.add("default")
        field = _object(item, keys, f"{where}[{i}]")
        name = _identifier(field["name"], f"{where}[{i}].name")
        ty = field["type"]
        if (
            name in names
            or name in {"id", "metadata", "registry", "created_at", "updated_at"}
            or not isinstance(ty, str)
            or ty not in (TYPES | {"date"} if allow_date else TYPES)
            or type(required) is not bool
            or type(field["unique"]) is not bool
        ):
            raise V1Error("INVALID_VALUE", f"{where}[{i}]: invalid field")
        default = field.get("default")
        if (
            not required
            and default is not None
            and not (ty == "bool" and default is False)
        ):
            raise V1Error(
                "INVALID_VALUE", f"{where}[{i}].default: expected null or false"
            )
        names.add(name)
        nullable = not required and default is None
        sql_default = ", server_default=sa.false()" if default is False else ""
        migration_default = (
            ", server_default=sa.text('false')" if default is False else ""
        )
        out.append(
            {
                "name": name,
                "name_literal": json.dumps(name),
                "type": ty,
                "sql_type": {
                    "str": "sa.String(255)",
                    "int": "sa.Integer",
                    "bool": "sa.Boolean",
                    "float": "sa.Float",
                    "date": "sa.Date",
                }[ty],
                "migration_type": {
                    "str": "sa.String(length=255)",
                    "int": "sa.Integer()",
                    "bool": "sa.Boolean()",
                    "float": "sa.Float()",
                    "date": "sa.Date()",
                }[ty],
                "nullable": str(nullable),
                "unique": str(field["unique"]),
                "sql_default": sql_default,
                "migration_default": migration_default,
                "create_type": f"{ty} | None" if nullable else ty,
                "create_default": ""
                if required
                else " = False"
                if default is False
                else " = None",
                "read_type": f"{ty} | None" if nullable else ty,
                "patch_type": f"{ty} | None",
                "required_entry": f"            {json.dumps(name)}: "
                + {
                    "str": '"sample"',
                    "int": "3",
                    "bool": "True",
                    "float": "1.5",
                    "date": '"2030-01-02"',
                }[ty]
                + ",\n"
                if required
                else "",
                "sample": {
                    "str": '"sample"',
                    "int": "3",
                    "bool": "True",
                    "float": "1.5",
                    "date": '"2030-01-02"',
                }[ty],
            }
        )
    return out


def _fields_v3(value: Any, where: str) -> list[dict[str, str]]:
    return _fields_service(value, where, False)


def _fields_v4(value: Any, where: str) -> list[dict[str, str]]:
    return _fields_service(value, where, True)


def _fields_v7(value: Any, where: str) -> list[dict[str, str]]:
    """Use the same field rules with concise, explicit template options."""
    fields = _fields_v4(value, where)
    for field in fields:
        unique = ", unique=True" if field["unique"] == "True" else ""
        field["column_options"] = unique + field["sql_default"]
        field["migration_options"] = (
            ", nullable=" + field["nullable"] + unique
            + field["migration_default"]
        )
        field["non_null_entry"] = (
            field["name_literal"] + "," if field["nullable"] == "False" else ""
        )
        field["unique_index_entry"] = (
            field["name_literal"] + ": " + json.dumps({
                "str": "string", "int": "int", "float": "double",
                "bool": "bool", "date": "date",
            }[field["type"]]) + ","
            if field["unique"] == "True" else ""
        )
    return fields


def _render(
    template: str, params: dict[str, Any], schema: dict[str, str], where: str,
    format_literals: bool = False,
) -> str:
    flow_section = re.compile(r"\{\{#flow_inputs\}\}(.*?)\{\{/flow_inputs\}\}", re.S)
    if flow_section.search(template):
        from .flow import input_fields
        rows = input_fields(params["flow_inputs"], params["parent_name"], params["child_name"])
        compact_inputs = schema.get('flow_inputs') == 'flow_inputs_v7'
        if compact_inputs:
            rows = [dict(row, variable='input_'+str(index), name_literal=json.dumps(row['name']))
                    for index, row in enumerate(rows)]
        def flow_row(match: re.Match[str]) -> str:
            allowed = {"name", "input_type", "variable", "name_literal"} if compact_inputs else {"name", "input_type"}
            if set(TAG.findall(match.group(1))) - allowed:
                raise V1Error("INVALID_BLOCK", "unsupported flow input placeholder")
            return "".join(TAG.sub(lambda tag, row=row: row[tag.group(1)], match.group(1)) for row in rows)
        template = flow_section.sub(flow_row, template)
    fields_kind = schema.get("fields")
    fields = (
        _fields_v7(params["fields"], where + ".fields")
        if fields_kind == "fields_v7"
        else
        _fields_v4(params["fields"], where + ".fields")
        if fields_kind == "fields_v4"
        else _fields_v3(params["fields"], where + ".fields")
        if fields_kind == "fields_v3"
        else _fields(params["fields"], where + ".fields")
        if fields_kind == "fields"
        else []
    )

    def section(match: re.Match[str]) -> str:
        allowed = set(fields[0]) if fields else set()
        if set(TAG.findall(match.group(1))) - allowed:
            raise V1Error("INVALID_BLOCK", f"{where}: unsupported field placeholder")
        return "".join(
            TAG.sub(lambda tag, field=field: field[tag.group(1)], match.group(1))
            for field in fields
        )

    source = SECTION.sub(section, template)
    values = {
        key: _literal(params[key], kind, f"{where}.{key}")
        for key, kind in schema.items()
        if kind not in {"fields", "fields_v3", "fields_v4", "fields_v7", "flow_inputs", "flow_inputs_v7"}
    }
    if format_literals:
        for key, kind in schema.items():
            if kind == "string" and len(values[key]) > 60:
                value = params[key]
                values[key] = "(\n" + "\n".join(
                    json.dumps(value[i:i + 40], ensure_ascii=False)
                    for i in range(0, len(value), 40)
                ) + "\n)"
    return TAG.sub(lambda tag: values[tag.group(1)], source)


def load_blocks(directory: Path) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.rglob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise V1Error("INVALID_BLOCK", f"{path}: {error}") from error
        for block in document if isinstance(document, list) else [document]:
            _add_block(blocks, block, path)
    if not blocks:
        raise V1Error("INVALID_BLOCK", f"{directory}: no blocks")
    return blocks


def _add_block(blocks: dict[str, dict[str, Any]], block: Any, path: Path) -> None:
    _object(block, {"id", "version", "imports", "params", "template"}, str(path))
    if not isinstance(block["id"], str) or not re.fullmatch(
        r"[a-z][a-z0-9_.]*", block["id"]
    ):
        raise V1Error("INVALID_BLOCK", f"{path}: invalid id")
    version_allowed = (
        block["version"] in {1, 2, 3}
        or block["version"] in {4, 5} and block["id"].startswith("compact.")
    ) if type(block["version"]) is int else False
    if not version_allowed or block["id"] in blocks:
        raise V1Error("INVALID_BLOCK", f"{path}: unsupported version or duplicate id")
    if not isinstance(block["params"], dict) or not isinstance(block["template"], str):
        raise V1Error("INVALID_BLOCK", f"{path}: invalid params or template")
    if not isinstance(block["imports"], list) or not all(
        isinstance(x, str) and x.startswith(("import ", "from "))
        for x in block["imports"]
    ):
        raise V1Error("INVALID_BLOCK", f"{path}: invalid imports")
    flow_section = re.compile(r"\{\{#flow_inputs\}\}.*?\{\{/flow_inputs\}\}", re.S)
    removed = flow_section.sub("", SECTION.sub("", block["template"]))
    if "{{#" in removed or "{{/" in removed:
        raise V1Error("INVALID_BLOCK", f"{path}: invalid section")
    used = set(TAG.findall(removed))
    used.update(tag for stmt in block["imports"] for tag in TAG.findall(stmt))
    if SECTION.search(block["template"]):
        used.add("fields")
    if flow_section.search(block["template"]):
        used.update({"flow_inputs", "parent_name", "child_name"})
    if used != set(block["params"]):
        raise V1Error(
            "INVALID_BLOCK", f"{path}: template parameters disagree with schema"
        )
    if any(
        kind
        not in {
            "identifier",
            "string",
            "nullable_string",
            "type",
            "integer",
            "boolean",
            "fields",
            "fields_v3",
            "fields_v4",
            "fields_v7",
            "flow_inputs",
            "flow_inputs_v7",
            "flow_program",
            "flow_engine",
            "routes_v7",
        }
        for kind in block["params"].values()
    ):
        raise V1Error("INVALID_BLOCK", f"{path}: unsupported parameter type")
    if "routes_v7" in block["params"].values() and not block["id"].startswith("compact."):
        raise V1Error("INVALID_BLOCK", "typed route fixtures require compact blocks")
    blocks[block["id"]] = block


def _imports(statements: list[str]) -> str:
    plain: set[str] = set()
    named: dict[str, set[str]] = {}
    for stmt in statements:
        if stmt.startswith("from "):
            module, names = stmt[5:].split(" import ", 1)
            named.setdefault(module, set()).update(
                part.strip() for part in names.split(",")
            )
        else:
            plain.add(stmt)

    def group(stmt: str) -> int:
        module = stmt.split()[1]
        if module in {"os", "unittest", "collections.abc"}:
            return 0
        return 2 if module.startswith(".") else 1

    entries = sorted(plain) + [
        f"from {module} import {', '.join(sorted(names))}"
        for module, names in sorted(named.items())
    ]
    return "\n\n".join(
        "\n".join(stmt for stmt in entries if group(stmt) == i)
        for i in range(3)
        if any(group(stmt) == i for stmt in entries)
    )


def assemble(dsl: Any, blocks: dict[str, dict[str, Any]], *,
             byte_limit: int | None = 65536) -> dict[str, str]:
    """Render a strict DSL to files without touching the filesystem."""
    d = _object(dsl, {"version", "files"}, "dsl")
    if d["version"] not in {DSL_VERSION, DSL_VERSION_2}:
        raise V1Error("UNSUPPORTED_VERSION", "dsl.version is not supported")
    limit = 12 if d["version"] == DSL_VERSION_2 else 8
    if not isinstance(d["files"], list) or not 0 < len(d["files"]) <= limit:
        raise V1Error("SCOPE_TOO_LARGE", f"dsl.files: expected 1..{limit} files")
    rendered: dict[str, str] = {}
    count = 0
    for fi, file in enumerate(d["files"]):
        f = _object(file, {"path", "blocks"}, f"dsl.files[{fi}]")
        path = _path(f["path"], f"dsl.files[{fi}].path", d["version"])
        if path in rendered or not isinstance(f["blocks"], list) or not f["blocks"]:
            raise V1Error("INVALID_SCHEMA", f"{path}: duplicate file or invalid blocks")
        imports: list[str] = []
        source: list[str] = []
        seen: set[str] = set()
        for bi, entry in enumerate(f["blocks"]):
            count += 1
            if count > 64:
                raise V1Error("SCOPE_TOO_LARGE", "dsl: more than 64 blocks")
            e = _object(entry, {"name", "id", "params", "refs"}, f"{path}.blocks[{bi}]")
            name = _identifier(e["name"], f"{path}.blocks[{bi}].name")
            if (
                name in seen
                or not isinstance(e["refs"], list)
                or not all(isinstance(ref, str) and ref in seen for ref in e["refs"])
            ):
                raise V1Error(
                    "INVALID_REF", f"{path}.{name}: duplicate name or unknown reference"
                )
            seen.add(name)
            if not isinstance(e["id"], str):
                raise V1Error(
                    "INVALID_VALUE", f"{path}.{name}: block id must be a string"
                )
            block = blocks.get(e["id"])
            if block is None:
                raise V1Error(
                    "UNSUPPORTED_BLOCK", f"{path}.{name}: unknown block {e['id']!r}"
                )
            if not isinstance(e["params"], dict) or set(e["params"]) != set(
                block["params"]
            ):
                raise V1Error(
                    "INVALID_VALUE",
                    f"{path}.{name}: parameter names disagree with block",
                )
            source.append(
                _render(
                    block["template"], e["params"], block["params"], f"{path}.{name}",
                    format_literals=block["version"] >= 2 and path.endswith(".py"),
                ).rstrip("\n")
            )
            literals = {
                key: _literal(e["params"][key], kind, f"{path}.{name}.{key}")
                for key, kind in block["params"].items()
                if kind not in {"fields", "fields_v3", "fields_v4", "fields_v7", "flow_inputs", "flow_inputs_v7"}
            }
            imports.extend(
                TAG.sub(lambda match, literals=literals: literals[match.group(1)], stmt)
                for stmt in block["imports"]
            )
        prefix = _imports(imports)
        body = ""
        for part in source:
            body += ("\n\n" if part.startswith("    ") else "\n\n\n") if body else ""
            body += part
        gap = "\n\n" if body.startswith("revision = ") else "\n\n\n"
        rendered[path] = (prefix + (gap if prefix and body else "") + body).rstrip(
            "\n"
        ) + "\n"
    if (
        d["version"] == DSL_VERSION_2
        and byte_limit is not None
        and sum(len(s.encode()) for s in rendered.values()) > byte_limit
    ):
        raise V1Error("SCOPE_TOO_LARGE", "dsl output exceeds 65536 bytes")
    return rendered
