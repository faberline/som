"""Independent HTTP observations for the bounded public flow requirement.

No block, DSL, tokenizer, model or expected source is used as a behavior answer.
Unfinished fault checks remain explicit. A diagnostic pass cannot close M2.
"""

from copy import deepcopy
import base64
import csv
from datetime import UTC, date, datetime
from decimal import Decimal
import gzip
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import uuid

import httpx

from audit_business_transactions import source_hashes
from composed_evidence import capture
from business_test_support import Providers
from flow_test_backends import FlowMongo, FlowPostgres, FlowProviders, shared_oracles
from flow_branch_cases import apply_case, branch_inputs, require_branch_coverage
from flow_fault_checks import checkpoint_failures, interrupted_forward, lease_checks, require_owner_routes
from flow_provider_fault_checks import provider_fault_checks
from flow_row_history import observe_rows
from verify_composed_services import verify_all
from verify_related import Server, payload, run
from verify_business_workflows import check_csv_values


def wire(row, resource):
    """Project observed storage values to the declared HTTP representation."""
    result = {}
    dates = {field['name'] for field in resource['fields'] if field['type'] == 'date'}
    floats = {field['name'] for field in resource['fields'] if field['type'] == 'float'}
    for name, value in row.items():
        if name.startswith("_som_"):
            continue
        if name == "_id":
            name = "id"
        if isinstance(value, datetime):
            value = value.date().isoformat() if name in dates else value.isoformat()
        elif isinstance(value, date):
            value = value.isoformat()
        elif name in floats and type(value) is int:
            # JSONB and MongoDB can represent an exact whole float as an
            # integer. The declared HTTP float type restores that scalar.
            value = float(value)
        result[name] = value
    return result


