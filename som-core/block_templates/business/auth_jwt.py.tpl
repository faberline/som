from pathlib import Path

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

JWT_KEY = Path(setting("JWT_PUBLIC_KEY_FILE")).read_bytes()
JWT_ISSUER = setting("JWT_ISSUER")
JWT_AUDIENCE = setting("JWT_AUDIENCE")
parsed_key = load_pem_public_key(JWT_KEY)
if not isinstance(parsed_key, RSAPublicKey) or parsed_key.key_size < 2048:
    raise RuntimeError("JWT_PUBLIC_KEY_FILE must contain an RSA key of at least 2048 bits")


def authenticate(request):
    try:
        claims = jwt.decode(bearer(request), JWT_KEY, algorithms=["RS256"], audience=JWT_AUDIENCE, issuer=JWT_ISSUER, options={"require": ["sub", "exp", "iat", "iss", "aud"]})
    except jwt.InvalidTokenError as exc:
        raise unauthorized() from exc
    return {"sub": claims["sub"], "role": claims.get("role", "reader")}
