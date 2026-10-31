"""Record the real current build, then restore its candidate layout without a build.

Producer commands are GitHub Linux QA only. All imports are from the standard
library until raw Git source and numbered native source authority pass.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time
from types import ModuleType

SELF = 'som-rs/scripts/hosted_readiness/product_runtime/current_candidate_layout.py'
AUTHORITY = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
PREPARE = 'som-rs/scripts/rust_output_acceptance/prepare_candidate.py'
RUST_PROFILE = 'som-rs/contracts/native-tools-v1/product-rust-tools-v1.json'
NATIVE_PROFILE = 'som-rs/contracts/native-tools-v1/artifacts.json'
SOURCE_ROOTS = ['som-rs/Cargo.toml', 'som-rs/Cargo.lock', 'som-rs/src', 'som-rs/contracts',
                'som-rs/som-targets/Cargo.toml', 'som-rs/som-targets/Cargo.lock',
                'som-rs/som-targets/src', 'som-rs/som-targets/catalog']
PROVENANCE_NAME = 'som-current-build-provenance'
VERSION = 'som.current-candidate-build-provenance.v1'
BEGIN_VERSION = 'som.current-candidate-build-begin.v1'
COMMAND_VERSION = 'som.current-candidate-build-command.v1'
LAYOUT_VERSION = 'som.current-candidate-restored-layout.v1'
F59 = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
TOOL_ROLES = {'SOM_RUFF': 'ruff', 'SOM_RUSTFMT': 'rustfmt', 'SOM_TS_NODE': 'node', 'SOM_TS_PRETTIER': 'prettier'}
RESTORED_TOOLS = {'SOM_RUFF': 'native/bin/ruff', 'SOM_RUSTFMT': 'compiler-rustfmt',
                  'SOM_TS_NODE': 'native/bin/node', 'SOM_TS_PRETTIER': 'native/prettier'}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def relative(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(part not in {'', '.', '..'} for part in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'unsafe relative member')
    return name


def path_clean(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked data path')
    return path


def read_file(path, maximum=512 * 1024**2, *, cargo_output=False):
    path = path_clean(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and (cargo_output or before.st_nlink == 1)
                and before.st_nlink > 0 and before.st_size <= maximum, 'bounded regular input required')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    stamp = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns, item.st_mode)
    require(stamp(before) == stamp(after) and len(raw) == before.st_size, 'input changed during raw read')
    return raw, {'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(before.st_mode),
                 'device': before.st_dev, 'inode': before.st_ino}


def read(path, maximum=512 * 1024**2):
    return read_file(path, maximum)[0]


def portable(value):
    if type(value) is dict and 'sha256' in value and 'size' in value:
        return {key: value[key] for key in ('sha256', 'size', 'mode')}
    return {key: portable(item) for key, item in value.items()}


def tree(root):
    root = path_clean(root)
    require(root.is_dir(), 'regular tree required')
    result = {}
    for path in sorted(root.rglob('*')):
        path_clean(path)
        require(path.is_dir() or path.is_file(), 'special tree member')
        if path.is_file():
            result[relative(path.relative_to(root).as_posix())] = read_file(path)[1]
    require(result, 'empty tree')
    return result


def dump(path, value):
    path = path_clean(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def write(path, raw, mode=0o644):
    path = path_clean(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
    path.chmod(mode)


def github():
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub execution only')
    values = {key: os.environ[key] for key in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SHA', 'GITHUB_JOB')}
    require(re.fullmatch('[^/\\s]+/[^/\\s]+', values['GITHUB_REPOSITORY'])
            and all(values[key].isdecimal() and int(values[key]) > 0 for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
            and re.fullmatch('[0-9a-f]{40}', values['GITHUB_SHA']), 'exact GitHub run identity required')
    return values


def pair(helper, candidate):
    require(type(helper) is str and re.fullmatch('[0-9a-f]{40}', helper)
            and helper == candidate and candidate != F59, 'new full same-candidate source pair required')


class Sources:
    def __init__(self, root, revision, source_reader=None):
        self.root, self.revision, self.reader, self.pins = path_clean(root), revision, source_reader, {}
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision, 'source HEAD differs')
        self.entries = {}
        for record in self.git('ls-tree', '-r', '-z', revision).split(b'\0'):
            if not record:
                continue
            fields, tab, raw_name = record.partition(b'\t')
            mode, kind, oid = fields.split()
            name = relative(raw_name.decode('utf-8', 'strict'))
            require(tab and kind == b'blob' and mode in {b'100644', b'100755', b'120000'}
                    and name not in self.entries, 'unsupported or duplicate tracked source')
            self.entries[name] = {'git_mode': mode.decode(), 'oid': oid.decode()}
        self.paths = set(self.entries)
        require(self.paths, 'empty tracked source tree')

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

    def blob(self, name):
        name = relative(name)
        require(name in self.entries, 'untracked source read')
        raw = self.git('cat-file', 'blob', self.entries[name]['oid'])
        require(len(raw) <= 32 * 1024**2, 'tracked source exceeds bound')
        return raw

    def read(self, name):
        require(self.entries[relative(name)]['git_mode'] in {'100644', '100755'}, 'regular Git source required')
        raw = self.blob(name)
        actual, info = read_file(self.root / name, 32 * 1024**2)
        require(actual == raw and bool(info['mode'] & 0o111) == (self.entries[name]['git_mode'] == '100755'),
                'working source bytes or executable mode differ from Git')
        if self.reader is not None:
            require(self.reader(name) == raw, 'external checked source reader differs')
        self.pins[name] = sha(raw)
        return raw

    def status(self, paths):
        return self.git('status', '--porcelain', '--untracked-files=all', '--', *paths).decode('utf-8', 'strict')

    def snapshot(self):
        result = {}
        for name, entry in sorted(self.entries.items()):
            original = self.blob(name)
            if entry['git_mode'] == '120000':
                file = self.root / name
                require(not any(p.is_symlink() for p in file.parents) and file.is_symlink(), 'tracked symlink identity changed')
                working = os.readlink(file).encode('utf-8', 'strict')
            else:
                working, _ = read_file(self.root / name, 32 * 1024**2)
            result[name] = {'git_mode': entry['git_mode'], 'git_sha256': sha(original), 'working_sha256': sha(working)}
        return result

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'source HEAD changed')
        for name, pin in list(self.pins.items()):
            require(sha(self.read(name)) == pin, 'checked source changed')


def raw_module(sources, name, path):
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(sources.root / path), None, ''
    exec(compile(sources.read(path), module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


def admit(repo, helper, candidate, source_reader=None):
    pair(helper, candidate)
    sources = Sources(repo, helper, source_reader)
    require(sources.read(SELF) == read(Path(__file__)), 'running layout helper differs from exact Git source')
    module = raw_module(sources, '_som_layout_native_authority', AUTHORITY)
    authority = module.verify(sources.read, mode='native-qa-v1', source_paths=sources.paths,
                              helper_revision=helper, candidate_revision=candidate)
    require(authority.get('source_authority_active') is True
            and authority.get('helper_revision') == helper and authority.get('candidate_revision') == candidate
            and authority.get('product_acceptance_passed') is False, 'sealed source authority required')
    roots = [node.value for node in ast.parse(sources.read(PREPARE)).body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'SOURCE_ROOTS' for target in node.targets)]
    require(len(roots) == 1 and ast.literal_eval(roots[0]) == SOURCE_ROOTS, 'legacy source roots changed')
    return sources, authority


def producer_root():
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'producer is Linux x86_64 QA only')
    github()
    temporary = path_clean(os.environ['RUNNER_TEMP'])
    require(temporary.is_dir(), 'existing runner temporary root required')
    return temporary / PROVENANCE_NAME


def tool_identities(paths):
    return {name: tree(path) if name == 'SOM_TS_PRETTIER' else read_file(path)[1] for name, path in paths.items()}


def pin_tools(sources, cargo, rustc, tools):
    require(set(tools) == set(TOOL_ROLES), 'all four native tool roles required')
    profile, native = parse(sources.read(RUST_PROFILE)), parse(sources.read(NATIVE_PROFILE))
    require(profile['host'] == 'x86_64-unknown-linux-gnu', 'Linux producer Rust profile required')
    rust = {name: read_file(path_clean(path))[1] for name, path in {'cargo': cargo, 'rustc': rustc}.items()}
    for name, item in rust.items():
        require(item['sha256'] == profile['executables'][name]['sha256']
                and item['size'] == profile['executables'][name]['bytes'] and item['mode'] & 0o111,
                'actual Cargo/rustc differs from source pin')
    selected = tool_identities(tools)
    for role, name in TOOL_ROLES.items():
        host = 'platform-independent JavaScript' if name == 'prettier' else profile['host']
        matches = [row for row in native['tools'] if row['tool'] == name and row['host'] == host]
        require(len(matches) == 1, 'native source tool pin ambiguous')
        observed = tree_digest(selected[role]) if name == 'prettier' else selected[role]['sha256']
        require(observed == matches[0]['tree_sha256' if name == 'prettier' else 'executable_sha256'], 'actual native tool differs')
    return {'cargo': rust['cargo'], 'rustc': rust['rustc'], 'native': selected}


def tree_digest(values):
    return sha(''.join(name + '\0' + item['sha256'] + '\n' for name, item in sorted(values.items())).encode())


def public_environment(config):
    # Only these actual public build inputs reach Cargo. No token or arbitrary
    # inherited environment enters the command or its retained environment map.
    env = {'PATH': str(Path(config['cargo']).parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'CARGO': config['cargo'], 'RUSTC': config['rustc'], 'CARGO_TARGET_DIR': config['target_dir'],
           'CARGO_NET_OFFLINE': 'true', 'CARGO_TERM_COLOR': 'never', 'CARGO_BUILD_JOBS': '2', 'RUST_BACKTRACE': '1'}
    env.update(config['tools'])
    for key in ('HOME', 'CARGO_HOME', 'RUSTUP_HOME', 'RUSTUP_TOOLCHAIN', 'TMPDIR'):
        value = os.environ.get(key)
        if value is not None:
            require('\0' not in value and '\n' not in value and '\r' not in value, 'invalid public environment value')
            env[key] = value
    return env


def verify_public_environment(env, config):
    required = {'PATH': str(Path(config['cargo']).parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                'CARGO': config['cargo'], 'RUSTC': config['rustc'], 'CARGO_TARGET_DIR': config['target_dir'],
                'CARGO_NET_OFFLINE': 'true', 'CARGO_TERM_COLOR': 'never', 'CARGO_BUILD_JOBS': '2', 'RUST_BACKTRACE': '1',
                **config['tools']}
    require(type(env) is dict and set(required) <= set(env)
            and set(env) <= set(required) | {'HOME', 'CARGO_HOME', 'RUSTUP_HOME', 'RUSTUP_TOOLCHAIN', 'TMPDIR'}
            and all(env[key] == value for key, value in required.items())
            and all(type(value) is str and not any(c in value for c in ('\0', '\n', '\r')) for value in env.values()),
            'only exact public build environment inputs are admitted')


def command_argv(config, stage):
    if stage == 'assets':
        return [config['cargo'], 'run', '--locked', '--offline', '--manifest-path', 'som-rs/Cargo.toml',
                '--bin', 'som-compiler-assets', '--', '--destination', 'som-rs/compiler-assets/current',
                '--formatter', config['tools']['SOM_RUFF']]
    require(stage == 'candidate', 'exact build stage required')
    return [config['cargo'], 'build', '--locked', '--offline', '--manifest-path',
            'som-rs/som-targets/Cargo.toml', '--bin', 'som']


def begin(repo, helper_revision, candidate_revision, *, cargo, rustc, tools, target_dir, assets):
    root = producer_root()
    require(not root.exists(), 'preserve existing build provenance')
    sources, authority = admit(repo, helper_revision, candidate_revision)
    target_dir, assets = path_clean(target_dir), path_clean(assets)
    require(target_dir.parent == path_clean(os.environ['RUNNER_TEMP']) and not target_dir.exists()
            and target_dir.name == 'som-experimental-cargo-target', 'fresh fixed target directory required')
    require(assets == sources.root / 'som-rs/compiler-assets/current', 'fixed compiler asset destination required')
    config = {'repo': str(sources.root), 'cargo': str(path_clean(cargo)), 'rustc': str(path_clean(rustc)),
              'tools': {key: str(path_clean(value)) for key, value in tools.items()},
              'target_dir': str(target_dir), 'assets': str(assets), 'profile': 'debug', 'host': 'x86_64-unknown-linux-gnu'}
    pins = pin_tools(sources, config['cargo'], config['rustc'], config['tools'])
    before = {'head': helper_revision, 'porcelain': sources.status(SOURCE_ROOTS)}
    require(before['porcelain'] == '', 'pre-build SOURCE_ROOTS must be clean')
    snapshot = sources.snapshot()
    sources.verify()
    root.mkdir(mode=0o700)
    record = {'version': BEGIN_VERSION, 'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
              'github': github(), 'config': config, 'native_source_authority': authority, 'tool_identities': pins,
              'tree_before': before, 'all_tracked_before': snapshot, 'source_roots': SOURCE_ROOTS,
              'public_environment': public_environment(config), 'started_at': datetime.datetime.now(datetime.UTC).isoformat(),
              'role': 'before_actual_asset_and_candidate_build', 'product_acceptance_passed': False}
    verify_public_environment(record['public_environment'], config)
    dump(root / 'begin.json', record)
    return record


def load_begin(repo, helper, candidate):
    root = producer_root()
    value = parse(read(root / 'begin.json'))
    sources, authority = admit(repo, helper, candidate)
    require(value['version'] == BEGIN_VERSION and value['helper_revision'] == helper
            and value['candidate_revision'] == candidate and value['github'] == github()
            and value['config']['repo'] == str(sources.root) and value['native_source_authority'] == authority,
            'actual build begin identity changed')
    config = value['config']
    verify_public_environment(value['public_environment'], config)
    require(pin_tools(sources, config['cargo'], config['rustc'], config['tools']) == value['tool_identities'],
            'build tool identities changed')
    return root, sources, value


def process_stamp(pid):
    # procfs reports zero st_size for this virtual regular file. Do not apply
    # saved-file length checks to it, and do not follow a link to another task.
    path = path_clean(Path('/proc') / str(pid) / 'stat')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'regular proc stamp required')
        raw = stream.read(65537)
    require(len(raw) <= 65536, 'proc stamp exceeds bound')
    fields = raw.rsplit(b')', 1)[1].split()
    return {'pid': pid, 'state': fields[0].decode(), 'pgrp': int(fields[2]), 'session': int(fields[3]), 'start_ticks': int(fields[19])}


def session_members(pid):
    rows = []
    for path in Path('/proc').iterdir():
        if path.name.isdecimal():
            try:
                item = process_stamp(int(path.name))
                if item['session'] == pid and item['state'] != 'Z':
                    rows.append(item)
            except (OSError, ValueError, IndexError):
                pass
    return sorted(rows, key=lambda item: item['pid'])


@contextmanager
def cancellation():
    state = {'cleanup': False, 'signal': None}
    def interrupt(number, _frame):
        if state['signal'] is None:
            state['signal'] = number
        if not state['cleanup']:
            raise InterruptedError('owned build command interrupted')
    previous = {item: signal.signal(item, interrupt) for item in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    try:
        yield state
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


def command(repo, helper_revision, candidate_revision, *, stage, argv):
    root, sources, initial = load_begin(repo, helper_revision, candidate_revision)
    config = initial['config']
    require(list(argv) == command_argv(config, stage), 'only the actual fixed Cargo argv is admitted')
    if stage == 'candidate':
        previous = parse(read(root / 'commands/00-assets/record.json'))
        require(previous['complete'] is True and previous['exit_code'] == 0, 'actual asset command must pass first')
    directory = root / 'commands' / ('00-assets' if stage == 'assets' else '01-candidate')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    record = {'version': COMMAND_VERSION, 'stage': stage, 'command': list(argv), 'cwd': '.',
              'environment': initial['public_environment'], 'github': initial['github'],
              'begin_sha256': sha(read(root / 'begin.json')), 'started_at': datetime.datetime.now(datetime.UTC).isoformat(),
              'complete': False, 'product_acceptance_passed': False, 'max_rss_bytes': None}
    record['tool_identities_before'] = initial['tool_identities']
    record['source_pins_before'] = dict(sources.pins)
    process, owner, terminal = None, None, None
    cleanup = {'signal_target': 'owned new process group only', 'signals': [], 'cleanup_passed': False,
               'wait_completed': False, 'owned_pid': None, 'exit_code': None, 'remaining_owned_session_members': []}
    started = time.monotonic()
    with cancellation() as interrupted:
        try:
            with (directory / 'stdout.raw').open('xb') as stdout, (directory / 'stderr.raw').open('xb') as stderr:
                process = subprocess.Popen(argv, cwd=sources.root, env=record['environment'], stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                cleanup['owned_pid'] = process.pid
                owner = process_stamp(process.pid)
                executable = (Path('/proc') / str(process.pid) / 'exe').stat()
                require(owner['pid'] == owner['pgrp'] == owner['session'] and owner['start_ticks'] > 0
                        and (executable.st_dev, executable.st_ino) ==
                            (initial['tool_identities']['cargo']['device'], initial['tool_identities']['cargo']['inode']),
                        'actual Cargo child ownership or executable differs')
                record['owned_start'] = {**owner, 'executable_device': executable.st_dev, 'executable_inode': executable.st_ino}
                terminal = process.wait(timeout=3000)
                cleanup.update(wait_completed=True, wait_exit_code=terminal)
        except BaseException as error:
            record['error'] = type(error).__name__
        finally:
            interrupted['cleanup'] = True
            if process is not None:
                try:
                    if process.poll() is None:
                        current = process_stamp(process.pid)
                        require(owner is not None and current['start_ticks'] == owner['start_ticks']
                                and current['pid'] == current['pgrp'] == current['session'], 'lost owned Cargo leader')
                        os.killpg(process.pid, signal.SIGTERM)
                        cleanup['signals'].append('SIGTERM')
                        try:
                            terminal = process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            current = process_stamp(process.pid)
                            require(current['start_ticks'] == owner['start_ticks']
                                    and current['pid'] == current['pgrp'] == current['session'], 'lost escalation ownership')
                            os.killpg(process.pid, signal.SIGKILL)
                            cleanup['signals'].append('SIGKILL')
                            terminal = process.wait(timeout=10)
                    else:
                        terminal = process.wait(timeout=10)
                    cleanup.update(wait_completed=True, wait_exit_code=terminal, exit_code=terminal,
                                   remaining_owned_session_members=session_members(process.pid))
                    cleanup['cleanup_passed'] = type(terminal) is int and cleanup['remaining_owned_session_members'] == []
                except BaseException as error:
                    cleanup['error'] = type(error).__name__
            record.update(cleanup=cleanup, exit_code=terminal, seconds=time.monotonic() - started,
                          interrupted_signal=interrupted['signal'], finished_at=datetime.datetime.now(datetime.UTC).isoformat())
            record['logs'] = {name: portable(read_file(directory / (name + '.raw'))[1]) for name in ('stdout', 'stderr')}
            try:
                after_tools = pin_tools(sources, config['cargo'], config['rustc'], config['tools'])
                record['tool_identities_after'] = after_tools
                require(after_tools == initial['tool_identities'], 'tools changed during Cargo')
                sources.verify()
                record['source_pins_after'] = dict(sources.pins)
                require(record['source_pins_before'] == record['source_pins_after'], 'build helper source vector changed')
                record['identity_recheck_passed'] = True
            except Exception as error:
                record.update(identity_recheck_passed=False, identity_error=type(error).__name__)
            record['complete'] = (owner is not None and terminal == 0 and cleanup['cleanup_passed']
                                  and record['identity_recheck_passed'] and 'error' not in record
                                  and interrupted['signal'] is None)
            dump(directory / 'record.json', record)
    return record


def make_words(text):
    result, word, escaped = [], [], False
    for char in text:
        if escaped:
            word.append(char); escaped = False
        elif char == '\\':
            escaped = True
        elif char.isspace():
            if word:
                result.append(''.join(word)); word = []
        else:
            require(char != '$', 'unexpanded make variable in dep-info')
            word.append(char)
    require(not escaped, 'unfinished dep-info escape')
    if word:
        result.append(''.join(word))
    return result


def dependency_files(raw, repo, target_dir, binary):
    text = raw.decode('utf-8', 'strict').replace('\\\n', ' ')
    require('\0' not in text and text.splitlines(), 'empty or invalid dep-info')
    first = text.splitlines()[0]
    target, separator, deps = first.partition(': ')
    require(separator and make_words(target) == [str(binary)], 'actual binary dep-info target differs')
    result, external = set(), set()
    repo, target_dir = Path(repo), Path(target_dir)
    for value in make_words(deps):
        path = Path(os.path.normpath(value if value.startswith('/') else str(repo / value)))
        if path.is_relative_to(repo):
            require(not path.is_relative_to(target_dir), 'target output cannot become repository source')
            result.add(relative(path.relative_to(repo).as_posix()))
        else:
            external.add(str(path))
    require(result and 'som-rs/som-targets/src/main.rs' in result, 'actual repository binary dependency universe missing')
    return sorted(result), sorted(external)


def source_universe(sources, before, dep_raw, config):
    reads, external = dependency_files(dep_raw, config['repo'], config['target_dir'], str(Path(config['target_dir']) / 'debug/som'))
    require(before['source_roots'] == SOURCE_ROOTS
            and before['tree_before'] == {'head': sources.revision, 'porcelain': ''}, 'pre-build source roots or HEAD differ')
    roots = {name for name in sources.paths if any(name == root or name.startswith(root + '/') for root in SOURCE_ROOTS)}
    require(all(any(name == root or name.startswith(root + '/') for name in roots) for root in SOURCE_ROOTS)
            and set(reads) <= sources.paths and set(before['all_tracked_before']) == sources.paths,
            'untracked dependency or incomplete actual pre-build source snapshot')
    names = roots | set(reads)
    require(sources.status(SOURCE_ROOTS) == '' and sources.status(reads) == '', 'built source roots or dependencies are dirty')
    files, modes = {}, {}
    for name in sorted(names):
        raw = sources.read(name)
        initial = before['all_tracked_before'][name]
        require(initial == {'git_mode': sources.entries[name]['git_mode'], 'git_sha256': sha(raw), 'working_sha256': sha(raw)},
                'actual source changed since before the build')
        files[name], modes[name] = sha(raw), sources.entries[name]['git_mode']
    return reads, external, files, modes


def verify_archive(path, source, sources):
    require(sha(read(path)) == source['archive_sha256'], 'source archive raw hash differs')
    files, directories = set(), set()
    expected_dirs = {parent.as_posix() for name in source['files'] for parent in Path(name).parents if parent.as_posix() != '.'}
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            name = relative(member.name.rstrip('/'))
            require(set(member.pax_headers) <= {'comment', 'path'}
                    and member.pax_headers.get('comment', source['commit']) == source['commit']
                    and member.pax_headers.get('path', member.name) == member.name,
                    'unbound source archive extension')
            if member.isdir():
                require(name in expected_dirs and name not in directories and member.mode == 0o755, 'source archive directory differs')
                directories.add(name)
                continue
            require(member.isfile() and name in source['files'] and name not in files,
                    'unlisted, linked, duplicate or special source archive member')
            expected = sources.read(name)
            stream = archive.extractfile(member)
            body = stream.read(32 * 1024**2 + 1)
            require(body == expected and sha(body) == source['files'][name]
                    and member.size == len(body)
                    and member.mode == (0o755 if source['git_modes'][name] == '100755' else 0o644),
                    'full source archive bytes or Git mode differ')
            files.add(name)
    require(files == set(source['files']) and directories == expected_dirs, 'full source archive universe differs')


def checked_commands(root, before):
    rows = []
    for index, stage in enumerate(('assets', 'candidate')):
        directory = Path(root) / 'commands' / f'{index:02d}-{stage}'
        row = parse(read(directory / 'record.json'))
        owner, cleanup = row.get('owned_start', {}), row.get('cleanup', {})
        require(row['version'] == COMMAND_VERSION and row['stage'] == stage
                and row['command'] == command_argv(before['config'], stage) and row['cwd'] == '.'
                and row['environment'] == before['public_environment'] and row['github'] == before['github']
                and row['begin_sha256'] == sha(read(Path(root) / 'begin.json'))
                and row.get('complete') is True and type(row.get('exit_code')) is int and row['exit_code'] == 0
                and row.get('identity_recheck_passed') is True and row.get('interrupted_signal') is None
                and row.get('tool_identities_before') == row.get('tool_identities_after') == before['tool_identities']
                and type(row.get('source_pins_before')) is dict and row['source_pins_before']
                and row['source_pins_before'] == row.get('source_pins_after')
                and 'error' not in row and row['product_acceptance_passed'] is False,
                'actual successful source-bound Cargo command record missing')
        require(all(type(owner.get(key)) is int and owner[key] > 0 for key in ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode'))
                and owner['pid'] == owner.get('pgrp') == owner.get('session')
                and type(owner.get('state')) is str and len(owner['state']) == 1 and owner['state'] != 'Z'
                and (owner.get('executable_device'), owner.get('executable_inode')) ==
                    (before['tool_identities']['cargo']['device'], before['tool_identities']['cargo']['inode'])
                and cleanup.get('owned_pid') == owner['pid'] and cleanup.get('signal_target') == 'owned new process group only'
                and type(cleanup.get('signals')) is list and cleanup['signals'] in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL'])
                and cleanup.get('remaining_owned_session_members') == [] and cleanup.get('cleanup_passed') is True
                and cleanup.get('wait_completed') is True and type(cleanup.get('wait_exit_code')) is int
                and cleanup['wait_exit_code'] == cleanup.get('exit_code') == row['exit_code'],
                'actual Cargo PID/start/group/wait/exit/cleanup evidence differs')
        require(row['logs'] == {name: portable(read_file(directory / (name + '.raw'))[1]) for name in ('stdout', 'stderr')},
                'complete separated Cargo raw log bytes differ')
        rows.append(row)
    return rows


def proof_inputs(sources, assembly_root, candidate):
    graph = raw_module(sources, '_som_layout_graph_checks', GRAPH)
    checked = graph.verify_compile_proof(Path(assembly_root), candidate)
    raw = read(Path(assembly_root) / 'manifest.json')
    proof = parse(raw)
    require(proof['initial_identities']['source'] == proof['independent_source_record']['source_sha256']
            and proof['independent_source_record']['commit'] == candidate, 'complete tested source proof differs')
    for name, expected in proof['initial_identities']['source'].items():
        require(sha(sources.read(name)) == expected, 'tested raw Git source changed')
    return checked, proof


def finish(repo, helper_revision, candidate_revision, *, assembly_root):
    root, sources, before = load_begin(repo, helper_revision, candidate_revision)
    require(not (root / 'provenance.json').exists(), 'preserve completed build provenance')
    config = before['config']
    commands = checked_commands(root, before)
    binary, dep_file = Path(config['target_dir']) / 'debug/som', Path(config['target_dir']) / 'debug/som.d'
    dep_raw = read(dep_file)
    reads, external, files, modes = source_universe(sources, before, dep_raw, config)
    checked, proof = proof_inputs(sources, assembly_root, candidate_revision)
    observed = {'binary': read_file(binary, cargo_output=True)[1], 'assets': tree(config['assets']),
                'tools': tool_identities(config['tools'])}
    require(observed == {name: proof['initial_identities'][name] for name in ('binary', 'assets', 'tools')},
            'actual same-build binary/assets/native tools differ from tested proof')
    source = {'version': 'som.compiler-candidate-source.v1', 'created_at': datetime.datetime.now(datetime.UTC).isoformat(),
              'commit': candidate_revision, 'roots': SOURCE_ROOTS, 'dep_info_files': reads, 'files': files, 'git_modes': modes}
    archive_argv = ['/usr/bin/git', '--no-replace-objects', '-c', 'tar.umask=0022', '-C', str(sources.root),
                    'archive', '--format=tar', candidate_revision, '--', *files]
    with (root / 'source.tar').open('xb') as stream:
        subprocess.run(archive_argv, stdout=stream, stderr=subprocess.PIPE, check=True)
    source['archive_sha256'] = sha(read(root / 'source.tar'))
    verify_archive(root / 'source.tar', source, sources)
    write(root / 'dep-info.raw', dep_raw)
    dump(root / 'source.json', source)
    packager = Path(config['target_dir']) / 'debug/som-compiler-assets'
    build = {'version': 'som.rust-output-candidate-build.v1', 'build_role': 'actual_current_build_job',
             'commands': commands, 'complete': True, 'profile': 'debug', 'toolchain': {
                 name: before['tool_identities'][name]['sha256'] for name in ('cargo', 'rustc')},
             'tree_before': before['tree_before'], 'tree_after': {'head': candidate_revision, 'porcelain': sources.status(SOURCE_ROOTS)},
             'read_files_status': {'head': candidate_revision, 'porcelain': sources.status(reads)}, 'untracked_reads': [],
             'source_unchanged': True, 'source_sha256': sha(read(root / 'source.json')),
             'binary_sha256': observed['binary']['sha256'], 'binary_bytes': observed['binary']['size'],
             'assets_from': config['assets'], 'asset_manifest_sha256': observed['assets']['manifest.json']['sha256'],
             'assets_packaged': {'packager_sha256': read_file(packager, cargo_output=True)[1]['sha256'],
                 'formatter': config['tools']['SOM_RUFF'], 'formatter_sha256': observed['tools']['SOM_RUFF']['sha256'],
                 'files': {name: item['sha256'] for name, item in observed['assets'].items()}},
             'som_ts_prettier': config['tools']['SOM_TS_PRETTIER'], 'prettier_tree_sha256': tree_digest(observed['tools']['SOM_TS_PRETTIER']),
             'compiler_rebuilt_in_callback': False, 'separate_cargo_test_job_claimed_for_this_binary': False,
             'product_acceptance_passed': False, 'whole_acceptance_complete': False}
    require(build['tree_after'] == build['tree_before'] and build['read_files_status']['porcelain'] == '', 'built source state changed')
    dump(root / 'build.json', build)
    require(read(dep_file) == dep_raw and sources.snapshot().keys() == before['all_tracked_before'].keys(), 'actual dependency or tracked universe changed')
    source_universe(sources, before, dep_raw, config)
    sources.verify()
    inventory = {path.relative_to(root).as_posix(): portable(read_file(path)[1]) for path in sorted(root.rglob('*')) if path.is_file()}
    provenance = {'version': VERSION, 'role': 'actual_same_job_build_before_upload', 'helper_revision': helper_revision,
                  'candidate_revision': candidate_revision, 'github': before['github'], 'host': config['host'],
                  'profile': 'debug', 'begin_sha256': sha(read(root / 'begin.json')), 'source_sha256': build['source_sha256'],
                  'build_sha256': sha(read(root / 'build.json')), 'compile_proof': checked,
                  'tested_identities': portable(observed), 'dep_info_external_paths': external,
                  'archive_command': archive_argv, 'files': inventory, 'complete': True,
                  'compiler_rebuilt_in_callback': False, 'separate_cargo_test_job_claimed_for_this_binary': False,
                  'product_acceptance_passed': False, 'whole_acceptance_complete': False}
    dump(root / 'provenance.json', provenance)
    return provenance


def catalog_origin(catalog, producer, helper, candidate):
    current = github()
    require(catalog['version'] == 'som.same-candidate-artifact-inputs.v1'
            and catalog['helper_revision'] == helper and catalog['candidate_revision'] == candidate
            and catalog['repository'] == current['GITHUB_REPOSITORY']
            and type(catalog['run_id']) is int and catalog['run_id'] == int(current['GITHUB_RUN_ID'])
            and type(catalog['run_attempt']) is int and catalog['run_attempt'] == int(current['GITHUB_RUN_ATTEMPT'])
            and catalog['product_acceptance_passed'] is False,
            'checked current-run artifact catalog required')
    require(all(producer[key] == current[key] for key in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')),
            'build provenance is from another repository/run/attempt')


def verify_provenance(repo, source_reader, helper, candidate, *, build_provenance_root, assembly_root,
                      carrier_root, restored_carrier_root, artifact_inputs):
    sources, authority = admit(repo, helper, candidate, source_reader)
    root, assembly_root, carrier_root, restored_root = map(path_clean, (build_provenance_root, assembly_root, carrier_root, restored_carrier_root))
    require(root == assembly_root.parent / PROVENANCE_NAME and assembly_root.name == 'som-unsealed-native-compile'
            and assembly_root.parent.name == '_temp', 'exact downloaded assembly-artifact provenance sibling required')
    catalog = artifact_inputs if type(artifact_inputs) is dict else parse(read(artifact_inputs))
    raw = read(root / 'provenance.json')
    provenance, before = parse(raw), parse(read(root / 'begin.json'))
    require(provenance['version'] == VERSION and provenance['role'] == 'actual_same_job_build_before_upload'
            and before['version'] == BEGIN_VERSION and before['role'] == 'before_actual_asset_and_candidate_build'
            and before['product_acceptance_passed'] is False
            and provenance['helper_revision'] == before['helper_revision'] == helper
            and provenance['candidate_revision'] == before['candidate_revision'] == candidate
            and provenance['github'] == before['github'] and before['native_source_authority'] == authority
            and provenance['host'] == before['config']['host'] == 'x86_64-unknown-linux-gnu'
            and platform.system() == 'Linux' and platform.machine() == 'x86_64'
            and provenance['profile'] == before['config']['profile'] == 'debug'
            and provenance.get('complete') is True and provenance.get('compiler_rebuilt_in_callback') is False
            and provenance.get('separate_cargo_test_job_claimed_for_this_binary') is False
            and provenance['product_acceptance_passed'] is provenance['whole_acceptance_complete'] is False,
            'actual same-source Linux build provenance required')
    verify_public_environment(before['public_environment'], before['config'])
    catalog_origin(catalog, provenance['github'], helper, candidate)
    graph = raw_module(sources, '_som_layout_artifact_admission', GRAPH)
    again = graph.validate_artifacts(catalog['artifacts'], helper, candidate, catalog['run_id'], catalog['repository'])
    require(again['artifacts'] == catalog['artifacts'], 'live same-run artifact catalog changed')
    inventory = {path.relative_to(root).as_posix(): portable(read_file(path)[1]) for path in sorted(root.rglob('*'))
                 if path.is_file() and path != root / 'provenance.json'}
    require(inventory == provenance['files'] and sha(read(root / 'begin.json')) == provenance['begin_sha256'],
            'complete actual provenance raw file universe changed')
    commands = checked_commands(root, before)
    for row in commands:
        for name, expected in row['source_pins_before'].items():
            require(sha(sources.read(name)) == expected, 'actual command helper source changed')
    source, build = parse(read(root / 'source.json')), parse(read(root / 'build.json'))
    reads, external, files, modes = source_universe(sources, before, read(root / 'dep-info.raw'), before['config'])
    require(source['version'] == 'som.compiler-candidate-source.v1' and source['commit'] == candidate
            and source['roots'] == SOURCE_ROOTS and source['dep_info_files'] == reads
            and source['files'] == files and source['git_modes'] == modes
            and external == provenance['dep_info_external_paths'], 'actual dependency-read full source universe differs')
    require(provenance['archive_command'] == ['/usr/bin/git', '--no-replace-objects', '-c', 'tar.umask=0022',
            '-C', before['config']['repo'], 'archive', '--format=tar', candidate, '--', *files],
            'actual build-job Git archive command differs')
    verify_archive(root / 'source.tar', source, sources)
    require(sha(read(root / 'source.json')) == provenance['source_sha256'] == build['source_sha256']
            and sha(read(root / 'build.json')) == provenance['build_sha256']
            and build['version'] == 'som.rust-output-candidate-build.v1' and build['build_role'] == 'actual_current_build_job'
            and build['complete'] is build['source_unchanged'] is True and build['profile'] == 'debug'
            and build['commands'] == commands and build['tree_before'] == build['tree_after'] == before['tree_before']
            and build['tree_before'] == {'head': candidate, 'porcelain': ''}
            and build['read_files_status'] == {'head': candidate, 'porcelain': ''} and build['untracked_reads'] == []
            and build['assets_from'] == before['config']['assets']
            and build['som_ts_prettier'] == before['config']['tools']['SOM_TS_PRETTIER']
            and build['assets_packaged']['formatter'] == before['config']['tools']['SOM_RUFF']
            and build['compiler_rebuilt_in_callback'] is build['separate_cargo_test_job_claimed_for_this_binary'] is False
            and build['product_acceptance_passed'] is build['whole_acceptance_complete'] is False,
            'actual original legacy build record differs')
    checked, proof = proof_inputs(sources, assembly_root, candidate)
    require(checked == provenance['compile_proof'], 'fresh compile/assembly proof differs from build job')
    carrier_raw = read(carrier_root / 'carrier.json')
    carrier = parse(carrier_raw)
    require(carrier['candidate_revision'] == candidate and carrier['compiler_rebuilt'] is False
            and carrier['origin_role'] == 'original already tested candidate bytes'
            and carrier['compile_manifest_sha256'] == checked['compile_manifest_sha256']
            and read(carrier_root / 'compile-manifest.json') == read(assembly_root / 'manifest.json')
            and carrier['github']['GITHUB_RUN_ID'] == str(catalog['run_id'])
            and carrier['github']['GITHUB_RUN_ATTEMPT'] == str(catalog['run_attempt']), 'same-build original current carrier required')
    observed = portable({'binary': read_file(restored_root / 'binary')[1], 'assets': tree(restored_root / 'assets'),
                          'tools': tool_identities({key: restored_root / path for key, path in RESTORED_TOOLS.items()})})
    expected = portable({key: proof['initial_identities'][key] for key in ('binary', 'assets', 'tools')})
    require(observed == expected == provenance['tested_identities'] == carrier['tested_identities']
            and build['binary_sha256'] == expected['binary']['sha256'] and build['binary_bytes'] == expected['binary']['size']
            and build['asset_manifest_sha256'] == expected['assets']['manifest.json']['sha256']
            and build['prettier_tree_sha256'] == tree_digest(expected['tools']['SOM_TS_PRETTIER'])
            and build['assets_packaged']['formatter_sha256'] == expected['tools']['SOM_RUFF']['sha256']
            and build['assets_packaged']['files'] == {name: item['sha256'] for name, item in expected['assets'].items()},
            'restored binary/assets/Prettier differ from original tested build')
    # Source pins bind both the producer's declared tool hashes and the same
    # host's native tools. Runtime paths do not have to exist in this callback.
    profile = parse(sources.read(RUST_PROFILE))
    require(build['toolchain'] == {key: profile['executables'][key]['sha256'] for key in ('cargo', 'rustc')}
            and all(before['tool_identities'][key]['sha256'] == build['toolchain'][key] for key in ('cargo', 'rustc'))
            and portable(before['tool_identities']['native']) == expected['tools'], 'original build tool source pins differ')
    native = parse(sources.read(NATIVE_PROFILE))
    for role, name in TOOL_ROLES.items():
        host = 'platform-independent JavaScript' if name == 'prettier' else provenance['host']
        pins = [item for item in native['tools'] if item['tool'] == name and item['host'] == host]
        observed_pin = tree_digest(expected['tools'][role]) if name == 'prettier' else expected['tools'][role]['sha256']
        require(len(pins) == 1 and observed_pin == pins[0]['tree_sha256' if name == 'prettier' else 'executable_sha256'],
                'same-host restored native tool source pin differs')
    sources.verify()
    return {'sources': sources, 'provenance': provenance, 'build': build, 'source': source, 'raw_sha256': sha(raw),
            'before': before, 'identity': expected, 'carrier_sha256': sha(carrier_raw), 'catalog': catalog}


def restored_build(original, candidate_dir, restored_root, provenance_sha256, original_sha256):
    # The only changed original fields are public runtime file locations. The
    # original command list and every original raw byte remain retained below.
    value = parse(canonical(original))
    replacements = {'assets_from': str(candidate_dir / 'assets'),
                    'som_ts_prettier': str(restored_root / RESTORED_TOOLS['SOM_TS_PRETTIER'])}
    changes = {key: {'original': value[key], 'restored': replacement} for key, replacement in replacements.items()}
    value.update(replacements)
    formatter = str(restored_root / RESTORED_TOOLS['SOM_RUFF'])
    changes['assets_packaged.formatter'] = {'original': value['assets_packaged']['formatter'], 'restored': formatter}
    value['assets_packaged']['formatter'] = formatter
    value['build_role'] = 'restored_same_build'
    value['restored_same_build'] = {'original_build_sha256': original_sha256, 'build_provenance_sha256': provenance_sha256,
                                  'runtime_path_normalizations': changes, 'compiler_rebuilt': False,
                                  'original_commands_reexecuted': False}
    return value


def layout_result(root, verified, helper, candidate):
    build = parse(read(root / 'build.json'))
    return {'version': LAYOUT_VERSION, 'role': 'restored_same_build', 'helper_revision': helper, 'candidate_revision': candidate,
            'candidate_dir': str(root), 'binary': str(root / 'som'), 'assets': str(root / 'assets'),
            'source_json': str(root / 'source.json'), 'source_tar': str(root / 'source.tar'), 'build_json': str(root / 'build.json'),
            'original_build_json': str(root / 'original-build-provenance/build.json'),
            'source_sha256': build['source_sha256'], 'binary_sha256': build['binary_sha256'],
            'asset_manifest_sha256': build['asset_manifest_sha256'], 'prettier_tree_sha256': build['prettier_tree_sha256'],
            'som_ts_prettier': build['som_ts_prettier'], 'build_provenance_sha256': verified['raw_sha256'],
            'compiler_rebuilt': False, 'product_acceptance_passed': False, 'whole_acceptance_complete': False}


def verify_candidate_layout(root, source_reader, helper_revision, candidate_revision, *, repo,
                            build_provenance_root, assembly_root, carrier_root, restored_carrier_root, artifact_inputs):
    root, restored_root = path_clean(root), path_clean(restored_carrier_root)
    verified = verify_provenance(repo, source_reader, helper_revision, candidate_revision,
        build_provenance_root=build_provenance_root, assembly_root=assembly_root, carrier_root=carrier_root,
        restored_carrier_root=restored_root, artifact_inputs=artifact_inputs)
    provenance_root = Path(build_provenance_root)
    expected_raw = {name: read(provenance_root / name) for name in verified['provenance']['files']}
    expected_raw['provenance.json'] = read(provenance_root / 'provenance.json')
    copied = tree(root / 'original-build-provenance')
    require(portable(copied) == {name: portable(read_file(provenance_root / name)[1]) for name in expected_raw}
            and all(read(root / 'original-build-provenance' / name) == raw for name, raw in expected_raw.items()),
            'original raw build provenance was not retained exactly')
    layout = parse(read(root / 'layout.json'))
    require(layout['version'] == LAYOUT_VERSION and layout['helper_revision'] == helper_revision
            and layout['candidate_revision'] == candidate_revision and layout['role'] == 'restored_same_build'
            and layout['build_provenance_sha256'] == verified['raw_sha256']
            and layout['compiler_rebuilt'] is layout['product_acceptance_passed'] is False,
            'saved same-build layout path facts differ')
    original_dir, original_restore = Path(layout['original_candidate_dir']), Path(layout['original_restored_carrier_root'])
    require(original_dir.is_absolute() and original_restore.is_absolute()
            and '..' not in original_dir.parts and '..' not in original_restore.parts
            and original_dir != original_restore and original_dir not in original_restore.parents
            and original_restore not in original_dir.parents,
            'saved runtime path normalization roots overlap or are unsafe')
    require(read(root / 'source.json') == read(provenance_root / 'source.json')
            and read(root / 'source.tar') == read(provenance_root / 'source.tar')
            and portable(read_file(root / 'som')[1]) == verified['identity']['binary']
            and portable(tree(root / 'assets')) == verified['identity']['assets'], 'same-build candidate layout bytes differ')
    expected_build = restored_build(verified['build'], original_dir, original_restore, verified['raw_sha256'], sha(read(provenance_root / 'build.json')))
    require(read(root / 'build.json') == canonical(expected_build) + b'\n', 'only explicit same-build runtime path normalization is admitted')
    require(sha(read(root / 'build.json')) == layout['compatibility_build_sha256'], 'saved compatibility build bytes changed')
    expected_names = {'som', 'source.json', 'source.tar', 'build.json', 'layout.json', 'assets', 'original-build-provenance'}
    require(expected_names <= {path.name for path in root.iterdir()}
            and {path.name for path in root.iterdir()} <= expected_names | {'runtime.json', 'server.log'}, 'unlisted candidate layout member')
    verified['sources'].verify()
    return layout_result(root, verified, helper_revision, candidate_revision)


def restore_candidate_layout(repo, source_reader, helper_revision, candidate_revision, *, build_provenance_root,
                             assembly_root, carrier_root, restored_carrier_root, artifact_inputs, out, prettier=None):
    out, restored_root = path_clean(out), path_clean(restored_carrier_root)
    require(path_clean(os.environ['RUNNER_TEMP']) in out.parents and not out.exists(), 'new owned candidate directory required')
    expected_prettier = restored_root / RESTORED_TOOLS['SOM_TS_PRETTIER']
    require(prettier is None or path_clean(prettier) == expected_prettier, 'use the exact restored same-build Prettier tree')
    verified = verify_provenance(repo, source_reader, helper_revision, candidate_revision,
        build_provenance_root=build_provenance_root, assembly_root=assembly_root, carrier_root=carrier_root,
        restored_carrier_root=restored_root, artifact_inputs=artifact_inputs)
    out.mkdir(mode=0o700)
    provenance_root = Path(build_provenance_root)
    names = [*verified['provenance']['files'], 'provenance.json']
    for name in names:
        write(out / 'original-build-provenance' / relative(name), read(provenance_root / name), read_file(provenance_root / name)[1]['mode'])
    for name in ('source.json', 'source.tar'):
        write(out / name, read(provenance_root / name), read_file(provenance_root / name)[1]['mode'])
    write(out / 'som', read(restored_root / 'binary'), verified['identity']['binary']['mode'])
    for name, item in verified['identity']['assets'].items():
        write(out / 'assets' / relative(name), read(restored_root / 'assets' / name), item['mode'])
    dump(out / 'build.json', restored_build(verified['build'], out, restored_root, verified['raw_sha256'], sha(read(provenance_root / 'build.json'))))
    dump(out / 'layout.json', {'version': LAYOUT_VERSION, 'role': 'restored_same_build',
        'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
        'original_candidate_dir': str(out), 'original_restored_carrier_root': str(restored_root),
        'compatibility_build_sha256': sha(read(out / 'build.json')), 'build_provenance_sha256': verified['raw_sha256'],
        'compiler_rebuilt': False, 'product_acceptance_passed': False})
    return verify_candidate_layout(out, source_reader, helper_revision, candidate_revision, repo=repo,
        build_provenance_root=build_provenance_root, assembly_root=assembly_root, carrier_root=carrier_root,
        restored_carrier_root=restored_root, artifact_inputs=artifact_inputs)


def main(argv=None):
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
            'standalone producer requires Python -I -S -B')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('begin', 'command', 'finish'))
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True)
    parser.add_argument('--candidate-revision', required=True)
    for key in ('cargo', 'rustc', 'ruff', 'rustfmt', 'node', 'prettier', 'target-dir', 'assets', 'assembly-root'):
        parser.add_argument('--' + key, type=Path)
    parser.add_argument('--stage', choices=('assets', 'candidate'))
    args, extra = parser.parse_known_args(argv)
    if args.operation == 'begin':
        require(not extra and all(getattr(args, name) is not None for name in
                ('cargo', 'rustc', 'ruff', 'rustfmt', 'node', 'prettier', 'target_dir', 'assets')), 'all real begin tool/path arguments required')
        record = begin(args.repo, args.helper_revision, args.candidate_revision, cargo=args.cargo, rustc=args.rustc,
            tools={'SOM_RUFF': args.ruff, 'SOM_RUSTFMT': args.rustfmt, 'SOM_TS_NODE': args.node, 'SOM_TS_PRETTIER': args.prettier},
            target_dir=args.target_dir, assets=args.assets)
    elif args.operation == 'command':
        require(args.stage is not None and extra and extra[0] == '--', 'actual command argv must follow --')
        record = command(args.repo, args.helper_revision, args.candidate_revision, stage=args.stage, argv=extra[1:])
    else:
        require(not extra and args.assembly_root is not None, 'actual complete assembly root required')
        record = finish(args.repo, args.helper_revision, args.candidate_revision, assembly_root=args.assembly_root)
    print(json.dumps({'operation': args.operation, 'provenance_root': str(producer_root()),
                      'complete': record.get('complete', False), 'product_acceptance_passed': False}))
    return 0 if args.operation != 'command' or record['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
