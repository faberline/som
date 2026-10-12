"""Check the shared provider failures through the actual HTTP handlers."""

import hashlib
import json
from contextlib import suppress
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi import Path as ApiPath
from fastapi.testclient import TestClient
import pytest
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[2]


def boundary():
    """Load the approved block in a small HTTP unit-test application."""
    library = json.loads(
        (ROOT / 'som-code-python/data/blocks/flow-v7-compact/blocks.json').read_text()
    )
    source = next(row['template'] for row in library
                  if row['id'] == 'compact.storage.common')
    module = ModuleType('object_boundary')
    module.__dict__.update(
        ClassVar=ClassVar, HTTPException=HTTPException, APIRouter=APIRouter,
        Depends=Depends, Annotated=Annotated, ApiPath=ApiPath,
        Request=Request, Response=Response, suppress=suppress,
        run_in_threadpool=run_in_threadpool, hashlib=hashlib,
        require_access=lambda: None,
    )
    # This block has no provider setup. All I/O is supplied by this unit test.
    exec(compile(source, 'approved-object-boundary', 'exec'), module.__dict__)
    application = FastAPI()
    application.include_router(module.object_router)
    return module, application


@pytest.mark.parametrize('name,status', [
    ('ObjectMissing', 404), ('ObjectExists', 409), ('ObjectUnavailable', 503),
])
def test_provider_details_never_enter_public_failures(name, status):
    module, _ = boundary()
    error = getattr(module, name)('secret SDK response', {'token': 'secret'})
    assert isinstance(error, HTTPException)
    assert error.status_code == status
    assert 'secret' not in error.detail


@pytest.mark.parametrize('method,name,status', [
    ('get', 'ObjectMissing', 404),
    ('get', 'ObjectUnavailable', 503),
    ('put', 'ObjectExists', 409),
    ('put', 'ObjectUnavailable', 503),
    ('delete', 'ObjectMissing', 204),
    ('delete', 'ObjectUnavailable', 503),
])
def test_provider_failure_keeps_its_http_status(method, name, status):
    module, application = boundary()

    def fail(*_arguments):
        raise getattr(module, name)('secret SDK response')

    module.storage_get = module.storage_delete = module.object_put = fail
    with TestClient(application) as client:
        response = getattr(client, method)('/objects/key', **(
            {'content': b'body'} if method == 'put' else {}
        ))
    assert response.status_code == status, response.text
    assert 'secret' not in response.text


def test_success_uses_one_provider_receipt_without_exposing_ownership():
    module, application = boundary()
    calls = []

    def put(key, body, owner, step):
        calls.append((key, body, owner, step))
        return SimpleNamespace(key=key, size=len(body), sha256='digest',
                               version='private', owned=True)

    module.object_put = put
    with TestClient(application) as client:
        response = client.put('/objects/key', content=b'body')
    assert response.status_code == 200, response.text
    assert response.json() == {'key': 'key', 'size': 4, 'sha256': 'digest'}
    assert calls == [('key', b'body', '', '')]
