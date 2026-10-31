#!/usr/bin/env python3
"""Bind the actual same-job consumer step. Keep all acceptance gates separate."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_typescript_input_consumer.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
PRODUCER = BASE + 'typescript_input_producer.py'
CONSUMER = BASE + 'typescript_input_consumer.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
BEFORE_PROFILE = BASE + 'inputs/recorded-before-profile-v1.json'
APPROVED19 = ('02cd9e79cdcedfef92816f67a3ccdf2e089964ab', '6953812ebe0f388cefe77ec90e8659a8f70d6e8ccf9517f6417e7a3b9b774c28')
ORIGINAL18 = ('1de8f05430a72f8d76f6159786cca71b0ae1a911', 'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf')


def need(value, message):
    if not value: raise ValueError(message)


def source_modules(repo, helper, candidate):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    need(Path(repo).absolute() == controls.root and helper == candidate == controls.helper_revision == controls.candidate_revision
         == os.environ.get('GITHUB_SHA') == os.environ.get('GITHUB_WORKFLOW_SHA'), 'actual native consumer source required')
    need(os.environ.get('GITHUB_JOB') == 'required-callbacks', 'actual same consumer job required')
    for role, path in (('runtime.context', CONTEXT), ('ts.transport.common', PRODUCER), ('ts.transport.consumer-data', CONSUMER)):
        need(controls.role_sources[role] == path, 'fixed consumer source role differs')
    context = controls.load_module('runtime.context')
    need(_SourceBytes is context.SourceBytes, 'fixed Source factory export identity differs')
    sources = _SourceBytes(controls.root, helper); sources.read(SELF)
    common = controls.load_module('ts.transport.common')
    data = controls.load_module('ts.transport.consumer-data')
    sources.read(PRODUCER); sources.read(CONSUMER); sources.verify()
    return controls, sources, common, data


def actual_base(data, sources, temporary):
    path = temporary / 'som-callback-context/context.json'
    raw = data.raw_file(path); base = data.parse(raw)
    need(base['version'] == 'som.full-product-context.v1' and base['qa_mode'] == 'native-qa-v1'
         and base['repo'] == str(sources.root) and base['helper_revision'] == base['candidate_revision'] == sources.revision,
         'actual run_full prepare-context-only output required')
    need(type(base.get('baseline_inputs')) is dict and base['baseline_inputs'],
         'Root must supply all actual baseline input roots in the original run_full base Context')
    gate = temporary / 'som-callback-raw/gate'
    protected = {'product_work': base['work_root'], 'candidate_dir': str(gate / 'candidate'), 'capture_out': str(gate / 'owner'),
        'assembly_root': base['assembly_root'], 'python_inputs_root': base['python_inputs_root'],
        'rust_inputs_root': base['rust_inputs_root'], 'carrier_root': base['carrier_root'], 'runtime_root': base['runtime_root'],
        'context_path': str(path), 'baseline_inputs': base['baseline_inputs'],
        'build_provenance_root': base.get('build_provenance_root'), 'current_companions_root': base.get('current_companions_root')}
    # Also derives and protects wheel/rust-tools/cargo/catalog from the real base.
    data._roots(sources.root, protected)
    return path, raw, base, protected


def trusted_catalog(common, data, sources):
    raw = os.environ.get('TS_INPUT_PRODUCER_CATALOG')
    need(type(raw) is str and raw, 'actual Root needs producer catalog required')
    catalog = data.parse(raw)
    workflow = sources.read('.github/workflows/rust-hosted-readiness.yml').decode()
    called = sources.read('.github/workflows/rust-same-candidate-products.yml').decode()
    need('typescript_input_producer_catalog: ${{ needs.typescript-regression-inputs.outputs.producer_catalog }}' in workflow
         and 'TS_INPUT_PRODUCER_CATALOG: ${{ inputs.typescript_input_producer_catalog }}' in called
         and 'id: typescript_input_consumer' in called
         and 'TS_INPUT_CONSUMER_PROOF: ${{ steps.typescript_input_consumer.outputs.consumer_proof }}' in called,
         'source-bound Root needs/with/input/step-output chain differs')
    profile_raw = sources.read(PROFILE)
    return data._catalog(catalog, data.sha(profile_raw), sources.revision)


def crypto_node(controls, sources, common, data, temporary, base):
    root = data.physical(base['runtime_root'], True)
    node = root / 'crypto-node/bin/node'
    if not node.exists():
        common.run_m6(controls, sources, 'derive', ['--pin', sources.root / common.PIN,
            '--out', root / 'crypto-node', '--metadata', temporary / 'som-ts-consumer-wrapper-raw/crypto-node.json'])
    data._node(sources, node)
    need(root in node.parents, 'crypto Node must be inside the protected runtime root')
    return node


def before_runtime(sources, data, temporary):
    raw = sources.read(BASE + 'product_runtime/runtime-bf3-qualification.json')
    qualification = data.parse(raw); root = data.physical(qualification['install_prefix'], True)
    need(temporary in root.parents, 'actual qualified runtime prefix must be in this runner')
    # This is the unchanged complete installed-runtime metadata verifier. It
    # neither starts a service nor constructs a synthetic Context.
    _BeforeRuntime.qualified_recorded_python(sources, {'runtime_root': str(root)})
    return {'runtime_root': str(root)}


def before_node(controls, sources, common, data, temporary):
    crypto_node(controls, sources, common, data, temporary, before_runtime(sources, data, temporary))


def restore_before19(controls, sources, common, data, temporary):
    """Restore all19 with V2; keep exact original18 metadata as a separate role."""
    import subprocess
    work = temporary / 'som-ts-before'
    node = crypto_node(controls, sources, common, data, temporary, before_runtime(sources, data, temporary))
    common.restore_original19(sources, data, node, work, work / 'approved19')
    metadata = work / 'original18-metadata'; metadata.mkdir(mode=0o700)
    def blob(name):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(sources.root),
            'cat-file', 'blob', ORIGINAL18[0] + ':' + name])
    raw = blob('inputs-index.json')
    need(data.sha(raw) == ORIGINAL18[1] and len(data.parse(raw)['packages']) == 18, 'distinct original18 index differs')
    data.write(metadata / 'inputs-index.json', raw)
    for number, package in enumerate(data.parse(raw)['packages']):
        need(package['manifest'] == f'package-{number:03d}.manifest.json', 'original18 manifest sequence differs')
        body = blob(package['manifest'])
        need(data.sha(body) == package['manifest_sha256'], 'original18 exact metadata differs')
        data.write(metadata / package['manifest'], body)


def stage_before(controls, sources, common, data, temporary):
    """Invoke only unchanged stage_inputs. Never run the old raw-exec main."""
    actual, _, admission = _BeforeGraph._admitted_controller_sources(sources.root, sources.revision, sources.revision)
    need(actual.root == sources.root and actual.revision == sources.revision
         and admission['source_authority_active'] is True, 'actual current admitted Graph source required before staging')
    for path in (BASE + 'stage_recorded_before_inputs.py', BASE + 'sealed_current_raw.py', BEFORE_PROFILE): actual.read(path)
    profile = data.parse(actual.read(BEFORE_PROFILE))
    need(set(profile['artifacts']) == {'original', 'calibration', 'fresh'}, 'all three historical data roles required')
    work = temporary / 'som-ts-before'; zips = work / 'original-zips'; zips.mkdir(mode=0o700)
    base = before_runtime(actual, data, temporary)
    node = crypto_node(controls, actual, common, data, temporary, base)
    for role, pin in sorted(profile['artifacts'].items()):
        _BeforeStager.artifact_metadata(pin, _BeforeGraph.artifact_metadata('faberline/som', pin['id']))
        count = common.download_zip(pin['id'], zips / (role + '.zip'), pin['digest'], data)
        need(count == pin['size_in_bytes'], 'whole historical original ZIP size differs')
    result = _BeforeStager.stage_inputs(actual, _BeforeGraph, _BeforeTransport, BEFORE_PROFILE,
        {role: zips / (role + '.zip') for role in profile['artifacts']}, work / 'original18-metadata',
        work / 'approved19', node, temporary / 'som-recorded-before-inputs', work / 'stage-evidence')
    need(result['complete'] is True and result['product_acceptance_passed'] is False, 'actual before stage did not complete')
    actual.verify(); sources.verify(); controls.verify_installed()


def provision(controls, sources, common, data, temporary):
    path, raw, base, protected = actual_base(data, sources, temporary)
    catalog = trusted_catalog(common, data, sources)
    # No pending item is erased. The unchanged admitted API must accept the
    # actual source profile before any decrypt or consumer container creation.
    data._profile(sources.read(PROFILE))
    node = crypto_node(controls, sources, common, data, temporary, base)
    download = temporary / 'som-ts-consumer-download'; download.mkdir(mode=0o700)
    archive = download / 'artifact.zip'
    common.download_zip(catalog['artifact_id'], archive, catalog['artifact_digest'], data)
    result = data.provision_inputs(sources, helper_revision=sources.revision, candidate_revision=sources.revision,
        producer_catalog=catalog, archive=archive, node=node, protected_roots=protected)
    need(data.raw_file(path) == raw and result['product_acceptance_passed'] is False, 'consumer changed the original base Context')
    # API itself emits consumer_proof to this actual GITHUB_OUTPUT. Its return
    # is not substituted for the next step's trusted output.
    data.dump(temporary / 'som-ts-consumer-wrapper-raw/provision.json', {'version': 'som.ts-consumer-call.v1',
        'receipt_sha256': result['consumer_proof']['receipt_sha256'], 'product_acceptance_passed': False})


def trusted_proof(data):
    need('M6_INPUTS_KEYRING' not in os.environ and 'M6_INPUTS_KEY' not in os.environ, 'keyless step must receive neither M6 input key')
    raw = os.environ.get('TS_INPUT_CONSUMER_PROOF')
    need(type(raw) is str and raw, 'actual preceding consumer step output required')
    return data.parse(raw)


def bind_context(sources, common, data, temporary):
    path, raw, base, protected = actual_base(data, sources, temporary)
    catalog = trusted_catalog(common, data, sources); proof = trusted_proof(data)
    root = temporary / data.ROOT_NAME
    need(data.raw_file(root / 'evidence/base-context.json') == raw
         and proof == data.parse(data.raw_file(root / 'consumer-step-proof.json')), 'actual base or trusted step output differs')
    inputs = data.parse(data.raw_file(root / 'context-inputs.json'))
    added = {'typescript_regression_inputs': inputs, 'typescript_regression_input_producer': catalog,
             'typescript_regression_input_consumer': proof}
    need(not set(added) & set(base), 'base Context already contains TS consumer bindings')
    replacement = {**base, **added}
    data._context_delta(base, replacement, inputs, catalog, proof)
    info = path.stat(); staging = path.with_name('.context-ts-binding.tmp')
    primary = None
    try:
        data.write(staging, data.canonical(replacement) + b'\n', stat.S_IMODE(info.st_mode))
        need(data.raw_file(path) == raw, 'original Context changed before exact three-field binding')
        os.replace(staging, path)
    except BaseException as error:
        primary = error; raise
    finally:
        if staging.exists():
            try: staging.unlink()
            except BaseException as error:
                if primary is None: raise
                primary.__som_ts_context_binding_cleanup__ = type(error).__name__
    report = data.verify_after_close(sources, helper_revision=sources.revision, candidate_revision=sources.revision,
        context_inputs=inputs, producer_catalog=catalog, trusted_consumer_proof=proof, protected_roots=protected)
    data.dump(temporary / 'som-ts-consumer-wrapper-raw/pre-product-keyless.json', report)


def postclose(sources, common, data, temporary):
    _, _, base, protected = actual_base(data, sources, temporary)
    catalog = trusted_catalog(common, data, sources); proof = trusted_proof(data)
    need(base['typescript_regression_input_producer'] == catalog and base['typescript_regression_input_consumer'] == proof,
         'actual Context does not contain trusted Root outputs')
    report = data.verify_after_close(sources, helper_revision=sources.revision, candidate_revision=sources.revision,
        context_inputs=base['typescript_regression_inputs'], producer_catalog=catalog,
        trusted_consumer_proof=proof, protected_roots=protected)
    data.dump(temporary / 'som-ts-consumer-wrapper-raw/post-product-keyless.json', report)


def retention_inventory(roots, data):
    result = {}
    for label, root in roots.items():
        need(not root.is_symlink(), 'whole retention root cannot be an alias')
        if not root.exists():
            result[label] = {'type': 'absent'}; continue
        data.physical(root, True)
        def onerror(error): raise error
        for directory, children, leaves in os.walk(root, followlinks=False, onerror=onerror):
            folder = Path(directory); relative = folder.relative_to(root).as_posix()
            result[label + '/' + relative] = {'type': 'directory', 'mode': stat.S_IMODE(folder.stat().st_mode)}
            for name in sorted(children + leaves):
                path = folder / name; info = path.lstat(); key = label + '/' + path.relative_to(root).as_posix()
                if stat.S_ISLNK(info.st_mode):
                    result[key] = {'type': 'opaque-alias-data', 'mode': stat.S_IMODE(info.st_mode), 'target': os.readlink(path)}
                elif stat.S_ISREG(info.st_mode):
                    digest, size = data.hash_file(path)
                    result[key] = {'type': 'regular', 'mode': stat.S_IMODE(info.st_mode), 'sha256': digest, 'size': size}
                else: need(stat.S_ISDIR(info.st_mode), 'unknown special raw member prevents complete retention')
    return result


def retain(controls, sources, common, data, temporary):
    """Retain all regular bytes and aliases as inert data; grant no archive view."""
    # A failed prepare can leave real private baseline data but no Context.
    # Retention derives its tool from the actual qualified runtime, not a fake
    # base JSON or a product pass object.
    node = crypto_node(controls, sources, common, data, temporary, before_runtime(sources, data, temporary))
    roots = {'consumer': temporary / data.ROOT_NAME, 'caller': temporary / 'som-ts-consumer-wrapper-raw',
             'before-inputs': temporary / 'som-recorded-before-inputs', 'before-staging': temporary / 'som-ts-before'}
    before = retention_inventory(roots, data)
    work = temporary / 'som-ts-consumer-retention'; work.mkdir(mode=0o700)
    packet = work / 'packet'; (packet / 'run/chunks').mkdir(parents=True, mode=0o700)
    archive = work / 'archive.tar'; slots = {}; ordinal = 0
    with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as tar:
        for name, row in sorted(before.items()):
            if row['type'] != 'regular': continue
            label, relative = name.split('/', 1); path = roots[label] / relative
            slot = f'files/{ordinal:06d}.raw'; ordinal += 1; slots[name] = slot
            info = tarfile.TarInfo(slot); info.size = row['size']; info.mode = 0o600; info.mtime = 0
            with path.open('rb') as stream: tar.addfile(info, stream)
            need(data.hash_file(path) == (row['sha256'], row['size']), 'retained regular bytes changed while archiving')
        manifest = data.canonical({'version': 'som.ts-private-raw-flat-archive.v1', 'nodes': before, 'file_slots': slots,
            'aliases': 'opaque metadata only; never instantiate or dereference in archive consumers',
            'authenticated_archive_view_admitted': False, 'product_acceptance_passed': False})
        info = tarfile.TarInfo('manifest.json'); info.size = len(manifest); info.mode = 0o600; info.mtime = 0
        tar.addfile(info, io.BytesIO(manifest))
    need(retention_inventory(roots, data) == before, 'whole consumer/caller raw universe changed during retention')
    chunks = []; whole = hashlib.sha256(); total = 0
    with archive.open('rb') as stream:
        while body := stream.read(32 * 1024**2):
            name = f'{len(chunks):06d}.part'; data.write(packet / 'run/chunks' / name, body)
            whole.update(body); total += len(body); chunks.append({'name': name, 'sha256': data.sha(body), 'bytes': len(body)})
    data.dump(packet / 'run/transport.json', {'version': 'som.ts-private-raw-ordered-chunks.v1',
        'archive_sha256': whole.hexdigest(), 'archive_bytes': total, 'ordered_chunks': chunks,
        'source_inventory_sha256': data.sha(data.canonical(before)), 'alias_instantiation': False,
        'authenticated_archive_view_admitted': False, 'product_acceptance_passed': False})
    common.seal_packet(sources, data, node, packet, temporary / 'som-ts-consumer-retained-sealed', work / 'seal-command')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True); parser.add_argument('--candidate-revision', required=True)
    parser.add_argument('operation', choices=('before-node', 'restore-before19', 'stage-before', 'provision', 'bind-context', 'postclose', 'retain'))
    args = parser.parse_args(argv)
    controls, sources, common, data = source_modules(args.repo, args.helper_revision, args.candidate_revision)
    temporary = data.physical(os.environ['RUNNER_TEMP'], True)
    raw = temporary / 'som-ts-consumer-wrapper-raw'; raw.mkdir(mode=0o700, exist_ok=True)
    if args.operation == 'before-node': before_node(controls, sources, common, data, temporary)
    elif args.operation == 'restore-before19': restore_before19(controls, sources, common, data, temporary)
    elif args.operation == 'stage-before': stage_before(controls, sources, common, data, temporary)
    elif args.operation == 'provision': provision(controls, sources, common, data, temporary)
    elif args.operation == 'bind-context': bind_context(sources, common, data, temporary)
    elif args.operation == 'postclose': postclose(sources, common, data, temporary)
    else: retain(controls, sources, common, data, temporary)
    sources.verify(); controls.verify_installed()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
