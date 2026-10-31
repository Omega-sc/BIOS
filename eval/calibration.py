"""Calibration module: fits likelihood-ratio evidence weights on training split and evaluates held-out calibration."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

from contracts.constants import POOL_MAX  # noqa: E402
from datagen.scenarios import SCENARIOS  # noqa: E402
from ingest.load_jsonl import load_telemetry  # noqa: E402

SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"
WEIGHTS_FILE = ROOT_DIR / "eval" / "fitted_weights.json"


def extract_features_from_db(db_path: Path) -> dict[str, Any]:
    """Extract telemetry features from incident SQLite DB without reading ground truth."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()

        # 1. Metric onsets and peaks
        features = {}
        for metric in [
            "db_pool.active_connections",
            "db_pool.queries_in_flight",
            "gateway.error_rate_5xx",
            "frontend.cpu_pct",
            "auth.latency_p99",
        ]:
            cur.execute(
                "SELECT ts_ms, value FROM metrics WHERE metric = ? ORDER BY ts_ms ASC",
                (metric,),
            )
            rows = cur.fetchall()
            if not rows:
                features[f"{metric}_onset"] = None
                features[f"{metric}_peak"] = 0.0
                continue

            values = [r[1] for r in rows]
            ts = [r[0] for r in rows]
            features[f"{metric}_peak"] = float(max(values))

            # Robust inflection detection with baseline std and minimum physical delta
            baseline = float(np.mean(values[:10])) if len(values) >= 10 else values[0]
            std = float(np.std(values[:10])) if len(values) >= 10 else 1.0

            min_deltas = {
                "frontend.cpu_pct": 15.0,
                "db_pool.active_connections": 5.0,
                "gateway.error_rate_5xx": 0.15,
                "db_pool.queries_in_flight": 4.0,
                "auth.latency_p99": 100.0,
            }
            delta = min_deltas.get(metric, 2.0)
            thresh = max(baseline + 3.5 * max(0.2, std), baseline + delta)

            onset = None
            for t, v in zip(ts, values):
                if v >= thresh:
                    onset = t
                    break
            features[f"{metric}_onset"] = onset

        # 2. Check for explicit error logs
        cur.execute(
            "SELECT COUNT(*) FROM logs WHERE service = 'auth-service' AND message LIKE '%leak%'"
        )
        features["auth_leak_logs"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM logs WHERE service = 'db-pool' AND message LIKE '%slow%'")
        features["db_slow_logs"] = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM logs WHERE service = 'frontend' AND message LIKE '%retry storm%'"
        )
        features["frontend_storm_logs"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM logs WHERE service = 'gateway' AND message LIKE '%50ms%'")
        features["gw_misconfig_logs"] = cur.fetchone()[0]

        # 3. Span failure counts
        cur.execute(
            "SELECT service, status_code, COUNT(*) FROM spans GROUP BY service, status_code"
        )
        span_stats = cur.fetchall()
        features["gw_504_count"] = sum(
            cnt for svc, sc, cnt in span_stats if svc == "gateway" and sc == 504
        )
        features["auth_500_count"] = sum(
            cnt for svc, sc, cnt in span_stats if svc == "auth-service" and sc == 500
        )
    finally:
        conn.close()
    return features


def evaluate_hypothesis_scores(
    features: dict[str, Any], weights: dict[str, float]
) -> dict[str, float]:
    """Score candidate root cause services using likelihood-ratio evidence weights."""
    active_peak = features.get("db_pool.active_connections_peak", 0.0)
    pool_onset = features.get("db_pool.active_connections_onset")
    gw_onset = features.get("gateway.error_rate_5xx_onset")
    cpu_onset = features.get("frontend.cpu_pct_onset")

    # Candidate 1: auth-service (pool leak)
    w_auth = 1.0
    if active_peak >= POOL_MAX:
        w_auth *= weights.get("metric_pool_max", 3.0)
    if pool_onset and gw_onset and pool_onset < gw_onset:
        w_auth *= weights.get("onset_pool_before_gw", 2.5)
    if features.get("auth_leak_logs", 0) > 0:
        w_auth *= weights.get("log_leak", 5.0)
    if cpu_onset and gw_onset and cpu_onset < gw_onset:
        w_auth /= weights.get("onset_cpu_before_gw", 4.0)

    # Candidate 2: db-pool (slow db)
    w_db = 1.0
    q_peak = features.get("db_pool.queries_in_flight_peak", 0.0)
    if q_peak > 8.0:
        w_db *= weights.get("metric_high_inflight", 3.5)
    if features.get("db_slow_logs", 0) > 0:
        w_db *= weights.get("log_slow_db", 5.0)

    # Candidate 3: frontend (retry storm origin)
    w_fe = 1.0
    if cpu_onset and gw_onset and cpu_onset <= gw_onset:
        w_fe *= weights.get("onset_cpu_before_gw", 6.0)
    if features.get("frontend_storm_logs", 0) > 0:
        w_fe *= weights.get("log_frontend_storm", 5.0)
    if cpu_onset and gw_onset and cpu_onset > gw_onset:
        w_fe /= weights.get("onset_gw_before_cpu", 4.0)

    # Candidate 4: gateway (timeout misconfig)
    w_gw = 1.0
    gw_504 = features.get("gw_504_count", 0)
    auth_lat = features.get("auth.latency_p99_peak", 0.0)
    if gw_504 > 10 and active_peak < 6.0 and auth_lat < 100.0:
        w_gw *= weights.get("metric_gw_isolated_failure", 6.0)
    if features.get("gw_misconfig_logs", 0) > 0:
        w_gw *= weights.get("log_gw_misconfig", 5.0)

    weights_dict = {
        "auth-service": w_auth,
        "db-pool": w_db,
        "frontend": w_fe,
        "gateway": w_gw,
    }
    total = sum(weights_dict.values()) + 1e-9
    return {k: float(v / total) for k, v in weights_dict.items()}


def fit_calibration_weights(
    train_seeds: list[int] = [101, 102, 103],
    test_seeds: list[int] = [104, 105],
) -> dict[str, Any]:
    """Fit likelihood ratio weights on training scenarios and report test calibration."""
    initial_weights = {
        "metric_pool_max": 3.0,
        "onset_pool_before_gw": 2.5,
        "log_leak": 5.0,
        "onset_cpu_before_gw": 4.0,
        "onset_gw_before_cpu": 3.0,
        "metric_high_inflight": 3.5,
        "log_slow_db": 5.0,
        "log_frontend_storm": 5.0,
        "metric_gw_isolated_failure": 6.0,
        "log_gw_misconfig": 5.0,
    }

    # Extract dataset samples
    def get_samples(seeds: list[int]) -> list[dict[str, Any]]:
        samples = []
        for sc in SCENARIOS:
            for seed in seeds:
                jsonl_path = SCENARIOS_DIR / f"{sc}_{seed}.jsonl"
                gt_path = SCENARIOS_DIR / f"{sc}_{seed}_gt.json"
                if not jsonl_path.exists() or not gt_path.exists():
                    continue

                db_temp = ROOT_DIR / "data" / f"eval_tmp_{seed}.db"
                try:
                    load_telemetry(jsonl_path, db_temp)
                    feat = extract_features_from_db(db_temp)
                finally:
                    if db_temp.exists():
                        db_temp.unlink()

                with open(gt_path, "r", encoding="utf-8") as f:
                    gt = json.load(f)

                samples.append(
                    {
                        "scenario": sc,
                        "seed": seed,
                        "features": feat,
                        "true_root_cause": gt["root_cause_service"],
                    }
                )
        return samples

    train_samples = get_samples(train_seeds)
    test_samples = get_samples(test_seeds)

    weight_keys = list(initial_weights.keys())

    # Objective function: regularized log-loss on training samples
    x0 = np.array([initial_weights[k] for k in weight_keys])
    bounds = [(1.1, 10.0) for _ in weight_keys]

    def loss_func(params: np.ndarray) -> float:
        w = {k: max(1.1, float(v)) for k, v in zip(weight_keys, params)}
        loss = 0.0
        for s in train_samples:
            scores = evaluate_hypothesis_scores(s["features"], w)
            p_true = max(1e-4, scores.get(s["true_root_cause"], 0.01))
            loss -= np.log(p_true)
        reg = 0.02 * float(np.sum((np.log(params) - np.log(x0)) ** 2))
        return float(loss + reg)

    res = minimize(loss_func, x0, method="L-BFGS-B", bounds=bounds)

    fitted_weights = {k: round(float(v), 3) for k, v in zip(weight_keys, res.x)}

    # Evaluate calibration metrics
    def compute_metrics(samples: list[dict[str, Any]], w: dict[str, float]) -> dict[str, float]:
        correct = 0
        brier = 0.0
        for s in samples:
            scores = evaluate_hypothesis_scores(s["features"], w)
            best_svc = max(scores, key=scores.get)
            if best_svc == s["true_root_cause"]:
                correct += 1
            # Brier score over all 4 candidate services
            for svc in ["auth-service", "db-pool", "frontend", "gateway"]:
                y = 1.0 if svc == s["true_root_cause"] else 0.0
                p = scores.get(svc, 0.0)
                brier += (p - y) ** 2
        acc = correct / max(1, len(samples))
        mean_brier = brier / (max(1, len(samples)) * 4.0)
        return {"top1_accuracy": round(float(acc), 4), "brier_score": round(float(mean_brier), 4)}

    train_metrics = compute_metrics(train_samples, fitted_weights)
    test_metrics = compute_metrics(test_samples, fitted_weights)

    output = {
        "weights": fitted_weights,
        "train_metrics": train_metrics,
        "heldout_test_metrics": test_metrics,
        "train_samples_count": len(train_samples),
        "test_samples_count": len(test_samples),
    }

    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTS_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(output, f, indent=2)

    return output


def main():
    parser = argparse.ArgumentParser(description="Calibrate evidence weights on training split")
    parser.parse_args()

    results = fit_calibration_weights()
    print("Fitted weights successfully:")
    print(json.dumps(results, indent=2))
    print(f"Weights saved to {WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
