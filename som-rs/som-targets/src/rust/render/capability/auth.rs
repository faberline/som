//! The auth providers of a capability_postgresql product (issue #53 F). jwt keeps the RS256
//! bearer tokens of the D product byte for byte. firebase checks Firebase ID tokens as
//! firebase_admin.auth.verify_id_token(check_revoked=True) does, through the Auth emulator in
//! tests. google_oauth signs callers in with Google OpenID Connect (PKCE, state, nonce and a
//! browser binding) and keeps their sessions in Redis, as the Python product does with authlib,
//! PyJWT and redis-py. The provider calls use hyper with rustls (ring); Redis is spoken in RESP2.
use serde_json::Value;

use super::INTEGRATIONS;

/// The auth provider a prompt selects.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Auth {
    Jwt,
    Firebase,
    Google,
}

impl Auth {
    /// The auth param. Without one the provider is jwt.
    pub(super) fn read(params: &Value) -> Result<Self, String> {
        match &params["auth"] {
            Value::Null => Ok(Self::Jwt),
            Value::String(name) => match name.as_str() {
                "jwt" => Ok(Self::Jwt),
                "firebase" => Ok(Self::Firebase),
                "google_oauth" => Ok(Self::Google),
                other => Err(format!("no capability auth {other}")),
            },
            _ => Err("param auth is not a string".to_owned()),
        }
    }

    /// The Rust type of the provider in the product.
    pub(super) fn name(self) -> &'static str {
        match self {
            Self::Jwt => "Jwt",
            Self::Firebase => "Firebase",
            Self::Google => "Google",
        }
    }
}

/// The text of INTEGRATIONS from the first `from` up to the next `to`.
fn slice(from: &str, to: &str) -> &'static str {
    let text: &'static str = INTEGRATIONS;
    let start = text.find(from).expect("integrations slice start");
    let end = start + text[start..].find(to).expect("integrations slice end");
    &text[start..end]
}

/// src/integrations.rs of a firebase or google_oauth product. A v5 product reads the auth settings
/// before the database, and its Firebase role claim may be any JSON value.
pub(super) fn integrations(auth: Auth, v5: bool, package: &str) -> String {
    let provider = auth.name();
    let mut out = String::from(match auth {
        Auth::Google => GOOGLE_IMPORTS,
        _ => FIREBASE_IMPORTS,
    });
    out += &slice("/// The settings of the product", "/// setting(NAME)").replace("Jwt", provider);
    out += slice("/// setting(NAME)", "/// One authenticated caller.");
    out += &if auth == Auth::Google {
        LOCAL.to_owned()
    } else {
        local()
    };
    let identity = slice(
        "/// One authenticated caller.",
        "/// RS256 bearer tokens checked",
    );
    if auth == Auth::Google {
        out += &identity.replace(
            "    pub role: String,\n}",
            "    pub role: String,\n    pub csrf: String,\n}",
        );
        out += &GOOGLE.replace("@PACKAGE@", package);
        out += REDIS;
        out += &KEYS.replace("@REFUSED@", "reply.status >= 400");
    } else {
        out += identity;
        out += FIREBASE;
        out += ADC;
        out += &KEYS.replace("@REFUSED@", "reply.status != 200");
        out += slice(
            "/// The token of an Authorization header",
            "/// A base64url segment",
        );
    }
    out += HTTP;
    out += slice("/// A base64url segment", "enum PublicKey");
    if auth == Auth::Firebase {
        out += slice("fn pem_block(", "fn rsa_components(");
    }
    let call = if auth == Auth::Google {
        "state.auth.authenticate(&parts.method, &parts.headers).await"
    } else {
        "state.auth.authenticate(&parts.headers).await"
    };
    let extractors = &INTEGRATIONS[INTEGRATIONS
        .find("/// A caller with a valid token.")
        .expect("extractors")..];
    out += &extractors.replace("state.auth.authenticate(&parts.headers)", call);
    if !v5 {
        return out;
    }
    out = out
        .replace(
            &format!("            database: database()?,\n            auth: {provider}::load()?,\n"),
            &format!("            auth: {provider}::load()?,\n            database: database()?,\n"),
        )
        .replace(
            &format!("            database: database_url(url)?,\n            auth: {provider}::load()?,\n"),
            &format!("            auth: {provider}::load()?,\n            database: database_url(url)?,\n"),
        );
    if auth == Auth::Google {
        return out
            .replace(EDITORS_NOT_LIST, EDITORS_V5)
            .replace(EDITORS_EMPTY, EDITORS_V5);
    }
    out.replace("    pub role: String,\n", "    pub role: Value,\n")
        .replace(
            "(Some(Value::String(sub)), Value::String(role)) if !sub.is_empty()",
            "(Some(Value::String(sub)), role) if !sub.is_empty()",
        )
}

/// APP_ENV, local_override and the urlsplit they read, without the path and the query: only the
/// Redis URL of google_oauth reads them.
pub(super) fn local() -> String {
    LOCAL
        .replace("    path: String,\n    query: String,\n", "")
        .replace("        rest = after[end..].to_owned();\n", "")
        .replace(URL_TAIL, "    Ok(Url { scheme, netloc })\n")
}

/// pem_block of the jwt integrations, which the service account assertion reads.
pub(super) fn pem_block() -> &'static str {
    slice("fn pem_block(", "fn rsa_components(")
}

/// time.time() of KEYS.
pub(super) fn now() -> &'static str {
    &KEYS[KEYS.find("/// time.time().").expect("now")..]
}

/// The application default credentials of ADC as their own type, for the object store: an Adc
/// keeps the access token and its calls take the HTTP client.
pub(super) fn adc() -> String {
    ADC.replace(
        "impl Firebase {\n",
        "/// The application default credentials and the access token they last gave.\nstruct Adc {\n    token: Mutex<Option<(String, Instant)>>,\n}\n\nimpl Adc {\n    fn new() -> Self {\n        Self {\n            token: Mutex::new(None),\n        }\n    }\n\n",
    )
    .replace(
        "async fn access_token(&self) -> Option<String>",
        "async fn access_token(&self, client: &HttpClient) -> Option<String>",
    )
    .replace("self.fetch_token().await?", "Self::fetch_token(client).await?")
    .replace(
        "async fn fetch_token(&self) -> Option<(String, u64)>",
        "async fn fetch_token(client: &HttpClient) -> Option<(String, u64)>",
    )
    .replace("send(&self.client, request)", "send(client, request)")
}

/// The api.rs import of the integrations a provider needs.
pub(super) fn api_imports(auth: Auth, detail: bool) -> String {
    let mut names = vec!["Access", "Identity", auth.name()];
    if auth == Auth::Google {
        names.extend(["google_callback", "google_login", "logout"]);
    }
    if detail {
        names.push("python_json_detail");
    }
    format!("use crate::integrations::{{{}}};\n", names.join(", "))
}

/// The api.rs errors of a provider: its state type, and the status errors of the provider calls.
pub(super) fn api_errors(auth: Auth, errors: String) -> String {
    if auth == Auth::Jwt {
        return errors;
    }
    errors
        .replace("    pub auth: Arc<Jwt>,", &format!("    pub auth: Arc<{}>,", auth.name()))
        .replace(
            "/// The pool and the token checks every handler shares.",
            "/// The pool and the caller checks every handler shares.",
        )
        .replace("    TooMany,\n", "    TooMany,\n    Status(StatusCode, &'static str),\n")
        .replace(
            "            Self::Internal =>",
            "            Self::Status(status, detail) => (status, json!({\"detail\": detail})),\n            Self::Internal =>",
        )
}

/// tests/api.rs: a cookie session identifies the caller too.
pub(super) fn tests(auth: Auth, tests: String) -> String {
    if auth == Auth::Jwt {
        return tests;
    }
    let start = tests
        .find("/// TEST_AUTH_HEADERS is the test client's identity")
        .expect("identity test");
    tests[..start].to_owned() + TEST_IDENTITY
}

