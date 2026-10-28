"""Independent A1-A16 mutations from the frozen plan and retained base bundles."""
from __future__ import annotations

import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def clone(value):
    return json.loads(canonical(value))


def request_for(identity, react, typescript, rust, python, v5_prompt, v5_assets):
    if identity not in {f"A{number}" for number in range(1, 17)}:
        raise ValueError("unknown frozen assemble recipe")
    base = python if identity == "A3" else typescript if identity == "A12" else rust if identity == "A13" else react
    bundle = clone(base)
    if identity == "A1":
        bundle["assets"]["formatter"]["sha256"] = "0" * 64
    elif identity == "A2":
        for name in ("target_contract", "catalog", "block_library", "backend", "formatter"):
            bundle["assets"][name] = clone(typescript["assets"][name])
    elif identity in {"A3", "A4", "A10", "A11", "A12", "A13"}:
        bundle["target"] = {"A4": "python", "A10": "rust", "A11": "typescript"}.get(identity, "react")
    elif identity in {"A5", "A14"}:
        bundle["dsl"]["files"][0]["blocks"][0]["id"] = (
            "orm.base" if identity == "A5" else typescript["dsl"]["files"][0]["blocks"][0]["id"])
    elif identity == "A6":
        bundle["prompt"] = clone(v5_prompt)
        for name in ("grammar", "compiler_contract", "selection_policy", "literal_policy", "serializer", "semantic_contracts"):
            bundle["assets"][name] = clone(v5_assets[name])
    elif identity == "A7":
        value = bundle["hashes"]["payload_sha256"]
        bundle["hashes"]["payload_sha256"] = ("1" if value[0] == "0" else "0") + value[1:]
    elif identity == "A8":
        old = bundle["prompt"]["resource"]["fields"][0]["name"]
        def rename(value):
            if isinstance(value, dict):
                return {key: rename(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rename(item) for item in value]
            return "__proto__" if value == old else value
        bundle["prompt"] = rename(bundle["prompt"])
        for file in bundle["dsl"]["files"]:
            for block in file["blocks"]:
                block["params"] = rename(block["params"])
    elif identity == "A9":
        files = bundle["dsl"]["files"]
        while len(files) < 20:
            files.append(clone(files[-1]))
    elif identity == "A15":
        files = bundle["dsl"]["files"]
        block = clone(files[-1]["blocks"][-1])
        position = len(files) - 1
        while sum(len(file["blocks"]) for file in files) < 65:
            while len(files[position]["blocks"]) >= 64:
                position -= 1
                if position < 0:
                    raise ValueError("assemble recipe has no remaining block capacity")
            files[position]["blocks"].append(clone(block))
    elif identity == "A16":
        left, right = bundle["dsl"]["files"][:2]
        left["path"], right["path"] = right["path"], left["path"]
    if identity != "A7":
        bundle.pop("hashes", None)
        digest = lambda value: hashlib.sha256(canonical(value)).hexdigest()
        hashes = {"prompt_sha256": digest(bundle["prompt"]), "dsl_sha256": digest(bundle["dsl"]), "payload_sha256": digest(bundle)}
        bundle["hashes"] = hashes
    return canonical({"bundle": bundle})
