"""Lossless structural tokens with validated prompt bindings, without answers.

Both runtime decoders use a bounded scalar table built from public contracts
and the public prompt. The model emits every container, key, value and order.
No file, block, inverse or reference is reconstructed by a planner.
"""

import json
import re
from functools import partial

from . import composed, flow
from .v1 import V1Error

VERSION = "som.flow.codec.v1"
COMPACT_VERSION = "som.flow.compact-codec.v1"
M3_INPUT_VERSION = "som.flow.m3-input.v1"
M3_SEMANTIC_INPUT_VERSION = "som.flow.m3-input.v2"
M3_BOUND_INPUT_VERSION = "som.flow.m3-input.v3"
SEMANTIC_ESCAPE = 759
SEMANTIC_BYTE_START = 760
LIMIT = 2048
STRUCTURE = ["<pad>", "<eos>", "<map>", "</map>", "<list>", "</list>", "<input>", "<plan>"]
TOKENS = STRUCTURE + [f"<v:{index}>" for index in range(1016)]
FIXED = """version files flow path blocks id name params refs $ref steps condition then else exports result input step field literal type args undo op inputs package resources parent child relation foreign_key on_delete operations scope action capabilities database auth storage workflow value_field group_field migration revision down_revision contract sha256 fields required unique default query filter_field date_field max_limit eq ne lt le gt ge is_null authorize transaction if delete_created restore_patch key body parent_id child_id data children csv create get list patch delete batch_create string integer float boolean date bytes str int bool parent_input child_input parent_patch child_patch children_input parent_row child_row parent_rows child_rows identity object batch json workflow sub role size sha256 id created_at updated_at resource typed_literal flow_name table path foreign_key revision down_revision parent_fk""".split()


def scalar_key(value):
    return composed.canonical(value)


def static_pool(metadata=None):
    """Assign stable meanings from the public codec and block contracts."""
    metadata = metadata or json.loads(flow.CATALOG.read_text())
    static = set(FIXED) | {flow.VERSION, flow.DSL_VERSION, flow.CONTRACT, "som.dsl.v2", "symbol"}
    static |= {"postgresql", "mongodb", "jwt", "firebase", "google_oauth", "none", "s3", "gcs", "json_export", "gzip_export", "csv", "decimal_summary", "statistics", "restrict"}
    static |= {"n"+str(index) for index in range(32)} | {"i"+str(index) for index in range(8)} | {"f"+str(index) for index in range(8)} | {"r"+str(index) for index in range(8)}
    static |= set(metadata["blocks"]) | set(metadata["slots"].values())
    for entry in metadata["blocks"].values():
        static.add(entry["provides"].split(":", 1)[1])
        static |= set(entry["bindings"])
        static |= {binding["$ref"] for binding in entry["bindings"].values()}
    return [None, False, True, *sorted(static)]


def pool(prompt, normalized=False, metadata=None):
    """Use no corpus, label, expected plan or program text."""
    flow.validate(prompt)
    result = static_pool(metadata)
    seen = {scalar_key(value) for value in result}
    def visit(value):
        if isinstance(value, dict):
            for key in sorted(value): visit(key); visit(value[key])
        elif isinstance(value, list):
            for item in value: visit(item)
        else:
            key = scalar_key(value)
            if key not in seen: seen.add(key); result.append(value)
    if not normalized: visit(prompt)
    visit(flow.semantic_view(prompt))
    if len(result) > 1016:
        raise V1Error("SCOPE_TOO_LARGE", "flow scalar binding table exceeds vocabulary")
    return result


def semantic_pool(metadata=None):
    """Keep operator meanings stable, independent of the prompt's values."""
    values = static_pool(metadata)
    known = {value for value in values if isinstance(value, str)}
    extra = set(flow.ARGUMENTS) | {"", "parent.id"}
    extra |= {"parent.f" + str(index) for index in range(8)}
    values.extend(sorted(extra - known))
    if len(values) > SEMANTIC_ESCAPE:
        raise V1Error("SCOPE_TOO_LARGE", "semantic vocabulary overlaps byte tokens")
    return values


