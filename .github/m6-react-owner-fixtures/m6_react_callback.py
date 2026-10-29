#!/usr/bin/env python3
"""Draft: run M6 collector and audit against one source-bound candidate.

This is not acceptance. The caller must supply the live owned services, inputs,
and checked transport. Result verification is intentionally unavailable until
launcher cleanup and cross-job restored-run identities have defined verifiers.
"""
from __future__ import annotations

import argparse
import contextlib
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
LAUNCHER = 'som-rs/scripts/hosted_readiness/run_current_candidate_gate.py'
GATE = 'm6-react003'
# Fail closed until the graph supplies reviewed source, owner and restored-run joins.
GRAPH_INTEGRATION_REVIEWED = False
OWNER_INTEGRATION_REVIEWED = False
INPUT_COMMIT = '1de8f05430a72f8d76f6159786cca71b0ae1a911'
INPUT_INDEX_SHA = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
ORIGINAL_INPUT_COMMIT = '55eb7f5c3b21d4f3b57eb721ab60c6aa97e10db3'
ORIGINAL_INPUT_INDEX_SHA = '78375feb14ed286ac5df4c52f13d5d1ae301b2057d5e3894edf0f36c2705c498'
AUDIT_BASE = 'som-rs/scripts/react_output_acceptance/audit/'
AUDIT_MODULES = ('render_rules', 'refusal_rules', 'api_raw', 'linux_install_map', 'linux_input_raw', 'baseline_raw', 'audit130')
INPUT_PROFILE = 'som-rs/contracts/react-output-v1/native-owner-input-authority.json'
DERIVED_SOURCES = {
    'som-rs/scripts/react_output_acceptance/native_owner_input_binding.py': 'ed8b910fbc79087231050fa5883cc2ca71ddfcdb881a289e32dcf41ce5f34e2d',
    'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py': 'f7231671f41cb78cfe50e195159f54c644baa1f65a0a186c52f2abc2b8877e41',
    'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py': '82efed50d624cfe1929de5448ca756bbec5c28f8f04ba652fe8286fc285f5d1b',
    'som-rs/scripts/react_output_acceptance/historical-response-reuse-map.json': '3b06627c31626238960d38ddb9e772e57c900a98a28ba6407790ca8196edd8ca',
}
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
                                           api_closure_tar=paths['api_closure_tar'], candidate_dir=candidate_dir)
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


