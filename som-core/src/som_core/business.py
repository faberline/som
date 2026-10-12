"""Typed integration contracts for the unreleased related-service prompt."""

from .v1 import V1Error, _object

DEFAULT = {"database": "postgresql", "auth": "none", "storage": "none", "workflow": "none", "value_field": None, "group_field": None}


def validate(p):
    caps = _object(p["capabilities"], set(DEFAULT), "capabilities")
    choices = {"database": {"postgresql", "mongodb"}, "auth": {"jwt", "firebase", "google_oauth"}, "storage": {"none", "s3", "gcs"}, "workflow": {"json_export", "csv", "gzip_export", "decimal_summary", "statistics"}}
    for key, allowed in choices.items():
        if not isinstance(caps[key], str) or caps[key] not in allowed:
            raise V1Error("INVALID_VALUE", "unsupported capability: " + key)
    fields = {f["name"]: f for f in p["resources"]["child"]["fields"]}
    if p["resources"]["child"]["path"] in {"/export", "/import"}:
        raise V1Error("INVALID_VALUE", "child path conflicts with a workflow route")
    numeric = caps["workflow"] in {"decimal_summary", "statistics"}
    if numeric:
        name = caps["value_field"]
        if not isinstance(name, str) or fields.get(name, {}).get("type") not in {"int", "float"}:
            raise V1Error("INVALID_VALUE", "numeric workflow requires a numeric value_field")
    elif caps["value_field"] is not None:
        raise V1Error("INVALID_VALUE", "this workflow has no value_field")
    if caps["workflow"] == "decimal_summary":
        group = caps["group_field"]
        if not isinstance(group, str) or fields.get(group, {}).get("type") != "str" or not fields[group]["required"]:
            raise V1Error("INVALID_VALUE", "decimal summary requires a required string group_field")
    elif caps["group_field"] is not None:
        raise V1Error("INVALID_VALUE", "this workflow has no group_field")
    return caps


def capabilities(p):
    return p.get("capabilities", DEFAULT)


def key(p):
    c = capabilities(p)
    return "." + ".".join(c[k] for k in ("database", "auth", "storage", "workflow")) if "capabilities" in p else ""


def document(p, result, blocks, entry):
    """Extend the reference layout using approved, typed entries only."""
    c = capabilities(p)
    files = result["files"]
    api = next(f for f in files if f["path"] == "{package}/api.py")
    api["path"] = "{package}/app.py"
    api["blocks"][0] = entry("head", "api.business.head." + c["database"])
    api["blocks"].insert(-1, entry("workflow", "api.business.workflow." + c["workflow"], refs=["head"]))
    api["blocks"].append(entry("app", "service.business.app." + c["database"]))
    files[:] = [f for f in files if not (f["path"] == "{package}/app.py" and f is not api)]
    integrations = [entry("auth_common", "auth.common"), entry("auth", "auth." + c["auth"], refs=["auth_common"])]
    if c["storage"] != "none":
        integrations += [entry("storage_common", "storage.common", refs=["auth"]), entry("storage", "storage." + c["storage"], refs=["storage_common"])]
    else:
        integrations += [entry("storage", "storage.none")]
    files.append({"path": "{package}/integrations.py", "blocks": integrations})
    for f in files:
        if f["path"] == "pyproject.toml":
            f["blocks"] = [entry("project", "service.business.project")]
        if f["path"] == "README.md":
            f["blocks"] = [entry("readme", "service.business.readme." + c["database"]), entry("auth", "readme.auth." + c["auth"]), entry("storage", "readme.storage." + c["storage"]), entry("workflow", "readme.workflow." + c["workflow"])]
        if f["path"].startswith("tests/"):
            f["blocks"][0] = entry("head", "test.business.head." + c["database"])
            f["blocks"][-1] = entry("test", "test.business.behavior", refs=["head", "parent", "child"])
    if c["database"] == "mongodb":
        files[:] = [f for f in files if f["path"] not in {"alembic/env.py", "alembic.ini"}]
        for f in files:
            if f["path"] == "{package}/database.py":
                f["blocks"] = [entry("database", "service.business.database.mongodb")]
            elif f["path"] == "{package}/models.py":
                f["blocks"] = [entry("base", "orm.mongo.base"), *[entry(role, "orm.mongo." + role, role, ["base"]) for role in ("parent", "child")]]
            elif f["path"].startswith("alembic/versions/"):
                f["path"] = "{package}/migrate.py"
                f["blocks"] = [entry("migration", "migration.mongo")]
    return result
