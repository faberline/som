#!/usr/bin/env python3
"""Reconstruct public regression inputs. Run on GitHub. Never accept products."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys

from export_goldens import (DIRECT_PATHS, EXAMPLES, FIXTURES, PUBLIC_REVISION,
                            Refusal, case_id, digest_text, no_symlinks, parse,
                            relative, require, sha, value_sha)

# Seal this to the independently reviewed hash-only export before enabling CI.
# A CLI argument or environment variable cannot change the trusted digest.
GOLDENS_SHA256 = "ea573d3c842d367b32538ad8b9fec01608b4f05f676a07a80d9b9acf81a69a8b"
SOURCE_COUNT = 35
PROBE = "som-core/scripts/probe_flow_maximum_matrix.py"
FREEZE = "som-core/scripts/freeze_flow_cases.py"
SERIALIZER = "som-rs/tests/compiler-v1/serializer.py"
STATEMENT_MAP = "som-rs/contracts/compiler-v1/field-statement-map.json"
RUNTIME_PINS = {
    "som-core/src/som_core/__init__.py": "654787391f426548a06af79d6ae1d2eb7e989a2f5c81cfecb8498d0d2a335042",
    "som-core/src/som_core/flow.py": "92a7228ed3c96965c8326e70fa9edfec90fe0150e56cbeae7131de1f11f057fe",
    "som-core/src/som_core/composed.py": "a65fd965d1e56b050d900961768176835aba97d994d1793a27f6a66a3e05a58b",
    "som-core/src/som_core/related.py": "3b19fbbfb7026b1e287622c6bf04def69e7080025c2f12c48ce2c1abf6e47dd7",
    "som-core/src/som_core/v1.py": "fc82db5016eb1befc4b076e1f611f99f25e89c71a9f6e10cddedae29693d32ce",
    "som-core/src/som_core/business.py": "c07af14470c165fb5496d019179ee5c1951a3d2ddfef98354a267062e4747e97",
    "som-core/src/som_core/flow_compact.py": "6e57b94b5fa5665b819a0e66f25af98e073712736b8c06d229b8b7ccfcd101c1",
    "som-core/src/som_core/flow_codec.py": "4f85a70fe04ad1fb35bf9846088f599fca4052aff554295222e5d2b9cff632d2",
    "som-core/src/som_core/quality.py": "04fcfaf21b564f613c0d3395796bb28ae93601c5d2be1dde988c6f243aa483c4",
    "som-core/src/som_core/compact_names.py": "5131a4d92b95f78e9a89c00d0508b7189be11b02c4e9afbb46ed888a2a601928",
    "som-rs/contracts/related-flow.v1.md": "716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091",
    "som-rs/contracts/related-business.v1.md": "9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380",
    PROBE: "3f5981e25b560b568100f33a6388d4173b5d7633df4b613d3508043130f1e527",
    FREEZE: "6b83e2c5aef86455055c8480e2c164fcc1d418687ad5f641f7938cf955df16ba",
    SERIALIZER: "bfb8eca480c08ee16c1a8bb42aae4a8a10f947ed0ca9c9c5521c75d09809632d",
    STATEMENT_MAP: "76744262ece22db17c61683b206beaab67105245888719f9e86c2c84e8283be2",
}


def request(body, *, public):
    options = {"ensure_ascii": False, "allow_nan": False}
    if public:
        return json.dumps(body, separators=(",", ":"), **options).encode()
    return (json.dumps(body, indent=2, **options) + "\n").encode()


class Sources:
    def __init__(self, repo, document):
        self.repo, self.pins, self.cache = repo, {}, {}
        rows = document["public_source_files"]
        require(len(rows) == SOURCE_COUNT, "public source set is incomplete")
        for row in rows:
            path = str(relative(row["path"]))
            require(row["revision"] == PUBLIC_REVISION and path not in self.pins,
                    "public source identity differs")
            self.pins[path] = digest_text(row["raw_sha256"])
        self.verify()

    def read(self, path):
        relative(path)
        require(path in self.pins, "public source is not pinned")
        file = self.repo / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == self.pins[path], "public source digest differs")
        return raw

    def document(self, path):
        if path not in self.cache:
            self.cache[path] = parse(self.read(path))
        return self.cache[path]

    def verify(self):
        for path in self.pins:
            self.read(path)

    def verify_runtime(self):
        for path, expected in RUNTIME_PINS.items():
            file = self.repo / path
            no_symlinks(file)
            require(sha(file.read_bytes()) == expected, "public recipe dependency digest differs")
            if path in self.pins:
                require(self.pins[path] == expected, "public recipe source pins conflict")


class PinnedSourceModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Execute verified source bytes directly. Ignore bytecode and extensions."""
    def __init__(self, sources):
        self.sources = sources
        self.modules = {"probe_flow_maximum_matrix": PROBE, "freeze_flow_cases": FREEZE,
                        "som_hosted_serializer": SERIALIZER}
        self.packages = set()
        prefix = "som-core/src/"
        for path in RUNTIME_PINS:
            if path.startswith(prefix) and path.endswith(".py"):
                parts = path[len(prefix):-3].split("/")
                package = parts[-1] == "__init__"
                if package:
                    parts.pop()
                name = ".".join(parts)
                require(name not in self.modules, "duplicate pinned source module")
                self.modules[name] = path
                if package:
                    self.packages.add(name)

    def handles(self, name):
        return name in self.modules or name == "som_core" or name.startswith("som_core.")

    def find_spec(self, fullname, path=None, target=None):
        if not self.handles(fullname):
            return None
        require(fullname in self.modules, "unbound public recipe module refused before execution")
        file = self.sources.repo / self.modules[fullname]
        locations = [] if fullname in self.packages else None
        return importlib.util.spec_from_file_location(fullname, file, loader=self,
                                                      submodule_search_locations=locations)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__spec__.name
        require(name in self.modules, "unbound public recipe module refused before execution")
        path = self.modules[name]
        file = self.sources.repo / path
        no_symlinks(file)
        raw = file.read_bytes()
        require(sha(raw) == RUNTIME_PINS[path], "public recipe source digest differs before execution")
        # A normal source loader can execute cached .pyc or a shadowing extension.
        # Compile the exact checked byte string, with no bytecode cache involved.
        module.__cached__ = None
        exec(compile(raw, str(file), "exec", dont_inherit=True), module.__dict__)


