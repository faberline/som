"""Recheck and relocate only Cargo's emitted directory-source configuration."""
from __future__ import annotations

import json
from pathlib import Path
import tarfile
import tomllib

SCRIPT = 'som-rs/scripts/hosted_readiness/product_runtime/consume_cargo.py'
PRODUCER = 'som-rs/scripts/hosted_readiness/product_runtime/provision_cargo.py'


def prepare(ctx):
    from full_product_context import canonical, dependency_identity, digest, read, relative, require
    producer = ctx.module('cargo_producer', PRODUCER)
    root = Path(ctx.data['cargo_root'])
    receipt_raw = read(root / 'receipt.json')
    report = json.loads(receipt_raw)
    require(report.get('version') == 'som.product-cargo-closure.v1'
            and report.get('status') == 'registry_closure_verified_product_gates_unrun'
            and report.get('tested_sha') == ctx.candidate_revision
            and report.get('vendor_package_count') == 352,
            'complete same-candidate Cargo closure required')
    dependency_identity(report, ctx.helper_revision, ctx.candidate_revision, ctx.candidate_admission)
    blobs = {producer.BASE + directory + '/' + leaf:
             ctx.sources.read(producer.BASE + directory + '/' + leaf)
             for directory, *_ in producer.LOCKS.values() for leaf in ('Cargo.toml', 'Cargo.lock')}
    locked, counts = producer.lock_union(blobs)
    require(report['registry_union_count'] == 352 and report['lock_counts'] == counts,
            'complete frozen Cargo dependency union differs')
    archive_path = root / 'cargo-closure.tar.gz'
    require(digest(read(archive_path, 512 * 1024**2)) == report['candidate_archive_sha256'],
            'qualified Cargo transport archive changed')
    destination = ctx.work / 'cargo-closure'
    destination.mkdir(mode=0o700, exist_ok=False)
    seen, expanded = set(), 0
    with tarfile.open(archive_path, 'r:gz') as archive:
        for item in archive:
            name = relative(item.name)
            require(item.isfile() and not item.pax_headers and name not in seen
                    and name.split('/')[0] in {'vendor', 'licenses', 'downloads', 'cargo-home'}
                    and item.size <= 50 * 1024**2 and 0 <= item.mode <= 0o777 and item.mode & 0o400,
                    'Cargo carrier member is unsafe or duplicated')
            expanded += item.size
            require(expanded <= 2 * 1024**3 and len(seen) < 200000, 'Cargo carrier exceeds fixed bounds')
            seen.add(name)
            body = archive.extractfile(item).read(50 * 1024**2 + 1)
            require(len(body) == item.size, 'Cargo archive member is truncated')
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(body)
            path.chmod(item.mode)
    packages = {(row['name'], row['version']): row for row in report['packages']}
    require(set(packages) == set(locked), 'Cargo package receipts omit a frozen locked package')
    vendor = destination / 'vendor'
    require(set(path.name for path in vendor.iterdir()) == {name + '-' + version for name, version in locked},
            'relocated vendor is not the exact 352 package set')
    expected_members = {'cargo-home/config.toml'}
    for key, (_, _, _, checksum) in locked.items():
        row, name = packages[key], key[0] + '-' + key[1]
        require(row['checksum'] == checksum and row['source'] == producer.REGISTRY, 'registry identity changed')
        actual = {name: digest(read(path, 50 * 1024**2)) for name, path in producer.tree_files(vendor / name).items()}
        raw_checksum = read(vendor / name / '.cargo-checksum.json')
        table = json.loads(raw_checksum)
        require(actual.pop('.cargo-checksum.json') == row['cargo_checksum_sha256']
                and actual == row['files'] and table == {'package': checksum, 'files': actual},
                'vendor source bytes or Cargo checksum metadata changed')
        expected_members.update('vendor/' + name + '/' + member for member in (*actual, '.cargo-checksum.json'))
        source = destination / 'downloads' / (name + '.crate')
        require(digest(read(source, 50 * 1024**2)) == checksum, 'publisher archive no longer matches frozen checksum')
        expected_members.add('downloads/' + source.name)
        for notice, expected in row['licenses'].items():
            path = 'licenses/' + name + '/' + relative(notice)
            require(digest(read(destination / path)) == expected, 'publisher license notice changed')
            expected_members.add(path)
    require(seen == expected_members, 'Cargo archive file universe differs from its full qualified scope')
    old_config = read(destination / 'cargo-home/config.toml')
    require(digest(old_config) == report['cargo_config_sha256'], 'emitted original Cargo configuration changed')
    parsed = tomllib.loads(old_config.decode())
    old_vendor = parsed['source']['vendored-sources']['directory']
    require(type(old_vendor) is str and Path(old_vendor).is_absolute() and Path(old_vendor).name == 'vendor'
            and parsed == {'source': {'crates-io': {'replace-with': 'vendored-sources'},
                                     'vendored-sources': {'directory': old_vendor}}, 'net': {'offline': True}},
            'unexpected inherited or registry Cargo configuration')
    home = ctx.work / 'cargo-home'
    home.mkdir(exist_ok=False)
    # Only this one checked absolute path changes. Package and checksum bytes do not.
    new_config = ('[source.crates-io]\nreplace-with = "vendored-sources"\n'
                  '[source.vendored-sources]\ndirectory = ' + json.dumps(str(vendor)) + '\n'
                  '[net]\noffline = true\n').encode()
    (home / 'config.toml').write_bytes(new_config)
    record = {'version': 'som.product-cargo-checked-relocation.v1',
              'producer_receipt_sha256': digest(receipt_raw), 'archive_sha256': report['candidate_archive_sha256'],
              'original_config_sha256': digest(old_config), 'config_sha256': digest(new_config),
              'old_vendor': old_vendor, 'new_vendor': str(vendor), 'registry_packages': 352,
              'package_bytes_changed': False, 'product_acceptance_passed': False}
    (ctx.work / 'cargo-relocation.json').write_bytes(canonical(record) + b'\n')
    return record