def serialize_semantic(value, values, *, _indices=None):
    """Spell unknown scalar values instead of assigning opaque local IDs."""
    indices = (_indices if _indices is not None else
               {scalar_key(item): index for index, item in enumerate(values)})
    tokens = []

    def emit(item):
        if isinstance(item, dict):
            tokens.append("<map>")
            for key in sorted(item):
                emit(key)
                emit(item[key])
            tokens.append("</map>")
        elif isinstance(item, list):
            tokens.append("<list>")
            for child in item:
                emit(child)
            tokens.append("</list>")
        else:
            index = indices.get(scalar_key(item))
            if index is not None:
                tokens.append(f"<v:{index}>")
            else:
                tokens.extend(["<list>", f"<v:{SEMANTIC_ESCAPE}>"])
                tokens.extend(f"<v:{SEMANTIC_BYTE_START + byte}>"
                              for byte in scalar_key(item).encode("utf-8"))
                tokens.append("</list>")

    emit(value)
    return tokens


def serialize(value, values):
    indices = {scalar_key(value): index for index, value in enumerate(values)}
    tokens = []
    def emit(value):
        if isinstance(value, dict):
            tokens.append("<map>")
            for key in sorted(value): emit(key); emit(value[key])
            tokens.append("</map>")
        elif isinstance(value, list):
            tokens.append("<list>")
            for item in value: emit(item)
            tokens.append("</list>")
        else:
            index = indices.get(scalar_key(value))
            if index is None: raise V1Error("INVALID_REF", "unknown scalar binding")
            tokens.append(f"<v:{index}>")
    emit(value)
    return tokens


def features(prompt, metadata=None, *, semantic=False):
    values = (semantic_pool(metadata) if semantic
              else pool(prompt, normalized=True, metadata=metadata))
    emit = (partial(serialize_semantic, _indices={
        scalar_key(item): index for index, item in enumerate(values)})
        if semantic else serialize)
    outputs = pool(prompt, metadata=metadata)
    indices = {scalar_key(value): index for index, value in enumerate(outputs)}
    # A destination token is visible with its semantic role. Names and
    # constants remain private bindings, even if they equal vocabulary words.
    tokens = ["<input>", *emit(flow.semantic_view(prompt), values), "<list>"]
    def binding(kind, slot, value):
        tokens.extend(["<list>", *emit(kind, values), *emit(slot, values), f"<v:{indices[scalar_key(value)]}>", "</list>"])
    binding("flow", "flow_name", prompt["flow"]["name"])
    for index, item in enumerate(prompt["flow"]["inputs"]): binding("input", "i"+str(index), item["name"])
    step_index=0
    def nodes(items):
        nonlocal step_index
        for node in items:
            binding("step", "n"+str(step_index), node["name"]); step_index+=1
            if node["op"]=="transaction": nodes(node["steps"])
            elif node["op"]=="if":
                nodes(node["then"]); nodes(node["else"])
                for name in sorted(node["exports"]): binding("step", "n"+str(step_index), name); step_index+=1
    nodes(prompt["flow"]["steps"])
    for role in ("parent", "child"):
        for index, definition in enumerate(prompt["resources"][role]["fields"]): binding(role,"f"+str(index),definition["name"])
    binding("relation", "parent_fk", prompt["relation"]["foreign_key"])
    def constants(value):
        if isinstance(value, dict):
            if set(value)=={"literal", "type"}: binding("literal",value["type"],value["literal"])
            else:
                for key in sorted(value): constants(value[key])
        elif isinstance(value,list):
            for item in value: constants(item)
    constants(prompt["flow"])
    for name,ref in sorted(prompt["flow"]["result"].items()):
        from copy import deepcopy
        q=deepcopy(prompt);q["flow"]["result"]={name:ref}
        projected=flow.semantic_view(q)["flow"]["result"]["r0"]
        tokens.extend(["<list>", *emit("result",values), *emit(projected,values), f"<v:{indices[scalar_key(name)]}>", "</list>"])
    tokens.extend(["</list>", "<plan>"])
    if len(tokens) > LIMIT: raise V1Error("SCOPE_TOO_LARGE", "flow input exceeds 2048 tokens")
    return "".join(tokens)