def scan_capture_source(source):
    """Record every regular file, directory and excluded entry without following links."""
    source = physical(source)
    files, directories, excluded = {}, {}, []
    def failed(_):
        raise ValueError('collector source scan failed')
    for directory, dirs, names in os.walk(source, topdown=True, followlinks=False, onerror=failed):
        accepted = []
        for name in sorted(dirs):
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                directories[relative] = stat.S_IMODE(info.st_mode)
                accepted.append(name)
            else:
                excluded.append({'path': relative, 'reason': 'symlink_not_followed' if stat.S_ISLNK(info.st_mode) else 'non_directory_not_followed'})
        dirs[:] = accepted
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                excluded.append({'path': relative, 'reason': 'symlink_not_followed'})
            elif not stat.S_ISREG(before.st_mode):
                excluded.append({'path': relative, 'reason': 'special_file_not_copied'})
            else:
                raw = read(path)
                after = path.lstat()
                require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
                        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode),
                        'collector source changed during scan')
                files[relative] = {'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(after.st_mode)}
    return {'files': dict(sorted(files.items())), 'directories': dict(sorted(directories.items())),
            'excluded': sorted(excluded, key=lambda row: row['path'])}


def copy_capture(source, destination):
    """Retain safe files despite incomplete runtime; report every omission."""
    source = physical(source)
    destination = physical(destination, exists=False)
    require(source.is_dir() and not destination.exists() and destination not in source.parents
            and source not in destination.parents, 'separate new capture destination required')
    destination.mkdir(mode=0o700)
    copied, excluded, errors = {}, [], []
    try:
        before = scan_capture_source(source)
    except (OSError, ValueError) as error:
        before = None
        errors.append({'path': '.', 'error': type(error).__name__, 'stage': 'before_scan'})
    if before is not None:
        for name, mode in sorted(before['directories'].items(), key=lambda item: (len(Path(item[0]).parts), item[0])):
            try:
                (destination / name).mkdir(parents=True, exist_ok=True)
                os.chmod(destination / name, mode)
            except OSError as error:
                errors.append({'path': name, 'error': type(error).__name__, 'stage': 'copy_directory'})
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
                os.chmod(destination / relative, stat.S_IMODE(mode))
                copied[relative] = sha(raw)
            except (OSError, ValueError) as error:
                errors.append({'path': relative, 'error': type(error).__name__})
    after = None
    try:
        after = scan_capture_source(source)
    except (OSError, ValueError) as error:
        errors.append({'path': '.', 'error': type(error).__name__, 'stage': 'after_scan'})
    stable = before is not None and before == after and not errors
    modes = stable and all(stat.S_IMODE((destination / name).stat().st_mode) == value['mode']
                           and copied.get(name) == value['sha256'] for name, value in before['files'].items())
    try:
        retained_scan = scan_capture_source(destination)
        directories_preserved = bool(stable and retained_scan['directories'] == before['directories'])
    except (OSError, ValueError):
        directories_preserved = False
    return {'version': 'som.m6-partial-collector-capture.v1',
            'copied_sha256': dict(sorted(copied.items())), 'excluded': excluded, 'errors': errors,
            'unverified_runtime_or_evidence': bool(excluded or errors),
            'all_observed_files_copied': not excluded and not errors,
            'source_snapshot': before, 'source_stability_verified': stable,
            'file_modes_preserved': bool(modes and not excluded), 'regular_file_modes_preserved': bool(modes),
            'empty_directories_preserved': directories_preserved, 'sealed_transport_verified': False,
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
           'capture_errors': [], 'fallback_unverified': True, 'unverified_existing_streams': []}
    for kind in ('stdout', 'stderr'):
        relative_dest = 'logs/' + name + '.' + kind
        destination = out / relative_dest
        existing = None
        try:
            if destination.exists() or destination.is_symlink():
                existing = read(destination)
                row[kind] = relative_dest
                row[kind + '_observed_sha256'] = sha(existing)
                row['unverified_existing_streams'].append(kind)
        except (OSError, ValueError, TypeError) as error:
            row['capture_errors'].append({'stage': kind + '_existing', 'error': type(error).__name__ + ': ' + str(error)})
            continue
        if kind not in command:
            row['capture_errors'].append({'stage': kind, 'error': 'original stream path unavailable'})
            continue
        try:
            relative_source = Path(command[kind])
            require(not relative_source.is_absolute() and relative_source.parts
                    and all(part not in ('', '.', '..') for part in relative_source.parts),
                    'unsafe original stream path')
            raw = read(ctx.work / relative_source)
            if existing is not None:
                require(existing == raw, 'previously retained stream bytes differ')
                row['unverified_existing_streams'].remove(kind)
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
    try:
        return command_evidence(ctx, out, label, command)
    except Exception as error:
        report.setdefault('command_evidence_errors', []).append(
            {'stage': label, 'error': type(error).__name__ + ': ' + str(error)})
        try:
            return fallback_command_streams(ctx, out, label, command)
        except Exception as secondary:
            report.setdefault('command_evidence_errors', []).append(
                {'stage': label + '_fallback', 'error': type(secondary).__name__ + ': ' + str(secondary)})
            return {'id': label, 'command': dict(command), 'streams_complete': False,
                    'postcheck': None, 'capture_errors': [], 'fallback_unverified': True}


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


def auditor_arguments(*, repo, collector_out, audit_out, api_closure_tar, candidate_dir=None):
    """Map explicit raw inputs to the existing auditor CLI without running it."""
    result = ['--repo', str(repo), '--run', str(collector_out), '--out', str(audit_out),
              '--api-closure-tar', str(api_closure_tar)]
    if candidate_dir is not None:
        result += ['--owned-candidate-root', str(candidate_dir)]
    return result


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
    # The launcher may write exactly this second, separately verified result
    # after the first verification. verify_result checks its content below.
    observed.pop('verified-result.json', None)
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


def approved_manifest(repo, commit=INPUT_COMMIT, index_sha=INPUT_INDEX_SHA, count=18):
    """Read the exact human-approved input index and its bound manifests."""
    def blob(name):
        require(re.fullmatch(r'inputs-index[.]json|package-[0-9]{3}[.]manifest[.]json', name) is not None,
                'approved manifest name differs')
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo),
                                        'show', commit + ':' + name],
                                       stderr=subprocess.DEVNULL, timeout=60)
    raw = blob('inputs-index.json')
    require(sha(raw) == index_sha, 'approved input index differs')
    index = strict_json(raw)
    require(type(index) is dict and index.get('version') == 'som.m6-input-index.v1'
            and type(index.get('packages')) is list and len(index['packages']) == count,
            'approved input index schema differs')
    result = {}
    for entry in index['packages']:
        raw = blob(entry['manifest'])
        require(sha(raw) == entry['manifest_sha256'], 'approved input manifest differs')
        manifest = strict_json(raw)
        require(manifest.get('version') == 'som.m6-sealed-inputs-manifest.v1'
                and manifest.get('total_files') == len(manifest.get('files', [])), 'input manifest schema differs')
        for file in manifest['files']:
            name = file['path']
            require(type(name) is str and name.startswith('.local/') and '..' not in Path(name).parts
                    and name not in result and re.fullmatch(r'[0-9a-f]{64}', file['sha256']) is not None,
                    'approved input member differs')
            require(type(file.get('bytes')) is int and file['bytes'] >= 0 and file.get('mode') in (0o644, 0o755),
                    'approved input size or mode differs')
            result[name] = {'sha256': file['sha256'], 'bytes': file['bytes'], 'mode': file['mode']}
    return result


