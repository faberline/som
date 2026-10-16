#!/usr/bin/env python3
"""Write the adapted som-core verifiers that the phase 2 behavior suite runs.

usage:
  adapt.py           write adapted/ from the pinned som-core originals
  adapt.py --check   fail unless adapted/ is exactly what this script writes

Each original is read from som-core/scripts and must have its pinned sha256. Each replacement
must match the stated number of times. The copies keep the original module names, so the
original mechanisms that find modules by name (composed_evidence.capture and
flow_test_backends.shared_oracles) still apply to them. The originals are never changed.

The replacements move only the language-specific steps to phase2_host.host_for(root): install,
the server command, migrations, the start without database configuration, the product's own
tests, the owned-process check before a kill, and the route surface. Every HTTP, database,
provider and fault check is the original code. One replacement adds checks instead: the branch
probes of flow_branch_cases also cover a comparison of two public inputs (PIVOT below). The
others cover flow shapes that only the public examples have (CONFLICT, SUBSET, EXISTING_*,
CSV_INPUT*, PROVIDER_SHAPE, CSV_UNIQUE, DATABASE_ONLY, REVISION_ROUTE and the LEASE replacements
below); contracts/rust-output-v1/phase2/behavior.json records how many cases each one reaches or
makes not applicable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCRIPTS = REPO / "som-core" / "scripts"
OUT = HERE / "adapted"
IMPORT = "from phase2_host import host_for\n"

UV_SYNC = ('run(["uv", "sync", "--python", "3.12"], cwd=root, env=env)', "host_for(root).prepare(env)")
MIGRATE = ("backend.migration_command(python, ", "host_for(root).migration_command(backend, ")
SUFFIXES = ('path.suffix in {".py", ".toml", ".ini", ".md"}', 'path.suffix in {".py", ".toml", ".ini", ".md", ".rs", ".sql"}')
UNITTEST = ('run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, env=env)',
            "run(host_for(root).test_command(), cwd=root, env=env)")
PROBE = ('subprocess.run([python, "-c", probe_source], cwd=root, env=missing, capture_output=True, text=True, check=False)',
         'subprocess.run(host_for(root).startup_probe(prompt["package"]), cwd=root, env=missing, capture_output=True, text=True, check=False)')
KILL_16 = ("                assert process.args[0] == str(server.root / '.venv/bin/python')\n"
           "                assert process.args[3] == prompt['package'] + '.app:app'\n",
           "                assert host_for(server.root).is_product_process(process, prompt['package'])\n")
KILL_4 = ("    assert process.args[0] == str(server.root / '.venv/bin/python')\n"
          "    assert process.args[3] == prompt['package'] + '.app:app'\n",
          "    assert host_for(server.root).is_product_process(process, prompt['package'])\n")
# A comparison of one public input with another has no literal, so the original leaves it
# unresolved. The adapted copy pins the other input to a fixed pivot and uses the pivot as the
# literal, so both arms are reached. It only adds probes; every other probe is the original's.
PIVOT = ("""        right = condition.get('right', {})
        if condition['op'] != 'is_null' and 'literal' not in right:
            unresolved.append(node['name'])
            continue
""", """        right = condition.get('right', {})
        if condition['op'] != 'is_null' and 'literal' not in right:
            others = target(right)
            pivot = None
            if len(others) == 1 and not others & sources:
                other = next(iter(others))
                pivot = {'date': '2030-01-01', 'str': 'set', 'string': 'set', 'int': 1, 'integer': 1,
                         'float': 0.5, 'bool': True, 'boolean': True}.get(value_kind(*other))
            if pivot is None:
                unresolved.append(node['name'])
                continue
            domain = domains.setdefault(other, [])
            if not any(type(pivot) is type(saved) and pivot == saved for saved in domain):
                domain.append(pivot)
            right = {'literal': pivot}
""")

# The conflict body of the idempotency check comes from phase2_host.changed_inputs, which keeps
# the original change for a boolean, an integer or a titled first input and covers the rest.
CONFLICT = ("""                changed = dict(inputs)
                first = prompt["flow"]["inputs"][0]
                changed[first["name"]] = (not inputs[first["name"]] if first["type"] == "boolean"
                                           else inputs[first["name"]] + 1 if first["type"] == "integer"
                                           else dict(inputs[first["name"]], title="changed-content"))
                conflict = client.post(route, json=changed, headers={"Idempotency-Key": key})
                assert conflict.status_code == 409, conflict.text
""", """                from phase2_host import changed_inputs
                changed = changed_inputs(prompt, inputs)
                if changed is not None:
                    conflict = client.post(route, json=changed, headers={"Idempotency-Key": key})
                    assert conflict.status_code == 409, conflict.text
