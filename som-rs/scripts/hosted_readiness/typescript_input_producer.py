#!/usr/bin/env python3
"""Produce new-current sealed TS data. No source or product grant is made."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_input_producer.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
CONSUMER = BASE + 'typescript_input_consumer.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
WIRE = '.github/m6-ts-role-transport/frozen-wire-profile-v1.json'
WIRE_SHA = '1f9cb5d59f3d6c742161fa72c5cc29d15abe0860f82e71f6342b1b2780ea024f'
ROLE_SHA = 'f45ce053c1174efb8a15ccc03a31da0ae730ebee099846ca0de4ef9417bfe32d'
INDEX_COMMIT = '02cd9e79cdcedfef92816f67a3ccdf2e089964ab'
INDEX_SHA = '6953812ebe0f388cefe77ec90e8659a8f70d6e8ccf9517f6417e7a3b9b774c28'
M6 = 'som-rs/scripts/react_output_acceptance/'
SEAL = M6 + 'seal_hosted_evidence.mjs'
PIN = 'som-rs/contracts/native-tools-v1/artifacts.json'
NAME = 'same-candidate-typescript-regression-inputs'
JOB = 'typescript-regression-inputs'
TOOLS = {
    'derive': ('entry.ts-transport.derive-node', '.github/m6-ts-role-transport/derive_node_tools.py'),
    'pyc': ('entry.ts-transport.rebuild-pyc', '.github/m6-ts-role-transport/rebuild_npm_pyc.py'),
    'materialize': ('entry.ts-transport.materialize', '.github/m6-ts-role-transport/materialize_roles.py'),
    'chunks': ('entry.ts-transport.chunks', '.github/m6-ts-role-transport/node_chunk_transport.py'),
    'node-role': ('entry.ts-transport.node-role', '.github/m6-ts-role-transport/node_role_transport.py'),
}


def need(value, message):
    if not value: raise ValueError(message)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def checked_sources(repo, helper, candidate):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    need(Path(repo).absolute() == controls.root and helper == candidate == controls.helper_revision == controls.candidate_revision
         == os.environ.get('GITHUB_SHA') == os.environ.get('GITHUB_WORKFLOW_SHA'), 'actual native same-source transport required')
    need(controls.role_sources['runtime.context'] == CONTEXT, 'fixed actual Source factory differs')
    context = controls.load_module('runtime.context')
    need(_SourceBytes is context.SourceBytes, 'fixed Source factory export identity differs')
    sources = _SourceBytes(controls.root, helper)
    sources.read(SELF); sources.verify()
    return controls, sources


def consumer_data(controls, sources):
    need(controls.role_sources['ts.transport.consumer-data'] == CONSUMER, 'fixed transport data verifier differs')
    sources.read(CONSUMER)
    return controls.load_module('ts.transport.consumer-data')


def profiles(sources, data):
    raw, wire_raw = sources.read(PROFILE), sources.read(WIRE)
    need(sha(wire_raw) == WIRE_SHA, 'immutable M6 wire profile differs')
    profile, wire = data.parse(raw), data.parse(wire_raw)
    fields = ('roles', 'siblings', 'native_typescript_tools', 'baseline_binary_sha256', 'baseline_source_revision')
    need(sha(canonical({k: profile[k] for k in fields})) == ROLE_SHA
         and all(profile[k] == wire[k] for k in fields), 'complete original semantic role maps differ')
    need(profile['version'] == wire['version'] == 'som.typescript-regression-input-profile.v1'
         and profile['baseline_profile'] == wire['baseline_profile']
         and profile['provenance']['npm_exact_tree_sha256'] == wire['provenance']['npm_exact_tree_sha256']
         and profile['product_acceptance'] == 'not_run' and type(profile['pending']) is list,
         'source profile identities or explicit pending edges differ')
    # Producer data preparation can retain a pending profile. Only the unchanged
    # actual consumer may admit pending=[] after Root resolves every source edge.
    return profile, sha(raw)


def run_m6(controls, sources, tool, argv):
    role, path = TOOLS[tool]
    need(controls.role_sources[role] == path, 'fixed immutable M6 tool role differs')
    sources.read(path)
    if tool == 'derive': sources.read(PIN)
    elif tool == 'pyc': sources.read('.github/m6-ts-role-transport/npm-pyc-rebuild-metadata.json')
    elif tool in {'materialize', 'node-role'}: sources.read(WIRE)
    code = controls.execute_entry(role, list(map(str, argv)))
    need(type(code) is int and code == 0, 'actual immutable M6 tool did not exit zero')
    sources.verify(); controls.verify_installed()


def crypto_environment():
    need(type(os.environ.get('M6_INPUTS_KEYRING')) is str and os.environ['M6_INPUTS_KEYRING'], 'step-only V1 keyring required')
    result = {k: os.environ[k] for k in ('HOME', 'TMPDIR') if k in os.environ}
    result.update(PATH='/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8', M6_INPUTS_KEYRING=os.environ['M6_INPUTS_KEYRING'])
    return result


def api(suffix):
    need(re.fullmatch(r'/actions/(?:runs/[1-9][0-9]*(?:/attempts/[1-9][0-9]*/jobs\?per_page=100&page=[1-9][0-9]*)?|artifacts/[1-9][0-9]*)', suffix),
         'fixed Actions metadata route required')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    token = os.environ.get('SOM_DEPENDENCY_ACTIONS_TOKEN')
    need(type(token) is str and token and '\r' not in token and '\n' not in token, 'existing Actions read token required')
    request = Request('https://api.github.com/repos/faberline/som' + suffix,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'User-Agent': 'som-ts-current-transport'})
    with build_opener(NoRedirect()).open(request, timeout=60) as response:
        raw = response.read(8 * 1024**2 + 1); need(response.status == 200 and len(raw) <= 8 * 1024**2, 'bounded Actions reply required')
    return json.loads(raw)


def download_zip(artifact_id, destination, digest, data):
    """Follow the signed storage URL without forwarding the Actions token."""
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    token = os.environ.get('SOM_DEPENDENCY_ACTIONS_TOKEN')
    need(type(token) is str and token, 'existing Actions read token required')
    request = Request('https://api.github.com/repos/faberline/som/actions/artifacts/' + str(artifact_id) + '/zip',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=60)
    except HTTPError as error:
        need(error.code == 302, 'artifact download redirect refused')
        location = error.headers['Location']
        error.close()
        from urllib.parse import urlsplit
        parsed = urlsplit(location)
        need(parsed.scheme == 'https' and parsed.username is None and parsed.password is None
             and parsed.hostname and (parsed.hostname.endswith('.blob.core.windows.net')
             or parsed.hostname.endswith('.actions.githubusercontent.com')), 'artifact signed storage origin refused')
        response = build_opener(NoRedirect()).open(Request(location), timeout=60)
    with response, destination.open('xb') as output:
        count = 0; h = hashlib.sha256()
        while chunk := response.read(1024 * 1024):
            count += len(chunk); need(count <= data.MAX_ZIP, 'whole artifact exceeds bound')
            output.write(chunk); h.update(chunk)
    need(h.hexdigest() == digest.removeprefix('sha256:'), 'whole actual artifact ZIP differs')
    return count


def seal_packet(sources, data, node, packet, sealed, evidence):
    before = data._node(sources, node)
    sources.read(SEAL); sources.read(M6 + 'sealed_inputs.mjs')
    primary = None
    post = {'complete': False, 'before': before, 'after': None,
            'source_postcheck_passed': False, 'product_acceptance_passed': False}
    try:
        data._run_owned([node, '--no-addons', sources.root / SEAL, '--root', packet, '--out', sealed],
            evidence.parent, evidence, crypto_environment(), before['executable_inode'])
    except BaseException as error:
        primary = error; raise
    finally:
        try:
            post['after'] = data._node(sources, node)
            need(post['after'] == before, 'actual crypto Node changed during seal')
            sources.verify()
            post.update(complete=True, source_postcheck_passed=True)
        except BaseException as error:
            if primary is None:
                primary = error
                raise
            primary.__som_ts_seal_postcheck_error__ = type(error).__name__
        finally:
            try:
                evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
                data.dump(evidence / 'postcheck.json', post)
            except BaseException as error:
                if primary is None: raise
                primary.__som_ts_seal_postcheck_receipt_error__ = type(error).__name__


def restore_original19(sources, data, node, work, output):
    """Keep unchanged decoder checks and all actual owned child observations."""
    work.mkdir(mode=0o700, exist_ok=True)
    data.physical(work, True)
    encrypted = work / 'encrypted19'; encrypted.mkdir(mode=0o700)
    evidence = work / 'evidence'; evidence.mkdir(mode=0o700)
    def blob(name):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(sources.root),
            'cat-file', 'blob', INDEX_COMMIT + ':' + name])
    raw = blob('inputs-index.json')
    need(sha(raw) == INDEX_SHA, 'complete approved19 index differs')
    index = data.parse(raw); need(len(index['packages']) == 19, 'all19 approved input packages required')
    data.write(encrypted / 'inputs-index.json', raw)
    config = evidence / 'config.json'
    data.dump(config, {'version': 'som.m6-hosted-inputs.v1', 'commit': INDEX_COMMIT, 'index_sha256': INDEX_SHA})
    for number, package in enumerate(index['packages']):
        need(package['archive'] == f'package-{number:03d}.sealed' and package['manifest'] == f'package-{number:03d}.manifest.json',
             'all ordered original19 packages required')
        manifest_raw = blob(package['manifest'])
        need(sha(manifest_raw) == package['manifest_sha256'], 'original19 manifest differs')
        data.write(encrypted / package['manifest'], manifest_raw)
        body = blob(package['archive'])
        need(sha(body) == data.parse(manifest_raw)['cipher_sha256'], 'original19 cipher differs')
        data.write(encrypted / package['archive'], body)
    sources.read(M6 + 'fetch_hosted_inputs.py'); sources.read(M6 + 'sealed_inputs.mjs')
    packages = _Fetch.load_manifests(sources.root, _Fetch.load_config(config))
    need(len(packages) == 19 and 'M6_INPUTS_KEYRING' in os.environ and 'M6_INPUTS_KEY' not in os.environ,
         'actual complete19 restore needs only this step V2 ring')
    keys = _Fetch.load_keyring({'M6_INPUTS_KEYRING': os.environ['M6_INPUTS_KEYRING']}, packages)
    before = data._node(sources, node)
    staged = work / 'private19'; staged.mkdir(mode=0o700); files = []
    for number, package in enumerate(packages):
        entry, manifest = package['entry'], package['manifest']
        need(data.raw_file(encrypted / entry['manifest']) == package['raw_manifest']
             and sha(data.raw_file(encrypted / entry['archive'])) == manifest['cipher_sha256'], 'actual retained19 decoder input differs')
        destination = staged / f'restored-{number:03d}'
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                       'M6_INPUTS_KEY': keys[manifest['cipher_sha256']]}
        primary = None
        try:
            data._run_owned([node, '--no-addons', sources.root / (M6 + 'sealed_inputs.mjs'), 'restore',
                '--archive', encrypted / entry['archive'], '--manifest', encrypted / entry['manifest'], '--out', destination],
                staged, evidence / f'package-{number:03d}', environment, before['executable_inode'], timeout=600)
        except BaseException as error:
            primary = error; raise
        finally:
            try:
                need(data._node(sources, node) == before, 'crypto Node changed during actual19 restore')
                sources.verify()
            except BaseException as error:
                if primary is None: raise
                primary.__som_original19_postcheck_error__ = type(error).__name__
        files.extend(_Fetch.check_restored(destination, manifest))
    receipt = {'version': _Fetch.PROVISION_VERSION, 'stage': 'input_provision', 'status': 'passed',
        'file_count': len(files), 'bytes': sum(row['bytes'] for _, row in files), 'index_sha256': INDEX_SHA, 'product_suites': 'not_run'}
    _Fetch.publish(output, files, receipt)
    data.dump(evidence / 'actual19-provision.json', receipt)


def new_current_packet(sources, data, temporary):
    """Keep the original new wire output. Create distinct final-profile records."""
    profile, profile_sha = profiles(sources, data)
    wire = temporary / 'packet'; current = temporary / 'ts-current-packet'
    before = data._tree(wire)
    need(not current.exists(), 'new current packet must be fresh')
    shutil.copytree(wire, current)
    need(data._tree(current) == before, 'complete wire copy changed bytes/modes/universe')
    changed = {}
    for name in ('materialization-receipt.json', 'node-tools-transport.json'):
        original = data.parse(data.raw_file(wire / 'run' / name))
        need(original['profile_sha256'] == WIRE_SHA and original['product_acceptance'] == 'not_run', 'original wire data record differs')
        updated = {**original, 'profile_sha256': profile_sha}
        target = current / 'run' / name
        target.unlink(); data.dump(target, updated)
        target.chmod(stat.S_IMODE((wire / 'run' / name).stat().st_mode))
        changed['run/' + name] = {'original_sha256': before['files']['run/' + name]['sha256'],
                                  'new_sha256': sha(data.raw_file(target)), 'only_changed_field': 'profile_sha256'}
    after = data._tree(current)
    need(data._tree(wire) == before and after['directories'] == before['directories']
         and set(after['files']) == set(before['files'])
         and all(after['files'][k] == v for k, v in before['files'].items() if k not in changed),
         'wire authority or non-profile data changed')
    data.dump(temporary / 'ts-producer-evidence/profile-wire-bridge.json', {
        'version': 'som.ts-new-current-profile-wire-bridge.v1', 'wire_profile_sha256': WIRE_SHA,
        'current_profile_sha256': profile_sha, 'semantic_map_sha256': ROLE_SHA, 'records': changed,
        'original_wire_unchanged': True, 'pending': profile['pending'], 'source_admission_complete': False,
        'product_acceptance_passed': False})


def bind_upload(sources, data, temporary):
    need(os.environ.get('GITHUB_JOB') == JOB, 'new literal producer job required; old roles job is not current')
    profile, profile_sha = profiles(sources, data)
    artifact_id = int(os.environ['TS_UPLOAD_ARTIFACT_ID']); upload_digest = 'sha256:' + os.environ['TS_UPLOAD_DIGEST'].removeprefix('sha256:')
    need(artifact_id > 0 and re.fullmatch('sha256:[0-9a-f]{64}', upload_digest), 'actual upload step metadata required')
    run = api('/actions/runs/' + os.environ['GITHUB_RUN_ID']); meta = api('/actions/artifacts/' + str(artifact_id))
    need(run['id'] == int(os.environ['GITHUB_RUN_ID']) and run['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
         and run['head_sha'] == sources.revision and run['path'].split('@', 1)[0] == '.github/workflows/rust-hosted-readiness.yml'
         and run['event'] == os.environ['GITHUB_EVENT_NAME'] and run['status'] == 'in_progress'
         and run['repository']['full_name'] == 'faberline/som' and run['repository']['id'] == 1390374878,
         'actual new producer source/run/attempt differs')
    jobs = []; total = None
    for page in range(1, 101):
        result = api(f'/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100&page={page}')
        count = result['total_count']
        need(type(count) is int and 0 < count <= 10000 and (total is None or count == total), 'complete producer job count differs')
        total = count
        jobs.extend(result['jobs'])
        if len(result['jobs']) < 100: break
    else: raise ValueError('complete producer job page bound exceeded')
    need(len(jobs) == total and len({job['id'] for job in jobs}) == total, 'whole actual attempt job identity universe differs')
    found = [j for j in jobs if j['name'] == JOB and j.get('runner_name') == os.environ['RUNNER_NAME']]
    need(len(found) == 1 and found[0]['status'] == 'in_progress' and found[0]['run_id'] == run['id']
         and found[0]['head_sha'] == sources.revision, 'actual new producer job is missing or ambiguous')
    need(meta['id'] == artifact_id and meta['name'] == NAME and meta['expired'] is False
         and meta['digest'] == upload_digest and meta['workflow_run']['id'] == run['id']
         and meta['workflow_run']['head_sha'] == sources.revision and meta['workflow_run']['repository_id'] == 1390374878,
         'actual current upload origin differs')
    need(type(meta['size_in_bytes']) is int and 0 < meta['size_in_bytes'] <= data.MAX_ZIP
         and datetime.fromisoformat(meta['expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc),
         'actual uploaded artifact is expired or outside the complete ZIP bound')
    evidence = temporary / 'ts-producer-evidence'
    archive = evidence / 'uploaded.zip'; count = download_zip(artifact_id, archive, upload_digest, data)
    need(count == meta['size_in_bytes'], 'actual full uploaded ZIP size differs')
    index_sha = sha(data.raw_file(temporary / 'ts-role-sealed/evidence-index.json'))
    preliminary = {'artifact_digest': upload_digest, 'index_sha256': index_sha}
    packet = data._extract_packet(archive, evidence / 'uploaded-sealed', preliminary)
    state = data._source_state(sources, sources.revision, sources.revision)
    node = temporary / 'ts-node-tools/bin/node'
    data._restore(sources, node, evidence / 'uploaded-sealed', evidence / 'authenticated', evidence / 'restore', state)
    plain = data._plain(evidence / 'authenticated', packet)
    restored = evidence / 'restored/run'
    expected, extra = data._materialize(profile, evidence / 'authenticated', restored, profile_sha)
    need(set(packet['authenticated_files']) == set(expected) | extra
         and all(packet['authenticated_files'][k] == v for k, v in expected.items()), 'complete new wire universe differs')
    roots, siblings, snapshots = data._roles(profile, restored)
    data._command_proof(sources, node, evidence, evidence / 'uploaded-sealed', evidence / 'authenticated', packet, state)
    need(data._plain(evidence / 'authenticated', packet) == plain, 'original authenticated producer bytes changed')
    receipt = {'version': 'som.typescript-regression-restored-inputs.v1', 'helper_revision': sources.revision,
        'candidate_revision': sources.revision, 'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'profile_sha256': profile_sha, 'roots': {k: str(v) for k, v in roots.items()},
        'files': {k: profile['roles'][k]['files'] for k in data.ROLES}, 'siblings': {k: str(v) for k, v in siblings.items()},
        'sibling_files': {'rs_prompts': profile['siblings']['rs_prompts']['files']}}
    data.dump(evidence / 'receipt.json', receipt)
    data.dump(evidence / 'origin.json', {'run': run, 'artifact': meta, 'job': found[0], 'role_snapshots': snapshots,
        'pending': profile['pending'], 'product_acceptance_passed': False})
    catalog = {'version': 'som.ts-input-producer-catalog-draft.v1', 'job': JOB,
        'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'head_sha': sources.revision,
        'artifact_id': artifact_id, 'artifact_digest': upload_digest, 'index_sha256': index_sha,
        'receipt_sha256': sha(data.raw_file(evidence / 'receipt.json')), 'profile_sha256': profile_sha}
    output = data.physical(os.environ['GITHUB_OUTPUT'])
    need(Path(os.environ['RUNNER_TEMP']) in output.parents and output.name.startswith('set_output_'), 'actual producer output channel required')
    sources.verify()
    with output.open('ab') as stream: stream.write(b'producer_catalog=' + canonical(catalog) + b'\n')


def retention_inventory(roots, data):
    result = {}
    for label, root in roots.items():
        need(not root.is_symlink(), 'whole retention root cannot be an alias')
        if not root.exists():
            result[label] = {'type': 'absent'}; continue
        data.physical(root, True)
        def onerror(error): raise error
        for directory, children, leaves in os.walk(root, followlinks=False, onerror=onerror):
            folder = Path(directory); relative = folder.relative_to(root).as_posix()
            result[label + '/' + relative] = {'type': 'directory', 'mode': stat.S_IMODE(folder.stat().st_mode)}
            for name in sorted(children + leaves):
                path = folder / name; info = path.lstat(); key = label + '/' + path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    result[key] = {'type': 'opaque-alias-data', 'mode': stat.S_IMODE(info.st_mode), 'target': os.readlink(path)}
                elif stat.S_ISREG(info.st_mode):
                    digest, size = data.hash_file(path)
                    result[key] = {'type': 'regular', 'mode': stat.S_IMODE(info.st_mode), 'sha256': digest, 'size': size}
                else: need(stat.S_ISDIR(info.st_mode), 'unknown special raw member prevents complete retention')
    return result


def exporter_control(sources, data, temporary, work, upload, expected, completed):
    """Publish only a fixed safe control schema. Every raw exporter file stays private."""
    command = None
    command_path = work / 'seal-command/command.json'
    if command_path.is_file():
        try:
            value = data.parse(data.raw_file(command_path))
            if type(value) is dict: command = value
        except Exception:
            pass  # Unknown is explicit below; no error text is published.
    observed = command is not None
    argv_matches = observed and command.get('argv') == list(map(str, expected))
    start = command.get('owned_start') if observed else None
    keys = ('pid', 'parent_pid', 'pgrp', 'session', 'start_ticks')
    safe_start = None
    if type(start) is dict and all(type(start.get(key)) is int and start[key] > 0 for key in keys):
        inode = start.get('executable_inode')
        if type(inode) is dict and set(inode) == {'device', 'inode', 'size'} and all(type(v) is int and v >= 0 for v in inode.values()):
            safe_start = {**{key: start[key] for key in keys}, 'executable_inode': inode}
    waits = []
    if observed and type(command.get('wait_attempts')) is list:
        for item in command['wait_attempts']:
            if (type(item) is dict and item.get('stage') in {'initial', 'term-wait', 'kill-wait'}
                    and type(item.get('completed')) is bool
                    and (item.get('exit_code') is None or type(item.get('exit_code')) is int)):
                waits.append({key: item.get(key) for key in ('stage', 'completed', 'exit_code')})
    postcheck_passed, node_inode = False, None
    post_path = work / 'seal-command/postcheck.json'
    if post_path.is_file():
        try:
            post = data.parse(data.raw_file(post_path))
            postcheck_passed = (post.get('complete') is True and post.get('source_postcheck_passed') is True
                                and type(post.get('before')) is dict and post['before'] == post.get('after'))
            if postcheck_passed: node_inode = post['before'].get('executable_inode')
        except Exception:
            pass
    raw = {}
    for name in ('stdout.raw', 'stderr.raw', 'owned-start-stat.raw', 'command.json', 'postcheck.json'):
        path = work / 'seal-command' / name
        item = {'publication': 'UNARCHIVED', 'observed': False, 'sha256': None, 'bytes': None}
        if path.is_file():
            try:
                pin, size = data.hash_file(path)
                item.update(observed=True, sha256=pin, bytes=size)
            except Exception:
                pass
        raw[name] = item
    code = command.get('exit_code') if observed else None
    code = code if type(code) is int else None
    pid = command.get('pid') if observed else None
    pid = pid if type(pid) is int and pid > 1 else None
    prior = os.environ.get('TS_PRODUCER_PRIOR_STATUS')
    need(prior in {'success', 'failure', 'cancelled', 'skipped'}, 'actual finite producer prior status required')
    control = {'version': 'som.ts-producer-retention-exporter-control.v1',
        'helper_revision': sources.revision, 'candidate_revision': sources.revision,
        'producer_job_prior_status': prior, 'private_archive_seal_completed': completed,
        'actual_command_record_observed': observed, 'actual_argv_matches_fixed': bool(argv_matches),
        'actual_argv': list(map(str, expected)) if argv_matches else None,
        'pid': pid, 'owned_start': safe_start, 'wait_attempts': waits, 'postcheck_passed': postcheck_passed,
        'exit_code': code, 'wait_recorded': command.get('wait_recorded') is True if observed else False,
        'owned_group_gone': command.get('owned_group_gone') is True if observed else False,
        'cleanup_error_count': len(command['cleanup_errors']) if observed and type(command.get('cleanup_errors')) is list else None,
        'raw_exporter_observations': raw,
        'boundary': 'Only original producer observations are encrypted. Final exporter raw is UNARCHIVED; hashes and sizes are observations, not published raw.',
        'authenticated_archive_view_admitted': False, 'product_acceptance_passed': False}
    ready = bool(argv_matches and safe_start is not None and code == 0 and control['wait_recorded']
        and control['owned_group_gone'] and control['cleanup_error_count'] == 0
        and pid == safe_start['pid'] == safe_start['pgrp'] == safe_start['session']
        and safe_start['parent_pid'] == os.getpid() and safe_start['executable_inode'] == node_inode
        and postcheck_passed and any(item['completed'] and item['exit_code'] == 0 for item in waits)
        and command.get('cwd') == str(work) and all(item['observed'] for item in raw.values())
        and all(raw[name + '.raw']['sha256'] == command.get(name + '_sha256')
                and raw[name + '.raw']['bytes'] == command.get(name + '_bytes') for name in ('stdout', 'stderr')))
    control.update(exporter_evidence_verified=ready, retention_complete=bool(completed and ready))
    data.dump(upload / 'exporter-control.json', control)
    if completed: need(ready, 'complete actual exporter control observations required')


def retain_producer_raw(sources, data, temporary):
    """Seal every original producer observation after origin or its first failure."""
    work = temporary / 'som-ts-producer-retention'; work.mkdir(mode=0o700)
    upload = temporary / 'som-ts-producer-retained-upload'; upload.mkdir(mode=0o700)
    node = temporary / 'ts-node-tools/bin/node'
    packet = work / 'packet'
    expected = [node, '--no-addons', sources.root / SEAL, '--root', packet, '--out', upload / 'sealed']
    completed, primary = False, None
    try:
        data._node(sources, node)  # No unqualified or fabricated pre-Node fallback.
        roots = {'producer': temporary / 'ts-producer-evidence', 'original19': temporary / 'ts-original19',
                 'wire-packet': temporary / 'packet', 'current-packet': temporary / 'ts-current-packet',
                 'semantic-sealed': temporary / 'ts-role-sealed', 'approved-inputs': temporary / 'approved-inputs'}
        before = retention_inventory(roots, data)
        (packet / 'run/chunks').mkdir(parents=True, mode=0o700)
        archive = work / 'archive.tar'; slots = {}; ordinal = 0
        with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as tar:
            for name, row in sorted(before.items()):
                if row['type'] != 'regular': continue
                label, relative = name.split('/', 1); path = roots[label] / relative
                slot = f'files/{ordinal:06d}.raw'; ordinal += 1; slots[name] = slot
                info = tarfile.TarInfo(slot); info.size = row['size']; info.mode = 0o600; info.mtime = 0
                with path.open('rb') as stream: tar.addfile(info, stream)
                need(data.hash_file(path) == (row['sha256'], row['size']), 'original producer bytes changed while archiving')
            manifest = data.canonical({'version': 'som.ts-private-raw-flat-archive.v1', 'nodes': before, 'file_slots': slots,
                'aliases': 'opaque metadata only; never instantiate or dereference in archive consumers',
                'authenticated_archive_view_admitted': False, 'product_acceptance_passed': False})
            info = tarfile.TarInfo('manifest.json'); info.size = len(manifest); info.mode = 0o600; info.mtime = 0
            tar.addfile(info, io.BytesIO(manifest))
        need(retention_inventory(roots, data) == before, 'complete original producer universe changed during retention')
        chunks = []; whole = hashlib.sha256(); total = 0
        with archive.open('rb') as stream:
            while body := stream.read(32 * 1024**2):
                name = f'{len(chunks):06d}.part'; data.write(packet / 'run/chunks' / name, body)
                whole.update(body); total += len(body); chunks.append({'name': name, 'sha256': data.sha(body), 'bytes': len(body)})
        data.dump(packet / 'run/transport.json', {'version': 'som.ts-private-raw-ordered-chunks.v1',
            'archive_sha256': whole.hexdigest(), 'archive_bytes': total, 'ordered_chunks': chunks,
            'source_inventory_sha256': data.sha(data.canonical(before)), 'alias_instantiation': False,
            'authenticated_archive_view_admitted': False, 'product_acceptance_passed': False})
        seal_packet(sources, data, node, packet, upload / 'sealed', work / 'seal-command')
        sources.verify()
        completed = True
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            exporter_control(sources, data, temporary, work, upload, expected, completed)
        except BaseException as secondary:
            if primary is None: raise
            primary.__som_ts_retention_control_error__ = type(secondary).__name__


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True); parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('operation', choices=('derive-node', 'restore-original19', 'rebuild-pyc', 'materialize', 'seal', 'origin', 'retain'))
    args = parser.parse_args(argv)
    need(os.environ.get('GITHUB_JOB') == JOB, 'only the new fixed producer job may run this entry')
    controls, sources = checked_sources(args.repo, args.helper_revision, args.candidate_revision)
    data = consumer_data(controls, sources); profiles(sources, data)
    temporary = data.physical(os.environ['RUNNER_TEMP'], True)
    evidence = temporary / 'ts-producer-evidence'; evidence.mkdir(mode=0o700, exist_ok=True)
    node = temporary / 'ts-node-tools/bin/node'
    if args.operation == 'derive-node':
        run_m6(controls, sources, 'derive', ['--pin', sources.root / PIN, '--out', temporary / 'ts-node-tools', '--metadata', evidence / 'node.json'])
        data._node(sources, node)
    elif args.operation == 'restore-original19':
        restore_original19(sources, data, node, temporary / 'ts-original19', temporary / 'approved-inputs')
    elif args.operation == 'rebuild-pyc':
        run_m6(controls, sources, 'pyc', ['--root', temporary / 'npm-prefix/lib/node_modules/npm',
            '--metadata', sources.root / '.github/m6-ts-role-transport/npm-pyc-rebuild-metadata.json'])
    elif args.operation == 'materialize':
        packet = temporary / 'packet'; packet.mkdir(mode=0o700)
        run_m6(controls, sources, 'materialize', ['--profile', sources.root / WIRE, '--approved', temporary / 'approved-inputs',
            '--node-root', temporary / 'ts-node-tools', '--out', packet / 'run'])
        run_m6(controls, sources, 'chunks', ['split', '--run', packet / 'run'])
        run_m6(controls, sources, 'node-role', ['pack', '--profile', sources.root / WIRE, '--run', packet / 'run'])
        new_current_packet(sources, data, temporary)
    elif args.operation == 'seal':
        seal_packet(sources, data, node, temporary / 'ts-current-packet', temporary / 'ts-role-sealed', evidence / 'seal')
    elif args.operation == 'origin':
        bind_upload(sources, data, temporary)
    else:
        retain_producer_raw(sources, data, temporary)
    sources.verify(); controls.verify_installed()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
