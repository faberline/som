# Synthetic-only compatibility prototype

These files are scratch source for review. They are not attached to a workflow.
`synthetic_only.py` cannot accept real host, role, case, request, product, or
policy identities. Its fixed toy source bytes are not a committed source vector,
sealed contract, accepted baseline, or native tool authority. Every returned
proof says `SYNTHETIC / NO ACCEPTANCE` and `acceptance_granted` is false.
The separate `strict_adapter.compare_native` remains disabled. This prototype
has no link to or bypass for that entry point.

Only the exact fixture identity and toy data can pass. The toy backend hashes
are derived from two immutable literal synthetic source strings. The fixture
verifies exact envelope fields, IDs, fixed prompt and DSL, canonical synthetic
prompt/DSL/payload hashes, source-derived before/after backend digests, raw
bytes outside exactly two pinned SHA leaves, and one exact named toy product.
The error fixture requires equal non-200 status and identical raw error bytes.
No caller can supply policy, descriptor expected values, a role authority, or a
verifier callback. This is an algorithm demonstration, not a real case validator.

## Reused raw comparison primitive

A caller passes raw immutable bytes of the existing `raw_digest_compat.py`.
The prototype checks SHA-256
`a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896`
before compiling those exact bytes into a separate module object. It does not
import through `PYTHONPATH`, execute an unchecked module, or reopen a file after
verification. The helper is only a raw byte primitive, not an authority. This
check does not prove a Git checkout, source closure, sealed policy, or deployed
artifact identity. Future production work needs separately reviewed, source-bound
and host/role/case-bound authorization and cannot reuse this fixture as such.

## GitHub-only tests

No tests, imports of these files, project programs, builds, or services ran on
the local authoring host. Stage these files in the runner, check out M12 source
`f59e28d910ff4adb16e3becfdc82bd2978ca84fa`, and point to the exact existing
`som-rs/scripts/hosted_readiness/raw_digest_compat.py` as the helper source.
From the directory holding the scratch files, the intended command is:

```
SOM_SYNTHETIC_RAW_HELPER=/absolute/path/to/checkout/som-rs/scripts/hosted_readiness/raw_digest_compat.py python3 -m unittest -v test_synthetic_only
```

The tests include a toy positive case and refusal cases for host, role, case,
request identity, unknown/extra leaves, descriptor ID and source pin, current
and rebased payload, prompt/DSL hashes, duplicate keys, raw-byte drift, product
paths/bytes, non-200 errors, and changed helper bytes before compilation. These
are synthetic fixtures only. User-facing runtime and acceptance tests remain
separate and must also run on GitHub.