@contextmanager
def runtime(sources):
    """Import unchanged public recipes only after verifying every dependency.

    The probe's model path remains an unused constant. Do not call main(),
    sources(), planners, assemblers, formatters or subprocess functions.
    """
    sources.verify_runtime()
    loader = PinnedSourceModules(sources)
    require(not any(loader.handles(name) for name in sys.modules), "public recipe module is already loaded")
    before = set(sys.modules)
    old_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.meta_path.insert(0, loader)
    def verify_imports():
        for name in set(sys.modules) - before:
            module = sys.modules[name]
            if loader.handles(name):
                path = Path(module.__file__).resolve(strict=True).relative_to(sources.repo).as_posix()
                require(path == loader.modules.get(name) and module.__spec__.loader is loader,
                        "unbound public recipe module imported")
    try:
        probe = importlib.import_module("probe_flow_maximum_matrix")
        serializer = importlib.import_module("som_hosted_serializer")
        # Verify the full import closure. Do not silently use an external package.
        verify_imports()
        sources.verify_runtime()
        yield probe.maximum_matrix, serializer.serialize_prompt
        verify_imports()
        sources.verify_runtime()
    finally:
        sys.meta_path.remove(loader)
        sys.dont_write_bytecode = old_bytecode
        for name in set(sys.modules) - before:
            if loader.handles(name):
                del sys.modules[name]


def public_case(sources, row):
    where = row["public_source"]
    if "prompt_pointer" in where:
        require(where["path"] == FIXTURES, "public fixture path differs")
        parts = where["prompt_pointer"].split("/")
        require(len(parts) == 4 and parts[:2] == ["", "cases"] and parts[2].isdigit()
                and parts[3] == "expected_prompt" and where["cnl_pointer"] == f"/cases/{parts[2]}/cnl",
                "public fixture selector differs")
        case = sources.document(FIXTURES)["cases"][int(parts[2])]
        require(case["id"] == row["id"], "public fixture identity differs")
        return case["expected_prompt"], case["cnl"]
    prompt_path, cnl_path = where["prompt_path"], where["cnl_path"]
    matches = [case for case in sources.document(EXAMPLES + "index.json")["cases"]
               if EXAMPLES + case["prompt"] == prompt_path and EXAMPLES + case["cnl"] == cnl_path]
    require(len(matches) == 1 and "PUBLIC_" + matches[0]["id"] == row["id"], "public example identity differs")
    return sources.document(prompt_path), sources.read(cnl_path).decode("utf-8")


