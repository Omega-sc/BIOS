"""Scenario generation module for Track 09 Autonomous Incident Investigation.

Generates 4 canonical scenarios across multiple seeds:
1. pool_leak: auth-service leaks DB connections, exhausting pool, gateway 504s; frontend CPU is red herring.
2. slow_db: db-pool queries degrade in latency, exhausting connections; frontend CPU is red herring.
3. retry_storm_origin: frontend client flood / aggressive retry storm originates overload; frontend CPU rises FIRST.
4. gateway_timeout_misconfig: gateway timeout misconfigured to aggressive value; 504s occur while db and auth are healthy.
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

from contracts.constants import POOL_MAX, T0_MS  # noqa: E402

SCENARIOS = [
    "pool_leak",
    "slow_db",
    "retry_storm_origin",
    "gateway_timeout_misconfig",
]

DEFAULT_SEEDS = [101, 102, 103, 104, 105]


def generate_scenario_telemetry(
    scenario_name: str,
    seed: int = 101,
    window_s: int = 400,
    onset_s: int = 120,
    auth_skew_ms: int = 12_000,
    log_drop_prob: float = 0.15,
    span_drop_prob: float = 0.03,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate telemetry and paired ground truth for a given scenario and seed."""
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []

    # Timeline markers
    t0 = T0_MS
    step_s = 3.0
    current_s = 0.0

    # Metric tracking
    active_conns = 3.0
    queries_in_flight = 2.0
    wait_queue = 0.0
    auth_latency = 45.0
    err_rate = 0.0
    cpu_pct = 15.0

    # Scenario-specific state
    while current_s <= window_s:
        t_ms = t0 + int(current_s * 1000)
        auth_t_ms = t_ms + auth_skew_ms

        if current_s < onset_s:
            # Baseline for all scenarios
            active_conns = max(1.0, min(5.0, active_conns + rng.uniform(-0.5, 0.5)))
            queries_in_flight = max(1.0, min(4.0, queries_in_flight + rng.uniform(-0.3, 0.3)))
            wait_queue = 0.0
            auth_latency = max(35.0, min(55.0, auth_latency + rng.uniform(-3.0, 3.0)))
            err_rate = 0.0
            cpu_pct = max(12.0, min(18.0, cpu_pct + rng.uniform(-1.0, 1.0)))
        else:
            # Incident phase
            dt = current_s - onset_s
            if scenario_name == "pool_leak":
                # Active connections climb to POOL_MAX over 70s, then 504s, then CPU spike
                active_conns = min(float(POOL_MAX), 3.0 + (dt / 70.0) * (POOL_MAX - 3.0))
                wait_queue = 0.0 if active_conns < 18 else max(0.0, (active_conns - 18) * 2.0)
                if active_conns >= float(POOL_MAX):
                    auth_latency = min(5000.0, 100.0 + (dt - 70.0) * 150.0)
                    err_rate = min(0.95, max(0.1, (dt - 75.0) * 0.04)) if dt >= 75.0 else 0.0
                    cpu_pct = (
                        min(96.0, 20.0 + (dt - 100.0) * 2.5 + rng.uniform(5.0, 10.0))
                        if dt >= 100.0
                        else 15.0 + rng.uniform(-2.0, 2.0)
                    )
                else:
                    err_rate = 0.0
                    cpu_pct = 15.0 + rng.uniform(-2.0, 2.0)

            elif scenario_name == "slow_db":
                # Slow queries cause queries_in_flight to climb first, then pool saturates, then 504
                queries_in_flight = min(18.0, 2.0 + (dt / 50.0) * 16.0)
                active_conns = min(float(POOL_MAX), 3.0 + (dt / 60.0) * (POOL_MAX - 3.0))
                wait_queue = max(0.0, (dt - 60.0) * 1.5) if dt >= 60.0 else 0.0
                auth_latency = min(5200.0, 50.0 + (dt / 60.0) * 5000.0)
                err_rate = min(0.92, (dt - 65.0) * 0.05) if dt >= 65.0 else 0.0
                cpu_pct = (
                    min(92.0, 18.0 + (dt - 90.0) * 2.0)
                    if dt >= 90.0
                    else 16.0 + rng.uniform(-2.0, 2.0)
                )

            elif scenario_name == "retry_storm_origin":
                # Frontend CPU spikes FIRST, then gateway error rate, pool remains partly utilized
                cpu_pct = min(98.0, 20.0 + dt * 2.5 + rng.uniform(5.0, 10.0))
                err_rate = min(0.85, dt * 0.03)
                active_conns = min(15.0, 3.0 + dt * 0.2)
                wait_queue = 0.0
                auth_latency = min(1200.0, 45.0 + dt * 20.0)

            elif scenario_name == "gateway_timeout_misconfig":
                # 504 errors spike immediately on gateway, but db-pool and auth remain healthy!
                err_rate = min(0.95, 0.80 + rng.uniform(0.0, 0.15))
                active_conns = max(1.0, min(4.0, active_conns + rng.uniform(-0.3, 0.3)))
                wait_queue = 0.0
                auth_latency = max(35.0, min(50.0, auth_latency + rng.uniform(-2.0, 2.0)))
                cpu_pct = max(12.0, min(22.0, cpu_pct + rng.uniform(-1.0, 1.0)))

        # Emit metrics
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
                "value": round(queries_in_flight, 2),
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
        current_s += step_s

    # Generate Spans and Logs (~500 traces per scenario)
    trace_count = 500
    cur_t = 0.0
    scaling = window_s / trace_count
    raw_logs = []

    for i in range(trace_count):
        cur_t += scaling
        if cur_t > window_s + 1e-5:
            break
        t_req_ms = t0 + int(cur_t * 1000)
        trace_id = f"tr-{scenario_name[:2]}-{seed}-{i:04d}"
        span_gw_id = f"sp-gw-{scenario_name[:2]}-{seed}-{i:04d}"
        span_auth_id = f"sp-auth-{scenario_name[:2]}-{seed}-{i:04d}"
        span_db_id = f"sp-db-{scenario_name[:2]}-{seed}-{i:04d}"

        # Status and durations
        is_error = False
        if cur_t >= onset_s:
            dt = cur_t - onset_s
            if scenario_name == "pool_leak" and dt >= 75.0:
                is_error = rng.random() < 0.85
            elif scenario_name == "slow_db" and dt >= 65.0:
                is_error = rng.random() < 0.85
            elif scenario_name == "retry_storm_origin" and dt >= 20.0:
                is_error = rng.random() < 0.70
            elif scenario_name == "gateway_timeout_misconfig":
                is_error = rng.random() < 0.85

        if is_error:
            if scenario_name == "gateway_timeout_misconfig":
                gw_status = 504
                gw_dur = 50  # Gateway misconfigured timeout at 50ms
                auth_status = 200  # Auth was still processing normally!
                auth_dur = 45
                db_status = 200
                db_dur = 15
            else:
                gw_status = 504
                gw_dur = 3000
                auth_status = 500
                auth_dur = 5000
                db_status = 500
                db_dur = 5000
        else:
            gw_status = 200
            gw_dur = int(rng.uniform(40, 80))
            auth_status = 200
            auth_dur = int(rng.uniform(25, 50))
            db_status = 200
            db_dur = int(rng.uniform(10, 25))

        # Build Spans
        gw_span = {
            "type": "span",
            "span_id": span_gw_id,
            "trace_id": trace_id,
            "parent_span_id": None,
            "service": "gateway",
            "name": "GET /api/resource",
            "start_ms": t_req_ms,
            "end_ms": t_req_ms + gw_dur,
            "status_code": gw_status,
            "attrs": {"http.status_code": gw_status},
        }

        auth_start_true = t_req_ms + 2
        auth_span = {
            "type": "span",
            "span_id": span_auth_id,
            "trace_id": trace_id,
            "parent_span_id": span_gw_id,
            "service": "auth-service",
            "name": "verify_auth",
            "start_ms": auth_start_true + auth_skew_ms,
            "end_ms": auth_start_true + auth_dur + auth_skew_ms,
            "status_code": auth_status,
            "attrs": {"error": "PoolTimeout" if auth_status == 500 else None},
        }

        db_start_true = auth_start_true + 5
        db_span = {
            "type": "span",
            "span_id": span_db_id,
            "trace_id": trace_id,
            "parent_span_id": span_auth_id,
            "service": "db-pool",
            "name": "get_connection",
            "start_ms": db_start_true,
            "end_ms": db_start_true + db_dur,
            "status_code": db_status,
            "attrs": {"error": "PoolTimeout" if db_status == 500 else None},
        }

        # Drop spans (~3% orphan drop)
        if rng.random() > span_drop_prob:
            records.append(gw_span)
        if rng.random() > span_drop_prob:
            records.append(auth_span)
        if rng.random() > span_drop_prob:
            records.append(db_span)

        # Build logs
        raw_logs.append(
            {
                "type": "log",
                "log_id": f"log-gw-{scenario_name[:2]}-{seed}-{i:04d}",
                "trace_id": trace_id,
                "span_id": span_gw_id,
                "service": "gateway",
                "ts_ms": t_req_ms + 1,
                "level": "INFO" if gw_status == 200 else "ERROR",
                "message": f"Gateway response {gw_status}",
            }
        )

        if scenario_name == "pool_leak" and cur_t >= onset_s and rng.random() < 0.12:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-auth-leak-{seed}-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_auth_id,
                    "service": "auth-service",
                    "ts_ms": auth_start_true + auth_skew_ms + 10,
                    "level": "ERROR",
                    "message": "Token validator error path: unhandled exception skipped release()",
                }
            )
        elif scenario_name == "slow_db" and cur_t >= onset_s and rng.random() < 0.15:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-db-slow-{seed}-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_db_id,
                    "service": "db-pool",
                    "ts_ms": db_start_true + 15,
                    "level": "WARN",
                    "message": "Slow table lock wait detected on users table (>2000ms)",
                }
            )
        elif scenario_name == "retry_storm_origin" and cur_t >= onset_s and rng.random() < 0.25:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-fe-storm-{seed}-{i:04d}",
                    "trace_id": None,
                    "span_id": None,
                    "service": "frontend",
                    "ts_ms": t_req_ms + 2,
                    "level": "WARN",
                    "message": "Client retry storm: rapid burst without exponential jitter",
                }
            )
        elif scenario_name == "gateway_timeout_misconfig" and is_error and rng.random() < 0.25:
            raw_logs.append(
                {
                    "type": "log",
                    "log_id": f"log-gw-cfg-{seed}-{i:04d}",
                    "trace_id": trace_id,
                    "span_id": span_gw_id,
                    "service": "gateway",
                    "ts_ms": t_req_ms + 52,
                    "level": "ERROR",
                    "message": "Gateway abort: request exceeded configured timeout of 50ms",
                }
            )

    # Apply log drop rate
    for log in raw_logs:
        if rng.random() > log_drop_prob:
            records.append(log)

    def get_sort_key(item: dict[str, Any]) -> int:
        return item["start_ms"] if item["type"] == "span" else item["ts_ms"]

    records.sort(key=get_sort_key)

    # Define ground truth per scenario
    gt_map = {
        "pool_leak": {
            "root_cause_service": "auth-service",
            "root_cause_mechanism": "connection_leak_in_error_path",
            "failing_resource": "db-pool",
            "red_herring_metric": "frontend.cpu_pct",
        },
        "slow_db": {
            "root_cause_service": "db-pool",
            "root_cause_mechanism": "slow_queries_lock_contention",
            "failing_resource": "db-pool",
            "red_herring_metric": "frontend.cpu_pct",
        },
        "retry_storm_origin": {
            "root_cause_service": "frontend",
            "root_cause_mechanism": "unconstrained_retry_storm",
            "failing_resource": "gateway",
            "red_herring_metric": None,
        },
        "gateway_timeout_misconfig": {
            "root_cause_service": "gateway",
            "root_cause_mechanism": "upstream_timeout_misconfigured_50ms",
            "failing_resource": "gateway",
            "red_herring_metric": "db_pool.active_connections",
        },
    }

    traces_generated = len(set(r["trace_id"] for r in records if r.get("trace_id")))

    ground_truth = {
        "scenario": scenario_name,
        "seed": seed,
        "total_records": len(records),
        "total_traces": traces_generated,
        "onset_ms": t0 + int(onset_s * 1000),
        "clock_skews_ms": {"auth-service": auth_skew_ms, "gateway": 0, "db-pool": 0, "frontend": 0},
        **gt_map[scenario_name],
    }

    return records, ground_truth


