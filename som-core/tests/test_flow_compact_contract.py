"""Check bounded flow scope and the compact generator's safety limits."""

import ast
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType

import pytest

from som_core import flow, flow_compact, quality
from som_core.v1 import V1Error, assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "som-code-python/data/blocks/flow-v7-compact"


def nullable_prompt():
    row = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl").read_text().splitlines()[0])
    prompt = deepcopy(row["prompt"])
    prompt["resources"]["parent"]["fields"].append(
        {"name": "optional_id", "type": "int", "required": False, "default": None, "unique": False}
    )
    prompt["flow"] = {
        "name": "lookup",
        "inputs": [{"name": "parent_id", "type": "integer"}],
        "steps": [
            {"name": "access", "op": "authorize", "args": {}, "undo": None},
            {"name": "found", "op": "parent.get", "args": {"parent_id": {"input": "parent_id"}}, "undo": None},
            {
                "name": "missing", "op": "if",
                "condition": {"op": "is_null", "left": {"step": "found", "field": "optional_id"}},
                "then": [{"name": "fallback", "op": "parent.get", "args": {"parent_id": {"literal": 1, "type": "integer"}}, "undo": None}],
                "else": [{"name": "requested", "op": "parent.get", "args": {"parent_id": {"step": "found", "field": "optional_id"}}, "undo": None}],
                "exports": {"selected": {"then": {"step": "fallback", "field": ""}, "else": {"step": "requested", "field": ""}}},
            },
        ],
        "result": {"row": {"step": "selected", "field": ""}},
    }
    return prompt


def test_non_null_branch_can_pass_the_refined_integer():
    prompt = nullable_prompt()
    assert flow.validate(prompt) == prompt
    assert flow.result_types(prompt) == {"row": "parent_row"}
    blocks = load_blocks(LIBRARY)
    standard = flow_compact.reference(prompt, blocks)
    alternative = flow_compact.reference(prompt, blocks, "checked")
    assert standard != alternative
    for plan in (standard, alternative):
        flow.validate_plan(prompt, plan, flow_compact.metadata())
        source = flow_compact.compile_program({"prompt": prompt, "plan": plan["flow"]})
        ast.parse(source)
        assert "ParentRow" in source
        assert "FlowPlan" not in source
        assert "eval(" not in source


@pytest.mark.parametrize("outside", [False, True])
def test_null_refinement_cannot_escape_its_branch(outside):
    prompt = nullable_prompt()
    if outside:
        prompt["flow"]["steps"].append({"name": "leaked", "op": "parent.get", "args": {"parent_id": {"step": "found", "field": "optional_id"}}, "undo": None})
    else:
        prompt["flow"]["steps"][2]["then"][0]["args"]["parent_id"] = {"step": "found", "field": "optional_id"}
    with pytest.raises(V1Error) as error:
        flow.validate(prompt)
    assert error.value.code == "INVALID_REF"


def schema_module(prompt):
    blocks = load_blocks(LIBRARY)
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    file = next(item for item in plan["files"] if item["path"].endswith("/schemas.py"))
    source = assemble({"version": "som.dsl.v2", "files": [file]}, blocks)[file["path"]]
    module = ModuleType("compact_schema_boundary")
    exec(compile(source, file["path"], "exec"), module.__dict__)
    return module


def test_public_resource_and_field_names_cannot_shadow_helpers():
    prompt = nullable_prompt()
    prompt["resources"]["parent"]["name"] = "Managed"
    prompt["resources"]["child"]["name"] = "Batch"
    prompt["resources"]["parent"]["fields"] = [
        {"name": "non_null", "type": "str", "required": True, "unique": False},
        {"name": "bounded_values", "type": "str", "required": True, "unique": False},
        {"name": "revision", "type": "int", "required": True, "unique": False},
    ]
    prompt["resources"]["parent"]["query"] = {"filter_field": None, "date_field": None, "max_limit": 100}
    prompt["flow"]["steps"] = [prompt["flow"]["steps"][0]]
    prompt["flow"]["result"] = {}
    module = schema_module(prompt)
    created = module.ManagedCreate(non_null="set", bounded_values="value", revision=0)
    assert created.model_dump() == {"non_null": "set", "bounded_values": "value", "revision": 0}
    assert module.BatchCreate is not module._BatchCreate
    assert module.ManagedRead is not module._ManagedRead
    with pytest.raises(ValueError):
        module.ManagedPatch(non_null=None)
    with pytest.raises(ValueError):
        module.ManagedCreate(non_null="x" * 256, bounded_values="value", revision=0)


@pytest.mark.parametrize('names', [('Store', 'Run'), ('Context', 'Resource'), ('ParentInput', 'ChildInput')])
def test_resource_imports_cannot_replace_private_runtime_classes(names):
    prompt = nullable_prompt()
    prompt['capabilities']['database'] = 'mongodb'
    prompt['resources']['parent']['name'], prompt['resources']['child']['name'] = names
    blocks = load_blocks(LIBRARY)
    lowered = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    for file in lowered['files']:
        if not file['path'].endswith('.py'):
            continue
        source = assemble({'version': 'som.dsl.v2', 'files': [file]}, blocks)[file['path']]
        tree = ast.parse(source)
        imports = {alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names}
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        assert not imports & classes, (file['path'], imports & classes)
        if file['path'].endswith('/database.py'):
            assert '_Store' in classes
            assert '_ParentDocument' in imports and '_ChildDocument' in imports


