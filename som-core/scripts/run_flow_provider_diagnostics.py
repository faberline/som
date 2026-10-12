"""Run provider faults on frozen drafts without claiming M2 acceptance."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import traceback
import uuid

import httpx

from audit_business_transactions import source_hashes
from composed_evidence import capture, failure_details
from fixture_guard import reserve_fixture
from flow_provider_fault_checks import provider_fault_checks
from flow_test_backends import FlowMongo, FlowPostgres, FlowProviders
from verify_python_quality import verify as verify_quality
from verify_related import Server, run


def verify(case, fixture, evidence):
    prompt = json.loads((case / 'prompt.json').read_text())
    root = (case / 'product').resolve()
    before = source_hashes(root)
    backend = (FlowPostgres if prompt['capabilities']['database'] == 'postgresql' else FlowMongo)(
        prompt, 'som_test_flow_' + uuid.uuid4().hex[:16], fixture)
    providers = FlowProviders(fixture, prompt)
    server = None
    backend.create(run)
    try:
        inherited = {key: value for key, value in os.environ.items()
                     if not key.startswith('AWS_') and key != 'GOOGLE_APPLICATION_CREDENTIALS'}
        env = inherited | backend.env | providers.environment()
        env.update(UV_CACHE_DIR='/private/tmp/som-uv-cache', UV_PYTHON_DOWNLOADS='never')
        run(['uv', 'sync', '--python', '3.12'], cwd=root, env=env)
        quality = verify_quality(root, evidence / 'quality')
        if not quality['passed']:
            raise AssertionError('draft quality check failed')
        python = str(root / '.venv/bin/python')
        run(backend.migration_command(python, True), cwd=root, env=env)
        backend.inspect()
        server = Server(root, prompt['package'], env)
        server.start()
        with httpx.Client(base_url=server.url, timeout=45) as client:
            providers.login(client)
            with capture(root) as observations:
                result = provider_fault_checks(
                    prompt, backend, client, server, providers,
                    lambda phase: {'parent': {'title': phase + '-' + uuid.uuid4().hex},
                                   'child': {'code': phase + '-' + uuid.uuid4().hex},
                                   'key': phase + '-' + uuid.uuid4().hex})
        assert source_hashes(root) == before
        return {'quality': quality, 'provider_faults': result,
                'source_sha256': before, 'source_unchanged': True,
                'observation_directory': str(observations),
                'diagnostic_only': True, 'full_m2_acceptance': False}
    finally:
        if server is not None:
            server.stop()
            server.log.close()
        providers.close()
        backend.close(run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--products', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ids')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    fixture = json.loads(args.fixture.read_text())
    products = json.loads((args.products / 'result.json').read_text())['records']
    selected = set(args.ids.split(',')) if args.ids else {row['id'] for row in products}
    assert selected <= {row['id'] for row in products}
    source_paths = [Path(__file__), Path(__file__).with_name('flow_provider_fault_checks.py'),
                    Path(__file__).with_name('flow_test_backends.py'),
                    Path(__file__).with_name('flow_fault_checks.py')]
    source_hashes_before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    records = []
    with reserve_fixture(fixture):
        for row in products:
            if row['id'] not in selected:
                continue
            evidence = args.output / row['id']
            evidence.mkdir()
            case = args.products / row['id']
            assert source_hashes(case / 'product') == row['source_sha256']
            result = {'id': row['id'], 'normal_assembler_accepted': row['normal_assembler_accepted'],
                      'normal_assembler_rejection': row['normal_assembler_rejection'],
                      'diagnostic_only': True, 'formal_case': False,
                      'full_m2_acceptance': False}
            try:
                result['behavior'] = verify(case, fixture, evidence)
                result['diagnostic_passed'] = result['behavior']['provider_faults']['passed']
            except Exception as error:
                result.update(diagnostic_passed=False, failure=failure_details(error), error=traceback.format_exc())
            (evidence / 'result.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
            records.append(result)
            report = {'diagnostic_only': True, 'formal_cases': 0, 'full_m2_acceptance': False,
                      'source_sha256': source_hashes_before, 'records': records}
            (args.output / 'result.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
            print(json.dumps({'id': row['id'], 'diagnostic_passed': result['diagnostic_passed'],
                              'failure': result.get('failure')}), flush=True)
    source_after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    assert source_after == source_hashes_before
    raise SystemExit(not all(row['diagnostic_passed'] for row in records))


if __name__ == '__main__':
    main()
