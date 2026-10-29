"""Independently inspect retained wheel bytes; never execute a wheel or imply legal approval."""
from __future__ import annotations

import base64
import csv
from email import policy
from email.parser import BytesParser
import hashlib
import io
import itertools
import json
import re
import stat
import zipfile

MAX_WHEEL = 150 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_MEMBER = 128 * 1024 * 1024
MAX_METADATA = 16 * 1024 * 1024
MAX_MEMBERS = 100000

class WheelAuditError(ValueError):
    pass


def need(value, reason):
    if not value:
        raise WheelAuditError(reason)


def digest(raw, algorithm='sha256'):
    return hashlib.new(algorithm, raw).hexdigest()


def normalize(name):
    need(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) is not None,
         'invalid distribution name')
    return re.sub(r'[-_.]+', '-', name).lower()


def safe_name(value):
    need(isinstance(value, str) and value and not any(x in value for x in ('\\', '\x00', '\n', '\r')),
         'invalid ZIP member name')
    need(not value.endswith('//'), 'repeated trailing separator')
    pieces = value.rstrip('/').split('/')
    need(not value.startswith('/') and all(x not in ('', '.', '..') for x in pieces), 'unsafe ZIP member name')
    return '/'.join(pieces)


def expanded_tags(value):
    part = r'[a-z0-9_]+(?:\.[a-z0-9_]+)*'
    need(isinstance(value, str) and len(value) <= 4096
         and re.fullmatch(part + '-' + part + '-' + part, value) is not None, 'invalid wheel tag')
    triples = [x.split('.') for x in value.split('-')]
    need(len(triples[0]) * len(triples[1]) * len(triples[2]) <= 4096, 'wheel tag expansion exceeds bound')
    return {'-'.join(items) for items in itertools.product(*triples)}


def header_value(message, name):
    value = message.get(name)
    return None if value is None else str(value)


def header_values(message, name):
    return [str(value) for value in message.get_all(name, [])]


