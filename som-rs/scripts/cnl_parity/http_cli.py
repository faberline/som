"""Exercise the public HTTP and CLI paths against fixed parity records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def post(url, route, value, canonical=False):
    body = canonical_bytes(value) if canonical else json.dumps(value).encode()
    request = urllib.request.Request(url + route, data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def command(binary, arguments, environment, expected_exit):
    result = subprocess.run([str(binary), *arguments], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    assert result.returncode == expected_exit, result.stderr.decode(errors="replace")
    if expected_exit:
        assert not result.stdout, "A refused CLI request wrote a partial result to stdout"
        return json.loads(result.stderr)
    return json.loads(result.stdout)


def check(binary: Path, assets: Path, corpus: dict, report: dict, output: Path, environment,
          baseline_bundles: Path | None = None):
    if baseline_bundles is not None:
        for target in ("python", "rust", "typescript", "react"):
            assert (baseline_bundles / f"{target}.bundle.json").is_file(), target
    output.mkdir()
    environment = environment.copy()
    environment["NO_PROXY"] = "127.0.0.1,localhost"
    environment["no_proxy"] = environment["NO_PROXY"]
    sources = {case["id"]: case["source"] for case in corpus["cases"]}
    evidence = []
    with (output / "server.log").open("wb") as server_log:
        server = subprocess.Popen([str(binary), "serve", "--assets", str(assets), "--port", "0"],
                                  env=environment, stdout=server_log, stderr=server_log)
        try:
            deadline = time.monotonic() + 30
            url = None
            while time.monotonic() < deadline:
                for line in (output / "server.log").read_text().splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("stage") == "listening":
                        url = "http://" + event["address"]
                        break
                if url:
                    break
                if server.poll() is not None:
                    raise RuntimeError((output / "server.log").read_text())
                time.sleep(0.05)
            assert url, "The local compiler did not start within 30 seconds"
            for target in ("python", "rust", "typescript", "react"):
                original = set(corpus["original_ids"])
                selected = next(key for key in sorted(report)
                                if key.startswith(target + "/") and "files" in report[key]
                                and key.split("/", 1)[1] in original)
                case = selected.split("/", 1)[1]
                expected = report[selected]
                source = output / f"{target}.cnl"
                source.write_text(sources[case])
                status, compiled = post(url, "/v1/compile", {
                    "format": "som.cnl.v1", "source": sources[case], "target": target,
                })
                assert status == 200, (target, compiled)
                bundle = compiled["bundle"]
                assert bundle["prompt"] == json.loads(expected["prompt"]), target
                assert bundle["dsl"] == json.loads(expected["dsl"]), target
                status, assembled = post(url, "/v1/assemble", {"bundle": bundle})
                assert status == 200 and assembled["files"] == expected["files"], target
                cli_bundle = command(binary, ["compile", str(source), "--target", target,
                                               "--url", url], environment, 0)
                assert cli_bundle == bundle, target
                bundle_file = output / f"{target}.bundle.json"
                bundle_file.write_text(json.dumps(cli_bundle))
                cli_files = command(binary, ["assemble", str(bundle_file), "--url", url],
                                    environment, 0)
                assert cli_files["files"] == expected["files"], target

                negative = "expanded/mixed/unknown-contract-then-bad-argument"
                rejected = report[f"{target}/{negative}"]["compile_error"]
                status, body = post(url, "/v1/compile", {
                    "format": "som.cnl.v1", "source": sources[negative], "target": target,
                })
                assert [status, body] == rejected, target
                source.write_text(sources[negative])
                cli_error = command(binary, ["compile", str(source), "--target", target,
                                              "--url", url], environment, 2)
                assert cli_error == rejected[1], target
                result = {"target": target, "positive_case": case,
                          "negative_case": negative, "passed": True}
                if baseline_bundles is not None:
                    old_file = baseline_bundles / f"{target}.bundle.json"
                    old_bytes = old_file.read_bytes()
                    old_bundle = json.loads(old_bytes)
                    # run.py compares this complete selected record with the
                    # retained baseline. Bind the old bundle to that same case.
                    for field in ("prompt", "dsl"):
                        assert canonical_bytes(old_bundle[field]) == canonical_bytes(
                            json.loads(expected[field])), (target, field)
                    assert old_bundle.get("target", "python") == target, target
                    identity_changed = old_bundle["assets"] != bundle["assets"]
                    # The CLI sends compact, sorted UTF-8 JSON. Match its bytes
                    # so diagnostic spans can be compared with the full body.
                    status, body = post(url, "/v1/assemble", {"bundle": old_bundle}, canonical=True)
                    if identity_changed:
                        assert status == 400 and body["code"] == "ASSET_HASH_MISMATCH", (target, status, body)
                    else:
                        assert status == 200 and body["files"] == expected["files"], (target, status, body)
                    cli_result = command(binary, ["assemble", str(old_file), "--url", url],
                                         environment, 2 if identity_changed else 0)
                    assert cli_result == body, target
                    result.update({
                        "old_bundle_identity_changed": identity_changed,
                        "old_bundle_replay": "refused" if identity_changed else "assembled",
                        "old_bundle_path": str(old_file),
                        "old_bundle_sha256": hashlib.sha256(old_bytes).hexdigest(),
                        "old_bundle_response": [status, body],
                    })
                evidence.append(result)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    (output / "results.json").write_text(json.dumps(evidence, indent=2) + "\n")
