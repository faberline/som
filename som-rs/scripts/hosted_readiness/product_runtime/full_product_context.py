"""Bound native tools and complete public inputs for hosted product checkers.

The optional context changes paths and input transport only. It does not change
the frozen checker's assertions or turn a partial result into acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType

SCRIPT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
PROFILE = 'som-rs/contracts/native-tools-v1/product-rust-tools-v1.json'
RUST_HELPER = 'som-rs/scripts/hosted_readiness/product_runtime/qualify_rust_tools.py'
WHEELS = 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py'
CONSUMER = 'som-rs/scripts/hosted_readiness/product_python/qualify_python.py'
GOLDENS = 'som-rs/scripts/hosted_readiness/inputs/public-golden-hashes-v1.json'
GOLDENS_SHA256 = 'ea573d3c842d367b32538ad8b9fec01608b4f05f676a07a80d9b9acf81a69a8b'
BASELINE = 'som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json'
BASELINE_SHA256 = '650c6d758519b92dc6bcc11678aa14e9942612a9ed0f1fd35202876065c425a9'
DEPENDENCY_GUARD = 'som-rs/scripts/hosted_readiness/dependency_candidate.py'
ENV = 'SOM_FULL_PRODUCT_CONTEXT'
_ACTIVE = None


class Refusal(ValueError):
    pass


def require(value, message):
    if not value:
        raise Refusal(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def dependency_identity(document, helper_revision, candidate_revision, admission):
    require(document.get('source_revision') == helper_revision
            and document.get('helper_revision') == helper_revision
            and document.get('candidate_revision') == candidate_revision,
            'explicit dependency helper and candidate identities differ')
    require(type(admission) is dict and admission.get('helper_revision') == helper_revision
            and admission.get('candidate_revision') == candidate_revision
            and admission.get('product_acceptance_passed') is False
            and document.get('candidate_admission') == admission,
            'dependency candidate admission proof differs from current checked inputs')


def read(path, maximum=64 * 1024 * 1024):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute clean input path required')
    require(not any(item.is_symlink() for item in (path, *path.parents)), 'linked input refused')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
            'regular bounded single-link input required')
    return path.read_bytes()


def relative(value):
    require(type(value) is str and value and '\\' not in value and not value.startswith('/')
            and not any(part in {'', '.', '..'} for part in value.split('/')),
            'safe relative member required')
    return value


class SourceBytes:
    def __init__(self, root, revision):
        self.root, self.revision, self.pins = Path(root).absolute(), revision, {}
        require(re.fullmatch('[0-9a-f]{40}', revision) is not None, 'full helper source commit required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == revision,
                'helper source HEAD differs')

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                                       stderr=subprocess.DEVNULL)

    def read(self, name):
        relative(name)
        result = self.git('ls-tree', '-z', self.revision, '--', name)
        entry, tab, actual = result.removesuffix(b'\0').partition(b'\t')
        fields = entry.split()
        require(result.count(b'\0') == 1 and tab and actual == name.encode()
                and len(fields) == 3 and fields[0] in {b'100644', b'100755'} and fields[1] == b'blob',
                'fixed regular Git source required')
        raw = self.git('cat-file', 'blob', fields[2].decode())
        require(len(raw) <= 32 * 1024 * 1024 and raw == read(self.root / name),
                'source differs from committed Git bytes')
        self.pins[name] = digest(raw)
        return raw

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == self.revision,
                'helper source HEAD changed')
        for path, before in list(self.pins.items()):
            require(digest(self.read(path)) == before, 'helper source changed')


def bound_module(sources, name, path):
    raw = sources.read(path)
    if name in sys.modules:
        module = sys.modules[name]
        require(getattr(module, '__som_raw_sha256__', None) == digest(raw)
                and getattr(module, '__file__', None) == str(sources.root / path),
                'preloaded source module differs')
        return module
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(sources.root / path), None, ''
    module.__som_raw_sha256__ = digest(raw)
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class GitSourceFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Use the exact checked Git bytes for repository Python imports."""
    def __init__(self, sources):
        self.sources, self.pending = sources, {}

    def find_spec(self, fullname, path=None, target=None):
        found = importlib.machinery.PathFinder.find_spec(fullname, path)
        if found is None or not found.origin or found.origin in {'built-in', 'frozen'}:
            return None
        origin = Path(found.origin).absolute()
        if self.sources.root not in origin.parents:
            return None
        require(origin.suffix == '.py', 'repository extension or bytecode import refused')
        name = origin.relative_to(self.sources.root).as_posix()
        self.pending[fullname] = (origin, self.sources.read(name))
        return importlib.util.spec_from_file_location(fullname, origin, loader=self,
            submodule_search_locations=[str(origin.parent)] if found.submodule_search_locations is not None else None)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        origin, raw = self.pending.pop(module.__name__)
        module.__file__, module.__cached__ = str(origin), None
        module.__som_raw_sha256__ = digest(raw)
        exec(compile(raw, str(origin), 'exec', dont_inherit=True), module.__dict__)


