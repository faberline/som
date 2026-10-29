#!/usr/bin/env python3
"""Run fresh CNL, CLI, refusal and old-bundle checks on one admitted candidate.

GitHub only. These gate results cannot grant whole-product acceptance.
"""
from __future__ import annotations

import argparse
import __future__
import ast
import builtins
import io
from contextlib import contextmanager, redirect_stdout
import importlib
import importlib.abc
import importlib.util
import tempfile
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import stat
import subprocess
import sys
from types import ModuleType, SimpleNamespace

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_candidate_semantic_gates.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
AUTHORITY = BASE + 'native_qa_authority.py'
VERSION = 'som.same-candidate-semantic-gates.v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def frozen_text(value):
    if value is None: return 'null'
    if type(value) is bool: return 'true' if value else 'false'
    if type(value) is int: return str(value)
    if type(value) is float:
        require(math.isfinite(value), 'nonfinite bundle number')
        text = repr(value)
        if 'e' in text:
            first, exponent = text.split('e', 1)
            sign = '-' if exponent.startswith('-') else '+' if exponent.startswith('+') else ''
            digits = exponent.lstrip('+-')
            text = first + 'e' + sign + (digits.zfill(2) if sign == '-' else digits)
        return text
    if type(value) is str: return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if type(value) is list: return '[' + ','.join(frozen_text(item) for item in value) + ']'
    require(type(value) is dict and all(type(k) is str for k in value), 'invalid bundle JSON value')
    return '{' + ','.join(frozen_text(k) + ':' + frozen_text(value[k]) for k in sorted(value)) + '}'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def read(path, limit=128 * 1024**2):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked path refused')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, 'bounded single-link file required')
        raw = stream.read(limit + 1)
    require(len(raw) == info.st_size and len(raw) <= limit, 'file length changed')
    return raw


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def bootstrap(repo, revision):
    require(re.fullmatch('[0-9a-f]{40}', revision) is not None, 'full helper revision required')
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                       env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                                            'GIT_CONFIG_GLOBAL': '/dev/null'}, timeout=30)
    require(git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == revision, 'helper HEAD differs')
    for name in (SELF, CONTEXT):
        entry = git('ls-tree', '-z', revision, '--', name)
        require(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
                and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular committed bootstrap required')
        require(read(repo / name) == git('cat-file', 'blob', revision + ':' + name), 'bootstrap bytes differ from Git')
    raw = read(repo / CONTEXT)
    require('full_product_context' not in sys.modules, 'unbound context preload refused')
    module = ModuleType('full_product_context')
    module.__file__, module.__package__, module.__cached__ = str(repo / CONTEXT), '', None
    module.__som_raw_sha256__ = sha(raw)
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


def path_set(sources):
    raw = sources.git('ls-tree', '-r', '-z', '--name-only', sources.revision)
    require(raw.endswith(b'\0'), 'complete tracked source path set required')
    names = raw[:-1].decode('utf-8', 'strict').split('\0')
    require(len(names) == len(set(names)), 'duplicate tracked source path')
    return set(names)


def rebase(original, current, value_sha):
    require(set(original['assets']) == set(current['assets']), 'asset descriptor universe changed')
    allowed = {'backend', 'formatter', 'target_contract'} if current.get('target') == 'rust' else {'backend', 'formatter'}
    for name in original['assets']:
        old, new = original['assets'][name], current['assets'][name]
        if old != new:
            require(name in allowed and set(old) == set(new) == {'id', 'sha256'}
                    and old['id'] == new['id'], 'unadmitted descriptor field changed')
    result = copy.deepcopy(original)
    result['assets'] = copy.deepcopy(current['assets'])
    result['hashes']['payload_sha256'] = value_sha({key: value for key, value in result.items() if key != 'hashes'})
    require(result == current, 'rebase changed fields outside admitted descriptors and payload hash')
    return result


class RawHttp:
    def __init__(self, service, driver, out):
        self.service, self.driver, self.out, self.rows = service, driver, out, []

    def post(self, base, body, route='/v1/compile'):
        require(base == f'http://127.0.0.1:{self.service.port}', 'foreign QA endpoint refused')
        require(route in ('/v1/compile', '/v1/assemble'), 'foreign QA route refused')
        raw = body.encode() if isinstance(body, str) else body
        index = len(self.rows)
        directory = self.out / 'http' / f'{index:05d}'
        directory.mkdir(parents=True)
        (directory / 'request.raw').write_bytes(raw)
        if route == '/v1/compile':
            status, reply, owner = self.service.call(route, raw)
        else:
            import urllib.error
            import urllib.request
            owner = self.service.ownership()
            request = urllib.request.Request(base + route, data=raw, headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with self.service.opener.open(request, timeout=120) as response:
                    status, reply = response.status, response.read()
            except urllib.error.HTTPError as error:
                with error:
                    status, reply = error.code, error.read()
        (directory / 'response.raw').write_bytes(reply)
        row = {'index': index, 'route': route, 'status': status, 'owned_socket': owner,
               'request_sha256': sha(raw), 'response_sha256': sha(reply)}
        save(directory / 'result.json', row)
        self.rows.append(row)
        return status, reply


class RawCli:
    """Retain the original checker's complete child streams and owned wait."""
    def __init__(self, ctx, binary, out, http):
        self.ctx, self.binary, self.out, self.rows, self.http = ctx, str(binary), out, [], http

    def run(self, argv, *, capture_output, timeout):
        require(capture_output is True and timeout == 120 and argv[0] == self.binary
                and argv[1] == 'compile', 'unexpected frozen CLI command')
        directory = self.out / 'cli' / f'{len(self.rows):04d}'
        directory.mkdir(parents=True)
        (directory / 'source.cnl.raw').write_bytes(read(Path(argv[2])))
        http_index = len(self.http.rows)
        record = self.ctx.command(argv, directory, {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                                  'frozen-cli', timeout=timeout, check=False)
        require(len(self.http.rows) == http_index + 1, 'CLI must send exactly one captured compile request')
        stdout = read(self.ctx.work / record['stdout'])
        stderr = read(self.ctx.work / record['stderr'])
        (directory / 'stdout.raw').write_bytes(stdout)
        (directory / 'stderr.raw').write_bytes(stderr)
        require(record['wait_completed'] is True and record['owned_group_cleanup_passed'] is True,
                'CLI wait or owned group cleanup incomplete')
        save(directory / 'command.json', record)
        self.rows.append({'argv': list(argv), 'http_request_index': http_index, 'exit_code': record['exit_code'], 'wait_completed': True,
                          'owned_group_cleanup_passed': True, 'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr)})
        return subprocess.CompletedProcess(argv, record['exit_code'], stdout, stderr)


def assembly_rows(root, revision):
    raw = read(root / 'assembly/manifest.json')
    value = json.loads(raw)
    require(value['tested_sha'] == revision and value['assembly_experiment_complete'] is True
            and value['observations_passed'] == 2045 and value['owned_cleanup']['cleanup_passed'] is True,
            'complete same-candidate assembly proof required')
    rows = {}
    for item in value['observations']:
        if item['mode'] != 'compile-default':
            continue
        key = item['compile_row_index']
        require(key not in rows and item['state'] == 'passed' and item['http_status'] == 200, 'duplicate or failed normal assembly')
        rows[key] = item
    require(len(rows) == 2039, 'all 2039 successful typed compile assemblies required')
    return rows, sha(raw)


def old_bundles(jobs, proof, checker, driver, http, assembly_root, revision):
    expected, manifest_sha = assembly_rows(assembly_root, revision)
    rows = []
    for index, job in enumerate(jobs):
        if job['expected_http_status'] != 200:
            continue
        typed = read(proof.root / f'rows/{index:04d}/response.raw')
        checked = checker.verify_response(job['target'], job['identity'], 200, typed)
        original = checked.original_bundle
        require(original is not None, 'verified old compile bundle unavailable')
        status, raw = http.post(f'http://127.0.0.1:{http.service.port}', driver.compact({'bundle': original}), '/v1/assemble')
        require(status == 400 and json.loads(raw)['code'] == 'ASSET_HASH_MISMATCH', 'old bundle was not refused as specified')
        old_http_index = len(http.rows) - 1
        current = json.loads(typed)['bundle']
        rebased = rebase(original, current, driver.experiment.value_sha)
        status, raw = http.post(f'http://127.0.0.1:{http.service.port}', driver.compact({'bundle': rebased}), '/v1/assemble')
        recorded = expected[index]
        require(recorded['identity'] == job['identity'] and recorded['target'] == job['target'], 'assembly identity differs')
        previous = read(assembly_root / recorded['response_path'])
        require(sha(previous) == recorded['response_sha256'] and status == 200 and raw == previous,
                'rebased assembly differs from the exact same-candidate proven response bytes')
        require(driver.experiment.value_sha(json.loads(raw)['files']) == recorded['actual_files_map_sha256'],
                'rebased complete file map differs')
        rows.append({'compile_row_index': index, 'old_http_index': old_http_index,
                     'rebased_http_index': len(http.rows) - 1, 'target': job['target'], 'identity': job['identity'], 'passed': True,
                     'old_bundle_refused': True, 'rebased_response_sha256': sha(raw),
                     'files_map_sha256': recorded['actual_files_map_sha256']})
    require(len(rows) == 2039 and Counter(row['target'] for row in rows) == {'python': 1342, 'rust': 697},
            'complete named old-bundle/rebase scope differs')
    return {'rows': rows, 'assembly_manifest_sha256': manifest_sha, 'complete': True}


def frozen_cli_refusals(ctx, driver, service, http, binary, out):
    cli = ctx.module('semantic_cli', 'som-rs/scripts/rust_output_acceptance/check_cli.py')
    commands = RawCli(ctx, binary, out, http)
    original_check = cli.check
    def capture_check(binary, base_url, scratch, label, source, target, expect=None, verify_request=False):
        return original_check(binary, base_url, scratch, label, source, target, expect, verify_request=True)
    cli.check = capture_check
    cli.subprocess = SimpleNamespace(run=commands.run)
    cli.post = http.post
    cli_path = out / 'frozen-cli.json'
    base = f'http://127.0.0.1:{service.port}'
    status = cli.main(['--binary', str(binary), '--base-url', base, '--out', str(cli_path), '--refusals', 'phase2'])
    report = json.loads(read(cli_path))
    require(status == 0 and not report['failed'] and report['passed'] == report['total'] == len(commands.rows)
            and report['refusals'] > 0 and report['refusals_exit_2'] == report['refusals'], 'frozen CLI gate failed')
    rust = ctx.module('semantic_rust_products', 'som-rs/som-targets/qa/check_rust_products.py')
    rust.targets.post = http.post
    v1 = json.loads(ctx.sources.read('som-rs/contracts/compiler-v1/examples/v1.prompt.json'))
    code, raw = http.post(base, rust.targets.dump({'format': 'som.prompt-json.v1', 'target': 'rust', 'prompt': v1}))
    require(code == 200, 'Rust refusal base did not compile')
    rows = rust.phase2_refusals(base, json.loads(raw)['bundle'])
    frozen = rust.PLAN['assemble_refusals']['recipes']
    expected_ids = [item['id'] for item in frozen]
    require(set(expected_ids) == {f'A{i}' for i in range(1, 10)}, 'frozen refusal recipe scope changed')
    required = {f'A{i}' for i in range(1, 10)} | {f'A{i}_PUBLIC_v6' for i in (1, 2, 4, 5, 7, 9)}
    require(len(rows) == len(required) and {row['id'] for row in rows} == required
            and all(row['passed'] is True for row in rows), 'complete frozen phase2 assemble refusals failed')
    save(out / 'frozen-assemble-refusals.json', {'rows': rows})
    return {'cli': report, 'cli_commands': commands.rows, 'assemble_refusals': rows, 'complete': True,
            'transport': 'unchanged frozen checker request-capture path applied to all CLI cases'}


def verify_server_events(log_path, root, rows, port):
    events = [json.loads(line) for line in read(log_path).splitlines() if line.startswith(b'{')]
    require(events and events[0] == {'stage': 'listening', 'address': f'127.0.0.1:{port}',
                                    'purpose': 'som.rust-compiler.v1'}, 'owned listening event differs')
    expected = []
    for row in rows:
        directory = root / 'http' / f"{row['index']:05d}"
        request = json.loads(read(directory / 'request.raw'))
        reply = json.loads(read(directory / 'response.raw'))
        if row['route'] == '/v1/compile':
            bundle = reply.get('bundle')
            expected.append({'stage': 'compile', 'http_status': row['status'],
                             'bundle_bytes': len(frozen_text(bundle).encode()) if bundle is not None else None})
        elif request.get('bundle', {}).get('version') == 'som.dsl-bundle.v2':
            target = request['bundle'].get('target')
            expected.append({'stage': 'assemble', 'target': target if isinstance(target, str) else 'rust',
                             'http_status': row['status']})
        elif row['status'] == 200:
            expected.append({'stage': 'assemble', 'formatted_bytes': sum(len(s.encode()) for s in reply['files'].values())})
    require(len(events) == len(expected) + 1, 'server event universe differs from all captured exchanges')
    for event, wanted in zip(events[1:], expected):
        require(set(event) == set(wanted) | {'total_ms'} and type(event['total_ms']) in (int, float)
                and math.isfinite(event['total_ms']) and event['total_ms'] >= 0
                and {key: value for key, value in event.items() if key != 'total_ms'} == wanted,
                'ordered server event differs from raw request and response')
    return {'raw_sha256': sha(read(log_path)), 'events': len(events), 'captured_http_exchanges': len(rows)}


def execute(args, module, ctx, authority, admission):
    sources = ctx.sources
    paths = path_set(sources)
    require(args.port == 18979, 'dedicated semantic QA port18979 required')
    carrier = ctx.module('semantic_carrier', BASE + 'product_runtime/candidate_carrier.py')
    restored = carrier.restore(args.carrier_root, args.out / 'restored-candidate',
                               args.assembly_root / 'manifest.json', args.candidate_revision)
    cnl = ctx.module('semantic_cnl', BASE + 'run_experimental_native_cnl.py')
    if args.gate == 'cnl':
        argv = ['--repo', str(args.repo), '--python-inputs', ctx.data['python_inputs_root'],
                '--rust-inputs', ctx.data['rust_inputs_root'], '--typed-proof', str(args.assembly_root),
                '--binary', str(restored['binary']), '--assets', str(restored['assets']),
                '--out', str(args.out / 'cnl'), '--tested-sha', args.candidate_revision, '--port', '18977',
                '--carrier-root', str(args.carrier_root)]
        for key, flag in (('SOM_RUFF', 'ruff'), ('SOM_RUSTFMT', 'rustfmt'), ('SOM_TS_NODE', 'node'), ('SOM_TS_PRETTIER', 'prettier')):
            argv.extend(['--' + flag, str(restored['tools'][key])])
        command = ctx.command([str(module.observer_python()), '-I', '-S', '-B', str(args.repo / (BASE + 'run_experimental_native_cnl.py')), *argv],
                              args.out, ctx.child_environment(), 'same-candidate-cnl2050', timeout=20000, check=False)
        status = command['exit_code']
        for stream in ('stdout', 'stderr'):
            (args.out / ('cnl-command.' + stream + '.raw')).write_bytes(read(ctx.work / command[stream]))
        save(args.out / 'cnl-command.json', command)
        result = json.loads(read(args.out / 'cnl/manifest.json'))
        require(status == 0 and result['experiment_complete'] is True and result['passed'] == 2050
                and result['actual_rows'] == cnl.EXPECTED and result['cleanup']['cleanup_passed'] is True,
                'complete CNL2050 gate failed')
        return {'cnl_manifest_sha256': sha(read(args.out / 'cnl/manifest.json')), 'complete': True}
    # The raw source finder binds these transitive imports before execution.
    import importlib
    sys.path.insert(0, str(args.repo / BASE))
    driver = importlib.import_module('run_experimental_native_compile')
    complete = importlib.import_module('complete_rust_inputs')
    checker = driver.experiment.ExperimentalChecker(args.repo)
    jobs, reused, completed, inputs, rust_tree = cnl.frozen_inputs(driver, complete, checker, args.repo,
        Path(ctx.data['python_inputs_root']), Path(ctx.data['rust_inputs_root']), args.out, args.candidate_revision)
    proof = cnl.TypedProof(args.assembly_root, jobs, checker, driver, args.candidate_revision, inputs)
    service_root = args.out / 'service'
    service_root.mkdir()
    service = driver.OwnedService(restored['binary'], restored['assets'], restored['tools'], args.port, service_root)
    for name, pin in proof.receipt['initial_identities']['source'].items():
        require(sha(checker.repo.read(name)) == pin, 'typed owning source differs before semantic service')
    result = {'capture_root': str(args.out), 'initial_identities': cnl.identities(driver, checker, restored['binary'], restored['assets'], restored['tools']),
              'launch': {'argv': service.argv, 'environment': service.environment}}
    with driver.cancellation_handlers() as cancellation:
        try:
            result['owned_start'] = service.start()
            status, health_raw, owner = service.call('/healthz')
            (args.out / 'health.response.raw').write_bytes(health_raw)
            health = json.loads(health_raw)
            require(status == 200 and health['status'] == 'ok' and health['model_loaded'] is False
                    and health['blocks_loaded'] is True and health['backend_sha256'] == checker.after['python_backend']
                    and health['asset_manifest_sha256'] == driver.asset_manifest_canonical_sha(restored['assets'])
                    and health['formatter_sha256'] == restored['identity']['tools']['SOM_RUFF']['sha256'],
                    'owned health differs from admitted source factories and tools')
            result['health'] = {'status': status, 'sha256': sha(health_raw), 'owner': owner}
            http = RawHttp(service, driver, args.out)
            result['cli_refusals'] = frozen_cli_refusals(ctx, driver, service, http, restored['binary'], args.out)
            result['old_bundle_rebase'] = old_bundles(jobs, proof, checker, driver, http, args.assembly_root, args.candidate_revision)
            result['http_rows'] = http.rows
        finally:
            cancellation['cleanup'] = True
            result['cleanup'] = service.close()
            save(args.out / 'owned-process.json', result)
        require(cancellation['signal'] is None and result['cleanup']['cleanup_passed'] is True, 'owned server cleanup failed')
    result['server_events'] = verify_server_events(service_root / 'server.log', args.out, http.rows, args.port)
    proof.verify()
    reused.verify()
    require(cnl.tree_hashes(Path(ctx.data['rust_inputs_root'])) == rust_tree, 'Rust inputs changed')
    require(carrier.identities(restored['root']) == restored['identity'], 'restored candidate changed')
    result['final_identities'] = cnl.identities(driver, checker, restored['binary'], restored['assets'], restored['tools'])
    require(result['initial_identities'] == result['final_identities'], 'semantic captured identities changed')
    return result


def file_inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'linked or special gate output')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(read(path, 300 * 1024**2))
    return result


def cli_scope(source_reader):
    cases_root = 'som-rs/tests/rust-output-v1/cases/'
    cases = json.loads(source_reader(cases_root + 'index.json'))['cases']
    negatives = json.loads(source_reader('som-rs/contracts/rust-output-v1/negative-cases.json'))['cases']
    rows = []
    for case in cases:
        raw = source_reader(cases_root + case['cnl'])
        rows.extend((case['id'], target, raw) for target in (None, 'python', 'rust'))
    for case in negatives:
        body = json.loads(case['body'])
        if case['form'] == 'cnl' and isinstance(body.get('target'), str) and isinstance(body.get('source'), str):
            rows.append((case['id'], body['target'], body['source'].encode()))
    first = cases[0]
    first_raw = source_reader(cases_root + first['cnl'])
    for target in ('RUST', '', 'go', ' rust', 'python3'):
        rows.append((f"{first['id']} unknown {target!r}", target, first_raw))
    rows.append((f"{first['id']} target 'typescript'", 'typescript', first_raw))
    inventory = json.loads(source_reader('som-rs/contracts/typescript-output-v1/case-inventory.json'))
    outside = next(item for item in inventory['public_examples'] if item.get('typescript') != 'in_scope' and item.get('cnl'))
    rows.append((f"{outside['id']} target 'typescript' refusal", 'typescript', source_reader(outside['cnl'])))
    return rows


class JoinSources:
    """Fresh raw Git authority. A callback reader cannot select the source bytes."""
    def __init__(self, repo, revision, reader):
        self.root, self.revision, self.reader, self.pins = Path(repo).absolute(), revision, reader, {}
        require(re.fullmatch('[0-9a-f]{40}', revision) is not None, 'complete verifier source revision required')
        require(self.root.is_dir() and not any(p.is_symlink() for p in (self.root, *self.root.parents)),
                'regular verifier source root required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision
                and self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root), 'verifier Git root or HEAD differs')
        self.read(SELF)

    def git(self, *arguments):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}, timeout=30)

    def read(self, name):
        safe_member(name)
        entry = self.git('ls-tree', '-z', self.revision, '--', name)
        require(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
                and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular committed verifier source required')
        raw = self.git('cat-file', 'blob', self.revision + ':' + name)
        require(len(raw) <= 32 * 1024**2 and read(self.root / name, 32 * 1024**2) == raw
                and self.reader(name) == raw, 'verifier source reader or working bytes differ from exact Git')
        require(name not in self.pins or self.pins[name] == sha(raw), 'verifier source changed during use')
        self.pins[name] = sha(raw)
        return raw

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'verifier source HEAD changed')
        for name in list(self.pins): self.read(name)


def safe_member(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(part not in {'', '.', '..'} for part in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'safe relative proof member required')
    return name


def parse_saved(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            require(name not in result, 'duplicate saved JSON member')
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite saved JSON member')))


def fresh_module(sources, name, path):
    require(name not in sys.modules, 'preloaded verifier project module refused: ' + name)
    raw = sources.read(path)
    module = ModuleType(name)
    module.__file__, module.__package__, module.__cached__ = str(sources.root / path), '', None
    module.__som_raw_sha256__ = sha(raw)
    sys.modules[name] = module
    try: exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class JoinModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, sources, paths):
        self.sources, self.paths, self.loaded = sources, paths, set()
        require(not (set(paths) & set(sys.modules)), 'preloaded named verifier helper refused')
        self.raw = {name: sources.read(path) for name, path in paths.items()}

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.paths:
            require(target is None, 'verifier helper reload refused')
            return importlib.util.spec_from_loader(fullname, self, origin=str(self.sources.root / self.paths[fullname]))
        return None

    def create_module(self, spec): return ModuleType(spec.name)

    def exec_module(self, module):
        require(module.__name__ not in self.loaded, 'verifier helper executed twice')
        raw, path = self.raw[module.__name__], self.paths[module.__name__]
        module.__file__, module.__package__, module.__cached__ = str(self.sources.root / path), '', None
        module.__som_raw_sha256__ = sha(raw)
        self.loaded.add(module.__name__)
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)

    def __enter__(self):
        self.previous_sys_path = list(sys.path)
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *errors):
        sys.meta_path.remove(self)
        sys.path[:] = self.previous_sys_path
        for name in self.loaded: sys.modules.pop(name, None)


