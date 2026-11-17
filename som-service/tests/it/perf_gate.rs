//! Performance gate: `/compile` and `/assemble` speed and memory, compared with `perf/baseline.json` (#207).
use serde_json::{Value, json};
use std::path::Path;
use std::time::{Duration, Instant};

const TASK_STATUS: &str = include_str!("../../contract/task-status.cnl");
const DEFER_FOUR: &str = include_str!("fixtures/defer-four.cnl");
const THRESHOLD_PERCENT: f64 = 20.0; // rps and p50
const P99_THRESHOLD_PERCENT: f64 = 50.0; // p99 is noisier
const PEAK_RSS_THRESHOLD_PERCENT: f64 = 20.0;
const SOAK_GROWTH_LIMIT_KB: u64 = 20480; // 20 MiB

struct Workload {
    per_fixture: usize,
    rounds: usize,
    warmup: usize,
    soak: usize,
}

const FULL: Workload = Workload {
    per_fixture: 200,
    rounds: 3,
    warmup: 200,
    soak: 3000,
};
const SMALL: Workload = Workload {
    per_fixture: 10,
    rounds: 1,
    warmup: 20,
    soak: 100,
};

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

/// Resident memory in kB from `/proc/self/status` (`VmRSS:` or `VmHWM:`); `None` when not Linux.
fn rss_kb(field: &str) -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|l| l.starts_with(field))?;
    line.split_whitespace().nth(1)?.parse().ok()
}

/// Hashes per second of SHA-256 over 64 KiB; the best of 3 runs of 100 ms.
fn calibration() -> f64 {
    use sha2::{Digest, Sha256};
    let buf = vec![0u8; 65536];
    let mut best = 0.0f64;
    for _ in 0..3 {
        let start = Instant::now();
        let mut hashes = 0u64;
        while start.elapsed() < Duration::from_millis(100) {
            let _ = Sha256::digest(&buf);
            hashes += 1;
        }
        best = best.max(hashes as f64 / start.elapsed().as_secs_f64());
    }
    best
}

/// Nearest rank: `sorted[ceil(p * n) - 1]`, `p` in (0, 1].
fn percentile(sorted: &[f64], p: f64) -> f64 {
    let rank = (p * sorted.len() as f64).ceil() as usize;
    sorted[rank - 1]
}

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

fn num(v: &Value) -> f64 {
    v.as_f64().unwrap_or_else(|| panic!("not a number: {v}"))
}

async fn timed_post(client: &reqwest::Client, url: &str, route: &str, body: &str) -> f64 {
    let started = Instant::now();
    let reply = client
        .post(format!("{url}/{route}"))
        .header("content-type", "application/json")
        .body(body.to_string())
        .send()
        .await
        .expect("send");
    let status = reply.status().as_u16();
    let _ = reply.bytes().await.expect("body");
    let ms = started.elapsed().as_secs_f64() * 1000.0;
    assert_eq!(status, 200, "{route} must answer 200");
    ms
}

/// Measures both routes against an in-process server, plus the memory of a soak run.
async fn measure(w: &Workload) -> Value {
    let cal = calibration();
    let url = start().await;
    let client = reqwest::Client::new();
    let compile_bodies = [
        json!({"format": som_service::model::CNL_FORMAT, "source": TASK_STATUS}).to_string(),
        json!({"format": som_service::model::CNL_FORMAT, "source": DEFER_FOUR}).to_string(),
    ];
    let mut assemble_bodies = Vec::new();
    for body in &compile_bodies {
        let reply = client
            .post(format!("{url}/compile"))
            .header("content-type", "application/json")
            .body(body.clone())
            .send()
            .await
            .expect("send");
        assert_eq!(reply.status().as_u16(), 200, "compile of the fixture");
        let reply: Value = reply.json().await.expect("json");
        assemble_bodies.push(json!({"bundle": reply["bundle"]}).to_string());
    }

    for i in 0..w.warmup {
        timed_post(&client, &url, "compile", &compile_bodies[i % 2]).await;
    }
    let before = rss_kb("VmRSS:");

    let mut routes = serde_json::Map::new();
    for (route, bodies) in [
        ("compile", &compile_bodies),
        (
            "assemble",
            &[assemble_bodies[0].clone(), assemble_bodies[1].clone()],
        ),
    ] {
        let mut best: Option<(f64, Vec<f64>)> = None;
        for _ in 0..w.rounds {
            let mut latencies = Vec::new();
            let started = Instant::now();
            for body in bodies.iter() {
                for _ in 0..w.per_fixture {
                    latencies.push(timed_post(&client, &url, route, body).await);
                }
            }
            let rps = latencies.len() as f64 / started.elapsed().as_secs_f64();
            if best.as_ref().is_none_or(|(b, _)| rps > *b) {
                latencies.sort_by(|a, b| a.partial_cmp(b).expect("finite latency"));
                best = Some((rps, latencies));
            }
        }
        let (rps, latencies) = best.expect("at least one round");
        routes.insert(
            route.to_string(),
            json!({
                "rps": round2(rps),
                "p50_ms": round2(percentile(&latencies, 0.50)),
                "p99_ms": round2(percentile(&latencies, 0.99)),
            }),
        );
    }

    // compile, compile, assemble, assemble
    let soak_requests = [
        ("compile", &compile_bodies[0]),
        ("compile", &compile_bodies[1]),
        ("assemble", &assemble_bodies[0]),
        ("assemble", &assemble_bodies[1]),
    ];
    for i in 0..w.soak {
        let (route, body) = soak_requests[i % 4];
        timed_post(&client, &url, route, body).await;
    }
    let after = rss_kb("VmRSS:");
    let soak_growth_kb = match (before, after) {
        (Some(b), Some(a)) => a.saturating_sub(b),
        _ => 0,
    };
    let peak_rss_kb = rss_kb("VmHWM:").unwrap_or(0);

    let machine_class = if std::env::var("GITHUB_ACTIONS").is_ok() {
        "ubuntu-24.04 GitHub-hosted runner, release build, concurrency 1"
    } else {
        "local machine, release build, concurrency 1"
    };
    json!({
        "machine_class": machine_class,
        "calibration_hashes_per_s": round2(cal),
        "compile": routes["compile"],
        "assemble": routes["assemble"],
        "peak_rss_kb": peak_rss_kb,
        "soak_growth_kb": soak_growth_kb,
    })
}

