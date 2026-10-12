"""Check named business-flow invariants absent from the frozen generic oracle."""

import os
import uuid

import httpx
from audit_business_transactions import source_hashes
from business_database_checks import Mongo, Postgres
from business_test_support import Providers
from verify_related import Server, payload, run


def assert_partial_update(before, after, field, value):
    assert set(after) == set(before), "PATCH changed the response schema"
    assert after[field] == value, (field, value, after[field])
    assert all(after[key] == previous for key, previous in before.items()
               if key not in {field, "updated_at"}), "PATCH changed an unsubmitted field"
    assert after["updated_at"] >= before["updated_at"]


def check_journeys(prompt, client):
    parent, child = (prompt["resources"][role] for role in ("parent", "child"))
    checks = []

    def request(method, route, status=200, **kwargs):
        response = client.request(method, route, **kwargs)
        assert response.status_code == status, (method, route, status, response.status_code, response.text)
        return response.json() if response.content else None

    def duplicate_nonunique(resource, first, serial):
        values = payload(resource, serial, True)
        values.update({field["name"]: first[field["name"]] for field in resource["fields"] if not field["unique"]})
        return values

    p1 = request("POST", parent["path"], 201, json=payload(parent, 17000, True))
    p2 = request("POST", parent["path"], 201, json=duplicate_nonunique(parent, p1, 17100))
    parent_route = parent["path"] + "/" + str(p1["id"])
    children_route = parent_route + child["path"]
    other_route = parent["path"] + "/" + str(p2["id"]) + child["path"]
    first_values = payload(child, 17200, True)
    c1 = request("POST", children_route, 201, json=first_values)
    c2 = request("POST", other_route, 201, json=duplicate_nonunique(child, c1, 17300))
    c3 = request("POST", children_route, 201, json=duplicate_nonunique(child, c1, 17400))
    assert {row["id"] for row in request("GET", children_route)} == {c1["id"], c3["id"]}
    assert [row["id"] for row in request("GET", other_route)] == [c2["id"]]
    checks.append("nonunique_values_can_repeat_within_and_across_parents")

    item = children_route + "/" + str(c1["id"])
    for resource, route, role in ((parent, parent_route, "parent"), (child, item, "child")):
        for field in resource["fields"]:
            before = request("GET", route)
            name = field["name"]
            nullable = not field["required"] and field.get("default") is None
            value = None if nullable else {"str": f"changed_{role}_{name}", "int": 0, "float": 0.0,
                                           "bool": not before[name], "date": "2030-12-31"}[field["type"]]
            after = request("PATCH", route, json={name: value})
            assert_partial_update(before, after, name, value)
            assert request("GET", route) == after
    assert request("GET", other_route + "/" + str(c2["id"])) == c2
    checks.append("every_field_patch_preserves_all_unsubmitted_values_and_other_parent_rows")

    boolean, day = child["query"]["filter_field"], child["query"]["date_field"]
    if boolean:
        for created, base, flag, date_value in ((c1, children_route, False, "2030-01-02"),
                                               (c2, other_route, False, "2030-01-02"),
                                               (c3, children_route, not day, "2030-01-03")):
            changed = {boolean: flag}
            if day:
                changed[day] = date_value
            request("PATCH", base + "/" + str(created["id"]), json=changed)
        filters = {boolean: "false"}
        if day:
            filters["before"] = "2030-01-03"
        rows = request("GET", children_route, params=filters)
        assert [row["id"] for row in rows] == [c1["id"]], rows
        checks.append("pending_items_query_excludes_date_boundary_and_other_parent_rows")
    request("DELETE", parent_route, 409)
    assert request("GET", item)["id"] == c1["id"]
    checks.append("failed_parent_delete_preserves_its_business_records")
    return checks


def verify_journeys(prompt, root, fixture):
    root = root.resolve()
    original = source_hashes(root)
    dbname = "som_test_flow_" + uuid.uuid4().hex[:16]
    mongo = prompt["capabilities"]["database"] == "mongodb"
    backend = (Mongo if mongo else Postgres)(prompt, dbname, fixture)
    providers = Providers(fixture, prompt)
    inherited = {key: value for key, value in os.environ.items()
                 if not key.startswith("AWS_") and key != "GOOGLE_APPLICATION_CREDENTIALS"}
    env = inherited | backend.env | {"UV_CACHE_DIR": "/private/tmp/som-uv-cache", "UV_PYTHON_DOWNLOADS": "never"}
    backend.create(run)
    server = None
    try:
        env.update(providers.environment())
        run(["uv", "sync", "--python", "3.12"], cwd=root, env=env)
        server = Server(root, prompt["package"], env)
        env["GOOGLE_REDIRECT_URI"] = server.url + "/auth/google/callback"
        run(backend.migration_command(str(root / ".venv/bin/python"), True), cwd=root, env=env)
        backend.inspect()
        server.start()
        with httpx.Client(base_url=server.url, timeout=15) as client:
            providers.login(client)
            checks = check_journeys(prompt, client)
        assert source_hashes(root) == original, "product source changed during business-flow audit"
        return {"status": "passed", "package": prompt["package"], "checks": checks, "source_sha256": original}
    finally:
        if server:
            server.stop()
            server.log.close()
        try:
            providers.close()
        finally:
            backend.close(run)