/// README.md of a provider.
pub(super) fn readme(auth: Auth, package: &str, readme: String) -> String {
    let (shape, me, guard, settings, test_env, tests) = match auth {
        Auth::Jwt => return readme,
        Auth::Firebase => (
            "Firebase ID tokens (`auth: firebase`)".to_owned(),
            "- `GET /auth/me`: the caller, `{\"sub\", \"role\"}`.\n".to_owned(),
            FIREBASE_GUARD.to_owned(),
            FIREBASE_SETTINGS,
            "TEST_AUTH_HEADERS='{\"authorization\": \"Bearer <token>\"}' TEST_AUTH_COOKIES='{}'",
            "The tests read the Firebase settings above. `TEST_AUTH_HEADERS` holds the test client's\nheaders and `TEST_AUTH_COOKIES` its cookies.",
        ),
        Auth::Google => (
            "Google OpenID Connect (`auth: google_oauth`)".to_owned(),
            GOOGLE_ROUTES.replace("@PACKAGE@", package),
            GOOGLE_GUARD.replace("@PACKAGE@", package),
            GOOGLE_SETTINGS,
            "TEST_AUTH_HEADERS='{\"x-csrf-token\": \"<csrf>\"}' TEST_AUTH_COOKIES='{\"som_@PACKAGE@\": \"<session>\"}'",
            "The tests read the Google and Redis settings above. `TEST_AUTH_HEADERS` holds the test\nclient's headers and `TEST_AUTH_COOKIES` its cookies.",
        ),
    };
    readme
        .replace("RS256 bearer tokens (`auth: jwt`)", &shape)
        .replace(
            "- `GET /auth/me`: the caller, `{\"sub\", \"role\"}`.\n",
            &me,
        )
        .replace(JWT_GUARD, &guard)
        .replace(JWT_SETTINGS, settings)
        .replace(
            "TEST_AUTH_HEADERS='{\"authorization\": \"Bearer <token>\"}'",
            &test_env.replace("@PACKAGE@", package),
        )
        .replace(
            "The tests read the token settings above. `TEST_AUTH_HEADERS` holds the test client's headers.",
            tests,
        )
}

const JWT_GUARD: &str =
    "Every route but `/healthz` needs `Authorization: Bearer <token>`, an RS256 token with `sub`,
`exp`, `iat`, `iss` and `aud`. A missing, expired or invalid token answers 401. A write needs the
`editor` role and answers 403 otherwise; a token without a role is a `reader`.
";

const JWT_SETTINGS: &str =
    "- `JWT_PUBLIC_KEY_FILE` (required): a PEM RSA public key of at least 2048 bits.
- `JWT_ISSUER` (required): the `iss` every token must carry.
- `JWT_AUDIENCE` (required): an `aud` every token must carry.
";

const FIREBASE_GUARD: &str =
    "Every route but `/healthz` needs `Authorization: Bearer <token>`, a Firebase ID token of the
project: RS256 under the securetoken keys (at least 2048 bits), with `aud` the project, `iss`
`https://securetoken.google.com/<project>`, a `sub` and current `iat`, `exp` and `auth_time`. The
account is looked up for every request: a disabled account, or a token issued before the account's
`validSince`, answers 401. A missing, expired or invalid token answers 401, and an unreachable
provider 503 `authentication provider unavailable`. A write needs the `editor` role claim and
answers 403 otherwise; a token without a role is a `reader`.
";

const FIREBASE_SETTINGS: &str =
    "- `FIREBASE_PROJECT_ID` (required): the Firebase project of the tokens.
- `FIREBASE_AUTH_EMULATOR_HOST`: `host:port` of a Firebase Auth emulator, whose tokens are not
  signed. Only `APP_ENV=test` with a loopback host may set it.
- `APP_ENV`: `test` for a local test run.
- Outside the emulator the account lookup uses the application default credentials:
  `GOOGLE_APPLICATION_CREDENTIALS`, the gcloud file, then the metadata server.
";

const GOOGLE_ROUTES: &str = "- `GET /auth/me`: the caller, `{\"sub\", \"role\", \"csrf\"}`.
- `GET /auth/google/login`: 302 to Google with PKCE (S256), a state and a nonce, and a browser
  binding cookie `som_@PACKAGE@_login`. They last 300 seconds.
- `GET /auth/google/callback`: `state` and `code` from Google. A missing, expired or reused state
  or a binding mismatch answers 400, an invalid ID token 401. It sets the session cookie
  `som_@PACKAGE@` and answers `{\"status\":\"ok\"}`.
- `POST /auth/logout`: ends the session. 204.
";

const GOOGLE_GUARD: &str = "Every other route but `/healthz` needs the session cookie `som_@PACKAGE@`; a missing or expired
session answers 401. A write also needs `X-CSRF-Token` with the `csrf` of `/auth/me` (403 `CSRF
token required` otherwise) and the `editor` role (403 otherwise); a subject outside
`GOOGLE_EDITOR_SUBJECTS` is a `reader`. Sessions live in Redis under
`<REDIS_NAMESPACE>:@PACKAGE@:session:` for the ID token's lifetime, at most an hour. While Redis is
unreachable the auth routes answer 503 `session store unavailable`.
";

const GOOGLE_SETTINGS: &str =
    "- `GOOGLE_CLIENT_ID` (required) and `GOOGLE_CLIENT_SECRET` (required): the OAuth client.
- `GOOGLE_REDIRECT_URI` (required): the callback URL, HTTPS outside tests.
- `GOOGLE_EDITOR_SUBJECTS` (required): a JSON list of the `sub` values with the `editor` role.
- `REDIS_URL` (required): a `redis://` or `unix://` URL of the session store.
- `REDIS_NAMESPACE` (required): the prefix of the session keys.
- `SOM_TEST_OIDC_ISSUER`: a local OpenID provider in place of Google. Only `APP_ENV=test` with a
  loopback host may set it.
- `APP_ENV`: `test` for a local test run, whose cookies are not `Secure`.
";

