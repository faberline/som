"""Finite controls for the actual inactive fixed-data helper.

All receipt-shaped records here are SYNTHETIC DATA ONLY. Their revisions,
source hashes, tool hashes, and QA maps do not describe any final source or
admitted receipt. No fixture supplies native, file, process, or review proof.
"""

import copy
import hashlib
import json
import unittest

import full12_input_manifest_rule as rule


_BEFORE_RAW = b'{"backend":{"files":{"Cargo.lock":"c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c","Cargo.toml":"6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1","block-contracts/composed-quality.json":"28f9bd134f31eb87fae5a680148447de3aaca9bad78fc10f0ac22b00cc40b0e5","block-contracts/composed.json":"3e58ed5b162cc4297324cc6da15ad9838c2a136d8a91762f63ac8d0c3af0b6b4","block-contracts/flow-v7-compact.json":"a2b96804e6f1c52a2ebf9e6310c6624b8b1b33d86b4f4763f07ffbeb645e103a","block-contracts/flow-v7.json":"d0ffbe92abbf8666e5e562022617e6eb81f4ef1c9fcc73b92fca8c718bba3f47","contracts/python-literal-encoding.v1.json":"7c25a1f45e54cec45e4053a5ceaf767188641ddde7da34dfce0747dc299dbf16","contracts/python-quality-v1/pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","contracts/related-business.v1.md":"9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380","contracts/related-flow.v1.md":"716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091","flow_compact/engine.py.tpl":"76923c4752bccd850b5fd55ebb77904aa3988170927ba22ee4acef8bbc556d5f","flow_compact/forward_support.py.tpl":"45250ff33586c4765733ccd69b8ca1bd52cb34893880bd89fe78d703dc29d557","src/business.rs":"b880c325a02c9a93a9e1470baa0416db54560b986511a71561583c1468c9df2f","src/cnl.rs":"8f14a9c5897b335066a619be907244f38f1ad3947d12ed7e1833fa7e77098789","src/cnl_semantics.rs":"4d4c6e706fbf83bc86ef5ae7f11cbdc884ba7a9d43ba7c03b7f7a5851311d834","src/compiler.rs":"9f131e728e2feb2b769d7150053a3949b5149a024a2cde1e38035c13c54e935f","src/composed.rs":"571c04a8e50f9856361dce94ebab813d8a8905aee9e2ea12dbd687f4f32d890c","src/flow.rs":"fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75","src/flow_compact.rs":"d4543446c2b5cd99fb020bbddb8f15ed3f1faecfc3ca8c08fb98249649687bb4","src/json_diagnostics.rs":"87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6","src/lib.rs":"60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f","src/main.rs":"042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5","src/planner.rs":"7958474d52f4e9b9f4fd1afc16e328428e32acc77dcbabd2ca3814f22610f12d","src/python_names.rs":"22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec","src/quality.rs":"71f5987b24559cb8507416dea15d6ddd6ac2dd5095c6c4c940b5c3a1328b0d21","src/related.rs":"ccc55e6018117a7087685de81da420da911832edaae51532e712be7b1b138dca","src/strict_json.rs":"06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161"},"version":"som.compiler-backend.v1"},"files":{"blocks.json":"c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e","legacy-blocks.json":"6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f","pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","quality-blocks.json":"0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc","quality.json":"6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21","ruff":"cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a"},"purpose":"som.rust-compiler.v1","version":"som.compiler-assets.v1"}\n'
_CURRENT_RAW = b'{"backend":{"files":{"Cargo.lock":"c7468fc0e5c51fc9031369b0dc292e47214ca5b7233dfb85216748107cc2493c","Cargo.toml":"6c673ba87bf98308042b8229727850956d90c7d24c03c8ea17b042c874d546b1","block-contracts/composed-quality.json":"28f9bd134f31eb87fae5a680148447de3aaca9bad78fc10f0ac22b00cc40b0e5","block-contracts/composed.json":"3e58ed5b162cc4297324cc6da15ad9838c2a136d8a91762f63ac8d0c3af0b6b4","block-contracts/flow-v7-compact.json":"a2b96804e6f1c52a2ebf9e6310c6624b8b1b33d86b4f4763f07ffbeb645e103a","block-contracts/flow-v7.json":"d0ffbe92abbf8666e5e562022617e6eb81f4ef1c9fcc73b92fca8c718bba3f47","contracts/native-tools-v1/CONTRACT.md":"47d72cabd57bdb380171a54f4dddeeabf25693c3781cabb483c572c162efac6e","contracts/native-tools-v1/artifacts.json":"959ba78afaffe3bbe7e9c675bdc17f49e9d6a8a282cd82e5ca149fb5f5eca193","contracts/python-literal-encoding.v1.json":"7c25a1f45e54cec45e4053a5ceaf767188641ddde7da34dfce0747dc299dbf16","contracts/python-quality-v1/pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","contracts/related-business.v1.md":"9e48b73fe018058bcf7473884e7536f3af13075bc9e18eee7da801a862c7c380","contracts/related-flow.v1.md":"716ae3f08c8ca79b50f757884b9140acc842d2213157c51ea4b9fd3b17d92091","flow_compact/engine.py.tpl":"76923c4752bccd850b5fd55ebb77904aa3988170927ba22ee4acef8bbc556d5f","flow_compact/forward_support.py.tpl":"45250ff33586c4765733ccd69b8ca1bd52cb34893880bd89fe78d703dc29d557","src/business.rs":"b880c325a02c9a93a9e1470baa0416db54560b986511a71561583c1468c9df2f","src/cnl.rs":"8f14a9c5897b335066a619be907244f38f1ad3947d12ed7e1833fa7e77098789","src/cnl_semantics.rs":"4d4c6e706fbf83bc86ef5ae7f11cbdc884ba7a9d43ba7c03b7f7a5851311d834","src/compiler.rs":"49c8309e102b1f7c686c8a975ed8b2c81d0133557c0443d8986474ca41cd9f3c","src/composed.rs":"571c04a8e50f9856361dce94ebab813d8a8905aee9e2ea12dbd687f4f32d890c","src/flow.rs":"fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75","src/flow_compact.rs":"d4543446c2b5cd99fb020bbddb8f15ed3f1faecfc3ca8c08fb98249649687bb4","src/json_diagnostics.rs":"87c38ad0077de8bb5eb1ee9edf5d4ecce7b5fce8fffcb19a4fc190b472d870b6","src/lib.rs":"60ea28026fbcbc72ca623717b81a47236a59e59ffe9f9914591e54730d8c158f","src/main.rs":"042c9abb969d17fa3f28dba7dfcec459ee63123da4ba5e3fd1645b1104a454c5","src/planner.rs":"7958474d52f4e9b9f4fd1afc16e328428e32acc77dcbabd2ca3814f22610f12d","src/python_names.rs":"22ab53da0538580165f792d3c47c0080a12c7dd8c2d86c5881c793827df6beec","src/quality.rs":"4522527da6a96bfee72365ab0b130c816022194fdff3de386cf604bb7c8e713b","src/related.rs":"ccc55e6018117a7087685de81da420da911832edaae51532e712be7b1b138dca","src/strict_json.rs":"06a8947fcfb9d7ade5afd963f06da72696d3845d64a957f881eded0666f37161"},"version":"som.compiler-backend.v1"},"files":{"blocks.json":"c7269e89d5f7b8fb2b36703cc2ccb6ef8201eccfc62fbffe526714fbd570311e","legacy-blocks.json":"6dd5359cc320cb804f87f3d16fa5899b5ae801f37c970e3b9ac4fced0cc0170f","pyproject.toml":"4cac7c6758372d4608895c53f1f427992d86988c85ff348c4eed7882efa762b4","quality-blocks.json":"0bfbf2a452a559a2661ac5c0c8355d0dfb9ce21cc310e505f121a51bb63e85cc","quality.json":"6ef259440b0965ed97c86a6a46bca743c1a2adf8ec9c1365c82e84dcbd67bd21","ruff":"cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a"},"purpose":"som.rust-compiler.v1","version":"som.compiler-assets.v1"}\n'
_EXPECTED_IDENTITIES = {
    'before': (3108,
               'c406d03c0edd98ab703a616c1c2be5d81f001966ac9c84280edce5765f91a138',
               'd95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa',
               '581ce38f29702c300342cdefe8fe5f1735cefcd1869d6485b44f30063a866711'),
    'current': (3325,
                'c22c0e87f69a3f52d1ab7a2708a401d6e8e17a6730dd05e7c1814d5c041c8bf9',
                '3d7f07bc80d6e88655a4c2ef6ddb4d5db8635f056c1e5ffba6e06d85ca8aa1c1',
                '92941b7adeb9c7555d4abdfb06d24b4a1bf92c9c1e2617a2a0c4cb5d3e529d0e'),
}
_ADOPTION_TEXT_SHA256 = 'ff6b5abac6f178360e09fa486c02c9238d43c440123df68033b72588116851b5'
_OWNER_NUMBERS = (('compiler-v1', 5), ('rust-output-v1', 8),
                  ('typescript-output-v1', 4), ('react-output-v1', 3))
