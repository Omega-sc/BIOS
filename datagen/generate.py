"""Telemetry generator for Track 09 Autonomous Incident Investigation.

Generates multi-service traces (gateway -> auth -> db-pool), unaligned logs, and metrics
with simulated clock skew (+12s on auth-service), 15% random log drop, and ~3% orphan spans.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from contracts.constants import POOL_MAX, SKEW_MS, T0_MS  # noqa: E402


def generate_telemetry(
    seed: int = 42,
    window_s: int = 480,
    leak_onset_s: int = 120,
    pool_hit_s: int = 210,
    first_504_s: int = 215,
    cpu_spike_s: int = 245,
    scenario: str = "pool_leak",
    log_drop_prob: float = 0.15,
    span_drop_prob: float = 0.03,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate telemetry records (spans, logs, metrics) and paired ground truth metadata."""
    rng = random.Random(seed)

    records: list[dict[str, Any]] = []
    auth_skew = SKEW_MS.get("auth-service", 12_000)

    # 1. Generate Metrics every 2.5s across window
    # Timestamps in ms relative to T0_MS
    active_conns = 2.0
    queries_in_flight = 2.0
    wait_queue = 0.0
    auth_latency = 42.0
    err_rate = 0.0
    cpu_pct = 14.0

    step_s = 2.5
    current_s = 0.0
    metrics_generated = 0

    while current_s <= window_s:
        t_ms = T0_MS + int(current_s * 1000)
        auth_t_ms = t_ms + auth_skew

        # Dynamic simulation of metrics based on timeline
        if current_s < leak_onset_s:
            active_conns = max(1.0, min(5.0, active_conns + rng.uniform(-0.5, 0.5)))
            wait_queue = 0.0
            auth_latency = max(35.0, min(55.0, auth_latency + rng.uniform(-3.0, 3.0)))
            err_rate = 0.0
            cpu_pct = max(10.0, min(18.0, cpu_pct + rng.uniform(-1.0, 1.0)))
        elif current_s < pool_hit_s:
            # Leaking active connections up to POOL_MAX (20)
            progress = (current_s - leak_onset_s) / (pool_hit_s - leak_onset_s)
            target_conns = 3.0 + progress * (POOL_MAX - 3.0)
            active_conns = min(float(POOL_MAX), target_conns + rng.uniform(-0.3, 0.3))
            wait_queue = 0.0 if active_conns < 18 else max(0.0, (active_conns - 18) * 1.5)
            auth_latency = 45.0 + progress * 60.0 + rng.uniform(-5.0, 5.0)
            err_rate = 0.0
            cpu_pct = max(11.0, min(19.0, cpu_pct + rng.uniform(-1.0, 1.0)))
        elif current_s < first_504_s:
            active_conns = float(POOL_MAX)
            wait_queue = max(1.0, wait_queue + rng.uniform(0.5, 1.5))
            auth_latency = max(200.0, auth_latency + rng.uniform(20.0, 40.0))
            err_rate = 0.0
            cpu_pct = max(12.0, min(20.0, cpu_pct + rng.uniform(-1.0, 1.0)))
        elif current_s < cpu_spike_s:
            active_conns = float(POOL_MAX)
            wait_queue = min(35.0, wait_queue + rng.uniform(0.8, 2.0))
            auth_latency = min(5200.0, auth_latency + rng.uniform(80.0, 180.0))
            # 504 errors start
            err_rate = min(0.92, max(0.40, err_rate + rng.uniform(0.05, 0.15)))
            cpu_pct = max(13.0, min(22.0, cpu_pct + rng.uniform(-1.0, 1.0)))
        else:
            # Post cpu_spike_s: client retry storm causes frontend CPU to spike
            active_conns = float(POOL_MAX)
            wait_queue = min(40.0, wait_queue + rng.uniform(-0.5, 0.5))
            auth_latency = max(4800.0, min(5500.0, auth_latency + rng.uniform(-40.0, 40.0)))
            err_rate = max(0.85, min(0.98, err_rate + rng.uniform(-0.02, 0.02)))
            # Spike CPU to ~90%+
            cpu_pct = min(98.0, max(85.0, cpu_pct + rng.uniform(5.0, 15.0)))

        # Emit metric points
        records.append(
            {
                "type": "metric",
                "service": "db-pool",
                "metric": "db_pool.active_connections",
                "ts_ms": t_ms,
                "value": round(active_conns, 2),
            }
        )
        records.append(
            {
                "type": "metric",
                "service": "db-pool",
                "metric": "db_pool.queries_in_flight",
                "ts_ms": t_ms,
                "value": round(queries_in_flight + rng.uniform(-0.5, 0.5), 2),
            }
        )
        records.append(
            {
                "type": "metric",
                "service": "db-pool",
                "metric": "db_pool.wait_queue_len",
                "ts_ms": t_ms,
                "value": round(wait_queue, 2),
            }
        )
        records.append(
            {
                "type": "metric",
                "service": "auth-service",
                "metric": "auth.latency_p99",
                "ts_ms": auth_t_ms,
                "value": round(auth_latency, 2),
            }
        )
        records.append(
            {
                "type": "metric",
                "service": "gateway",
                "metric": "gateway.error_rate_5xx",
                "ts_ms": t_ms,
                "value": round(err_rate, 4),
            }
        )
        records.append(
            {
                "type": "metric",
                "service": "frontend",
                "metric": "frontend.cpu_pct",
                "ts_ms": t_ms,
                "value": round(cpu_pct, 2),
            }
        )
        metrics_generated += 6
        current_s += step_s

    # 2. Generate Traces & Spans & Correlated Logs
    # Approximately 800 traces over 480 seconds = ~1.67 traces/sec
    trace_count = 800
    trace_intervals = [rng.uniform(0.2, 1.0) for _ in range(trace_count)]
    total_interval_time = sum(trace_intervals)
    scaling = window_s / total_interval_time

    cur_time = 0.0
    raw_logs: list[dict[str, Any]] = []

    for i in range(trace_count):
        cur_time += trace_intervals[i] * scaling
        if cur_time > window_s + 1e-5:
            break

        t_req_ms = T0_MS + int(cur_time * 1000)
        trace_id = f"tr-{i:04d}"
        span_gw_id = f"sp-gw-{i:04d}"
        span_auth_id = f"sp-auth-{i:04d}"
        span_db_id = f"sp-db-{i:04d}"

        # Determine outcome based on phase
        is_leaking_request = False
        if cur_time < first_504_s:
            # Before 504s: baseline and leak ramp
            if cur_time >= leak_onset_s:
                # 15% of requests during leak ramp trigger the leak bug
                is_leaking_request = rng.random() < 0.15

            gw_duration = int(rng.uniform(40, 75))
            auth_duration = int(rng.uniform(25, 45))
            db_duration = int(rng.uniform(10, 20))
            gw_status = 200
            auth_status = 200
            db_status = 200
        else:
            # After 504 onset: pool exhausted
            is_leaking_request = False
            # 85% fail with 504 timeout; 15% might squeak by if queue briefly yields
            if rng.random() < 0.88:
                gw_duration = 3000  # Gateway timeout threshold
                auth_duration = 5000  # Auth pool acquire timeout
                db_duration = 5000
                gw_status = 504
                auth_status = 500
                db_status = 500
            else:
                gw_duration = int(rng.uniform(150, 400))
                auth_duration = int(rng.uniform(120, 350))
                db_duration = int(rng.uniform(60, 200))
                gw_status = 200
                auth_status = 200
                db_status = 200

        # Spans
        # Gateway span
        gw_span = {
            "type": "span",
            "span_id": span_gw_id,
            "trace_id": trace_id,
            "parent_span_id": None,
            "service": "gateway",
            "name": "GET /subsidy/claim",
            "start_ms": t_req_ms,
            "end_ms": t_req_ms + gw_duration,
            "status_code": gw_status,
            "attrs": {"http.method": "GET", "http.status_code": gw_status},
        }

        # Auth span (emitted with +12s clock skew)
        auth_start_true = t_req_ms + 2
        auth_span = {
            "type": "span",
            "span_id": span_auth_id,
            "trace_id": trace_id,
            "parent_span_id": span_gw_id,
            "service": "auth-service",
            "name": "authenticate_user",
            "start_ms": auth_start_true + auth_skew,
            "end_ms": auth_start_true + auth_duration + auth_skew,
            "status_code": auth_status,
            "attrs": {
                "rpc.service": "auth",
                "error": "PoolTimeout" if auth_status == 500 else None,
            },
        }

        # DB span
        db_start_true = auth_start_true + 5
        db_span = {
            "type": "span",
            "span_id": span_db_id,
            "trace_id": trace_id,
            "parent_span_id": span_auth_id,
            "service": "db-pool",
            "name": "acquire_connection",
            "start_ms": db_start_true,
            "end_ms": db_start_true + db_duration,
            "status_code": db_status,
            "attrs": {
                "pool.size": POOL_MAX,
                "error": "TimeoutExpired" if db_status == 500 else None,
            },
        }

        # Drop spans randomly (~3% orphan spans)
        if rng.random() > span_drop_prob:
            records.append(gw_span)
        if rng.random() > span_drop_prob:
            records.append(auth_span)
        if rng.random() > span_drop_prob:
            records.append(db_span)

        # Logs generated for this trace
        # Gateway log
        raw_logs.append(
            {
                "type": "log",
                "log_id": f"log-gw-{i:04d}",
                "trace_id": trace_id,
                "span_id": span_gw_id,
                "service": "gateway",
                "ts_ms": t_req_ms + 1,
                "level": "INFO" if gw_status == 200 else "ERROR",
                "message": f"Processed claim request status={gw_status}"
                if gw_status == 200
                else "Upstream auth-service timed out after 3000ms, returning 504",
            }
        )

        # Auth log (skewed ts_ms)
        if is_leaking_request:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-auth-leak-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_auth_id,
                    "service": "auth-service",
                    "ts_ms": auth_start_true + auth_skew + 10,
                    "level": "ERROR",
                    "message": "Token validator error path: unhandled exception skipped release()",
                }
            )
        elif auth_status == 500:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-auth-err-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_auth_id,
                    "service": "auth-service",
                    "ts_ms": auth_start_true + auth_skew + 4900,
                    "level": "ERROR",
                    "message": "Failed to acquire DB connection from pool within 5000ms",
                }
            )
        else:
            if rng.random() < 0.42:
                raw_logs.append(
                    {
                        "type": "log",
                        "log_id": f"log-auth-ok-{i:04d}",
                        "trace_id": trace_id,
                        "span_id": span_auth_id,
                        "service": "auth-service",
                        "ts_ms": auth_start_true + auth_skew + 15,
                        "level": "INFO",
                        "message": "User token validated successfully",
                    }
                )

        # DB log
        if db_status == 500:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-db-err-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_db_id,
                    "service": "db-pool",
                    "ts_ms": db_start_true + 4950,
                    "level": "ERROR",
                    "message": f"Connection pool exhausted (active=20/{POOL_MAX}), checkout request queued and timed out",
                }
            )

        # Frontend retry log if post-504
        if cur_time >= cpu_spike_s and gw_status == 504 and rng.random() < 0.50:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-fe-{i:04d}",
                    "trace_id": None,
                    "span_id": None,
                    "service": "frontend",
                    "ts_ms": t_req_ms + 3050,
                    "level": "WARN",
                    "message": "Client encountered 504 timeout; retrying with exponential backoff",
                }
            )

    # Apply 15% random log drop rate
    for log in raw_logs:
        if rng.random() > log_drop_prob:
            records.append(log)

    # Sort records chronologically by unaligned ts_ms / start_ms
    def get_sort_key(item: dict[str, Any]) -> int:
        if item["type"] == "span":
            return item["start_ms"]
        elif item["type"] == "log":
            return item["ts_ms"]
        elif item["type"] == "metric":
            return item["ts_ms"]
        return 0

    records.sort(key=get_sort_key)
    traces_generated = len(set(r["trace_id"] for r in records if r.get("trace_id")))

    ground_truth = {
        "scenario": scenario,
        "seed": seed,
        "total_records": len(records),
        "total_traces": traces_generated,
        "raw_logs_before_drop": len(raw_logs),
        "root_cause_service": "auth-service",
        "root_cause_mechanism": "connection_leak_in_error_path",
        "failing_resource": "db-pool",
        "red_herring_metric": "frontend.cpu_pct",
        "red_herring_onset_ms": T0_MS + int(cpu_spike_s * 1000),
        "true_leak_onset_ms": T0_MS + int(leak_onset_s * 1000),
        "pool_exhausted_onset_ms": T0_MS + int(pool_hit_s * 1000),
        "first_504_onset_ms": T0_MS + int(first_504_s * 1000),
        "clock_skews_ms": {"auth-service": auth_skew, "gateway": 0, "db-pool": 0, "frontend": 0},
        "log_drop_rate": log_drop_prob,
        "span_drop_rate": span_drop_prob,
    }

    return records, ground_truth


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic telemetry dataset")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--output", type=str, default="data/telemetry.jsonl", help="Output telemetry JSONL"
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default="data/ground_truth.json",
        help="Output ground truth JSON",
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    gt_path = Path(args.ground_truth)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.parent.mkdir(parents=True, exist_ok=True)

    records, ground_truth = generate_telemetry(seed=args.seed)

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    with open(gt_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ground_truth, f, indent=2)

    version_path = out_path.parent / "VERSION"
    with open(version_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("1\n")

    print(f"Generated {len(records)} records saved to {out_path}")
    print(f"Ground truth saved to {gt_path}")
    print(f"Dataset version saved to {version_path}")


if __name__ == "__main__":
    main()