const URL_TAIL: &str =
    "    let rest = rest.split_once('#').map_or(rest.as_str(), |(before, _)| before);
    let (path, query) = rest.split_once('?').unwrap_or((rest, \"\"));
    Ok(Url {
        scheme,
        netloc,
        path: path.to_owned(),
        query: query.to_owned(),
    })
";

const EDITORS_NOT_LIST: &str = "GOOGLE_EDITOR_SUBJECTS must be a JSON list of strings";
const EDITORS_EMPTY: &str = "GOOGLE_EDITOR_SUBJECTS must contain nonempty strings";
const EDITORS_V5: &str = "GOOGLE_EDITOR_SUBJECTS must be a JSON list of subject strings";

const TEST_IDENTITY: &str = r#"/// TEST_AUTH_HEADERS and TEST_AUTH_COOKIES are the test client's identity: with an authorization
/// header or a session cookie the caller is known, without them it is refused.
#[tokio::test]
async fn test_client_headers_identify_the_caller() {
    let app = app();
    let read = |name: &str| -> serde_json::Map<String, Value> {
        serde_json::from_str(&std::env::var(name).unwrap_or_else(|_| "{}".into()))
            .expect("a JSON object")
    };
    let headers = read("TEST_AUTH_HEADERS");
    let cookies = read("TEST_AUTH_COOKIES");
    let mut request = Request::builder().method("GET").uri("/auth/me");
    for (name, value) in &headers {
        request = request.header(name.as_str(), value.as_str().expect("header text"));
    }
    if !cookies.is_empty() {
        let pairs: Vec<String> = cookies
            .iter()
            .map(|(name, value)| format!("{name}={}", value.as_str().expect("cookie text")))
            .collect();
        request = request.header("cookie", pairs.join("; "));
    }
    let known = headers.keys().any(|name| name.eq_ignore_ascii_case("authorization"))
        || !cookies.is_empty();
    let expected = if known { StatusCode::OK } else { StatusCode::UNAUTHORIZED };
    let request = request.body(Body::empty()).expect("request");
    assert_eq!(status(&app, request).await, expected);
}
"#;

// ---------------------------------------------------------------- generated code

const FIREBASE_IMPORTS: &str = r##"use std::str::FromStr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::body::Bytes;
use axum::extract::FromRequestParts;
use axum::http::{HeaderMap, Method, Request, StatusCode, header::AUTHORIZATION, request::Parts};
use base64::Engine;
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use http_body_util::{BodyExt, Full};
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::client::legacy::{Client, connect::HttpConnector};
use hyper_util::rt::TokioExecutor;
use serde::Serialize;
use serde_json::{Map, Value, json};
use sqlx::postgres::PgConnectOptions;

use crate::api::{ApiError, AppState};

"##;

const GOOGLE_IMPORTS: &str = r##"use std::str::FromStr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::body::{Body, Bytes};
use axum::extract::{FromRequestParts, Query, State, rejection::QueryRejection};
use axum::http::{
    HeaderMap, Method, Request, StatusCode,
    header::{CONTENT_TYPE, COOKIE, LOCATION, SET_COOKIE},
    request::Parts,
};
use axum::response::Response;
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use http_body_util::{BodyExt, Full};
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::client::legacy::{Client, connect::HttpConnector};
use hyper_util::rt::TokioExecutor;
use serde::Serialize;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::postgres::PgConnectOptions;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

use crate::api::{ApiError, AppState};

"##;

/// APP_ENV, local_override and the urlsplit they read.
const LOCAL: &str = r##"/// APP_ENV=test marks a local test run.
fn test_mode() -> bool {
    std::env::var_os("APP_ENV").is_some_and(|value| value == "test")
}

/// local_override(NAME): a set value must name a loopback service without credentials, and only in
/// a test run. An empty value is unset.
fn local_override(name: &str, test_mode: bool) -> Result<Option<String>, String> {
    let value = std::env::var_os(name)
        .map(|found| found.to_string_lossy().into_owned())
        .unwrap_or_default();
    if value.is_empty() {
        return Ok(None);
    }
    let url = url_split(&if value.contains("://") {
        value.clone()
    } else {
        format!("http://{value}")
    })?;
    let local = url
        .hostname()
        .is_some_and(|host| ["127.0.0.1", "localhost", "::1"].contains(&host.as_str()));
    if !test_mode || !local || url.credentials() || !["http", "https"].contains(&url.scheme.as_str())
    {
        return Err(format!("{name} is allowed only for local test services"));
    }
    Ok(Some(value))
}

/// The parts of a URL that urllib.parse.urlsplit (Python 3.12) reads.
struct Url {
    scheme: String,
    netloc: String,
    path: String,
    query: String,
}

fn url_split(text: &str) -> Result<Url, String> {
    let mut rest: String = text
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\r' | '\n'))
        .collect();
    let mut scheme = String::new();
    if let Some(colon) = rest.find(':') {
        let head = &rest[..colon];
        if head.starts_with(|c: char| c.is_ascii_alphabetic())
            && head.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'))
        {
            scheme = head.to_ascii_lowercase();
            rest = rest[colon + 1..].to_owned();
        }
    }
    let mut netloc = String::new();
    if let Some(after) = rest.strip_prefix("//") {
        let end = after.find(['/', '?', '#']).unwrap_or(after.len());
        netloc = after[..end].to_owned();
        rest = after[end..].to_owned();
        if netloc.contains('[') != netloc.contains(']') {
            return Err("Invalid IPv6 URL".to_owned());
        }
        if netloc.contains('[') {
            bracketed_host(&netloc)?;
        }
    }
    let rest = rest.split_once('#').map_or(rest.as_str(), |(before, _)| before);
    let (path, query) = rest.split_once('?').unwrap_or((rest, ""));
    Ok(Url {
        scheme,
        netloc,
        path: path.to_owned(),
        query: query.to_owned(),
    })
}

/// urlsplit's check of a bracketed host: an IPv6 address or an IPvFuture.
fn bracketed_host(netloc: &str) -> Result<(), String> {
    let invalid = || "Invalid IPv6 URL".to_owned();
    let host_port = netloc.rsplit_once('@').map_or(netloc, |(_, after)| after);
    let host = match host_port.split_once('[') {
        Some((before, inside)) => {
            let (host, port) = inside.split_once(']').unwrap_or((inside, ""));
            if !before.is_empty() || (!port.is_empty() && !port.starts_with(':')) {
                return Err(invalid());
            }
            host
        }
        None => host_port.split_once(']').map_or(host_port, |(host, _)| host),
    };
    if let Some(future) = host.strip_prefix('v') {
        let valid = future.split_once('.').is_some_and(|(hex, after)| {
            !hex.is_empty() && hex.chars().all(|c| c.is_ascii_hexdigit()) && !after.is_empty()
        });
        return if valid {
            Ok(())
        } else {
            Err("IPvFuture address is invalid".to_owned())
        };
    }
    if host.parse::<std::net::Ipv4Addr>().is_ok() {
        return Err("An IPv4 address cannot be in brackets".to_owned());
    }
    let address = host.split_once('%').map_or(host, |(address, _)| address);
    if address.parse::<std::net::Ipv6Addr>().is_err() {
        return Err(format!("'{host}' does not appear to be an IPv4 or IPv6 address"));
    }
    Ok(())
}

impl Url {
    /// The host and the port text after the userinfo.
    fn host_port(&self) -> (&str, &str) {
        let info = self.netloc.rsplit_once('@').map_or(self.netloc.as_str(), |(_, after)| after);
        match info.split_once('[') {
            Some((_, inside)) => {
                let (host, after) = inside.split_once(']').unwrap_or((inside, ""));
                (host, after.split_once(':').map_or("", |(_, port)| port))
            }
            None => info.split_once(':').unwrap_or((info, "")),
        }
    }

    /// The host in lower case, but for its zone.
    fn hostname(&self) -> Option<String> {
        let (host, _) = self.host_port();
        if host.is_empty() {
            return None;
        }
        Some(match host.split_once('%') {
            Some((address, zone)) => format!("{}%{zone}", address.to_lowercase()),
            None => host.to_lowercase(),
        })
    }

    /// A user name or a password.
    fn credentials(&self) -> bool {
        self.netloc
            .rsplit_once('@')
            .is_some_and(|(info, _)| !info.replacen(':', "", 1).is_empty())
    }
}

"##;

const FIREBASE: &str = r##"const FIREBASE_AUDIENCE: &str =
    "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit";
const FIREBASE_KEYS: &str =
    "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com";
const FIREBASE_UNAVAILABLE: &str = "authentication provider unavailable";

/// Python truth of a JSON value.
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|float| float != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Firebase ID tokens, checked as firebase_admin.auth.verify_id_token(check_revoked=True) checks
/// them, then the time claims. FIREBASE_AUTH_EMULATOR_HOST selects the Auth emulator, whose tokens
/// are not signed.
pub struct Firebase {
    client: HttpClient,
    project: String,
    emulator: Option<String>,
    keys: Keys,
    token: Mutex<Option<(String, Instant)>>,
}

impl Firebase {
    fn load() -> Result<Self, String> {
        let project = setting("FIREBASE_PROJECT_ID")?;
        let emulator = local_override("FIREBASE_AUTH_EMULATOR_HOST", test_mode())?;
        Ok(Self {
            client: http_client(),
            project,
            emulator,
            keys: Keys::new(FIREBASE_KEYS.to_owned(), FIREBASE_UNAVAILABLE),
            token: Mutex::new(None),
        })
    }

    /// The caller of a request: 401 for a refused token, 503 when the provider is unreachable.
    pub async fn authenticate(&self, headers: &HeaderMap) -> Result<Identity, ApiError> {
        let token = bearer(headers).ok_or(ApiError::Unauthorized)?;
        let claims = self.verify(&token).await?;
        let now = now();
        let finite = |name: &str| match claims.get(name) {
            Some(Value::Number(number)) => number.as_f64().filter(|time| time.is_finite()),
            _ => None,
        };
        let (Some(exp), Some(iat), Some(auth_time)) = (finite("exp"), finite("iat"), finite("auth_time"))
        else {
            return Err(ApiError::Unauthorized);
        };
        if exp <= now || iat > now || auth_time > now {
            return Err(ApiError::Unauthorized);
        }
        let role = claims
            .get("role")
            .cloned()
            .unwrap_or_else(|| Value::from("reader"));
        match (claims.get("sub"), role) {
            (Some(Value::String(sub)), Value::String(role)) if !sub.is_empty() => Ok(Identity {
                sub: sub.clone(),
                role,
            }),
            _ => Err(ApiError::Unauthorized),
        }
    }

    /// The token checks of firebase_admin, then the account: not disabled, and the token issued
    /// after the account's validSince.
    async fn verify(&self, token: &str) -> Result<Map<String, Value>, ApiError> {
        let emulated = self.emulator.is_some();
        if self.emulator.as_deref().is_some_and(|host| host.contains("//")) {
            return Err(ApiError::Unauthorized);
        }
        let Jws {
            input,
            header,
            claims,
            signature,
        } = jws(token).ok_or(ApiError::Unauthorized)?;
        let issuer = format!("https://securetoken.google.com/{}", self.project);
        let subject = claims
            .get("sub")
            .and_then(Value::as_str)
            .filter(|sub| !sub.is_empty() && sub.chars().count() <= 128)
            .map(str::to_owned);
        if claims.get("aud").is_some_and(|aud| aud == FIREBASE_AUDIENCE)
            || (!emulated && !header.get("kid").is_some_and(truthy))
            || (!emulated && header.get("alg").and_then(Value::as_str) != Some("RS256"))
            || claims.get("aud").and_then(Value::as_str) != Some(self.project.as_str())
            || claims.get("iss").and_then(Value::as_str) != Some(issuer.as_str())
        {
            return Err(ApiError::Unauthorized);
        }
        let uid = subject.ok_or(ApiError::Unauthorized)?;
        if !emulated {
            let kid = header.get("kid").and_then(Value::as_str);
            let key = self.keys.find(&self.client, kid).await?;
            if !verified(&key, &input, &signature) {
                return Err(ApiError::Unauthorized);
            }
            let now = now().trunc();
            let time = |name: &str| match claims.get(name) {
                Some(Value::Number(number)) => number.as_f64(),
                _ => None,
            };
            match (time("iat"), time("exp")) {
                (Some(iat), Some(exp)) if iat <= now && exp >= now => {}
                _ => return Err(ApiError::Unauthorized),
            }
        }
        let user = self.lookup(&uid).await?;
        if user.get("disabled").is_some_and(truthy) {
            return Err(ApiError::Unauthorized);
        }
        let since = match user.get("validSince") {
            None | Some(Value::Null) => 0,
            Some(found) => python_int(found).ok_or(ApiError::Unauthorized)?,
        };
        let iat = match claims.get("iat") {
            Some(Value::Number(number)) => number.as_f64(),
            Some(Value::Bool(flag)) => Some(f64::from(u8::from(*flag))),
            _ => None,
        }
        .ok_or(ApiError::Unauthorized)?;
        if iat * 1000.0 < since as f64 * 1000.0 {
            return Err(ApiError::Unauthorized);
        }
        Ok(claims)
    }

    /// accounts:lookup of one user, through the emulator or with the application default
    /// credentials.
    async fn lookup(&self, uid: &str) -> Result<Map<String, Value>, ApiError> {
        let unavailable = || ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, FIREBASE_UNAVAILABLE);
        let path = format!("identitytoolkit.googleapis.com/v1/projects/{}/accounts:lookup", self.project);
        let (url, authorization) = match &self.emulator {
            Some(host) => (format!("http://{host}/{path}"), "Bearer owner".to_owned()),
            None => {
                let token = self.access_token().await.ok_or_else(unavailable)?;
                (format!("https://{path}"), format!("Bearer {token}"))
            }
        };
        let body = serde_json::to_vec(&json!({"localId": [uid]})).map_err(|_| ApiError::Internal)?;
        let headers = [
            ("authorization", authorization.as_str()),
            ("content-type", "application/json"),
        ];
        let request = outbound(Method::POST, &url, &headers, body).ok_or_else(unavailable)?;
        let reply = send(&self.client, request).await.ok_or_else(unavailable)?;
        if reply.status >= 400 {
            let message = python_json(&reply.body)
                .and_then(|(body, _)| body.pointer("/error/message").and_then(Value::as_str).map(str::to_owned))
                .unwrap_or_default();
            let code = message.split(':').next().unwrap_or_default();
            return Err(if ["USER_NOT_FOUND", "USER_DISABLED", "INVALID_ID_TOKEN"].contains(&code) {
                ApiError::Unauthorized
            } else {
                unavailable()
            });
        }
        let Some((Value::Object(body), _)) = python_json(&reply.body) else {
            return Err(unavailable());
        };
        match body.get("users").and_then(Value::as_array).and_then(|users| users.first()) {
            Some(Value::Object(user)) if user.get("localId").is_some_and(truthy) => Ok(user.clone()),
            _ => Err(ApiError::Unauthorized),
        }
    }
}

"##;

/// The application default credentials of the production account lookup.
pub(super) const ADC: &str = r##"const SCOPES: [&str; 6] = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/datastore",
    "https://www.googleapis.com/auth/devstorage.read_write",
    "https://www.googleapis.com/auth/firebase",
    "https://www.googleapis.com/auth/identitytoolkit",
    "https://www.googleapis.com/auth/userinfo.email",
];

