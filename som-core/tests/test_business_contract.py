"""Contract rejection and every candidate block's parameter boundary."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from som_core.v1 import (
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "som-core/data/business-service"


def test_business_prompt_rejections():
    base = json.loads((DATA / "requirements.jsonl").read_text().splitlines()[0])["prompt"]
    mutations = [
        lambda p: p["capabilities"].update(auth="bypass"),
        lambda p: p["capabilities"].update(database="sqlite"),
        lambda p: p["capabilities"].update(storage="filesystem"),
        lambda p: p["capabilities"].update(workflow="eval"),
        lambda p: p["capabilities"].update(value_field="title"),
        lambda p: p["capabilities"].update(group_field="title"),
        lambda p: p["capabilities"].update(workflow="statistics", value_field="title"),
        lambda p: p["capabilities"].update(secret="not-a-prompt-input"),
        lambda p: p["resources"]["child"].update(path="/export"),
        lambda p: p["resources"]["parent"].update(path="/auth/me"),
        lambda p: p["resources"]["child"]["fields"][0].update(name="model_dump"),
    ]
    cases = []
    for change in mutations:
        prompt = deepcopy(base)
        change(prompt)
        with pytest.raises(V1Error) as error:
            validate_prompt(prompt)
        cases.append({"prompt": prompt, "code": error.value.code})
    # This file is a parity fixture, not a training or checkpoint input.
    assert all(case["code"] in {"INVALID_VALUE", "INVALID_SCHEMA"} for case in cases)


def test_all_new_blocks_have_positive_compositions_and_reject_extra_parameters():
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/v5")
    old = load_blocks(ROOT / "som-code-python/data/blocks/v4")
    assert all(blocks[key] == value for key, value in old.items())
    templates = json.loads((DATA / "templates.json").read_text())
    # Use validated resource samples to render each static template variant.
    # Structure varies by backend, query, auth, storage and workflow.
    import sys
    sys.path.insert(0, str(ROOT / "som-core/scripts"))
    from build_business_corpus import add_caps
    from build_related_corpus import case, date, field, flag, resource
    from som_core.related import document
    seen = set()
    for key in templates:
        pieces = key.split(".")
        p = case(0, 0, 999001)
        for role, kind in zip(("parent", "child"), pieces[3:5], strict=True):
            fields = [field("name"), field("amount", "float", False)]
            if kind != "plain":
                fields.append(flag("active"))
            if kind == "date":
                fields.append(date("deadline"))
            p["resources"][role] = resource(role.title(), role + "_records", fields)
        if len(pieces) > 5:
            assert add_caps(p, *pieces[5:])
        resolved = resolve_prompt_refs(p, document(p, blocks))
        assemble(resolved, blocks)
        for f in resolved["files"]:
            for b in f["blocks"]:
                if b["id"] in seen or b["id"] in old:
                    continue
                seen.add(b["id"])
                bad = deepcopy(resolved)
                target = next(item for file in bad["files"] for item in file["blocks"] if item["id"] == b["id"])
                target["params"]["unapproved_source"] = "print('not allowed')"
                with pytest.raises(V1Error):
                    assemble(bad, blocks)
    assert seen == set(blocks) - set(old)
