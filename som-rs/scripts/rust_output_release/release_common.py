"""Shared helpers for the #51 Rust output release tools.

The process, port, pointer and receipt helpers come from the #44 switch
controller (compiler_default_switch.py), loaded by path after its sha256 is
checked. That file is not changed. This module adds raw byte exchanges and the
pinned canaries the #51 prerequisites describe.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[3]
BASE_PATH = REPO / 'som-rs/scripts/compiler_default_switch.py'
BASE_SHA256 = '289a8470c5de2a183b01c8a138ecb4bc6cfe80a624f0291b32b779cc854bfb12'
PREREQUISITES_VERSION = 'som.rust-output-switch-prerequisites.v1'
COMPILER_PREREQUISITES_VERSION = 'som.compiler-output-switch-prerequisites.v1'
PROTECTED_PORTS = {8765, 18765}
HARNESS_PORTS = range(18919, 19000)


def _load_base():
    actual = hashlib.sha256(BASE_PATH.read_bytes()).hexdigest()
    if actual != BASE_SHA256:
        raise RuntimeError(f'{BASE_PATH} changed: {actual}')
    spec = importlib.util.spec_from_file_location('compiler_default_switch', BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {BASE_PATH}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()
require, digest, load, canon, exact_json = base.require, base.digest, base.load, base.canon, base.exact_json
validate_argv, bound, link_state = base.validate_argv, base.bound, base.link_state
atomic_bytes, atomic_link, identity, alive = base.atomic_bytes, base.atomic_link, base.identity, base.alive
terminate, finish_stop, free_port, port_free = base.terminate, base.finish_legacy_stop, base.free_port, base.port_free
rollback_defer_signal = base.rollback_defer_signal


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def harness_port(port):
    require(port in HARNESS_PORTS and port not in PROTECTED_PORTS, f'{port} is not a harness port (18919-18999)')


def free_harness_port(exclude=()):
    """A harness port with no listener. Ports outside 18919-18999 are never used."""
    for port in HARNESS_PORTS:
        if port in exclude:
            continue
        try:
            port_free(port)
            return port
        except (RuntimeError, OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError('no free harness port')


def post_raw(port, path, body):
    """One loopback exchange without proxies or redirects. Returns (status, bytes)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=body,
                                     headers={'Content-Type': 'application/json'},
                                     method='GET' if body is None else 'POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def cli_argv(binary, step, port):
    return [str(binary), *step['args'], '--url', f'http://127.0.0.1:{port}']


class Release(base.Run):
    """The #44 Run with byte-exact exchanges and the #51 pinned canaries."""

    def start(self, argv, expected):
        """Defer interruption until the inherited start owns its child handle.

        The inherited method owns startup-failure cleanup. On success it records
        the owner before returning. The caller's normal interrupt handler then
        drives its existing rollback/cleanup. Never infer cleanup from deferral.
        """
        previous, pending = {}, []
        def defer(signum, frame):
            pending.append((signum, frame))
        try:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                handler = signal.getsignal(signum)
                if callable(handler):
                    previous[signum] = handler
                    signal.signal(signum, defer)
            owner = super().start(argv, expected)
        finally:
            # Block only while restoring handlers, never across the child exec.
            # This prevents an arriving signal from interrupting restoration.
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(previous))
            try:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if pending:
            previous[pending[0][0]](*pending[0])
        return owner

    def raw(self, port, path, body):
        if body is not None:
            self.save('request.raw', body)
        status, data = post_raw(port, path, body)
        self.save('response.raw', data)
        require(status == 200, f'HTTP {status}: {path} on {port}')
        return data

    def cli_raw(self, binary, step, port):
        argv = cli_argv(binary, step, port)
        result = subprocess.run(argv, capture_output=True, timeout=120)
        self.save('cli-argv.json', canon(argv).encode())
        self.save('cli-stdout.raw', result.stdout)
        self.save('cli-stderr.raw', result.stderr)
        require(result.returncode == 0, f'CLI failed: {step["args"][0]}: {result.returncode}')
        return result.stdout

    def canary(self, port, binaries, cases, label):
        """Every pinned HTTP and CLI exchange of every case, byte for byte."""
        for case in cases:
            for step in case['steps']:
                expected = bound(step['response']).read_bytes()
                if step['kind'] == 'http':
                    body = bound(step['request']).read_bytes()
                    require(self.raw(port, step['path'], body) == expected,
                            f'{case["name"]} {step["name"]} differs from the pinned response')
                else:
                    for name in step['binaries']:
                        require(self.cli_raw(binaries[name], step, port) == expected,
                                f'{case["name"]} {step["name"]} ({name} CLI) differs from the pinned stdout')
            self.event('canary_passed', name=case['name'], port=port, label=label)

    def m2_canary(self, port, binaries, cases):
        """Generate on a legacy M2 service: the full reply and the CLI stdout bytes."""
        for case in cases:
            expected_bytes = bound(case['expected_response']).read_bytes()
            expected = json.loads(expected_bytes)
            prompt = bound(case['prompt'])
            reply = json.loads(self.raw(port, '/v1/generate', canon(load(prompt)).encode()))
            require(reply == expected, f'{case["name"]} M2 generate differs from the pinned reply')
            for name in ('legacy', 'new'):
                step = {'args': ['generate', str(prompt)]}
                require(self.cli_raw(binaries[name], step, port) == expected_bytes,
                        f'{case["name"]} M2 generate ({name} CLI) differs')
            self.event('m2_canary_passed', name=case['name'], port=port)


