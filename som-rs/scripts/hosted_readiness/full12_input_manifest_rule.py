"""Fixed INPUT-pair diagnostics. No caller or record gains authority here.

This inactive module accepts byte data and four untrusted data projections.
It does not read files, authenticate receipts, or run any acceptance step.
"""

import hashlib
import json


DECISION_ID = 'som.mac-full12-input-manifest.v1:829efe8e49e32bbd58b655e854443eea593a467775fdc7e74d0c4d44dd570061'
PROPOSAL_MANIFEST_SHA256 = 'f95e13b9660a85f68c60fdd4f2c47f6b580ff308143fa70113829e5d7e6d9f58'
ADOPTION_TEXT = 'Additional Mac full12 INPUT decision som.mac-full12-input-manifest.v1:829efe8e49e32bbd58b655e854443eea593a467775fdc7e74d0c4d44dd570061: this receipt explicitly adopts only the independently reconstructed complete INPUT assets/manifest.json backend source-vector difference, and its exact recomputed manifest/backend identities, for the reviewed original-before/current-native pair. Every other manifest member and all six asset files remain byte-identical. Each full manifest must equal its own actual compiled release. Original run_hosted_acceptance.py:280 four-way equality remains NOT_PASSED and is retained separately. This INPUT decision is outside prior response decision3cc4a99acd70b1a909640a1ddcf10db2f9cbc2e59e3fca641cbfd8f69268bc7b. All671 cases, both typed-target forms, original CNL forms, all12 steps, exact product outputs, tool/source/process controls and four-receipt lifecycle remain mandatory. This receipt must also bind the exact frozen proposal-manifest SHA256 and the final selected source/tool/QA maps; no draft, local data report or component pass activates this scope.'
RESPONSE_PROPOSAL_SHA256 = '3cc4a99acd70b1a909640a1ddcf10db2f9cbc2e59e3fca641cbfd8f69268bc7b'
_BEFORE_MANIFEST_RAW = b'{"backend":{"files":{"Cargo.lock":"c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c","Cargo.toml":"6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1","block-contracts/composed-quality.json":"28f9bd134f31eb87fae5a680148447de3aaca9bad78fc10f0ac22b00cc40b0e5","block-contracts/composed.json":"3e58ed5b162cc4297324cc6da15ad9838c2a136d8a91762f63ac8d0c3af0b6b4","block-contracts/flow-v7-compact.json":"a2b96804e6f1c52a2ebf9e6310c6624b8b1b33d86b4f4763f07ffbeb645e103a","block-contracts/flow-v7.json":"d0ffbe92abbf8666e5e562022617e6eb81f4ef1c9fcc73b92fca8c718bba3f47","contracts/python-literal-encoding.v1.json":"7c25a1f45e54cec45e4053a5ceaf767188641ddde7da34dfce0747dc299dbf16","contracts/python-quality-v1/pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","contracts/related-business.v1.md":"9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380","contracts/related-flow.v1.md":"716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091","flow_compact/engine.py.tpl":"76923c4752bccd850b5fd55ebb77904aa3988170927ba22ee4acef8bbc556d5f","flow_compact/forward_support.py.tpl":"45250ff33586c4765733ccd69b8ca1bd52cb34893880bd89fe78d703dc29d557","src/business.rs":"b880c325a02c9a93a9e1470baa0416db54560b986511a71561583c1468c9df2f","src/cnl.rs":"8f14a9c5897b335066a619be907244f38f1ad3947d12ed7e1833fa7e77098789","src/cnl_semantics.rs":"4d4c6e706fbf83bc86ef5ae7f11cbdc884ba7a9d43ba7c03b7f7a5851311d834","src/compiler.rs":"9f131e728e2feb2b769d7150053a3949b5149a024a2cde1e38035c13c54e935f","src/composed.rs":"571c04a8e50f9856361dce94ebab813d8a8905aee9e2ea12dbd687f4f32d890c","src/flow.rs":"fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75","src/flow_compact.rs":"d4543446c2b5cd99fb020bbddb8f15ed3f1faecfc3ca8c08fb98249649687bb4","src/json_diagnostics.rs":"87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6","src/lib.rs":"60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f","src/main.rs":"042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5","src/planner.rs":"7958474d52f4e9b9f4fd1afc16e328428e32acc77dcbabd2ca3814f22610f12d","src/python_names.rs":"22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec","src/quality.rs":"71f5987b24559cb8507416dea15d6ddd6ac2dd5095c6c4c940b5c3a1328b0d21","src/related.rs":"ccc55e6018117a7087685de81da420da911832edaae51532e712be7b1b138dca","src/strict_json.rs":"06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161"},"version":"som.compiler-backend.v1"},"files":{"blocks.json":"c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e","legacy-blocks.json":"6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f","pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","quality-blocks.json":"0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc","quality.json":"6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21","ruff":"cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a"},"purpose":"som.rust-compiler.v1","version":"som.compiler-assets.v1"}\n'
_CURRENT_MANIFEST_RAW = b'{"backend":{"files":{"Cargo.lock":"c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c","Cargo.toml":"6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1","block-contracts/composed-quality.json":"28f9bd134f31eb87fae5a680148447de3aaca9bad78fc10f0ac22b00cc40b0e5","block-contracts/composed.json":"3e58ed5b162cc4297324cc6da15ad9838c2a136d8a91762f63ac8d0c3af0b6b4","block-contracts/flow-v7-compact.json":"a2b96804e6f1c52a2ebf9e6310c6624b8b1b33d86b4f4763f07ffbeb645e103a","block-contracts/flow-v7.json":"d0ffbe92abbf8666e5e562022617e6eb81f4ef1c9fcc73b92fca8c718bba3f47","contracts/native-tools-v1/CONTRACT.md":"47d72cabd57bdb380171a54f4dddeeabf25693c3781cabb483c572c162efac6e","contracts/native-tools-v1/artifacts.json":"959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193","contracts/python-literal-encoding.v1.json":"7c25a1f45e54cec45e4053a5ceaf767188641ddde7da34dfce0747dc299dbf16","contracts/python-quality-v1/pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","contracts/related-business.v1.md":"9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380","contracts/related-flow.v1.md":"716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091","flow_compact/engine.py.tpl":"76923c4752bccd850b5fd55ebb77904aa3988170927ba22ee4acef8bbc556d5f","flow_compact/forward_support.py.tpl":"45250ff33586c4765733ccd69b8ca1bd52cb34893880bd89fe78d703dc29d557","src/business.rs":"b880c325a02c9a93a9e1470baa0416db54560b986511a71561583c1468c9df2f","src/cnl.rs":"8f14a9c5897b335066a619be907244f38f1ad3947d12ed7e1833fa7e77098789","src/cnl_semantics.rs":"4d4c6e706fbf83bc86ef5ae7f11cbdc884ba7a9d43ba7c03b7f7a5851311d834","src/compiler.rs":"49c8309e102b1f7c686c8a975ed8b2c81d0133557c0443d8986474ca41cd9f3c","src/composed.rs":"571c04a8e50f9856361dce94ebab813d8a8905aee9e2ea12dbd687f4f32d890c","src/flow.rs":"fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75","src/flow_compact.rs":"d4543446c2b5cd99fb020bbddb8f15ed3f1faecfc3ca8c08fb98249649687bb4","src/json_diagnostics.rs":"87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6","src/lib.rs":"60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f","src/main.rs":"042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5","src/planner.rs":"7958474d52f4e9b9f4fd1afc16e328428e32acc77dcbabd2ca3814f22610f12d","src/python_names.rs":"22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec","src/quality.rs":"4522527da6a96bfee72365ab0b130c816022194fdff3de386cf604bb7c8e713b","src/related.rs":"ccc55e6018117a7087685de81da420da911832edaae51532e712be7b1b138dca","src/strict_json.rs":"06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161"},"version":"som.compiler-backend.v1"},"files":{"blocks.json":"c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e","legacy-blocks.json":"6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f","pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","quality-blocks.json":"0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc","quality.json":"6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21","ruff":"cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a"},"purpose":"som.rust-compiler.v1","version":"som.compiler-assets.v1"}\n'
_OWNERS = (('compiler-v1', 5), ('rust-output-v1', 8),
           ('typescript-output-v1', 4), ('react-output-v1', 3))
