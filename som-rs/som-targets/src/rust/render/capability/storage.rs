//! The object storage of a capability_postgresql product (issue #53 G). none keeps the products of
//! D, E and F byte for byte. s3 and gcs add PUT, GET and DELETE /objects/{key}: an object of 1 byte
//! to 1 MiB stored once under its key, as the Python product stores it with boto3 or
//! google-cloud-storage. s3 signs path-style requests with SigV4 by hand (sha2 and hmac); gcs calls
//! the JSON API with the application default credentials of the firebase provider. The calls use
//! the hyper client of the providers.
use serde_json::Value;

use super::auth::{self, Auth};

/// The object storage a prompt selects.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Storage {
    None,
    S3,
    Gcs,
}

impl Storage {
    /// The storage param. Without one there is no object storage.
    pub(super) fn read(params: &Value) -> Result<Self, String> {
        match &params["storage"] {
            Value::Null => Ok(Self::None),
            Value::String(name) => match name.as_str() {
                "none" => Ok(Self::None),
                "s3" => Ok(Self::S3),
                "gcs" => Ok(Self::Gcs),
                other => Err(format!("no capability storage {other}")),
            },
            _ => Err("param storage is not a string".to_owned()),
        }
    }

    /// The Rust type of the object store in the product.
    fn name(self) -> &'static str {
        match self {
            Self::None => "",
            Self::S3 => "S3",
            Self::Gcs => "Gcs",
        }
    }
}

/// src/main.rs: the state holds the object store too.
pub(super) fn main(storage: Storage, main: String) -> String {
    if storage == Storage::None {
        return main;
    }
    main.replace(
        "        auth: Arc::new(settings.auth),\n",
        "        auth: Arc::new(settings.auth),\n        storage: Arc::new(settings.storage),\n",
    )
}

/// src/integrations.rs: the object store, read after the auth settings as the Python product
/// reads it, and what it needs of the providers' code.
pub(super) fn integrations(storage: Storage, auth: Auth, text: String) -> String {
    if storage == Storage::None {
        return text;
    }
    let name = storage.name();
    let provider = auth.name();
    let (std_uses, uses) = match (auth, storage) {
        (Auth::Jwt, Storage::S3) => ("use std::time::Duration;\n", JWT_S3_USES),
        (Auth::Jwt, _) => (
            "use std::sync::Mutex;\nuse std::time::{Duration, Instant};\n",
            JWT_GCS_USES,
        ),
        (Auth::Firebase, Storage::S3) => {
            ("", "use hmac::{Hmac, Mac};\nuse sha2::{Digest, Sha256};\n")
        }
        (Auth::Firebase, _) => ("", "use sha2::{Digest, Sha256};\n"),
        (Auth::Google, Storage::S3) => ("", "use hmac::{Hmac, Mac};\n"),
        (Auth::Google, _) => ("", "use base64::engine::general_purpose::STANDARD;\n"),
    };
    let mut out = text
        .replacen(
            "use std::str::FromStr;\n",
            &format!("use std::str::FromStr;\n{std_uses}"),
            1,
        )
        .replacen(
            "use sqlx::postgres::PgConnectOptions;\n",
            &format!("{uses}use sqlx::postgres::PgConnectOptions;\n"),
            1,
        )
        .replace(
            &format!("    pub auth: {provider},\n}}"),
            &format!("    pub auth: {provider},\n    pub storage: {name},\n}}"),
        )
        .replace(
            &format!("            auth: {provider}::load()?,\n"),
            &format!(
                "            auth: {provider}::load()?,\n            storage: {name}::load()?,\n"
            ),
        );
    let mut code = String::new();
    if auth == Auth::Jwt {
        code += &auth::local();
    }
    code += OBJECTS;
    if storage == Storage::S3 {
        code += S3;
    } else {
        code += GCS;
        if auth == Auth::Firebase {
            // The account lookup and the object store share the application default credentials.
            // The fields first: Adc has a token field of its own.
            out = out
                .replace(
                    "    token: Mutex<Option<(String, Instant)>>,\n",
                    "    adc: Adc,\n",
                )
                .replace(
                    "            token: Mutex::new(None),\n",
                    "            adc: Adc::new(),\n",
                )
                .replace(
                    "self.access_token().await",
                    "self.adc.access_token(&self.client).await",
                )
                .replace(auth::ADC, &auth::adc());
        } else {
            code += &auth::adc();
        }
    }
    if auth == Auth::Jwt {
        code += if storage == Storage::S3 {
            // Only the application default credentials post a form.
            &auth::HTTP[..auth::HTTP.find("/// urllib.parse.urlencode").expect("form")]
        } else {
            auth::HTTP
        };
        if storage == Storage::Gcs {
            code += auth::now();
        }
    }
    if auth == Auth::Google && storage == Storage::Gcs {
        code += auth::pem_block();
    }
    let at = out
        .find("/// A caller with a valid token.")
        .expect("extractors");
    out.insert_str(at, &code);
    out
}

