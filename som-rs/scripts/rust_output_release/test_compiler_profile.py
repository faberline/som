"""Compiler profile fixtures. All runtime owners and process calls are fakes."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bound(binding):
    path = Path(binding["path"])
    require(path.is_absolute() and digest(path) == binding["sha256"], "bound input changed")
    return path


def pointer(value, selector):
    for key in selector.removeprefix("/").split("/"):
        value = value[key]
    return value


def forbidden(*_args, **_kwargs):
    raise AssertionError("a real process operation must not run in this fixture")


def isolated_modules():
    """Use the real nonfrozen controller helpers with a fake frozen base."""
    fake = types.SimpleNamespace(Run=object, require=require, digest=digest,
        load=lambda path: json.loads(Path(path).read_bytes()),
        canon=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
        exact_json=lambda left, right: json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True),
        bound=bound, pointer=pointer)
    fake.validate_argv = lambda argv: require(isinstance(argv, list) and bool(argv)
        and all(isinstance(value, str) and value and not any(char.isspace() for char in value) for value in argv), "invalid fake argv")
    for name in ("link_state", "atomic_bytes", "atomic_link", "identity", "alive",
                 "terminate", "finish_legacy_stop", "free_port", "port_free", "rollback_defer_signal"):
        setattr(fake, name, forbidden)
    path = HERE / "release_common.py"
    source = path.read_text()
    require(source.count("base = _load_base()") == 1, "base injection point changed")
    common = types.ModuleType("fixture_compiler_release_common")
    common.__file__ = str(path)
    common.__dict__["_FAKE_BASE"] = fake
    exec(compile(source.replace("base = _load_base()", "base = _FAKE_BASE"), str(path), "exec"), common.__dict__)
    modules = {"common": common}
    with mock.patch.dict(sys.modules, {"release_common": common}):
        for name in ("switch", "check_default", "rehearse"):
            spec = importlib.util.spec_from_file_location("fixture_compiler_" + name, HERE / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
    return modules


class Fixture:
    def __init__(self, root, rc):
        self.root, self.rc = root, rc
        self.config = {"version": "som.compiler-output-switch-prerequisites.v1", "repo": str(root),
                       "profile": "compiler-only", "gates": {}, "previous_canaries": {}}
        formatter = root / "prettier"
        formatter.mkdir(parents=True)
        formatter_bytes = b"fake pinned formatter; never executed\n"
        (formatter / "standalone.mjs").write_bytes(formatter_bytes)
        formatter_sha = hashlib.sha256(b"standalone.mjs\0" + hashlib.sha256(formatter_bytes).hexdigest().encode() + b"\n").hexdigest()
        self.runtimes = {}
        for role, targets in (("candidate", ["python", "rust", "typescript", "react"]),
                              ("previous", ["python", "rust", "typescript"])):
            directory = root / role
            directory.mkdir(parents=True)
            (directory / "som").write_bytes(("fake binary " + role).encode())
            assets = directory / "assets"
            assets.mkdir()
            (assets / "blocks.json").write_bytes(("fake blocks " + role).encode())
            manifest = {"files": {"blocks.json": digest(assets / "blocks.json")}}
            self.write(assets / "manifest.json", manifest)
            health = {"targets": targets, "asset_manifest_sha256": rc.sha_bytes(rc.canon(manifest).encode())}
            self.write(directory / "source.json", {"commit": "a" * 40})
            source_sha = digest(directory / "source.json")
            runtime = {"binary_sha256": digest(directory / "som"), "health": health, "accepted": False,
                       "source_sha256": source_sha, "prettier_tree_sha256": formatter_sha}
            self.write(directory / "runtime.json", runtime)
            self.write(directory / "build.json", {"binary_sha256": runtime["binary_sha256"], "complete": True,
                                                   "source_sha256": source_sha, "source_unchanged": True,
                                                   "tree_before": {"head": "a" * 40, "porcelain": ""},
                                                   "tree_after": {"head": "a" * 40, "porcelain": ""},
                                                   "prettier_tree_sha256": formatter_sha})
            self.config[role] = {name: self.binding(directory / filename)
                                 for name, filename in (("binary", "som"), ("runtime", "runtime.json"),
                                                        ("build", "build.json"), ("source", "source.json"))}
            self.config[role]["assets"] = {"path": str(assets), "manifest_sha256": health["asset_manifest_sha256"]}
            self.runtimes[role] = runtime
        self.config["typescript_formatter"] = {"env": "SOM_TS_PRETTIER", "path": str(formatter), "tree_sha256": formatter_sha}
        gate = root / "accepted-gate.json"
        self.write(gate, {"passed": True})
        self.config["gates"]["acceptance"] = {**self.binding(gate), "assertions": {"/passed": True}}
        for family in ("python", "rust", "typescript", "react"):
            self.config[family + "_canaries"] = [self.canary(family, index) for index in range(2)]
            if family != "react":
                self.config["previous_canaries"][family] = [self.canary("previous-" + family, index) for index in range(2)]
        for cases in (self.config["python_canaries"], self.config["previous_canaries"]["python"]):
            cases[0]["family"], cases[1]["family"] = "flow", "legacy"

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def binding(self, path):
        return {"path": str(path), "sha256": digest(path)}

    def canary(self, family, index):
        directory = self.root / "inputs" / family / str(index)
        directory.mkdir(parents=True)
        request, response = directory / "request.json", directory / "response.json"
        request.write_bytes(b'{"format":"som.prompt-json.v1"}')
        response.write_bytes(b'{"status":"ok"}')
        cnl = directory / "source.cnl"
        cnl.write_bytes(b"Fake public CNL.\n")
        return {"name": f"{family}-{index}", "family": family,
                "steps": [{"kind": "http", "name": "compile", "path": "/v1/compile",
                           "request": self.binding(request), "response": self.binding(response)},
                          {"kind": "cli", "name": "cli-compile", "args": ["compile", str(cnl)],
                           "inputs": [self.binding(cnl)], "binaries": ["previous" if family.startswith("previous-") else "new"],
                           "response": self.binding(response)}]}


class CompilerProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-compiler-profile-")
        self.addCleanup(temporary.cleanup)
        self.modules = isolated_modules()
        self.rc = self.modules["common"]
        self.root = Path(temporary.name).resolve()
        self.fixture = Fixture(self.root, self.rc)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.rc, "REPO", self.root).start()

    def test_compiler_profile_selects_all_candidate_and_previous_families(self):
        config = self.fixture.config
        self.assertIs(self.rc.compiler_profile(config), True)
        self.assertEqual(self.rc.candidate_cases(config), sum((config[name + "_canaries"]
                         for name in ("python", "rust", "typescript", "react")), []))
        self.assertEqual(self.rc.previous_cases(config), sum((config["previous_canaries"][name]
                         for name in ("python", "rust", "typescript")), []))
        self.rc.check_prerequisites(config)

    def test_each_required_family_needs_two_bound_cases(self):
        original = self.fixture.config
        for role, family in (("candidate", "python"), ("candidate", "rust"),
                             ("candidate", "typescript"), ("candidate", "react"),
                             ("previous", "python"), ("previous", "rust"), ("previous", "typescript")):
            with self.subTest(role=role, family=family):
                config = copy.deepcopy(original)
                if role == "candidate":
                    config[family + "_canaries"] = config[family + "_canaries"][:1]
                else:
                    config["previous_canaries"][family] = config["previous_canaries"][family][:1]
                with self.assertRaises(RuntimeError):
                    self.rc.check_prerequisites(config)

    def test_each_candidate_and_previous_identity_file_is_bound(self):
        for role in ("candidate", "previous"):
            for name in ("binary", "runtime", "build", "source"):
                with self.subTest(role=role, binding=name):
                    config = copy.deepcopy(self.fixture.config)
                    config[role][name]["sha256"] = "0" * 64
                    with self.assertRaises(RuntimeError):
                        self.rc.check_prerequisites(config)

    def test_source_runtime_and_build_binary_disagreement_refuses(self):
        fixture = self.fixture
        for role in ("candidate", "previous"):
            with self.subTest(role=role):
                config = copy.deepcopy(fixture.config)
                path = Path(config[role]["runtime"]["path"])
                original = path.read_bytes()
                changed = json.loads(original)
                changed["binary_sha256"] = "0" * 64
                fixture.write(path, changed)
                config[role]["runtime"] = fixture.binding(path)
                with self.assertRaises(RuntimeError):
                    self.rc.check_prerequisites(config)
                path.write_bytes(original)

    def test_asset_file_and_pinned_canary_drift_refuse(self):
        fixture = self.fixture
        config = fixture.config
        block = Path(config["previous"]["assets"]["path"]) / "blocks.json"
        original = block.read_bytes()
        block.write_bytes(b"changed assets\n")
        with self.assertRaises(RuntimeError):
            self.rc.check_prerequisites(config)
        block.write_bytes(original)
        request = Path(config["react_canaries"][0]["steps"][0]["request"]["path"])
        request.write_bytes(b"changed HTTP request\n")
        with self.assertRaises(RuntimeError):
            self.rc.check_prerequisites(config)

    def test_canary_cli_owner_source_and_target_are_required(self):
        for mutation in ("owner", "source", "target", "route"):
            with self.subTest(mutation=mutation):
                config = copy.deepcopy(self.fixture.config)
                steps = config["react_canaries"][0]["steps"]
                if mutation == "owner":
                    steps[1]["binaries"] = ["previous"]
                elif mutation == "source":
                    steps[1]["inputs"] = []
                elif mutation == "target":
                    steps[1]["args"] += ["--target", "rust"]
                else:
                    steps[0]["path"] = "/arbitrary"
                with self.assertRaises(RuntimeError):
                    self.rc.check_prerequisites(config)

    def test_rebound_gate_cannot_turn_boolean_pass_into_integer_one(self):
        fixture = self.fixture
        config = copy.deepcopy(fixture.config)
        gate = Path(config["gates"]["acceptance"]["path"])
        fixture.write(gate, {"passed": 1})
        config["gates"]["acceptance"].update(fixture.binding(gate))
        with self.assertRaises(RuntimeError):
            self.rc.check_prerequisites(config)

    def test_formatter_bytes_must_match_both_candidate_receipts(self):
        path = Path(self.fixture.config["typescript_formatter"]["path"]) / "standalone.mjs"
        path.write_bytes(b"different fake formatter\n")
        with self.assertRaises(RuntimeError):
            self.rc.check_prerequisites(self.fixture.config)

    def test_legacy_profile_keeps_existing_previous_python_selection(self):
        config = {"version": self.rc.PREREQUISITES_VERSION,
                  "python_canaries": [{"name": "python"}], "rust_canaries": [{"name": "rust"}],
                  "previous_python_canaries": [{"name": "previous-python"}]}
        self.assertIs(self.rc.compiler_profile(config), False)
        self.assertEqual(self.rc.previous_cases(config), config["previous_python_canaries"])
        self.assertEqual(self.rc.candidate_cases(config), config["python_canaries"] + config["rust_canaries"])
        self.assertIsNone(self.rc.assert_current(config, {}))

    def test_unknown_expected_binary_hash_refuses(self):
        for role in ("candidate", "previous"):
            self.assertEqual(self.rc.expected_runtime(self.fixture.config, self.fixture.config[role]["binary"]["sha256"]),
                             self.fixture.runtimes[role])
        with self.assertRaises(RuntimeError):
            self.rc.expected_runtime(self.fixture.config, "f" * 64)

    def test_current_receipt_must_bind_selected_binary_assets_and_health(self):
        fixture = self.fixture
        for candidate in (False, True):
            role = "candidate" if candidate else "previous"
            current = {"command": [fixture.config[role]["binary"]["path"], "serve", "--assets",
                                   fixture.config[role]["assets"]["path"], "--port", "18940"],
                       "identity": {"binary_sha256": fixture.config[role]["binary"]["sha256"]},
                       "health": fixture.runtimes[role]["health"]}
            self.rc.assert_current(fixture.config, current, candidate=candidate)
            for field in ("binary", "assets", "health"):
                with self.subTest(role=role, field=field):
                    changed = copy.deepcopy(current)
                    if field == "binary":
                        changed["identity"]["binary_sha256"] = "0" * 64
                    elif field == "assets":
                        changed["command"][3] = fixture.config["previous" if candidate else "candidate"]["assets"]["path"]
                    else:
                        changed["health"]["targets"] = ["python"]
                    with self.assertRaises(RuntimeError):
                        self.rc.assert_current(fixture.config, changed, candidate=candidate)

    def test_compiler_rehearsal_runs_five_fake_scenarios_without_live_default(self):
        fixture, rehearsal = self.fixture, self.modules["rehearse"]
        prerequisites = self.root / "prerequisites.json"
        fixture.write(prerequisites, fixture.config)
        live = self.root / "som-rs/runtime/default.json"
        live.parent.mkdir(parents=True)
        live.write_bytes(b"invalid live receipt: must never be read\n")
        output = self.root / ".local/rust-compiler-goal/acceptance/rehearsal"
        launched, calls = [], []

        def offline(directory, seed, port, source_sha):
            self.assertIn(port, self.rc.HARNESS_PORTS)
            self.assertEqual(seed['command'][0], fixture.config["previous"]["binary"]["path"])
            self.assertEqual(seed['health'], fixture.runtimes["previous"]["health"])
            launched.append(port)
            root = directory / 'root'
            (root / 'som-rs/runtime').mkdir(parents=True)
            (root / 'som-rs/compiler-assets').mkdir(parents=True)
            (root / 'som-rs/runtime/current').symlink_to(Path(seed['command'][0]).parent)
            (root / 'som-rs/compiler-assets/current').symlink_to(seed['command'][3])
            command = seed['command'][:-1] + [str(port)]
            receipt = {**seed, 'pid': 9000 + port, 'command': command,
                       'identity': {**seed['identity'], 'pid': 9000 + port, 'argv': command, 'port': port}}
            fixture.write(root / 'som-rs/runtime/default.json', receipt)
            fixture.write(directory / 'stand-in/stale-default.json', receipt)
            return root, {'pid': receipt['pid'], 'old_pid_alive': False,
                          'receipt': 'stand-in/stale-default.json', 'default_sha256': digest(directory / 'stand-in/stale-default.json')}

        def call(argv, directory, name):
            port = int(argv[argv.index("--port") + 1])
            self.assertIn(port, self.rc.HARNESS_PORTS)
            calls.append((name, argv))
            destination = Path(argv[argv.index("--output") + 1])
            destination.mkdir()
            selected = argv[argv.index("--inject-failure") + 1] if "--inject-failure" in argv else None
            if name in ("switch", "restore", "resume"):
                stage = "resume_verified" if name == "resume" else "restore_verified" if name == "restore" else "rollback_verified" if selected else "committed"
                fixture.write(destination / "journal.json", [{"stage": stage,
                              "message": f"injected failure at {selected}" if selected else "fake completed stage"}])
                if name == 'resume':
                    path = Path(argv[argv.index('--root') + 1]) / 'som-rs/runtime/default.json'
                    receipt = json.loads(path.read_bytes())
                    receipt['pid'] += 100
                    receipt['identity']['pid'] = receipt['pid']
                    fixture.write(path, receipt)
            return {"name": name, "argv": argv, "exit_code": 1 if selected else 0}

        with mock.patch.object(rehearsal, "offline_sandbox", side_effect=offline), \
             mock.patch.object(rehearsal, "call", side_effect=call), \
             mock.patch.object(self.rc, "free_harness_port", side_effect=range(18940, 18945)), \
             mock.patch.object(self.rc, "alive", return_value=False), \
             mock.patch.object(self.rc, "post_raw", side_effect=forbidden), \
             mock.patch.object(rehearsal.subprocess, "run", side_effect=forbidden), \
             mock.patch.dict(self.rc.os.environ, {}, clear=True), \
             mock.patch.object(sys, "argv", ["rehearse.py", "--prerequisites", str(prerequisites), "--out", str(output)]), \
             mock.patch("builtins.print"):
            self.assertEqual(rehearsal.main(), 0)
        report = json.loads((output / "rehearsal.json").read_bytes())
        self.assertEqual([row["name"] for row in report["scenarios"]],
                         ["switch", "after_stop", "after_start", "after_pointer", "after_commit"])
        self.assertTrue(all(row["passed"] for row in report["scenarios"]))
        self.assertIs(report["live_default_accessed"], False)
        self.assertEqual(len(launched), 5)
        self.assertTrue(all(row['resume']['exit_code'] == 0 and row['offline_seed']['old_pid_alive'] is False
                            for row in report['scenarios']))
        self.assertEqual(live.read_bytes(), b"invalid live receipt: must never be read\n")
        previous_sha = fixture.config["previous"]["binary"]["sha256"]
        for name, argv in calls:
            if name == "check-restored" or (name == "check" and "switch" not in Path(argv[argv.index("--root") + 1]).parts):
                self.assertEqual(argv[argv.index("--expect-binary-sha256") + 1], previous_sha)

    def test_rollback_runs_every_previous_compiler_family(self):
        config = self.fixture.config
        previous = self.modules["rehearse"].rehearsal_seed(config)
        run = types.SimpleNamespace(children=[], start=mock.Mock(return_value={"pid": 999}),
                                    health=mock.Mock(), canary=mock.Mock())
        cases = self.rc.previous_cases(config)
        pointers = {"runtime": {"kind": "absent"}, "assets": {"kind": "absent"}}
        with mock.patch.object(self.rc, "atomic_link"):
            result = self.modules["switch"].bring_back(run, self.root, 18940, previous, pointers, [], {}, cases)
        self.assertEqual(result, {"pid": 999})
        run.canary.assert_called_once_with(18940, {}, cases, "restored-default")
        self.assertEqual(len(cases), 6)
        self.assertEqual({case["family"] for case in cases}, {"flow", "legacy", "previous-rust", "previous-typescript"})


if __name__ == "__main__":
    unittest.main()
