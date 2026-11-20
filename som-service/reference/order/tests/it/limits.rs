use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use axum::http::StatusCode;
use axum::routing::get;
use server_lifecycle::LifecycleController;
use som_reference_order::application::idempotency::SystemClock;
use som_reference_order::infrastructure::memory_store::MemoryStore;
use som_reference_order::interfaces::limits::{
    DEFAULT_MAX_KEYS, DEFAULT_READ_CAPACITY, DEFAULT_REFILL_SECS, DEFAULT_WRITE_CAPACITY, Limits,
    REQUEST_TIMEOUT_SECS, SHUTDOWN_GRACE_SECS, request_timeout,
};
use som_reference_order::interfaces::metrics::OperationMetrics;
use som_reference_order::interfaces::routes::{BODY_LIMIT_BYTES, router_with_limits};
use som_reference_order::interfaces::server::{serve, shutdown_trigger};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::common::{body_json, call, open_verifier, send, valid_body};

fn limited_app(limits: Limits) -> Router {
    router_with_limits(
        Arc::new(MemoryStore::new()),
        Arc::new(SystemClock),
        open_verifier(),
        Arc::new(OperationMetrics::new()),
        limits,
    )
}

async fn bind_local() -> (tokio::net::TcpListener, std::net::SocketAddr) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind a local port");
    let addr = listener.local_addr().expect("local address");
    (listener, addr)
}

#[tokio::test]
async fn request_times_out_after_30_seconds_and_shutdown_is_graceful() {
    // Constants (decisions 30 and 31).
    assert_eq!(REQUEST_TIMEOUT_SECS, 30);
    assert_eq!(SHUTDOWN_GRACE_SECS, 10);
    assert_eq!(Limits::standard().timeout, Duration::from_secs(30));

    // A handler slower than the (short, replaced) limit answers 503 unavailable with Retry-After.
    let slow = Router::new()
        .route(
            "/slow",
            get(|| async {
                tokio::time::sleep(Duration::from_millis(300)).await;
                "late"
            }),
        )
        .layer(axum::middleware::from_fn_with_state(
            Duration::from_millis(50),
            request_timeout,
        ));
    let (status, headers, body) = call(&slow, "GET", "/slow", None).await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        headers
            .get("retry-after")
            .expect("503 carries retry-after")
            .to_str()
            .expect("retry-after is text"),
        "1"
    );
    assert_eq!(body_json(&body)["error"], "unavailable");

    // A real server: an in-flight request finishes after the shutdown signal, then the server stops.
    let (listener, addr) = bind_local().await;
    let lifecycle = LifecycleController::serving();
    let app = Router::new().route(
        "/slow",
        get(|| async {
            tokio::time::sleep(Duration::from_millis(400)).await;
            "done"
        }),
    );
    let server = tokio::spawn(serve(listener, app, lifecycle.clone()));
    let mut stream = tokio::net::TcpStream::connect(addr)
        .await
        .expect("connect to the server");
    stream
        .write_all(b"GET /slow HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await
        .expect("write the request");
    tokio::time::sleep(Duration::from_millis(100)).await;
    let trigger = shutdown_trigger(lifecycle.clone());
    let shutdown =
        tokio::spawn(async move { trigger.trigger("test", "graceful shutdown test").await });
    let mut response = Vec::new();
    tokio::time::timeout(Duration::from_secs(5), stream.read_to_end(&mut response))
        .await
        .expect("the in-flight response arrives in time")
        .expect("read the response");
    let text = String::from_utf8_lossy(&response).into_owned();
    assert!(text.starts_with("HTTP/1.1 200"), "response was: {text}");
    assert!(text.ends_with("done"), "response was: {text}");
    tokio::time::timeout(Duration::from_secs(5), server)
        .await
        .expect("the server stops in time")
        .expect("the server task ends");
    shutdown.await.expect("the shutdown task ends");
}

#[tokio::test]
async fn timeout_layer_is_wired_into_the_data_routes() {
    let app = limited_app(Limits {
        timeout: Duration::from_millis(100),
        admission: None,
    });
    let (listener, addr) = bind_local().await;
    let lifecycle = LifecycleController::serving();
    let server = tokio::spawn(serve(listener, app, lifecycle.clone()));

    // The body promises 100 bytes and sends 5: the handler waits for the rest.
    let mut stream = tokio::net::TcpStream::connect(addr)
        .await
        .expect("connect to the server");
    stream
        .write_all(
            b"POST /v1/orders HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 100\r\nConnection: close\r\n\r\n{\"a\":",
        )
        .await
        .expect("write the partial request");
    let mut response = Vec::new();
    tokio::time::timeout(Duration::from_secs(5), stream.read_to_end(&mut response))
        .await
        .expect("the timed-out response arrives in time")
        .expect("read the response");
    let text = String::from_utf8_lossy(&response).to_lowercase();
    assert!(text.starts_with("http/1.1 503"), "response was: {text}");
    assert!(text.contains("unavailable"), "response was: {text}");
    assert!(text.contains("retry-after: 1"), "response was: {text}");

    shutdown_trigger(lifecycle).trigger("test", "end").await;
    tokio::time::timeout(Duration::from_secs(5), server)
        .await
        .expect("the server stops in time")
        .expect("the server task ends");
}

