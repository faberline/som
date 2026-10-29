"""Install source-proven derived pairs inside an ephemeral GH runner only."""
import argparse
import os
from pathlib import Path
import reconstruct_historical_responses as recon

def install(inputs,derived,raw,*,map_sha=recon.MAP_SHA,expected_pairs=668):
    inputs=inputs.resolve(strict=True);derived=derived.resolve(strict=True)
    recon.need(inputs!=derived and not inputs.is_relative_to(derived) and not derived.is_relative_to(inputs))
    recon.need(recon.sha(raw)==map_sha);mapping=recon.parse(raw);names={}
    for row in mapping['reconstructed_cases']:
        for suffix in ('raw','json'):
            path=row['historical_'+suffix+'_path'];digest=row['historical_'+suffix+'_sha256']
            recon.need(path not in names);names[path]=digest
    recon.need(len(names)==2*expected_pairs)
    verified=[]
    for name,digest in names.items():
        source=recon.safe(derived,name);target=recon.safe(inputs,name)
        recon.need(recon.sha(recon.read(source))==digest and not target.exists() and not target.is_symlink())
        recon.need(target.parent.is_dir() and not target.parent.is_symlink())
        verified.append((source,target))
    for source,target in verified:os.link(source,target,follow_symlinks=False)
    return len(verified)

def main():
    p=argparse.ArgumentParser()
    for arg in ('inputs','derived','map'):p.add_argument('--'+arg,required=True,type=Path)
    a=p.parse_args()
    recon.need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
    count=install(a.inputs,a.derived,recon.read(a.map))
    print('Installed %d later-capture derived response files; no acceptance inferred.' % count)
if __name__=='__main__':main()
