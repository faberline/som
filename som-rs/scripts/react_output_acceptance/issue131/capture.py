#!/usr/bin/env python3
"""Fresh-before only: capture the historic Rust baseline on a hosted macOS runner.

The authenticated sources and sealed output transport are the caller's job.
This program never identifies its output as a historical wire capture or a
product acceptance result. All preparation must succeed before it starts SOM.
"""
import argparse
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
    public_plan = [{k: v for k, v in item.items() if k != 'request'} for item in plan]
    for item in plan:
        write(out / 'requests' / (str(item['slot_id']) + '.raw'), item['request'])
    write(out / 'plan.json', pretty({'role': 'fresh-before', 'historical_wire': False,
                                   'product_acceptance': 'not_run', 'slots': public_plan, 'refusals': refused}))
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
    need(free_port(), 'QA port already in use')
    health = load(args.baseline / 'runtime.json')['health']
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG') if k in os.environ}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    argv = [str(binary), 'serve', '--assets', str(args.baseline / 'assets'), '--port', str(PORT)]
    result = {'role': 'fresh-before', 'historical_wire': False, 'product_acceptance': 'not_run',
              'baseline_sha256': BASELINE_SHA, 'argv': argv, 'port': PORT,
              'baseline_provenance': identity, 'assets': assets,
              'runner': {'platform': platform.platform(), 'machine': platform.machine(),
                         'python': sys.version, 'python_executable': sys.executable,
                         'github_sha': os.environ.get('GITHUB_SHA'), 'github_run_id': os.environ.get('GITHUB_RUN_ID'),
                         'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')},
              'owner_pid': os.getpid(), 'pid': None, 'wait_exit': None,
              'process_group_gone': False, 'port_free_after': False, 'rows': [], 'error': None}
    proc = None
    from owned_candidate import CandidateOwner
    owner = CandidateOwner(out, args.baseline, PORT)
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
                        need(response.status == 200 and json.loads(response.read()) == health, 'baseline health differs')
                    break
                except (urllib.error.URLError, TimeoutError):
                    need(time.monotonic() < deadline, 'baseline startup timed out')
                    time.sleep(0.25)
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
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if proc is not None:
            try:
                result['wait_exit'] = owner.wait_after_signal(timeout=15)
            except Exception as exc:
                result['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
            owner.save()
        result['port_free_after'] = free_port()
        result['process_group_gone'] = proc is None or group_gone(proc.pid)
        result['baseline_sha256_after'] = sha(read(binary))
        result['assets_unchanged'] = asset_identity(args.baseline / 'assets') == assets
        result['unrun'] = count_unrun(plan, result['rows'])
        result['passed'] = (result['error'] is None and result['unrun'] == 0 and
                            all(row['matched'] for row in result['rows']) and result['port_free_after'] and
                            result['process_group_gone'] and result['assets_unchanged'] and
                            result['baseline_sha256_after'] == BASELINE_SHA and
                            result['wait_exit'] is not None and 'cleanup_error' not in result)
        write(out / 'receipt.json', pretty(result))
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser()
    for name in ('inventory', 'evidence', 'run003', 'baseline', 'out', 'repo', 'sealed'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo / 'som-rs/scripts/typescript_output_acceptance'))
    import check_rust_baseline as checker
    return execute(args, checker)


if __name__ == '__main__':
    raise SystemExit(main())