def check_prerequisites(config):
    """Hashes of every bound input. Returns the parsed receipts the tools need."""
    if config.get('version') == COMPILER_PREREQUISITES_VERSION:
        return check_compiler_prerequisites(config)
    require(config.get('profile') in (None, 'legacy-m2'), 'compiler-only requires its explicit version')
    require(config['version'] == PREREQUISITES_VERSION, 'wrong prerequisites version')
    repo = Path(config['repo'])
    require(repo == REPO, 'prerequisites belong to another repository')
    candidate = {name: bound(config['candidate'][name]) for name in ('binary', 'runtime', 'build')}
    runtime = load(candidate['runtime'])
    require(runtime['binary_sha256'] == config['candidate']['binary']['sha256'], 'candidate receipt binary differs')
    require(load(candidate['build'])['binary_sha256'] == runtime['binary_sha256'], 'build receipt binary differs')
    for gate in config['gates'].values():
        receipt = load(bound(gate))
        for selector, expected in gate['assertions'].items():
            actual = base.pointer(receipt, selector)
            require(type(actual) is type(expected) and actual == expected, f'gate failed: {selector}')
    legacy = load(bound(config['legacy_default']))
    for name in ('legacy_binary', 'legacy_model_manifest', 'legacy_blocks'):
        bound(config[name])
    for case in (config['python_canaries'] + previous_python(config)
                 + config['rust_canaries'] + config.get('typescript_canaries', [])
                 + config.get('react_canaries', [])):
        for step in case['steps']:
            bound(step['response'])
            if step['kind'] == 'http':
                bound(step['request'])
            for binding in step.get('inputs', []):
                bound(binding)
    for case in config['m2_canaries']:
        bound(case['prompt'])
        bound(case['expected_response'])
    require({c['family'] for c in config['python_canaries']} >= {'flow', 'legacy'}, 'missing python canary family')
    require(len(config['rust_canaries']) >= 2, 'need two Rust canaries')
    if 'react_canaries' in config:
        require(len(config['react_canaries']) >= 2, 'need two React canaries')
    if 'typescript_canaries' in config:
        require(len(config['typescript_canaries']) >= 2, 'need two TypeScript canaries')
        formatter = config.get('typescript_formatter')
        require(isinstance(formatter, dict) and formatter.get('env') == 'SOM_TS_PRETTIER',
                'missing or invalid typescript_formatter')
        formatter_path = formatter.get('path')
        require(isinstance(formatter_path, str) and Path(formatter_path).is_absolute() and Path(formatter_path).exists(),
                f'invalid typescript_formatter path: {formatter_path}')
    if 'assets' in config['candidate']:
        assets_match(Path(config['candidate']['assets']['path']), runtime['health'])
        require({c['family'] for c in previous_python(config)} >= {'flow', 'legacy'},
                'missing previous python canary family')
    return runtime, legacy


def compiler_profile(config):
    return config.get('version') == COMPILER_PREREQUISITES_VERSION and config.get('profile') == 'compiler-only'


def candidate_cases(config):
    return (config['python_canaries'] + config['rust_canaries']
            + config.get('typescript_canaries', []) + config.get('react_canaries', []))