def install_source_finder(sources):
    finder = GitSourceFinder(sources)
    sys.meta_path.insert(0, finder)
    return finder


class Context:
    def __init__(self, path):
        self.path, self.raw = Path(path).absolute(), read(Path(path).absolute())
        self.data = json.loads(self.raw)
        require(self.data.get('version') == 'som.full-product-context.v1'
                and os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux',
                'GitHub hosted product context required')
        self.repo = Path(self.data['repo']).absolute()
        self.helper_revision = self.data['helper_revision']
        self.candidate_revision = self.data['candidate_revision']
        self.sources = SourceBytes(self.repo, self.helper_revision)
        self.sources.read(SCRIPT)
        self.modules, self.cache = {}, {}
        self.work = Path(self.data['work_root']).absolute()
        temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        require(temporary in self.work.parents and self.repo not in self.work.parents,
                'work directory must be owned RUNNER_TEMP output')
        self.command_lock = threading.Lock()
        self.module_lock = threading.RLock()
        self.input_lock = threading.RLock()
        for key in ('assembly_root', 'wheel_root', 'runtime_root', 'rust_tools_root', 'cargo_root'):
            value = Path(self.data[key]).absolute()
            require(temporary in value.parents and '..' not in value.parts and value.is_dir()
                    and not any(item.is_symlink() for item in (value, *value.parents)),
                    'regular hosted input root required: ' + key)
        guard = self.module('dependency_guard', DEPENDENCY_GUARD)
        self.candidate_admission = guard.verify_candidate_inputs(self.repo, self.helper_revision,
            self.candidate_revision, Path(self.data['assembly_root']))
        require(self.candidate_admission.get('helper_revision') == self.helper_revision
                and self.candidate_admission.get('candidate_revision') == self.candidate_revision,
                'candidate/helper source roles differ from reviewed admission')

    def module(self, key, path):
        with self.module_lock:
            if key not in self.modules:
                self.modules[key] = bound_module(self.sources, '_som_full_' + key, path)
            return self.modules[key]

    def runtime(self):
        if 'runtime' not in self.cache:
            wheels = self.module('wheels', WHEELS)
            sources = wheels.Sources(self.repo, self.helper_revision)
            policy = json.loads(sources.read(wheels.POLICY))
            values = wheels.admitted_runtime(Path(self.data['runtime_root']), sources, policy, self.helper_revision)
            reuse, qualification, inventory, tools, native = values
            self.cache['runtime'] = {'reuse': reuse, 'qualification': qualification, 'inventory': inventory,
                                     'tools': tools, 'identity': native, 'sources': sources}
        return self.cache['runtime']

    def rust_tools(self):
        helper = self.module('rust_tools', RUST_HELPER)
        profile = helper.profile_data(self.sources.read(PROFILE))
        out = Path(self.data['rust_tools_root'])
        if 'rust_tools' not in self.cache:
            self.cache['rust_tools'] = helper.verify_qualification(out, profile, self.helper_revision)
        # This rehashes each real executable immediately before its selected use.
        tools = helper.verify_tools(out, profile)
        require(set(tools) == {'rustc', 'cargo', 'rustfmt', 'cargo-fmt', 'cargo-clippy', 'clippy-driver'},
                'complete six-tool native profile required')
        return tools

    def verify_postgres(self):
        runtime = self.runtime()
        root = Path(self.data['runtime_root'])
        for name in ('postgres', 'initdb', 'pg_ctl', 'createdb'):
            relative_path = 'install/postgresql/bin/' + name
            require(digest(read(root / relative_path)) == runtime['inventory'][relative_path]['sha256'],
                    'qualified PostgreSQL binary changed')
        return root / 'install/postgresql/bin'

    def python_rows(self):
        if 'python_rows' not in self.cache:
            q = self.module('python_inputs', CONSUMER)
            golden_raw, before_raw = self.sources.read(GOLDENS), self.sources.read(BASELINE)
            require(digest(golden_raw) == GOLDENS_SHA256 and digest(before_raw) == BASELINE_SHA256,
                    'frozen product authorities differ')
            goldens = json.loads(golden_raw)
            artifact = Path(self.data['assembly_root'])
            document = json.loads(read(artifact / 'assembly/manifest.json'))
            checked = q.products(artifact, document, goldens, self.candidate_revision,
                                 digest(golden_raw), digest(before_raw))
            authority = [row for cohort in ('python_public', 'python_legacy') for row in goldens['cohorts'][cohort]]
            rows = []
            for index, (row, entry, golden) in enumerate(zip(checked, document['products']['python'], authority)):
                variant = entry['compile_variants'][0]
                require(variant['compile_variant'] == 'none', 'default Python product proof required')
                prompt = json.loads(read(artifact / variant['request_path']))['bundle']['prompt']
                require(digest(canonical(prompt)) == golden['prompt_sha256'], 'frozen prompt differs')
                files = {item['path']: read(artifact / variant['product_dir'] / item['path']).decode('utf-8')
                         for item in variant['files']}
                require(digest(canonical(files)) == golden['files_map_sha256'], 'whole product map changed')
                rows.append({**row, 'index': index, 'prompt': prompt, 'files': files, 'golden': golden})
            require(len(rows) == 671 and len({(r['cohort'], r['id']) for r in rows}) == 671,
                    'whole ordered 671 Python product set required')
            self.cache['python_rows'] = rows
        return self.cache['python_rows']

    def rust_rows(self):
        if 'rust_rows' not in self.cache:
            root = Path(self.data['assembly_root'])
            document = json.loads(read(root / 'assembly/manifest.json'))
            authority = json.loads(self.sources.read(GOLDENS))['cohorts']
            expected = [{**row, 'cohort': name} for name in ('rust_phase1', 'rust_phase2') for row in authority[name]
                        if row.get('files_map_sha256')]
            actual = document['products']['rust']
            require(len(actual) == len(expected) == 697, 'complete 697 assembled Rust products required')
            rows = []
            for row, golden in zip(actual, expected):
                require((row['cohort'], row['id']) == (golden['cohort'], golden['id'])
                        and (golden.get('prompt_sha256') is None or row['prompt_sha256'] == golden['prompt_sha256'])
                        and row['state'] == 'passed', 'ordered frozen Rust identity differs')
                files = {item['path']: read(root / relative(row['product_dir']) / relative(item['path'])).decode()
                         for item in row['files']}
                require(digest(canonical(files)) == golden['files_map_sha256'] == row['actual_files_map_sha256']
                        and {name: digest(text.encode()) for name, text in files.items()} ==
                            {item['path']: item['sha256'] for item in row['files']},
                        'whole frozen Rust file map differs')
                request = json.loads(read(root / relative(row['request_path'])))
                prompt = request['bundle']['prompt']
                require(digest(canonical(prompt)) == row['prompt_sha256'], 'saved Rust prompt differs')
                rows.append({**row, 'files': files, 'prompt': prompt, 'golden': golden})
            self.cache['rust_rows'] = rows
        return self.cache['rust_rows']

    def recorded_files(self, prompt, target, identity=None):
        require(self.data.get('transport') == 'recorded-assembled-products', 'recorded transport not selected')
        key = digest(canonical(prompt))
        require(target in {'python', 'rust'}, 'recorded product target differs')
        with self.input_lock:
            rows = self.python_rows() if target == 'python' else self.rust_rows()
        matches = [row for row in rows if digest(canonical(row['prompt'])) == key
                   and (identity is None or row['id'] == identity)]
        require(matches and len({digest(canonical(row['files'])) for row in matches}) == 1,
                'no exact recorded prompt and whole-product association: ' + str(identity or key))
        record = {'target': target, 'requested_id': identity, 'prompt_sha256': key,
                  'files_map_sha256': digest(canonical(matches[0]['files'])),
                  'saved_named_rows': [{'cohort': row['cohort'], 'id': row['id']} for row in matches],
                  'candidate_revision': self.candidate_revision, 'compiler_request_performed': False}
        directory = self.work / 'recorded-input-reads'
        directory.mkdir(exist_ok=True)
        with self.command_lock:
            index = self.cache.get('recorded_read_sequence', 0)
            self.cache['recorded_read_sequence'] = index + 1
        (directory / f'{os.getpid()}-{index:06d}.json').write_bytes(canonical(record) + b'\n')
        return dict(matches[0]['files'])

    def legacy_cases(self):
        rows = [row for row in self.python_rows() if row['cohort'] == 'python_legacy']
        require(len(rows) == 544, 'whole 544 legacy input cohort required')
        return [{'id': row['id'], 'group': 'legacy:' + row['golden']['group'],
                 'prompt_sha256': row['golden']['prompt_sha256'], 'prompt': row['prompt'], 'cnl': None}
                for row in rows]

    def phase2_cases(self, checker):
        inventory = json.loads(self.sources.read('som-rs/contracts/rust-output-v1/phase2/case-inventory.json'))
        py = self.python_rows()
        out = []
        for expected in inventory['rows']:
            matches = [row for row in py if row['golden']['prompt_sha256'] == expected['prompt_sha256']
                       and row['golden']['files_map_sha256'] == expected['product_sha256']]
            if expected['family'] != 'rs':
                matches = [row for row in matches if row['id'] == expected['id']]
            prompt = matches[0]['prompt'] if matches else self.native_prompt(expected)
            require(digest(canonical(prompt)) == expected['prompt_sha256'], 'phase2 frozen prompt differs')
            def missing(identity=expected['id']):
                raise Refusal('required historical/fixed-before Python file authority missing: ' + identity)
            files = matches[0]['files'] if matches else None
            row = {**expected, 'load_prompt': lambda prompt=prompt: prompt,
                   'load_files': (lambda files=files: files) if files is not None else missing}
            require(prompt['version'] == expected['prompt_version'], 'phase2 prompt version differs')
            row['capabilities'] = bool(prompt.get('capabilities'))
            actual_checker, standard = checker(row)
            require((actual_checker, standard) == (expected['checker'], expected['standard']),
                    'phase2 dispatch differs from frozen inventory')
            row['checker'], row['standard'] = actual_checker, standard
            row['hosted_input_role'] = 'exact_named_or_duplicate_prompt_and_product_hash_match' if files else 'required_authority_missing'
            out.append(row)
        require(len(out) == 584 and [r['id'] for r in out] == [r['id'] for r in inventory['rows']],
                'whole ordered phase2 inventory required')
        return out

    def native_prompt(self, expected):
        # The saved complete compile proof retains every exact typed request,
        # including both current refusals. No simulated compiler is involved.
        root = Path(self.data['assembly_root'])
        manifest = json.loads(read(root / 'manifest.json'))
        require(manifest.get('tested_sha') == self.candidate_revision
                and manifest.get('experiment_complete') is True and manifest.get('passed') == 2041,
                'same-candidate complete saved compile proof required')
        matches = [row for row in manifest['rows'] if row.get('target') == 'rust'
                   and row.get('identity') == {'phase': 'phase2', 'id': expected['id']}]
        require(len(matches) == 1, 'native paired prompt input is missing')
        row = matches[0]
        require(type(row['index']) is int and 0 <= row['index'] < 2041 and row['state'] == 'passed',
                'saved native compile row differs')
        body = read(root / 'rows' / f'{row["index"]:04d}' / 'request.raw')
        require(digest(body) == row['expected_request_sha256'],
                'native typed input bytes changed')
        request = json.loads(body)
        require(request.get('format') == 'som.prompt-json.v1' and request.get('target') == 'rust',
                'native typed input envelope differs')
        require(digest(canonical(request['prompt'])) == expected['prompt_sha256'], 'frozen native prompt hash differs')
        return request['prompt']

    def closure(self, identity):
        root = Path(self.data['wheel_root'])
        document = json.loads(read(root / 'receipt.json'))
        require(document.get('version') == 'som.hosted-python-wheel-closure-qualification.v1'
                and document.get('wheel_closure_qualification_complete') is True
                and document.get('all_named_environment_checks_complete') is True,
                'complete current-helper wheel qualification required')
        dependency_identity(document, self.helper_revision, self.candidate_revision, self.candidate_admission)
        environments = document['environments']
        selected = [row for row in environments if row['id'] == identity]
        require(len(selected) == 1 and selected[0]['state'] == 'offline-environment-qualified',
                'own named wheel environment is not qualified')
        entry = selected[0]
        rows = [row for row in document['closures'] if row['id'] == entry['dependency_set']]
        require(len(rows) == 1 and rows[0]['state'] == 'wheel-closure-qualified', 'complete wheel closure missing')
        record = rows[0]
        directory = root / 'closures' / relative(record['id'])
        require(json.loads(read(directory / 'receipt.json')) == record, 'wheel closure receipt differs')
        p = self.module('wheels', WHEELS)
        audits = {row['name']: json.loads(read(directory / 'wheel-audits' / row['name'] / 'inventory.json'))
                  for row in record['wheels']}
        closure = {'root': directory, 'cache': directory / 'cache', 'wheels': directory / 'wheels',
                   'pins': p.locked_requirements(read(directory / 'requirements.selected.lock')),
                   'record': record, 'audits': audits}
        p.verify_closure(closure)
        self.cache.setdefault('used_closures', {})[record['id']] = closure
        project = root / 'environments' / relative(identity)
        receipt = json.loads(read(project / 'receipt.json'))
        require(receipt.get('state') == 'offline-environment-qualified'
                and receipt.get('uv_lock_sha256') == digest(read(project / 'uv.lock')),
                'own frozen project lock is missing or changed')
        require(receipt.get('project_inputs_sha256') == digest(read(project / 'pyproject.toml')),
                'own qualified project bytes differ')
        return closure, project, receipt

    def environment_identity(self, files):
        if 'pyproject.toml' not in files:
            return 'observer'
        matches = [row for row in self.python_rows() if row.get('pyproject_bytes') == files['pyproject.toml']]
        require(matches, 'own project is not in the complete frozen product scope')
        # Identical project bytes have identical declared groups and extras.
        # Each actual product still receives its own cache and venv.
        return 'product-' + f'{matches[0]["index"]:04d}'

    def command(self, argv, cwd, env, label, timeout=1800, check=True):
        directory = self.work / 'environment-commands'
        directory.mkdir(parents=True, exist_ok=True)
        with self.command_lock:
            sequence = self.cache.get('command_sequence', 0)
            self.cache['command_sequence'] = sequence + 1
        prefix = f'{os.getpid()}-{sequence:06d}-{label}'
        stdout, stderr = directory / (prefix + '.stdout'), directory / (prefix + '.stderr')
        public_env = {key: ('[redacted transport token]' if key == 'SOM_DEPENDENCY_ACTIONS_TOKEN' else value)
                      for key, value in env.items()}
        record = {'argv': list(map(str, argv)), 'cwd': str(cwd), 'environment': public_env,
                  'started_monotonic': time.monotonic(), 'exit_code': None, 'wait_completed': False}
        self.cache.setdefault('commands', []).append(record)
        child = None
        try:
            with stdout.open('xb') as output, stderr.open('xb') as errors:
                child = subprocess.Popen(record['argv'], cwd=cwd, env=env, stdout=output, stderr=errors,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
                record['pid'] = child.pid
                try:
                    record['exit_code'] = child.wait(timeout=timeout)
                    record['wait_completed'] = True
                except BaseException:
                    try: os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: child.wait(timeout=90)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        child.wait(timeout=30)
                    record['wait_completed'] = child.poll() is not None
                    raise
        finally:
            record['elapsed_seconds'] = time.monotonic() - record['started_monotonic']
            record['exit_code'] = child.poll() if child is not None else None
            if child is not None:
                p = self.module('wheels', WHEELS)
                record['owned_group_cleanup_passed'] = p.stop_owned_descendants(child.pid)
            for key, path in (('stdout', stdout), ('stderr', stderr)):
                if path.exists():
                    record[key] = path.relative_to(self.work).as_posix()
                    record[key + '_sha256'] = digest(read(path, 128 * 1024 * 1024))
            (directory / (prefix + '.json')).write_bytes(canonical(record) + b'\n')
        require(record.get('owned_group_cleanup_passed') and record['wait_completed'],
                'full product command wait or owned group cleanup missing: ' + label)
        if check:
            require(record['exit_code'] == 0, 'full product command failed: ' + label)
        return record

    def capture(self, argv, cwd, env, timeout):
        result = self.command(argv, cwd, env, 'frozen-product-command', timeout, check=False)
        return result['exit_code'], (read(self.work / result['stdout'], 128 * 1024**2)
                                     + read(self.work / result['stderr'], 128 * 1024**2)).decode('utf-8', 'replace')

    def prepare_python(self, root, files=None):
        root = Path(root).absolute()
        require(self.work in root.parents and root.is_dir() and not root.is_symlink(),
                'owned product root required')
        if files is None:
            project_path = root / 'pyproject.toml'
            files = {'pyproject.toml': read(project_path).decode()} if project_path.exists() else {}
        identity = self.environment_identity(files)
        if identity == 'observer' and root != self.work / 'observer':
            return observer_python()
        previous = self.cache.get('product_environments', {}).get(str(root))
        if previous:
            require(digest(read(root / 'uv.lock')) == previous['lock_sha256']
                    and Path(previous['python']).resolve() == self.runtime()['tools']['python'].resolve(),
                    'owned product environment changed on repeated preparation')
            return Path(previous['python'])
        closure, original, receipt = self.closure(identity)
        expected_project = read(original / 'pyproject.toml')
        project = root / 'pyproject.toml'
        if project.exists():
            require(read(project) == expected_project, 'actual product project bytes differ from its wheel closure')
        else:
            require(identity == 'observer', 'cannot invent a product project')
            project.write_bytes(expected_project)
        lock = root / 'uv.lock'
        frozen_lock = read(original / 'uv.lock')
        if lock.exists(): require(read(lock) == frozen_lock, 'existing product lock differs')
        else: lock.write_bytes(frozen_lock)
        key = digest(str(root).encode())
        cache = self.work / 'product-caches' / key
        cache.mkdir(parents=True, exist_ok=False)
        runtime = self.runtime()
        python, uv = runtime['tools']['python'], runtime['tools']['uv']
        venv = root / '.venv'
        require(not venv.exists() and not venv.is_symlink(), 'full product environment must start empty')
        env = self.child_environment()
        # Preserve the product's ordinary uv mode. Do not propagate the separate
        # qualification-only UV_NO_INSTALLER_METADATA switch.
        env.update(UV_CACHE_DIR=str(cache), UV_PROJECT_ENVIRONMENT=str(venv), UV_OFFLINE='1',
                   UV_PYTHON_DOWNLOADS='never', UV_PYTHON=str(python), UV_NO_BUILD='1',
                   UV_NO_INDEX='1', UV_FIND_LINKS=str(closure['wheels']))
        require('UV_NO_INSTALLER_METADATA' not in env, 'qualification metadata mode leaked to full product')
        p = self.module('wheels', WHEELS)
        for directory in ('logs', 'home', 'tmp'):
            (self.work / directory).mkdir(exist_ok=True)
        assets = p.installer_assets(self.sources.read(p.INSTALLER_AUTHORITY))
        preparation = {'commands': []}
        seed_runner = p.Runner(self.work, preparation, runtime['tools'], runtime['identity'], assets)
        p.prepare_frozen_url_cache(seed_runner, closure, root, cache,
                                   self.work / 'cache-preparation' / key, 'cache-' + key)
        require(not venv.exists() and 'UV_NO_INSTALLER_METADATA' not in env,
                'cache seed must not become the ordinary product environment')
        self.command([uv, 'sync', '--python', '3.12'], root, env, 'ordinary-offline-uv-sync')
        require(read(project) == expected_project and read(lock) == frozen_lock,
                'ordinary full product sync changed project or its frozen lock')
        executable = venv / 'bin/python'
        require(executable.is_file() and executable.resolve() == python.resolve(),
                'product interpreter is not the admitted base Python')
        self.cache.setdefault('product_environments', {})[str(root)] = {'python': str(executable),
            'environment': env, 'qualification_environment': identity, 'lock_sha256': digest(frozen_lock),
            'default_installer_metadata': True, 'full_product_tests': 'pending'}
        evidence = self.work / 'product-environments' / key
        evidence.mkdir(parents=True)
        p = self.module('wheels', WHEELS)
        (evidence / 'installed-before.json').write_bytes(canonical(p.installed_inventory(venv, self.work)) + b'\n')
        public = dict(self.cache['product_environments'][str(root)])
        public['environment'] = {key: ('[redacted transport token]' if key == 'SOM_DEPENDENCY_ACTIONS_TOKEN' else value)
                                 for key, value in public['environment'].items()}
        (evidence / 'environment.json').write_bytes(canonical(public) + b'\n')
        return executable

    def release_python(self, root, passed):
        root = Path(root).absolute()
        require(type(passed) is bool and self.work in root.parents, 'owned product cleanup and boolean result required')
        selected = self.cache.get('product_environments', {}).get(str(root))
        if not selected:
            return
        key = digest(str(root).encode())
        evidence = self.work / 'product-environments' / key
        venv = root / '.venv'
        p = self.module('wheels', WHEELS)
        after = p.installed_inventory(venv, self.work)
        raw = canonical(after) + b'\n'
        before = read(evidence / 'installed-before.json', 128 * 1024**2)
        (evidence / 'installed-after.json').write_bytes(raw)
        same = raw == before
        record = {'same_installed_files_before_after': same, 'product_gates_passed': passed,
                  'ordinary_installer_scope_independent_audit': 'unrun',
                  'installed_after_sha256': digest(raw), 'owned_venv_removed': False,
                  'owned_cache_removed': False, 'product_acceptance_passed': False}
        if passed and same:
            require(venv.is_dir() and not venv.is_symlink() and Path(selected['python']).resolve()
                    == self.runtime()['tools']['python'].resolve(), 'owned cleanup interpreter changed')
            shutil.rmtree(venv)
            cache = Path(selected['environment']['UV_CACHE_DIR'])
            require(cache == self.work / 'product-caches' / key and not cache.is_symlink(), 'owned cache identity changed')
            shutil.rmtree(cache)
            record.update(owned_venv_removed=True, owned_cache_removed=True)
            self.cache['product_environments'].pop(str(root))
        (evidence / 'cleanup.json').write_bytes(canonical(record) + b'\n')
        require(same, 'ordinary product installed environment changed during product commands')

    def child_environment(self):
        runtime, rust = self.runtime(), self.rust_tools()
        root = Path(self.data['runtime_root'])
        path = [str(rust['cargo'].parent), str(root / 'install/postgresql/bin'),
                str(root / 'install/redis/bin'), str(root / 'install/mongodb/bin'),
                str(runtime['tools']['uv'].parent), str(Path(self.data['observer_python']).parent), '/usr/bin', '/bin']
        environment = {'PATH': os.pathsep.join(path), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                'HOME': str(self.work / 'home'), 'TMPDIR': str(self.work / 'tmp'), 'RUNNER_TEMP': os.environ['RUNNER_TEMP'],
                'GITHUB_ACTIONS': 'true', ENV: str(self.path), 'PYTHONDONTWRITEBYTECODE': '1',
                'PYTHONPATH': str(self.repo / 'som-rs/scripts/hosted_readiness/product_runtime'),
                'CARGO_HOME': str(self.work / 'cargo-home'), 'CARGO_NET_OFFLINE': 'true',
                'RUSTC': str(rust['rustc']), 'CARGO': str(rust['cargo']), 'RUSTFMT': str(rust['rustfmt']),
                'UV_OFFLINE': '1', 'UV_PYTHON_DOWNLOADS': 'never',
                'SOM_PRODUCT_PYTHON': str(runtime['tools']['python']), 'SOM_PRODUCT_UV': str(runtime['tools']['uv'])}
        if os.environ.get('SOM_DEPENDENCY_ACTIONS_TOKEN'):
            environment['SOM_DEPENDENCY_ACTIONS_TOKEN'] = os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN']
        return environment

    def verify(self):
        self.sources.verify()
        # Re-read complete original inputs. Cached text maps cannot prove that
        # the transported source products stayed unchanged during the run.
        self.cache.pop('python_rows', None)
        self.cache.pop('rust_rows', None)
        self.python_rows()
        self.rust_rows()
        guard = self.module('dependency_guard', DEPENDENCY_GUARD)
        current = guard.verify_candidate_inputs(self.repo, self.helper_revision, self.candidate_revision,
                                                Path(self.data['assembly_root']))
        require(current['mode'] == self.candidate_admission['mode']
                and current['helper_revision'] == self.helper_revision
                and current['candidate_revision'] == self.candidate_revision,
                'candidate admission changed during the product run')
        runtime = self.runtime()
        runtime['reuse'].verify_installed_tree(Path(self.data['runtime_root']), runtime['inventory'], runtime['qualification'])
        runtime['sources'].verify()
        self.rust_tools()
        helper = self.module('rust_tools', RUST_HELPER)
        helper.verify_install_tree(Path(self.data['rust_tools_root']),
                                   helper.profile_data(self.sources.read(PROFILE)))
        for closure in self.cache.get('used_closures', {}).values():
            self.module('wheels', WHEELS).verify_closure(closure)
        require(read(self.path) == self.raw, 'hosted context changed during product checks')

    def rust_quality(self, root, env, target_dir):
        root = Path(root)
        tools = self.rust_tools()
        files = sorted(path.relative_to(root).as_posix() for path in root.rglob('*.rs')
                       if 'target' not in path.relative_to(root).parts)
        require(files, 'real Rust product source required')
        original = {name: digest(read(root / name)) for name in files + ['Cargo.toml', 'Cargo.lock']}
        selected = {**env, **rust_environment(), 'CARGO_TARGET_DIR': str(target_dir)}
        self.command([tools['rustfmt'], '--edition', '2024', '--config-path', '/dev/null', '--check', *files],
                     root, selected, 'rustfmt-check')
        self.command([tools['cargo'], 'clippy', '--locked', '--offline', '--all-targets', '--', '-D', 'warnings'],
                     root, selected, 'cargo-clippy')
        require({name: digest(read(root / name)) for name in original} == original,
                'Rust quality command changed assembled source or frozen lock')

    def phase2_result(self, out, every, requested, codes):
        shard = self.data['shard']
        selected = [row['id'] for i, row in enumerate(every) if i % shard['count'] == shard['index']]
        require(requested == selected, 'phase2 shard order or complete named scope differs')
        rows, missing = [], []
        for identity in selected:
            name = re.sub('[^A-Za-z0-9]+', '_', identity).strip('_')
            path = Path(out) / 'cases' / (name + '.json')
            if path.is_file():
                row = json.loads(read(path))
                require(row['id'] == identity, 'phase2 shard named row differs')
                rows.append({'id': identity, 'passed': row['passed'] is True, 'raw_sha256': digest(read(path))})
            else:
                missing.append(identity)
        record = {'version': 'som.full-product-phase2-shard.v1', 'shard': shard,
                  'stage': self.data['stage'], 'helper_revision': self.helper_revision,
                  'candidate_revision': self.candidate_revision,
                  'selected_ids': selected, 'rows': rows, 'missing': missing,
                  'whole_inventory_total': len(every), 'whole_inventory_complete': False,
                  'whole_inventory_sha256': digest(canonical([row['id'] for row in every])),
                  'shard_complete': not missing and len(rows) == len(selected), 'worker_exit_codes': codes,
                  'product_acceptance_passed': False}
        (Path(out) / 'hosted-shard-summary.json').write_bytes(canonical(record) + b'\n')
        return 0 if record['shard_complete'] and all(row['passed'] for row in rows) and not any(codes) else 1


def active():
    return bool(os.environ.get(ENV))


def context():
    global _ACTIVE
    require(active(), 'explicit hosted product context required')
    path = Path(os.environ[ENV]).absolute()
    if _ACTIVE is None:
        _ACTIVE = Context(path)
    require(_ACTIVE.path == path and read(path) == _ACTIVE.raw, 'hosted context identity changed')
    return _ACTIVE


def rust_bin():
    tools = context().rust_tools()
    require(len({path.parent for path in tools.values()}) == 1, 'one admitted Rust bin directory required')
    return tools['cargo'].parent


def rust_environment():
    tools = context().rust_tools()
    root = str(tools['cargo'].parent)
    return {'PATH': root + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin'),
            'RUSTC': str(tools['rustc']), 'CARGO': str(tools['cargo']),
            'RUSTFMT': str(tools['rustfmt']), 'CARGO_NET_OFFLINE': 'true'}


def postgres_bin():
    return context().verify_postgres()


def legacy_cases():
    return context().legacy_cases()


def phase2_cases(checker):
    return context().phase2_cases(checker)


def recorded_files(prompt, target, identity=None):
    return context().recorded_files(prompt, target, identity)


def recorded_transport():
    return context().data.get('transport') == 'recorded-assembled-products'


def phase2_result(out, every, requested, codes):
    return context().phase2_result(out, every, requested, codes)


def rust_quality(root, env, target_dir):
    return context().rust_quality(root, env, target_dir)


def observer_python():
    path = Path(context().data['observer_python']).absolute()
    require(path.is_file() and context().work in path.parents
            and path.resolve() == context().runtime()['tools']['python'].resolve(),
            'owned observer interpreter required')
    return path


def prepare_python(root, files=None):
    return context().prepare_python(root, files)


def release_python(root, passed):
    return context().release_python(root, passed)


def product_environment(root, original):
    selected = context().cache.get('product_environments', {}).get(str(Path(root).absolute()))
    return {**original, **selected['environment']} if selected else original


def tool_command(script, arguments):
    ctx = context()
    script = Path(script).absolute()
    require(ctx.repo in script.parents and script.suffix == '.py', 'fixed repository checker required')
    relative_path = script.relative_to(ctx.repo).as_posix()
    ctx.sources.read(relative_path)
    return [str(observer_python()), '-B', str(ctx.repo / 'som-rs/scripts/hosted_readiness/run_full_products.py'),
            '--execute-tool', relative_path, '--', *arguments]
