"""Independent public-prompt observations for eleven admitted renderer cases.

This module never reads blocks, DSL, model data, or expected generated source.
It runs only after the controller authorizes the execution runner.
"""
from __future__ import annotations
import base64
import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math
import os
import operator
from pathlib import Path
import uuid
import struct
import sys

CODE_DIR = Path(__file__).resolve().parent
ACCEPTANCE_DIR = CODE_DIR.parent
if str(ACCEPTANCE_DIR) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_DIR))
from paths import default_output_root, find_repo
REPO = find_repo(__file__)

def lazy_export_checks(identity, prompt, product):
    """Supplement live product checks with a closed final-expression probe.

    Only the selected export assignment executes. The service source stays
    unchanged. The unselected input reader raises if evaluation reaches it.
    This result never substitutes for live product or full_m2 acceptance.
    """
    from types import SimpleNamespace
    source_path = product / prompt['package'] / 'flows.py'
    source = source_path.read_text()
    tree = ast.parse(source)
    branch = next(node for node in prompt['flow']['steps'] if node['op'] == 'if')
    assert len(branch['exports']) == 1
    export = next(iter(branch['exports']))
    bindings = [node.value.args[0].id for node in ast.walk(tree)
                if isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].slice, ast.Constant) and node.targets[0].slice.value == export
                and isinstance(node.targets[0].value, ast.Attribute) and node.targets[0].value.attr == 'values'
                and isinstance(node.value, ast.Call) and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)]
    assert len(bindings) == 1, bindings
    target = bindings[0]
    selections = [node for node in ast.walk(tree) if isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name) and node.targets[0].id == target
                  and isinstance(node.value, ast.IfExp)]
    assert len(selections) == 1
    selected = selections[0]
    allowed = (ast.Assign, ast.IfExp, ast.Attribute, ast.Name, ast.Load, ast.Store,
               ast.Compare, ast.Is, ast.Constant)
    assert all(isinstance(node, allowed) for node in ast.walk(selected)), 'probe refuses a non-closed expression'
    program = compile(ast.fix_missing_locations(ast.Module(body=[deepcopy(selected)], type_ignores=[])),
                      'closed_final_export_expression', 'exec')
    checks = []
    if identity == 'REF_BRANCH_EXPORT_SAME_TYPE':
        assert isinstance(selected.value.test, ast.Name)
        input_names = [item['name'] for item in prompt['flow']['inputs']]
        class Inputs:
            def __init__(self, permitted, values, accesses):
                self.permitted, self.values, self.accesses = permitted, values, accesses
            def __getattr__(self, name):
                assert name == self.permitted, 'unselected arm was evaluated: ' + name
                self.accesses.append(name)
                return self.values[name]
        for left, right in ((11, 11), (11, 27)):
            inputs = dict(zip(input_names, (left, right), strict=True))
            condition = branch['condition']
            decision = predicate(condition['op'], inputs[condition['left']['input']], inputs[condition['right']['input']])
            arm = 'then' if decision else 'else'
            reference = branch['exports'][export][arm]
            permitted = 'input_' + str(input_names.index(reference['input']))
            accesses = []
            namespace = {'__builtins__': {}, 'ctx': SimpleNamespace(inputs=Inputs(
                permitted, {'input_0': left, 'input_1': right}, accesses)), selected.value.test.id: decision}
            exec(program, namespace)
            assert type(namespace[target]) is int and namespace[target] == inputs[reference['input']]
            assert accesses == [permitted]
            checks.append({'decision': decision, 'result': namespace[target], 'reads': accesses,
                           'unselected_arm_reader': 'raises if reached'})
    else:
        assert identity == 'PUBLIC_v7-branch-null'
        comparison = selected.value.test
        assert isinstance(comparison, ast.Compare) and isinstance(comparison.left, ast.Attribute)
        assert isinstance(comparison.left.value, ast.Name) and comparison.left.attr == 'description'
        variable = comparison.left.value.id
        class Row:
            def __init__(self, value, accesses):
                self.value, self.accesses = value, accesses
            @property
            def description(self):
                self.accesses.append('description')
                return self.value
        for value in (None, '', 'ready', '中文'):
            accesses = []
            namespace = {'__builtins__': {}, variable: Row(value, accesses)}
            exec(program, namespace)
            expected = branch['exports'][export]['then']['literal'] if value is None else value
            assert type(namespace[target]) is str and namespace[target] == expected
            assert accesses == ['description'] * (1 if value is None else 2)
            checks.append({'description': value, 'result': namespace[target], 'reads': accesses,
                           'null_else_expression_was_skipped': value is None})
    return {'passed': True, 'scope': 'supplemental closed expression only; live product evidence is separate',
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'assignment': ast.unparse(selected), 'checks': checks}

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')

