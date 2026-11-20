use std::sync::{Arc, Mutex, OnceLock};

use axum::Router;
use axum::extract::Extension;
use axum::http::StatusCode;
use axum::routing::get;
use som_reference_order::interfaces::error::ApiError;
use som_reference_order::interfaces::request_id::{RequestId, request_id_layer};
use uuid::Uuid;

use crate::common::{app, body_json, send};

/// A log sink that the test can read back.
#[derive(Clone, Default)]
struct Buf(Arc<Mutex<Vec<u8>>>);

impl Buf {
    fn text(&self) -> String {
        String::from_utf8_lossy(&self.0.lock().expect("log lock")).into_owned()
    }
}

impl std::io::Write for Buf {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.0.lock().expect("log lock").extend_from_slice(data);
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for Buf {
    type Writer = Buf;

    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

async fn boom(Extension(id): Extension<RequestId>) -> ApiError {
    ApiError::internal("secret", &id.0)
}

/// The id the response carries, checked to be a UUIDv7 that the log has.
fn assert_made_id(headers: &axum::http::HeaderMap, buf: &Buf) -> String {
    let id = headers
        .get("x-request-id")
        .expect("response has x-request-id")
        .to_str()
        .expect("x-request-id is text")
        .to_owned();
    let parsed = Uuid::parse_str(&id).expect("made id is a UUID");
    assert_eq!(parsed.get_version_num(), 7, "made id is a UUIDv7");
    assert!(buf.text().contains(&id), "log holds the made id");
    id
}

/// The one log sink of this test binary. Tests in parallel emit the same log
/// callsites, so a thread-local subscriber per test races with them; one global
/// subscriber does not. Each test checks only the ids it sent.
fn log_sink() -> &'static Buf {
    static SINK: OnceLock<Buf> = OnceLock::new();
    SINK.get_or_init(|| {
        let buf = Buf::default();
        let subscriber = tracing_subscriber::fmt()
            .with_writer(buf.clone())
            .with_ansi(false)
            .finish();
        tracing::subscriber::set_global_default(subscriber).expect("no other global subscriber");
        tracing::callsite::rebuild_interest_cache();
        buf
    })
}

#[tokio::test]
async fn request_id_is_read_or_made_returned_and_logged() {
    let buf = log_sink();
    let (app, _store) = app();

    // a. a given id is returned unchanged and logged on the request line
    let (status, headers, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("x-request-id", "abc-123")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers
            .get("x-request-id")
            .expect("response has x-request-id")
            .to_str()
            .expect("x-request-id is text"),
        "abc-123"
    );
    assert!(
        buf.text()
            .lines()
            .any(|line| line.contains("request done") && line.contains("abc-123")),
        "log line of the request holds the given id: {}",
        buf.text()
    );

    // b. no id: a UUIDv7 is made, returned and logged
    let (status, headers, _) = send(&app, "GET", "/v1/orders", &[], None).await;
    assert_eq!(status, StatusCode::OK);
    assert_made_id(&headers, buf);

    // c. an id over 128 bytes is replaced and never logged
    let long = "a".repeat(129);
    let (status, headers, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("x-request-id", long.as_str())],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let id = assert_made_id(&headers, buf);
    assert_ne!(id, long);
    assert!(
        !buf.text().contains(&long),
        "the over-long id is not logged"
    );

    // d. an empty id is replaced by a UUIDv7
    let (status, headers, _) = send(&app, "GET", "/v1/orders", &[("x-request-id", "")], None).await;
    assert_eq!(status, StatusCode::OK);
    assert_made_id(&headers, buf);

    // f. an error response carries the id too
    let (status, headers, _) = send(&app, "GET", "/v1/orders/missing", &[], None).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    let id = headers
        .get("x-request-id")
        .expect("404 has x-request-id")
        .to_str()
        .expect("x-request-id is text");
    assert!(!id.is_empty(), "404 carries a non-empty id");

    // e. the 500 body uses the same id as the response header. It is in this
    // test, not a second one: a parallel test that shares the layer's log
    // callsites would race with this test's subscriber.
    let inner: Router = Router::new()
        .route("/boom", get(boom))
        .layer(axum::middleware::from_fn(request_id_layer));
    let (status, headers, body) =
        send(&inner, "GET", "/boom", &[("x-request-id", "req-500")], None).await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(
        headers
            .get("x-request-id")
            .expect("500 has x-request-id")
            .to_str()
            .expect("x-request-id is text"),
        "req-500"
    );
    assert_eq!(body_json(&body)["details"][0]["request_id"], "req-500");
}
