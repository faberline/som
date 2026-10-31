#!/usr/bin/env python3
"""Prove one historical public checked DSL on GitHub. No product acceptance."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType

import reconstruct_public_inputs as replay
from export_goldens import Refusal, digest_text, no_symlinks, parse, pointer, require, sha, value_sha

RECIPE_FILE = "som-rs/scripts/hosted_readiness/inputs/quality-checked-recipe-v1.json"
RECIPE_SHA256 = "64429c01d823eafdad4439b75a45664316539ff57b25d3cbbf2bba8c475d2f2a"
GOLDENS_FILE = "som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json"
INPUT_FILE = "som-core/data/flow-v7-r10/dev.jsonl"
BLOCKS_DIRECTORY = "som-code-python/data/blocks/flow-v7-compact"
EXPECTED_PROMPT_SHA256 = "964c0f77e772a69ce62a9b6d823b4f42e3da5d5d3ae8d14ebbdc4dd846b0b030"
EXPECTED_DSL_SHA256 = "a8c192e00af95355ab9cdc8dafd444f386caf6116a1c298a44326fa242b7e34a"
COHORT = {"name": "python_legacy", "id": "m2-alternative-checked", "index": 531,
          "family": "quality218", "group": "m2-quality218", "partition": "alternative-plans",
          "assembly_variant": "checked", "behavior_expected": False, "preserve_named_row": True}
MODULE_PREFIX = "som-core/src/"
MODULE_FILES = frozenset({MODULE_PREFIX + "som_core/" + name + ".py" for name in
                          ("__init__", "flow", "composed", "related", "v1", "business",
                           "flow_compact", "flow_codec", "quality", "compact_names")})
DATA_FILES = frozenset({INPUT_FILE, BLOCKS_DIRECTORY + "/blocks.json",
                        "som-code-python/data/block-contracts/flow-v7.json",
                        "som-code-python/data/block-contracts/flow-v7-compact.json",
                        "som-rs/contracts/related-flow.v1.md", "som-rs/contracts/related-business.v1.md"})
ANCHORS = {"som-rs/evidence/rust-compiler-acceptance-2026-10-14.json": "/gate_receipts/legacy544/sha256",
           "som-rs/evidence/rust-output-acceptance-2026-10-16.json": "/results/python_replay/inputs/legacy_result_sha256"}


class ProofSources:
    def __init__(self, repo):
        self.repo = repo
        file = repo / RECIPE_FILE
        no_symlinks(file)
        self.raw = file.read_bytes()
        require(sha(self.raw) == RECIPE_SHA256, "checked recipe digest differs")
        recipe = parse(self.raw)
        require(recipe["version"] == "som.public-checked-dsl-recipe.v1"
                and recipe["product_acceptance_passed"] is False and type(recipe["case_count"]) is int
                and recipe["case_count"] == 1 and recipe["public_repository"] == "faberline/som"
                and recipe["public_source_revision"] == replay.PUBLIC_REVISION and recipe["cohort"] == COHORT,
                "checked recipe identity or scope differs")
        self.recipe, self.pins = recipe, {}
        for key, expected in (("public_runtime_module_pins", MODULE_FILES), ("public_data_pins", DATA_FILES)):
            rows = recipe[key]
            require(len(rows) == len(expected) and {row["path"] for row in rows} == expected,
                    "checked recipe source scope differs")
            for row in rows:
                require(row["path"] not in self.pins and row["revision"] == replay.PUBLIC_REVISION,
                        "checked recipe source identity differs")
                digest = digest_text(row["raw_sha256"])
                if row["path"] in MODULE_FILES:
                    require(digest == replay.RUNTIME_PINS[row["path"]], "checked recipe module pin differs")
                self.pins[row["path"]] = digest
        input_recipe, call = recipe["input"], recipe["recipe"]
        require(input_recipe["path"] == INPUT_FILE and type(input_recipe["line_1based"]) is int
                and input_recipe["line_1based"] == 1 and input_recipe["pointer"] == "/prompt"
                and input_recipe["raw_sha256"] == self.pins[INPUT_FILE]
                and input_recipe["prompt_sha256"] == EXPECTED_PROMPT_SHA256,
                "checked recipe public input selector differs")
        require(call["call"] == 'som_core.flow_compact.reference(prompt, blocks, "checked")'
                and call["function_path"] == "som-core/src/som_core/flow_compact.py" and call["function"] == "reference"
                and call["blocks_loader"] == "som_core.v1.load_blocks" and call["blocks_directory"] == BLOCKS_DIRECTORY
                and call["exact_recursive_json_members"] == ["blocks.json"]
                and call["canonical_dsl_sha256"] == EXPECTED_DSL_SHA256
                and call["validation_call"] == "som_core.flow.validate_plan(prompt, dsl, som_core.flow_compact.metadata())"
                and call["new_module_dependencies"] == [], "checked recipe operation differs")
        chain = recipe["accepted_hash_chain"]
        require(chain["legacy_result"]["raw_sha256"] == replay.LEGACY_RESULT_SHA256
                and chain["legacy_inventory"]["checked_prevalidation_dsl_sha256"] == EXPECTED_DSL_SHA256
                and chain["accepted_variant_bundle"]["canonical_dsl_sha256"] == EXPECTED_DSL_SHA256
                and chain["accepted_variant_bundle"]["prompt_sha256"] == EXPECTED_PROMPT_SHA256,
                "accepted checked DSL anchor chain differs")
        anchor_rows = chain["public_anchors"]
        require(len(anchor_rows) == len(ANCHORS) and {row["path"] for row in anchor_rows} == set(ANCHORS),
                "accepted public anchor scope differs")
        self.anchors = {}
        for row in anchor_rows:
            require(row["revision"] == replay.PUBLIC_REVISION and row["accepted_result_pointer"] == ANCHORS[row["path"]]
                    and row["accepted_result_raw_sha256"] == replay.LEGACY_RESULT_SHA256,
                    "accepted public anchor binding differs")
            self.anchors[row["path"]] = digest_text(row["raw_sha256"])
        self.verify()
        self.verify_goldens()

    def read(self, path, expected):
        file = self.repo / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == expected, "checked recipe public file digest differs")
        return raw

    def members(self):
        directory = self.repo / BLOCKS_DIRECTORY
        no_symlinks(directory)
        found = []
        for root, directories, files in os.walk(directory, followlinks=False):
            for name in [*directories, *files]:
                file = Path(root) / name
                no_symlinks(file)
                if file.name.endswith(".json"):
                    require(file.is_file(), "checked blocks JSON member is not regular")
                    found.append(file.relative_to(directory).as_posix())
        require(sorted(found) == ["blocks.json"], "checked blocks recursive JSON membership differs")
        return sorted(found)

    def verify_goldens(self):
        saved = self.recipe["accepted_hash_chain"]["sealed_public_goldens"]
        require(saved["path"] == GOLDENS_FILE and saved["raw_sha256"] == replay.GOLDENS_SHA256
                and saved["case_pointer"] == "/cohorts/python_legacy/531", "checked golden locator differs")
        goldens = replay.load_goldens(self.repo / GOLDENS_FILE)
        row = pointer(goldens, saved["case_pointer"])
        require(all(row[key] == COHORT[key] for key in ("id", "index", "family", "group", "partition", "behavior_expected"))
                and row["variant"] == COHORT["assembly_variant"] and row["prompt_sha256"] == EXPECTED_PROMPT_SHA256
                and row["files_map_sha256"] == saved["files_map_sha256"]
                and row["checked_dsl_provenance"] == "public_reference_recipe_unrun", "checked frozen named row differs")

    def verify(self):
        self.read(RECIPE_FILE, RECIPE_SHA256)
        for path, expected in self.pins.items():
            self.read(path, expected)
        for path, expected in self.anchors.items():
            document = parse(self.read(path, expected))
            require(pointer(document, ANCHORS[path]) == replay.LEGACY_RESULT_SHA256,
                    "accepted public anchor value differs")
        self.members()


@contextmanager
def checked_runtime(sources):
    """Use the reviewed source loader for only the ten closed core modules."""
    sources.verify()
    loader = replay.PinnedSourceModules(sources)
    loader.modules = {name: path for name, path in loader.modules.items() if path in MODULE_FILES}
    loader.packages &= set(loader.modules)
    require(len(loader.modules) == 10 and not any(loader.handles(name) for name in sys.modules),
            "checked core module scope differs or is already loaded")
    before, old_bytecode = set(sys.modules), sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.meta_path.insert(0, loader)
    def check_loaded():
        loaded = {name for name in set(sys.modules) - before if loader.handles(name)}
        require(loaded == set(loader.modules), "checked core import closure differs")
        for name in loaded:
            module = sys.modules[name]
            require(module.__spec__.loader is loader and module.__cached__ is None
                    and Path(module.__file__).resolve(strict=True) == sources.repo / loader.modules[name],
                    "checked core module did not use verified source loader")
        return sorted(loaded)
    try:
        compact = importlib.import_module("som_core.flow_compact")
        flow = importlib.import_module("som_core.flow")
        v1 = importlib.import_module("som_core.v1")
        yield compact, flow, v1, check_loaded
        check_loaded()
        sources.verify()
    finally:
        sys.meta_path.remove(loader)
        sys.dont_write_bytecode = old_bytecode
        for name in set(sys.modules) - before:
            if loader.handles(name):
                del sys.modules[name]


_NATIVE_CHECKED_ROLES = {'_som_active_semantic_common_postclose_join_prove_checked_reference': {'som_core': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core',
                                                                        'som_core.business': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.business',
                                                                        'som_core.compact_names': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.compact-names',
                                                                        'som_core.composed': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.composed',
                                                                        'som_core.flow': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow',
                                                                        'som_core.flow_codec': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow-codec',
                                                                        'som_core.flow_compact': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow-compact',
                                                                        'som_core.quality': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.quality',
                                                                        'som_core.related': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.related',
                                                                        'som_core.v1': 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.v1'},
 '_som_active_semantic_common_runtime_join_prove_checked_reference': {'som_core': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core',
                                                                      'som_core.business': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.business',
                                                                      'som_core.compact_names': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.compact-names',
                                                                      'som_core.composed': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.composed',
                                                                      'som_core.flow': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.flow',
                                                                      'som_core.flow_codec': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.flow-codec',
                                                                      'som_core.flow_compact': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.flow-compact',
                                                                      'som_core.quality': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.quality',
                                                                      'som_core.related': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.related',
                                                                      'som_core.v1': 'semantic.common-runtime.join.prove-checked-reference.recipe.som-core.v1'},
 '_som_active_semantic_common_verify_join_prove_checked_reference': {'som_core': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core',
                                                                     'som_core.business': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.business',
                                                                     'som_core.compact_names': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.compact-names',
                                                                     'som_core.composed': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.composed',
                                                                     'som_core.flow': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.flow',
                                                                     'som_core.flow_codec': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.flow-codec',
                                                                     'som_core.flow_compact': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.flow-compact',
                                                                     'som_core.quality': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.quality',
                                                                     'som_core.related': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.related',
                                                                     'som_core.v1': 'semantic.common-verify.join.prove-checked-reference.recipe.som-core.v1'},
 '_som_active_semantic_entry_join_prove_checked_reference': {'som_core': 'semantic.entry.join.prove-checked-reference.recipe.som-core',
                                                             'som_core.business': 'semantic.entry.join.prove-checked-reference.recipe.som-core.business',
                                                             'som_core.compact_names': 'semantic.entry.join.prove-checked-reference.recipe.som-core.compact-names',
                                                             'som_core.composed': 'semantic.entry.join.prove-checked-reference.recipe.som-core.composed',
                                                             'som_core.flow': 'semantic.entry.join.prove-checked-reference.recipe.som-core.flow',
                                                             'som_core.flow_codec': 'semantic.entry.join.prove-checked-reference.recipe.som-core.flow-codec',
                                                             'som_core.flow_compact': 'semantic.entry.join.prove-checked-reference.recipe.som-core.flow-compact',
                                                             'som_core.quality': 'semantic.entry.join.prove-checked-reference.recipe.som-core.quality',
                                                             'som_core.related': 'semantic.entry.join.prove-checked-reference.recipe.som-core.related',
                                                             'som_core.v1': 'semantic.entry.join.prove-checked-reference.recipe.som-core.v1'},
 '_som_active_semantic_graph_join_prove_checked_reference': {'som_core': 'semantic.graph.join.prove-checked-reference.recipe.som-core',
                                                             'som_core.business': 'semantic.graph.join.prove-checked-reference.recipe.som-core.business',
                                                             'som_core.compact_names': 'semantic.graph.join.prove-checked-reference.recipe.som-core.compact-names',
                                                             'som_core.composed': 'semantic.graph.join.prove-checked-reference.recipe.som-core.composed',
                                                             'som_core.flow': 'semantic.graph.join.prove-checked-reference.recipe.som-core.flow',
                                                             'som_core.flow_codec': 'semantic.graph.join.prove-checked-reference.recipe.som-core.flow-codec',
                                                             'som_core.flow_compact': 'semantic.graph.join.prove-checked-reference.recipe.som-core.flow-compact',
                                                             'som_core.quality': 'semantic.graph.join.prove-checked-reference.recipe.som-core.quality',
                                                             'som_core.related': 'semantic.graph.join.prove-checked-reference.recipe.som-core.related',
                                                             'som_core.v1': 'semantic.graph.join.prove-checked-reference.recipe.som-core.v1'}}


class _NativeCheckedInputs:
    def __init__(self, repo, pins):
        self.repo, self.pins = repo, dict(pins)


def _checked_native_mode():
    if __name__ in {'prove_checked_reference', '__main__'} and '__som_native_active_controls__' not in globals():
        return False
    require(type(__name__) is str and type(_NATIVE_CHECKED_ROLES) is dict
            and __name__ in _NATIVE_CHECKED_ROLES, 'unlisted native checked recipe namespace')
    require('__som_native_active_controls__' in globals(), 'native checked controls binding is absent')
    return True


def _native_checked_controls():
    require(_checked_native_mode(), 'native checked recipe controls required')
    try:
        from _som_native_active_controls_binding import controls
    except ImportError as error:
        raise Refusal('actual Core checked recipe import binding required') from error
    controls.verify_installed()
    targets = _NATIVE_CHECKED_ROLES[__name__]
    expected = {'som_core' if path.endswith('/__init__.py') else path[len(MODULE_PREFIX):-3].replace('/', '.'):
                path for path in MODULE_FILES}
    require(type(targets) is dict and set(targets) == set(expected) and len(targets) == 10
            and all(type(role) is str and controls.role_sources.get(role) == expected[name] for name, role in targets.items()),
            'fixed native checked module/source universe differs')
    return controls, targets, expected


@contextmanager
def _native_checked_runtime(sources):
    controls, targets, expected = _native_checked_controls()
    require(type(sources) is ProofSources, 'actual source-owned native checked inputs required')
    fields = object.__getattribute__(sources, '__dict__')
    require(type(fields) is dict and set(fields) == {'repo', 'raw', 'recipe', 'pins', 'anchors'}
            and type(fields['repo']) is type(controls.root) and fields['repo'] == controls.root
            and type(fields['raw']) is bytes and type(fields['recipe']) is dict
            and all(type(fields[name]) is dict and all(type(key) is str and type(value) is str
                    for key, value in fields[name].items()) for name in ('pins', 'anchors')),
            'native checked source data fields differ')
    sources.verify()
    typed_sources = _NativeCheckedInputs(sources.repo, {path: sources.pins[path] for path in MODULE_FILES})
    lease = controls.borrow(); primary = None; modules = {}
    def check_loaded():
        lease.verify_installed()
        for name, module in modules.items():
            path = expected[name]
            require(type(module) is ModuleType and module.__file__ == str(sources.repo / path)
                    and module.__cached__ is None and module.__som_raw_sha256__ == sources.pins[path]
                    and sys.modules.get(module.__name__) is module,
                    'checked Core module did not keep its actual source identity')
        require(set(modules) == set(expected), 'checked Core import closure differs')
        sources.verify()
        return sorted(modules)
    try:
        modules['som_core.flow_compact'] = lease.load_module(targets['som_core.flow_compact'])
        for name in sorted(targets):
            if name not in modules: modules[name] = lease.load_module(targets[name])
        check_loaded()
        yield modules['som_core.flow_compact'], modules['som_core.flow'], modules['som_core.v1'], check_loaded
    except BaseException as error:
        primary = error
        raise
    finally:
        secondary = None
        try:
            if modules: check_loaded()
            else: lease.verify_installed(); sources.verify()
        except BaseException as error:
            secondary = error
        try:
            lease.close()
        except BaseException as error:
            if secondary is None: secondary = error
            else: BaseException.__setattr__(secondary, '__som_checked_close_error__', error)
        if secondary is not None:
            if primary is None: raise secondary
            BaseException.__setattr__(primary, '__som_native_checked_postcheck_error__', secondary)


def _checked_recipe(sources):
    if not _checked_native_mode():
        return checked_runtime(sources)
    return _native_checked_runtime(sources)


def prove(repo, out, tested_sha):
    require(re.fullmatch(r"[0-9a-f]{40}", tested_sha) is not None, "tested commit must be an exact Git SHA")
    require(not out.exists() and out != repo and repo not in out.parents, "proof output must be new and outside the checkout")
    no_symlinks(out)
    if _checked_native_mode():
        controls, _, _ = _native_checked_controls()
        require(type(repo) is type(controls.root) and repo == controls.root
                and tested_sha == controls.helper_revision == controls.candidate_revision,
                'native checked source/revision differs before ProofSources construction')
    sources = ProofSources(repo)
    prompt = parse(sources.read(INPUT_FILE, sources.pins[INPUT_FILE]).decode("utf-8").splitlines()[0])["prompt"]
    require(value_sha(prompt) == EXPECTED_PROMPT_SHA256, "checked public prompt digest differs")
    with _checked_recipe(sources) as (compact, flow, v1, check_loaded):
        blocks = v1.load_blocks(repo / BLOCKS_DIRECTORY)
        dsl = compact.reference(prompt, blocks, "checked")
        require(value_sha(dsl) == EXPECTED_DSL_SHA256, "generated checked DSL differs from accepted canonical digest")
        flow.validate_plan(prompt, dsl, compact.metadata())
        require(value_sha(dsl) == EXPECTED_DSL_SHA256 and value_sha(prompt) == EXPECTED_PROMPT_SHA256,
                "checked semantic validation changed a frozen value")
        loaded = check_loaded()
    sources.verify()
    sources.verify_goldens()
    raw = (json.dumps(dsl, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    out.mkdir(parents=True, exist_ok=False)
    with (out / "checked.dsl.json").open("xb") as stream:
        stream.write(raw)
    report = {"version": "som.checked-reference-proof.v1", "tested_sha": tested_sha, "case_count": 1,
              "state": "one public historical checked DSL reconstructed and validated; product gates remain unrun",
              "product_acceptance_passed": False, "full_input_set_ready": False, "product_suites": "unrun",
              "behavior_gates": "unrun", "raw_compile_reply_comparison": "unrun",
              "rust_input_scope": "not evaluated; this proof covers one Python legacy checked DSL only",
              "cohort": COHORT, "recipe": {"path": RECIPE_FILE, "raw_sha256": RECIPE_SHA256},
              "public_source_revision": replay.PUBLIC_REVISION, "loaded_core_modules": loaded,
              "module_and_data_pins": sources.pins, "public_anchor_pins": sources.anchors,
              "recursive_blocks_json_members": sources.members(), "source_checks_before_and_after": "passed",
              "prompt_sha256": EXPECTED_PROMPT_SHA256, "accepted_canonical_dsl_sha256": EXPECTED_DSL_SHA256,
              "generated_canonical_dsl_sha256": value_sha(dsl), "canonical_dsl_equal": True,
              "semantic_validation": "passed", "checked_dsl": {"path": "checked.dsl.json", "raw_sha256": sha(raw),
                                                                         "canonical_sha256": value_sha(dsl)},
              "current_case_checked_dsl_state": "public_reference_recipe_verified",
              "historical_case_checked_dsl_provenance": "public_reference_recipe_unrun"}
    with (out / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tested-sha", required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "proof output traversal refused")
        report = prove(args.repo.resolve(strict=True), args.out.absolute(), args.tested_sha)
        print(json.dumps({"state": report["state"], "case_count": 1, "canonical_dsl_equal": True,
                          "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError, AttributeError) as error:
        print("refused: " + (str(error) if isinstance(error, Refusal) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
