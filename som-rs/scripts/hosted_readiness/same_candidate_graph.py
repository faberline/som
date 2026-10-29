"""Same-run source and artifact binding. No stage result grants acceptance."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType
from urllib.request import HTTPRedirectHandler, Request, build_opener

SELF = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
AUTHORITY = 'som-rs/scripts/hosted_readiness/native_qa_authority.py'
STAGES = (('python-quality', 8, 671), ('phase1', 1, 115),
          ('phase2-python', 8, 584), ('phase2-rust', 16, 584))
ARTIFACTS = {'public_inputs', 'assembly', 'carrier', 'wheel', 'cargo', 'runtime', 'rust_tools'}
CALLBACKS = {'current-python-companions', 'cnl', 'cli-refusals-rebase',
             'named-contract-acceptance', 'typescript-regression', 'm6-react003'}
F59 = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'


def require(value, message):
    if not value: raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(rows):
        result = {}
        for name, value in rows:
            require(name not in result, 'duplicate JSON key')
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def relative(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(part not in {'', '.', '..'} for part in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'unsafe source name')
    return name


def read(path, maximum=64 * 1024**2):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked data path')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum, 'regular bounded data required')
    return path.read_bytes()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream: stream.write(canonical(value) + b'\n')


def pair(helper, candidate):
    require(type(helper) is str and re.fullmatch('[0-9a-f]{40}', helper)
            and helper == candidate and candidate != F59, 'new exact same-source helper/candidate required')


class Sources:
    def __init__(self, root, revision):
        self.root, self.revision, self.pins = Path(root).absolute(), revision, {}
        require(re.fullmatch('[0-9a-f]{40}', revision), 'full source commit required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision, 'source HEAD differs')
        tree = self.git('ls-tree', '-r', '-z', revision)
        self.entries = {}
        for entry in tree.split(b'\0'):
            if not entry: continue
            fields, tab, raw_name = entry.partition(b'\t')
            mode, kind, oid = fields.split()
            name = relative(raw_name.decode('utf-8', 'strict'))
            require(tab and name not in self.entries, 'ambiguous Git tree entry')
            self.entries[name] = (mode, kind, oid)
        self.paths = sorted(self.entries)

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
                                       stderr=subprocess.DEVNULL)

    def read(self, name):
        name = relative(name)
        mode, kind, oid = self.entries[name]
        require(mode in {b'100644', b'100755'} and kind == b'blob', 'regular Git source required')
        body = self.git('cat-file', 'blob', oid.decode())
        require(len(body) <= 32 * 1024**2 and body == read(self.root / name), 'working source differs from exact Git blob')
        self.pins[name] = sha(body)
        return body

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'source HEAD changed')
        for name, pin in list(self.pins.items()): require(sha(self.read(name)) == pin, 'source bytes changed')


def raw_module(sources, name, path):
    body = sources.read(path)
    if name in sys.modules:
        module = sys.modules[name]
        require(getattr(module, '__file__', None) == str(sources.root / path)
                and getattr(module, '__som_raw_sha256__', None) == sha(body), 'preloaded helper source differs')
        return module
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(sources.root / path), None, ''
    module.__som_raw_sha256__ = sha(body)
    sys.modules[name] = module
    try: exec(compile(body, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def admit_source(repo, helper, candidate):
    pair(helper, candidate)
    workflow_binding = source_workflow_binding(candidate)
    sources = Sources(repo, helper)
    workflow_raw = sources.read('.github/workflows/rust-hosted-readiness.yml')
    workflow_binding['workflow_source_sha256'] = sha(workflow_raw)
    sources.workflow_binding = workflow_binding
    authority = raw_module(sources, '_som_same_candidate_native_authority', AUTHORITY)
    result = authority.verify(sources.read, mode='native-qa-v1', source_paths=sources.paths,
                              helper_revision=helper, candidate_revision=candidate)
    require(result.get('source_authority_active') is True and result.get('mode') == 'native-qa-v1'
            and result.get('helper_revision') == helper and result.get('candidate_revision') == candidate
            and result.get('product_acceptance_passed') is False
            and result.get('compiler_acceptance_passed') is False
            and result.get('whole_acceptance_complete') is False, 'sealed native source admission failed')
    sources.verify()
    return sources, authority, result


def source_workflow_binding(candidate, environ=None, reader=None):
    """Use GitHub's actual workflow source identity. Never replace merge SHA metadata."""
    environ = os.environ if environ is None else environ
    reader = github_json if reader is None else reader
    require(environ.get('GITHUB_ACTIONS') == 'true' and environ.get('GITHUB_EVENT_NAME') in {'push', 'workflow_dispatch'}
            and environ.get('GITHUB_SHA') == environ.get('GITHUB_WORKFLOW_SHA') == candidate,
            'formal graph requires actual push/dispatch source SHA; PR merge identity cannot be overridden')
    run_id, attempt = int(environ['GITHUB_RUN_ID']), int(environ['GITHUB_RUN_ATTEMPT'])
    repository = environ['GITHUB_REPOSITORY']
    require(run_id > 0 and attempt > 0, 'actual workflow run and attempt required')
    record = reader(repository, '/actions/runs/' + str(run_id))
    require(record['id'] == run_id and record['head_sha'] == candidate and record['run_attempt'] == attempt
            and record['event'] == environ['GITHUB_EVENT_NAME']
            and record['repository']['full_name'] == repository
            and record['path'].split('@', 1)[0] == '.github/workflows/rust-hosted-readiness.yml',
            'live GitHub run source/event/workflow differs from current candidate')
    return {'version': 'som.actual-workflow-source-binding.v1', 'repository': repository, 'run_id': run_id,
            'run_attempt': attempt, 'candidate_revision': candidate, 'event': record['event'],
            'workflow_id': record['workflow_id'], 'workflow_path': record['path'],
            'github_sha': environ['GITHUB_SHA'], 'github_workflow_sha': environ['GITHUB_WORKFLOW_SHA'],
            'live_run_record_canonical_sha256': sha(canonical(record)), 'live_run_record': record,
            'controller_independent_source_review': 'external prerequisite'}