/// Err(messages) when `measured` is worse than `baseline` by more than the thresholds.
fn check(baseline: &Value, measured: &Value) -> Result<(), Vec<String>> {
    let scale =
        num(&measured["calibration_hashes_per_s"]) / num(&baseline["calibration_hashes_per_s"]);
    let slack = THRESHOLD_PERCENT / 100.0;
    let p99_slack = P99_THRESHOLD_PERCENT / 100.0;
    let mut errors = Vec::new();
    for route in ["compile", "assemble"] {
        let (base, got) = (&baseline[route], &measured[route]);
        let rps_limit = num(&base["rps"]) * scale * (1.0 - slack);
        let rps = num(&got["rps"]);
        if rps < rps_limit {
            errors.push(format!("{route}.rps {rps} is below {rps_limit:.2}"));
        }
        let p50_limit = num(&base["p50_ms"]) / scale * (1.0 + slack);
        let p50 = num(&got["p50_ms"]);
        if p50 > p50_limit {
            errors.push(format!("{route}.p50_ms {p50} is above {p50_limit:.2}"));
        }
        let p99_limit = num(&base["p99_ms"]) / scale * (1.0 + p99_slack);
        let p99 = num(&got["p99_ms"]);
        if p99 > p99_limit {
            errors.push(format!("{route}.p99_ms {p99} is above {p99_limit:.2}"));
        }
    }
    let base_peak = num(&baseline["peak_rss_kb"]);
    let peak = num(&measured["peak_rss_kb"]);
    if base_peak > 0.0 && peak > 0.0 {
        let limit = base_peak * (1.0 + PEAK_RSS_THRESHOLD_PERCENT / 100.0);
        if peak > limit {
            errors.push(format!("peak_rss_kb {peak} is above {limit:.0}"));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors)
    }
}

/// A baseline that is `factor` times faster: rps * factor, p50 and p99 / factor, both routes.
fn faster(baseline: &Value, factor: f64) -> Value {
    let mut out = baseline.clone();
    for route in ["compile", "assemble"] {
        out[route]["rps"] = json!(num(&baseline[route]["rps"]) * factor);
        out[route]["p50_ms"] = json!(num(&baseline[route]["p50_ms"]) / factor);
        out[route]["p99_ms"] = json!(num(&baseline[route]["p99_ms"]) / factor);
    }
    out
}

fn fixed_baseline() -> Value {
    json!({
        "machine_class": "test machine",
        "calibration_hashes_per_s": 100000.0,
        "compile": {"rps": 1000.0, "p50_ms": 1.0, "p99_ms": 2.0},
        "assemble": {"rps": 500.0, "p50_ms": 2.0, "p99_ms": 4.0},
        "peak_rss_kb": 50000,
        "soak_growth_kb": 100
    })
}

#[test]
fn percentile_nearest_rank() {
    let hundred: Vec<f64> = (1..=100).map(f64::from).collect();
    assert_eq!(percentile(&hundred, 0.50), 50.0);
    assert_eq!(percentile(&hundred, 0.99), 99.0);
    assert_eq!(percentile(&[5.0], 0.99), 5.0);
}

#[test]
fn check_accepts_identical_numbers() {
    let b = fixed_baseline();
    assert!(check(&b, &b).is_ok());
}

#[test]
fn check_accepts_baseline_10_percent_faster() {
    let b = fixed_baseline();
    assert!(check(&faster(&b, 1.1), &b).is_ok());
}