_FALSE_FIELDS = ('native_authority', 'receipt_admission', 'source_admission',
                 'SourceSeal', 'product_acceptance', 'full12_passed', 'M12_passed')
_MAX_MANIFEST_BYTES = 8192
_MAX_JSON_DEPTH = 8
_MAX_MAP_ENTRIES = 4096
_MAX_PATH_CHARS = 512


def _parse_exact_manifest(raw):
    """Parse exactly one of the two approved complete manifest byte strings."""
    if type(raw) is not bytes:
        raise ValueError('manifest must be exact builtin bytes')
    if not 2 <= len(raw) <= _MAX_MANIFEST_BYTES:
        raise ValueError('manifest byte bound')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as error:
        raise ValueError('manifest UTF8') from error

    # Bound nesting before the JSON decoder sees the input. Quoted braces and
    # escaped quotes do not count. The decoder still checks JSON syntax.
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise ValueError('manifest depth bound')
        elif char in '}]':
            depth -= 1

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('manifest duplicate key')
            result[key] = value
        return result

    def no_constant(value):
        raise ValueError('manifest nonfinite value')

    def no_float(value):
        raise ValueError('manifest floating number')

    try:
        manifest = json.loads(text, object_pairs_hook=unique_object,
                              parse_constant=no_constant, parse_float=no_float)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError('manifest JSON syntax') from error
    if type(manifest) is not dict or set(manifest) != {'version', 'purpose', 'backend', 'files'}:
        raise ValueError('manifest top schema')
    if (type(manifest['version']) is not str or type(manifest['purpose']) is not str
            or manifest['version'] != 'som.compiler-assets.v1'
            or manifest['purpose'] != 'som.rust-compiler.v1'):
        raise ValueError('manifest version or purpose')
    backend = manifest['backend']
    if (type(backend) is not dict or set(backend) != {'version', 'files'}
            or type(backend['version']) is not str
            or backend['version'] != 'som.compiler-backend.v1'):
        raise ValueError('manifest backend schema or version')
    assets = manifest['files']
    if type(assets) is not dict or set(assets) != {
            'blocks.json', 'legacy-blocks.json', 'quality-blocks.json',
            'pyproject.toml', 'quality.json', 'ruff'}:
        raise ValueError('manifest asset schema')
    source_files = backend['files']
    if type(source_files) is not dict or len(source_files) not in (27, 29):
        raise ValueError('manifest backend file schema')
    for mapping in (assets, source_files):
        for name, digest in mapping.items():
            if (type(name) is not str or type(digest) is not str or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest)):
                raise ValueError('manifest file SHA schema')
    try:
        canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError('manifest canonical encoding') from error
    if raw != canonical + b'\n':
        raise ValueError('manifest canonical bytes and one LF')
    if raw not in (_BEFORE_MANIFEST_RAW, _CURRENT_MANIFEST_RAW):
        raise ValueError('not a fixed approved manifest')
    return manifest


