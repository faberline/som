"""Independently check the approved 18-package input restore; never product acceptance."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

COMMIT='1de8f05430a72f8d76f6159786cca71b0ae1a911'
INDEX='c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
ORIGINAL_COMMIT='55eb7f5c3b21d4f3b57eb721ab60c6aa97e10db3'
ORIGINAL_INDEX='78375feb14ed286ac5df4c52f13d5d1ae301b2057d5e3894edf0f36c2705c498'
MAP='3b06627c31626238960d38ddb9e772e57c900a98a28ba6407790ca8196edd8ca'
EXPECTED={
  'package-016':('36f930a3a9ca8f38a11a7371aca4e5ad1a54cdefad8c090e74ded72c4ccadc4d','6e32fc2b1e5e2b9841ccf74d435b9d7c1c8d95471f50fcd281c1adb54a64410d',8996665,1644,33533106),
  'package-017':('4f1dbc5047ab247753950ddd282684fc55f53c1b0ba1465768d304488a135371','5150663b4c2d68c0a0f7207d5bd3872e36348c5497d6581875f6949725525c89',4974778,1030,18177247),
}
class Refused(ValueError):pass

def need(x):
    if not x:raise Refused('supplement provision proof refused')
def sha(x):return hashlib.sha256(x).hexdigest()
def obj(data):
    def unique(pairs):
        out={}
        for key,value in pairs:need(key not in out);out[key]=value
        return out
    return json.loads(data,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(Refused('nonfinite')))
def equal(a,b):
    return type(a) is type(b) and (a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a) if isinstance(a,dict)
        else len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b)) if isinstance(a,list) else a==b)
def git(repo,revision,name):
    need(bool(re.fullmatch(r'(?:inputs-index[.]json|package-[0-9]{3}[.](?:manifest[.]json|sealed))',name)))
    return subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'show',revision+':'+name],timeout=120)
def regular(root,name):
    need(type(name) is str and not name.startswith('/') and '\\' not in name and all(p not in ('','.','..') for p in name.split('/')))
    path=root
    for part in name.split('/'):
        path=path/part;need(not path.is_symlink())
    need(path.is_file());return path.read_bytes()
def verify_pair(encoded,document):
    need(encoded.endswith(b'\n') and not any(c in encoded[:-1] for c in b'\r\n \t'))
    raw=base64.b64decode(encoded[:-1],validate=True);record=obj(document)
    need(type(record['http_status']) is int and record['http_status']==200 and equal(record['body'],obj(raw)))

def verify(repo, restored, mapping_raw):
    need(sha(mapping_raw)==MAP); mapping=obj(mapping_raw)
    old=git(repo,ORIGINAL_COMMIT,'inputs-index.json');current=git(repo,COMMIT,'inputs-index.json')
    need(sha(old)==ORIGINAL_INDEX and sha(current)==INDEX)
    old_index,index=obj(old),obj(current)
    need(old_index['version']==index['version']=='som.m6-input-index.v1' and len(old_index['packages'])==16
         and len(index['packages'])==18 and index['packages'][:16]==old_index['packages'])
    expected_files={row['path']:(row['sha256'],row['bytes']) for row in mapping['remaining_files']}
    need(len(expected_files)==2674 and mapping['remaining_file_count']==2674 and mapping['remaining_bytes']==51710353)
    found={}; all_paths=set(); total=0
    for i,entry in enumerate(index['packages']):
        name=f'package-{i:03d}';need(entry['archive']==name+'.sealed' and entry['manifest']==name+'.manifest.json')
        manifest_raw=git(repo,COMMIT,entry['manifest']);need(sha(manifest_raw)==entry['manifest_sha256'])
        manifest=obj(manifest_raw);cipher=git(repo,COMMIT,entry['archive'])
        need(manifest['version']=='som.m6-sealed-inputs-manifest.v1' and manifest['cipher_sha256']==sha(cipher)
             and manifest['total_files']==len(manifest['files']))
        if i>=16:
            ms,cs,size,count,byte_count=EXPECTED[name]
            need(sha(manifest_raw)==ms and sha(cipher)==cs and len(cipher)==size
                 and len(manifest['files'])==count and sum(row['bytes'] for row in manifest['files'])==byte_count)
        for row in manifest['files']:
            rel=row['path'];need(rel not in all_paths);all_paths.add(rel)
            if i>=16:
                need(rel in expected_files and expected_files[rel]==(row['sha256'],row['bytes']))
                data=regular(restored,rel);need(len(data)==row['bytes'] and sha(data)==row['sha256'])
                found[rel]=data;total+=len(data)
    need(set(found)==set(expected_files) and total==51710353)
    bases={name[:-len('.response.json')] for name in found if name.endswith('.response.json')}
    need(len(bases)==1337 and set(found)=={b+'.response.'+suffix for b in bases for suffix in ('json','raw.b64')})
    for base in bases:
        verify_pair(found[base+'.response.raw.b64'],found[base+'.response.json'])
    receipt=obj(regular(restored,'provision.json'))
    need(receipt['version']=='som.m6-input-provision.v1' and receipt['stage']=='input_provision'
         and receipt['status']=='passed' and receipt['index_sha256']==INDEX and receipt['product_suites']=='not_run'
         and receipt['file_count']==len(all_paths))
    return {'status':'passed','input_commit':COMMIT,'index_sha256':INDEX,'packages':18,
            'supplement_files':len(found),'supplement_bytes':total,'response_pairs':len(bases),
            'original_16_index_sha256':ORIGINAL_INDEX,'product_acceptance':'not_run'}

def main():
    repo=Path(os.environ['GITHUB_WORKSPACE']).resolve();root=Path(os.environ['RUNNER_TEMP']).resolve()/'approved-inputs'
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and 'M6_INPUTS_KEYRING' not in os.environ)
    mapping=regular(repo,'som-rs/scripts/react_output_acceptance/historical-response-reuse-map.json')
    print(json.dumps(verify(repo,root,mapping),sort_keys=True))
if __name__=='__main__':main()
