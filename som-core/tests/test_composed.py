"""Observable negative cases for the new composition boundary."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from som_core import composed as c
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/scripts"))
from eval_transport import observed_ranges, page


@pytest.fixture
def case():
    p = c.from_legacy(json.loads((ROOT / "som-core/data/business-service/requirements.jsonl").read_text().splitlines()[0])["prompt"])
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/composed")
    return p, c.reference(p, blocks), blocks


def test_subsets_are_explicit_and_lossless(case):
    p, _, blocks = case
    for ops in (p["operations"], p["operations"][:1], p["operations"][3:7], list(reversed(p["operations"]))):
        q = deepcopy(p); q["operations"] = ops
        d = c.reference(q, blocks)
        assert c.decode(c.encode(d)) == d
        c.validate_plan(q, d)
        assert assemble(resolve_prompt_refs(q,d),blocks)


def test_equivalent_dependency_names_are_valid(case):
    p, d, _ = case
    for f in d["files"]:
        for b in f["blocks"]:
            b["name"] = "renamed_"+b["name"]
            b["refs"] = ["renamed_"+r for r in b["refs"]]
    c.validate_plan(p,d)


@pytest.mark.parametrize("change,code", [
    ("auth", "INVALID_COMPOSITION"), ("transaction", "INVALID_COMPOSITION"),
    ("wrong_database", "INVALID_COMPOSITION"), ("wrong_binding", "INVALID_REF"),
    ("dependency", "INVALID_REF"), ("extra_route", "INVALID_COMPOSITION"),
    ("early_close", "INVALID_REF"), ("workflow", "INVALID_REF"),
])
def test_unsafe_compositions_rejected(case, change, code):
    p, d, _ = case
    api = next(f for f in d["files"] if f["path"] == c.SLOTS["app"])
    auth = next(f for f in d["files"] if f["path"] == c.SLOTS["integrations"])
    if change == "auth": auth["blocks"] = [b for b in auth["blocks"] if b["name"] != "auth"]
    if change == "transaction":
        api["blocks"] = [b for b in api["blocks"] if b["name"] != "batch"]
        next(b for b in api["blocks"] if b["name"]=="tail")["refs"].remove("batch")
    if change == "wrong_database": api["blocks"][0]["id"] = "api.business.head.mongodb"
    if change == "wrong_binding": api["blocks"][1]["params"]["path"]["$ref"] = "child.path"
    if change == "dependency": api["blocks"][1]["refs"] = []
    if change == "extra_route":
        extra = deepcopy(api["blocks"][1]); extra["name"]="duplicate"
        api["blocks"].insert(2,extra)
    if change == "early_close":
        tail = next(b for b in api["blocks"] if b["name"]=="tail")
        api["blocks"].remove(tail); api["blocks"].insert(1,tail)
    if change == "workflow": api["blocks"] = [b for b in api["blocks"] if b["name"] != "workflow"]
    with pytest.raises(V1Error) as caught: c.validate_plan(p,d)
    assert caught.value.code == code


def test_codec_requires_end_and_well_formed_binding(case):
    _, d, _ = case
    for text in (c.encode(d).removesuffix("<eos>"), c.encode(d)+"junk", "<file:app><block:api.tail><name:tail><bind:x:y:z><deps><end></file><eos>"):
        with pytest.raises(V1Error): c.decode(text)


def test_full_source_audit_detects_missing_middle_and_changed_bytes(capsys):
    data = ('first\n'+'漢字'*100+'\nlast\n').encode()
    page(data)
    output = capsys.readouterr().out
    assert observed_ranges(data,[output])["complete"]
    assert not observed_ranges(data,[output.replace("漢字","字漢",1)])["complete"]
    page(data,0,6); first=capsys.readouterr().out
    page(data,len(data)-6); last=capsys.readouterr().out
    assert not observed_ranges(data,[first,last])["complete"]


def test_truncation_only_rereads_the_missing_bytes(capsys):
    data = b'first-middle-last'
    page(data)
    whole = capsys.readouterr().out
    truncated = whole.replace('middle', '[tool output truncated]')
    observed = observed_ranges(data, [truncated])
    assert observed['missing_ranges'] == [(6, 12)]
    page(data, 6, 12, page_index=2)
    assert observed_ranges(data, [truncated, capsys.readouterr().out])['complete']


def test_workflow_route_collision_is_rejected(case):
    p, _, _ = case
    p['resources']['child']['path'] = '/export'
    with pytest.raises(V1Error) as caught:
        c.validate(p)
    assert caught.value.code == 'INVALID_VALUE'


def test_contract_cannot_accept_unauthorized_or_nonatomic_routes(case):
    p, d, _ = case
    for identity, property_name in [('api.related.batch', 'transaction'), ('api.related.parent.create', 'authorization')]:
        catalog = json.loads(c.CATALOG.read_text())
        catalog['blocks'][identity][property_name] = None
        with pytest.raises(V1Error) as caught:
            c.validate_plan(p, d, catalog)
        assert caught.value.code == 'INVALID_COMPOSITION'


def test_library_bytes_are_bound_to_the_contract(case):
    _, _, library = case
    c.validate_library(library)
    changed = deepcopy(library)
    changed['api.related.batch']['template'] += '\n# different bytes'
    with pytest.raises(V1Error) as caught:
        c.validate_library(changed)
    assert caught.value.code == 'INVALID_BLOCK'


def test_same_bytes_from_another_delivery_do_not_prove_a_retry_was_visible(capsys):
    data = b'{"files":{}}'
    page(data, delivery_id='first')
    output = capsys.readouterr().out
    assert observed_ranges(data, [output], 'first')['complete']
    assert not observed_ranges(data, [output], 'second')['complete']
