"""Private fixture controls. Generated products cannot import this module."""

import time
import uuid
from pathlib import Path

import boto3
import httpx
import jwt
import redis


class Providers:
    def __init__(self, manifest, prompt):
        self.manifest = manifest
        self.prompt = prompt
        self.caps = prompt["capabilities"]
        self.run_id = uuid.uuid4().hex
        self.bucket = "som-test-" + self.run_id

    def control(self, **actions):
        response = httpx.post(self.manifest["base_url"] + "/__control", json=actions, timeout=15)
        response.raise_for_status()

    def environment(self):
        m = self.manifest
        self.control(faults={}, users={"editor": {}, "reader": {}}, next_user="editor", bucket=self.bucket)
        if self.caps["storage"] == "s3":
            client = boto3.client("s3", endpoint_url=m["s3_url"], region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
            client.create_bucket(Bucket=self.bucket)
        env = {"APP_ENV": "test", "JWT_PUBLIC_KEY_FILE": m["public_key"], "JWT_ISSUER": "som-local-fixture", "JWT_AUDIENCE": self.prompt["package"], "FIREBASE_PROJECT_ID": "demo-som", "FIREBASE_AUTH_EMULATOR_HOST": m["base_url"].removeprefix("http://"), "GOOGLE_CLIENT_ID": "som-local-client", "GOOGLE_CLIENT_SECRET": "local-test-secret", "GOOGLE_EDITOR_SUBJECTS": '["editor"]', "SOM_TEST_OIDC_ISSUER": m["base_url"], "REDIS_URL": m["redis_url"], "REDIS_NAMESPACE": "som-test-" + self.run_id, "S3_BUCKET": self.bucket, "SOM_TEST_S3_ENDPOINT": m["s3_url"], "GCS_BUCKET": self.bucket, "GCS_PROJECT": "demo-som", "STORAGE_EMULATOR_HOST": m["base_url"]}
        # Fixtures supply fake credentials through the normal SDK interface.
        # A valid application does not need a test-only credential fallback.
        env.update(AWS_ACCESS_KEY_ID="som-local-test", AWS_SECRET_ACCESS_KEY="som-local-test",
                   AWS_DEFAULT_REGION="us-east-1", AWS_REGION="us-east-1",
                   AWS_EC2_METADATA_DISABLED="true", AWS_SHARED_CREDENTIALS_FILE="/dev/null",
                   AWS_CONFIG_FILE="/dev/null")
        return env

    def jwt(self, role="editor", changes=None, remove=(), algorithm="RS256", key=None):
        now = int(time.time())
        claims = {"sub": role, "role": role, "iss": "som-local-fixture", "aud": self.prompt["package"], "iat": now, "exp": now + 3600}
        claims.update(changes or {})
        for name in remove:
            claims.pop(name, None)
        return jwt.encode(claims, key or Path(self.manifest["private_key"]).read_bytes(), algorithm=algorithm)

    def login(self, client, role="editor"):
        auth = self.caps["auth"]
        if auth == "jwt":
            client.headers["Authorization"] = "Bearer " + self.jwt(role)
        elif auth == "firebase":
            response = httpx.post(self.manifest["base_url"] + "/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword", json={"email": role + "@example.test", "password": "test-only"})
            response.raise_for_status()
            client.headers["Authorization"] = "Bearer " + response.json()["idToken"]
        else:
            self.control(next_user=role)
            first = client.get("/auth/google/login")
            assert first.status_code == 302, first.text
            provider = client.get(first.headers["location"])
            assert provider.status_code == 302, provider.text
            callback = client.get(provider.headers["location"])
            assert callback.status_code == 200, callback.text
            identity = client.get("/auth/me")
            assert identity.status_code == 200, identity.text
            client.headers["X-CSRF-Token"] = identity.json()["csrf"]
            return provider.headers["location"]
        identity = client.get("/auth/me")
        assert identity.status_code == 200, identity.text
        assert identity.json()["role"] == role

    def auth_checks(self, client, route, create_payload, env, done):
        with httpx.Client(base_url=str(client.base_url), timeout=15) as other:
            assert other.get(route).status_code == 401
            self.login(other, "reader")
            assert other.get(route).status_code == 200
            assert other.post(route, json=create_payload).status_code == 403
            if self.caps["storage"] != "none":
                assert other.put("/objects/reader", content=b"x").status_code == 403
        done("missing_identity_and_reader_write_denial")
        if self.caps["auth"] == "jwt":
            invalid = [self.jwt(changes={"exp": 1}), self.jwt(changes={"iss": "wrong"}), self.jwt(changes={"aud": "wrong"}), self.jwt(remove=["exp"]), self.jwt(algorithm="HS256", key=b"independent-wrong-secret-at-least-32-bytes")]
            prefix, signature = self.jwt().rsplit(".", 1)
            invalid.append(prefix + "." + ("A" if signature[0] != "A" else "B") + signature[1:])
            for token in invalid:
                assert client.get(route, headers={"Authorization": "Bearer " + token}).status_code == 401
            done("jwt_signature_algorithm_issuer_audience_expiry_and_required_claims")
        elif self.caps["auth"] == "firebase":
            for state in ({"disabled": True}, {"validSince": str(int(time.time()) + 60)}):
                self.control(users={"editor": state})
                assert client.get(route).status_code == 401
            self.control(users={"editor": {}})
            assert client.get(route).status_code == 200
            token = client.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(token, options={"verify_signature": False})
            for changes in ({"aud": "other-project"}, {"exp": 1}, {"iss": "wrong"}):
                bad = jwt.encode(claims | changes, key=None, algorithm="none")
                response = client.get(route, headers={"Authorization": "Bearer " + bad})
                assert response.status_code == 401, (changes, response.status_code, response.text)
            done("firebase_project_expiry_revocation_disabled_user_and_recovery")
        else:
            assert client.get("/auth/google/callback", params={"code": "wrong", "state": "wrong"}).status_code == 400
            replay = self.login(client)
            assert client.get(replay).status_code == 400
            first = client.get("/auth/google/login")
            provider = client.get(first.headers["location"])
            with httpx.Client(base_url=str(client.base_url)) as stranger:
                assert stranger.get(provider.headers["location"]).status_code == 400
            self.control(faults={"nonce": True})
            first = client.get("/auth/google/login")
            provider = client.get(first.headers["location"])
            assert client.get(provider.headers["location"]).status_code == 401
            self.control(faults={})
            self.login(client)
            assert client.post(route, json=create_payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
            cookies = dict(client.cookies)
            assert client.post("/auth/logout").status_code == 204
            with httpx.Client(base_url=str(client.base_url), cookies=cookies) as stale:
                assert stale.get(route).status_code == 401
            self.login(client)
            store = redis.Redis.from_url(self.manifest["redis_url"])
            prefix = env["REDIS_NAMESPACE"] + ":" + self.prompt["package"] + ":session:"
            for key in store.scan_iter(match=prefix + "*"):
                store.pexpire(key, 1)
            time.sleep(.02)
            assert client.get(route).status_code == 401
            self.control(redis="stop")
            assert client.get("/auth/google/login").status_code == 503
            self.control(redis="start")
            self.login(client)
            done("oauth_pkce_state_replay_browser_binding_nonce_csrf_logout_expiry_redis_failure_and_recovery")

    def close(self):
        self.control(faults={}, users={"editor": {}, "reader": {}}, next_user="editor")
