"""M3 target order. The model still emits the complete unchanged DSL."""

import json
from pathlib import Path
import re

from . import flow, flow_codec
from .v1 import V1Error

VERSION = "som.flow.m3-output-order.v1"
ROOT_KEYS = ("flow", "files", "version")
POLICY = Path(__file__).resolve().parents[3] / "som-rs/contracts/m3-output-order.v1.json"
_TOKEN_IDS = {token: index for index, token in enumerate(flow_codec.TOKENS)}


def policy() -> dict:
    return json.loads(POLICY.read_text())


def encode(prompt: dict, dsl: dict, metadata=None) -> str:
    flow.validate_plan(prompt, dsl, metadata)
    values = flow_codec.pool(prompt, metadata=metadata)
    tokens = ["<map>"]
    for key in ROOT_KEYS:
        tokens.extend(flow_codec.serialize(key, values))
        tokens.extend(flow_codec.serialize(dsl[key], values))
    tokens.extend(["</map>", "<eos>"])
    if len(tokens) > flow_codec.LIMIT:
        raise V1Error("SCOPE_TOO_LARGE", "flow output exceeds 2048 tokens")
    return "".join(tokens)


def token_ids(encoded: str) -> list[int]:
    tokens = re.findall(r"<[^<>\s]+>", encoded)
    if "".join(tokens) != encoded:
        raise ValueError("malformed M3 target tokens")
    return [_TOKEN_IDS[token] for token in tokens]
