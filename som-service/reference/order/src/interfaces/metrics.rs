use std::sync::Arc;
use std::time::Instant;

use axum::extract::{MatchedPath, Request, State};
use axum::middleware::Next;
use axum::response::Response;
use metrics_prometheus::{Counter, Label, LabeledSample, Latency, SampleGroup, render_labeled};
use service_observability::MetricsProvider;

use crate::interfaces::routes::{COLLECTION_PATH, ITEM_PATH};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Operation {
    Create,
    Read,
    List,
    Replace,
    Delete,
}

impl Operation {
    pub const ALL: [Operation; 5] = [
        Operation::Create,
        Operation::Read,
        Operation::List,
        Operation::Replace,
        Operation::Delete,
    ];

    pub fn as_str(&self) -> &'static str {
        match self {
            Operation::Create => "create",
            Operation::Read => "read",
            Operation::List => "list",
            Operation::Replace => "replace",
            Operation::Delete => "delete",
        }
    }
}

/// The operation of a request, from its method and its matched route path.
pub fn operation_of(method: &axum::http::Method, matched_path: &str) -> Option<Operation> {
    match (method.as_str(), matched_path) {
        ("POST", COLLECTION_PATH) => Some(Operation::Create),
        ("GET", COLLECTION_PATH) => Some(Operation::List),
        ("GET", ITEM_PATH) => Some(Operation::Read),
        ("PUT", ITEM_PATH) => Some(Operation::Replace),
        ("DELETE", ITEM_PATH) => Some(Operation::Delete),
        _ => None,
    }
}

#[derive(Debug, Default)]
pub struct OperationStats {
    pub requests: Counter,
    pub errors: Counter,
    pub latency: Latency,
}

/// Per-operation counts and latency of the order API.
///
/// Families, one row per operation with the label `operation="<name>"`:
/// - `order_operation_requests_total`: calls of the operation;
/// - `order_operation_errors_total`: calls that answered status 400 or above
///   (a 401 counts, because the auth check is inside the count);
/// - `order_operation_latency_microseconds_sum` and
///   `order_operation_latency_microseconds_count`: the latency in microseconds
///   (`_count` is the number of calls that were timed).
///
/// A request with no matching operation (for example an unknown path) is not counted.
#[derive(Debug, Default)]
pub struct OperationMetrics {
    create: OperationStats,
    read: OperationStats,
    list: OperationStats,
    replace: OperationStats,
    delete: OperationStats,
}

impl OperationMetrics {
    pub fn new() -> OperationMetrics {
        OperationMetrics::default()
    }

    pub fn stats(&self, operation: Operation) -> &OperationStats {
        match operation {
            Operation::Create => &self.create,
            Operation::Read => &self.read,
            Operation::List => &self.list,
            Operation::Replace => &self.replace,
            Operation::Delete => &self.delete,
        }
    }
}

// core-gap: 28 (core has no per-operation middleware)
pub async fn record_operation(
    State(metrics): State<Arc<OperationMetrics>>,
    request: Request,
    next: Next,
) -> Response {
    let operation = request
        .extensions()
        .get::<MatchedPath>()
        .and_then(|path| operation_of(request.method(), path.as_str()));
    let start = Instant::now();
    let response = next.run(request).await;
    if let Some(operation) = operation {
        let stats = metrics.stats(operation);
        stats.requests.incr();
        let micros = u64::try_from(start.elapsed().as_micros()).unwrap_or(u64::MAX);
        stats.latency.observe(micros);
        if response.status().as_u16() >= 400 {
            stats.errors.incr();
        }
    }
    response
}

fn rows(
    metrics: &OperationMetrics,
    value: impl Fn(&OperationStats) -> u64,
) -> Vec<LabeledSample<'static>> {
    Operation::ALL
        .iter()
        .map(|operation| {
            LabeledSample::new(
                vec![Label::new("operation", operation.as_str())],
                value(metrics.stats(*operation)),
            )
        })
        .collect()
}

impl MetricsProvider for OperationMetrics {
    fn render_metrics(&self) -> String {
        let requests = rows(self, |stats| stats.requests.get());
        let errors = rows(self, |stats| stats.errors.get());
        let latency_sum = rows(self, |stats| stats.latency.sum().get());
        let latency_count = rows(self, |stats| stats.latency.count().get());
        let groups = [
            SampleGroup::new(
                "order_operation_requests_total",
                "counter",
                "Calls of each order operation.",
                &requests,
            ),
            SampleGroup::new(
                "order_operation_errors_total",
                "counter",
                "Calls of each order operation that answered status 400 or above.",
                &errors,
            ),
            SampleGroup::new(
                "order_operation_latency_microseconds_sum",
                "counter",
                "Total latency of each order operation in microseconds.",
                &latency_sum,
            ),
            SampleGroup::new(
                "order_operation_latency_microseconds_count",
                "counter",
                "Number of timed calls of each order operation.",
                &latency_count,
            ),
        ];
        render_labeled(&groups)
    }
}