def generate_all_scenarios(
    scenarios: list[str] | None = None,
    seeds: list[int] | None = None,
    output_dir: str | Path = "data/scenarios",
):
    """Generate all scenario datasets and write to output_dir."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = scenarios or SCENARIOS
    seeds = seeds or DEFAULT_SEEDS

    generated = []
    for sc in scenarios:
        for sd in seeds:
            records, gt = generate_scenario_telemetry(sc, seed=sd)
            jsonl_file = out_dir / f"{sc}_{sd}.jsonl"
            gt_file = out_dir / f"{sc}_{sd}_gt.json"

            with open(jsonl_file, "w", encoding="utf-8", newline="\n") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

            with open(gt_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(gt, f, indent=2)

            generated.append((str(jsonl_file), str(gt_file), len(records)))
    return generated


def main():
    parser = argparse.ArgumentParser(description="Generate scenario dataset matrix")
    parser.add_argument("--output-dir", type=str, default="data/scenarios", help="Output directory")
    args = parser.parse_args()

    results = generate_all_scenarios(output_dir=args.output_dir)
    print(f"Generated {len(results)} scenario datasets in {args.output_dir}:")
    for jsonl_file, gt_file, rec_count in results[:4]:
        print(f"  {jsonl_file} ({rec_count} records) + {gt_file}")
    if len(results) > 4:
        print(f"  ... and {len(results) - 4} more.")


if __name__ == "__main__":
    main()