def proof_roots_data(roots, saved_root, repo):
    required = {'assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'}
    require(type(roots) is dict and set(roots) == required, 'all four trusted catalog proof roots are required')
    result = {}
    for name, value in roots.items():
        path = Path(value).absolute()
        require('..' not in path.parts and path.is_dir() and not any(p.is_symlink() for p in (path, *path.parents))
                and path != saved_root and saved_root not in path.parents and path not in saved_root.parents
                and path != repo and repo not in path.parents, 'proof root is linked, absent or overlaps callback/source')
        result[name] = path
    require(len(set(result.values())) == len(required), 'trusted proof roles must have distinct roots')
    return result


def verify_owner(owner, start, binary_inode=None):
    require(type(owner) is dict and type(start) is dict, 'saved owned process stamp missing')
    for value in (start, owner):
        require(all(type(value.get(key)) is int and value[key] > 0 for key in
                    ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode'))
                and value['pid'] == value['pgrp'] == value['session']
                and type(value.get('state')) is str and value['state'] != 'Z', 'owned process PID/session/start stamp differs')
        listener = value.get('listener', {})
        require(set(listener) == {'table', 'address', 'inode'} and listener['table'] == 'tcp'
                and listener['address'] == '0100007F' and type(listener['inode']) is str
                and listener['inode'].isdecimal() and int(listener['inode']) > 0, 'owned loopback socket stamp differs')
    fields = ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode', 'listener')
    require(all(owner[key] == start[key] for key in fields), 'HTTP owner is not the one admitted service')
    if binary_inode is not None:
        require(type(binary_inode) is int and start['executable_inode'] == binary_inode, 'owned executable inode differs from captured tested binary')


def verify_cleanup(cleanup, start):
    require(cleanup.get('cleanup_passed') is True and cleanup.get('remaining_owned_session_members') == []
            and type(cleanup.get('exit_code')) is int and cleanup.get('owned_pid') == start['pid']
            and cleanup.get('signal_target') == 'owned new process group only'
            and cleanup.get('signals') in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL']), 'owned service cleanup differs')


def verify_health(raw, record, typed_raw, after, assets_sha, ruff_sha, start, inode):
    value = parse_saved(raw)
    require(raw == typed_raw and record.get('status', record.get('http_status')) == 200
            and record.get('sha256', record.get('raw_sha256')) == sha(raw), 'raw health differs from fresh typed candidate proof')
    require(value.get('status') == 'ok' and value.get('model_loaded') is False and value.get('blocks_loaded') is True
            and value.get('backend_sha256') == after['python_backend'] and value.get('asset_manifest_sha256') == assets_sha
            and value.get('formatter_sha256') == ruff_sha, 'health owning factory, asset or formatter differs')
    verify_owner(record.get('owner', record.get('owned_socket')), start, inode)


def verify_carrier_role(record, revision, proof_sha, expected, run_id, attempt):
    require(record.get('version') == 'som.tested-candidate-carrier.v1' and record.get('candidate_revision') == revision
            and record.get('compile_manifest_sha256') == proof_sha and record.get('tested_identities') == expected
            and record.get('compiler_rebuilt') is False and record.get('origin_role') == 'original already tested candidate bytes'
            and record.get('product_acceptance_passed') is False
            and record.get('github', {}).get('GITHUB_RUN_ID') == run_id
            and record.get('github', {}).get('GITHUB_RUN_ATTEMPT') == attempt,
            'formal carrier source/proof/portable identities/run/attempt or original-build role differs')


def verify_capture_identities(value, expected, sources, proof, cnl):
    initial, final = value['initial_identities'], value['final_identities']
    require(initial == final and cnl.portable_identity({k: initial[k] for k in ('binary', 'assets', 'tools')}) == expected,
            'captured binary, assets or tools differ from trusted carrier/proof')
    owning = initial['source']
    require(set(proof.receipt['initial_identities']['source']) <= set(owning), 'captured owning source vector omits typed proof members')
    require(all(sha(sources.read(name)) == wanted for name, wanted in owning.items()), 'captured owning source hash differs')
    return initial


def verify_assembly_authority(root, jobs, proof, checker, driver, assembly, checked_helper, sources):
    raw = read(root / 'assembly/manifest.json')
    value = parse_saved(raw)
    require(value.get('version') == 'som.native-assembly-experiment.v1' and value.get('tested_sha') == sources.revision
            and value.get('assembly_experiment_complete') is True and value.get('compile_experiment_complete') is True
            and value.get('identity_stability_passed') is True and value.get('product_acceptance_passed') is False
            and value.get('observations_passed') == 2045 and value.get('owned_cleanup') == proof.receipt['cleanup']
            and value.get('saved_observation_stability_passed') is True and value.get('checked_input_stability_passed') is True,
            'complete fresh2045 assembly manifest required')
    require(proof.receipt.get('assembly_extension', {}).get('manifest_sha256') == sha(raw)
            and value.get('compile_stage', {}).get('canonical_row_receipts_sha256') == driver.experiment.value_sha(proof.receipt['rows']),
            'typed and assembly manifests are not joined by exact hashes')
    document, _, _ = driver.baseline(sources.root)
    goldens = driver.mapper.load_goldens(sources.root / (BASE + 'inputs/public-golden-hashes-v1.json'))
    plan = assembly.AssemblyExperiment(sources.root, None, None, None, sources.revision)
    plan.plan_jobs(goldens, document, jobs)
    observations = value['observations']
    require(len(observations) == len(plan.plan) == 2045 and value['actual_observations'] == assembly.OBSERVATIONS,
            'whole ordered2045 assembly observation scope differs')
    checked = {checked_helper.COHORT['id']: (checked_helper.EXPECTED_PROMPT_SHA256, checked_helper.EXPECTED_DSL_SHA256)}
    supplement = parse_saved(checker.repo.read(assembly.replay.SUPPLEMENT_FILE))
    require(sha(checker.repo.read(assembly.replay.SUPPLEMENT_FILE)) == assembly.replay.SUPPLEMENT_SHA256, 'checked supplement changed')
    for row in supplement['checked_dsl_inputs']:
        require(row['id'] in assembly.replay.CHECKED_DSL_SHA256
                and driver.experiment.value_sha(row['checked_dsl_input']) == row['dsl_sha256'] == assembly.replay.CHECKED_DSL_SHA256[row['id']],
                'checked DSL source authority differs')
        checked[row['id']] = row['prompt_sha256'], row['dsl_sha256']
    normal = {}
    expected_members = set()
    for index, (slot, row) in enumerate(zip(plan.plan, observations)):
        named = {k: v for k, v in slot.items() if k not in {'golden', 'accepted', 'rust_rebased'}}
        require(all(row.get(k) == v for k, v in named.items()) and row.get('observation_index') == index
                and row.get('state') == 'passed' and row.get('http_status') == 200, 'assembly named plan/index differs')
        prefix = f'assembly/observations/{index:04d}'
        require(row['request_path'] == prefix + '/assemble.request.raw' and row['response_path'] == prefix + '/assemble.response.raw'
                and row['product_dir'] == prefix + '/product', 'assembly proof member path differs')
        request, response = read(root / row['request_path']), read(root / row['response_path'])
        current = parse_saved(proof.read(f"rows/{slot['compile_row_index']:04d}/response.raw"))['bundle']
        if slot['mode'] == 'checked-dsl':
            bundle = parse_saved(request)['bundle']
            prompt_sha, dsl_sha = checked[slot['id']]
            current = assembly.checked_bundle(current, bundle['dsl'], prompt_sha, dsl_sha)
        require(request == driver.compact({'bundle': current}) and sha(request) == row['request_sha256']
                and sha(response) == row['response_sha256'], 'assembly request or response differs from exact fresh typed bundle')
        files = assembly.files_from_response(200, response)
        actual_map = driver.experiment.value_sha(files)
        require(actual_map == row['actual_files_map_sha256'] and assembly.file_receipts(files) == row['files'], 'assembly whole file map differs')
        if slot['target'] == 'python' and slot['mode'] == 'compile-default':
            accepted = slot['accepted']
            require(sha(response) == accepted['qa_assemble_response_sha256'] == accepted['baseline_assemble_response_sha256'],
                    'Python default assembly differs from frozen raw authority')
        if slot['compare_frozen_product']:
            assembly.verify_product_files(files, slot['golden'], slot['target'], slot.get('rust_rebased'))
        products = root / row['product_dir']
        actual_files = {name: read(products / name).decode('utf-8', 'strict') for name in file_inventory(products)}
        require(actual_files == files and len(files) == row['file_count'], 'saved assembly product universe or bytes differ')
        require(parse_saved(read(root / prefix / 'result.json')) == row, 'assembly per-observation receipt differs')
        verify_owner(row.get('owned_socket'), proof.receipt['owned_start'], proof.receipt['initial_identities']['binary']['inode'])
        expected_members.update([prefix + '/assemble.request.raw', prefix + '/assemble.response.raw', prefix + '/result.json'])
        expected_members.update(prefix + '/product/' + name for name in files)
        if slot['mode'] == 'compile-default':
            require(slot['compile_row_index'] not in normal, 'duplicate normal typed assembly')
            normal[slot['compile_row_index']] = row
    require(set(file_inventory(root / 'assembly/observations')) == {n.removeprefix('assembly/observations/') for n in expected_members}
            and len(normal) == 2039, 'assembly observation member universe differs')
    return normal, sha(raw)


def verify_cnl_saved_rows(root, value, jobs, proof, oracle, driver, start, binary_inode):
    require(len(jobs) == len(value['rows']) == 2050 and dict(Counter(j['group'] for j in jobs)) ==
            {'python_cnl': 1342, 'rust_cnl': 699, 'rust_native_json': 9}, 'complete independently derived CNL2050 plan required')
    keys = [(j['group'], j['target'], frozen_text(j['identity']), j['form']) for j in jobs]
    require(len(set(keys)) == 2050, 'duplicate independent CNL named/form slot')
    expected_files = set()
    for index, (job, row) in enumerate(zip(jobs, value['rows'])):
        directory = root / 'rows' / f'{index:04d}'
        require(all(row.get(k) == v for k, v in job.items() if k != 'raw') and row.get('index') == index
                and row.get('state') == 'passed', 'CNL group/identity/form/request/typed reference differs from frozen plan')
        raw, response = read(directory / 'request.raw'), read(directory / 'response.raw')
        require(raw == job['raw'] and sha(raw) == job['request_sha256'] == row['request_sha256'], 'CNL complete raw request differs')
        if job['target'] == 'rust' and job['identity']['id'] in ('RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'):
            expected_status, expected_response, diagnostic = oracle.expected(job)
            require(row.get('expected_diagnostic') == diagnostic and row.get('comparison') == 'independent frozen compiler collision rule and raw span',
                    'CNL current refusal diagnostic oracle differs')
        else:
            reference = proof.reference(job['target'], job['identity'])
            require(reference == job['typed_reference'] and reference['status'] == 200, 'CNL trusted typed reference differs')
            expected_status, expected_response = 200, proof.read(reference['path'])
            require(sha(expected_response) == reference['sha256'] and row.get('comparison') == 'exact complete current typed reply bytes',
                    'CNL current positive typed raw authority differs')
        require(row['http_status'] == row['expected_status'] == expected_status and response == expected_response
                and sha(response) == row['response_sha256'] == row['expected_response_sha256'], 'CNL response bytes/status differ from independent oracle')
        verify_owner(row.get('owned_socket'), start, binary_inode)
        require(parse_saved(read(directory / 'result.json')) == row, 'CNL per-row receipt differs')
        expected_files.update(f'{index:04d}/' + name for name in ('request.raw', 'response.raw', 'result.json'))
    require(set(file_inventory(root / 'rows')) == expected_files, 'CNL raw row universe differs')
    return dict(Counter(row['group'] for row in value['rows']))


def verify_cnl_events(log_path, root, rows):
    events = [parse_saved(line) for line in read(log_path).splitlines() if line.startswith(b'{')]
    require(len(events) == len(rows) + 1 and events[0] == {'stage': 'listening', 'address': '127.0.0.1:18977',
            'purpose': 'som.rust-compiler.v1'}, 'complete CNL owned server event universe differs')
    for index, (event, row) in enumerate(zip(events[1:], rows)):
        reply = parse_saved(read(root / 'rows' / f'{index:04d}' / 'response.raw'))
        bundle = reply.get('bundle')
        wanted = {'stage': 'compile', 'http_status': row['http_status'],
                  'bundle_bytes': len(frozen_text(bundle).encode()) if bundle is not None else None}
        require(set(event) == set(wanted) | {'total_ms'} and type(event['total_ms']) in (int, float)
                and math.isfinite(event['total_ms']) and event['total_ms'] >= 0
                and {k: v for k, v in event.items() if k != 'total_ms'} == wanted, 'CNL owning server event differs from exact raw response')


def verify_rebase_saved_rows(root, details, http, jobs, proof, checker, driver, normal, assembly_sha):
    saved = details['old_bundle_rebase']
    require(saved.get('assembly_manifest_sha256') == assembly_sha and saved.get('complete') is True,
            'old-bundle join assembly manifest differs from trusted proof')
    expected = [(index, job) for index, job in enumerate(jobs) if job['expected_http_status'] == 200]
    rows = saved['rows']
    require(len(rows) == len(expected) == 2039 and Counter(j['target'] for _, j in expected) == {'python': 1342, 'rust': 697},
            'whole2039 old-bundle/rebase scope differs')
    used = set()
    for row, (index, job) in zip(rows, expected):
        require(row.get('compile_row_index') == index and row.get('target') == job['target'] and row.get('identity') == job['identity']
                and row.get('passed') is True and row.get('old_bundle_refused') is True, 'named typed rebase index or identity differs')
        old_index, new_index = row['old_http_index'], row['rebased_http_index']
        require(type(old_index) is int and type(new_index) is int and old_index >= 0 and new_index == old_index + 1
                and new_index < len(http) and old_index not in used and new_index not in used, 'reused or missing rebase HTTP exchange')
        used.update((old_index, new_index))
        old, new = http[old_index], http[new_index]
        old_dir, new_dir = (root / 'http' / f"{item['index']:05d}" for item in (old, new))
        typed = proof.read(f'rows/{index:04d}/response.raw')
        original = checker.verify_response(job['target'], job['identity'], 200, typed).original_bundle
        require(original is not None, 'accepted raw typed proof has no old bundle')
        current = parse_saved(typed)['bundle']
        rebased = rebase(original, current, driver.experiment.value_sha)
        require(read(old_dir / 'request.raw') == driver.compact({'bundle': original})
                and read(new_dir / 'request.raw') == driver.compact({'bundle': rebased}), 'old or rebased raw request carries another typed case')
        require(old['route'] == new['route'] == '/v1/assemble' and old['status'] == 400 and new['status'] == 200
                and parse_saved(read(old_dir / 'response.raw')).get('code') == 'ASSET_HASH_MISMATCH', 'normal old-bundle refusal differs')
        recorded = normal[index]
        require(recorded['identity'] == job['identity'] and recorded['target'] == job['target'], 'trusted assembly/typed row differs')
        expected_raw = read(proof.root / recorded['response_path'])
        actual = read(new_dir / 'response.raw')
        require(actual == expected_raw and sha(actual) == recorded['response_sha256'] == row['rebased_response_sha256']
                and driver.experiment.value_sha(parse_saved(actual)['files']) == recorded['actual_files_map_sha256'] == row['files_map_sha256'],
                'rebased response or whole file map differs from trusted fresh assembly')
    return used


def verify_full_command(directory, command, row, expected_binary, expected_cwd):
    full = parse_saved(read(directory / 'command.json'))
    require(full.get('argv') == command['argv'] and full.get('argv', [None])[0] == expected_binary
            and full.get('cwd') == expected_cwd and full.get('exit_code') == command['exit_code'] == row['exit_code']
            and type(full.get('pid')) is int and full['pid'] > 0
            and full.get('wait_completed') is True and command.get('wait_completed') is True
            and full.get('owned_group_cleanup_passed') is True and command.get('owned_group_cleanup_passed') is True,
            'full saved CLI command contradicts argv/exit/wait/group evidence')
    require(type(full.get('started_monotonic')) in (float, int) and type(full.get('elapsed_seconds')) in (float, int)
            and math.isfinite(full['started_monotonic']) and math.isfinite(full['elapsed_seconds'])
            and full['started_monotonic'] >= 0 and full['elapsed_seconds'] >= 0
            and full.get('environment') == {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}, 'full CLI command timing or environment differs')
    for stream in ('stdout', 'stderr'):
        safe_member(full[stream])
        require(full[stream].startswith('environment-commands/') and full[stream].endswith('.' + stream)
                and sha(read(directory / (stream + '.raw'))) == full[stream + '_sha256'] == command[stream + '_sha256'] == row[stream + '_sha256'],
                'full CLI command stream or raw byte hash differs')
    require(full['stdout'].removesuffix('.stdout') == full['stderr'].removesuffix('.stderr'), 'CLI stdout/stderr command prefix differs')
    return full


@contextmanager
def trusted_proof_bundle(repo, source_reader, helper_revision, candidate_revision, roots, saved_root, expected_admission):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'saved proof verification runs only on GitHub Linux')
    sources = JoinSources(repo, helper_revision, source_reader)
    authority = fresh_module(sources, '_som_semantic_join_authority', AUTHORITY)
    try:
        admission = authority.verify(sources.read, 'native-qa-v1', path_set(sources), helper_revision, candidate_revision)
        require(admission == expected_admission and admission.get('source_authority_active') is True, 'fresh numbered semantic source admission differs')
        required = ('run_experimental_native_cnl', 'export_goldens', 'raw_digest_compat', 'reconstruct_public_inputs',
                    'map_rust_inputs', 'complete_rust_inputs', 'experimental_native_compile', 'run_experimental_native_compile',
                    'run_experimental_native_assembly', 'prove_checked_reference')
        paths = {name: BASE + name + '.py' for name in required}
        paths['_som_semantic_join_carrier'] = BASE + 'product_runtime/candidate_carrier.py'
        paths['rust_profile_phase2'] = 'som-rs/tests/rust-output-v1/rust_profile_phase2.py'
        paths['_som_semantic_original_cli'] = CLI_SOURCE
        require(set(paths.values()) <= set(admission['admitted_repository_python_sources']), 'semantic verifier helper is outside exact sealed source authority')
        with JoinModules(sources, paths):
            driver = importlib.import_module('run_experimental_native_compile')
            complete = importlib.import_module('complete_rust_inputs')
            cnl = importlib.import_module('run_experimental_native_cnl')
            assembly = importlib.import_module('run_experimental_native_assembly')
            checked = importlib.import_module('prove_checked_reference')
            carrier = importlib.import_module('_som_semantic_join_carrier')
            before = {name: file_inventory(path) for name, path in roots.items()}
            temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
            require(not any(temporary == p or p in temporary.parents for p in (sources.root, saved_root, *roots.values())), 'verification scratch overlaps protected input')
            with tempfile.TemporaryDirectory(prefix='som-semantic-proof-join-', dir=temporary) as temporary_name:
                scratch = Path(temporary_name)
                checker = driver.experiment.ExperimentalChecker(sources.root)
                jobs, reused, completed, inputs, rust_tree = cnl.frozen_inputs(driver, complete, checker, sources.root,
                    roots['python_inputs_root'], roots['rust_inputs_root'], scratch, candidate_revision)
                proof = cnl.TypedProof(roots['assembly_root'], jobs, checker, driver, candidate_revision, inputs)
                cnl_jobs = cnl.plan_jobs(driver, jobs, reused, completed, roots['rust_inputs_root'], proof)
                oracle = cnl.RefusalOracle(checker, driver)
                for job in cnl_jobs:
                    if job['target'] == 'rust' and job['identity']['id'] in cnl.REFUSALS: oracle.expected(job)
                normal, assembly_sha = verify_assembly_authority(roots['assembly_root'], jobs, proof, checker, driver, assembly, checked, sources)
                typed_start = proof.receipt['owned_start']
                verify_owner(typed_start, typed_start, proof.receipt['initial_identities']['binary']['inode'])
                for row in proof.receipt['rows']:
                    verify_owner(row.get('owned_socket'), typed_start, proof.receipt['initial_identities']['binary']['inode'])
                carrier_raw = read(roots['carrier_root'] / 'carrier.json')
                carrier_record = parse_saved(carrier_raw)
                verify_carrier_role(carrier_record, candidate_revision, proof.manifest_sha256,
                    carrier.portable({name: proof.receipt['initial_identities'][name] for name in ('binary', 'assets', 'tools')}),
                    os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'])
                restored = carrier.restore(roots['carrier_root'], scratch / 'verified-carrier', roots['assembly_root'] / 'manifest.json', candidate_revision)
                typed_health = proof.read('health.response.raw')
                verify_health(typed_health, proof.receipt['health_consistency'], typed_health, checker.after,
                    driver.asset_manifest_canonical_sha(restored['assets']), restored['identity']['tools']['SOM_RUFF']['sha256'],
                    typed_start, proof.receipt['initial_identities']['binary']['inode'])
                saved_carrier = saved_root / 'restored-candidate'
                require(carrier.tree(saved_carrier) == carrier_record['files']
                        and carrier.identities(saved_carrier) == restored['identity'], 'saved restored carrier universe, modes or bytes differ')
                yield SimpleNamespace(sources=sources, authority=authority, admission=admission, driver=driver, cnl=cnl,
                    checker=checker, jobs=jobs, proof=proof, cnl_jobs=cnl_jobs, oracle=oracle, normal=normal,
                    assembly_sha=assembly_sha, carrier=restored, carrier_record=carrier_record)
                proof.verify(); reused.verify()
                require(cnl.tree_hashes(roots['rust_inputs_root']) == rust_tree
                        and before == {name: file_inventory(path) for name, path in roots.items()}, 'trusted proof/input files changed during semantic join')
                require(authority.verify(sources.read, 'native-qa-v1', path_set(sources), helper_revision, candidate_revision) == admission,
                        'native source authority changed during semantic join')
                sources.verify()
    finally:
        sys.modules.pop('_som_semantic_join_authority', None)


def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo=None, proof_roots=None):
    """Join saved outputs to trusted catalog roots. No installed code runs here.

    repo and proof_roots are mandatory for every successful call. None defaults
    preserve the old early-envelope refusal fixtures, before source execution.
    """
    root = Path(root).absolute()
    report_raw = read(root / 'result.json')
    report = parse_saved(report_raw)
    require(helper_revision == candidate_revision and report.get('version') == VERSION
            and report.get('gate') == gate and report.get('helper_revision') == helper_revision
            and report.get('candidate_revision') == candidate_revision
            and candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
            and report.get('product_acceptance_passed') is False, 'semantic gate identity differs')
    require(all(report.get(key) is True for key in ('complete', 'all_required_passed', 'owned_waits_complete',
            'owned_cleanup_complete', 'raw_logs_complete')), 'incomplete semantic gate')
    require(repo is not None and proof_roots is not None, 'trusted repo and all catalog proof roots are mandatory')
    repo = Path(repo).absolute()
    roots = proof_roots_data(proof_roots, root, repo)
    actual = file_inventory(root); actual.pop('result.json')
    require(actual == report['saved_files_sha256'], 'semantic raw file universe changed')
    with trusted_proof_bundle(repo, source_reader, helper_revision, candidate_revision, roots, root, report['source_admission']) as trusted:
        pins = report['bound_source_sha256']
        require(SELF in pins and all(sha(trusted.sources.read(name)) == digest for name, digest in pins.items()), 'semantic captured source pins differ')
        details = report['details']
        if gate == 'cnl':
            verify_cnl_result(root, report, trusted)
        else:
            require(gate == 'cli-refusals-rebase', 'unknown semantic gate')
            verify_cli_rebase_result(root, report, trusted)
        require(read(root / 'result.json') == report_raw and file_inventory(root) == {'result.json': sha(report_raw), **actual},
                'saved callback raw files changed during independent join')
    return report


def verify_cnl_result(root, report, trusted):
    details, cnl, proof, driver = report['details'], trusted.cnl, trusted.proof, trusted.driver
    raw = read(root / 'cnl/manifest.json'); value = parse_saved(raw)
    require(sha(raw) == details['cnl_manifest_sha256'] and value['tested_sha'] == report['candidate_revision']
            and value['experiment_complete'] is True and value['passed'] == 2050
            and value['actual_rows'] == cnl.EXPECTED and len(value['rows']) == 2050
            and value['identity_stability_passed'] is True and value['input_and_typed_proof_stability_passed'] is True
            and value['product_acceptance_passed'] is False and value.get('interrupted_signal') is None
            and value['inputs'] == proof.receipt['inputs'] and value['typed_proof_manifest_sha256'] == proof.manifest_sha256
            and value['typed_raw_rows_independently_reverified'] == 2041, 'CNL manifest differs from complete frozen typed proof')
    initial = verify_capture_identities(value, trusted.carrier['identity'], trusted.sources, proof, cnl)
    transport = value['typed_identity_transport']
    require(transport == {'mode': 'verified-same-candidate-portable-carrier-identities',
        'carrier_sha256': trusted.carrier['carrier_sha256'], 'archive_sha256': trusted.carrier['archive_sha256'],
        'compile_manifest_sha256': proof.manifest_sha256, 'candidate_revision': report['candidate_revision'], 'compiler_rebuilt': False},
        'CNL explicit portable carrier binding differs')
    start = value['owned_start']; verify_owner(start, start, initial['binary']['inode'])
    verify_health(read(root / 'cnl/health.response.raw'), value['health_consistency'],
        proof.read('health.response.raw'), trusted.checker.after, driver.asset_manifest_canonical_sha(trusted.carrier['assets']),
        trusted.carrier['identity']['tools']['SOM_RUFF']['sha256'], start, initial['binary']['inode'])
    require(verify_cnl_saved_rows(root / 'cnl', value, trusted.cnl_jobs, proof, trusted.oracle, driver, start,
                initial['binary']['inode']) == cnl.EXPECTED, 'verified CNL group counts differ')
    verify_cleanup(value['cleanup'], start)
    verify_cnl_events(root / 'cnl/server.log', root / 'cnl', value['rows'])
    command = parse_saved(read(root / 'cnl-command.json'))
    require(command.get('exit_code') == 0 and command.get('wait_completed') is True
            and command.get('owned_group_cleanup_passed') is True and type(command.get('pid')) is int and command['pid'] > 0,
            'CNL outer child actual wait or cleanup missing')
    for stream in ('stdout', 'stderr'):
        require(sha(read(root / ('cnl-command.' + stream + '.raw'))) == command[stream + '_sha256'], 'CNL command raw stream differs')


CLI_SOURCE = 'som-rs/scripts/rust_output_acceptance/check_cli.py'
RUST_RECIPE_SOURCE = 'som-rs/som-targets/qa/check_rust_products.py'
TARGET_ASSERTION_SOURCE = 'som-rs/som-targets/qa/check_targets.py'
RUST_RECIPE_FUNCTIONS = ('h', 'rehash', 'rename_field', 'python_compile', 'with_shared',
                         'base_recipes', 'renamed', 'refusal_row', 'phase2_refusals')
TARGET_ASSERTION_FUNCTIONS = ('sha', 'dump', 'resolve')


class OfflineTranscript:
    """Read one ordered captured transcript. There is no network fallback."""
    def __init__(self, root, rows, base):
        self.root, self.rows, self.base, self.cursor = Path(root), rows, base, 0

    def take(self):
        require(self.cursor < len(self.rows), 'missing original frozen gate HTTP call')
        row = self.rows[self.cursor]
        require(row.get('index') == self.cursor, 'original gate transcript order differs')
        directory = self.root / 'http' / f'{self.cursor:05d}'
        request, response = read(directory / 'request.raw'), read(directory / 'response.raw')
        require(parse_saved(read(directory / 'result.json')) == row and sha(request) == row['request_sha256']
                and sha(response) == row['response_sha256'] and type(row.get('status')) is int,
                'original gate raw HTTP record differs')
        self.cursor += 1
        return row, request, response

    def post(self, base, body, route='/v1/compile'):
        require(base == self.base and route in ('/v1/compile', '/v1/assemble'), 'offline assertion endpoint differs')
        expected = body.encode() if isinstance(body, str) else body
        require(type(expected) is bytes, 'offline assertion request must be complete bytes')
        row, request, response = self.take()
        require(row['route'] == route and request == expected, 'original frozen assertion request or route differs')
        return row['status'], response


class OfflineCli:
    """Supply the retained CLI child streams to the unchanged CLI assertions."""
    def __init__(self, root, commands, transcript):
        self.root, self.commands, self.transcript, self.cursor = Path(root), commands, transcript, 0

    def capture(self, binary, base, cnl_path, target):
        require(base == self.transcript.base and self.cursor < len(self.commands), 'missing original frozen CLI child')
        index = self.cursor
        command = self.commands[index]
        directory = self.root / 'cli' / f'{index:04d}'
        full = parse_saved(read(directory / 'command.json'))
        require(command['http_request_index'] == self.transcript.cursor and command['argv'] == full['argv']
                and full.get('wait_completed') is True
                and full.get('owned_group_cleanup_passed') is True and type(full.get('exit_code')) is int
                and type(command.get('exit_code')) is int and full.get('exit_code') == command['exit_code']
                and read(cnl_path) == read(directory / 'source.cnl.raw'), 'offline CLI child/source/wait record differs')
        expected_tail = [] if target is None else ['--target', target]
        require(command['argv'][5:] == expected_tail, 'offline CLI original target argument differs')
        row, request, _ = self.transcript.take()
        # The original check() compares this actual captured request with its
        # own cli_body(), and compares each child output to the HTTP control.
        done = subprocess.CompletedProcess(command['argv'], full['exit_code'],
            read(directory / 'stdout.raw'), read(directory / 'stderr.raw'))
        self.cursor += 1
        return done, [(row['route'], request)]


def no_replay_process(*args, **kwargs):
    raise ValueError('native process or live network is forbidden during saved assertion replay')


def replay_cli_assertions(cli, root, saved, commands, transcript, out, binary):
    """Only transport hooks change. All original check/main bodies stay exact."""
    original_check = cli.check
    offline = OfflineCli(root, commands, transcript)
    def capture_check(binary, base_url, scratch, label, source, target, expect=None, verify_request=False):
        return original_check(binary, base_url, scratch, label, source, target, expect, verify_request=True)
    previous = {name: getattr(cli, name) for name in ('check', '_run_cli_with_request_capture', 'post', 'subprocess', 'NO_PROXY_OPENER')}
    try:
        cli.check = capture_check
        cli._run_cli_with_request_capture = offline.capture
        cli.post = transcript.post
        cli.subprocess = SimpleNamespace(run=no_replay_process)
        cli.NO_PROXY_OPENER = SimpleNamespace(open=no_replay_process)
        with redirect_stdout(io.StringIO()):
            status = cli.main(['--binary', str(binary), '--base-url', transcript.base,
                               '--out', str(out), '--refusals', 'phase2'])
        replayed = parse_saved(read(out))
        require(status == 0 and replayed == saved and replayed.get('failed') == []
                and replayed['passed'] == replayed['total'] == offline.cursor == len(commands),
                'original frozen CLI assertions or complete report differ on raw replay')
        return replayed
    finally:
        for name, value in previous.items(): setattr(cli, name, value)


def selected_original_functions(sources, path, names, namespace, assignments=()):
    """Compile exact original AST nodes in an explicit stdlib-only namespace."""
    raw = sources.read(path)
    tree = ast.parse(raw, filename=str(sources.root / path))
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    require(len(definitions) == len(names) and {node.name for node in definitions} == set(names)
            and all(isinstance(node, ast.FunctionDef) and not node.decorator_list for node in definitions),
            'exact original assertion function closure is missing or ambiguous')
    selected_assignments = []
    for name in assignments:
        found = [node for node in tree.body if isinstance(node, ast.Assign)
                 and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name]
        require(len(found) == 1, 'exact original assertion global is missing or ambiguous')
        value = ast.literal_eval(found[0].value)
        require(name == 'RUST_ASSETS' and value == ('catalog', 'block_library', 'backend', 'formatter'),
                'unreviewed original assertion global')
        selected_assignments.extend(found)
    nodes = sorted([*definitions, *selected_assignments], key=lambda node: node.lineno)
    require(any(isinstance(node, ast.ImportFrom) and node.module == '__future__'
                and any(alias.name == 'annotations' for alias in node.names) for node in tree.body),
            'original assertion annotation semantics differ')
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    allowed_builtins = {name: getattr(builtins, name) for name in
        ('all', 'any', 'bool', 'dict', 'enumerate', 'float', 'int', 'isinstance', 'len', 'list', 'next', 'range', 'set', 'sorted', 'str', 'tuple', 'zip')}
    scope = {'__name__': '_som_original_saved_assertions', '__file__': str(sources.root / path),
             '__builtins__': allowed_builtins, 'json': json, 'copy': copy, 'hashlib': hashlib, **namespace}
    exec(compile(module, str(sources.root / path), 'exec', flags=__future__.annotations.compiler_flag, dont_inherit=True), scope)
    # This is an audit of exact untouched source nodes, not rewritten assertions.
    provenance = {'path': path, 'raw_sha256': sha(raw),
                  'function_AST_sha256': {node.name: sha(ast.dump(node, include_attributes=False).encode()) for node in definitions},
                  'assignment_AST_sha256': {node.targets[0].id: sha(ast.dump(node, include_attributes=False).encode()) for node in selected_assignments}}
    return SimpleNamespace(**{name: scope[name] for name in (*names, *assignments)}), provenance


def original_refusal_assertions(sources, transcript):
    for name in ('v1', 'v3', 'v6'):
        sources.read('som-rs/contracts/compiler-v1/examples/' + name + '.prompt.json')
    plan = parse_saved(sources.read('som-rs/contracts/rust-output-v1/acceptance-plan.json'))
    sources.read('som-rs/contracts/rust-output-v1/negative-cases.json')
    targets, target_provenance = selected_original_functions(sources, TARGET_ASSERTION_SOURCE,
        TARGET_ASSERTION_FUNCTIONS, {})
    targets.EXAMPLES = sources.root / 'som-rs/contracts/compiler-v1/examples'
    targets.post = transcript.post
    recipes, recipe_provenance = selected_original_functions(sources, RUST_RECIPE_SOURCE,
        RUST_RECIPE_FUNCTIONS, {'targets': targets, 'CONTRACT': sources.root / 'som-rs/contracts/rust-output-v1', 'PLAN': plan},
        assignments=('RUST_ASSETS',))
    return recipes, targets, {'targets': target_provenance, 'recipes': recipe_provenance}


def replay_refusal_assertions(recipes, targets, transcript, base_prompt, saved_rows):
    status, raw = transcript.post(transcript.base, targets.dump({'format': 'som.prompt-json.v1', 'target': 'rust', 'prompt': base_prompt}))
    require(status == 200, 'original frozen recipe Rust base did not compile')
    rows = recipes.phase2_refusals(transcript.base, parse_saved(raw)['bundle'])
    require(len(rows) == len(saved_rows) == 15 and rows == saved_rows and all(row.get('passed') is True for row in rows),
            'original frozen fifteen refusal/status/pointer/span/A8 assertions differ on raw replay')
    return rows


def replay_original_gate_assertions(root, details, trusted):
    """Consume all original gate calls once, then require the complete rebase suffix."""
    require({CLI_SOURCE, RUST_RECIPE_SOURCE, TARGET_ASSERTION_SOURCE} <=
            set(trusted.admission['admitted_repository_python_sources']), 'original assertion source is outside sealed QA admission')
    transcript = OfflineTranscript(root, details['http_rows'], 'http://127.0.0.1:18979')
    cli = importlib.import_module('_som_semantic_original_cli')
    require(getattr(cli, '__som_raw_sha256__', None) == sha(trusted.sources.read(CLI_SOURCE)), 'original CLI raw source binding differs')
    with tempfile.TemporaryDirectory(prefix='som-original-gate-replay-', dir=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)) as directory:
        replay_cli_assertions(cli, root, details['cli_refusals']['cli'], details['cli_refusals']['cli_commands'], transcript,
            Path(directory) / 'original-cli.json', Path(root) / 'restored-candidate/binary')
        recipes, targets, _ = original_refusal_assertions(trusted.sources, transcript)
        base_prompt = parse_saved(trusted.sources.read('som-rs/contracts/compiler-v1/examples/v1.prompt.json'))
        replay_refusal_assertions(recipes, targets, transcript, base_prompt, details['cli_refusals']['assemble_refusals'])
    prefix = transcript.cursor
    rows = details['old_bundle_rebase']['rows']
    require([(row['old_http_index'], row['rebased_http_index']) for row in rows]
            == [(prefix + 2 * index, prefix + 2 * index + 1) for index in range(2039)]
            and len(transcript.rows) == prefix + 4078,
            'missing, extra or reordered original-gate/rebase HTTP calls')
    return {'original_gate_HTTP_calls': prefix, 'rebase_HTTP_calls': 4078, 'all_calls_consumed_once': True}