/// The api.rs import of the object store.
pub(super) fn api_imports(storage: Storage) -> String {
    if storage == Storage::None {
        return String::new();
    }
    format!(
        "use crate::integrations::{{OBJECT_MAX_BYTES, ObjectError, {}, sha256_hex}};\n",
        storage.name()
    )
}

/// The api.rs state holds the object store, whose errors are status errors.
pub(super) fn api_errors(storage: Storage, auth: Auth, errors: String) -> String {
    if storage == Storage::None {
        return errors;
    }
    let errors = errors
        .replace(
            "/// The pool and the ",
            "/// The pool, the object store and the ",
        )
        .replace(
            &format!("    pub auth: Arc<{}>,\n}}", auth.name()),
            &format!(
                "    pub auth: Arc<{}>,\n    pub storage: Arc<{}>,\n}}",
                auth.name(),
                storage.name()
            ),
        );
    if auth != Auth::Jwt {
        return errors;
    }
    errors
        .replace("    TooMany,\n", "    TooMany,\n    Status(StatusCode, &'static str),\n")
        .replace(
            "            Self::Internal =>",
            "            Self::Status(status, detail) => (status, json!({\"detail\": detail})),\n            Self::Internal =>",
        )
}

/// The api.rs handlers of /objects/{key}.
pub(super) fn api_handlers(storage: Storage) -> &'static str {
    if storage == Storage::None {
        ""
    } else {
        API_OBJECTS
    }
}

/// tests/api.rs: the state holds the object store too.
pub(super) fn tests(storage: Storage, tests: String) -> String {
    if storage == Storage::None {
        return tests;
    }
    tests.replace(
        "        auth: Arc::new(settings.auth),\n",
        "        auth: Arc::new(settings.auth),\n        storage: Arc::new(settings.storage),\n",
    )
}

/// README.md: the object routes, the storage settings and their credential sources.
pub(super) fn readme(storage: Storage, auth: Auth, readme: String) -> String {
    let (shape, settings, tests) = match storage {
        Storage::None => return readme,
        Storage::S3 => (
            "S3 object storage\n(`storage: s3`)",
            S3_SETTINGS,
            "The tests also read the S3 settings above but do not call the object store.",
        ),
        Storage::Gcs => (
            "Google Cloud Storage objects\n(`storage: gcs`)",
            GCS_SETTINGS,
            "The tests also read the GCS settings above but do not call the object store.",
        ),
    };
    let settings = if auth == Auth::Jwt {
        format!("{settings}- `APP_ENV`: `test` for a local test run.\n")
    } else {
        settings.to_owned()
    };
    let mut out = readme
        .replace("no object storage\n(`storage: none`)", shape)
        .replace(
            "- `SOM_APP_ADDR`: the listen address.",
            &format!("{settings}- `SOM_APP_ADDR`: the listen address."),
        )
        .replace("\n## Migration\n", &format!("\n{tests}\n\n## Migration\n"));
    let start = out.find("## Routes\n\n").expect("routes") + "## Routes\n\n".len();
    let end = start + out[start..].find("\n\n").expect("routes end") + 1;
    out.insert_str(end, OBJECT_ROUTES);
    out
}

