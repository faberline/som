"""Bind independent diagnostic source authorities; run on a hosted runner."""
import hashlib
import os
from pathlib import Path
import subprocess

BEFORE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
CANDIDATE = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
SAME = {
    'som-rs/src/python_names.rs': '22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec',
    'som-rs/src/json_diagnostics.rs': '87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6',
    'som-rs/src/strict_json.rs': '06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161',
    'som-rs/src/main.rs': '042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5',
    'som-rs/src/lib.rs': '60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f',
    'som-rs/contracts/compiler-v1/selection-policy.json': '6fc29cf4791de4e2c6ec716a03426cf66cb1bdd0c5659c30511b837da8552f56',
}
COMPILER = {
    BEFORE: '9f131e728e2feb2b769d7150053a3949b5149a024a2cde1e38035c13c54e935f',
    CANDIDATE: '49c8309e102b1f7c686c8a975ed8b2c81d0133557c0443d8986474ca41cd9f3c',
}


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('hosted runner required')
    repo = Path(os.environ['GITHUB_WORKSPACE'])
    for rev in (BEFORE, CANDIDATE):
        for name, expected in {**SAME, 'som-rs/src/compiler.rs': COMPILER[rev]}.items():
            raw = subprocess.run(['git', 'show', f'{rev}:{name}'], cwd=repo, check=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=15).stdout
            if hashlib.sha256(raw).hexdigest() != expected:
                raise SystemExit('diagnostic source authority differs')
    print('diagnostic source authorities bound; binary provenance remains separate')


if __name__ == '__main__':
    main()
