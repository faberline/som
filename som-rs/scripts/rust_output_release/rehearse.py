#!/usr/bin/env python3
"""Rehearse the #51 switch, its rollback and its recovery on harness ports.

Each scenario gets a sandbox root under --out with som-rs/runtime/default.json,
runtime/current and compiler-assets/current. A stand-in of the current live
default command (same binary and assets) serves a harness port, and the
sandbox receipt names it. The live default on 8765 is only read.

Scenarios:
- switch: switch.py succeeds, check_default.py passes on the candidate, then
  switch.py --restore puts the stand-in command back and check_default.py
  passes on it.
- after_stop, after_start, after_pointer, after_commit: switch.py fails on an
  injected failure, exits nonzero and journals rollback_verified, and
  check_default.py passes on the restored previous command.

Each subprocess's argv, exit code, times and output files are recorded in
rehearsal.json. Every sandbox owner is stopped by verified identity at the end.

usage: rehearse.py --prerequisites FILE --out NEW_DIR
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_common as rc  # noqa: E402

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable


def call(argv, out, name):
    started = time.time()
    with (out / f'{name}.stdout').open('xb') as stdout, (out / f'{name}.stderr').open('xb') as stderr:
        code = subprocess.run(argv, stdout=stdout, stderr=stderr, cwd=rc.REPO).returncode
    return {'name': name, 'argv': argv, 'exit_code': code, 'started_at': started, 'ended_at': time.time(),
            'stdout_sha256': rc.digest(out / f'{name}.stdout'), 'stderr_sha256': rc.digest(out / f'{name}.stderr')}


LAUNCHER = ('import subprocess, sys\n'
            'log = open(sys.argv[1], "xb")\n'
            'child = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,\n'
            '                         close_fds=True, start_new_session=True)\n'
            'print(child.pid)\n')


def launch_detached(argv, log, port, health):
    """Start ARGV through a launcher that exits, so the service is not our child and launchd reaps it."""
    rc.port_free(port)
    launched = subprocess.run([PYTHON, '-c', LAUNCHER, str(log), *argv], capture_output=True, text=True,
                              check=True, cwd=rc.REPO)
    pid = int(launched.stdout)
    end = time.monotonic() + 45
    while time.monotonic() < end:
        try:
            owner = rc.identity(pid, argv, port)
            status, raw = rc.post_raw(port, '/healthz', None)
            if status == 200 and rc.exact_json(json.loads(raw), health):
                return owner
        except (subprocess.CalledProcessError, OSError, RuntimeError, ValueError):
            pass
        rc.require(rc.alive(pid), f'stand-in exited: {argv}')
        time.sleep(.2)
    raise RuntimeError('stand-in startup timed out')


def sandbox(out, live, port):
    """A sandbox root whose receipt names a fresh stand-in of the live command on PORT."""
    root = out / 'root'
    (root / 'som-rs/runtime').mkdir(parents=True)
    (root / 'som-rs/compiler-assets').mkdir(parents=True)
    (root / 'som-rs/runtime/current').symlink_to(str(Path(live['command'][0]).parent))
    (root / 'som-rs/compiler-assets/current').symlink_to(live['command'][3])
    (out / 'stand-in').mkdir()
    owner = launch_detached(live['command'][:-1] + [str(port)], out / 'stand-in/process.log', port, live['health'])
    receipt = {**live, 'pid': owner['pid'], 'command': owner['argv'], 'identity': owner,
               'process_logs': str(out / 'stand-in/process.log'), 'evidence': str(out / 'stand-in'),
               'rehearsal_stand_in_of': rc.digest(rc.REPO / 'som-rs/runtime/default.json')}
    (root / 'som-rs/runtime/default.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return root


def stop_sandbox(root, port):
    receipt = rc.load(root / 'som-rs/runtime/default.json')
    if rc.alive(receipt['pid']):
        rc.terminate(rc.identity(receipt['pid'], receipt['command'], port))
    return receipt['pid']


def journal_stages(path):
    return [event['stage'] for event in rc.load(path / 'journal.json')]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--prerequisites', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    prerequisites, out = args.prerequisites.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = rc.load(prerequisites)
    rc.check_prerequisites(config)
    rc.apply_formatter_env(config)
    live_bytes = (rc.REPO / 'som-rs/runtime/default.json').read_bytes()
    live = json.loads(live_bytes)
    rc.require(rc.identity(live['pid'], live['command'], 8765) == live['identity'], 'live default differs')
    previous_sha = live['identity']['binary_sha256']
    candidate_sha = config['candidate']['binary']['sha256']
    scenarios = []
    for name in ('switch', 'after_stop', 'after_start', 'after_pointer', 'after_commit'):
        directory = out / name
        directory.mkdir()
        port = rc.free_harness_port()
        root = sandbox(directory, live, port)
        common = ['--prerequisites', str(prerequisites), '--root', str(root), '--port', str(port)]
        calls, problems = [], []
        try:
            switch = [PYTHON, str(HERE / 'switch.py'), *common, '--rehearsal', '--execute',
                      '--output', str(directory / 'switch')]
            if name != 'switch':
                switch += ['--inject-failure', name]
            calls.append(call(switch, directory, 'switch'))
            stages = journal_stages(directory / 'switch')
            check_sha = candidate_sha if name == 'switch' else previous_sha
            calls.append(call([PYTHON, str(HERE / 'check_default.py'), *common, '--expect-binary-sha256', check_sha,
                               '--output', str(directory / 'check')], directory, 'check'))
            if name == 'switch':
                if calls[0]['exit_code'] != 0 or stages[-1] != 'committed':
                    problems.append('switch did not commit')
                calls.append(call([PYTHON, str(HERE / 'switch.py'), *common, '--rehearsal',
                                   '--restore', str(directory / 'switch'), '--output', str(directory / 'restore')],
                                  directory, 'restore'))
                if calls[-1]['exit_code'] != 0 or journal_stages(directory / 'restore')[-1] != 'restore_verified':
                    problems.append('restore did not verify')
                calls.append(call([PYTHON, str(HERE / 'check_default.py'), *common, '--expect-binary-sha256',
                                   previous_sha, '--output', str(directory / 'check-restored')],
                                  directory, 'check-restored'))
            else:
                if calls[0]['exit_code'] == 0 or stages[-1] != 'rollback_verified':
                    problems.append('injected failure was not rolled back')
                if f'injected failure at {name}' not in json.dumps(rc.load(directory / 'switch/journal.json')):
                    problems.append('journal does not show the injected failure')
            for row in calls:
                if row['name'].startswith('check') and row['exit_code'] != 0:
                    problems.append(f'{row["name"]} failed')
        except Exception as error:
            problems.append(repr(error))
        finally:
            stopped = stop_sandbox(root, port)
        scenarios.append({'name': name, 'port': port, 'root': str(root), 'calls': calls,
                          'switch_stages': journal_stages(directory / 'switch') if (directory / 'switch/journal.json').exists() else None,
                          'stopped_pid': stopped, 'problems': problems, 'passed': not problems})
    rc.require((rc.REPO / 'som-rs/runtime/default.json').read_bytes() == live_bytes, 'live receipt changed')
    rc.require(rc.identity(live['pid'], live['command'], 8765) == live['identity'], 'live default changed')
    report = {'version': 'som.rust-output-switch-rehearsal.v1', 'prerequisites': {'path': str(prerequisites),
              'sha256': rc.digest(prerequisites)}, 'prerequisites_receipt_sha256': rc.receipt_sha(config),
              'script_sha256': {p.name: rc.digest(p) for p in sorted(HERE.glob('*.py'))},
              'live_default_sha256': rc.sha_bytes(live_bytes), 'live_default_unchanged': True,
              'scenarios': scenarios, 'passed': all(s['passed'] for s in scenarios)}
    (out / 'rehearsal.json').write_text(json.dumps(report, indent=1) + '\n')
    print(json.dumps({'passed': report['passed'], 'scenarios': {s['name']: s['passed'] for s in scenarios}}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
