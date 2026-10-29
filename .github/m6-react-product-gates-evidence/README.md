# DRAFT: Linux QA profile for React product gates

This scratch proposal is unsealed and is not product acceptance. It is based on
`f59e28d910ff4adb16e3becfdc82bd2978ca84fa` and leaves the production React
formatter descriptor at `636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8`.
The example records the independently audited Ubuntu 24.04 input artifact. It
does **not** establish that the same cache passes on Ubuntu 22.04.

The existing runner's four initial commands and its two builds remain in the
same order. The product, lockfile and cache comparisons remain. macOS remains
the default. The proposed optional `--qa-profile` selects a separate Linux QA
profile. It cannot be combined with `--cache`.

The profile checks its own exact authority SHA, complete contents, GitHub-hosted
Ubuntu 22.04, a physical workspace under `RUNNER_TEMP`, Node bytes, npm tree,
and a read-only cache tree. It checks the same tool and cache trees again after
the six commands. It also requires the exact 56-file Prettier tree. A failed
recheck causes the product gate to fail and records the reason in `gates.json`.
The profile never changes any bundle field. The Linux QA tool identity is not
the frozen product formatter provenance.

This successor adds raw evidence for the six product commands. Each completed
command records argv, cwd, environment, exit code, wait state, and the SHA of
its retained combined log. A timeout, process error, or missing exit marks the
remaining commands unrun. A completed wait means that the child returned from
`subprocess.run`; it is not independent OS evidence. A failed postcheck is
recorded and cannot pass the gate. A process error does not establish whether
the child spawned or was waited for.

For Linux QA only, `prettier-evidence` retains the installed Prettier files
before cleanup. The runner checks their tree before and after copying. The
independent auditor must hash the retained files itself. These records alone
do not prove the full product or release acceptance.

`PROFILE_SHA256` is deliberately `None`. Therefore the proposed Linux path
refuses to run until the owner independently verifies the Ubuntu 22.04 profile,
chooses a final canonical profile and binds its SHA through the formal receipt
and reviewed source. The example is not an accepted profile. Do not substitute
a candidate-supplied SHA for authority. The original 24.04 cache was retained
and independently audited; a new 22.04 observation and its evidence are needed.

The frozen React002 files map covers `run_gates.py`. A real edit requires the
next numbered React receipt, archived old bytes, new complete file map,
independent source review, and an acyclic target-contract hash calculation. If
the new helper or the final profile becomes part of the contract, its exact
scope, SHA and receipt linkage need to be included. This draft does not seal a
receipt and does not alter the target-contract or production formatter.

Further integration remains required:

1. Propagate the explicit profile from `run_acceptance.py` to
   `check_products.py` and then to `run_gates.py`. Keep all 12 steps, 129
   products, 30 negative cases and 16 bundle refusals in scope.
2. Bind the complete profile, cache inventory, command argv, six exit codes,
   both builds, before/after snapshots and per-case raw logs in the independent
   auditor. The auditor must verify the exact candidate and actual profile.
3. Complete the separately pinned Python, PostgreSQL and process-monitoring
   profile for each API gate. An npm reference installation does not prove it.
4. Run every synthetic test and product gate on a GitHub-hosted runner only.
   The synthetic fixtures in this directory are unrun and do not build or run
   a product. They must not be treated as product acceptance.

The strict tree scan rejects symlinks, special files, invalid names and scan
errors. The observed npm and cache trees have no symlinks. The pinned trees and
their 22.04 usability still require the source, executable, license, archive and
platform checks described in the independent evidence policy. The fixed cache
SHA identifies the retained artifact; it does not claim byte-for-byte cache
reproducibility across separate cache generation runs.