def features_m3(prompt, metadata=None):
    """Reorder public root fields, retaining all facts and private bindings.

    Arrays and the scalar table stay unchanged. The legacy encoder and
    output codec stay unchanged. No block, file or reference is inferred.
    """
    original = features(prompt, metadata)
    values = pool(prompt, normalized=True, metadata=metadata)
    view = flow.semantic_view(prompt)
    prefix = "<input>" + "".join(serialize(view, values))
    if not original.startswith(prefix):
        raise V1Error("INVALID_SCHEMA", "legacy flow input prefix differs")
    keys = ["capabilities", "operations", *[
        key for key in sorted(view) if key not in {"capabilities", "operations"}
    ]]
    tokens = ["<input>", "<map>"]
    for key in keys:
        tokens.extend(serialize(key, values))
        tokens.extend(serialize(view[key], values))
    tokens.append("</map>")
    return "".join(tokens) + original[len(prefix):]


def features_m3_semantic(prompt, metadata=None):
    """Show all semantic facts with stable words or explicit UTF-8 bytes."""
    original = features(prompt, metadata, semantic=True)
    values = semantic_pool(metadata)
    emit = partial(serialize_semantic, _indices={
        scalar_key(item): index for index, item in enumerate(values)})
    view = flow.semantic_view(prompt)
    prefix = "<input>" + "".join(emit(view, values))
    if not original.startswith(prefix):
        raise V1Error("INVALID_SCHEMA", "semantic flow input prefix differs")
    keys = ["capabilities", "operations", *[
        key for key in sorted(view) if key not in {"capabilities", "operations"}
    ]]
    tokens = ["<input>", "<map>"]
    for key in keys:
        tokens.extend(emit(key, values))
        tokens.extend(emit(view[key], values))
    tokens.append("</map>")
    result = "".join(tokens) + original[len(prefix):]
    if result.count("<") > LIMIT:
        raise V1Error("SCOPE_TOO_LARGE", "flow input exceeds 2048 tokens")
    return result


def compound_bindings(prompt):
    """Bind each validated compound selector to its private scalar index."""
    fields = {role: {item["name"]: "f" + str(index)
                    for index, item in enumerate(prompt["resources"][role]["fields"])}
              for role in ("parent", "child")}
    bindings = {}

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"step", "field"} and "." in value["field"]:
                role, field = value["field"].split(".", 1)
                if role not in fields:
                    raise V1Error("INVALID_REF", "unsupported compound selector")
                bindings[role + "." + fields[role].get(field, field)] = value["field"]
            else:
                for item in value.values():
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(prompt["flow"])
    return sorted(bindings.items())


def features_m3_bound(prompt, metadata=None):
    """Expose compound-reference destinations without exposing private names."""
    original = features_m3_semantic(prompt, metadata)
    end = "</list><plan>"
    if not original.endswith(end):
        raise V1Error("INVALID_SCHEMA", "semantic binding suffix differs")
    values = semantic_pool(metadata)
    outputs = pool(prompt, metadata=metadata)
    emit = partial(serialize_semantic, _indices={
        scalar_key(item): index for index, item in enumerate(values)})
    tokens = []
    for slot, value in compound_bindings(prompt):
        tokens.extend(["<list>", *emit("field", values), *emit(slot, values),
                       *serialize(value, outputs), "</list>"])
    result = original[:-len(end)] + "".join(tokens) + end
    if result.count("<") > LIMIT:
        raise V1Error("SCOPE_TOO_LARGE", "flow input exceeds 2048 tokens")
    return result


def m3_features(prompt, metadata=None, *, input_policy=M3_BOUND_INPUT_VERSION):
    """Select an explicit input policy without changing archived encoders."""
    if input_policy == M3_INPUT_VERSION:
        return features_m3(prompt, metadata)
    if input_policy == M3_SEMANTIC_INPUT_VERSION:
        return features_m3_semantic(prompt, metadata)
    if input_policy == M3_BOUND_INPUT_VERSION:
        return features_m3_bound(prompt, metadata)
    raise V1Error("INVALID_SCHEMA", "unknown M3 input policy")