def verify_cli_rebase_result(root, report, trusted):
    details, proof, driver, cnl = report['details'], trusted.proof, trusted.driver, trusted.cnl
    initial = verify_capture_identities(details, trusted.carrier['identity'], trusted.sources, proof, cnl)
    start = details['owned_start']; verify_owner(start, start, initial['binary']['inode'])
    verify_health(read(root / 'health.response.raw'), details['health'], proof.read('health.response.raw'), trusted.checker.after,
        driver.asset_manifest_canonical_sha(trusted.carrier['assets']), trusted.carrier['identity']['tools']['SOM_RUFF']['sha256'], start,
        initial['binary']['inode'])
    require(parse_saved(read(root / 'owned-process.json')) == {k: v for k, v in details.items()
        if k not in {'server_events', 'final_identities'}}, 'saved full service/process receipt differs')
    http = details['http_rows']
    require(http and [row['index'] for row in http] == list(range(len(http))), 'HTTP exchange order differs')
    for row in http:
        directory = root / 'http' / f"{row['index']:05d}"
        require(parse_saved(read(directory / 'result.json')) == row and sha(read(directory / 'request.raw')) == row['request_sha256']
                and sha(read(directory / 'response.raw')) == row['response_sha256'], 'raw HTTP exchange differs')
        verify_owner(row.get('owned_socket'), start, initial['binary']['inode'])
    require(set(file_inventory(root / 'http')) == {f'{i:05d}/{n}' for i in range(len(http))
            for n in ('request.raw', 'response.raw', 'result.json')}, 'HTTP raw member universe differs')
    require(details['server_events'] == verify_server_events(root / 'service/server.log', root, http, 18979), 'ordered complete server events differ')
    rebase_used = verify_rebase_saved_rows(root, details, http, trusted.jobs, proof, trusted.checker, driver, trusted.normal, trusted.assembly_sha)
    cli = details['cli_refusals']['cli']
    require(parse_saved(read(root / 'frozen-cli.json')) == cli and cli['failed'] == []
            and cli['passed'] == cli['total'] == len(cli['rows']) and cli['refusals'] == cli['refusals_exit_2'] > 0, 'frozen complete CLI report differs')
    scope = cli_scope(trusted.sources.read)
    require([(r['input'], r['target']) for r in cli['rows']] == [(i, t) for i, t, raw in scope]
            and cli['refusals_profile'] == 'phase2' and cli['binary_sha256'] == trusted.carrier['identity']['binary']['sha256'], 'whole frozen CLI scope or binary differs')
    commands = details['cli_refusals']['cli_commands']
    require(len(commands) == len(cli['rows']), 'missing CLI command')
    captured_root = details['capture_root']
    require(type(captured_root) is str and Path(captured_root).is_absolute() and '..' not in Path(captured_root).parts, 'captured owned root missing')
    binary = captured_root + '/restored-candidate/binary'
    require(details['launch']['argv'] == [binary, 'serve', '--assets', captured_root + '/restored-candidate/assets', '--port', '18979'], 'captured service launch differs')
    for index, (command, row) in enumerate(zip(commands, cli['rows'])):
        directory = root / 'cli' / f'{index:04d}'
        source = scope[index][2]
        require(read(directory / 'source.cnl.raw') == source and row['passed'] is True and row['problems'] == [], 'CLI frozen source or original assertion failed')
        full = verify_full_command(directory, command, row, binary, captured_root + f'/cli/{index:04d}')
        argv, target = command['argv'], scope[index][1]
        require(len(argv) == (5 if target is None else 7) and argv[1] == 'compile' and argv[3] == '--url'
                and Path(argv[2]).name == sha(row['input'].encode())[:16] + '.cnl'
                and (target is None or argv[5:] == ['--target', target]) and full['pid'] != start['pid'],
                'full CLI argument target/source or child identity differs from frozen command')
        proxy = re.fullmatch(r'http://127[.]0[.]0[.]1:([0-9]+)', argv[4])
        require(proxy is not None and 1024 <= int(proxy[1]) <= 65535 and int(proxy[1]) not in {8765, 18765},
                'CLI request-capture proxy is not an owned QA endpoint')
        request = {'format': 'som.cnl.v1', 'source': source.decode('utf-8')}
        if scope[index][1] is not None: request['target'] = scope[index][1]
        expected_body = driver.compact(request)
        capture_index = command['http_request_index']
        require(type(capture_index) is int and 0 <= capture_index < len(http) - 1 and capture_index not in rebase_used
                and capture_index + 1 not in rebase_used, 'CLI captured HTTP index overlaps another gate')
        captured, control = http[capture_index:capture_index + 2]
        for item in (captured, control):
            require(item['route'] == '/v1/compile' and read(root / 'http' / f"{item['index']:05d}" / 'request.raw') == expected_body,
                    'CLI captured/control complete request differs from frozen source/target')
        require(captured['request_sha256'] == control['request_sha256'] == row['expected_request_sha256'] == row['request_sha256']
                and captured['response_sha256'] == control['response_sha256'] and captured['status'] == control['status'] == row['http_status'], 'CLI captured/control response differs')
        raw = read(root / 'http' / f"{captured['index']:05d}" / 'response.raw')
        if captured['status'] == 200:
            require(command['exit_code'] == 0 and parse_saved(read(directory / 'stdout.raw')) == parse_saved(raw)['bundle'], 'CLI success output differs')
        else:
            require(command['exit_code'] == 2 and read(directory / 'stdout.raw') == b'' and parse_saved(read(directory / 'stderr.raw')) == parse_saved(raw), 'CLI refusal output differs')
    require(set(file_inventory(root / 'cli')) == {f'{i:04d}/{n}' for i in range(len(commands))
        for n in ('source.cnl.raw', 'stdout.raw', 'stderr.raw', 'command.json')}, 'full CLI child member universe differs')
    refusal_ids = {f'A{i}' for i in range(1, 10)} | {f'A{i}_PUBLIC_v6' for i in (1, 2, 4, 5, 7, 9)}
    refusals = details['cli_refusals']['assemble_refusals']
    require(len(refusals) == 15 and {row['id'] for row in refusals} == refusal_ids
            and all(row['passed'] is True and row['problems'] == [] for row in refusals)
            and parse_saved(read(root / 'frozen-assemble-refusals.json')) == {'rows': refusals}, 'complete frozen phase2 refusal rows differ')
    verify_cleanup(details['cleanup'], start)
    replay_original_gate_assertions(root, details, trusted)