impl Firebase {
    /// An access token of the application default credentials, kept until a minute before it
    /// expires.
    async fn access_token(&self) -> Option<String> {
        let cached = self.token.lock().ok()?.clone();
        if let Some((token, _)) = cached.filter(|(_, until)| Instant::now() < *until) {
            return Some(token);
        }
        let (token, lifetime) = self.fetch_token().await?;
        let until = Instant::now() + Duration::from_secs(lifetime.saturating_sub(60));
        *self.token.lock().ok()? = Some((token.clone(), until));
        Some(token)
    }

    /// google.auth.default(): GOOGLE_APPLICATION_CREDENTIALS, the gcloud credentials file, then the
    /// metadata server.
    async fn fetch_token(&self) -> Option<(String, u64)> {
        let configured = std::env::var_os("GOOGLE_APPLICATION_CREDENTIALS")
            .filter(|path| !path.is_empty())
            .map(std::path::PathBuf::from);
        let gcloud = std::env::var_os("HOME")
            .map(|home| {
                std::path::Path::new(&home).join(".config/gcloud/application_default_credentials.json")
            })
            .filter(|path| path.exists());
        let request = match configured.or(gcloud) {
            Some(path) => {
                let Value::Object(info) = serde_json::from_slice(&std::fs::read(path).ok()?).ok()? else {
                    return None;
                };
                let text = |name: &str| info.get(name).and_then(Value::as_str).unwrap_or_default();
                let (url, body) = match text("type") {
                    "service_account" => {
                        let url = Some(text("token_uri"))
                            .filter(|url| !url.is_empty())
                            .unwrap_or("https://oauth2.googleapis.com/token");
                        let assertion = assertion(&info, url)?;
                        let grant = "urn:ietf:params:oauth:grant-type:jwt-bearer";
                        (url, form(&[("grant_type", grant), ("assertion", &assertion)]))
                    }
                    "authorized_user" => (
                        "https://oauth2.googleapis.com/token",
                        form(&[
                            ("grant_type", "refresh_token"),
                            ("client_id", text("client_id")),
                            ("client_secret", text("client_secret")),
                            ("refresh_token", text("refresh_token")),
                        ]),
                    ),
                    _ => return None,
                };
                let headers = [("content-type", "application/x-www-form-urlencoded")];
                outbound(Method::POST, url, &headers, body.into_bytes())?
            }
            None => {
                let host = std::env::var("GCE_METADATA_HOST")
                    .ok()
                    .filter(|host| !host.is_empty())
                    .unwrap_or_else(|| "metadata.google.internal".to_owned());
                let url = format!(
                    "http://{host}/computeMetadata/v1/instance/service-accounts/default/token?scopes={}",
                    quote_plus(&SCOPES.join(","))
                );
                outbound(Method::GET, &url, &[("metadata-flavor", "Google")], Vec::new())?
            }
        };
        let reply = send(&self.client, request).await?;
        if reply.status != 200 {
            return None;
        }
        let (Value::Object(body), _) = python_json(&reply.body)? else {
            return None;
        };
        let token = body.get("access_token")?.as_str()?.to_owned();
        let lifetime = body.get("expires_in").and_then(Value::as_u64).unwrap_or(3600);
        Some((token, lifetime))
    }
}

