"""The bounded two-resource contract. No application source is accepted here."""

import re

from .v1 import V1Error, _fields_v4, _identifier, _object

VERSION = "som.prompt.v5"
CRUD = (
    "Create a row. Return 409 for a unique value conflict.",
    "Get a row by id. Return 404 if it does not exist.",
    "List scoped rows with the configured filters and pagination in id order.",
    "Update supplied fields. Return 404 if the row does not exist.",
    "Delete a row. Reject a parent with children with 409.",
)
BATCH = "Create a parent and 1 to 20 children in one transaction. Roll back all writes on failure."
TEST = "Check related CRUD, filters, defaults, timestamps, isolation, atomic batch, delete restriction, and migration."
OPS = ("create", "get", "list", "patch", "delete")
RESERVED = {"parent_id", "child_id", "before", "limit", "offset", "db", "date", "datetime", "model_config", "model_fields", "model_dump", "revision_id", "settings", "save", "insert", "delete", "get", "find", "find_one"}


def validate(prompt):
    keys = {"version", "package", "resources", "relation", "logic", "tests", "migration"}
    if isinstance(prompt, dict) and "capabilities" in prompt:
        keys.add("capabilities")
    p = _object(prompt, keys, "prompt")
    _identifier(p["package"], "package")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", p["package"]):
        raise V1Error("INVALID_VALUE", "invalid package")
    resources = _object(p["resources"], {"parent", "child"}, "resources")
    relation = _object(p["relation"], {"foreign_key", "on_delete"}, "relation")
    fk = _identifier(relation["foreign_key"], "relation.foreign_key")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", fk) or fk in RESERVED | {"id", "created_at", "updated_at", "metadata", "registry"}:
        raise V1Error("INVALID_VALUE", "invalid foreign key")
    if relation["on_delete"] != "restrict":
        raise V1Error("INVALID_VALUE", "only restrict deletion is supported")
    for role, resource in resources.items():
        r = _object(resource, {"name", "table", "path", "fields", "query"}, role)
        for key in ("name", "table"):
            _identifier(r[key], role + "." + key)
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]{0,39}", r["name"]) or r["name"] in {"Base", "Field", "ConfigDict", "BaseModel", "Mapped", "BatchCreate", "BatchRead"}:
            raise V1Error("INVALID_VALUE", "invalid resource class name")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", r["table"]) or r["table"] == "alembic_version":
            raise V1Error("INVALID_VALUE", "invalid table")
        if not isinstance(r["path"], str) or not re.fullmatch(r"(?:/[a-z][a-z0-9_-]*){1,3}", r["path"]) or r["path"].split("/")[1] in {"healthz", "docs", "redoc", "openapi.json", "auth", "objects"}:
            raise V1Error("INVALID_VALUE", "invalid resource path")
        if not isinstance(r["fields"], list) or not 1 <= len(r["fields"]) <= 8:
            raise V1Error("SCOPE_TOO_LARGE", "expected 1..8 fields per resource")
        _fields_v4(r["fields"], role + ".fields")
        for field in r["fields"]:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", field["name"]) or field["name"] in RESERVED | {fk} or field["name"].startswith("model_"):
                raise V1Error("INVALID_VALUE", "reserved field name")
            if field["type"] == "date" and (field["required"] or field["unique"] or field.get("default") is not None):
                raise V1Error("INVALID_VALUE", "dates must be optional with null default")
        query = _object(r["query"], {"filter_field", "date_field", "max_limit"}, role + ".query")
        if type(query["max_limit"]) is not int or not 20 <= query["max_limit"] <= 100:
            raise V1Error("INVALID_VALUE", "max_limit must be 20..100")
        fields = {field["name"]: field for field in r["fields"]}
        for key, ty in (("filter_field", "bool"), ("date_field", "date")):
            name = query[key]
            if name is not None:
                _identifier(name, key)
                field = fields.get(name, {})
                if field.get("type") != ty or field.get("unique") or field.get("required") or (ty == "bool" and field.get("default") is not False):
                    raise V1Error("INVALID_VALUE", "invalid query field")
        dates = [f for f in r["fields"] if f["type"] == "date"]
        if len(dates) > 1 or (dates and (query["date_field"] != dates[0]["name"] or query["filter_field"] is None)) or (not dates and query["date_field"] is not None):
            raise V1Error("INVALID_VALUE", "date filtering requires a boolean filter and one date")
    parent, child = resources["parent"], resources["child"]
    identifiers = [r["name"] + suffix for r in resources.values() for suffix in ("", "Create", "Read", "Patch")]
    if len(set(identifiers)) != len(identifiers) or parent["table"] == child["table"] or parent["path"] == child["path"]:
        raise V1Error("INVALID_VALUE", "resource names, tables and paths must be distinct")
    logic = p["logic"]
    if not isinstance(logic, list) or len(logic) != 11:
        raise V1Error("UNSUPPORTED_LOGIC", "expected eleven scoped steps")
    pairs = []
    for entry in logic:
        step = _object(entry, {"scope", "step"}, "logic entry")
        if not isinstance(step["scope"], str) or not isinstance(step["step"], str):
            raise V1Error("INVALID_VALUE", "invalid logic entry")
        pairs.append((step["scope"], step["step"]))
    expected = {(role, step) for role in ("parent", "child") for step in CRUD} | {("batch", BATCH)}
    if set(pairs) != expected:
        raise V1Error("UNSUPPORTED_LOGIC", "each supported scoped step must occur once")
    if p["tests"] != [TEST]:
        raise V1Error("UNSUPPORTED_TEST", "unsupported related test contract")
    migration = _object(p["migration"], {"revision", "down_revision"}, "migration")
    _identifier(migration["revision"], "migration.revision")
    if migration["down_revision"] is not None:
        raise V1Error("INVALID_VALUE", "down_revision must be null")
    if "capabilities" in p:
        from .business import validate as validate_business
        validate_business(p)
    return p


