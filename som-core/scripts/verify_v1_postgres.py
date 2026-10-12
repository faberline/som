"""Run independent CRUD and migration checks against a v1 candidate.

PostgreSQL is the final gate. SQLite is a preliminary agent-side check.
Run with the corpus environment, which has FastAPI, SQLAlchemy, and Alembic.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid
from copy import deepcopy
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from som_core.v1 import (
    LOGIC_STEPS,
    LOGIC_STEPS_V2,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core" / "tests"))
from test_v1 import dsl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--heldout-index", type=int)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/v1/heldout.jsonl"
    )
    parser.add_argument(
        "--prompt-json",
        type=Path,
        help="validate an agent-authored prompt with the held-out DSL template",
    )
    parser.add_argument(
        "--files-json",
        type=Path,
        help="service response file map; omit for the Python reference",
    )
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        help="read a flagship-authored package for independent behavior checks",
    )
    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="use an isolated SQLite file for agent-side preliminary checks",
    )
    args = parser.parse_args()
    if args.files_json and args.candidate_dir:
        parser.error("use only one of --files-json and --candidate-dir")
    if args.prompt_json and args.heldout_index is None:
        parser.error("--prompt-json requires --heldout-index")
    if args.heldout_index is None:
        prompt = {"version": "som.prompt.v1"}
        document = dsl()
        resolved_document = document
        package = "items"
        table = "items"
        route = "/items"
        logic = list(LOGIC_STEPS)
        fields = [
            {"name": "title", "type": "str", "required": True, "unique": True},
            {"name": "active", "type": "bool", "required": False, "unique": False},
        ]
    else:
        rows = args.cases.read_text().splitlines()
        row = json.loads(rows[args.heldout_index])
        document = deepcopy(row["dsl"])
        prompt = (
            json.loads(args.prompt_json.read_text())
            if args.prompt_json
            else row["prompt"]
        )
        if args.prompt_json:
            validate_prompt(prompt)
            operation_names = ("create", "get", "list", "patch", "delete")
            steps = (
                LOGIC_STEPS_V2 if prompt["version"] == "som.prompt.v2" else LOGIC_STEPS
            )
            name_by_step = dict(zip(steps, operation_names, strict=True))
            api_blocks = document["files"][2]["blocks"]
            by_name = {block["name"]: block for block in api_blocks}
            document["files"][2]["blocks"] = [
                by_name["head"],
                *(by_name[name_by_step[step]] for step in prompt["logic"]),
                by_name["tail"],
            ]
        resolved_document = resolve_prompt_refs(prompt, document)
        package = prompt["package"]
        table = prompt["resource"]["table"]
        route = prompt["resource"]["path"]
        logic = prompt["logic"]
        fields = prompt["resource"]["fields"]
    payload = {
        f["name"]: {"str": "sample", "int": 3, "bool": True, "float": 1.5}[f["type"]]
        for f in fields
    }
    name = f"som_v1_{uuid.uuid4().hex[:12]}"
    if not args.sqlite:
        subprocess.run(["createdb", name], check=True)
    try:
        with tempfile.TemporaryDirectory(prefix="som-v1-pg-") as temporary:
            root = Path(temporary)
            if args.candidate_dir:
                files = {
                    file["path"]: (args.candidate_dir / file["path"]).read_text()
                    for file in resolved_document["files"]
                }
            elif args.files_json:
                files = json.loads(args.files_json.read_text())
            else:
                files = assemble(
                    resolved_document,
                    load_blocks(ROOT / "som-code-python" / "data" / "blocks" / "v1"),
                )
            if set(files) != {file["path"] for file in resolved_document["files"]}:
                raise AssertionError(
                    "generated file paths do not match the held-out task"
                )
            if args.files_json and args.heldout_index is not None:
                expected = assemble(
                    resolved_document,
                    load_blocks(ROOT / "som-code-python" / "data" / "blocks" / "v1"),
                )
                if files != expected:
                    raise AssertionError("generated files differ from the ordered task")
            for path, source in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")

            url = (
                f"sqlite:///{root / 'verify.db'}"
                if args.sqlite
                else f"postgresql+psycopg:///{name}"
            )
            env = os.environ | {"PYTHONPATH": str(root), "TEST_DATABASE_URL": url}
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(root / "tests"),
                    "-v",
                ],
                check=True,
                env=env,
            )

            sys.path.insert(0, str(root))
            api = importlib.import_module(f"{package}.api")
            models = importlib.import_module(f"{package}.models")
            create_router = api.create_router
            base_model = models.Base

            engine = create_engine(url)
            try:
                base_model.metadata.create_all(engine)

                def get_session():
                    with Session(engine) as session:
                        yield session

                app = FastAPI()
                api_router = create_router(get_session)
                expected_routes = {
                    LOGIC_STEPS[0]: ("POST", route),
                    LOGIC_STEPS[1]: ("GET", f"{route}/{{item_id}}"),
                    LOGIC_STEPS[2]: ("GET", route),
                    LOGIC_STEPS[3]: ("PATCH", f"{route}/{{item_id}}"),
                    LOGIC_STEPS[4]: ("DELETE", f"{route}/{{item_id}}"),
                }
                if prompt["version"] == "som.prompt.v2":
                    expected_routes[LOGIC_STEPS_V2[2]] = ("GET", route)
                actual_routes = [
                    (next(iter(item.methods)), item.path) for item in api_router.routes
                ]
                assert actual_routes == [expected_routes[step] for step in logic]
                app.include_router(api_router)
                client = TestClient(app)
                created = client.post(route, json=payload)
                assert created.status_code == 201, created.text
                item_id = created.json()["id"]
                if any(f["unique"] for f in fields):
                    assert client.post(route, json=payload).status_code == 409
                alternate = {
                    f["name"]: {
                        "str": "another",
                        "int": 4,
                        "bool": False,
                        "float": 2.5,
                    }[f["type"]]
                    for f in fields
                }
                second = client.post(route, json=alternate)
                assert second.status_code == 201, second.text
                second_id = second.json()["id"]
                assert [item["id"] for item in client.get(route).json()] == [
                    item_id,
                    second_id,
                ]
                if prompt["version"] == "som.prompt.v2":
                    third = client.post(route, json=payload)
                    assert third.status_code == 201, third.text
                    third_id = third.json()["id"]
                    field = prompt["query"]["filter_field"]
                    sample = payload[field]
                    other = alternate[field]
                    assert [
                        item["id"]
                        for item in client.get(
                            route, params={"filter_value": sample}
                        ).json()
                    ] == [item_id, third_id]
                    assert [
                        item["id"]
                        for item in client.get(
                            route, params={"filter_value": other}
                        ).json()
                    ] == [second_id]
                    assert [
                        item["id"]
                        for item in client.get(
                            route, params={"limit": 2, "offset": 1}
                        ).json()
                    ] == [second_id, third_id]
                    assert [
                        item["id"]
                        for item in client.get(
                            route,
                            params={"filter_value": sample, "limit": 1, "offset": 1},
                        ).json()
                    ] == [third_id]
                    assert client.get(route, params={"limit": 0}).status_code == 422
                    assert (
                        client.get(
                            route, params={"limit": prompt["query"]["max_limit"] + 1}
                        ).status_code
                        == 422
                    )
                    assert client.get(route, params={"offset": -1}).status_code == 422
                assert client.get(f"{route}/{item_id}").status_code == 200
                assert client.patch(f"{route}/{item_id}", json={}).status_code == 200
                first_field = fields[0]
                changed_value = {
                    "str": "updated",
                    "int": 5,
                    "bool": False,
                    "float": 3.5,
                }[first_field["type"]]
                changed = client.patch(
                    f"{route}/{item_id}",
                    json={first_field["name"]: changed_value},
                )
                assert changed.status_code == 200, changed.text
                assert changed.json()[first_field["name"]] == changed_value
                assert client.get(f"{route}/999999").status_code == 404
                assert client.patch(f"{route}/999999", json={}).status_code == 404
                assert client.delete(f"{route}/999999").status_code == 404
                assert client.delete(f"{route}/{item_id}").status_code == 204
                assert client.get(f"{route}/{item_id}").status_code == 404
                if any(not f["required"] for f in fields):
                    required_payload = {
                        f["name"]: alternate[f["name"]] for f in fields if f["required"]
                    }
                    assert client.post(route, json=required_payload).status_code == 201
                base_model.metadata.drop_all(engine)

                migration_file = root / resolved_document["files"][4]["path"]
                spec = importlib.util.spec_from_file_location(
                    "som_v1_migration", migration_file
                )
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with engine.begin() as connection:
                    module.op = Operations(MigrationContext.configure(connection))
                    module.upgrade()
                    inspection = inspect(connection)
                    columns = {
                        column["name"]: column
                        for column in inspection.get_columns(table)
                    }
                    assert set(columns) == {"id", *(field["name"] for field in fields)}
                    unique_columns = {
                        name
                        for constraint in inspection.get_unique_constraints(table)
                        for name in constraint["column_names"]
                    }
                    for field in fields:
                        assert (
                            columns[field["name"]]["nullable"] != field["required"]
                        ), f"migration field {field['name']}: wrong nullable flag"
                        assert (field["name"] in unique_columns) == field["unique"], (
                            f"migration field {field['name']}: wrong unique flag"
                        )
                    module.downgrade()
                    assert table not in inspect(connection).get_table_names()
            finally:
                engine.dispose()
    finally:
        if not args.sqlite:
            subprocess.run(["dropdb", "--if-exists", "--force", name], check=True)
    print("generated unittest, independent CRUD, and Alembic checks passed")


if __name__ == "__main__":
    main()