def decode_m3_semantic_view(text, metadata=None):
    """Recover the model's semantic view without the original prompt."""
    tokens = re.findall(r"<[^<>\s]+>", text)
    if "".join(tokens) != text or not tokens or tokens[0] != "<input>":
        raise V1Error("INVALID_SCHEMA", "malformed semantic model input")
    if len(tokens) > LIMIT or tokens[-1] != "<plan>":
        raise V1Error("SCOPE_TOO_LARGE", "incomplete or oversized semantic input")
    values = semantic_pool(metadata)
    index = 1

    def scalar_index(token):
        match = re.fullmatch(r"<v:(0|[1-9][0-9]*)>", token)
        if match is None:
            raise V1Error("INVALID_SCHEMA", "invalid semantic scalar token")
        return int(match[1])

    def read(depth=0):
        nonlocal index
        if depth > 32 or index >= len(tokens):
            raise V1Error("INVALID_SCHEMA", "truncated semantic input")
        token = tokens[index]
        index += 1
        if token == "<list>" and index < len(tokens) and tokens[index] == f"<v:{SEMANTIC_ESCAPE}>":
            index += 1
            data = bytearray()
            while index < len(tokens) and tokens[index] != "</list>":
                byte = scalar_index(tokens[index]) - SEMANTIC_BYTE_START
                if not 0 <= byte <= 255:
                    raise V1Error("INVALID_SCHEMA", "invalid semantic UTF-8 byte")
                data.append(byte)
                index += 1
            if index >= len(tokens):
                raise V1Error("INVALID_SCHEMA", "unclosed semantic scalar")
            index += 1
            try:
                value = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise V1Error("INVALID_SCHEMA", "invalid semantic scalar value") from error
            if isinstance(value, (dict, list)) or scalar_key(value).encode("utf-8") != data:
                raise V1Error("INVALID_SCHEMA", "noncanonical semantic scalar")
            return value
        if token in {"<map>", "<list>"}:
            end = "</map>" if token == "<map>" else "</list>"
            result = {} if token == "<map>" else []
            while index < len(tokens) and tokens[index] != end:
                value = read(depth + 1)
                if token == "<map>":
                    if not isinstance(value, str) or value in result:
                        raise V1Error("INVALID_SCHEMA", "duplicate or invalid semantic key")
                    result[value] = read(depth + 1)
                else:
                    result.append(value)
            if index >= len(tokens):
                raise V1Error("INVALID_SCHEMA", "unclosed semantic container")
            index += 1
            return result
        number = scalar_index(token)
        if number >= len(values):
            raise V1Error("INVALID_SCHEMA", "unknown semantic scalar")
        return values[number]

    view = read()
    if not isinstance(view, dict) or index >= len(tokens) or tokens[index] != "<list>":
        raise V1Error("INVALID_SCHEMA", "semantic root or bindings missing")
    return view


def encode(prompt, dsl, metadata=None):
    flow.validate_plan(prompt, dsl, metadata)
    tokens = [*serialize(dsl, pool(prompt, metadata=metadata)), "<eos>"]
    if len(tokens) > LIMIT: raise V1Error("SCOPE_TOO_LARGE", "flow output exceeds 2048 tokens")
    return "".join(tokens)


def decode(prompt, text, metadata=None):
    values = pool(prompt, metadata=metadata)
    tokens = re.findall(r"<[^<>\s]+>", text)
    if "".join(tokens) != text or not tokens or tokens[-1] != "<eos>":
        raise V1Error("INVALID_DSL", "malformed or incomplete flow token sequence")
    if len(tokens) > LIMIT: raise V1Error("SCOPE_TOO_LARGE", "flow output exceeds 2048 tokens")
    index = 0
    def read(depth=0):
        nonlocal index
        if depth > 32 or index >= len(tokens)-1: raise V1Error("INVALID_DSL", "truncated or deep flow sequence")
        token = tokens[index]; index += 1
        if token in {"<map>", "<list>"}:
            end = "</map>" if token == "<map>" else "</list>"
            result = {} if token == "<map>" else []
            while index < len(tokens)-1 and tokens[index] != end:
                value = read(depth+1)
                if token == "<map>":
                    if not isinstance(value, str) or value in result: raise V1Error("INVALID_DSL", "invalid or duplicate map key")
                    result[value] = read(depth+1)
                else: result.append(value)
            if index >= len(tokens)-1: raise V1Error("INVALID_DSL", "unclosed container")
            index += 1
            return result
        match = re.fullmatch(r"<v:(0|[1-9][0-9]*)>", token)
        if match is None or int(match[1]) >= len(values): raise V1Error("INVALID_DSL", "unknown structural token or binding")
        return values[int(match[1])]
    result = read()
    if index != len(tokens)-1: raise V1Error("INVALID_DSL", "extra tokens after plan")
    flow.validate_plan(prompt, result, metadata)
    return result
