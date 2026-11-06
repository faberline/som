use anyhow::{ensure, Result};
use chrono::{DateTime, Utc};
use defer_access::AuthConfig;
use defer_dispatch::HttpDispatcher;
use defer_queue::server::{router, AppState};
use defer_replication::DeferRaft;
use defer_shared_kernel::{CreateTask, DeferScheduler, QueuePolicy};
use raft_runtime::Membership;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn task(id: &str) -> CreateTask {
    serde_json::from_value(serde_json::json!({
        "task_id":id,"target":{"url":"http://127.0.0.1:1/no-dispatch"},
        "schedule_at":"2026-11-06T00:00:00Z","max_attempts":1
    })).unwrap()
}

#[tokio::main]
async fn main() -> Result<()> {
    let output = std::env::args().nth(1).expect("ready output path");
    let dir = tempfile::tempdir()?;
    let raft = Arc::new(DeferRaft::spawn(
        Arc::new(Mutex::new(DeferScheduler::new())), &dir.path().join("raft"), 0,
        Membership { voters: vec![0], learners: vec![] }, HashMap::new(),
        DeferRaft::host_config(8),
    )?);
    let deadline = Instant::now() + Duration::from_secs(8);
    while !raft.is_leader().await {
        ensure!(Instant::now() < deadline, "single-node leader timeout");
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    let now: DateTime<Utc> = "2026-11-06T00:00:00Z".parse()?;
    for queue in ["scheduled", "jobs", "succeeded", "dead-lettered", "canceled"] {
        raft.configure_queue(queue.into(), QueuePolicy::default()).await?;
        raft.create_task(queue.into(), task("t")).await?;
        if queue == "canceled" {
            ensure!(raft.cancel(queue.into(), "t".into()).await?, "cancel failed");
        } else if queue != "scheduled" {
            let leases = raft.lease_due(queue.into(), now, 1).await?;
            ensure!(leases.len() == 1, "fixture lease missing");
            let lease = &leases[0];
            if queue == "succeeded" {
                ensure!(raft.ack(queue.into(), lease.attempt_id.clone(), lease.epoch, now).await?, "ack failed");
            } else if queue == "dead-lettered" {
                ensure!(raft.nack(queue.into(), lease.attempt_id.clone(), lease.epoch, now).await?.is_some(), "nack failed");
            }
        }
    }
    let path_values = ["simple", "team/a", "x%20", "hello world", "a?b", "a#b", "雪☃", "", ".", "..", "%2e", "%2F"];
    raft.configure_queue("paths".into(), QueuePolicy::default()).await?;
    for value in path_values {
        raft.create_task("paths".into(), task(value)).await?;
        raft.configure_queue(value.into(), QueuePolicy::default()).await?;
        raft.create_task(value.into(), task("path-task")).await?;
    }
    let registry = serde_json::json!({
        "reader":{"subject":"reader","roles":{"*":"read"}},
        "writer":{"subject":"writer","roles":{"*":"write"}},
        "admin":{"subject":"admin","roles":{"*":"admin"}},
        "queue-reader":{"subject":"queue-reader","roles":{"jobs":"read"}},
        "denied":{"subject":"s","roles":{"other":"read"}}
    }).to_string();
    let auth = AuthConfig::resolve("required", None, Some(&registry))?;
    let state = AppState::new(raft.clone(), HttpDispatcher::new(Duration::from_secs(2), None)?, auth, 8*1024*1024);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let base_url = format!("http://{}", listener.local_addr()?);
    let server = tokio::spawn(service_http::serve(listener, router(state), std::future::pending()));
    let open_state = AppState::new(raft.clone(), HttpDispatcher::new(Duration::from_secs(2), None)?, AuthConfig::open(), 8*1024*1024);
    let open_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let open_base_url = format!("http://{}", open_listener.local_addr()?);
    let open_server = tokio::spawn(service_http::serve(open_listener, router(open_state), std::future::pending()));
    let ready_tmp = format!("{output}.tmp");
    std::fs::write(&ready_tmp, serde_json::to_vec_pretty(&serde_json::json!({"base_url":base_url,"open_base_url":open_base_url,"applied_index":raft.applied_index()}))?)?;
    std::fs::rename(ready_tmp, output)?;
    tokio::signal::ctrl_c().await?;
    server.abort(); open_server.abort(); raft.shutdown().await?;
    Ok(())
}
