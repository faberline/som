"""Synthetic algorithm prototype; NEVER a source of production authority.

Only the literal fixture identities and fixture data below are admitted. This
module has no policy input, source checkout access, network, accepted-case input,
or path to strict_adapter.compare_native. Its result always states SYNTHETIC and
NO ACCEPTANCE. Its toy source strings and canonical JSON are NOT SOM source
vectors, native-tool pins, sealed policy or real-candidate evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import hashlib
import json
import types
from typing import Any

import strict_adapter

# Pin the *bytes* of the existing f59 raw primitive before compiling it. This is
# a fixture dependency identity only; it is NOT Git/receipt authority.
RAW_HELPER_SHA256 = "a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896"
FIXTURE_HOST = "SYNTHETIC-HOST-NOT-A-PLATFORM"
FIXTURE_ROLE = "SYNTHETIC-ROLE-NOT-AN-ACCEPTED-ROLE"
CASE_OK = "SYNTHETIC-CASE-OK"
CASE_ERROR = "SYNTHETIC-CASE-ERROR"
REQUEST_OK = b'{"request":"synthetic-ok"}'
REQUEST_ERROR = b'{"request":"synthetic-error"}'
ERROR_RAW = b'{"error":"synthetic-only"}'
BEFORE_SOURCE = b"SYNTHETIC SOURCE BEFORE; NOT PRODUCT CODE\n"
AFTER_SOURCE = b"SYNTHETIC SOURCE AFTER; NOT PRODUCT CODE\n"
BACKEND_ID = "synthetic.backend.not-product.v1"
BUNDLE_VERSION = "synthetic.bundle.not-product.v1"
TARGET = "synthetic-not-a-target"
CHANGED_LEAVES = ("/bundle/assets/backend/sha256", "/bundle/hashes/payload_sha256")
PRODUCT_PATH = "synthetic/fixture.txt"
PRODUCT_BYTES = b"synthetic product, not a real case\n"


class FixtureRefused(ValueError):
    """The synthetic fixture or its immutable helper bytes do not match."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureRefused(message)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _primitive(source: bytes) -> types.ModuleType:
    """Load exactly the pinned source bytes, with no PYTHONPATH module lookup."""
    _require(type(source) is bytes, "immutable helper bytes required")
    _require(_sha(source) == RAW_HELPER_SHA256, "helper digest differs")
    module = types.ModuleType("_synthetic_pinned_raw_helper")
    # The same already-verified immutable bytes are compiled and executed.
    # There is no reopen-by-path race or import-before-pin check. This is NOT
    # a claim of repository source, receipt, policy, or runtime authority.
    exec(compile(source, "<pinned-synthetic-raw-helper>", "exec"), module.__dict__)
    _require(callable(getattr(module, "verify_raw", None)), "helper API missing")
    _require(callable(getattr(module, "mask_raw", None)), "helper API missing")
    return module


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        _require(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def _bad_number(_: str) -> None:
    raise FixtureRefused("non-integer or non-finite number not in fixture grammar")


def _parse(raw: bytes, primitive: types.ModuleType) -> Any:
    _require(type(raw) is bytes, "immutable raw bytes required")
    try:
        # Pinned scanner enforces UTF-8, exact JSON syntax, depth and duplicate
        # keys before json.loads. Empty pins mean no byte is masked.
        primitive.mask_raw(raw, (), "before")
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                          parse_float=_bad_number, parse_constant=_bad_number)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FixtureRefused("invalid fixture JSON") from error


