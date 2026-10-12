"""Seal fresh test compositions after diagnostic test-driven engineering changes."""
import hashlib
import itertools
import json
import random

from build_business_corpus import add_caps
from build_related_corpus import case
from build_composed_corpus import graph
from som_core import composed as c
from som_core.v1 import V1Error, load_blocks


def main():
    folder = c.ROOT / 'som-core/data/composed'
    path = folder / 'blind.jsonl'
    if path.exists():
        raise ValueError('blind set already sealed')
    seen = [set(), set(), set()]
    protected = {}
    for group in ('train', 'dev', 'heldout', 'regression'):
        source = folder / (group + '.jsonl')
        protected[group] = hashlib.sha256(source.read_bytes()).hexdigest()
        with source.open() as lines:
            for line in lines:
                prompt = json.loads(line)['prompt']
                for known, value in zip(seen, (c.features(prompt), c.signature(prompt), graph(prompt))):
                    known.add(value)
    library = load_blocks(c.ROOT / 'som-code-python/data/blocks/composed')
    choices = list(itertools.product(('postgresql', 'mongodb'), ('jwt', 'firebase', 'google_oauth'),
        ('none', 's3', 'gcs'), ('json_export', 'csv', 'gzip_export', 'decimal_summary', 'statistics')))
    rows = []
    serial = 1700000
    while len(rows) < 80:
        serial += 1
        prompt = case(serial % 10, serial % 5, serial, training=True)
        if not add_caps(prompt, *choices[serial % len(choices)]):
            continue
        try:
            prompt = c.from_legacy(prompt)
            rng = random.Random(serial + 17)
            prompt['operations'] = rng.sample(prompt['operations'], rng.randint(1, 11))
            keys = (c.features(prompt), c.signature(prompt), graph(prompt))
            if any(value in known for known, value in zip(seen, keys)):
                continue
            dsl = c.reference(prompt, library)
            c.validate_plan(prompt, dsl)
        except V1Error:
            continue
        rows.append({'prompt': prompt, 'dsl': dsl})
        for known, value in zip(seen, keys): known.add(value)
    path.write_text(''.join(c.canonical(row) + '\n' for row in rows))
    record = {'purpose': 'fresh final model test; prior heldout is now diagnostic regression',
        'checkpoint_selection': 'development only; this set must not select checkpoints',
        'cases': len(rows), 'app_families': len({r['prompt']['package'] for r in rows}),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'protected_sources': protected,
        'model_manifest_at_seal': hashlib.sha256((c.ROOT / 'som-rs/models/composed-candidate/manifest.json').read_bytes()).hexdigest()}
    (folder / 'blind-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
