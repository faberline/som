"""Exercise generated SDK boundaries with deliberately malformed provider data."""

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request


def verify(root: Path, prompt: dict, fixture: dict) -> dict:
    from business_test_support import Providers
    os.environ.update(Providers(fixture, prompt).environment() |
                      {"GOOGLE_REDIRECT_URI": "http://127.0.0.1:65530/auth/google/callback"})
    sys.path.insert(0, str(root))
    module = importlib.import_module(prompt["package"] + ".integrations")
    cases = []

    def request(method="GET", cookies="", csrf=""):
        return Request({"type": "http", "method": method,
                        "headers": [(b"authorization", b"Bearer synthetic-token"),
                                    (b"cookie", cookies.encode()),
                                    (b"x-csrf-token", csrf.encode())]})

    def rejected(name, code, action):
        try:
            action()
        except HTTPException as error:
            assert error.status_code == code, (name, code, error.status_code)
        else:
            raise AssertionError((name, "provider value was accepted"))
        cases.append(name)

    authentication = prompt["capabilities"]["auth"]
    if authentication == "jwt":
        for claims in ({}, {"sub": 4}, {"sub": []}, {"sub": ""}, {"sub": "user", "role": []}):
            with patch.object(module.jwt, "decode", return_value=claims):
                rejected("jwt malformed identity " + str(len(cases)), 401,
                         lambda: module.authenticate(request()))
    elif authentication == "firebase":
        valid = {"uid": "user", "role": "editor", "exp": time.time() + 300,
                 "iat": time.time() - 60, "auth_time": time.time() - 60}
        values = [None, [], {}, valid | {"uid": 3}, valid | {"uid": ""},
                  valid | {"role": []}, valid | {"exp": float("nan")},
                  valid | {"iat": True}, valid | {"auth_time": float("inf")}]
        for claims in values:
            with patch.object(module._fb_auth, "verify_id_token", return_value=claims):
                rejected("firebase malformed claims " + str(len(cases)), 401,
                         lambda: module.authenticate(request()))
        with patch.object(module._fb_auth, "verify_id_token", return_value=valid):
            assert module.authenticate(request()) == {"sub": "user", "role": "editor"}
        cases.append("firebase validated first-party identity")
    else:
        cookie = module.SESSION_COOKIE + "=synthetic"
        valid = {"sub": "user", "role": "editor", "csrf": "proof"}
        for stored in ([], {}, valid | {"sub": 3}, valid | {"sub": ""},
                       valid | {"role": []}, {"sub": "user", "role": "editor"},
                       valid | {"csrf": ""}):
            with patch.object(module, "redis_read", return_value=json.dumps(stored)):
                rejected("oauth malformed session " + str(len(cases)), 401,
                         lambda: module.authenticate(request(cookies=cookie)))
        with patch.object(module, "redis_read", return_value=json.dumps(valid)):
            rejected("oauth stored identity still requires write CSRF", 403,
                     lambda: module.authenticate(request("POST", cookie)))
            assert module.authenticate(request("POST", cookie, "proof")) == valid
        cases.append("oauth valid typed identity and CSRF")
        with patch.object(module.redis_client, "get", return_value=b"invalid SDK response"):
            rejected("redis SDK response must be decoded text", 503,
                     lambda: module.redis_read("key"))
        for saved in ([], {}, {"nonce": 3, "verifier": "x", "binding": "x"}):
            with patch.object(module, "redis_read", return_value=json.dumps(saved)):
                rejected("oauth malformed login state " + str(len(cases)), 400,
                         lambda: module.google_callback(request(), "state", "code"))
        saved = json.dumps({"nonce": "nonce", "verifier": "verifier", "binding": "binding"})
        callback_request = request(cookies=module.BINDING_COOKIE + "=binding")
        for claims in ({"sub": 3, "exp": time.time()+300, "nonce": "nonce"},
                       {"sub": "", "exp": time.time()+300, "nonce": "nonce"},
                       {"sub": "user", "exp": float("nan"), "nonce": "nonce"}):
            with patch.object(module, "redis_read", return_value=saved), \
                 patch.object(module.OAuth2Session, "fetch_token", return_value={"id_token": "encoded"}), \
                 patch.object(module.google_keys, "get_signing_key_from_jwt", return_value=SimpleNamespace(key="key")), \
                 patch.object(module.jwt, "decode", return_value=claims):
                rejected("oauth malformed verified SDK claims " + str(len(cases)), 401,
                         lambda: module.google_callback(callback_request, "state", "code"))
    if prompt["capabilities"]["storage"] == "gcs":
        with patch.object(module.gcs_bucket, "blob") as blob:
            blob.return_value.download_as_bytes.return_value = "unexpected text"
            try:
                module.storage_get("key")
            except module.ObjectUnavailable:
                cases.append("gcs rejects non-byte SDK data")
            else:
                raise AssertionError("GCS returned unvalidated SDK data")
    module.close_integrations()
    assert module.CLOSERS == []
    cases.append("all registered SDK resources close")
    return {"passed": True, "checks": cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.root, json.loads(args.prompt.read_text()), json.loads(args.fixture.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
