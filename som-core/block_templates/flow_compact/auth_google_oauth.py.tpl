

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
editor_subjects = TypeAdapter(list[str]).validate_json(setting("GOOGLE_EDITOR_SUBJECTS"), strict=True)
if any(not value for value in editor_subjects):
    raise RuntimeError("GOOGLE_EDITOR_SUBJECTS must contain nonempty strings")
EDITOR_SUBJECTS = set(editor_subjects)
SESSION_COOKIE = "som_" + APPLICATION
BINDING_COOKIE = SESSION_COOKIE + "_login"
REDIS_PREFIX = setting("REDIS_NAMESPACE") + ":" + APPLICATION + ":"
redis_client = redis.Redis.from_url(setting("REDIS_URL"), decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
google_keys = jwt.PyJWKClient(OIDC_JWKS, timeout=5)


class LoginState(TypedDict):
    """Typed service contract."""
    nonce: str
    verifier: str
    binding: str


class OAuthToken(TypedDict):
    """Typed service contract."""
    id_token: str


class GoogleClaims(TypedDict):
    """Typed service contract."""
    sub: str
    exp: int | float
    nonce: str


@contextmanager
def session_errors() -> Iterator[None]:
    """Keep Redis errors at the session boundary."""
    try:
        yield
    except redis.RedisError as error:
        raise failure(503, "session store unavailable") from error


def redis_read(key: str, consume: bool = False) -> str | None:
    """Validate an atomic read or consume result."""
    try:
        with session_errors():
            value: object = redis_client.getdel(key) if consume else redis_client.get(key)
        return TypeAdapter(str | None).validate_python(value, strict=True)
    except ValueError as error:
        raise failure(503, "invalid session response") from error


def redis_set(key: str, value: str, seconds: int) -> None:
    """Refuse to replace another session or state."""
    with session_errors():
        if redis_client.set(key, value, ex=seconds, nx=True) is not True:
            raise failure(503, "session creation failed")


def session_cookie(response: Response, name: str, value: str, seconds: int) -> None:
    """Use the same browser limits for state and identity."""
    response.set_cookie(name, value, max_age=seconds, httponly=True, secure=not TEST_MODE, samesite="lax")


def oauth_client() -> OAuth2Session:


    """Oauth client."""
    return OAuth2Session(OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, scope="openid email profile", redirect_uri=OIDC_REDIRECT_URI, token_endpoint_auth_method="client_secret_post", code_challenge_method="S256")


def same_secret(left: str, right: str) -> bool:
    """Compare bytes so malformed Unicode inputs also fail safely."""
    return hmac.compare_digest(left.encode(), right.encode())


@auth_router.get("/auth/google/login")
def google_login() -> RedirectResponse:
    """Google login."""
    state, nonce, verifier, binding = [secrets.token_urlsafe(32) for _ in range(4)]
    redis_set( REDIS_PREFIX + "state:" + state, json.dumps({"nonce": nonce, "verifier": verifier, "binding": binding}), 300)
    with oauth_client() as client:
        url, _ = client.create_authorization_url(OIDC_AUTHORIZE, state=state, nonce=nonce, code_verifier=verifier)
    response = RedirectResponse(url, status_code=302)
    session_cookie(response, BINDING_COOKIE, binding, 300)
    return response


@auth_router.get("/auth/google/callback")
def google_callback(request: Request, state: str, code: str) -> Response:
    """Google callback."""
    if len(state) > 128 or len(code) > 4096:
        raise HTTPException(status_code=400, detail="invalid OAuth callback")
    saved = redis_read(REDIS_PREFIX + "state:" + state, consume=True)
    if not saved:
        raise HTTPException(status_code=400, detail="expired or reused OAuth state")
    try:
        login_state = TypeAdapter(LoginState).validate_json(saved, strict=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid OAuth state") from exc
    if not same_secret(login_state["binding"], request.cookies.get(BINDING_COOKIE, "")):
        raise HTTPException(status_code=400, detail="OAuth browser binding mismatch")
    try:
        with oauth_client() as client:
            token = TypeAdapter(OAuthToken).validate_python(client.fetch_token(OIDC_TOKEN, code=code, code_verifier=login_state["verifier"], timeout=5), strict=True)
        encoded = token["id_token"]
        key = google_keys.get_signing_key_from_jwt(encoded).key
        claims = TypeAdapter(GoogleClaims).validate_python(jwt.decode(encoded, key, algorithms=["RS256"], audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER, options={"require": ["sub", "exp", "iat", "iss", "aud", "nonce"]}), strict=True)
        if not claims["sub"] or not math.isfinite(claims["exp"]):
            raise unauthorized()
        if not same_secret(claims["nonce"], login_state["nonce"]):
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
    identity: Identity = {"sub": claims["sub"], "role": "editor" if claims["sub"] in EDITOR_SUBJECTS else "reader", "csrf": secrets.token_urlsafe(32)}
    redis_set( REDIS_PREFIX + "session:" + session_id, json.dumps(identity), lifetime)
    response = Response(content='{"status":"ok"}', media_type="application/json")
    session_cookie(response, SESSION_COOKIE, session_id, lifetime)
    response.delete_cookie(BINDING_COOKIE)
    return response


def authenticate(request: Request) -> Identity:


    """Authenticate."""
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if not session_id or len(session_id) > 128:
        raise unauthorized()
    saved = redis_read( REDIS_PREFIX + "session:" + session_id)
    if not saved:
        raise unauthorized()
    try:
        identity = TypeAdapter(Identity).validate_json(saved, strict=True)
    except ValueError as exc:
        raise unauthorized() from exc
    if not identity["sub"] or not identity.get("csrf"):
        raise unauthorized()
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not same_secret(identity.get("csrf", ""), request.headers.get("x-csrf-token", "")):
        raise HTTPException(status_code=403, detail="CSRF token required")
    return identity


@auth_router.post("/auth/logout", status_code=204, dependencies=[Depends(require_identity)])
def logout(request: Request) -> Response:
    """Logout."""
    with session_errors():
        redis_client.delete(REDIS_PREFIX + "session:" + request.cookies[SESSION_COOKIE])
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE)
    return response
CLOSERS.append(redis_client.close)
