"""Retain complete current raw evidence through the existing M6 sealed transport.

This transports data. It does not admit a runtime or grant product acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType

SELF = 'som-rs/scripts/hosted_readiness/sealed_current_raw.py'
GRAPH = 'som-rs/scripts/hosted_readiness/same_candidate_graph.py'
M6 = 'som-rs/scripts/react_output_acceptance/'
SCRIPTS = (M6 + 'seal_hosted_evidence.mjs', M6 + 'restore_hosted_evidence.mjs', M6 + 'sealed_inputs.mjs')
CHUNK = 32 * 1024**2
MAX_FILE = 320 * 1024**2
MAX_TOTAL = 8 * 1024**3
MAX_MEMBERS = 200000
STAGES = {'current-python-companions', 'cnl', 'cli-refusals-rebase',
          'named-contract-acceptance', 'typescript-regression', 'm6-react003', 'full-product-join', 'whole-graph-join'}


def product_shard(stage):
    return type(stage) is str and re.fullmatch(r'full-product-(python-quality|phase1|phase2-python|phase2-rust)-[0-9]+', stage) is not None


def stage_policy(record, stage):
    require(stage in STAGES or product_shard(stage), 'named affected raw evidence stage required')
    require(record['opaque_aliases_allowed'] is product_shard(stage), 'opaque aliases only belong to registered product shards')


def require(value, message):
    if not value: raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate raw transport JSON key'); result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite raw transport JSON')))


def relative(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(p not in {'', '.', '..'} for p in name.split('/'))
            and all(32 <= ord(c) != 127 for c in name), 'unsafe evidence member')
    return name


def physical(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked evidence path')
    return path


def owned_new(path):
    path = physical(path)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(temporary in path.parents and not path.exists() and path.parent.is_dir(), 'fresh owned evidence output required')
    return path


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def hash_file(path, maximum=MAX_FILE, single_link=True):
    path = physical(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink >= 1
            and (not single_link or before.st_nlink == 1) and before.st_size <= maximum,
            'bounded regular evidence with exact link policy required')
    value = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        require(identity(before) == identity(os.fstat(stream.fileno())), 'opened evidence identity differs')
        count = 0
        while body := stream.read(1024**2):
            count += len(body); require(count <= maximum, 'evidence exceeds bound'); value.update(body)
        require(count == before.st_size and identity(before) == identity(os.fstat(stream.fileno())), 'evidence changed during read')
    require(identity(before) == identity(path.lstat()), 'evidence path changed during read')
    return value.hexdigest(), before


def read_file(path, maximum=MAX_FILE):
    pin, info = hash_file(path, maximum)
    body = path.read_bytes()
    require(len(body) == info.st_size and sha(body) == pin and identity(path.lstat()) == identity(info),
            'raw transport data changed during checked read')
    return body


def inventory(root, external_aliases=None):
    root = physical(root)
    require(root.is_dir(), 'physical raw evidence directory required')
    rows, identities, total = [], {}, 0
    for path in [root, *sorted(root.rglob('*'))]:
        physical(path.parent)
        info = path.lstat()
        name = '.' if path == root else relative(path.relative_to(root).as_posix())
        require(not info.st_mode & 0o7000, 'special evidence mode refused')
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            require(external_aliases is not None, 'links are refused in whole regular raw evidence')
            target = os.readlink(path)
            require(target and '\x00' not in target, 'empty raw alias target')
            normalized = Path(os.path.normpath(str(path.parent / target)))
            internal = normalized == root or root in normalized.parents
            require(internal or str(normalized) in external_aliases, 'unregistered external product evidence alias')
            row = {'path': name, 'kind': 'opaque-alias', 'mode': mode, 'size': 0, 'target': target,
                   'target_scope': 'raw-root' if internal else 'qualified-runtime',
                   'target_sha256': None if internal else external_aliases[str(normalized)]}
            require(identity(path.lstat()) == identity(info), 'opaque alias changed during inventory')
        elif stat.S_ISDIR(info.st_mode):
            row = {'path': name, 'kind': 'directory', 'mode': mode, 'size': 0}
        else:
            # Ordinary product installs may share regular cache inodes. Retain their
            # bytes and original inode/link-count metadata; restore each name as a new
            # single-link data file. This never admits these files for execution.
            pin, checked = hash_file(path, single_link=external_aliases is None)
            require(identity(checked) == identity(info), 'raw file identity changed')
            row = {'path': name, 'kind': 'file', 'mode': mode, 'size': info.st_size, 'sha256': pin}
            total += info.st_size
        rows.append(row); identities[name] = identity(info)
        require(len(rows) <= MAX_MEMBERS and total <= MAX_TOTAL, 'raw evidence universe exceeds bound')
    return rows, identities


def pack_raw(root, archive, external_aliases=None):
    root, archive = physical(root), physical(archive)
    rows, initial = inventory(root, external_aliases)
    with tarfile.open(archive, 'x', format=tarfile.USTAR_FORMAT) as tar:
        for row in rows:
            if row['path'] == '.' or row['kind'] == 'opaque-alias': continue
            info = tarfile.TarInfo(row['path'])
            info.type = tarfile.DIRTYPE if row['kind'] == 'directory' else tarfile.REGTYPE
            info.mode, info.size, info.mtime = row['mode'], row['size'], 0
            # Refuse names that need PAX. Never silently change the restore contract.
            info.tobuf(format=tarfile.USTAR_FORMAT, encoding='utf-8', errors='strict')
            path = root / row['path']
            if row['kind'] == 'directory': tar.addfile(info)
            else:
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
                    require(identity(os.fstat(stream.fileno())) == initial[row['path']], 'archive input identity changed')
                    tar.addfile(info, stream)
                    require(identity(os.fstat(stream.fileno())) == initial[row['path']], 'archive input changed while copied')
    final, final_ids = inventory(root, external_aliases)
    require(rows == final and initial == final_ids, 'complete raw evidence changed during archive creation')
    pin, info = hash_file(archive, MAX_TOTAL + 256 * 1024**2)
    return {'inventory': rows, 'original_identities': initial, 'inventory_sha256': sha(canonical(rows)),
            'opaque_aliases_allowed': external_aliases is not None, 'external_alias_authority': external_aliases,
            'omission_ledger': [],
            'archive_sha256': pin, 'archive_bytes': info.st_size}


def split_archive(archive, root):
    root.mkdir(mode=0o700)
    rows = []
    with archive.open('rb') as stream:
        while body := stream.read(CHUNK):
            name = str(len(rows)).zfill(6) + '.chunk'
            with (root / name).open('xb') as output: output.write(body)
            rows.append({'name': name, 'bytes': len(body), 'sha256': sha(body)})
    require(rows and all(r['bytes'] == CHUNK for r in rows[:-1]), 'ordered fixed chunks required')
    return rows


def rejoin_archive(root, rows, archive, expected_size, expected_sha):
    require(type(rows) is list and 0 < len(rows) <= 270, 'bounded exact chunk list required')
    require({p.name for p in root.iterdir()} == {r['name'] for r in rows}, 'missing or extra sealed chunk')
    value, count = hashlib.sha256(), 0
    with archive.open('xb') as output:
        for index, row in enumerate(rows):
            require(set(row) == {'name', 'bytes', 'sha256'} and row['name'] == str(index).zfill(6) + '.chunk'
                    and type(row['bytes']) is int and 0 < row['bytes'] <= CHUNK
                    and (index == len(rows) - 1 or row['bytes'] == CHUNK), 'chunk order or bound differs')
            pin, info = hash_file(root / row['name'], CHUNK)
            require(pin == row['sha256'] and info.st_size == row['bytes'], 'sealed chunk bytes differ')
            body = read_file(root / row['name'], CHUNK); output.write(body); value.update(body); count += len(body)
    require(count == expected_size and count <= MAX_TOTAL + 256 * 1024**2
            and value.hexdigest() == expected_sha, 'whole raw archive hash or size differs')


def unpack_raw(archive, record, out):
    rows = record['inventory']
    require(type(rows) is list and 0 < len(rows) <= MAX_MEMBERS
            and sha(canonical(rows)) == record['inventory_sha256'], 'complete raw member inventory differs')
    require(rows[0]['path'] == '.' and rows[0]['kind'] == 'directory', 'raw root directory record required')
    names = [relative(r['path']) for r in rows[1:]]
    expected = {r['path']: r for r in rows[1:] if r['kind'] != 'opaque-alias'}
    require(len(set(names)) == len(rows) - 1 and set(record['original_identities']) == {r['path'] for r in rows}
            and record['omission_ledger'] == [],
            'duplicate raw member or missing original identity')
    for row in rows:
        require(type(row['mode']) is int and 0 <= row['mode'] <= 0o777
                and row['kind'] in {'file', 'directory', 'opaque-alias'} and type(row['size']) is int and row['size'] >= 0,
                'raw inventory mode, type or size differs')
        keys = {'path', 'kind', 'mode', 'size'}
        if row['kind'] == 'file': keys.add('sha256')
        if row['kind'] == 'opaque-alias':
            keys.update(('target', 'target_scope', 'target_sha256'))
            require(record['opaque_aliases_allowed'] is True and type(row['target']) is str and row['target']
                    and row['target_scope'] in {'raw-root', 'qualified-runtime'} and row['size'] == 0,
                    'opaque product evidence alias authority missing')
            if row['target_scope'] == 'qualified-runtime':
                require(row['target_sha256'] in record['external_alias_authority'].values()
                        and re.fullmatch('[0-9a-f]{64}', row['target_sha256']), 'opaque runtime target hash missing')
        require(set(row) == keys, 'unknown raw inventory field')
    require(sum(r['size'] for r in rows) <= MAX_TOTAL, 'raw output exceeds bound')
    with tarfile.open(archive, 'r:') as tar:
        headers = tar.getmembers()
        require(len(headers) == len(expected) and len({m.name for m in headers}) == len(headers), 'raw tar member universe differs')
        for member in headers:
            row = expected[relative(member.name)]
            require(not member.pax_headers and not member.linkname and member.uid == member.gid == member.mtime == 0
                    and member.uname == member.gname == '' and member.mode == row['mode'] and member.size == row['size']
                    and ((member.isfile() and row['kind'] == 'file') or (member.isdir() and row['kind'] == 'directory')),
                    'raw tar extended header, link, type, size or mode differs')
        out.mkdir(mode=0o700)
        for row in rows[1:]:
            if row['kind'] == 'directory': (out / row['path']).mkdir(mode=0o700)
        for member in headers:
            if not member.isfile(): continue
            row = expected[member.name]; value = hashlib.sha256(); count = 0
            with tar.extractfile(member) as source, (out / member.name).open('xb') as output:
                while body := source.read(1024**2):
                    count += len(body); require(count <= row['size'], 'raw tar member exceeds size')
                    value.update(body); output.write(body)
            require(count == row['size'] and value.hexdigest() == row['sha256'], 'restored raw member bytes differ')
            (out / member.name).chmod(row['mode'])
        for row in reversed(rows):
            if row['kind'] == 'directory': (out if row['path'] == '.' else out / row['path']).chmod(row['mode'])
    final, _ = inventory(out)
    require(final == [row for row in rows if row['kind'] != 'opaque-alias'], 'restored complete raw regular evidence universe differs')


def product_scope(root, context_path, data, helper, candidate, stage, shard_plan):
    work = Path(data['work_root'])
    shard = {'stage': data['stage'], **data['shard']}
    require(data['qa_mode'] == 'native-qa-v1' and data['helper_revision'] == helper == data['candidate_revision'] == candidate
            and work == root / 'som-full-product-run' and context_path == work / 'context.json'
            and shard in shard_plan and stage == 'full-product-' + data['stage'] + '-' + str(data['shard']['index']),
            'product raw Context must own the exact enclosing stage and named shard')


def product_alias_authority(sources, root, context_path, helper, candidate, stage, shard_plan):
    """Select external targets from checked Context roles. Never follow a raw alias."""
    require(context_path is not None, 'product evidence requires its source-bound Context')
    context_raw = read_file(context_path)
    data = parse(context_raw)
    product_scope(root, context_path, data, helper, candidate, stage, shard_plan)
    driver = ModuleType('_som_raw_scope_products_driver')
    path = 'som-rs/scripts/hosted_readiness/run_full_products.py'; raw = sources.read(path)
    driver.__file__, driver.__cached__, driver.__package__ = str(sources.root / path), None, ''
    sys.modules[driver.__name__] = driver
    exec(compile(raw, driver.__file__, 'exec', dont_inherit=True), driver.__dict__)
    module = driver.load_context_module(sources.root, helper, 'native-qa-v1', candidate)
    ctx = module.Context(context_path); module._ACTIVE = ctx
    try:
        runtime = ctx.runtime(); python = runtime['tools']['python']
        relative_python = Path(python).relative_to(Path(data['runtime_root'])).as_posix()
        pin = runtime['inventory'][relative_python]['sha256']
        require(hash_file(python)[0] == pin, 'qualified external Python target hash differs')
        aliases = {str(python): pin}
        # Actual qualified Rust role selection checks all six executable hashes.
        for tool in ctx.rust_tools().values(): aliases[str(tool)] = hash_file(tool)[0]
        ctx.verify(); sources.verify()
        require(read_file(context_path) == context_raw, 'product raw Context changed during role admission')
        return aliases, {'context_sha256': sha(context_raw), 'runtime_root': data['runtime_root'],
                         'runtime_identity': runtime['identity'], 'target_sha256': aliases}
    finally:
        lease = getattr(ctx, 'qa_import_controls', None)
        if lease is not None: lease.close()
        module._ACTIVE = None


def bootstrap(repo, revision, candidate):
    repo = physical(repo)
    def source(name):
        raw = subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo),
                                      'cat-file', 'blob', revision + ':' + name], stderr=subprocess.DEVNULL)
        require(raw == physical(repo / name).read_bytes(), 'raw transport helper differs from exact source')
        return raw
    raw = source(GRAPH)
    module = ModuleType('_som_sealed_current_graph')
    module.__file__, module.__cached__, module.__package__ = str(repo / GRAPH), None, ''
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    sources, authority, admission = module.admit_source(repo, revision, candidate)
    require(sources.read(SELF) == Path(__file__).read_bytes(), 'raw transport entry source differs')
    for name in SCRIPTS: sources.read(name)
    return module, sources, authority, admission


def checked_node(sources, node):
    pins = parse(sources.read('som-rs/contracts/native-tools-v1/artifacts.json'))
    rows = [r for r in pins['tools'] if r['tool'] == 'node' and r.get('host') == 'x86_64-unknown-linux-gnu']
    require(len(rows) == 1, 'one exact Linux Node pin required')
    value, info = hash_file(node)
    require(value == rows[0]['executable_sha256'] and info.st_mode & 0o111, 'native Node executable differs from source pin')
    return {'sha256': value, 'identity': identity(info)}


def run_m6(sources, node, node_record, script, arguments, evidence):
    require(script in SCRIPTS[:2] and 'M6_INPUTS_KEYRING' in os.environ, 'step-only M6 keyring required')
    for name in SCRIPTS: sources.read(name)
    require(checked_node(sources, node) == node_record, 'Node changed before sealed transport execution')
    command = [str(node), '--no-addons', str(sources.root / script), *map(str, arguments)]
    # No NODE_OPTIONS preload, alternate module path or external CA hook reaches this child.
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ', 'M6_INPUTS_KEYRING')
                   if key in os.environ}
    first_error = None
    with (evidence / 'm6.stdout').open('xb') as stdout, (evidence / 'm6.stderr').open('xb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True, cwd=evidence, env=environment)
        waited, killed = False, False
        try:
            process.wait(timeout=1200); waited = True
        except BaseException as error:
            first_error = error
        finally:
            try: os.killpg(process.pid, signal.SIGTERM); killed = True
            except ProcessLookupError: pass
            if not waited:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            elif killed:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
    record = {'exit_code': process.returncode, 'wait_recorded': True, 'owned_group_cleanup_attempted': True,
              'stdout_sha256': hash_file(evidence / 'm6.stdout')[0], 'stderr_sha256': hash_file(evidence / 'm6.stderr')[0]}
    if first_error is not None: record['error_type'] = type(first_error).__name__
    try:
        require(checked_node(sources, node) == node_record, 'Node changed during sealed transport execution')
        sources.verify()
        record['source_and_node_postcheck_complete'] = True
    except BaseException as error:
        record['postcheck_error_type'] = type(error).__name__
        if first_error is None: first_error = error
    (evidence / 'command.json').write_bytes(canonical(record) + b'\n')
    if first_error is not None: raise first_error
    require(process.returncode == 0, 'existing M6 sealed evidence command refused')
    return record


def packet_inventory(root, index_sha):
    require({p.name for p in root.iterdir()} == {'receipt.json', 'sealed'}, 'sealed packet outer universe differs')
    sealed = physical(root / 'sealed')
    raw = read_file(sealed / 'evidence-index.json', 1024**2)
    require(sha(raw) == index_sha, 'trusted same-run sealed index hash differs')
    index = parse(raw)
    require(index['version'] == 'som.m6-evidence-index.v1' and index['status'] == 'encrypted', 'existing M6 sealed index required')
    expected = {'evidence-index.json'}
    for row in index['packages']:
        expected.update((relative(row['archive']), relative(row['manifest'])))
        require(hash_file(sealed / row['archive'], 129 * 1024**2)[0] == row['cipher_sha256']
                and hash_file(sealed / row['manifest'])[0] == row['manifest_sha256'], 'sealed package or manifest hash differs')
    require({p.name for p in sealed.iterdir()} == expected, 'sealed package universe differs')
    return index


def bind(row, args):
    require(row['version'] == 'som.current-raw-sealed-transport.v1' and row['stage'] == args.stage
            and row['helper_revision'] == args.helper_revision and row['candidate_revision'] == args.candidate_revision
            and row['run_id'] == int(os.environ['GITHUB_RUN_ID']) and row['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
            and row['repository'] == os.environ['GITHUB_REPOSITORY'] and row['product_acceptance_passed'] is False,
            'sealed raw belongs to another stage, source, run or attempt')


def main(argv=None):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux sealed transport only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('seal', 'restore'))
    for name in ('repo', 'node', 'out', 'evidence-out'): parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision', 'stage'): parser.add_argument('--' + name, required=True)
    parser.add_argument('--raw-root', type=Path); parser.add_argument('--packet-root', type=Path)
    parser.add_argument('--product-context', type=Path)
    parser.add_argument('--index-sha256'); parser.add_argument('--artifact-id', type=int)
    parser.add_argument('--artifact-digest'); parser.add_argument('--artifact-name')
    args = parser.parse_args(argv)
    require(args.stage in STAGES or product_shard(args.stage),
            'named affected raw evidence stage required')
    graph, sources, authority, admission = bootstrap(args.repo, args.helper_revision, args.candidate_revision)
    out, evidence = owned_new(args.out), owned_new(args.evidence_out)
    require(out != evidence and out not in evidence.parents and evidence not in out.parents, 'disjoint raw output/evidence required')
    evidence.mkdir(mode=0o700)
    node_record = checked_node(sources, args.node)
    if args.operation == 'seal':
        require(args.raw_root is not None, 'complete raw stage tree required')
        root = physical(args.raw_root)
        require(root != out and root not in out.parents and out not in root.parents
                and evidence not in root.parents and root not in evidence.parents, 'disjoint raw/sealed roots required')
        with tempfile.TemporaryDirectory(prefix='current-raw-pack-', dir=evidence) as scratch:
            scratch = Path(scratch); archive = scratch / 'raw.tar'
            aliases, alias_proof = None, None
            if product_shard(args.stage):
                aliases, alias_proof = product_alias_authority(sources, root, args.product_context,
                    args.helper_revision, args.candidate_revision, args.stage, graph.matrix()['include'])
            record = pack_raw(root, archive, aliases)
            stage_policy(record, args.stage)
            record['product_alias_source_authority'] = alias_proof
            hosted = scratch / 'hosted'; transport = hosted / 'run/transport'; transport.mkdir(parents=True)
            record.update(version='som.current-raw-sealed-transport.v1', stage=args.stage,
                helper_revision=args.helper_revision, candidate_revision=args.candidate_revision,
                run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
                repository=os.environ['GITHUB_REPOSITORY'], product_acceptance_passed=False)
            record['chunks'] = split_archive(archive, transport / 'chunks')
            manifest_raw = canonical(record) + b'\n'; (transport / 'transport.json').write_bytes(manifest_raw)
            out.mkdir(mode=0o700)
            run_m6(sources, args.node, node_record, SCRIPTS[0], ['--root', hosted, '--out', out / 'sealed'], evidence)
            rows, ids = inventory(root, aliases)
            require(rows == record['inventory'] and ids == record['original_identities'], 'raw stage changed while sealed')
            index_sha = hash_file(out / 'sealed/evidence-index.json')[0]
            safe = {k: record[k] for k in ('version', 'stage', 'helper_revision', 'candidate_revision', 'run_id',
                    'run_attempt', 'repository', 'product_acceptance_passed', 'archive_sha256', 'archive_bytes', 'inventory_sha256')}
            safe.update(index_sha256=index_sha, transport_manifest_sha256=sha(manifest_raw), member_count=len(record['inventory']),
                        chunk_count=len(record['chunks']), source_admission_sha256=admission['qa_source_admission_sha256'])
            (out / 'receipt.json').write_bytes(canonical(safe) + b'\n')
            packet_inventory(out, index_sha)
    else:
        require(args.packet_root and args.artifact_id and re.fullmatch('sha256:[0-9a-f]{64}', args.artifact_digest or '')
                and re.fullmatch('[0-9a-f]{64}', args.index_sha256 or ''), 'same-run artifact and trusted sealed index required')
        current = graph.artifact_metadata(os.environ['GITHUB_REPOSITORY'], args.artifact_id)
        require(current['id'] == args.artifact_id and current['name'] == args.artifact_name
                and current['digest'] == args.artifact_digest and current['expired'] is False
                and current['workflow_run']['id'] == int(os.environ['GITHUB_RUN_ID'])
                and current['workflow_run']['head_sha'] == args.candidate_revision, 'sealed artifact identity differs')
        packet_inventory(args.packet_root, args.index_sha256)
        safe = parse(read_file(args.packet_root / 'receipt.json', 1024**2)); bind(safe, args)
        require(safe['index_sha256'] == args.index_sha256
                and safe['source_admission_sha256'] == admission['qa_source_admission_sha256'], 'sealed receipt authority differs')
        with tempfile.TemporaryDirectory(prefix='current-raw-restore-', dir=evidence) as scratch:
            scratch = Path(scratch); hosted = scratch / 'hosted'
            run_m6(sources, args.node, node_record, SCRIPTS[1], ['--root', args.packet_root / 'sealed', '--out', hosted,
                    '--index-sha256', args.index_sha256], evidence)
            transport = hosted / 'run/transport'
            require({p.name for p in hosted.iterdir()} == {'run'} and {p.name for p in (hosted / 'run').iterdir()} == {'transport'}
                    and {p.name for p in transport.iterdir()} == {'transport.json', 'chunks'}, 'restored sealed envelope universe differs')
            manifest_raw = read_file(transport / 'transport.json')
            require(sha(manifest_raw) == safe['transport_manifest_sha256'], 'sealed raw manifest differs')
            record = parse(manifest_raw); bind(record, args)
            stage_policy(record, args.stage)
            require(all(record[k] == safe[k] for k in ('archive_sha256', 'archive_bytes', 'inventory_sha256'))
                    and len(record['inventory']) == safe['member_count'] and len(record['chunks']) == safe['chunk_count'],
                    'sealed full archive/member identity differs')
            archive = scratch / 'raw.tar'
            rejoin_archive(transport / 'chunks', record['chunks'], archive, record['archive_bytes'], record['archive_sha256'])
            unpack_raw(archive, record, out)
            (evidence / 'opaque-aliases.json').write_bytes(canonical({'aliases': [row for row in record['inventory']
                if row['kind'] == 'opaque-alias'], 'source_authority': record['product_alias_source_authority'],
                'aliases_instantiated': False, 'omission_ledger': record['omission_ledger']}) + b'\n')
        (evidence / 'restore.json').write_bytes(canonical({'version': 'som.current-raw-sealed-restore.v1',
             'artifact_id': args.artifact_id, 'artifact_digest': args.artifact_digest, 'index_sha256': args.index_sha256,
             'raw_inventory_sha256': record['inventory_sha256'], 'complete_raw_regular_bytes_restored': True,
             'complete_opaque_alias_metadata_retained': True, 'aliases_instantiated': False,
             'native_code_executed_from_restored_evidence': False, 'product_acceptance_passed': False}) + b'\n')
        index_sha = args.index_sha256
    sources.verify()
    require(checked_node(sources, args.node) == node_record, 'Node changed after sealed transport')
    if args.operation == 'seal' and 'GITHUB_OUTPUT' in os.environ:
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('sealed_index_sha256=' + index_sha + '\n')
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        # Raw source paths and raw HTTP data do not enter the public workflow log.
        sys.stderr.write('current raw sealed transport refused; retain the private stage evidence\n')
        raise SystemExit(1)