def matrix():
    rows = [{'stage': stage, 'index': index, 'count': count} for stage, count, _ in STAGES for index in range(count)]
    require(len(rows) == 33, 'whole product shard plan differs')
    return {'include': rows}


def receipt_paths(root):
    root = Path(root)
    plan = matrix()['include']
    names = ['full-product-' + row['stage'] + '-' + str(row['index']) for row in plan]
    require(root.is_dir() and {p.name for p in root.iterdir()} == set(names), 'missing, extra or duplicate product shard artifact')
    paths = [root / name / 'som-full-product-run/result.json' for name in names]
    for path, expected in zip(paths, plan):
        receipt = parse(read(path))
        require(receipt['stage'] == expected['stage']
                and receipt['shard'] == {'index': expected['index'], 'count': expected['count']},
                'artifact name and exact shared shard role differ')
    return paths


def github_json(repository, suffix):
    require(re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            and re.fullmatch(r'/actions/(artifacts/[0-9]+|runs/[0-9]+(?:/artifacts\?per_page=100&page=[0-9]+)?)', suffix),
            'bounded exact GitHub artifact API path required')
    request = Request('https://api.github.com/repos/' + repository + suffix,
                      headers={'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                               'User-Agent': 'som-same-candidate-artifact-admission',
                               'Authorization': 'Bearer ' + os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN']})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError('artifact metadata redirects are refused')
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        require(response.status == 200, 'artifact API HTTP status differs')
        raw = response.read(1024**2 + 1)
    require(len(raw) <= 1024**2, 'artifact API record exceeds bound')
    return parse(raw)


def artifact_metadata(repository, artifact_id):
    require(type(artifact_id) is int and artifact_id > 0, 'exact artifact id required')
    return github_json(repository, '/actions/artifacts/' + str(artifact_id))


def required_stage_artifacts(kind):
    if kind == 'products':
        return {'full-product-' + r['stage'] + '-' + str(r['index']): 'full-product-' + r['stage'] + '-' + str(r['index'])
                for r in matrix()['include']}
    if kind == 'callbacks':
        return {'required-same-candidate-' + stage: stage for stage in sorted(CALLBACKS - {'current-python-companions'})}
    require(kind == 'product-join', 'unknown exact encrypted stage set')
    return {'same-candidate-product-join': 'full-product-join'}


def stage_artifact_origins(kind, repository, run_id, candidate, reader=github_json):
    expected = required_stage_artifacts(kind)
    observed, total = {}, None
    for page in range(1, 101):
        document = reader(repository, '/actions/runs/' + str(run_id) + '/artifacts?per_page=100&page=' + str(page))
        require(type(document['total_count']) is int and document['total_count'] <= 10000
                and (total is None or total == document['total_count']), 'artifact pagination count changed')
        total = document['total_count']; rows = document['artifacts']
        require(type(rows) is list and len(rows) <= 100, 'bounded artifact API page required')
        for row in rows:
            if row['name'] not in expected: continue
            require(row['name'] not in observed and row['expired'] is False
                    and row['workflow_run']['id'] == run_id and row['workflow_run']['head_sha'] == candidate
                    and re.fullmatch('sha256:[0-9a-f]{64}', row['digest']), 'duplicate, stale or foreign raw stage artifact')
            observed[row['name']] = row
        if page * 100 >= total: break
    require(set(observed) == set(expected), 'missing required encrypted raw stage artifact')
    return observed


def restore_stage_set(sources, kind, packet_roots, out, evidence_out, node, helper, candidate):
    """Bind each immutable same-run job output packet to the live artifact API."""
    pair(helper, candidate)
    plan = required_stage_artifacts(kind)
    require({p.name for p in packet_roots.iterdir()} == set(plan), 'downloaded encrypted packet universe differs')
    origins = stage_artifact_origins(kind, os.environ['GITHUB_REPOSITORY'], int(os.environ['GITHUB_RUN_ID']), candidate)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(all(path.is_absolute() and temporary in path.parents and not path.exists() for path in (out, evidence_out))
            and out != evidence_out and out not in evidence_out.parents and evidence_out not in out.parents,
            'new separate stage restore/evidence roots required')
    out.mkdir(mode=0o700); evidence_out.mkdir(mode=0o700)
    transport = raw_module(sources, '_som_graph_sealed_raw_stage_data',
                           'som-rs/scripts/hosted_readiness/sealed_current_raw.py')
    rows = []
    for name, stage in plan.items():
        origin = origins[name]
        packet = packet_roots / name
        # This safe receipt is the immutable same-job output. The exact artifact ID/digest,
        # complete encrypted index and AEAD-bound run/stage/source are rechecked by restore.
        output_raw = read(packet / 'receipt.json'); output = parse(output_raw)
        transport.main(['restore', '--repo', str(sources.root), '--helper-revision', helper, '--candidate-revision', candidate,
            '--node', str(node), '--stage', stage, '--packet-root', str(packet), '--out', str(out / name),
            '--evidence-out', str(evidence_out / name), '--index-sha256', output['index_sha256'],
            '--artifact-id', str(origin['id']), '--artifact-digest', origin['digest'], '--artifact-name', name])
        rows.append({'name': name, 'stage': stage, 'artifact_id': origin['id'], 'artifact_digest': origin['digest'],
                     'job_output_receipt_sha256': sha(output_raw), 'sealed_index_sha256': output['index_sha256']})
    sources.verify()
    dump(evidence_out / 'stage-set.json', {'version': 'som.same-run-sealed-stage-set.v1', 'kind': kind,
         'helper_revision': helper, 'candidate_revision': candidate, 'rows': rows, 'product_acceptance_passed': False})
    return rows


def validate_artifacts(rows, helper, candidate, run_id, repository, reader=artifact_metadata):
    pair(helper, candidate)
    require(set(rows) == ARTIFACTS and len({row['id'] for row in rows.values()}) == len(ARTIFACTS),
            'complete distinct same-run artifact ID set required')
    checked = {}
    for role, row in rows.items():
        require(type(row) is dict and set(row) == {'id', 'name', 'digest'}
                and re.fullmatch('sha256:[0-9a-f]{64}', row['digest']), 'artifact id name and ZIP digest required')
        current = reader(repository, row['id'])
        require(current['id'] == row['id'] and current['name'] == row['name']
                and current['digest'] == row['digest'] and current.get('expired') is False
                and current['workflow_run']['id'] == run_id
                and current['workflow_run']['head_sha'] == candidate, 'artifact is stale, foreign, expired or from another candidate/run')
        checked[role] = current
    return {'version': 'som.same-candidate-artifact-inputs.v1', 'helper_revision': helper,
            'candidate_revision': candidate, 'repository': repository, 'run_id': run_id,
            'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']), 'artifacts': rows,
            'github_metadata': checked, 'product_acceptance_passed': False}