def equivalent(actual, expected):
    """Compare timestamps by instant, never hide a data or type difference."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        return set(actual) == set(expected) and all(equivalent(actual[key], expected[key]) for key in expected)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(equivalent(a, b) for a, b in zip(actual, expected, strict=True))
    if isinstance(actual, str) and isinstance(expected, str) and 'T' in actual and 'T' in expected:
        try:
            return datetime.fromisoformat(actual).astimezone(UTC) == datetime.fromisoformat(expected).astimezone(UTC)
        except ValueError:
            pass
    return type(actual) is type(expected) and actual == expected


def workflow_receipt(prompt, client, rows, parent_id):
    """Check the export content against direct database observations."""
    response = client.get(prompt["resources"]["parent"]["path"] + "/" + str(parent_id) + "/export")
    assert response.status_code == 200, response.text
    kind = prompt["capabilities"]["workflow"]
    if kind in {"json_export", "gzip_export"}:
        content = gzip.decompress(response.content) if kind == "gzip_export" else response.content
        assert equivalent(json.loads(content), rows), (json.loads(content), rows)
        data = json.loads(content)
    elif kind == "csv":
        check_csv_values(response.text, prompt["resources"]["child"]["fields"], rows)
        # A CSV body and the structured workflow receipt are separate values.
        # Derive the receipt from observed rows, including managed fields.
        data = deepcopy(rows)
    else:
        field = prompt["capabilities"]["value_field"]
        if kind == "decimal_summary":
            groups = {}
            for row in rows:
                unit = row[prompt["capabilities"]["group_field"]]
                groups[unit] = groups.get(unit, Decimal(0)) + Decimal(str(row[field]))
            assert {key: Decimal(value) for key, value in response.json()["totals"].items()} == groups
        else:
            values = [row[field] for row in rows if row[field] is not None]
            expected = {"count": len(values), "mean": statistics.fmean(values) if values else None,
                        "median": statistics.median(values) if values else None,
                        "minimum": min(values) if values else None,
                        "maximum": max(values) if values else None}
            assert equivalent(response.json(), expected), (response.json(), expected)
        data = response.json()
    return {"body": base64.b64encode(response.content).decode(), "size": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(), "data": data}


def expected_run(prompt, inputs, backend, client, initial, history):
    """Check each operation against its native committed row image."""
    rows = {role: [wire(row, prompt['resources'][role]) for row in initial[role]]
            for role in ("parent", "child")}
    values, branches = {}, {}
    foreign_key = prompt["relation"]["foreign_key"]
    events = iter(history)
    transactions = {}

    def reference(ref, visible):
        if "input" in ref:
            return inputs[ref["input"]]
        if "literal" in ref:
            return ref["literal"]
        value = visible[ref["step"]]
        for key in ref["field"].split(".") if ref["field"] else []:
            value = value[key]
        return deepcopy(value)

    def found(role, identifier=None, data=None):
        selected = [row for row in rows[role]
                    if (row["id"] == identifier if identifier is not None
                        else all(row[key] == value for key, value in data.items()))]
        assert len(selected) == 1, (role, identifier, data, selected)
        return deepcopy(selected[0])

    def write(role, method, data, parent_id, identifier, group):
        event = next(events, None)
        assert event is not None and (event['role'], event['operation']) == (
            role, 'insert' if method == 'create' else 'update'), (role, method, event)
        observed = wire(event['row'], prompt['resources'][role])
        assert event['transaction'] is not None
        transactions.setdefault(group, set()).add(event['transaction'])
        if method == 'create':
            assert not any(row['id'] == observed['id'] for row in rows[role]), observed
            expected = {field['name']: data.get(field['name'], field.get('default'))
                        for field in prompt['resources'][role]['fields']}
            if role == 'child':
                expected[foreign_key] = parent_id
            expected.update({key: observed[key] for key in ('id', 'created_at', 'updated_at')})
        else:
            expected = found(role, identifier=identifier)
            assert observed['id'] == identifier
            expected.update(data)
            assert datetime.fromisoformat(observed['updated_at']) >= datetime.fromisoformat(expected['updated_at'])
            expected['updated_at'] = observed['updated_at']
            rows[role] = [row for row in rows[role] if row['id'] != identifier]
        for field in prompt['resources'][role]['fields']:
            if field['type'] == 'float' and isinstance(expected[field['name']], int):
                expected[field['name']] = float(expected[field['name']])
        assert equivalent(observed, expected), (role, method, observed, expected)
        rows[role].append(deepcopy(observed))
        rows[role].sort(key=lambda row: row['id'])
        return observed

    def walk(nodes, visible, group=None):
        visible = dict(visible)
        for node in nodes:
            operation = node["op"]
            if operation == "transaction":
                visible = walk(node["steps"], visible, node['name'])
                continue
            if operation == "if":
                condition = node["condition"]
                left = reference(condition["left"], visible)
                right = reference(condition["right"], visible) if "right" in condition else None
                op = condition["op"]
                if op == "is_null":
                    decision = left is None
                elif op == "eq":
                    decision = left == right
                elif op == "ne":
                    decision = left != right
                elif op == "lt":
                    decision = left < right
                elif op == "le":
                    decision = left <= right
                elif op == "gt":
                    decision = left > right
                else:
                    assert op == "ge"
                    decision = left >= right
                branches[node["name"]] = decision
                arm = "then" if decision else "else"
                scoped = walk(node[arm], visible, group)
                for name, exports in node["exports"].items():
                    visible[name] = reference(exports[arm], scoped)
                continue
            arguments = {key: reference(ref, visible) for key, ref in node["args"].items()}
            if operation == "authorize":
                identity = client.get("/auth/me")
                assert identity.status_code == 200
                value = {key: identity.json()[key] for key in ("sub", "role")}
            elif operation == "batch.create":
                parent = write('parent', 'create', arguments['parent'], None, None, group)
                value = {'parent': parent, 'children': [
                    write('child', 'create', child, parent['id'], None, group)
                    for child in arguments['children']]}
            elif operation == "workflow.run":
                children = [row for row in rows["child"] if row[foreign_key] == arguments["parent_id"]]
                value = workflow_receipt(prompt, client, children, arguments["parent_id"])
            elif operation == "workflow.import":
                value = []
                for entry in csv.DictReader(io.StringIO(arguments['csv'])):
                    converted = {}
                    for field in prompt['resources']['child']['fields']:
                        raw = entry[field['name']]
                        converted[field['name']] = (None if raw == r'\N' else
                                                    int(raw) if field['type'] == 'int' else
                                                    float(raw) if field['type'] == 'float' else
                                                    raw.lower() == 'true' if field['type'] == 'bool' else raw)
                    value.append(write('child', 'create', converted, arguments['parent_id'], None, group))
            elif operation.startswith("object."):
                response = client.get("/objects/" + arguments["key"])
                assert response.status_code == 200
                value = {"key": arguments["key"], "size": len(response.content),
                         "body": base64.b64encode(response.content).decode(),
                         "sha256": hashlib.sha256(response.content).hexdigest()}
                if operation == "object.put":
                    assert value["body"] == arguments["body"]
            else:
                role, method = operation.split(".")
                if method in {'create', 'patch'}:
                    if method == 'patch' and not arguments['data']:
                        value = found(role, identifier=arguments[role + '_id'])
                    else:
                        value = write(role, method, arguments['data'], arguments.get('parent_id'),
                                      arguments.get(role + '_id'), group)
                elif method == 'get':
                    value = found(role, identifier=arguments[role + '_id'])
                else:
                    assert method == "list"
                    value = [row for row in rows[role] if role == "parent" or row[foreign_key] == arguments["parent_id"]][:20]
                if role == "child" and "parent_id" in arguments and not isinstance(value, list):
                    assert value[foreign_key] == arguments["parent_id"]
            visible[node["name"]] = value
        return visible

    values = walk(prompt["flow"]["steps"], values)
    assert next(events, None) is None, 'unexpected committed resource write'
    assert None not in transactions and all(len(ids) == 1 for ids in transactions.values()), transactions
    assert len({identifier for ids in transactions.values() for identifier in ids}) == len(transactions), transactions
    for role in ('parent', 'child'):
        final = [wire(row, prompt['resources'][role]) for row in backend.rows(role)]
        assert equivalent(rows[role], final), (role, rows[role], final)
    return {name: reference(ref, values) for name, ref in prompt["flow"]["result"].items()}, branches


def flow_inputs(prompt, serial, choose, servings, existing_id):
    """Use synthetic data while exercising zero, null and both branch arms."""
    parent = payload(prompt["resources"]["parent"], serial)
    if "servings" in parent:
        parent["servings"] = servings
    child = payload(prompt["resources"]["child"], serial + 1)
    if "amount" in child:
        child["amount"] = 0.1
    inputs = {}
    child_inputs = 0
    for field in prompt["flow"]["inputs"]:
        kind = field["type"]
        if kind == "parent_input":
            value = parent
        elif kind == "child_input":
            # Two branches can require different payloads of the same type.
            # Distinct synthetic values reveal a wrong input reference.
            value = child if child_inputs == 0 else payload(prompt['resources']['child'], serial + 10000 * child_inputs + 1)
            if 'amount' in value:
                value['amount'] = 0.1 * (child_inputs + 1)
            child_inputs += 1
        elif kind == "children_input":
            value = [child, payload(prompt["resources"]["child"], serial + 2)]
        elif kind in {"parent_patch", "child_patch"}:
            resource = prompt["resources"][kind.split("_")[0]]
            value = {item["name"]: (None if not item["required"] and item.get("default") is None
                                    else False if item["type"] == "bool" else 0)
                     for item in resource["fields"] if item["type"] in {"int", "bool"} or not item["required"]}
        elif kind == "boolean":
            value = choose
        elif kind == "integer":
            value = existing_id
        elif kind == "date":
            value = "2029-12-31" if choose else "2030-01-01"
        elif kind == "float":
            value = 0.1
        elif field["name"] == "csv":
            stream = io.StringIO(newline="")
            names = [item["name"] for item in prompt["resources"]["child"]["fields"]]
            writer = csv.DictWriter(stream, names, lineterminator="\n")
            writer.writeheader()
            data = payload(prompt["resources"]["child"], serial + 3, True)
            writer.writerow({key: "\\N" if value is None else value for key, value in data.items()})
            value = stream.getvalue()
        else:
            value = "flow_" + str(serial)
        inputs[field["name"]] = value
    return inputs


def generated_tests(python, root, env):
    """Run the supplied tests and reject an empty or skipped suite."""
    command = [python, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    result = subprocess.run(command, cwd=root, env=env, text=True,
                            capture_output=True, timeout=180, check=False)
    summary = re.search(r'Ran (\d+) tests? in ', result.stderr)
    assert result.returncode == 0 and summary and int(summary[1]) > 0, (
        result.returncode, result.stdout, result.stderr)
    assert '(skipped=' not in result.stderr, result.stderr
    return {'passed': True, 'command': command, 'tests': int(summary[1]),
            'stdout': result.stdout, 'stderr': result.stderr,
            'source_sha256': source_hashes(root / 'tests')}


def verify_flows(prompt, root, fixture):
    """Start a real service and observe typed outcomes and idempotency."""
    before = source_hashes(root)
    backend = (FlowMongo if prompt["capabilities"]["database"] == "mongodb" else FlowPostgres)(
        prompt, "som_test_flow_" + uuid.uuid4().hex[:16], fixture)
    providers = FlowProviders(fixture, prompt)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("AWS_") and key != "GOOGLE_APPLICATION_CREDENTIALS"}
    env.update(backend.env)
    env.update(UV_CACHE_DIR=os.environ.get("SOM_EVAL_UV_CACHE_DIR", "/private/tmp/som-uv-cache"),
               UV_LINK_MODE="copy", UV_PYTHON_DOWNLOADS="never")
    server = None
    row_observer = None
    records = []
    backend.create(run)
    try:
        env.update(providers.environment())
        run(["uv", "sync", "--python", "3.12"], cwd=root, env=env)
        python = str(root / ".venv/bin/python")
        server = Server(root, prompt["package"], env)
        env["GOOGLE_REDIRECT_URI"] = server.url + "/auth/google/callback"
        run(backend.migration_command(python, True), cwd=root, env=env)
        backend.inspect()
        unit_tests = generated_tests(python, root, env)
        server.start()
        route = "/flows/" + prompt["flow"]["name"] + "/runs"
        with httpx.Client(base_url=server.url, timeout=45) as client:
            providers.login(client)
            seed = client.post(prompt["resources"]["parent"]["path"], json=payload(prompt["resources"]["parent"], 800000))
            assert seed.status_code == 201, seed.text
            csv_header_check = {"applicable": False, "passed": True}
            if prompt["capabilities"]["workflow"] == "csv":
                import_route = (prompt["resources"]["parent"]["path"]
                                + "/" + str(seed.json()["id"]) + "/import")
                before_invalid_csv = backend.snapshot()
                invalid_csv = client.post(import_route, content='"unterminated\n',
                                          headers={"Content-Type": "text/csv"})
                assert invalid_csv.status_code == 422, invalid_csv.text
                assert backend.snapshot() == before_invalid_csv
                csv_header_check = {"applicable": True, "passed": True,
                                    "case": "unterminated_header_quote",
                                    "expected_status": 422,
                                    "actual_status": invalid_csv.status_code,
                                    "body": invalid_csv.json(), "writes": 0}
            row_observer = observe_rows(backend)
            branch_matrix = branch_inputs(prompt)
            for index, changes in enumerate(branch_matrix['cases']):
                inputs = apply_case(flow_inputs(prompt, 810000 + 100 * index,
                                               True, 1, seed.json()["id"]), changes)
                key = "flow-" + uuid.uuid4().hex
                response, initial, history = row_observer.capture(
                    lambda: client.post(route, json=inputs, headers={"Idempotency-Key": key}))
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["state"] == "succeeded", result
                expected, branches = expected_run(prompt, inputs, backend, client, initial, history)
                assert equivalent(result["result"], expected), (result["result"], expected)
                journal = backend.journal(key)
                assert journal["branches"] == branches
                assert journal["state"] == "succeeded" and journal["result"] == result["result"]
                assert journal["inputs"] == inputs
                snapshot = backend.snapshot()
                repeat = client.post(route, json=inputs, headers={"Idempotency-Key": key})
                assert repeat.status_code == 200 and repeat.json() == result
                assert backend.snapshot() == snapshot
                changed = dict(inputs)
                first = prompt["flow"]["inputs"][0]
                changed[first["name"]] = (not inputs[first["name"]] if first["type"] == "boolean"
                                           else inputs[first["name"]] + 1 if first["type"] == "integer"
                                           else dict(inputs[first["name"]], title="changed-content"))
                conflict = client.post(route, json=changed, headers={"Idempotency-Key": key})
                assert conflict.status_code == 409, conflict.text
                status = client.get("/flow-runs/" + result["run_id"])
                assert status.status_code == 200 and status.json() == result
                recovered = client.post("/flow-runs/" + result["run_id"] + "/recover")
                assert recovered.status_code == 200 and recovered.json() == result
                assert backend.snapshot() == snapshot
                records.append({"key": key, "run_id": result["run_id"], "branches": branches,
                                "expected": expected, "result": result, "journal": journal,
                                'initial_rows': initial, 'committed_row_history': history})
            coverage = require_branch_coverage(branch_matrix, records)
            owner_checks = require_owner_routes(prompt, client, providers, result)
            fault_checks = checkpoint_failures(
                prompt, backend, client,
                lambda phase: flow_inputs(prompt, 880000 if phase == 'late_failure' else 881000,
                                          True, 1, seed.json()['id']))
            lease_result = lease_checks(
                prompt, backend, client,
                lambda phase: flow_inputs(prompt, {'heartbeat_transaction': 890000,
                                                   'stale_executor': 891000,
                                                   'heartbeat_failure': 892000}[phase],
                                          True, 1, seed.json()['id']))
            restart_checks = interrupted_forward(
                prompt, backend, client, server,
                lambda: flow_inputs(prompt, 893000, True, 1, seed.json()['id']))
            provider_serial = iter(range(900000, 901000, 100))
            provider_checks = provider_fault_checks(
                prompt, backend, client, server, providers,
                lambda _phase: flow_inputs(prompt, next(provider_serial), True, 1, seed.json()['id']))
        backend.no_orphans()
        assert source_hashes(root) == before
        fault_matrix = {'database_faults': fault_checks, 'leases': lease_result,
                        'restart_and_inverse_interruption': restart_checks,
                        'provider_versions_and_interruption': provider_checks}
        pending = [name for name, result in fault_matrix.items() if not result.get('passed')]
        return {"core_flow_passed": True, "records": records,
                'generated_tests': unit_tests,
                'csv_header_check': csv_header_check,
                'branch_coverage': coverage,
                'owner_checks': owner_checks, 'database_fault_checks': fault_checks, 'lease_checks': lease_result,
                'restart_checks': restart_checks,
                'provider_fault_checks': provider_checks,
                "checks": ["typed_result_from_database_and_provider_observations", "both_boolean_arms_and_numeric_boundary",
                           "journal_branch_decisions", "durable_step_and_result_data", "idempotency_and_conflict",
                           "completed_recovery_does_not_replay"],
                "full_fault_matrix_passed": not pending,
                "pending": pending}
    finally:
        if row_observer:
            row_observer.close()
        if server:
            server.stop()
            server.log.close()
        providers.close()
        backend.close(run)


def verify(prompt, root, fixture):
    """Use the old complete CRUD checks and the new flow observations."""
    from fixture_guard import reserve_fixture
    with reserve_fixture(fixture):
        return _verify(prompt, root, fixture)


def _verify(prompt, root, fixture):
    """Hold all fixture controls across both check groups."""
    common = deepcopy(prompt)
    common.pop("flow")
    common["version"] = "som.prompt.v5"
    with shared_oracles():
        crud = verify_all(common, root, fixture)
    with capture(root) as evidence:
        flows = verify_flows(prompt, root, fixture)
    flows['evidence_directory'] = str(evidence)
    return {"crud": crud, "flow": flows,
            "full_m2_acceptance": flows['core_flow_passed'] and flows['full_fault_matrix_passed']}
