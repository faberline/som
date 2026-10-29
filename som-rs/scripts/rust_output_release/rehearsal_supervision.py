"""Bound direct rehearsal children without pretending to reap detached services.

The child owns its rollback and sandbox cleanup. A timeout is always a failure.
Only an observed child wait is reported here; service cleanup is a separate gate.
"""
from __future__ import annotations

import json
from pathlib import Path
import signal
import subprocess
import time


def invoke(argv, cwd, out, name, timeout=None, grace=180, lifecycle_dir=None, defer_spawn_signals=False):
    """Retain direct-child lifecycle even if waiting or cleanup is interrupted."""
    out = Path(out)
    record = {'argv': list(argv), 'cwd': str(cwd), 'pid': None, 'outcome': 'not_started',
              'exit_code': None, 'wait_result': None, 'term_sent': False,
              'kill_sent': False, 'cleanup_verified': False}
    child = None
    previous = {}
    pending = []
    def defer(signum, frame):
        pending.append((signum, frame))
    def restore_handlers():
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        previous.clear()
    started = time.monotonic()
    try:
        with (out / (name + '.stdout')).open('xb') as stdout, (out / (name + '.stderr')).open('xb') as stderr:
            if defer_spawn_signals:
                for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                    handler = signal.getsignal(signum)
                    if not callable(handler):
                        raise ValueError('spawn deferral requires explicit signal handlers')
                    previous[signum] = handler
                for signum in previous:
                    signal.signal(signum, defer)
            child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            record['pid'] = child.pid
            record['outcome'] = 'running'
            try:
                handlers = dict(previous)
                restore_handlers()
                if pending:
                    handlers[pending[0][0]](*pending[0])
                record['wait_result'] = child.wait(timeout=timeout)
                record['exit_code'] = child.returncode
                record['outcome'] = 'completed'
            except BaseException as error:
                # Popen is our direct child; never signal a PID obtained from a receipt.
                record['outcome'] = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'interrupted'
                record['error_type'] = type(error).__name__
                try:
                    if child.poll() is None:
                        child.terminate()
                        record['term_sent'] = True
                    try:
                        record['wait_result'] = child.wait(timeout=grace)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        record['kill_sent'] = True
                        record['wait_result'] = child.wait(timeout=30)
                    record['exit_code'] = child.returncode
                except BaseException as cleanup_error:
                    record['wait_error_type'] = type(cleanup_error).__name__
                if not isinstance(error, subprocess.TimeoutExpired):
                    raise
    except OSError as error:
        record['outcome'] = 'process_error'
        record['error_type'] = type(error).__name__
    finally:
        restore_handlers()
        record['elapsed_seconds'] = time.monotonic() - started
        # This field deliberately remains false here. A direct wait cannot prove
        # that a detached service, its listener, and the sandbox were cleaned up.
        lifecycle_dir = out if lifecycle_dir is None else Path(lifecycle_dir)
        with (lifecycle_dir / (name + '.lifecycle.json')).open('x', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
    return record
