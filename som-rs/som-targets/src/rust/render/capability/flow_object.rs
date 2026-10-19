//! Issue #54 J2: object.put and object.get in the flow products. The object store of a flow block
//! makes the calls of the Python flow product (compact.storage.flow.s3 and .gcs): a conditional
//! create that carries the run and step that own it, a receipt of the bytes, and the provider
//! version a compensation deletes. A flow with object.put on S3 checks bucket versioning before it
//! serves. The /objects routes answer as the Python flow product answers. Without object storage
//! nothing changes.

use serde_json::Value;

use super::flow::once;
use super::storage::Storage;

/// src/integrations.rs: the flow calls of the object store.
pub(super) fn integrations(params: &Value, text: String) -> Result<String, String> {
    let storage = Storage::read(params)?;
    if storage == Storage::None {
        return Ok(text);
    }
    let mut text = once(
        &text,
        "    Exists,\n    Unavailable,\n}\n",
        "    Exists,\n    Unavailable,\n    /// A stored object over OBJECT_MAX_BYTES.\n    Large,\n}\n",
    )?;
    let code = if storage == Storage::S3 {
        for (anchor, with) in S3_EDITS {
            text = once(&text, anchor, with)?;
        }
        S3_FLOW
    } else {
        GCS_FLOW
    };
    Ok(text + FLOW_OBJECT + code)
}

/// src/api.rs: the object errors and the upload of the Python flow product.
pub(super) fn api(params: &Value, text: String) -> Result<String, String> {
    if Storage::read(params)? == Storage::None {
        return Ok(text);
    }
    let text = once(&text, OBJECT_ERROR, FLOW_OBJECT_ERROR)?;
    once(&text, UPLOAD, FLOW_UPLOAD)
}

/// src/main.rs: a flow with object.put on S3 serves only on a versioned bucket.
pub(super) fn main(params: &Value, text: String) -> Result<String, String> {
    if Storage::read(params)? != Storage::S3 || !puts(&params["flow"]["steps"]) {
        return Ok(text);
    }
    let listener = "    let listener = tokio::net::TcpListener::bind(&address)\n";
    once(
        &text,
        listener,
        &format!("    state.storage.verify_flow_storage().await?;\n{listener}"),
    )
}

/// README.md: the object errors of the flow product.
pub(super) fn readme(params: &Value, text: String) -> Result<String, String> {
    let storage = Storage::read(params)?;
    if storage == Storage::None {
        return Ok(text);
    }
    let text = once(
        &text,
        "409 `object key already contains different data`",
        "409 `object contains different data`",
    )?;
    let unavailable = "  storage unavailable; retry the request`.\n";
    if storage == Storage::S3 && puts(&params["flow"]["steps"]) {
        once(
            &text,
            unavailable,
            "  storage unavailable`.\n- The flow's `object.put` deletes the version it created when it compensates, so the\n  service starts only on a bucket with versioning enabled.\n",
        )
    } else {
        once(&text, unavailable, "  storage unavailable`.\n")
    }
}

/// src/flows.rs: the object steps and their compensation.
pub(super) fn runtime(params: &Value, text: String) -> Result<String, String> {
    let storage = Storage::read(params)?;
    if storage == Storage::None {
        return Ok(text);
    }
    let mut text = text;
    for (anchor, with) in RUNTIME_EDITS {
        text = once(&text, anchor, with)?;
    }
    Ok(text + &RUNTIME.replace("@STORE@", storage.name()))
}

/// Whether a step, in a transaction or a branch arm too, is object.put.
fn puts(steps: &Value) -> bool {
    steps.as_array().into_iter().flatten().any(|step| {
        step["op"] == "object.put"
            || ["steps", "then", "else"]
                .iter()
                .any(|arm| puts(&step[*arm]))
    })
}

// ---------------------------------------------------------------- generated code