/// The signed JWT a service account trades for an access token.
fn assertion(info: &Map<String, Value>, audience: &str) -> Option<String> {
    let text = |name: &str| info.get(name).and_then(Value::as_str);
    let issued = now().trunc() as i64;
    let header = json!({"alg": "RS256", "typ": "JWT", "kid": text("private_key_id")});
    let claims = json!({
        "iat": issued,
        "exp": issued + 3600,
        "iss": text("client_email")?,
        "aud": audience,
        "scope": SCOPES.join(" "),
    });
    let input = format!(
        "{}.{}",
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&header).ok()?),
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&claims).ok()?)
    );
    let (_, body) = pem_block(text("private_key")?)?;
    let compact: String = body.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    let key = ring::signature::RsaKeyPair::from_pkcs8(&STANDARD.decode(compact).ok()?).ok()?;
    let mut signature = vec![0; key.public().modulus_len()];
    key.sign(
        &ring::signature::RSA_PKCS1_SHA256,
        &ring::rand::SystemRandom::new(),
        input.as_bytes(),
        &mut signature,
    )
    .ok()?;
    Some(format!("{input}.{}", URL_SAFE_NO_PAD.encode(signature)))
}

"##;

const GOOGLE: &str = r##"const APPLICATION: &str = "@PACKAGE@";
const SESSION_COOKIE: &str = "som_@PACKAGE@";
const BINDING_COOKIE: &str = "som_@PACKAGE@_login";
const PROVIDER_UNAVAILABLE: &str = "identity provider unavailable";

/// Google OpenID Connect sign-in, as the Python product does it with authlib and PyJWT, and the
/// sessions it keeps in Redis. SOM_TEST_OIDC_ISSUER selects a local provider in tests.
pub struct Google {
    client: HttpClient,
    client_id: String,
    client_secret: String,
    redirect_uri: String,
    issuer: String,
    authorize: String,
    token: String,
    keys: Keys,
    editors: Vec<String>,
    test_mode: bool,
    redis: Redis,
    prefix: String,
}

impl Google {
    fn load() -> Result<Self, String> {
        let test_mode = test_mode();
        let client_id = setting("GOOGLE_CLIENT_ID")?;
        let client_secret = setting("GOOGLE_CLIENT_SECRET")?;
        let redirect_uri = setting("GOOGLE_REDIRECT_URI")?;
        let local = local_override("SOM_TEST_OIDC_ISSUER", test_mode)?;
        let (issuer, authorize, token, jwks) = match local {
            Some(base) => (
                base.clone(),
                format!("{base}/authorize"),
                format!("{base}/token"),
                format!("{base}/jwks"),
            ),
            None => (
                "https://accounts.google.com".to_owned(),
                "https://accounts.google.com/o/oauth2/v2/auth".to_owned(),
                "https://oauth2.googleapis.com/token".to_owned(),
                "https://www.googleapis.com/oauth2/v3/certs".to_owned(),
            ),
        };
        if !test_mode && url_split(&redirect_uri)?.scheme != "https" {
            return Err("GOOGLE_REDIRECT_URI must use HTTPS outside tests".to_owned());
        }
        let not_list = || "GOOGLE_EDITOR_SUBJECTS must be a JSON list of strings".to_owned();
        let editors = match serde_json::from_str(&setting("GOOGLE_EDITOR_SUBJECTS")?) {
            Ok(Value::Array(items)) => items
                .into_iter()
                .map(|item| match item {
                    Value::String(subject) => Ok(subject),
                    _ => Err(not_list()),
                })
                .collect::<Result<Vec<String>, String>>()?,
            _ => return Err(not_list()),
        };
        if editors.iter().any(String::is_empty) {
            return Err("GOOGLE_EDITOR_SUBJECTS must contain nonempty strings".to_owned());
        }
        let prefix = format!("{}:{APPLICATION}:", setting("REDIS_NAMESPACE")?);
        let redis = Redis::from_url(&setting("REDIS_URL")?)?;
        Ok(Self {
            client: http_client(),
            client_id,
            client_secret,
            redirect_uri,
            issuer,
            authorize,
            token,
            keys: Keys::new(jwks, PROVIDER_UNAVAILABLE),
            editors,
            test_mode,
            redis,
            prefix,
        })
    }

