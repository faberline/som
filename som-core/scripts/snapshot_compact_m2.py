"""Archive this failed M2 round. This command cannot promote or close M2."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_sources(record):
    """Check the exact source scope recorded by each component receipt."""
    return all((ROOT / name).is_file() and digest(ROOT / name) == value
               for name, value in record.get('source_sha256', {}).items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--round', type=Path, required=True)
    parser.add_argument('--legacy-round', type=Path, required=True)
    parser.add_argument('--seal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.round = args.round.resolve()
    args.legacy_round = args.legacy_round.resolve()
    args.seal = args.seal.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    receipt_paths = {
        'budget': args.round / 'budget-002/result.json',
        'maximum_matrix': args.round / 'maximum-matrix-007/result.json',
        'codec': args.round / 'codec-008/result.json',
        'artifacts': args.round / 'artifacts-004/result.json',
        'formal': args.round / 'behavior-007-formal/result.json',
        'quality_postgresql': args.round / 'quality-006-postgresql/result.json',
        'quality_mongodb': args.round / 'quality-006-mongodb/result.json',
        'environment': args.round / 'environment-009.json',
        'legacy100': args.legacy_round / 'legacy-behavior-035/result.json',
        'services14': args.legacy_round / 'legacy-behavior-037-regression.json',
        'compositions120': args.legacy_round / 'legacy-behavior-038/heldout.json',
        'former_blind80': args.legacy_round / 'legacy-behavior-038/blind.json',
        'legacy_metal_archive': args.legacy_round / 'legacy-archive-046.json',
        'source_seal': args.seal / 'seal.json',
    }
    receipts = {name: read(path) for name, path in receipt_paths.items()}
    formal = receipts['formal']
    assert formal['attempted'] == formal['required'] == len(formal['records']) == 114
    assert receipts['artifacts']['artifact_cases'] == 128
    assert receipts['maximum_matrix']['cases'] == 90
    assert receipts['codec']['cases'] == 201
    assert receipts['source_seal']['counts'] == {'dev': 12, 'formal': 114}
    for name, count in (('legacy100', 100), ('services14', 14),
                        ('compositions120', 120), ('former_blind80', 80)):
        assert receipts[name]['passed'] and len(receipts[name]['results']) == count
    assert receipts['legacy_metal_archive']['passed']
    assert receipts['legacy_metal_archive']['count'] == 314
    baseline = read(ROOT / 'som-rs/evals/m2-flow-2026-10-02/r1/snapshot-002/result.json')
    protected = baseline['protected_asset_sha256']
    changed = [name for name, expected in protected.items() if digest(ROOT / name) != expected]
    assert not changed, changed
    defaults = {name: str((ROOT / name).resolve()) for name in baseline['defaults']}
    assert defaults == baseline['defaults']
    original_sources = baseline['source_sha256']
    originals = [ROOT / name for name in original_sources
                 if name.startswith('som-core/block_templates/flow/')
                 or name in ('som-code-python/data/blocks/flow-v7/blocks.json',
                             'som-code-python/data/block-contracts/flow-v7.json',
                             'som-core/data/flow-v7-r10/dev.jsonl',
                             'som-core/data/flow-v7-r10/formal.jsonl')]
    assert all(digest(path) == original_sources[str(path.relative_to(ROOT))] for path in originals)
    weights = list((ROOT / 'som-rs/models/flow-m2-candidate').rglob('*.safetensors'))
    assert not weights, 'The prerequisite gate failed. Test weights must not exist.'
    source_checks = {name: current_sources(receipts[name]) for name in
                     ('maximum_matrix', 'codec', 'artifacts', 'formal', 'source_seal')}
    assert all(source_checks.values()), source_checks
    assert all(receipts[name]['source_unchanged'] for name in source_checks)
    assert receipts['environment']['passed']
    report = {
        'version': 'som.m2.compact-failed-round.v1', 'm2_passed': False,
        'stage': 'size gate failed; complete flow fault matrix and Metal remain incomplete',
        'limits': {'files': 12, 'blocks': 64, 'bytes': 65536,
                   'input_tokens': 2048, 'output_tokens': 2048},
        'formal': {'attempted': 114, 'core_passed': formal['core_passed'],
                   'full_passed': formal['full_passed'],
                   'size_refused': sum(row.get('rejection') == 'SCOPE_TOO_LARGE'
                                       for row in formal['records']),
                   'other_failures': [row['id'] for row in formal['records']
                                      if not row['core_passed'] and row.get('rejection') != 'SCOPE_TOO_LARGE'],
                   'origin': 'reference DSL; not inference or model accuracy'},
        'maximum_matrix': {key: receipts['maximum_matrix'][key] for key in
                           ('cases', 'budget_passed', 'normal_accepted', 'normal_assembler_agrees')},
        'maximum_outputs': [max((row for row in receipts['maximum_matrix']['records']
                                 if row['capabilities']['database'] == database),
                                key=lambda row: row['formatted_bytes'])
                            for database in ('postgresql', 'mongodb')],
        'codec': {key: receipts['codec'][key] for key in ('cases', 'valid', 'rejected', 'passed')},
        'artifacts': {key: receipts['artifacts'][key] for key in
                      ('program_cases', 'program_passed', 'artifact_cases', 'artifact_passed',
                       'product_accepted', 'passed')},
        'quality_maximum_samples': {name: receipts[name]['passed'] for name in
                                    ('quality_postgresql', 'quality_mongodb')},
        'legacy': {'old100': 100, 'services14': 14, 'compositions120': 120,
                   'former_blind80': 80, 'full_archived_metal_responses_equal': 314,
                   'scope': 'archived v6 and legacy weights; no flow_plan weights'},
        'candidate_sources_current': source_checks,
        'protected_assets': {'count': len(protected), 'changed': changed,
                             'sha256': protected, 'original_flow_files_unchanged': len(originals)},
        'defaults': defaults, 'test_weights_created': False, 'new_flow_metal_passed': False,
        'training_admission': False, 'paid_agent_runs': 0, 'current_switched': False,
        'observations': {'new_flow_model_bytes': None, 'new_flow_peak_resident_bytes': None,
                         'new_flow_inference_ms': None, 'new_flow_complete_generation_ms': None,
                         'reason': 'No admitted v7 weights or generated model product.'},
        'receipt_sha256': {str(path.relative_to(ROOT)): digest(path) for path in receipt_paths.values()},
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'required_next': ['reduce every maximum and supported product below the fixed budgets',
                          'finish object-version, lost-response, compensation-interruption and branch/group checks',
                          'seal the accepted source and make development-only test weights',
                          'prove all 12 Rust Metal/HTTP/CLI paths, then close issues 4-6 and M2'],
    }
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'tracked-work.patch').write_bytes(
        subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT))
    (args.output / 'git-status.txt').write_bytes(
        subprocess.check_output(['git', 'status', '--short'], cwd=ROOT))
    print(json.dumps({key: report[key] for key in ('m2_passed', 'formal', 'training_admission')}))


if __name__ == '__main__':
    main()
