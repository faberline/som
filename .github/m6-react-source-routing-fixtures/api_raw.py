"""Independent checks of retained API wire bytes and OS snapshots. No tool imports."""
from __future__ import annotations

import base64
import errno
import hashlib
import json
from pathlib import Path
import shlex
import urllib.parse


class ApiRawError(ValueError):
    def __init__(self, message: str, status: str = "failed"):
        super().__init__(message)
        self.status = status


def need(value, message: str, status: str = "failed") -> None:
    if not value:
        raise ApiRawError(message, status)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def integer(value) -> bool:
    return type(value) is int


def retained(reader, directory: Path, record: dict) -> bytes:
    need(isinstance(record, dict) and isinstance(record.get("file"), str), "raw API file reference missing", "unrun")
    relative = Path(record["file"])
    need(not relative.is_absolute() and ".." not in relative.parts, "raw API path escapes its directory")
    path = reader.path(relative, directory)
    need(path.is_relative_to(directory), "raw API path escapes its directory")
    raw = reader.bytes(path)
    need(digest(raw) == record.get("sha256"), "raw API file hash differs")
    return raw


def body(record: dict) -> bytes:
    need(isinstance(record, dict) and isinstance(record.get("base64"), str), "wire body missing", "unrun")
    try:
        raw = base64.b64decode(record["base64"], validate=True)
    except ValueError as exc:
        raise ApiRawError("wire body is not valid base64") from exc
    need(digest(raw) == record.get("sha256"), "wire body hash differs")
    return raw