const S3_EDITS: [(&str, &str); 4] = [
    (
        "        let credentials = self.credentials.as_ref()?;
        let path = format!(\"{}/{}/{}\", self.prefix, quote_segment(&self.bucket), quote_segment(key));
",
        "        self.signed_at(method, key, &[], extra, body)
    }

    /// The SigV4 request of a call on the bucket (an empty key) or on an object, with its query
    /// pairs in canonical order. A pair without a value is sent bare, as botocore sends it.
    fn signed_at(
        &self,
        method: Method,
        key: &str,
        query: &[(&str, &str)],
        extra: &[(&str, &str)],
        body: Vec<u8>,
    ) -> Option<Request<Full<Bytes>>> {
        let credentials = self.credentials.as_ref()?;
        let mut path = format!(\"{}/{}\", self.prefix, quote_segment(&self.bucket));
        if !key.is_empty() {
            path = format!(\"{path}/{}\", quote_segment(key));
        }
        let canonical_query: Vec<String> = query
            .iter()
            .map(|(name, value)| format!(\"{name}={}\", quote_segment(value)))
            .collect();
        let sent: Vec<String> = query
            .iter()
            .map(|(name, value)| {
                if value.is_empty() {
                    (*name).to_owned()
                } else {
                    format!(\"{name}={}\", quote_segment(value))
                }
            })
            .collect();
",
    ),
    (
        "        let mut canonical = format!(\"{}\\n{path}\\n\\n\", method.as_str());\n",
        "        let mut canonical = format!(\"{}\\n{path}\\n{}\\n\", method.as_str(), canonical_query.join(\"&\"));\n",
    ),
    (
        "        outbound(method, &format!(\"{}{path}\", self.origin), &pairs, body)\n",
        "        let url = if sent.is_empty() {
            format!(\"{}{path}\", self.origin)
        } else {
            format!(\"{}{path}?{}\", self.origin, sent.join(\"&\"))
        };
        outbound(method, &url, &pairs, body)
",
    ),
    (
        "        let text = String::from_utf8_lossy(&reply.body);
        let code = text
            .split_once(\"<Code>\")
            .and_then(|(_, after)| after.split_once(\"</Code>\"))
            .map_or_else(|| reply.status.to_string(), |(code, _)| code.to_owned());
        Err(match code.as_str() {
            \"NoSuchKey\" | \"404\" => ObjectError::Missing,
            \"PreconditionFailed\" | \"ConditionalRequestConflict\" | \"412\" => ObjectError::Exists,
            _ => ObjectError::Unavailable,
        })
",
        "        Err(s3_code(reply.status, &reply.body))\n",
    ),
];

const FLOW_OBJECT: &str = r##"/// The receipt of an object step: the bytes with their size and digest, and the provider version
/// a run owns.
pub struct FlowObject {
    pub key: String,
    pub body: Vec<u8>,
    pub version: Option<String>,
    pub owned: bool,
}

impl FlowObject {
    /// The receipt as the Python FlowObject dumps it, the bytes in base64.
    pub fn json(&self) -> serde_json::Value {
        serde_json::json!({
            "key": self.key,
            "size": self.body.len(),
            "sha256": sha256_hex(&self.body),
            "body": base64::Engine::encode(&base64::engine::general_purpose::STANDARD, &self.body),
            "version": self.version,
            "owned": self.owned,
        })
    }
}

/// object_receipt: a stored object over OBJECT_MAX_BYTES is too large.
fn flow_receipt(key: &str, body: Vec<u8>, version: Option<String>, owned: bool) -> Result<FlowObject, ObjectError> {
    if body.len() > OBJECT_MAX_BYTES {
        return Err(ObjectError::Large);
    }
    Ok(FlowObject {
        key: key.to_owned(),
        body,
        version,
        owned,
    })
}

"##;

const S3_FLOW: &str = r##"/// The error of an S3 code, else of the status of a reply without one, as storage_errors maps
/// the botocore error.
fn s3_code(status: u16, body: &[u8]) -> ObjectError {
    let text = String::from_utf8_lossy(body);
    let code = text
        .split_once("<Code>")
        .and_then(|(_, after)| after.split_once("</Code>"))
        .map_or_else(|| status.to_string(), |(code, _)| code.to_owned());
    match code.as_str() {
        "NoSuchKey" | "NoSuchVersion" | "404" => ObjectError::Missing,
        "PreconditionFailed" | "ConditionalRequestConflict" | "412" | "409" => ObjectError::Exists,
        _ => ObjectError::Unavailable,
    }
}

/// A reply with its headers: S3 answers a version and the object metadata in them.
struct FlowReply {
    status: u16,
    headers: axum::http::HeaderMap,
    body: Bytes,
}

async fn flow_send(client: &HttpClient, request: Request<Full<Bytes>>) -> Option<FlowReply> {
    let exchange = async {
        let response = client.request(request).await.ok()?;
        let status = response.status().as_u16();
        let headers = response.headers().clone();
        let body = response.into_body().collect().await.ok()?.to_bytes();
        Some(FlowReply { status, headers, body })
    };
    tokio::time::timeout(Duration::from_secs(5), exchange).await.ok().flatten()
}

/// The contents of each <name> element of an S3 listing.
fn xml_blocks<'a>(text: &'a str, name: &str) -> Vec<&'a str> {
    let (open, close) = (format!("<{name}>"), format!("</{name}>"));
    let mut blocks = Vec::new();
    let mut rest = text;
    while let Some((_, after)) = rest.split_once(open.as_str()) {
        let Some((block, tail)) = after.split_once(close.as_str()) else {
            break;
        };
        blocks.push(block);
        rest = tail;
    }
    blocks
}

/// The unescaped text of the first <name> element.
fn xml_text(text: &str, name: &str) -> Option<String> {
    let block = xml_blocks(text, name).into_iter().next()?;
    Some(
        block
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&apos;", "'")
            .replace("&amp;", "&"),
    )
}

fn flow_header<'a>(headers: &'a axum::http::HeaderMap, name: &str) -> Result<Option<&'a str>, ObjectError> {
    headers
        .get(name)
        .map(|value| value.to_str().map_err(|_| ObjectError::Unavailable))
        .transpose()
}

/// object_owned on the metadata headers of a version.
fn flow_meta(headers: &axum::http::HeaderMap, owner: &str, step: &str) -> Result<bool, ObjectError> {
    Ok(flow_header(headers, "x-amz-meta-som-run")? == Some(owner)
        && flow_header(headers, "x-amz-meta-som-step")? == Some(step))
}

impl S3 {
    /// A 2xx reply with its headers, else the error of its S3 code.
    async fn call_headers(&self, request: Option<Request<Full<Bytes>>>) -> Result<FlowReply, ObjectError> {
        let request = request.ok_or(ObjectError::Unavailable)?;
        let reply = flow_send(&self.client, request).await.ok_or(ObjectError::Unavailable)?;
        if (200..300).contains(&reply.status) {
            return Ok(reply);
        }
        Err(s3_code(reply.status, &reply.body))
    }

    /// object_get: the receipt of the bytes under the key.
    pub async fn flow_get(&self, key: &str) -> Result<FlowObject, ObjectError> {
        flow_receipt(key, self.get(key).await?, None, false)
    }

    /// existing_object: equal bytes are the same create, owned only with this run's version.
    async fn existing_object(&self, key: &str, body: &[u8], owner: &str, step: &str) -> Result<FlowObject, ObjectError> {
        let mut existing = self.flow_get(key).await?;
        if existing.body != body {
            return Err(ObjectError::Exists);
        }
        if !owner.is_empty() {
            existing.version = self.object_version(key, owner, step).await?;
            existing.owned = existing.version.is_some();
        }
        Ok(existing)
    }

    /// object_put: put_object(IfNoneMatch="*") with the run and step that own the version.
    pub async fn flow_put(&self, key: &str, body: &[u8], owner: &str, step: &str) -> Result<FlowObject, ObjectError> {
        let sha256 = sha256_hex(body);
        let headers = [
            ("content-type", "application/octet-stream"),
            ("if-none-match", "*"),
            ("x-amz-meta-sha256", sha256.as_str()),
            ("x-amz-meta-som-run", owner),
            ("x-amz-meta-som-step", step),
        ];
        let reply = match self.call_headers(self.signed(Method::PUT, key, &headers, body.to_vec())).await {
            Err(ObjectError::Exists) => return self.existing_object(key, body, owner, step).await,
            other => other?,
        };
        let version = flow_header(&reply.headers, "x-amz-version-id")?.map(str::to_owned);
        if !owner.is_empty() && version.as_deref().is_none_or(|version| version.is_empty() || version == "null") {
            return Err(ObjectError::Unavailable);
        }
        flow_receipt(key, body.to_vec(), version, true)
    }

    /// Whether this run and step own one version of the key.
    async fn owned_version(&self, key: &str, version: &str, owner: &str, step: &str) -> Result<bool, ObjectError> {
        let request = self.signed_at(Method::HEAD, key, &[("versionId", version)], &[], Vec::new());
        let reply = self.call_headers(request).await?;
        flow_meta(&reply.headers, owner, step)
    }

    /// object_reverse: delete this run's version. A missing version is already deleted.
    pub async fn object_reverse(&self, key: &str, owner: &str, step: &str, version: Option<&str>) -> Result<bool, ObjectError> {
        let Some(version) = version else {
            return Ok(false);
        };
        let outcome: Result<bool, ObjectError> = async {
            if !self.owned_version(key, version, owner, step).await? {
                return Ok(false);
            }
            let request = self.signed_at(Method::DELETE, key, &[("versionId", version)], &[], Vec::new());
            self.call_headers(request).await?;
            Ok(true)
        }
        .await;
        match outcome {
            Err(ObjectError::Missing) => Ok(true),
            other => other,
        }
    }

    /// object_version: the one version of the key this run and step own, through every page of
    /// list_object_versions.
    pub async fn object_version(&self, key: &str, owner: &str, step: &str) -> Result<Option<String>, ObjectError> {
        let mut found: Option<String> = None;
        let mut markers: Option<(String, String)> = None;
        let mut seen: Vec<(String, String)> = Vec::new();
        loop {
            let mut query: Vec<(&str, &str)> = Vec::new();
            if let Some((key_marker, _)) = &markers {
                query.push(("key-marker", key_marker));
            }
            query.push(("prefix", key));
            if let Some((_, version_marker)) = &markers {
                query.push(("version-id-marker", version_marker));
            }
            query.push(("versions", ""));
            let reply = self.call_headers(self.signed_at(Method::GET, "", &query, &[], Vec::new())).await?;
            let text = String::from_utf8_lossy(&reply.body).into_owned();
            for item in xml_blocks(&text, "Version") {
                if xml_text(item, "Key").as_deref() != Some(key) {
                    continue;
                }
                let version = xml_text(item, "VersionId").ok_or(ObjectError::Unavailable)?;
                if self.owned_version(key, &version, owner, step).await? {
                    if found.as_ref().is_some_and(|found| *found != version) {
                        return Err(ObjectError::Unavailable);
                    }
                    found = Some(version);
                }
            }
            if xml_text(&text, "IsTruncated").as_deref() != Some("true") {
                return Ok(found);
            }
            let next = (
                xml_text(&text, "NextKeyMarker").unwrap_or_default(),
                xml_text(&text, "NextVersionIdMarker").unwrap_or_default(),
            );
            if seen.contains(&next) {
                return Err(ObjectError::Unavailable);
            }
            seen.push(next.clone());
            markers = Some(next);
        }
    }

    /// verify_flow_storage: a compensation deletes the version it created, so the bucket keeps
    /// versions.
    pub async fn verify_flow_storage(&self) -> Result<(), String> {
        let request = self.signed_at(Method::GET, "", &[("versioning", "")], &[], Vec::new());
        let reply = self
            .call_headers(request)
            .await
            .map_err(|_| "S3 bucket versioning is unavailable".to_owned())?;
        if xml_text(&String::from_utf8_lossy(&reply.body), "Status").as_deref() != Some("Enabled") {
            return Err("S3 bucket versioning is required for flow compensation".to_owned());
        }
        Ok(())
    }
}

"##;

const GCS_FLOW: &str = r##"/// flow_generation: a positive generation, a JSON integer or 1 to 20 decimal digits.
fn flow_generation(value: &serde_json::Value) -> Result<u128, ObjectError> {
    let generation = match value {
        serde_json::Value::Number(number) => number.as_u64().map(u128::from),
        serde_json::Value::String(text)
            if (1..=20).contains(&text.len()) && text.bytes().all(|byte| byte.is_ascii_digit()) =>
        {
            text.parse().ok()
        }
        _ => None,
    };
    generation.filter(|generation| *generation > 0).ok_or(ObjectError::Unavailable)
}

/// A JSON API status as storage_errors maps the google-cloud-storage error.
fn flow_status(reply: Reply) -> Result<Reply, ObjectError> {
    match reply.status {
        200..=299 => Ok(reply),
        404 => Err(ObjectError::Missing),
        409 | 412 => Err(ObjectError::Exists),
        _ => Err(ObjectError::Unavailable),
    }
}

fn flow_resource(reply: &Reply) -> Result<serde_json::Value, ObjectError> {
    serde_json::from_slice(&reply.body).map_err(|_| ObjectError::Unavailable)
}

/// object_owned on the metadata of an object resource: none is empty, else text values only.
fn flow_owned(metadata: &serde_json::Value, owner: &str, step: &str) -> Result<bool, ObjectError> {
    let empty = serde_json::Map::new();
    let metadata = match metadata {
        serde_json::Value::Null => &empty,
        serde_json::Value::Object(metadata) => metadata,
        _ => return Err(ObjectError::Unavailable),
    };
    if metadata.values().any(|value| !value.is_string()) {
        return Err(ObjectError::Unavailable);
    }
    let value = |name: &str| metadata.get(name).and_then(serde_json::Value::as_str);
    Ok(value("som-run") == Some(owner) && value("som-step") == Some(step))
}

impl Gcs {
    /// object_get: the receipt of the bytes under the key.
    pub async fn flow_get(&self, key: &str) -> Result<FlowObject, ObjectError> {
        flow_receipt(key, self.get(key).await?, None, false)
    }

    /// existing_object: equal bytes are the same create, owned only with this run's generation.
    async fn existing_object(&self, key: &str, body: &[u8], owner: &str, step: &str) -> Result<FlowObject, ObjectError> {
        let mut existing = self.flow_get(key).await?;
        if existing.body != body {
            return Err(ObjectError::Exists);
        }
        if !owner.is_empty() {
            existing.version = self.object_version(key, owner, step).await?;
            existing.owned = existing.version.is_some();
        }
        Ok(existing)
    }

    /// object_put: upload_from_string(if_generation_match=0) with the run and step that own the
    /// generation.
    pub async fn flow_put(&self, key: &str, body: &[u8], owner: &str, step: &str) -> Result<FlowObject, ObjectError> {
        let sha256 = sha256_hex(body);
        let url = format!(
            "{}/upload/storage/v1/b/{}/o?uploadType=multipart&ifGenerationMatch=0",
            self.base,
            quote_segment(&self.bucket)
        );
        let metadata = serde_json::json!({
            "name": key,
            "contentType": "application/octet-stream",
            "metadata": {"sha256": sha256, "som-run": owner, "som-step": step},
        });
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
        let reply = match self.call(Method::POST, &url, headers, content).await.and_then(flow_status) {
            Err(ObjectError::Exists) => return self.existing_object(key, body, owner, step).await,
            other => other?,
        };
        let generation = flow_generation(&flow_resource(&reply)?["generation"])?;
        flow_receipt(key, body.to_vec(), Some(generation.to_string()), true)
    }

    /// object_reverse: delete this run's generation. A missing generation is already deleted.
    pub async fn object_reverse(&self, key: &str, owner: &str, step: &str, version: Option<&str>) -> Result<bool, ObjectError> {
        let Some(version) = version else {
            return Ok(false);
        };
        let generation = flow_generation(&serde_json::Value::from(version))?;
        let outcome: Result<bool, ObjectError> = async {
            let url = format!("{}?generation={generation}", self.object_url("", key));
            let reply = flow_status(self.call(Method::GET, &url, Vec::new(), Vec::new()).await?)?;
            let resource = flow_resource(&reply)?;
            if !flow_owned(&resource["metadata"], owner, step)? {
                return Ok(false);
            }
            let current = flow_generation(&resource["generation"])?;
            let url = format!(
                "{}?generation={generation}&ifGenerationMatch={current}",
                self.object_url("", key)
            );
            flow_status(self.call(Method::DELETE, &url, Vec::new(), Vec::new()).await?)?;
            Ok(true)
        }
        .await;
        match outcome {
            Err(ObjectError::Missing) => Ok(true),
            other => other,
        }
    }

    /// object_version: the one generation of the key this run and step own, through every page
    /// of list_blobs(versions=True).
    pub async fn object_version(&self, key: &str, owner: &str, step: &str) -> Result<Option<String>, ObjectError> {
        let mut found: Option<String> = None;
        let mut token: Option<String> = None;
        let mut seen: Vec<String> = Vec::new();
        loop {
            let mut url = format!(
                "{}/storage/v1/b/{}/o?prefix={}&versions=true",
                self.base,
                quote_segment(&self.bucket),
                quote_segment(key)
            );
            if let Some(token) = &token {
                url += &format!("&pageToken={}", quote_segment(token));
            }
            let reply = match self.call(Method::GET, &url, Vec::new(), Vec::new()).await.and_then(flow_status) {
                Err(ObjectError::Missing) => return Ok(None),
                other => other?,
            };
            let page = flow_resource(&reply)?;
            for item in page["items"].as_array().into_iter().flatten() {
                if item["name"].as_str() != Some(key) || !flow_owned(&item["metadata"], owner, step)? {
                    continue;
                }
                let version = flow_generation(&item["generation"])?.to_string();
                if found.as_ref().is_some_and(|found| *found != version) {
                    return Err(ObjectError::Unavailable);
                }
                found = Some(version);
            }
            let Some(next) = page["nextPageToken"].as_str().filter(|next| !next.is_empty()) else {
                return Ok(found);
            };
            if seen.iter().any(|token| token == next) {
                return Err(ObjectError::Unavailable);
            }
            seen.push(next.to_owned());
            token = Some(next.to_owned());
        }
    }
}

"##;

const OBJECT_ERROR: &str = r#"/// 404 for a missing object, else 503.
fn object_error(error: ObjectError) -> ApiError {
    match error {
        ObjectError::Missing => ApiError::Status(StatusCode::NOT_FOUND, "object not found"),
        ObjectError::Exists | ObjectError::Unavailable => ApiError::Status(
            StatusCode::SERVICE_UNAVAILABLE,
            "object storage unavailable; retry the request",
        ),
    }
}
"#;

const FLOW_OBJECT_ERROR: &str = r#"/// The status of an object error, as the Python ObjectError carries it.
fn object_error(error: ObjectError) -> ApiError {
    match error {
        ObjectError::Missing => ApiError::Status(StatusCode::NOT_FOUND, "object not found"),
        ObjectError::Exists => ApiError::Status(StatusCode::CONFLICT, "object contains different data"),
        ObjectError::Unavailable => ApiError::Status(StatusCode::SERVICE_UNAVAILABLE, "object storage unavailable"),
        ObjectError::Large => ApiError::Status(StatusCode::PAYLOAD_TOO_LARGE, "object is too large"),
    }
}
"#;

const UPLOAD: &str = r#"    let sha256 = sha256_hex(&body);
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
"#;

const FLOW_UPLOAD: &str = r#"    let sha256 = sha256_hex(&body);
    state.storage.flow_put(&key, &body, "", "").await.map_err(object_error)?;
"#;

const RUNTIME_EDITS: [(&str, &str); 5] = [
    (
        ") -> Response {\n    let mut problems = Vec::new();\n",
        ") -> Response {\n    let mut problems = Vec::new();\n    let _ = OBJECTS.set(state.storage.clone());\n",
    ),
    (
        "async fn recover(\n    State(state): State<AppState>,\n    access: Result<Access, crate::api::ApiError>,\n    Path(run_id): Path<String>,\n) -> Response {\n",
        "async fn recover(\n    State(state): State<AppState>,\n    access: Result<Access, crate::api::ApiError>,\n    Path(run_id): Path<String>,\n) -> Response {\n    let _ = OBJECTS.set(state.storage.clone());\n",
    ),
    (
        "    async fn step(&mut self, step: &Value, group: &str) -> Result<(), Fail> {\n",
        "    async fn step(&mut self, step: &Value, group: &str) -> Result<(), Fail> {\n        if let Some(done) = self.object_step(step).await {\n            return done;\n        }\n",
    ),
    (
        "                self.step(node, \"\").await?;\n                self.persist().await?;\n",
        "                self.step(node, \"\").await?;\n                if node[\"op\"] != \"object.put\" {\n                    self.persist().await?;\n                }\n",
    ),
    (
        "                return Err(Fail::http(503, \"invalid external journal\"));\n",
        "                self.external_inverse().await?;\n                continue;\n",
    ),
];

const RUNTIME: &str = r##"
// ---------------------------------------------------------------- objects

/// The object store of the routes, which the object steps share.
static OBJECTS: std::sync::OnceLock<std::sync::Arc<crate::integrations::@STORE@>> = std::sync::OnceLock::new();

fn objects() -> Result<&'static crate::integrations::@STORE@, Fail> {
    OBJECTS
        .get()
        .map(|store| &**store)
        .ok_or_else(|| Fail::Runtime("object store required".to_owned()))
}

/// base64.b64decode(validate=True): canonical padding, and the bits after the last byte are
/// ignored.
const FLOW_BASE64: base64::engine::GeneralPurpose = base64::engine::GeneralPurpose::new(
    &base64::alphabet::STANDARD,
    base64::engine::GeneralPurposeConfig::new().with_decode_allow_trailing_bits(true),
);

/// The status of an object error, as the Python ObjectError carries it.
fn object_fail(error: crate::integrations::ObjectError) -> Fail {
    use crate::integrations::ObjectError;
    match error {
        ObjectError::Missing => Fail::http(404, "object not found"),
        ObjectError::Exists => Fail::http(409, "object contains different data"),
        ObjectError::Unavailable => Fail::http(503, "object storage unavailable"),
        ObjectError::Large => Fail::http(413, "object is too large"),
    }
}

impl Ctx {
    /// object.put and object.get. Other steps are None.
    async fn object_step(&mut self, step: &Value) -> Option<Result<(), Fail>> {
        match step["op"].as_str() {
            Some("object.put") => Some(self.object_put(step).await),
            Some("object.get") => Some(self.object_get(step).await),
            _ => None,
        }
    }

    /// flow_put: journal the intent before the provider receives the write, then the version the
    /// provider proves. Its own checkpoints replace the one after a step.
    async fn object_put(&mut self, step: &Value) -> Result<(), Fail> {
        use crate::integrations::ObjectError;
        let name = step["name"].as_str().unwrap_or_default().to_owned();
        let key = self.resolve(&step["args"]["key"])?;
        let key = key.as_str().ok_or(Fail::Invalid)?.to_owned();
        let source = self.resolve(&step["args"]["body"])?;
        let source = source.as_str().ok_or(Fail::Invalid)?;
        let valid = (1..=80).contains(&key.len())
            && key.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'));
        if !valid {
            return Err(Fail::http(422, "invalid object key"));
        }
        let body = base64::Engine::decode(&FLOW_BASE64, source).map_err(|_| Fail::http(422, "invalid base64 body"))?;
        if body.is_empty() {
            return Err(Fail::http(422, "empty object"));
        }
        if body.len() > crate::integrations::OBJECT_MAX_BYTES {
            return Err(Fail::http(413, "object is too large"));
        }
        self.record.effects.push(Effect {
            group: format!("external_{name}"),
            step: name.clone(),
            undo: step["undo"].as_str().unwrap_or_default().to_owned(),
            role: String::new(),
            row_id: 0,
            revision: 0,
            before: Map::new(),
            key: key.clone(),
            version: None,
            owned: false,
            uncertain: true,
        });
        self.persist().await?;
        let store = objects()?;
        let receipt = match store.flow_put(&key, &body, &self.record.run_id, &name).await {
            Ok(receipt) => receipt,
            Err(ObjectError::Exists) => {
                match store.object_version(&key, &self.record.run_id, &name).await {
                    Ok(version) => {
                        if let Some(effect) = self.record.effects.last_mut() {
                            effect.owned = version.is_some();
                            effect.version = version;
                            effect.uncertain = false;
                        }
                    }
                    Err(ObjectError::Unavailable) => {}
                    Err(error) => return Err(object_fail(error)),
                }
                self.persist().await?;
                return Err(object_fail(ObjectError::Exists));
            }
            Err(error) => return Err(object_fail(error)),
        };
        if let Some(effect) = self.record.effects.last_mut() {
            effect.owned = receipt.owned;
            effect.version = receipt.version.clone();
            effect.uncertain = false;
        }
        self.record.values.insert(name, receipt.json());
        self.persist().await
    }

    /// object_get: the receipt of the bytes under the key.
    async fn object_get(&mut self, step: &Value) -> Result<(), Fail> {
        let name = step["name"].as_str().unwrap_or_default().to_owned();
        let key = self.resolve(&step["args"]["key"])?;
        let key = key.as_str().ok_or(Fail::Invalid)?;
        let receipt = objects()?.flow_get(key).await.map_err(object_fail)?;
        self.record.values.insert(name, receipt.json());
        Ok(())
    }

    /// external_inverse: journal a proven version before deleting that version.
    async fn external_inverse(&mut self) -> Result<(), Fail> {
        use crate::integrations::ObjectError;
        let store = objects()?;
        let Some(mut effect) = self.record.effects.last().cloned() else {
            return Ok(());
        };
        if effect.uncertain {
            let version = store
                .object_version(&effect.key, &self.record.run_id, &effect.step)
                .await
                .map_err(object_fail)?
                .ok_or_else(|| object_fail(ObjectError::Unavailable))?;
            effect.version = Some(version);
            effect.owned = true;
            effect.uncertain = false;
            if let Some(last) = self.record.effects.last_mut() {
                *last = effect.clone();
            }
            self.persist().await?;
        }
        if effect.owned
            && !store
                .object_reverse(&effect.key, &self.record.run_id, &effect.step, effect.version.as_deref())
                .await
                .map_err(object_fail)?
        {
            return Err(object_fail(ObjectError::Unavailable));
        }
        self.record.effects.pop();
        self.persist().await
    }
}
"##;
