#!/usr/bin/env python3
"""Record the provisioned Python image immediately after the Mac provisioner."""
import json
import os
from pathlib import Path

import mac_parity_capture as parity
import parity_python_authority as authority

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
BINARY=Path('/opt/homebrew/opt/python@3.12/bin/python3.12')

def capture(out,repo,python,provision_report,revision,sources):
    out=Path(out);out.mkdir(mode=0o700)
    observer=parity.TrackingRecorder(out)
    row=authority.capture(observer,out,python,Path(repo)/'som-rs/scripts/react_output_acceptance/parity_python_probe.py',repo,provision_report,python)
    if observer.errors:raise ValueError('Python provisioning observation failed')
    receipt={'version':'som.m6-python-provision-identity.v1','source_revision':revision,'source_before':sources,
             'events':observer.events,'python_authority':row}
    return receipt

def main():
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or os.environ.get('RUNNER_OS')!='macOS':
        raise ValueError('GitHub Mac required')
    temp=Path(os.environ['RUNNER_TEMP'])
    out=temp/'m6-python-provision-identity'
    before=parity.source_hashes()
    receipt=capture(out,REPO,BINARY,temp/'m6-provision/report.json',os.environ['GITHUB_SHA'],before)
    receipt['source_after']=parity.source_hashes()
    if receipt['source_after']!=before:raise ValueError('Python provisioning source changed')
    with (out/'receipt.json').open('x') as stream:json.dump(receipt,stream,sort_keys=True)

if __name__=='__main__':main()
