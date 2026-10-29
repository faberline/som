"""Unused scratch adapter. Strict comparison only; native compatibility is disabled.

This module is not an acceptance gate. Its results prove equality only between
bytes supplied by its caller. They do not authenticate those bytes, associate a
reply with a request, prove an approved role or host, validate a bundle schema,
run a service, or prove a product gate.

Future compatibility must first obtain an independently reviewed, sealed and
source-bound authority. No such authority exists here. The disabled entry point
never calls or trusts a caller-provided verifier, callback, policy or expected
value. It never imports the M12 experimental checker as an authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac


class ComparisonRefused(ValueError):
    """Strict byte comparison failed or input was invalid."""


class CompatibilityUnavailable(ComparisonRefused):
    """Native compatibility cannot be admitted by this scratch adapter."""


def _status(value: object) -> int:
    # bool is an int subclass. It is not an HTTP status.
    if type(value) is not int or not 100 <= value <= 599:
        raise ComparisonRefused("invalid HTTP status")
    return value


def _bytes(value: object) -> bytes:
    # Keep a snapshot, rather than allowing a caller to mutate a bytearray.
    if type(value) is not bytes:
        raise ComparisonRefused("immutable bytes required")
    return value


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class RawEqualityProof:
    """Immutable hashes of the supplied equal replies, not provenance proof."""

    before_status: int
    after_status: int
    before_sha256: str
    after_sha256: str
    byte_length: int
    acceptance_granted: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class FileEqualityProof:
    """One immutable path and content digest from the supplied equal maps."""

    path: str
    before_sha256: str
    after_sha256: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class ProductEqualityProof:
    """Immutable file evidence; it does not prove a product behavior gate."""

    files: tuple[FileEqualityProof, ...]
    acceptance_granted: bool = field(default=False, init=False)


def compare_raw(*, before: bytes, after: bytes, before_status: int,
                after_status: int) -> RawEqualityProof:
    """Require exact HTTP status and exact immutable response bytes.

    Even an equal non-200 response can be compared here. The owning acceptance
    gate must separately verify its required status, request identity and role.
    """
    left_status, right_status = _status(before_status), _status(after_status)
    left, right = _bytes(before), _bytes(after)
    if left_status != right_status:
        raise ComparisonRefused("HTTP statuses differ")
    if not hmac.compare_digest(left, right):
        raise ComparisonRefused("raw response bytes differ")
    return RawEqualityProof(left_status, right_status, _sha(left), _sha(right), len(left))


def _files(value: object) -> tuple[tuple[str, bytes], ...]:
    """Snapshot a nonempty exact dict with safe relative POSIX paths."""
    if type(value) is not dict or not value:
        raise ComparisonRefused("a nonempty exact file map is required")
    rows: list[tuple[str, bytes]] = []
    for path, content in value.items():
        if type(path) is not str or not path or "\\" in path or "\x00" in path:
            raise ComparisonRefused("invalid product path")
        try:
            path.encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise ComparisonRefused("product path is not UTF-8") from error
        if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
            raise ComparisonRefused("product path must be a safe relative path")
        rows.append((path, _bytes(content)))
    return tuple(sorted(rows))


def compare_products(*, before_files: dict[str, bytes],
                     after_files: dict[str, bytes]) -> ProductEqualityProof:
    """Require the complete supplied file maps and every file byte to match.

    Callers must independently bind these maps to their exact named case and to
    the expected complete file set. Empty maps cannot pass as product evidence.
    """
    left, right = _files(before_files), _files(after_files)
    if tuple(path for path, _ in left) != tuple(path for path, _ in right):
        raise ComparisonRefused("product file sets differ")
    files = []
    for (path, old), (_, new) in zip(left, right):
        if not hmac.compare_digest(old, new):
            raise ComparisonRefused("product file bytes differ")
        files.append(FileEqualityProof(path, _sha(old), _sha(new), len(old)))
    return ProductEqualityProof(tuple(files))


def compare_native(*, before: object, after: object, before_status: object,
                   after_status: object, host: object, role: object,
                   policy: object, source_authority: object,
                   products: object) -> None:
    """Refuse all native compatibility requests until reviewed integration.

    This is only a future integration seam. It intentionally does not examine
    or invoke input objects. A policy flag, an arbitrary object, an expected
    digest, or a callback cannot authorize changed bytes. Implementing this
    entry point requires a separately reviewed sealed policy and concrete
    source-bound authority; an opt-in switch alone is never sufficient.
    """
    raise CompatibilityUnavailable("native compatibility authority is unavailable")
