# M6 native comparator scratch

This is unused draft work. It grants no acceptance, policy, product, or release authority.
It does not edit or replace the existing M6 collector, auditor, or frozen contract.

`strict_adapter.py` exposes two strict comparisons. `compare_raw` checks the exact
HTTP status and immutable response bytes. `compare_products` checks the supplied
complete file maps and each file byte. Their immutable proof objects record
content hashes; they do not authenticate provenance or bind an input, source,
host, or named case. An owning acceptance gate must prove all of those separately.
A successful comparison does not mean a product or milestone passed.

`compare_native` is a deliberately disabled future integration seam. Every call
raises `CompatibilityUnavailable` before reading or invoking any caller-supplied
policy or authority. There is no switch, injectable callback, source-check stub,
allowed digest path, or bypass that can enable native compatibility here.
The disabled behavior is intentional. The expected native leaf pairs and their
reviewed source-bound authority are still unsealed and unavailable.

The test source covers strict raw/status equality, changed descriptor IDs and
payload bytes, raw non-allowlisted drift, product set and byte drift, bad paths,
mutable input, immutable proof results, and universal native denial under forged
or malformed policy, host, role, leaf, callback, hash, payload and product cases.
These native cases only verify global denial. They do not claim that the future
individual validators have been implemented.

## Existing M12 primitive and authority boundary

At M12 Git source `f59e28d910ff4adb16e3becfdc82bd2978ca84fa`, the inspected
`som-rs/scripts/hosted_readiness/raw_digest_compat.py` has SHA-256
`a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896`.
Its `verify_raw` and `verify_frozen` are raw JSON digest-leaf byte primitives;
their caller must independently authorize each path and old/new pin. They do
not establish authority, payload validity, HTTP status, or product acceptance.

The inspected `som-rs/scripts/hosted_readiness/experimental_native_compile.py`
has SHA-256 `d2f85ffbb58b5a0114b1e3e61aec6776e75d02477597364d4f7306ecfe8c1b3e`.
Its source-bound factory checks are explicitly experimental and admit only
Python/Rust compile scope. They do not authorize React or full TypeScript.
This scratch adapter imports neither file and duplicates neither implementation.
These hashes are inspection records, not acceptance pins or executable authority.

A future reviewed adapter should first validate a sealed React-specific policy,
verify the exact committed authority and helper source bytes and their complete
source vectors, bind each case and host to independently derived old/new values,
validate raw response schema/status and canonical prompt/DSL/payload hashes,
and only then call the reviewed low-level raw-digest primitive. It must verify
all product paths/bytes separately. Preserve the acyclic dependency graph:
observed evidence and expected target/backend hashes cannot enter the source or
receipt vectors that determine those same hashes. No draft or PENDING policy
value may turn on an exception. Unknown cases, targets, hosts, roles, digest
leaves and malformed data must fail closed.

## Verification

Do not run tests, imports, builds or services on this local host. No local test
was run during authoring. These files are under `/private/tmp` only and are not
attached to any GitHub workflow.

After the owner reviews and safely stages the exact sources in a GH runner, the
intended synthetic fixture command is:

```
python3 -m unittest -v test_strict_adapter
```

Run from the directory containing both files. That runner command has not yet
been executed. It validates only this scratch adapter's narrow behavior. Full
M6/M12 product and acceptance gates remain separate and unrun on this adapter.