def main(argv=None):
    parser = argparse.ArgumentParser()
    for field in ('repo', 'assembly-root', 'carrier-root', 'product-context', 'out'):
        parser.add_argument('--' + field, type=Path, required=True)
    for field in ('helper-revision', 'candidate-revision'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--qa-mode', choices=['native-qa-v1'], required=True)
    parser.add_argument('--gate', choices=['cnl', 'cli-refusals-rebase'], required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux only')
    require(args.helper_revision == args.candidate_revision and args.candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa',
            'fresh common final candidate required')
    args.repo = args.repo.absolute()
    args.out = args.out.absolute()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(temporary in args.out.parents and not args.out.exists() and '..' not in args.out.parts
            and not any(p.is_symlink() for p in args.out.parents), 'new owned gate output required')
    module = bootstrap(args.repo, args.helper_revision)
    sources = module.SourceBytes(args.repo, args.helper_revision)
    authority = module.bound_module(sources, '_som_semantic_authority', AUTHORITY)
    admission = authority.verify(sources.read, args.qa_mode, path_set(sources), args.helper_revision, args.candidate_revision)
    require(admission['source_authority_active'] is True, 'sealed source authority required')
    data = json.loads(read(args.product_context))
    require(data['repo'] == str(args.repo) and data['helper_revision'] == args.helper_revision
            and data['candidate_revision'] == args.candidate_revision and data['qa_mode'] == args.qa_mode
            and Path(data['assembly_root']) == args.assembly_root, 'gate context identity differs')
    os.environ[module.ENV] = str(args.product_context.absolute())
    ctx = module.Context(args.product_context)
    ctx.sources.read(SELF)
    module._ACTIVE = ctx
    require(Path(sys.executable).resolve() == module.observer_python().resolve(), 'qualified observer Python required')
    module.install_source_finder(ctx.sources)
    sys.path[:0] = [str(args.repo / 'som-rs/tests/rust-output-v1'), str(args.repo / 'som-rs/som-targets/qa'),
                   str(args.repo / 'som-rs/scripts/compiler_acceptance')]
    protected = [args.repo, args.assembly_root, args.carrier_root, args.product_context,
                 *[Path(data[name]) for name in ('python_inputs_root', 'rust_inputs_root', 'runtime_root',
                                                'wheel_root', 'rust_tools_root', 'cargo_root')]]
    require(all(args.out != path and path not in args.out.parents and args.out not in path.parents
                for path in protected), 'gate output overlaps a protected input')
    args.out.mkdir(mode=0o700)
    report = {'version': VERSION, 'gate': args.gate, 'helper_revision': args.helper_revision,
              'candidate_revision': args.candidate_revision, 'product_acceptance_passed': False,
              'complete': False, 'all_required_passed': False, 'owned_waits_complete': False,
              'owned_cleanup_complete': False, 'raw_logs_complete': False, 'source_admission': admission}
    try:
        report['details'] = execute(args, module, ctx, authority, admission)
        ctx.verify()
        require(authority.verify(ctx.sources.read, args.qa_mode, path_set(ctx.sources), args.helper_revision,
                                 args.candidate_revision) == admission, 'sealed authority changed')
        sources.verify()
        report.update(complete=True, all_required_passed=True, owned_waits_complete=True,
                      owned_cleanup_complete=True, raw_logs_complete=True)
    except BaseException as error:
        report['error'] = type(error).__name__
        report['reason'] = str(error)
    finally:
        report['bound_source_sha256'] = dict(sorted(ctx.sources.pins.items()))
        report['saved_files_sha256'] = file_inventory(args.out)
        save(args.out / 'result.json', report)
    return 0 if report['all_required_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
