"""Compare actual prior GitHub venv inventory to independently derived source bytes.

This is input preparation evidence only, not a product test or acceptance.
"""
import json
import os
from pathlib import Path
import sys
import tarfile

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import linux_install_map as audit

def need(ok, message):
    if not ok: raise ValueError(message)

def main():
    temp=Path(os.environ['RUNNER_TEMP'])
    source=temp/'react39-source/react-python-qa-closure.tar'
    proof=temp/'react-live-venv-proof.tar'
    raw_tar=source.read_bytes()
    need(audit.sha(raw_tar)==audit.TAR_SHA,'source TAR differs')
    with tarfile.open(source,'r:') as archive:
        index={item.name.rstrip('/'):item for item in archive.getmembers()}
        closure=audit.tar_data(archive,index,audit.BASE+'/receipt.json')
        rows=json.loads(closure)['wheels']
        inventories={row['name']:audit.tar_data(archive,index,audit.BASE+'/wheel-audits/'+row['name']+'/inventory.json') for row in rows}
    with tarfile.open(proof,'r:') as archive:
        index={item.name.rstrip('/'):item for item in archive.getmembers()}
        installed=audit.strict_json(audit.tar_data(archive,index,'som-react-python-closure/environments/react-api-qa/live-installed-files.json'))
        qualified=audit.tar_data(archive,index,'som-product-runtime-qualification/qualification-evidence/installed-files.json')
    need(audit.sha(qualified)==audit.RUNTIME_INVENTORY_SHA,'runtime inventory differs')
    installer=Path('som-rs/scripts/hosted_readiness/product_python/uv099-installer-authority.json').read_bytes()
    venv=Path('/home/runner/work/_temp/som-react-python-closure/environments/react-api-qa/venv')
    python=Path('/home/runner/work/_temp/som-product-runtime-qualification/install/python/bin/python3.12')
    expected,dirs=audit.derive(source,closure,inventories,installer,venv,python)
    need(set(installed)==set(expected),'installed scope differs')
    for name,row in expected.items():
        actual=installed[name]
        need(all(type(actual.get(key)) is type(value) and actual.get(key)==value for key,value in row.items()),'installed source differs: '+name)
    print(json.dumps({'state':'source-map-matches-prior-saved-inventory','wheel_count':len(rows),
                      'file_and_link_count':len(expected),'source_derived_directories':len(dirs),
                      'installed_mode':'observed only','current_product_acceptance':False},sort_keys=True))
    return 0
if __name__=='__main__': sys.exit(main())
