//! The HTTP service uses the core `service-http` error envelope and probe routes (#173).
use serde_json::Value;
use service_http::ErrorEnvelope;

async fn start() -> String {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("bind");
    let url = format!("http://{}", listener.local_addr().expect("addr"));
    tokio::spawn(async move {
        axum::serve(listener, som_service::server::router())
            .await
            .expect("serve");
    });
    url
}

#[tokio::test]
async fn service_errors_use_core_envelope() {
    let url = start().await;
    let reply = reqwest::Client::new()
        .post(format!("{url}/compile"))
        .header("content-type", "application/json")
        .body("{")
        .send()
        .await
        .expect("send");
    assert_eq!(reply.status().as_u16(), 400);
    let bytes = reply.bytes().await.expect("body");
    let envelope: ErrorEnvelope = serde_json::from_slice(&bytes).expect("core ErrorEnvelope");
    assert_eq!(envelope.error, "invalid_json");
    assert!(!envelope.message.is_empty());
    let body: Value = serde_json::from_slice(&bytes).expect("json");
    assert_eq!(body["status"], "error");
    assert_eq!(body["code"], "INVALID_JSON");
    assert!(body["diagnostics"].is_array());
}

#[tokio::test]
async fn probe_routes_come_from_core() {
    let url = start().await;
    for path in ["/healthz", "/readyz", "/metrics", "/openapi.json", "/docs"] {
        let reply = reqwest::get(format!("{url}{path}")).await.expect("get");
        assert_eq!(reply.status().as_u16(), 200, "{path}");
    }
    let health: Value = reqwest::get(format!("{url}/health"))
        .await
        .expect("get")
        .json()
        .await
        .expect("json");
    assert_eq!(health["compiler"], "som-service.v1");
}