def verify_compile_proof(root, candidate):
    compiled_raw, assembled_raw = read(Path(root) / 'manifest.json'), read(Path(root) / 'assembly/manifest.json')
    compiled, assembled = parse(compiled_raw), parse(assembled_raw)
    require(compiled['tested_sha'] == assembled['tested_sha'] == candidate
            and compiled['experiment_complete'] is True and compiled['passed'] == 2041
            and compiled['expected_rows'] == compiled['actual_rows'] == {'python': 1342, 'rust': 699}
            and len(compiled['rows']) == 2041
            and [row['index'] for row in compiled['rows']] == list(range(2041))
            and all(row['state'] == 'passed' and row['http_status'] == row['expected_http_status']
                    for row in compiled['rows'])
            and compiled['identity_stability_passed'] is True
            and compiled['initial_identities'] == compiled['final_identities']
            and assembled['assembly_experiment_complete'] is True
            and compiled['product_acceptance_passed'] is assembled['product_acceptance_passed'] is False
            and compiled['cleanup']['cleanup_passed'] is assembled['owned_cleanup']['cleanup_passed'] is True
            and compiled['cleanup']['remaining_owned_session_members'] == assembled['owned_cleanup']['remaining_owned_session_members'] == []
            and assembled['checked_input_stability_passed'] is assembled['saved_observation_stability_passed']
                is assembled['identity_stability_passed'] is True
            and assembled['expected_observations'] == assembled['actual_observations']
                == {'python_default': 1342, 'rust_default': 697, 'python_checked': 6}
            and assembled['expected_products'] == assembled['actual_products'] == assembled['product_slots_passed']
                == {'python': 671, 'rust': 697}
            and assembled['observations_passed'] == 2045
            and assembled['compile_stage']['rows'] == assembled['compile_stage']['passed'] == 2041,
            'complete current same-job compile/assembly proof required')
    observations = assembled['observations']
    require(len(observations) == 2045 and [row['observation_index'] for row in observations] == list(range(2045))
            and all(row['state'] == 'passed' and row['http_status'] == 200
                    and row['frozen_product_files_passed'] is True for row in observations),
            'complete 2045 assembly observations required')
    return {'compile_manifest_sha256': sha(compiled_raw), 'assembly_manifest_sha256': sha(assembled_raw),
            'binary_identity': compiled['initial_identities']['binary']}