def previous_cases(config):
    if compiler_profile(config):
        return [case for family in ('python', 'rust', 'typescript') for case in config['previous_canaries'][family]]
    return previous_python(config)


def profile_runtime(config, candidate=True):
    return load(bound(config['candidate' if candidate else 'previous']['runtime']))


def expected_runtime(config, expected_sha):
    require(compiler_profile(config), 'expected runtime selection needs the compiler-only profile')
    roles = [name for name in ('candidate', 'previous') if config[name]['binary']['sha256'] == expected_sha]
    require(len(roles) == 1, 'expected binary is not a unique pinned compiler')
    return profile_runtime(config, roles[0] == 'candidate')


def formatter_tree_sha(root):
    """The exact prettier tree algorithm used by prepare_candidate.py."""
    root = Path(root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'formatter is not a physical directory')
    tree = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            tree.update(f'{relative}\0symlink:{os.readlink(path)}\n'.encode())
        elif path.is_file():
            tree.update(f'{relative}\0{digest(path)}\n'.encode())
        else:
            require(path.is_dir(), 'formatter has a special file')
    return tree.hexdigest()


def compiler_artifact(config, role):
    artifact = config[role]
    paths = {name: bound(artifact[name]) for name in ('binary', 'runtime', 'build', 'source')}
    require(all(not path.is_symlink() for path in paths.values()), f'{role} input is a symlink')
    runtime, build, source = (load(paths[name]) for name in ('runtime', 'build', 'source'))
    binary_sha, source_sha = artifact['binary']['sha256'], artifact['source']['sha256']
    require(runtime['binary_sha256'] == build['binary_sha256'] == binary_sha, f'{role} receipt binary differs')
    require(runtime.get('source_sha256') == build.get('source_sha256') == source_sha, f'{role} source receipt differs')
    require(build.get('complete') is True and build.get('source_unchanged') is True, f'{role} build is not complete')
    commit = source.get('commit')
    require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit), f'{role} source commit missing')
    for point in ('tree_before', 'tree_after'):
        require(build[point].get('head') == commit and build[point].get('porcelain') == '', f'{role} source changed')
    assets = Path(artifact['assets']['path'])
    require(assets.is_absolute() and assets.is_dir() and not assets.is_symlink(), f'{role} assets directory invalid')
    manifest = assets / 'manifest.json'
    require(not manifest.is_symlink(), f'{role} manifest is a symlink')
    for name in load(manifest)['files']:
        require(Path(name).name == name, 'asset filename must be flat')
    require(artifact['assets']['manifest_sha256'] == runtime['health']['asset_manifest_sha256'], f'{role} asset binding differs')
    assets_match(assets, runtime['health'])
    return runtime, build


def check_compiler_prerequisites(config):
    require(compiler_profile(config), 'wrong compiler-only profile')
    require(Path(config['repo']) == REPO, 'prerequisites belong to another repository')
    runtime, build = compiler_artifact(config, 'candidate')
    previous, previous_build = compiler_artifact(config, 'previous')
    require(config['candidate']['binary']['sha256'] != config['previous']['binary']['sha256'], 'compiler identities must differ')
    require(set(config['previous_canaries']) == {'python', 'rust', 'typescript'}, 'previous compiler families differ')
    for role, groups in (('new', {family: config[family + '_canaries'] for family in ('python', 'rust', 'typescript', 'react')}),
                         ('previous', config['previous_canaries'])):
        for family, cases in groups.items():
            require(isinstance(cases, list) and len(cases) >= 2 and len({case['name'] for case in cases}) == len(cases),
                    f'need two distinct {role} {family} canaries')
            if family == 'python':
                require({case['family'] for case in cases} >= {'flow', 'legacy'}, 'missing Python flow or legacy family')
            for case in cases:
                steps = case['steps']
                require(isinstance(steps, list) and {step['kind'] for step in steps} == {'http', 'cli'}, 'canary needs HTTP and CLI evidence')
                for step in steps:
                    bound(step['response'])
                    if step['kind'] == 'http':
                        require(step['path'] in ('/v1/compile', '/v1/assemble', '/v1/generate'), 'unexpected canary route')
                        bound(step['request'])
                    else:
                        args = step['args']
                        validate_argv(args)
                        require(args[0] in ('compile', 'assemble', 'generate') and len(args) in (2, 4), 'unexpected canary CLI')
                        require(len(args) == 2 or (args[0] == 'compile' and args[2:] == ['--target', family]), 'unexpected canary target')
                        require(step['binaries'] == [role], 'canary CLI belongs to another compiler')
                        inputs = step.get('inputs', [])
                        require(inputs and args[1] in [str(bound(binding)) for binding in inputs], 'CLI source is not bound')
                    for binding in step.get('inputs', []):
                        bound(binding)
    gates = config['gates']
    require(isinstance(gates, dict) and gates, 'compiler acceptance gates missing')
    passed = False
    for gate in gates.values():
        receipt = load(bound(gate))
        require(isinstance(gate.get('assertions'), dict) and gate['assertions'], 'gate assertions missing')
        for selector, expected in gate['assertions'].items():
            actual = base.pointer(receipt, selector)
            require(type(actual) is type(expected) and actual == expected, f'gate failed: {selector}')
            passed = passed or expected is True
    require(passed, 'acceptance pass assertion missing')
    formatter = config['typescript_formatter']
    require(formatter.get('env') == 'SOM_TS_PRETTIER', 'invalid typescript_formatter')
    tree = formatter_tree_sha(Path(formatter['path']))
    require(tree == formatter.get('tree_sha256') == runtime.get('prettier_tree_sha256') == build.get('prettier_tree_sha256'),
            'candidate formatter tree differs')
    for document in (previous, previous_build):
        require(document.get('prettier_tree_sha256', tree) == tree, 'previous formatter tree differs')
    return runtime, previous


