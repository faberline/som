"""Real short-lived Python process nesting; GitHub-hosted runner only."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')


class OuterCancellationTests(unittest.TestCase):
    def test_interrupted_outer_wait_keeps_raw_failure_and_waits_owned_children(self):
        with tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as temp:
            repo=Path(temp)/'repo'; repo.mkdir()
            fixture=Path(__file__).resolve().parent
            entry=repo/'som-rs/scripts/react_output_acceptance/m6_native_entry.py'
            entry.parent.mkdir(parents=True)
            shutil.copyfile(fixture/'synthetic_native_entry.py',entry)
            env=dict(os.environ,M6_TEST_FIXTURE_DIR=str(fixture))
            outer=subprocess.Popen([sys.executable,'-I','-S','-B',str(fixture/'synthetic_outer_worker.py'),
                                    str(fixture),str(repo)],env=env,start_new_session=True,
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+12
                while not (repo/'case-started').exists() and outer.poll() is None and time.monotonic()<deadline:
                    time.sleep(.05)
                self.assertTrue((repo/'case-started').exists(),'nested controlled child did not start')
                os.killpg(outer.pid,signal.SIGTERM)
                stdout,stderr=outer.communicate(timeout=15)
                self.assertEqual(outer.returncode,0,stderr.decode(errors='replace'))
                self.assertTrue((repo/'case-child-stopped').is_file())
                self.assertTrue((repo/'outer-interrupted').is_file())
                receipt=json.loads((repo/'.local/run/receipts/01-products.json').read_bytes())
                self.assertEqual(receipt['judgment'],'failed')
                self.assertEqual(receipt['source_routing']['interruption_cleanup']['nested_services'],'unverified')
                self.assertEqual(receipt['source_routing']['wait']['state'],'completed')
                case=json.loads((repo/'.local/run/products/case/api-invocation.json').read_bytes())
                self.assertEqual(case['outcome'],'interrupted')
                self.assertEqual(case['interruption_cleanup']['owned_services'],'unverified')
                self.assertEqual(case['wait']['state'],'completed')
            finally:
                if outer.poll() is None:
                    outer.terminate()
                    try: outer.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        outer.kill(); outer.wait(timeout=10)


if __name__ == '__main__': unittest.main()