const OBJECT_ROUTES: &str = "- `PUT /objects/{key}`: store the request body, 1 byte to 1 MiB, under `key` (1 to 80 of
  `A-Za-z0-9_-`, else 422). It answers `{\"key\", \"size\", \"sha256\"}`. The same body again answers
  the same; a different body answers 409 `object key already contains different data`. An empty
  body answers 422 `empty object` and a larger one 413 `object exceeds 1 MiB`.
- `GET /objects/{key}`: the object, `application/octet-stream` with its `X-Content-SHA256`. 404
  `object not found` if it does not exist.
- `DELETE /objects/{key}`: delete the object. 204, also when it does not exist.
- While the object store is unreachable or refuses a call, the object routes answer 503 `object
  storage unavailable; retry the request`.
";

const S3_SETTINGS: &str = "- `S3_BUCKET` (required): the bucket of the objects.
- `AWS_REGION`: the bucket's region, `us-east-1` by default. Requests are path-style to
  `https://s3.amazonaws.com` (`https://s3.<region>.amazonaws.com` outside `us-east-1`), signed
  with SigV4.
- `SOM_TEST_S3_ENDPOINT`: a local S3 endpoint URL in place of AWS, called with the access key
  `local-test`. Only `APP_ENV=test` with a loopback host may set it.
- AWS credentials are read at startup from two sources only: `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY` (with `AWS_SESSION_TOKEN`), else the `AWS_PROFILE` profile (`default`)
  of the shared credentials file `AWS_SHARED_CREDENTIALS_FILE` (`~/.aws/credentials`). The config
  file, SSO, web identity, process, container and instance metadata credentials are not read.
  Without credentials every object call answers 503.
";

const GCS_SETTINGS: &str = "- `GCS_BUCKET` (required): the bucket of the objects.
- `GCS_PROJECT` (required): the project of the bucket.
- `STORAGE_EMULATOR_HOST`: a local Cloud Storage emulator in place of Google, called without
  credentials. Only `APP_ENV=test` with a loopback host may set it.
- Outside the emulator the object calls use the application default credentials:
  `GOOGLE_APPLICATION_CREDENTIALS` (a service account or authorized user JSON file), the gcloud
  file `~/.config/gcloud/application_default_credentials.json`, then the metadata server
  (`GCE_METADATA_HOST`, `metadata.google.internal` by default). No other source is read. They are
  read at the first object call, and without them every object call answers 503.
";

// ---------------------------------------------------------------- generated code

const JWT_S3_USES: &str = "use axum::body::Bytes;
use axum::http::Request;
use hmac::{Hmac, Mac};
use http_body_util::{BodyExt, Full};
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::client::legacy::{Client, connect::HttpConnector};
use hyper_util::rt::TokioExecutor;
use sha2::{Digest, Sha256};
";

const JWT_GCS_USES: &str = "use axum::body::Bytes;
use axum::http::Request;
use http_body_util::{BodyExt, Full};
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::client::legacy::{Client, connect::HttpConnector};
use hyper_util::rt::TokioExecutor;
use serde_json::json;
use sha2::{Digest, Sha256};
";

const OBJECTS: &str = r##"/// The largest object of /objects/{key}: 1 MiB.
pub const OBJECT_MAX_BYTES: usize = 1_048_576;

/// Why an object call failed: no object under the key, an object already under it, or an object
/// store that is unreachable or refuses the call.
pub enum ObjectError {
    Missing,
    Exists,
    Unavailable,
}

/// hashlib.sha256(data).hexdigest().
pub fn sha256_hex(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(char::from(DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(DIGITS[usize::from(byte & 15)]));
    }
    out
}