#[tokio::test]
async fn admission_defaults_apply() {
    // Constants (decision 31).
    assert_eq!(DEFAULT_READ_CAPACITY, 600);
    assert_eq!(DEFAULT_WRITE_CAPACITY, 120);
    assert_eq!(DEFAULT_REFILL_SECS, 60);
    assert_eq!(DEFAULT_MAX_KEYS, 1024);
    assert_eq!(BODY_LIMIT_BYTES, 1024 * 1024);

    // The explicit defaults parse, and the empty environment gives the same defaults on.
    let explicit = service_http::AdmissionConfig::from_lookup("ORDER", |key| match key {
        "ORDER_ADMISSION_READ_CAPACITY" => Some("600".to_owned()),
        "ORDER_ADMISSION_WRITE_CAPACITY" => Some("120".to_owned()),
        "ORDER_ADMISSION_REFILL_SECS" => Some("60".to_owned()),
        "ORDER_ADMISSION_MAX_KEYS" => Some("1024".to_owned()),
        _ => None,
    })
    .expect("the explicit defaults parse");
    assert!(explicit.is_enabled());
    let defaults = Limits::from_lookup(|_| None).expect("the empty environment is valid");
    assert!(defaults.admission.is_some());
    assert_eq!(defaults.timeout, Duration::from_secs(30));

    // A bad value is an error.
    assert!(
        Limits::from_lookup(
            |key| (key == "ORDER_ADMISSION_READ_CAPACITY").then(|| "abc".to_owned())
        )
        .is_err()
    );

    // The 601st read of one key in a window gives 429 rate_limited with Retry-After.
    let app = limited_app(Limits::from_lookup(|_| None).expect("defaults"));
    for _ in 0..600 {
        let (status, _, _) = call(&app, "GET", "/v1/orders", None).await;
        assert_eq!(status, StatusCode::OK);
    }
    let (status, headers, body) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    let retry: u64 = headers
        .get("retry-after")
        .expect("429 carries retry-after")
        .to_str()
        .expect("retry-after is text")
        .parse()
        .expect("retry-after is an integer");
    assert!(retry >= 1);
    assert_eq!(body_json(&body)["error"], "rate_limited");

    // The 121st write of one key gives 429; reads are a separate class and still answer.
    let app = limited_app(Limits::from_lookup(|_| None).expect("defaults"));
    for _ in 0..120 {
        let (status, _, _) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
        assert_eq!(status, StatusCode::CREATED);
    }
    let (status, headers, body) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert!(headers.get("retry-after").is_some());
    assert_eq!(body_json(&body)["error"], "rate_limited");
    let (status, _, _) = call(&app, "GET", "/v1/orders", None).await;
    assert_eq!(status, StatusCode::OK);

    // Keys are separate: two reads with key a pass, the third is limited, key b still passes.
    let app = limited_app(
        Limits::from_lookup(|key| (key == "ORDER_ADMISSION_READ_CAPACITY").then(|| "2".to_owned()))
            .expect("valid"),
    );
    for _ in 0..2 {
        let (status, _, _) = send(
            &app,
            "GET",
            "/v1/orders",
            &[("authorization", "Bearer a")],
            None,
        )
        .await;
        assert_eq!(status, StatusCode::OK);
    }
    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer a")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer b")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);

    // The env names override the defaults: the write capacity of 1.
    let app = limited_app(
        Limits::from_lookup(|key| {
            (key == "ORDER_ADMISSION_WRITE_CAPACITY").then(|| "1".to_owned())
        })
        .expect("valid"),
    );
    let (status, _, _) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    assert_eq!(status, StatusCode::CREATED);
    let (status, _, _) = call(&app, "POST", "/v1/orders", Some(valid_body())).await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);

    // The env names override the defaults: read capacity 1 and max keys 1.
    let app = limited_app(
        Limits::from_lookup(|key| match key {
            "ORDER_ADMISSION_MAX_KEYS" | "ORDER_ADMISSION_READ_CAPACITY" => Some("1".to_owned()),
            _ => None,
        })
        .expect("valid"),
    );
    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer a")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let (status, _, _) = send(
        &app,
        "GET",
        "/v1/orders",
        &[("authorization", "Bearer a")],
        None,
    )
    .await;
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);

    // A refill of zero is refused (core refuses a zero value).
    assert!(
        Limits::from_lookup(|key| (key == "ORDER_ADMISSION_REFILL_SECS").then(|| "0".to_owned()))
            .is_err()
    );
}

#[tokio::test]
async fn admission_off_when_the_limits_have_no_controller() {
    let app = limited_app(Limits {
        timeout: Duration::from_secs(30),
        admission: None,
    });
    for _ in 0..700 {
        let (status, _, _) = call(&app, "GET", "/v1/orders", None).await;
        assert_eq!(status, StatusCode::OK);
    }
}
