# Performance

This document gives the measured speed and memory of `som-service`. The gate in
`tests/it/perf_gate.rs` compares each CI run with `perf/baseline.json` (#207).

## Throughput and latency

Machine class: local machine, release build, concurrency 1

| Route | Throughput (requests per second) | p50 (ms) | p99 (ms) |
|---|---|---|---|
| `POST /compile` | 1593.63 | 0.64 | 0.86 |
| `POST /assemble` | 925.22 | 1.31 | 1.84 |

The workload is `contract/task-status.cnl` and `tests/it/fixtures/defer-four.cnl`, one request at a time against an in-process `serve`; the best of 3 rounds of 400 requests per route.

## Memory

Peak resident memory: 13728 kB

Memory growth after the soak run: 1952 kB (limit 20480 kB)

The soak run sends 3000 requests after a warm-up of 200. The growth is the resident memory after the soak minus the resident memory before it.

## Regression gate

Regression threshold: 20 percent

Throughput must not fall, and p50 must not rise, by more than 20 percent. p99 may rise by 50 percent. Peak memory may rise by 20 percent. The gate scales the baseline with a hash-speed calibration, so a faster or slower runner does not fail it on its own.

## Run it

```sh
SOM_PERF_GATE=1 cargo test --release --locked --test it perf_gate -- --test-threads=1
```

To record a new baseline (only with a reviewed reason):

```sh
SOM_PERF_RECORD="$PWD/perf/baseline.json" cargo test --release --locked --test it perf_gate_within_threshold -- --test-threads=1
```
