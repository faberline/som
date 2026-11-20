use std::time::Duration;

use server_lifecycle::LifecycleController;
use service_http::LifecycleShutdownTrigger;

use crate::interfaces::limits::SHUTDOWN_GRACE_SECS;

/// Serve `app` on `listener` until `lifecycle` is shut down (decision 30: graceful shutdown).
///
/// A binary calls `shutdown_trigger(lifecycle.clone()).trigger("signal", "...")` from
/// `service_http::shutdown_on_signal` or `run_signal_bridge`, and awaits `serve`.
pub async fn serve(
    listener: tokio::net::TcpListener,
    app: axum::Router,
    lifecycle: LifecycleController,
) {
    service_http::serve_with_lifecycle(
        listener,
        app,
        service_http::HttpServerOptions::default(),
        lifecycle,
    )
    .await;
}

/// The shutdown trigger with the standard's grace (10 seconds, decision 30) and 1 second reserved.
pub fn shutdown_trigger(lifecycle: LifecycleController) -> LifecycleShutdownTrigger {
    LifecycleShutdownTrigger::new(
        lifecycle,
        Duration::from_secs(SHUTDOWN_GRACE_SECS),
        Duration::from_secs(1),
    )
    .unwrap_or_else(|error| panic!("the shutdown grace is valid: {error}"))
}