def restore_current_carrier(sources, catalog_path, assembly_root, carrier_root, out, helper, candidate):
    pair(helper, candidate)
    catalog = parse(read(catalog_path))
    require(catalog['version'] == 'som.same-candidate-artifact-inputs.v1'
            and catalog['helper_revision'] == helper and catalog['candidate_revision'] == candidate
            and catalog['run_id'] == int(os.environ['GITHUB_RUN_ID'])
            and catalog['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
            and catalog['repository'] == os.environ['GITHUB_REPOSITORY']
            and catalog['product_acceptance_passed'] is False, 'exact current-run artifact catalog required')
    # Recheck live artifact identity, rather than trusting a catalog boolean.
    again = validate_artifacts(catalog['artifacts'], helper, candidate, catalog['run_id'], catalog['repository'])
    require(again['artifacts'] == catalog['artifacts'], 'same-run artifact identities changed')
    proof = verify_compile_proof(assembly_root, candidate)
    carrier_raw = read(Path(carrier_root) / 'carrier.json')
    carrier = parse(carrier_raw)
    require(carrier['candidate_revision'] == candidate and carrier['compiler_rebuilt'] is False
            and carrier['origin_role'] == 'original already tested candidate bytes'
            and carrier['github']['GITHUB_RUN_ID'] == str(catalog['run_id'])
            and carrier['github']['GITHUB_RUN_ATTEMPT'] == str(catalog['run_attempt'])
            and carrier['compile_manifest_sha256'] == proof['compile_manifest_sha256'],
            'fresh same-job exact built carrier required; historical or rebuilt candidate refused')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(out.is_absolute() and temporary in out.parents and not out.exists(), 'new owned carrier restore root required')
    module = raw_module(sources, '_som_same_candidate_carrier_data',
                        'som-rs/scripts/hosted_readiness/product_runtime/candidate_carrier.py')
    restored = module.restore(Path(carrier_root), out, Path(assembly_root) / 'manifest.json', candidate)
    require(module.identities(out) == restored['identity']
            and restored['identity']['binary']['sha256'] == proof['binary_identity']['sha256'],
            'restored current binary/assets/tools byte smoke failed before execution')
    sources.verify()
    return {'version': 'som.same-job-current-carrier-restored.v1', 'helper_revision': helper,
            'candidate_revision': candidate, 'carrier_json_sha256': sha(carrier_raw),
            'compile_proof': proof, 'restored_identity': restored['identity'],
            'restored_root': str(out), 'data_only_restore_complete': True,
            'native_code_executed': False, 'product_acceptance_passed': False}


def verify_callback(sources, root, stage, helper, candidate, proof_roots):
    """Use each owning raw-data verifier. Summary booleans never admit a stage."""
    pair(helper, candidate)
    require(stage in CALLBACKS and set(proof_roots) == {'assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'},
            'complete graph-chosen callback proof roots required')
    verify_compile_proof(proof_roots['assembly_root'], candidate)
    if stage in {'cnl', 'cli-refusals-rebase'}:
        module = raw_module(sources, '_som_graph_semantic_gate_data',
                            'som-rs/scripts/hosted_readiness/run_candidate_semantic_gates.py')
        checked = module.verify_result(root, sources.read, helper, candidate, stage,
                                       repo=sources.root, proof_roots=proof_roots)
        manifest = Path(root) / 'result.json'
    elif stage == 'named-contract-acceptance':
        module = raw_module(sources, '_som_graph_named_contract_data',
                            'som-rs/scripts/hosted_readiness/run_same_candidate_contracts.py')
        checked = module.verify_result(root, sources.read, helper, candidate, stage,
                                       repo=sources.root, proof_roots=proof_roots)
        manifest = Path(root) / 'result.json'
    elif stage == 'current-python-companions':
        guard = raw_module(sources, '_som_graph_current_candidate_admission',
                           'som-rs/scripts/hosted_readiness/dependency_candidate.py')
        admission = guard.verify_candidate_inputs(sources.root, helper, candidate, proof_roots['assembly_root'])
        module = raw_module(sources, '_som_graph_current_companion_data',
                            'som-rs/scripts/hosted_readiness/run_current_python_companions.py')
        checked = module.verify_capture(root, sources.read, helper, candidate, admission,
                    proof_roots['assembly_root'], qa_mode='native-qa-v1', source_paths=sources.paths)
        manifest = Path(root) / 'manifest.json'
    else:
        module = raw_module(sources, '_som_graph_current_candidate_gate_data',
                            'som-rs/scripts/hosted_readiness/run_current_candidate_gate.py')
        checked = module.verify_result(root, sources.read, helper, candidate, stage,
                                       repo=sources.root, proof_roots=proof_roots)
        manifest = Path(root) / 'result.json'
    require(type(checked) in {dict, list} and checked, 'owning raw callback verifier returned no checked data')
    sources.verify()
    return {'version': 'som.same-candidate-owning-callback-data-check.v1', 'stage': stage,
            'helper_revision': helper, 'candidate_revision': candidate,
            'raw_manifest_sha256': sha(read(manifest)), 'owning_data_verification_complete': True,
            'bound_source_sha256': dict(sorted(sources.pins.items())),
            'product_acceptance_passed': False, 'whole_acceptance_complete': False}


def join_graph(sources, callbacks_root, current_root, product_join, helper, candidate, proof_roots):
    pair(helper, candidate)
    expected = required_stage_artifacts('callbacks')
    require({p.name for p in callbacks_root.iterdir()} == set(expected), 'missing or extra required final gate')
    joined_raw = read(product_join); joined = parse(joined_raw)
    require(joined['version'] == 'som.full-product-preparation-join.v1'
            and joined['identities']['helper_revision'] == helper == joined['identities']['candidate_revision'] == candidate
            and joined['identities']['qa_mode'] == 'native-qa-v1'
            and joined['whole_named_product_cohorts_joined'] is joined['all_preparation_gates_passed'] is True
            and joined['product_acceptance_passed'] is joined['whole_acceptance_complete'] is False,
            'whole current 33-shard product join failed or belongs to another candidate')
    require(set(joined['cohorts']) == {stage for stage, _, _ in STAGES}, 'joined product cohort scope differs')
    for stage, _, count in STAGES:
        row = joined['cohorts'][stage]
        require(row['total'] == row['run'] == row['passed'] == count and row['failed'] == [], 'incomplete whole product cohort')
    checked = [verify_callback(sources, current_root, 'current-python-companions', helper, candidate, proof_roots)]
    for name, stage in expected.items():
        checked.append(verify_callback(sources, callbacks_root / name / 'gate', stage, helper, candidate, proof_roots))
    sources.verify()
    return {'version': 'som.same-candidate-whole-graph-data-join.v1', 'helper_revision': helper, 'candidate_revision': candidate,
            'product_join_raw_sha256': sha(joined_raw), 'required_callback_scope': sorted(CALLBACKS),
            'callbacks': checked, 'same_candidate_graph_data_join_complete': True,
            'product_acceptance_passed': False, 'compiler_acceptance_passed': False, 'whole_acceptance_complete': False,
            'remaining_acceptance_authority': 'controller independent complete raw-data/source/runtime audit'}


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux execution only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('matrix', 'receipt-paths', 'admit', 'artifact-inputs', 'restore-carrier', 'verify-callback', 'restore-stage-set', 'join-graph'))
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--helper-revision')
    parser.add_argument('--candidate-revision')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--artifact-spec', type=Path)
    parser.add_argument('--artifact-inputs', type=Path)
    parser.add_argument('--assembly-root', type=Path)
    parser.add_argument('--python-inputs-root', type=Path)
    parser.add_argument('--rust-inputs-root', type=Path)
    parser.add_argument('--carrier-root', type=Path)
    parser.add_argument('--stage', choices=sorted(CALLBACKS))
    parser.add_argument('--set', dest='stage_set', choices=('products', 'callbacks', 'product-join'))
    parser.add_argument('--packet-roots', type=Path)
    parser.add_argument('--evidence-out', type=Path)
    parser.add_argument('--node', type=Path)
    parser.add_argument('--current-companions-root', type=Path)
    parser.add_argument('--product-join', type=Path)
    args = parser.parse_args()
    if args.operation == 'matrix':
        value = canonical(matrix()).decode()
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream: stream.write('matrix=' + value + '\n')
    elif args.operation == 'receipt-paths':
        for path in receipt_paths(args.root): sys.stdout.buffer.write(str(path).encode() + b'\0')
    elif args.operation == 'admit':
        sources, _, record = admit_source(args.repo, args.helper_revision, args.candidate_revision)
        sources.verify(); dump(args.out, {'native_source_authority': record, 'workflow_binding': sources.workflow_binding,
                                         'product_acceptance_passed': False, 'whole_acceptance_complete': False})
    elif args.operation in {'restore-carrier', 'verify-callback', 'restore-stage-set', 'join-graph'}:
        sources, _, authority = admit_source(args.repo, args.helper_revision, args.candidate_revision)
        if args.operation == 'restore-stage-set':
            restore_stage_set(sources, args.stage_set, args.packet_roots, args.out, args.evidence_out, args.node,
                              args.helper_revision, args.candidate_revision)
        elif args.operation == 'restore-carrier':
            record = restore_current_carrier(sources, args.artifact_inputs, args.assembly_root, args.carrier_root,
                                             args.out, args.helper_revision, args.candidate_revision)
            dump(args.out.parent / (args.out.name + '-restore.json'), record)
        else:
            catalog = parse(read(args.artifact_inputs))
            require(catalog['helper_revision'] == args.helper_revision and catalog['candidate_revision'] == args.candidate_revision
                    and catalog['run_id'] == int(os.environ['GITHUB_RUN_ID'])
                    and catalog['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
                    and catalog['repository'] == os.environ['GITHUB_REPOSITORY'], 'trusted same-run graph catalog required')
            validate_artifacts(catalog['artifacts'], args.helper_revision, args.candidate_revision,
                               catalog['run_id'], catalog['repository'])
            roots = {name: getattr(args, name) for name in
                     ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root')}
            require(all(value is not None for value in roots.values()), 'four graph proof roots are required')
            if args.operation == 'join-graph':
                record = join_graph(sources, args.root, args.current_companions_root, args.product_join,
                                    args.helper_revision, args.candidate_revision, roots)
            else:
                record = verify_callback(sources, args.root, args.stage, args.helper_revision, args.candidate_revision, roots)
            dump(args.out, record)
    else:
        pair(args.helper_revision, args.candidate_revision)
        record = validate_artifacts(parse(read(args.artifact_spec)), args.helper_revision, args.candidate_revision,
                                    int(os.environ['GITHUB_RUN_ID']), os.environ['GITHUB_REPOSITORY'])
        dump(args.out, record)


if __name__ == '__main__': main()
