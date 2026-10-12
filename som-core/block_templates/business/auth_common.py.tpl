import json
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response

APPLICATION = {{package}}
TEST_MODE = os.environ.get("APP_ENV") == "test"
auth_router = APIRouter()


def setting(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(name + " is required")
    return value


def local_override(name):
    value = os.environ.get(name)
    if value:
        parsed = urlsplit(value if "://" in value else "http://" + value)
        if not TEST_MODE or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.scheme not in {"http", "https"}:
            raise RuntimeError(name + " is allowed only for local test services")
    return value


def unauthorized():
    return HTTPException(status_code=401, detail="invalid or expired authentication", headers={"WWW-Authenticate": "Bearer"})


def bearer(request):
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or len(token) > 16384:
        raise unauthorized()
    return token


def require_identity(request: Request):
    identity = authenticate(request)
    if not isinstance(identity.get("sub"), str) or not identity["sub"]:
        raise unauthorized()
    return identity


def require_access(request: Request, identity=Depends(require_identity)):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and identity.get("role", "reader") != "editor":
        raise HTTPException(status_code=403, detail="editor role required")
    return identity


@auth_router.get("/auth/me")
def who_am_i(identity=Depends(require_identity)):
    return {key: identity[key] for key in ("sub", "role", "csrf") if key in identity}
