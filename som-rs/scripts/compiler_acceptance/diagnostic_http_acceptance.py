#!/usr/bin/env python3
"""Extra diagnostic acceptance. Never starts a service or changes expectations."""
import argparse
import copy
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from paths import find_repo, default_output_root, require_new_output
ROOT = find_repo(__file__)
MANDATED_DIAGNOSTIC_FIELDS = ('code', 'byte_start', 'byte_end', 'line', 'column',
                              'end_line', 'end_column', 'prompt_pointer')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def load(path):
    return json.loads(path.read_text())

def write_new(path, data):
    with path.open('xb') as file:
        file.write(data)

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()

def modules(args):
    os.environ['SOM_REPO'] = str(args.repo.resolve())
    qa = importlib.import_module('qa_harness')
    reference = importlib.import_module('public_reference')
    # The public validator loads this lazily during prep. Import it in both
    # phases so the same source files bind before any candidate call.
    importlib.import_module('som_core.business')
    return qa, reference

def input_hashes(args):
    paths = [Path(__file__).resolve(), args.cases.resolve()]
    paths += sorted(HERE.rglob('*.py'))
    paths += [args.repo / 'som-rs/contracts/compiler-v1/selection-policy.json',
              args.repo / 'som-rs/contracts/compiler-v1/canonical-vectors.json']
    paths += [args.repo / rel for rel in (
        'som-code-python/data/blocks/v5/blocks.json',
        'som-code-python/data/blocks/composed-quality/blocks.json',
        'som-code-python/data/blocks/flow-v7-compact-self/blocks.json',
        'som-code-python/data/block-contracts/composed-quality.json',
        'som-code-python/data/block-contracts/flow-v7-compact.json',
        'som-rs/tests/compiler-v1/typed-json-fixtures.json',
        'som-rs/tests/compiler-v1/typed-json-oracle-cases.json',
        'som-rs/tests/compiler-v1/cnl-negative-cases.json',
        'som-rs/contracts/compiler-v1/examples/v7.cnl')]
    # Bind public constructor sources loaded by the independent oracle.
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and (name.startswith('som_core') or name.startswith('build_')):
            paths.append(Path(path).resolve())
    return {str(p.resolve()): sha(p.read_bytes()) for p in sorted(set(paths))}