def direct_prompt(sources, row):
    selected = []
    for locator in row["public_sources"]:
        require(locator["path"] in DIRECT_PATHS and locator["pointer"] == "/prompt"
                and type(locator["line_1based"]) is int and locator["line_1based"] > 0,
                "direct legacy selector differs")
        raw = sources.read(locator["path"]).decode("utf-8").splitlines()[locator["line_1based"] - 1]
        prompt = parse(raw)["prompt"]
        require(value_sha(prompt) == row["prompt_sha256"], "direct legacy prompt differs")
        selected.append(prompt)
    require(selected, "direct legacy source is absent")
    # Every listed source must preserve JSON member order for the saved writer.
    require(all(request({"format": "som.prompt-json.v1", "prompt": item}, public=False)
                == request({"format": "som.prompt-json.v1", "prompt": selected[0]}, public=False)
                for item in selected), "direct legacy source order differs")
    return selected[0]


def load_goldens(path):
    no_symlinks(path)
    digest_text(GOLDENS_SHA256)
    raw = path.read_bytes()
    require(sha(raw) == GOLDENS_SHA256, "golden export is not sealed")
    document = parse(raw)
    require(document["version"] == "som.public-golden-hashes.v1"
            and document["public_repository"] == "faberline/som"
            and document["public_source_revision"] == PUBLIC_REVISION
            and document["product_acceptance_passed"] is False, "golden identity differs")
    return document


def ordered(rows, count):
    require(len(rows) == count and len({case_id(row["id"]) for row in rows}) == count,
            "named input cohort differs")
    require([row["index"] for row in rows] == list(range(count)), "input cohort order differs")


def save_inputs(out, cohort, index, prompt, cnl, *, public):
    directory = out / cohort / f"{index:04d}"
    directory.mkdir(parents=True, exist_ok=False)
    bodies = {"compile-json": {"format": "som.prompt-json.v1", "prompt": prompt},
              "compile-cnl": {"format": "som.cnl.v1", "source": cnl}}
    raw = {name: request(body, public=public) for name, body in bodies.items()}
    (directory / "prompt.json").write_bytes((json.dumps(prompt, ensure_ascii=False, indent=2,
                                                       allow_nan=False) + "\n").encode())
    (directory / "source.cnl").write_bytes(cnl.encode())
    for name, data in raw.items():
        (directory / (name + ".request.raw")).write_bytes(data)
    return {name: {"path": (directory / (name + ".request.raw")).relative_to(out).as_posix(),
                   "sha256": sha(data)} for name, data in raw.items()}