@contextlib.contextmanager
def source_bound_derived_inputs(source_reader, repo):
    """Re-run the independently reviewed two-source input join (no wire claim)."""
    profile = strict_json(source_reader(INPUT_PROFILE))
    require(profile == {'version': 'som.m6-native-owner-input-authority.v1',
        'approved_commit': INPUT_COMMIT, 'approved_index_sha256': INPUT_INDEX_SHA,
        'original_commit': ORIGINAL_INPUT_COMMIT, 'original_index_sha256': ORIGINAL_INPUT_INDEX_SHA,
        'derived_source_sha256': DERIVED_SOURCES,
        'later_artifact': {'id': 12843589912, 'name': 'm6-sealed-raw-evidence',
            'size': 249935784, 'sha256': '33fbad9d63cfa257636384b9b7c10e1406f2306922e4f28e8189c79f703b041e',
            'run_id': 40743738705, 'head_sha': '40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0',
            'repository_id': 1390374878, 'index_sha256': '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea'}},
        'tracked React input authority differs')
    temp = physical(Path(os.environ['RUNNER_TEMP']))
    metadata = strict_json(read(temp / 'metadata.json', 1024 * 1024))
    artifact = profile['later_artifact']
    origin = metadata.get('workflow_run')
    require(type(origin) is dict and metadata.get('id') == artifact['id']
            and metadata.get('name') == artifact['name'] and metadata.get('size_in_bytes') == artifact['size']
            and metadata.get('digest') == 'sha256:' + artifact['sha256'] and metadata.get('expired') is False
            and origin.get('id') == artifact['run_id'] and origin.get('head_sha') == artifact['head_sha']
            and origin.get('repository_id') == artifact['repository_id'], 'later artifact origin differs')
    archive = physical(temp / 'evidence.zip')
    require(archive.is_file() and archive.stat().st_size == artifact['size'], 'later artifact size differs')
    with archive.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == artifact['sha256'], 'later artifact bytes differ')
    sources = {path: source_reader(path) for path in DERIVED_SOURCES}
    require(all(type(raw) is bytes and sha(raw) == DERIVED_SOURCES[path] for path, raw in sources.items()),
            'reviewed two-source verifier bytes differ')
    names = [('reconstruct_historical_responses', 'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py'),
             ('verify_historical_response_provenance', 'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py'),
             ('_som_m6_reviewed_derived_inputs', 'som-rs/scripts/react_output_acceptance/native_owner_input_binding.py')]
    owned, previous_path = {}, list(sys.path)
    try:
        for name, path in names:
            require(name not in sys.modules, 'preloaded derived-input verifier refused')
            module = ModuleType(name)
            module.__file__, module.__package__, module.__cached__ = str(repo / path), '', None
            module.__som_raw_sha256__ = sha(sources[path])
            sys.modules[name] = module; owned[name] = module
            exec(compile(sources[path], module.__file__, 'exec', dont_inherit=True), module.__dict__)
            require(sys.modules.get(name) is module, 'derived-input verifier identity changed')
        bound = owned['_som_m6_reviewed_derived_inputs'].approved_inputs(
            repo / '.local/rust-compiler-goal/acceptance/public-products-003b',
            repo / '.local/rust-compiler-goal/acceptance/legacy544/run-005',
            temp / 'm6-final-input-config.json', temp)
        require(type(bound.files) is dict and bound.repo == repo, 'two-source result differs')
        yield bound
        require(all(sys.modules.get(name) is module for name, module in owned.items()),
                'derived-input verifier identity changed')
    finally:
        sys.path[:] = previous_path
        for name, module in reversed(tuple(owned.items())):
            if sys.modules.get(name) is module:
                del sys.modules[name]