    /// The cookie attributes after Path and SameSite: Secure outside tests.
    fn secure(&self) -> &'static str {
        if self.test_mode { "" } else { "; Secure" }
    }

    /// GET /auth/google/login: a state for 300 seconds, then 302 to the provider.
    async fn login(&self) -> Result<Response, ApiError> {
        let [state, nonce, verifier, binding] =
            [token_urlsafe()?, token_urlsafe()?, token_urlsafe()?, token_urlsafe()?];
        let saved = python_dumps(&[("nonce", &nonce), ("verifier", &verifier), ("binding", &binding)]);
        self.redis.set(&format!("{}state:{state}", self.prefix), &saved, 300).await?;
        let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()));
        let query = form(&[
            ("response_type", "code"),
            ("client_id", &self.client_id),
            ("redirect_uri", &self.redirect_uri),
            ("scope", "openid email profile"),
            ("state", &state),
            ("nonce", &nonce),
            ("code_challenge", &challenge),
            ("code_challenge_method", "S256"),
        ]);
        let secure = self.secure();
        Response::builder()
            .status(StatusCode::FOUND)
            .header(LOCATION, format!("{}?{query}", self.authorize))
            .header(
                SET_COOKIE,
                format!("{BINDING_COOKIE}={binding}; HttpOnly; Max-Age=300; Path=/; SameSite=lax{secure}"),
            )
            .body(Body::empty())
            .map_err(|_| ApiError::Internal)
    }

    /// GET /auth/google/callback: the saved state, the code exchange and the ID token checks, then a
    /// session for the token's lifetime, at most an hour.
    async fn callback(&self, pairs: &[(String, String)], headers: &HeaderMap) -> Result<Response, ApiError> {
        let value = |name: &str| pairs.iter().rev().find(|(key, _)| key == name).map(|(_, found)| found);
        let (state, code) = (value("state"), value("code"));
        let (Some(state), Some(code)) = (state, code) else {
            let problems = [("state", state), ("code", code)]
                .into_iter()
                .filter(|(_, found)| found.is_none())
                .map(|(name, _)| json!({"type": "missing", "loc": ["query", name], "msg": "Field required"}))
                .collect();
            return Err(ApiError::Invalid(problems));
        };
        let refused = |detail| ApiError::Status(StatusCode::BAD_REQUEST, detail);
        if state.chars().count() > 128 || code.chars().count() > 4096 {
            return Err(refused("invalid OAuth callback"));
        }
        let saved = self
            .redis
            .read(&format!("{}state:{state}", self.prefix), true)
            .await?
            .filter(|saved| !saved.is_empty())
            .ok_or_else(|| refused("expired or reused OAuth state"))?;
        let login = match python_json(saved.as_bytes()) {
            Some((Value::Object(login), _)) => login,
            _ => return Err(refused("invalid OAuth state")),
        };
        let field = |name: &str| login.get(name).and_then(Value::as_str);
        let (Some(nonce), Some(verifier), Some(binding)) = (field("nonce"), field("verifier"), field("binding")) else {
            return Err(refused("invalid OAuth state"));
        };
        let cookie = cookies(headers).remove(BINDING_COOKIE).unwrap_or_default();
        if !same(binding, &cookie) {
            return Err(refused("OAuth browser binding mismatch"));
        }
        let encoded = self.exchange(code, verifier).await?;
        let claims = self.id_token(&encoded).await?;
        let (Some(Value::String(sub)), Some(Value::Number(exp)), Some(Value::String(claimed))) =
            (claims.get("sub"), claims.get("exp"), claims.get("nonce"))
        else {
            return Err(ApiError::Unauthorized);
        };
        let exp = exp.as_f64().filter(|exp| exp.is_finite()).ok_or(ApiError::Unauthorized)?;
        if sub.is_empty() || !same(claimed, nonce) {
            return Err(ApiError::Unauthorized);
        }
        let lifetime = (exp - now()).trunc().min(3600.0);
        if lifetime <= 0.0 {
            return Err(ApiError::Unauthorized);
        }
        let lifetime = lifetime as i64;
        let session = token_urlsafe()?;
        let role = if self.editors.contains(sub) { "editor" } else { "reader" };
        let identity = python_dumps(&[("sub", sub), ("role", role), ("csrf", &token_urlsafe()?)]);
        self.redis
            .set(&format!("{}session:{session}", self.prefix), &identity, lifetime)
            .await?;
        let secure = self.secure();
        Response::builder()
            .header(CONTENT_TYPE, "application/json")
            .header(
                SET_COOKIE,
                format!("{SESSION_COOKIE}={session}; HttpOnly; Max-Age={lifetime}; Path=/; SameSite=lax{secure}"),
            )
            .header(SET_COOKIE, expired(BINDING_COOKIE))
            .body(Body::from(r#"{"status":"ok"}"#))
            .map_err(|_| ApiError::Internal)
    }

    /// authlib's fetch_token with client_secret_post: the ID token of the code.
    async fn exchange(&self, code: &str, verifier: &str) -> Result<String, ApiError> {
        let mut pairs = vec![
            ("grant_type", "authorization_code"),
            ("redirect_uri", self.redirect_uri.as_str()),
        ];
        if !code.is_empty() {
            pairs.push(("code", code));
        }
        pairs.extend([
            ("code_verifier", verifier),
            ("client_id", self.client_id.as_str()),
            ("client_secret", self.client_secret.as_str()),
        ]);
        let headers = [
            ("accept", "application/json"),
            ("content-type", "application/x-www-form-urlencoded;charset=UTF-8"),
        ];
        let request = outbound(Method::POST, &self.token, &headers, form(&pairs).into_bytes())
            .ok_or(ApiError::Unauthorized)?;
        let unavailable = ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, PROVIDER_UNAVAILABLE);
        let Some(reply) = send(&self.client, request).await else {
            return Err(unavailable);
        };
        if reply.status >= 500 {
            return Err(unavailable);
        }
        match python_json(&reply.body) {
            Some((Value::Object(token), _)) if !token.contains_key("error") => match token.get("id_token") {
                Some(Value::String(encoded)) => Ok(encoded.clone()),
                _ => Err(ApiError::Unauthorized),
            },
            _ => Err(ApiError::Unauthorized),
        }
    }

    /// The claims of an RS256 ID token under the provider's keys, checked as PyJWT 2.15 checks
    /// them with sub, exp, iat, iss, aud and nonce required.
    async fn id_token(&self, encoded: &str) -> Result<Map<String, Value>, ApiError> {
        let Jws {
            input,
            header,
            claims,
            signature,
        } = jws(encoded).ok_or(ApiError::Unauthorized)?;
        if let Some(kid) = header.get("kid") {
            kid.as_str().ok_or(ApiError::Unauthorized)?;
        }
        if header.contains_key("crit") {
            let Some(Value::Array(crit)) = header.get("crit") else {
                return Err(ApiError::Unauthorized);
            };
            if crit.is_empty()
                || !crit
                    .iter()
                    .all(|ext| ext.as_str() == Some("b64") && header.contains_key("b64"))
            {
                return Err(ApiError::Unauthorized);
            }
        }
        if header.get("b64") == Some(&Value::Bool(false)) {
            return Err(ApiError::Unauthorized);
        }
        let key = self
            .keys
            .find(&self.client, header.get("kid").and_then(Value::as_str))
            .await?;
        if header.get("alg").and_then(Value::as_str) != Some("RS256")
            || !verified(&key, &input, &signature)
            || !self.check_claims(&claims)
        {
            return Err(ApiError::Unauthorized);
        }
        Ok(claims)
    }

    fn check_claims(&self, claims: &Map<String, Value>) -> bool {
        let required = ["sub", "exp", "iat", "iss", "aud", "nonce"];
        if required
            .iter()
            .any(|name| claims.get(*name).is_none_or(Value::is_null))
        {
            return false;
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |elapsed| i128::from(elapsed.as_secs()));
        let time = |name: &str| claims.get(name).map(python_int);
        if matches!(time("iat"), Some(None)) || time("iat").flatten().is_some_and(|iat| iat > now) {
            return false;
        }
        if matches!(time("nbf"), Some(None)) || time("nbf").flatten().is_some_and(|nbf| nbf > now) {
            return false;
        }
        if matches!(time("exp"), Some(None)) || time("exp").flatten().is_some_and(|exp| exp <= now)
        {
            return false;
        }
        if claims.get("iss").and_then(Value::as_str) != Some(self.issuer.as_str()) {
            return false;
        }
        let audience = match &claims["aud"] {
            Value::String(one) if !one.is_empty() => vec![one.as_str()],
            Value::Array(many) if !many.is_empty() => {
                let Some(names) = many.iter().map(Value::as_str).collect::<Option<Vec<_>>>() else {
                    return false;
                };
                names
            }
            _ => return false,
        };
        if !audience.contains(&self.client_id.as_str()) {
            return false;
        }
        ["sub", "jti"]
            .iter()
            .all(|name| claims.get(*name).is_none_or(Value::is_string))
    }

    /// The caller of a session cookie. A write also needs the session's X-CSRF-Token.
    pub async fn authenticate(&self, method: &Method, headers: &HeaderMap) -> Result<Identity, ApiError> {
        let session = cookies(headers).remove(SESSION_COOKIE).unwrap_or_default();
        if session.is_empty() || session.chars().count() > 128 {
            return Err(ApiError::Unauthorized);
        }
        let saved = self
            .redis
            .read(&format!("{}session:{session}", self.prefix), false)
            .await?
            .filter(|saved| !saved.is_empty())
            .ok_or(ApiError::Unauthorized)?;
        let Some((Value::Object(stored), _)) = python_json(saved.as_bytes()) else {
            return Err(ApiError::Unauthorized);
        };
        let (Some(Value::String(sub)), Some(Value::String(role)), Some(Value::String(csrf))) =
            (stored.get("sub"), stored.get("role"), stored.get("csrf"))
        else {
            return Err(ApiError::Unauthorized);
        };
        if sub.is_empty() || csrf.is_empty() {
            return Err(ApiError::Unauthorized);
        }
        let reads = [Method::GET, Method::HEAD, Method::OPTIONS];
        if !reads.contains(method) && !same(csrf, &latin1(headers, "x-csrf-token")) {
            return Err(ApiError::Status(StatusCode::FORBIDDEN, "CSRF token required"));
        }
        Ok(Identity {
            sub: sub.clone(),
            role: role.clone(),
            csrf: csrf.clone(),
        })
    }

    /// POST /auth/logout: the caller's session ends.
    async fn logout(&self, method: &Method, headers: &HeaderMap) -> Result<Response, ApiError> {
        self.authenticate(method, headers).await?;
        let session = cookies(headers).remove(SESSION_COOKIE).unwrap_or_default();
        self.redis.delete(&format!("{}session:{session}", self.prefix)).await?;
        Response::builder()
            .status(StatusCode::NO_CONTENT)
            .header(SET_COOKIE, expired(SESSION_COOKIE))
            .body(Body::empty())
            .map_err(|_| ApiError::Internal)
    }
}

pub async fn google_login(State(state): State<AppState>) -> Result<Response, ApiError> {
    state.auth.login().await
}

pub async fn google_callback(
    State(state): State<AppState>,
    query: Result<Query<Vec<(String, String)>>, QueryRejection>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let pairs = query.map(|Query(pairs)| pairs).unwrap_or_default();
    state.auth.callback(&pairs, &headers).await
}

pub async fn logout(
    State(state): State<AppState>,
    method: Method,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    state.auth.logout(&method, &headers).await
}

/// Starlette's delete_cookie: an empty value that expires now.
fn expired(name: &str) -> String {
    let now = chrono::Utc::now().format("%a, %d %b %Y %H:%M:%S GMT");
    format!("{name}=\"\"; expires={now}; Max-Age=0; Path=/; SameSite=lax")
}

/// A header as Starlette reads it: the first one, as latin-1.
fn latin1(headers: &HeaderMap, name: &str) -> String {
    headers
        .get(name)
        .map(|value| value.as_bytes().iter().map(|&byte| char::from(byte)).collect())
        .unwrap_or_default()
}

/// Starlette's cookie_parser of the Cookie header: the last value of a name wins.
fn cookies(headers: &HeaderMap) -> std::collections::HashMap<String, String> {
    let mut found = std::collections::HashMap::new();
    for chunk in latin1(headers, COOKIE.as_str()).split(';') {
        let (key, value) = chunk.split_once('=').unwrap_or(("", chunk));
        let (key, value) = (key.trim(), value.trim());
        if !key.is_empty() || !value.is_empty() {
            found.insert(key.to_owned(), cookie_value(value));
        }
    }
    found
}