def _canon(value: Any) -> bytes:
    """Synthetic integer-only JSON canonical form, not the SOM serializer."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8", "strict")
    except (ValueError, TypeError, UnicodeError) as error:
        raise FixtureRefused("fixture canonicalization failed") from error


def _prompt() -> dict[str, str]:
    return {"name": "synthetic-case", "version": "synthetic.prompt.v1"}


def _dsl() -> dict[str, Any]:
    return {"files": [PRODUCT_PATH], "version": "synthetic.dsl.v1"}


def _bundle(raw: bytes, primitive: types.ModuleType, source: bytes) -> dict[str, Any]:
    top = _parse(raw, primitive)
    _require(type(top) is dict and set(top) == {"bundle"}, "fixture envelope differs")
    bundle = top["bundle"]
    _require(type(bundle) is dict and set(bundle) == {
        "version", "target", "prompt", "dsl", "assets", "hashes"
    }, "fixture bundle schema differs")
    _require(bundle["version"] == BUNDLE_VERSION and bundle["target"] == TARGET,
             "fixture version/target differs")
    _require(bundle["prompt"] == _prompt() and bundle["dsl"] == _dsl(),
             "fixture prompt/DSL differs")
    assets = bundle["assets"]
    _require(type(assets) is dict and set(assets) == {"backend"}, "unknown asset/leaf")
    _require(assets["backend"] == {"id": BACKEND_ID, "sha256": _sha(source)},
             "fixture source-derived descriptor differs")
    hashes = bundle["hashes"]
    _require(type(hashes) is dict and set(hashes) == {
        "prompt_sha256", "dsl_sha256", "payload_sha256"
    }, "fixture hash schema differs")
    _require(hashes["prompt_sha256"] == _sha(_canon(bundle["prompt"])), "prompt hash differs")
    _require(hashes["dsl_sha256"] == _sha(_canon(bundle["dsl"])), "DSL hash differs")
    payload = {key: value for key, value in bundle.items() if key != "hashes"}
    _require(hashes["payload_sha256"] == _sha(_canon(payload)), "payload hash differs")
    return bundle


@dataclass(frozen=True, slots=True)
class SyntheticProof:
    label: str
    request_sha256: str
    before_sha256: str
    after_sha256: str
    compared_paths: tuple[str, ...]
    product_sha256: str | None
    acceptance_granted: bool = field(default=False, init=False)


def compare_fixture(*, helper_source: bytes, host: str, role: str, case: str,
                    request: bytes, before_status: int, after_status: int,
                    before: bytes, after: bytes, changed_paths: tuple[str, ...],
                    before_products: dict[str, bytes], after_products: dict[str, bytes]) -> SyntheticProof:
    """Exercise a hardcoded toy fixture. It cannot admit real policy or inputs."""
    _require(type(host) is str and host == FIXTURE_HOST, "wrong fixture host")
    _require(type(role) is str and role == FIXTURE_ROLE, "wrong fixture role")
    _require(type(case) is str and case in {CASE_OK, CASE_ERROR}, "wrong fixture case")
    _require(type(request) is bytes and request == (REQUEST_OK if case == CASE_OK else REQUEST_ERROR),
             "fixture request identity differs")
    _require(type(before_status) is int and type(after_status) is int and
             before_status == after_status == (200 if case == CASE_OK else 422),
             "fixture status differs")
    _require(type(before) is bytes and type(after) is bytes, "immutable raw bytes required")
    _require(type(changed_paths) is tuple, "explicit immutable fixture leaf set required")
    helper = _primitive(helper_source)
    if case == CASE_ERROR:
        _require(changed_paths == (), "error fixtures permit no exceptions")
        _require(type(before_products) is dict and before_products == {} and
                 type(after_products) is dict and after_products == {}, "error fixture has products")
        _parse(before, helper)
        _parse(after, helper)
        _require(before == after == ERROR_RAW, "error bytes differ")
        return SyntheticProof("SYNTHETIC / NO ACCEPTANCE", _sha(request), _sha(before),
                              _sha(after), (), None)
    _require(changed_paths == CHANGED_LEAVES, "unknown, missing, extra or reordered fixture leaves")
    left, right = _bundle(before, helper, BEFORE_SOURCE), _bundle(after, helper, AFTER_SOURCE)
    # Compute the old and new payload from the complete parsed objects. We do
    # not accept any expected digest or source authority from the caller.
    old_payload = _sha(_canon({key: value for key, value in left.items() if key != "hashes"}))
    new_payload = _sha(_canon({key: value for key, value in right.items() if key != "hashes"}))
    rebased = copy.deepcopy(right)
    rebased["assets"]["backend"]["sha256"] = _sha(BEFORE_SOURCE)
    rebased_payload = _sha(_canon({key: value for key, value in rebased.items() if key != "hashes"}))
    _require(rebased_payload == old_payload, "rebased fixture payload differs")
    pins = ((CHANGED_LEAVES[0], _sha(BEFORE_SOURCE), _sha(AFTER_SOURCE)),
            (CHANGED_LEAVES[1], old_payload, new_payload))
    try:
        helper.verify_raw(before, after, pins)
    except ValueError as error:
        raise FixtureRefused("unlisted fixture raw byte drift") from error
    # The accepted set is one hardcoded toy product; equal arbitrary maps do
    # not qualify. No real source/product map is accepted by this prototype.
    expected = {PRODUCT_PATH: PRODUCT_BYTES}
    _require(type(before_products) is dict and before_products == expected and
             type(after_products) is dict and after_products == expected,
             "fixture product set or bytes differ")
    try:
        proof = strict_adapter.compare_products(before_files=before_products,
                                                after_files=after_products)
    except ValueError as error:
        raise FixtureRefused("fixture product comparison refused") from error
    _require(proof.acceptance_granted is False and len(proof.files) == 1,
             "strict fixture proof shape differs")
    return SyntheticProof("SYNTHETIC / NO ACCEPTANCE", _sha(request), _sha(before),
                          _sha(after), CHANGED_LEAVES, proof.files[0].before_sha256)