""")

# A read-only flow is held at the table it reads. The original holds the parent table, which is
# kept whenever the flow reads the parent. A flow without any database operation never waits on a
# gate, so its lease checks are recorded as not applicable instead of failing.
LEASE_READ = ("""    first_write = first_database_write(prompt['flow']['steps'])
    read_only = first_write is None
""", """    first_write = first_database_write(prompt['flow']['steps'])
    read_only = first_write is None
    first_read = first_resource_read(prompt['flow']['steps'])
    if read_only and first_read is None:
        return {'passed': True, 'applicable': False, 'records': [],
                'reason': 'the flow has no database operation that a gate can hold'}
""")
LEASE_ROLE = ("        role, method = ('parent', 'get') if read_only else first_write\n",
              "        role, method = first_read if read_only else first_write\n")
LEASE_GATE = ("(PostgresReadGate(backend, prompt) if read_only", "(PostgresReadGate(backend, prompt, role) if read_only")
READ_GATE = ("""    def __init__(self, backend, prompt):
        assert backend.name.startswith('som_test_flow_'), backend.name
        table = prompt['resources']['parent']['table']
""", """    def __init__(self, backend, prompt, role='parent'):
        assert backend.name.startswith('som_test_flow_'), backend.name
        table = prompt['resources'][role]['table']
""")
FIRST_READ = ("""def checkpoint_target(nodes):
""", """def first_resource_read(nodes):
    \"\"\"The resource a read-only flow reads: the parent when any step reads it, as the original.\"\"\"
    def operations(items):
        for node in items:
            yield node['op']
            for key in ('steps', 'then', 'else'):
                yield from operations(node.get(key, []))
    found = set(operations(nodes))
    if found & {'parent.get', 'parent.list', 'workflow.run'}:
        return 'parent', 'get'
    if found & {'child.get', 'child.list'}:
        return 'child', 'get'
    return None


def checkpoint_target(nodes):
""")

# A public flow whose prompt lists only some operations is checked by the subset verifier, as
# verify_composed_services routes an operation subset. The original always checks the complete
# CRUD surface, which the legacy flows (no operations list) and the 11-operation public flows have.
SUBSET = ('    common["version"] = "som.prompt.v5"\n',
          '    common["version"] = ("som.prompt.v6" if len(common.get("operations", ())) not in (0, 11)\n'
          '                         else "som.prompt.v5")\n')

# An input that names an existing child or object is bound to one seeded through the HTTP API
# (phase2_host.existing_bindings). The original binds every integer input to the seeded parent.
EXISTING_SEED = ('            assert seed.status_code == 201, seed.text\n',
                 '            assert seed.status_code == 201, seed.text\n'
                 '            from phase2_host import seed_existing\n'
                 '            existing = seed_existing(prompt, client, seed.json()["id"])\n')
EXISTING_DEF = ("def flow_inputs(prompt, serial, choose, servings, existing_id):\n",
                "def flow_inputs(prompt, serial, choose, servings, existing_id, existing=None):\n")
EXISTING_RETURN = ('        inputs[field["name"]] = value\n    return inputs\n',
                   '        inputs[field["name"]] = value\n'
                   '    from phase2_host import bind_existing\n'
                   '    return bind_existing(prompt, inputs, existing)\n')
EXISTING_MAIN = ('True, 1, seed.json()["id"]), changes)', 'True, 1, seed.json()["id"], existing), changes)')
EXISTING_FAULTS = ("True, 1, seed.json()['id']))", "True, 1, seed.json()['id'], existing))")

# The provider fault matrix asserts that the interrupted object put follows a committed database
# write. A flow without that shape (only public single-operation examples) records it as not
# applicable instead of failing.
PROVIDER_SHAPE = ("""    if prompt['capabilities']['storage'] == 'none':
        return {'applicable': False, 'passed': True, 'records': []}
""", """    if prompt['capabilities']['storage'] == 'none':
        return {'applicable': False, 'passed': True, 'records': []}
    from phase2_host import object_put_after_database_write
    if not object_put_after_database_write(prompt):
        return {'applicable': False, 'passed': True, 'records': [],
                'reason': 'the flow puts no object after a database write'}
""")

# The late conflict of a CSV import needs a unique child field. Without one (only public
# examples) the conflict is recorded as not applicable; every other import check is the original.
CSV_UNIQUE = ("""        unique = next(f["name"] for f in child["fields"] if f["unique"])
        conflict = [payload(child, 94000 + i, True) for i in range(2)]
        conflict[1][unique] = conflict[0][unique]
        before = snapshot()
        request("POST", route + "/import", 409, content=csv_text(conflict), headers={"Content-Type": "text/csv"})
        assert before == snapshot()
