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
import shutil
import subprocess
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[3]
BASE_PATH = REPO / 'som-rs/scripts/compiler_default_switch.py'
BASE_SHA256 = '289a8470c5de2a183b01c8a138ecb4bc6cfe80a624f0291b32b779cc854bfb12'
PREREQUISITES_VERSION = 'som.rust-output-switch-prerequisites.v1'
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
