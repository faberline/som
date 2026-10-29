"""Check draft source pins on GitHub; this is not acceptance or a seal."""
import hashlib
import json
from pathlib import Path
import sys

repo=Path.cwd()
api=json.loads((repo/'som-rs/contracts/react-output-v1/linux-api-qa-authority.json').read_bytes())
qa=json.loads((repo/'som-rs/contracts/react-output-v1/linux-qa-audit-authority.json').read_bytes())
for authority in (api,qa):
    for path,pin in authority['source_sha256'].items():
        raw=(repo/path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pin:
            print('source mismatch: '+path,file=sys.stderr)
            raise SystemExit(1)
for filename,key,authority in (
    ('som-react-linux-api-profile.json','api_profile_sha256',api),
    ('som-react-linux-qa-profile.json','node_profile_sha256',api),
    ('som-react-linux-qa-profile.json','profile_sha256',qa)):
    raw=(repo/'som-rs/contracts/react-output-v1'/filename).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=authority[key]:
        raise SystemExit('profile mismatch: '+filename)
print(json.dumps({'state':'draft-authority-bytes-consistent','api_paths':len(api['source_sha256']),
                  'qa_paths':len(qa['source_sha256']),'product_acceptance_passed':False}))
