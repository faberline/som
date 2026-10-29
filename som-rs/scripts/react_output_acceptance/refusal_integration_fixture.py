#!/usr/bin/env python3
"""Bounded old-candidate fixture, not product or parity acceptance."""
import contextlib
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(os.path.realpath(os.environ['GITHUB_WORKSPACE']))
BASE = ROOT / '.local/refusal-integration-fixture'
CODE = ROOT / 'som-rs/scripts/react_output_acceptance'
CHECKER = ROOT / 'som-rs/som-targets/qa/check_rust_products.py'
OLD = Path(os.environ['RUNNER_TEMP']) / 'evidence/candidate'
QA = 'http://127.0.0.1:18919'
INPUT_COMMIT = '55eb7f5c3b21d4f3b57eb721ab60c6aa97e10db3'
INPUT_SHA = '78375feb14ed286ac5df4c52f13d5d1ae301b2057d5e3894edf0f36c2705c498'
SOURCES = [
    'som-rs/som-targets/qa/check_rust_products.py',
    'som-rs/som-targets/qa/check_targets.py',
    'som-rs/tests/rust-output-v1/rust_profile.py',
    'som-rs/tests/rust-output-v1/rust_profile_phase2.py',
    'som-rs/scripts/rust_output_acceptance/python_replay.py',
    'som-rs/scripts/compiler_acceptance/qa_harness.py',
    'som-rs/scripts/compiler_acceptance/paths.py',
    'som-rs/scripts/react_output_acceptance/record_rust006_http.py',
    'som-rs/scripts/react_output_acceptance/audit/audit_mac_rust006_extra.py',
    'som-rs/scripts/react_output_acceptance/refusal_integration_fixture.py',
]
def need(ok, label):
    if not ok: raise ValueError(label)
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def free():
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', 18919)) == errno.ECONNREFUSED
def main():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted' and os.environ.get('RUNNER_OS') == 'macOS', 'hosted macOS only')
    need(not BASE.exists() and free(), 'fixture path or port unavailable')
    BASE.mkdir(mode=0o700)
    source_before = {name: digest(ROOT / name) for name in SOURCES}
    need(digest(OLD / 'som') == '59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308', 'old candidate hash differs')
    sys.path.insert(0, str(CODE))
    sys.path.insert(0, str(CODE / 'audit'))
    import record_rust006_http as recorder
    import audit_mac_rust006_extra as audit
    class BoundedCalls(audit.Calls):
        def __init__(self, root, script):
            self.root = root
            manifest = audit.json_file(root / 'manifest.json')
            audit.need(manifest.get('version') == 'som.m6-rust006-http-recording.v1' and manifest.get('exit_code') == 2
                and manifest.get('recording_errors') == [] and manifest.get('script', {}).get('path') == script
                and manifest['script']['sha256'] == audit.sha(audit.read(ROOT / script)), 'bounded recorder identity differs')
            self.rows = manifest['calls']
            audit.need(type(self.rows) is list, 'calls missing')
            self.index = 0
    # This substitution is local to the fixture process; the release audit remains unchanged.
    audit.Calls = BoundedCalls
    def blob(name):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(ROOT), 'show', INPUT_COMMIT + ':' + name], timeout=120)
    files = audit.manifest_map(blob('inputs-index.json'), INPUT_SHA, blob)
    bound = audit.BoundInputs(ROOT, files)
    legacy = ROOT / '.local/rust-compiler-goal/acceptance/legacy544/run-005'
    env = {key: value for key, value in os.environ.items() if key in recorder.CHILD_ENV}
    env['SOM_TS_PRETTIER'] = str(ROOT / '.local/m6-hosted/prettier')
    env['CARGO_NET_OFFLINE'] = 'true'
    proc = None
    stopped = False
    failure = None
    cleanup = {'candidate_started': False, 'term_sent': False, 'direct_wait': False,
               'timed_out': False, 'port_free': False, 'error_type': None}
    try:
        with (BASE / 'candidate.log').open('wb') as log:
            proc = subprocess.Popen([str(OLD / 'som'), 'serve', '--assets', str(OLD / 'assets'), '--port', '18919'], cwd=OLD, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            cleanup['candidate_started'] = True
            deadline = time.monotonic() + 30
            while True:
                need(proc.poll() is None, 'candidate exited')
                try:
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(QA + '/healthz', timeout=2) as response:
                        need(response.status == 200, 'health status differs')
                        break
                except OSError:
                    need(time.monotonic() < deadline, 'health timeout')
                    time.sleep(.2)
            os.environ['SOM_TS_PRETTIER'] = env['SOM_TS_PRETTIER']
            os.environ['CARGO_NET_OFFLINE'] = 'true'
            args = ['--qa', QA, '--pg', 'unused:1', '--work', str(BASE / 'work'), '--target-dir', str(BASE / 'target'), '--out', str(BASE / 'report.json'), '--no-gates', '--refusals', 'phase2']
            with (BASE / 'checker.stdout').open('w') as out, (BASE / 'checker.stderr').open('w') as err, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = recorder.run_checker(BASE / 'raw', CHECKER, args)
            need(code == 2, 'checker did not exit 2')
            report = audit.json_file(BASE / 'report.json')
            summary = report['summary']
            need(summary['assemble_refusals'] == {'passed': 15, 'total': 15} and summary['bundle_checks'] == {'passed': 115, 'total': 115} and summary['acceptance_cases']['checked'] == 115 and summary['product_gates']['total'] == 0 and summary['parity']['status'] == 'not_run' and summary['failed'] == [] and len(summary['blocked']) == 1, 'checker summary differs')
            need(len(report['cases']) == 115 and all(row['parity']['status'] == 'not_run' for row in report['cases']) and len(report['assemble_refusals']) == 15, 'checker rows differ')
            result = audit.audit_refusals(BASE / 'raw', legacy, bound)
            need(result['refusals'] == 15 and result['bundle_cases'] == 115, 'independent counts differ')
    except Exception as exc:
        failure = exc
    finally:
        try:
            if proc is not None and proc.poll() is None:
                need(os.getpgid(proc.pid) == proc.pid, 'candidate process group differs')
                os.killpg(proc.pid, signal.SIGTERM)
                cleanup['term_sent'] = True
                try:
                    proc.wait(timeout=10)
                    cleanup['direct_wait'] = True
                    stopped = True
                except subprocess.TimeoutExpired:
                    cleanup['timed_out'] = True
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)
                    cleanup['direct_wait'] = True
            elif proc is not None:
                proc.wait(timeout=10)
                cleanup['direct_wait'] = True
            cleanup['port_free'] = free()
        except Exception as exc:
            cleanup['error_type'] = type(exc).__name__
        if failure is not None:
            print(json.dumps({'failure_type': type(failure).__name__, 'cleanup': cleanup}), file=sys.stderr)
    if failure is not None:
        raise failure
    need(stopped and cleanup['port_free'] and cleanup['error_type'] is None, 'candidate wait or port cleanup unverified')
    need(source_before == {name: digest(ROOT / name) for name in SOURCES}, 'source changed during fixture')
    print(json.dumps({'status': 'bounded_fixture_pass', 'candidate_sha256': digest(OLD / 'som'), 'checker_exit': 2, 'parity': 'not_run', 'product_gates': 'not_run', 'product_acceptance': False, 'independent': result, 'candidate_wait_completed': stopped, 'port_free': True, 'source_sha256': source_before}, sort_keys=True))
if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'bounded_fixture_failed', 'error_type': type(exc).__name__, 'product_acceptance': False}), file=sys.stderr)
        raise SystemExit(1)