def strict_equal(actual, expected, member=None):
    from qa_harness import exact_equal
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            strict_equal(actual[name], expected[name], name) for name in expected)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            strict_equal(left, right, member) for left, right in zip(actual, expected, strict=True))
    if member in {'created_at', 'updated_at'} and isinstance(actual, str):
        return datetime.fromisoformat(actual).astimezone(UTC) == datetime.fromisoformat(expected).astimezone(UTC)
    return exact_equal(actual, expected)

def persisted_equal(actual, expected, zero_observations, path='', member=None):
    """Compare durable runtime data; compiler artifact equality stays exact.

    JSONB stores numerical zero without an IEEE sign encoding. Record this
    representation limit. Never coerce a type or excuse another value change.
    """
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        valid = actual.keys() == expected.keys()
        for name in sorted(actual.keys() & expected.keys()):
            pointer = path + '/' + name.replace('~', '~0').replace('/', '~1')
            if not persisted_equal(actual[name], expected[name], zero_observations, pointer, name):
                valid = False
        return valid
    if isinstance(actual, list):
        valid = len(actual) == len(expected)
        for index, (left, right) in enumerate(zip(actual, expected)):
            if not persisted_equal(left, right, zero_observations, path + '/' + str(index), member):
                valid = False
        return valid
    if type(actual) is float:
        if not math.isfinite(actual) or not math.isfinite(expected):
            return False
        if actual == expected == 0.0:
            actual_bits = struct.pack('!d', actual).hex()
            expected_bits = struct.pack('!d', expected).hex()
            if actual_bits != expected_bits:
                zero_observations.append({'path': path, 'actual': actual, 'expected': expected,
                    'actual_bits': actual_bits, 'expected_bits': expected_bits,
                    'scope': 'persisted JSONB typed numerical equality',
                    'bit_preservation': False, 'representation_limit': 'JSONB zero sign'})
            return True
    return strict_equal(actual, expected, member)

def literal_representation_checks(prompt, product):
    """Verify direct float result literals in generated Python, before storage."""
    source = (product / prompt['package'] / 'flows.py').read_text()
    tree = ast.parse(source)
    forwards = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == 'forward']
    outputs = [node.args[0] for forward in forwards for node in ast.walk(forward)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == 'FlowResult'
               and node.func.attr == 'model_validate' and len(node.args) == 1
               and isinstance(node.args[0], ast.Dict)]
    checks = []
    for name, reference in prompt['flow']['result'].items():
        value = reference.get('literal')
        if reference.get('type') != 'float' or type(value) is not float:
            continue
        assert len(forwards) == len(outputs) == 1
        hits = [expr for node in outputs
                for key, expr in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == name]
        matching = []
        for expr in hits:
            try:
                literal = ast.literal_eval(expr)
            except (ValueError, TypeError):
                continue
            if type(literal) is float and struct.pack('!d', literal) == struct.pack('!d', value):
                matching.append(expr)
        assert len(hits) == len(matching) == 1, (name, [ast.unparse(expr) for expr in hits])
        checks.append({'result': name, 'literal': value, 'bits': struct.pack('!d', value).hex(),
                       'source_expression': ast.unparse(matching[0]), 'bit_preservation': True})
    return {'passed': True, 'scope': 'generated Python direct literal before durable storage',
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'checks': checks}

def predicate(operation, left, right=None):
    if operation == 'is_null':
        return left is None
    operations = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
                  'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}
    if operation not in operations:
        raise ValueError('unsupported bounded predicate: ' + operation)
    result = operations[operation](left, right)
    assert type(result) is bool
    return result

