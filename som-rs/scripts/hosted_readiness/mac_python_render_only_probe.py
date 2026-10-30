"""A bounded real Mac build/process/raw-response diagnostic.

Only run_probe is public. Its result is data, never a native admission object.
The selected one-case diagnostic does not qualify the full product or Mac Context.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import traceback
from types import MappingProxyType

_HERE = Path(__file__).resolve().parent
_GIT = '/usr/bin/git'
_RUST = Path('/opt/homebrew/Cellar/rust/1.96.1/bin')
_BEFORE = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
_BASE = '49393e8ce2e1551472eb0158495f858f683c9d68'
_BEFORE_BINARY = '2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
_RUFF = 'cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a'
_PROFILE = '4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4'
_INPUT_COMMIT = '55eb7f5c3b21d4f3b57eb721ab60c6aa97e10db3'
_INPUT_INDEX = '78375feb14ed286ac5df4c52f13d5d1ae301b2057d5e3894edf0f36c2705c498'
_MAX_BODY = 16 * 1024 * 1024
_MAX_FILE = 128 * 1024 * 1024
_ASSETS = ('blocks.json', 'legacy-blocks.json', 'quality-blocks.json',
           'quality.json', 'ruff', 'pyproject.toml', 'manifest.json')
_ASSET_SOURCE = {
    'blocks.json': 'som-code-python/data/blocks/flow-v7-compact-self/blocks.json',
    'legacy-blocks.json': 'som-code-python/data/blocks/v5/blocks.json',
    'quality-blocks.json': 'som-code-python/data/blocks/composed-quality/blocks.json',
    'pyproject.toml': 'som-rs/contracts/python-quality-v1/pyproject.toml',
}
_ROOTS = ('som-rs/Cargo.toml', 'som-rs/Cargo.lock', 'som-rs/src', 'som-rs/contracts',
          'som-rs/som-targets/Cargo.toml', 'som-rs/som-targets/Cargo.lock',
          'som-rs/som-targets/src', 'som-rs/som-targets/catalog')
_MODULE_PINS = {
    'python_backend_source_data': '144c662a236b51e953827de8a0ca4ea4078c2eab57d2d5d9c037bc5d572a961f',
    'raw_digest_compat': 'a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896',
    'python_raw_response_compat': '5d0a69ab90acc46475944851aa81e11110458345a7fe4a87228cf157d12adc30',
}
# Filled from the frozen selected plan and raw57 assembly as literal source data.
_FIXED_SOURCE_PINS = {'som-rs/contracts/compiler-v1/acceptance-plan.json': {'bytes': 6670,
                                                       'sha256': 'fe21a5e91b393c55324f7c4b7fd47d83cdfeab7455f1251390bb07cfe479b324'},
 'som-rs/contracts/react-output-v1/acceptance-plan.json': {'bytes': 14222,
                                                           'sha256': 'c3145cdc4528357dba8faa9e80ce2687204eef7c2812581366ab34d7df33792f'},
 'som-rs/contracts/react-output-v1/toolchain.json': {'bytes': 69021,
                                                     'sha256': '49ad8ccd194b110e10a8141ecdfe2dafc7f649ff085ffb709bb06a7df7269bc2'},
 'som-rs/contracts/rust-output-v1/acceptance-plan.json': {'bytes': 23196,
                                                          'sha256': 'bea96ef0e19eb5d2e9923cba5e8a41d05df2ac95e0d55baf2d080a5f9f76a83d'},
 'som-rs/contracts/rust-output-v1/toolchain.json': {'bytes': 8471,
                                                    'sha256': '9a05dd1953defc89c4a44eada333cbfc41570e4728295ab1a2be060c76b7029d'},
 'som-rs/contracts/typescript-output-v1/acceptance-plan.json': {'bytes': 12471,
                                                                'sha256': '17478469d02bf362e6d394ef9081ff4e842eeab239fbfc89550fba92951d35f5'},
 'som-rs/contracts/typescript-output-v1/toolchain.json': {'bytes': 20930,
                                                          'sha256': '0875aeb7e60aa72a44bc59c9aef89b971dffc5fc5304813e0b04ec5dc26da618'},
 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json': {'bytes': 13159,
                                                                       'sha256': 'f8e649ea96bac3f5e62ef01537c4c517a857e0743d05fb38028d013faf08b640'},
 'som-rs/scripts/react_output_acceptance/fetch_hosted_inputs.py': {'bytes': 15371,
                                                                   'sha256': '916c013815a467c71f9bf922104dd33cb7a0fd7e2ee30568921751b21dbbb03a'},
 'som-rs/scripts/react_output_acceptance/hosted_inputs.json': {'bytes': 185,
                                                               'sha256': '16da55005fbfc1296cddcd5640b3f90f7b9cdc17811d0d0e16cf627f8bf2b051'},
 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt': {'bytes': 862,
                                                                           'sha256': '217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35'},
 'som-rs/scripts/react_output_acceptance/macos_bottles.json': {'bytes': 79486,
                                                               'sha256': 'b4d6664c5ae890c23436b18cbdfba86ba434eb98e76bf534bf59c3693a3cf750'},
 'som-rs/scripts/react_output_acceptance/provision_macos.py': {'bytes': 79207,
                                                               'sha256': 'e9aca95daca5e21b231cea1ab3c67be64b4226458d755580412f9a807638a4b1'},
 'som-rs/scripts/react_output_acceptance/sealed_inputs.mjs': {'bytes': 14578,
                                                              'sha256': 'bda649080df1fced5ac6cfabdc300d9d9b1b3d9a53a1240c11fdce80e0b1e9c9'},
 'som-rs/scripts/rust_output_acceptance/prepare_candidate.py': {'bytes': 13268,
                                                                'sha256': '6cff0032291805dfec367b8cd4817876d1c9b3e0c25494487543bb8af82c159e'}}
_BASE63_PINS = {'.github/workflows/python-raw-response-data57.yml': {'bytes': 11461,
                                                      'sha256': '8920bd2c9c7a3209757d2105bb3f109d1875a97e045eedf2b8a27363f46aadb4'},
 'som-code-python/data/block-contracts/composed-quality.json': {'bytes': 66883,
                                                                'sha256': '28f9bd134f31eb87fae5a680148447de3aaca9bad78fc10f0ac22b00cc40b0e5'},
 'som-code-python/data/block-contracts/composed.json': {'bytes': 66836,
                                                        'sha256': '3e58ed5b162cc4297324cc6da15ad9838c2a136d8a91762f63ac8d0c3af0b6b4'},
 'som-code-python/data/block-contracts/flow-v7-compact.json': {'bytes': 105217,
                                                               'sha256': 'a2b96804e6f1c52a2ebf9e6310c6624b8b1b33d86b4f4763f07ffbeb645e103a'},
 'som-code-python/data/block-contracts/flow-v7.json': {'bytes': 112651,
                                                       'sha256': 'd0ffbe92abbf8666e5e562022617e6eb81f4ef1c9fcc73b92fca8c718bba3f47'},
 'som-core/block_templates/flow_compact/engine.py.tpl': {'bytes': 9930,
                                                         'sha256': '76923c4752bccd850b5fd55ebb77904aa3988170927ba22ee4acef8bbc556d5f'},
 'som-core/block_templates/flow_compact/forward_support.py.tpl': {'bytes': 2449,
                                                                  'sha256': '45250ff33586c4765733ccd69b8ca1bd52cb34893880bd89fe78d703dc29d557'},
 'som-rs/Cargo.lock': {'bytes': 77355,
                       'sha256': 'c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c'},
 'som-rs/Cargo.toml': {'bytes': 1179,
                       'sha256': '6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1'},
 'som-rs/contracts/compiler-v1/canonical-vectors.json': {'bytes': 2222,
                                                         'sha256': 'dd09b41f49702f70098574f638e10ea33646366a77dfa486539f324d6f9ff83f'},
 'som-rs/contracts/native-tools-v1/CONTRACT.md': {'bytes': 3705,
                                                  'sha256': '47d72cabd57bdb380171a54f4dddeeabf25693c3781cabb483c572c162efac6e'},
 'som-rs/contracts/native-tools-v1/artifacts.json': {'bytes': 29314,
                                                     'sha256': '959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193'},
 'som-rs/contracts/python-literal-encoding.v1.json': {'bytes': 18039,
                                                      'sha256': '7c25a1f45e54cec45e4053a5ceaf767188641ddde7da34dfce0747dc299dbf16'},
 'som-rs/contracts/python-quality-v1/pyproject.toml': {'bytes': 880,
                                                       'sha256': '4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4'},
 'som-rs/contracts/related-business.v1.md': {'bytes': 8591,
                                             'sha256': '9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380'},
 'som-rs/contracts/related-flow.v1.md': {'bytes': 5143,
                                         'sha256': '716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/DATA_PROOF.json': {'bytes': 6728,
                                                                                   'sha256': '362e6b6de7dde2be3f88af08a9e541abc397a1b2a14d62e62601f7870715cab5'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/assemble/request.raw': {'bytes': 4021,
                                                                                                       'sha256': 'd5ca56f0e57a1a1e8d9fea4d66b8f4dbc630d4fd78243657aecb77d901deb7ba'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/assemble/response.raw': {'bytes': 5156,
                                                                                                        'sha256': '2d35cf5c6c8f870eaa727f4adc807087d1f2ff9e0d4c865e880d0fc24c00259b'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/assemble/transport.json': {'bytes': 469,
                                                                                                          'sha256': '56108b5ebfc28124a4abbd1c448f8ebf8db5d08101b0b879a93bb253fab0e353'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/compile/request.raw': {'bytes': 600,
                                                                                                      'sha256': '154de99f48bf44c6054258a21e0cdbbaacf8794971d2950ea44ca0461a58824e'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/compile/response.raw': {'bytes': 4035,
                                                                                                       'sha256': '85a2fa60890628b6972490103fa1d598de7bf824f9a59e2e8d62404d83a417a3'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/before/compile/transport.json': {'bytes': 468,
                                                                                                         'sha256': '151c5a986e0268bf619504aa22018aee03f214c1df1f8d1b64bf0308d251f956'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/assemble/request.raw': {'bytes': 4021,
                                                                                                          'sha256': '6930742b07a28dcc6c283251d9b97ac7eab6701b8aca84c1397902ce4c1957c2'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/assemble/response.raw': {'bytes': 5156,
                                                                                                           'sha256': '2d35cf5c6c8f870eaa727f4adc807087d1f2ff9e0d4c865e880d0fc24c00259b'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/assemble/transport.json': {'bytes': 469,
                                                                                                             'sha256': '45e4c41bd15bdf9f13c676aa3aff19c6f87ff55e38cb32947a8019024ffb3174'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/compile/request.raw': {'bytes': 600,
                                                                                                         'sha256': '154de99f48bf44c6054258a21e0cdbbaacf8794971d2950ea44ca0461a58824e'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/compile/response.raw': {'bytes': 4035,
                                                                                                          'sha256': 'f1d9e2ab46dd1aba0f536a90101b080bff5dd08dfedabb9cb029467d56bc82f2'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/omitted/candidate/compile/transport.json': {'bytes': 468,
                                                                                                            'sha256': '323eb3b2472e405bc61595811ce51c44224ba7da4407ef24ec948c08b3f7c304'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/proposal-manifest.json': {'bytes': 4166,
                                                                                          'sha256': 'a72505622f3bb6335a0d4c60e1a0c4e1185c01ddceee0738d83cbc2db625e8f8'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/assemble/request.raw': {'bytes': 4021,
                                                                                                      'sha256': 'd5ca56f0e57a1a1e8d9fea4d66b8f4dbc630d4fd78243657aecb77d901deb7ba'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/assemble/response.raw': {'bytes': 5156,
                                                                                                       'sha256': '2d35cf5c6c8f870eaa727f4adc807087d1f2ff9e0d4c865e880d0fc24c00259b'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/assemble/transport.json': {'bytes': 469,
                                                                                                         'sha256': '56108b5ebfc28124a4abbd1c448f8ebf8db5d08101b0b879a93bb253fab0e353'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/compile/request.raw': {'bytes': 618,
                                                                                                     'sha256': 'e3cee4ca7e97c1161482aff5b7d0a41838aade8c88da0ffce3675679a6120e40'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/compile/response.raw': {'bytes': 4035,
                                                                                                      'sha256': '85a2fa60890628b6972490103fa1d598de7bf824f9a59e2e8d62404d83a417a3'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/before/compile/transport.json': {'bytes': 468,
                                                                                                        'sha256': '151c5a986e0268bf619504aa22018aee03f214c1df1f8d1b64bf0308d251f956'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/assemble/request.raw': {'bytes': 4021,
                                                                                                         'sha256': '6930742b07a28dcc6c283251d9b97ac7eab6701b8aca84c1397902ce4c1957c2'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/assemble/response.raw': {'bytes': 5156,
                                                                                                          'sha256': '2d35cf5c6c8f870eaa727f4adc807087d1f2ff9e0d4c865e880d0fc24c00259b'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/assemble/transport.json': {'bytes': 469,
                                                                                                            'sha256': '45e4c41bd15bdf9f13c676aa3aff19c6f87ff55e38cb32947a8019024ffb3174'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/compile/request.raw': {'bytes': 618,
                                                                                                        'sha256': 'e3cee4ca7e97c1161482aff5b7d0a41838aade8c88da0ffce3675679a6120e40'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/compile/response.raw': {'bytes': 4035,
                                                                                                         'sha256': 'f1d9e2ab46dd1aba0f536a90101b080bff5dd08dfedabb9cb029467d56bc82f2'},
 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1/python/candidate/compile/transport.json': {'bytes': 468,
                                                                                                           'sha256': '323eb3b2472e405bc61595811ce51c44224ba7da4407ef24ec948c08b3f7c304'},
 'som-rs/scripts/hosted_readiness/python_backend_source_data.py': {'bytes': 14408,
                                                                   'sha256': '144c662a236b51e953827de8a0ca4ea4078c2eab57d2d5d9c037bc5d572a961f'},
 'som-rs/scripts/hosted_readiness/python_raw_response_compat.py': {'bytes': 19986,
                                                                   'sha256': '5d0a69ab90acc46475944851aa81e11110458345a7fe4a87228cf157d12adc30'},
 'som-rs/scripts/hosted_readiness/raw_digest_compat.py': {'bytes': 8573,
                                                          'sha256': 'a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896'},
 'som-rs/scripts/hosted_readiness/test_python_backend_source_data.py': {'bytes': 15635,
                                                                        'sha256': 'e5048f4b2eda06ff18173138947ff6abef1f72460d9ebd257b0e65aee991a488'},
 'som-rs/scripts/hosted_readiness/test_python_raw_response_compat.py': {'bytes': 19962,
                                                                        'sha256': '99d2dae894c679477f86672fe3912cfd2574c04cc08bb0b19630f6f5cfec45f2'},
 'som-rs/scripts/hosted_readiness/test_raw_digest_compat.py': {'bytes': 7868,
                                                               'sha256': '99d425e7a7392534ee8635bdad2ed33c5ae60afbff4aa799761b47b3a09e8d37'},
 'som-rs/src/business.rs': {'bytes': 2296,
                            'sha256': 'b880c325a02c9a93a9e1470baa0416db54560b986511a71561583c1468c9df2f'},
 'som-rs/src/cnl.rs': {'bytes': 62689,
                       'sha256': '8f14a9c5897b335066a619be907244f38f1ad3947d12ed7e1833fa7e77098789'},
 'som-rs/src/cnl_semantics.rs': {'bytes': 44869,
                                 'sha256': '4d4c6e706fbf83bc86ef5ae7f11cbdc884ba7a9d43ba7c03b7f7a5851311d834'},
 'som-rs/src/compiler.rs': {'bytes': 23937,
                            'sha256': '49c8309e102b1f7c686c8a975ed8b2c81d0133557c0443d8986474ca41cd9f3c'},
 'som-rs/src/composed.rs': {'bytes': 13301,
                            'sha256': '571c04a8e50f9856361dce94ebab813d8a8905aee9e2ea12dbd687f4f32d890c'},
 'som-rs/src/flow.rs': {'bytes': 31750,
                        'sha256': 'fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75'},
 'som-rs/src/flow_compact.rs': {'bytes': 42051,
                                'sha256': 'd4543446c2b5cd99fb020bbddb8f15ed3f1faecfc3ca8c08fb98249649687bb4'},
 'som-rs/src/json_diagnostics.rs': {'bytes': 39875,
                                    'sha256': '87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6'},
 'som-rs/src/lib.rs': {'bytes': 39990,
                       'sha256': '60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f'},
 'som-rs/src/main.rs': {'bytes': 13876,
                        'sha256': '042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5'},
 'som-rs/src/planner.rs': {'bytes': 32379,
                           'sha256': '7958474d52f4e9b9f4fd1afc16e328428e32acc77dcbabd2ca3814f22610f12d'},
 'som-rs/src/python_names.rs': {'bytes': 13110,
                                'sha256': '22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec'},
 'som-rs/src/quality.rs': {'bytes': 18181,
                           'sha256': '4522527da6a96bfee72365ab0b130c816022194fdff3de386cf604bb7c8e713b'},
 'som-rs/src/related.rs': {'bytes': 9792,
                           'sha256': 'ccc55e6018117a7087685de81da420da911832edaae51532e712be7b1b138dca'},
 'som-rs/src/strict_json.rs': {'bytes': 10417,
                               'sha256': '06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161'}}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _plain(value):
    if type(value) in (dict, MappingProxyType):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_plain(item) for item in value]
    return value


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) in (list, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _json(raw):
    def pairs(rows):
        out = {}
        for key, value in rows:
            _need(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _compact(value):
    return json.dumps(_plain(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _write(path, value):
    raw = value if type(value) is bytes else json.dumps(_plain(value), ensure_ascii=False,
                                                     indent=2, allow_nan=False).encode() + b'\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def _physical(path, *, exists=True):
    _need(type(path) is type(Path()) and path.is_absolute(), 'exact absolute Path required')
    _need(str(path) == os.path.normpath(str(path)), 'normalized path required')
    for part in (path, *path.parents):
        _need(not part.is_symlink(), 'physical path must not contain a symbolic link')
    if exists:
        _need(path.exists(), 'required physical path is missing')
    return path


def _read(path, limit=_MAX_FILE):
    _physical(path)
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'bounded regular file required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    end = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    _need(identity(before) == identity(opened) == identity(after) == identity(end)
          and len(raw) == before.st_size, 'file changed during its complete read')
    return raw


def _file_sha(path):
    """Hash an installed native file with bounded memory and stable identity."""
    _physical(path)
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode) and before.st_size <= 1024 * 1024 * 1024,
          'bounded regular installed file required')
    digest, total = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        while True:
            raw = stream.read(1024 * 1024)
            if not raw:
                break
            total += len(raw)
            _need(total <= 1024 * 1024 * 1024, 'installed file grew beyond bound')
            digest.update(raw)
        after = os.fstat(stream.fileno())
    end = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
    _need(identity(before) == identity(opened) == identity(after) == identity(end)
          and total == before.st_size, 'installed file changed during its complete hash')
    return digest.hexdigest()


def _argv(argv):
    _need(type(argv) is list and argv and all(type(item) is str and item
          and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in item)
          for item in argv), 'restricted nonempty argv required')
    return argv


def _environment(repository):
    keys = ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'CI', 'SDKROOT',
            'DEVELOPER_DIR', 'CARGO_HOME', 'RUSTUP_HOME', 'GITHUB_ACTIONS',
            'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_ARCH', 'RUNNER_TEMP')
    result = {name: os.environ[name] for name in keys if name in os.environ}
    result.update(CARGO_NET_OFFLINE='true', PYTHONDONTWRITEBYTECODE='1', LC_ALL='C', LANG='C',
                  SOM_TS_PRETTIER=str(repository / '.local/m6-hosted/prettier'))
    return result


def _git(repository, *args, limit=64 * 1024 * 1024):
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    done = subprocess.run([_GIT, '--no-replace-objects', '-C', str(repository), *args],
                          env=env, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=60, check=False)
    _need(done.returncode == 0 and len(done.stdout) <= limit and len(done.stderr) <= 65536,
          'fixed Git operation failed or exceeded its output bound')
    return done.stdout


def _head(repository):
    raw = _git(repository, 'rev-parse', 'HEAD', limit=128)
    _need(re.fullmatch(rb'[0-9a-f]{40}\n', raw), 'full selected HEAD required')
    return raw.decode().strip()


def _runner(repository, frozen_before_root, output_root):
    _need(all(type(path) is type(Path()) for path in (repository, frozen_before_root, output_root)),
          'three exact Path arguments required before any comparison')
    for path in (repository, frozen_before_root, output_root):
        _physical(path, exists=path != output_root)
        _argv([str(path)])
    _need(os.environ.get('GITHUB_ACTIONS') == 'true'
          and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
          and os.environ.get('RUNNER_OS') == 'macOS' and os.environ.get('RUNNER_ARCH') == 'ARM64'
          and platform.system() == 'Darwin' and platform.machine() == 'arm64'
          and platform.mac_ver()[0].split('.')[0] == '26', 'GitHub hosted arm64 Mac26 required')
    _need(sys.version_info[:3] == (3, 12, 11), 'actual frozen Mac Python3.12.11 required')
    python = Path('/opt/homebrew/opt/python@3.12/bin/python3.12').resolve(strict=True)
    _need(Path(sys.executable).resolve(strict=True) == python, 'fixed Python executable required')
    _need(_git(repository, 'rev-parse', '--show-toplevel').decode().rstrip('\n') == str(repository)
          and repository == _HERE.parents[2], 'selected physical repository root differs')
    _need(_head(repository) == os.environ.get('GITHUB_SHA'), 'actual checkout and GitHub SHA differ')
    _need(_git(repository, 'merge-base', '--is-ancestor', _BASE, 'HEAD') == b'',
          'qualified raw57 base is not an ancestor')
    _need(frozen_before_root == repository / '.local/m6-hosted/baseline', 'fixed before root differs')
    temporary = _physical(Path(os.environ['RUNNER_TEMP']))
    _need(output_root != temporary and output_root.is_relative_to(temporary)
          and not output_root.exists() and not output_root.is_symlink(), 'new runner temporary output required')
    return {'head': _head(repository), 'python': str(python), 'python_sha256': _sha(_read(python)),
            'version': sys.version, 'platform': platform.platform(), 'run_id': os.environ.get('GITHUB_RUN_ID'),
            'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'source_authority': False}


def _checkout_identity(repository):
    index_name = _git(repository, 'rev-parse', '--git-path', 'index').decode('utf-8', 'strict').rstrip('\n')
    index = Path(index_name)
    if not index.is_absolute():
        index = repository / index
    return {'head': _head(repository), 'index_sha256': _sha(_read(index)),
            'head_reference': _git(repository, 'rev-parse', '--symbolic-full-name', 'HEAD').decode().rstrip('\n')}


@contextlib.contextmanager
def _qualified_modules(repository):
    loaded = []
    try:
        for name, expected in _MODULE_PINS.items():
            _need(name not in sys.modules, 'qualified helper name was already loaded')
            path = repository / 'som-rs/scripts/hosted_readiness' / (name + '.py')
            _need(_sha(_read(path)) == expected, 'qualified helper bytes differ')
            spec = importlib.util.spec_from_file_location(name, path)
            _need(spec is not None and spec.loader is not None, 'fixed normal source spec required')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            loaded.append((name, module))
            spec.loader.exec_module(module)
        yield tuple(module for _, module in loaded)
    finally:
        for name, module in reversed(loaded):
            if sys.modules.get(name) is module:
                del sys.modules[name]


def _snapshot(repository, reader, names):
    revision = _head(repository)
    rows = {}
    for name in sorted(set(names)):
        raw, row = reader._git_blob(repository, revision, name)
        path = repository / name
        _need(_read(path) == raw and stat.S_IMODE(path.lstat().st_mode)
              == (0o755 if row['mode'] == '100755' else 0o644), 'physical selected Git blob/mode differs')
        rows[name] = dict(row)
    return {'head': revision, 'status': _git(repository, 'status', '--porcelain=v1',
            '--untracked-files=all', '--', *sorted(rows)).decode('utf-8', 'strict'), 'files': rows}


def _source_names(repository):
    raw = _git(repository, 'ls-files', '-z', '--', *_ROOTS)
    names = set(raw.decode('utf-8', 'strict').rstrip('\0').split('\0'))
    names.update(_BASE63_PINS)
    names.update(_FIXED_SOURCE_PINS)
    names.update(_ASSET_SOURCE.values())
    names.update(('som-rs/src/compiler.rs', 'som-rs/src/quality.rs',
                  'som-rs/scripts/hosted_readiness/mac_python_render_only_probe.py',
                  'som-rs/scripts/hosted_readiness/test_mac_python_render_only_probe.py',
                  '.github/workflows/mac-python-render-only-probe.yml',
                  'som-rs/scripts/react_output_acceptance/provision_macos.py',
                  'som-rs/scripts/react_output_acceptance/fetch_hosted_inputs.py',
                  'som-rs/scripts/react_output_acceptance/sealed_inputs.mjs'))
    _need('' not in names, 'empty source inventory refused')
    return names


def _bind_probe_source(repository, reader, compat):
    for name, pin in {**_BASE63_PINS, **_FIXED_SOURCE_PINS}.items():
        raw = _read(repository / name)
        _need(len(raw) == pin['bytes'] and _sha(raw) == pin['sha256'], 'frozen source dependency differs')
    sources = compat._derive_sources(repository)
    vectors = compat._qualify_canonical(repository, sources)
    original = _snapshot(repository, reader, _source_names(repository))
    _need(original['status'] == '', 'selected source inventory has local changes')
    return sources, vectors, original


def _tree(root):
    digest, files, links = hashlib.sha256(), 0, 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f'{relative}\0symlink:{os.readlink(path)}\n'.encode())
            links += 1
        elif path.is_file():
            digest.update(f'{relative}\0{_file_sha(path)}\n'.encode())
            files += 1
        else:
            _need(path.is_dir(), 'tool tree contains a special file')
    return [digest.hexdigest(), files, links]


def _tool_pins(contracts):
    pins = {}
    def add(path, expected):
        _need(type(path) is str and Path(path).is_relative_to('/opt/homebrew')
              and '..' not in Path(path).parts and re.fullmatch('[0-9a-f]{64}', expected),
              'fixed tool path/hash differs')
        _need(path not in pins or pins[path] == expected, 'tool contracts disagree')
        pins[path] = expected
    for document in contracts.values():
        if 'rust' in document:
            rust = document['rust']
            for name in ('rustc', 'cargo', 'rustfmt', 'cargo-fmt', 'cargo-clippy', 'clippy-driver'):
                add(str(Path(rust['bin_dir']) / name), rust[name]['sha256'])
        if 'node' in document:
            node = document['node']
            add(node['binary'], node['sha256'])
            add(node['libnode']['path'], node['libnode']['sha256'])
            for link, item in node['homebrew_libraries'].items():
                add(link, item['sha256'])
                add(item['resolved'], item['sha256'])
        if 'postgresql' in document:
            pg = document['postgresql']
            for name in ('postgres', 'initdb', 'pg_ctl'):
                add(str(Path(pg['bin_dir']) / name), pg[name]['sha256'])
    return pins


def _verify_frozen_tools_and_before(repository, before, reader, sources, compat, out):
    contract_names = ('rust-output-v1', 'react-output-v1', 'typescript-output-v1')
    contracts = {name: _json(_read(repository / f'som-rs/contracts/{name}/toolchain.json'))
                 for name in contract_names}
    pins = _tool_pins(contracts)
    checks = []
    for name, expected in pins.items():
        path = Path(name).resolve(strict=True)
        actual = _file_sha(path)
        _need(actual == expected, 'installed frozen tool/dynamic library bytes differ')
        checks.append({'path': name, 'resolved': str(path), 'sha256': actual})
    for document in contracts.values():
        if 'npm' in document:
            item = document['npm']
            _need(_tree(Path(item['root'])) == [item['tree_sha256'], item['tree_files'], item['tree_symlinks']],
                  'full installed npm tree differs')
    prettier = repository / '.local/m6-hosted/prettier'
    _need(_tree(prettier) == ['f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299', 56, 0],
          'full approved Prettier tree differs')
    bottles_raw = _read(repository / 'som-rs/scripts/react_output_acceptance/macos_bottles.json')
    bottles = _json(bottles_raw)
    provision_raw = _read(Path(os.environ['RUNNER_TEMP']) / 'm6-provision/report.json')
    provision = _json(provision_raw)
    _need(provision['version'] == 'som.react-output-macos-provision.v1'
          and provision['status'] == 'passed' and provision['stage'] == 'complete'
          and provision['manifest_sha256'] == _sha(bottles_raw)
          and provision['python']['status'] == 'passed', 'unchanged real full tool provision did not pass')
    _need(provision['contract_sha256'] == {
        f'som-rs/contracts/{name}/toolchain.json':
            _FIXED_SOURCE_PINS[f'som-rs/contracts/{name}/toolchain.json']['sha256']
        for name in contract_names}, 'same-source provision contract binding differs')
    packages = provision['packages']
    _need(len(packages) == len(bottles['bottles']) == 33, 'full33 provision package inventory differs')
    expected_packages = {row['formula']: row for row in bottles['bottles']}
    _need(len({row['formula'] for row in packages}) == 33, 'duplicate provision package')
    for row in packages:
        source = expected_packages[row['formula']]
        variants = [source, *({**source, **item} for item in source.get('alternatives', []))]
        _need(row['status'] == 'passed' and row['version'] == source['version']
              and row['revision'] == source['revision']
              and row['expected_keg_directory'] == source['expected_keg_directory']
              and any(row['selected_archive_sha256'] == item['archive_sha256']
                      and row['selected_target'] == item['target']
                      and row['selected_bottle_rebuild'] == item['bottle_rebuild'] for item in variants),
              'provision package selection differs from immutable bottle closure')
    report_pins = {row['path']: row for row in provision['hash_checks'] if 'expected_sha256' in row}
    _need(set(report_pins) == set(pins) and all(row['status'] == 'passed'
          and row['actual_sha256'] == row['expected_sha256'] == pins[name]
          for name, row in report_pins.items()), 'provision installed file checks differ')
    index_raw, _ = reader._git_blob(repository, _INPUT_COMMIT, 'inputs-index.json')
    _need(_sha(index_raw) == _INPUT_INDEX, 'approved complete input index differs')
    index = _json(index_raw)
    _need(index['version'] == 'som.m6-input-index.v1', 'approved input index version differs')
    input_rows = {}
    for package in index['packages']:
        raw, _ = reader._git_blob(repository, _INPUT_COMMIT, package['manifest'])
        _need(_sha(raw) == package['manifest_sha256'], 'approved input manifest differs')
        manifest = _json(raw)
        _need(manifest['version'] == 'som.m6-sealed-inputs-manifest.v1'
              and len(manifest['files']) == manifest['total_files'], 'approved complete input manifest differs')
        for row in manifest['files']:
            name = row['path']
            _need(name not in input_rows and name.startswith('.local/') and '..' not in Path(name).parts,
                  'approved input path differs or repeats')
            input_rows[name] = row
    # Validate the full restored set, including data not exercised by this one case.
    for name, row in input_rows.items():
        path = repository / name
        raw = _read(path)
        _need(len(raw) == row['bytes'] and _sha(raw) == row['sha256']
              and stat.S_IMODE(path.lstat().st_mode) in (row['mode'], row['mode'] & ~0o222),
              'approved restored input bytes/mode differ')
    _need(_sha(_read(before / 'som')) == _BEFORE_BINARY and os.access(before / 'som', os.X_OK),
          'accepted before binary differs')
    receipt = _read(repository / 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json')
    _need(_sha(receipt) == 'f8e649ea96bac3f5e62ef01537c4c517a857e0743d05fb38028d013faf08b640'
          and _BEFORE.encode() in receipt and _BEFORE_BINARY.encode() in receipt,
          'accepted immutable before build receipt differs')
    expected = _expected_assets(repository, reader, sources[0], compat)
    assets = _verify_assets(before / 'assets', expected)
    record = {'installed_files': checks, 'provision_report_sha256': _sha(provision_raw),
              'provision_report': provision, 'input_commit': _INPUT_COMMIT, 'input_index_sha256': _INPUT_INDEX,
              'input_files_verified': len(input_rows), 'before_binary_sha256': _BEFORE_BINARY,
              'before_assets': assets, 'new_privacy_or_encryption_requirement': False}
    _write(out / 'tools-and-before.json', record)
    return record


def _expected_assets(repository, reader, source, compat):
    files = {}
    for name, relative in _ASSET_SOURCE.items():
        raw, _ = reader._git_blob(repository, source['revision'], relative)
        files[name] = raw
    _need(_sha(files['pyproject.toml']) == _PROFILE, 'source quality profile differs')
    # Check the reviewed declarations in each owning source, not a supplied manifest.
    quality, _ = reader._git_blob(repository, source['revision'], 'som-rs/src/quality.rs')
    compiler, _ = reader._git_blob(repository, source['revision'], 'som-rs/src/compiler.rs')
    _need(b'som.python-quality.v1' in quality and b'0.16.6' in quality and _RUFF.encode() in compiler,
          'reviewed native quality/compiler declarations differ')
    config = {'id': 'som.python-quality.v1', 'formatter_version': 'ruff 0.16.6',
              'formatter_sha256': _RUFF, 'profile_sha256': _PROFILE}
    files['quality.json'] = compat.canonical(config).encode() + b'\n'
    hashes = {name: _sha(raw) for name, raw in files.items()}
    hashes['ruff'] = _RUFF
    manifest = {'version': 'som.compiler-assets.v1', 'purpose': 'som.rust-compiler.v1',
                'backend': _json(source['manifest_bytes']), 'files': hashes}
    files['manifest.json'] = compat.canonical(manifest).encode() + b'\n'
    return files


def _verify_assets(root, expected):
    _physical(root)
    _need(root.is_dir() and {path.name for path in root.iterdir()} == set(_ASSETS),
          'complete seven-file regular asset set differs')
    result = {}
    for name in _ASSETS:
        path = root / name
        raw = _read(path)
        _need((_sha(raw) == _RUFF and os.access(path, os.X_OK)) if name == 'ruff'
              else raw == expected[name], 'source-derived asset bytes differ')
        result[name] = {'bytes': len(raw), 'sha256': _sha(raw), 'mode': stat.S_IMODE(path.lstat().st_mode)}
    return result


def _build_command(command, cwd, env, out, index):
    _argv(command)
    started = time.monotonic()
    argv = ['/usr/bin/time', '-l', *command]
    record = {'argv': command, 'timed_argv': argv, 'cwd': str(cwd), 'exit_code': None,
              'deadline_seconds': 1800, 'native_ledger': [], 'cleanup': None}
    owner, primary, logs = None, None, []
    try:
        logs.append((out / f'build-{index}.stdout').open('xb'))
        logs.append((out / f'build-{index}.stderr').open('xb'))
        record['time_executable_sha256'] = _file_sha(Path('/usr/bin/time'))
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=logs[0], stderr=logs[1], shell=False,
                                   close_fds=True, start_new_session=True)
        owner = _BuildOwned(process, argv, cwd, record['native_ledger'], logs,
                            executable_sha256=record['time_executable_sha256'])
        record['initial_pid'] = owner.pid
        _wait_build_until(owner, time.monotonic() + 1800, command)
        record['exit_code'] = owner.terminal[2]
        _need(record['exit_code'] == 0, 'native build/package/test command failed')
    except BaseException as error:
        primary = error
        record['error'] = ''.join(traceback.format_exception(error))
        raise
    finally:
        secondary = []
        if owner is not None:
            try:
                record['cleanup'] = _close_build_owned(owner)
            except BaseException as error:
                secondary.append(error)
                record['cleanup'] = {'pid': owner.pid, 'terminal': owner.terminal,
                                     'absence_verified': False,
                                     'error': ''.join(traceback.format_exception(error))}
        else:
            record['cleanup'] = {'pid': None, 'terminal': None, 'absence_verified': False,
                                 'reason': 'No validated captured build owner; no signal authority inferred.'}
            for stream in logs:
                try:
                    stream.close()
                except BaseException as error:
                    secondary.append(error)
        record['seconds'] = time.monotonic() - started
        record['secondary_errors'] = [''.join(traceback.format_exception(error)) for error in secondary]
        try:
            _write(out / f'build-{index}.json', record)
        except BaseException as error:
            secondary.append(error)
        if primary is not None:
            for error in secondary:
                primary.add_note('Secondary build cleanup/evidence error: '
                                 + ''.join(traceback.format_exception(error)))
        elif secondary:
            first = secondary[0]
            for error in secondary[1:]:
                first.add_note('Additional build cleanup/evidence error: '
                               + ''.join(traceback.format_exception(error)))
            raise first
    return record


class _BuildOwned:
    """Private transient command state for the four fixed timed build commands."""
    __slots__ = ('process', 'pid', 'creator', 'argv', 'root', 'executable', 'binary_sha256',
                 'start', 'terminal', 'known', 'ledger', 'logs', 'closed')

    def __init__(self, process, argv, cwd, ledger, logs, *, executable_sha256):
        pid = process.pid
        _need(type(pid) is int and pid > 1, 'initial build PID must be exact int')
        self.process, self.pid, self.creator = process, pid, os.getpid()
        self.argv, self.root = tuple(argv), cwd
        # The fixed factory hashes the executable before Popen. No fallible
        # filesystem observation may strand a validated child inside __init__.
        self.executable = Path(argv[0])
        self.binary_sha256 = executable_sha256
        self.start, self.terminal = None, None
        self.known, self.ledger, self.logs, self.closed = set(), ledger, logs, False
        ledger.append({'event': 'build-created', 'pid': pid, 'creator_pid': self.creator,
                       'argv': list(argv), 'cwd': str(cwd), 'executable': str(self.executable),
                       'executable_sha256_observation': self.binary_sha256,
                       'loaded_text_observed': False, 'native_authority': False})


def _wait_build_child(owner):
    pid = _current_pid(owner)
    if owner.terminal is not None:
        return owner.terminal
    record = {'event': 'build-waitpid', 'pid': pid, 'flags': 'WNOHANG'}
    owner.ledger.append(record)
    try:
        result = os.waitpid(pid, os.WNOHANG)
        _need(type(result) is tuple and len(result) == 2
              and all(type(value) is int for value in result), 'actual build wait result type differs')
        record['result'] = list(result)
        actual, status = result
        _need(0 <= status <= 65535, 'raw build wait status exceeds native bound')
        if actual == 0:
            _need(status == 0, 'nonterminal build wait status differs')
            return None
        _need(actual == pid and (os.WIFEXITED(status) or os.WIFSIGNALED(status)),
              'actual terminal direct build child required')
        code = os.waitstatus_to_exitcode(status)
        _need(type(code) is int, 'decoded actual build status differs')
        owner.terminal = (actual, status, code)
        owner.process.returncode = code  # Housekeeping after actual reaping only.
        record['exit_code'] = code
        return owner.terminal
    except BaseException as error:
        record['error'] = ''.join(traceback.format_exception(error))
        raise


def _observe_build_owned(owner):
    """Return False for an actually finished command, without inventing live data."""
    pid = _current_pid(owner)
    if _wait_build_child(owner) is not None:
        return False
    native = {'event': 'build-owner', 'pid': pid, 'pgid': None, 'sid': None}
    owner.ledger.append(native)
    try:
        native['pgid'] = os.getpgid(pid)
        native['sid'] = os.getsid(pid)
    except ProcessLookupError as error:
        native['error'] = ''.join(traceback.format_exception(error))
        if _wait_build_child(owner) is not None:
            return False
        raise
    except BaseException as error:
        native['error'] = ''.join(traceback.format_exception(error))
        raise
    group, session = native['pgid'], native['sid']
    _need(type(group) is int and type(session) is int, 'exact native build group/session integers required')
    _need(group == session == pid and os.getpid() == owner.creator,
          'native build creator/group/session differs')

    def read(argv):
        if _wait_build_child(owner) is not None:
            return None
        code, stdout, stderr = _native(argv, owner.ledger)
        if _wait_build_child(owner) is not None:
            owner.ledger.append({'event': 'build-live-observation-ended-at-terminal', 'argv': argv})
            return None
        _need(code == 0 and stderr == '', 'native build identity observation failed')
        return stdout

    uid = read(['/bin/ps', '-p', str(pid), '-o', 'uid='])
    if uid is None:
        return False
    _need(_decimal(uid.strip()) == os.getuid(), 'actual build UID differs')
    command = read(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command='])
    if command is None:
        return False
    _need(command.strip() == ' '.join(owner.argv), 'actual build argv differs')
    start = read(['/bin/ps', '-p', str(pid), '-o', 'lstart='])
    if start is None:
        return False
    _need(start.strip() != '', 'actual build start is empty')
    if owner.start is None:
        owner.start = start.strip()
    _need(start.strip() == owner.start, 'actual build start differs')
    row = read(['/bin/ps', '-ww', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,command='])
    if row is None:
        return False
    fields = row.strip().split(None, 3)
    _need(len(fields) == 4 and tuple(_decimal(value) for value in fields[:3])
          == (pid, owner.creator, pid) and fields[3] == ' '.join(owner.argv),
          'actual build PPID/PGID/argv differs')
    texts = read(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-d', 'txt', '-Fn'])
    if texts is None:
        return False
    _need('n' + str(owner.executable) in texts.splitlines()
          and _file_sha(owner.executable) == owner.binary_sha256, 'actual build executable mapping/hash differs')
    cwd = read(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'])
    if cwd is None:
        return False
    values = cwd.splitlines()
    _need([value for value in values if value.startswith('p')] == ['p' + str(pid)]
          and [value for value in values if value.startswith('n')] == ['n' + str(owner.root)],
          'actual build cwd differs')
    table = _table(owner.ledger)
    if _wait_build_child(owner) is not None:
        return False
    _need(table.get(pid) == (owner.creator, pid), 'actual build process table differs')
    _remember_descendants(owner, table)
    owner.ledger.append({'event': 'build-live-identity-complete', 'pid': pid})
    return True


def _wait_build_until(owner, deadline, command):
    next_observation = 0.0
    while _wait_build_child(owner) is None:
        now = time.monotonic()
        if now >= deadline:
            raise subprocess.TimeoutExpired(['/usr/bin/time', '-l', *command], 1800)
        if now >= next_observation:
            if not _observe_build_owned(owner):
                return
            next_observation = time.monotonic() + 5
        time.sleep(0.05)


def _signal_build_owned(owner, kind):
    _need(kind in (signal.SIGTERM, signal.SIGKILL), 'fixed build signal required')
    if not _observe_build_owned(owner):
        return False
    owner.ledger.append({'event': 'phase', 'phase': 'final-pre-build-signal-wait', 'signal': int(kind)})
    if _wait_build_child(owner) is not None:
        return False
    record = {'event': 'build-signal', 'pid': owner.pid, 'signal': int(kind), 'succeeded': False}
    owner.ledger.append(record)
    try:
        os.killpg(owner.pid, kind)
        record['succeeded'] = True
        return True
    except BaseException as error:
        record['error'] = ''.join(traceback.format_exception(error))
        raise


class _BuildGroupPresent(ValueError):
    """A known same-group child can still be exiting after the direct wait."""


def _build_absence(owner):
    _need(owner.terminal is not None, 'actual build terminal wait is missing')
    table = _table(owner.ledger)
    _need(owner.pid not in table, 'reaped build PID was reused; absence is unknown')
    remaining = owner.known & set(table)
    _need(all(table[pid][1] == owner.pid for pid in remaining),
          'known build child left its group or PID was reused; absence is unknown')
    if remaining or any(group == owner.pid for _, group in table.values()):
        raise _BuildGroupPresent('build group/known descendants remain after direct wait')
    for argv in (['/bin/ps', '-p', str(owner.pid), '-o', 'pid='],
                 ['/usr/sbin/lsof', '-a', '-p', str(owner.pid), '-d', 'cwd', '-Fn']):
        code, stdout, stderr = _native(argv, owner.ledger)
        _need(code == 1 and stdout == stderr == '', 'actual build target absence unknown')
    try:
        group = os.getpgid(owner.pid)
    except ProcessLookupError:
        owner.ledger.append({'event': 'build-getpgid-after-reap', 'pid': owner.pid, 'result': 'ESRCH'})
    else:
        owner.ledger.append({'event': 'build-getpgid-after-reap', 'pid': owner.pid, 'result': group})
        raise ValueError('actual build PID/group absence unknown')


def _close_build_owned(owner):
    _current_pid(owner)
    primary = None
    try:
        if _wait_build_child(owner) is None:
            _signal_build_owned(owner, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while _wait_build_child(owner) is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if owner.terminal is None:
                _signal_build_owned(owner, signal.SIGKILL)
                deadline = time.monotonic() + 5
                while _wait_build_child(owner) is None and time.monotonic() < deadline:
                    time.sleep(0.05)
        _need(owner.terminal is not None, 'actual build terminal wait missing or child still live')
        return {'pid': owner.pid, 'terminal': list(owner.terminal), 'absence_verified': True,
                'native_ledger': owner.ledger, 'wait_scope': 'direct timing wrapper only; group/known absence separate'}
    except BaseException as error:
        primary = error
        raise
    finally:
        secondary = []
        if owner.terminal is None and primary is not None:
            try:
                _wait_build_child(owner)
            except BaseException as error:
                secondary.append(error)
        if owner.terminal is not None:
            try:
                # The direct wrapper can be reaped just before an already dying
                # command child disappears. Observe only; never signal after reap.
                absence_deadline = time.monotonic() + 5
                while True:
                    try:
                        _build_absence(owner)
                        break
                    except _BuildGroupPresent:
                        if time.monotonic() >= absence_deadline:
                            raise
                        time.sleep(0.05)
                owner.closed = primary is None
            except BaseException as error:
                secondary.append(error)
        for stream in owner.logs:
            try:
                stream.close()
            except BaseException as error:
                secondary.append(error)
        if secondary:
            owner.closed = False
            if primary is not None:
                for error in secondary:
                    primary.add_note('Secondary build absence/log error: '
                                     + ''.join(traceback.format_exception(error)))
            else:
                first = secondary[0]
                for error in secondary[1:]:
                    first.add_note('Additional build absence/log error: '
                                   + ''.join(traceback.format_exception(error)))
                raise first


def _dep_info(dep_file, worktree):
    raw = _read(dep_file)
    text = raw.decode('utf-8', 'strict').replace('\\\n', ' ')
    first = text.splitlines()[0]
    _need(': ' in first, 'complete actual Cargo dep-info required')
    tokens = re.split(r'(?<!\\) ', first.partition(': ')[2])
    result = set()
    for token in tokens:
        token = token.replace('\\ ', ' ').strip()
        if not token:
            continue
        path = Path(os.path.normpath(token))
        _need(path.is_absolute(), 'absolute dep-info input required')
        if path.is_relative_to(worktree):
            relative = path.relative_to(worktree).as_posix()
            _need(not relative.startswith('som-rs/compiler-assets/'), 'untracked compiled source refused')
            result.add(relative)
    _need(result, 'actual repository dep-info is empty')
    return raw, result


def _build_candidate_only(repository, reader, sources, compat, original, env, out):
    worktree, candidate = out / 'source-worktree', out / 'candidate'
    _need(not worktree.exists() and not candidate.exists(), 'owned build directories already exist')
    candidate.mkdir()
    _git(repository, 'worktree', 'add', '--detach', str(worktree), original['head'])
    _need(_snapshot(worktree, reader, original['files']) == original, 'owned build worktree source differs')
    assets = worktree / 'som-rs/compiler-assets/current'
    _need(not assets.exists() and not assets.is_symlink(), 'private assets destination must be absent')
    before = repository / '.local/m6-hosted/baseline'
    commands = [
        ([_RUST / 'cargo', 'build', '--release', '--locked', '--offline', '--bin', 'som-compiler-assets'],
         worktree / 'som-rs', candidate / 'target-assets'),
        ([candidate / 'target-assets/release/som-compiler-assets', '--destination', assets,
          '--formatter', before / 'assets/ruff'], worktree, candidate / 'target-assets'),
        ([_RUST / 'cargo', 'test', '--locked', '--offline'], worktree / 'som-rs/som-targets', candidate / 'target'),
        ([_RUST / 'cargo', 'build', '--release', '--locked', '--offline', '--bin', 'som'],
         worktree / 'som-rs/som-targets', candidate / 'target'),
    ]
    completed = []
    for index, (argv, cwd, target) in enumerate(commands):
        command_env = {**env, 'PATH': str(_RUST) + ':' + env.get('PATH', '/usr/bin:/bin'),
                       'RUSTC': str(_RUST / 'rustc'), 'CARGO': str(_RUST / 'cargo'),
                       'CARGO_TARGET_DIR': str(target)}
        completed.append(_build_command([str(value) for value in argv], cwd, command_env, out, index))
    deps = set(original['files'])
    dep_records = {}
    for name, path in [('som', candidate / 'target/release/som.d'),
                       ('som-compiler-assets', candidate / 'target-assets/release/som-compiler-assets.d')]:
        raw, names = _dep_info(path, worktree)
        deps.update(names)
        dep_records[name] = {'sha256': _sha(raw), 'paths': sorted(names)}
        _write(out / (name + '.d'), raw)
    public = _snapshot(repository, reader, deps)
    private = _snapshot(worktree, reader, deps)
    _need(public == private and _snapshot(repository, reader, original['files']) == original,
          'public/private compiled source changed or differs')
    _need(compat._derive_sources(worktree)[1]['manifest_bytes'] == sources[1]['manifest_bytes'],
          'private source backend manifest differs')
    expected = _expected_assets(repository, reader, sources[1], compat)
    packaged = _verify_assets(assets, expected)
    shutil.copytree(assets, candidate / 'assets', copy_function=shutil.copy2)
    _need(_verify_assets(candidate / 'assets', expected) == packaged, 'copied current assets differ')
    built = candidate / 'target/release/som'
    built_raw = _read(built)
    shutil.copy2(built, candidate / 'som')
    _need(_read(candidate / 'som') == built_raw and os.access(candidate / 'som', os.X_OK),
          'copied built executable differs')
    _write(out / 'source.json', {'version': 'som.mac-probe-source.v1', **public, 'dep_info': dep_records})
    with tarfile.open(out / 'source.tar', 'x', format=tarfile.PAX_FORMAT) as archive:
        for name, row in sorted(public['files'].items()):
            raw = _read(worktree / name)
            _need(_sha(raw) == row['sha256'], 'source changed before archive')
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), int(row['mode'], 8) & 0o777, 0
            archive.addfile(info, io.BytesIO(raw))
    with tarfile.open(out / 'source.tar', 'r:') as archive:
        members = archive.getmembers()
        _need([item.name for item in members] == sorted(public['files'])
              and all(item.isfile() for item in members), 'exact source archive membership differs')
        for item in members:
            stream = archive.extractfile(item)
            _need(stream is not None and _sha(stream.read()) == public['files'][item.name]['sha256'],
                  'source archive complete member bytes differ')
    record = {'version': 'som.mac-probe-build.v1', 'source_head': public['head'], 'commands': completed,
              'source_json_sha256': _sha(_read(out / 'source.json')),
              'source_tar_sha256': _sha(_read(out / 'source.tar')),
              'built_binary_sha256': _sha(built_raw), 'copied_binary_sha256': _sha(_read(candidate / 'som')),
              'packaged_assets': packaged, 'dep_info': dep_records, 'SourceSeal': False}
    _write(out / 'build.json', record)
    return candidate, record, public


def _native(argv, ledger):
    """Keep complete native observation bytes; the decoded form is for strict parsers."""
    record = {'argv': list(argv), 'exit_code': None, 'stdout_hex': '', 'stderr_hex': ''}
    ledger.append(record)
    try:
        done = subprocess.run(argv, env={'PATH': '/usr/bin:/bin:/usr/sbin', 'LC_ALL': 'C', 'LANG': 'C'},
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
        record.update(exit_code=done.returncode, stdout_hex=done.stdout.hex(), stderr_hex=done.stderr.hex())
        _need(len(done.stdout) <= 16 * 1024 * 1024 and len(done.stderr) <= 65536,
              'native observation exceeds fixed bound')
        return done.returncode, done.stdout.decode('utf-8', 'strict'), done.stderr.decode('utf-8', 'strict')
    except BaseException as error:
        record['error'] = ''.join(traceback.format_exception(error))
        raise


def _decimal(text):
    _need(type(text) is str and re.fullmatch('[0-9]+', text), 'strict decimal native field required')
    return int(text)


def _port_absent(port, ledger):
    _need(type(port) is int and port in (19062, 19063), 'fixed diagnostic port required')
    code, stdout, stderr = _native(['/usr/sbin/lsof', '-nP', f'-iTCP:{port}',
                                    '-sTCP:LISTEN', '-t'], ledger)
    _need(code == 1 and stdout == stderr == '', 'diagnostic port is occupied or its absence is unknown')


def _table(ledger):
    code, stdout, stderr = _native(['/bin/ps', '-axo', 'pid=,ppid=,pgid='], ledger)
    _need(code == 0 and stderr == '', 'complete process table observation failed')
    result = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        _need(len(fields) == 3, 'process table row differs')
        pid, ppid, pgid = (_decimal(value) for value in fields)
        _need(pid not in result, 'process table PID repeats')
        result[pid] = (ppid, pgid)
    _need(result, 'process table is empty')
    return result


class _Owned:
    """Private creator state. It is never accepted or returned by run_probe."""
    __slots__ = ('process', 'pid', 'creator', 'role', 'root', 'executable', 'assets', 'port',
                 'binary_sha256', 'asset_hashes', 'argv', 'start', 'terminal', 'term_sent',
                 'known', 'ledger', 'logs', 'unplanned', 'closed')

    def __init__(self, process, spec, argv, ledger, logs):
        pid = process.pid
        _need(type(pid) is int and pid > 1, 'direct child initial PID must be exact int')
        self.process, self.pid, self.creator = process, pid, os.getpid()
        self.role, self.root, self.executable = spec['role'], spec['root'], spec['executable']
        self.assets, self.port = spec['assets'], spec['port']
        self.binary_sha256, self.asset_hashes = spec['binary_sha256'], spec['asset_hashes']
        self.argv, self.ledger, self.logs = tuple(argv), ledger, logs
        self.start, self.terminal, self.term_sent = None, None, False
        self.known, self.unplanned, self.closed = set(), False, False


def _current_pid(owner):
    current = owner.process.pid
    # Do not compare, format, hash or call a syscall before the exact-type gate.
    _need(type(current) is int, 'current direct Popen PID must be exact int')
    _need(current == owner.pid, 'current direct Popen PID differs from private initial PID')
    return owner.pid


def _wait_owned_child(owner):
    pid = _current_pid(owner)
    if owner.terminal is not None:
        return owner.terminal
    record = {'event': 'waitpid', 'pid': pid, 'flags': 'WNOHANG'}
    owner.ledger.append(record)
    try:
        result = os.waitpid(pid, os.WNOHANG)
        _need(type(result) is tuple and len(result) == 2
              and all(type(value) is int for value in result), 'actual waitpid result type differs')
        record['result'] = list(result)
        actual_pid, raw_status = result
        _need(0 <= raw_status <= 65535, 'raw kernel wait status exceeds its native bound')
        if actual_pid == 0:
            _need(raw_status == 0, 'nonterminal waitpid status differs')
            return None
        _need(actual_pid == pid and (os.WIFEXITED(raw_status) or os.WIFSIGNALED(raw_status)),
              'actual terminal direct child wait required')
        code = os.waitstatus_to_exitcode(raw_status)
        _need(type(code) is int, 'decoded actual kernel status differs')
        owner.terminal = (actual_pid, raw_status, code)
        owner.unplanned = not owner.term_sent
        owner.process.returncode = code  # Housekeeping only, after genuine kernel reaping.
        record.update(exit_code=code, unplanned=owner.unplanned)
        return owner.terminal
    except BaseException as error:
        record['error'] = ''.join(traceback.format_exception(error))
        raise


def _remember_descendants(owner, table):
    discovered = {pid for pid, (_, group) in table.items() if group == owner.pid and pid != owner.pid}
    parents = {owner.pid}
    while True:
        children = {pid for pid, (ppid, _) in table.items() if ppid in parents}
        if children <= parents:
            break
        parents.update(children)
    owner.known.update(discovered | (parents - {owner.pid}))


def _observe_owned_role(owner, listener=True):
    pid = _current_pid(owner)
    _need(_wait_owned_child(owner) is None, 'unplanned serve exit before probe-owned termination')
    native = {'event': 'owner', 'pid': pid, 'pgid': os.getpgid(pid), 'sid': os.getsid(pid)}
    owner.ledger.append(native)
    _need(native['pgid'] == native['sid'] == pid and os.getpid() == owner.creator,
          'native direct process group/session/creator differs')
    def success(argv):
        code, stdout, stderr = _native(argv, owner.ledger)
        _need(code == 0 and stderr == '', 'required native owner observation failed')
        return stdout
    uid = success(['/bin/ps', '-p', str(pid), '-o', 'uid=']).strip()
    _need(_decimal(uid) == os.getuid(), 'actual owner UID differs')
    command = success(['/bin/ps', '-ww', '-p', str(pid), '-o', 'command=']).strip()
    _need(command == ' '.join(owner.argv), 'actual owner argv differs')
    start = success(['/bin/ps', '-p', str(pid), '-o', 'lstart=']).strip()
    _need(start != '', 'actual process start observation is empty')
    if owner.start is None:
        owner.start = start
    _need(start == owner.start, 'actual process start observation differs')
    fields = success(['/bin/ps', '-ww', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,command=']).strip().split(None, 3)
    _need(len(fields) == 4 and tuple(_decimal(value) for value in fields[:3])
          == (pid, owner.creator, pid) and fields[3] == ' '.join(owner.argv),
          'actual direct PPID/PGID/argv differs')
    texts = success(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-d', 'txt', '-Fn']).splitlines()
    _need('n' + str(owner.executable) in texts and _sha(_read(owner.executable)) == owner.binary_sha256,
          'actual executable text mapping/path/on-disk hash differs')
    cwd = success(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines()
    _need([value for value in cwd if value.startswith('p')] == ['p' + str(pid)]
          and [value for value in cwd if value.startswith('n')] == ['n' + str(owner.root)],
          'actual process cwd differs')
    _need({path.name for path in owner.assets.iterdir()} == set(_ASSETS), 'live asset set differs')
    for name, expected in owner.asset_hashes.items():
        _need(_sha(_read(owner.assets / name)) == expected['sha256'], 'live owned asset bytes differ')
    table = _table(owner.ledger)
    _need(table.get(pid) == (owner.creator, pid), 'actual owner process table row differs')
    _remember_descendants(owner, table)
    if listener:
        values = success(['/usr/sbin/lsof', '-nP', '-a', f'-iTCP:{owner.port}',
                          '-sTCP:LISTEN', '-Fpn']).splitlines()
        _need([value for value in values if value.startswith('p')] == ['p' + str(pid)]
              and [value for value in values if value.startswith('n')] == [f'n127.0.0.1:{owner.port}'],
              'sole owned loopback listener differs')
    # The final wait catches a child that exited while native observations ran.
    _need(_wait_owned_child(owner) is None, 'unplanned serve exit during owner observations')
    return native


def _spawn_owned_role(spec, env, ledger):
    _need(type(spec) is dict and spec['role'] in ('before', 'candidate')
          and spec['port'] == (19062 if spec['role'] == 'before' else 19063), 'fixed internally built role required')
    for name in ('root', 'executable', 'assets'):
        _physical(spec[name])
    _need(spec['executable'] == spec['root'] / 'som' and spec['assets'] == spec['root'] / 'assets'
          and _sha(_read(spec['executable'])) == spec['binary_sha256'], 'verified role executable differs')
    _port_absent(spec['port'], ledger)
    argv = _argv([str(spec['executable']), 'serve', '--assets', str(spec['assets']), '--port', str(spec['port'])])
    logs = []
    owner = None
    try:
        _physical(spec['logs_root'], exists=False)
        _need(not spec['logs_root'].exists(), 'new owned service log directory required')
        spec['logs_root'].mkdir(parents=True)
        logs.append((spec['logs_root'] / 'serve.stdout').open('xb'))
        logs.append((spec['logs_root'] / 'serve.stderr').open('xb'))
        process = subprocess.Popen(argv, cwd=spec['root'], env=env, stdin=subprocess.DEVNULL,
                                   stdout=logs[0], stderr=logs[1], shell=False, close_fds=True,
                                   start_new_session=True)
        owner = _Owned(process, spec, argv, ledger, logs)
        ledger.append({'event': 'serve-created', 'role': owner.role, 'pid': owner.pid,
                       'creator_pid': owner.creator, 'argv': list(argv), 'cwd': str(owner.root),
                       'executable': str(owner.executable), 'binary_sha256': owner.binary_sha256})
        deadline = time.monotonic() + 30
        while True:
            _observe_owned_role(owner, listener=False)
            code, stdout, stderr = _native(['/usr/sbin/lsof', '-nP', '-a', f'-iTCP:{owner.port}',
                                          '-sTCP:LISTEN', '-Fpn'], ledger)
            if code == 0:
                _observe_owned_role(owner, listener=True)
                return owner
            _need(code == 1 and stdout == stderr == '', 'startup listener observation failed')
            _need(time.monotonic() < deadline, 'owned service did not open its fixed listener')
            time.sleep(0.05)
    except BaseException as error:
        if owner is not None:
            try:
                # Snapshot before linking to this same ledger; records contain it.
                cleanup = _plain(_cleanup_roles([owner], primary=error))
                ledger.append({'event': 'failed-start-cleanup', 'records': cleanup})
            except BaseException as cleanup_error:
                error.add_note('Secondary failed-start cleanup/evidence error: '
                               + ''.join(traceback.format_exception(cleanup_error)))
        else:
            for stream in logs:
                stream.close()
        raise


def _signal_owned(owner, kind):
    _need(kind in (signal.SIGTERM, signal.SIGKILL), 'fixed owned signal required')
    if _wait_owned_child(owner) is not None:
        _need(kind == signal.SIGKILL and owner.term_sent and not owner.unplanned,
              'unplanned serve exit before probe-owned TERM')
        return False
    try:
        _observe_owned_role(owner, listener=False)
    except BaseException:
        # TERM may finish while the optional later KILL gate observes the child.
        # An actual terminal wait forbids KILL; it is not an owner failure to hide.
        if kind == signal.SIGKILL and owner.term_sent and owner.terminal is not None and not owner.unplanned:
            return False
        raise
    # Recheck immediately before the syscall. The check and signal are sequential.
    owner.ledger.append({'event': 'phase', 'phase': 'final-pre-signal-wait', 'signal': int(kind)})
    if _wait_owned_child(owner) is not None:
        _need(kind == signal.SIGKILL and owner.term_sent and not owner.unplanned,
              'unplanned serve exit at final pre-signal wait gate')
        return False
    record = {'event': 'signal', 'pid': owner.pid, 'signal': int(kind), 'succeeded': False}
    owner.ledger.append(record)
    try:
        os.killpg(owner.pid, kind)
        record['succeeded'] = True
        if kind == signal.SIGTERM:
            owner.term_sent = True
        return True
    except BaseException as error:
        record['error'] = ''.join(traceback.format_exception(error))
        raise


def _absence_owned(owner):
    _need(owner.terminal is not None, 'absence without actual terminal kernel wait is unknown')
    table = _table(owner.ledger)
    _need(owner.pid not in table and not (owner.known & set(table))
          and all(group != owner.pid for _, group in table.values()), 'owned child/group/descendants remain or PID reused')
    _port_absent(owner.port, owner.ledger)
    for argv in (['/bin/ps', '-p', str(owner.pid), '-o', 'pid='],
                 ['/usr/sbin/lsof', '-a', '-p', str(owner.pid), '-d', 'cwd', '-Fn']):
        code, stdout, stderr = _native(argv, owner.ledger)
        _need(code == 1 and stdout == stderr == '', 'target process absence is unknown')
    try:
        group = os.getpgid(owner.pid)
    except ProcessLookupError:
        owner.ledger.append({'event': 'getpgid-after-reap', 'pid': owner.pid, 'result': 'ESRCH'})
    else:
        owner.ledger.append({'event': 'getpgid-after-reap', 'pid': owner.pid, 'result': group})
        raise ValueError('terminal child PID was reused or its absence is unknown')


def _close_owned_role(owner):
    _current_pid(owner)
    primary = None
    try:
        if _wait_owned_child(owner) is None:
            _signal_owned(owner, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while _wait_owned_child(owner) is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if owner.terminal is None:
                _signal_owned(owner, signal.SIGKILL)
                deadline = time.monotonic() + 5
                while _wait_owned_child(owner) is None and time.monotonic() < deadline:
                    time.sleep(0.05)
        _need(owner.terminal is not None, 'owned terminal wait is missing or child remains live')
        _need(not owner.unplanned and owner.term_sent, 'unplanned serve exit before probe-owned TERM, including exit0')
        return {'role': owner.role, 'pid': owner.pid, 'terminal': list(owner.terminal),
                'term_sent': owner.term_sent, 'unplanned': owner.unplanned, 'absence_verified': True,
                'native_ledger': owner.ledger}
    except BaseException as error:
        primary = error
        raise
    finally:
        first_secondary = None
        if owner.terminal is None and primary is not None:
            try:
                # A failed identity/signal gate never grants another signal.
                # Still retain an actual nonblocking wait when its PID gate permits it.
                _wait_owned_child(owner)
            except BaseException as error:
                first_secondary = error
        # Even an exit discovered in the final pre-TERM gate retains absence data.
        if owner.terminal is not None:
            try:
                _absence_owned(owner)
                if primary is None:
                    owner.closed = True
            except BaseException as error:
                if first_secondary is None:
                    first_secondary = error
                else:
                    first_secondary.add_note('Additional terminal absence error: '
                                             + ''.join(traceback.format_exception(error)))
        for stream in owner.logs:
            try:
                stream.close()
            except BaseException as error:
                if first_secondary is None:
                    first_secondary = error
                else:
                    first_secondary.add_note('Additional log close error: '
                                             + ''.join(traceback.format_exception(error)))
        if first_secondary is not None:
            owner.closed = False
            if primary is None:
                raise first_secondary
            primary.add_note('Secondary terminal absence/log error: '
                             + ''.join(traceback.format_exception(first_secondary)))


def _cleanup_roles(roles, primary=None):
    records, errors = [], []
    for owner in reversed(roles):
        try:
            records.append(_close_owned_role(owner))
        except BaseException as error:
            errors.append(error)
            records.append({'role': owner.role, 'pid': owner.pid, 'terminal': owner.terminal,
                            'term_sent': owner.term_sent, 'unplanned': owner.unplanned,
                            'absence_verified': False, 'error': ''.join(traceback.format_exception(error)),
                            'native_ledger': owner.ledger})
    if primary is not None:
        for error in errors:
            primary.add_note('Secondary owned cleanup error: ' + ''.join(traceback.format_exception(error)))
    elif errors:
        first = errors[0]
        for error in errors[1:]:
            first.add_note('Additional owned cleanup error: ' + ''.join(traceback.format_exception(error)))
        # Keep the full record available to run_probe without wrapping the error.
        first.add_note('Cleanup evidence: ' + json.dumps(_plain(records)))
        raise first
    return records


def _capture_exchange(owner, request, phase, target):
    _need(type(request) is bytes and len(request) <= _MAX_BODY and phase in ('compile', 'assemble'),
          'fixed bounded HTTP request required')
    _observe_owned_role(owner)
    target.mkdir(parents=True, exist_ok=False)
    _write(target / 'request.raw', request)
    connection = http.client.HTTPConnection('127.0.0.1', owner.port, timeout=120)
    transport = {'method': 'POST', 'route': '/v1/' + phase, 'owned_pid': owner.pid,
                 'port': owner.port, 'status': None, 'headers': [], 'read_complete': False,
                 'bytes': 0, 'sha256': None}
    try:
        connection.request('POST', transport['route'], body=request,
                           headers={'Content-Type': 'application/json', 'Content-Length': str(len(request))})
        response = connection.getresponse()
        transport.update(status=response.status, headers=[list(row) for row in response.getheaders()])
        raw = response.read(_MAX_BODY + 1)
        _write(target / 'response.raw', raw)
        transport.update(bytes=len(raw), sha256=_sha(raw))
        lengths = [value for key, value in transport['headers'] if key.lower() == 'content-length']
        _need(response.status == 200 and len(raw) <= _MAX_BODY and len(lengths) == 1
              and re.fullmatch('[0-9]+', lengths[0]) and int(lengths[0]) == len(raw),
              'complete bounded successful HTTP response required')
        transport['read_complete'] = True
        return raw, _compact(transport)
    except BaseException as error:
        try:
            _write(target / 'capture-error.txt', ''.join(traceback.format_exception(error)).encode())
        except BaseException as evidence_error:
            error.add_note('Secondary capture error write: '
                           + ''.join(traceback.format_exception(evidence_error)))
        raise
    finally:
        primary = sys.exception()
        first_secondary = None
        try:
            connection.close()
        except BaseException as error:
            first_secondary = error
        try:
            _write(target / 'transport.json', _compact(transport))
        except BaseException as error:
            if first_secondary is None:
                first_secondary = error
            else:
                first_secondary.add_note('Secondary transport write: '
                                         + ''.join(traceback.format_exception(error)))
        # Always observe after HTTP, preserving the original body failure.
        try:
            _observe_owned_role(owner)
        except BaseException as error:
            if first_secondary is None:
                first_secondary = error
            else:
                first_secondary.add_note('Post-HTTP owner check: '
                                         + ''.join(traceback.format_exception(error)))
        if first_secondary is not None:
            if primary is None:
                raise first_secondary
            primary.add_note('Secondary HTTP close/evidence/owner error: '
                             + ''.join(traceback.format_exception(first_secondary)))


def _capture_owned_pair(roles, retained, compat, out):
    pairs = []
    for form in ('omitted', 'python'):
        pair = {'form': form}
        request = retained[f'{form}/before/compile/request.raw']
        _need(request == retained[f'{form}/candidate/compile/request.raw'], 'fixed retained input pair differs')
        _need(len(request) == (600 if form == 'omitted' else 618), 'fixed request length differs')
        for owner in roles:
            compile_raw, compile_transport = _capture_exchange(owner, request, 'compile',
                                                             out / 'raw' / form / owner.role / 'compile')
            envelope = compat._parse(compile_raw)
            _need(type(envelope) is dict and type(envelope.get('bundle')) is dict, 'own complete bundle is missing')
            assemble_request = compat.compact({'bundle': envelope['bundle']})
            assemble_raw, assemble_transport = _capture_exchange(owner, assemble_request, 'assemble',
                                                                out / 'raw' / form / owner.role / 'assemble')
            pair[owner.role] = {'compile_request': request, 'compile_response': compile_raw,
                                'compile_transport': compile_transport, 'assemble_request': assemble_request,
                                'assemble_response': assemble_raw, 'assemble_transport': assemble_transport}
        pairs.append(pair)
    return pairs


def _compare_fresh_pairs(pairs, sources, compat):
    # Fixed private data primitive after actual source/build/creator/capture steps.
    return [_plain(compat._compare_pair_data(pair, sources)) for pair in pairs]


def run_probe(repository, frozen_before_root, output_root):
    """Run the selected one-case real Mac diagnostic, using three exact Paths."""
    identity = _runner(repository, frozen_before_root, output_root)
    output_root.mkdir()
    roles, primary = [], None
    result = {'version': 'som.mac-python-render-only-probe.v1', 'passed': False,
              'identity': identity, 'native_authority': False, 'SourceSeal': False,
              'genuine_Mac_Context': False, 'full671': False, 'full12': False,
              'four_receipts_approved': False, 'product_acceptance_passed': False,
              'historical100_wire_recovered': False, 'signal_causality_is_atomic': False}
    result['creation_attempts'] = []
    checkout = _checkout_identity(repository)
    result['checkout_before'] = checkout
    try:
        with _qualified_modules(repository) as (reader, raw, compat):
            sources, vectors, original = _bind_probe_source(repository, reader, compat)
            result['canonical_vectors'] = vectors
            result['backend_sources'] = [{key: _plain(value) for key, value in source.items()
                                          if key != 'manifest_bytes'}
                                         | {'manifest_utf8': source['manifest_bytes'].decode('utf-8')}
                                         for source in sources]
            result['tools'] = _verify_frozen_tools_and_before(repository, frozen_before_root,
                                                             reader, sources, compat, output_root)
            env = _environment(repository)
            candidate, build, full_source = _build_candidate_only(repository, reader, sources, compat,
                                                                 original, env, output_root)
            result.update(build=build, source=full_source)
            _need(_checkout_identity(repository) == checkout, 'public checkout HEAD/index changed during build')
            retained = compat._authenticate_fixture(repository / 'som-rs/scripts/hosted_readiness/fixtures/python-raw-compat-v1')
            for role, root, port, binary, assets in (
                ('before', frozen_before_root, 19062, _BEFORE_BINARY, result['tools']['before_assets']),
                ('candidate', candidate, 19063, build['copied_binary_sha256'], build['packaged_assets'])):
                spec = {'role': role, 'root': root, 'executable': root / 'som',
                        'assets': root / 'assets', 'port': port, 'binary_sha256': binary,
                        'asset_hashes': assets, 'logs_root': output_root / 'logs' / role}
                ledger = []
                result['creation_attempts'].append({'role': role, 'native_ledger': ledger})
                roles.append(_spawn_owned_role(spec, env, ledger))
            pairs = _capture_owned_pair(roles, retained, compat, output_root)
            result['pairs'] = _compare_fresh_pairs(pairs, sources, compat)
            result['fresh_exchange_count'] = 8
            _need(_snapshot(repository, reader, full_source['files']) == full_source,
                  'actual selected source changed during live diagnostic')
            _need(_snapshot(output_root / 'source-worktree', reader, full_source['files']) == full_source,
                  'private compiled source changed during live diagnostic')
            _need(_sha(_read(frozen_before_root / 'som')) == _BEFORE_BINARY,
                  'immutable restored before binary changed')
            _need(_verify_assets(frozen_before_root / 'assets',
                                _expected_assets(repository, reader, sources[0], compat))
                  == result['tools']['before_assets'], 'immutable restored before assets changed')
            _need(_checkout_identity(repository) == checkout, 'public checkout HEAD/index changed during capture')
            result['passed'] = True
    except BaseException as error:
        primary = error
        result['body_error'] = ''.join(traceback.format_exception(error))
        raise
    finally:
        try:
            result['cleanup'] = _cleanup_roles(roles, primary=primary)
        except BaseException as error:
            result['passed'] = False
            result['cleanup_error'] = ''.join(traceback.format_exception(error))
            raise
        finally:
            if primary is not None:
                result['passed'] = False
            active_error = sys.exception()
            try:
                _write(output_root / 'result.json', result)
            except BaseException as evidence_error:
                if active_error is None:
                    raise
                active_error.add_note('Secondary result write error: '
                                      + ''.join(traceback.format_exception(evidence_error)))
    return _freeze(result)
