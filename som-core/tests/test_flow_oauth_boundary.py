"""Typed sessions and malformed secret inputs must preserve auth failures."""

import ast
from contextlib import contextmanager
import hmac
import json
from pathlib import Path
from types import ModuleType
from typing import NotRequired, TypedDict
from collections.abc import Iterator

from fastapi import HTTPException, Request
from pydantic import TypeAdapter
import pytest
import redis

from som_core import flow_compact
from som_core.v1 import assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def boundary(saved):
    rows = [json.loads(line) for line in (ROOT / "som-core/data/flow-v7-r10/formal.jsonl").read_text().splitlines()]
    prompt = next(row["prompt"] for row in rows
                  if row["prompt"]["capabilities"]["auth"] == "google_oauth")
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    item = next(file for file in plan["files"] if file["path"].endswith("/integrations.py"))
    source = assemble({"version": "som.dsl.v2", "files": [item]}, blocks)[item["path"]]
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in {"Identity", "unauthorized", "same_secret", "authenticate"}]
    assert len(nodes) == 4
    module = ModuleType("generated_oauth_boundary")
    module.__dict__.update(TypedDict=TypedDict, NotRequired=NotRequired,
                           HTTPException=HTTPException, Request=Request,
                           TypeAdapter=TypeAdapter, hmac=hmac,
                           SESSION_COOKIE="session", REDIS_PREFIX="unit:",
                           redis_read=lambda _key: saved,
                           failure=lambda status, detail: HTTPException(status, detail))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), item["path"], "exec"), module.__dict__)
    return module


def request(method="GET", csrf=b"", binding=b"owned"):
    return Request({"type": "http", "method": method,
                    "headers": [(b"cookie", b"session=" + binding),
                                (b"x-csrf-token", csrf)]})


@pytest.mark.parametrize("value", [None, "bad-json", "{}", '[]',
                                  '{"sub":"","role":"editor","csrf":"ok"}',
                                  '{"sub":"owner","role":1,"csrf":"ok"}',
                                  '{"sub":"owner","role":"editor"}',
                                  '{"sub":"owner","role":"editor","csrf":null}',
                                  '{"sub":"owner","role":"editor","csrf":""}'])
def test_malformed_or_missing_session_returns_401(value):
    with pytest.raises(HTTPException) as error:
        boundary(value).authenticate(request())
    assert error.value.status_code == 401


def test_strict_identity_keeps_csrf_and_rejects_wrong_or_unicode_header():
    identity = {"sub": "owner", "role": "editor", "csrf": "correct"}
    module = boundary(json.dumps(identity))
    assert module.authenticate(request()) == identity
    assert module.authenticate(request("POST", b"correct")) == identity
    for header in (b"", b"wrong", b"\xe9"):
        with pytest.raises(HTTPException) as error:
            module.authenticate(request("POST", header))
        assert error.value.status_code == 403
    assert module.same_secret("nonce", "nonce")
    assert not module.same_secret("nonce", "\u00e9")


def session_boundary(client):
    """Load the actual compact Redis boundary without provider setup."""
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    source = blocks["compact.auth.google_oauth"]["template"]
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef)
             and node.name in {"session_errors", "redis_read", "redis_set", "logout"}]
    assert len(nodes) == 4
    module = ModuleType("generated_session_boundary")
    from fastapi import APIRouter, Depends, Response
    module.__dict__.update(contextmanager=contextmanager, Iterator=Iterator,
                           TypeAdapter=TypeAdapter, redis=redis,
                           redis_client=client, Request=Request, Response=Response,
                           auth_router=APIRouter(), Depends=Depends,
                           require_identity=lambda: {}, SESSION_COOKIE="session",
                           REDIS_PREFIX="unit:",
                           failure=lambda status, detail: HTTPException(status, detail))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "session-boundary", "exec"),
         module.__dict__)
    return module


class SessionRedis:
    """Observe keys, atomic consumption and set-if-absent options."""

    def __init__(self):
        self.values = {}
        self.calls = []
        self.error = None

    def check(self):
        if self.error is not None:
            raise self.error

    def get(self, key):
        self.check()
        self.calls.append(("get", key))
        return self.values.get(key)

    def getdel(self, key):
        self.check()
        self.calls.append(("getdel", key))
        return self.values.pop(key, None)

    def set(self, key, value, **options):
        self.check()
        self.calls.append(("set", key, options))
        assert options["nx"] is True
        if key in self.values:
            return None
        self.values[key] = value
        return True

    def delete(self, key):
        self.check()
        self.calls.append(("delete", key))
        return self.values.pop(key, None) is not None


def test_redis_state_is_consumed_atomically_and_cannot_be_overwritten():
    client = SessionRedis()
    module = session_boundary(client)
    module.redis_set("state", "saved", 300)
    assert client.calls[-1] == ("set", "state", {"ex": 300, "nx": True})
    with pytest.raises(HTTPException) as error:
        module.redis_set("state", "replacement", 300)
    assert error.value.status_code == 503
    assert module.redis_read("state") == "saved"
    assert module.redis_read("state", consume=True) == "saved"
    assert module.redis_read("state", consume=True) is None
    assert client.calls[-2:] == [("getdel", "state"), ("getdel", "state")]


@pytest.mark.parametrize("operation", ["read", "consume", "set", "logout"])
def test_redis_errors_are_503_without_provider_details(operation):
    client = SessionRedis()
    client.error = redis.RedisError("private provider diagnostic")
    module = session_boundary(client)
    calls = {"read": lambda: module.redis_read("key"),
             "consume": lambda: module.redis_read("key", consume=True),
             "set": lambda: module.redis_set("key", "data", 300),
             "logout": lambda: module.logout(request())}
    with pytest.raises(HTTPException) as error:
        calls[operation]()
    assert error.value.status_code == 503
    assert error.value.detail == "session store unavailable"


@pytest.mark.parametrize("value", [17, True, b"undecoded", ["unexpected"]])
def test_redis_read_does_not_coerce_malformed_sdk_results(value):
    client = SessionRedis()
    client.values["key"] = value
    with pytest.raises(HTTPException) as error:
        session_boundary(client).redis_read("key")
    assert error.value.status_code == 503
    assert error.value.detail == "invalid session response"


def test_logout_deletes_only_the_authenticated_session():
    client = SessionRedis()
    client.values = {"unit:session:owned": "identity", "unit:session:other": "other"}
    response = session_boundary(client).logout(request())
    assert response.status_code == 204
    assert client.values == {"unit:session:other": "other"}
    assert client.calls == [("delete", "unit:session:owned")]