def original_prompt_oracle(prompt, inputs, backend, client, initial, history):
    """Resolve original expressions and observed writes, with identity path parts.

    Each supported operation is explicit. Unknown operations fail this oracle.
    Managed IDs and timestamps come only from native committed row observations.
    """
    import verify_flow_service as existing
    rows = {role: [existing.wire(row, prompt['resources'][role]) for row in initial[role]]
            for role in ('parent', 'child')}
    events = iter(history)
    branches, groups = {}, {}

    def reference(ref, visible):
        if 'input' in ref:
            return deepcopy(inputs[ref['input']])
        if 'literal' in ref:
            return deepcopy(ref['literal'])
        value = visible[ref['step']]
        for part in filter(None, ref['field'].split('.')):
            value = value[part]
        return deepcopy(value)

    def write_resource(role, operation, args, group):
        if operation == 'patch' and not args['data']:
            matches = [row for row in rows[role] if row['id'] == args[role + '_id']]
            assert len(matches) == 1
            return deepcopy(matches[0])
        event = next(events, None)
        expected_operation = 'insert' if operation == 'create' else 'update'
        assert event and (event['role'], event['operation']) == (role, expected_operation), event
        assert group is not None and event['transaction'] is not None
        groups.setdefault(group, set()).add(event['transaction'])
        observed = existing.wire(event['row'], prompt['resources'][role])
        if operation == 'create':
            assert all(row['id'] != observed['id'] for row in rows[role])
            expected = {field['name']: deepcopy(args['data'].get(field['name'], field.get('default')))
                        for field in prompt['resources'][role]['fields']}
            if role == 'child':
                expected[prompt['relation']['foreign_key']] = args['parent_id']
            expected.update({key: observed[key] for key in ('id', 'created_at', 'updated_at')})
        else:
            matches = [row for row in rows[role] if row['id'] == args[role + '_id']]
            assert len(matches) == 1
            expected = deepcopy(matches[0])
            expected.update(args['data'])
            assert observed['id'] == args[role + '_id']
            assert datetime.fromisoformat(observed['updated_at']) >= datetime.fromisoformat(expected['updated_at'])
            expected['updated_at'] = observed['updated_at']
            rows[role] = [row for row in rows[role] if row['id'] != observed['id']]
        for field in prompt['resources'][role]['fields']:
            if field['type'] == 'float' and type(expected[field['name']]) is int:
                expected[field['name']] = float(expected[field['name']])
        assert existing.equivalent(observed, expected), (observed, expected)
        rows[role].append(deepcopy(observed))
        rows[role].sort(key=lambda row: row['id'])
        return observed

    def walk(nodes, visible, group=None):
        visible = dict(visible)
        for node in nodes:
            operation = node['op']
            if operation == 'transaction':
                visible = walk(node['steps'], visible, node['name'])
            elif operation == 'if':
                condition = node['condition']
                left = reference(condition['left'], visible)
                right = reference(condition['right'], visible) if 'right' in condition else None
                decision = predicate(condition['op'], left, right)
                assert type(decision) is bool
                branches[node['name']] = decision
                arm = 'then' if decision else 'else'
                local = walk(node[arm], visible, group)
                for name, export in node['exports'].items():
                    visible[name] = reference(export[arm], local)
            else:
                args = {name: reference(ref, visible) for name, ref in node['args'].items()}
                if operation == 'authorize':
                    response = client.get('/auth/me')
                    assert response.status_code == 200
                    value = {key: response.json()[key] for key in ('sub', 'role')}
                elif operation in {'parent.create', 'child.create', 'parent.patch', 'child.patch'}:
                    role, method = operation.split('.')
                    value = write_resource(role, method, args, group)
                elif operation == 'workflow.run':
                    children = [row for row in rows['child']
                                if row[prompt['relation']['foreign_key']] == args['parent_id']]
                    value = existing.workflow_receipt(prompt, client, children, args['parent_id'])
                else:
                    raise ValueError('unsupported bounded operation: ' + operation)
                visible[node['name']] = value
        return visible

    values = walk(prompt['flow']['steps'], {})
    assert next(events, None) is None, 'extra committed resource write'
    assert all(len(ids) == 1 for ids in groups.values()), groups
    assert len({identifier for ids in groups.values() for identifier in ids}) == len(groups), groups
    for role in ('parent', 'child'):
        actual = [existing.wire(row, prompt['resources'][role]) for row in backend.rows(role)]
        assert existing.equivalent(actual, rows[role]), (role, actual, rows[role])
    result = {name: reference(ref, values) for name, ref in prompt['flow']['result'].items()}
    return result, branches, values

