APPLICATION = {{package}}
TEST_MODE = os.environ.get("APP_ENV") == "test"
auth_router = APIRouter()
CLOSERS: list[Callable[[], None]] = []


def close_integrations() -> None:


    """Close integrations."""
    with ExitStack() as shutdown:
        for closer in CLOSERS:
            shutdown.callback(closer)
        CLOSERS.clear()


class Identity(TypedDict):
    """Typed service contract."""
    sub: str
    role: str
    csrf: NotRequired[str]


def setting(name: str) -> str:


    """Setting."""
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(name + " is required")
    return value


def local_override(name: str) -> str | None:


    """Local override."""
    value = os.environ.get(name)
    if value:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        if (not TEST_MODE or parsed.hostname not in
            {"127.0.0.1", "localhost", "::1"} or parsed.username or
            parsed.password or parsed.scheme not in {"http", "https"}):
            raise RuntimeError(name + " is allowed only for local test services")
    return value


def unauthorized() -> HTTPException:


    """Unauthorized."""
    return HTTPException(status_code=401,
                         detail="invalid or expired authentication",
                         headers={"WWW-Authenticate": "Bearer"})


def require_identity(request: Request) -> Identity:


    """Require identity."""
    return authenticate(request)


def require_access(request: Request,
                   identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    """Require editor permission for writes."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and identity["role"] != "editor":
        raise HTTPException(status_code=403, detail="editor role required")
    return identity


@auth_router.get("/auth/me")
def who_am_i(identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    """Who am i."""
    return identity
