"""The language-specific steps of the phase 2 behavior suite (contracts/rust-output-v1/phase2/services.json).

The adapted som-core verifiers (adapted/, written by adapt.py) call host_for(root) wherever the
original verifier ran a Python-only command: install, start the server, migrate, probe a start
without database configuration, run the product's own tests, and list the route surface. Every
HTTP, database, provider and fault check stays the original language-neutral code.

- PythonProduct runs exactly the commands of the original verifiers, so a Python product is
  checked as the accepted runs checked it. Its route surface is probed and also compared with
  /openapi.json, which records that the probe and the original check agree.
- RustProduct follows the phase 2 services contract. It is unexercised until #53 produces Rust
  products for these cases.

A product directory is registered before a verifier runs and unregistered after it.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import threading

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
TOOLCHAIN = json.loads((SOM_RS / "contracts" / "rust-output-v1" / "toolchain.json").read_text())
CARGO = pathlib.Path(TOOLCHAIN["rust"]["bin_dir"]) / "cargo"
FORBIDDEN_PORTS = frozenset({8765, 18765})
FRAMEWORK_FALLBACK = {404: {"detail": "Not Found"}, 405: {"detail": "Method Not Allowed"}}
PROBE_ID = 2147483000

_REGISTRY: dict[pathlib.Path, object] = {}
_LOCK = threading.Lock()


def register(root: pathlib.Path, host: object) -> None:
    with _LOCK:
        _REGISTRY[pathlib.Path(root).resolve()] = host


def unregister(root: pathlib.Path) -> None:
    with _LOCK:
        _REGISTRY.pop(pathlib.Path(root).resolve(), None)


def host_for(root: pathlib.Path) -> PythonProduct | RustProduct:
    with _LOCK:
        host = _REGISTRY.get(pathlib.Path(root).resolve())
    if host is None:
        raise RuntimeError(f"no product host registered for {root}")
    return host  # type: ignore[return-value]


def route_candidates(resources: dict) -> list[tuple[str, str, str]]:
    """(template, method, concrete path) for every route a composed product may have.

    The templates are the ones verify_composed_subsets looks up in /openapi.json.
    """
    parent = resources["parent"]["path"]
    child = parent + "/{parent_id}" + resources["child"]["path"]
    out = []
    for base in (parent, child):
        item = base + ("/{parent_id}" if base == parent else "/{child_id}")
        for method, template in (("post", base), ("get", base), ("get", item), ("patch", item), ("delete", item)):
            out.append((template, method, template.replace("{parent_id}", str(PROBE_ID))
                        .replace("{child_id}", str(PROBE_ID))))
    batch = parent + "/with-children"
    out.append((batch, "post", batch))
    return out


def probe_surface(client, resources: dict) -> dict[str, dict[str, dict]]:
    """Which candidate routes exist, from HTTP answers alone.

    A request that cannot change a row is sent to each candidate: an empty JSON object to POST
    and PATCH, and ids that no seeded row has. A route is absent exactly when the answer is the
    framework fallback: 404 {"detail": "Not Found"} or 405 {"detail": "Method Not Allowed"}.
    A present route answers its own status (401, 403, 404 with the product detail, 422, ...).
    """
    surface: dict[str, dict[str, dict]] = {}
    for template, method, path in route_candidates(resources):
        kwargs = {"json": {}} if method in ("post", "patch") else {}
        response = client.request(method.upper(), path, **kwargs)
        fallback = FRAMEWORK_FALLBACK.get(response.status_code)
        try:
            body = response.json()
        except ValueError:
            body = None
        if fallback is None or body != fallback:
            surface.setdefault(template, {})[method] = {"probe_status": response.status_code}
    return surface


def changed_inputs(prompt: dict, inputs: dict) -> dict | None:
    """The idempotency conflict body: the same flow inputs with the first input changed.

    verify_flow_service changes a boolean or an integer first input, and otherwise sets title on
    an object input. This keeps those three changes exactly and covers the other first inputs:
    an object input without a title field changes its first field, and a scalar input changes by
    its type. A flow without inputs has no body to change, so it returns None.
    """
    if not prompt["flow"]["inputs"]:
        return None
    first = prompt["flow"]["inputs"][0]
    name, kind = first["name"], first["type"]
    changed = dict(inputs)
    if kind in ("parent_input", "child_input"):
        fields = prompt["resources"][kind.split("_")[0]]["fields"]
        field = next((f for f in fields if f["name"] == "title"), fields[0])
        changed[name] = dict(inputs[name], **{field["name"]: changed_value(field["type"], inputs[name].get(field["name"]))})
    else:
        changed[name] = changed_value(kind, inputs[name])
    return changed


def changed_value(kind: str, value):
    if kind in ("bool", "boolean"):
        return not value
    if kind in ("int", "integer"):
        return value + 1
    if kind == "float":
        return value + 0.5
    if kind == "date":
        import datetime
        return (datetime.date.fromisoformat(value) + datetime.timedelta(days=1)).isoformat()
    if kind in ("str", "string"):
        return "changed-content"
    raise ValueError(f"no conflict change for input type {kind}")


SEED_OBJECT_KEY = "flow_seed_object"
SEED_OBJECT_BODY = b"phase 2 seeded object\n"
DATABASE_WRITES = frozenset({"parent.create", "parent.patch", "child.create", "child.patch",
                             "batch.create", "workflow.import"})


def flow_nodes(nodes: list[dict]):
    """Every flow node in source order, arms and transaction bodies included."""
    for node in nodes:
        yield node
        for arm in ("steps", "then", "else"):
            yield from flow_nodes(node.get(arm, []))


def existing_bindings(prompt: dict) -> dict[str, str]:
    """Flow inputs that name a resource the flow expects to exist before it runs.

    verify_flow_service binds every integer input to the seeded parent, which fits the flows it
    was written for. A public example can also read or patch an existing child by an input id,
    or read an existing object by an input key. Those inputs are bound to a seeded child under
    the seeded parent and to a seeded object; every other input keeps the original value. An
    object key input that the flow also puts is the flow's own object, not an existing one, so it
    keeps the original value.
    """
    out: dict[str, str] = {}
    put = {node["args"]["key"]["input"] for node in flow_nodes(prompt["flow"]["steps"])
           if node["op"] == "object.put" and "input" in node.get("args", {}).get("key", {})}
    for node in flow_nodes(prompt["flow"]["steps"]):
        args = node.get("args", {})
        if node["op"] in ("child.get", "child.patch") and "input" in args.get("child_id", {}):
            out[args["child_id"]["input"]] = "child"
        if node["op"] == "object.get" and "input" in args.get("key", {}) and args["key"]["input"] not in put:
            out[args["key"]["input"]] = "object"
    return out


def seed_existing(prompt: dict, client, parent_id: int) -> dict:
    """Create the existing child and object that existing_bindings needs, through the HTTP API."""
    from verify_related import payload
    wanted = set(existing_bindings(prompt).values())
    seeded: dict = {}
    if "child" in wanted:
        path = prompt["resources"]["parent"]["path"] + f"/{parent_id}" + prompt["resources"]["child"]["path"]
        response = client.post(path, json=payload(prompt["resources"]["child"], 800001))
        assert response.status_code == 201, response.text
        seeded["child"] = response.json()["id"]
    if "object" in wanted:
        response = client.put("/objects/" + SEED_OBJECT_KEY, content=SEED_OBJECT_BODY)
        assert response.status_code in (200, 201), response.text
        seeded["object"] = SEED_OBJECT_KEY
    return seeded


def bind_existing(prompt: dict, inputs: dict, seeded: dict | None) -> dict:
    if not seeded:
        return inputs
    return {name: seeded[existing_bindings(prompt)[name]] if name in existing_bindings(prompt) else value
            for name, value in inputs.items()}


def csv_inputs(prompt: dict) -> set[str]:
    """Inputs passed as the csv argument of workflow.import. The original fills CSV text only
    into an input named csv; a public example may name it otherwise."""
    return {node["args"]["csv"]["input"] for node in flow_nodes(prompt["flow"]["steps"])
            if node["op"] == "workflow.import" and "input" in node["args"].get("csv", {})}


WRITTEN_ROLES = {"parent.create": ("parent",), "parent.patch": ("parent",), "child.create": ("child",),
                 "child.patch": ("child",), "batch.create": ("parent", "child"), "workflow.import": ("child",)}


def independent_patch_available(prompt: dict) -> bool:
    """Whether the product has a PATCH route for every resource the flow writes.

    The revision_conflict phase of flow_fault_checks edits a written row through that route. A
    product that lists an operation subset without it cannot take the independent edit.
    """
    if "operations" not in prompt:
        return True
    actions = {(item["scope"], item["action"]) for item in prompt["operations"]}
    roles = {role for node in flow_nodes(prompt["flow"]["steps"]) for role in WRITTEN_ROLES.get(node["op"], ())}
    return all((role, "patch") in actions for role in roles)


def object_put_after_database_write(prompt: dict) -> bool:
    """Whether the flow puts an object after a database write, the shape the provider fault
    matrix of flow_provider_fault_checks is written for (it asserts that the interrupted put
    follows a committed database write)."""
    wrote = False
    for node in flow_nodes(prompt["flow"]["steps"]):
        if node["op"] in DATABASE_WRITES:
            wrote = True
        elif node["op"] == "object.put" and wrote:
            return True
    return False


class PythonProduct:
    """A generated Python product, run as the accepted verifiers run it."""

    kind = "python"

    def __init__(self, root: pathlib.Path) -> None:
        self.root = pathlib.Path(root)
        self.python = str(self.root / ".venv/bin/python")
        self.surface_checks: list[dict] = []

    def prepare(self, env: dict) -> None:
        from verify_related import run
        run(["uv", "sync", "--python", "3.12"], cwd=self.root, env=env)

    def serve_command(self, package: str, port: int) -> list[str]:
        assert port not in FORBIDDEN_PORTS
        return [self.python, "-m", "uvicorn", package + ".app:app", "--host", "127.0.0.1",
                "--port", str(port), "--log-level", "warning"]

    def is_product_process(self, process: subprocess.Popen, package: str) -> bool:
        return process.args[0] == self.python and process.args[3] == package + ".app:app"

    def migration_command(self, backend, up: bool) -> list[str]:
        return backend.migration_command(self.python, up)

    def startup_probe(self, package: str) -> list[str]:
        source = f"from fastapi.testclient import TestClient\nfrom {package}.app import app\nwith TestClient(app):\n    pass\n"
        return [self.python, "-c", source]

    def test_command(self) -> list[str]:
        return [self.python, "-m", "unittest", "discover", "-s", "tests", "-v"]

    def test_summary(self, result: subprocess.CompletedProcess) -> tuple[int, bool]:
        summary = re.search(r"Ran (\d+) tests? in ", result.stderr)
        return (int(summary[1]) if summary else 0), "(skipped=" in result.stderr

    def route_surface(self, client, resources: dict) -> dict[str, dict[str, dict]]:
        probed = probe_surface(client, resources)
        response = client.get("/openapi.json")
        assert response.status_code == 200, (response.status_code, response.text)
        paths = response.json()["paths"]
        documented = {(t, m) for t, m, _ in route_candidates(resources) if m in paths.get(t, {})}
        found = {(t, m) for t, methods in probed.items() for m in methods}
        self.surface_checks.append({"documented": sorted(map(list, documented)),
                                    "probed": sorted(map(list, found)), "equal": documented == found})
        assert documented == found, ("route probe differs from /openapi.json", sorted(documented ^ found))
        return probed


class RustProduct:
    """A generated Rust product under the phase 2 services contract. Unexercised until #53."""

    kind = "rust"

    def __init__(self, root: pathlib.Path, target_dir: pathlib.Path, build_lock: threading.Lock) -> None:
        self.root = pathlib.Path(root)
        self.target_dir = pathlib.Path(target_dir)
        self.build_lock = build_lock
        self.binary = str(self.root / "som_app")
        self.surface_checks: list[dict] = []

    def cargo_env(self, env: dict) -> dict:
        return {**env, "CARGO_NET_OFFLINE": "true", "CARGO_TARGET_DIR": str(self.target_dir),
                "PATH": str(CARGO.parent) + os.pathsep + env.get("PATH", "")}

    def prepare(self, env: dict) -> None:
        from verify_related import run
        with self.build_lock:
            run([str(CARGO), "build", "--locked", "--offline"], cwd=self.root, env=self.cargo_env(env),
                timeout=1800)
            run([str(CARGO), "test", "--locked", "--offline", "--no-run"], cwd=self.root,
                env=self.cargo_env(env), timeout=1800)
            shutil.copy2(self.target_dir / "debug" / "som_app", self.binary)

    def serve_command(self, package: str, port: int) -> list[str]:
        assert port not in FORBIDDEN_PORTS
        return [self.binary, "serve", "--host", "127.0.0.1", "--port", str(port)]

    def is_product_process(self, process: subprocess.Popen, package: str) -> bool:
        return process.args[0] == self.binary and process.args[1] == "serve"

    def migration_command(self, backend, up: bool) -> list[str]:
        return [self.binary, "migrate", "upgrade" if up else "downgrade"]

    def startup_probe(self, package: str) -> list[str]:
        return [self.binary, "check-config"]

    def test_command(self) -> list[str]:
        return ["/usr/bin/env", "CARGO_NET_OFFLINE=true", f"CARGO_TARGET_DIR={self.target_dir}",
                str(CARGO), "test", "--locked", "--offline", "--", "--test-threads=1"]

    def test_summary(self, result: subprocess.CompletedProcess) -> tuple[int, bool]:
        rows = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", result.stdout)
        return sum(int(r[0]) for r in rows), any(int(r[2]) for r in rows)

    def route_surface(self, client, resources: dict) -> dict[str, dict[str, dict]]:
        return probe_surface(client, resources)