@contextlib.contextmanager
def source_bound_auditor(source_reader, repo):
    """Load the auditor and its actual imports only from exact bound source bytes."""
    owned, previous_path = {}, list(sys.path)
    try:
        for name in AUDIT_MODULES:
            require(name not in sys.modules, 'preloaded React auditor module refused')
            path = AUDIT_BASE + name + '.py'
            raw = source_reader(path)
            require(type(raw) is bytes and raw, 'source-bound React auditor bytes required')
            module = ModuleType(name)
            module.__file__, module.__package__, module.__cached__ = str(repo / path), '', None
            module.__som_raw_sha256__ = sha(raw)
            sys.modules[name] = module
            owned[name] = module
            exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
            require(sys.modules.get(name) is module, 'React auditor module identity changed')
        yield owned['audit130']
        require(all(sys.modules.get(name) is module for name, module in owned.items()),
                'React auditor module identity changed')
    finally:
        sys.path[:] = previous_path
        for name, module in reversed(tuple(owned.items())):
            if sys.modules.get(name) is module:
                del sys.modules[name]


def verify_react_raw_domain(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots, shared):
    """Rederive the React audit from retained bytes and separately bound inputs."""
    repo = physical(repo)
    check_local_capture_consistency(root, source_reader, helper_revision, candidate_revision, gate)
    capture = strict_json(read(root / 'result.json'))
    require(not capture.get('error') and not capture.get('capture_error')
            and not capture.get('audit_capture_error') and not capture.get('finalization_errors')
            and not capture.get('command_evidence_errors'), 'React capture has failure evidence')
    context_raw = read(root.parent / 'context.input.json')
    context = strict_json(context_raw)
    require(sha(context_raw) == shared.get('context_sha256') and type(context) is dict
            and context.get('repo') == str(repo) and context.get('helper_revision') == candidate_revision
            and context.get('candidate_revision') == candidate_revision and context.get('qa_mode') == 'native-qa-v1',
            'React command context differs from shared proof')
    inputs = context.get('m6_react_inputs')
    expected_input_keys = {'baseline_profile', 'legacy_run', 'rust_baseline_run', 'qa_profile',
                           'api_profile', 'api_closure_tar', 'collector_out', 'audit_out'}
    require(type(inputs) is dict and set(inputs) == expected_input_keys
            and all(type(value) is str and value for value in inputs.values()), 'React context inputs differ')
    paths = capture.get('input_paths')
    require(type(paths) is dict and set(paths) == expected_input_keys | {'candidate_dir'}
            and all(paths[name] == inputs[name] for name in expected_input_keys),
            'React recorded input paths differ from source-bound context')
    original = physical(paths['collector_out'])
    candidate = physical(paths['candidate_dir'])
    require(repo / '.local' in original.parents and candidate == root.parent / 'candidate',
            'React run or owned candidate path differs')
    api = strict_json(read(physical(paths['api_profile']), 1024 * 1024))
    require(type(api) is dict and api.get('version') == 'som.react-linux-api-profile.v1'
            and api.get('node_profile') == paths['qa_profile'] and type(api.get('venv')) is str,
            'React API profile differs')
    python = physical(Path(api['venv'])) / 'bin/python'
    collector_args = collector_arguments(candidate_dir=candidate, collector_out=original,
        baseline_url='http://127.0.0.1:18979', legacy_run=paths['legacy_run'],
        rust_baseline_run=paths['rust_baseline_run'], python=python,
        qa_profile=paths['qa_profile'], api_profile=paths['api_profile'])
    audit_args = auditor_arguments(repo=repo, collector_out=original, audit_out=Path(paths['audit_out']),
                                   api_closure_tar=paths['api_closure_tar'], candidate_dir=candidate)
    observer = context.get('observer_python')
    require(type(observer) is str and Path(observer).is_absolute(), 'React observer Python differs')
    driver = 'som-rs/scripts/hosted_readiness/run_full_products.py'
    source_reader(CONTEXT); source_reader(driver)
    expected_commands = {
        'collector': [observer, '-I', '-S', '-B', str(repo / driver), '--execute-tool', COLLECTOR, '--', *collector_args],
        'audit': [observer, '-I', '-S', '-B', str(repo / driver), '--execute-tool', AUDITOR, '--', *audit_args],
    }
    rows = capture.get('rows')
    require(type(rows) is list and [row.get('id') for row in rows] == ['collector', 'audit'],
            'both React commands are required')
    for row in rows:
        command = row['command']
        # The external-candidate auditor intentionally exits 1 until the
        # common owner rederives its process binding after service cleanup.
        expected_exit = 0 if row['id'] == 'collector' else 1
        require(row.get('streams_complete') is True and not row.get('capture_errors')
                and not row.get('fallback_unverified') and type(command.get('pid')) is int
                and command['pid'] > 0 and command.get('exit_code') == expected_exit
                and command.get('wait_completed') is True and command.get('owned_group_cleanup_passed') is True
                and row.get('postcheck') is not None and command.get('cwd') == str(repo)
                and command.get('argv') == expected_commands[row['id']],
                'React command argv, cwd, wait, cleanup or logs differ')
        post = strict_json(read(root / row['postcheck']))
        tool = COLLECTOR if row['id'] == 'collector' else AUDITOR
        require(post.get('version') == 'som.full-product-checker-postcheck.v1'
                and post.get('tool') == tool and post.get('pid') == command['pid']
                and post.get('helper_revision') == post.get('candidate_revision') == candidate_revision
                and post.get('entry_executed') is True and post.get('entry_exit_code') == expected_exit
                and post.get('source_runtime_tools_inputs_unchanged') is True
                and post.get('product_acceptance_passed') is False, 'React source postcheck differs')
        pins = post.get('source_sha256')
        require(type(pins) is dict and tool in pins and all(sha(source_reader(p)) == v for p, v in pins.items()),
                'React command source bytes differ')
    copied = capture.get('collector_capture')
    require(type(copied) is dict and type(copied.get('copied_sha256')) is dict
            and not copied.get('errors') and copied.get('source_stability_verified') is True
            and copied.get('regular_file_modes_preserved') is True, 'React retained collector inventory missing')
    snapshot = copied.get('source_snapshot')
    require(type(snapshot) is dict and snapshot == scan_capture_source(original)
            and type(snapshot.get('files')) is dict and snapshot.get('excluded') == []
            and sorted(copied.get('excluded', []), key=lambda item: item['path']) == snapshot['excluded'],
            'React collector source tree differs from captured source')
    retained = inventory(root / 'collector')
    retained_scan = scan_capture_source(root / 'collector')
    require(retained == copied['copied_sha256']
            and set(retained) == set(snapshot['files'])
            and copied.get('empty_directories_preserved') is True
            and retained_scan['directories'] == snapshot['directories'] and not retained_scan['excluded']
            and all(retained[name] == record['sha256']
                    and stat.S_IMODE((root / 'collector' / name).stat().st_mode) == record['mode']
                    and (root / 'collector' / name).stat().st_size == record['size']
                    for name, record in snapshot['files'].items()), 'React collector copy or mode differs')
    current_runtime = strict_json(read(root.parent / 'services/current/runtime.json'))
    candidate_runtime = strict_json(read(candidate / 'runtime.json'))
    require(current_runtime == candidate_runtime and current_runtime.get('pid') is not None,
            'React candidate and shared service runtime differ')
    approved = approved_manifest(repo)
    original_approved = approved_manifest(repo, ORIGINAL_INPUT_COMMIT, ORIGINAL_INPUT_INDEX_SHA, 16)
    audit_raw = read(root / 'audit.json')
    recorded = strict_json(audit_raw)
    with source_bound_auditor(source_reader, repo) as auditor:
        fresh = auditor.run_audit(repo, original, physical(paths['api_closure_tar']), candidate)
    require(fresh == recorded and fresh.get('owned_candidate_process_binding') == 'unverified'
            and fresh.get('run') == original.relative_to(repo).as_posix(), 'fresh React raw audit differs')
    totals = fresh.get('totals')
    require(type(totals) is dict and totals.get('failed') == totals.get('unrun') == 0
            and type(fresh.get('rows')) is list and bool(fresh['rows']) and not fresh.get('missing_files'),
            'React named raw checks are incomplete')
    allowed_attestations = {'contract/sealed', 'issue_gates/phase1-contract',
                            'issue_gates/phase1-target-amendments', 'issue_gates/phase1-acceptance'}
    require(all(row.get('status') != 'attested' or row.get('id') in allowed_attestations for row in fresh['rows']),
            'unexpected unverified React attestation')
    audited = fresh.get('files')
    require(type(audited) is dict and audited, 'React audit file inventory missing')
    run_prefix = original.relative_to(repo).as_posix() + '/'
    with source_bound_derived_inputs(source_reader, repo) as bound:
        for name, digest in audited.items():
            require(type(name) is str and type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest),
                    'React audit file identity differs')
            if name.startswith(run_prefix):
                relative = name[len(run_prefix):]
                require(retained.get(relative) == digest and sha(read(root / 'collector' / relative)) == digest,
                        'React retained run bytes differ')
            elif name.startswith('@owned-candidate/'):
                member = name.removeprefix('@owned-candidate/')
                require(member in ('som', 'build.json', 'runtime.json') and sha(read(candidate / member)) == digest,
                        'React owned candidate file differs')
            elif name.startswith('.local/'):
                raw = bound.read(repo / name)
                bound_record = bound.files.get(name)
                require(type(bound_record) is dict and bound_record.get('sha256') == digest == sha(raw)
                        and bound_record.get('bytes') == len(raw), 'React input source role differs')
                if bound_record.get('source') == 'later-capture-derived':
                    # These are the 1,336 previously missing files. Their bytes
                    # come from the verified mapping and later capture, not
                    # from the old input manifest or an original wire record.
                    require(name not in approved and name not in original_approved
                            and stat.S_IMODE((repo / name).stat().st_mode) == 0o644,
                            'derived React input identity or mode differs')
                else:
                    identity = approved.get(name)
                    require(type(identity) is dict and identity['sha256'] == digest
                            and identity['bytes'] == len(raw)
                            and stat.S_IMODE((repo / name).stat().st_mode) == identity['mode'],
                            'React input is absent from an approved source role')
            else:
                require(sha(source_reader(name)) == digest, 'React audit source differs from bound source')
    require(all('@owned-candidate/' + n in audited for n in ('som', 'build.json', 'runtime.json'))
            and shared.get('owned_waits_complete') is True and shared.get('owned_cleanup_complete') is True,
            'React candidate or shared post-close evidence missing')
    require(read(root / 'audit.json') == audit_raw and snapshot == scan_capture_source(original),
            'React audit or collector source changed during verification')
    return {'version': 'som.m6-react-domain-proof.v1', 'complete': True, 'all_required_passed': True,
            'raw_logs_complete': True, 'product_acceptance_passed': False,
            'audit_sha256': sha(audit_raw), 'audited_file_count': len(audited),
            'candidate_pid': current_runtime['pid'], 'candidate_binary_sha256': sha(read(candidate / 'som')),
            'approved_input_index_sha256': INPUT_INDEX_SHA,
            'retained_run_sha256': sha(canonical(retained)), 'audit_totals': totals}


