"""GitHub-hosted-only controlled subprocess lifecycle checks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rehearsal_supervision as supervision


def hosted():
    return os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def worker(out):
    service = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], start_new_session=True)
    (out / 'service.pid').write_text(str(service.pid))
    def stop(_signum, _frame):
        raise SystemExit(19)
    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            time.sleep(.1)
    finally:
        service.terminate()
        code = service.wait(timeout=10)
        (out / 'worker-cleanup.json').write_text(json.dumps({'pid': service.pid, 'wait': code, 'alive': alive(service.pid)}))


def controller(out):
    import rehearse
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, rehearse.interrupted)
    try:
        rehearse.call([sys.executable, str(Path(__file__).resolve()), 'worker', str(out)], out, 'inner')
    finally:
        # The production controller performs ownership-verified sandbox cleanup here.
        (out / 'controller-finally.json').write_text(json.dumps({'finally_ran': True}))


class SupervisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hosted():
            raise RuntimeError('GitHub-hosted runner required')

    def test_direct_child_wait_and_exit_are_retained(self):
        with tempfile.TemporaryDirectory() as value:
            out = Path(value)
            result = supervision.invoke([sys.executable, '-c', 'print("ok")'], out, out, 'simple', timeout=20, grace=10)
            self.assertEqual(result['outcome'], 'completed')
            self.assertEqual(result['wait_result'], 0)
            self.assertEqual(result['exit_code'], 0)
            self.assertFalse(result['cleanup_verified'])
            self.assertEqual((out / 'simple.stdout').read_bytes(), b'ok\n')
            self.assertEqual(json.loads((out / 'simple.lifecycle.json').read_bytes()), result)

    def test_timeout_asks_controller_and_owned_service_to_clean_up(self):
        with tempfile.TemporaryDirectory() as value:
            out = Path(value)
            result = supervision.invoke([sys.executable, str(Path(__file__).resolve()), 'controller', str(out)],
                                        out, out, 'outer', timeout=3, grace=20)
            self.assertEqual(result['outcome'], 'timeout')
            self.assertTrue(result['term_sent'])
            self.assertFalse(result['kill_sent'])
            self.assertIs(type(result['wait_result']), int)
            self.assertEqual(result['wait_result'], result['exit_code'])
            self.assertFalse(result['cleanup_verified'])
            self.assertTrue(json.loads((out / 'controller-finally.json').read_bytes())['finally_ran'])
            inner = json.loads((out / 'inner.lifecycle.json').read_bytes())
            self.assertEqual(inner['outcome'], 'interrupted')
            self.assertTrue(inner['term_sent'])
            self.assertFalse(inner['kill_sent'])
            self.assertIs(type(inner['wait_result']), int)
            pid = int((out / 'service.pid').read_text())
            cleanup = json.loads((out / 'worker-cleanup.json').read_bytes())
            self.assertEqual(cleanup['pid'], pid)
            self.assertIs(type(cleanup['wait']), int)
            self.assertFalse(cleanup['alive'])
            self.assertFalse(alive(pid))

    def test_forced_child_stop_does_not_claim_detached_cleanup(self):
        with tempfile.TemporaryDirectory() as value:
            out = Path(value)
            code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)'
            result = supervision.invoke([sys.executable, '-c', code], out, out, 'forced', timeout=1, grace=1)
            self.assertEqual(result['outcome'], 'timeout')
            self.assertTrue(result['term_sent'])
            self.assertTrue(result['kill_sent'])
            self.assertIs(type(result['wait_result']), int)
            self.assertEqual(result['wait_result'], result['exit_code'])
            self.assertFalse(result['cleanup_verified'])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] in ('worker', 'controller'):
        if not hosted():
            raise RuntimeError('GitHub-hosted runner required')
        {'worker': worker, 'controller': controller}[sys.argv[1]](Path(sys.argv[2]))
    else:
        unittest.main()
