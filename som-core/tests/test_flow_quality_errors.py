"""The generated handler must keep useful errors without unsafe input data."""

import ast
import json
from pathlib import Path
from types import ModuleType
from typing import TypedDict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, TypeAdapter
import pytest

from som_core import flow_compact
from som_core.v1 import assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


class Sample(BaseModel):
    """A typed boundary in an isolated unit-test application."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    amount: float


def generated_handler():
    row = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl").read_text().splitlines()[0])
    prompt = row["prompt"]
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    item = next(file for file in plan["files"] if file["path"].endswith("/app.py"))
    source = assemble({"version": "som.dsl.v2", "files": [item]}, blocks)[item["path"]]
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
             and node.name in {"_ValidationIssue", "validation_error"}
             or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
             and node.target.id == "ISSUES"]
    assert len(nodes) == 3
    application = FastAPI()
    module = ModuleType("generated_validation_handler")
    module.__dict__.update(app=application, TypedDict=TypedDict, Request=Request,
                           RequestValidationError=RequestValidationError,
                           JSONResponse=JSONResponse, TypeAdapter=TypeAdapter)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), item["path"], "exec"), module.__dict__)

    @application.post("/unit-input")
    def receive(payload: Sample) -> Sample:
        return payload

    return application


@pytest.mark.parametrize("body", ['{"amount": NaN}', '{"amount": Infinity}',
                                  '{"amount": -Infinity}', '{"amount": "wrong"}',
                                  '{}', '{"amount": 0, "extra": "secret"}', '{'])
def test_nonfinite_and_other_input_errors_return_safe_422(body):
    with TestClient(generated_handler()) as client:
        response = client.post("/unit-input", content=body,
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    issues = response.json()["detail"]
    assert issues and all(set(issue) == {"type", "loc", "msg"} for issue in issues)
    assert all(issue["loc"][0] == "body" for issue in issues)
    assert "secret" not in response.text and "NaN" not in response.text


def test_zero_remains_valid():
    with TestClient(generated_handler()) as client:
        response = client.post("/unit-input", json={"amount": 0})
    assert response.status_code == 200 and response.json() == {"amount": 0}