def audit_wheel(raw, filename, expected_name, expected_version, recorded):
    """Check a wheel and recorded inventory. Return raw metadata and notice bytes.

    The caller must independently check archive provenance, dependency locks,
    publisher metadata and the retained evidence paths. `signatures_unverified`
    means the wheel carried external RECORD signatures; no key check is done.
    """
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_WHEEL, 'wheel bytes missing or oversized')
    need(isinstance(recorded, dict), 'wheel receipt missing')
    need(isinstance(filename, str) and re.fullmatch(r'[A-Za-z0-9_.+]+(?:-[A-Za-z0-9_.+]+){4,5}\.whl', filename),
         'wheel filename differs')
    pieces = filename[:-4].split('-')
    need(len(pieces) in (5, 6) and isinstance(expected_name, str) and normalize(expected_name) == expected_name
         and isinstance(expected_version, str) and normalize(pieces[0]) == expected_name and pieces[1] == expected_version,
         'wheel filename identity differs')
    require_tags = expanded_tags('-'.join(pieces[-3:]))
    wheel_sha = digest(raw)
    need(recorded.get('filename') == filename and recorded.get('sha256') == wheel_sha
         and type(recorded.get('size')) is int and recorded['size'] == len(raw)
         and recorded.get('name') == expected_name and recorded.get('version') == expected_version,
         'wheel receipt identity differs')
    try:
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as wheel:
            infos = wheel.infolist()
            need(0 < len(infos) <= MAX_MEMBERS, 'wheel member count differs')
            names, types, directories = set(), {}, []
            total = 0
            for entry in infos:
                need(entry.orig_filename == entry.filename, 'NUL in ZIP name')
                name = safe_name(entry.filename)
                need(name not in names, 'duplicate ZIP path')
                names.add(name)
                kind = (entry.external_attr >> 16) & 0o170000
                need(kind in (0, stat.S_IFREG, stat.S_IFDIR) and not entry.flag_bits & 1
                     and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'wheel member type refused')
                need((kind != stat.S_IFREG or not entry.is_dir()) and (kind != stat.S_IFDIR or entry.is_dir()),
                     'wheel member type conflicts with name')
                need(0 <= entry.file_size <= MAX_MEMBER and 0 <= entry.compress_size <= MAX_WHEEL,
                     'wheel member exceeds bound')
                need(not entry.is_dir() or entry.file_size == 0, 'wheel directory contains data')
                total += entry.file_size
                need(total <= MAX_EXPANDED, 'wheel expansion exceeds bound')
                types[name] = 'directory' if entry.is_dir() else 'file'
                if entry.is_dir():
                    directories.append(name)
            for name in names:
                parts = name.split('/')
                need(all(types.get('/'.join(parts[:i])) != 'file' for i in range(1, len(parts))),
                     'wheel parent path is a file')
            files, data_by_name = {}, {}
            for entry in infos:
                if entry.is_dir():
                    continue
                name = entry.filename
                data = wheel.read(entry)
                need(len(data) == entry.file_size, 'wheel member length differs')
                files[name] = {'size': len(data), **{algo: digest(data, algo) for algo in ('sha256', 'sha384', 'sha512')}}
                if name.endswith(('.dist-info/METADATA', '.dist-info/WHEEL', '.dist-info/RECORD')):
                    need(len(data) <= MAX_METADATA, 'wheel metadata exceeds bound')
                    data_by_name[name] = data
            roots = []
            for name in files:
                if name.count('/') != 1 or not name.endswith('.dist-info/METADATA'):
                    continue
                prefix = name.split('/', 1)[0].removesuffix('.dist-info')
                distribution, separator, version = prefix.rpartition('-')
                if separator and normalize(distribution) == expected_name and version == expected_version:
                    roots.append(name.removesuffix('METADATA'))
            need(len(roots) == 1, 'wheel distribution metadata root differs')
            prefix = roots[0]
            meta_path, wheel_path, record_path = prefix + 'METADATA', prefix + 'WHEEL', prefix + 'RECORD'
            need(all(name in data_by_name for name in (meta_path, wheel_path, record_path)), 'wheel metadata missing')
            metadata_raw, wheel_raw, record_raw = (data_by_name[x] for x in (meta_path, wheel_path, record_path))
            metadata = BytesParser(policy=policy.default).parsebytes(metadata_raw)
            need(len(metadata.get_all('Name', [])) == len(metadata.get_all('Version', [])) == 1
                 and normalize(str(metadata['Name'])) == expected_name and str(metadata['Version']) == expected_version,
                 'wheel metadata identity differs')
            wheel_doc = BytesParser(policy=policy.default).parsebytes(wheel_raw)
            need(len(wheel_doc.get_all('Wheel-Version', [])) == 1 and str(wheel_doc['Wheel-Version']) == '1.0',
                 'wheel version differs')
            tag_headers = header_values(wheel_doc, 'Tag')
            need(tag_headers, 'wheel tags missing')
            tags = set()
            for tag in tag_headers:
                tags.update(expanded_tags(tag))
                need(len(tags) <= 4096, 'wheel tag set exceeds bound')
            need(tags == require_tags, 'wheel tag set differs from filename')
            try:
                rows = list(csv.reader(io.StringIO(record_raw.decode('utf-8', 'strict'), newline=''), strict=True))
            except (UnicodeError, csv.Error) as exc:
                raise WheelAuditError('invalid wheel RECORD') from exc
            by_name = {}
            for row in rows:
                need(len(row) == 3, 'RECORD row differs')
                name, hash_field, length = row
                need(safe_name(name) == name and name not in by_name and name in files, 'RECORD path differs')
                if name == record_path:
                    need(hash_field == length == '', 'RECORD self entry differs')
                else:
                    algo, sep, encoded = hash_field.partition('=')
                    need(sep and algo in ('sha256', 'sha384', 'sha512') and re.fullmatch(r'[0-9]+', length),
                         'RECORD digest or size missing')
                    expected = base64.urlsafe_b64encode(bytes.fromhex(files[name][algo])).rstrip(b'=').decode('ascii')
                    need(encoded == expected and int(length) == files[name]['size'], 'RECORD content differs')
                by_name[name] = row
            need(record_path in by_name, 'RECORD self entry missing')
            signatures = {prefix + 'RECORD.jws', prefix + 'RECORD.p7s'}
            members, notices, notice_bytes, unverified_signatures = [], [], {}, []
            for name in sorted(files):
                value = files[name]
                unsigned = name in signatures and name not in by_name
                need(name in by_name or unsigned, 'wheel file not in RECORD')
                if name in signatures:
                    unverified_signatures.append(name)
                members.append({'path': name, 'sha256': value['sha256'], 'size': value['size'],
                                'record_binding': 'external-signature' if unsigned else 'self' if name == record_path else 'sha256'})
                basename = name.rsplit('/', 1)[-1].lower()
                if '.dist-info/licenses/' in name or basename.startswith(('license', 'licence', 'copying', 'notice')):
                    body = wheel.read(name)
                    preserved = digest(name.encode('utf-8')) + '.txt'
                    notices.append({'path': name, 'sha256': value['sha256'], 'size': value['size'], 'preserved': preserved})
                    notice_bytes[preserved] = body
            calculated = {'filename': filename, 'sha256': wheel_sha, 'size': len(raw),
                          'name': expected_name, 'version': expected_version,
                          'metadata_sha256': digest(metadata_raw), 'record_sha256': digest(record_raw),
                          'dist_info_dir': prefix.removesuffix('/'),
                          'data_dir': prefix.removesuffix('.dist-info/') + '.data',
                          'directories': directories, 'record_entries': rows,
                          'wheel_metadata_sha256': digest(wheel_raw), 'requires_dist': header_values(metadata, 'Requires-Dist'),
                          'requires_python': header_value(metadata, 'Requires-Python'),
                          'license_expression': header_value(metadata, 'License-Expression'),
                          'license_label': header_value(metadata, 'License'),
                          'license_files_declared': header_values(metadata, 'License-File'),
                          'license_notices': notices, 'notice_status': 'present' if notices else 'none_in_publisher_wheel; independent legal review unresolved',
                          'members': members}
            def canonical(value):
                return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            need(all(key in recorded and canonical(recorded[key]) == canonical(value) for key, value in calculated.items()),
                 'wheel receipt inventory differs')
            need(recorded.get('record_verified') is True and recorded.get('independent_metadata_license_audit') == 'unrun',
                 'wheel receipt scope differs')
            return {'identity': calculated, 'wheel_tags': sorted(tags),
                    'raw_evidence': {'METADATA': metadata_raw, 'WHEEL': wheel_raw, 'RECORD': record_raw,
                                     **notice_bytes},
                    'signatures_unverified': unverified_signatures,
                    'legal_license_review': 'unrun', 'product_acceptance_passed': False}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError) as exc:
        raise WheelAuditError('invalid wheel ZIP') from exc