def js_string(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def error_from(status: int, payload, fields: list[dict]) -> dict:
    result = {"status": status, "detail": f"HTTP {status}", "fieldErrors": {}, "otherErrors": []}
    if not isinstance(payload, dict) or "detail" not in payload:
        return result
    details = payload["detail"]
    if isinstance(details, str):
        result["detail"] = details
        return result
    if not isinstance(details, list):
        return result
    result["detail"] = "validation failed"
    names = {field["name"] for field in fields}
    for item in details:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc") if isinstance(item.get("loc"), list) else []
        message = js_string(item["msg"]) if "msg" in item else "invalid"
        if len(loc) == 2 and loc[0] == "body" and isinstance(loc[1], str) and loc[1] in names:
            result["fieldErrors"].setdefault(loc[1], []).append(message)
        else:
            result["otherErrors"].append((".".join(map(js_string, loc)) + ": " if loc else "") + message)
    return result


def sample(field: dict, tag: str):
    kind = field["type"]
    if kind == "str":
        return f"{tag} {field['name']}"
    if kind == "int":
        return {"min": 3, "extra": 11, "changed": 19}.get(tag, 7)
    if kind == "float":
        return {"min": 0.5, "extra": 2.5, "changed": 4.5}.get(tag, 1.5)
    if kind == "bool":
        return not field["default"] if "default" in field else tag != "min"
    need(kind == "date", "unsupported API sample field type")
    return {"min": "2026-03-01", "changed": "2026-04-01"}.get(tag, "2026-01-05")


def same(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return list(left) == list(right) and all(same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return left == right
    return type(left) is type(right) and left == right


class Exchange:
    def __init__(self, rows: list[dict], base: str, fields: list[dict]):
        self.rows, self.base, self.fields, self.index = rows, base, fields, 0

    def call(self, method: str, path: str, expected: int, request=None, *, network=False) -> dict:
        need(self.index < len(self.rows), "active API exchange missing", "unrun")
        row = self.rows[self.index]
        self.index += 1
        sent = row.get("request", {})
        url = ("http://127.0.0.1:9" if network else self.base) + path
        need(sent.get("method") == method and sent.get("url") == url, "API request method, path or query differs")
        raw_request = body(sent.get("body"))
        expected_body = b"" if request is None else json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
        need(raw_request == expected_body, "API request body differs from frozen samples")
        if request is not None:
            headers = dict(sent.get("headers", []))
            need(headers.get("content-type") == "application/json", "API JSON content type differs")
        if network:
            need(row.get("response") is None and isinstance(row.get("error"), dict), "network-error step did not reject fetch")
            need(isinstance(row["error"].get("name"), str) and row["error"]["name"], "fetch failure type missing", "unrun")
            return {"ok": False, "error": {"status": 0, "detail": "network error", "fieldErrors": {}, "otherErrors": []}}
        need(isinstance(row.get("response"), dict), "active API HTTP response evidence missing",
             "unrun" if row.get("error") is None else "failed")
        need(row.get("error") is None, "active API exchange has both a response and a fetch error")
        reply = row["response"]
        need(integer(reply.get("status")), "HTTP response status missing", "unrun")
        raw = body(reply.get("body"))
        try:
            payload = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            payload = None
        if reply["status"] == expected:
            return {"ok": True, "value": payload}
        return {"ok": False, "error": error_from(reply["status"], payload, self.fields)}


def check_row(result: dict, fields: list[dict], sent: dict, timestamps: bool) -> dict:
    need(result.get("ok") is True and isinstance(result.get("value"), dict), "create did not return a row")
    row = result["value"]
    need(integer(row.get("id")) and row["id"] >= 1, "row id is not a positive integer")
    for field in fields:
        expected = sent[field["name"]] if field["name"] in sent else field.get("default")
        need(field["name"] in row and same(row[field["name"]], expected), "row field/default differs")
    if timestamps:
        need(isinstance(row.get("created_at"), str) and isinstance(row.get("updated_at"), str), "row timestamps missing")
    return row


def check_error(result: dict, status: int, detail: str) -> dict:
    need(result.get("ok") is False and result["error"]["status"] == status and result["error"]["detail"] == detail, "API status or error detail differs")
    return result["error"]


def behavior(rows: list[dict], base: str, prompt: dict, contract: dict, report: dict) -> int:
    version, resource = prompt["version"], prompt["resource"]
    strict = version in ("som.prompt.v3", "som.prompt.v4")
    fields = []
    for field in resource["fields"]:
        item = {**field, "nullable": not field["required"] and field.get("default") is None}
        if not field["required"]:
            item["default"] = field.get("default")
        fields.append(item)
    query = prompt.get("query") if version != "som.prompt.v1" else None
    not_nullable = next((field for field in fields if not field["nullable"]), None)
    required = next((field for field in fields if field["required"]), None)
    unique = next((field for field in fields if field["unique"]), None)
    facts = {"query": query is not None, "filter": query is not None,
             "before": query is not None and query.get("date_field") is not None,
             "not_nullable": not_nullable is not None, "required": required is not None, "unique": unique is not None}
    steps = report.get("steps", [])
    need([step.get("name") for step in steps] == [step["name"] for step in contract["steps"]] and len(steps) == 20, "API step inventory differs")
    need(same(report.get("facts"), facts) and report.get("resource") == resource["name"], "API facts/resource differ from prompt")
    conditions = {"list filter": "filter", "list before": "before", "list page": "query", "list limit too large": "query", "patch null on a field that is not nullable": "not_nullable", "create missing a required field": "required", "create unique conflict": "unique"}
    for step in steps:
        applicable = facts[conditions[step["name"]]] if step["name"] in conditions else True
        if applicable:
            need(step.get("ok") is True and not step.get("skipped"), "API result contradicts raw active exchange")
        else:
            need(step.get("ok") is None and step.get("skipped") == contract["skip_reasons"][step["name"]], "API conditional skip differs")
    need(report.get("failed") is False, "API report records a failure")
    x = Exchange(rows, base, fields)
    path = resource["path"]
    page = [("limit", 20), ("offset", 0)] if query is not None else []

    def listed(params=page):
        suffix = "?" + urllib.parse.urlencode([(key, js_string(value)) for key, value in params]) if params else ""
        return x.call("GET", path + suffix, 200)

    def value(result: dict):
        need(result.get("ok") is True, "API success returned an error")
        return result["value"]

    need(same(value(listed()), []), "initial list is not empty")
    minimum = {field["name"]: sample(field, "min") for field in fields if field["required"]}
    first = check_row(x.call("POST", path, 201, minimum), fields, minimum, strict)
    full_body = {field["name"]: sample(field, "full") for field in fields}
    full = check_row(x.call("POST", path, 201, full_body), fields, full_body, strict)
    item = lambda identity: path + "/" + str(identity)
    need(same(value(x.call("GET", item(full["id"]), 200)), full), "get row differs from created row")
    expected_rows = [first, full]
    need(same(value(listed()), expected_rows), "list order or rows differ")
    if query is not None:
        field = query["filter_field"]
        param = field if strict else "filter_value"
        expected = [row for row in expected_rows if same(row[field], full_body[field])]
        params = dict(page)
        params[param] = full_body[field]
        need(same(value(listed(list(params.items()))), expected), "filtered rows differ")
    if facts["before"]:
        field = query["date_field"]
        expected = [row for row in expected_rows if row[field] is not None and row[field] < "2026-02-01"]
        need(same(value(listed([*page, ("before", "2026-02-01")])), expected), "before-date rows differ")
    if query is not None:
        need(same(value(listed([("limit", 1), ("offset", 1)])), [full]), "page result differs")
        check_error(listed([("limit", query["max_limit"] + 1), ("offset", 0)]), 422, "validation failed")
    changed = next((field for field in fields if field["type"] == "str"), fields[0])
    patch = {changed["name"]: sample(changed, "changed")}
    patched = value(x.call("PATCH", item(first["id"]), 200, patch))
    need(isinstance(patched, dict), "patch response row missing")
    for field in fields:
        expected = patch[field["name"]] if field["name"] in patch else first[field["name"]]
        need(field["name"] in patched and same(patched[field["name"]], expected), "patch changed an unrelated field or failed to change its field")
    if strict:
        need(isinstance(patched.get("updated_at"), str) and patched["updated_at"] >= first["updated_at"], "patch timestamp moved backwards")
    if not_nullable is not None:
        result = x.call("PATCH", item(first["id"]), 200, {not_nullable["name"]: None})
        error = check_error(result, 422 if strict else 409, "validation failed" if strict else "conflict")
        if strict:
            need(error["fieldErrors"] or error["otherErrors"], "null validation has no messages")
    if required is not None:
        error = check_error(x.call("POST", path, 201, {}), 422, "validation failed")
        need(error["fieldErrors"].get(required["name"]), "required field error missing")
    extra = {field["name"]: sample(field, "extra") for field in fields if field["required"]}
    result = x.call("POST", path, 201, {**extra, "unknown_field": 1})
    if strict:
        error = check_error(result, 422, "validation failed")
        need(any(message.startswith("body.unknown_field: ") for message in error["otherErrors"]), "unknown-field validation message missing")
    else:
        row = check_row(result, fields, extra, False)
        need("unknown_field" not in row, "unknown body field was returned")
    if unique is not None:
        check_error(x.call("POST", path, 201, full_body), 409, "conflict")
    check_error(x.call("GET", item(2147483647), 200), 404, "not found")
    check_error(x.call("PATCH", item(2147483647), 200, {}), 404, "not found")
    need(value(x.call("DELETE", item(full["id"]), 204)) is None, "delete result is not null")
    check_error(x.call("GET", item(full["id"]), 200), 404, "not found")
    check_error(x.call("DELETE", item(full["id"]), 204), 404, "not found")
    suffix = "?" + urllib.parse.urlencode(page) if page else ""
    check_error(x.call("GET", path + suffix, 200, network=True), 0, "network error")
    need(x.index == len(rows), "unexpected extra API exchange")
    return x.index


def numeric_table(raw: bytes) -> dict[int, tuple[int, int]]:
    table = {}
    for line in raw.decode().splitlines():
        columns = line.split()
        need(len(columns) == 3 and all(value.isdigit() for value in columns), "OS process table is malformed")
        pid, parent, group = map(int, columns)
        need(pid not in table, "OS process table repeats a pid")
        table[pid] = parent, group
    return table


def python_argv_matches(reader, observed: str, launched: list[str], toolchain: dict) -> bool:
    if observed == " ".join(launched):
        return True
    if not launched or launched[0] != str(reader.repo / "som-rs/runtime/acceptance-m3-venv/bin/python"):
        return False
    # macOS's framework launcher execs Python.app with the same PID and arguments.
    # Admit only the interpreter in the exact reviewed bottle, never a basename,
    # resolved path on the audit host, or an arbitrary Python installation.
    recipe = reader.json(reader.repo / "som-rs/scripts/react_output_acceptance/macos_bottles.json")
    entries = [row for row in recipe.get("bottles", []) if row.get("formula") == "python@3.12"]
    need(recipe.get("version") == "som.react-output-macos-bottles.v1" and len(entries) == 1,
         "Python framework alias recipe missing", "unrun")
    bottle = entries[0]
    keg = "/opt/homebrew/Cellar/python@3.12/3.12.11_1"
    need(toolchain.get("python", {}).get("version") == bottle.get("version") == "3.12.11"
         and type(bottle.get("revision")) is int and bottle["revision"] == 1
         and bottle.get("expected_keg_directory") == keg
         and bottle.get("archive_sha256") == "1443684f05205bf2aca7b050fdea79c93fa4f52fdbf2f085c037aef09b19b0a0",
         "Python framework alias differs from the pinned bottle")
    framework = keg + "/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
    return observed == " ".join([framework, *launched[1:]])


def cleanup(reader, directory: Path, events: list[dict], api: dict, base: str, work: Path, toolchain: dict, linux: dict | None = None) -> int:
    lsof = linux["lsof"] if linux is not None else "/usr/sbin/lsof"
    processes = api.get("processes")
    need(isinstance(processes, list) and {row.get("role") for row in processes} == {"postgres", "python_product"}
         and len(processes) == 2, "owned API process inventory missing", "unrun")

    def command(record: dict, expected: list[str] | None = None) -> bytes:
        need(isinstance(record, dict), "raw OS command missing", "unrun")
        need(integer(record.get("exit_code")), "raw OS exit code missing", "unrun")
        if expected is not None:
            need(record.get("argv") == expected, "raw OS command differs")
        retained(reader, directory, record.get("stderr"))
        return retained(reader, directory, record.get("stdout"))

    pg_records = [event for event in events if event.get("kind") == "pg_ctl"]
    need(len(pg_records) == 2, "PostgreSQL start/stop raw commands missing", "unrun")
    init = [event for event in events if event.get("kind") == "initdb"]
    need(len(init) == 1, "fresh PostgreSQL initialization evidence missing", "unrun")
    init_command = [str(Path(toolchain["postgresql"]["bin_dir"]) / "initdb"), "-D", str(work / "pgdata"),
                    "-U", "postgres", "-A", "trust", "--encoding=UTF8", "--locale=C", "--no-instructions"]
    command(init[0], init_command)
    need(init[0]["exit_code"] == 0 and events.index(init[0]) < events.index(pg_records[0]), "PostgreSQL initialization failed or did not precede start")
    probe_index = next(index for index, event in enumerate(events) if event.get("kind") == "probe")
    for process in processes:
        pid, role = process.get("pid"), process.get("role")
        need(process.get("stopped") is True, "process report contradicts raw cleanup evidence")
        need(integer(pid) and pid > 1, "owned pid invalid")
        expected_cwd = str(reader.path(process.get("expected_cwd")))
        need(expected_cwd == str(work / ("pgdata" if role == "postgres" else "python")), "owned process cwd is outside this API case")
        started = [event for event in events if event.get("kind") == "process" and event.get("pid") == pid and event.get("phase") == "cwd-check"]
        need(len(started) >= 2, "start/pre-stop ownership snapshots missing", "unrun")
        need(events.index(started[0]) < probe_index < events.index(started[-1]), "API probe is outside the recorded server lifetime")
        known = {pid}
        group = None
        for event in (started[0], started[-1]):
            raw = command(event.get("ps"), ["/bin/ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,command="])
            columns = raw.decode().strip().split(None, 3)
            need(len(columns) == 4 and all(value.isdigit() for value in columns[:3]), "live process argv snapshot missing", "unrun")
            observed_pid, parent, pgid = map(int, columns[:3])
            need(observed_pid == pid and event.get("pgid") == pgid and event.get("pgid_error_errno") is None, "process PID/PGID snapshot differs")
            need(event["ps"]["exit_code"] == 0, "live process snapshot failed")
            cwd = command(event.get("cwd"), [lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
            lines = cwd.decode().splitlines()
            need(event["cwd"]["exit_code"] == 0 and [line[1:] for line in lines if line.startswith("p")] == [str(pid)]
                 and [line[1:] for line in lines if line.startswith("n")] == [expected_cwd], "raw lsof pid/cwd differs")
            table = numeric_table(command(event.get("table"), ["/bin/ps", "-axo", "pid=,ppid=,pgid="]))
            need(event["table"]["exit_code"] == 0, "live process table command failed", "unrun")
            need(table.get(pid) == (parent, pgid), "ps snapshots disagree")
            group = pgid
            if role == "python_product":
                need((columns[3] == " ".join(process["command"]) if linux is not None else
                      python_argv_matches(reader, columns[3], process["command"], toolchain))
                     and pgid == pid, "Python argv or session group differs")
            else:
                argv = shlex.split(columns[3])
                need(Path(argv[0]).name == "postgres" and "-D" in argv and argv[argv.index("-D") + 1] == expected_cwd, "PostgreSQL master argv differs")
            changed = True
            while changed:
                children = {child for child, (ppid, _group) in table.items() if ppid in known}
                changed, known = not children <= known, known | children
            if pgid == pid:
                known |= {child for child, (_ppid, child_group) in table.items() if child_group == pgid}
        finished = [event for event in events if event.get("kind") == "process" and event.get("pid") == pid and event.get("phase") == "after-stop"]
        need(len(finished) == 1, "after-stop OS snapshot missing", "unrun")
        event = finished[0]
        need(events.index(event) > events.index(started[-1]), "final OS snapshot preceded process stop")
        table = numeric_table(command(event.get("table"), ["/bin/ps", "-axo", "pid=,ppid=,pgid="]))
        need(event["table"]["exit_code"] == 0 and table, "after-stop process table missing", "unrun")
        need(not known & set(table), "owned process or descendant remains alive")
        if group == pid:
            need(not any(row[1] == group for row in table.values()), "owned process group remains alive")
        need(command(event.get("ps"), ["/bin/ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,command="]).strip() == b""
             and event["ps"]["exit_code"] == 1, "owned pid remains present")
        need(command(event.get("cwd"), [lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"]).strip() == b""
             and event["cwd"]["exit_code"] == 1, "owned cwd remains present")
        need(event.get("pgid") is None and event.get("pgid_error_errno") == errno.ESRCH, "after-stop getpgid did not report ESRCH")
        port = event.get("port")
        need(integer(port) and 1 <= port <= 65535 and port not in (8765, 18765), "owned QA port missing", "unrun")
        need(command(event.get("listener"), [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).strip() == b""
             and event["listener"]["exit_code"] == 1, "owned QA listener remains present")
        need(all(retained(reader, directory, event[key]["stderr"]).strip() == b"" for key in ("ps", "table", "cwd", "listener")),
             "after-stop OS command has an error, so absence is not proved", "unrun")
        if role == "python_product":
            argv = process["command"]
            need("--port" in argv and int(argv[argv.index("--port") + 1]) == port
                 and urllib.parse.urlsplit(base).port == port, "Python service port binding differs")
            expected = [linux["python"] if linux is not None else str(reader.repo / "som-rs/runtime/acceptance-m3-venv/bin/python"), "-m", "uvicorn",
                        "som_behavior_host:app", "--app-dir", str(work / "_shim"), "--host", "127.0.0.1",
                        "--port", str(port), "--no-access-log", "--log-level", "warning"]
            need(argv == expected, "Python server command differs from the frozen runner")
        else:
            for phase in ("after-start", "before-stop"):
                records = [item for item in events if item.get("kind") == "postmaster" and item.get("phase") == phase]
                need(len(records) == 1, "postmaster pid-file snapshot missing", "unrun")
                if phase == "after-start":
                    need(events.index(pg_records[0]) < events.index(records[0]) < events.index(started[0]), "postmaster start proof is out of order")
                else:
                    need(events.index(started[-1]) < events.index(records[0]) < events.index(pg_records[1]) < events.index(event), "postmaster stop proof is out of order")
                lines = retained(reader, directory, records[0].get("raw")).decode().splitlines()
                need(len(lines) >= 4 and lines[0] == str(pid) and lines[1] == expected_cwd and lines[3] == str(port), "postmaster identity differs")
            for record in pg_records:
                argv = record.get("argv", [])
                command(record)
                need(record["exit_code"] == 0 and Path(argv[0]).name == "pg_ctl" and "-D" in argv
                     and argv[argv.index("-D") + 1] == expected_cwd, "PostgreSQL control command differs")
            binary = str(Path(toolchain["postgresql"]["bin_dir"]) / "pg_ctl")
            expected = [binary, "-D", expected_cwd, "-l", str(work / "postgres.log"), "-w", "-o",
                        f"-F -h 127.0.0.1 -p {port} -k '' -c timezone=UTC", "start"]
            need(pg_records[0]["argv"] == process["command"] == expected
                 and pg_records[1]["argv"] == [binary, "-D", expected_cwd, "-m", "fast", "-w", "stop"], "PostgreSQL lifecycle order or fast stop differs")
    return len(processes)


def linux_equal(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(linux_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(linux_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def linux_context(reader, directory: Path, receipt: dict, linux: dict) -> dict:
    """Join one case to the caller's independently audited run-level context.

    The caller must establish source, wheel, runtime and profile provenance
    before calling this function. A case receipt cannot prove that provenance.
    """
    need(isinstance(linux, dict), "verified Linux API input context missing", "unrun")
    keys = ("api_profile_sha256", "node_profile_sha256", "node", "python", "pg_bin", "lsof", "ps", "os_tools")
    recorded = receipt.get("linux_qa")
    need(isinstance(recorded, dict) and all(k in linux for k in keys), "Linux API input identity missing", "unrun")
    need(all(linux_equal(recorded.get(k), linux[k]) for k in keys), "Linux API input identity differs")
    need(recorded.get("recheck_passed") is True and "recheck_error" not in recorded,
         "Linux API final case check did not succeed")
    need(linux["lsof"] == "/usr/bin/lsof" and linux["ps"] == "/bin/ps", "Linux OS tool paths differ")
    for key in ("node", "python", "pg_bin"):
        path = linux[key]
        need(isinstance(path, str) and Path(path).is_absolute() and ".." not in Path(path).parts,
             "Linux tool path is invalid")
    tools = linux["os_tools"]
    need(isinstance(tools, dict) and set(tools) == {"lsof", "ps"}, "Linux OS tool inventory missing", "unrun")
    for name in ("lsof", "ps"):
        item = tools[name]
        need(isinstance(item, dict) and item.get("path") == linux[name]
             and isinstance(item.get("resolved_path"), str) and Path(item["resolved_path"]).is_absolute()
             and isinstance(item.get("sha256"), str) and len(item["sha256"]) == 64
             and all(c in "0123456789abcdef" for c in item["sha256"])
             and integer(item.get("mode")) and item["mode"] & 0o111,
             "Linux OS tool metadata differs")
    return recorded


def linux_lifecycle(reader, directory: Path, events: list[dict], api: dict, probe: dict, linux: dict, work: Path) -> None:
    """Check direct child waits and the source-bound OS observations.

    PostgreSQL's postmaster is a daemon: cleanup() checks pg_ctl -w plus
    postmaster PID, cwd, descendants and listener absence. It is not a direct
    child and this function never claims an OS wait for it.
    """
    versions = [e for e in events if e.get("kind") == "os-tool-version"]
    need(len(versions) == 2 and {e.get("tool") for e in versions} == {"lsof", "ps"},
         "Linux OS tool versions missing", "unrun")
    for item in versions:
        name = item["tool"]
        need(item.get("argv") == [linux[name], "-v" if name == "lsof" else "--version"]
             and type(item.get("exit_code")) is int and item["exit_code"] == 0
             and item.get("error_type") is None, "Linux OS tool version command differs")
        stdout = retained(reader, directory, item.get("stdout"))
        stderr = retained(reader, directory, item.get("stderr"))
        need(stdout or stderr, "Linux OS tool version output missing", "unrun")
    starts = [(i, e) for i, e in enumerate(events) if e.get("kind") == "child-start"]
    waits = [(i, e) for i, e in enumerate(events) if e.get("kind") == "child-wait"]
    need(starts and waits, "direct child start/wait evidence missing", "unrun")
    by_pid = {}
    for index, item in starts:
        pid, argv = item.get("pid"), item.get("argv")
        need(integer(pid) and pid > 1 and pid not in by_pid and isinstance(argv, list) and argv
             and all(isinstance(x, str) for x in argv) and isinstance(item.get("cwd"), str),
             "direct child identity differs")
        by_pid[pid] = (index, item)
    for index, item in waits:
        pid = item.get("pid")
        need(integer(pid) and pid in by_pid and index > by_pid[pid][0]
             and item.get("state") == "completed" and integer(item.get("exit_code"))
             and item.get("error_type") is None, "direct child wait is missing or unverified", "unrun")
    for pid, (_index, _item) in by_pid.items():
        values = [w["exit_code"] for _, w in waits if w["pid"] == pid]
        need(values and len(set(values)) == 1, "direct child wait is absent or inconsistent", "unrun")

    def match_record(event: dict, *, pid: int | None = None, cwd: str | None = None):
        found = [(i, start) for i, start in starts if start["argv"] == event.get("argv")
                 and (pid is None or start["pid"] == pid)]
        need(len(found) == 1, "raw command does not identify one direct child", "unrun")
        index, start = found[0]
        need(index < events.index(event) and (cwd is None or start["cwd"] == cwd), "raw command order or cwd differs")
        values = [w["exit_code"] for _, w in waits if w["pid"] == start["pid"]]
        need(values and all(value == event.get("exit_code") for value in values), "command exit and direct wait differ")

    for event in [e for e in events if e.get("kind") in ("initdb", "pg_ctl")]:
        match_record(event)
    match_record(probe, pid=probe.get("pid"), cwd=str(reader.repo))
    python_rows = [p for p in api["processes"] if p.get("role") == "python_product"]
    need(len(python_rows) == 1, "Python product process missing", "unrun")
    python = python_rows[0]
    launch = [(i, item) for i, item in starts if item["pid"] == python.get("pid")]
    need(len(launch) == 1 and launch[0][1]["argv"] == python.get("command")
         and launch[0][1]["cwd"] == str(work / "python"), "Python server child identity differs")
    exit_values = [w["exit_code"] for _, w in waits if w["pid"] == python["pid"]]
    need(integer(python.get("exit_code")) and exit_values and all(x == python["exit_code"] for x in exit_values),
         "Python server wait differs from process report", "unrun")
    migrate = [item for _, item in starts if item["argv"] == [linux["python"], str(work / "_alembic/migrate.py"),
                                                               str(work / "_alembic"), str(work / "python/alembic/versions")]]
    need(len(migrate) == 1 and migrate[0]["cwd"] == str(work / "python")
         and all(w["exit_code"] == 0 for _, w in waits if w["pid"] == migrate[0]["pid"]),
         "Python migration child wait differs", "unrun")
    probe_index = events.index(probe)
    port = urllib.parse.urlsplit(probe["argv"][3]).port
    listeners = [(i, event) for i, event in enumerate(events) if event.get("kind") == "listener"
                 and event.get("argv") == [linux["lsof"], "-nP", "-a", "-p", str(python["pid"]),
                                            f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]
                 and event.get("exit_code") == 0]
    need(any(i < probe_index for i, _ in listeners) and any(i > probe_index for i, _ in listeners),
         "Python listener before/after API probe missing", "unrun")
    for _, event in listeners:
        stdout = retained(reader, directory, event.get("stdout"))
        stderr = retained(reader, directory, event.get("stderr"))
        need(stdout.split() == [str(python["pid"]).encode()] and not stderr.strip(), "Python listener PID differs")


def linux_invocation(reader, directory: Path, api_report_path: Path, product_dir: Path, linux: dict) -> None:
    """Bind the recorder child command, wait and log to this specific case."""
    case_dir = api_report_path.parent.parent
    invocation = reader.json(case_dir / "api-invocation.json")
    expected = [linux["python"], str(reader.repo / "som-rs/scripts/react_output_acceptance/record_api.py"),
                str(case_dir / "python"), str(product_dir), str(api_report_path.parent),
                "--raw-out", str(directory), "--api-profile", linux["api_profile_path"],
                "--qa-profile", linux["node_profile_path"], "--full-validation", linux["full_before_path"]]
    need(invocation.get("version") == "som.react-api-invocation.v1" and invocation.get("argv") == expected
         and invocation.get("cwd") == str(reader.repo) and invocation.get("outcome") == "exited"
         and type(invocation.get("exit_code")) is int and invocation["exit_code"] == 0
         and linux_equal(invocation.get("wait"), {"state": "completed", "exit_code": 0})
         and invocation.get("failure") is None and invocation.get("log") == "api.log",
         "API recorder invocation or wait differs")
    need(reader.hash(case_dir / "api.log") == invocation.get("log_sha256"), "API recorder log differs")


def validate(reader, raw_dir: Path, api_report_path: Path, prompt: dict, contract: dict, product_dir: Path, *, linux: dict | None = None) -> dict:
    directory = reader.path(raw_dir)
    receipt = reader.json(directory / "receipt.json")
    need(receipt.get("version") == "som.react-api-raw.v1", "API raw receipt version missing", "unrun")
    need(receipt.get("recording_errors", []) == [], "API OS recording was incomplete", "unrun")
    need(integer(receipt.get("exit_code")) and receipt["exit_code"] == 0, "API recorder exited with failure")
    api = reader.json(api_report_path)
    need(receipt.get("api_report_sha256") == reader.hash(api_report_path), "API report binding differs")
    repo = reader.repo
    toolchain = reader.json(repo / "som-rs/contracts/react-output-v1/toolchain.json")
    if linux is not None:
        linux_qa = linux_context(reader, directory, receipt, linux)
        toolchain = {**toolchain, "node": {"binary": linux_qa["node"]},
                     "postgresql": {"bin_dir": linux_qa["pg_bin"]}}
    else:
        need("linux_qa" not in receipt, "Linux evidence requires independently verified input context", "unrun")
    for field, path in (("probe_sha256", repo / "som-rs/tests/react-output-v1/api_contract.mjs"),
                        ("runner_sha256", repo / "som-rs/tests/react-output-v1/run_api_contract.py"),
                        ("hook_sha256", repo / "som-rs/scripts/react_output_acceptance/record_api_fetch.mjs"),
                        ("product_api_sha256", product_dir / "src/api.ts"),
                        ("product_form_sha256", product_dir / "src/form.ts"),
                        ("product_schema_sha256", product_dir / "src/schema.ts")):
        need(receipt.get(field) == reader.hash(path), "API source/binary input binding differs")
    need(reader.bytes(product_dir / "src/api.ts") == reader.bytes(repo / "som-rs/contracts/react-output-v1/reference/src/api.ts"), "API client is not the fixed frozen client")
    need(reader.bytes(product_dir / "src/form.ts") == reader.bytes(repo / "som-rs/contracts/react-output-v1/reference/src/form.ts"), "API client helper is not the fixed frozen helper")
    events = receipt.get("events")
    need(isinstance(events, list), "API OS events missing", "unrun")
    probes = [event for event in events if event.get("kind") == "probe"]
    need(len(probes) == 1, "exactly one API Node probe must be recorded", "unrun")
    probe = probes[0]
    argv = probe.get("argv", [])
    need(len(argv) == 5 and argv[0] == toolchain["node"]["binary"]
         and Path(argv[1]) == repo / "som-rs/tests/react-output-v1/api_contract.mjs"
         and Path(argv[2]) == product_dir and Path(argv[4]) == Path(api["case"]) / "prompt.json", "API Node argv differs")
    need(Path(api["case"]) == api_report_path.parent.parent / "python" and Path(api["react"]) == product_dir,
         "API report case/product binding differs")
    need(reader.json(argv[4]) == prompt, "API prompt differs from product prompt")
    base = argv[3]
    url = urllib.parse.urlsplit(base)
    need(url.scheme == "http" and url.hostname == "127.0.0.1" and url.port not in (None, 9, 8765, 18765)
         and not url.username and not url.password and not url.path and not url.query and not url.fragment, "API service is not its owned QA endpoint")
    stdout = retained(reader, directory, probe.get("stdout"))
    retained(reader, directory, probe.get("stderr"))
    need(integer(probe.get("exit_code")) and integer(api.get("exit_code"))
         and probe["exit_code"] == api["exit_code"] == 0 and same(json.loads(stdout), api.get("result")), "API Node result differs from retained stdout")
    files = receipt.get("fetch_files")
    need(isinstance(files, list) and len(files) == 1, "API fetch trace missing or repeated", "unrun")
    need(integer(probe.get("pid")) and files[0].get("file") == f"fetch-{probe['pid']}.jsonl", "API fetch trace is not bound to the child PID")
    rows = [json.loads(line) for line in retained(reader, directory, files[0]).splitlines() if line]
    need(rows, "API fetch trace is empty", "unrun")
    need(all(row.get("version") == "som.react-api-fetch.v1" and integer(row.get("sequence")) and row["sequence"] == index
         and row.get("pid") == probe["pid"] for index, row in enumerate(rows, 1)), "API fetch trace sequence/PID differs")
    exchanges = behavior(rows, base, prompt, contract, api["result"])
    processes = cleanup(reader, directory, events, api, base, api_report_path.parent, toolchain, linux)
    if linux is not None:
        linux_lifecycle(reader, directory, events, api, probe, linux, api_report_path.parent)
        linux_invocation(reader, directory, api_report_path, product_dir, linux)
    return {"steps": 20, "exchanges": exchanges, "processes": processes}
