# Related business service contract v1

The supplied operations select which parent/child CRUD and batch routes exist. Rules below apply to selected routes and chosen capabilities. Omitted operations must not appear in OpenAPI. Each service has one parent, one child, one database, one auth provider, one storage choice, and one workflow.

The resource paths in the contract are exact. Parent CRUD uses parent.path and
parent.path/{parent_id}. Child CRUD uses parent.path/{parent_id}+child.path,
and appends /{child_id} for one child. Parent absence always returns 404.
Access to a child under another parent returns 404. Lists order by integer ID,
default limit=20, and reject limits outside 1..query.max_limit or negative offsets.
A configured boolean filter uses the field name. before=YYYY-MM-DD is an exclusive
date bound on date_field. Combine both filters. Null dates do not match before.
Each field's required, unique and default values are exact. Uniqueness is global
within its resource, with multiple nulls allowed. Required booleans need a value.
Reject extra input fields and null for nonnullable fields. PATCH changes only
submitted fields. Reject strings over 255 characters, integers outside signed
32-bit range, and nonfinite floats. Dates use YYYY-MM-DD. Invalid input returns422.
The service manages IDs, created_at, updated_at and the child foreign key.
Their inputs are forbidden. Timestamps are UTC and reflect the actual write time.
Use the same UTC timestamp representation in POST, GET, PATCH, and list responses.
POST returns201, DELETE returns204, missing rows return404, unique conflicts409.
Deleting a parent with children returns409. Concurrent child creation and parent
deletion must not create orphan children. POST parent.path/with-children accepts
{"parent": parent-input, "children": [child-input,...]} with 1..20 children.
It returns201 with parent and children. Commit one transaction. A late unique
conflict returns409 and undoes the parent and all children. The next request works.

DATABASE
For postgresql, use SQLAlchemy and DATABASE_URL with postgresql+psycopg.
Missing DATABASE_URL fails startup clearly. Use Alembic upgrade head and downgrade
base. Tables contain exactly the custom fields, id, created_at, updated_at, and
the managed child foreign key where applicable. Declare that foreign key NOT NULL
with ON DELETE RESTRICT. Create a child index (foreign_key,id). For each configured
filter field, create a separate index (field,id) on the parent or
(foreign_key,field,id) on the child. This applies to both boolean and date fields.
For mongodb, use Beanie and PyMongo AsyncMongoClient, MONGODB_URL and
MONGODB_DATABASE. Missing settings fail startup clearly. Require a replica set.
Use actual multi-document transactions, stable integer IDs, unique indexes, and
the same relationship and rollback outcomes. The integer API id maps to MongoDB
_id. Create a child index (foreign_key,_id). For each configured filter field,
create a separate parent index (field,_id) or child index (foreign_key,field,_id).
Provide python -m PACKAGE.migrate upgrade
and downgrade. Downgrade must only drop collections when APP_ENV=test and
SOM_TEST_DATABASE_NAME matches a name beginning som_test_. Data survives restart.
GET /healthz needs no authentication. It checks the configured database connection
and returns200 or503. It does not need to query object storage or identity providers.
Database checks use only isolated
databases made by the prepared checker. TEST_DATABASE_URL or SOM_TEST_DATABASE_NAME
identifies the test database. Do not touch other databases or start your own server.

