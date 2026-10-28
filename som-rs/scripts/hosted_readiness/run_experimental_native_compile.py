#!/usr/bin/env python3
"""Run every Python/Rust TS065 compile comparison on an owned GitHub service.

This is an experimental, unsealed compile experiment. It never accepts products.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

import experimental_native_compile as experiment
import map_rust_inputs as mapper
from export_goldens import Refusal, digest_text, no_symlinks, parse, relative, require, sha, value_sha

DRIVER = "som-rs/scripts/hosted_readiness/run_experimental_native_compile.py"
TARGET_MAIN = "som-rs/som-targets/src/main.rs"
EXPECTED = {"python": 1342, "rust": 699}
TIMEOUT = 120
TOOLS = {"SOM_RUFF": "ruff", "SOM_RUSTFMT": "rustfmt", "SOM_TS_NODE": "node", "SOM_TS_PRETTIER": "prettier"}


class DriverInterrupted(InterruptedError):
    pass


class RowsStopped(BaseException):
    def __init__(self, records, cause):
        self.records, self.cause = records, cause


@contextmanager
def cancellation_handlers():
    state = {"signal": None, "raised": False, "cleanup": False}
    def interrupt(number, _frame):
        if state["signal"] is None:
            state["signal"] = number
        if state["cleanup"] or state["raised"]:
            return
        state["raised"] = True
        raise DriverInterrupted("owned compile driver interrupted")
    watched = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
    previous = {number: signal.getsignal(number) for number in watched}
    for number in watched:
        signal.signal(number, interrupt)
    try:
        yield state
    finally:
        state["cleanup"] = True
        for number, handler in previous.items():
            signal.signal(number, handler)


def asset_manifest_canonical_sha(assets):
    file = assets / "manifest.json"
    no_symlinks(file)
    return experiment.value_sha(experiment.parse(file.read_bytes()))


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def compact(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def sorted_objects(value):
    if type(value) is dict:
        return {key: sorted_objects(value[key]) for key in sorted(value)}
    if type(value) is list:
        return [sorted_objects(item) for item in value]
    return value


def dump(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def read_request(root, locator, expected):
    relative(locator["path"])
    file = root / locator["path"]
    no_symlinks(file)
    raw = file.read_bytes()
    require(sha(raw) == digest_text(locator.get("raw_sha256", locator.get("sha256"))) == digest_text(expected),
            "request bytes differ from independent accepted QA request")
    return raw


def baseline(repo):
    file = repo / experiment.BASELINE
    no_symlinks(file)
    raw = file.read_bytes()
    require(sha(raw) == experiment.BASELINE_SHA256, "accepted TS065 artifact digest differs")
    document = experiment.parse(raw)
    require(document["version"] == "som.ts065-accepted-response-hashes.v1" and document["product_acceptance_passed"] is False,
            "accepted baseline identity differs")
    rows = [("python", row) for row in document["baselines"]["python"]["rows"]]
    rust = document["baselines"]["rust"]["rows"]
    require(len(rust) == 2093, "accepted Rust baseline whole scope differs")
    rows += [("rust", row) for row in rust if row["check"] == "compile_equal"]
    require(dict(Counter(target for target, _ in rows)) == EXPECTED, "compile experiment whole scope differs")
    keys = []
    for target, row in rows:
        fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
        identity = {key: row[key] for key in fields}
        key = (target, *(identity[name] for name in fields))
        require(key not in keys and row["passed"] is True and row["status"] == "passed", "baseline row is duplicated or unpassed")
        keys.append(key)
        names = ("qa_compile_request_sha256", "baseline_compile_request_sha256", "compile_request_sha256") if target == "python" else ("qa_request_sha256", "baseline_request_sha256", "request_sha256")
        require(len({digest_text(row[name]) for name in names}) == 1, "accepted QA request authorities disagree")
    return document, rows, keys


def python_request(original, prompt, variant, expected):
    require(variant in {"none", "python"}, "unknown Python baseline variant")
    base = {"format": "som.prompt-json.v1", "prompt": prompt}
    candidates = []
    if variant == "none":
        candidates.append(("original-verified-raw", original))
    for name, value in (("compact-preserved-prompt-order", prompt), ("compact-recursive-sorted-prompt-keys", sorted_objects(prompt))):
        body = {"format": "som.prompt-json.v1", "prompt": value}
        if variant == "python":
            body["target"] = "python"
        require(value_sha(value) == value_sha(prompt), "Python request encoding changed values")
        candidates.append((name, compact(body)))
    matches = []
    expected_body = {**base, **({"target": "python"} if variant == "python" else {})}
    for name, raw in candidates:
        require(value_sha(parse(raw)) == value_sha(expected_body), "Python request candidate changed its envelope or values")
        if sha(raw) == expected:
            matches.append((name, raw))
    require(matches, "no allowed encoding matches the complete frozen Python QA request hash")
    return matches[0]


def prepare_jobs(repo, python_inputs, rust_inputs, rows, tested_sha):
    goldens = mapper.load_goldens(repo / "som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json")
    reused = mapper.Reused(python_inputs, goldens)
    file = rust_inputs / "manifest.json"
    no_symlinks(file)
    rust_raw = file.read_bytes()
    completed = parse(rust_raw)
    require(completed["version"] == "som.public-rust-input-completion.v1"
            and completed["tested_sha"] == tested_sha
            and completed["product_acceptance_passed"] is False and completed["paired_request_set_ready"] is True
            and completed["golden_export_sha256"] == mapper.GOLDENS_SHA256
            and completed["baseline_artifact"] == {"path": experiment.BASELINE, "raw_sha256": experiment.BASELINE_SHA256},
            "Rust completion manifest identity differs")
    rust_rows, expected_rust = {}, [(row["phase"], row["id"]) for target, row in rows if target == "rust"]
    actual_rust = []
    for cohort, phase, count in [("rust_phase1", "phase1", 115), ("rust_phase2", "phase2", 584)]:
        items = completed["cohorts"][cohort]
        require(len(items) == count and [item["index"] for item in items] == list(range(count)), "Rust completed row order differs")
        for item in items:
            key = (phase, item["id"])
            require(key not in rust_rows, "Rust completed identity duplicated")
            rust_rows[key] = item
            actual_rust.append(key)
    require(actual_rust == expected_rust, "Rust completed named scope differs")
    expected_python = [(source, identity, variant) for cohort, source in [("python_public", "public"), ("python_legacy", "legacy")]
                       for identity in reused.by_cohort[cohort] for variant in ["none", "python"]]
    require(expected_python == [(row["source"], row["id"], row["variant"]) for target, row in rows if target == "python"],
            "Python accepted ordered identities differ")
    jobs = []
    for target, row in rows:
        fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
        job = {"target": target, "identity": {field: row[field] for field in fields}, "accepted_row_index": row["row_index"],
               "expected_request_sha256": row["qa_compile_request_sha256"] if target == "python" else row["qa_request_sha256"],
               "expected_http_status": row["qa_compile_status"] if target == "python" else row["qa_status"],
               "accepted_qa_response_sha256": row["qa_compile_response_sha256"] if target == "python" else row["qa_response_sha256"]}
        try:
            if target == "python":
                cohort = "python_public" if row["source"] == "public" else "python_legacy"
                source = reused.by_cohort[cohort][row["id"]]
                locator = source["locators"]["compile-json"]
                original = reused.read(locator["path"], locator["raw_sha256"])
                job["encoding"], raw = python_request(original, source["prompt"], row["variant"], job["expected_request_sha256"])
            else:
                item = rust_rows[(row["phase"], row["id"])]
                locator = item["baseline_json_request"]
                provenance = locator["provenance"]
                require(provenance["artifact"] == experiment.BASELINE and provenance["artifact_raw_sha256"] == experiment.BASELINE_SHA256
                        and provenance["phase"] == row["phase"] and provenance["id"] == row["id"]
                        and provenance["check"] == "compile_equal" and provenance["accepted_qa_request_sha256"] == job["expected_request_sha256"],
                        "Rust completed QA request provenance differs")
                raw = read_request(rust_inputs, locator, job["expected_request_sha256"])
                body = parse(raw)
                require(list(body) == ["format", "target", "prompt"] and body["format"] == "som.prompt-json.v1"
                        and body["target"] == "rust" and value_sha(body["prompt"]) == item["expected_prompt_sha256"],
                        "Rust accepted request header or prompt differs")
                job["encoding"] = "complete-frozen-QA-request"
            require(sha(raw) == job["expected_request_sha256"], "complete QA request hash differs before scheduling")
            job["raw"] = raw
        except InterruptedError:
            raise
        except Exception as error:
            job["input_error"] = type(error).__name__
            if isinstance(error, (Refusal, experiment.Refusal)):
                job["input_reason"] = str(error)
        jobs.append(job)
    reused.verify()
    require(file.read_bytes() == rust_raw, "Rust completed manifest changed during planning")
    return jobs, {"python_input_manifest_sha256": reused.manifest_sha256, "rust_input_manifest_sha256": sha(rust_raw)}


def file_identity(path):
    no_symlinks(path)
    require(path.is_file(), "regular identity file required")
    info = path.stat()
    return {"sha256": sha(path.read_bytes()), "size": info.st_size, "mode": stat.S_IMODE(info.st_mode), "device": info.st_dev, "inode": info.st_ino}


def tree_identity(root):
    no_symlinks(root)
    require(root.is_dir(), "identity tree directory required")
    result = {}
    for path in sorted(root.rglob("*")):
        no_symlinks(path)
        require(path.is_dir() or path.is_file(), "nonregular identity tree member")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_identity(path)
    require(result, "identity tree is empty")
    return result


def source_snapshot(checker):
    require(checker.repo.git("rev-parse", "--verify", "HEAD").decode().strip() == checker.repo.commit, "candidate Git HEAD changed")
    result = {}
    for path, expected in sorted(checker.repo.pins.items()):
        file = checker.repo.root / path
        no_symlinks(file)
        result[path] = sha(file.read_bytes())
        require(result[path] == expected, "candidate owning source changed")
    return result


def listeners(port, proc=Path("/proc")):
    result = []
    for table in ("tcp", "tcp6"):
        for line in (proc / "net" / table).read_text().splitlines()[1:]:
            fields = line.split()
            address, port_hex = fields[1].split(":")
            if fields[3] == "0A" and int(port_hex, 16) == port:
                result.append({"table": table, "address": address, "inode": fields[9]})
    return result


def process_stamp(pid, proc=Path("/proc")):
    fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "state": fields[0], "pgrp": int(fields[2]), "session": int(fields[3]), "start_ticks": int(fields[19])}


def socket_owner(pid, port, stamp, binary, proc=Path("/proc")):
    current = process_stamp(pid, proc)
    require(current["start_ticks"] == stamp["start_ticks"] and current["pgrp"] == current["session"] == pid
            and current["state"] != "Z", "owned service process identity differs")
    executable = proc / str(pid) / "exe"
    info = executable.stat()
    require((info.st_dev, info.st_ino) == (binary["device"], binary["inode"]), "owned service executable differs")
    sockets = set()
    for fd in (proc / str(pid) / "fd").iterdir():
        try:
            match = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(fd))
            if match:
                sockets.add(match[1])
        except FileNotFoundError:
            pass
    found = listeners(port, proc)
    require(len(found) == 1 and found[0]["table"] == "tcp" and found[0]["address"] == "0100007F"
            and found[0]["inode"] in sockets, "child does not own the exact loopback listening socket")
    return {**current, "listener": found[0], "executable_inode": info.st_ino}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class OwnedService:
    def __init__(self, binary, assets, tools, port, out):
        require(sys.platform == "linux" and type(port) is int and 1024 <= port <= 65535 and port not in {8765, 18765}, "Linux owned loopback port required")
        require(not listeners(port), "requested port is occupied")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        self.binary, self.port, self.out = file_identity(binary), port, out
        require(self.binary["mode"] & 0o111 and binary.read_bytes()[:4] == b"\x7fELF", "native executable ELF candidate required")
        self.process, self.stamp, self.log = None, None, None
        self.argv = [str(binary), "serve", "--assets", str(assets), "--port", str(port)]
        self.environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "RUST_BACKTRACE": "1", "TMPDIR": str(out / "tmp"), **{key: str(value) for key, value in tools.items()}}
        (out / "tmp").mkdir()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def start(self):
        self.log = (self.out / "server.log").open("xb")
        self.process = subprocess.Popen(self.argv, cwd=self.out, env=self.environment, stdin=subprocess.DEVNULL,
                                        stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        self.stamp = process_stamp(self.process.pid)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            require(self.process.poll() is None, "owned service exited before listening")
            try:
                return self.ownership()
            except InterruptedError:
                raise
            except (OSError, ValueError):
                time.sleep(0.1)
        raise Refusal("owned service did not establish a verified listening socket")

    def ownership(self):
        require(self.process is not None and self.process.poll() is None, "owned service is not running")
        return socket_owner(self.process.pid, self.port, self.stamp, self.binary)

    def call(self, route, raw=None):
        ownership = self.ownership()
        require(route in {"/healthz", "/v1/compile"}, "unapproved service route")
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}" + route, data=raw,
                                        headers={"Content-Type": "application/json"}, method="GET" if raw is None else "POST")
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                return response.status, response.read(), ownership
        except urllib.error.HTTPError as error:
            return error.code, error.read(), ownership

    def close(self):
        receipt = {"owned_pid": self.process.pid if self.process else None, "signal_target": "owned new process group only", "cleanup_passed": True, "signals": []}
        try:
            if self.process is not None and self.process.poll() is None:
                current = process_stamp(self.process.pid)
                require(current["start_ticks"] == self.stamp["start_ticks"] and current["pgrp"] == current["session"] == self.process.pid,
                        "cleanup process ownership differs")
                os.killpg(self.process.pid, signal.SIGTERM)
                receipt["signals"].append("SIGTERM")
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    current = process_stamp(self.process.pid)
                    require(current["start_ticks"] == self.stamp["start_ticks"] and current["pgrp"] == current["session"] == self.process.pid,
                            "cleanup escalation ownership differs")
                    os.killpg(self.process.pid, signal.SIGKILL)
                    receipt["signals"].append("SIGKILL")
                    self.process.wait(timeout=10)
            if self.process is not None:
                receipt["exit_code"] = self.process.poll()
                # Never target an unknown listener or a group after losing its
                # leader identity. Any remaining group makes cleanup fail.
                remaining = []
                for directory in Path("/proc").iterdir():
                    if directory.name.isdecimal():
                        try:
                            item = process_stamp(int(directory.name))
                            if item["session"] == self.process.pid and item["state"] != "Z":
                                remaining.append(item)
                        except (OSError, ValueError, IndexError):
                            pass
                receipt["remaining_owned_session_members"] = remaining
                receipt["cleanup_passed"] = not remaining and self.process.poll() is not None
        except Exception as error:
            receipt.update(cleanup_passed=False, error=type(error).__name__)
        finally:
            if self.log is not None:
                self.log.close()
        return receipt


def run_rows(jobs, checker, service, out, *, start_index=0):
    records = []
    for index, job in enumerate(jobs, start=start_index):
        directory = out / "rows" / f"{index:04d}"
        started = time.monotonic()
        record = {key: value for key, value in job.items() if key != "raw"}
        record.update(index=index, started_at=utc(), state="unrun", http_status=None)
        try:
            directory.mkdir(parents=True)
            try:
                require("input_error" not in job and "raw" in job, "input did not match accepted request hash")
                require(sha(job["raw"]) == job["expected_request_sha256"], "accepted request changed before sending")
                (directory / "request.raw").write_bytes(job["raw"])
                require(service is not None, "owned candidate service unavailable")
                status, raw, ownership = service.call("/v1/compile", job["raw"])
                (directory / "response.raw").write_bytes(raw)
                record.update(http_status=status, current_response_sha256=sha(raw), owned_socket=ownership, state="failed")
                checked = checker.verify_response(job["target"], job["identity"], status, raw)
                dump(directory / "checker.json", checked.report)
                record["state"] = "passed"
            except InterruptedError:
                raise
            except Exception as error:
                record["error"] = type(error).__name__
                if isinstance(error, (Refusal, experiment.Refusal)):
                    record["error_reason"] = str(error)
                if "raw" in job and service is not None and record["state"] == "unrun":
                    record["state"] = "failed_transport_or_ownership"
            record.update(finished_at=utc(), elapsed_seconds=time.monotonic() - started)
            dump(directory / "result.json", record)
            records.append(record)
        except BaseException as error:
            record.update(state="interrupted" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "recording_failed",
                          error=type(error).__name__, finished_at=utc(), elapsed_seconds=time.monotonic() - started)
            records.append(record)
            raise RowsStopped(records, error) from error
    return records


def _run(repo, python_inputs, rust_inputs, binary, assets, tools, port, out, tested_sha, cancellation):
    require(re.fullmatch(r"[0-9a-f]{40}", tested_sha) is not None, "exact tested Git SHA required")
    require(not out.exists() and all(out != root and root not in out.parents for root in [repo, python_inputs, rust_inputs, assets, *tools.values()]), "new output outside source/input/asset/tool trees required")
    no_symlinks(out)
    out.mkdir(parents=True)
    started, checker, service, jobs, records = time.monotonic(), None, None, [], []
    report = {"state": "experimental-unsealed", "product_acceptance_passed": False, "policy_state": "unsealed",
              "tested_sha": tested_sha, "started_at": utc(), "expected_rows": EXPECTED,
              "remaining": {"assembly": "unrun", "cnl_semantic_and_refusal_checks": "unrun", "products_and_behavior": "unrun", "typescript_full_scope": "unrun"}}
    try:
        document, accepted, keys = baseline(repo)
        jobs = [{"target": target, "identity": {key: row[key] for key in (("source", "id", "variant") if target == "python" else ("phase", "id"))},
                 "accepted_row_index": row["row_index"], "expected_request_sha256": row["qa_compile_request_sha256"] if target == "python" else row["qa_request_sha256"],
                 "input_error": "SetupNotRun"} for target, row in accepted]
        checker = experiment.ExperimentalChecker(repo)
        require(checker.repo.commit == tested_sha and set(checker.rows) == set(keys), "checker candidate or whole scope differs")
        require(checker.repo.read(DRIVER) == Path(__file__).read_bytes(), "running HTTP driver differs from committed source")
        checker.repo.read(TARGET_MAIN)
        for name in ["map_rust_inputs.py", "reconstruct_public_inputs.py", "export_goldens.py"]:
            checker.repo.read("som-rs/scripts/hosted_readiness/" + name)
        jobs, input_record = prepare_jobs(repo, python_inputs, rust_inputs, accepted, tested_sha)
        report["inputs"] = input_record
        initial = {"source": source_snapshot(checker), "binary": file_identity(binary), "assets": tree_identity(assets),
                   "tools": {key: tree_identity(path) if key == "SOM_TS_PRETTIER" else file_identity(path) for key, path in tools.items()}}
        artifacts = experiment.parse(checker.repo.read(experiment.ARTIFACTS))["tools"]
        for key, name in TOOLS.items():
            pin = next(item for item in artifacts if item["tool"] == name and item["host"] == ("platform-independent JavaScript" if name == "prettier" else experiment.HOST))
            observed = (sha("".join(path + "\0" + item["sha256"] + "\n" for path, item in initial["tools"][key].items()).encode())
                        if name == "prettier" else initial["tools"][key]["sha256"])
            require(observed == pin["tree_sha256" if name == "prettier" else "executable_sha256"], "selected native tool differs from committed pin")
        report.update(initial_identities=initial, independent_source_record=checker.source_record(),
                      asset_manifest_canonical_sha256=asset_manifest_canonical_sha(assets))
        service = OwnedService(binary, assets, tools, port, out)
        report["launch"] = {"argv": service.argv, "environment": service.environment}
        report["owned_start"] = service.start()
        status, raw, owner = service.call("/healthz")
        (out / "health.response.raw").write_bytes(raw)
        health = experiment.parse(raw)
        require(status == 200 and health["status"] == "ok" and health["model_loaded"] is False and health["blocks_loaded"] is True
                and health["backend_sha256"] == checker.after["python_backend"]
                and health["asset_manifest_sha256"] == report["asset_manifest_canonical_sha256"]
                and health["formatter_sha256"] == initial["tools"]["SOM_RUFF"]["sha256"], "owned health consistency differs")
        report["health_consistency"] = {"http_status": status, "raw_sha256": sha(raw), "owned_socket": owner, "expected_pin_authority": "committed source/artifacts, never health"}
        records = run_rows(jobs, checker, service, out)
    except BaseException as error:
        if isinstance(error, RowsStopped):
            records, error = error.records, error.cause
        report["setup_error"] = type(error).__name__
        if isinstance(error, (Refusal, experiment.Refusal)):
            report["setup_reason"] = str(error)
    finally:
        cancellation["cleanup"] = True
        try:
            report["cleanup"] = service.close() if service is not None else {"cleanup_passed": True, "owned_pid": None}
        except BaseException as error:
            report["cleanup"] = {"cleanup_passed": False, "error": type(error).__name__}
        try:
            if len(records) < len(jobs):
                records += run_rows(jobs[len(records):], checker, None, out, start_index=len(records))
        except RowsStopped as error:
            records += error.records
            report["row_recording_error"] = type(error.cause).__name__
        except BaseException as error:
            report["row_recording_error"] = type(error).__name__
        if "initial_identities" in report:
            try:
                final = {"source": source_snapshot(checker), "binary": file_identity(binary), "assets": tree_identity(assets),
                         "tools": {key: tree_identity(path) if key == "SOM_TS_PRETTIER" else file_identity(path) for key, path in tools.items()}}
                report["final_identities"] = final
                report["identity_stability_passed"] = final == report["initial_identities"]
            except Exception as error:
                report.update(identity_stability_passed=False, final_identity_error=type(error).__name__)
    report.update(rows=records, actual_rows=dict(Counter(row["target"] for row in records)),
                  passed=sum(row["state"] == "passed" for row in records), finished_at=utc(), elapsed_seconds=time.monotonic() - started)
    report["interrupted_signal"] = cancellation["signal"]
    report["experiment_complete"] = (report["actual_rows"] == EXPECTED and report["passed"] == 2041
                                     and report.get("identity_stability_passed") is True and report["cleanup"]["cleanup_passed"]
                                     and "setup_error" not in report and cancellation["signal"] is None)
    dump(out / "manifest.json", report)
    print(json.dumps({key: report[key] for key in ["state", "policy_state", "product_acceptance_passed", "actual_rows", "passed", "experiment_complete"]}))
    return report


def run(repo, python_inputs, rust_inputs, binary, assets, tools, port, out, tested_sha):
    with cancellation_handlers() as cancellation:
        return _run(repo, python_inputs, rust_inputs, binary, assets, tools, port, out, tested_sha, cancellation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["repo", "python-inputs", "rust-inputs", "binary", "assets", "out", "ruff", "rustfmt", "node", "prettier"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        require(".." not in args.out.parts, "output traversal refused")
        tools = {key: getattr(args, name).absolute() for key, name in TOOLS.items()}
        report = run(args.repo.resolve(strict=True), args.python_inputs.resolve(strict=True), args.rust_inputs.resolve(strict=True),
                     args.binary.absolute(), args.assets.absolute(), tools, args.port, args.out.absolute(), args.tested_sha)
        return 0 if report["experiment_complete"] else 1
    except Exception as error:
        print(json.dumps({"state": "experimental-unsealed", "product_acceptance_passed": False, "experiment_complete": False, "error": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
