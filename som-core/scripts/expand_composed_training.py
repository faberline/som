"""Append new training graphs. Preserve every development and heldout byte."""
import ast
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random

from build_business_corpus import add_caps
from build_related_corpus import case
from build_composed_corpus import graph
from som_core import composed as c
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--train-count', type=int, default=8000)
    parser.add_argument('--dev-count', type=int, default=120)
    parser.add_argument('--canonical-dependencies', action='store_true')
    args = parser.parse_args()
    folder = c.ROOT / 'som-core/data/composed'
    manifest = json.loads((folder / 'manifest.json').read_text())
    groups = {name: [json.loads(line) for line in (folder / (name + '.jsonl')).read_text().splitlines()]
              for name in ('regression', 'heldout', 'dev', 'train')}
    protected = {name: sha((folder / (name + '.jsonl')).read_bytes()) for name in ('regression', 'heldout')}
    seen = [set(), set(), set()]
    for rows in groups.values():
        for row in rows:
            for known, value in zip(seen, (c.features(row['prompt']), c.signature(row['prompt']), graph(row['prompt']))):
                known.add(value)
    library = load_blocks(c.ROOT / 'som-code-python/data/blocks/composed')
    choices = list(itertools.product(('postgresql', 'mongodb'), ('jwt', 'firebase', 'google_oauth'),
                                    ('none', 's3', 'gcs'), ('json_export', 'csv', 'gzip_export', 'decimal_summary', 'statistics')))
    serial = max(700000, manifest.get('expansion', {}).get('end_serial', 700000))
    start_serial = serial
    while len(groups['train']) < args.train_count or len(groups['dev']) < args.dev_count:
        group = 'dev' if len(groups['dev']) < args.dev_count else 'train'
        serial += 1
        rng = random.Random(serial + 17)
        prompt = case(serial % 8, serial % 5, serial, training=True)
        if not add_caps(prompt, *choices[serial % len(choices)]):
            continue
        try:
            prompt = c.from_legacy(prompt)
            prompt['operations'] = rng.sample(prompt['operations'], rng.randint(1, 11))
            keys = (c.features(prompt), c.signature(prompt), graph(prompt))
            if any(value in known for known, value in zip(seen, keys)):
                continue
            dsl = c.reference(prompt, library)
            c.validate_plan(prompt, dsl)
            files = assemble(resolve_prompt_refs(prompt, dsl), library)
            for name, source in files.items():
                if name.endswith('.py'):
                    ast.parse(source)
        except V1Error:
            continue
        groups[group].append({'prompt': prompt, 'dsl': dsl})
        for known, value in zip(seen, keys):
            known.add(value)
        manifest['cases'][group].append({
            'app': serial % 8, 'input_sha256': sha(keys[0].encode()),
            'structure_sha256': sha(keys[1].encode()), 'graph_sha256': sha(keys[2].encode()),
            'operations': len(prompt['operations']),
            'output_blocks': sum(len(file['blocks']) for file in dsl['files']),
            'source_bytes': sum(len(source.encode()) for source in files.values())})
    for group in ('train', 'dev'):
        if args.canonical_dependencies:
            for row in groups[group]:
                for file in row['dsl']['files']:
                    for block in file['blocks']:
                        # Dependency order has no execution meaning. A single
                        # canonical order removes a redundant copying task.
                        block['refs'].sort()
                c.validate_plan(row['prompt'], row['dsl'])
        target = folder / (group + '.jsonl')
        target.write_text(''.join(c.canonical(row) + '\n' for row in groups[group]))
        manifest['files'][target.name] = sha(target.read_bytes())
    for name, rows in manifest['cases'].items():
        manifest['counts'][name] = {
            'services': len(rows), 'apps': len({row['app'] for row in rows}),
            'operations': sum(row['operations'] for row in rows),
            **{label: len({row[key] for row in rows}) for label, key in
               [('unique_inputs', 'input_sha256'), ('unique_structures', 'structure_sha256'), ('unique_graphs', 'graph_sha256')]}}
    for a, b in itertools.combinations(groups, 2):
        for key in ('input_sha256', 'structure_sha256', 'graph_sha256'):
            count = len({m[key] for m in manifest['cases'][a]} & {m[key] for m in manifest['cases'][b]})
            assert count == 0
            manifest['split_overlap'][f'{a}/{b}/{key}'] = count
    for name, value in protected.items():
        assert sha((folder / (name + '.jsonl')).read_bytes()) == value
    manifest['expansion'] = {'training_and_development_only': True, 'start_serial': start_serial, 'end_serial': serial, 'protected_files': protected, 'canonical_dependency_order': args.canonical_dependencies}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest['counts']))


if __name__ == '__main__':
    main()