_FALSE_FIELDS = ('native_authority', 'receipt_admission', 'source_admission',
                 'SourceSeal', 'product_acceptance', 'full12_passed', 'M12_passed')
_CLAIM_KEYS = ('authority', 'native_authority', 'status', 'sealed', 'final',
               'final_pass', 'final-pass', 'activation', 'activated',
               'SourceSeal', 'product_acceptance', 'M12_passed')


def _raw(manifest):
    return (json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def _manifest_pairs():
    return (('before', _BEFORE_RAW),
            ('current', _CURRENT_RAW))


def _changed_sha(digest):
    return ('1' if digest[0] == '0' else '0') + digest[1:]


def _synthetic_records():
    """Produce untrusted syntax data, never real or future final maps."""
    digest = lambda text: hashlib.sha256(('SYNTHETIC-DATA-ONLY:' + text).encode()).hexdigest()
    revision = hashlib.sha1(b'SYNTHETIC-DATA-ONLY:NOT-A-REAL-REVISION').hexdigest()
    result = []
    for owner, number in _OWNER_NUMBERS:
        result.append({
            'owner': owner,
            'number': number,
            'mac_full12_input_manifest_v1': {
                'decision_id': rule.DECISION_ID,
                'proposal_manifest_sha256': rule.PROPOSAL_MANIFEST_SHA256,
                'adoption_text': rule.ADOPTION_TEXT,
                'response_proposal_sha256': rule.RESPONSE_PROPOSAL_SHA256,
                'strict_history': 'strict_input_not_passed',
                'final_source_tool_QA_binding': {
                    'source_revision': revision,
                    'source_files': {
                        'synthetic/shared.py': digest('shared'),
                        'synthetic/' + owner + '.py': digest(owner + ':source'),
                    },
                    'tool_files': {'/synthetic/tools/python': digest('tool')},
                    'qa_files': {
                        'synthetic/qa/' + owner + '.py': digest(owner + ':own-QA'),
                        'synthetic/qa/' + owner + '-extra.py': digest(owner + ':extra-QA'),
                    },
                },
            },
        })
    return result


def _binding(record):
    return record['mac_full12_input_manifest_v1']['final_source_tool_QA_binding']


class Full12InputManifestRuleTests(unittest.TestCase):
    def assert_manifest_refuses(self, raw, reason):
        with self.assertRaisesRegex(ValueError, reason):
            rule._parse_exact_manifest(raw)

    def assert_projection_refuses(self, records, reason):
        with self.assertRaisesRegex(ValueError, reason):
            rule.check_literal_adoption_data(records)

    def assert_no_authority(self, result):
        for field in _FALSE_FIELDS:
            self.assertIs(result[field], False, field)
        self.assertEqual(result['original_full12'], 'unrun')
        self.assertEqual(result['prospective_full12'], 'unrun')

    def test_P01_complete_fixed_pair_recomputes_three_identities(self):
        self.assertEqual(rule._BEFORE_MANIFEST_RAW, _BEFORE_RAW)
        self.assertEqual(rule._CURRENT_MANIFEST_RAW, _CURRENT_RAW)
        result = rule.compare_exact_input_pair(_BEFORE_RAW,
                                               _CURRENT_RAW)
        self.assertIs(result['pair_data_conformant'], True)
        self.assertIs(result['strict_whole_manifest_equal'], False)
        self.assertEqual(result['original_strict_input'], 'not_passed')
        self.assert_no_authority(result)
        self.assertEqual(result['decision_id'],
                         'som.mac-full12-input-manifest.v1:829efe8e49e32bbd58b655e854443eea593a467775fdc7e74d0c4d44dd570061')
        self.assertEqual(result['proposal_manifest_sha256'],
                         'f95e13b9660a85f68c60fdd4f2c47f6b580ff308143fa70113829e5d7e6d9f58')
        for role, raw in _manifest_pairs():
            with self.subTest(role=role):
                manifest = rule._parse_exact_manifest(raw)
                self.assertEqual(manifest, json.loads(raw))
                actual = result['identities'][role]
                expected = _EXPECTED_IDENTITIES[role]
                self.assertEqual(tuple(actual[key] for key in (
                    'manifest_file_bytes', 'manifest_file_sha256',
                    'canonical_asset_manifest_sha256', 'backend_sha256')), expected)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected[1])
                self.assertEqual(hashlib.sha256(raw[:-1]).hexdigest(), expected[2])
                backend_raw = json.dumps(manifest['backend'], sort_keys=True,
                                         separators=(',', ':'), ensure_ascii=False).encode()
                self.assertEqual(hashlib.sha256(backend_raw).hexdigest(), expected[3])
                self.assertEqual(actual['backend_files'], manifest['backend']['files'])
                self.assertEqual(actual['asset_file_hashes'], manifest['files'])

    def test_P02_every_backend_digest_change_refuses(self):
        for role, raw in _manifest_pairs():
            original = json.loads(raw)
            for path in original['backend']['files']:
                with self.subTest(role=role, path=path):
                    changed = copy.deepcopy(original)
                    old = changed['backend']['files'][path]
                    changed['backend']['files'][path] = _changed_sha(old)
                    self.assert_manifest_refuses(_raw(changed), 'not a fixed approved manifest')

    def test_P02_missing_extra_renamed_equal_count_backend_entries_refuse(self):
        for role, raw in _manifest_pairs():
            original = json.loads(raw)
            for path in original['backend']['files']:
                for operation in ('missing', 'extra', 'renamed', 'equal_count_substitution'):
                    with self.subTest(role=role, path=path, operation=operation):
                        changed = copy.deepcopy(original)
                        files = changed['backend']['files']
                        if operation == 'missing':
                            del files[path]
                        elif operation == 'extra':
                            files['synthetic-extra.rs'] = files[path]
                        elif operation == 'renamed':
                            files['synthetic-renamed/' + path] = files.pop(path)
                        else:
                            files['synthetic-substitution.rs'] = files.pop(path)
                        reason = ('manifest backend file schema' if operation in ('missing', 'extra')
                                  else 'not a fixed approved manifest')
                        self.assert_manifest_refuses(_raw(changed), reason)

    def test_P02_backend_version_and_digest_schema_refuse(self):
        for role, raw in _manifest_pairs():
            for value in ('other', 1, True, None, [], {}):
                with self.subTest(role=role, version=repr(value)):
                    changed = json.loads(raw)
                    changed['backend']['version'] = value
                    self.assert_manifest_refuses(_raw(changed), 'manifest backend schema or version')
            for value in ('A' * 64, 'x' * 64, 'a' * 63, 'a' * 65, 1, True, None, [], {}):
                with self.subTest(role=role, digest=repr(value)):
                    changed = json.loads(raw)
                    changed['backend']['files']['src/lib.rs'] = value
                    self.assert_manifest_refuses(_raw(changed), 'manifest file SHA schema')

    def test_P03_every_nonbackend_value_change_refuses(self):
        for role, raw in _manifest_pairs():
            for field in ('version', 'purpose'):
                with self.subTest(role=role, field=field):
                    changed = json.loads(raw)
                    changed[field] += '-changed'
                    self.assert_manifest_refuses(_raw(changed), 'manifest version or purpose')
            for filename in json.loads(raw)['files']:
                with self.subTest(role=role, filename=filename):
                    changed = json.loads(raw)
                    changed['files'][filename] = _changed_sha(changed['files'][filename])
                    self.assert_manifest_refuses(_raw(changed), 'not a fixed approved manifest')
                for value in ('A' * 64, 'a' * 63, 'a' * 65, 1, True, None, [], {}):
                    with self.subTest(role=role, filename=filename, wrong_type_or_SHA=repr(value)):
                        changed = json.loads(raw)
                        changed['files'][filename] = value
                        self.assert_manifest_refuses(_raw(changed), 'manifest file SHA schema')
                with self.subTest(role=role, missing_asset=filename):
                    changed = json.loads(raw)
                    del changed['files'][filename]
                    self.assert_manifest_refuses(_raw(changed), 'manifest asset schema')

    def test_P03_schema_unknown_missing_keys_and_container_types_refuse(self):
        for role, raw in _manifest_pairs():
            for field in ('version', 'purpose', 'backend', 'files'):
                with self.subTest(role=role, missing=field):
                    changed = json.loads(raw)
                    del changed[field]
                    self.assert_manifest_refuses(_raw(changed), 'manifest top schema')
            for level, reason in (('top', 'manifest top schema'),
                                  ('backend', 'manifest backend schema or version'),
                                  ('assets', 'manifest asset schema')):
                with self.subTest(role=role, unknown=level):
                    changed = json.loads(raw)
                    target = changed if level == 'top' else changed['backend' if level == 'backend' else 'files']
                    target['unexpected'] = 'a' * 64
                    self.assert_manifest_refuses(_raw(changed), reason)
            cases = ((None, [], 'manifest top schema'),
                     ('version', True, 'manifest version or purpose'),
                     ('purpose', None, 'manifest version or purpose'),
                     ('backend', [], 'manifest backend schema or version'),
                     ('files', [], 'manifest asset schema'))
            for field, value, reason in cases:
                with self.subTest(role=role, field=field, wrong_type=type(value).__name__):
                    changed = json.loads(raw)
                    if field is None:
                        changed = value
                    else:
                        changed[field] = value
                    self.assert_manifest_refuses(_raw(changed), reason)
            changed = json.loads(raw)
            changed['backend']['files'] = []
            self.assert_manifest_refuses(_raw(changed), 'manifest backend file schema')
            for key in ('version', 'files'):
                with self.subTest(role=role, missing_backend=key):
                    changed = json.loads(raw)
                    del changed['backend'][key]
                    self.assert_manifest_refuses(_raw(changed), 'manifest backend schema or version')

    def test_P03_UTF8_duplicate_escaped_nonfinite_and_surrogate_refuse(self):
        self.assert_manifest_refuses(b'{}\xff\n', 'manifest UTF8')
        for raw in (b'{"x":1,"x":2}\n', b'{"x":1,"\\u0078":2}\n'):
            with self.subTest(duplicate=raw):
                self.assert_manifest_refuses(raw, 'manifest duplicate key')
        for value in (b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(nonfinite=value):
                self.assert_manifest_refuses(b'{"x":' + value + b'}\n', 'manifest nonfinite value')
        for value in (b'1e999', b'-1e999', b'0.5'):
            with self.subTest(float_or_overflow=value):
                self.assert_manifest_refuses(b'{"x":' + value + b'}\n', 'manifest floating number')
        self.assert_manifest_refuses(
            _BEFORE_RAW.replace(b'"src/lib.rs"', b'"\\ud800"', 1),
            'manifest canonical encoding')
        for raw in (b'{', b'{} trailing\n', b'{"x":1,}\n', b'"unterminated\n'):
            with self.subTest(syntax=raw):
                reason = 'manifest byte bound' if len(raw) < 2 else 'manifest JSON syntax'
                self.assert_manifest_refuses(raw, reason)

    def test_P03_canonical_spelling_and_one_LF_are_exact(self):
        for role, raw in _manifest_pairs():
            manifest = json.loads(raw)
            variants = (
                raw[:-1], raw + b'\n', raw[:-1] + b'\r\n', b' ' + raw,
                raw.replace(b':', b': ', 1),
                (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode(),
                (json.dumps(dict(reversed(tuple(manifest.items()))), separators=(',', ':')) + '\n').encode(),
                raw.replace(b'"purpose"', b'"purp\\u006fse"', 1),
            )
            for index, variant in enumerate(variants):
                with self.subTest(role=role, variant=index):
                    self.assert_manifest_refuses(variant, 'manifest canonical bytes and one LF')

    def test_P03_builtin_bytes_and_finite_size_depth_bounds(self):
        class BytesSubclass(bytes):
            pass
        for value in (BytesSubclass(_BEFORE_RAW), bytearray(_BEFORE_RAW),
                      memoryview(_BEFORE_RAW), _BEFORE_RAW.decode(), None):
            with self.subTest(type=type(value).__name__):
                self.assert_manifest_refuses(value, 'manifest must be exact builtin bytes')
        for raw in (b'', b' ', b' ' * 8193):
            with self.subTest(size=len(raw)):
                self.assert_manifest_refuses(raw, 'manifest byte bound')
        self.assert_manifest_refuses(b' ' * 8192, 'manifest JSON syntax')
        self.assert_manifest_refuses(b'[' * 9 + b'0' + b']' * 9 + b'\n', 'manifest depth bound')
        self.assert_manifest_refuses(b'[' * 8 + b'0' + b']' * 8 + b'\n', 'manifest top schema')
        # Quoted braces and an escaped quote cannot consume the nesting bound.
        quoted = json.dumps({'x': '{' * 40 + '"' + '[' * 40}).encode() + b'\n'
        self.assert_manifest_refuses(quoted, 'manifest top schema')

    def test_P05_fixed_before_current_roles_cannot_be_swapped_or_copied(self):
        before, current = _BEFORE_RAW, _CURRENT_RAW
        for left, right in ((current, before), (before, before), (current, current)):
            with self.subTest(left=hashlib.sha256(left).hexdigest(), right=hashlib.sha256(right).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'fixed before/current argument roles'):
                    rule.compare_exact_input_pair(left, right)

    def test_P10_four_synthetic_projections_keep_distinct_complete_QA_maps(self):
        self.assertEqual(hashlib.sha256(rule.ADOPTION_TEXT.encode()).hexdigest(), _ADOPTION_TEXT_SHA256)
        records = _synthetic_records()
        saved = copy.deepcopy(records)
        result = rule.check_literal_adoption_data(records)
        self.assertEqual(records, saved)
        self.assertIs(result['adoption_data_conformant'], True)
        self.assertEqual(result['input_kind'], 'untrusted_builtin_data_projection')
        self.assertEqual(result['strict_input'], 'not_passed')
        self.assert_no_authority(result)
        for row, record in zip(result['owner_maps'], records):
            self.assertEqual((row['owner'], row['number']), (record['owner'], record['number']))
            for key, value in _binding(record).items():
                self.assertEqual(row[key], value)
        self.assertEqual(len({tuple(row['qa_files']) for row in result['owner_maps']}), 4)

    def test_P10_missing_extra_duplicate_stale_owner_number_and_order_refuse(self):
        for size in (0, 1, 2, 3, 5):
            with self.subTest(size=size):
                records = _synthetic_records()
                records = records[:size] if size < 4 else records + [copy.deepcopy(records[0])]
                self.assert_projection_refuses(records, 'four builtin data records required')
        for index in range(4):
            for field, value in (('owner', 'other'), ('number', 0), ('number', True)):
                with self.subTest(index=index, field=field, value=value):
                    records = _synthetic_records()
                    records[index][field] = value
                    self.assert_projection_refuses(records, 'fixed owner number and order')
            records = _synthetic_records()
            records[index] = copy.deepcopy(records[(index + 1) % 4])
            self.assert_projection_refuses(records, 'fixed owner number and order')
        self.assert_projection_refuses(list(reversed(_synthetic_records())), 'fixed owner number and order')

    def test_P10_exact_literals_and_every_forbidden_claim_key_refuse(self):
        for index in range(4):
            for key in ('decision_id', 'proposal_manifest_sha256', 'adoption_text',
                        'response_proposal_sha256', 'strict_history'):
                with self.subTest(index=index, literal=key):
                    records = _synthetic_records()
                    records[index]['mac_full12_input_manifest_v1'][key] += ' changed'
                    self.assert_projection_refuses(records, 'exact adoption literal data')
            for level, reason in (('record', 'record exact data schema'),
                                  ('adoption', 'adoption exact data schema'),
                                  ('binding', 'binding exact data schema')):
                for key in _CLAIM_KEYS + ('unexpected',):
                    for value in (False, True):
                        with self.subTest(index=index, level=level, claim=key, value=value):
                            records = _synthetic_records()
                            target = records[index]
                            if level != 'record':
                                target = target['mac_full12_input_manifest_v1']
                            if level == 'binding':
                                target = target['final_source_tool_QA_binding']
                            target[key] = value
                            self.assert_projection_refuses(records, reason)

    def test_P10_missing_required_keys_and_builtin_types_refuse(self):
        class ListSubclass(list):
            pass
        class DictSubclass(dict):
            pass
        class StrSubclass(str):
            pass
        class IntSubclass(int):
            pass
        for records in (tuple(_synthetic_records()), ListSubclass(_synthetic_records()), None):
            self.assert_projection_refuses(records, 'four builtin data records required')
        for level, reason in (('record', 'record exact data schema'),
                              ('adoption', 'adoption exact data schema'),
                              ('binding', 'binding exact data schema')):
            original = _synthetic_records()[0]
            target = original if level == 'record' else original['mac_full12_input_manifest_v1']
            if level == 'binding':
                target = target['final_source_tool_QA_binding']
            for key in tuple(target):
                with self.subTest(level=level, missing=key):
                    records = _synthetic_records()
                    changed = records[0] if level == 'record' else records[0]['mac_full12_input_manifest_v1']
                    if level == 'binding':
                        changed = changed['final_source_tool_QA_binding']
                    del changed[key]
                    self.assert_projection_refuses(records, reason)
            for operation in ('dict_subclass', 'key_subclass'):
                with self.subTest(level=level, operation=operation):
                    records = _synthetic_records()
                    if level == 'record':
                        parent, key = records, 0
                    elif level == 'adoption':
                        parent, key = records[0], 'mac_full12_input_manifest_v1'
                    else:
                        parent = records[0]['mac_full12_input_manifest_v1']
                        key = 'final_source_tool_QA_binding'
                    value = parent[key]
                    if operation == 'dict_subclass':
                        parent[key] = DictSubclass(value)
                    else:
                        first = next(iter(value))
                        parent[key] = {StrSubclass(k) if k == first else k: v for k, v in value.items()}
                    self.assert_projection_refuses(records, reason)
            for wrong in (None, [], 'wrong', 1, True):
                with self.subTest(level=level, wrong_container=type(wrong).__name__):
                    records = _synthetic_records()
                    if level == 'record':
                        records[0] = wrong
                    elif level == 'adoption':
                        records[0]['mac_full12_input_manifest_v1'] = wrong
                    else:
                        records[0]['mac_full12_input_manifest_v1']['final_source_tool_QA_binding'] = wrong
                    self.assert_projection_refuses(records, reason)
        for field, value in (('owner', StrSubclass('compiler-v1')), ('number', IntSubclass(5))):
            records = _synthetic_records()
            records[0][field] = value
            self.assert_projection_refuses(records, 'fixed owner number and order')
        for field in ('decision_id', 'proposal_manifest_sha256', 'adoption_text',
                      'response_proposal_sha256', 'strict_history'):
            records = _synthetic_records()
            adoption = records[0]['mac_full12_input_manifest_v1']
            adoption[field] = StrSubclass(adoption[field])
            self.assert_projection_refuses(records, 'exact adoption literal data')

    def test_P10_map_revision_syntax_bounds_and_subclasses_refuse(self):
        class DictSubclass(dict):
            pass
        class StrSubclass(str):
            pass
        for revision in ('a' * 39, 'a' * 41, 'A' * 40, 'x' * 40, 1, True, None,
                         StrSubclass('a' * 40)):
            with self.subTest(revision=repr(revision)):
                records = _synthetic_records()
                _binding(records[0])['source_revision'] = revision
                self.assert_projection_refuses(records, 'source revision SHA1 schema')
        for field in ('source_files', 'tool_files', 'qa_files'):
            for value in ({}, [], None, DictSubclass({'synthetic/x': 'a' * 64}),
                          {'synthetic/' + str(i): 'a' * 64 for i in range(4097)}):
                with self.subTest(field=field, wrong_map=type(value).__name__, size=len(value) if value is not None else None):
                    records = _synthetic_records()
                    _binding(records[0])[field] = value
                    self.assert_projection_refuses(records, 'nonempty bounded builtin file map required')
            absolute = field == 'tool_files'
            good_path = '/synthetic/x' if absolute else 'synthetic/x'
            bad_paths = ('', '/' if absolute else '/absolute', 'a//b', 'a/./b', 'a/../b',
                         'a\\b', 'a\x00b', 'nonascii-é', 'x' * 513, StrSubclass(good_path))
            if absolute:
                bad_paths = tuple(path if path in ('', '/') or type(path) is StrSubclass else '/' + path for path in bad_paths) + ('relative',)
            for path in bad_paths:
                with self.subTest(field=field, path=repr(path)):
                    records = _synthetic_records()
                    _binding(records[0])[field] = {path: 'a' * 64}
                    self.assert_projection_refuses(records, 'file map path (schema|components)')
            for digest in ('a' * 63, 'a' * 65, 'A' * 64, 'x' * 64, 1, True, [], {},
                           StrSubclass('a' * 64)):
                with self.subTest(field=field, digest=repr(digest)):
                    records = _synthetic_records()
                    _binding(records[0])[field] = {good_path: digest}
                    self.assert_projection_refuses(records, 'file map SHA256 schema')
            circular = {}
            circular[good_path] = circular
            records = _synthetic_records()
            _binding(records[0])[field] = circular
            self.assert_projection_refuses(records, 'file map SHA256 schema')
            records = _synthetic_records()
            prefix = '/' if absolute else ''
            boundary_map = {prefix + 'synthetic-boundary/' + str(i): 'a' * 64 for i in range(4096)}
            _binding(records[0])[field] = boundary_map
            bounded_result = rule.check_literal_adoption_data(records)
            self.assertEqual(len(bounded_result['owner_maps'][0][field]), 4096)
            records = _synthetic_records()
            boundary_path = '/' + 'x' * 511 if absolute else 'x' * 512
            _binding(records[0])[field] = {boundary_path: 'a' * 64}
            bounded_result = rule.check_literal_adoption_data(records)
            self.assertEqual(bounded_result['owner_maps'][0][field], {boundary_path: 'a' * 64})

    def test_P10_source_revision_and_overlapping_hash_data_must_agree(self):
        records = _synthetic_records()
        _binding(records[1])['source_revision'] = 'a' * 40
        self.assert_projection_refuses(records, 'source revision data differs')
        for field, path, reason in (
                ('source_files', 'synthetic/shared.py', 'overlapping repository hash data differs'),
                ('tool_files', '/synthetic/tools/python', 'overlapping tool hash data differs'),
                ('qa_files', 'synthetic/shared.py', 'overlapping repository hash data differs')):
            with self.subTest(field=field):
                records = _synthetic_records()
                _binding(records[1])[field][path] = 'a' * 64
                self.assert_projection_refuses(records, reason)
        records = _synthetic_records()
        qa_path, qa_digest = next(iter(_binding(records[0])['qa_files'].items()))
        _binding(records[1])['qa_files'][qa_path] = _changed_sha(qa_digest)
        self.assert_projection_refuses(records, 'overlapping repository hash data differs')

    def test_P10_returned_data_cannot_mutate_input_or_next_diagnostic(self):
        records = _synthetic_records()
        saved = copy.deepcopy(records)
        result = rule.check_literal_adoption_data(records)
        row = result['owner_maps'][0]
        for field in ('source_files', 'tool_files', 'qa_files'):
            self.assertIsNot(row[field], _binding(records[0])[field])
            row[field].clear()
        self.assertEqual(records, saved)
        again = rule.check_literal_adoption_data(records)
        self.assertEqual(again['owner_maps'][0]['qa_files'], _binding(records[0])['qa_files'])
        pair = rule.compare_exact_input_pair(_BEFORE_RAW, _CURRENT_RAW)
        pair['identities']['before']['backend_files'].clear()
        again_pair = rule.compare_exact_input_pair(_BEFORE_RAW, _CURRENT_RAW)
        self.assertEqual(len(again_pair['identities']['before']['backend_files']), 27)

    def test_P13_data_results_never_claim_full_acceptance_or_rewrite_history(self):
        pair = rule.compare_exact_input_pair(_BEFORE_RAW, _CURRENT_RAW)
        adoption = rule.check_literal_adoption_data(_synthetic_records())
        self.assertEqual(pair['version'], 'som.mac-full12-input-pair-data.v1')
        self.assertEqual(adoption['version'], 'som.mac-full12-literal-adoption-data.v1')
        self.assertEqual(pair['input_kind'], 'untrusted_captured_manifest_bytes')
        self.assert_no_authority(pair)
        self.assert_no_authority(adoption)
        self.assertIs(pair['strict_whole_manifest_equal'], False)
        self.assertEqual(pair['original_strict_input'], 'not_passed')
        self.assertEqual(adoption['strict_input'], 'not_passed')
        self.assertNotIn('original_run', pair)
        self.assertNotIn('original_run', adoption)
        self.assertNotIn('strict_passed', pair)
        self.assertNotIn('strict_passed', adoption)


if __name__ == '__main__':
    unittest.main(verbosity=2)
