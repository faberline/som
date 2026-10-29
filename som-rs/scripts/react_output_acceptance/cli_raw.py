"""Keep exact CLI streams and its forwarding exchange. No product code changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def check_case(helper, binary: str, base_url: str, scratch: Path, label: str, source: str,
               target: str | None, expect: str | None = None, verify_request: bool = True) -> dict:
    digest = lambda data: hashlib.sha256(data).hexdigest()
    identity = digest(label.encode())[:16]
    cnl = scratch / f"{identity}.cnl"
    cnl.write_text(source, encoding="utf-8")
    request = {"format": "som.cnl.v1", "source": source}
    if target is not None:
        request["target"] = target
    expected_request = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    done, captured = helper._run_cli_with_request_capture(binary, base_url, cnl, target)
    status, http_raw = helper.post(base_url, expected_request)
    row = {"input": label, "target": target, "http_status": status, "exit_code": done.returncode,
           "expected_request_sha256": digest(expected_request), "request_sha256": None,
           "stdout_sha256": digest(done.stdout), "stderr_sha256": digest(done.stderr),
           "http_response_sha256": digest(http_raw), "capture_count": len(captured)}
    problems = []
    raw = {"stdout": done.stdout, "stderr": done.stderr, "http_response": http_raw}
    if len(captured) != 1:
        problems.append("CLI must send exactly one request")
    else:
        route, body = captured[0]
        raw["request"] = body
        row["request_route"] = route
        row["request_sha256"] = digest(body)
        if route != "/v1/compile" or body != expected_request:
            problems.append("CLI forwarding request differs")
    for name, data in raw.items():
        filename = scratch / f"{identity}.{name}.raw"
        with filename.open("xb") as stream:
            stream.write(data)
        row[f"{name}_file"] = filename.relative_to(scratch.parent).as_posix()
    try:
        response = json.loads(http_raw)
        if status == 200:
            row["bundle_version"] = response.get("bundle", {}).get("version")
            if done.returncode != 0 or json.loads(done.stdout) != response.get("bundle"):
                problems.append("CLI success output differs from HTTP bundle")
        else:
            row["code"] = response.get("code")
            if done.returncode != 2 or json.loads(done.stderr) != response:
                problems.append("CLI refusal output differs from HTTP reply")
        if expect is not None and row.get("code") != expect:
            problems.append("CLI refusal code differs")
    except (ValueError, TypeError, AttributeError):
        problems.append("CLI or HTTP output is invalid JSON")
    row.update(problems=problems, passed=not problems)
    return row