AUTHENTICATION
All CRUD, export/import and object routes require authentication. Missing or invalid
identity returns401. A reader write returns403. GET /auth/me returns sub and role.
JWT: use PyJWT with a pinned RS256 algorithm, RSA public key from
JWT_PUBLIC_KEY_FILE, JWT_ISSUER and JWT_AUDIENCE. Require sub,iat,exp,iss,aud.
JWT role=editor permits writes. Validate expiry, signature, issuer and audience.
Firebase: use firebase-admin, FIREBASE_PROJECT_ID, verify_id_token with revoked and
disabled-user checks. Use the trusted role claim. Reject expired and wrong-project
tokens. Local-only tests use FIREBASE_AUTH_EMULATOR_HOST with APP_ENV=test.
Google OAuth: use Authlib, Google client ID/secret/redirect settings named
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI. GOOGLE_EDITOR_SUBJECTS
is a JSON list of editor subject IDs. Others are readers. GET /auth/google/login
returns302 and starts authorization-code flow with PKCE S256, random one-use state, browser binding
and nonce. GET /auth/google/callback validates them and ID-token issuer, audience,
signature and expiry. A successful callback returns200. Invalid or replayed state
returns400. Invalid identity or nonce returns401. Logout returns204.
Keep expiring state (300s) and sessions (max3600s or token
expiry) in Redis at REDIS_URL, using keys REDIS_NAMESPACE:PACKAGE:state:<state>
and REDIS_NAMESPACE:PACKAGE:session:<session-id>. Use an HttpOnly SameSite=Lax
session cookie, Secure outside tests. In OAuth mode GET /auth/me returns a JSON
object with exactly these named required properties: sub, role, and csrf.
The csrf property contains the random CSRF token. The key is exactly "csrf".
Writes require it as X-CSRF-Token. POST /auth/logout removes the session. Redis
failure returns503. Local tests use SOM_TEST_OIDC_ISSUER with /authorize,/token,/jwks.
Test endpoint overrides are allowed only when APP_ENV=test and host is loopback.
Default Google production issuer is https://accounts.google.com. No auth bypass.

OBJECT STORAGE (when selected)
Use boto3 for S3 or google-cloud-storage for GCS. S3_BUCKET or GCS_BUCKET and
GCS_PROJECT name storage. Use normal SDK production credentials. The checker gives
S3 SDKs synthetic AWS credentials for local tests. No cloud credentials are used.
Local tests set
SOM_TEST_S3_ENDPOINT or STORAGE_EMULATOR_HOST. Local overrides require APP_ENV=test
and loopback. PUT/GET/DELETE /objects/{key}, key pattern [A-Za-z0-9_-]{1,80}.
Accept raw nonempty bytes, at most1MiB. Empty422, unsafe key422, too large413,
missing404, provider failures503. PUT returns200 {key,size,sha256}. A repeated PUT
of the same body returns the same result. Different data under the same key409.
GET returns exact bytes and X-Content-SHA256. DELETE is idempotent204. Use atomic
create-if-absent provider conditions. A failed operation can be retried. The client
stores the key in an object_key field where present. Object and DB writes are
separate operations; do not claim a transaction spanning both providers.

BUSINESS WORKFLOWS
GET parent.path/{parent_id}/export processes its children in ID order. Limit all
workflows to1000 rows, return413 above that and404 for missing parent.
json_export: return full read-schema rows as JSON using the standard json module.
gzip_export: use gzip to return deterministic (mtime=0) compressed JSON rows.
csv: use csv/io. Export only custom child fields in declared order with a header.
Use \N for null. Compare numbers and dates by their field type. Boolean text uses
true or false, with either letter case accepted. Preserve string field contents.
POST parent.path/{parent_id}/import accepts text/csv with matching
headers and1..20 rows, validates all values, commits once or rolls back all rows,
returns201 with created full rows, invalid CSV422 and late unique conflict409.
decimal_summary: use Decimal(str(value)) to sum value_field by group_field.
Ignore null values. Return {"totals": {group: decimal-string}}. Preserve zeros.
statistics: use statistics for nonnull value_field. Return count,mean,median,
minimum,maximum; count0 with all remaining values null for an empty set.

DELIVERABLES AND CHECKS
Use at most12 product files and64KiB source. Include PACKAGE/app.py (app entry),
models, input/read/patch schemas, database setup, tests, migration, pyproject.toml,
and README.md. Replace PACKAGE with the contract package. Use uv sync and the
pinned dependencies in dependency-contract.txt. Do not install or start local
infrastructure yourself. The prepared checker supplies settings and runs installs,
migrations, Uvicorn, HTTP checks, concurrency, failure/recovery and restart checks.
Your unittest tests read TEST_AUTH_HEADERS (JSON object) and TEST_AUTH_COOKIES
(JSON object) only as client headers/cookies. The application must never use these
variables as an auth bypass. Tests must not drop or reset data outside their isolated
database. The checker also inspects real DB state and indexes independently.
