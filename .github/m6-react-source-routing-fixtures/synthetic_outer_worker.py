"""Synthetic outer collector for GitHub-only cancellation fixtures."""
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace

fixture=Path(sys.argv[1]); repo=Path(sys.argv[2])
sys.path.insert(0,str(fixture))
import run_acceptance as collector

def interrupt(number, frame): raise KeyboardInterrupt(str(number))
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGTERM, interrupt)
collector.NATIVE_ROUTING_REVIEWED=True
entry='som-rs/scripts/react_output_acceptance/m6_native_entry.py'
target='fake_step.py'
ctx=SimpleNamespace(qa_mode='native-qa-v1',qa_source_authority={'admitted_repository_python_sources':[entry,target]},
                    sources=SimpleNamespace(read=lambda path:b'fixture'),path=repo/'context.json',
                    helper_revision='a'*40,candidate_revision='a'*40)
sys.modules['full_product_context']=SimpleNamespace(context=lambda:ctx)
out=repo/'.local/run'; (out/'logs').mkdir(parents=True); (out/'receipts').mkdir(); (out/'api-qa-evidence').mkdir()
(out/'api-qa-evidence/full-before.json').write_text('{}')
step={'index':1,'step':'products','argv':[sys.executable,target],'report':out/'products/report.json','kind':'report'}
try:
    collector.run_single_step(step,out,repo,repo/'api.json')
except KeyboardInterrupt:
    (repo/'outer-interrupted').write_text('yes')
