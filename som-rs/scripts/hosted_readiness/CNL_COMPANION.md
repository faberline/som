# Complete native CNL companion experiment

Run this stage only on a GitHub Linux x86_64 runner.
Use `python3 -I -S -B`.
Use the binary, packaged assets and four formatter tools from the preceding
complete typed compile and assembly stage in the same job.
The dedicated QA port is 18977.
Ports 8765 and 18765 cannot be used.

The stage checks 2,050 named observations:

| Input form | Complete scope |
|---|---:|
| Python CNL with no target or explicit Python target | 1,342 |
| Rust CNL | 699 |
| Original native Rust JSON | 9 |

The 699 Rust CNL observations include all 13 original native CNL requests.
The nine original JSON requests retain their exact frozen raw bodies.
Duplicate prompt values keep their separate named slots.

Before helper execution, the driver reads all seven helper modules as regular
Git blobs at the declared exact HEAD.
It compares the current source bytes with those blobs.
Its source loader compiles only those bound bytes in fresh registered modules.
It cannot select an existing Python bytecode cache.
Preloaded helper names and helper reloads are refused.

The driver rechecks all 2,041 saved typed requests and raw replies.
Each request must match the independently accepted whole request hash.
Each reply passes the bound experimental checker again.
Saved success flags cannot approve their own raw reply.
The exact source, binary, assets and native tool identities must match the
preceding typed stage.

All 2,120 Rust input files are reconstructed again from the unchanged public
source recipes, frozen examples, serializer and accepted hashes.
Every input file must match that independent reconstruction.
Python inputs retain their independent prompt, CNL and known request pins.

For every successful input, the complete raw reply must equal the corresponding
current typed reply byte for byte.
No digest leaf is ignored in this comparison.
This also proves the same current prompt, DSL, asset and payload bytes.

Three observations are current compiler refusals:
RS12 CNL, RS13 CNL and the original RS13 JSON request.
Their code, status, detail, full diagnostic and location come from the frozen
phase 2 expectation, compiler selection policy and raw request text.
JSON locations use the actual pointer token in the raw request.
CNL locations use the named filter token in the CNL source.
The two original forms must also match every frozen native diagnostic field.
Old phase 1 scope errors cannot replace these current HTTP 400 refusals.

The shared owned-service helper starts one new process group.
It checks the child PID, start time, binary inode and exact loopback socket
before every request.
It waits for and stops only that owned process group.
The driver saves every request, reply, result, health reply and cleanup receipt.
It checks all source, input, typed proof, binary, asset and tool bytes again.
A failure, timeout, interrupt, missing result or incomplete cleanup fails the stage.

This remains an unsealed experiment.
It keeps product acceptance and runtime readiness false.
It does not run or accept product quality, build, behavior, TypeScript or React
gates and cannot approve a numbered native compatibility policy.
