#!/usr/bin/env python3
"""Prepared #44 switch. Default mode checks files and prints a plan only."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import struct
import sys
import time
import urllib.error
import urllib.request

ROLES = {'contract', 'compiler', 'runtime', 'legacy_544', 'renderer_behavior',
         'candidate_acceptance', 'switch_authorization'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def rollback_defer_signal(signum, frame):
    """Defer already-handled signals during rollback without ignoring them at exec."""
    return None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f'duplicate JSON key: {key}')
            value[key] = item
        return value
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def exact_json(left, right):
    """Compare JSON types and every finite float bit, including signed zero."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(exact_json(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return math.isfinite(left) and math.isfinite(right) and struct.pack('>d', left) == struct.pack('>d', right)
    return type(left) in (str, int, bool, type(None)) and left == right


def validate_argv(argv):
    # ps renders an argv display string. Restrict this task's known launch
    # arguments so whitespace cannot conceal a different argument boundary.
    require(isinstance(argv, list) and bool(argv), 'argv must be a nonempty array')
    require(all(isinstance(a, str) and a and not any(c.isspace() or ord(c) < 32 or ord(c) == 127
                for c in a) for a in argv), 'argv contains whitespace or control bytes')


def pointer(value, selector):
    require(selector.startswith('/'), 'assertion needs a JSON pointer')
    for item in selector[1:].split('/'):
        item = item.replace('~1', '/').replace('~0', '~')
        value = value[int(item)] if isinstance(value, list) else value[item]
    return value


def bound(binding):
    path = Path(binding['path'])
    require(path.is_absolute(), 'all bound paths must be absolute')
    require(digest(path) == binding['sha256'], f'bound input changed: {path}')
    return path


def link_state(path):
    if path.is_symlink():
        return {'kind': 'symlink', 'target': os.readlink(path)}
    require(not path.exists(), f'current pointer must be a symlink or absent: {path}')
    return {'kind': 'absent'}