#[test]
fn check_rejects_baseline_30_percent_faster() {
    let b = fixed_baseline();
    let errors = check(&faster(&b, 1.3), &b).expect_err("a 30 percent faster baseline must fail");
    assert!(
        errors.iter().any(|e| e.contains("compile.rps")),
        "{errors:?}"
    );
    assert!(
        errors.iter().any(|e| e.contains("compile.p50_ms")),
        "{errors:?}"
    );
}

#[test]
fn check_rejects_peak_memory_growth() {
    let b = fixed_baseline();
    let mut measured = b.clone();
    measured["peak_rss_kb"] = json!(70000);
    let errors = check(&b, &measured).expect_err("40 percent more peak memory must fail");
    assert!(
        errors.iter().any(|e| e.contains("peak_rss_kb")),
        "{errors:?}"
    );
}

#[test]
fn check_scales_with_calibration() {
    let b = fixed_baseline();
    let mut measured = b.clone();
    measured["calibration_hashes_per_s"] = json!(50000.0);
    for route in ["compile", "assemble"] {
        let base = &b[route];
        measured[route]["rps"] = json!(num(&base["rps"]) / 2.0);
        measured[route]["p50_ms"] = json!(num(&base["p50_ms"]) * 2.0);
        measured[route]["p99_ms"] = json!(num(&base["p99_ms"]) * 2.0);
    }
    assert!(check(&b, &measured).is_ok(), "{:?}", check(&b, &measured));
}

#[tokio::test(flavor = "multi_thread")]
async fn perf_gate_within_threshold() {
    let record = std::env::var("SOM_PERF_RECORD").ok();
    let gate = std::env::var("SOM_PERF_GATE").is_ok_and(|v| v == "1");
    let w = if gate || record.is_some() {
        FULL
    } else {
        SMALL
    };
    let measured = measure(&w).await;

    for route in ["compile", "assemble"] {
        let r = &measured[route];
        assert!(num(&r["rps"]) > 0.0, "{route}.rps must be positive");
        assert!(
            num(&r["p50_ms"]) <= num(&r["p99_ms"]),
            "{route}: p50 must not exceed p99"
        );
    }
    let has_rss = rss_kb("VmRSS:").is_some();
    if (gate || record.is_some()) && !has_rss {
        panic!("gate and record modes need /proc/self/status");
    }
    if has_rss {
        let growth = measured["soak_growth_kb"].as_u64().expect("soak growth");
        assert!(
            growth < SOAK_GROWTH_LIMIT_KB,
            "memory grew {growth} kB in the soak run, limit {SOAK_GROWTH_LIMIT_KB} kB"
        );
    }

    if let Some(path) = record {
        let mut text = serde_json::to_string_pretty(&measured).expect("json");
        text.push('\n');
        std::fs::write(&path, text).expect("write the recorded baseline");
        return;
    }
    if gate {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("perf/baseline.json");
        let text = std::fs::read_to_string(&path).expect("read perf/baseline.json");
        let baseline: Value = serde_json::from_str(&text).expect("baseline json");
        if let Err(messages) = check(&baseline, &measured) {
            panic!(
                "{}\nupdate perf/baseline.json only with a reviewed reason",
                messages.join("\n")
            );
        }
    }
}

#[test]
fn performance_doc_matches_baseline() {
    let read = |rel: &str| {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
    };
    let baseline: Value = serde_json::from_str(&read("perf/baseline.json")).expect("baseline json");
    let doc = read("PERFORMANCE.md");

    assert!(
        num(&baseline["peak_rss_kb"]) > 0.0,
        "a placeholder baseline is not accepted"
    );
    assert!(
        num(&baseline["compile"]["rps"]) > 0.0,
        "a placeholder baseline is not accepted"
    );

    let peak = baseline["peak_rss_kb"].as_u64().expect("peak_rss_kb");
    let growth = baseline["soak_growth_kb"].as_u64().expect("soak_growth_kb");
    let mut expected = Vec::new();
    for (route, label) in [("compile", "POST /compile"), ("assemble", "POST /assemble")] {
        expected.push(format!(
            "| `{label}` | {:.2} | {:.2} | {:.2} |",
            num(&baseline[route]["rps"]),
            num(&baseline[route]["p50_ms"]),
            num(&baseline[route]["p99_ms"])
        ));
    }
    expected.push(format!("Peak resident memory: {peak} kB"));
    expected.push(format!(
        "Memory growth after the soak run: {growth} kB (limit {SOAK_GROWTH_LIMIT_KB} kB)"
    ));
    expected.push(format!(
        "Regression threshold: {} percent",
        THRESHOLD_PERCENT as u32
    ));
    expected.push(
        baseline["machine_class"]
            .as_str()
            .expect("machine_class")
            .to_string(),
    );
    for word in ["Throughput", "p50", "p99", "Memory"] {
        expected.push(word.to_string());
    }
    for text in &expected {
        assert!(doc.contains(text.as_str()), "PERFORMANCE.md lacks: {text}");
    }
}
