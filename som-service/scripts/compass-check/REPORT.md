# Compass check of the generated Customer crate (#311)

Exploration only. The tool is not a gate, not in CI, and not a dependency of
`som-service` or of a generated crate. Compass is pinned to the core tag `v0.5.2`
(commit `461794b2`). The tool's own lock is `scripts/compass-check/Cargo.lock`;
`cargo deny` runs in the workspace only, so it does not check that lock.

## What compass found

Output of `cargo run --manifest-path scripts/compass-check/Cargo.toml --release`:

| variant | expected | found | true | false | missed | ms |
|---|---|---|---|---|---|---|
| clean | [] | [] | 0 | 0 | 0 | 12 |
| m1_no_interfaces | missing-layer:interfaces | missing-layer:interfaces | 1 | 0 | 0 | 9 |
| m2_domain_imports_interfaces | bad-direction:domain->interfaces | bad-direction:domain->interfaces | 1 | 0 | 0 | 10 |
| m3_application_imports_infrastructure | bad-direction:application->infrastructure | bad-direction:application->infrastructure | 1 | 0 | 0 | 10 |
| m4_store_trait_renamed | missing-symbol:CustomerStore | missing-symbol:CustomerStore | 1 | 0 | 0 | 9 |
| m5_use_case_renamed | missing-symbol:list_customers | missing-symbol:list_customers | 1 | 0 | 0 | 10 |

## Counts

- True findings: 5 of 5 seeded defects.
- False findings: 0 (the clean crate gives none).
- Missed defects: 0.
- Compass work: 60 ms for six crates (`compass_ms`); 75 ms for the whole run (`total_ms`).
- Compass tag: `v0.5.2`.

The sample is small: five seeded defects, each one edit of the clean crate. The
checks are plain: the layer file exists, a `use crate::<layer>` line in the
parsed tree, and a named `pub` item.

## Compass defects that blocked a check

- none. Functions come from `compass::outline`. Structs and traits come from
  the `ParsedFile` tree (`walk`, `node_text`), so no text fallback was used.
  Import edges come from the `use_declaration` nodes of the same tree. The
  `compass::graph::ImportGraph` API was not used.

## Recommendation

adopt later. All five seeded defects are found and the clean crate gives no false finding, at about 10 ms per crate.
The sample is five synthetic edits. The reference test `reference/order/tests/it/layers.rs` only checks that the four layers exist, so the direction and symbol checks are new, but they have not been shown on real drift yet.