def atomic_bytes(path, data):
    temp = path.with_name(path.name + f'.switch-{os.getpid()}')
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_link(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    if state['kind'] == 'absent':
        if path.is_symlink():
            path.unlink()
        else:
            require(not path.exists(), f'refuse removing non-link: {path}')
    else:
        temp = path.with_name(path.name + f'.switch-{os.getpid()}')
        os.symlink(state['target'], temp)
        os.replace(temp, path)


def command(args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


def identity(pid, argv, port):
    # Fail closed on macOS sandbox restrictions; do not infer ownership from a PID.
    validate_argv(argv)
    require(command(['ps', '-p', str(pid), '-o', 'uid=']) == str(os.getuid()), 'wrong process UID')
    actual = command(['ps', '-ww', '-p', str(pid), '-o', 'command='])
    require(actual == ' '.join(argv), f'process argv mismatch for {pid}')
    start = command(['ps', '-p', str(pid), '-o', 'lstart='])
    listeners = command(['lsof', '-nP', '-a', '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fpn'])
    require([line for line in listeners.splitlines() if line.startswith('p')] == ['p' + str(pid)],
            'port ownership is absent, shared, or different')
    require('n127.0.0.1:' + str(port) in listeners.splitlines(), 'listener is not IPv4 loopback')
    texts = command(['lsof', '-nP', '-a', '-p', str(pid), '-d', 'txt', '-Fn'])
    require('n' + str(Path(argv[0]).resolve()) in texts.splitlines(), 'loaded executable path mismatch')
    cwd = command(['lsof', '-nP', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'])
    directories = [line[1:] for line in cwd.splitlines() if line.startswith('n')]
    require(len(directories) == 1, 'process working directory is unavailable')
    return {'pid': pid, 'uid': os.getuid(), 'argv': argv, 'start': start, 'port': port,
            'binary_sha256': digest(argv[0]), 'cwd': directories[0]}


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def terminate(owner):
    require(identity(owner['pid'], owner['argv'], owner['port']) == owner, 'process identity changed')
    os.kill(owner['pid'], signal.SIGTERM)
    end = time.monotonic() + 20
    while alive(owner['pid']) and time.monotonic() < end:
        time.sleep(.1)
    require(not alive(owner['pid']), 'owned process did not stop; no SIGKILL fallback')


def finish_legacy_stop(owner):
    # Stop intent has already been admitted. Never reuse a process which may
    # still exit from an earlier SIGTERM. Signals still require full identity.
    if not alive(owner['pid']):
        return
    end = time.monotonic() + 20
    try:
        terminate(owner)
    except (subprocess.CalledProcessError, OSError, RuntimeError) as stop_failure:
        # A shutting-down owned process can lose its listening socket before
        # its PID exits. Wait without signaling any unverified process.
        while alive(owner['pid']) and time.monotonic() < end:
            time.sleep(.1)
        require(not alive(owner['pid']),
                f'legacy stop did not complete; no unverified signal or SIGKILL: {stop_failure}')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def port_free(port):
    # A global listener guard also covers BSD wildcard/different-address reuse.
    query = ['lsof', '-nP', '-a', '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fpn']
    absent = subprocess.run(query, capture_output=True, text=True, check=False, timeout=10)
    require(absent.returncode == 1 and not absent.stdout.strip() and not absent.stderr.strip(),
            'port has a listener or listener absence cannot be verified')
    # Match Unix listener reuse after closed connections, without REUSEPORT.
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(1)
        proof = subprocess.run(query, capture_output=True, text=True, check=False, timeout=10)
        lines = proof.stdout.splitlines()
        require(proof.returncode == 0 and not proof.stderr.strip()
                and [line for line in lines if line.startswith('p')] == ['p' + str(os.getpid())]
                and [line for line in lines if line.startswith('n')] == ['n127.0.0.1:' + str(port)],
                'probe is not the sole IPv4 loopback listener')


class Run:
    def __init__(self, output, cwd):
        self.output = output
        self.cwd = cwd
        self.index = 0
        self.children = []
        self.owners = {}
        self.events = []
        self.recovering = False
        self.evidence_errors = []

    def evidence_failure(self, operation, error):
        self.evidence_errors.append({'operation': operation, 'error': repr(error)})
        try:
            print(f'rollback evidence unavailable ({operation}): {error}', file=sys.stderr)
        except OSError:
            pass

    def save(self, name, data):
        self.index += 1
        path = self.output / f'{self.index:04d}-{name}'
        try:
            path.write_bytes(data)
        except OSError as error:
            if not self.recovering:
                raise
            self.evidence_failure(str(path), error)
        return path

    def event(self, stage, **fields):
        self.events.append({'stage': stage, 'at': time.time(), **fields})
        try:
            atomic_bytes(self.output / 'journal.json', (json.dumps(self.events, indent=2) + '\n').encode())
        except OSError as error:
            if not self.recovering:
                raise
            self.evidence_failure('journal:' + stage, error)

    def http(self, port, path, value=None):
        raw = None if value is None else canon(value).encode()
        if raw is not None:
            self.save('request.json', raw)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=raw,
                                         headers={'Content-Type': 'application/json'},
                                         method='GET' if raw is None else 'POST')
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open(request, timeout=120) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        self.save('response.raw', body)
        require(status == 200, f'HTTP {status}: {path}')
        value = json.loads(body)
        require(isinstance(value, dict) and value.get('status') == 'ok', f'non-ok reply: {path}')
        return value

    def cli(self, binary, operation, source, port):
        argv = [str(binary), operation, str(source), '--url', f'http://127.0.0.1:{port}']
        result = subprocess.run(argv, capture_output=True, timeout=120)
        self.save('cli-argv.json', canon(argv).encode())
        self.save('cli-stdout.raw', result.stdout)
        self.save('cli-stderr.raw', result.stderr)
        require(result.returncode == 0, f'CLI failed: {operation}: {result.returncode}')
        return json.loads(result.stdout)

    def health(self, port, expected):
        actual = self.http(port, '/healthz')
        require(exact_json(actual, expected), f'full health mismatch on {port}')
        return actual

    def start(self, argv, expected):
        port = int(argv[-1])
        port_free(port)
        log = self.output / f'process-{len(self.children)}.log'
        child = None
        try:
            try:
                stream = log.open('xb')
            except OSError as error:
                if not self.recovering:
                    raise
                self.evidence_failure('process log:' + str(log), error)
                stream = None
            try:
                child = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                         stdout=stream if stream is not None else subprocess.DEVNULL,
                                         stderr=subprocess.STDOUT, close_fds=True,
                                         start_new_session=True, cwd=self.cwd)
                self.children.append(child)
            finally:
                if stream is not None:
                    stream.close()
            self.event('started', pid=child.pid, argv=argv, log=str(log),
                       log_available=stream is not None)
            end = time.monotonic() + 45
            while time.monotonic() < end:
                require(child.poll() is None, f'service exited: {argv}')
                try:
                    owner = identity(child.pid, argv, port)
                    self.owners[child.pid] = owner
                    self.health(port, expected)
                    return owner
                except (subprocess.CalledProcessError, OSError, urllib.error.URLError):
                    time.sleep(.2)
            raise RuntimeError('service startup timed out')
        except BaseException:
            # Owned child handle avoids signaling a receipt PID on startup failure.
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
            raise

    def stop_child(self, owner):
        child = next(p for p in self.children if p.pid == owner['pid'])
        require(identity(owner['pid'], owner['argv'], owner['port']) == owner, 'child identity changed')
        child.terminate()
        child.wait(timeout=20)
        self.event('stopped', pid=child.pid)

    def canary(self, port, old_binary, new_binary, cases, compiler):
        for case in cases:
            prompt_path = bound(case['prompt'])
            expected = load(bound(case['expected_response']))
            require(expected.get('status') == 'ok' and bool(expected.get('files')), 'invalid expected full reply')
            prompt = load(prompt_path)
            actual = self.http(port, '/v1/generate', prompt)
            require(actual == expected, 'generate differs from pinned full response')
            for binary in (old_binary, new_binary):
                require(self.cli(binary, 'generate', prompt_path, port) == expected, 'old/new CLI response differs')
            if compiler:
                source = bound(case['cnl']).read_text()
                compiled = self.http(port, '/v1/compile', {'format': 'som.cnl.v1', 'source': source})
                bundle = compiled['bundle']
                require(exact_json(bundle['prompt'], prompt), 'compiled prompt differs')
                typed = self.http(port, '/v1/compile', {'format': 'som.prompt-json.v1', 'prompt': prompt})
                require(exact_json(typed, compiled), 'CNL/JSON full compile response differs')
                require(exact_json(self.cli(new_binary, 'compile', bound(case['cnl']), port), bundle), 'CLI bundle differs')
                saved = self.save('bundle.json', canon(bundle).encode())
                require(self.http(port, '/v1/assemble', {'bundle': bundle}) == expected, 'saved bundle files differ')
                require(self.cli(new_binary, 'assemble', saved, port) == expected, 'CLI saved bundle files differ')
            self.event('full_canary_passed', name=case['name'], port=port, compiler=compiler)


def prepare(config, execute):
    require(config['version'] == 'som.default-switch-prerequisites.v1', 'wrong prerequisite version')
    for binding in config.get('reference_inputs', {}).values():
        bound(binding)
    repo = Path(config['repo']).resolve()
    default = bound(config['legacy_default'])
    require(default == repo / 'som-rs/runtime/default.json', 'wrong default path')
    legacy = load(default)
    runtime = load(bound(config['candidate_runtime']))
    require(runtime['binary_sha256'] == config['candidate_binary']['sha256'], 'candidate receipt binary differs')
    binary = bound(config['candidate_binary'])
    require(str(binary) == runtime['argv'][0], 'candidate receipt argv differs')
    validate_argv(runtime['argv'])
    require(type(runtime['port']) is int and runtime['argv'] == [str(binary), 'serve', '--assets',
            runtime['argv'][3], '--port', str(runtime['port'])], 'wrong candidate launch')
    assets = Path(runtime['argv'][3])
    manifest = load(bound(config['asset_manifest']))
    require(Path(config['asset_manifest']['path']) == assets / 'manifest.json', 'wrong manifest path')
    require(hashlib.sha256(canon(manifest).encode()).hexdigest() == runtime['health']['asset_manifest_sha256'],
            'canonical manifest identity differs')
    require(set(p.name for p in assets.iterdir()) == {'manifest.json', *manifest['files']}, 'unexpected assets')
    for name, expected in manifest['files'].items():
        require(Path(name).name == name, 'asset filename must be flat')
        require(not (assets / name).is_symlink() and digest(assets / name) == expected, f'asset changed: {name}')
    old = bound(config['legacy_binary'])
    validate_argv(legacy['command'])
    require(str(old) == legacy['command'][0], 'wrong old binary')
    require(legacy['command'][1:] == ['serve', '--model', str(repo / 'som-rs/models/current'),
            '--blocks', str(repo / 'som-code-python/data/blocks/current/blocks.json'), '--port', '8765'],
            'legacy launch is not the exact expected M2 command')
    require(Path(config['legacy_model_manifest']['path']) == repo / 'som-rs/models/current/manifest.json',
            'manifest must belong to the exact accepted model pointer')
    require(Path(config['legacy_blocks']['path']) == Path(legacy['command'][5]),
            'blocks must belong to the exact accepted block pointer')
    require(digest(bound(config['legacy_model_manifest'])) == legacy['health']['model_manifest_sha256'],
            'accepted model manifest differs')
    require(digest(bound(config['legacy_blocks'])) == legacy['health']['blocks_sha256'], 'accepted blocks differ')
    require(set(config['gates']) == ROLES, 'required gate roles missing or extra')
    if execute:
        for role, binding in config['gates'].items():
            receipt = load(bound(binding))
            require(bool(binding['assertions']) and any(v is True for v in binding['assertions'].values()), f'no passed assertion: {role}')
            for selector, expected in binding['assertions'].items():
                actual = pointer(receipt, selector)
                require(type(actual) is type(expected) and actual == expected, f'gate failed: {role}:{selector}')
        approval = load(bound(config['gates']['switch_authorization']))
        require(approval['action'] == 'switch-default-8765' and approval['approved'] is True, 'switch not approved')
        require(approval['candidate_binary_sha256'] == digest(binary), 'approval candidate differs')
        require(approval['script_sha256'] == digest(__file__), 'approval script differs')
        require(approval['prerequisites_sha256'] == config['_config_sha256'], 'approval prerequisites differ')
    require(len(config['canaries']) >= 2, 'need flow and legacy compatibility canaries')
    require({c['family'] for c in config['canaries']} >= {'flow', 'legacy'}, 'missing canary family')
    for case in config['canaries']:
        for name in ('prompt', 'cnl', 'expected_response'):
            bound(case[name])
    for name, relative in [('model', 'som-rs/models/current'), ('blocks', 'som-code-python/data/blocks/current'),
                           ('runtime', 'som-rs/runtime/current'), ('assets', 'som-rs/compiler-assets/current')]:
        require(link_state(repo / relative) == config['pointers'][name], f'pointer changed: {name}')
    return repo, legacy, runtime, binary, old, assets, manifest


def install(binary, assets, repo, manifest_sha):
    binary_dest = repo / 'som-rs/runtime/versions' / digest(binary) / 'som'
    asset_dest = repo / 'som-rs/compiler-assets/versions' / manifest_sha
    for source, destination, tree in [(binary, binary_dest, False), (assets, asset_dest, True)]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        require(not destination.is_symlink(), 'version destination must not be a symlink')
        if not destination.exists():
            temp = destination.with_name(destination.name + f'.stage-{os.getpid()}')
            shutil.copytree(source, temp) if tree else shutil.copy2(source, temp)
            os.rename(temp, destination)
        if tree:
            require(set(p.name for p in destination.iterdir()) == set(p.name for p in source.iterdir()), 'installed tree differs')
            for path in source.iterdir():
                require(digest(destination / path.name) == digest(path), 'installed asset bytes differ')
        else:
            require(digest(destination) == digest(source), 'installed binary differs')
    return binary_dest, asset_dest


def execute(config, prepared, output):
    repo, legacy, runtime, binary, old, assets, manifest = prepared
    output.mkdir(parents=True, exist_ok=False)
    run = Run(output, config['legacy_live_identity']['cwd'])
    run.save('prerequisites.json', canon(config).encode())
    snapshot = (repo / 'som-rs/runtime/default.json').read_bytes()
    run.save('legacy-default.json', snapshot)
    lock_path = repo / 'som-rs/runtime/.default-switch.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(config, True)
        owner = identity(legacy['pid'], legacy['command'], 8765)
        require(owner == config['legacy_live_identity'], 'live legacy identity differs from approval')
        run.health(8765, legacy['health'])
        changed = False
        new_owner = None
        committed_bytes = None
        try:
            clone_argv = legacy['command'][:-1] + [str(free_port())]
            clone = run.start(clone_argv, legacy['health'])
            try:
                run.canary(clone['port'], old, binary, config['canaries'], False)
            finally:
                run.stop_child(clone)
            candidate_argv = [str(binary), 'serve', '--assets', str(assets), '--port', str(free_port())]
            canary = run.start(candidate_argv, runtime['health'])
            try:
                run.canary(canary['port'], old, binary, config['canaries'], True)
            finally:
                run.stop_child(canary)
            installed, installed_assets = install(binary, assets, repo, runtime['health']['asset_manifest_sha256'])
            run.event('immutable_install_verified', binary=str(installed), assets=str(installed_assets))
            prepare(config, True)
            require(identity(owner['pid'], owner['argv'], 8765) == owner, 'legacy identity changed before stop')
            changed = True
            run.event('stop_legacy_intent', owner=owner)
            terminate(owner)
            argv = [str(installed), 'serve', '--assets', str(installed_assets), '--port', '8765']
            new_owner = run.start(argv, runtime['health'])
            run.event('new_default_owner', owner=new_owner)
            run.canary(8765, old, installed, config['canaries'], True)
            for name, rel, target in [('runtime', 'som-rs/runtime/current', installed.parent),
                                       ('assets', 'som-rs/compiler-assets/current', installed_assets)]:
                require(link_state(repo / rel) == config['pointers'][name], 'current pointer changed externally')
                atomic_link(repo / rel, {'kind': 'symlink', 'target': str(target)})
                run.event('pointer_set', name=name, target=str(target))
            for name, rel in [('model', 'som-rs/models/current'), ('blocks', 'som-code-python/data/blocks/current')]:
                require(link_state(repo / rel) == config['pointers'][name], 'accepted legacy pointer changed')
            # Check again after pointer commit; failures still restore M2.
            run.health(8765, runtime['health'])
            run.canary(8765, old, installed, config['canaries'], True)
            require(identity(new_owner['pid'], argv, 8765) == new_owner, 'new default identity changed')
            require(digest(repo / 'som-rs/runtime/default.json') == config['legacy_default']['sha256'],
                    'default receipt changed externally')
            receipt = {'version': 'som.default-runtime.v2', 'pid': new_owner['pid'], 'command': argv,
                       'health': runtime['health'], 'identity': new_owner, 'accepted': True,
                       'process_logs': str(output / 'process-*.log'),
                       'evidence': str(output), 'previous_default_sha256': hashlib.sha256(snapshot).hexdigest()}
            committed_bytes = (json.dumps(receipt, indent=2) + '\n').encode()
            atomic_bytes(repo / 'som-rs/runtime/default.json', committed_bytes)
            run.event('committed', receipt=receipt)
        except BaseException as failure:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(signum, rollback_defer_signal)
            # Evidence failure must not prevent restoring the accepted runtime.
            run.recovering = True
            run.event('failed', error=repr(failure), rollback_required=changed)
            # A handled signal can interrupt clone caller assignment or stop.
            # Stop every admitted non-default child even before stop intent.
            # Keep the original failure; report cleanup failures separately.
            for child_owner in list(run.owners.values()):
                if child_owner['pid'] == owner['pid'] or child_owner['port'] == 8765:
                    continue
                try:
                    if alive(child_owner['pid']):
                        run.stop_child(child_owner)
                except BaseException as cleanup_failure:
                    run.event('OWNED_CLONE_CLEANUP_FAILED',
                              owner=child_owner, error=repr(cleanup_failure))
            if changed:
                # Preserve originals. Do not restore a v4 canary or change model pointers.
                try:
                    # A signal can arrive after start returns but before
                    # new_owner receives its return value. Use admitted child
                    # identities as well as that local variable.
                    admitted = dict(run.owners)
                    if new_owner is not None:
                        admitted[new_owner['pid']] = new_owner
                    for child_owner in admitted.values():
                        if (child_owner['port'] == 8765
                                and child_owner['argv'][0] == str(installed)
                                and alive(child_owner['pid'])):
                            run.stop_child(child_owner)
                    for name, rel, target in [('runtime', 'som-rs/runtime/current', installed.parent),
                                              ('assets', 'som-rs/compiler-assets/current', installed_assets)]:
                        require(link_state(repo / rel) in [config['pointers'][name],
                                {'kind': 'symlink', 'target': str(target)}], 'current pointer changed externally')
                        atomic_link(repo / rel, config['pointers'][name])
                    for name, rel in [('model', 'som-rs/models/current'), ('blocks', 'som-code-python/data/blocks/current')]:
                        require(link_state(repo / rel) == config['pointers'][name], 'accepted pointer changed externally')
                    finish_legacy_stop(owner)
                    restored = run.start(legacy['command'], legacy['health'])
                    run.health(8765, legacy['health'])
                    run.canary(8765, old, binary, config['canaries'], False)
                    current = (repo / 'som-rs/runtime/default.json').read_bytes()
                    require(current == snapshot or current == committed_bytes, 'default receipt changed externally')
                    rollback = {**legacy, 'pid': restored['pid'], 'identity': restored,
                                'rollback_evidence': str(output), 'rollback_from': new_owner,
                                'rollback_evidence_errors': list(run.evidence_errors),
                                'rollback_evidence_complete': not run.evidence_errors}
                    if restored['pid'] != owner['pid']:
                        rollback.pop('stdout', None)
                        rollback.pop('stderr', None)
                        rollback['process_logs'] = str(output / 'process-*.log')
                    atomic_bytes(repo / 'som-rs/runtime/default.json', (json.dumps(rollback, indent=2) + '\n').encode())
                    run.event('rollback_verified' if not run.evidence_errors
                              else 'rollback_restored_with_evidence_errors', owner=restored,
                              evidence_errors=list(run.evidence_errors))
                except BaseException as rollback_failure:
                    run.event('ROLLBACK_FAILED_REQUIRES_OPERATOR', error=repr(rollback_failure))
                    raise RuntimeError(f'rollback failed: {rollback_failure}; original failure: {failure}') from rollback_failure
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    config = load(args.prerequisites)
    # Bind everything except the approval receipt to avoid a circular receipt hash.
    unsigned = dict(config)
    unsigned['gates'] = {k: v for k, v in config['gates'].items() if k != 'switch_authorization'}
    config['_config_sha256'] = hashlib.sha256(canon(unsigned).encode()).hexdigest()
    prepared = prepare(config, args.execute)
    if not args.execute:
        print(json.dumps({'mode': 'dry-run', 'production_mutations': 0, 'service_calls': 0,
                          'process_inspection': 0, 'prerequisites_sha256': config['_config_sha256'],
                          'gates_checked': False, 'live_owner_checked': False,
                          'plan': ['verify gate/approval hashes and exact old owner',
                                   'fresh exact M2 clone: full old/new CLI+HTTP canaries',
                                   'fresh compiler clone: CNL/JSON, saved bundle, full files',
                                   'install hash-versioned binary/assets; stop exact old owner',
                                   'start explicit compiler version on 8765; full checks',
                                   'replace both current pointers; full checks; atomic default receipt',
                                   'on any failure after stop intent: exact M2 restore and full checks']}, indent=2))
        return
    require(args.output is not None and args.output.is_absolute(), '--execute needs a fresh absolute output path')
    def interrupted(signum, frame):
        raise RuntimeError(f'interrupted by signal {signum}')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    execute(config, prepared, args.output)


if __name__ == '__main__':
    main()
