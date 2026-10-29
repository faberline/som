#!/usr/bin/env python3
"""Fresh-before only: capture the historic Rust baseline on a hosted macOS runner.

The authenticated sources and sealed output transport are the caller's job.
This program never identifies its output as a historical wire capture or a
product acceptance result. All preparation must succeed before it starts SOM.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

INVENTORY_SHA = '83489c5cc729b7f79e513cfbf2b6c7c93cca717daff093a19f101d566ca6c44f'
REPORT_SHA = '37c74335072cc93a64eb7663cc90c1fdd522e4d4be6a4f47e23f82b18e434b84'
RECORDING_SHA = '34114b9069f47db454ab3030df63fbee2f142a6c1c6acb0fc62ab1883400fbfa'
EVIDENCE_INDEX_SHA = '3759239392208eab81bf740ca9ca2a41465be3e3e4995de2bd255774f4596bea'
BASELINE_SHA = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
RUSTFMT_PATH = Path('/opt/homebrew/Cellar/rust/1.96.1/bin/rustfmt')
RUSTFMT_SHA = '2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f'
PORT = 18917


def need(test, reason):
    if not test:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    need(path.is_file() and not path.is_symlink(), 'source missing or symlink')
    return path.read_bytes()


def load(path):
    return json.loads(read(path))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def jbytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def pretty(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def evidence_index(sealed):
    index_raw = read(sealed / 'evidence-index.json')
    need(sha(index_raw) == EVIDENCE_INDEX_SHA, 'evidence index identity')
    index, files = json.loads(index_raw), {}
    for package in index['packages']:
        raw = read(sealed / package['manifest'])
        need(sha(raw) == package['manifest_sha256'], 'evidence manifest identity')
        manifest = json.loads(raw)
        need(manifest['cipher_sha256'] == package['cipher_sha256'], 'evidence package identity')
        for entry in manifest['files']:
            need(entry['path'] not in files, 'duplicate evidence member')
            files[entry['path']] = entry['sha256']
    return files


def prepare(inventory, evidence, run, checker, sealed):
    need(sha(read(inventory)) == INVENTORY_SHA, 'inventory identity')
    index = evidence_index(sealed)
    def bound(member):
        raw = read(evidence / member)
        need(index.get(member) == sha(raw), 'unbound evidence bytes')
        return raw
    report_path = evidence / 'run/rust-baseline/report.json'
    recorded = evidence / 'run/rust-baseline-raw'
    need(sha(bound('run/rust-baseline/report.json')) == REPORT_SHA, 'report identity')
    need(sha(bound('run/rust-baseline-raw/manifest.json')) == RECORDING_SHA, 'recording identity')
    report = load(report_path)
    recording = load(recorded / 'manifest.json')
    need(report['passed'] is True and report['not_run_count'] == 0 and report['failed_count'] == 0,
         'accepted report failed')
    need(recording['exit_code'] == 0 and recording['recording_errors'] == [], 'recorder failed')
    slots = load(inventory)['slots']
    compile_slots = {(s['phase'], s['case']): s for s in slots if s['cohort'] == 'rust006'}
    assemble_slots = {(s['phase'], s['case']): s for s in slots if s['cohort'] == 'rust006_conditional_assemble'}
    need(len(compile_slots) == 699 and len(assemble_slots) == 697, 'slot count')
    positive = {k for k, s in compile_slots.items() if s['report_status'] == 200}
    refused = {k for k, s in compile_slots.items() if s['report_status'] != 200}
    need(positive == set(assemble_slots) and len(refused) == 2, 'refusal boundary')
    need({k[1] for k in refused} == {'RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'}, 'refusal identities')
    rows = {}
    for row in report['rows']:
        key = (row['phase'], row['id'], row['check'])
        need(key not in rows, 'duplicate report row')
        rows[key] = row
    by_response = {}
    call_ids = {}
    for call in recording['calls']:
        need(call['id'] not in call_ids, 'duplicate recorder call')
        call_ids[call['id']] = call
        if call.get('method') == 'POST' and call.get('url') == 'http://127.0.0.1:18919/v1/assemble':
            key = (call.get('request_sha256'), call.get('response_sha256'))
            by_response.setdefault(key, []).append(call)
    product_path = run / 'rust-products.json'
    need(sha(read(product_path)) == report['rust_products_sha256'], 'run-003 phase1 identity')
    phase1 = {r['id']: r for r in load(product_path)['cases']}
    summary = load(run / 'phase2/summary.json')
    plan, expected = [], {}
    for key in sorted(positive, key=lambda k: assemble_slots[k]['slot_id']):
        phase, case = key
        slot, source = assemble_slots[key], compile_slots[key]
        candidates = source['source_candidates']
        need(candidates, 'compile source unavailable')
        raw = None
        for candidate in candidates:
            need(candidate['manifest_bound'] is True and candidate['status'] == 200, 'compile source unbound')
            candidate_raw = bound(candidate['response_member'])
            request_raw = bound(candidate['request_member'])
            call = call_ids[candidate['call_id']]
            call_name = f"run/rust-baseline-raw/calls/{candidate['call_id']:06d}"
            need(json.loads(bound(call_name + '.json')) == call and call['method'] == 'POST'
                 and call['url'] == 'http://127.0.0.1:18917/v1/compile' and call['status'] == 200
                 and call['complete'] is True and call['error'] is None
                 and candidate['request_member'] == call_name + '.request.raw'
                 and candidate['response_member'] == call_name + '.response.raw'
                 and call['request_sha256'] == sha(request_raw) and call['response_sha256'] == sha(candidate_raw),
                 'compile recorder join')
            need(sha(candidate_raw) == source['report_response_sha256'] == candidate['response_package']['sha256']
                 and sha(request_raw) == source['historical_request_sha256'] == candidate['request_package']['sha256'],
                 'compile source bytes differ')
            need(raw is None or raw == candidate_raw, 'compile sources disagree')
            raw = candidate_raw
        compile_row = rows[(phase, case, 'compile_equal')]
        need(compile_row['passed'] is True and compile_row['baseline_status'] == 200
             and compile_row['baseline_response_sha256'] == sha(raw)
             and compile_row['baseline_request_sha256'] == source['historical_request_sha256'], 'compile role')
        doc = json.loads(raw)
        need(doc.get('status') == 'ok' and type(doc.get('bundle')) is dict, 'baseline bundle')
        request = jbytes({'bundle': doc['bundle']})
        refused_row = rows[(phase, case, 'baseline_bundle_refused')]
        need(refused_row['passed'] is True and refused_row['qa_status'] == 400
             and refused_row['request_sha256'] == refused_row['qa_request_sha256'] == sha(request),
             'original serializer request identity')
        prior_calls = by_response.get((refused_row['request_sha256'], refused_row['qa_response_sha256']), [])
        prior_sources = []
        for call in prior_calls:
            call_name = f"run/rust-baseline-raw/calls/{call['id']:06d}"
            need(json.loads(bound(call_name + '.json')) == call and call['complete'] is True
                 and call['error'] is None and call['status'] == 400
                 and call['request_file'] == f"calls/{call['id']:06d}.request.raw"
                 and call['response_file'] == f"calls/{call['id']:06d}.response.raw", 'prior refusal call')
            prior_request = bound(call_name + '.request.raw')
            prior_response = bound(call_name + '.response.raw')
            need(prior_request == request and sha(prior_request) == call['request_sha256']
                 and sha(prior_response) == call['response_sha256'], 'prior refusal raw request differs')
            prior_sources.append(call_name + '.request.raw')
        if phase == 'phase1':
            files_sha = phase1[case]['files_sha256']
        else:
            import re
            name = re.sub(r'[^A-Za-z0-9]+', '_', case).strip('_') + '.json'
            record_path = run / 'phase2/cases' / name
            record_raw = read(record_path)
            need(sha(record_raw) == summary['case_sha256'][case]
                 == report['records_sha256']['phase2_case_files'][case], 'run-003 phase2 identity')
            files_sha = json.loads(record_raw)['rust']['files_sha256']
        row = rows[(phase, case, 'rebased_files')]
        need(row['passed'] is True and row['qa_status'] == 200
             and row['files_sha256'] == row['recorded_files_sha256'] == files_sha, 'accepted expected hash')
        calls = by_response.get((row['qa_request_sha256'], row['qa_response_sha256']), [])
        need(calls, 'accepted complete filemap unavailable')
        maps = []
        for call in calls:
            need(call['complete'] is True and call['error'] is None and call['status'] == 200, 'accepted call incomplete')
            call_name = f"run/rust-baseline-raw/calls/{call['id']:06d}"
            need(json.loads(bound(call_name + '.json')) == call
                 and call['request_file'] == f"calls/{call['id']:06d}.request.raw"
                 and call['response_file'] == f"calls/{call['id']:06d}.response.raw", 'expected recorder join')
            raw_response = bound('run/rust-baseline-raw/' + call['response_file'])
            raw_request = bound('run/rust-baseline-raw/' + call['request_file'])
            need(sha(raw_response) == call['response_sha256'] and sha(raw_request) == call['request_sha256'], 'accepted call bytes')
            files = json.loads(raw_response).get('files')
            need(type(files) is dict and checker.canonical_sha256(files) == files_sha, 'accepted full filemap identity')
            maps.append(files)
        need(all(x == maps[0] for x in maps), 'ambiguous accepted filemaps')
        identity = str(slot['slot_id'])
        expected[identity] = maps[0]
        plan.append({'slot_id': slot['slot_id'], 'case': case, 'phase': phase,
                     'method': 'POST', 'endpoint': '/v1/assemble', 'request_sha256': sha(request),
                     'prior_candidate_refusal_request_sha256': refused_row['request_sha256'],
                     'prior_candidate_raw_request_state': 'verified' if prior_sources else 'unavailable',
                     'prior_candidate_raw_request_sources': prior_sources,
                     'expected_files_sha256': files_sha, 'expected_source': 'accepted-130-rebased-and-run003',
                     'accepted_response_sha256': row['qa_response_sha256'],
                     'baseline_compile_response_sha256': sha(raw), 'request': request})
    refusal_rows = []
    for key in sorted(refused):
        source = compile_slots[key]
        row = rows[(key[0], key[1], 'compile_equal')]
        need(row['passed'] is True and row['baseline_status'] == source['report_status']
             and row['baseline_response_sha256'] == source['report_response_sha256'], 'refusal role')
        for candidate in source['source_candidates']:
            call = call_ids[candidate['call_id']]
            call_name = f"run/rust-baseline-raw/calls/{candidate['call_id']:06d}"
            need(candidate['manifest_bound'] is True and candidate['status'] == source['report_status']
                 and sha(bound(candidate['response_member'])) == source['report_response_sha256']
                 and sha(bound(candidate['request_member'])) == source['historical_request_sha256']
                 and json.loads(bound(call_name + '.json')) == call and call['method'] == 'POST'
                 and call['url'] == 'http://127.0.0.1:18917/v1/compile' and call['status'] == source['report_status']
                 and call['complete'] is True and call['error'] is None
                 and candidate['request_member'] == call_name + '.request.raw'
                 and candidate['response_member'] == call_name + '.response.raw'
                 and call['request_sha256'] == source['historical_request_sha256']
                 and call['response_sha256'] == source['report_response_sha256'],
                 'refusal source bytes')
        need(source['source_candidates'], 'refusal source unavailable')
        refusal_rows.append({'phase': key[0], 'case': key[1], 'status': source['report_status'],
                             'response_sha256': source['report_response_sha256'],
                             'role': 'historical-compile-refusal-no-assemble'})
    return plan, expected, refusal_rows


def free_port():
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', PORT))
            return True
        except OSError:
            return False


def asset_identity(root):
    need(root.is_dir() and not root.is_symlink(), 'assets directory missing')
    found = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        directory = Path(directory)
        need(all(not (directory / name).is_symlink() for name in dirs + files), 'asset symlink')
        for name in sorted(files):
            path = directory / name
            found[path.relative_to(root).as_posix()] = sha(read(path))
    need(found, 'assets empty')
    return found


def group_gone(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def count_unrun(plan, rows):
    return len(plan) - len(rows) + sum(row['comparison'] == 'unrun' for row in rows)


def process_evidence(proc, argv, binary, out, stage, initial=None, prelaunch=None):
    pid = str(proc.pid)
    commands = {
        'identity': ['/bin/ps', '-ww', '-p', pid, '-o', 'pid=,ppid=,pgid=,stat=,command='],
        'start_time': ['/bin/ps', '-p', pid, '-o', 'lstart='],
        'listeners': ['/usr/sbin/lsof', '-nP', '-iTCP:' + str(PORT), '-sTCP:LISTEN', '-Fp'],
        'executable': ['/usr/sbin/lsof', '-nP', '-a', '-p', pid, '-d', 'txt', '-FfDin'],
    }
    results, receipt = {}, {}
    for name, command in commands.items():
        result = subprocess.run(command, capture_output=True, check=False, timeout=10)
        write(out / 'process-evidence' / (stage + '-' + name + '.stdout'), result.stdout)
        write(out / 'process-evidence' / (stage + '-' + name + '.stderr'), result.stderr)
        receipt[name] = {'argv': command, 'exit_code': result.returncode,
                         'stdout_sha256': sha(result.stdout), 'stderr_sha256': sha(result.stderr)}
        need(result.returncode == 0 and not result.stderr, 'process evidence command failed')
        results[name] = result.stdout.decode('utf-8').strip()
    fields = results['identity'].split(None, 4)
    need(len(fields) == 5 and fields[:3] == [pid, str(os.getpid()), pid] and
         not fields[3].startswith('Z') and fields[4] == ' '.join(argv), 'process identity differs')
    listeners = [line[1:] for line in results['listeners'].splitlines() if line.startswith('p')]
    need(listeners == [pid], 'listener is not exclusively owned by baseline')
    info = binary.stat()
    executable_identity = (info.st_dev, info.st_ino)
    if prelaunch is not None:
        need(executable_identity == (prelaunch['binary_device'], prelaunch['binary_inode'])
             and info.st_size == prelaunch['binary_size'], 'baseline file identity changed')
    records, record = [], None
    for line in results['executable'].splitlines():
        if line.startswith('f'):
            record = {'fd': line[1:]}
            records.append(record)
        elif record is not None and line[:1] in ('D', 'i', 'n'):
            need(line[0] not in record, 'duplicate executable evidence field')
            record[line[0]] = line[1:]
    observed = False
    for record in records:
        if record['fd'] == 'txt' and 'D' in record and 'i' in record:
            try:
                if (int(record['D'], 0), int(record['i'], 10)) == executable_identity:
                    observed = True
            except ValueError:
                pass
    need(observed, 'owned process mapped executable inode is unverified')
    start_time = results['start_time']
    need(bool(start_time), 'process start time missing')
    identity = {'pid': proc.pid, 'parent_pid': os.getpid(), 'pgid': proc.pid, 'start_time': start_time,
                'binary_device': info.st_dev, 'binary_inode': info.st_ino, 'binary_size': info.st_size,
                'commands': receipt}
    if initial is not None:
        for name in ('pid', 'parent_pid', 'pgid', 'start_time', 'binary_device', 'binary_inode', 'binary_size'):
            need(identity[name] == initial[name], 'process identity changed during capture')
    return identity


def cleanup_listener_evidence(out):
    command = ['/usr/sbin/lsof', '-nP', '-iTCP:' + str(PORT), '-sTCP:LISTEN', '-Fp']
    result = subprocess.run(command, capture_output=True, check=False, timeout=10)
    write(out / 'process-evidence/cleanup-listeners.stdout', result.stdout)
    write(out / 'process-evidence/cleanup-listeners.stderr', result.stderr)
    need(not result.stderr and (result.returncode == 0 or (result.returncode == 1 and not result.stdout)),
         'cleanup listener evidence unavailable')
    return {'argv': command, 'exit_code': result.returncode, 'stdout_sha256': sha(result.stdout),
            'stderr_sha256': sha(result.stderr), 'listener_absent': result.returncode == 1}


def cleanup_bind_evidence(out):
    result = {'address': '127.0.0.1', 'port': PORT, 'available': False, 'errno': None}
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', PORT))
            result['available'] = True
        except OSError as exc:
            result['errno'] = exc.errno
    write(out / 'process-evidence/cleanup-bind.json', pretty(result))
    return result


def cleanup_socket_evidence(out):
    command = ['/usr/sbin/netstat', '-an', '-p', 'tcp']
    result = subprocess.run(command, capture_output=True, check=False, timeout=10)
    write(out / 'process-evidence/cleanup-sockets.stdout', result.stdout)
    write(out / 'process-evidence/cleanup-sockets.stderr', result.stderr)
    need(result.returncode == 0 and not result.stderr, 'cleanup socket evidence unavailable')
    states = []
    for line in result.stdout.decode('utf-8').splitlines():
        fields = line.split()
        if fields and fields[0] in ('tcp4', 'tcp6') and len(fields) >= 6 and fields[3].endswith('.' + str(PORT)):
            states.append(fields[5])
    return {'argv': command, 'exit_code': result.returncode, 'stdout_sha256': sha(result.stdout),
            'stderr_sha256': sha(result.stderr), 'states': states}


def finalize_capture(result, proc, owner, binary, asset_root, assets, plan, out):
    errors = []
    def attempt(name, function, fallback):
        try:
            return function()
        except Exception as exc:
            errors.append({'stage': name, 'error_type': type(exc).__name__})
            return fallback
    if proc is not None:
        result['wait_exit'] = attempt('direct_wait', lambda: owner.wait_after_signal(timeout=15), None)
        if result['wait_exit'] is None:
            for event in reversed(getattr(owner, 'events', [])):
                if (event.get('kind') in ('candidate-wait', 'candidate-cleanup-wait') and
                        event.get('state') == 'completed' and type(event.get('exit_code')) is int):
                    result['wait_exit'] = event['exit_code']
                    break
        attempt('owner_receipt', owner.save, None)
    result['listener_after'] = attempt('listener_check', lambda: cleanup_listener_evidence(out), None)
    result['bind_after'] = attempt('bind_check', lambda: cleanup_bind_evidence(out), None)
    result['sockets_after'] = attempt('socket_check', lambda: cleanup_socket_evidence(out), None)
    result['process_group_gone'] = proc is None or attempt('group_check', lambda: group_gone(proc.pid), False)
    result['baseline_sha256_after'] = attempt('baseline_recheck', lambda: sha(read(binary)), None)
    result['assets_unchanged'] = attempt('asset_recheck', lambda: asset_identity(asset_root) == assets, False)
    result['rustfmt_sha256_after'] = attempt('rustfmt_recheck', lambda: sha(read(RUSTFMT_PATH)), None)
    result['cleanup_errors'] = errors
    result['unrun'] = count_unrun(plan, result['rows'])
    result['passed'] = (result['error'] is None and result['unrun'] == 0 and
                        all(row['matched'] for row in result['rows']) and result['listener_after'] is not None and
                        result['listener_after']['listener_absent'] and result['bind_after'] is not None and
                        result['sockets_after'] is not None and 'LISTEN' not in result['sockets_after']['states'] and
                        (result['bind_after']['available'] or (result['bind_after']['errno'] == errno.EADDRINUSE and
                         'TIME_WAIT' in result['sockets_after']['states'])) and
                        result['process_group_gone'] and result['assets_unchanged'] and
                        result['baseline_sha256_after'] == BASELINE_SHA and
                        result['rustfmt_sha256_after'] == RUSTFMT_SHA and
                        result['wait_exit'] is not None and not errors)
    write(out / 'receipt.json', pretty(result))


def execute(args, checker):
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_OS') == 'macOS'
         and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Darwin' and platform.machine() == 'arm64', 'hosted macOS arm64 required')
    out = args.out
    need(not out.exists(), 'output already exists')
    out.mkdir(parents=True, mode=0o700)
    try:
        plan, expected, refused = prepare(args.inventory, args.evidence, args.run003, checker, args.sealed)
    except Exception as exc:
        write(out / 'receipt.json', pretty({'role': 'fresh-before', 'historical_wire': False,
              'product_acceptance': 'not_run', 'passed': False, 'unrun': 697,
              'stage': 'source_preparation', 'error_type': type(exc).__name__}))
        raise
    stage = 'request_freeze'
    try:
        public_plan = [{k: v for k, v in item.items() if k != 'request'} for item in plan]
        for item in plan:
            write(out / 'requests' / (str(item['slot_id']) + '.raw'), item['request'])
        write(out / 'plan.json', pretty({'role': 'fresh-before', 'historical_wire': False,
                                       'product_acceptance': 'not_run', 'slots': public_plan, 'refusals': refused}))
        stage = 'baseline_provenance'
        binary = args.baseline / 'som'
        need(sha(read(binary)) == BASELINE_SHA, 'baseline binary identity')
        release_path = args.repo / 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json'
        release = load(release_path)['candidate']
        need(release['binary_sha256'] == BASELINE_SHA and
             release['build_commit'] == '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af' and
             binary.stat().st_size == release['binary_bytes'], 'accepted release identity')
        identity = {'release_record_sha256': sha(read(release_path)), 'build_commit': release['build_commit']}
        for name, field in (('source.json', 'source_json_sha256'), ('build.json', 'build_receipt_sha256'),
                            ('runtime.json', 'runtime_receipt_sha256')):
            identity[name] = sha(read(args.baseline / name))
            need(identity[name] == release[field], 'accepted baseline provenance differs')
        binary_file = subprocess.run(['/usr/bin/file', str(binary)], capture_output=True, check=True, timeout=10)
        description = binary_file.stdout.decode('utf-8').strip()
        need('Mach-O' in description and 'arm64' in description, 'baseline binary platform differs')
        identity['binary_description'] = description
        assets = asset_identity(args.baseline / 'assets')
        binary_stat = binary.stat()
        prelaunch = {'binary_device': binary_stat.st_dev, 'binary_inode': binary_stat.st_ino,
                     'binary_size': binary_stat.st_size}
        need(sha(read(binary)) == BASELINE_SHA and binary.stat().st_ino == binary_stat.st_ino
             and binary.stat().st_dev == binary_stat.st_dev, 'baseline prelaunch identity differs')
        stage = 'formatter'
        need(sha(read(RUSTFMT_PATH)) == RUSTFMT_SHA, 'accepted rustfmt identity differs')
        rustfmt = subprocess.run([str(RUSTFMT_PATH), '--version'], capture_output=True, check=True, timeout=15)
        write(out / 'rustfmt-version.stdout', rustfmt.stdout)
        write(out / 'rustfmt-version.stderr', rustfmt.stderr)
        need(rustfmt.stdout.decode('utf-8').startswith('rustfmt 1.9.0') and not rustfmt.stderr,
             'accepted rustfmt version differs')
        stage = 'port_check'
        need(free_port(), 'QA port already in use')
        health = load(args.baseline / 'runtime.json')['health']
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG') if k in os.environ}
        env['SOM_TS_PRETTIER'] = str(args.prettier)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        argv = [str(binary), 'serve', '--assets', str(args.baseline / 'assets'), '--port', str(PORT)]
        result = {'role': 'fresh-before', 'historical_wire': False, 'product_acceptance': 'not_run',
                  'baseline_sha256': BASELINE_SHA, 'argv': argv, 'port': PORT,
                  'baseline_provenance': identity, 'assets': assets,
                  'binary_prelaunch': prelaunch,
                  'rustfmt': {'path': str(RUSTFMT_PATH), 'sha256': RUSTFMT_SHA,
                              'version': rustfmt.stdout.decode('utf-8').strip()},
                  'runner': {'platform': platform.platform(), 'machine': platform.machine(),
                             'python': sys.version, 'python_executable': sys.executable,
                             'github_sha': os.environ.get('GITHUB_SHA'), 'github_run_id': os.environ.get('GITHUB_RUN_ID'),
                             'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')},
                  'owner_pid': os.getpid(), 'pid': None, 'wait_exit': None,
                  'process_group_gone': False, 'rows': [], 'error': None}
        proc = None
        stage = 'owner_setup'
        from owned_candidate import CandidateOwner
        owner = CandidateOwner(out, args.baseline, PORT)
    except Exception as exc:
        write(out / 'receipt.json', pretty({'role': 'fresh-before', 'historical_wire': False,
              'product_acceptance': 'not_run', 'passed': False, 'unrun': 697,
              'stage': stage, 'error_type': type(exc).__name__}))
        raise
    try:
        with (out / 'baseline.stdout').open('xb') as stdout, (out / 'baseline.stderr').open('xb') as stderr:
            proc = subprocess.Popen(argv, cwd=args.baseline, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
            owner.process = proc
            result['pid'] = proc.pid
            deadline = time.monotonic() + 45
            while True:
                identity = subprocess.run(['/bin/ps', '-p', str(proc.pid), '-o', 'pid=,ppid=,pgid=,stat='],
                                          capture_output=True, timeout=10, check=False)
                fields = identity.stdout.decode().split()
                need(identity.returncode == 0 and len(fields) == 4 and
                     fields[:3] == [str(proc.pid), str(os.getpid()), str(proc.pid)] and
                     not fields[3].startswith('Z'), 'baseline ownership lost during startup')
                try:
                    with opener.open(f'http://127.0.0.1:{PORT}/healthz', timeout=2) as response:
                        health_raw = response.read()
                        write(out / 'health.raw', health_raw)
                        need(response.status == 200 and json.loads(health_raw) == health, 'baseline health differs')
                        result['health_status'] = response.status
                        result['health_sha256'] = sha(health_raw)
                    break
                except (urllib.error.URLError, TimeoutError):
                    need(time.monotonic() < deadline, 'baseline startup timed out')
                    time.sleep(0.25)
            result['process_start'] = process_evidence(proc, argv, binary, out, 'start', prelaunch=prelaunch)
            for item in plan:
                slot = str(item['slot_id'])
                row = {'slot_id': item['slot_id'], 'status': None, 'response_sha256': None,
                       'matched': False, 'comparison': 'unrun'}
                result['rows'].append(row)
                request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/assemble', data=item['request'],
                                                 headers={'Content-Type': 'application/json'}, method='POST')
                try:
                    with opener.open(request, timeout=60) as response:
                        row['status'], raw = response.status, response.read()
                except urllib.error.HTTPError as response:
                    row['status'], raw = response.code, response.read()
                write(out / 'responses' / (slot + '.raw'), raw)
                row['response_sha256'] = sha(raw)
                doc = json.loads(raw)
                files = doc.get('files') if type(doc) is dict else None
                row['matched'] = (row['status'] == 200 and type(files) is dict and
                                  checker.qa.exact_equal(files, expected[slot]))
                row['comparison'] = 'passed' if row['matched'] else 'failed'
            result['process_end'] = process_evidence(proc, argv, binary, out, 'end', result['process_start'])
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        finalize_capture(result, proc, owner, binary, args.baseline / 'assets', assets, plan, out)
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser()
    for name in ('inventory', 'evidence', 'run003', 'baseline', 'out', 'repo', 'sealed', 'prettier'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo / 'som-rs/scripts/typescript_output_acceptance'))
    import check_rust_baseline as checker
    return execute(args, checker)


if __name__ == '__main__':
    raise SystemExit(main())