def prep(args):
    args.references = require_new_output(args.references, default_output_root(args.repo))
    qa, reference = modules(args)
    cases = load(args.cases)
    vectors = qa.canonical_vector_check(load(args.repo / 'som-rs/contracts/compiler-v1/canonical-vectors.json'))
    if not vectors['passed']:
        raise ValueError('independent canonical vector check failed')
    rows = {name: {'prompt': prompt, 'dsl': reference.reference_dsl(prompt)}
            for name, prompt in cases['fixtures'].items()}
    for row in rows.values():
        ok, code = qa.public_python_validate(args.repo, row['prompt'])
        if not ok:
            raise ValueError(f'baseline prompt refused by public validator: {code}')
    result = {'version': 'som.extra-diagnostic-reference.v1', 'created_at': now(),
              'fixtures': rows, 'canonical_vectors': vectors,
              'freeze_receipts': qa.verify_freeze(args.repo),
              'input_hashes': input_hashes(args), 'candidate_url': None,
              'policy': 'Public constructors only. Prepared before candidate execution. No saved candidate plans or model corpus read.'}
    args.references.parent.mkdir(parents=True, exist_ok=True)
    write_new(args.references, encoded(result))
    print(json.dumps({'references': str(args.references), 'sha256': sha(args.references.read_bytes()),
                      'fixtures': len(rows), 'candidate_tests_run': 0}))

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def json_wire(value):
    """Serialize raw JSON and independently record each value-token byte span."""
    chunks, spans, length = [], {}, 0
    def append(text):
        nonlocal length
        data = text.encode('utf-8')
        chunks.append(data)
        length += len(data)
    def visit(v, pointer):
        start = length
        if isinstance(v, dict):
            append('{')
            for i, (k, item) in enumerate(sorted(v.items())):
                if i:
                    append(',')
                append(json.dumps(k, ensure_ascii=False) + ':')
                visit(item, pointer + '/' + k.replace('~', '~0').replace('/', '~1'))
            append('}')
        elif isinstance(v, list):
            append('[')
            for i, item in enumerate(v):
                if i:
                    append(',')
                visit(item, pointer + '/' + str(i))
            append(']')
        else:
            append(json.dumps(v, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
        spans[pointer] = (start, length)
    visit(value, '')
    return b''.join(chunks), spans

def span_fields(raw, pointer, spans):
    while pointer not in spans and pointer:
        pointer = pointer.rsplit('/', 1)[0]
    start, end = spans[pointer]
    def point(n):
        text = raw[:n].decode('utf-8')
        return text.count('\n') + 1, len(text.rsplit('\n', 1)[-1]) + 1
    line, column = point(start)
    end_line, end_column = point(end)
    return {'byte_start': start, 'byte_end': end, 'line': line, 'column': column,
            'end_line': end_line, 'end_column': end_column}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

def mutate(bundle, name):
    b = copy.deepcopy(bundle)
    if name == 'null_blocks':
        b['dsl']['files'][0]['blocks'] = None
    elif name == 'missing_file':
        b['dsl']['files'].pop(0)
    elif name == 'bad_binding':
        b['dsl']['files'][0]['blocks'][0]['params'] = {'unsafe': 'source'}
    elif name.startswith('actual_ref_'):
        b['dsl']['files'][0]['blocks'][1]['params']['date_field']['$ref'] = 'wrong.binding'
    elif name == 'wrong_undo':
        b['dsl']['flow']['steps'][1]['steps'][0]['undo'] = None
    elif name == 'wrong_flow_structure':
        b['dsl']['flow']['steps'] = None
    else:
        raise ValueError(f'unknown prepared mutation {name}')
    return b

def run(args):
    args.output = require_new_output(args.output, default_output_root(args.repo))
    parsed = urllib.parse.urlparse(args.url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.port or parsed.port == 8765 or parsed.path not in ('', '/'):
        raise ValueError('an explicit candidate loopback HTTP port other than 8765 is required')
    qa, _ = modules(args)
    freeze_receipts = qa.verify_freeze(args.repo)
    cases, reference, index = load(args.cases), load(args.references), load(args.asset_index)
    errors = qa.validate_asset_index_document(index)
    if errors:
        raise ValueError(f'independent asset index malformed: {errors}')
    current_inputs = input_hashes(args)
    if current_inputs != reference['input_hashes']:
        raise ValueError('prepared reference input identities changed; prepare a new reference file')
    if not qa.canonical_vector_check(load(args.repo / 'som-rs/contracts/compiler-v1/canonical-vectors.json'))['passed']:
        raise ValueError('canonical vectors failed')
    args.output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    results = []
    expected_ids = ['health_before', 'health_after']
    expected_ids += ['baseline_' + k for k in cases['fixtures']]
    expected_ids += [c['id'] for group in ('typed', 'bundles', 'cnl') for c in cases[group]]
    if (len(cases['typed']), len(cases['bundles']), len(cases['cnl']), len(cases['fixtures'])) != (33, 25, 34, 7):
        raise ValueError('required case counts changed')
    if len(expected_ids) != 101 or len(set(expected_ids)) != 101:
        raise ValueError('exactly 101 distinct required case IDs are required')
    metadata = {'version': 'som.extra-http-diagnostic-acceptance.v1', 'started_at': now(),
                'url': args.url, 'input_hashes': current_inputs,
                'references_sha256': sha(args.references.read_bytes()),
                'asset_index_sha256': sha(args.asset_index.read_bytes()),
                'package_manifest_sha256': sha(args.package_manifest.read_bytes()),
                'candidate_binary_sha256': sha(args.candidate_binary.read_bytes()),
                'freeze_receipts': freeze_receipts,
                'required': cases['counts'], 'baseline_compiles_required': 7,
                'status': 'running', 'results': results}
    write_new(args.output / 'inputs.json', encoded(metadata))
    def call(ident, endpoint, payload=None):
        raw, spans = json_wire(payload) if payload is not None else (b'', {})
        write_new(args.output / (ident + '.request.raw'), raw)
        req = urllib.request.Request(args.url.rstrip('/') + endpoint,
                                     data=raw if payload is not None else None,
                                     headers={'Content-Type': 'application/json'},
                                     method='POST' if payload is not None else 'GET')
        try:
            with opener.open(req, timeout=60) as response:
                status, data = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, data = error.code, error.read()
        write_new(args.output / (ident + '.response.raw'), data)
        body = json.loads(data)
        write_new(args.output / (ident + '.response.json'), encoded(body))
        row = {'id': ident, 'http_status': status, 'request_sha256': sha(raw),
               'response_sha256': sha(data), 'request_bytes': len(raw), 'passed': False}
        return row, body, raw, spans
    def rejected(c, endpoint, payload):
        row, body, raw, spans = call(c['id'], endpoint, payload)
        problems = []
        if row['http_status'] != c['status'] or body.get('status') != 'error' or body.get('code') != c['code']:
            problems.append('frozen status/code mismatch')
        ds = body.get('diagnostics')
        if not isinstance(ds, list) or len(ds) != 1:
            problems.append('one complete diagnostic required')
        else:
            d = ds[0]
            if 'exact_diagnostic' in c:
                expected = c['exact_diagnostic']
                if any(d.get(key) != expected.get(key) for key in MANDATED_DIAGNOSTIC_FIELDS):
                    problems.append('mandated CNL code/span/pointer mismatch')
            else:
                expected_span = span_fields(raw, c['pointer'], spans)
                row['expected_span'] = expected_span
                if d.get('prompt_pointer') != c['pointer']:
                    problems.append('precise JSON pointer mismatch')
                for key, val in expected_span.items():
                    if d.get(key) != val:
                        problems.append('raw document span mismatch: ' + key)
            if d.get('code') != c['code'] or d.get('cause') != body.get('detail') or not d.get('allowed_form'):
                problems.append('diagnostic envelope fields mismatch')
        row.update(passed=not problems, problems=problems, expected=c)
        results.append(row)
    try:
        health_row, health, _, _ = call('health_before', '/healthz')
        manifest = load(args.package_manifest)
        backend = index['by_prompt_version']['som.prompt.v1']['backend']['sha256']
        health_row['passed'] = (health_row['http_status'] == 200 and health.get('model_loaded') is False
                                and health.get('asset_manifest_sha256') == qa.sha(manifest)
                                and health.get('backend_sha256') == backend)
        results.append(health_row)
        if not health_row['passed']:
            raise ValueError('candidate health does not match final bound package/index')
        bundles = {}
        policy = load(args.repo / 'som-rs/contracts/compiler-v1/selection-policy.json')
        for name, prompt in cases['fixtures'].items():
            row, body, _, _ = call('baseline_' + name, '/v1/compile', {'format': 'som.prompt-json.v1', 'prompt': prompt})
            problems = []
            if row['http_status'] != 200 or body.get('status') != 'ok':
                problems.append('baseline compile failed')
            else:
                b = body.get('bundle')
                problems += qa.verify_bundle(b, prompt, index, policy)
                if not qa.exact_equal(b.get('dsl'), reference['fixtures'][name]['dsl']):
                    problems.append('whole DSL differs from independently prepared public constructor')
                if not problems:
                    bundles[name] = b
            row.update(passed=not problems, problems=problems)
            results.append(row)
        for c in cases['typed']:
            rejected(c, '/v1/compile', {'format': 'som.prompt-json.v1', 'prompt': c['prompt']})
        for c in cases['bundles']:
            if c['fixture'] not in bundles:
                results.append({'id': c['id'], 'passed': False, 'unrun': True, 'reason': 'independent baseline checks failed'})
                continue
            b = mutate(bundles[c['fixture']], c['mutation'])
            b['hashes'] = {'prompt_sha256': qa.sha(b['prompt']), 'dsl_sha256': qa.sha(b['dsl']),
                           'payload_sha256': qa.sha({k: b[k] for k in ('version', 'prompt', 'dsl', 'assets')})}
            rejected(c, '/v1/assemble', {'bundle': b})
        for c in cases['cnl']:
            rejected(c, '/v1/compile', {'format': 'som.cnl.v1', 'source': c['source']})
        last_row, last, _, _ = call('health_after', '/healthz')
        last_row['passed'] = last_row['http_status'] == 200 and last == health
        results.append(last_row)
        done = {r['id'] for r in results if not r.get('unrun')}
        complete = len(results) == 101 and done == set(expected_ids)
        metadata['status'] = 'pass' if complete and all(r['passed'] for r in results) else 'fail'
    except Exception as error:
        metadata['status'] = 'fail'
        metadata['execution_error'] = str(error)
    finally:
        done = {r['id'] for r in results if not r.get('unrun')}
        metadata['unrun_ids'] = [ident for ident in expected_ids if ident not in done]
        metadata['required_ids'] = expected_ids
        metadata['required_count'] = 101
        metadata['complete'] = len(results) == 101 and done == set(expected_ids)
        if not metadata['complete'] or metadata['unrun_ids']:
            metadata['status'] = 'fail'
        metadata['finished_at'] = now()
        metadata['passed'] = sum(r['passed'] for r in results)
        metadata['failed'] = sum(not r['passed'] for r in results)
        write_new(args.output / 'results.json', encoded(metadata))
    print(json.dumps({k: metadata[k] for k in ('status', 'passed', 'failed', 'unrun_ids')}))
    return 0 if metadata['status'] == 'pass' else 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prep', 'run'])
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--cases', type=Path, default=HERE / 'extra-http-cases.json')
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--url')
    parser.add_argument('--asset-index', type=Path)
    parser.add_argument('--package-manifest', type=Path)
    parser.add_argument('--candidate-binary', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'prep':
        prep(args)
        return 0
    if not all((args.url, args.asset_index, args.package_manifest, args.candidate_binary, args.output)):
        parser.error('run needs final URL, asset index, package manifest, binary and new output directory')
    return run(args)

if __name__ == '__main__':
    raise SystemExit(main())