def assert_current(config, current, candidate=False):
    if not compiler_profile(config):
        return
    role = 'candidate' if candidate else 'previous'
    expected = profile_runtime(config, candidate)
    require(digest(current['command'][0]) == current['identity']['binary_sha256'] == config[role]['binary']['sha256'],
            f'current identity is not the pinned {role} compiler')
    require(exact_json(current['health'], expected['health']), f'current health is not the pinned {role} compiler')
    assets_match(Path(current['command'][3]), expected['health'])


def apply_formatter_env(config):
    """Set SOM_TS_PRETTIER in os.environ when typescript_formatter is present."""
    formatter = config.get('typescript_formatter')
    if formatter is None:
        return
    require(isinstance(formatter, dict) and formatter.get('env') == 'SOM_TS_PRETTIER',
            'invalid typescript_formatter')
    path = formatter.get('path')
    require(isinstance(path, str) and bool(path), 'invalid typescript_formatter path')
    current = os.environ.get('SOM_TS_PRETTIER')
    require(current in (None, path), f'SOM_TS_PRETTIER={current} conflicts with pinned {path}')
    os.environ['SOM_TS_PRETTIER'] = path


def previous_python(config):
    """The Python canaries of the previous default. A candidate with its own assets (#57)
    answers compile with other asset descriptors, so the previous default has its own set."""
    return config.get('previous_python_canaries', config['python_canaries'])


def candidate_assets(config, current):
    """The asset directory the candidate serves: its own (#57) or the current default's."""
    if 'assets' in config['candidate']:
        return Path(config['candidate']['assets']['path'])
    return Path(current['command'][3])


def install_assets(assets, root, health):
    """Copy an asset directory to ROOT/som-rs/compiler-assets/versions/<manifest sha> once."""
    destination = root / 'som-rs/compiler-assets/versions' / health['asset_manifest_sha256']
    require(not destination.is_symlink(), 'asset destination must not be a symlink')
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = destination.with_name(destination.name + f'.stage-{os.getpid()}')
        stage.mkdir()
        for name in ['manifest.json', *load(assets / 'manifest.json')['files']]:
            shutil.copy2(assets / name, stage / name)
        os.rename(stage, destination)
    assets_match(destination, health)
    return destination


def assets_match(assets, health):
    """An installed asset directory equals the manifest the health reports."""
    manifest = load(assets / 'manifest.json')
    require(sha_bytes(canon(manifest).encode()) == health['asset_manifest_sha256'], 'asset manifest differs')
    require(set(p.name for p in assets.iterdir()) == {'manifest.json', *manifest['files']}, 'unexpected asset files')
    for name, expected in manifest['files'].items():
        require(not (assets / name).is_symlink() and digest(assets / name) == expected, f'asset changed: {name}')


def receipt_sha(config):
    return sha_bytes(canon(config).encode())
