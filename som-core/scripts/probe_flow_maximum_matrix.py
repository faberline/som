"""Measure every maximum capability tuple without accepting oversized drafts.

These boundary cases are diagnostics. They do not change the sealed formal
or development fixtures. They never train or select a checkpoint.
"""

import argparse
import hashlib
import itertools
import json
from pathlib import Path

from som_core import flow, flow_codec, flow_compact, quality
from som_core.v1 import V1Error, assemble, load_blocks

from freeze_flow_cases import action, requirement, result_ref

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "som-code-python/data/blocks/flow-v7-compact"
FORMATTER = ROOT / "som-rs/models/composed-quality-candidate/ruff"


def maximum_matrix():
    """Keep eight fields, eight actions, two transactions and nested branches."""
    tuples = itertools.product(
        ("postgresql", "mongodb"), ("jwt", "firebase", "google_oauth"),
        ("none", "s3", "gcs"),
        ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"),
    )
    for index, capabilities in enumerate(tuples):
        prompt = requirement(*capabilities, 4000 + index, 21)
        prompt['resources']['parent']['fields'] += [
            {"name": "rating", "type": "float", "required": True, "unique": False},
            {"name": "number", "type": "int", "required": True, "unique": False},
            {"name": "enabled", "type": "bool", "required": False,
             "default": False, "unique": False},
        ]
        if capabilities[2] == "none":
            prompt['flow']['steps'].insert(4, action(
                'read_child', 'child.get', {
                    'parent_id': result_ref('created_parent', 'id'),
                    'child_id': result_ref('created_child', 'id'),
                }))
        flow.validate(prompt)
        yield 'maximum-' + '-'.join(capabilities), prompt


def sources():
    paths = [Path(__file__).resolve(), LIBRARY / 'blocks.json', FORMATTER,
             flow_compact.CATALOG, flow.CONTRACT_PATH,
             ROOT / 'som-core/scripts/freeze_flow_cases.py',
             ROOT / 'som-core/data/composed/regression.jsonl',
             quality.PROFILE]
    paths += [ROOT / ('som-core/data/flow-v7-r10/' + name)
              for name in ('dev.jsonl', 'formal.jsonl', 'manifest.json')]
    paths += list((ROOT / 'som-core/block_templates/flow_compact').glob('*.tpl'))
    paths += [ROOT / ('som-core/src/som_core/' + name + '.py') for name in
              ('flow', 'flow_compact', 'flow_codec', 'v1', 'quality',
               'composed', 'compact_names')]
    return {str(path.relative_to(ROOT)): quality.sha256(path) for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = sources()
    library = load_blocks(LIBRARY)
    records = []
    for identity, prompt in maximum_matrix():
        destination = args.output / identity
        destination.mkdir()
        plan = flow_compact.reference(prompt, library)
        lowered = flow_compact.resolve(prompt, plan)
        features = flow_codec.features(prompt, flow_compact.metadata())
        encoded = flow_codec.encode(prompt, plan, flow_compact.metadata())
        assert flow_codec.decode(prompt, encoded, flow_compact.metadata()) == plan
        files = {}
        for file in lowered['files']:
            source = assemble({'version': 'som.dsl.v2', 'files': [file]}, library)[file['path']]
            if file['path'].endswith('.py'):
                source = quality.format_source(file['path'], quality.assemble_imports(source),
                                               FORMATTER, package=prompt['package'])
            files[file['path']] = source
            target = destination / 'diagnostic-draft' / file['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        normal_files, rejection = None, None
        try:
            normal_files = flow_compact.assemble(prompt, plan, library, FORMATTER)
        except V1Error as error:
            rejection = error.code
        total = sum(len(source.encode()) for source in files.values())
        block_count = sum(len(file['blocks']) for file in plan['files'])
        budgets = (len(files) <= 12 and block_count <= 64
                   and max(features.count('<'), encoded.count('<')) <= 2048)
        record = {'id': identity, 'capabilities': prompt['capabilities'],
                  'formatted_bytes': total, 'files': len(files), 'blocks': block_count,
                  'input_tokens': features.count('<'), 'output_tokens': encoded.count('<'),
                  'file_bytes': {path: len(source.encode()) for path, source in files.items()},
                  'source_sha256': {path: hashlib.sha256(source.encode()).hexdigest()
                                    for path, source in files.items()},
                  'budgets_passed': budgets, 'normal_accepted': normal_files is not None,
                  'rejection': rejection, 'normal_agrees':
                      normal_files == files if budgets else rejection == 'SCOPE_TOO_LARGE'}
        records.append(record)
        for name, value in (('prompt.json', prompt), ('dsl.json', plan), ('result.json', record)):
            (destination / name).write_text(json.dumps(value, indent=2) + '\n')
        (destination / 'model-input.txt').write_text(features)
        (destination / 'reference-output.txt').write_text(encoded)
        print(json.dumps({key: record[key] for key in
                          ('id', 'formatted_bytes', 'budgets_passed', 'normal_accepted', 'rejection')}),
              flush=True)
    unchanged = before == sources()
    report = {'stage': 'maximum_tuple_budget_diagnostic', 'product_acceptance': False,
              'formal_fixtures_changed': False, 'training_use': False,
              'source_sha256': before, 'source_unchanged': unchanged,
              'cases': len(records), 'budget_passed': sum(row['budgets_passed'] for row in records),
              'normal_accepted': sum(row['normal_accepted'] for row in records),
              'normal_assembler_agrees': all(row['normal_agrees'] for row in records),
              'records': records}
    report['passed'] = (unchanged and len(records) == 90
                        and all(row['budgets_passed'] and row['normal_agrees'] for row in records))
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(not report['passed'])


if __name__ == '__main__':
    main()
