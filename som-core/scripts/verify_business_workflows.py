"""Behavior checks use requirements and HTTP results, never block source."""

import csv
import gzip
import hashlib
import io
import json
import statistics
from datetime import date
from decimal import Decimal

from verify_related import payload


def csv_value(value, kind):
    if value == "\\N":
        return None
    if kind == "bool":
        assert value.lower() in {"true", "false"}, value
        return value.lower() == "true"
    if kind == "int":
        return int(value)
    if kind == "float":
        number = Decimal(value)
        assert number.is_finite(), value
        return number
    if kind == "date":
        return date.fromisoformat(value)
    return value


def check_csv_values(text, fields, records):
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames == [field["name"] for field in fields]
    rows = list(reader)
    assert len(rows) == len(records)
    for row, expected in zip(rows, records, strict=True):
        assert set(row) == {field["name"] for field in fields}
        for field in fields:
            key, kind = field["name"], field["type"]
            value = expected[key]
            expected_text = "\\N" if value is None else str(value)
            assert csv_value(row[key], kind) == csv_value(expected_text, kind), (key, row[key], value)


def check_workflows(prompt, client, providers, snapshot, done):
    parent, child = (prompt["resources"][r] for r in ("parent", "child"))
    caps = prompt["capabilities"]
    workflow = caps["workflow"]
    value_spec = next((field for field in child['fields']
                       if field['name'] == caps['value_field']), None)
    nullable_value = (value_spec is not None and not value_spec['required']
                      and value_spec.get('default') is None)
    expected_values = []

    def request(method, route, status, **kwargs):
        r = client.request(method, route, **kwargs)
        assert r.status_code == status, (method, route, status, r.status_code, r.text)
        return r

    p = request("POST", parent["path"], 201, json=payload(parent, 92000, True)).json()
    route = parent["path"] + "/" + str(p["id"])
    children = route + child["path"]
    records = []
    for index in range(3):
        row = payload(child, 92001 + index, True)
        if caps["value_field"]:
            row[caps["value_field"]] = [0.1, 0.2, 0.0][index]
            if workflow == "statistics" and index == 2 and nullable_value:
                row[caps["value_field"]] = None
            expected_values.append(row[caps["value_field"]])
        if caps["group_field"]:
            row[caps["group_field"]] = "kg"
        records.append(request("POST", children, 201, json=row).json())
    if workflow == 'statistics' and not nullable_value:
        invalid = payload(child, 92999, True)
        invalid[caps['value_field']] = None
        before = snapshot()
        request('POST', children, 422, json=invalid)
        assert before == snapshot()
    export = request("GET", route + "/export", 200)
    if workflow == "json_export":
        assert export.json() == records, (export.json(), records)
    elif workflow == "gzip_export":
        decoded = json.loads(gzip.decompress(export.content))
        assert decoded == records, (decoded, records)
        assert request("GET", route + "/export", 200).content == export.content
    elif workflow == "decimal_summary":
        assert Decimal(export.json()["totals"]["kg"]) == Decimal("0.3")
        changed = {caps["value_field"]: .4}
        request("PATCH", children + "/" + str(records[0]["id"]), 200, json=changed)
        assert Decimal(request("GET", route + "/export", 200).json()["totals"]["kg"]) == Decimal("0.6")
    elif workflow == "statistics":
        result = export.json()
        values = [value for value in expected_values if value is not None]
        assert result == {"count": len(values), "mean": statistics.fmean(values),
                          "median": statistics.median(values),
                          "minimum": min(values), "maximum": max(values)}
        request("PATCH", children + "/" + str(records[0]["id"]), 200, json={caps["value_field"]: 0})
        assert request("GET", route + "/export", 200).json()["minimum"] == 0
    elif workflow == "csv":
        columns = [f["name"] for f in child["fields"]]
        check_csv_values(export.text, child["fields"], records)

        def csv_text(items, headers=columns):
            text = io.StringIO(newline="")
            writer = csv.DictWriter(text, headers, lineterminator="\n")
            writer.writeheader()
            writer.writerows({key: "\\N" if row[key] is None else row[key] for key in headers} for row in items)
            return text.getvalue()

        new = [payload(child, 93000 + i, True) for i in range(2)]
        request("POST", route + "/import", 201, content=csv_text(new), headers={"Content-Type": "text/csv"})
        unique = next(f["name"] for f in child["fields"] if f["unique"])
        conflict = [payload(child, 94000 + i, True) for i in range(2)]
        conflict[1][unique] = conflict[0][unique]
        before = snapshot()
        request("POST", route + "/import", 409, content=csv_text(conflict), headers={"Content-Type": "text/csv"})
        assert before == snapshot()
        for text in ("wrong\nx\n", csv_text([]), csv_text([payload(child, 95000 + i, True) for i in range(21)])):
            request("POST", route + "/import", 422, content=text, headers={"Content-Type": "text/csv"})
            assert before == snapshot()
        request("POST", route + "/import", 201, content=csv_text([payload(child, 96000, True)]), headers={"Content-Type": "text/csv"})
        done("csv_import_validation_late_conflict_atomic_rollback_and_recovery")
    done("business_" + workflow + "_content_and_numeric_or_null_semantics")
    request("GET", parent["path"] + "/999999999/export", 404)

    if caps["storage"] != "none":
        body = b"synthetic object bytes\x00\xff\n"
        key = "asset_" + providers.run_id
        path = "/objects/" + key
        request("GET", path, 404)
        request("PUT", path, 422, content=b"")
        request("PUT", path, 413, content=b"x" * (1024 * 1024 + 1))
        request("PUT", "/objects/unsafe.key", 422, content=body)
        providers.control(faults={caps["storage"]: True})
        try:
            request("PUT", path, 503, content=body)
        finally:
            providers.control(faults={})
        created = request("PUT", path, 200, content=body).json()
        assert created == {"key": key, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        assert request("GET", path, 200).content == body
        assert request("GET", path, 200).headers["x-content-sha256"] == created["sha256"]
        assert request("PUT", path, 200, content=body).json() == created
        request("PUT", path, 409, content=b"different")
        # The actual business record retains the independently stored object key.
        if any(field['name'] == 'object_key' for field in child['fields']):
            saved = request("PATCH", children + "/" + str(records[0]["id"]), 200, json={"object_key": key}).json()
            assert saved["object_key"] == key
        else:
            before = snapshot()
            request('PATCH', children + '/' + str(records[0]['id']), 422, json={'object_key': key})
            assert before == snapshot()
        providers.control(faults={caps["storage"]: True})
        try:
            request("GET", path, 503)
            request("DELETE", path, 503)
        finally:
            providers.control(faults={})
        assert request("GET", path, 200).content == body
        request("DELETE", path, 204)
        request("GET", path, 404)
        request("DELETE", path, 204)
        done(caps["storage"] + "_upload_download_checksum_metadata_idempotence_size_key_failure_and_recovery")

    # Bounds apply to all app inputs, including partial updates.
    item = children + "/" + str(records[0]["id"])
    for field in child["fields"]:
        if field["type"] == "str":
            request("PATCH", item, 422, json={field["name"]: "x" * 256})
        elif field["type"] == "int":
            request("PATCH", item, 422, json={field["name"]: 2**31})
        elif field["type"] == "float":
            request("PATCH", item, 422, content=json.dumps({field["name"]: "Infinity"}), headers={"Content-Type": "application/json"})
    done("bounded_string_integer_and_finite_float_inputs")
