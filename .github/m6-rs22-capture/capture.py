#!/usr/bin/env python3
"""Capture RS22 baseline observations on a disposable GitHub macOS runner.

The output is fresh 2bce evidence, never a recovered historical request or an
acceptance decision. It contains sensitive raw exchanges and must be sealed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASELINE = "2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c"
OLD_SOURCE = "8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af"
MATRIX_SHA = "ebf77d94f2a58a55e19b9de4b9e339635e292f51d5bbe4e949d274463bad0578"
INPUT_SHA = "428e948240ca94c5be26e390fe7d058e6570087ec4cac700ab949c38b0bc2486"
NEGATIVE_SHA = "9bb36d689ad4be26e608ac8be779a1a8bb8db043d37788b5819c9fe1bf402e22"
PHASE2_SHA = "b2d13ec6ede88ee6175ba12abd7b608453330030abb184c66a37c886d1e8e7e6"
VECTORS_SHA = "dd09b41f49702f70098574f638e10ea33646366a77dfa486539f324d6f9ff83f"
PINS = {
    "som": BASELINE,
    "build.json": "0918984e7c9c00c5299d62d38209658385b960f996d84bf0514b52c04c528d73",
    "runtime.json": "7c4ba46a498bb0f601bd67ff77f99dfe75e4ee1669d6af3c53f3b338a550b970",
    "source.json": "d40578d8845fff8bfeff627924aa501e56f3973eddb09c2d871e25adec2bbbe5",
    "assets/blocks.json": "c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e",
    "assets/legacy-blocks.json": "6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f",
    "assets/quality-blocks.json": "0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc",
    "assets/quality.json": "6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21",
    "assets/ruff": "cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a",
    "assets/pyproject.toml": "4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4",
    "assets/manifest.json": "c406d03c0edd98ab703a616c1c2be5d81f001966ac9c84280edce5765f91a138",
}
PORT = 18917
REFUSALS = {"RS12_CNL_V3_FILTER_LIMIT", "RS13_JSON_V3_FILTER_OFFSET"}
MAX_BODY = 32 * 1024 * 1024


class Refusal(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, "duplicate JSON member")
        result[key] = value
    return result


def parse(raw):
    need(type(raw) is bytes and len(raw) <= MAX_BODY, "invalid raw input")
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal("nonfinite number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Refusal("invalid JSON") from exc


def canonical(value):
    """Frozen canonical JSON, qualified against all checked-in vectors."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        need(math.isfinite(value), "nonfinite number")
        result = repr(value)
        if "e" in result:
            mantissa, exponent = result.split("e", 1)
            sign = "-" if exponent.startswith("-") else "+" if exponent.startswith("+") else ""
            digits = exponent.lstrip("+-")
            if sign == "-" and len(digits) < 2:
                digits = digits.zfill(2)
            result = mantissa + "e" + sign + digits
        return result
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(x) for x in value) + "]"
    need(type(value) is dict and all(type(k) is str for k in value), "unsupported JSON")
    return "{" + ",".join(canonical(k) + ":" + canonical(value[k]) for k in sorted(value)) + "}"


def value_sha(value):
    return sha(canonical(value).encode("utf-8", "strict"))


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8", "strict")


def typed_python_request(typed_raw, expected_source_sha, expected_prompt_sha, expected_wire_sha):
    need(sha(typed_raw) == expected_source_sha, "typed input bytes differ")
    typed = parse(typed_raw)
    need(type(typed) is dict and set(typed) == {"format", "prompt", "target"}
         and typed["format"] == "som.prompt-json.v1" and typed["target"] == "rust", "typed envelope differs")
    need(value_sha(typed["prompt"]) == expected_prompt_sha, "typed input prompt differs")
    request = compact({"format": typed["format"], "prompt": typed["prompt"]})
    need(sha(request) == expected_wire_sha, "typed Python wire differs")
    return request


def qualify_vectors(raw):
    need(sha(raw) == VECTORS_SHA, "canonical vector source differs")
    vectors = parse(raw)["vectors"]
    need(type(vectors) is list and len(vectors) == 11, "canonical vector count differs")
    for item in vectors:
        expected = item["canonical"].encode("utf-8")
        need(canonical(parse(item["input_json"].encode("utf-8"))).encode("utf-8") == expected
             and sha(expected) == item["sha256"], "canonical vector mismatch")