def test_m2_records_size_and_keeps_legacy_quality_limit():
    prompt = nullable_prompt()
    prompt["flow"]["result"] = {"value": {"literal": "界" * 20000, "type": "string"}}
    blocks = load_blocks(LIBRARY)
    plan = flow_compact.reference(prompt, blocks)
    binary = ROOT / "som-rs/models/composed-quality-candidate/ruff"
    files = flow_compact.assemble(prompt, plan, blocks, binary)
    assert sum(len(source.encode()) for source in files.values()) > 65536
    assert len(files) <= 12
    with pytest.raises(V1Error) as error:
        quality.finalize({"pyproject.toml": files["pyproject.toml"],
                          "README.md": "界" * 22000}, binary)
    assert error.value.code == "SCOPE_TOO_LARGE"


def test_assembler_refuses_changed_block_source():
    prompt = nullable_prompt()
    blocks = load_blocks(LIBRARY)
    plan = flow_compact.reference(prompt, blocks)
    selected = plan["files"][0]["blocks"][0]["id"]
    blocks[selected] = dict(blocks[selected], template=blocks[selected]["template"] + "\n# changed\n")
    with pytest.raises(V1Error) as error:
        flow_compact.assemble(prompt, plan, blocks, ROOT / "som-rs/models/composed-quality-candidate/ruff")
    assert error.value.code == "INVALID_BLOCK"


def test_compact_effect_modes_preserve_the_selected_compensators():
    rows = [json.loads(line) for line in (ROOT / 'som-core/data/flow-v7-r10/formal.jsonl').read_text().splitlines()]
    prompt = next(row['prompt'] for row in rows if row['id'] == 'semantic-00')
    blocks = load_blocks(LIBRARY)
    plan = flow_compact.reference(prompt, blocks)
    encoded_before = json.dumps(plan, sort_keys=True)
    source = flow_compact.compile_program({'prompt': prompt, 'plan': plan['flow']})
    tree = ast.parse(source)
    modes = [keyword.value.value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == 'Effect'
             for keyword in node.keywords if keyword.arg == 'undo']
    assert set(modes) == {'delete_created', 'restore_patch'}
    assert json.dumps(plan, sort_keys=True) == encoded_before
    module = schema_module(prompt)
    assert module.Effect(step='created', undo='delete_created').group == ''
    with pytest.raises(ValueError):
        module.Effect(step='created', undo='guess')


def test_call_lowering_cannot_change_valid_string_constants():
    row = json.loads((ROOT / 'som-core/data/flow-v7-r10/formal.jsonl').read_text().splitlines()[0])
    prompt = deepcopy(row['prompt'])
    literal = '資料: ctx.export( and ctx.batch( must stay as text'
    prompt['flow']['result'] = {'literal': {'literal': literal, 'type': 'string'}}
    blocks = load_blocks(LIBRARY)
    plan = flow_compact.reference(prompt, blocks)
    program = flow_compact.compile_program({'prompt': prompt, 'plan': plan['flow']})
    strings = [node.value for node in ast.walk(ast.parse(program))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert literal in strings


def test_model_json_preserves_aliases_and_partial_input():
    prompt = nullable_prompt()
    module = schema_module(prompt)
    parent = {field['name']: (1 if field['type'] == 'int' else False
              if field['type'] == 'bool' else 'value')
              for field in prompt['resources']['parent']['fields']
              if field.get('required')}
    # A partial patch must omit defaults, while the public input keeps aliases.
    patch = module.ParentPatchData.model_validate({'optional_id': 0})
    assert module.model_json(patch, True) == {'optional_id': 0}
    value = module.FlowInput.model_validate({'parent_id': 3})
    assert module.model_json(value) == {'parent_id': 3}
    created = module.ParentInputData.model_validate(parent)
    assert module.model_json(created)['optional_id'] is None


@pytest.mark.parametrize(
    "identity,version,accepted",
    [("compact.test", 4, True), ("legacy.test", 4, False),
     ("compact.test", 5, True), ("legacy.test", 5, False),
     ("compact.test", 6, False), ("legacy.test", 3, True)],
)
def test_new_block_version_keeps_legacy_admission(tmp_path, identity, version, accepted):
    block = {"id": identity, "version": version, "imports": [],
             "params": {}, "template": ""}
    (tmp_path / "blocks.json").write_text(json.dumps([block]))
    if accepted:
        assert load_blocks(tmp_path) == {identity: block}
    else:
        with pytest.raises(V1Error) as error:
            load_blocks(tmp_path)
        assert error.value.code == "INVALID_BLOCK"
