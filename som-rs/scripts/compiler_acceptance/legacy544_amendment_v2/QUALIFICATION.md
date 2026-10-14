This is an additive QA adapter. It does not change the candidate binary, production source, frozen QA57 files, prompt inventory, archived products, or test rules.

The adapter loads the exact original runner hash `35df9239135bddce5e50e6068b62f8b7d34c66301d0359c8e1f083d046eca3a0`. It replaces only that module's `subprocess` reference and its `write_json` function. It does not patch the process-wide subprocess module. It preserves the original interruption context and original exit codes.

The subprocess adapter changes only the exact `verify_service_v3.py` call. The command, source case file, heldout index, generated response file, timeout, captured output, and exit rule remain unchanged. The child receives the two verified public import paths first in PYTHONPATH. Other environment values and pre-existing import paths remain unchanged. Other commands pass through unchanged. The adapter refuses a changed verifier command or an unexpected explicit verifier environment.

The writer keeps ordinary string-key JSON bytes unchanged. Native date and datetime values use the `$som_native_evidence_v1` tag. A date tag records the native type and ISO date. A datetime tag records the native type, six-digit microseconds, ISO offset, fold, awareness, timezone name, and offset in microseconds. This records a native observation. It does not convert a runtime product value or change a comparison.

Version 005 accepts the exact SQLAlchemy `quoted_name` string subtype for database row keys. It writes the key's underlying string because JSON object keys are strings. The subtype's `quote` flag is SQL identifier metadata, not part of the observed product value. Other string subclasses and all non-string keys still fail with the actual rejected key type in the error.

Well-formed date and datetime tags can pass through a second evidence write. The original runner reloads pending JSON for its emergency receipt. This second write must retain each typed tag after validating its exact structure. Invalid tags, unsupported key types, unknown native types, and non-finite floats refuse serialization. Unknown objects are never converted with `str`. The emergency wrapper receipt records only safe error text, type and JSON pointer. It does not copy the unsupported object.

Serialization completes before a target file is opened. The writer uses a sibling temporary file, flush, fsync and atomic replacement. A serialization or replacement error preserves the previous target bytes. Temporary files are removed. The wrapper's outer error record can still be written if the original emergency receipt encounters an unsupported object.

The original M3 acceptance writers use broad `default=str` at run_m3_flow_acceptance.py lines264/286 and run_m3_flow_gate.py line164. This adapter has a narrower policy. It keeps the native date/time type and rejects unknown types. It leaves all historical writer files unchanged.

The root auditor checks original acceptance flags, exact544 records and IDs,540 prompt inputs,1080 compile requests,544 assembly products,452 behavior jobs,444 quality jobs, raw requests/responses, archive parity, candidate identities, and cleanup. It does not reinterpret the native database observations. The new tag format does not replace an oracle or a pass flag.

The execution descriptor records actual Python executable and argv, delegated argv, cwd, start/end times, original runner and QA57 bindings, wrapper and codec identities, manifest seal, and before/after hashes of all73 production-source files,57 original QA files, additive files, candidate archive/binary, and the complete public127 receipt. Changed or missing bound inputs force a nonzero exit. The new run cannot claim that controller003d passed. Its legacy worker failed. The complete public127 result remains a separate immutable prerequisite.

Root must first run one legacy100 case and the first quality218 case as separate real diagnostic qualifications. Those diagnostics must test the original import boundary, complete strict checks, native date/time evidence and failure output. They do not replace the final gate. Root must then run all544 jobs in a new `run-004` directory. Successful run003 jobs cannot be copied into run004. The original failed003 run, all100 import failures, and its314-job pending receipt remain unchanged.

Offline qualification uses only an in-memory SQLite database for the SQLAlchemy row-key case. Other process boundaries use stubs. It does not call a service, formatter, Docker, or behavior checker. Its synthetic outcomes are not product acceptance. Amendment 004's real diagnostic remains a preserved failure: legacy100 passed, but M2 evidence serialization rejected the actual SQLAlchemy `quoted_name` subtype before it could save that row. Version 005 qualifies this narrow normalization. Independent review of version 005, fresh real diagnostic jobs using version 005, all544 acceptance, final root audit, and promotion remain unrun.

The wrapper adds two flags to the original runner arguments:

```text
--amendment-manifest /absolute/path/manifest.json
--amendment-manifest-sha256 ACTUAL_RAW_MANIFEST_SHA256
```

It removes these two flags before the original parser runs. Root must use the exact original full-run arguments, change only the executable script to `legacy544_corrected.py`, and use a fresh output directory. Set SOM_REPO to `/Users/chrischeng/faberlines/som`. The manifest must be prepared and bound before execution. Installation, archive binding and final auditor input updates remain root-owned.