""", """        unique = next((f["name"] for f in child["fields"] if f["unique"]), None)
        before = snapshot()
        if unique is not None:
            conflict = [payload(child, 94000 + i, True) for i in range(2)]
            conflict[1][unique] = conflict[0][unique]
            request("POST", route + "/import", 409, content=csv_text(conflict), headers={"Content-Type": "text/csv"})
            assert before == snapshot()
        else:
            done("csv_import_late_conflict_not_applicable_without_unique_child_field")
""")

# The original fills CSV text only into an input named csv. The input passed as the csv argument
# of workflow.import gets the same text under any name.
CSV_INPUT_IMPORT = ('    parent = payload(prompt["resources"]["parent"], serial)\n',
                    '    from phase2_host import csv_inputs\n'
                    '    parent = payload(prompt["resources"]["parent"], serial)\n')
CSV_INPUT = ('        elif field["name"] == "csv":\n',
             '        elif field["name"] == "csv" or field["name"] in csv_inputs(prompt):\n')

# A flow whose only writes are object puts has no database write to compensate or interrupt. The
# original reports such a structure as pending; the adapted copy records it as not applicable,
# like a flow without writes. A flow with any database write is checked as the original.
DATABASE_ONLY = ("    if not resource_write_operations(prompt['flow']['steps']):\n",
                 "    if not resource_write_operations(prompt['flow']['steps']) - {'object.put'}:\n")

# The revision_conflict phase edits a written row through its PATCH route. A product whose
# operation subset has no such route records that phase as not applicable; late_failure still runs.
REVISION_ROUTE = ("    for phase in ('late_failure', 'revision_conflict'):\n", """    from phase2_host import independent_patch_available
    phases = ('late_failure', 'revision_conflict')
    if not independent_patch_available(prompt):
        phases = ('late_failure',)
        records.append({'phase': 'revision_conflict', 'applicable': False, 'passed': True,
                        'reason': 'the product has no PATCH route for the independent edit'})
    for phase in phases:
""")

# module: (original sha256, line the host import goes after or None, [(old, new, count), ...])
MODULES: dict[str, tuple[str, str | None, list[tuple[str, str, int]]]] = {
    "verify_related": ("ec9cc11116c2ceb46dac687da9291faefd7c56aefbb282a5e088e1b5272b1ba1", "import httpx\n", [
        ('subprocess.Popen([str(self.root / ".venv/bin/python"), "-m", "uvicorn", self.package + ".app:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"], cwd=self.root',
         "subprocess.Popen(host_for(self.root).serve_command(self.package, self.port), cwd=self.root", 1),
    ]),
    "verify_composed_related": ("0b02c9e05b930f300d1168fb292c7fa863826d0be87d8eed70a712bb9a53dc29",
                                "from verify_related import Server, payload, run\n", [
        (*UV_SYNC, 1), (*PROBE, 1), (*MIGRATE, 3), (*UNITTEST, 1),
    ]),
    "audit_business_transactions": ("2f8f617bed62963db89066db18772bbd08f9ae31bac54166aee322191eb4122a",
                                    "from verify_related import Server, payload, run\n", [
        (*UV_SYNC, 1), (*MIGRATE, 1), (*SUFFIXES, 1),
    ]),
    "verify_composed_transactions": ("2cc9931592f8bf32a458aee32274125c6ac347b817191375090c011c69d07a85",
                                     "from verify_related import Server, payload, run\n", [
        (*UV_SYNC, 1), (*MIGRATE, 1), (*SUFFIXES, 1),
    ]),
    "verify_composed_journeys": ("b39a29d181d023dd9bf71aed72b8602cb06224b7880159732499cde90f8b286c",
                                 "from verify_related import Server, payload, run\n", [
        (*UV_SYNC, 1),
        ('backend.migration_command(str(root / ".venv/bin/python"), True)', "host_for(root).migration_command(backend, True)", 1),
    ]),
    "verify_composed_subsets": ("97d33fb63aa061190ede56b4ff2179d28927b2211b3bf46b3e3487a30b8c18ae",
                                "from verify_related import Server, payload, run\n", [
        ("run(['uv','sync','--python','3.12'],cwd=root,env=env)", "host_for(root).prepare(env)", 1),
        ("subprocess.run([python,'-c',f'from fastapi.testclient import TestClient\\nfrom {prompt[\"package\"]}.app import app\\nwith TestClient(app): pass'],cwd=root,env=missing,text=True,capture_output=True)",
         "subprocess.run(host_for(root).startup_probe(prompt['package']),cwd=root,env=missing,text=True,capture_output=True)", 1),
        ("backend.migration_command(python,", "host_for(root).migration_command(backend,", 3),
        ("run([python,'-m','unittest','discover','-s','tests','-v'],cwd=root,env=env)",
         "run(host_for(root).test_command(),cwd=root,env=env)", 1),
        ("specification=request('GET','/openapi.json').json()['paths']",
         "specification=host_for(root).route_surface(client,resources)", 1),
    ]),
    "verify_flow_service": ("6cd3b8932d5c9c092f12baeca442814c66cbc3fe22cc10cdee100e126827fbbe",
                            "from verify_related import Server, payload, run\n", [
        (*UV_SYNC, 1), (*MIGRATE, 1), (*CONFLICT, 1), (*SUBSET, 1), (*EXISTING_SEED, 1),
        (*EXISTING_DEF, 1), (*EXISTING_RETURN, 1), (*EXISTING_MAIN, 1), (*EXISTING_FAULTS, 4), (*CSV_INPUT_IMPORT, 1), (*CSV_INPUT, 1),
        ("    command = [python, '-m', 'unittest', 'discover', '-s', 'tests', '-v']\n",
         "    command = host_for(root).test_command()\n", 1),
        ("    summary = re.search(r'Ran (\\d+) tests? in ', result.stderr)\n"
         "    assert result.returncode == 0 and summary and int(summary[1]) > 0, (\n"
         "        result.returncode, result.stdout, result.stderr)\n"
         "    assert '(skipped=' not in result.stderr, result.stderr\n"
         "    return {'passed': True, 'command': command, 'tests': int(summary[1]),\n",
         "    tests, skipped = host_for(root).test_summary(result)\n"
         "    assert result.returncode == 0 and tests > 0, (\n"
         "        result.returncode, result.stdout, result.stderr)\n"
         "    assert not skipped, result.stderr\n"
         "    return {'passed': True, 'command': command, 'tests': tests,\n", 1),
    ]),
    "flow_branch_cases": ("52aeb03342c99a985ade2cb833e7a6f0b657bfa3eb6ed603cc74b4ef3a4241ac", None, [(*PIVOT, 1)]),
    "flow_fault_checks": ("a20be84fb254c52197a673ecc51db15fa05ea8ddbd188f6d1baa3a21f967ea47",
                          "from verify_related import payload\n", [
        (*KILL_16, 2), (*LEASE_READ, 1), (*LEASE_ROLE, 1), (*LEASE_GATE, 1), (*READ_GATE, 1), (*FIRST_READ, 1), (*DATABASE_ONLY, 2), (*REVISION_ROUTE, 1),
    ]),
    "flow_provider_fault_checks": ("27b814508cc3cfc35f5f020fcd02f831f7fcfb40bc20fc4d8c74772993d6a58a",
                                   "from flow_fault_checks import database_rows, require_restored, run_id, wait_for\n",
                                   [(*KILL_4, 1), (*PROVIDER_SHAPE, 1)]),
    "verify_business_workflows": ("45edd24a39ea095212d43285ca57c5e742f7ab4c914b0def3824244ef33d7a48", None,
                                  [(*CSV_UNIQUE, 1)]),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def adapted(name: str) -> str:
    pinned, anchor, replacements = MODULES[name]
    raw = (SCRIPTS / f"{name}.py").read_bytes()
    if sha(raw) != pinned:
        raise SystemExit(f"{name}.py has sha256 {sha(raw)}, pinned {pinned}")
    text = raw.decode()
    if anchor is not None and text.count(anchor) != 1:
        raise SystemExit(f"{name}: import anchor matches {text.count(anchor)} times")
    for old, new, count in replacements:
        if text.count(old) != count:
            raise SystemExit(f"{name}: {old[:60]!r} matches {text.count(old)} times, expected {count}")
        text = text.replace(old, new)
    if anchor is not None:
        text = text.replace(anchor, anchor + IMPORT)
    header = (f"# Adapted by som-rs/behavior/phase2/adapt.py from som-core/scripts/{name}.py "
              f"(sha256 {pinned}). Do not edit.\n")
    return header + text


def manifest(files: dict[str, str]) -> str:
    value = {"version": "som.rust-output-phase2-adapted-verifiers.v1",
             "originals": {f"som-core/scripts/{n}.py": MODULES[n][0] for n in MODULES},
             "adapted": {f"{n}.py": sha(files[n].encode()) for n in MODULES},
             "replacements": {n: len(MODULES[n][2]) for n in MODULES}}
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = {name: adapted(name) for name in MODULES}
    expected = {f"{n}.py": s for n, s in files.items()} | {"MANIFEST.json": manifest(files)}
    if args.check:
        actual = {p.name: p.read_text() for p in OUT.glob("*") if p.is_file()} if OUT.is_dir() else {}
        if actual != expected:
            print("adapted/ differs from adapt.py output:", sorted(set(actual) ^ set(expected))
                  or sorted(k for k in expected if actual.get(k) != expected[k]), file=sys.stderr)
            return 1
        print(f"adapted/ is current: {len(files)} modules")
        return 0
    OUT.mkdir(exist_ok=True)
    for name, text in expected.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(files)} adapted modules to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
