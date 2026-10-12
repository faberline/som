"""Match failed assertions to recorded requests, before provider cleanup."""
import ast
import json
from pathlib import Path
import subprocess
import traceback

import httpx

UNKNOWN = object()
SECRET = {'authorization', 'cookie', 'access_token', 'id_token', 'refresh_token',
          'private_key', 'client_secret', 'password', 'jwt_secret'}


def safe(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) in (tuple, list, set):
        return [safe(item) for item in value]
    if type(value) is dict:
        return {str(key): '[redacted]' if str(key).lower() in SECRET else safe(item)
                for key, item in value.items()}
    return '<' + type(value).__name__ + '>'


def recorded_http(root, started_at=None):
    folders = sorted(path for path in (root / 'check-results').glob('evidence-*')
                     if started_at is None or path.stat().st_mtime >= started_at)
    if not folders:
        return []
    rows = (folders[-1] / 'events.jsonl').read_text().splitlines()
    return [row for line in rows if (row := json.loads(line)).get('kind') == 'http']


def value(node, frame, events):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return frame.f_locals.get(node.id, frame.f_globals.get(node.id, UNKNOWN))
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [value(item, frame, events) for item in node.elts]
        return values if all(item is not UNKNOWN for item in values) else UNKNOWN
    if isinstance(node, ast.Subscript):
        target, key = value(node.value, frame, events), value(node.slice, frame, events)
        if type(target) in (dict, list, tuple, str) and type(key) in (str, int):
            try:
                return target[key]
            except (KeyError, IndexError, TypeError):
                pass
    if isinstance(node, ast.Attribute):
        target = value(node.value, frame, events)
        if isinstance(target, httpx.Response) and node.attr in {'status_code', 'text'}:
            return getattr(target, node.attr)
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'json' and not node.args:
            target = value(node.func.value, frame, events)
            if isinstance(target, httpx.Response):
                return target.json()
        if isinstance(node.func, ast.Name) and node.func.id == 'len' and len(node.args) == 1:
            target = value(node.args[0], frame, events)
            if type(target) in (dict, list, tuple, str, bytes):
                return len(target)
    # The verifier's request helper returns decoded JSON. Reuse the recorded
    # response only when its method and path match. Never invoke the helper.
    observed = matching_request(node, frame, events)
    if observed is not None:
        return observed.get('json')
    return UNKNOWN


def matching_request(node, frame, events):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'request' and len(node.args) >= 2:
        method, path = (value(arg, frame, []) for arg in node.args[:2])
        return next((row for row in reversed(events)
                     if row['method'] == method and row['path'] == path), None)
    return None


def differences(expected, actual, path='$', limit=6):
    if expected == actual:
        return []
    result = []
    if type(expected) is dict and type(actual) is dict:
        for key in sorted(set(expected) | set(actual)):
            result += differences(expected.get(key, '<missing>'), actual.get(key, '<missing>'), path + '.' + key, limit)
            if len(result) >= limit:
                break
    elif type(expected) is list and type(actual) is list:
        if len(expected) != len(actual):
            result.append({'path': path + '.length', 'expected': len(expected), 'actual': len(actual)})
        for index, (left, right) in enumerate(zip(expected, actual)):
            result += differences(left, right, f'{path}[{index}]', limit)
            if len(result) >= limit:
                break
    else:
        result.append({'path': path, 'expected': expected, 'actual': actual})
    return result[:limit]


def failure_details(error, root, started_at=None):
    frames = traceback.extract_tb(error.__traceback__)
    last = frames[-1]
    trace = error.__traceback__
    while trace.tb_next:
        trace = trace.tb_next
    events = recorded_http(root, started_at)
    observed = next((row for row in reversed(events) if not row['path'].startswith('/__')), None)
    expected, actual = last.line, safe(error.args[0]) if error.args else UNKNOWN
    comparison = None
    try:
        statement = ast.parse(last.line).body[0]
        expression = statement.test if isinstance(statement, ast.Assert) else None
        if expression is not None:
            matched = [row for node in ast.walk(expression)
                       if (row := matching_request(node, trace.tb_frame, events)) is not None]
            if matched:
                observed = matched[0]
        if isinstance(expression, ast.Compare) and len(expression.comparators) == 1:
            left = value(expression.left, trace.tb_frame, events)
            right = value(expression.comparators[0], trace.tb_frame, events)
            if left is not UNKNOWN and right is not UNKNOWN:
                expected, actual = safe(right), safe(left)
                comparison = type(expression.ops[0]).__name__
    except (SyntaxError, ValueError, TypeError):
        pass
    process = trace.tb_frame.f_locals.get('result')
    if isinstance(process, subprocess.CompletedProcess) and process.returncode != 0:
        expected = {'exit_code': 0}
        actual = {'exit_code': process.returncode,
                  'stdout': process.stdout, 'stderr': process.stderr}
        comparison = 'process_exit'
    elif not isinstance(error, AssertionError):
        expected = {'exception': None}
        actual = {'exception': type(error).__name__, 'message': str(error)}
    if actual is UNKNOWN and observed is not None:
        actual = {'http_status': observed['status'], 'json': safe(observed.get('json')),
                  'response_sha256': observed['response_sha256']}
    if actual is UNKNOWN:
        actual = {'error_type': type(error).__name__, 'message': str(error)}
    result = {'case': last.name, 'assertion': last.line, 'comparison': comparison,
              'expected': expected, 'actual': actual,
              'location': f'{last.filename}:{last.lineno}'}
    if comparison == 'Eq':
        result['differences'] = differences(expected, actual)
    if observed:
        result['request'] = {key: observed[key] for key in ('method', 'path', 'status')}
    return result


def compact(failure):
    if failure is None:
        return None
    result = {key: failure[key] for key in ('case', 'location', 'assertion', 'comparison')}
    for key in ('expected', 'actual'):
        encoded = json.dumps(failure[key], ensure_ascii=False)
        result[key] = failure[key] if len(encoded) <= 1200 else 'See differences and saved diagnostic.'
    if failure.get('differences'):
        result['differences'] = []
        for entry in failure['differences']:
            item = {'path': entry['path']}
            for key in ('expected', 'actual'):
                encoded = json.dumps(entry[key], ensure_ascii=False)
                item[key] = entry[key] if len(encoded) <= 600 else {'preview': encoded[:550], 'characters': len(encoded)}
            result['differences'].append(item)
    if failure.get('request'):
        result['request'] = failure['request']
    return result
