"""Scoped two-source input binding derived from independently reviewed 70cc proof."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sys
from contextlib import contextmanager
from types import ModuleType
REPO = Path(__file__).resolve().parents[3]
SELF = 'som-rs/scripts/react_output_acceptance/native_owner_input_binding.py'
CONTEXT = 'som-rs/scripts/hosted_readiness/product_runtime/full_product_context.py'
DERIVED = 'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py'
PROVENANCE = 'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py'
BINDER_ROLE = 'm6.input.binding'
HELPERS = {
    'derived': ('m6.input.derived', DERIVED, 'reconstruct_historical_responses',
                'f7231671f41cb78cfe50e195159f54c644baa1f65a0a186c52f2abc2b8877e41'),
    'provenance': ('m6.input.provenance', PROVENANCE, 'verify_historical_response_provenance',
                   '82efed50d624cfe1929de5448ca756bbec5c28f8f04ba652fe8286fc285f5d1b'),
}

def _native_route():
    if '__som_native_active_controls__' in globals():
        return True
    try:
        from _som_native_active_controls_binding import controls as _probe
    except ModuleNotFoundError as error:
        need(error.name == '_som_native_active_controls_binding'
             and '_som_native_active_controls' not in sys.modules,
             'native input binder has no fixed active-controls binding')
        return False
    raise ValueError('native input binder has no fixed active-controls binding')

def _native_identity():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    need(controls.role_sources.get(BINDER_ROLE) == SELF and type(REPO) is type(controls.root)
         and REPO == controls.root, 'fixed native input binder root/source required')
    need(type(__som_native_context_module__) is ModuleType, 'actual fixed Context module required')
    namespace = ModuleType.__getattribute__(__som_native_context_module__, '__dict__')
    need(namespace.get('__file__') == str(REPO / CONTEXT)
         and namespace.get('Context') is __som_native_Context__
         and namespace.get('SourceBytes') is __som_native_SourceBytes__,
         'actual fixed Context source/classes differ')
    ctx = namespace.get('_ACTIVE')
    need(type(ctx) is __som_native_Context__, 'genuine active native Context is unavailable; upstream join pending')
    values = object.__getattribute__(ctx, '__dict__')
    need(type(values) is dict and type(values.get('qa_mode')) is str and values['qa_mode'] == 'native-qa-v1'
         and type(values.get('repo')) is type(controls.root) and values['repo'] == controls.root
         and type(values.get('helper_revision')) is str and type(values.get('candidate_revision')) is str
         and values['helper_revision'] == values['candidate_revision'] == controls.helper_revision == controls.candidate_revision,
         'actual native input Context root/revisions differ')
    context_lease = values.get('qa_import_controls')
    need(type(context_lease) is type(controls), 'actual native Context source lease required')
    context_lease.verify_installed()
    need(context_lease.root == controls.root and context_lease.helper_revision == controls.helper_revision
         and context_lease.candidate_revision == controls.candidate_revision,
         'actual native Context source lease owner differs')
    sources = values.get('sources')
    need(type(sources) is __som_native_SourceBytes__, 'actual native input SourceBytes required')
    data = object.__getattribute__(sources, '__dict__')
    need(type(data) is dict and type(data.get('root')) is type(controls.root) and data['root'] == controls.root
         and type(data.get('revision')) is str and data['revision'] == controls.helper_revision
         and type(data.get('pins')) is dict
         and all(type(path) is str and type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin)
                 for path, pin in data['pins'].items()), 'native input source owner differs')
    authority = values.get('qa_source_authority')
    need(type(authority) is dict and authority.get('source_authority_active') is True
         and type(authority.get('helper_revision')) is str and type(authority.get('candidate_revision')) is str
         and authority.get('helper_revision') == authority.get('candidate_revision') == controls.helper_revision
         and type(authority.get('admitted_repository_python_sources')) is list
         and all(type(path) is str for path in authority['admitted_repository_python_sources'])
         and {SELF, CONTEXT, DERIVED, PROVENANCE} <= set(authority['admitted_repository_python_sources']),
         'complete actual native input source authority required')
    need(sha(sources.read(SELF)) == __som_raw_sha256__, 'native input binder differs from actual Core source')
    return controls, ctx, sources

@contextmanager
def _native_owner():
    controls, ctx, sources = _native_identity()
    lease = controls.borrow()
    primary = None
    try:
        lease.verify_installed()
        yield lease, ctx, sources
    except BaseException as error:
        primary = error
        raise
    finally:
        errors = []
        def owner_unchanged():
            current, current_ctx, current_sources = _native_identity()
            need(current is controls and current_ctx is ctx and current_sources is sources,
                 'native input owner pointer changed')
        def source_unchanged():
            current, current_ctx, current_sources = _native_identity()
            need(current is controls and current_ctx is ctx and current_sources is sources,
                 'native input source pointer changed')
            sources.verify()
        for action in (owner_unchanged, source_unchanged, lease.verify_installed, lease.close):
            try:
                action()
            except BaseException as error:
                errors.append(error)
        if errors:
            first = primary if primary is not None else errors[0]
            BaseException.__setattr__(first, '__som_native_input_cleanup_secondary__',
                tuple(errors if primary is not None else errors[1:]))
            if primary is None:
                raise first

def _native_helpers(controls, sources):
    for role, path, name, expected in HELPERS.values():
        need(controls.role_sources.get(role) == path and sha(sources.read(path)) == expected,
             'fixed native input helper source differs')
    modules = {}
    for key, (role, path, name, expected) in HELPERS.items():
        module = controls.load_module(role)
        need(type(module) is ModuleType, 'actual Core input helper module required')
        namespace = ModuleType.__getattribute__(module, '__dict__')
        need(namespace.get('__name__') == name and namespace.get('__file__') == str(REPO / path)
             and namespace.get('__som_raw_sha256__') == expected,
             'native input helper namespace/source differs')
        modules[key] = module
    provenance = ModuleType.__getattribute__(modules['provenance'], '__dict__')
    need(provenance.get('r') is modules['derived'], 'native provenance reconstruction pointer differs')
    controls.verify_installed()
    return modules['derived'], modules['provenance']

def _native_inputs(public, legacy, config_path, provenance_root):
    with _native_owner() as (controls, ctx, sources):
        need(type(public) is type(REPO) and type(legacy) is type(REPO)
             and type(config_path) is type(REPO) and type(provenance_root) is type(REPO),
             'native input arguments must be actual path data')
        need(public==REPO/'.local/rust-compiler-goal/acceptance/public-products-003b' and
             legacy==REPO/'.local/rust-compiler-goal/acceptance/legacy544/run-005', 'historic source roots differ')
        need(config_path is not None and provenance_root is not None, 'two-source historic provenance is required')
        config=json_file(config_path)
        need(type(config) is dict and set(config)=={'version','commit','index_sha256'} and
             config['version']=='som.m6-hosted-inputs.v1' and config['commit']==HISTORIC_RESPONSE_APPROVED_COMMIT and
             config['index_sha256']==HISTORIC_RESPONSE_APPROVED_INDEX_SHA256, 'approved 18-package source differs')
        index=git_blob(config['commit'],'inputs-index.json')
        files=manifest_map(index,config['index_sha256'],lambda name:git_blob(config['commit'],name))
        derived, provenance = _native_helpers(controls, sources)
        map_raw=read(REPO/'som-rs/scripts/react_output_acceptance/historical-response-reuse-map.json')
        temp=Path(provenance_root).resolve(strict=True)
        original=derived.derive(map_raw,temp/'input-packages',REPO,temp/'evidence-sealed',temp/'evidence')
        evidence=derived.indexed_files(temp/'evidence-sealed',derived.EVIDENCE_SHA,True)
        load=lambda name:derived.checked(temp/'evidence',name,evidence)
        recording=load(provenance.BASE+'manifest.json'); report=load('run/python-baseline/report.json')
        need(sha(recording)==provenance.RECORDING_SHA and sha(report)==provenance.REPORT_SHA,
             'later capture raw authorities differ')
        joined=provenance.join(derived.parse(map_raw),derived.parse(recording),derived.parse(report),load)
        need(joined['derived_pairs']==668 and len(original)==1336,'derived response inventory differs')
        files=merge_derived(files,original,REPO)
        return BoundInputs(REPO,files)
def need(value, text):
    if not value: raise ValueError(text)

def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(path):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents), 'physical file required')
    need(path.stat().st_size <= 128 * 1024 * 1024, 'file too large')
    return path.read_bytes()
def obj(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            need(k not in result, 'duplicate member')
            result[k] = v
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
def json_file(path): return obj(read(path))
HISTORIC_RESPONSE_APPROVED_INDEX_SHA256 = 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'
HISTORIC_RESPONSE_APPROVED_COMMIT = '1de8f05430a72f8d76f6159786cca71b0ae1a911'

def git_blob(commit, name):
    import subprocess
    need(re.fullmatch('[0-9a-f]{40}',commit) is not None and
         re.fullmatch(r'(?:inputs-index\.json|package-[0-9]{3}\.manifest\.json)',name) is not None,
         'input Git blob identity differs')
    return subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'show',commit+':'+name],timeout=120)

def manifest_map(index_raw, expected_sha, load):
    need(type(expected_sha) is str and re.fullmatch('[0-9a-f]{64}',expected_sha) is not None
         and sha(index_raw)==expected_sha, 'input index source differs')
    index=obj(index_raw)
    need(type(index) is dict and set(index)=={'version','packages'} and index['version']=='som.m6-input-index.v1'
         and type(index['packages']) is list and 1<=len(index['packages'])<=1000, 'input index differs')
    files={}; names=set()
    for package in index['packages']:
        need(type(package) is dict and set(package)=={'archive','manifest','manifest_sha256'}
             and re.fullmatch(r'package-[0-9]{3}\.sealed',package.get('archive','')) is not None
             and package['manifest']==package['archive'][:-7]+'.manifest.json'
             and package['manifest'] not in names, 'input package identity differs')
        names.add(package['manifest'])
        raw=load(package['manifest'])
        need(sha(raw)==package['manifest_sha256'],'input manifest hash differs')
        manifest=obj(raw)
        need(type(manifest) is dict and set(manifest)=={'version','cipher_sha256','uncompressed_bytes','total_files','files'}
             and manifest['version']=='som.m6-sealed-inputs-manifest.v1'
             and re.fullmatch('[0-9a-f]{64}',manifest['cipher_sha256']) is not None
             and type(manifest['files']) is list and type(manifest['total_files']) is int
             and manifest['total_files']==len(manifest['files']), 'input manifest differs')
        for row in manifest['files']:
            need(type(row) is dict and set(row)=={'path','sha256','bytes','mode'} and
                 type(row['path']) is str and row['path'] and not row['path'].startswith('/') and
                 '\\' not in row['path'] and all(part not in ('','.','..') for part in row['path'].split('/')) and
                 type(row['bytes']) is int and row['bytes']>=0 and row['mode'] in (0o644,0o755) and
                 type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}',row['sha256']) is not None and
                 row['path'] not in files, 'input member identity differs')
            files[row['path']]={'sha256':row['sha256'],'bytes':row['bytes']}
    return files

class BoundInputs:
    def __init__(self,repo,files):
        self.repo=repo
        self.files=files
    def read(self,path):
        path=Path(path)
        need(path.is_absolute() and path.is_relative_to(self.repo), 'input outside repository')
        relative=path.relative_to(self.repo).as_posix()
        need(relative in self.files, 'input is not in approved package manifest')
        data=read(path); expected=self.files[relative]
        need(len(data)==expected['bytes'] and sha(data)==expected['sha256'], 'input bytes differ from approved manifest')
        return data
    def json(self,path):return obj(self.read(path))

def merge_derived(files, original, repo):
    combined=dict(files)
    for name,data in original.items():
        need(name not in combined and read(repo/name)==data,'derived response bytes or source role differs')
        combined[name]={'sha256':sha(data),'bytes':len(data),'source':'later-capture-derived'}
    return combined

def approved_inputs(public,legacy,config_path=None,provenance_root=None):
    if _native_route():
        return _native_inputs(public, legacy, config_path, provenance_root)
    need(public==REPO/'.local/rust-compiler-goal/acceptance/public-products-003b' and
         legacy==REPO/'.local/rust-compiler-goal/acceptance/legacy544/run-005', 'historic source roots differ')
    need(config_path is not None and provenance_root is not None, 'two-source historic provenance is required')
    config=json_file(config_path)
    need(type(config) is dict and set(config)=={'version','commit','index_sha256'} and
         config['version']=='som.m6-hosted-inputs.v1' and config['commit']==HISTORIC_RESPONSE_APPROVED_COMMIT and
         config['index_sha256']==HISTORIC_RESPONSE_APPROVED_INDEX_SHA256, 'approved 18-package source differs')
    index=git_blob(config['commit'],'inputs-index.json')
    files=manifest_map(index,config['index_sha256'],lambda name:git_blob(config['commit'],name))
    # This path was produced from the approved #130 capture; it is never relabeled
    # as the historical wire. Recompute and join its exact source in this job.
    import sys
    sys.path.insert(0,str(REPO/'som-rs/scripts/react_output_acceptance'))
    try:
        import reconstruct_historical_responses as derived
        import verify_historical_response_provenance as provenance
        map_raw=read(REPO/'som-rs/scripts/react_output_acceptance/historical-response-reuse-map.json')
        temp=Path(provenance_root).resolve(strict=True)
        original=derived.derive(map_raw,temp/'input-packages',REPO,temp/'evidence-sealed',temp/'evidence')
        evidence=derived.indexed_files(temp/'evidence-sealed',derived.EVIDENCE_SHA,True)
        load=lambda name:derived.checked(temp/'evidence',name,evidence)
        recording=load(provenance.BASE+'manifest.json'); report=load('run/python-baseline/report.json')
        need(sha(recording)==provenance.RECORDING_SHA and sha(report)==provenance.REPORT_SHA,
             'later capture raw authorities differ')
        joined=provenance.join(derived.parse(map_raw),derived.parse(recording),derived.parse(report),load)
        need(joined['derived_pairs']==668 and len(original)==1336,'derived response inventory differs')
        files=merge_derived(files,original,REPO)
    finally:
        sys.path.pop(0)
    return BoundInputs(REPO,files)
