"""Provider ownership and pre-write validation checks for compact blocks."""

import asyncio
import ast
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from google.api_core import exceptions as gcs_errors
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
import pytest
import requests

from som_core.v1 import _render

ROOT = Path(__file__).resolve().parents[2]


class ObjectUnavailable(Exception):
    """The provider cannot prove ownership or an outcome."""


class ObjectExists(Exception):
    """Another write owns different content."""


class ObjectMissing(Exception):
    """The requested provider version does not exist."""


def provider(kind, **values):
    blocks = json.loads((ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json").read_text())
    block = next(item for item in blocks if item["id"] == "compact.storage.flow." + kind)
    source = _render(block["template"], {"versioned": True}, block["params"], "provider", True)
    module = ModuleType("compact_provider_boundary")
    module.__dict__.update(BaseModel=BaseModel, ConfigDict=ConfigDict, TypeAdapter=TypeAdapter,
                           ValidationError=ValidationError, requests=requests,
                           gcs_errors=gcs_errors, hashlib=hashlib, base64=base64,
                           ObjectMissing=ObjectMissing, ObjectExists=ObjectExists,
                           ObjectUnavailable=ObjectUnavailable, MAX_OBJECT_BYTES=1048576,
                           ClientError=ClientError, BotoCoreError=BotoCoreError,
                           failure=lambda status, detail: HTTPException(status, detail), **values)
    # Load the actual SDK error boundary. Avoid provider setup and network I/O.
    boundary = next(item for item in blocks if item['id'] == 'compact.storage.' + kind)
    tree = ast.parse(boundary['template'])
    guard = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'storage_errors')
    module.__dict__['contextmanager'] = contextmanager
    exec(compile('from __future__ import annotations\n' + ast.unparse(guard), 'provider-errors', 'exec'), module.__dict__)
    exec(compile("from __future__ import annotations\n" + source, "compact-provider", "exec"), module.__dict__)
    return module


class S3:
    """Immutable versions with independently controlled ownership receipts."""

    def __init__(self, versions, conflict=False):
        self.versions = versions
        self.conflict = conflict
        self.deleted = []
        self.heads = []

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return self

    def paginate(self, **_options):
        yield {"Versions": [{"Key": "key", "VersionId": version} for version in self.versions]}

    def head_object(self, *, Key, VersionId, **_options):
        self.heads.append((Key, VersionId))
        return {"Metadata": self.versions[VersionId], "ETag": "etag"}

    def put_object(self, **_options):
        if self.conflict:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        return {"VersionId": "created"}

    def delete_object(self, **options):
        self.deleted.append(options)


@pytest.mark.parametrize("ours", [False, True])
def test_s3_same_body_retry_distinguishes_our_write_from_a_prior_object(ours):
    metadata = {"som-run": "run" if ours else "another", "som-step": "step"}
    client = S3({"immutable": metadata}, conflict=True)
    module = provider("s3", S3_BUCKET="bucket", s3_client=client, storage_get=lambda _key: b"content")
    receipt = module.object_put("key", b"content", "run", "step")
    assert receipt.owned is ours
    assert receipt.version == ("immutable" if ours else None)
    assert receipt.body == base64.b64encode(b"content").decode()
    assert not client.deleted


def test_s3_ambiguous_owned_versions_are_not_guessed():
    client = S3({key: {"som-run": "run", "som-step": "step"} for key in ("one", "two")})
    module = provider("s3", S3_BUCKET="bucket", s3_client=client)
    with pytest.raises(ObjectUnavailable):
        module.object_version("key", "run", "step")
    assert not client.deleted


def test_s3_unknown_version_never_reaches_a_delete():
    client = S3({})
    module = provider("s3", S3_BUCKET="bucket", s3_client=client)
    assert module.object_reverse("key", "run", "step", None) is False
    assert client.heads == client.deleted == []


def test_s3_compensation_deletes_only_the_proven_immutable_version():
    client = S3({
        "owned": {"som-run": "run", "som-step": "step"},
        "newer": {"som-run": "another", "som-step": "step"},
    })
    module = provider("s3", S3_BUCKET="bucket", s3_client=client)
    assert module.object_reverse("key", "run", "step", "owned") is True
    assert client.heads == [("key", "owned")]
    assert client.deleted == [{"Bucket": "bucket", "Key": "key", "VersionId": "owned"}]
    assert module.object_reverse("key", "run", "step", "newer") is False
    assert len(client.deleted) == 1


@pytest.mark.parametrize("value", [[], "invalid", {"som-run": 17}])
def test_malformed_s3_ownership_is_an_error_and_never_deletes(value):
    client = S3({"version": value})
    module = provider("s3", S3_BUCKET="bucket", s3_client=client)
    with pytest.raises(ObjectUnavailable):
        module.object_reverse("key", "run", "step", "version")
    with pytest.raises(ObjectUnavailable):
        module.object_version("key", "run", "step")
    assert not client.deleted


def test_gcs_missing_ownership_metadata_does_not_hide_an_owned_generation():
    blobs = [SimpleNamespace(name="key", metadata=None, generation=1),
             SimpleNamespace(name="other", metadata={"som-run": "run", "som-step": "step"}, generation=2),
             SimpleNamespace(name="key", metadata={"som-run": "run", "som-step": "step"}, generation=3)]
    client = SimpleNamespace(list_blobs=lambda *_args, **_kwargs: iter(blobs))
    module = provider("gcs", GCS_BUCKET="bucket", gcs_client=client)
    assert module.object_version("key", "run", "step") == "3"


def test_gcs_unknown_version_never_reaches_provider_io():
    module = provider("gcs")
    assert module.object_reverse("key", "run", "step", None) is False


@pytest.mark.parametrize(
    "body,status",
    [("!", 422), ("", 422), (base64.b64encode(b"x" * 1048577).decode(), 413)],
    ids=["invalid", "empty", "oversize"],
)
def test_bad_object_content_is_rejected_before_recording_an_intent(body, status):
    source = (ROOT / "som-core/block_templates/flow_compact/forward_support.py.tpl").read_text()
    module = ModuleType("compact_forward_boundary")
    import re
    module.__dict__.update(base64=base64, re=re, HTTPException=HTTPException)
    exec(compile("from __future__ import annotations\nclass Methods:\n" + source, "compact-forward", "exec"), module.__dict__)
    provider_module = provider("s3")
    module.check_flow_body = provider_module.check_flow_body
    ctx = module.Methods()
    ctx.record = SimpleNamespace(effects=[])
    effect = SimpleNamespace(group="", key="", uncertain=False)
    with pytest.raises(HTTPException) as error:
        asyncio.run(ctx.put(effect, "key", body))
    assert error.value.status_code == status
    assert ctx.record.effects == []


@pytest.mark.parametrize("outcome", ["owned", "absent", "unavailable"])
def test_conflict_keeps_proven_or_uncertain_ownership_for_compensation(outcome):
    source = (ROOT / "som-core/block_templates/flow_compact/forward_support.py.tpl").read_text()
    module = ModuleType("compact_conflict_boundary")
    import re

    def conflict(*_args):
        raise ObjectExists()

    def version(*_args):
        if outcome == "unavailable":
            raise ObjectUnavailable()
        return "our-version" if outcome == "owned" else None

    async def call(function, *args):
        return function(*args)

    module.__dict__.update(
        base64=base64, re=re, HTTPException=HTTPException,
        ObjectExists=ObjectExists, ObjectUnavailable=ObjectUnavailable,
        run_in_threadpool=call, flow_object_put=conflict,
        flow_object_version=version,
        check_flow_body=provider("s3").check_flow_body,
    )
    exec(compile("from __future__ import annotations\nclass Methods:\n" + source, "compact-conflict", "exec"), module.__dict__)
    context = module.Methods()
    context.record = SimpleNamespace(run_id="run", effects=[], values={})
    effect = SimpleNamespace(group="", step="step", key="", version=None, uncertain=False, owned=False)
    snapshots = []

    async def persist():
        snapshots.append((effect.version, effect.uncertain, effect.owned))

    context.persist = persist
    with pytest.raises(ObjectExists):
        asyncio.run(context.put(effect, "key", base64.b64encode(b"content").decode()))
    assert context.record.effects == [effect]
    assert snapshots[0] == (None, True, False)
    expected = {
        "owned": ("our-version", False, True),
        "absent": (None, False, False),
        "unavailable": (None, True, False),
    }
    assert snapshots[-1] == expected[outcome]
