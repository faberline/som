"""Prepare extra provider fault probes through the normal compact assembler.

These probes do not replace the sealed 114 formal or 12 development cases.
No test weights may read these prompts or their plans.
"""

import argparse
import hashlib
import json
from pathlib import Path

from som_core import flow_compact, quality
from som_core.v1 import V1Error, assemble, load_blocks

from freeze_flow_cases import requirement

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostic-oversize', action='store_true',
                        help='inspect oversized drafts; never a product acceptance result')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    blocks = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
    records = []
    for index, (database, storage) in enumerate(
        (database, storage) for database in ('postgresql', 'mongodb')
        for storage in ('s3', 'gcs')
    ):
        prompt = requirement(database, 'jwt', storage, 'json_export', 5000 + index, 0)
        prompt['operations'] = [{'scope': 'parent', 'action': 'list'},
                                {'scope': 'child', 'action': 'list'}]
        for role, field in (('parent', 'title'), ('child', 'code')):
            prompt['resources'][role]['fields'] = [
                {'name': field, 'type': 'str', 'required': True, 'unique': role == 'child'}
            ]
            prompt['resources'][role]['query'] = {'filter_field': None, 'date_field': None, 'max_limit': 100}
        steps = prompt['flow']['steps'][:2]
        steps.append({'name': 'stored', 'op': 'object.put',
                      'args': {'key': {'input': 'key'}, 'body': {'literal': 'c29tLWZsb3ctZmF1bHQ=', 'type': 'bytes'}},
                      'undo': 'delete_created'})
        prompt['flow'] = {'name': 'store', 'inputs': [
            {'name': 'parent', 'type': 'parent_input'},
            {'name': 'child', 'type': 'child_input'},
            {'name': 'key', 'type': 'string'},
        ], 'steps': steps, 'result': {'object': {'step': 'stored', 'field': 'sha256'},
                                    'parent': {'step': 'created_parent', 'field': 'id'}}}
        identity = database + '-' + storage
        case = args.output / identity
        case.mkdir()
        (case / 'prompt.json').write_text(json.dumps(prompt, indent=2) + '\n')
        plan = flow_compact.reference(prompt, blocks)
        (case / 'dsl.json').write_text(json.dumps(plan, indent=2) + '\n')
        binary = ROOT / 'som-rs/models/composed-quality-candidate/ruff'
        rejection = None
        try:
            files = flow_compact.assemble(prompt, plan, blocks, binary)
        except V1Error as error:
            if not args.diagnostic_oversize or error.code != 'SCOPE_TOO_LARGE':
                raise
            rejection = error.code
            files = {}
            for file in flow_compact.resolve(prompt, plan)['files']:
                raw = assemble({'version': 'som.dsl.v2', 'files': [file]}, blocks)[file['path']]
                files[file['path']] = (quality.format_source(file['path'], quality.assemble_imports(raw),
                                                            binary, package=prompt['package'])
                                       if file['path'].endswith('.py') else raw)
        (case / 'draft.json').write_text(json.dumps({'diagnostic_only': True,
                                                    'normal_assembler_rejection': rejection,
                                                    'files': files}) + '\n')
        for path, contents in files.items():
            target = case / 'product' / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        records.append({'id': identity, 'diagnostic_only': True, 'normal_assembler_accepted': rejection is None,
                        'normal_assembler_rejection': rejection,
                        'formal_case': False, 'training_example': False,
                        'bytes': sum(len(contents.encode()) for contents in files.values()),
                        'source_sha256': {path: hashlib.sha256(contents.encode()).hexdigest()
                                          for path, contents in files.items()}})
    (args.output / 'result.json').write_text(json.dumps({'records': records}, indent=2) + '\n')
    print(json.dumps([{'id': row['id'], 'bytes': row['bytes']} for row in records]))


if __name__ == '__main__':
    main()