/// http.cookies._unquote: a quoted value loses its quotes and its backslash escapes.
fn cookie_value(value: &str) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() < 2 || chars[0] != '"' || chars[chars.len() - 1] != '"' {
        return value.to_owned();
    }
    let inner = &chars[1..chars.len() - 1];
    let mut out = String::new();
    let mut index = 0;
    while index < inner.len() {
        if inner[index] == '\\' && index + 1 < inner.len() {
            let octal = inner.get(index + 1..index + 4).filter(|digits| {
                matches!(digits[0], '0'..='3') && digits[1..].iter().all(|c| matches!(c, '0'..='7'))
            });
            if let Some(digits) = octal {
                let code = digits
                    .iter()
                    .fold(0u8, |total, digit| total * 8 + digit.to_digit(8).map_or(0, |d| d as u8));
                out.push(char::from(code));
                index += 4;
            } else {
                out.push(inner[index + 1]);
                index += 2;
            }
            continue;
        }
        out.push(inner[index]);
        index += 1;
    }
    out
}

/// secrets.token_urlsafe(32).
fn token_urlsafe() -> Result<String, ApiError> {
    let mut bytes = [0u8; 32];
    ring::rand::SecureRandom::fill(&ring::rand::SystemRandom::new(), &mut bytes)
        .map_err(|_| ApiError::Internal)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

/// hmac.compare_digest: equal bytes, in a time that does not depend on where they differ.
fn same(left: &str, right: &str) -> bool {
    left.len() == right.len()
        && left
            .bytes()
            .zip(right.bytes())
            .fold(0u8, |differ, (one, other)| differ | (one ^ other))
            == 0
}

/// json.dumps of string pairs: ", " and ": " separators, and ASCII only.
fn python_dumps(pairs: &[(&str, &str)]) -> String {
    let quote = |text: &str| {
        let mut out = String::from("\"");
        for c in text.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                '\r' => out.push_str("\\r"),
                '\t' => out.push_str("\\t"),
                '\u{8}' => out.push_str("\\b"),
                '\u{c}' => out.push_str("\\f"),
                ' '..='~' => out.push(c),
                _ => {
                    let mut units = [0u16; 2];
                    for unit in c.encode_utf16(&mut units) {
                        out.push_str(&format!("\\u{unit:04x}"));
                    }
                }
            }
        }
        out + "\""
    };
    let items: Vec<String> = pairs
        .iter()
        .map(|(name, value)| format!("{}: {}", quote(name), quote(value)))
        .collect();
    format!("{{{}}}", items.join(", "))
}

"##;

/// The Redis client of the Google sessions, in RESP2.
const REDIS: &str = r##"const STORE_UNAVAILABLE: &str = "session store unavailable";

enum RedisAddress {
    Tcp(String, u16),
    Unix(String),
}

/// A Redis server as redis-py reads REDIS_URL, with a fresh connection for each command and the
/// 2 second connect and socket timeouts of the Python product.
struct Redis {
    address: RedisAddress,
    username: Option<String>,
    password: Option<String>,
    db: i128,
}

impl Redis {
    fn from_url(url: &str) -> Result<Self, String> {
        if !["redis://", "rediss://", "unix://"].iter().any(|scheme| url.starts_with(scheme)) {
            return Err(
                "Redis URL must specify one of the following schemes (redis://, rediss://, unix://)"
                    .to_owned(),
            );
        }
        if url.starts_with("rediss://") {
            return Err("REDIS_URL with TLS (rediss://) is not supported by this product".to_owned());
        }
        let parts = url_split(url)?;
        let mut db = None;
        for pair in parts.query.split('&') {
            let Some((name, value)) = pair.split_once('=') else {
                continue;
            };
            if value.is_empty() || db.is_some() || unquote(&name.replace('+', " ")) != "db" {
                continue;
            }
            let value = unquote(&unquote(&value.replace('+', " ")));
            let invalid = || "Invalid value for 'db' in connection URL.".to_owned();
            db = Some(python_int(&Value::String(value)).ok_or_else(invalid)?);
        }
        let userinfo = parts.netloc.rsplit_once('@').map(|(info, _)| info);
        let (user, pass) = userinfo.map_or((None, None), |info| match info.split_once(':') {
            Some((user, pass)) => (Some(user), Some(pass)),
            None => (Some(info), None),
        });
        let username = user.filter(|user| !user.is_empty()).map(unquote);
        let password = pass.filter(|pass| !pass.is_empty()).map(unquote);
        let address = if parts.scheme == "unix" {
            RedisAddress::Unix(unquote(&parts.path))
        } else {
            let host = parts
                .hostname()
                .map_or_else(|| "localhost".to_owned(), |host| unquote(&host));
            let (_, port) = parts.host_port();
            let port = if port.is_empty() {
                6379
            } else if port.bytes().all(|byte| byte.is_ascii_digit()) {
                match port.parse::<u32>() {
                    Ok(0) => 6379,
                    Ok(port) => u16::try_from(port).map_err(|_| "Port out of range 0-65535".to_owned())?,
                    Err(_) => return Err("Port out of range 0-65535".to_owned()),
                }
            } else {
                return Err(format!("Port could not be cast to integer value as '{port}'"));
            };
            if db.is_none() && !parts.path.is_empty() {
                db = python_int(&Value::String(unquote(&parts.path).replace('/', "")));
            }
            RedisAddress::Tcp(host, port)
        };
        Ok(Self {
            address,
            username,
            password,
            db: db.unwrap_or(0),
        })
    }

    /// SET key value EX seconds NX.
    async fn set(&self, key: &str, value: &str, seconds: i64) -> Result<(), ApiError> {
        self.call(&["SET", key, value, "EX", &seconds.to_string(), "NX"]).await?;
        Ok(())
    }

    /// GET, or GETDEL to consume the value.
    async fn read(&self, key: &str, consume: bool) -> Result<Option<String>, ApiError> {
        let command = if consume { "GETDEL" } else { "GET" };
        match self.call(&[command, key]).await? {
            None => Ok(None),
            Some(bytes) => String::from_utf8(bytes)
                .map(Some)
                .map_err(|_| ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, "invalid session store response")),
        }
    }

    /// DEL key.
    async fn delete(&self, key: &str) -> Result<(), ApiError> {
        self.call(&["DEL", key]).await?;
        Ok(())
    }

    /// One command on a new connection: AUTH and SELECT first when the URL has them.
    async fn call(&self, command: &[&str]) -> Result<Option<Vec<u8>>, ApiError> {
        let reply = match &self.address {
            RedisAddress::Tcp(host, port) => {
                match within(tokio::net::TcpStream::connect((host.as_str(), *port))).await {
                    Some(stream) => self.session(stream, command).await,
                    None => None,
                }
            }
            RedisAddress::Unix(path) => match within(tokio::net::UnixStream::connect(path)).await {
                Some(stream) => self.session(stream, command).await,
                None => None,
            },
        };
        reply.ok_or(ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, STORE_UNAVAILABLE))
    }

    async fn session<S: AsyncRead + AsyncWrite + Unpin>(
        &self,
        stream: S,
        command: &[&str],
    ) -> Option<Option<Vec<u8>>> {
        let mut stream = BufReader::new(stream);
        if self.username.is_some() || self.password.is_some() {
            let password = self.password.as_deref().unwrap_or_default();
            let auth = match &self.username {
                Some(username) => vec!["AUTH", username.as_str(), password],
                None => vec!["AUTH", password],
            };
            if resp(&mut stream, &auth).await?.as_deref() != Some(b"OK".as_slice()) {
                return None;
            }
        }
        if self.db != 0 {
            let db = self.db.to_string();
            if resp(&mut stream, &["SELECT", &db]).await?.as_deref() != Some(b"OK".as_slice()) {
                return None;
            }
        }
        resp(&mut stream, command).await
    }
}

/// An I/O step within the 2 second socket timeout.
async fn within<T>(step: impl std::future::Future<Output = std::io::Result<T>>) -> Option<T> {
    tokio::time::timeout(Duration::from_secs(2), step).await.ok()?.ok()
}