/// urllib.parse.quote(text, safe=""): one path segment.
fn quote_segment(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(char::from(byte));
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

"##;

const S3: &str = r##"/// An access key, its secret and a session token.
struct AwsCredentials {
    access_key: String,
    secret_key: String,
    token: Option<String>,
}

/// The credentials of the environment, else of the shared credentials file. No other source is
/// read.
fn aws_credentials() -> Option<AwsCredentials> {
    let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
    if let (Some(access_key), Some(secret_key)) = (env("AWS_ACCESS_KEY_ID"), env("AWS_SECRET_ACCESS_KEY")) {
        let token = env("AWS_SESSION_TOKEN").or_else(|| env("AWS_SECURITY_TOKEN"));
        return Some(AwsCredentials {
            access_key,
            secret_key,
            token,
        });
    }
    let path = env("AWS_SHARED_CREDENTIALS_FILE")
        .map(std::path::PathBuf::from)
        .or_else(|| env("HOME").map(|home| std::path::Path::new(&home).join(".aws/credentials")))?;
    let profile = env("AWS_PROFILE").unwrap_or_else(|| "default".to_owned());
    let text = std::fs::read_to_string(path).ok()?;
    let mut section = String::new();
    let mut values = std::collections::HashMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(['#', ';']) {
            continue;
        }
        if let Some(name) = line.strip_prefix('[').and_then(|rest| rest.strip_suffix(']')) {
            section = name.trim().to_owned();
            continue;
        }
        if section != profile {
            continue;
        }
        if let Some((name, value)) = line.split_once('=') {
            values.insert(name.trim().to_owned(), value.trim().to_owned());
        }
    }
    let value = |name: &str| values.get(name).filter(|value| !value.is_empty()).cloned();
    Some(AwsCredentials {
        access_key: value("aws_access_key_id")?,
        secret_key: value("aws_secret_access_key")?,
        token: value("aws_session_token").or_else(|| value("aws_security_token")),
    })
}

fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC takes a key of any length");
    mac.update(data);
    mac.finalize().into_bytes().to_vec()
}

/// S3 objects, called path-style with SigV4 as boto3 calls them: one attempt, no retries.
/// SOM_TEST_S3_ENDPOINT selects a local S3 in tests, called with the access key local-test.
pub struct S3 {
    client: HttpClient,
    bucket: String,
    region: String,
    origin: String,
    host: String,
    prefix: String,
    credentials: Option<AwsCredentials>,
}

impl S3 {
    fn load() -> Result<Self, String> {
        let bucket = setting("S3_BUCKET")?;
        let endpoint = local_override("SOM_TEST_S3_ENDPOINT", test_mode())?;
        let region = std::env::var("AWS_REGION")
            .ok()
            .filter(|region| !region.is_empty())
            .unwrap_or_else(|| "us-east-1".to_owned());
        let (url, credentials) = match endpoint {
            Some(url) => {
                let local = AwsCredentials {
                    access_key: "local-test".to_owned(),
                    secret_key: "local-test".to_owned(),
                    token: None,
                };
                (url, Some(local))
            }
            None if region == "us-east-1" => ("https://s3.amazonaws.com".to_owned(), aws_credentials()),
            None => (format!("https://s3.{region}.amazonaws.com"), aws_credentials()),
        };
        let Some((scheme, rest)) = url.split_once("://") else {
            return Err(format!("Invalid endpoint: {url}"));
        };
        let (host, path) = rest.split_once('/').unwrap_or((rest, ""));
        let path = path.trim_end_matches('/');
        Ok(Self {
            client: http_client(),
            bucket,
            region,
            origin: format!("{scheme}://{host}"),
            host: host.to_owned(),
            prefix: if path.is_empty() { String::new() } else { format!("/{path}") },
            credentials,
        })
    }

