"""Synthetic owned process nesting for GitHub-only cancellation fixtures."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest import mock

args = sys.argv
repo = Path(args[args.index('--repo') + 1])
postcheck = Path(args[args.index('--postcheck') + 1])
scope = args[args.index('--scope') + 1]

def interrupt(number, frame): raise KeyboardInterrupt(str(number))
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGTERM, interrupt)

if scope == 'case':
    child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', 'import time; time.sleep(20)'],
                             start_new_session=True)
    (repo/'case-started').write_text(str(child.pid))
    try:
        child.wait(timeout=25)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        (repo/'case-child-stopped').write_text(str(child.returncode))
        postcheck.write_text('{}')
else:
    fixture = Path(os.environ['M6_TEST_FIXTURE_DIR'])
    sys.path.insert(0, str(fixture))
    common = ModuleType('common'); common.CONTRACT=repo/'contracts'; common.REPO=repo
    with mock.patch.dict(sys.modules, {'common':common, 'check_contract':ModuleType('check_contract')}):
        import check_products as products
    products.REPO=repo
    entry='som-rs/scripts/react_output_acceptance/m6_native_entry.py'
    target='fake_api.py'
    sources=SimpleNamespace(read=lambda path:b'fixture')
    ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority={'admitted_repository_python_sources':[entry,target]},
                        path=repo/'context.json',helper_revision='a'*40,candidate_revision='a'*40)
    before=repo/'.local/run/api-qa-evidence/full-before.json'
    holder=SimpleNamespace(qa_mode='native-qa-v1',context=ctx,live={'python':sys.executable},
                           router=SimpleNamespace(sources=sources),api_profile=repo/'api.json',full_before=before)
    sys.modules['_som_m6_checked_router']=holder
    case=repo/'.local/run/products/case'; case.mkdir(parents=True,exist_ok=True)
    try:
        products.api_invocation([str(repo/target)],case/'api.log',case/'api-invocation.json',sys.executable,
                                timeout=30,grace=4,full_before=before)
    except KeyboardInterrupt:
        pass
    finally:
        postcheck.write_text('{}')