/// One RESP2 command and its reply: a status, an integer or a bulk string, or None for nil. An
/// error reply, or any other, fails.
async fn resp<S: AsyncRead + AsyncWrite + Unpin>(
    stream: &mut BufReader<S>,
    command: &[&str],
) -> Option<Option<Vec<u8>>> {
    let mut request = format!("*{}\r\n", command.len()).into_bytes();
    for argument in command {
        request.extend_from_slice(format!("${}\r\n", argument.len()).as_bytes());
        request.extend_from_slice(argument.as_bytes());
        request.extend_from_slice(b"\r\n");
    }
    within(stream.write_all(&request)).await?;
    within(stream.flush()).await?;
    let mut line = Vec::new();
    within(stream.read_until(b'\n', &mut line)).await?;
    let text = line.strip_suffix(b"\r\n")?;
    let (&kind, rest) = text.split_first()?;
    match kind {
        b'+' | b':' => Some(Some(rest.to_vec())),
        b'$' => {
            let length: i64 = std::str::from_utf8(rest).ok()?.parse().ok()?;
            let Ok(length) = usize::try_from(length) else {
                return Some(None);
            };
            let mut bulk = vec![0; length + 2];
            within(stream.read_exact(&mut bulk)).await?;
            bulk.truncate(length);
            Some(Some(bulk))
        }
        _ => None,
    }
}

/// urllib.parse.unquote: %XX escapes as UTF-8, with replacement characters.
fn unquote(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        let escaped = bytes
            .get(index + 1..index + 3)
            .filter(|pair| bytes[index] == b'%' && pair.iter().all(u8::is_ascii_hexdigit))
            .and_then(|pair| u8::from_str_radix(std::str::from_utf8(pair).ok()?, 16).ok());
        match escaped {
            Some(byte) => {
                out.push(byte);
                index += 3;
            }
            None => {
                out.push(bytes[index]);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

"##;

/// The JWKS keys, cached as PyJWKClient caches them.
const KEYS: &str = r##"/// An RS256 key of a JWKS document.
#[derive(Clone)]
struct SigningKey {
    kid: String,
    modulus: Vec<u8>,
    exponent: Vec<u8>,
}

type KeyCache = Mutex<Option<(Instant, Vec<SigningKey>)>>;

/// The signing keys of a JWKS URL, kept for 300 seconds. A kid that is not there reloads them once.
struct Keys {
    url: String,
    unavailable: &'static str,
    cache: KeyCache,
}

impl Keys {
    fn new(url: String, unavailable: &'static str) -> Self {
        Self {
            url,
            unavailable,
            cache: Mutex::new(None),
        }
    }

    async fn find(&self, client: &HttpClient, kid: Option<&str>) -> Result<SigningKey, ApiError> {
        let cached = self.cache.lock().ok().and_then(|cache| {
            cache
                .as_ref()
                .filter(|(loaded, _)| loaded.elapsed() < Duration::from_secs(300))
                .map(|(_, keys)| keys.clone())
        });
        let keys = match cached {
            Some(keys) => keys,
            None => self.load(client).await?,
        };
        if let Some(found) = pick(&keys, kid) {
            return Ok(found);
        }
        let keys = self.load(client).await?;
        pick(&keys, kid).ok_or(ApiError::Unauthorized)
    }

    async fn load(&self, client: &HttpClient) -> Result<Vec<SigningKey>, ApiError> {
        let loaded = self.fetch(client).await;
        if let Ok(mut cache) = self.cache.lock() {
            *cache = loaded.as_ref().ok().map(|keys| (Instant::now(), keys.clone()));
        }
        loaded
    }

    /// An unreachable or refusing endpoint is unavailable; a document without RS256 signing keys
    /// is a refused token.
    async fn fetch(&self, client: &HttpClient) -> Result<Vec<SigningKey>, ApiError> {
        let unavailable = ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, self.unavailable);
        let Some(request) = outbound(Method::GET, &self.url, &[], Vec::new()) else {
            return Err(unavailable);
        };
        let Some(reply) = send(client, request).await else {
            return Err(unavailable);
        };
        if @REFUSED@ {
            return Err(unavailable);
        }
        let Some((Value::Object(set), _)) = python_json(&reply.body) else {
            return Err(ApiError::Unauthorized);
        };
        let keys: Vec<SigningKey> = match set.get("keys") {
            Some(Value::Array(keys)) => keys.iter().filter_map(signing_key).collect(),
            _ => Vec::new(),
        };
        if keys.is_empty() {
            Err(ApiError::Unauthorized)
        } else {
            Ok(keys)
        }
    }
}

/// An RSA key for signatures with a kid, or None.
fn signing_key(key: &Value) -> Option<SigningKey> {
    if key.get("kty")? != "RSA" {
        return None;
    }
    if !matches!(key.get("use"), None | Some(Value::Null)) && key.get("use")? != "sig" {
        return None;
    }
    let kid = key.get("kid")?.as_str().filter(|kid| !kid.is_empty())?;
    let trim = |bytes: Vec<u8>| {
        let first = bytes.iter().position(|byte| *byte != 0).unwrap_or(bytes.len());
        bytes[first..].to_vec()
    };
    Some(SigningKey {
        kid: kid.to_owned(),
        modulus: trim(segment(key.get("n")?.as_str()?)?),
        exponent: trim(segment(key.get("e")?.as_str()?)?),
    })
}

fn pick(keys: &[SigningKey], kid: Option<&str>) -> Option<SigningKey> {
    keys.iter().find(|key| Some(key.kid.as_str()) == kid).cloned()
}

/// An RS256 signature of at least 2048 bits.
fn verified(key: &SigningKey, input: &str, signature: &[u8]) -> bool {
    ring::signature::RsaPublicKeyComponents {
        n: &key.modulus,
        e: &key.exponent,
    }
    .verify(
        &ring::signature::RSA_PKCS1_2048_8192_SHA256,
        input.as_bytes(),
        signature,
    )
    .is_ok()
}

/// A compact JWS: its signing input, header and claims objects, and signature.
struct Jws {
    input: String,
    header: Map<String, Value>,
    claims: Map<String, Value>,
    signature: Vec<u8>,
}

fn jws(token: &str) -> Option<Jws> {
    let (input, signature) = token.rsplit_once('.')?;
    let (header, claims) = input.split_once('.')?;
    let Value::Object(header) = python_json(&segment(header)?)?.0 else {
        return None;
    };
    let Value::Object(claims) = python_json(&segment(claims)?)?.0 else {
        return None;
    };
    Some(Jws {
        input: input.to_owned(),
        header,
        claims,
        signature: segment(signature)?,
    })
}

/// time.time().
fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

"##;

/// The HTTP client of the provider calls.
pub(super) const HTTP: &str = r##"/// HTTPS with the webpki roots (rustls and ring), or plain HTTP to a local test service.
type HttpClient = Client<HttpsConnector<HttpConnector>, Full<Bytes>>;

fn http_client() -> HttpClient {
    let connector = HttpsConnectorBuilder::new()
        .with_webpki_roots()
        .https_or_http()
        .enable_http1()
        .build();
    Client::builder(TokioExecutor::new()).build(connector)
}

/// The status and body of a provider reply.
struct Reply {
    status: u16,
    body: Bytes,
}

/// One provider call within the 5 second timeout of the Python product, or None when it fails.
async fn send(client: &HttpClient, request: Request<Full<Bytes>>) -> Option<Reply> {
    let exchange = async {
        let response = client.request(request).await.ok()?;
        let status = response.status().as_u16();
        let body = response.into_body().collect().await.ok()?.to_bytes();
        Some(Reply { status, body })
    };
    tokio::time::timeout(Duration::from_secs(5), exchange)
        .await
        .ok()
        .flatten()
}

fn outbound(
    method: Method,
    url: &str,
    headers: &[(&str, &str)],
    body: Vec<u8>,
) -> Option<Request<Full<Bytes>>> {
    let mut request = Request::builder().method(method).uri(url);
    for (name, value) in headers {
        request = request.header(*name, *value);
    }
    request.body(Full::new(Bytes::from(body))).ok()
}

/// urllib.parse.urlencode of the pairs.
fn form(pairs: &[(&str, &str)]) -> String {
    let encoded: Vec<String> = pairs
        .iter()
        .map(|(name, value)| format!("{}={}", quote_plus(name), quote_plus(value)))
        .collect();
    encoded.join("&")
}

/// urllib.parse.quote_plus.
fn quote_plus(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(char::from(byte));
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

"##;
