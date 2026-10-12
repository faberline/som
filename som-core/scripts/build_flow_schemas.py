"""Publish structural schemas; semantic validation remains mandatory."""

from copy import deepcopy
import json
from pathlib import Path

from som_core import composed, flow

ROOT = Path(__file__).resolve().parents[2]
NAME = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$", "maxLength": 64}
STRING = {"type": "string"}


def object_schema(properties, optional=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties,
            "required": [name for name in properties if name not in optional]}


def reference(name):
    return {"$ref": "#/$defs/" + name}


def definitions():
    refs = [object_schema({"input": NAME}),
            object_schema({"step": NAME, "field": STRING}),
            object_schema({"literal": {}, "type": {"enum": sorted(flow.SCALARS)}})]
    predicate = {"oneOf": [
        object_schema({"op": {"const": "is_null"}, "left": reference("runtime_ref")}),
        object_schema({"op": {"enum": ["eq", "ne", "lt", "le", "gt", "ge"]},
                       "left": reference("runtime_ref"), "right": reference("runtime_ref")})]}
    exports = {"type": "object", "propertyNames": NAME, "additionalProperties":
               object_schema({"then": reference("runtime_ref"), "else": reference("runtime_ref")})}
    return {"runtime_ref": {"oneOf": refs}, "predicate": predicate,
            "exports": exports, "results": {"type": "object", "propertyNames": NAME,
                                             "additionalProperties": reference("runtime_ref")}}


def nodes(dsl=False):
    key = "id" if dsl else "op"
    atomics = flow.catalog()
    actions = sorted(identity for identity, meta in atomics.items()
                     if meta["operation"] in flow.ARGUMENTS) if dsl else sorted(flow.ARGUMENTS)
    controls = {kind: sorted(identity for identity, meta in atomics.items()
                            if meta["operation"] == kind) for kind in ("if", "transaction")}
    inverse = sorted(identity for identity, meta in atomics.items() if meta["inverse"]) if dsl else ["delete_created", "restore_patch"]
    return {"oneOf": [
        object_schema({"name": NAME, key: {"enum": actions},
                       "args": {"type": "object", "propertyNames": NAME, "additionalProperties": reference("runtime_ref")},
                       "undo": {"enum": [None, *inverse]}}),
        object_schema({"name": NAME, key: {"enum": controls["transaction"]} if dsl else {"const": "transaction"},
                       "steps": reference("nodes")}),
        object_schema({"name": NAME, key: {"enum": controls["if"]} if dsl else {"const": "if"},
                       "condition": reference("predicate"), "then": reference("nodes"),
                       "else": reference("nodes"), "exports": reference("exports")})]}


def header(identity, defs, properties):
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": identity, "title": identity,
            "$comment": "Structural schema only. The semantic validator also checks types, scopes, dependencies, capabilities, permissions, compensation and total budgets.",
            "$defs": defs, **object_schema(properties)}


def prompt_schema():
    defs = definitions()
    defs["node"] = nodes()
    defs["nodes"] = {"type": "array", "maxItems": 12, "items": reference("node")}
    field = object_schema({"name": NAME, "type": {"enum": ["str", "int", "float", "bool", "date"]},
                           "required": {"type": "boolean"}, "unique": {"type": "boolean"},
                           "default": {"enum": [None, False]}}, optional=("default",))
    query = object_schema({"filter_field": {"type": ["string", "null"]},
                           "date_field": {"type": ["string", "null"]}, "max_limit": {"const": 100}})
    resource = object_schema({"name": NAME, "table": NAME,
                              "path": {"type": "string", "pattern": "^/[a-z][a-z0-9_/]*$"},
                              "fields": {"type": "array", "minItems": 1, "maxItems": 8, "items": field}, "query": query})
    operations = {"oneOf": [object_schema({"scope": {"enum": ["parent", "child"]},
                                          "action": {"enum": ["create", "get", "list", "patch", "delete"]}}),
                                object_schema({"scope": {"const": "batch"}, "action": {"const": "batch_create"}})]}
    return header(flow.VERSION, defs, {
        "version": {"const": flow.VERSION},
        "contract": object_schema({"id": {"const": flow.CONTRACT}, "sha256": {"const": flow.contract_hash()}}),
        "package": NAME, "resources": object_schema({"parent": resource, "child": deepcopy(resource)}),
        "relation": object_schema({"foreign_key": NAME, "on_delete": {"const": "restrict"}}),
        "operations": {"type": "array", "minItems": 1, "maxItems": 11, "items": operations},
        "capabilities": object_schema({"database": {"enum": ["postgresql", "mongodb"]},
                                       "auth": {"enum": ["jwt", "firebase", "google_oauth"]},
                                       "storage": {"enum": ["none", "s3", "gcs"]},
                                       "workflow": {"enum": ["json_export", "gzip_export", "csv", "decimal_summary", "statistics"]},
                                       "value_field": {"type": ["string", "null"]}, "group_field": {"type": ["string", "null"]}}),
        "migration": object_schema({"revision": STRING, "down_revision": {"type": ["string", "null"]}}),
        "flow": object_schema({"name": NAME,
                               "inputs": {"type": "array", "maxItems": 8, "items": object_schema({"name": NAME, "type": {"enum": sorted(flow.INPUT_TYPES)}})},
                               "steps": reference("nodes"), "result": reference("results")})})


def dsl_schema():
    defs = definitions()
    defs["node"] = nodes(dsl=True)
    defs["nodes"] = {"type": "array", "maxItems": 12, "items": reference("node")}
    catalog = json.loads(flow.CATALOG.read_text())
    block = object_schema({"id": {"enum": sorted(catalog["blocks"])}, "name": NAME,
                           "params": {"type": "object", "additionalProperties": object_schema({"$ref": STRING})},
                           "refs": {"type": "array", "items": object_schema({"symbol": NAME})}})
    return header(flow.DSL_VERSION, defs, {
        "version": {"const": flow.DSL_VERSION},
        "files": {"type": "array", "minItems": 1, "maxItems": 12,
                  "items": object_schema({"path": {"enum": sorted(catalog["slots"].values())},
                                          "blocks": {"type": "array", "minItems": 1, "maxItems": 64, "items": block}})},
        "flow": object_schema({"name": NAME, "steps": reference("nodes"), "result": reference("results")})})


def main():
    destination = ROOT / "som-rs/contracts"
    for name, schema in (("som.prompt.v7.schema.json", prompt_schema()), ("som.dsl.v3.schema.json", dsl_schema())):
        (destination / name).write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n")
        print(name)


if __name__ == "__main__":
    main()
