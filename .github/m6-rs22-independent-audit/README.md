# RS22 independent data audit draft

Draft only. Neither source nor fixtures have run. Review both before use.
They use no product code, network, subprocess, or service. They read restored
raw evidence and pinned public inputs; they do not print private content.

Source format is pinned to capture commit e124baa273ffa1281ac26d343133a359051ccd31.
The script verifies frozen source hashes, named-case inventory, 44 compile
pairs and 40 assemble pairs, original wire input identity, file hashes,
refusal status and stored diagnostic bodies, and port/wait records. Native
refusals must match all frozen diagnostic fields; extra fields fail closed.
Typed refusal diagnostics have no independent complete authority. Their raw
replies and code/shape are checked, but the audit is incomplete and exits 2.
Failure exits 1. There is intentionally no complete-pass exit code.

Use a separate GitHub-hosted audit job with a trusted checkout of that exact
commit. Restore the authenticated encrypted artifact to a private temporary
folder before invoking this script. Restore the public typed-input artifact to
another private temporary folder. Verify the run, artifact digest, and approved
key handling separately. These scripts do not authenticate artifact origin,
prove actual loaded binary/assets/health, inspect capture logs, or independently
establish process execution; they check saved records only. They do not compare
the baseline with current Rust or establish M6 acceptance.

Copy both draft files to a reviewable GH-only audit workflow after review. Run
synthetic fixtures only on the GH runner, then the data audit. Replace the
placeholders with the exact restored paths. Do not log decrypted content.

    python3 -m unittest -v test_audit
    python3 audit.py --capture "$CAPTURE_ROOT" --repo "$GITHUB_WORKSPACE" --inputs "$PUBLIC_INPUT_ROOT"

A complete typed diagnostics rule and a synthetic end-to-end 44-pair corpus are
still needed before this draft can be treated as a complete independent audit.
