"""Validate the quality candidate on Metal, final bytes and frozen legacy output."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time

import httpx

from bench_service_budget import listener_pid, rss_bytes
from som_core import composed as c, quality
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs, validate_prompt


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8767')
    parser.add_argument('--model', type=Path, default=c.ROOT / 'som-rs/models/composed-quality-candidate')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True, help='stderr log of this isolated Rust service')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    library = load_blocks(c.ROOT / 'som-code-python/data/blocks/composed-quality')
    legacy_library = load_blocks(c.ROOT / 'som-code-python/data/blocks/composed')
    legacy_file = json.loads((args.model / 'legacy/blocks.json').read_text())
    assert all(legacy_library[block['id']] == block for block in legacy_file)
    groups = {
        'legacy100': 'service-regression-v4/heldout.jsonl',
        'legacy_v1': 'v1/heldout.jsonl', 'legacy_v2': 'query-v2/heldout.jsonl',
        'legacy_v3': 'service-v3/heldout.jsonl', 'legacy_related': 'related-service/heldout.jsonl',
        'legacy_business': 'business-service/requirements.jsonl',
        'services14': 'composed/regression.jsonl', 'compositions120': 'composed/heldout.jsonl',
        'blind80': 'composed/blind.jsonl',
    }
    corpus = {name: [json.loads(line) for line in (c.ROOT / 'som-core/data' / source).read_text().splitlines()]
              for name, source in groups.items()}
    report = {'model_manifest_sha256': digest(args.model / 'manifest.json'),
              'model_bytes': sum(p.stat().st_size for p in args.model.rglob('*') if p.is_file()),
              'sources_sha256': {name: digest(c.ROOT / 'som-core/data' / source) for name, source in groups.items()},
              'cases': [], 'rejections': [], 'performance': [], 'limits_enforced': False}
    stop = threading.Event()
    memory = []
    pid = listener_pid(int(args.url.rsplit(':', 1)[1]))
    report['idle_resident_bytes'] = rss_bytes(pid)

    def sample():
        while not stop.wait(0.02):
            memory.append(rss_bytes(pid))

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    with httpx.Client(base_url=args.url, timeout=65) as client:
        health = client.get('/healthz').json()
        assert health['model_manifest_sha256'] == report['model_manifest_sha256']
        report['health'] = health
        assert health['quality_contract'] == quality.VERSION and health['formatter_version'] == quality.RUFF_VERSION

        def request(prompt, destination):
            trace_start = args.trace.stat().st_size
            started = time.perf_counter()
            response = client.post('/v1/generate', json=prompt)
            body = response.json()
            elapsed = (time.perf_counter() - started) * 1000
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.content)
            with args.trace.open('rb') as source:
                source.seek(trace_start)
                lines = source.read().decode().splitlines()
            traces = [json.loads(line) for line in lines if line.startswith('{')]
            return response.status_code, body, elapsed, traces

        for name, rows in corpus.items():
            passed = 0
            for index, row in enumerate(rows):
                path = args.output / 'responses' / name / f'{index:04d}.json'
                status, body, elapsed, traces = request(row['prompt'], path)
                dsl = row['dsl']
                if row['prompt']['version'] == c.VERSION and status == 200:
                    decoded = [trace for trace in traces if trace.get('stage') == 'decoded_plan']
                    assert len(decoded) == 1, 'missing or ambiguous actual model plan'
                    dsl = c.decode(decoded[0]['text'])
                    c.validate_plan(row['prompt'], dsl, json.loads(quality.CATALOG.read_text()))
                    path.with_suffix('.plan.json').write_text(json.dumps(dsl, indent=2) + '\n')
                expected = (quality.reference(row['prompt'], dsl, library, args.model / 'ruff')
                            if row['prompt']['version'] == c.VERSION else
                            assemble(resolve_prompt_refs(row['prompt'], dsl), legacy_library))
                success = status == 200 and body.get('status') == 'ok' and body.get('files') == expected
                item = {'group': name, 'index': index, 'app': row['prompt']['package'],
                        'passed': success, 'http_status': status, 'milliseconds': elapsed,
                        'response_sha256': digest(path), 'response_bytes': path.stat().st_size,
                        'source_bytes': sum(len(value.encode()) for value in body.get('files', {}).values())}
                item['stages'] = [trace for trace in traces if trace.get('stage') != 'decoded_plan']
                item['reference_plan_matches'] = dsl == row['dsl']
                if not success:
                    item['error'] = {k: v for k, v in body.items() if k != 'files'}
                report['cases'].append(item)
                passed += success
            print(json.dumps({'group': name, 'passed': passed, 'total': len(rows)}), flush=True)
        for source in ('v1', 'service-v3', 'date-service-v4', 'related-service'):
            for index, line in enumerate((c.ROOT / f'som-core/data/{source}/rejections.jsonl').read_text().splitlines()):
                row = json.loads(line)
                try:
                    validate_prompt(row['prompt'])
                except V1Error as error:
                    status, body, _, _ = request(row['prompt'], args.output / 'rejections' / f'{source}-{index}.json')
                    report['rejections'].append({'source': source, 'index': index, 'expected': error.code,
                                                'actual': body.get('code'), 'passed': status >= 400 and body.get('code') == error.code})
        base = corpus['services14'][0]['prompt']
        for mutation in ('contract', 'operation', 'duplicate', 'wrong_type', 'route_collision'):
            prompt = deepcopy(base)
            if mutation == 'contract': prompt['contract']['sha256'] = '0' * 64
            if mutation == 'operation': prompt['operations'][0]['action'] = 'execute_python'
            if mutation == 'duplicate': prompt['operations'][1] = deepcopy(prompt['operations'][0])
            if mutation == 'wrong_type': prompt['resources']['parent']['fields'][0]['required'] = 'true'
            if mutation == 'route_collision': prompt['resources']['child']['path'] = '/export'
            try:
                c.validate(prompt)
            except V1Error as error:
                status, body, _, _ = request(prompt, args.output / 'rejections' / (mutation + '.json'))
                report['rejections'].append({'source': mutation, 'expected': error.code, 'actual': body.get('code'),
                                            'passed': status >= 400 and body.get('code') == error.code})
            else:
                raise AssertionError('negative prompt unexpectedly valid')
        malformed = client.post('/v1/generate', content=b'{', headers={'Content-Type': 'application/json'})
        report['rejections'].append({'source': 'malformed_json', 'passed': malformed.status_code == 400 and malformed.json().get('code') == 'INVALID_JSON'})
        # Observe complete generation, with no performance threshold in this phase.
        selected = [(name, index, row) for name in ('legacy100', 'services14', 'compositions120', 'blind80')
                    for index, row in enumerate(corpus[name])]
        for round_index in range(1):
            for _, _, row in selected[:5]:
                client.post('/v1/generate', json=row['prompt']).json()
            for name, index, row in selected:
                status, body, elapsed, traces = request(row['prompt'], args.output / 'timed' / f'{round_index}-{name}-{index}.json')
                report['performance'].append({'round': round_index, 'group': name, 'index': index,
                    'app': row['prompt']['package'], 'milliseconds': elapsed, 'passed': status == 200,
                    'stages': [trace for trace in traces if trace.get('stage') != 'decoded_plan'],
                    'source_bytes': sum(len(value.encode()) for value in body.get('files', {}).values())})
        assert client.get('/healthz').json() == health
    stop.set()
    sampler.join()
    report['peak_resident_bytes'] = max([report['idle_resident_bytes'], *memory])
    report['memory_samples'] = len(memory)
    report['passed'] = all(r['passed'] for r in report['cases'] + report['rejections'] + report['performance'])
    report['p95_ms_by_round'] = {}
    for name in ('legacy100', 'services14', 'compositions120', 'blind80', 'all'):
        values = []
        for round_index in range(1):
            times = sorted(r['milliseconds'] for r in report['performance'] if r['round'] == round_index and (name == 'all' or r['group'] == name))
            values.append(times[math.ceil(len(times)*0.95)-1])
        report['p95_ms_by_round'][name] = values
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('passed', 'model_bytes', 'idle_resident_bytes', 'peak_resident_bytes', 'p95_ms_by_round')}))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