def input_probes(identity, prompt, existing_id):
    from verify_flow_service import flow_inputs
    if identity == 'REF_BRANCH_EXPORT_SAME_TYPE' or identity.startswith('REF_COMPARE_'):
        pairs = [(11, 11), (11, 27), (27, 11), (0, 0), (0, -1), (-9, -9), (-9, 4), (4, -9)]
        return [{'left_value': left, 'right_value': right} for left, right in pairs]
    samples = []
    nullable = identity in {'PUBLIC_v7-branch-null', 'REF_NULLABLE_OPTIONAL_FIELD'}
    values = (None, '', 'ready', '中文') if nullable else (None,)
    for index, description in enumerate(values):
        inputs = flow_inputs(prompt, 930000 + index * 100, True, 1, existing_id)
        if nullable:
            child_input = next(item['name'] for item in prompt['flow']['inputs'] if item['type'] == 'child_input')
            inputs[child_input]['description'] = description
        samples.append(inputs)
    return samples

def no_write_recovery(prompt, backend, client, server, inputs, root):
    """Fail and interrupt a branch checkpoint without assuming a resource read."""
    from flow_fault_checks import PostgresGate, database_rows, run_id, wait_for
    import httpx
    branch_node = next(node for node in prompt['flow']['steps'] if node['op'] == 'if')
    branch = branch_node['name']
    condition = branch_node['condition']
    expected_decision = predicate(condition['op'], inputs[condition['left']['input']],
                                  inputs[condition['right']['input']])
    route = '/flows/' + prompt['flow']['name'] + '/runs'
    records = []
    for phase in ('branch_checkpoint_failure', 'branch_checkpoint_crash'):
        key = 'renderer-' + uuid.uuid4().hex
        before = database_rows(backend)
        gate = PostgresGate(backend, branch, 'branches')
        write(root / (phase + '-request.json'), {'phase': phase, 'key': key,
              'inputs': inputs, 'branch': branch, 'attempted_decision': expected_decision,
              'gate_identifier': gate.identifier, 'rows_before': before})
        gate.install()
        closed = False
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.post, route, json=inputs, headers={'Idempotency-Key': key})
                try:
                    wait_for(gate.waiting, 'owned branch checkpoint pause')
                    durable = deepcopy(backend.journal(key))
                    paused_rows = database_rows(backend)
                    write(root / (phase + '-paused.json'), {'durable_journal': durable,
                          'rows': paused_rows, 'gate_identifier': gate.identifier})
                    assert durable['state'] == 'running' and durable['effects'] == []
                    assert strict_equal(durable['inputs'], inputs)
                    assert branch not in durable['branches'], durable
                    assert paused_rows == before
                    if phase == 'branch_checkpoint_crash':
                        process = server.process
                        assert process is not None and process.poll() is None
                        assert process.args[0] == str(server.root / '.venv/bin/python')
                        assert process.args[3] == prompt['package'] + '.app:app'
                        process.kill()
                        process.wait(timeout=10)
                        gate.close()
                        closed = True
                finally:
                    if not closed:
                        gate.release()
                if phase == 'branch_checkpoint_failure':
                    response = future.result(timeout=45)
                    write(root / (phase + '-failure-response.json'), {
                          'status': response.status_code, 'raw_response': response.text,
                          'journal': backend.journal(key), 'rows': database_rows(backend)})
                    assert response.status_code == 503, response.text
                    identifier = run_id(response)
                else:
                    try:
                        response = future.result(timeout=15)
                    except httpx.HTTPError:
                        pass
                    else:
                        raise AssertionError('killed owned app returned: ' + response.text)
                    after_crash = backend.journal(key)
                    write(root / (phase + '-after-crash.json'), {
                          'journal': after_crash, 'rows': database_rows(backend)})
                    assert after_crash == durable
                    identifier = durable['run_id']
                    backend.expire(identifier)
                    server.start()
            recovered = client.post('/flow-runs/' + identifier + '/recover')
            journal = backend.journal(key)
            after_rows = database_rows(backend)
            write(root / (phase + '-first-recovery.json'), {
                  'status': recovered.status_code, 'raw_response': recovered.text,
                  'journal': journal, 'rows': after_rows})
            assert recovered.status_code == 503, recovered.text
            assert journal['state'] == 'compensated' and journal['effects'] == [], journal
            assert strict_equal(journal['inputs'], inputs)
            assert strict_equal(journal['values'], durable['values'])
            # The BEFORE UPDATE gate rejects the attempted branch checkpoint.
            # Recovery preserves committed state and must not replay forward.
            assert strict_equal(journal['branches'], durable['branches'])
            assert branch not in journal['branches'], journal
            assert strict_equal(journal.get('result'), durable.get('result'))
            assert after_rows == before
            repeated = client.post('/flow-runs/' + identifier + '/recover')
            repeated_journal = backend.journal(key)
            repeated_rows = database_rows(backend)
            write(root / (phase + '-repeated-recovery.json'), {
                  'status': repeated.status_code, 'raw_response': repeated.text,
                  'journal': repeated_journal, 'rows': repeated_rows})
            assert repeated.status_code == 503 and repeated.json() == recovered.json()
            assert repeated_journal == journal
            assert repeated_rows == before
            record = {'phase': phase, 'passed': True, 'key': key, 'inputs': inputs,
                      'durable_before_failure': durable, 'journal_after_recovery': journal,
                      'recovery_status': recovered.status_code, 'recovery_body': recovered.json(),
                      'rows_before': before, 'rows_after': after_rows,
                      'attempted_decision': expected_decision,
                      'checkpoint_committed': False, 'forward_replayed': False}
            records.append(record)
            write(root / (phase + '.json'), record)
        finally:
            if not closed:
                gate.close()
    return {'passed': True, 'business_compensation_applicable': False,
            'reason': 'original flow has no business writes; durable no-write recovery is checked',
            'records': records}

