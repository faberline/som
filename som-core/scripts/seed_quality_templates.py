"""Author typed copies of the approved blocks; never run in requests."""

import json
import hashlib
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "som-core/block_templates/quality"
BASE = {b["id"]: b for b in json.loads((ROOT / "som-code-python/data/blocks/composed/blocks.json").read_text())}


def write(identity: str, source: str, imports: list[str] | None = None) -> None:
    block = BASE[identity]
    source = source.rstrip() + "\n"
    source = source.replace('timezone.utc', 'UTC')
    if identity.startswith(('api.related.', 'api.mongo.', 'api.business.head.')):
        source = source.replace('data.model_dump(exclude_unset=True)', 'model_values(data, exclude_unset=True)')
        source = source.replace('data.model_dump()', 'model_values(data)')
        source = source.replace('data.parent.model_dump()', 'model_values(data.parent)')
        source = source.replace('item.model_dump()', 'model_values(item)')
        # Shared helpers are private. Valid operation subsets may not call each
        # helper, so they do not pretend to be public route or module symbols.
        for helper in ('require_child', 'encode', 'workflow_import'):
            source = re.sub(r'\b' + helper + r'(?=\()', '_' + helper, source)
        source = source.replace('    # pylint: disable-next=unused-variable\n', '')
    if identity.startswith('api.business.workflow.'):
        source = re.sub(r'\bworkflow_import(?=\()', '_workflow_import', source)
    source = source.replace('from datetime import date, datetime, time, timezone', 'from datetime import UTC, date, datetime, time')
    source = source.replace('from datetime import datetime, timezone', 'from datetime import UTC, datetime')
    imports = [i.replace('timezone', 'UTC') if i.startswith('from datetime import') else i for i in (block['imports'] if imports is None else imports)]
    if identity.startswith('orm.composed.mongo.'):
        statements = re.findall(r'^(?:from [^\n]+ import [^\n]+|import [^\n]+)$', source, re.M)
        source = re.sub(r'^(?:from [^\n]+ import [^\n]+|import [^\n]+)\n?', '', source, flags=re.M)
        imports += statements
        # Valid resource names may equal typing symbols. Keep SDK-only names
        # in a private namespace that the public identifier contract excludes.
        for symbol in ('ClassVar', 'Callable', 'TypedDict'):
            alias = '_Som' + symbol
            source = re.sub(r'\b' + symbol + r'\b', alias, source)
            imports = [re.sub(r'\b' + symbol + r'\b', symbol + ' as ' + alias, item)
                       for item in imports]
    source = re.sub(r'^(\s*)(async )?def ([^\n]+):\n',
                    lambda m: m[0] + m[1] + '    """'
                    + m[3].split('(')[0].replace('_', ' ').capitalize()
                    + '."""\n', source, flags=re.M)
    source = re.sub(r'("""[^\n]*"""\n)([ \t]*"""[^\n]*"""\n)', r'\2', source)
    source = re.sub(r'^([ \t]*)class ([^:#\n]+):([^\n]*)\n',
                    lambda m: m[0] + m[1] + '    """Typed service contract."""\n',
                    source, flags=re.M)
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / (identity + ".json")).write_text(json.dumps(
        {"template": source, "imports": imports},
        indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    for identity in json.loads((ROOT / "som-code-python/data/block-contracts/composed.json").read_text())["blocks"]:
        source = BASE[identity]["template"].rstrip() + "\n"
        if identity.startswith("api.related."):
            source = source.replace("db: Session = Depends(get_session)", "db: Annotated[Session, Depends(get_session)]")
            source = source.replace("limIt: int = Query(20, alias=\"limit\", ge=1, le={{max_limit}})", "page_size: Annotated[int, Query(alias=\"limit\", ge=1, le={{max_limit}})] = 20")
            source = source.replace("offset: int = Query(0, ge=0)", "offset: Annotated[int, Query(ge=0)] = 0").replace("limIt", "page_size")
            source = source.replace('Depends(get_session)', 'Depends(session_provider)')
            # Required dependencies precede the optional query parameters.
            source = re.sub(r'def (list_\w+)\((.*), db: Annotated\[Session, Depends\(session_provider\)\]\)',
                            r'def \1(db: Annotated[Session, Depends(session_provider)], \2)', source)
            role = "Parent" if ".parent." in identity else "Child"
            returns = "list[" + role + "]" if ".list." in identity else "Response" if ".delete" in identity else "BatchRead" if identity.endswith("batch") else role
            source = re.sub(r'^(    def [^\n]+)\):', r'\1) -> ' + returns + ':', source, flags=re.M)
            if identity.endswith("batch"):
                source = source.replace('return {"parent": parent, "children": children}', 'return BatchRead.model_validate({"parent": parent, "children": children})')
            if '.list.' in identity:
                role = 'Parent' if '.parent.' in identity else 'Child'
                class_name = role + 'ListQuery'
                header_end = source.index('\n', source.index('    def '))
                body = source[header_end + 1:]
                fields = '        limit: int = Field(20, ge=1, le={{max_limit}})\n        offset: int = Field(0, ge=0)\n'
                if not identity.endswith('.plain'):
                    fields += '        {{filter_field}}: bool | None = None\n'
                    body = body.replace('{{filter_field}} is not None', 'query.{{filter_field}} is not None').replace('== {{filter_field}}', '== query.{{filter_field}}')
                if identity.endswith('.date'):
                    fields += '        before: date | None = None\n'
                    body = body.replace('before is not None', 'query.before is not None').replace('< before', '< query.before')
                body = body.replace('.limit(page_size)', '.limit(query.limit)').replace('.offset(offset)', '.offset(query.offset)')
                decorator = source[:source.index('    def ')]
                arguments = ('parent_id: int, ' if role == 'Child' else '') + 'db: Annotated[Session, Depends(session_provider)], query: Annotated[' + class_name + ', Query()]'
                source = '    class ' + class_name + '(BaseModel):\n' + fields + '\n' + decorator + '    def list_' + role.lower() + '(' + arguments + ') -> list[' + role + ']:\n' + body
        if identity.startswith("schema."):
            source = source.replace("def bounded_values(self):", "def bounded_values(self) -> Self:")
            source = re.sub(r'-> "\{\{name\}\}Patch"', '-> Self', source)
            source = source.replace("nullable = {", "nullable: dict[str, bool] = {")
            source = source.replace('model_config = ConfigDict', 'model_config: ClassVar[ConfigDict] = ConfigDict')
            source = source.replace('type(value) is int', 'isinstance(value, int) and not isinstance(value, bool)')
        if identity.startswith(("orm.", "migration.related.")) and not '.mongo.' in identity:
            source = source.replace('sa.func.now()', 'database_now()')
            source = source.replace('\n    pass', '')
            source = re.sub(r'^(class [^\n]+):\n', r'# pylint: disable-next=too-few-public-methods\n\1:\n', source, flags=re.M)
        if identity.startswith('migration.related.'):
            source = re.sub(r'^(revision|down_revision|branch_labels|depends_on) = ([^\n]+)', r'\1 = \2  # pylint: disable=invalid-name', source, flags=re.M)
            source = re.sub(r'^([ \t]*)(op\.[^\n]+)', r'\1# pylint: disable-next=no-member\n\1\2', source, flags=re.M)
        if identity == 'service.alembic.env':
            source = re.sub(r'^([ \t]*)((?:with |if )?context\.[^\n]+)', r'\1# pylint: disable-next=no-member\n\1\2', source, flags=re.M)
        if identity.startswith("test.related.payload"):
            source = source.replace("payload():", "payload() -> dict[str, object]:")
        source = source.replace("def healthz():", "def healthz() -> dict[str, str]:")
        source = source.replace("def upgrade():", "def upgrade() -> None:").replace("def downgrade():", "def downgrade() -> None:")
        source = source.replace("def upgrade() -> None:", "def upgrade() -> None:")
        write(identity, source, BASE[identity]["imports"] + (["from typing import ClassVar, Self"] if identity.startswith("schema.") else []) + (["from sqlalchemy.sql.functions import now as database_now"] if identity.startswith(('orm.', 'migration.related.')) and '.mongo.' not in identity else []) + (["from pydantic import BaseModel, Field"] if identity.startswith('api.related.') and '.list.' in identity else []))
    write("service.init", "", [])

    write("auth.common", '''APPLICATION = {{package}}
TEST_MODE = os.environ.get("APP_ENV") == "test"
auth_router = APIRouter()
CLOSERS: list[Callable[[], None]] = []


def close_integrations() -> None:
    with ExitStack() as shutdown:
        for closer in CLOSERS:
            shutdown.callback(closer)
        CLOSERS.clear()


class Identity(TypedDict):
    sub: str
    role: str
    csrf: NotRequired[str]


def setting(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(name + " is required")
    return value


def local_override(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        if (not TEST_MODE or parsed.hostname not in
            {"127.0.0.1", "localhost", "::1"} or parsed.username or
            parsed.password or parsed.scheme not in {"http", "https"}):
            raise RuntimeError(name + " is allowed only for local test services")
    return value


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401,
                         detail="invalid or expired authentication",
                         headers={"WWW-Authenticate": "Bearer"})


def bearer(request: Request) -> str:
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or len(token) > 16384:
        raise unauthorized()
    return token


def validated_identity(subject: object, role: object) -> Identity:
    if not isinstance(subject, str) or not subject or not isinstance(role, str):
        raise unauthorized()
    return {"sub": subject, "role": role}


def require_identity(request: Request) -> Identity:
    return authenticate(request)


def require_access(request: Request,
                   identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    """Require editor permission for writes."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and identity["role"] != "editor":
        raise HTTPException(status_code=403, detail="editor role required")
    return identity


@auth_router.get("/auth/me")
def who_am_i(identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    return identity
''', ["import json", "import os", "from collections.abc import Callable", "from contextlib import ExitStack", "from urllib.parse import urlsplit", "from typing import Annotated, NotRequired, TypedDict", "from fastapi import APIRouter, Depends, HTTPException, Request, Response"])

    source = BASE["auth.jwt"]["template"].replace("def authenticate(request):", "def authenticate(request: Request) -> Identity:")
    source = source.replace('return {"sub": claims["sub"], "role": claims.get("role", "reader")}', 'return validated_identity(claims.get("sub"), claims.get("role", "reader"))')
    write("auth.jwt", source)

    source = BASE['storage.common']['template']
    source = re.sub(r'(class Object\w+\(Exception\):)\n    pass', r'\1', source)
    source = source.replace('def object_response_error(error):', 'def object_response_error(error: ObjectMissing | ObjectUnavailable) -> Never:')
    source = source.replace('key: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")', 'key: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,80}$")]')
    source = source.replace('async def upload_object(request: Request, key:', 'async def upload_object(request: Request, key:').replace(')):\n', ')):\n')
    source = re.sub(r'(async def upload_object\([^\n]+\)):', r'\1 -> dict[str, str | int]:', source)
    source = re.sub(r'(def download_object\([^\n]+\)):', r'\1 -> Response:', source)
    source = re.sub(r'(def remove_object\([^\n]+\)):', r'\1 -> Response:', source)
    source = source.replace('body = bytearray()', 'buffer = bytearray()').replace('body.extend(chunk)', 'buffer.extend(chunk)').replace('len(body) > MAX_OBJECT_BYTES', 'len(buffer) > MAX_OBJECT_BYTES', 1).replace('if not body:', 'if not buffer:').replace('body = bytes(body)', 'body = bytes(buffer)')
    source = source.replace('except ObjectExists:', 'except ObjectExists as conflict:').replace('raise HTTPException(status_code=409, detail="object key already contains different data")', 'raise HTTPException(status_code=409, detail="object key already contains different data") from conflict')
    write('storage.common', source, BASE['storage.common']['imports'] + ['from typing import Annotated, Never'])
    for identity in ('storage.s3', 'storage.gcs'):
        source = BASE[identity]['template']
        source = source.replace('def storage_put(key, body, sha256):', 'def storage_put(key: str, body: bytes, sha256: str) -> None:')
        source = source.replace('def storage_get(key):', 'def storage_get(key: str) -> bytes:').replace('def storage_delete(key):', 'def storage_delete(key: str) -> None:')
        if identity == 'storage.s3':
            source = re.sub(r'^s3_options = [^\n]+\n', '', source, flags=re.M)
            source = source.replace('**s3_options', 'aws_access_key_id="local-test" if s3_endpoint else None, aws_secret_access_key="local-test" if s3_endpoint else None')
            source = source.replace('def s3_error(exc):', 'def s3_error(exc: BotoCoreError | ClientError) -> Never:')
        else:
            source = source.replace('gcs_requests', 'requests').replace('import requests as requests', 'import requests')
            source = source.replace('from google.cloud import storage as gcs_storage', 'import google.cloud.storage as gcs_storage  # type: ignore[import-untyped]')
            source = source.replace('GCSAnonymousCredentials() if gcs_endpoint else None', 'local_gcs_credentials() if gcs_endpoint else None')
            source = source.replace('GCS_BUCKET =', 'def local_gcs_credentials() -> Credentials:\n    return GCSAnonymousCredentials()  # type: ignore[no-untyped-call]\n\n\nGCS_BUCKET =')
            source = source.replace('return gcs_bucket.blob(key).download_as_bytes(start=0, end=MAX_OBJECT_BYTES, timeout=5, retry=None)', 'value: object = gcs_bucket.blob(key).download_as_bytes(start=0, end=MAX_OBJECT_BYTES, timeout=5, retry=None)\n        if not isinstance(value, bytes):\n            raise ObjectUnavailable("SDK did not return bytes")\n        return value')
        source += '\nCLOSERS.append(' + ('s3_client' if identity == 'storage.s3' else 'gcs_client') + '.close)\n'
        source = source.replace('AnonymousCredentials as GCSAnonymousCredentials', 'AnonymousCredentials').replace('GCSAnonymousCredentials()', 'AnonymousCredentials()')
        write(identity, source, [item.replace('import requests as gcs_requests', 'import requests')
                                for item in BASE[identity]['imports']]
              + ['from typing import Never', 'from google.auth.credentials import Credentials'])

    source = BASE['auth.firebase']['template']
    source = source.replace('import firebase_admin', 'import firebase_admin  # type: ignore[import-untyped]')
    source = source.replace('from firebase_admin import auth as firebase_auth, credentials as firebase_credentials', 'import firebase_admin.auth as firebase_auth  # type: ignore[import-untyped]\nimport firebase_admin.credentials as firebase_credentials  # type: ignore[import-untyped]')
    source = source.replace('from firebase_admin import exceptions as firebase_errors', 'import firebase_admin.exceptions as firebase_errors  # type: ignore[import-untyped]')
    source = source.replace('class LocalFirebaseCredential(firebase_credentials.Base):', 'class LocalFirebaseCredential(\n    firebase_credentials.Base,  # type: ignore[misc,no-any-unimported]\n):\n    """Credential for the isolated Firebase emulator."""')
    source = source.replace('def get_credential(self):', 'def get_credential(self) -> Credentials:')
    source = source.replace('return AnonymousCredentials()', 'return AnonymousCredentials()  # type: ignore[no-untyped-call]')
    source = source.replace('def authenticate(request):', 'def authenticate(request: Request) -> Identity:')
    source = source.replace('claims = firebase_auth.verify_id_token(bearer(request), app=firebase_app, check_revoked=True)', 'claims = TypeAdapter(dict[str, object]).validate_python(firebase_auth.verify_id_token(bearer(request), app=firebase_app, check_revoked=True), strict=True)')
    start = source.index('        if any('); stop = source.index('\n    except ', start)
    source = source[:start] + '''        times: dict[str, float] = {}
        for key in ("exp", "iat", "auth_time"):
            value = claims.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("invalid token time claim")
            times[key] = float(value)
        if times["exp"] <= now or times["iat"] > now or times["auth_time"] > now:
            raise ValueError("expired or invalid token time claims")''' + source[stop:]
    source = source.replace('return {"sub": claims["uid"], "role": claims.get("role", "reader")}', 'return validated_identity(claims.get("uid"), claims.get("role", "reader"))')
    source = source.replace('firebase_credentials', '_fb_creds').replace('firebase_auth', '_fb_auth').replace('firebase_errors', '_fb_errors')
    source += '\ndef close_firebase() -> None:\n    firebase_admin.delete_app(firebase_app)\n\n\nCLOSERS.append(close_firebase)\n'
    write('auth.firebase', source, BASE['auth.firebase']['imports'] + ['from google.auth.credentials import Credentials', 'from pydantic import TypeAdapter'])

    source = BASE['auth.google_oauth']['template']
    start = source.index('editor_subjects ='); stop = source.index('SESSION_COOKIE', start)
    source = source[:start] + '''editor_subjects = TypeAdapter(list[str]).validate_json(setting("GOOGLE_EDITOR_SUBJECTS"), strict=True)
if any(not value for value in editor_subjects):
    raise RuntimeError("GOOGLE_EDITOR_SUBJECTS must contain nonempty strings")
EDITOR_SUBJECTS = set(editor_subjects)
''' + source[stop:]
    start = source.index('def redis_call('); stop = source.index('def oauth_client', start)
    source = source[:start] + '''class LoginState(TypedDict):
    nonce: str
    verifier: str
    binding: str


class OAuthToken(TypedDict):
    id_token: str


class GoogleClaims(TypedDict):
    sub: str
    exp: int | float
    nonce: str


class SessionIdentity(TypedDict):
    sub: str
    role: str
    csrf: str


def redis_read(key: str, consume: bool = False) -> str | None:
    try:
        value: object = redis_client.getdel(key) if consume else redis_client.get(key)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="session store unavailable") from exc
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=503, detail="invalid session store response")
    return value


def redis_set(key: str, value: str, seconds: int) -> None:
    try:
        redis_client.set(key, value, ex=seconds, nx=True)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="session store unavailable") from exc


def redis_delete(key: str) -> None:
    try:
        redis_client.delete(key)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="session store unavailable") from exc


''' + source[stop:]
    source = source.replace('def oauth_client():', 'def oauth_client() -> OAuth2Session:')
    source = source.replace('def google_login():', 'def google_login() -> RedirectResponse:')
    source = source.replace('redis_call("set",', 'redis_set(').replace(', ex=300, nx=True)', ', 300)').replace(', ex=lifetime, nx=True)', ', lifetime)')
    source = source.replace('redis_call("getdel", REDIS_PREFIX + "state:" + state)', 'redis_read(REDIS_PREFIX + "state:" + state, consume=True)')
    source = source.replace('redis_call("get",', 'redis_read(').replace('redis_call("delete",', 'redis_delete(')
    source = source.replace('def google_callback(request: Request, state: str, code: str):', 'def google_callback(request: Request, state: str, code: str) -> Response:')
    source = source.replace('saved = json.loads(saved)', 'try:\n        login_state = TypeAdapter(LoginState).validate_json(saved, strict=True)\n    except ValueError as exc:\n        raise HTTPException(status_code=400, detail="invalid OAuth state") from exc')
    source = source.replace('saved["binding"]', 'login_state["binding"]').replace('saved["verifier"]', 'login_state["verifier"]').replace('saved["nonce"]', 'login_state["nonce"]')
    source = source.replace('token = client.fetch_token(OIDC_TOKEN, code=code, code_verifier=login_state["verifier"], timeout=5)', 'token = TypeAdapter(OAuthToken).validate_python(client.fetch_token(OIDC_TOKEN, code=code, code_verifier=login_state["verifier"], timeout=5), strict=True)')
    source = source.replace('claims = jwt.decode(encoded, key, algorithms=["RS256"], audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER, options={"require": ["sub", "exp", "iat", "iss", "aud", "nonce"]})', 'claims = TypeAdapter(GoogleClaims).validate_python(jwt.decode(encoded, key, algorithms=["RS256"], audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER, options={"require": ["sub", "exp", "iat", "iss", "aud", "nonce"]}), strict=True)')
    source = source.replace('identity = {"sub": claims["sub"]', 'identity: Identity = {"sub": claims["sub"]')
    source = source.replace('        if not isinstance(claims["nonce"], str)', '        if not claims["sub"] or not math.isfinite(claims["exp"]):\n            raise unauthorized()\n        if not isinstance(claims["nonce"], str)')
    source = source.replace('def authenticate(request):', 'def authenticate(request: Request) -> Identity:')
    source = source.replace('identity = json.loads(saved)', 'try:\n        stored = TypeAdapter(SessionIdentity).validate_json(saved, strict=True)\n    except ValueError as exc:\n        raise unauthorized() from exc\n    identity = validated_identity(stored["sub"], stored["role"])\n    if not stored["csrf"]:\n        raise unauthorized()\n    identity["csrf"] = stored["csrf"]')
    source = source.replace('identity["csrf"], request.headers', 'identity.get("csrf", ""), request.headers')
    source = source.replace('@auth_router.post("/auth/logout", status_code=204)', '@auth_router.post("/auth/logout", status_code=204, dependencies=[Depends(require_identity)])')
    source = source.replace('def logout(request: Request, identity=Depends(require_identity)):', 'def logout(request: Request) -> Response:')
    source += '\nCLOSERS.append(redis_client.close)\n'
    write('auth.google_oauth', source, BASE['auth.google_oauth']['imports'] + ['from pydantic import TypeAdapter', 'import math'])

    source = BASE["api.business.head.postgresql"]["template"]
    source = source.replace("def create_router(get_session):", "def create_router(session_provider: Callable[[], Iterator[Session]]) -> APIRouter:")
    source = source.replace('Depends(get_session)', 'Depends(session_provider)')
    source = source.replace('def create_router(session_provider: Callable[[], Iterator[Session]]) -> APIRouter:', '# pylint: disable-next=too-many-locals,too-many-statements\ndef create_router(session_provider: Callable[[], Iterator[Session]]) -> APIRouter:')
    source = source.replace("def require_parent(db, parent_id):", "def require_parent(db: Session, parent_id: int) -> Parent:")
    source = source.replace("def require_child(db, parent_id, child_id):", "def require_child(db: Session, parent_id: int, child_id: int) -> Child:")
    source = source.replace("def integrity_failure(db, exc, missing_parent=False):", "def integrity_failure(db: Session, exc: IntegrityError, missing_parent: bool = False) -> Never:")
    source = source.replace("async def workflow_rows(parent_id):", "async def workflow_rows(parent_id: int) -> list[JsonObject]:")
    source = source.replace("def read():", "def read() -> list[JsonObject]:")
    source = source.replace("async def workflow_import(parent_id, records):", "# pylint: disable-next=unused-variable\n    async def workflow_import(parent_id: int, records: list[ChildCreate]) -> list[JsonObject]:")
    source = source.replace("def write():", "def write() -> list[JsonObject]:")
    source = source.replace('ChildRead.model_validate(row).model_dump(mode="json")', 'json_object(ChildRead.model_validate(row).model_dump(mode="json"))')
    source = source.replace("        from starlette.concurrency import run_in_threadpool\n", "")
    source = source.replace("            from .database import engine\n", "")
    types = '''type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
type FieldValue = str | int | float | bool | date | None
JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
FIELD_VALUES: TypeAdapter[dict[str, FieldValue]] = TypeAdapter(dict[str, FieldValue])


def json_object(value: object) -> JsonObject:
    return JSON_OBJECT.validate_python(value)


def model_values(model: BaseModel, *, exclude_unset: bool = False) -> dict[str, FieldValue]:
    return FIELD_VALUES.validate_python(model.model_dump(exclude_unset=exclude_unset), strict=True)


'''
    write("api.business.head.postgresql", types + source, BASE["api.business.head.postgresql"]["imports"] + ["from typing import Annotated, Never", "from collections.abc import Callable, Iterator", "from pydantic import BaseModel, TypeAdapter", "from .database import engine", "from starlette.concurrency import run_in_threadpool"])

    for identity in ('orm.composed.mongo.parent', 'orm.composed.mongo.child'):
        source = BASE[identity]['template']
        source = source.replace('id: int = Field(alias="_id")', 'id: int = Field(alias="_id")  # type: ignore[assignment]')
        source = source.replace('UNIQUE_FIELDS: ClassVar[dict]', 'UNIQUE_FIELDS: ClassVar[dict[str, _SomUniqueField]]')
        source = source.replace('QUERY_INDEXES: ClassVar[list]', 'QUERY_INDEXES: ClassVar[list[list[str]]]')
        source = source.replace('if key]', 'if isinstance(key, str)]')
        source = source.replace('[] + [[key, "_id"] for key in [{{filter_field}}, {{date_field}}] if isinstance(key, str)]', '_som_query_indexes([{{filter_field}}, {{date_field}}], [])')
        source = source.replace('[["{{foreign_key}}", "_id"], ] + [["{{foreign_key}}", key, "_id"] for key in [{{filter_field}}, {{date_field}}] if isinstance(key, str)]', '[["{{foreign_key}}", "_id"]] + _som_query_indexes([{{filter_field}}, {{date_field}}], ["{{foreign_key}}"])')
        source = source.replace('[["{{foreign_key}}", "_id"]] + _som_query_indexes([{{filter_field}}, {{date_field}}], ["{{foreign_key}}"] )', '[["{{foreign_key}}", "_id"], *_som_query_indexes([{{filter_field}}, {{date_field}}], ["{{foreign_key}}"]) ]')
        source = source.replace('[["{{foreign_key}}", "_id"]] + _som_query_indexes([{{filter_field}}, {{date_field}}], ["{{foreign_key}}"])', '[["{{foreign_key}}", "_id"], *_som_query_indexes([{{filter_field}}, {{date_field}}], ["{{foreign_key}}"]) ]')
        source = source.replace('bson_encoders = {datetime: lambda value: value, date: lambda value: datetime.combine(value, time(), timezone.utc)}', 'bson_encoders: ClassVar[dict[type[date], Callable[[date], datetime]]] = {datetime: encode_date, date: encode_date}')
        source = source.replace('        name =', '        name: ClassVar[str] =')
        source = source.replace('    class Settings:', '    # pylint: disable-next=too-few-public-methods\n    class Settings:')
        source = source.replace('class {{name}}(_SomBeanieDocument):', '# pylint: disable-next=too-many-ancestors\nclass {{name}}(_SomBeanieDocument):')
        source = source.replace('class {{name}}(_SomBeanieDocument):\n', 'class {{name}}(_SomBeanieDocument):\n    # pylint: disable=duplicate-code\n')
        write(identity, source, BASE[identity]['imports'] + ['from collections.abc import Callable'])
    write('orm.composed.mongo.base', BASE['orm.composed.mongo.base']['template'] + '\n\n\n' + '''class _SomUniqueField(TypedDict):
    unique: bool
    bson_type: str


def encode_date(value: date) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time(), timezone.utc)


def _som_query_indexes(fields: list[str | None], prefix: list[str]) -> list[list[str]]:
    return [[*prefix, field, "_id"] for field in fields if field is not None]
''', BASE['orm.composed.mongo.base']['imports'] + ['from typing import TypedDict'])

    source = BASE['service.business.database.mongodb']['template']
    source = source.replace('client = AsyncMongoClient', 'client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient')
    source = source.replace('async def initialize():', 'async def initialize() -> None:')
    source = source.replace('async def lifespan(app):', 'async def lifespan(_app: FastAPI) -> AsyncIterator[None]:')
    source = source.replace('def get_session():\n    return None', 'def get_session() -> None:\n    """MongoDB creates one transaction session per write."""')
    source = re.sub(r'^    from .models import ([^\n]+)\n', r'from .models import \1\n', source, flags=re.M)
    write('service.business.database.mongodb', source, BASE['service.business.database.mongodb']['imports'] + ['from collections.abc import AsyncIterator', 'from fastapi import FastAPI'])
    source = BASE['migration.mongo']['template'].split('\n', 1)[1]
    source = source.replace('async def migrate(action):', 'async def migrate(action: str) -> None:')
    source = source.replace('raise RuntimeError("downgrade requires the explicitly named isolated test database")', 'raise RuntimeError("downgrade requires the explicitly named "\n                                   "isolated test database")')
    write('migration.mongo', source)

    source = BASE['api.business.head.mongodb']['template']
    source = source.replace('def create_router(get_session):', '# pylint: disable-next=too-many-locals,too-many-statements\ndef create_router(_session_provider: Callable[[], None]) -> APIRouter:')
    source = source.replace('def encode(values):', 'def encode(values: Mapping[str, FieldValue]) -> dict[str, object]:').replace('type(value) is date', 'isinstance(value, date) and not isinstance(value, datetime)')
    source = source.replace('async def transaction(callback):', 'async def transaction[T](callback: Callable[[AsyncClientSession], Awaitable[T]]) -> T:')
    source = source.replace('async def require_parent(parent_id, session=None, touch=False):', 'async def require_parent(parent_id: int, session: AsyncClientSession | None = None, touch: bool = False) -> Parent:')
    source = source.replace('        if touch:', '        row: Parent | None\n        if touch:', 1)
    source = source.replace('row = await Parent.get_pymongo_collection().find_one_and_update', 'raw = await Parent.get_pymongo_collection().find_one_and_update')
    source = source.replace('session=session)\n        else:', 'session=session)\n            row = Parent.model_validate(raw) if raw is not None else None\n        else:', 1)
    source = source.replace('async def require_child(parent_id, child_id, session=None):', 'async def require_child(parent_id: int, child_id: int, session: AsyncClientSession | None = None) -> Child:')
    source = source.replace('async def insert(model, values, session):', 'async def insert[T: MongoDocument](model: type[T], values: Mapping[str, object], session: AsyncClientSession) -> T:')
    source = source.replace('counter = await database', 'raw_counter = await database')
    source = source.replace('row = model(id=counter["value"], created_at=now, updated_at=now, **values)', 'adapter: TypeAdapter[T] = TypeAdapter(model)\n        row = adapter.validate_python({"_id": counter["value"], "created_at": now, "updated_at": now, **values})')
    source = source.replace('        now = datetime.now(timezone.utc)', '        counter = TypeAdapter(Counter).validate_python(raw_counter, strict=True)\n        now = datetime.now(timezone.utc)', 1)
    source = source.replace('async def workflow_rows(parent_id):', 'async def workflow_rows(parent_id: int) -> list[JsonObject]:')
    source = source.replace('async def workflow_import(parent_id, records):', '# pylint: disable-next=unused-variable\n    async def workflow_import(parent_id: int, records: list[ChildCreate]) -> list[JsonObject]:')
    source = source.replace('async def write(session):', 'async def write(session: AsyncClientSession) -> list[JsonObject]:')
    source = source.replace('ChildRead.model_validate(row).model_dump(mode="json")', 'json_object(ChildRead.model_validate(row).model_dump(mode="json"))')
    source = source.replace('ChildRead.model_validate(await insert(Child, {"', 'ChildRead.model_validate(await insert(Child, {"')
    source = source.replace('return [ChildRead.model_validate(await insert(Child, {"{{foreign_key}}": parent_id, **data.model_dump()}, session)).model_dump(mode="json") for data in records]', 'return [json_object(ChildRead.model_validate(await insert(Child, {"{{foreign_key}}": parent_id, **data.model_dump()}, session)).model_dump(mode="json")) for data in records]')
    write('api.business.head.mongodb', types + 'class Counter(TypedDict):\n    value: int\n\n\n' + source, BASE['api.business.head.mongodb']['imports'] + ['from collections.abc import Callable, Awaitable, Mapping', 'from typing import Annotated, TypedDict', 'from pymongo.asynchronous.client_session import AsyncClientSession', 'from beanie import Document as MongoDocument', 'from pydantic import BaseModel, TypeAdapter'])

    for identity in BASE:
        if not identity.startswith('api.mongo.'):
            continue
        source = BASE[identity]['template']
        role = 'Parent' if '.parent.' in identity else 'Child'
        returns = 'list[' + role + ']' if '.list.' in identity else 'Response' if '.delete' in identity else 'BatchRead' if identity.endswith('.batch') else role
        callback_return = 'None' if '.delete' in identity else 'BatchRead' if identity.endswith('.batch') else role
        source = re.sub(r'^(    async def [^\n]+)\):', r'\1) -> ' + returns + ':', source, flags=re.M)
        source = source.replace('async def write(session):', 'async def write(session: AsyncClientSession) -> ' + callback_return + ':')
        source = source.replace('filters = {', 'filters: dict[str, object] = {')
        source = source.replace('return await Parent.find_one(Parent.id == row.id, session=session)', 'return await require_parent(row.id, session)')
        source = source.replace('return await Child.find_one(Child.id == row.id, session=session)', 'return await require_child(parent_id, row.id, session)')
        source = source.replace('return {"parent": parent, "children": children}', 'return BatchRead.model_validate({"parent": parent, "children": children})')
        source = source.replace('limIt: int = Query(20, alias="limit", ge=1, le={{max_limit}})', 'page_size: Annotated[int, Query(alias="limit", ge=1, le={{max_limit}})] = 20')
        source = source.replace('offset: int = Query(0, ge=0)', 'offset: Annotated[int, Query(ge=0)] = 0').replace('limIt', 'page_size')
        write(identity, source)

    for database in ('postgresql', 'mongodb'):
        identity = 'service.business.app.' + database
        source = BASE[identity]['template'].replace('def healthz():', 'def healthz() -> dict[str, str]:')
        if database == 'postgresql':
            shutdown = '    try:\n        yield\n    finally:\n        try:\n            close_integrations()\n        finally:\n            engine.dispose()\n'
            source = source.replace('app = FastAPI()', 'app = FastAPI(lifespan=lifespan)')
        else:
            shutdown = '    async with database_lifespan(application):\n        try:\n            yield\n        finally:\n            close_integrations()\n'
            source = source.replace('database, get_session, lifespan', 'database, get_session, lifespan as database_lifespan')
        source = source.replace('app = FastAPI(', '@asynccontextmanager\nasync def lifespan(application: FastAPI) -> AsyncIterator[None]:\n' + shutdown + '\n\napp = FastAPI(', 1)
        if database == 'postgresql':
            source = source.replace('lifespan(application:', 'lifespan(_application:')
        source += '''
JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def _has_nonfinite(value: JsonValue) -> bool:
    if isinstance(value, float):
        return not isfinite(value)
    if isinstance(value, list):
        return any(_has_nonfinite(item) for item in value)
    if isinstance(value, dict):
        return any(_has_nonfinite(item) for item in value.values())
    return False


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> Response:
    try:
        body = JSON_VALUE.validate_python(error.body, strict=True)
    except ValidationError:
        return await request_validation_exception_handler(request, error)
    if _has_nonfinite(body):
        return JSONResponse(status_code=422, content={"detail": [{
            "type": "finite_number", "loc": ["body"],
            "msg": "Input must contain finite JSON numbers",
        }]})
    return await request_validation_exception_handler(request, error)
'''
        write(identity, source, BASE[identity]['imports'] + [
            'from collections.abc import AsyncIterator',
            'from contextlib import asynccontextmanager',
            'from .integrations import close_integrations',
            'from math import isfinite',
            'from fastapi import Request, Response',
            'from fastapi.exceptions import RequestValidationError',
            'from fastapi.exception_handlers import request_validation_exception_handler',
            'from starlette.responses import JSONResponse',
            'from pydantic import TypeAdapter, ValidationError',
        ])

    for name in ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"):
        identity = "api.business.workflow." + name
        source = BASE[identity]["template"]
        source = source.replace("async def export_rows(parent_id: int):", "async def export_rows(parent_id: int) -> Response:")
        source = source.replace("async def import_rows(parent_id: int, body: str = Body(media_type=\"text/csv\")):", "async def import_rows(parent_id: int, body: Annotated[str, Body(media_type=\"text/csv\")]) -> list[JsonObject]:")
        source = source.replace('async def import_rows(parent_id: int, data: str = Body(media_type="text/csv", max_length=1048576)):', 'async def import_rows(parent_id: int, data: Annotated[str, Body(media_type="text/csv", max_length=1048576)]) -> list[JsonObject]:')
        if name == "decimal_summary":
            source = source.replace("async def export_rows(parent_id: int) -> Response:", "async def export_rows(parent_id: int) -> dict[str, dict[str, str]]:")
            source = source.replace("totals = {}", "totals: dict[str, Decimal] = {}").replace("key = row[{{group_field}}]", "key = str(row[{{group_field}}])")
        if name == "statistics":
            source = source.replace("async def export_rows(parent_id: int) -> Response:", "async def export_rows(parent_id: int) -> dict[str, int | float | None]:")
            source = source.replace("values = [", "values = [").replace("row[{{value_field}}] for", "float(str(row[{{value_field}}])) for")
        write(identity, source)

    write("test.composed.behavior", '''class Route(TypedDict):
    method: str
    path: str


class ComposedServiceTest(unittest.TestCase):
    def test_selected_routes_require_identity(self) -> None:
        routes = TypeAdapter(list[Route]).validate_json(ROUTES)
        application: object = importlib.import_module(APPLICATION + ".app").app
        self.assertIsInstance(application, FastAPI)
        if not isinstance(application, FastAPI):
            raise TypeError("service app must be a FastAPI application")
        with TestClient(application) as client:
            for route in routes:
                path = route["path"].replace("{parent_id}", "1").replace("{child_id}", "1")
                response = client.request(route["method"], path, json={})
                self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
'''.replace('class Route(TypedDict):', 'ROUTES = {{routes}}\n\n\nclass Route(TypedDict):'), ["import importlib", "import unittest", "from typing import TypedDict", "from pydantic import TypeAdapter", "from fastapi import FastAPI", "from fastapi.testclient import TestClient"])
    for database in ("postgresql", "mongodb"):
        identity = "test.business.head." + database
        source = BASE[identity]["template"]
        source = re.sub(r'^from \{\{package\}\}.app import app\n?', '', source, flags=re.M)
        write(identity, 'APPLICATION = "{{package}}"\n' + source,
              [i for i in BASE[identity]["imports"] if '.app import app' not in i])

    project = BASE["service.business.project"]["template"]
    project = project.replace('dev = ["httpx==0.28.1"]', '''dev = [
    "httpx==0.28.1", "ruff==0.16.6", "mypy==2.3.1",
    "pylint==4.0.9", "isort==9.0.2", "boto3-stubs[s3]==1.43.106",
    "types-requests==2.33.0.20260906", "types-authlib==1.8.0.20260911",
]''')
    project += "\n" + (ROOT / "som-rs/contracts/python-quality-v1/pyproject.toml").read_text().replace('known-first-party = []', 'known-first-party = ["{{package}}"]')
    project += '\nknown_first_party = ["{{package}}"]\n'
    quality_root = ROOT / "som-rs/contracts/python-quality-v1"
    dependency_groups = tomllib.loads(project)["project"]["dependencies"]
    dependency_groups += tomllib.loads(project)["dependency-groups"]["dev"]
    dependencies_hash = hashlib.sha256(
        json.dumps(dependency_groups, separators=(",", ":")).encode()
    ).hexdigest()
    project += '\n[tool.som.python-quality]\n'
    project += 'contract = "som.python-quality.v1"\n'
    for key, name in (("profile_sha256", "pyproject.toml"),
                      ("contract_sha256", "contract.md"),
                      ("exceptions_sha256", "exceptions.json")):
        project += key + ' = "' + hashlib.sha256(
            (quality_root / name).read_bytes()).hexdigest() + '"\n'
    project += 'dependencies_sha256 = "' + dependencies_hash + '"\n'
    project += 'formatter_sha256 = "' + hashlib.sha256(
        Path("/private/tmp/som-python-quality-tools-2026-10-01/bin/ruff").read_bytes()
    ).hexdigest() + '"\n'
    registry = json.loads((quality_root / "exceptions.json").read_text())
    project += "exception_registry = '''" + json.dumps(
        registry["exceptions"], separators=(",", ":")
    ) + "'''\n"
    project += '\n[tool.som.python-quality.tools]\n'
    for name, version in (("ruff", "0.16.6"), ("mypy", "2.3.1"),
                          ("pylint", "4.0.9"), ("isort", "9.0.2")):
        project += name + ' = "' + version + '"\n'
    write("service.business.project", project)


if __name__ == "__main__":
    main()