def query_kind(resource):
    q = resource["query"]
    return "date" if q["date_field"] else "boolean" if q["filter_field"] else "plain"


def operation(step, prompt):
    from .business import capabilities
    prefix = "api.mongo" if capabilities(prompt)["database"] == "mongodb" else "api.related"
    role = step["scope"]
    if role == "batch":
        return prefix + ".batch"
    op = OPS[CRUD.index(step["step"])]
    return f"{prefix}.{role}.{op}" + ("." + query_kind(prompt["resources"][role]) if op == "list" else "")


def model_input(step, prompt):
    from .business import capabilities
    role = step["scope"]
    kind = "atomic" if role == "batch" else query_kind(prompt["resources"][role])
    return f"<database>{capabilities(prompt)['database']}</database><scope>{role}</scope><query>{kind}</query><step>{step['step']}</step><block>"


def values(prompt):
    p = validate(prompt)
    result = {"package": p["package"], "migration.revision": p["migration"]["revision"], "migration.down_revision": None, "relation.foreign_key": p["relation"]["foreign_key"]}
    for role, r in p["resources"].items():
        for key in ("name", "table", "path", "fields"):
            result[f"{role}.{key}"] = r[key]
        for key, value in r["query"].items():
            result[f"{role}.{key}"] = value
    base = p["resources"]["parent"]["path"]
    result["child.suffix"] = p["resources"]["child"]["path"]
    result["parent.item_path"] = base + "/{parent_id}"
    result["child.path"] = base + "/{parent_id}" + p["resources"]["child"]["path"]
    result["child.item_path"] = result["child.path"] + "/{child_id}"
    result["batch.path"] = base + "/with-children"
    result["relation.target"] = p["resources"]["parent"]["table"] + ".id"
    if "capabilities" in p:
        for key, value in p["capabilities"].items():
            result["capabilities." + key] = value
    return result


def template_key(prompt):
    from .business import key
    return VERSION + "." + ".".join(query_kind(prompt["resources"][r]) for r in ("parent", "child")) + key(prompt)


def document(prompt, blocks):
    """Reference composition, with API entries named by operation scope."""
    p = validate(prompt)

    def entry(name, bid, role=None, refs=(), **overrides):
        common = {"package": "package", "revision": "migration.revision", "foreign_key": "relation.foreign_key", "target": "relation.target", "parent_name": "parent.name", "child_name": "child.name", "parent_table": "parent.table", "child_table": "child.table", "parent_path": "parent.path", "child_suffix": "child.suffix"}
        common.update(value_field="capabilities.value_field", group_field="capabilities.group_field", workflow="capabilities.workflow")
        if role:
            common.update({k: f"{role}.{k}" for k in ("name", "table", "path", "fields", "filter_field", "date_field", "max_limit")})
        common.update(overrides)
        return {"name": name, "id": bid, "params": {k: {"$ref": common[k]} for k in blocks[bid]["params"]}, "refs": list(refs)}

    def file(path, entries):
        return {"path": path, "blocks": entries}

    models = [entry("base", "orm.base")]
    schemas = [entry("bounds", "schema.related.bounds")]
    migration = [entry("head", "migration.related.head")]
    api = [entry("head", "api.related.head")]
    for role in ("parent", "child"):
        kind = query_kind(p["resources"][role])
        models.append(entry(role, f"orm.related.{role}.{kind}", role, ["base"]))
        schemas.append(entry(role, f"schema.related.{role}", role, ["bounds"]))
        migration.append(entry(role, f"migration.related.{role}.{kind}", role, ["head"]))
    schemas.append(entry("batch", "schema.related.batch", refs=["parent", "child"]))
    for step in p["logic"]:
        role = step["scope"]
        bid = operation(step, p)
        name = "batch" if role == "batch" else role + "_" + OPS[CRUD.index(step["step"])]
        path = "batch.path" if role == "batch" else role + (".item_path" if name.split("_")[-1] in {"get", "patch", "delete"} else ".path")
        api.append(entry(name, bid, role if role != "batch" else None, ["head"], path=path))
    api.append(entry("tail", "api.tail", refs=["head"]))
    migration.append(entry("tail", "migration.related.tail", refs=["parent", "child"]))
    result = {"version": "som.dsl.v2", "files": [
        file("{package}/models.py", models),
        file("{package}/schemas.py", schemas),
        file("{package}/api.py", api),
        file("tests/test_{package}.py", [entry("head", "test.related.head"), entry("parent", "test.related.payload.parent", "parent"), entry("child", "test.related.payload.child", "child"), entry("test", "test.related.behavior", refs=["head", "parent", "child"])]),
        file("alembic/versions/{migration.revision}_{package}.py", migration),
        file("{package}/app.py", [entry("app", "service.app")]),
        file("{package}/database.py", [entry("database", "service.database")]),
        file("{package}/__init__.py", [entry("init", "service.init")]),
        file("alembic/env.py", [entry("env", "service.alembic.env")]),
        file("alembic.ini", [entry("config", "service.alembic.ini")]),
        file("pyproject.toml", [entry("project", "service.project")]),
        file("README.md", [entry("readme", "service.readme")]),
    ]}
    if "capabilities" in p:
        from .business import document as extend
        return extend(p, result, blocks, entry)
    return result
