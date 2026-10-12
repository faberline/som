# Related flow contract v1

This contract extends related-business.v1 and som.python-quality.v1. The
flagship owns business choices. SOM translates those choices into approved
blocks. A new format is development data, not a release.

Each service has one parent, one child, one database, one identity provider,
one storage choice, one existing workflow, and one named flow. Existing
single-operation routes retain their business contract.

The flow declares typed inputs, ordered named steps, and named JSON results.
References distinguish inputs, earlier step results, and typed literals.
No source code, SQL, block IDs, loops or parallel nodes occur in the prompt.
DSL v3 uses three separate reference shapes. A block parameter has
`{"$ref":"parent.name"}` for a verified generation binding. Runtime data has
`{"input":"parent"}` or `{"step":"created_parent","field":"id"}`.
A block dependency has `{"symbol":"engine"}`. These kinds are not
interchangeable. Typed constants use `{"literal":true,"type":"boolean"}`.
The model emits the complete file list, block list, runtime tree and every
reference. Binding a name never inserts an absent block or inverse.
There are at most eight action nodes, two if/else nodes at depth at most two,
and two nonnested database transaction scopes. Both branch arms export the
same names and types. Only exports survive a branch. Forward and cross-arm
references are invalid. Conditions use eq, ne, lt, le, gt, ge or is_null.

Authorization precedes all work. Every database mutation is inside a named
transaction and declares delete_created or restore_patch compensation.
Object creation declares delete_created. Deletion is not a flow operation.
Provider calls must not occur inside a database transaction. Conditions and
pure calculations may use the current transaction. All writes and their
step journal use its actual database session and one commit.
The operation vocabulary is authorize, parent/child.create, parent/child.get,
parent/child.list, parent/child.patch, batch.create, workflow.run,
workflow.import, object.put and object.get. CSV import requires the CSV
capability. Flow inputs use string, integer, float, boolean, date,
parent_input, child_input, parent_patch, child_patch or children_input.
Dates compare as ISO calendar dates. A null check needs a nullable scalar.
Names are ASCII Python identifiers of at most64 characters, with no leading
underscore or model_ prefix. Flow routes reserve /flows and /flow-runs.

POST /flows/{name}/runs requires Idempotency-Key. The uniqueness scope is
identity subject plus flow plus key. Changed content under a key returns409.
Active runs return202. Success returns200 with run_id,state,result.
GET /flow-runs/{run_id} and POST /flow-runs/{run_id}/recover belong only to
the run owner. All writes require editor access and OAuth CSRF where applicable.
Status does not expose stored inputs, snapshots, credentials or provider tokens.

States are running,succeeded,compensating,compensated,recovery_required.
The database stores intent before external work. Leases last30s and renew
every5s. Atomic claims advance an execution epoch. A lost claim may not commit
business writes. Recovery finishes reverse work only. No recovery replays
forward work. There is no background recovery scheduler.

Reverse committed effects in actual execution order. Reverse an original
database group in one transaction. A private monotonically increasing revision
prevents replacing another writer's changes. Patch restores only affected
business fields. updated_at reflects the reverse write time. Never lower a
revision. New rows are deleted only if unchanged by other writers.

S3 reverse creation requires bucket versioning and the exact VersionId.
GCS reverse creation requires the exact generation. Identical preexisting
objects have no reverse effect. Uncertain external outcomes remain pending;
absence alone after an uncertain create does not prove completion.
Original business failures retain their status. Unfinished reverse work
returns503 with run_id. There is no distributed atomic commit claim.

All outputs remain standalone and have at most12 files,64 blocks,65536
formatted source bytes. Input and output model sequences each have at most2048
tokens. Wrong, absent or incompatible choices fail; no answer repair or fallback.

M2 uses12 development smoke tasks and114 sealed positive tasks:90 capability
tuples and24 semantic cases. Formal tasks do not select test weights. All old
100 tasks and214 combinations remain regression. Real cloud, full model
training, paid agent comparison and changing current are outside M2.

Acceptance checks derive outcomes from the public requirements and database
or provider observations. They must not compare product code to a template.
All three paths of nested branching need observations. Late failures must
prove a committed write before reverse work. A lost provider response must
prove provider ownership and an immutable version. Recovery must prove no
additional forward execution. Parallel recovery must prove one claimant.
Every source, request, fixture setting, checker and observation has a hash.
