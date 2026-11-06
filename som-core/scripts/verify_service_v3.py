"""Check a standalone SOM service against an isolated real PostgreSQL database."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from som_core.v1 import assemble, load_blocks, resolve_prompt_refs, validate_prompt
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python/.venv/bin/python"
ALEMBIC = ROOT / "som-code-python/.venv/bin/alembic"
if os.environ.get("SOM_FULL_PRODUCT_CONTEXT"):
    from full_product_context import observer_python
    PYTHON = observer_python()
    ALEMBIC = PYTHON.parent / "alembic"
BLOCKS_V3 = ROOT / "som-code-python/data/blocks/v3"
BLOCKS_V4 = ROOT / "som-code-python/data/blocks/v4"


def command(
    args: list[str], *, cwd: Path | None = None, env: dict | None = None
) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} exited {result.returncode}\n{result.stdout}{result.stderr}"
        )


def source_map(
    prompt: dict, dsl: dict, files_json: Path | None, candidate_dir: Path | None
) -> dict[str, str]:
    resolved = resolve_prompt_refs(prompt, dsl)
    paths = {item["path"] for item in resolved["files"]}
    if files_json:
        payload = json.loads(files_json.read_text())
        files = payload.get("files", payload)
    elif candidate_dir:
        files = {path: (candidate_dir / path).read_text() for path in paths}
    else:
        blocks = BLOCKS_V4 if prompt["version"] == "som.prompt.v4" else BLOCKS_V3
        files = assemble(resolved, load_blocks(blocks))
    if set(files) != paths or not all(
        isinstance(source, str) for source in files.values()
    ):
        raise ValueError("candidate files differ from the required service file set")
    return files


def check_api(root: Path, prompt: dict, url: str) -> None:
    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, str(root))
    package = prompt["package"]
    app = importlib.import_module(f"{package}.app").app
    database = importlib.import_module(f"{package}.database")
    table = prompt["resource"]["table"]
    route = prompt["resource"]["path"]
    fields = prompt["resource"]["fields"]
    filter_field = prompt["query"]["filter_field"]
    date_field = prompt["query"].get("date_field")
    max_limit = prompt["query"]["max_limit"]
    columns = {column["name"] for column in inspect(database.engine).get_columns(table)}
    assert columns == {"id", "created_at", "updated_at"} | {f["name"] for f in fields}
    indexes = inspect(database.engine).get_indexes(table)
    assert any(index["column_names"] == [filter_field, "id"] for index in indexes), (
        indexes
    )
    if date_field:
        assert any(index["column_names"] == [date_field, "id"] for index in indexes), (
            indexes
        )
    payload = {
        field["name"]: {"str": "sample", "int": 3, "bool": True, "float": 1.5}[
            field["type"]
        ]
        for field in fields
        if field["required"]
    }
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        first = client.post(route, json=payload)
        assert first.status_code == 201, first.text
        row = first.json()
        item_id = row["id"]
        assert row[filter_field] is False
        assert row["created_at"] and row["updated_at"]
        for field in fields:
            name = field["name"]
            if field["required"]:
                assert row[name] == payload[name]
            elif field["default"] is None:
                assert row[name] is None
            else:
                assert row[name] is False
        assert client.get(f"{route}/{item_id}").status_code == 200
        assert (
            client.get(route, params={filter_field: False}).json()[0]["id"] == item_id
        )
        updated = client.patch(f"{route}/{item_id}", json={filter_field: True})
        assert updated.status_code == 200, updated.text
        assert updated.json()[filter_field] is True
        assert updated.json()["created_at"] == row["created_at"]
        assert updated.json()["updated_at"] >= row["updated_at"]
        for field in fields:
            name = field["name"]
            if name != filter_field:
                assert updated.json()[name] == row[name]
        assert [
            r["id"]
            for r in client.get(
                route, params={filter_field: True, "limit": 1, "offset": 0}
            ).json()
        ] == [item_id]
        assert client.get(route, params={filter_field: False}).json() == []
        assert client.get(route, params={"offset": 1}).json() == []
        if date_field:
            assert client.get(route, params={"before": "2030-01-03"}).json() == []
            dated = client.patch(f"{route}/{item_id}", json={date_field: "2030-01-02"})
            assert dated.status_code == 200, dated.text
            assert dated.json()[date_field] == "2030-01-02"
            assert [
                item["id"]
                for item in client.get(route, params={"before": "2030-01-03"}).json()
            ] == [item_id]
            assert client.get(route, params={"before": "2030-01-02"}).json() == []
            assert client.get(route, params={"before": "2030-01-01"}).json() == []
            assert [
                item["id"]
                for item in client.get(
                    route, params={filter_field: True, "before": "2030-01-03"}
                ).json()
            ] == [item_id]
            assert (
                client.get(
                    route, params={filter_field: False, "before": "2030-01-03"}
                ).json()
                == []
            )
            assert client.get(route, params={"before": "not-a-date"}).status_code == 422
            assert (
                client.post(
                    route, json={**payload, date_field: "not-a-date"}
                ).status_code
                == 422
            )
            cleared = client.patch(f"{route}/{item_id}", json={date_field: None})
            assert cleared.status_code == 200, cleared.text
            assert client.get(route, params={"before": "2030-01-03"}).json() == []
            assert (
                client.patch(
                    f"{route}/{item_id}", json={date_field: "2030-01-02"}
                ).status_code
                == 200
            )
        for params in ({"limit": 0}, {"limit": max_limit + 1}, {"offset": -1}):
            assert client.get(route, params=params).status_code == 422
        assert (
            client.patch(f"{route}/{item_id}", json={filter_field: None}).status_code
            == 422
        )
        for field in fields:
            name = field["name"]
            if field["required"]:
                assert (
                    client.post(
                        route,
                        json={
                            key: value for key, value in payload.items() if key != name
                        },
                    ).status_code
                    == 422
                )
            if field["required"] or field.get("default") is False:
                assert (
                    client.patch(f"{route}/{item_id}", json={name: None}).status_code
                    == 422
                )
        assert (
            client.post(route, json={"created_at": "2020-01-01", **payload}).status_code
            == 422
        )
        assert client.get(f"{route}/999999").status_code == 404
        assert client.patch(f"{route}/999999", json={}).status_code == 404
        assert client.delete(f"{route}/999999").status_code == 404
        duplicate = client.post(route, json=payload)
        if any(f["unique"] and f["required"] for f in fields):
            assert duplicate.status_code == 409, duplicate.text
        else:
            assert duplicate.status_code == 201, duplicate.text
        if package == "todos" and max_limit == 100:
            for _ in range(20):
                assert client.post(route, json=payload).status_code == 201
            assert len(client.get(route).json()) == 20
            assert len(client.get(route, params={"limit": 100}).json()) == 22
    database.engine.dispose()
    restart_check = (
        "from fastapi.testclient import TestClient\n"
        f"from {package}.app import app\n"
        f"response = TestClient(app).get('{route}/{item_id}')\n"
        "assert response.status_code == 200, response.text\n"
    )
    command([str(PYTHON), "-c", restart_check], cwd=root, env=os.environ.copy())
    with TestClient(app) as client:
        assert client.delete(f"{route}/{item_id}").status_code == 204
        assert client.get(f"{route}/{item_id}").status_code == 404


def verify(
    index: int, cases: Path, files_json: Path | None, candidate_dir: Path | None
) -> dict:
    row = json.loads(cases.read_text().splitlines()[index])
    prompt, dsl = row["prompt"], row["dsl"]
    validate_prompt(prompt)
    if prompt["version"] not in {"som.prompt.v3", "som.prompt.v4"}:
        raise ValueError("expected a standalone service prompt")
    files = source_map(prompt, dsl, files_json, candidate_dir)
    name = f"som_v{4 if prompt['version'] == 'som.prompt.v4' else 3}_{uuid.uuid4().hex[:16]}"
    url = f"postgresql+psycopg:///{name}"
    command(["createdb", name])
    try:
        with tempfile.TemporaryDirectory(prefix="som-v3-verify-") as temporary:
            root = Path(temporary)
            for path, source in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            env = os.environ | {"TEST_DATABASE_URL": url, "DATABASE_URL": url}
            command(
                [str(PYTHON), "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=root,
                env=env,
            )
            command([str(ALEMBIC), "upgrade", "head"], cwd=root, env=env)
            check_api(root, prompt, url)
            command([str(ALEMBIC), "downgrade", "base"], cwd=root, env=env)
            from sqlalchemy import create_engine

            engine = create_engine(url)
            assert not inspect(engine).has_table(prompt["resource"]["table"])
            engine.dispose()
        return {"index": index, "status": "passed", "file_count": len(files)}
    finally:
        command(["dropdb", "--if-exists", "--force", name])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/service-v3/heldout.jsonl"
    )
    parser.add_argument("--heldout-index", type=int, required=True)
    parser.add_argument("--files-json", type=Path)
    parser.add_argument("--candidate-dir", type=Path)
    args = parser.parse_args()
    if args.files_json and args.candidate_dir:
        parser.error("use only one of --files-json and --candidate-dir")
    print(
        json.dumps(
            verify(args.heldout_index, args.cases, args.files_json, args.candidate_dir)
        )
    )


if __name__ == "__main__":
    main()