def physical_file(root, relative):
    need(type(relative) is str and relative and "\\" not in relative and "\x00" not in relative,
         "invalid path")
    parts = relative.split("/")
    need(not PurePosixPath(relative).is_absolute() and all(x not in ("", ".", "..") for x in parts), "unsafe path")
    node = root
    for part in parts:
        node = node / part
        need(not node.is_symlink(), "symlink refused")
    need(node.is_file(), "missing file")
    return node.read_bytes()


def identity(root):
    need(root.is_absolute() and root.resolve() == root and not root.is_symlink()
         and not (root / "assets").is_symlink(), "baseline path differs")
    need(set(p.name for p in (root / "assets").iterdir()) == {p.split("/")[1] for p in PINS if p.startswith("assets/")},
         "asset set differs")
    for name, expected in PINS.items():
        need(sha(physical_file(root, name)) == expected, "baseline identity differs")
    build = parse(physical_file(root, "build.json"))
    runtime = parse(physical_file(root, "runtime.json"))
    source = parse(physical_file(root, "source.json"))
    need(source.get("commit") == OLD_SOURCE and build.get("complete") is True, "old source differs")
    for side in ("tree_before", "tree_after"):
        need(build.get(side, {}).get("head") == OLD_SOURCE and build[side].get("porcelain") == "", "old build tree differs")
    need(build.get("binary_sha256") == runtime.get("binary_sha256") == BASELINE
         and build.get("source_sha256") == runtime.get("source_sha256") == PINS["source.json"]
         and type(runtime.get("health")) is dict, "old receipts differ")
    return runtime["health"]


def identity_observation(root, folder):
    folder.mkdir()
    observations = {}
    for name in PINS:
        raw = physical_file(root, name)
        digest = sha(raw)
        need(digest == PINS[name], "baseline observed identity differs")
        observations[name] = {"sha256": digest, "bytes": len(raw)}
        if name in ("build.json", "runtime.json", "source.json", "assets/manifest.json"):
            observations[name]["raw"] = save(folder / name.replace("/", "_"), raw)
    return observations


def no_listener(port):
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    need(result.returncode == 1 and not result.stdout and not result.stderr, "listener is present or unknown")
    return {"port": port, "exit_code": result.returncode, "stdout_sha256": sha(result.stdout),
            "stderr_sha256": sha(result.stderr), "stdout": "", "stderr": ""}


def owned_listener(pid):
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-Fp"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    need(result.returncode == 0 and not result.stderr and result.stdout.splitlines() == [f"p{pid}".encode()],
         "listener ownership differs")
    return {"port": PORT, "pid": pid, "exit_code": result.returncode,
            "stdout": result.stdout.decode("ascii"), "stderr": "",
            "stdout_sha256": sha(result.stdout), "stderr_sha256": sha(result.stderr)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refusal("redirect refused")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def http(method, endpoint, body=None):
    need(endpoint in ("/healthz", "/v1/compile", "/v1/assemble"), "endpoint refused")
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}{endpoint}", data=body, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        response = OPENER.open(request, timeout=120)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(MAX_BODY + 1)
        need(len(raw) <= MAX_BODY, "response too large")
        return response.status, raw


