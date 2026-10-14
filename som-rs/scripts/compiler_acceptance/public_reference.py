"""Independent public-constructor and renderer oracle, without a runner entrypoint.

Expected plans come from the existing public Python constructors and frozen
block libraries. This module does not read saved answer DSL or model outputs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))
from paths import default_output_root, find_repo  # noqa: E402

ROOT = find_repo(__file__)
OUTPUT_ROOT = default_output_root(ROOT)
for source_path in (ROOT / 'som-core/src', ROOT / 'som-core/scripts'):
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))

from som_core import composed, flow_compact, quality, related, v1  # noqa: E402
import build_v1_corpus  # noqa: E402
import build_query_corpus  # noqa: E402
import build_service_corpus  # noqa: E402
import build_date_corpus_v4  # noqa: E402

BINARY = Path('ruff-not-configured')
PROFILE = ROOT / 'som-rs/contracts/python-quality-v1/pyproject.toml'
LEGACY = {b['id']: b for b in json.loads((ROOT / 'som-code-python/data/blocks/v5/blocks.json').read_text(encoding='utf-8'))}
CURRENT = {b['id']: b for b in json.loads((ROOT / 'som-code-python/data/blocks/flow-v7-compact/blocks.json').read_text(encoding='utf-8'))}
QUALITY_BLOCKS = {b['id']: b for b in json.loads((ROOT / 'som-code-python/data/blocks/composed-quality/blocks.json').read_text(encoding='utf-8'))}
OPS = ['create', 'get', 'list', 'patch', 'delete']


def reference_dsl(prompt: dict) -> dict:
    version = int(prompt['version'].rsplit('v', 1)[1])
    if version <= 4:
        steps = [v1.LOGIC_STEPS, v1.LOGIC_STEPS_V2, v1.LOGIC_STEPS_V3, v1.LOGIC_STEPS_V4][version - 1]
        order = tuple(OPS[steps.index(step)] for step in prompt['logic'])
        if version == 1:
            return build_v1_corpus.case(order, 0)['dsl']
        if version == 2:
            return build_query_corpus.query_case(order, 0)['dsl']
        row = build_service_corpus.case(order, 0, 0)
        if version == 3:
            return row['dsl']
        return build_date_corpus_v4.upgrade(row, 'due_on', 0)['dsl']
    if version == 5:
        return related.document(prompt, LEGACY)
    if version == 6:
        return composed.reference(prompt, QUALITY_BLOCKS)
    return flow_compact.reference(prompt, CURRENT, 'standard')


def render_raw(prompt: dict, dsl: dict) -> dict[str, str]:
    version = prompt['version']
    if version == 'som.prompt.v7':
        lowered = flow_compact.resolve(prompt, dsl)
        files = {}
        for file in lowered['files']:
            files.update(v1.assemble({'version': 'som.dsl.v2', 'files': [file]}, CURRENT, byte_limit=None))
        return files
    blocks = QUALITY_BLOCKS if version == 'som.prompt.v6' else LEGACY
    return v1.assemble(v1.resolve_prompt_refs(prompt, dsl), blocks)