def compare_exact_input_pair(before_raw, current_raw):
    """Compare the fixed pair as data. This does not admit a compiled release."""
    before = _parse_exact_manifest(before_raw)
    current = _parse_exact_manifest(current_raw)
    if before_raw != _BEFORE_MANIFEST_RAW or current_raw != _CURRENT_MANIFEST_RAW:
        raise ValueError('fixed before/current argument roles')
    if {key: value for key, value in before.items() if key != 'backend'} != {
            key: value for key, value in current.items() if key != 'backend'}:
        raise ValueError('nonbackend manifest data differs')
    if before['backend']['version'] != current['backend']['version']:
        raise ValueError('backend version differs')

    identities = {}
    for role, raw, manifest in (('before', before_raw, before),
                                ('current', current_raw, current)):
        canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False).encode('utf-8')
        backend_raw = json.dumps(manifest['backend'], sort_keys=True,
                                 separators=(',', ':'), ensure_ascii=False,
                                 allow_nan=False).encode('utf-8')
        identities[role] = {
            'manifest_file_bytes': len(raw),
            'manifest_file_sha256': hashlib.sha256(raw).hexdigest(),
            'canonical_asset_manifest_sha256': hashlib.sha256(canonical).hexdigest(),
            'backend_sha256': hashlib.sha256(backend_raw).hexdigest(),
            'backend_files': dict(manifest['backend']['files']),
            'asset_file_hashes': dict(manifest['files']),
        }
    result = {
        'version': 'som.mac-full12-input-pair-data.v1',
        'decision_id': DECISION_ID,
        'proposal_manifest_sha256': PROPOSAL_MANIFEST_SHA256,
        'input_kind': 'untrusted_captured_manifest_bytes',
        'pair_data_conformant': True,
        'strict_whole_manifest_equal': before_raw == current_raw,
        'original_strict_input': 'not_passed',
        'original_full12': 'unrun',
        'prospective_full12': 'unrun',
        'identities': identities,
    }
    for field in _FALSE_FIELDS:
        result[field] = False
    return result