def input_rows(repo, inputs, matrix_path):
    need(inputs.is_absolute() and inputs.resolve() == inputs and not inputs.is_symlink(), "artifact root differs")
    need(matrix_path == repo / ".github/m6-rs22-capture/input-matrix.json", "matrix path differs")
    matrix_raw = physical_file(repo, ".github/m6-rs22-capture/input-matrix.json")
    need(sha(matrix_raw) == MATRIX_SHA, "matrix differs")
    matrix = parse(matrix_raw)
    need(matrix.get("typed_artifact_input_manifest_sha256") == INPUT_SHA, "matrix artifact identity differs")
    negative_raw = physical_file(repo, "som-rs/contracts/rust-output-v1/negative-cases.json")
    phase_raw = physical_file(repo, "som-rs/contracts/rust-output-v1/phase2/case-inventory.json")
    vectors_raw = physical_file(repo, "som-rs/contracts/compiler-v1/canonical-vectors.json")
    need(sha(negative_raw) == NEGATIVE_SHA and sha(phase_raw) == PHASE2_SHA, "frozen inputs differ")
    qualify_vectors(vectors_raw)
    manifest_raw = physical_file(inputs, "manifest.json")
    need(sha(manifest_raw) == INPUT_SHA, "artifact manifest differs")
    manifest = parse(manifest_raw)
    need(manifest.get("version") == "som.public-rust-input-completion.v1"
         and manifest.get("tested_sha") == "f59e28d910ff4adb16e3becfdc82bd2978ca84fa", "artifact source differs")
    negative = {x["id"]: x for x in parse(negative_raw)["cases"] if x["id"].startswith("RS")}
    phase = {x["id"]: x for x in parse(phase_raw)["rows"] if x.get("kind") == "rs"}
    artifact = {x["id"]: x for x in manifest["cohorts"]["rust_phase2"] if x.get("kind") == "rs"}
    rows = matrix["rows"]
    need(len(negative) == len(phase) == len(artifact) == len(rows) == 22, "RS22 scope differs")
    need(len({x["id"] for x in rows}) == 22 and set(negative) == set(phase) == set(artifact) == {x["id"] for x in rows},
         "RS22 IDs differ")
    result = []
    for row in rows:
        case_id = row["id"]
        need(re.fullmatch(r"RS[0-9]{2}_[A-Z0-9_]+", case_id) is not None, "case ID differs")
        case, frozen, supplied = negative[case_id], phase[case_id], artifact[case_id]
        status = 400 if case_id in REFUSALS else 200
        need(row["form"] == case["form"] and row["expected_control_status"] == status
             and row["frozen_prompt_sha256"] == frozen["prompt_sha256"] == supplied["expected_prompt_sha256"],
             "frozen case identity differs")
        native = case["python_control_body"].encode("utf-8")
        need(sha(native) == row["native_control_wire_sha256"], "native wire differs")
        typed_record = supplied["requests"]["json"]
        need(typed_record["path"] == row["typed_rust_source_path"]
             and typed_record["raw_sha256"] == row["typed_rust_source_sha256"], "typed input identity differs")
        typed_raw = physical_file(inputs, typed_record["path"])
        python_typed = typed_python_request(typed_raw, typed_record["raw_sha256"],
                                             row["frozen_prompt_sha256"],
                                             row["fresh_python_typed_request_sha256"])
        result.append((row, case, native, typed_raw, python_typed))
    need(sum(x[0]["expected_control_status"] == 200 for x in result) == 20, "success scope differs")
    return result


def expected_error(status, raw, case, request, native):
    doc = parse(raw)
    expected = case["expected"]
    need(status == 400 and type(doc) is dict and doc.get("code") == expected["code"], "error status/code differs")
    diagnostics = doc.get("diagnostics")
    need(type(diagnostics) is list and len(diagnostics) == 1 and type(diagnostics[0]) is dict, "diagnostic missing")
    if native:
        moved = case.get("python_control_expected", "").startswith("the same diagnostic, located")
        location = {"line", "column", "end_line", "end_column", "byte_start", "byte_end"}
        for key, value in expected.items():
            if not moved or key not in location:
                need(diagnostics[0].get(key) == value, "native diagnostic differs")
        if moved:
            start, end = diagnostics[0].get("byte_start"), diagnostics[0].get("byte_end")
            need(type(start) is int and type(end) is int and 0 <= start < end <= len(request)
                 and request[start:end].decode("utf-8") == case["span_text"], "native error span differs")
    return doc


