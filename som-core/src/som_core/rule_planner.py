"""A corpus-free baseline planner driven by public block contracts.

This module does not import reference planners or load training/test answers.
Its fixed layout and symbol bindings are deliberately reported as rules.
"""

from copy import deepcopy
import json

from . import composed, related
from .v1 import V1Error


def plan(prompt: dict, catalog: dict) -> dict:
    """Select compatible providers, then satisfy their declared dependencies."""
    prompt = composed.validate(prompt)
    required = list(catalog["required_common"])
    required += catalog["required_database"][prompt["capabilities"]["database"]]
    operations = [
        "app:" + ("batch" if operation["scope"] == "batch" else
                  operation["scope"] + "_" + operation["action"])
        for operation in prompt["operations"]
    ]
    required += operations
    if prompt["capabilities"]["storage"] != "none":
        required.append("integrations:storage_common")
    selected = {}
    pending = list(required)
    while pending:
        symbol = pending.pop(0)
        if symbol in selected:
            continue
        matches = []
        for identity, meta in catalog["blocks"].items():
            if meta["provides"] != symbol:
                continue
            if any(prompt["capabilities"][key] != value
                   for key, value in meta["capabilities"].items()):
                continue
            if meta.get("query"):
                role, kind = meta["query"]
                if related.query_kind(prompt["resources"][role]) != kind:
                    continue
            matches.append((identity, meta))
        if len(matches) != 1:
            raise V1Error("INVALID_COMPOSITION", "ambiguous or absent provider: " + symbol)
        selected[symbol] = matches[0]
        pending += matches[0][1]["requires"]
    # The public common list defines deterministic layout. CRUD order comes
    # solely from the prompt; dependencies are ordered before their consumer.
    priority = {symbol: index for index, symbol in enumerate(required)}
    priority.update({symbol: priority["app:workflow"] - 0.5 + index / 100
                     for index, symbol in enumerate(operations)})
    files = []
    for slot, path in composed.SLOTS.items():
        local = {symbol: pair for symbol, pair in selected.items()
                 if pair[1]["slot"] == slot}
        emitted = set()
        nodes = []
        while local:
            ready = [symbol for symbol, (_, meta) in local.items()
                     if all(need in emitted for need in meta["requires"]
                            if need.startswith(slot + ":"))]
            if not ready:
                raise V1Error("INVALID_REF", "cyclic contract dependency")
            symbol = min(ready, key=lambda value: (priority.get(value, 1000), value))
            identity, meta = local.pop(symbol)
            refs = [need.split(":", 1)[1] for need in meta["requires"]
                    if need.startswith(slot + ":")]
            if symbol == "app:tail":
                refs = ["head", *[value.split(":", 1)[1] for value in operations], "workflow"]
            nodes.append({"id": identity, "name": symbol.split(":", 1)[1],
                          "params": deepcopy(meta["bindings"]), "refs": refs})
            emitted.add(symbol)
        if nodes:
            files.append({"path": path, "blocks": nodes})
    result = {"version": "som.dsl.v2", "files": files}
    composed.validate_plan(prompt, result, catalog)
    return result


def quality_plan(prompt: dict) -> dict:
    """Read only the approved public quality catalog."""
    from .quality import CATALOG
    return plan(prompt, json.loads(CATALOG.read_text()))