    /// The SigV4 request of one object call, or None without credentials.
    fn signed(&self, method: Method, key: &str, extra: &[(&str, &str)], body: Vec<u8>) -> Option<Request<Full<Bytes>>> {
        let credentials = self.credentials.as_ref()?;
        let path = format!("{}/{}/{}", self.prefix, quote_segment(&self.bucket), quote_segment(key));
        let time = chrono::Utc::now();
        let stamp = time.format("%Y%m%dT%H%M%SZ").to_string();
        let day = time.format("%Y%m%d").to_string();
        let payload = sha256_hex(&body);
        let mut headers: Vec<(String, String)> = vec![
            ("host".to_owned(), self.host.clone()),
            ("x-amz-content-sha256".to_owned(), payload.clone()),
            ("x-amz-date".to_owned(), stamp.clone()),
        ];
        if let Some(token) = &credentials.token {
            headers.push(("x-amz-security-token".to_owned(), token.clone()));
        }
        headers.extend(extra.iter().map(|(name, value)| ((*name).to_owned(), (*value).to_owned())));
        headers.sort();
        let mut canonical = format!("{}\n{path}\n\n", method.as_str());
        for (name, value) in &headers {
            canonical += &format!("{name}:{}\n", value.trim());
        }
        let names: Vec<&str> = headers.iter().map(|(name, _)| name.as_str()).collect();
        let signed = names.join(";");
        canonical += &format!("\n{signed}\n{payload}");
        let scope = format!("{day}/{}/s3/aws4_request", self.region);
        let text = format!("AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{}", sha256_hex(canonical.as_bytes()));
        let mut signing = hmac_sha256(format!("AWS4{}", credentials.secret_key).as_bytes(), day.as_bytes());
        for part in [self.region.as_str(), "s3", "aws4_request"] {
            signing = hmac_sha256(&signing, part.as_bytes());
        }
        let signature = hex(&hmac_sha256(&signing, text.as_bytes()));
        let authorization = format!(
            "AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders={signed}, Signature={signature}",
            credentials.access_key
        );
        let mut pairs: Vec<(&str, &str)> = headers
            .iter()
            .map(|(name, value)| (name.as_str(), value.as_str()))
            .collect();
        pairs.push(("authorization", &authorization));
        outbound(method, &format!("{}{path}", self.origin), &pairs, body)
    }

    /// A 2xx reply, else the error of its S3 code, or of its status without a code.
    async fn call(&self, request: Option<Request<Full<Bytes>>>) -> Result<Reply, ObjectError> {
        let request = request.ok_or(ObjectError::Unavailable)?;
        let reply = send(&self.client, request).await.ok_or(ObjectError::Unavailable)?;
        if (200..300).contains(&reply.status) {
            return Ok(reply);
        }
        let text = String::from_utf8_lossy(&reply.body);
        let code = text
            .split_once("<Code>")
            .and_then(|(_, after)| after.split_once("</Code>"))
            .map_or_else(|| reply.status.to_string(), |(code, _)| code.to_owned());
        Err(match code.as_str() {
            "NoSuchKey" | "404" => ObjectError::Missing,
            "PreconditionFailed" | "ConditionalRequestConflict" | "412" => ObjectError::Exists,
            _ => ObjectError::Unavailable,
        })
    }

    /// put_object(IfNoneMatch="*") with the sha256 metadata.
    pub async fn put(&self, key: &str, body: &[u8], sha256: &str) -> Result<(), ObjectError> {
        let headers = [
            ("content-type", "application/octet-stream"),
            ("if-none-match", "*"),
            ("x-amz-meta-sha256", sha256),
        ];
        self.call(self.signed(Method::PUT, key, &headers, body.to_vec()))
            .await
            .map(|_| ())
    }

    /// get_object of the first OBJECT_MAX_BYTES + 1 bytes.
    pub async fn get(&self, key: &str) -> Result<Vec<u8>, ObjectError> {
        let range = format!("bytes=0-{OBJECT_MAX_BYTES}");
        let request = self.signed(Method::GET, key, &[("range", range.as_str())], Vec::new());
        let reply = self.call(request).await?;
        let end = reply.body.len().min(OBJECT_MAX_BYTES + 1);
        Ok(reply.body[..end].to_vec())
    }

