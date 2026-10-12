"""Recompute data separation from the exact model projection and typed graphs."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

from build_composed_corpus import graph
from som_core import composed as c


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('preserve the prior audit')
    folder = c.ROOT / 'som-core/data/composed'
    sets = {}
    report = {'groups': {}, 'overlap': {}, 'files': {}}
    selected_blocks = set()
    atoms = set()
    for name in ('regression', 'heldout', 'dev', 'train', 'blind'):
        path = folder / (name + '.jsonl')
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        sets[name] = {'model_inputs': set(), 'name_free_structures': set(), 'compositions': set()}
        for row in rows:
            prompt = row['prompt']
            sets[name]['model_inputs'].add(c.features(prompt))
            sets[name]['name_free_structures'].add(c.signature(prompt))
            sets[name]['compositions'].add(graph(prompt))
            if name in {'train', 'dev'}:
                assert prompt['package'] not in {'app_experiments', 'app_documents'}
            if name == 'train':
                selected_blocks.update(block['id'] for file in row['dsl']['files'] for block in file['blocks'])
                atoms.update((op['scope'], op['action']) for op in prompt['operations'])
        report['groups'][name] = {'services': len(rows), 'packages': len({row['prompt']['package'] for row in rows}),
            'operations': sum(len(row['prompt']['operations']) for row in rows),
            **{kind: len(values) for kind, values in sets[name].items()}}
        report['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for a, b in itertools.combinations(sets, 2):
        for kind in sets[a]:
            count = len(sets[a][kind] & sets[b][kind])
            report['overlap'][f'{a}/{b}/{kind}'] = count
            assert count == 0
    catalog = json.loads(c.CATALOG.read_text())
    report['uncovered_blocks'] = sorted(set(catalog['blocks']) - selected_blocks)
    report['operation_atoms'] = sorted(atoms)
    report['covered_blocks'] = len(selected_blocks)
    report['passed'] = not report['uncovered_blocks'] and len(atoms) == 11
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
