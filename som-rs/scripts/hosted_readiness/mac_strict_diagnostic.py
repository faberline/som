"""GitHub macOS preparation diagnostic. No formal acceptance authority."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback

CANDIDATE = '9763e9b86f36a99229b70d8c571734e9a740e81c'
INPUTS = '1de8f05430a72f8d76f6159786cca71b0ae1a911'
INPUT_INDEX = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
BEFORE_BINARY = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
BEFORE_SOURCE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
BASE = 'som-rs/scripts/react_output_acceptance/'
INVENTORY = 'som-rs/scripts/hosted_readiness/mac_strict_diagnostic_sources.json'
RUST = Path('/opt/homebrew/Cellar/rust/1.96.1/bin')
NODE = Path('/opt/homebrew/Cellar/node/26.5.0/bin/node')
PORTS = {'before': 19061, 'candidate': 19063}
MAX = 128 * 1024**2

def need(value, message):
    if not value:
        raise ValueError(message)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            value.update(chunk)
    return value.hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')

def regular(path):
    path = Path(path).absolute()
    need(not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'single-link regular file required')
    return path

def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'GIT_CONFIG_GLOBAL': '/dev/null',
             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'}, stderr=subprocess.PIPE, timeout=120)

def sources(control, repo):
    need(git(repo, 'rev-parse', 'HEAD').decode().strip() == CANDIDATE, 'fixed candidate source differs')
    manifest = json.loads(regular(control / INVENTORY).read_bytes())
    need(manifest['candidate_revision'] == CANDIDATE, 'source inventory candidate differs')
    rows = {}
    for row in git(repo, 'ls-tree', '-r', '-z', '--full-tree', CANDIDATE).split(b'\0'):
        if row:
            meta, name = row.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            need(kind == 'blob' and mode in ('100644', '100755', '120000'), 'complete tracked source types differ')
            rows[name.decode()] = {'mode': mode, 'git_blob': oid}
    need(rows == manifest['files'], 'complete fixed candidate Git inventory differs')
    for name, pin in rows.items():
        if pin['mode'] == '120000':
            path = repo / name
            need(path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'fixed tracked data alias differs')
            target = os.readlink(path)
            raw = os.fsencode(target)
            need(not os.path.isabs(target) and (path.parent / target).resolve(strict=False).is_relative_to(repo)
                 and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == pin['git_blob'],
                 'tracked internal data alias changed')
            continue
        path = regular(repo / name)
        raw = path.read_bytes()
        need(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == pin['git_blob']
             and bool(path.stat().st_mode & 0o111) == (pin['mode'] == '100755'), 'candidate working source differs: ' + name)
    need(not git(repo, 'status', '--porcelain', '--untracked-files=all', '--ignored').strip(), 'candidate worktree has unlisted source')
    return {'candidate_revision': CANDIDATE, 'controller_revision': os.environ['GITHUB_SHA'],
            'inventory_sha256': file_sha(control / INVENTORY), 'tracked_files': len(rows),
            'native_source_authority_sealed': False, 'product_acceptance_passed': False}

def clean_env(home, *, offline=True):
    names = ('SDKROOT', 'DEVELOPER_DIR', 'TMPDIR', 'CI', 'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT',
             'RUNNER_OS', 'RUNNER_ARCH', 'RUNNER_TEMP', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')
    env = {k: os.environ[k] for k in names if k in os.environ}
    env.update(PATH=str(RUST) + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', HOME=str(home),
               LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8', PYTHONDONTWRITEBYTECODE='1',
               CARGO_NET_OFFLINE='true' if offline else 'false')
    return env

def group_gone(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True

class Owned:
    def __init__(self, argv, cwd, env, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True)
        self.process = None
        self.record = {'argv': list(map(str, argv)), 'cwd': str(cwd), 'pid': None,
                       'wait_attempts': [], 'signals': [], 'secondary_errors': [],
                       'product_acceptance_passed': False}
        self.stdout = (self.directory / 'stdout.raw').open('xb')
        self.stderr = (self.directory / 'stderr.raw').open('xb')
        try:
            self.process = subprocess.Popen(self.record['argv'], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=self.stdout, stderr=self.stderr, shell=False, start_new_session=True)
            self.record['pid'] = self.process.pid
            self.record['parent_pid'] = os.getpid()
            try:
                self.record['observed_pgid'] = os.getpgid(self.process.pid)
                need(self.record['observed_pgid'] == self.process.pid, 'owned process session differs')
            except ProcessLookupError:
                need(self.process.poll() is not None, 'live owned process group could not be observed')
                self.record['pgid_observation'] = 'direct child exited before group observation'
            snap = subprocess.run(['/bin/ps', '-ww', '-p', str(self.process.pid), '-o',
                                   'pid=,ppid=,pgid=,lstart=,command='], capture_output=True, timeout=10)
            (self.directory / 'start-ps.stdout.raw').write_bytes(snap.stdout)
            (self.directory / 'start-ps.stderr.raw').write_bytes(snap.stderr)
            self.record['start_ps_exit_code'] = snap.returncode
        except BaseException as error:
            self.record['first_error'] = {'type': type(error).__name__, 'message': str(error)}
            self.close(preserve=error)
            raise

    def wait(self, timeout):
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.record['wait_attempts'].append({'completed': False, 'timeout_seconds': timeout})
            raise
        self.record['wait_attempts'].append({'completed': True, 'exit_code': code})
        return code

    def close(self, preserve=None):
        errors = []
        first_cleanup = None
        def remember(error, phase):
            nonlocal first_cleanup
            if first_cleanup is None:
                first_cleanup = error
            row = {'phase': phase, 'type': type(error).__name__, 'message': str(error)}
            errors.append(row)
            self.record.setdefault('first_error', row)
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    for sig, seconds in ((signal.SIGTERM, 15), (signal.SIGKILL, 15)):
                        if self.process.poll() is not None:
                            break
                        need(os.getpgid(self.process.pid) == self.process.pid,
                             'live direct child group ownership differs; refuse signal')
                        os.killpg(self.process.pid, sig)
                        self.record['signals'].append({'signal': sig.name, 'target': 'owned_live_child_group'})
                        try:
                            self.wait(seconds)
                        except subprocess.TimeoutExpired:
                            pass
            except BaseException as error:
                remember(error, 'signal_or_ownership')
            # A failed identity observation or signal must never skip the actual
            # Popen wait. This bounded wait neither kills nor trusts an unknown PID.
            self.record['wait_recorded'] = False
            try:
                code = self.wait(5)
                self.record['exit_code'] = code
                self.record['wait_recorded'] = True
            except BaseException as error:
                remember(error, 'final_wait')
            try:
                self.record['owned_group_gone'] = group_gone(self.process.pid)
                need(self.record['owned_group_gone'], 'owned process group remains; no unknown PID was signalled')
            except BaseException as error:
                remember(error, 'group_postcheck')
        for stream in (self.stdout, self.stderr):
            try:
                stream.close()
            except BaseException as error:
                remember(error, 'stream_close')
        for name in ('stdout', 'stderr'):
            try:
                path = self.directory / (name + '.raw')
                self.record[name] = {'bytes': path.stat().st_size, 'sha256': file_sha(path)}
            except BaseException as error:
                remember(error, 'stream_metadata')
        self.record['secondary_errors'].extend(errors)
        try:
            save(self.directory / 'command.json', self.record)
        except BaseException as error:
            remember(error, 'command_retention')
        if preserve is None and errors:
            first_cleanup.__som_mac_diagnostic_secondary__ = errors
            raise first_cleanup
        if preserve is not None and errors:
            preserve.__som_mac_diagnostic_secondary__ = errors

def command(argv, cwd, env, out, timeout=1800):
    owner = Owned(argv, cwd, env, out)
    primary = None
    try:
        code = owner.wait(timeout)
        need(code == 0, 'command exited with ' + str(code))
        return owner.record
    except BaseException as error:
        primary = error
        owner.record.setdefault('first_error', {'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        owner.close(preserve=primary)

def provision(control, repo, out):
    need('M6_INPUTS_KEYRING' in os.environ and 'M6_INPUTS_KEY' not in os.environ,
         'approved V2 keyring must exist only in provision step')
    temp = Path(os.environ['RUNNER_TEMP'])
    home = temp / 'som-m12-mac-diagnostic-home'
    home.mkdir(exist_ok=True)
    env = clean_env(home, offline=False)
    manifest = json.loads((repo / (BASE + 'macos_bottles.json')).read_bytes())
    commits = {manifest[k]['commit'] for k in ('frozen_file_supplement', 'npm_tree_supplement')}
    for i, revision in enumerate(sorted(commits | {INPUTS})):
        need(re.fullmatch('[0-9a-f]{40}', revision), 'fixed provisioning commit required')
        command(['/usr/bin/git', '-C', repo, 'fetch', '--no-tags', '--depth=1', 'origin', revision], repo, env,
                out / ('fetch-' + str(i)))
    sources(control, repo)
    command([sys.executable, '-I', '-S', '-B', repo / (BASE + 'provision_macos.py'),
             '--out', out / 'tool-provision'], repo, env, out / 'tool-provision-command', 7200)
    sources(control, repo)
    config = {'version': 'som.m6-hosted-inputs.v1', 'commit': INPUTS, 'index_sha256': INPUT_INDEX}
    save(out / 'input-config.json', config)
    index = git(repo, 'cat-file', 'blob', INPUTS + ':inputs-index.json')
    need(digest(index) == INPUT_INDEX and len(json.loads(index)['packages']) == 18, 'strict original18 input identity differs')
    write = out / 'input-index.raw'
    write.write_bytes(index)
    input_env = dict(env, M6_INPUTS_KEYRING=os.environ['M6_INPUTS_KEYRING'])
    command([sys.executable, '-I', '-S', '-B', repo / (BASE + 'fetch_hosted_inputs.py'),
             '--repo-root', repo, '--config', out / 'input-config.json',
             '--out', temp / 'som-m12-mac-approved18', '--node', NODE], repo, input_env,
             out / 'input-provision-command', 7200)
    sources(control, repo)

def http_capture(owner, port, route, raw, out):
    need(owner.process.poll() is None, 'actual owned compiler exited')
    out.mkdir(parents=True)
    (out / 'request.raw').write_bytes(raw)
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=120)
    try:
        connection.request('POST' if raw else 'GET', route, body=raw if raw else None,
                           headers={'Content-Type': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        record = {'method': 'POST' if raw else 'GET', 'route': route, 'status': response.status,
                  'headers': response.getheaders(), 'owned_pid': owner.process.pid, 'port': port, 'read_complete': False}
        primary = None
        body = bytearray()
        try:
            with (out / 'response.raw').open('xb') as stream:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    body.extend(chunk)
                    need(len(body) <= MAX, 'response exceeds frozen diagnostic bound')
            record['read_complete'] = True
        except BaseException as error:
            primary = error
            record['first_error'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            record.update(bytes=len(body), sha256=digest(body))
            try:
                save(out / 'transport.json', record)
            except BaseException as error:
                if primary is None:
                    raise
                primary.__som_mac_http_retention_failure__ = type(error).__name__
        need(owner.process.poll() is None, 'compiler exited during response')
        return response.status, bytes(body)
    finally:
        connection.close()

def wait_health(owner, port, out, expected=None):
    until = time.monotonic() + 60
    attempts = []
    while True:
        need(owner.process.poll() is None, 'compiler startup exited')
        try:
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                conn.request('GET', '/healthz')
                response = conn.getresponse()
                body = response.read(MAX + 1)
                need(len(body) <= MAX, 'health response exceeds bound')
                (out / ('health-attempt-' + str(len(attempts)) + '.raw')).write_bytes(body)
                attempts.append({'status': response.status, 'sha256': digest(body)})
                if response.status == 200:
                    health = json.loads(body)
                    need(health.get('model_loaded') is False, 'model-loaded compiler refused')
                    if expected is not None:
                        need(health == expected, 'genuine before health differs from approved runtime')
                    save(out / 'health.json', health)
                    break
            finally:
                conn.close()
        except OSError as error:
            attempts.append({'error': type(error).__name__})
        need(time.monotonic() < until, 'health wait timed out')
        time.sleep(.2)
    save(out / 'health-attempts.json', attempts)
    observed = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(owner.process.pid),
                              '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fpnt'], capture_output=True, timeout=10)
    (out / 'listener.stdout.raw').write_bytes(observed.stdout)
    (out / 'listener.stderr.raw').write_bytes(observed.stderr)
    need(observed.returncode == 0 and ('p' + str(owner.process.pid)).encode() in observed.stdout.splitlines()
         and ('n127.0.0.1:' + str(port)).encode() in observed.stdout.splitlines(), 'owned loopback listener proof differs')

def asset_snapshot(root):
    expected = {'blocks.json', 'legacy-blocks.json', 'quality-blocks.json', 'quality.json',
                'ruff', 'pyproject.toml', 'manifest.json'}
    need({p.name for p in root.iterdir()} == expected, 'complete compiler asset universe differs')
    return {p.name: {'sha256': file_sha(regular(p)), 'bytes': p.stat().st_size,
                     'mode': stat.S_IMODE(p.stat().st_mode)} for p in sorted(root.iterdir())}

def compare(control, repo, out):
    need('M6_INPUTS_KEYRING' not in os.environ and 'M6_INPUTS_KEY' not in os.environ, 'build/compare must receive no input key')
    temp = Path(os.environ['RUNNER_TEMP'])
    home = temp / 'som-m12-mac-diagnostic-home'
    env = clean_env(home)
    approved = temp / 'som-m12-mac-approved18'
    before = approved / '.local/m6-hosted/baseline'
    need(file_sha(regular(before / 'som')) == BEFORE_BINARY, 'genuine2bce binary differs')
    source = json.loads(regular(before / 'source.json').read_bytes())
    need(source['commit'] == BEFORE_SOURCE, 'genuine2bce source differs')
    rust = json.loads((repo / 'som-rs/contracts/rust-output-v1/toolchain.json').read_bytes())['rust']
    tools = {}
    for name in ('cargo', 'rustc', 'rustfmt'):
        path = regular(RUST / name)
        need(file_sha(path) == rust[name]['sha256'], 'frozen Mac Rust tool differs: ' + name)
        tools[name] = {'path': str(path), 'sha256': file_sha(path)}
    env.update(RUSTC=str(RUST / 'rustc'), CARGO=str(RUST / 'cargo'),
               CARGO_HOME=str(temp / 'som-m12-mac-diagnostic-cargo'),
               CARGO_TARGET_DIR=str(temp / 'som-m12-mac-diagnostic-target'),
               SOM_TS_PRETTIER=str(approved / '.local/m6-hosted/prettier'))
    for name in ('CARGO_HOME', 'CARGO_TARGET_DIR'):
        Path(env[name]).mkdir(exist_ok=False)
    for i, manifest in enumerate(('som-rs/Cargo.toml', 'som-rs/som-targets/Cargo.toml')):
        command([RUST / 'cargo', 'fetch', '--locked', '--manifest-path', repo / manifest], repo,
                dict(env, CARGO_NET_OFFLINE='false'), out / ('cargo-fetch-' + str(i)), 3600)
    sources(control, repo)
    command([RUST / 'cargo', 'build', '--release', '--locked', '--offline', '--manifest-path',
             repo / 'som-rs/som-targets/Cargo.toml', '--bin', 'som'], repo, env, out / 'build-candidate', 7200)
    # Use a distinct target directory so the two different binaries named som cannot replace one another.
    built = Path(env['CARGO_TARGET_DIR']) / 'release/som'
    binaries = out / 'binaries'
    binaries.mkdir()
    shutil.copy2(regular(built), binaries / 'candidate')
    # Keep the already approved before executable in runner input scratch.
    # Only the newly built candidate binary belongs in this diagnostic upload.
    binary_paths = {'before': regular(before / 'som'), 'candidate': binaries / 'candidate'}
    command([RUST / 'cargo', 'build', '--release', '--locked', '--offline', '--manifest-path',
             repo / 'som-rs/Cargo.toml', '--bin', 'som-compiler-assets'], repo,
             dict(env, CARGO_TARGET_DIR=str(temp / 'som-m12-mac-diagnostic-assets-target')), out / 'build-assets', 7200)
    sources(control, repo)
    formatter = regular(before / 'assets/ruff')
    command([temp / 'som-m12-mac-diagnostic-assets-target/release/som-compiler-assets', '--destination',
             out / 'candidate-assets', '--formatter', formatter], repo, env, out / 'package-assets', 1800)
    save(out / 'tool-identities.json', tools)
    binary_pins = {role: file_sha(binary_paths[role]) for role in PORTS}
    assets_before = {'before': asset_snapshot(before / 'assets'), 'candidate': asset_snapshot(out / 'candidate-assets')}
    save(out / 'assets-before.json', assets_before)
    save(out / 'binary-identities.json', {'before': {'path': str(binary_paths['before']),
         'sha256': binary_pins['before'], 'source': BEFORE_SOURCE,
         'approved18_commit': INPUTS, 'approved18_index_sha256': INPUT_INDEX,
         'original_source_file_sha256': file_sha(before / 'source.json')},
         'candidate': {'path': str(binary_paths['candidate']), 'sha256': binary_pins['candidate'], 'source': CANDIDATE}})
    sources(control, repo)
    owners = {}
    primary = None
    report = {'version': 'som.mac-strict-preparation-diagnostic.v1', 'rows': [],
              'scope': 'one publicv1 prompt, omitted target and explicit python, live compile plus own-bundle assemble',
              'source_authority_sealed': False, 'product_acceptance_passed': False,
              'formal_final_candidate_acceptance': False, 'full_twelve_stage_acceptance': 'UNRUN'}
    try:
        for role, port in PORTS.items():
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
            assets = before / 'assets' if role == 'before' else out / 'candidate-assets'
            owner = Owned([binary_paths[role], 'serve', '--assets', assets, '--port', str(port)], repo, env, out / ('service-' + role))
            owners[role] = owner
            expected = json.loads((before / 'runtime.json').read_bytes())['health'] if role == 'before' else None
            wait_health(owner, port, out / ('service-' + role), expected)
        prompt = json.loads((repo / 'som-rs/contracts/compiler-v1/examples/v1.prompt.json').read_bytes())
        for form in ('omitted', 'python'):
            payload = {'format': 'som.prompt-json.v1', 'prompt': prompt}
            if form == 'python':
                payload['target'] = 'python'
            raw = canonical(payload)
            replies = {}
            for role in PORTS:
                replies[role] = http_capture(owners[role], PORTS[role], '/v1/compile', raw, out / form / role / 'compile')
            for stage in ('compile', 'assemble'):
                if stage == 'assemble':
                    assembled = {}
                    for role, (status, body) in replies.items():
                        if status == 200:
                            bundle = json.loads(body)['bundle']
                            assembled[role] = http_capture(owners[role], PORTS[role], '/v1/assemble',
                                canonical({'bundle': bundle}), out / form / role / 'assemble')
                    current = assembled
                else:
                    current = replies
                complete = set(current) == set(PORTS)
                equal = complete and current['before'] == current['candidate'] and current['before'][0] == 200
                row = {'form': form, 'stage': stage, 'both_executed': complete, 'whole_response_bytes_equal': equal}
                if complete:
                    left, right = current['before'][1], current['candidate'][1]
                    row.update(before_status=current['before'][0], candidate_status=current['candidate'][0],
                               before_sha256=digest(left), candidate_sha256=digest(right),
                               first_difference_offset=next((i for i, (a, b) in enumerate(zip(left, right)) if a != b),
                                   min(len(left), len(right)) if left != right else None))
                report['rows'].append(row)
                if not equal:
                    report.setdefault('first_failure', row)
    except BaseException as error:
        primary = error
        report.setdefault('first_failure', {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()})
    finally:
        cleanup = []
        for role, owner in owners.items():
            try:
                owner.close()
                cleanup.append({'role': role, 'wait_recorded': owner.record.get('wait_recorded'),
                                'owned_group_gone': owner.record.get('owned_group_gone')})
            except BaseException as error:
                cleanup.append({'role': role, 'error': type(error).__name__, 'message': str(error)})
                report.setdefault('first_failure', {'stage': 'cleanup', 'role': role,
                                                   'type': type(error).__name__, 'message': str(error)})
        report['cleanup'] = cleanup
        report['diagnostic_equal'] = (primary is None and 'first_failure' not in report
            and len(report['rows']) == 4 and all(x['whole_response_bytes_equal'] for x in report['rows'])
            and len(cleanup) == 2 and all(x.get('wait_recorded') and x.get('owned_group_gone') for x in cleanup))
        try:
            report['source_after'] = sources(control, repo)
            report['binary_after'] = {role: file_sha(binary_paths[role]) for role in PORTS}
            need(report['binary_after'] == binary_pins and binary_pins['before'] == BEFORE_BINARY,
                 'actual compiler binary changed during diagnostic')
            report['assets_after'] = {'before': asset_snapshot(before / 'assets'),
                                      'candidate': asset_snapshot(out / 'candidate-assets')}
            need(report['assets_after'] == assets_before, 'compiler assets changed during diagnostic')
            need(all(file_sha(RUST / name) == pin['sha256'] for name, pin in tools.items()),
                 'frozen compiler tool changed during diagnostic')
        except BaseException as error:
            report['diagnostic_equal'] = False
            report.setdefault('first_failure', {'type': type(error).__name__, 'message': str(error)})
        try:
            save(out / 'strict-comparison.json', report)
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                primary.__som_mac_comparison_retention_failure__ = type(error).__name__
    if primary is not None:
        raise primary
    need(report['diagnostic_equal'], 'strict preparation comparison differs; no metadata rewrite or acceptance waiver')

def main():
    control = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    repo = control / 'candidate-source'
    phase = os.environ['DIAG_PHASE']
    need(phase in ('provision', 'compare'), 'fixed diagnostic phase required')
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Darwin' and platform.machine() == 'arm64'
         and platform.mac_ver()[0].split('.')[0] == '26', 'GitHub macOS26 arm64 only')
    identity = sources(control, repo)
    out = Path(os.environ['RUNNER_TEMP']).resolve(strict=True) / 'som-m12-mac-strict-diagnostic'
    need(not out.is_symlink() and (not out.exists() if phase == 'provision' else out.is_dir()), 'fixed fresh diagnostic root required')
    if phase == 'provision':
        out.mkdir(mode=0o700)
    save(out / (phase + '-source.json'), identity)
    try:
        (provision if phase == 'provision' else compare)(control, repo, out)
    except BaseException as error:
        try:
            save(out / (phase + '-first-failure.json'), {'type': type(error).__name__, 'message': str(error),
                 'traceback': traceback.format_exc(), 'product_acceptance_passed': False})
        except BaseException as secondary:
            error.__som_mac_phase_retention_failure__ = type(secondary).__name__
        raise

if __name__ == '__main__':
    main()