    pub async fn delete(&self, key: &str) -> Result<(), ObjectError> {
        self.call(self.signed(Method::DELETE, key, &[], Vec::new()))
            .await
            .map(|_| ())
    }
}

"##;

const GCS: &str = r##"/// Cloud Storage objects through the JSON API, as google-cloud-storage calls it: one attempt
/// within 5 seconds. STORAGE_EMULATOR_HOST selects a local emulator in tests, called without
/// credentials.
pub struct Gcs {
    client: HttpClient,
    bucket: String,
    base: String,
    emulated: bool,
    adc: Adc,
}

impl Gcs {
    fn load() -> Result<Self, String> {
        let bucket = setting("GCS_BUCKET")?;
        let emulator = local_override("STORAGE_EMULATOR_HOST", test_mode())?;
        setting("GCS_PROJECT")?;
        let base = match &emulator {
            Some(host) if host.contains("://") => host.trim_end_matches('/').to_owned(),
            Some(host) => format!("http://{}", host.trim_end_matches('/')),
            None => "https://storage.googleapis.com".to_owned(),
        };
        Ok(Self {
            client: http_client(),
            bucket,
            base,
            emulated: emulator.is_some(),
            adc: Adc::new(),
        })
    }

    /// One call, with a bearer token of the application default credentials outside the emulator.
    async fn call(
        &self,
        method: Method,
        url: &str,
        mut headers: Vec<(&'static str, String)>,
        body: Vec<u8>,
    ) -> Result<Reply, ObjectError> {
        if !self.emulated {
            let token = self.adc.access_token(&self.client).await.ok_or(ObjectError::Unavailable)?;
            headers.push(("authorization", format!("Bearer {token}")));
        }
        let pairs: Vec<(&str, &str)> = headers.iter().map(|(name, value)| (*name, value.as_str())).collect();
        let request = outbound(method, url, &pairs, body).ok_or(ObjectError::Unavailable)?;
        send(&self.client, request).await.ok_or(ObjectError::Unavailable)
    }

    fn object_url(&self, prefix: &str, key: &str) -> String {
        format!(
            "{}{prefix}/storage/v1/b/{}/o/{}",
            self.base,
            quote_segment(&self.bucket),
            quote_segment(key)
        )
    }

    /// A multipart upload with if_generation_match=0 and the sha256 metadata.
    pub async fn put(&self, key: &str, body: &[u8], sha256: &str) -> Result<(), ObjectError> {
        let url = format!(
            "{}/upload/storage/v1/b/{}/o?uploadType=multipart&ifGenerationMatch=0",
            self.base,
            quote_segment(&self.bucket)
        );
        let metadata = json!({"name": key, "contentType": "application/octet-stream", "metadata": {"sha256": sha256}});
        let metadata = serde_json::to_vec(&metadata).map_err(|_| ObjectError::Unavailable)?;
        let boundary = format!("som-object-{sha256}");
        let mut content = Vec::with_capacity(body.len() + metadata.len() + 256);
        content.extend_from_slice(
            format!("--{boundary}\r\ncontent-type: application/json; charset=UTF-8\r\n\r\n").as_bytes(),
        );
        content.extend_from_slice(&metadata);
        content.extend_from_slice(
            format!("\r\n--{boundary}\r\ncontent-type: application/octet-stream\r\n\r\n").as_bytes(),
        );
        content.extend_from_slice(body);
        content.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        let headers = vec![("content-type", format!("multipart/related; boundary=\"{boundary}\""))];
        let reply = self.call(Method::POST, &url, headers, content).await?;
        match reply.status {
            200..=299 => Ok(()),
            409 | 412 => Err(ObjectError::Exists),
            _ => Err(ObjectError::Unavailable),
        }
    }

    /// download_as_bytes(start=0, end=OBJECT_MAX_BYTES).
    pub async fn get(&self, key: &str) -> Result<Vec<u8>, ObjectError> {
        let url = format!("{}?alt=media", self.object_url("/download", key));
        let headers = vec![("range", format!("bytes=0-{OBJECT_MAX_BYTES}"))];
        let reply = self.call(Method::GET, &url, headers, Vec::new()).await?;
        match reply.status {
            200 | 206 => {
                let end = reply.body.len().min(OBJECT_MAX_BYTES + 1);
                Ok(reply.body[..end].to_vec())
            }
            404 => Err(ObjectError::Missing),
            _ => Err(ObjectError::Unavailable),
        }
    }

    pub async fn delete(&self, key: &str) -> Result<(), ObjectError> {
        let url = self.object_url("", key);
        let reply = self.call(Method::DELETE, &url, Vec::new(), Vec::new()).await?;
        match reply.status {
            200..=299 => Ok(()),
            404 => Err(ObjectError::Missing),
            _ => Err(ObjectError::Unavailable),
        }
    }
}

"##;

const API_OBJECTS: &str = r#"
/// The key pattern ^[A-Za-z0-9_-]{1,80}$, else 422.
fn object_key(path: Result<Path<String>, PathRejection>) -> Result<String, ApiError> {
    let key = segments(path);
    let valid = (1..=80).contains(&key.len())
        && key.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'));
    if valid {
        return Ok(key);
    }
    Err(ApiError::Invalid(vec![problem(
        "string_pattern_mismatch",
        json!(["path", "key"]),
        "String should match pattern '^[A-Za-z0-9_-]{1,80}$'",
    )]))
}

/// 404 for a missing object, else 503.
fn object_error(error: ObjectError) -> ApiError {
    match error {
        ObjectError::Missing => ApiError::Status(StatusCode::NOT_FOUND, "object not found"),
        ObjectError::Exists | ObjectError::Unavailable => ApiError::Status(
            StatusCode::SERVICE_UNAVAILABLE,
            "object storage unavailable; retry the request",
        ),
    }
}

/// Store an object once. The same body again is accepted; a different body is a conflict.
async fn upload_object(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
    payload: Result<Bytes, BytesRejection>,
) -> Result<Json<Value>, ApiError> {
    access?;
    let key = object_key(path)?;
    let body = match payload {
        Ok(body) if body.len() <= OBJECT_MAX_BYTES => body,
        _ => return Err(ApiError::Status(StatusCode::PAYLOAD_TOO_LARGE, "object exceeds 1 MiB")),
    };
    if body.is_empty() {
        return Err(ApiError::Status(StatusCode::UNPROCESSABLE_ENTITY, "empty object"));
    }
    let sha256 = sha256_hex(&body);
    match state.storage.put(&key, &body, &sha256).await {
        Ok(()) => {}
        Err(ObjectError::Exists) => {
            let existing = state.storage.get(&key).await.map_err(object_error)?;
            if existing[..] != body[..] {
                return Err(ApiError::Status(
                    StatusCode::CONFLICT,
                    "object key already contains different data",
                ));
            }
        }
        Err(ObjectError::Missing) => return Err(ApiError::Internal),
        Err(error) => return Err(object_error(error)),
    }
    Ok(Json(json!({"key": key, "size": body.len(), "sha256": sha256})))
}

async fn download_object(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ApiError> {
    access?;
    let key = object_key(path)?;
    let body = state.storage.get(&key).await.map_err(object_error)?;
    if body.len() > OBJECT_MAX_BYTES {
        return Err(ApiError::Status(
            StatusCode::PAYLOAD_TOO_LARGE,
            "stored object exceeds 1 MiB",
        ));
    }
    let digest = sha256_hex(&body);
    Ok(([("x-content-sha256", digest)], body).into_response())
}

/// 204, also for a missing object.
async fn remove_object(
    State(state): State<AppState>,
    access: Result<Access, ApiError>,
    path: Result<Path<String>, PathRejection>,
) -> Result<StatusCode, ApiError> {
    access?;
    let key = object_key(path)?;
    match state.storage.delete(&key).await {
        Ok(()) | Err(ObjectError::Missing) => Ok(StatusCode::NO_CONTENT),
        Err(error) => Err(object_error(error)),
    }
}
"#;
