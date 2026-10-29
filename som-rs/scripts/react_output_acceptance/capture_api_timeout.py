"""Retain one real timeout and cooperative child cleanup on GitHub only."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

with mock.patch.dict(sys.modules, {'check_contract': ModuleType('check_contract')}):
    import check_products

out = Path(os.environ['RUNNER_TEMP']) / 'react-api-timeout-proof'
out.mkdir()
marker = out / 'child-state.txt'
code = ("import pathlib,subprocess,sys,time\n"
        "marker=pathlib.Path(sys.argv[1])\n"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
        "try:\n"
        " marker.write_text(str(p.pid))\n"
        " time.sleep(30)\n"
        "finally:\n"
        " p.terminate(); p.wait(timeout=5); marker.write_text('stopped')\n")
row = check_products.api_invocation(['-c', code, str(marker)], out / 'api.log',
                                    out / 'api-invocation.json', sys.executable, timeout=1, grace=10)
if (row['outcome'] != 'timed-out' or row['failure'] != 'timeout'
        or row.get('timeout_cleanup', {}).get('method') != 'interrupt-recorder'
        or row['timeout_cleanup'].get('recorder_wait') != 'completed'
        or marker.read_text() != 'stopped'):
    raise SystemExit('timeout cleanup fixture failed')
print(json.dumps(row, sort_keys=True))