def shared_postclose_proof(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots):
    """Load and execute only the launcher bytes supplied by source authority."""
    name = '_som_m6_react_postclose_launcher'
    require(name not in sys.modules, 'preloaded post-close launcher refused')
    raw = source_reader(LAUNCHER)
    require(type(raw) is bytes and bool(raw), 'source-bound launcher bytes required')
    module = ModuleType(name)
    module.__file__ = str(physical(repo) / LAUNCHER)
    module.__cached__, module.__package__, module.__som_raw_sha256__ = None, '', sha(raw)
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
        require(sys.modules.get(name) is module and callable(getattr(module, 'verify_owner_postclose', None)),
                'post-close launcher source or verifier differs')
        proof = module.verify_owner_postclose(root, source_reader, helper_revision, candidate_revision,
                                              gate, repo=repo, proof_roots=proof_roots)
        require(sys.modules.get(name) is module, 'post-close launcher module changed')
        return proof
    finally:
        if sys.modules.get(name) is module:
            del sys.modules[name]


def verify_result(root, source_reader, helper_revision, candidate_revision, gate=GATE, *, repo, proof_roots):
    """Return a new result only after owner raw audit and shared post-close proof."""
    root = physical(root)
    capture_raw = read(root / 'result.json')
    capture = strict_json(capture_raw)
    require(capture.get('owned_waits_complete') is False and capture.get('owned_cleanup_complete') is False,
            'capture shared cleanup must remain pending')
    shared = shared_postclose_proof(root, source_reader, helper_revision, candidate_revision, gate,
                                    repo=repo, proof_roots=proof_roots)
    expected = {'version', 'gate', 'helper_revision', 'candidate_revision', 'capture_result_sha256',
                'context_sha256', 'artifact_inputs_sha256', 'services_manifest_sha256',
                'layout_binding_sha256', 'source_admission_sha256', 'owner_adapter', 'proof_files_sha256',
                'bound_source_sha256', 'owned_waits_complete', 'owned_cleanup_complete',
                'product_acceptance_passed'}
    require(type(shared) is dict and set(shared) == expected
            and shared.get('version') == 'som.owner-shared-postclose-proof.v1'
            and shared.get('gate') == gate and shared.get('helper_revision') == helper_revision
            and shared.get('candidate_revision') == candidate_revision
            and shared.get('capture_result_sha256') == sha(capture_raw)
            and shared.get('owned_waits_complete') is True and shared.get('owned_cleanup_complete') is True
            and shared.get('product_acceptance_passed') is False, 'shared post-close proof differs')
    domain = verify_react_raw_domain(root, source_reader, helper_revision, candidate_revision, gate,
                                     repo=repo, proof_roots=proof_roots, shared=shared)
    require(type(domain) is dict and domain.get('complete') is True
            and domain.get('all_required_passed') is True and domain.get('raw_logs_complete') is True,
            'React raw domain incomplete')
    require(read(root / 'result.json') == capture_raw, 'capture bytes changed during verification')
    verified = {'version': 'som.m6-react-verified-owner.v1', 'gate': gate,
                'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
                'capture_result_sha256': sha(capture_raw), 'shared_postclose_proof': shared,
                'react_domain_proof': domain, 'complete': True, 'all_required_passed': True,
                'owned_waits_complete': True, 'owned_cleanup_complete': True,
                'raw_logs_complete': True, 'product_acceptance_passed': False}
    verified_path = root / 'verified-result.json'
    if verified_path.exists() or verified_path.is_symlink():
        require(strict_json(read(verified_path)) == verified, 'retained verified result differs')
    return verified


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
