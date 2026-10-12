import math
import time

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials as firebase_credentials
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import GoogleAuthError
from firebase_admin import exceptions as firebase_errors

FIREBASE_PROJECT = setting("FIREBASE_PROJECT_ID")
firebase_emulator = local_override("FIREBASE_AUTH_EMULATOR_HOST")


class LocalFirebaseCredential(firebase_credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


firebase_app = firebase_admin.initialize_app(
    LocalFirebaseCredential() if firebase_emulator else None,
    options={"projectId": FIREBASE_PROJECT},
    name=APPLICATION,
)


def authenticate(request):
    try:
        claims = firebase_auth.verify_id_token(bearer(request), app=firebase_app, check_revoked=True)
        now = time.time()
        if any(type(claims.get(key)) not in {int, float} or not math.isfinite(claims[key]) for key in ("exp", "iat", "auth_time")) or claims["exp"] <= now or claims["iat"] > now or claims["auth_time"] > now:
            raise ValueError("expired or invalid token time claims")
    except (firebase_auth.InvalidIdTokenError, firebase_auth.ExpiredIdTokenError, firebase_auth.RevokedIdTokenError, firebase_auth.UserDisabledError, firebase_auth.UserNotFoundError, ValueError, KeyError, TypeError) as exc:
        raise unauthorized() from exc
    except (firebase_errors.FirebaseError, GoogleAuthError) as exc:
        raise HTTPException(status_code=503, detail="authentication provider unavailable") from exc
    return {"sub": claims["uid"], "role": claims.get("role", "reader")}
