def bearer(request: Request) -> str:
    """Read one bounded bearer token."""
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or len(token) > 16384:
        raise unauthorized()
    return token


def validated_identity(subject: object, role: object) -> Identity:
    """Validate provider claims before using them for access."""
    if not isinstance(subject, str) or not subject or not isinstance(role, str):
        raise unauthorized()
    return {"sub": subject, "role": role}


