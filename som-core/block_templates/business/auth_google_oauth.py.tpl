import hmac
import secrets
import time

import jwt
import redis
import requests
from authlib.integrations.requests_client import OAuth2Session
from authlib.oauth2.rfc6749.errors import OAuth2Error
from fastapi.responses import RedirectResponse

OIDC_CLIENT_ID = setting("GOOGLE_CLIENT_ID")
OIDC_CLIENT_SECRET = setting("GOOGLE_CLIENT_SECRET")
OIDC_REDIRECT_URI = setting("GOOGLE_REDIRECT_URI")
test_issuer = local_override("SOM_TEST_OIDC_ISSUER")
OIDC_ISSUER = test_issuer or "https://accounts.google.com"
OIDC_AUTHORIZE = OIDC_ISSUER + ("/authorize" if test_issuer else "/o/oauth2/v2/auth")
OIDC_TOKEN = OIDC_ISSUER + "/token" if test_issuer else "https://oauth2.googleapis.com/token"
OIDC_JWKS = OIDC_ISSUER + "/jwks" if test_issuer else "https://www.googleapis.com/oauth2/v3/certs"
if not TEST_MODE and urlsplit(OIDC_REDIRECT_URI).scheme != "https":
    raise RuntimeError("GOOGLE_REDIRECT_URI must use HTTPS outside tests")
editor_subjects = json.loads(setting("GOOGLE_EDITOR_SUBJECTS"))
if not isinstance(editor_subjects, list) or any(not isinstance(value, str) or not value for value in editor_subjects):
    raise RuntimeError("GOOGLE_EDITOR_SUBJECTS must be a JSON list of subject strings")
EDITOR_SUBJECTS = set(editor_subjects)
SESSION_COOKIE = "som_" + APPLICATION
BINDING_COOKIE = SESSION_COOKIE + "_login"
REDIS_PREFIX = setting("REDIS_NAMESPACE") + ":" + APPLICATION + ":"
redis_client = redis.Redis.from_url(setting("REDIS_URL"), decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
google_keys = jwt.PyJWKClient(OIDC_JWKS, timeout=5)


def redis_call(method, *args, **kwargs):
    try:
        return getattr(redis_client, method)(*args, **kwargs)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="session store unavailable") from exc


def oauth_client():
    return OAuth2Session(OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, scope="openid email profile", redirect_uri=OIDC_REDIRECT_URI, token_endpoint_auth_method="client_secret_post", code_challenge_method="S256")


@auth_router.get("/auth/google/login")
def google_login():
    state, nonce, verifier, binding = [secrets.token_urlsafe(32) for _ in range(4)]
    redis_call("set", REDIS_PREFIX + "state:" + state, json.dumps({"nonce": nonce, "verifier": verifier, "binding": binding}), ex=300, nx=True)
    with oauth_client() as client:
        url, _ = client.create_authorization_url(OIDC_AUTHORIZE, state=state, nonce=nonce, code_verifier=verifier)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(BINDING_COOKIE, binding, max_age=300, httponly=True, secure=not TEST_MODE, samesite="lax")
    return response


@auth_router.get("/auth/google/callback")
def google_callback(request: Request, state: str, code: str):
    if len(state) > 128 or len(code) > 4096:
        raise HTTPException(status_code=400, detail="invalid OAuth callback")
    saved = redis_call("getdel", REDIS_PREFIX + "state:" + state)
    if not saved:
        raise HTTPException(status_code=400, detail="expired or reused OAuth state")
    saved = json.loads(saved)
    if not hmac.compare_digest(saved["binding"], request.cookies.get(BINDING_COOKIE, "")):
        raise HTTPException(status_code=400, detail="OAuth browser binding mismatch")
    try:
        with oauth_client() as client:
            token = client.fetch_token(OIDC_TOKEN, code=code, code_verifier=saved["verifier"], timeout=5)
        encoded = token["id_token"]
        key = google_keys.get_signing_key_from_jwt(encoded).key
        claims = jwt.decode(encoded, key, algorithms=["RS256"], audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER, options={"require": ["sub", "exp", "iat", "iss", "aud", "nonce"]})
        if not isinstance(claims["nonce"], str) or not hmac.compare_digest(claims["nonce"], saved["nonce"]):
            raise unauthorized()
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(status_code=503, detail="identity provider unavailable") from exc
    except (jwt.InvalidTokenError, jwt.PyJWKClientError, OAuth2Error, KeyError, ValueError) as exc:
        raise unauthorized() from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="identity provider unavailable") from exc
    lifetime = min(3600, int(claims["exp"] - time.time()))
    if lifetime <= 0:
        raise unauthorized()
    session_id = secrets.token_urlsafe(32)
    identity = {"sub": claims["sub"], "role": "editor" if claims["sub"] in EDITOR_SUBJECTS else "reader", "csrf": secrets.token_urlsafe(32)}
    redis_call("set", REDIS_PREFIX + "session:" + session_id, json.dumps(identity), ex=lifetime, nx=True)
    response = Response(content='{"status":"ok"}', media_type="application/json")
    response.set_cookie(SESSION_COOKIE, session_id, max_age=lifetime, httponly=True, secure=not TEST_MODE, samesite="lax")
    response.delete_cookie(BINDING_COOKIE)
    return response


def authenticate(request):
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if not session_id or len(session_id) > 128:
        raise unauthorized()
    saved = redis_call("get", REDIS_PREFIX + "session:" + session_id)
    if not saved:
        raise unauthorized()
    identity = json.loads(saved)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(identity["csrf"], request.headers.get("x-csrf-token", "")):
        raise HTTPException(status_code=403, detail="CSRF token required")
    return identity


@auth_router.post("/auth/logout", status_code=204)
def logout(request: Request, identity=Depends(require_identity)):
    redis_call("delete", REDIS_PREFIX + "session:" + request.cookies[SESSION_COOKIE])
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response
