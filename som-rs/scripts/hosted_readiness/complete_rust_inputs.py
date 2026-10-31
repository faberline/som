#!/usr/bin/env python3
"""Complete public Rust requests on GitHub. No parser bypass or product acceptance.

Nine typed companions use only pinned public examples plus explicit JSON edits.
Five CNL companions use the existing source-pinned serializer. Every canonical
prompt must match its frozen hash. Every baseline JSON request must match the
independently sealed TS065 full raw request hash. Native RS bytes stay separate.
Compiler semantic checks and all product gates remain unrun.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType

import map_rust_inputs as mapper
import reconstruct_public_inputs as replay
from export_goldens import Refusal, case_id, digest_text, no_symlinks, parse, relative, require, sha, value_sha

BASELINE_FILE = "som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json"
BASELINE_SHA256 = "650c6d758519b92dc6bcc11678aa14e9942612a9ed0f1fd35202876065c425a9"
BUILDER = "som-rs/tests/rust-output-v1/build_negative_cases.py"
WRITER = "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py"
EXAMPLES = "som-rs/contracts/compiler-v1/examples/"
EXTRA_PINS = {
    BUILDER: "6c0d9313452a8e44f93b981babadf3b7ab433a96d3d9020e56f1004a62f7c7c7",
    WRITER: "9a4608a53f271697e5279018825f2a045d032595eaacc5748ef8d1637a9c52ce",
    replay.SERIALIZER: "bfb8eca480c08ee16c1a8bb42aae4a8a10f947ed0ca9c9c5521c75d09809632d",
    replay.STATEMENT_MAP: "76744262ece22db17c61683b206beaab67105245888719f9e86c2c84e8283be2",
}
CAPABILITIES = {"database": "postgresql", "auth": "jwt", "storage": "none", "workflow": "json_export",
                "value_field": None, "group_field": None}
# These are authoring edits from build_negative_cases.py's public CNL recipes.
# They construct test inputs; they never change compiler validation.
TYPED_RECIPES = {
    "RS07_CNL_FIELD_SELF": ("v3", [("replace", "/resource/fields/0/name", "self")]),
    "RS09_CNL_PATH_TRAILING_SLASH": ("v1", [("replace", "/resource/path", "/items/")]),
    "RS11_CNL_PATH_HEALTHZ": ("v4", [("replace", "/resource/path", "/healthz")]),
    "RS12_CNL_V3_FILTER_LIMIT": ("v3", [("replace", "/resource/fields/2/name", "limit"),
                                         ("replace", "/query/filter_field", "limit")]),
    "RS14_CNL_V5_FOREIGN_KEY_SELF": ("v5", [("replace", "/relation/foreign_key", "self")]),
    "RS16_CNL_V5_FIRST_REFUSAL": ("v5", [("replace", "/resources/child/fields/0/name", "a__b"),
                                       ("replace", "/relation/foreign_key", "self")]),
    "RS17_CNL_PATH_BEFORE_FIELD": ("v1", [("replace", "/resource/path", "/items/"),
                                         ("replace", "/resource/fields/0/name", "Title")]),
    "RS20_CNL_V5_CAPABILITIES_FIRST": ("v5", [("replace", "/resources/child/fields/0/name", "a__b"),
                                             ("add", "/capabilities", CAPABILITIES)]),
    "RS21_CNL_TABLE_SQLX_MIGRATIONS": ("v1", [("replace", "/resource/table", "_sqlx_migrations")]),
}


class Sources(mapper.Public):
    def __init__(self, repo, goldens):
        super().__init__(repo, goldens)
        for path, digest in EXTRA_PINS.items():
            require(path not in self.pins or self.pins[path] == digest, "companion source pins conflict")
            self.pins[path] = digest_text(digest)
        self.verify()

    def locator(self, path, pointer):
        return {"path": path, "source_file_sha256": self.pins[path], "symbol" if path.endswith(".py") else "pointer": pointer}


def apply_edit(prompt, operation, pointer, value):
    require(operation in {"add", "replace"} and pointer.startswith("/"), "invalid public authoring edit")
    parts = pointer[1:].split("/")
    target = prompt
    for key in parts[:-1]:
        if isinstance(target, list):
            require(key.isdecimal() and str(int(key)) == key, "invalid authoring array index")
            target = target[int(key)]
        else:
            require(isinstance(target, dict) and key in target, "authoring pointer is absent")
            target = target[key]
    key = parts[-1]
    require(isinstance(target, dict) and ((key in target) if operation == "replace" else (key not in target)),
            "authoring edit does not match public base structure")
    target[key] = deepcopy(value)


def typed_companion(identity, expected, sources, negative):
    require(identity in TYPED_RECIPES and negative["id"] == identity and negative["form"] == "cnl",
            "unexpected typed companion case")
    base, edits = TYPED_RECIPES[identity]
    require(negative["base_example"] == base + ".cnl", "public CNL recipe base differs")
    path = EXAMPLES + base + ".prompt.json"
    prompt = deepcopy(parse(sources.raw(path)))
    for operation, pointer, value in edits:
        apply_edit(prompt, operation, pointer, value)
    require(value_sha(prompt) == digest_text(expected), "constructed typed companion differs from frozen prompt " + case_id(identity))
    return prompt, {"kind": "public example plus explicit JSON-pointer edits",
                    "base": sources.locator(path, ""), "recipe": sources.locator(BUILDER, "build"),
                    "mirrored_cnl_case": identity,
                    "json_edits": [{"op": operation, "pointer": pointer, "value": value} for operation, pointer, value in edits]}


@contextmanager
def serializer_runtime(sources):
    """Reuse the existing loader for exactly one pinned source module."""
    sources.verify()
    require(sources.pins[replay.SERIALIZER] == replay.RUNTIME_PINS[replay.SERIALIZER]
            and sources.pins[replay.STATEMENT_MAP] == replay.RUNTIME_PINS[replay.STATEMENT_MAP],
            "independent serializer pins differ")
    loader = replay.PinnedSourceModules(sources)
    loader.modules = {"som_hosted_serializer": replay.SERIALIZER}
    loader.packages = set()
    require(not any(loader.handles(name) for name in sys.modules), "recipe module is already loaded")
    old_bytecode, before = sys.dont_write_bytecode, set(sys.modules)
    sys.dont_write_bytecode = True
    sys.meta_path.insert(0, loader)
    try:
        module = importlib.import_module("som_hosted_serializer")
        require(module.__spec__.loader is loader and module.__cached__ is None, "serializer did not use pinned source")
        yield module.serialize_prompt
        loaded = {name for name in set(sys.modules) - before if loader.handles(name)}
        require(loaded == {"som_hosted_serializer"}, "serializer import closure differs")
        sources.verify()
    finally:
        sys.meta_path.remove(loader)
        sys.dont_write_bytecode = old_bytecode
        for name in set(sys.modules) - before:
            if loader.handles(name):
                del sys.modules[name]


_NATIVE_SERIALIZER_ROLES = {'_som_active_cnl_entry_helper_complete_rust_inputs': {'module': '_som_active_cnl_entry_helper_complete_rust_inputs_recipe_serializer',
                                                       'serializer': 'cnl.entry.helper.complete-rust-inputs.recipe.serializer'},
 '_som_active_companions_entry_capture_complete_rust_inputs': {'module': '_som_active_companions_entry_capture_complete_rust_inputs_recipe_serializer',
                                                               'serializer': 'companions.entry.capture.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_capture_complete': {'module': '_som_active_semantic_capture_complete_recipe_serializer',
                                           'serializer': 'semantic.capture.complete.recipe.serializer'},
 '_som_active_semantic_capture_driver_imports_complete_rust_inputs': {'module': '_som_active_semantic_capture_driver_imports_complete_rust_inputs_recipe_serializer',
                                                                      'serializer': 'semantic.capture.driver.imports.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_common_postclose_join_complete_rust_inputs': {'module': '_som_active_semantic_common_postclose_join_complete_rust_inputs_recipe_serializer',
                                                                     'serializer': 'semantic.common-postclose.join.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_common_runtime_join_complete_rust_inputs': {'module': '_som_active_semantic_common_runtime_join_complete_rust_inputs_recipe_serializer',
                                                                   'serializer': 'semantic.common-runtime.join.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_common_verify_join_complete_rust_inputs': {'module': '_som_active_semantic_common_verify_join_complete_rust_inputs_recipe_serializer',
                                                                  'serializer': 'semantic.common-verify.join.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_entry_join_complete_rust_inputs': {'module': '_som_active_semantic_entry_join_complete_rust_inputs_recipe_serializer',
                                                          'serializer': 'semantic.entry.join.complete-rust-inputs.recipe.serializer'},
 '_som_active_semantic_graph_join_complete_rust_inputs': {'module': '_som_active_semantic_graph_join_complete_rust_inputs_recipe_serializer',
                                                          'serializer': 'semantic.graph.join.complete-rust-inputs.recipe.serializer'}}


def _serializer_native_mode():
    if __name__ in {'complete_rust_inputs', '__main__'} and '__som_native_active_controls__' not in globals():
        return False
    require(type(__name__) is str and type(_NATIVE_SERIALIZER_ROLES) is dict
            and __name__ in _NATIVE_SERIALIZER_ROLES, 'unlisted native companion serializer namespace')
    require('__som_native_active_controls__' in globals(), 'native companion controls binding is absent')
    return True


def _native_serializer_controls():
    require(_serializer_native_mode(), 'native companion serializer controls required')
    try:
        from _som_native_active_controls_binding import controls
    except ImportError as error:
        raise Refusal('actual Core companion serializer import binding required') from error
    controls.verify_installed()
    target = _NATIVE_SERIALIZER_ROLES[__name__]
    require(type(target) is dict and set(target) == {'serializer', 'module'}
            and all(type(value) is str for value in target.values())
            and controls.role_sources.get(target['serializer']) == replay.SERIALIZER,
            'fixed companion serializer role/source differs')
    return controls, target


@contextmanager
def _native_serializer_runtime(sources):
    controls, target = _native_serializer_controls()
    require(type(sources) is Sources and type(sources.repo) is type(controls.root) and sources.repo == controls.root,
            'actual source-owned native companion inputs required')
    sources.verify()
    require(sources.pins[replay.SERIALIZER] == replay.RUNTIME_PINS[replay.SERIALIZER]
            and sources.pins[replay.STATEMENT_MAP] == replay.RUNTIME_PINS[replay.STATEMENT_MAP],
            'independent serializer pins differ')
    lease = controls.borrow(); primary = None
    try:
        module = lease.load_module(target['serializer'])
        require(type(module) is ModuleType and module.__name__ == target['module']
                and module.__file__ == str(sources.repo / replay.SERIALIZER) and module.__cached__ is None
                and module.__som_raw_sha256__ == sources.pins[replay.SERIALIZER],
                'actual Core companion serializer identity differs')
        lease.verify_installed()
        yield module.serialize_prompt
    except BaseException as error:
        primary = error
        raise
    finally:
        secondary = None
        try:
            lease.verify_installed(); sources.verify()
        except BaseException as error:
            secondary = error
        try:
            lease.close()
        except BaseException as error:
            if secondary is None: secondary = error
            else: BaseException.__setattr__(secondary, '__som_serializer_close_error__', error)
        if secondary is not None:
            if primary is None: raise secondary
            BaseException.__setattr__(primary, '__som_native_serializer_postcheck_error__', secondary)


def _serializer_recipe(sources):
    if not _serializer_native_mode():
        return serializer_runtime(sources)
    return _native_serializer_runtime(sources)


def sorted_objects(value):
    if isinstance(value, dict):
        return {key: sorted_objects(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [sorted_objects(item) for item in value]
    return value


class Baseline:
    def __init__(self, repo, expected_ids):
        self.path = repo / BASELINE_FILE
        self.raw = self.read()
        document = parse(self.raw)
        require(document["version"] == "som.ts065-accepted-response-hashes.v1"
                and document["public_repository"] == "faberline/som"
                and document["public_source_revision"] == mapper.PUBLIC_REVISION
                and document["product_acceptance_passed"] is False, "accepted baseline artifact identity differs")
        rows = document["baselines"]["rust"]["rows"]
        require(len(rows) == 2093 and dict(Counter(row["check"] for row in rows)) ==
                {"compile_equal": 699, "baseline_bundle_refused": 697, "rebased_files": 697}, "accepted Rust baseline scope differs")
        self.rows = {}
        for row in rows:
            if row["check"] != "compile_equal":
                continue
            key = (row["phase"], case_id(row["id"]))
            require(key not in self.rows and row["passed"] is True and row["status"] == "passed", "accepted compile row is duplicate or unpassed")
            require(row["qa_request_sha256"] == row["baseline_request_sha256"] == row["request_sha256"], "accepted compile request pins conflict")
            digest_text(row["qa_request_sha256"])
            self.rows[key] = row
        require(set(self.rows) == expected_ids, "accepted Rust baseline named scope differs")

    def read(self):
        no_symlinks(self.path)
        raw = self.path.read_bytes()
        require(sha(raw) == BASELINE_SHA256, "accepted TS065 baseline artifact digest differs")
        return raw

    def request(self, phase, identity, prompt, expected_status):
        row = self.rows[(phase, identity)]
        require(row["qa_status"] == row["baseline_status"] == expected_status, "accepted baseline HTTP status differs")
        sorted_prompt = sorted_objects(prompt)
        require(value_sha(sorted_prompt) == value_sha(prompt), "object ordering changed prompt values")
        # Exactly these two encodings are allowed. No value rewrite or hash mask.
        candidates = [("preserved-source-object-order", prompt), ("recursive-sorted-object-keys", sorted_prompt)]
        matches = [(name, mapper.compact({"format": "som.prompt-json.v1", "target": "rust", "prompt": candidate}))
                   for name, candidate in candidates]
        matches = [(name, raw) for name, raw in matches if sha(raw) == row["qa_request_sha256"]]
        require(matches, "accepted full Rust baseline request bytes differ for " + case_id(identity))
        name, raw = matches[0]
        return raw, {"artifact": BASELINE_FILE, "artifact_raw_sha256": BASELINE_SHA256,
                     "row_index": row["row_index"], "phase": phase, "id": identity, "check": "compile_equal",
                     "accepted_qa_request_sha256": row["qa_request_sha256"], "accepted_qa_status": row["qa_status"],
                     "accepted_qa_response_sha256": digest_text(row["qa_response_sha256"]),
                     "encoding": name, "header_order": ["format", "target", "prompt"]}


def request_bytes(stage, item):
    relative(item["path"])
    file = stage / item["path"]
    no_symlinks(file)
    raw = file.read_bytes()
    require(sha(raw) == digest_text(item["raw_sha256"]), "staged request bytes differ")
    return raw


def add_request(stage, row, label, raw, provenance):
    directory = stage / Path(next(item["path"] for item in row["requests"].values() if item)).parent
    item = mapper.write_request(directory, label, raw, provenance)
    item["path"] = (directory / item["path"]).relative_to(stage).as_posix()
    return item


def complete(repo, inputs, goldens, out, tested_sha):
    require(not out.exists() and out != repo and repo not in out.parents and out != inputs and inputs not in out.parents,
            "completion output must be new and outside source/input trees")
    no_symlinks(out)
    if _serializer_native_mode():
        controls, _ = _native_serializer_controls()
        require(type(repo) is type(controls.root) and repo == controls.root
                and tested_sha == controls.helper_revision == controls.candidate_revision,
                'native companion source/revision differs before Sources construction')
    out.parent.mkdir(parents=True, exist_ok=True)
    sources = Sources(repo, goldens)
    negative = {row["id"]: row for row in sources.document(mapper.NEGATIVE)["cases"] if row["id"].startswith("RS")}
    phase2 = {row["id"]: row for row in sources.document(mapper.P2)["rows"]}
    with tempfile.TemporaryDirectory(prefix="som-rust-inputs-", dir=out.parent) as temporary:
        stage = Path(temporary).resolve() / "mapped"
        report = mapper.map_inputs(repo, inputs, goldens, stage, tested_sha)
        rows = [row for cohort in ["rust_phase1", "rust_phase2"] for row in report["cohorts"][cohort]]
        expected_ids = {("phase1" if cohort == "rust_phase1" else "phase2", row["id"])
                        for cohort in ["rust_phase1", "rust_phase2"] for row in report["cohorts"][cohort]}
        baseline = Baseline(repo, expected_ids)
        missing_typed = [row for row in rows if row["requests"]["json"] is None]
        missing_cnl = [row for row in rows if row["requests"]["cnl"] is None]
        require(len(missing_typed) == 9 and {row["id"] for row in missing_typed} == set(TYPED_RECIPES)
                and len(missing_cnl) == 5, "companion completion scope differs")
        for row in missing_typed:
            prompt, provenance = typed_companion(row["id"], row["expected_prompt_sha256"], sources, negative[row["id"]])
            row["requests"]["json"] = add_request(stage, row, "json", mapper.compact({"format": "som.prompt-json.v1", "prompt": prompt, "target": "rust"}), provenance)
            row["canonical_typed_prompt_sha256"] = value_sha(prompt)
            row["typed_prompt_proof"] = "public example and explicit authoring edits matched frozen canonical prompt hash; CNL compiler semantics unrun"
        with _serializer_recipe(sources) as serialize:
            statement_map = sources.document(replay.STATEMENT_MAP)
            for row in missing_cnl:
                prompt = parse(request_bytes(stage, row["requests"]["json"]))["prompt"]
                require(value_sha(prompt) == row["expected_prompt_sha256"], "serializer companion typed prompt differs")
                cnl = serialize(prompt, statement_map=statement_map)
                require(isinstance(cnl, str), "serializer companion is not text")
                row["requests"]["cnl"] = add_request(stage, row, "cnl", mapper.compact({"format": "som.cnl.v1", "source": cnl, "target": "rust"}),
                                                    {"kind": "independent public serializer; compiler semantic proof unrun",
                                                     "serializer": sources.locator(replay.SERIALIZER, "serialize_prompt"),
                                                     "statement_map": sources.locator(replay.STATEMENT_MAP, "")})
                row["cnl_source_sha256"] = sha(cnl.encode())
        encoding_counts = Counter()
        for cohort in ["rust_phase1", "rust_phase2"]:
            phase = "phase1" if cohort == "rust_phase1" else "phase2"
            for row in report["cohorts"][cohort]:
                body = parse(request_bytes(stage, row["requests"]["json"]))
                require(set(body) == {"format", "target", "prompt"} and body["format"] == "som.prompt-json.v1"
                        and body["target"] == "rust" and value_sha(body["prompt"]) == row["expected_prompt_sha256"],
                        "verified typed Rust request differs")
                raw, provenance = baseline.request(phase, row["id"], body["prompt"], row["expected"]["http_status"])
                row["baseline_json_request"] = add_request(stage, row, "baseline-json", raw, provenance)
                encoding_counts[provenance["encoding"]] += 1
                if phase == "phase2":
                    row["phase2_expected"] = deepcopy(phase2[row["id"]]["phase2_expected"])
                row["source_recipe_proof"] = {"typed": row["requests"]["json"]["provenance"],
                                               "cnl": row["requests"]["cnl"]["provenance"],
                                               "canonical_typed_prompt_sha256": value_sha(body["prompt"]),
                                               "expected_prompt_sha256": row["expected_prompt_sha256"]}
                row["cnl_semantic_validation"] = "unrun"
                row["cnl_refusal_validation"] = "unrun"
        sources.verify()
        require(baseline.read() == baseline.raw and sum(encoding_counts.values()) == 699, "baseline request proof scope changed")
        report.update(version="som.public-rust-input-completion.v1", state="all699 typed and paired requests supplied;699 accepted baseline request hashes matched; compiler semantics and product gates unrun",
                      typed_input_set_ready=True, paired_request_set_ready=True, full_input_set_ready=False, product_acceptance_passed=False,
                      compiler_semantic_checks="unrun", cnl_refusal_validation="unrun", raw_compile_reply_comparison="unrun",
                      product_suites="unrun", behavior_gates="unrun",
                      baseline_artifact={"path": BASELINE_FILE, "raw_sha256": BASELINE_SHA256},
                      baseline_request_encoding_counts=dict(encoding_counts), public_source_pins=sources.pins,
                      historical_mapper_deferred=report["deferred"],
                      deferred={"typed_prompt_from_cnl": [], "cnl_from_typed_prompt": [], "compiler_semantic_checks": "all699 remain unrun"})
        report["counts"].update(canonical_typed_prompt_rows=699, typed_prompt_deferred=0, cnl_companion_deferred=0,
                                both_forms_supplied=699, requests_supplied=1398, baseline_json_requests=699,
                                baseline_full_request_hashes_verified=699, explicit_typed_companions=9, serializer_cnl_companions=5)
        with (stage / "manifest.json").open("w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        declared = {"manifest.json"}
        for row in rows:
            for item in [*row["requests"].values(), row["baseline_json_request"], row.get("python_control_native")]:
                if item is not None:
                    request_bytes(stage, item)
                    declared.add(item["path"])
        actual = set()
        for file in stage.rglob("*"):
            no_symlinks(file)
            if file.is_file():
                actual.add(file.relative_to(stage).as_posix())
        require(actual == declared and len(actual) == 2120, "completed request file scope differs")
        shutil.copytree(stage, out)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--goldens", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tested-sha", required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "completion output traversal refused")
        report = complete(args.repo.resolve(strict=True), args.inputs.resolve(strict=True), mapper.load_goldens(args.goldens), args.out.absolute(), args.tested_sha)
        print(json.dumps({"state": report["state"], "counts": report["counts"], "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, StopIteration, ImportError, AttributeError) as error:
        print("refused: " + (str(error) if isinstance(error, Refusal) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
