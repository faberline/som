"""Exact four-leaf native comparison for the current Rust/TypeScript baseline.

This does not extend the frozen experimental compile validator's target scope.
It uses its existing source-only factory derivation, then verifies full raw bytes.
"""
from __future__ import annotations
import copy
import hashlib
import json

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_native_digests.py'
FACTORY = BASE + 'experimental_native_compile.py'
RAW = BASE + 'raw_digest_compat.py'
LEAVES = ('/bundle/assets/backend/sha256', '/bundle/assets/target_contract/sha256',
          '/bundle/assets/formatter/sha256', '/bundle/hashes/payload_sha256')


def require(value, message):
    if not value:
        raise ValueError(message)


def authorities(ctx, target):
    require(target in {'typescript', 'rust', 'python'} and ctx.qa_mode == 'native-qa-v1'
            and ctx.helper_revision == ctx.candidate_revision
            and ctx.qa_source_authority.get('source_authority_active') is True,
            'sealed same-candidate native source authority required')
    require({SELF, FACTORY, RAW} <= set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'native digest/factory helper closure is outside exact source admission')
    key = 'typescript-regression-native-digest-authority-' + target
    if key in ctx.cache:
        return ctx.cache[key]
    policy_path = ('som-rs/contracts/compiler-v1/native-tools.json' if target == 'python' else
                   'som-rs/contracts/' + target + '-output-v1/native-tools-compatibility.json')
    policy = json.loads(ctx.sources.read(policy_path))
    leaves = set(LEAVES) - ({'/bundle/assets/target_contract/sha256'} if target == 'python' else set())
    require(policy['qa_authority_state'] == 'SEALED_AUTHORITY' and policy['sealing_dependencies'] == []
            and len(policy['allowed_compile_digest_leaves']) == len(leaves)
            and {row['path'] for row in policy['allowed_compile_digest_leaves']} == leaves,
            'exact sealed native digest-leaf policy required')
    shared_key = 'typescript_regression_native_source_factory_values'
    if shared_key not in ctx.cache:
        ctx.sources.read(SELF)
        factory = ctx.module('typescript_regression_native_factory', FACTORY)
        repository = factory.Repository(ctx.repo)
        after, receipts = factory.after_pins(repository)
        require(repository.commit == ctx.helper_revision, 'native factory derives another source')
        for path, pin in repository.pins.items():
            require(hashlib.sha256(ctx.sources.read(path)).hexdigest() == pin, 'factory owning source differs')
        ctx.cache[shared_key] = factory, after
    factory, after = ctx.cache[shared_key]
    names = (('backend', 'render_only_formatter', 'formatted_formatter') if target == 'python' else
             ('backend', 'target_contract', 'formatter'))
    before_values = {name: factory.BEFORE[target + '_' + name] for name in names}
    after_values = {name: after[target + '_' + name] for name in names}
    raw = ctx.module('typescript_regression_raw_digest', RAW)
    result = factory, raw, before_values, after_values
    ctx.cache[key] = result
    return result


def compare(ctx, target, before_raw, after_raw):
    factory, raw, before_values, after_values = authorities(ctx, target)
    before, after = json.loads(before_raw), json.loads(after_raw)
    require(type(before) is dict and type(after) is dict and before.get('status') == after.get('status') == 'ok'
            and before['bundle'].get('target', 'python') == after['bundle'].get('target', 'python') == target,
            'matching successful target bundles required')
    pins = []
    names = ('backend', 'formatter') if target == 'python' else ('backend', 'target_contract', 'formatter')
    for name in names:
        factory_name = name
        if target == 'python' and name == 'formatter':
            factory_name = ('formatted_formatter' if before['bundle']['prompt']['version'] in
                            {'som.prompt.v6', 'som.prompt.v7'} else 'render_only_formatter')
        old, new = before['bundle']['assets'][name], after['bundle']['assets'][name]
        require(type(old) is dict and type(new) is dict and set(old) == set(new) == {'id', 'sha256'}
                and old['id'] == new['id'] == factory.IDS[target + '_' + factory_name]
                and old['sha256'] == before_values[factory_name]
                and new['sha256'] == after_values[factory_name],
                'descriptor differs from owning source factory: ' + name)
        pins.append(('/bundle/assets/' + name + '/sha256', old['sha256'], new['sha256']))
    for document in (before, after):
        bundle = document['bundle']
        require(set(bundle['hashes']) == {'prompt_sha256', 'dsl_sha256', 'payload_sha256'}, 'bundle hash universe differs')
        for name, value in (('prompt_sha256', bundle['prompt']), ('dsl_sha256', bundle['dsl']),
                            ('payload_sha256', {k: v for k, v in bundle.items() if k != 'hashes'})):
            require(bundle['hashes'][name] == factory.value_sha(value), 'complete canonical bundle hash differs')
    pins.append(('/bundle/hashes/payload_sha256', before['bundle']['hashes']['payload_sha256'],
                 after['bundle']['hashes']['payload_sha256']))
    raw.verify_raw(before_raw, after_raw, pins)
    return {'target': target, 'before_raw_sha256': hashlib.sha256(before_raw).hexdigest(),
            'after_raw_sha256': hashlib.sha256(after_raw).hexdigest(),
            'exact_digest_pins': [list(pin) for pin in pins], 'product_acceptance_passed': False}


def rebase(ctx, target, before_raw, after_raw):
    proof = compare(ctx, target, before_raw, after_raw)
    old, new = json.loads(before_raw)['bundle'], json.loads(after_raw)['bundle']
    result = copy.deepcopy(old)
    for name in ('backend', 'target_contract', 'formatter'):
        result['assets'][name]['sha256'] = new['assets'][name]['sha256']
    factory, _, _, _ = authorities(ctx, target)
    result['hashes']['payload_sha256'] = factory.value_sha({k: v for k, v in result.items() if k != 'hashes'})
    require(result == new, 'rebase changed more than the admitted source-owned digest leaves')
    return result, proof


def historical_python_replay(ctx, expected_raw, current_raw, original_check):
    """Keep the old five-descriptor replay and the new three-leaf role separate.

    The intermediate is source-derived metadata, never a recorded HTTP answer
    or a before compiler process. The old assertion still checks every original
    prompt/DSL field and every descriptor outside its original five-member set.
    """
    factory, _, before_values, _ = authorities(ctx, 'python')
    current = json.loads(current_raw)
    intermediate = copy.deepcopy(current)
    bundle = intermediate['bundle']
    formatter = ('formatted_formatter' if bundle['prompt']['version'] in
                 {'som.prompt.v6', 'som.prompt.v7'} else 'render_only_formatter')
    bundle['assets']['backend']['sha256'] = before_values['backend']
    bundle['assets']['formatter']['sha256'] = before_values[formatter]
    bundle['hashes']['payload_sha256'] = factory.value_sha({k: v for k, v in bundle.items() if k != 'hashes'})
    raw = json.dumps(intermediate, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    proof = compare(ctx, 'python', raw, current_raw)
    problem = original_check(expected_raw, raw)
    return problem, {'role': 'source-derived-metadata-intermediate', 'recorded_before_http': False,
        'historical_raw_sha256': hashlib.sha256(expected_raw).hexdigest(), 'native_digest_proof': proof,
        'original_historical_assertion_passed': problem is None, 'product_acceptance_passed': False}