def save(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": f"run/{path.parent.name}/{path.name}", "sha256": sha(raw), "bytes": len(raw)}


def execute_case(directory, row, case, form, request, result=None):
    if result is None:
        result = {}
    result.update(form=form, status="attempted", expected_status=row["expected_control_status"])
    try:
        result["compile_request"] = save(directory / f"{form}.compile.request.raw", request)
        status, response = http("POST", "/v1/compile", request)
        result["compile_status"] = status
        result["compile_response"] = save(directory / f"{form}.compile.response.raw", response)
        need(status == row["expected_control_status"], "unexpected compile status")
        if status == 400:
            expected_error(status, response, case, request, form == "native")
            result.update(status="captured_refusal", products="not_applicable_refusal",
                          typed_diagnostic_note="code/status recorded; full typed diagnostic audit remains required")
            return result
        body = parse(response)
        bundle = body.get("bundle") if type(body) is dict else None
        need(type(bundle) is dict and bundle.get("version") == "som.dsl-bundle.v1", "bundle missing")
        need(value_sha(bundle.get("prompt")) == row["frozen_prompt_sha256"], "prompt hash differs")
        request_assemble = compact({"bundle": bundle})
        result["assemble_request"] = save(directory / f"{form}.assemble.request.raw", request_assemble)
        astatus, assembly = http("POST", "/v1/assemble", request_assemble)
        result["assemble_status"] = astatus
        result["assemble_response"] = save(directory / f"{form}.assemble.response.raw", assembly)
        need(astatus == 200, "assemble failed")
        assembled = parse(assembly)
        files = assembled.get("files") if type(assembled) is dict else None
        need(type(files) is dict and files and all(type(k) is str and type(v) is str for k, v in files.items()), "files missing")
        result["files_sha256"] = {name: sha(value.encode("utf-8", "strict")) for name, value in sorted(files.items())}
        result["files_map_sha256"] = value_sha(files)
        result["status"] = "captured_product"
        return result
    except Exception:
        result["status"] = "failed"
        raise

def guarded_output(repo, out):
    need(repo.is_absolute() and repo.resolve() == repo and out == repo / ".local/m6-rs22/capture", "output path refused")
    need(not out.exists() and not out.is_symlink(), "output already exists")
    for directory in (out, *out.parents):
        need(not directory.is_symlink(), "output symlink refused")
        if directory == repo:
            break


def stop_child(process, cleanup):
    cleanup.update({"pid": process.pid, "child_started": True, "terminate_attempted": False,
                    "terminate_sent": False, "kill_attempted": False, "kill_sent": False,
                    "wait_completed": False, "wait_returncode": None, "post_wait_poll": None})
    if process.poll() is None:
        cleanup["terminate_attempted"] = True
        try:
            process.terminate()
            cleanup["terminate_sent"] = True
        except OSError as exc:
            cleanup["terminate_error_type"] = type(exc).__name__
            cleanup["escalated"] = True
    try:
        if cleanup["escalated"]:
            raise subprocess.TimeoutExpired("terminate", 0)
        wait_returncode = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        cleanup["escalated"] = True
        if process.poll() is None:
            cleanup["kill_attempted"] = True
            process.kill()
            cleanup["kill_sent"] = True
        wait_returncode = process.wait(timeout=5)
    cleanup["wait_completed"] = True
    cleanup["wait_returncode"] = wait_returncode
    cleanup["post_wait_poll"] = process.poll()
    need(type(wait_returncode) is int and type(cleanup["post_wait_poll"]) is int
         and cleanup["post_wait_poll"] == wait_returncode,
         "child wait not confirmed")
    cleanup["child_stopped"] = True


def main(argv=None):
    parser = argparse.ArgumentParser()
    for flag in ("root", "inputs", "matrix", "out"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    summary = {"version": "som.rs22.fresh-baseline-capture.v1", "status": "unrun", "product_acceptance_passed": False,
               "role": "fresh fixed 2bce baseline observation; not historical wire recovery", "source_commit": None,
               "baseline_binary_sha256": BASELINE, "rows": [], "ports": {"before": {}, "after": {}},
               "cleanup": {"child_started": False, "child_stopped": False, "escalated": False}}
    process, root, stage = None, None, "host"
    try:
        need(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
             and os.environ.get("RUNNER_OS") == "macOS" and os.environ.get("GITHUB_REPOSITORY") == "faberline/som"
             and platform.system() == "Darwin" and platform.machine() == "arm64"
             and platform.mac_ver()[0].split(".")[0] == "26", "GitHub macOS host required")
        repo = args.root
        guarded_output(repo, args.out)
        args.out.mkdir(parents=True, exist_ok=False)
        root = args.out
        run = root / "run"
        run.mkdir()
        stage = "source"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()
        need(re.fullmatch(r"[0-9a-f]{40}", head) is not None and head == os.environ.get("GITHUB_SHA"), "checkout differs")
        summary["source_commit"] = head
        rows = input_rows(repo, args.inputs, args.matrix)
        summary["rows"] = [{"id": r[0]["id"], "native": {"status": "unrun"}, "typed": {"status": "unrun"},
                            "typed_rust_input_sha256": sha(r[3]), "prompt_sha256": r[0]["frozen_prompt_sha256"],
                            "historical_issue57_defect": r[0]["historical_python_defect_repaired_issue57"]} for r in rows]
        baseline = repo / ".local/m6-hosted/baseline"
        stage = "baseline_identity"
        health = identity(baseline)
        summary["identity_before"] = identity_observation(baseline, run / "identity-before")
        stage = "ports"
        for port in (PORT, 8765, 18765):
            summary["ports"]["before"][str(port)] = no_listener(port)
        stage = "start"
        keep = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "SDKROOT", "DEVELOPER_DIR")
        environment = {key: os.environ[key] for key in keep if key in os.environ}
        environment["SOM_TS_PRETTIER"] = str(repo / ".local/m6-hosted/prettier")
        with (run / "baseline.stdout").open("wb") as stdout, (run / "baseline.stderr").open("wb") as stderr:
            process = subprocess.Popen([str(baseline / "som"), "serve", "--assets", str(baseline / "assets"),
                                        "--port", str(PORT)], cwd=baseline, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            summary["child_pid"] = process.pid
            deadline = time.monotonic() + 30
            while True:
                need(process.poll() is None and time.monotonic() < deadline, "baseline startup failed")
                try:
                    status, raw = http("GET", "/healthz")
                except OSError:
                    time.sleep(0.1)
                    continue
                health_folder = run / "health"
                health_folder.mkdir()
                summary["health"] = {"status": status, "response": save(health_folder / "response.raw", raw)}
                need(status == 200 and parse(raw) == health, "baseline health differs")
                break
            summary["ports"]["owned"] = owned_listener(process.pid)
            stage = "capture"
            for i, (row, case, native, rust_raw, typed) in enumerate(rows):
                folder = run / row["id"]
                folder.mkdir()
                summary["rows"][i]["typed_rust_input"] = save(folder / "typed-rust.input.raw", rust_raw)
                for form, request in (("native", native), ("typed", typed)):
                    execute_case(folder, row, case, form, request, result=summary["rows"][i][form])
                if row["expected_control_status"] == 200:
                    need(summary["rows"][i]["native"]["files_map_sha256"] == summary["rows"][i]["typed"]["files_map_sha256"],
                         "native and typed files differ")
        stage = "post_identity"
        need(identity(baseline) == health, "baseline changed")
        summary["identity_after"] = identity_observation(baseline, run / "identity-after")
        need(input_rows(repo, args.inputs, args.matrix) == rows, "capture source inputs changed")
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout.decode().strip()
        need(after == head, "checkout changed")
        summary["source_commit_after"] = after
        summary["status"] = "captured"
    except Exception as exc:
        summary["status"] = "failed"
        summary["failed_stage"] = stage
        summary["failure_type"] = type(exc).__name__
    finally:
        if process is not None:
            try:
                stop_child(process, summary["cleanup"])
                if summary["cleanup"]["escalated"]:
                    summary["status"] = "failed"
            except (OSError, ValueError, subprocess.SubprocessError):
                summary["status"] = "failed"
                summary["cleanup"]["child_stopped"] = False
        if root is not None:
            try:
                for port in (PORT, 8765, 18765):
                    summary["ports"]["after"][str(port)] = no_listener(port)
                summary["cleanup"]["listeners_absent"] = True
            except (OSError, ValueError, subprocess.SubprocessError):
                summary["status"] = "failed"
                summary["cleanup"]["listeners_absent"] = False
            (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": summary["status"], "failed_stage": summary.get("failed_stage"),
                      "role": summary["role"], "product_acceptance_passed": False}))
    return 0 if summary["status"] == "captured" and summary["cleanup"].get("child_stopped") and summary["cleanup"].get("listeners_absent") else 1


if __name__ == "__main__":
    sys.exit(main())