def focused_behavior(identity, prompt, product, fixture, evidence):
    """Run a complete installed product through original-prompt observations."""
    import httpx
    import verify_related as related
    import verify_flow_service as existing
    from audit_business_transactions import source_hashes
    from composed_evidence import capture
    from fixture_guard import reserve_fixture
    from flow_fault_checks import checkpoint_failures, lease_checks, interrupted_forward
    from flow_provider_fault_checks import provider_fault_checks
    from flow_row_history import observe_rows
    from flow_test_backends import FlowPostgres, FlowProviders
    evidence.mkdir(parents=True, exist_ok=False)
    before_sources = source_hashes(product)
    phases = {}
    records = []
    backend = FlowPostgres(prompt, 'som_test_flow_' + uuid.uuid4().hex[:16], fixture)
    providers = FlowProviders(fixture, prompt)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('AWS_') and key != 'GOOGLE_APPLICATION_CREDENTIALS'}
    env.update(backend.env)
    env.update(UV_CACHE_DIR=os.environ.get('SOM_EVAL_UV_CACHE_DIR',
                    str(default_output_root(REPO) / 'cache/uv')),
               UV_LINK_MODE='copy', UV_PYTHON_DOWNLOADS='never')
    server = observer = None
    backend_created = False

    def phase(name, operation):
        import traceback
        try:
            value = operation()
            assert value.get('passed') is True, value
            phases[name] = value
        except Exception as exc:
            phases[name] = {'passed': False, 'error': str(exc), 'traceback': traceback.format_exc()}
        write(evidence / (name + '.json'), phases[name])

    def request(client, method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        with (evidence / 'explicit-http.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'method': method, 'path': path, 'json_input': kwargs.get('json'),
                'idempotency_key': kwargs.get('headers', {}).get('Idempotency-Key'),
                'status': response.status_code, 'raw_response_b64': base64.b64encode(response.content).decode()},
                ensure_ascii=False) + '\n')
        return response

    with reserve_fixture(fixture), capture(product) as trace:
        try:
            phase('generated_literal_representation', lambda: literal_representation_checks(prompt, product))
            if identity in {'REF_BRANCH_EXPORT_SAME_TYPE', 'PUBLIC_v7-branch-null'}:
                phase('lazy_export_expression', lambda: lazy_export_checks(identity, prompt, product))
            backend.create(related.run)
            backend_created = True
            env.update(providers.environment())
            related.run(['uv', 'sync', '--python', '3.12'], cwd=product, env=env)
            python = str(product / '.venv/bin/python')
            server = related.Server(product, prompt['package'], env)
            env['GOOGLE_REDIRECT_URI'] = server.url + '/auth/google/callback'
            related.run(backend.migration_command(python, True), cwd=product, env=env)
            backend.inspect()
            phase('generated_tests', lambda: existing.generated_tests(python, product, env))
            server.start()
            with httpx.Client(base_url=server.url, timeout=45) as client:
                providers.login(client)
                seed = request(client, 'POST', prompt['resources']['parent']['path'],
                               json=related.payload(prompt['resources']['parent'], 920000))
                assert seed.status_code == 201, seed.text
                observer = observe_rows(backend)
                route = '/flows/' + prompt['flow']['name'] + '/runs'
                samples = input_probes(identity, prompt, seed.json()['id'])

                def value_checks():
                    for index, inputs in enumerate(samples):
                        key = 'observe-' + uuid.uuid4().hex
                        response, initial, history = observer.capture(lambda: request(
                            client, 'POST', route, json=inputs, headers={'Idempotency-Key': key}))
                        assert response.status_code == 200, response.text
                        observed = response.json()
                        assert observed['state'] == 'succeeded'
                        expected, branches, values = original_prompt_oracle(prompt, inputs, backend, client, initial, history)
                        journal = backend.journal(key)
                        zero_observations = []
                        comparisons = {
                            'http_result': persisted_equal(observed['result'], expected, zero_observations, '/http/result'),
                            'journal_result': persisted_equal(journal['result'], expected, zero_observations, '/journal/result'),
                            'journal_branches': persisted_equal(journal['branches'], branches, zero_observations, '/journal/branches'),
                            'journal_inputs': persisted_equal(journal['inputs'], inputs, zero_observations, '/journal/inputs'),
                            'journal_values': persisted_equal(journal['values'], values, zero_observations, '/journal/values'),
                        }
                        write(evidence / f'value-{index}-comparison.json', {
                            'observed': observed, 'journal': journal, 'expected': expected,
                            'expected_branches': branches, 'expected_values': values,
                            'comparisons': comparisons, 'jsonb_zero_representation_limits': zero_observations,
                            'inputs': inputs, 'key': key, 'initial_rows': initial, 'committed_history': history})
                        assert all(comparisons.values()), comparisons
                        if identity == 'REF_BRANCH_EXPORT_SAME_TYPE':
                            assert type(observed['result']['chosen']) is int
                        if identity == 'PUBLIC_v7-branch-null':
                            assert type(observed['result']['label']) is str
                        if identity == 'PUBLIC_v7-empty-field-segments':
                            assert type(observed['result']['parent']) is int
                        snapshot = deepcopy({role: backend.rows(role) for role in ('parent', 'child')})
                        repeat = request(client, 'POST', route, json=inputs, headers={'Idempotency-Key': key})
                        repeat_equal = persisted_equal(repeat.json(), observed, zero_observations, '/repeat')
                        write(evidence / f'value-{index}-repeat.json', {'status': repeat.status_code,
                              'body': repeat.json(), 'equal': repeat_equal,
                              'jsonb_zero_representation_limits': zero_observations})
                        assert repeat.status_code == 200 and repeat_equal
                        changed = deepcopy(inputs)
                        first = prompt['flow']['inputs'][0]
                        if first['type'] == 'integer':
                            changed[first['name']] += 1
                        else:
                            resource = prompt['resources'][first['type'].split('_')[0]]
                            changed[first['name']].update(related.payload(resource, 990000 + index))
                        conflict = request(client, 'POST', route, json=changed, headers={'Idempotency-Key': key})
                        assert conflict.status_code == 409, conflict.text
                        status = request(client, 'GET', '/flow-runs/' + observed['run_id'])
                        recovery = request(client, 'POST', '/flow-runs/' + observed['run_id'] + '/recover')
                        status_equal = persisted_equal(status.json(), observed, zero_observations, '/status')
                        recovery_equal = persisted_equal(recovery.json(), observed, zero_observations, '/recover')
                        write(evidence / f'value-{index}-status-recover.json', {
                              'status_http': status.status_code, 'status_body': status.json(),
                              'recover_http': recovery.status_code, 'recover_body': recovery.json(),
                              'status_equal': status_equal, 'recovery_equal': recovery_equal,
                              'jsonb_zero_representation_limits': zero_observations})
                        assert status.status_code == recovery.status_code == 200
                        assert status_equal and recovery_equal
                        assert existing.equivalent({role: backend.rows(role) for role in ('parent', 'child')}, snapshot)
                        record = {'index': index, 'inputs': inputs, 'key': key, 'expected': expected,
                                  'expected_branches': branches, 'observed': observed, 'journal': journal,
                                  'initial_rows': initial, 'committed_history': history,
                                  'jsonb_zero_representation_limits': zero_observations,
                                  'runtime_comparison_scope': 'typed numerical equality after persistence'}
                        records.append(record)
                        write(evidence / f'value-{index}.json', record)
                    arms = {name: sorted({record['expected_branches'][name] for record in records})
                            for name in records[0]['expected_branches']}
                    if identity == 'REF_BRANCH_EXPORT_SAME_TYPE' or identity.startswith('REF_COMPARE_') or identity in {'PUBLIC_v7-branch-null', 'REF_NULLABLE_OPTIONAL_FIELD'}:
                        branch_name = next(node['name'] for node in prompt['flow']['steps'] if node['op'] == 'if')
                        assert arms == {branch_name: [False, True]}, arms
                    if identity == 'PUBLIC_v7-empty-branch':
                        assert arms == {'nothing': [True]}, arms
                    return {'passed': True, 'records': records, 'observed_arms': arms,
                            'constant_false_arm_reachable': False if identity == 'PUBLIC_v7-empty-branch' else None}

                phase('typed_values_journal_branches_idempotency', value_checks)
                if identity == 'REF_BRANCH_EXPORT_SAME_TYPE' or identity.startswith('REF_COMPARE_'):
                    def invalid_inputs():
                        for name in ('left_value', 'right_value'):
                            invalid = dict(samples[0], **{name: True})
                            snapshot = backend.snapshot()
                            response = request(client, 'POST', route, json=invalid,
                                               headers={'Idempotency-Key': 'invalid-' + uuid.uuid4().hex})
                            assert response.status_code == 422 and backend.snapshot() == snapshot
                        return {'passed': True, 'bool_is_not_integer': True}
                    phase('strict_integer_input_type', invalid_inputs)
                    def no_write_checks():
                        outcomes = []
                        condition = next(node['condition'] for node in prompt['flow']['steps'] if node['op'] == 'if')
                        by_decision = {}
                        for inputs in samples:
                            decision = predicate(condition['op'], inputs[condition['left']['input']], inputs[condition['right']['input']])
                            by_decision.setdefault(decision, inputs)
                        assert set(by_decision) == {False, True}
                        for index, decision in enumerate((False, True)):
                            inputs = by_decision[decision]
                            outcomes.append(no_write_recovery(prompt, backend, client, server, inputs,
                                                             evidence / f'no-write-{index}'))
                        return {'passed': all(row['passed'] is True for row in outcomes),
                                'both_branch_decisions': True, 'outcomes': outcomes}
                    phase('no_write_failure_recovery', no_write_checks)
                else:
                    serials = iter(range(940000, 990000, 100))
                    def fault_input(_phase=None):
                        return existing.flow_inputs(prompt, next(serials), True, 1, seed.json()['id'])
                    phase('checkpoint_compensation', lambda: checkpoint_failures(prompt, backend, client, fault_input))
                    phase('lease_faults', lambda: lease_checks(prompt, backend, client, fault_input))
                    phase('process_interruption_and_recovery', lambda: interrupted_forward(prompt, backend, client, server, fault_input))
                    phase('provider_faults', lambda: provider_fault_checks(prompt, backend, client, server, providers, fault_input))
                if records:
                    phase('owner_routes', lambda: existing.require_owner_routes(prompt, client, providers, records[-1]['observed']))
            backend.no_orphans()
            assert source_hashes(product) == before_sources
        finally:
            if observer:
                observer.close()
            if server:
                server.stop()
                server.log.close()
            providers.close()
            if backend_created:
                backend.close(related.run)
    outcome = {'checker': 'scratch independent original-prompt observations plus existing fault checks',
               'passed': bool(phases) and all(row.get('passed') is True for row in phases.values()),
               'full_m2_acceptance': 'NOT CLAIMED; unchanged verifier outcome is separate',
               'phases': phases, 'evidence_directory': str(evidence), 'process_http_trace': str(trace),
               'source_sha256_before': before_sources, 'source_sha256_after': source_hashes(product)}
    write(evidence / 'result.json', outcome)
    return outcome
