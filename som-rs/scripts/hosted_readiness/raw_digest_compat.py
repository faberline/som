"""Prove the byte scope of explicitly pinned JSON digest changes.

Pins are a sequence of (JSON pointer, before SHA-256 value, after SHA-256 value).
The caller must approve the paths and obtain all pins independently. This module
does not approve a compatibility policy, validate HTTP statuses or payload hashes, or prove product
acceptance. In particular, callers must keep prompt/DSL/file hashes out of a
formatter/backend compatibility policy.

Only literal, unescaped 64-character lowercase hexadecimal string values can
change. Every other byte, including JSON spelling, spacing, ordering and quotes,
is retained. Input is limited to 16 MiB and 256 nested containers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Sequence

Pin = tuple[str, str, str]
Path = tuple[str, ...]
MAX_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 256
HEX = re.compile(r"[0-9a-f]{64}\Z")
NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


class RawDigestError(ValueError):
    """The input or claimed compatibility scope is invalid."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RawDigestError(message)


def pointer_path(pointer: str) -> Path:
    require(isinstance(pointer, str), "JSON pointer must be a string")
    if pointer == "":
        return ()
    require(pointer.startswith("/"), "JSON pointer must start with /")
    parts = []
    for token in pointer[1:].split("/"):
        require(re.search(r"~(?![01])", token) is None, "invalid JSON pointer escape")
        parts.append(token.replace("~1", "/").replace("~0", "~"))
    return tuple(parts)


def checked_pins(pins: Sequence[Pin]) -> list[tuple[Path, str, str]]:
    require(isinstance(pins, (list, tuple)), "pins must be an explicit sequence")
    result, seen = [], set()
    for pin in pins:
        require(isinstance(pin, (list, tuple)) and len(pin) == 3, "pin must name pointer, before and after")
        pointer, before, after = pin
        path = pointer_path(pointer)
        require(path not in seen, "duplicate JSON pointer")
        require(
            isinstance(before, str) and HEX.fullmatch(before) is not None
            and isinstance(after, str) and HEX.fullmatch(after) is not None,
            "before and after pins must be exact lowercase SHA-256 strings",
        )
        seen.add(path)
        result.append((path, before, after))
    return result


class _Document:
    """Validate all JSON while locating value tokens by decoded JSON paths."""

    def __init__(self, raw: bytes):
        require(isinstance(raw, bytes), "raw JSON must be immutable bytes")
        require(len(raw) <= MAX_BYTES, "raw JSON exceeds the size limit")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawDigestError("raw JSON is not UTF-8") from error
        self.raw, self.pos, self.nodes = raw, 0, {}
        self.value((), 0)
        self.space()
        require(self.pos == len(raw), "extra bytes after JSON value")

    def space(self) -> None:
        while self.pos < len(self.raw) and self.raw[self.pos] in b" \t\r\n":
            self.pos += 1

    def string(self) -> str:
        start = self.pos
        require(self.raw[start:start + 1] == b'"', "expected JSON string")
        self.pos += 1
        while self.pos < len(self.raw):
            byte = self.raw[self.pos]
            if byte == 92:
                self.pos += 2
            elif byte == 34:
                self.pos += 1
                try:
                    return json.loads(self.raw[start:self.pos].decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as error:
                    raise RawDigestError("invalid JSON string") from error
            else:
                self.pos += 1
        raise RawDigestError("unterminated JSON string")

    def take(self, token: bytes) -> bool:
        if self.raw[self.pos:self.pos + len(token)] == token:
            self.pos += len(token)
            return True
        return False

    def value(self, path: Path, depth: int) -> None:
        require(depth <= MAX_DEPTH, "JSON nesting exceeds the depth limit")
        self.space()
        start, decoded = self.pos, None
        byte = self.raw[self.pos:self.pos + 1]
        if byte == b'"':
            kind, decoded = "string", self.string()
        elif self.take(b"{"):
            kind, seen = "object", set()
            self.space()
            if not self.take(b"}"):
                while True:
                    self.space()
                    key = self.string()
                    require(key not in seen, "duplicate JSON object key")
                    seen.add(key)
                    self.space()
                    require(self.take(b":"), "expected JSON object colon")
                    self.value((*path, key), depth + 1)
                    self.space()
                    if self.take(b"}"):
                        break
                    require(self.take(b","), "expected JSON object comma")
        elif self.take(b"["):
            kind, index = "array", 0
            self.space()
            if not self.take(b"]"):
                while True:
                    self.value((*path, str(index)), depth + 1)
                    index += 1
                    self.space()
                    if self.take(b"]"):
                        break
                    require(self.take(b","), "expected JSON array comma")
        elif any(self.take(token) for token in (b"true", b"false", b"null")):
            kind = "scalar"
        else:
            number = NUMBER.match(self.raw, self.pos)
            require(number is not None, "invalid JSON value")
            self.pos = number.end()
            kind = "scalar"
        self.nodes[path] = (kind, start, self.pos, decoded)


def _rewrite(raw: bytes, pins: list[tuple[Path, str, str]], side: str, target: str) -> bytes:
    require(side in {"before", "after"}, "side must be before or after")
    require(target in {"before", "mask"}, "target must be before or mask")
    nodes, spans = _Document(raw).nodes, []
    for path, before, after in pins:
        require(path in nodes, "pinned JSON pointer is missing")
        kind, start, end, decoded = nodes[path]
        expected = before if side == "before" else after
        token = b'"' + expected.encode("ascii") + b'"'
        require(kind == "string" and decoded == expected and raw[start:end] == token, "pinned leaf is not the exact unescaped digest")
        replacement = before.encode("ascii") if target == "before" else b"0" * 64
        spans.append((start + 1, end - 1, replacement))
    spans.sort()
    pieces, previous = [], 0
    for start, end, replacement in spans:
        require(previous <= start < end <= len(raw), "digest spans overlap or are invalid")
        pieces.extend((raw[previous:start], replacement))
        previous = end
    pieces.append(raw[previous:])
    return b"".join(pieces)


def mask_raw(raw: bytes, pins: Sequence[Pin], side: str) -> bytes:
    """Mask only exact pinned leaves. No paths or values are inferred."""
    return _rewrite(raw, checked_pins(pins), side, "mask")


def masked_sha256(raw: bytes, pins: Sequence[Pin], side: str) -> str:
    """Hash a raw reply with only exact, independently pinned leaves masked."""
    return hashlib.sha256(mask_raw(raw, pins, side)).hexdigest()


def verify_raw(before: bytes, after: bytes, pins: Sequence[Pin]) -> bool:
    """Require exact raw equality outside the approved, exact digest leaves."""
    checked = checked_pins(pins)
    old = _rewrite(before, checked, "before", "mask")
    new = _rewrite(after, checked, "after", "mask")
    require(hmac.compare_digest(old, new), "raw bytes changed outside the pinned digest leaves")
    return True


def rebased_sha256(after: bytes, pins: Sequence[Pin]) -> str:
    """Hash current raw bytes after replacing exact after pins with before pins."""
    return hashlib.sha256(_rewrite(after, checked_pins(pins), "after", "before")).hexdigest()


def verify_frozen(after: bytes, frozen_before_sha256: str, pins: Sequence[Pin]) -> bool:
    """Check an independently pinned original raw hash without its old body."""
    require(isinstance(frozen_before_sha256, str) and HEX.fullmatch(frozen_before_sha256) is not None, "frozen raw SHA-256 pin is invalid")
    require(
        hmac.compare_digest(rebased_sha256(after, pins), frozen_before_sha256),
        "rebased raw reply hash differs from the frozen golden",
    )
    return True
