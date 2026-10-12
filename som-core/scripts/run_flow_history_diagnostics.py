"""Check refused services with native history and faults, without admission.

Oversized products stay refused by the normal assembler. This runner uses
frozen diagnostic drafts and cannot count them as accepted M2 products.
"""

import argparse
import hashlib
import json
from pathlib import Path
import traceback

from som_core import flow_compact, quality
from som_core.v1 import V1Error, load_blocks
from audit_business_transactions import source_hashes
from composed_evidence import failure_details
from fixture_guard import reserve_fixture
from verify_flow_service import verify_flows
from verify_python_quality import verify as verify_quality

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--drafts', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--ids', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((args.drafts / 'result.json').read_text())
    assert receipt['blocks_sha256'] == quality.sha256(ROOT / 'som-code-python/data/blocks/flow-v7-compact/blocks.json')
    assert receipt['catalog_sha256'] == quality.sha256(flow_compact.CATALOG)
    identities = args.ids.split(',')
    assert len(set(identities)) == len(identities)
    assert set(identities) <= {row['id'] for row in receipt['records']}
    fixture = json.loads(args.fixture.read_text())
    sources = [Path(__file__), Path(__file__).with_name('verify_flow_service.py'),
               Path(__file__).with_name('flow_row_history.py'),
               Path(__file__).with_name('flow_branch_cases.py'),
               Path(__file__).with_name('flow_fault_checks.py'),
               Path(__file__).with_name('flow_provider_fault_checks.py'),
               Path(__file__).with_name('flow_test_backends.py'),
               Path(__file__).with_name('local_flow_services.py'),
               Path(__file__).with_name('flow_gcs_fixture.py')]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    records = []
    with reserve_fixture(fixture):
        for identity in identities:
            case = args.drafts / identity
            evidence = args.output / identity
            evidence.mkdir()
            prompt = json.loads((case / 'prompt.json').read_text())
            product = (case / 'product').resolve()
            original = source_hashes(product)
            result = {'id': identity, 'diagnostic_only': True,
                      'normal_assembler_accepted': False, 'full_m2_acceptance': False,
                      'source_sha256': original}
            try:
                plan = json.loads((case / 'dsl.json').read_text())
                blocks = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
                try:
                    flow_compact.assemble(prompt, plan, blocks, ROOT / 'som-rs/models/composed-quality-candidate/ruff')
                except V1Error as error:
                    assert error.code == 'SCOPE_TOO_LARGE'
                    result['normal_assembler_rejection'] = error.code
                else:
                    raise AssertionError('this runner only diagnoses refused oversized products')
                result['quality'] = verify_quality(product, evidence / 'quality')
                assert result['quality']['passed']
                result['behavior'] = verify_flows(prompt, product, fixture)
                assert source_hashes(product) == original
                result['source_unchanged'] = True
                result['diagnostic_passed'] = (result['behavior']['core_flow_passed']
                                                and result['behavior']['full_fault_matrix_passed'])
            except Exception as error:
                result.update(diagnostic_passed=False, failure=failure_details(error), error=traceback.format_exc())
            records.append(result)
            (evidence / 'result.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
            report = {'diagnostic_only': True, 'full_m2_acceptance': False,
                      'source_sha256': before, 'records': records}
            (args.output / 'result.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
            print(json.dumps({'id': identity, 'diagnostic_passed': result['diagnostic_passed'],
                              'failure': result.get('failure')}), flush=True)
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    raise SystemExit(not all(row['diagnostic_passed'] for row in records))


if __name__ == '__main__':
    main()