def reconstruct(document, repo, out):
    require(not out.exists() and repo != out and repo not in out.parents,
            "output must be a new directory outside the source checkout")
    no_symlinks(out)
    sources = Sources(repo, document)
    public = document["cohorts"]["python_public"]
    legacy = document["cohorts"]["python_legacy"]
    ordered(public, 127)
    ordered(legacy, 544)
    expected_routes = {"verified_tracked": 443, "public_generator_unrun": 90,
                       "blocked_no_public_source": 11}
    require(dict(Counter(row["input_provenance"] for row in legacy)) == expected_routes,
            "legacy reconstruction scope differs")
    require(len({row["prompt_sha256"] for row in legacy}) == 540, "legacy duplicate scope differs")
    out.mkdir(parents=True, exist_ok=False)
    public_rows, legacy_rows = [], []
    for row in public:
        prompt, cnl = public_case(sources, row)
        require(value_sha(prompt) == row["prompt_sha256"] and sha(cnl.encode()) == row["cnl_sha256"],
                "public input digest differs")
        requests = save_inputs(out, "python_public", row["index"], prompt, cnl, public=True)
        require({name: item["sha256"] for name, item in requests.items()} == row["request_sha256"],
                "public request bytes differ")
        public_rows.append({"id": row["id"], "index": row["index"], "state": "reconstructed",
                            "prompt_sha256": row["prompt_sha256"], "cnl_sha256": row["cnl_sha256"],
                            "requests": requests})
    with runtime(sources) as (maximum_matrix, serialize_prompt):
        generated = {}
        for identity, prompt in maximum_matrix():
            identity = case_id("m2-" + identity)
            require(identity not in generated, "duplicate generated identity")
            generated[identity] = prompt
        expected_ids = {row["id"] for row in legacy if row["input_provenance"] == "public_generator_unrun"}
        require(len(generated) == 90 and set(generated) == expected_ids, "generator named cohort differs")
        for row in legacy:
            entry = {"id": row["id"], "index": row["index"], "prompt_sha256": row["prompt_sha256"],
                     "cnl_sha256": row["cnl_sha256"], "checked_dsl_state": row.get("checked_dsl_provenance", "not_applicable")}
            if row["input_provenance"] == "blocked_no_public_source":
                entry["state"] = "blocked_no_public_source"
                legacy_rows.append(entry)
                continue
            prompt = direct_prompt(sources, row) if row["input_provenance"] == "verified_tracked" else generated[row["id"]]
            require(value_sha(prompt) == row["prompt_sha256"], "reconstructed legacy prompt differs")
            if row["input_provenance"] == "public_generator_unrun":
                recipe = row["public_recipe"]
                require(recipe["path"] == PROBE and recipe["function"] == "maximum_matrix" and recipe["id_prefix"] == "m2-"
                        and recipe["source_sha256"] == RUNTIME_PINS[PROBE]
                        and recipe["constructor"]["path"] == FREEZE and recipe["constructor"]["function"] == "requirement"
                        and recipe["constructor"]["source_sha256"] == RUNTIME_PINS[FREEZE], "generator recipe differs")
            cnl = serialize_prompt(prompt, contract_dir=repo / "som-rs/contracts/compiler-v1")
            require(sha(cnl.encode()) == row["cnl_sha256"], "reconstructed legacy CNL differs")
            requests = save_inputs(out, "python_legacy", row["index"], prompt, cnl, public=False)
            if row["input_provenance"] == "verified_tracked":
                require(requests["compile-json"]["sha256"] == row["typed_request_sha256"],
                        "direct legacy request bytes differ")
            entry.update(state="reconstructed", requests=requests)
            legacy_rows.append(entry)
    sources.verify()
    require(len(public_rows) == 127 and len(legacy_rows) == 544, "input manifest is incomplete")
    blocked_checked = [row["id"] for row in legacy_rows if row["checked_dsl_state"] == "blocked_no_public_source"]
    unrun_checked = [row["id"] for row in legacy_rows if row["checked_dsl_state"] == "public_reference_recipe_unrun"]
    require(len(blocked_checked) == 2 and len(unrun_checked) == 1, "checked DSL scope differs")
    report = {"version": "som.public-input-reconstruction.v1", "golden_export_sha256": GOLDENS_SHA256,
              "source_revision": PUBLIC_REVISION, "product_acceptance_passed": False,
              "state": "partial public input reconstruction; full products and private inputs remain blocked",
              "product_suites": "unrun", "behavior_gates": "unrun", "raw_compile_reply_comparison": "unrun",
              "full_input_set_ready": False, "generator_prompt_proof": "90 exact named prompt and CNL hashes matched",
              "counts": {"public_reconstructed": 127, "legacy_reconstructed": 533, "legacy_blocked": 11,
                         "legacy_named_total": 544, "legacy_generator_reconstructed": 90,
                         "checked_dsl_blocked": 2, "checked_dsl_public_recipe_unrun": 1},
              "public_source_files": document["public_source_files"], "recipe_dependencies": RUNTIME_PINS,
              "blocked_checked_dsl_ids": blocked_checked, "unrun_checked_dsl_ids": unrun_checked,
              "cohorts": {"python_public": public_rows, "python_legacy": legacy_rows}}
    with (out / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--goldens", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "output traversal refused")
        report = reconstruct(load_goldens(args.goldens), args.repo.resolve(strict=True), args.out.absolute())
        print(json.dumps({"state": report["state"], "counts": report["counts"], "product_acceptance_passed": False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, ImportError, AttributeError) as error:
        print("refused: " + (str(error) if isinstance(error, Refusal) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
