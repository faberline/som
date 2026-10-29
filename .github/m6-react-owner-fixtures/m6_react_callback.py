#!/usr/bin/env python3
"""Draft: run M6 collector and audit against one source-bound candidate.

This is not acceptance. The caller must supply the live owned services, inputs,
and checked transport. Result verification is intentionally unavailable until
launcher cleanup and cross-job restored-run identities have defined verifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType

SELF = 'som-rs/scripts/react_output_acceptance/m6_react_callback.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
COLLECTOR = 'som-rs/scripts/react_output_acceptance/run_acceptance.py'
AUDITOR = 'som-rs/scripts/react_output_acceptance/audit/audit130.py'
GATE = 'm6-react003'
# Fail closed until the graph supplies reviewed source, owner and restored-run joins.
GRAPH_INTEGRATION_REVIEWED = False
OWNER_INTEGRATION_REVIEWED = False
HEX = re.compile(r'[0-9a-f]{40}\Z')


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def physical(path, *, exists=True):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and
            not any(p.is_symlink() for p in (path, *path.parents)), 'absolute physical path required')
    if exists:
        require(path.exists() and path.resolve() == path, 'physical path missing')
    return path


def read(path, maximum=128 * 1024**2):
    path = physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
                'bounded single-link file required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum, 'file too large')
        return raw
    finally:
        os.close(fd)


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def inventory(root):
    root = physical(root)
    require(root.is_dir(), 'callback evidence directory required')
    result = {}
    def refuse_scan(_):
        raise ValueError('callback evidence scan failed')
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=refuse_scan):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'linked callback evidence refused')
            if stat.S_ISDIR(mode):
                continue
            require(stat.S_ISREG(mode), 'special callback evidence refused')
            result[path.relative_to(root).as_posix()] = sha(read(path))
    return dict(sorted(result.items()))


def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL, timeout=60)


def admit(repo, revision, candidate, context_path):
    """Refuse until reviewed native source authority precedes every graph import.

    The previous draft only compared the graph bytes with a requested Git HEAD.
    That comparison does not authorize the revision before executing its code.
    """
    raise ValueError('native pre-import source authority is not implemented')


def run_gate(ctx, candidate_dir, services, out, gate):
    """Capture M6 work under the shared service owner; acceptance stays closed.

    The explicit input key is a proposed graph interface, not an existing seal.
    The launcher, not this callback, owns the service stop and wait.
    """
    require(OWNER_INTEGRATION_REVIEWED, 'M6 shared owner integration is not implemented')
    repo, out = physical(ctx.repo), physical(out)
    temporary = physical(Path(os.environ['RUNNER_TEMP']))
    require(gate == GATE and out.is_dir() and out.name == 'owner' and temporary in out.parents
            and repo not in out.parents and not any(out.iterdir()), 'fresh shared owner directory required')
    revision = ctx.helper_revision
    require(HEX.fullmatch(revision or '') is not None and revision == ctx.candidate_revision
            and ctx.qa_mode == 'native-qa-v1', 'same native source identity required')
    source_authority = ctx.qa_source_authority
    require(source_authority.get('source_authority_active') is True
            and source_authority.get('helper_revision') == source_authority.get('candidate_revision') == revision
            and all(source_authority.get(k) is False for k in ('product_acceptance_passed',
                'compiler_acceptance_passed', 'whole_acceptance_complete')), 'native source authority required')
    admitted = source_authority.get('admitted_repository_python_sources', [])
    require(all(p in admitted for p in (SELF, COLLECTOR, AUDITOR)), 'owner sources not admitted')
    for path in (SELF, COLLECTOR, AUDITOR):
        ctx.sources.read(path)
    report = {'version': 'som.m6-react-graph-callback.v1', 'gate': gate,
              'helper_revision': revision, 'candidate_revision': revision, 'rows': [],
              'product_acceptance_passed': False, 'complete': False,
              'all_required_passed': False, 'owned_waits_complete': False,
              'owned_cleanup_complete': False, 'raw_logs_complete': False,
              'remaining': ['contract-allowed baseline and source-bound React inputs',
                            'independent raw auditor and launcher post-close cleanup join',
                            'sealed cross-job collector restore and path mapping']}
    collector = audit_out = None
    try:
        required = {'baseline_profile', 'legacy_run', 'rust_baseline_run', 'qa_profile',
                    'api_profile', 'api_closure_tar', 'collector_out', 'audit_out'}
        inputs = ctx.data.get('m6_react_inputs')
        require(type(inputs) is dict and set(inputs) == required
                and all(type(value) is str and value for value in inputs.values()),
                'explicit M6 owner inputs are unavailable')
        baseline_profile = inputs['baseline_profile']
        require(baseline_profile.startswith('som-rs/scripts/hosted_readiness/')
                and baseline_profile.endswith('.json') and '..' not in Path(baseline_profile).parts,
                'admitted baseline profile source path required')
        ctx.sources.read(baseline_profile)
        paths = {name: physical(Path(inputs[name])) for name in
                 ('legacy_run', 'rust_baseline_run', 'qa_profile', 'api_profile', 'api_closure_tar')}
        require(paths['legacy_run'].is_dir() and paths['rust_baseline_run'].is_dir()
                and all(paths[name].is_file() for name in ('qa_profile', 'api_profile', 'api_closure_tar')),
                'explicit M6 input paths have wrong type')
        collector = physical(Path(inputs['collector_out']), exists=False)
        audit_out = physical(Path(inputs['audit_out']), exists=False)
        for path in (collector, audit_out):
            require(repo / '.local' in path.parents and path.parent.is_dir() and not path.exists(),
                    'fresh repo-local collector and audit paths required')
        require(collector != audit_out and collector not in audit_out.parents
                and audit_out not in collector.parents, 'collector and audit outputs overlap')
        api = strict_json(read(paths['api_profile'], 1024 * 1024))
        require(type(api) is dict and api.get('version') == 'som.react-linux-api-profile.v1'
                and api.get('node_profile') == str(paths['qa_profile']), 'explicit API/QA profiles differ')
        python = physical(Path(api['venv'])) / 'bin/python'
        require(python.is_file(), 'qualified API venv Python unavailable')
        candidate_dir = physical(candidate_dir)
        require(candidate_dir.is_dir(), 'actual candidate layout required')
        report['input_paths'] = {'candidate_dir': str(candidate_dir), 'collector_out': str(collector),
                                 'audit_out': str(audit_out), 'baseline_profile': baseline_profile,
                                 **{name: str(path) for name, path in paths.items()}}
        ctx.verify()
        current = services.start_current()
        require(current['url'] == 'http://127.0.0.1:18978'
                and Path(current['candidate_dir']) == candidate_dir, 'shared current service differs')
        baseline = services.start_baseline(baseline_profile)
        require(baseline['url'] == 'http://127.0.0.1:18979', 'shared baseline service differs')
        module = sys.modules.get('full_product_context')
        require(module is not None and callable(getattr(module, 'tool_command', None))
                and module.context() is ctx, 'active source-bound Context required')
        env = safe_child_environment(ctx, os.environ)
        args = collector_arguments(candidate_dir=candidate_dir, collector_out=collector,
            baseline_url=baseline['url'], legacy_run=paths['legacy_run'],
            rust_baseline_run=paths['rust_baseline_run'], python=python,
            qa_profile=paths['qa_profile'], api_profile=paths['api_profile'])
        report['rows'].append(run_command(ctx, module, repo, env, out, report, 'collector', COLLECTOR, args, 18000))
        ctx.verify(); services.verify_all()
        if collector.is_dir():
            audit_args = auditor_arguments(repo=repo, collector_out=collector, audit_out=audit_out,
                                           api_closure_tar=paths['api_closure_tar'])
            report['rows'].append(run_command(ctx, module, repo, env, out, report, 'audit', AUDITOR, audit_args, 7200))
            ctx.verify(); services.verify_all()
        else:
            raise ValueError('collector output was not produced')
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        # Preserve partial failures. A copied snapshot is data, not a restored-run proof.
        try:
            if collector is not None and collector.is_dir():
                report['collector_capture'] = copy_capture(collector, out / 'collector')
        except BaseException as error:
            report['capture_error'] = type(error).__name__ + ': ' + str(error)
        try:
            if audit_out is not None and audit_out.is_file():
                raw = read(audit_out)
                save(out / 'audit.json', raw)
                report['audit_sha256'] = sha(raw)
        except BaseException as error:
            report['audit_capture_error'] = type(error).__name__ + ': ' + str(error)
        finalize_report(ctx, out, report)
    return report


def partial_output_inventory(root):
    """List safe retained files after a failed strict inventory, without following links."""
    files, omitted, errors = {}, [], []
    def failed(error):
        errors.append({'path': '.', 'error': type(error).__name__})
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=failed):
        safe_dirs = []
        for name in sorted(dirs):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
                    safe_dirs.append(name)
                else:
                    omitted.append(rel)
            except OSError as error:
                errors.append({'path': rel, 'error': type(error).__name__})
        dirs[:] = safe_dirs
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISREG(mode) and not stat.S_ISLNK(mode):
                    files[rel] = sha(read(path))
                else:
                    omitted.append(rel)
            except (OSError, ValueError) as error:
                errors.append({'path': rel, 'error': type(error).__name__})
    return {'files': dict(sorted(files.items())), 'omitted': omitted, 'errors': errors,
            'complete': False, 'source_stability_verified': False}


def finalize_report(ctx, out, report):
    """Retain the original outcome when bookkeeping or result writing fails."""
    errors = report.setdefault('finalization_errors', [])
    try:
        report['bound_source_sha256'] = dict(sorted(ctx.sources.pins.items()))
    except BaseException as error:
        errors.append({'stage': 'source_pins', 'error': type(error).__name__ + ': ' + str(error)})
        report['bound_source_sha256'] = {}
    try:
        report['saved_files_sha256'] = inventory(out)
    except BaseException as error:
        errors.append({'stage': 'inventory', 'error': type(error).__name__ + ': ' + str(error)})
        try:
            partial = partial_output_inventory(out)
            report['saved_files_sha256'] = partial['files']
            report['partial_output_inventory'] = partial
        except BaseException as secondary:
            errors.append({'stage': 'partial_inventory', 'error': type(secondary).__name__ + ': ' + str(secondary)})
            report['saved_files_sha256'] = {}
    try:
        save(out / 'result.json', canonical(report) + b'\n')
    except BaseException as error:
        errors.append({'stage': 'write_result', 'error': type(error).__name__ + ': ' + str(error)})
        # One bounded alternate file. No recursion or deletion of partial data.
        try:
            save(out / 'result.partial.json', canonical(report) + b'\n')
        except BaseException as secondary:
            errors.append({'stage': 'write_partial_result', 'error': type(secondary).__name__ + ': ' + str(secondary)})


def copy_capture(source, destination):
    """Retain safe files despite incomplete runtime; report every omission."""
    source = physical(source)
    destination = physical(destination, exists=False)
    require(source.is_dir() and not destination.exists() and destination not in source.parents
            and source not in destination.parents, 'separate new capture destination required')
    destination.mkdir(mode=0o700)
    copied, excluded, errors = {}, [], []
    def scan_error(error):
        name = getattr(error, 'filename', None)
        try:
            relative = str(Path(name).relative_to(source)) if name else '.'
        except ValueError:
            relative = '.'
        errors.append({'path': relative, 'error': type(error).__name__})
    for directory, dirs, names in os.walk(source, topdown=True, followlinks=False, onerror=scan_error):
        accepted_dirs = []
        for name in sorted(dirs):
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    excluded.append({'path': relative, 'reason': 'symlink_not_followed'})
                elif stat.S_ISDIR(mode):
                    accepted_dirs.append(name)
                else:
                    excluded.append({'path': relative, 'reason': 'non_directory_not_followed'})
            except OSError as error:
                errors.append({'path': relative, 'error': type(error).__name__})
        dirs[:] = accepted_dirs
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    excluded.append({'path': relative, 'reason': 'symlink_not_followed'})
                    continue
                if not stat.S_ISREG(mode):
                    excluded.append({'path': relative, 'reason': 'special_file_not_copied'})
                    continue
                raw = read(path)
                save(destination / relative, raw)
                copied[relative] = sha(raw)
            except (OSError, ValueError) as error:
                errors.append({'path': relative, 'error': type(error).__name__})
    return {'version': 'som.m6-partial-collector-capture.v1',
            'copied_sha256': dict(sorted(copied.items())), 'excluded': excluded, 'errors': errors,
            'unverified_runtime_or_evidence': bool(excluded or errors),
            'all_observed_files_copied': not excluded and not errors,
            'source_stability_verified': False, 'file_modes_preserved': False,
            'empty_directories_preserved': False, 'sealed_transport_verified': False,
            'complete': False}


def command_evidence(ctx, out, name, command):
    """Copy actual raw streams, including partial output after command failure."""
    record = dict(command)
    row = {'id': name, 'command': record, 'streams_complete': False, 'postcheck': None,
           'capture_errors': []}
    streams = 0
    for kind in ('stdout', 'stderr'):
        if kind not in command:
            continue
        try:
            data = read(ctx.work / command[kind])
            relative = 'logs/' + name + '.' + kind
            save(out / relative, data)
            row[kind] = relative
            row[kind + '_observed_sha256'] = sha(data)
            require(row[kind + '_observed_sha256'] == command[kind + '_sha256'], 'command log changed')
            streams += 1
        except (OSError, ValueError, KeyError, TypeError) as error:
            row['capture_errors'].append({'stage': kind, 'error': type(error).__name__ + ': ' + str(error)})
    row['streams_complete'] = streams == 2
    if type(command.get('pid')) is int:
        try:
            path = ctx.work / 'checker-postchecks' / (str(command['pid']) + '.json')
            if path.is_file():
                post = read(path)
                relative = 'logs/' + name + '.postcheck.json'
                save(out / relative, post)
                row.update(postcheck=relative, postcheck_sha256=sha(post))
        except (OSError, ValueError, KeyError, TypeError) as error:
            row['capture_errors'].append({'stage': 'postcheck', 'error': type(error).__name__ + ': ' + str(error)})
    return row


def fallback_command_streams(ctx, out, name, command):
    """One bounded best-effort copy after an unexpected evidence-handler error."""
    row = {'id': name, 'command': dict(command), 'streams_complete': False, 'postcheck': None,
           'capture_errors': [], 'fallback_unverified': True}
    for kind in ('stdout', 'stderr'):
        if kind not in command:
            row['capture_errors'].append({'stage': kind, 'error': 'original stream path unavailable'})
            continue
        try:
            relative_source = Path(command[kind])
            require(not relative_source.is_absolute() and relative_source.parts
                    and all(part not in ('', '.', '..') for part in relative_source.parts),
                    'unsafe original stream path')
            raw = read(ctx.work / relative_source)
            relative_dest = 'logs/' + name + '.' + kind
            destination = out / relative_dest
            if destination.exists() or destination.is_symlink():
                require(read(destination) == raw, 'previously retained stream bytes differ')
            else:
                save(destination, raw)
            row[kind] = relative_dest
            row[kind + '_observed_sha256'] = sha(raw)
            require(row[kind + '_observed_sha256'] == command[kind + '_sha256'],
                    'fallback stream hash differs')
        except (OSError, ValueError, KeyError, TypeError) as error:
            row['capture_errors'].append({'stage': kind, 'error': type(error).__name__ + ': ' + str(error)})
    return row


def run_command(ctx, module, repo, env, out, report, label, script, arguments, timeout):
    """Keep recorded failure data even if Context.command raises on timeout."""
    prior = len(ctx.cache.get('commands', []))
    try:
        command = ctx.command(module.tool_command(repo / script, arguments), repo, env,
                              'm6-react-' + label, timeout=timeout, check=False)
    except BaseException:
        try:
            rows = ctx.cache.get('commands', [])
            if len(rows) == prior + 1:
                report['rows'].append(command_evidence(ctx, out, label, rows[-1]))
        except BaseException as error:
            report.setdefault('command_evidence_errors', []).append(
                {'stage': label, 'error': type(error).__name__ + ': ' + str(error)})
            try:
                rows = ctx.cache.get('commands', [])
                if len(rows) == prior + 1:
                    report['rows'].append(fallback_command_streams(ctx, out, label, rows[-1]))
            except BaseException as secondary:
                report.setdefault('command_evidence_errors', []).append(
                    {'stage': label + '_fallback', 'error': type(secondary).__name__ + ': ' + str(secondary)})
        raise
    return command_evidence(ctx, out, label, command)


def validate_args(args, ctx, graph):
    repo = physical(args.repo)
    require(ctx.repo == repo and args.qa_mode == 'native-qa-v1', 'repository or mode differs')
    require(ctx.data.get('assembly_root') == str(physical(args.assembly_root)), 'assembly root differs')
    require(ctx.data.get('candidate_revision') == args.candidate_revision, 'candidate differs')
    # The graph's carrier restore and current assembly proof remain independent inputs.
    physical(args.carrier_root)
    compile_proof = graph.verify_compile_proof(args.assembly_root, args.candidate_revision)
    candidate = physical(args.candidate_dir)
    build = json.loads(read(candidate / 'build.json'))
    runtime = json.loads(read(candidate / 'runtime.json'))
    binary = read(candidate / 'som')
    require(build['tree_before']['head'] == args.candidate_revision and build['complete'] is True
            and build['binary_sha256'] == runtime['binary_sha256'] == sha(binary)
            and sha(binary) == compile_proof['binary_identity']['sha256']
            and runtime['port'] == args.port == 18978, 'candidate build, binary or port differs')
    require(args.baseline == 'http://127.0.0.1:18979', 'owned baseline port differs')
    for item in (args.legacy_run, args.rust_baseline_run, args.qa_profile, args.api_profile, args.api_closure_tar):
        physical(item)
    # Collector/auditor Reader currently requires this run to be under the repo.
    collector_out = physical(args.collector_out, exists=False)
    require(repo / '.local' in collector_out.parents and not collector_out.exists(),
            'fresh repo-local collector output required')
    out = physical(args.out, exists=False)
    temporary = physical(Path(os.environ['RUNNER_TEMP']))
    require(temporary in out.parents and not out.exists() and out.parent.is_dir(),
            'fresh RUNNER_TEMP callback output required')
    require(ctx.data.get('runtime_root') is not None, 'qualified runtime context missing')
    return compile_proof


def safe_child_environment(ctx, environ):
    env = ctx.child_environment()
    for key in ('RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH', 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        if key in environ:
            env[key] = environ[key]
    env.pop('UV_NO_INSTALLER_METADATA', None)
    env.pop('SOM_DEPENDENCY_ACTIONS_TOKEN', None)
    return env


def collector_arguments(*, candidate_dir, collector_out, baseline_url, legacy_run,
                        rust_baseline_run, python, qa_profile, api_profile):
    """Map explicit owner inputs to the existing collector CLI without running it."""
    return ['--candidate', str(candidate_dir), '--out', str(collector_out),
            '--baseline', baseline_url, '--legacy-run', str(legacy_run),
            '--rust-baseline-run', str(rust_baseline_run), '--python', str(python),
            '--protected-default', 'absent', '--cleanup-products-runtime', '--record-raw-http',
            '--qa-profile', str(qa_profile), '--api-profile', str(api_profile)]


def auditor_arguments(*, repo, collector_out, audit_out, api_closure_tar):
    """Map explicit raw inputs to the existing auditor CLI without running it."""
    return ['--repo', str(repo), '--run', str(collector_out), '--out', str(audit_out),
            '--api-closure-tar', str(api_closure_tar)]


def run(args):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and
            os.environ.get('RUNNER_OS') == 'Linux' and sys.platform == 'linux',
            'GitHub-hosted Linux runner required')
    require(GRAPH_INTEGRATION_REVIEWED, 'M6 graph integration is unreviewed; product run not admitted')
    repo = physical(args.repo)
    physical(args.product_context)
    graph, sources, authority, module, ctx = admit(repo, args.helper_revision, args.candidate_revision,
                                                   args.product_context)
    proof = validate_args(args, ctx, graph)
    out = args.out
    out.mkdir(mode=0o700)
    report = {'version': 'som.m6-react-graph-callback.v1', 'gate': GATE,
              'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
              'source_admission': authority, 'compile_proof': proof,
              'collector_out': str(args.collector_out), 'rows': [],
              'product_acceptance_passed': False, 'complete': False,
              'all_required_passed': False, 'owned_waits_complete': False,
              'owned_cleanup_complete': False, 'raw_logs_complete': False,
              'remaining': ['source-bound candidate and baseline owner/cleanup join',
                            'sealed restored-run mapping and independent verifier',
                            'collector child source routing and reviewed React003 authority']}
    env = safe_child_environment(ctx, os.environ)
    api_profile = json.loads(read(args.api_profile, 1024 * 1024))
    venv = physical(Path(api_profile['venv']))
    python = venv / 'bin/python'
    require(python.is_file(), 'live venv interpreter missing')
    collect_args = collector_arguments(candidate_dir=args.candidate_dir, collector_out=args.collector_out,
        baseline_url=args.baseline, legacy_run=args.legacy_run, rust_baseline_run=args.rust_baseline_run,
        python=python, qa_profile=args.qa_profile, api_profile=args.api_profile)
    audit_out = repo / '.local' / ('m6-react-audit-' + out.name + '.json')
    physical(audit_out, exists=False)
    require(not audit_out.exists(), 'fresh audit output required')
    try:
        report['rows'].append(run_command(ctx, module, repo, env, out, report, 'collector', COLLECTOR,
                                          collect_args, timeout=18000))
        ctx.verify(); sources.verify()
        if args.collector_out.is_dir():
            audit_args = auditor_arguments(repo=repo, collector_out=args.collector_out,
                                          audit_out=audit_out, api_closure_tar=args.api_closure_tar)
            report['rows'].append(run_command(ctx, module, repo, env, out, report, 'audit', AUDITOR,
                                              audit_args, timeout=7200))
            if audit_out.is_file():
                audit_raw = read(audit_out)
                save(out / 'audit.json', audit_raw)
                report['audit_sha256'] = sha(audit_raw)
            ctx.verify(); sources.verify()
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        report['bound_source_sha256'] = dict(sorted({**sources.pins, **ctx.sources.pins}.items()))
        report['saved_files_sha256'] = inventory(out)
        save(out / 'result.json', canonical(report) + b'\n')
    # Even a successful collector and auditor do not verify service cleanup or
    # prove that a restored raw run is the same run. The graph must provide both.
    return 1


def strict_json(raw):
    def unique(pairs):
        output = {}
        for key, value in pairs:
            require(key not in output, 'duplicate callback JSON key')
            output[key] = value
        return output
    def nonfinite(_):
        raise ValueError('nonfinite callback JSON number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def check_local_capture_consistency(root, source_reader, helper_revision, candidate_revision, gate=GATE):
    """Check retained bytes only. This does not verify a process, audit or restored run."""
    root = physical(root)
    require(root.is_dir() and gate == GATE and HEX.fullmatch(helper_revision or '') is not None
            and helper_revision == candidate_revision, 'same callback identity required')
    result = strict_json(read(root / 'result.json'))
    require(isinstance(result, dict) and result.get('version') == 'som.m6-react-graph-callback.v1'
            and result.get('gate') == gate and result.get('helper_revision') == helper_revision
            and result.get('candidate_revision') == candidate_revision
            and result.get('product_acceptance_passed') is False, 'callback result identity differs')
    files = result.get('saved_files_sha256')
    require(isinstance(files, dict), 'callback inventory missing')
    observed = inventory(root)
    observed.pop('result.json', None)
    require(observed == files, 'callback evidence file universe differs')
    pins = result.get('bound_source_sha256')
    require(isinstance(pins, dict) and SELF in pins and all(isinstance(k, str) and isinstance(v, str)
            and re.fullmatch(r'[0-9a-f]{64}', v) is not None for k, v in pins.items()),
            'callback source pins missing')
    for path, pin in pins.items():
        require(sha(source_reader(path)) == pin, 'callback source bytes differ')
    rows = result.get('rows')
    require(isinstance(rows, list) and 1 <= len(rows) <= 2 and all(isinstance(row, dict) for row in rows)
            and [row.get('id') for row in rows] in (['collector'], ['collector', 'audit']),
            'callback observed row order differs')
    for row in rows:
        name = row['id']
        command = row.get('command')
        require(isinstance(command, dict) and isinstance(command.get('argv'), list)
                and all(isinstance(arg, str) for arg in command['argv']), 'observed argv missing')
        for kind in ('stdout', 'stderr'):
            relative = row.get(kind)
            if relative is None:
                require(kind not in command and kind + '_sha256' not in command, 'unretained stream claimed')
                continue
            require(relative == 'logs/' + name + '.' + kind and relative in files
                    and files[relative] == command.get(kind + '_sha256'), 'retained stream differs')
        require(row.get('streams_complete') is all(row.get(kind) is not None for kind in ('stdout', 'stderr')),
                'stream completeness claim differs')
        post = row.get('postcheck')
        if post is not None:
            require(post == 'logs/' + name + '.postcheck.json' and post in files
                    and row.get('postcheck_sha256') == files[post], 'postcheck bytes differ')
            strict_json(read(root / post))
        else:
            require(row.get('postcheck_sha256') is None, 'missing postcheck hash differs')
    audit = result.get('audit_sha256')
    require((audit is None and 'audit.json' not in files) or
            (audit is not None and files.get('audit.json') == audit), 'audit bytes differ')
    if audit is not None:
        strict_json(read(root / 'audit.json'))
    # Consistent files can still be forged, incomplete or bound to another run.
    return {'version': 'som.m6-react-local-capture-consistency.v1', 'gate': gate,
            'observed_rows': [row['id'] for row in rows], 'local_bytes_consistent': True,
            'process_waits_verified': False, 'service_cleanup_verified': False,
            'audit_semantics_verified': False, 'sealed_transport_verified': False,
            'product_acceptance_passed': False}


def verify_result(root, source_reader, helper_revision, candidate_revision, gate=GATE, *, repo, proof_roots):
    """Fail closed until graph-owned service cleanup and sealed run join exist."""
    check_local_capture_consistency(root, source_reader, helper_revision, candidate_revision, gate)
    raise ValueError('M6 callback independent graph verifier is not implemented')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'assembly-root', 'carrier-root', 'product-context', 'out', 'candidate-dir',
                 'legacy-run', 'rust-baseline-run', 'qa-profile', 'api-profile', 'api-closure-tar',
                 'collector-out'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision', 'qa-mode', 'baseline'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--port', type=int, required=True)
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
