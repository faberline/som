#!/usr/bin/env python3
"""Assemble the complete Python/Rust scope after exact compile proof on one child.

This is an unsealed experiment. It does not accept products or change policy.
Run on GitHub only. Default Python assemblies retain the exact TS065 QA raw
response authority. Three checked Python plans are six additional observations.
Rust always uses its normal target plan. Python checked provenance is context.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

import run_experimental_native_compile as driver
import prove_checked_reference as proof
import reconstruct_public_inputs as replay
from export_goldens import Refusal, digest_text, no_symlinks, parse, relative, require, sha, value_sha

SELF = "som-rs/scripts/hosted_readiness/run_experimental_native_assembly.py"
GOLDENS = "som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json"
ROOT = "assembly"
MANIFEST = ROOT + "/manifest.json"
OBSERVATIONS = {"python_default": 1342, "rust_default": 697, "python_checked": 6}
PRODUCT_COUNTS = {"python": 671, "rust": 697}
CHECKED_IDS = {proof.COHORT["id"], *replay.CHECKED_DSL_SHA256}
OWNERS = ["som-rs/som-targets/src/main.rs", "som-rs/som-targets/src/rust/mod.rs",
          "som-rs/src/compiler.rs", "som-rs/scripts/rust_output_acceptance/python_replay.py",
          "som-rs/scripts/typescript_output_acceptance/check_python_baseline.py",
          "som-rs/scripts/typescript_output_acceptance/check_rust_baseline.py"]


def key(target, identity):
    fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
    return (target, *(identity[field] for field in fields))


def post_assemble(service, raw):
    """Use the same owned child and no-proxy opener. Check ownership every call."""
    ownership = service.ownership()
    request = urllib.request.Request(f"http://127.0.0.1:{service.port}/v1/assemble", data=raw,
                                    headers={"Content-Type": "application/json"}, method="POST")
    try:
        with service.opener.open(request, timeout=driver.TIMEOUT) as response:
            return response.status, response.read(), ownership
    except urllib.error.HTTPError as error:
        return error.code, error.read(), ownership


def files_from_response(status, raw):
    require(type(status) is int and status == 200, "assembly must return HTTP200")
    document = driver.experiment.parse(raw)
    require(type(document) is dict and set(document) == {"status", "files"} and document["status"] == "ok",
            "assembly response schema differs")
    files = document["files"]
    require(type(files) is dict and files and all(type(path) is str and type(text) is str for path, text in files.items()),
            "assembly requires a nonempty complete text file map")
    for path in files:
        relative(path)
        require("\x00" not in path and ":" not in path, "unsafe product path")
    paths = sorted(files)
    require(not any(other.startswith(path + "/") for i, path in enumerate(paths) for other in paths[i + 1:]),
            "product file/directory path conflict")
    return files


def file_receipts(files):
    return [{"path": path, "sha256": sha(text.encode("utf-8", "strict")), "size": len(text.encode("utf-8", "strict"))}
            for path, text in sorted(files.items())]


def verify_product_files(files, golden, target, rust_rebased=None):
    actual = value_sha(files)
    require(actual == digest_text(golden["files_map_sha256"]), "complete frozen product file map differs")
    receipts = file_receipts(files)
    if target == "python":
        require(type(golden["file_count"]) is int and len(files) == golden["file_count"], "Python frozen file count differs")
        observed = sorted(({"path_sha256": sha(row["path"].encode()), "content_sha256": row["sha256"]} for row in receipts),
                          key=lambda row: row["path_sha256"])
        accepted = sorted(golden["accepted_per_file_hashes"], key=lambda row: row["path_sha256"])
        require(len(accepted) == len({row["path_sha256"] for row in accepted}) == len(files) and observed == accepted,
                "Python complete accepted per-file hashes differ")
    else:
        require(rust_rebased is not None and rust_rebased["check"] == "rebased_files"
                and rust_rebased["passed"] is True and rust_rebased["status"] == "passed" and rust_rebased["qa_status"] == 200,
                "Rust accepted rebased map authority differs")
        require(actual == digest_text(rust_rebased["files_sha256"]) == digest_text(rust_rebased["recorded_files_sha256"]),
                "Rust accepted rebased and recorded complete map hashes differ")
    return actual, receipts


def write_product(directory, files):
    """Validate every path before writing. All files are new under an owned row."""
    require(not directory.exists(), "product output must be new")
    no_symlinks(directory)
    receipts = file_receipts(files)
    for row in receipts:
        relative(row["path"])
        no_symlinks(directory / row["path"])
    directory.mkdir()
    for path, content in sorted(files.items()):
        file = directory / path
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("xb") as stream:
            stream.write(content.encode("utf-8", "strict"))
    actual = {}
    for file in directory.rglob("*"):
        no_symlinks(file)
        require(file.is_dir() or file.is_file(), "nonregular product member")
        if file.is_file():
            actual[file.relative_to(directory).as_posix()] = file.read_bytes().decode("utf-8", "strict")
    require(actual == files and file_receipts(actual) == receipts, "saved complete product bytes differ")
    return receipts


def checked_bundle(bundle, dsl, expected_prompt, expected_dsl):
    """Only Python accepts the public checked flow plan; normal assembler validates it."""
    require(bundle.get("version") == "som.dsl-bundle.v1" and "target" not in bundle,
            "checked DSL adapter admits Python bundles only")
    require(value_sha(bundle["prompt"]) == bundle["hashes"]["prompt_sha256"] == expected_prompt
            and value_sha(dsl) == expected_dsl, "checked DSL prompt or canonical pin differs")
    result = deepcopy(bundle)
    result["dsl"] = deepcopy(dsl)
    result["hashes"]["dsl_sha256"] = value_sha(dsl)
    result["hashes"]["payload_sha256"] = value_sha({name: value for name, value in result.items() if name != "hashes"})
    require(result["prompt"] == bundle["prompt"] and result["assets"] == bundle["assets"], "checked adapter changed prompt or current assets")
    return result


class AssemblyExperiment:
    def __init__(self, repo, python_inputs, rust_inputs, checked_proof, tested_sha):
        self.repo, self.python_inputs, self.rust_inputs = repo, python_inputs, rust_inputs
        self.checked_proof, self.tested_sha = checked_proof, tested_sha
        self.checked, self.plan, self.product_golds, self.input_snapshots = {}, [], {}, {}
        self.report = {"version": "som.native-assembly-experiment.v1", "tested_sha": tested_sha,
                       "state": "experimental-unsealed", "policy_state": "unsealed", "product_acceptance_passed": False,
                       "full_input_set_ready": False, "product_runtime_ready": False, "assembly_experiment_complete": False,
                       "expected_observations": OBSERVATIONS, "expected_products": PRODUCT_COUNTS,
                       "observations": [], "products": {"python": [], "rust": []},
                       "remaining": {"old_bundle_rejection_and_rebase": "unrun", "cnl_semantics_and_refusals": "unrun",
                                     "offline_builds_and_product_behavior": "unrun", "typescript_full_scope": "unrun"}}

    def external(self, root, path):
        relative(path)
        file = root / path
        no_symlinks(file)
        raw = file.read_bytes()
        self.input_snapshots[(root, path)] = sha(raw)
        return raw

    def load_checked(self, checker, goldens):
        raw = checker.repo.read(replay.SUPPLEMENT_FILE)
        require(sha(raw) == replay.SUPPLEMENT_SHA256, "approved supplement pin differs")
        supplement = parse(raw)
        for row in supplement["checked_dsl_inputs"]:
            identity = row["id"]
            require(identity in replay.CHECKED_DSL_SHA256 and identity not in self.checked and row["variant"] == "checked",
                    "supplement checked named scope differs")
            golden = next(item for item in goldens["cohorts"]["python_legacy"] if item["id"] == identity)
            require(row["legacy_index"] == golden["index"] and row["prompt_sha256"] == golden["prompt_sha256"]
                    and value_sha(row["checked_dsl_input"]) == row["dsl_sha256"] == replay.CHECKED_DSL_SHA256[identity],
                    "supplement checked DSL binding differs")
            self.checked[identity] = {"dsl": row["checked_dsl_input"], "dsl_sha256": row["dsl_sha256"],
                                      "prompt_sha256": row["prompt_sha256"], "source": replay.SUPPLEMENT_FILE}
        require(set(self.checked) == set(replay.CHECKED_DSL_SHA256), "two supplied checked DSLs required")
        sources = proof.ProofSources(self.repo)
        for path, expected in {**sources.pins, **sources.anchors}.items():
            require(sha(checker.repo.read(path)) == expected, "quality checked proof source differs from candidate")
        require(sha(checker.repo.read(proof.RECIPE_FILE)) == proof.RECIPE_SHA256, "quality checked recipe differs")
        raw = self.external(self.checked_proof, "manifest.json")
        manifest = parse(raw)
        require(manifest["version"] == "som.checked-reference-proof.v1" and manifest["tested_sha"] == self.tested_sha
                and manifest["cohort"] == proof.COHORT and manifest["case_count"] == 1
                and manifest["recipe"] == {"path": proof.RECIPE_FILE, "raw_sha256": proof.RECIPE_SHA256}
                and manifest["public_source_revision"] == replay.PUBLIC_REVISION
                and manifest["module_and_data_pins"] == sources.pins and manifest["public_anchor_pins"] == sources.anchors
                and manifest["recursive_blocks_json_members"] == sources.members()
                and manifest["source_checks_before_and_after"] == "passed"
                and manifest["semantic_validation"] == "passed" and manifest["canonical_dsl_equal"] is True
                and manifest["product_acceptance_passed"] is False
                and manifest["prompt_sha256"] == proof.EXPECTED_PROMPT_SHA256
                and manifest["accepted_canonical_dsl_sha256"] == manifest["generated_canonical_dsl_sha256"] == proof.EXPECTED_DSL_SHA256,
                "same-candidate quality checked proof manifest differs")
        expected_modules = sorted("som_core" if path.endswith("/__init__.py") else path[len(proof.MODULE_PREFIX):-3].replace("/", ".") for path in proof.MODULE_FILES)
        require(manifest["loaded_core_modules"] == expected_modules, "quality checked proof module closure differs")
        locator = manifest["checked_dsl"]
        require(locator["path"] == "checked.dsl.json" and locator["canonical_sha256"] == proof.EXPECTED_DSL_SHA256,
                "quality checked DSL locator differs")
        dsl_raw = self.external(self.checked_proof, locator["path"])
        dsl = parse(dsl_raw)
        require(sha(dsl_raw) == digest_text(locator["raw_sha256"]) and value_sha(dsl) == proof.EXPECTED_DSL_SHA256,
                "quality checked DSL bytes differ")
        self.checked[proof.COHORT["id"]] = {"dsl": dsl, "dsl_sha256": proof.EXPECTED_DSL_SHA256,
                                              "prompt_sha256": proof.EXPECTED_PROMPT_SHA256, "source": "same-candidate public quality checked proof"}
        return sha(raw)

    def prepare(self, checker, document, jobs):
        for module, path in [(sys.modules[__name__], SELF), (driver, driver.DRIVER),
                             (proof, "som-rs/scripts/hosted_readiness/prove_checked_reference.py"),
                             (replay, "som-rs/scripts/hosted_readiness/reconstruct_public_inputs.py")]:
            require(Path(module.__file__).resolve() == self.repo / path and checker.repo.read(path) == Path(module.__file__).read_bytes(),
                    "running assembly helper differs from committed candidate source")
        for path in OWNERS:
            checker.repo.read(path)
        raw = checker.repo.read(GOLDENS)
        require(sha(raw) == driver.mapper.GOLDENS_SHA256, "frozen product golden artifact differs")
        goldens = driver.mapper.load_goldens(self.repo / GOLDENS)
        require(set(row["id"] for row in goldens["cohorts"]["python_legacy"] if row["variant"] == "checked") == CHECKED_IDS,
                "Python checked product slot scope differs")
        proof_manifest_sha = self.load_checked(checker, goldens)
        self.report["input_receipts"] = {"goldens_raw_sha256": driver.mapper.GOLDENS_SHA256,
                                          "ts065_raw_sha256": driver.experiment.BASELINE_SHA256,
                                          "checked_proof_manifest_sha256": proof_manifest_sha,
                                          "approved_supplement_raw_sha256": replay.SUPPLEMENT_SHA256}
        self.report["authority"] = {"python_default_raw": "TS065 accepted QA assemble response bytes for each of1342 compile variants",
                                      "python_product_slots": "public127 plus legacy544 frozen file maps, complete per-file hashes and assembly variants, each checked for both compile variants",
                                      "rust_products": "697 public frozen Rust file maps plus each corresponding TS065 rebased_files.files_sha256 and recorded_files_sha256",
                                      "rust_plan": "som-targets/src/rust/mod.rs assemble recomputes plan(prompt), verifies composition and renders the normal target plan; Python checked provenance is context only",
                                      "old_control_requests": "not projected onto current bundles; old-bundle and rebase gates remain unrun"}
        self.plan_jobs(goldens, document, jobs)
        self.report["source_pin_record"] = checker.source_record()
        self.report["preparation_passed"] = True

    def plan_jobs(self, goldens, document, jobs):
        python = {(source, row["id"]): (cohort, row) for cohort, source in [("python_public", "public"), ("python_legacy", "legacy")]
                  for row in goldens["cohorts"][cohort]}
        rust = {(phase, row["id"]): (cohort, index, row) for cohort, phase in [("rust_phase1", "phase1"), ("rust_phase2", "phase2")]
                for index, row in enumerate(goldens["cohorts"][cohort])}
        accepted_python = {(row["source"], row["id"], row["variant"]): row for row in document["baselines"]["python"]["rows"]}
        rebased = {(row["phase"], row["id"]): row for row in document["baselines"]["rust"]["rows"] if row["check"] == "rebased_files"}
        checked_jobs, refused = [], []
        for index, job in enumerate(jobs):
            target, identity = job["target"], job["identity"]
            if target == "python":
                cohort, golden = python[(identity["source"], identity["id"])]
                accepted = accepted_python[(identity["source"], identity["id"], identity["variant"])]
                require(job["expected_http_status"] == accepted["qa_compile_status"] == accepted["qa_assemble_status"] == accepted["baseline_assemble_status"] == 200,
                        "Python accepted assembly status differs")
                require(accepted["qa_assemble_response_sha256"] == accepted["baseline_assemble_response_sha256"], "Python accepted raw assembly authorities disagree")
                slot = {"target": target, "identity": identity, "cohort": cohort, "source": identity["source"], "id": identity["id"],
                        "index": golden["index"], "compile_variant": identity["variant"], "compile_row_index": index,
                        "mode": "compile-default", "assembly_variant": golden.get("variant", "standard"),
                        "golden": golden, "accepted": accepted, "compare_frozen_product": golden.get("variant", "standard") == "standard"}
                self.plan.append(slot)
                if slot["assembly_variant"] == "checked":
                    extra = {**slot, "mode": "checked-dsl", "compare_frozen_product": True}
                    checked_jobs.append(extra)
                self.product_golds[(target, cohort, golden["index"])] = golden
            else:
                cohort, position, golden = rust[(identity["phase"], identity["id"])]
                is_refusal = identity["id"] in driver.mapper.REFUSALS
                require((job["expected_http_status"] == 400) == is_refusal and job["expected_http_status"] in {200, 400}, "Rust exact product/refusal status scope differs")
                if is_refusal:
                    require(golden["expected"]["result"] == "refusal" and golden["files_map_sha256"] is None,
                            "Rust refusal frozen slot differs")
                    refused.append({"identity": identity, "compile_row_index": index, "expected_http_status": 400, "assembly": "not applicable to exact400 compile refusal"})
                    continue
                r = rebased[(identity["phase"], identity["id"])]
                require(golden["files_map_sha256"] == r["files_sha256"] == r["recorded_files_sha256"], "Rust product map authorities disagree")
                self.plan.append({"target": target, "identity": identity, "cohort": cohort, "phase": identity["phase"], "id": identity["id"],
                                  "index": position, "compile_row_index": index, "mode": "compile-default", "rust_assembly_mode": "normal-target-plan",
                                  "python_assembly_variant_provenance": golden.get("assembly_variant", "standard"),
                                  "golden": golden, "rust_rebased": r, "compare_frozen_product": True})
                self.product_golds[(target, cohort, position)] = golden
        self.plan += checked_jobs
        counts = Counter(item["target"] + ("_checked" if item["mode"] == "checked-dsl" else "_default") for item in self.plan)
        require(dict(counts) == OBSERVATIONS and len(refused) == 2
                and dict(Counter(k[0] for k in self.product_golds)) == PRODUCT_COUNTS,
                "complete assembly observation or product named scope differs")
        self.report["compile_refusals_without_assembly"] = refused

    def observe(self, slot, observation_index, checker, service, compile_record, out):
        directory = out / ROOT / "observations" / f"{observation_index:04d}"
        no_symlinks(directory)
        directory.mkdir(parents=True, exist_ok=False)
        record = {name: deepcopy(value) for name, value in slot.items() if name not in {"golden", "accepted", "rust_rebased"}}
        record.update(observation_index=observation_index, state="failed", started_at=driver.utc())
        if slot["target"] == "python" and slot["mode"] == "compile-default":
            record["accepted_qa_assemble_raw_sha256"] = slot["accepted"]["qa_assemble_response_sha256"]
        if slot["compare_frozen_product"]:
            record["expected_files_map_sha256"] = slot["golden"]["files_map_sha256"]
            if slot["target"] == "python":
                record["accepted_product_raw_sha256"] = slot["golden"]["raw_assemble_response_sha256"]
            else:
                record["ts065_rebased_file_authority"] = {name: slot["rust_rebased"][name] for name in ["row_index", "phase", "id", "check", "files_sha256", "recorded_files_sha256"]}
        started = time.monotonic()
        try:
            source = out / "rows" / f"{slot['compile_row_index']:04d}" / "response.raw"
            no_symlinks(source)
            raw = source.read_bytes()
            require(compile_record["state"] == "passed" and sha(raw) == compile_record["current_response_sha256"],
                    "complete compile proof receipt or raw bytes differ")
            verified = checker.verify_response(slot["target"], slot["identity"], compile_record["http_status"], raw)
            require(verified.original_bundle is not None, "successful compile proof has no admitted bundle")
            bundle = driver.experiment.parse(raw)["bundle"]
            require(value_sha(bundle["prompt"]) == slot["golden"].get("prompt_sha256", bundle["hashes"]["prompt_sha256"])
                    or slot["target"] == "rust" and slot["phase"] == "phase1" and slot["golden"].get("prompt_sha256") is None,
                    "assembly prompt differs from frozen slot")
            if slot["target"] == "python" and slot["mode"] == "compile-default":
                before = driver.compact({"bundle": verified.original_bundle})
                require(sha(before) == slot["accepted"]["qa_assemble_request_sha256"] == slot["accepted"]["baseline_assemble_request_sha256"],
                        "accepted QA default assemble request bytes differ")
            if slot["mode"] == "checked-dsl":
                checked = self.checked[slot["id"]]
                bundle = checked_bundle(bundle, checked["dsl"], checked["prompt_sha256"], checked["dsl_sha256"])
                record["checked_dsl"] = {name: value for name, value in checked.items() if name != "dsl"}
            request = driver.compact({"bundle": bundle})
            request_path = directory / "assemble.request.raw"
            with request_path.open("xb") as stream:
                stream.write(request)
            record.update(request_path=request_path.relative_to(out).as_posix(), request_sha256=sha(request),
                          current_bundle_payload_sha256=bundle["hashes"]["payload_sha256"])
            status, reply, ownership = post_assemble(service, request)
            response_path = directory / "assemble.response.raw"
            with response_path.open("xb") as stream:
                stream.write(reply)
            record.update(response_path=response_path.relative_to(out).as_posix(), response_sha256=sha(reply), http_status=status, owned_socket=ownership)
            files = files_from_response(status, reply)
            actual = value_sha(files)
            record.update(actual_files_map_sha256=actual, file_count=len(files))
            if slot["target"] == "python" and slot["mode"] == "compile-default":
                require(sha(reply) == digest_text(slot["accepted"]["qa_assemble_response_sha256"]), "exact TS065 QA default Python assembly bytes differ")
                record["accepted_qa_assemble_raw_sha256"] = slot["accepted"]["qa_assemble_response_sha256"]
            if slot["compare_frozen_product"]:
                actual, receipts = verify_product_files(files, slot["golden"], slot["target"], slot.get("rust_rebased"))
                record.update(expected_files_map_sha256=slot["golden"]["files_map_sha256"], frozen_product_files_passed=True)
                if slot["target"] == "python":
                    require(sha(reply) == digest_text(slot["golden"]["raw_assemble_response_sha256"]), "exact original frozen Python product assembly bytes differ")
                    record["accepted_product_raw_sha256"] = slot["golden"]["raw_assemble_response_sha256"]
                else:
                    record["ts065_rebased_file_authority"] = {name: slot["rust_rebased"][name] for name in ["row_index", "phase", "id", "check", "files_sha256", "recorded_files_sha256"]}
            product = directory / "product"
            record["files"] = write_product(product, files)
            record.update(product_dir=product.relative_to(out).as_posix(), state="passed", prompt_sha256=value_sha(bundle["prompt"]))
        except InterruptedError:
            record.update(state="interrupted", finished_at=driver.utc(), elapsed_seconds=time.monotonic() - started)
            driver.dump(directory / "result.json", record)
            self.report["observations"].append(record)
            raise
        except Exception as error:
            record["error"] = type(error).__name__
            if isinstance(error, (Refusal, driver.experiment.Refusal)):
                record["reason"] = str(error)
        record.update(finished_at=driver.utc(), elapsed_seconds=time.monotonic() - started)
        driver.dump(directory / "result.json", record)
        return record

    def run(self, checker, service, jobs, records, out):
        require(len(records) == len(jobs) == 2041 and all(row["state"] == "passed" for row in records),
                "every exact compile comparison must pass before assembly starts")
        require(self.report.get("preparation_passed") is True, "full assembly preparation did not pass")
        self.report["compile_stage"] = {"rows": 2041, "passed": 2041, "canonical_row_receipts_sha256": value_sha(records),
                                       "owned_pid": service.process.pid, "owned_start_ticks": service.stamp["start_ticks"]}
        for index, slot in enumerate(self.plan):
            record = self.observe(slot, index, checker, service, records[slot["compile_row_index"]], out)
            self.report["observations"].append(record)
        return {"manifest_path": MANIFEST, "observations": len(self.report["observations"]), "state": "experimental-unsealed"}

    def products(self):
        observations = self.report["observations"]
        indexed = {}
        for row in observations:
            if row["compare_frozen_product"]:
                k = (row["target"], row["cohort"], row["index"], row.get("compile_variant", "rust"))
                require(k not in indexed, "frozen product observation duplicated")
                indexed[k] = row
        products = {"python": [], "rust": []}
        common = ["id", "cohort", "index", "prompt_sha256", "actual_files_map_sha256", "expected_files_map_sha256", "file_count", "product_dir", "files", "state", "compile_row_index", "observation_index", "request_path", "request_sha256", "response_path", "response_sha256"]
        for (target, cohort, position), golden in self.product_golds.items():
            variants = ["none", "python"] if target == "python" else ["rust"]
            rows = [indexed.get((target, cohort, position, variant)) for variant in variants]
            item = {"id": golden["id"], "cohort": cohort, "index": position, "expected_files_map_sha256": golden["files_map_sha256"], "state": "unrun"}
            if all(row is not None and row["state"] == "passed" for row in rows):
                first = rows[0]
                item.update({name: deepcopy(first[name]) for name in common})
                if target == "python":
                    require(rows[0]["actual_files_map_sha256"] == rows[1]["actual_files_map_sha256"], "two Python compile variants produced different frozen products")
                    item.update(source=first["source"], assembly_variant=golden.get("variant", "standard"), selected_compile_variant="none",
                                compile_variants=[{**{name: deepcopy(row[name]) for name in common}, "compile_variant": row["compile_variant"]} for row in rows],
                                accepted_per_file_hashes=golden["accepted_per_file_hashes"], behavior_expected=golden.get("behavior_expected", True))
                else:
                    item.update(phase=first["phase"], rust_assembly_mode="normal-target-plan", python_assembly_variant_provenance=golden.get("assembly_variant", "standard"),
                                standard_behavior_flag=golden.get("standard"), ts065_rebased_file_authority=first["ts065_rebased_file_authority"])
            else:
                item["state"] = "failed" if any(row is not None and row["state"] != "passed" for row in rows) else "unrun"
            products[target].append(item)
        return products

    def finalize(self, compile_report, out):
        directory = out / ROOT
        no_symlinks(directory)
        directory.mkdir(exist_ok=True)
        try:
            self.report["products"] = self.products()
            for (root, path), expected in self.input_snapshots.items():
                no_symlinks(root / path)
                require(sha((root / path).read_bytes()) == expected, "checked input changed during assembly")
            self.report["checked_input_stability_passed"] = True
            for row in self.report["observations"]:
                if row["state"] == "passed":
                    for label in ["request", "response"]:
                        relative(row[label + "_path"])
                        file = out / row[label + "_path"]
                        no_symlinks(file)
                        require(sha(file.read_bytes()) == row[label + "_sha256"], "saved assembly exchange changed")
                    relative(row["product_dir"])
                    directory = out / row["product_dir"]
                    no_symlinks(directory)
                    files = {}
                    for file in directory.rglob("*"):
                        no_symlinks(file)
                        require(file.is_dir() or file.is_file(), "saved product contains a nonregular member")
                        if file.is_file():
                            files[file.relative_to(directory).as_posix()] = file.read_bytes().decode("utf-8", "strict")
                    require(value_sha(files) == row["actual_files_map_sha256"] and file_receipts(files) == row["files"],
                            "saved complete assembly product changed")
            self.report["saved_observation_stability_passed"] = True
        except Exception as error:
            self.report["finalization_error"] = type(error).__name__
            if isinstance(error, (Refusal, driver.experiment.Refusal)):
                self.report["finalization_reason"] = str(error)
        rows = self.report["observations"]
        actual = dict(Counter(row["target"] + ("_checked" if row["mode"] == "checked-dsl" else "_default") for row in rows))
        products = self.report["products"]
        self.report.update(actual_observations=actual, actual_products={target: len(items) for target, items in products.items()},
                           observations_passed=sum(row["state"] == "passed" for row in rows),
                           product_slots_passed={target: sum(row["state"] == "passed" for row in items) for target, items in products.items()},
                           owned_cleanup=compile_report.get("cleanup"), identity_stability_passed=compile_report.get("identity_stability_passed", False),
                           compile_experiment_complete=compile_report.get("experiment_complete", False), finished_at=driver.utc())
        self.report["assembly_experiment_complete"] = (actual == OBSERVATIONS and all(row["state"] == "passed" for row in rows)
              and self.report["actual_products"] == PRODUCT_COUNTS and self.report["product_slots_passed"] == PRODUCT_COUNTS
              and self.report.get("checked_input_stability_passed") is True
              and self.report.get("saved_observation_stability_passed") is True and "finalization_error" not in self.report
              and compile_report.get("experiment_complete") is True and compile_report.get("identity_stability_passed") is True
              and compile_report.get("cleanup", {}).get("cleanup_passed") is True)
        driver.dump(out / MANIFEST, self.report)
        return {"manifest_path": MANIFEST, "manifest_sha256": sha((out / MANIFEST).read_bytes()),
                "assembly_experiment_complete": self.report["assembly_experiment_complete"], "product_acceptance_passed": False,
                "actual_observations": actual, "actual_products": self.report["actual_products"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["repo", "python-inputs", "rust-inputs", "checked-proof", "binary", "assets", "out", "ruff", "rustfmt", "node", "prettier"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "output traversal refused")
        repo = args.repo.resolve(strict=True)
        python_inputs, rust_inputs, checked = [path.resolve(strict=True) for path in [args.python_inputs, args.rust_inputs, args.checked_proof]]
        out = args.out.absolute()
        require(out != checked and checked not in out.parents, "output must be outside checked proof inputs")
        extension = AssemblyExperiment(repo, python_inputs, rust_inputs, checked, args.tested_sha)
        tools = {key: getattr(args, name).absolute() for key, name in driver.TOOLS.items()}
        report = driver.run(repo, python_inputs, rust_inputs, args.binary.absolute(), args.assets.absolute(), tools,
                            args.port, out, args.tested_sha, extension=extension)
        return 0 if report.get("assembly_extension", {}).get("assembly_experiment_complete") is True else 1
    except Exception as error:
        print(driver.json.dumps({"state": "experimental-unsealed", "product_acceptance_passed": False,
                                 "assembly_experiment_complete": False, "error": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
