"""Prepare admitted inputs and dependency receipts. Never start a fixture."""
from __future__ import annotations
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
ACCEPTANCE_DIR = ROOT.parent
if str(ACCEPTANCE_DIR) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_DIR))
from paths import default_output_root, find_repo, require_new_output
REPO = find_repo(__file__)
OUTPUT_ROOT = default_output_root(REPO) / 'renderer_behavior'
DEFAULT_PREPARATION = OUTPUT_ROOT / 'preparation'
SCRIPTS = REPO / 'som-core/scripts'
CONTRACT = REPO / 'som-rs/contracts/compiler-v1'
TESTS = REPO / 'som-rs/tests/compiler-v1'
sys.path[:0] = [str(SCRIPTS), str(REPO / 'som-core/src')]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def prepare(output: Path):
    output = require_new_output(output, OUTPUT_ROOT)
    output.mkdir(parents=True)
    import qa_harness as qa
    import verify_flow_service
    import verify_python_quality
    import m3_local_fixture
    from flow_branch_cases import branch_inputs
    from som_core.flow import validate as validate_prompt
    freeze = qa.verify_freeze(REPO)
    source = TESTS / 'cnl-positive-fixtures.json'
    frozen_rows = {row['id']: row for row in json.loads(source.read_text())['cases']}
    public = CONTRACT / 'examples'
    spec = importlib.util.spec_from_file_location('frozen_serializer', TESTS / 'serializer.py')
    serializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serializer)
    input_index = json.loads((ROOT / 'inputs/index.json').read_text())
    assert input_index['version'] == 'som.renderer-behavior-inputs.v1'
    assert len(input_index['cases']) == 11
    cases = []
    added = {'REF_COMPARE_EQ', 'REF_COMPARE_NE', 'REF_COMPARE_LT', 'REF_COMPARE_LE',
             'REF_COMPARE_GT', 'REF_COMPARE_GE', 'REF_NULLABLE_OPTIONAL_FIELD'}
    for entry in input_index['cases']:
        identity = entry['id']
        directory = ROOT / 'inputs' / identity
        assert sha(directory / 'prompt.json') == entry['prompt_sha256']
        assert sha(directory / 'source.cnl') == entry['cnl_sha256']
        assert sha(directory / 'origin.json') == entry['origin_sha256']
        prompt = json.loads((directory / 'prompt.json').read_text())
        cnl = (directory / 'source.cnl').read_text()
        case = json.loads((directory / 'origin.json').read_text())
        expected = json.loads((public / 'v7.prompt.json').read_text())
        if identity in frozen_rows:
            assert qa.exact_equal(prompt, frozen_rows[identity]['expected_prompt'])
            assert cnl == frozen_rows[identity]['cnl']
        elif identity == 'PUBLIC_v7-branch-null':
            assert qa.exact_equal(prompt, json.loads((public / 'v7-branch-null.prompt.json').read_text()))
            assert cnl == (public / 'v7-branch-null.cnl').read_text()
        elif identity == 'PUBLIC_v7-empty-field-segments':
            expected['flow']['result']['parent'] = {'step': 'made', 'field': '.id.'}
            assert qa.exact_equal(prompt, expected)
            assert cnl == serializer.serialize_prompt(prompt, contract_dir=CONTRACT)
        elif identity == 'PUBLIC_v7-empty-branch':
            expected['flow']['steps'].insert(1, {'name': 'nothing', 'op': 'if',
                'condition': {'op': 'eq', 'left': {'literal': 1, 'type': 'integer'},
                              'right': {'literal': 1, 'type': 'integer'}},
                'then': [], 'else': [], 'exports': {}})
            assert qa.exact_equal(prompt, expected)
            assert cnl == serializer.serialize_prompt(prompt, contract_dir=CONTRACT)
        else:
            raise ValueError('unapproved packaged case: ' + identity)
        assert sha(REPO / case['source']) == case['source_sha256']
        validate_prompt(prompt)
        generated_cnl = serializer.serialize_prompt(prompt, contract_dir=CONTRACT)
        case.update(source=str(REPO / case['source']), prompt_path=str(directory / 'prompt.json'),
                    cnl_path=str(directory / 'source.cnl'), origin_path=str(directory / 'origin.json'),
                    branch_matrix=branch_inputs(prompt),
                    source_matches_serializer_bytes=cnl == generated_cnl,
                    serializer_cnl_sha256=hashlib.sha256(generated_cnl.encode()).hexdigest())
        cases.append(case)
    versions = {}
    missing = []
    for name in ('ruff', 'mypy', 'pylint', 'isort', 'httpx', 'sqlalchemy', 'psycopg', 'pydantic',
                 'PyJWT', 'requests', 'pymongo', 'boto3', 'moto', 'cryptography'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    pins = verify_python_quality.PINS
    pins_match = all(versions.get(name) == version for name, version in pins.items())
    binaries = {'python': str(Path(sys.executable).resolve()), 'ruff': str(Path(sys.executable).with_name('ruff')),
                'moto_server': str(Path(sys.executable).with_name('moto_server'))}
    for name in ('uv', 'mongod', 'redis-server'):
        binaries[name] = shutil.which(name)
    for name in ('initdb', 'pg_ctl', 'createdb', 'dropdb'):
        binaries[name] = str(m3_local_fixture.PG_BIN / name)
    binary_records = {name: {'path': path, 'exists': bool(path and Path(path).is_file()),
                            'sha256': sha(Path(path)) if path and Path(path).is_file() else None}
                      for name, path in binaries.items()}
    # Hash the source modules actually imported by the unchanged public checkers.
    imported = {}
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and Path(path).is_file() and Path(path).suffix == '.py':
            resolved = Path(path).resolve()
            if resolved.is_relative_to(SCRIPTS.resolve()) or resolved.is_relative_to((REPO / 'som-core/src').resolve()):
                imported[str(resolved)] = sha(resolved)
    for path in (TESTS / 'serializer.py', public / 'v7-branch-null.cnl',
                 CONTRACT / 'CONTRACT.md', CONTRACT / 'selection-policy.json',
                 REPO / 'som-rs/contracts/python-quality-v1/pyproject.toml',
                 ACCEPTANCE_DIR / 'qa_harness.py', ACCEPTANCE_DIR / 'paths.py',
                 ACCEPTANCE_DIR / 'public_products.py', ACCEPTANCE_DIR / 'acceptance_cleanup.py',
                 ROOT / 'prepare.py', ROOT / 'run.py', ROOT / 'behavior_support.py',
                 ROOT / 'inputs/index.json'):
        imported[str(path)] = sha(path)
    dependencies = {'python_version': sys.version, 'python_executable': sys.executable,
                    'versions': versions, 'quality_pins': pins, 'quality_pins_match': pins_match,
                    'missing_distributions': missing, 'binaries': binary_records,
                    'checker_imports_passed': True,
                    'services_started': False, 'fixtures_started': False, 'candidate_calls': 0}
    write(output / 'dependencies.json', dependencies)
    limitations = {
        'REF_BRANCH_EXPORT_SAME_TYPE': [
            'Existing branch_inputs requires a literal right operand; choose compares two inputs.',
            'Existing lease_checks pauses a parent resource read for write-free flows; this flow has no resource read.',
        ],
        'PUBLIC_v7-branch-null': [
            'Existing idempotency-conflict probe writes parent.title although this resource declares parent.name; it gets extra_forbidden before the conflict check.',
        ],
        'PUBLIC_v7-empty-field-segments': [
            'Existing expected_run.reference splits .id. without removing empty parts; it indexes an empty key.',
        ],
        'PUBLIC_v7-empty-branch': [
            'Existing idempotency-conflict probe invents parent.title for a resource with parent.name; it gets extra_forbidden before conflict checks.',
            'Existing branch_inputs marks a literal-only condition unresolved.',
            'The constant 1==1 branch has no reachable false arm; existing require_branch_coverage demands both arms.',
        ],
    }
    for identity in sorted(added):
        limitations[identity] = ([
            'Existing branch_inputs requires a literal right operand; compare uses two integer inputs.',
            'Existing lease_checks pauses a parent resource read for write-free flows; this flow has no resource read.',
        ] if identity.startswith('REF_COMPARE_') else [])
    manifest = {'version': 'som.compiler-renderer-behavior-preparation.v1', 'freeze_chain': freeze,
                'cases': cases, 'known_checker_limits': limitations, 'source_sha256': imported,
                'ready_dependencies': not missing and pins_match and all(row['exists'] for row in binary_records.values()),
                'execution_status': 'UNRUN; await approved final candidate and explicit root execution instruction',
                'fixture': 'Fresh M3Fixture local PostgreSQL17 plus task-owned Mongo/Redis/provider mocks',
                'checks': ['unchanged full strict quality', 'unchanged verify_flow_service.verify full_m2_acceptance',
                           'independent original-prompt typed value/DB row/journal branch observations',
                           'idempotency conflict and completed recovery', 'targeted checkpoint failure and crash recovery'],
                'models': 'No model loaded or run', 'production_changes': []}
    write(output / 'preparation.json', manifest)
    print(json.dumps({'cases': len(cases), 'ready_dependencies': manifest['ready_dependencies'],
                      'services_started': False, 'fixtures_started': False, 'candidate_calls': 0}))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_PREPARATION)
    args = parser.parse_args()
    prepare(args.output)