def check_literal_adoption_data(records):
    """Check four fixed untrusted projections. No receipt is authenticated.

    Exact schema: list[4] in fixed owner order. Each record contains owner,
    number, and mac_full12_input_manifest_v1. That field contains decision_id,
    proposal_manifest_sha256, adoption_text, response_proposal_sha256,
    strict_history, and final_source_tool_QA_binding. Binding contains only
    source_revision, source_files, tool_files, and qa_files. Each file map is
    nonempty. Source/QA keys are relative POSIX paths. Tool keys are absolute
    POSIX paths. All values are lowercase SHA256. No path is read here.

    Final maps and revisions remain untrusted input data. Equality cannot
    prove their completeness, current source, history, review, or chronology.
    Status, sealed, final-pass, activation, and authority keys are forbidden.
    """
    if type(records) is not list or len(records) != 4:
        raise ValueError('four builtin data records required')

    def hex_string(value, width):
        return (type(value) is str and len(value) == width
                and all(char in '0123456789abcdef' for char in value))

    def file_map(value, absolute):
        if type(value) is not dict or not 1 <= len(value) <= _MAX_MAP_ENTRIES:
            raise ValueError('nonempty bounded builtin file map required')
        copied = {}
        for path, digest in value.items():
            if (type(path) is not str or not 1 <= len(path) <= _MAX_PATH_CHARS
                    or not path.isascii() or any(ord(char) < 32 or ord(char) > 126 for char in path)
                    or '\\' in path or path.startswith('/') is not absolute):
                raise ValueError('file map path schema')
            parts = path[1:].split('/') if absolute else path.split('/')
            if any(part in ('', '.', '..') for part in parts):
                raise ValueError('file map path components')
            if not hex_string(digest, 64):
                raise ValueError('file map SHA256 schema')
            copied[path] = digest
        return copied

    checked = []
    shared_revision = None
    repository_hashes = {}
    tool_hashes = {}
    for record, expected in zip(records, _OWNERS):
        if (type(record) is not dict or any(type(key) is not str for key in record)
                or set(record) != {
                'owner', 'number', 'mac_full12_input_manifest_v1'}):
            raise ValueError('record exact data schema')
        if (type(record['owner']) is not str or type(record['number']) is not int
                or (record['owner'], record['number']) != expected):
            raise ValueError('fixed owner number and order')
        adoption = record['mac_full12_input_manifest_v1']
        if (type(adoption) is not dict or any(type(key) is not str for key in adoption)
                or set(adoption) != {
                'decision_id', 'proposal_manifest_sha256', 'adoption_text',
                'response_proposal_sha256', 'strict_history', 'final_source_tool_QA_binding'}):
            raise ValueError('adoption exact data schema')
        for key, expected_value in (
                ('decision_id', DECISION_ID),
                ('proposal_manifest_sha256', PROPOSAL_MANIFEST_SHA256),
                ('adoption_text', ADOPTION_TEXT),
                ('response_proposal_sha256', RESPONSE_PROPOSAL_SHA256),
                ('strict_history', 'strict_input_not_passed')):
            if type(adoption[key]) is not str or adoption[key] != expected_value:
                raise ValueError('exact adoption literal data')
        binding = adoption['final_source_tool_QA_binding']
        if (type(binding) is not dict or any(type(key) is not str for key in binding)
                or set(binding) != {'source_revision', 'source_files', 'tool_files', 'qa_files'}):
            raise ValueError('binding exact data schema')
        revision = binding['source_revision']
        if not hex_string(revision, 40):
            raise ValueError('source revision SHA1 schema')
        if shared_revision is None:
            shared_revision = revision
        elif revision != shared_revision:
            raise ValueError('source revision data differs')
        source_files = file_map(binding['source_files'], False)
        tool_files = file_map(binding['tool_files'], True)
        qa_files = file_map(binding['qa_files'], False)
        for mapping in (source_files, qa_files):
            for path, digest in mapping.items():
                if path in repository_hashes and repository_hashes[path] != digest:
                    raise ValueError('overlapping repository hash data differs')
                repository_hashes[path] = digest
        for path, digest in tool_files.items():
            if path in tool_hashes and tool_hashes[path] != digest:
                raise ValueError('overlapping tool hash data differs')
            tool_hashes[path] = digest
        checked.append({
            'owner': record['owner'], 'number': record['number'],
            'source_revision': revision, 'source_files': source_files,
            'tool_files': tool_files, 'qa_files': qa_files,
        })
    result = {
        'version': 'som.mac-full12-literal-adoption-data.v1',
        'decision_id': DECISION_ID,
        'proposal_manifest_sha256': PROPOSAL_MANIFEST_SHA256,
        'input_kind': 'untrusted_builtin_data_projection',
        'adoption_data_conformant': True,
        'strict_input': 'not_passed',
        'original_full12': 'unrun',
        'prospective_full12': 'unrun',
        'owner_maps': checked,
    }
    for field in _FALSE_FIELDS:
        result[field] = False
    return result
