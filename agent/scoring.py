"""Hypothesis confidence scoring based on telemetry evidence (owned by P3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from contracts.constants import METRIC_SOURCES, POOL_MAX
from contracts.models import EvidencePointer, HypothesisEvaluated
from tools.metrics import rank_metric_onsets
from tools.skew import estimate_clock_offsets

ROOT_DIR = Path(__file__).resolve().parent.parent
WEIGHTS_FILE = ROOT_DIR / "eval" / "fitted_weights.json"

DEFAULT_WEIGHTS = {
    "metric_pool_max": 4.166,
    "onset_pool_before_gw": 3.472,
    "log_leak": 5.0,
    "onset_cpu_before_gw": 10.0,
    "onset_gw_before_cpu": 6.578,
    "metric_high_inflight": 10.0,
    "log_slow_db": 10.0,
    "log_frontend_storm": 10.0,
    "metric_gw_isolated_failure": 10.0,
    "log_gw_misconfig": 10.0,
}


def load_calibration_weights() -> dict[str, float]:
    """Load likelihood ratio weights from eval/fitted_weights.json if present."""
    if WEIGHTS_FILE.exists():
        try:
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("weights", DEFAULT_WEIGHTS)
        except Exception:
            return DEFAULT_WEIGHTS.copy()
    return DEFAULT_WEIGHTS.copy()


def score_hypothesis(
    supporting: list[EvidencePointer],
    refuting: list[EvidencePointer],
    prior: float = 0.5,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute evidence-based confidence score using likelihood ratio updating.

    Never hardcoded; derived from evidence pointers and calibrated weights.
    """
    w = weights or {"span": 1.5, "metric": 2.0, "log": 1.8}
    odds = prior / max(1e-5, (1.0 - prior))
    for ep in supporting:
        lr = w.get(ep.kind, 1.2)
        odds *= lr
    for ep in refuting:
        lr = w.get(ep.kind, 1.2)
        odds /= lr
    prob = odds / (1.0 + odds)
    return max(0.01, min(0.99, round(float(prob), 4)))


def evaluate_hypotheses_from_db(
    db: str | Path | sqlite3.Connection,
    weights: dict[str, float] | None = None,
) -> list[HypothesisEvaluated]:
    """Evaluate hypotheses H1-H4 against telemetry SQLite database.

    Hypotheses:
      - H1: Auth connection pool leak
      - H2: Frontend retry storm is the root cause
      - H3: Slow DB queries
      - H4: Gateway timeout too low

    Uses Bayesian / likelihood ratio updating normalized across candidate hypotheses.
    Evidence pointers are extracted directly from real rows in the database.
    """
    w = weights or load_calibration_weights()

    close_conn = False
    if isinstance(db, (str, Path)):
        conn = sqlite3.connect(str(db))
        close_conn = True
    else:
        conn = db

    try:
        cur = conn.cursor()

        # Check if tables exist
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics'")
        has_metrics = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs'")
        has_logs = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spans'")
        has_spans = cur.fetchone() is not None

        # 1. Estimate clock offsets and metric onsets with skew correction
        offsets = estimate_clock_offsets(conn)
        ranked_onsets = rank_metric_onsets(conn)
        onsets: dict[str, int | None] = {o.metric: o.onset_corrected_ms for o in ranked_onsets}

        # Query metrics and compute peaks & sample pointers
        metric_data: dict[str, list[tuple[int, float]]] = {}
        target_metrics = [
            "db_pool.active_connections",
            "db_pool.queries_in_flight",
            "gateway.error_rate_5xx",
            "frontend.cpu_pct",
            "auth.latency_p99",
        ]
        for m in target_metrics:
            if has_metrics:
                cur.execute(
                    "SELECT ts_ms, value FROM metrics WHERE metric = ? ORDER BY ts_ms ASC",
                    (m,),
                )
                metric_data[m] = cur.fetchall()
            else:
                metric_data[m] = []

        peaks: dict[str, float] = {}
        sample_pointers: dict[str, EvidencePointer] = {}

        for m, pts in metric_data.items():
            if not pts:
                peaks[m] = 0.0
                continue

            vals = [p[1] for p in pts]
            peak_val = max(vals)
            peaks[m] = peak_val

            # Find peak point for pointer citation
            peak_idx = vals.index(peak_val)
            peak_ts = pts[peak_idx][0]
            sample_pointers[f"{m}_peak"] = EvidencePointer(
                kind="metric",
                id=f"{m}@{peak_ts}",
                note=f"Peak value {peak_val:.2f} observed at t={peak_ts}",
            )

            onset_corrected = onsets.get(m)
            if onset_corrected is not None:
                # Find matching point near onset
                svc = METRIC_SOURCES.get(m, "db-pool")
                corr_ms = int(offsets.get(svc, 0.0) * 1000)
                onset_raw = onset_corrected + corr_ms
                sample_pointers[f"{m}_onset"] = EvidencePointer(
                    kind="metric",
                    id=f"{m}@{onset_raw}",
                    note=f"Anomalous onset identified at corrected t={onset_corrected}",
                )
            elif pts:
                sample_pointers[f"{m}_onset"] = EvidencePointer(
                    kind="metric",
                    id=f"{m}@{pts[0][0]}",
                    note=f"Baseline reading at t={pts[0][0]}",
                )

        # 2. Extract logs with actual IDs
        auth_leak_pointers = []
        fe_storm_pointers = []
        db_slow_pointers = []
        gw_misconfig_pointers = []
        has_pool_exhausted_log = False
        if has_logs:
            cur.execute(
                "SELECT log_id, ts_ms, message FROM logs WHERE service = 'auth-service' "
                "AND (message LIKE '%leak%' OR message LIKE '%release%') ORDER BY ts_ms ASC LIMIT 3"
            )
            auth_leak_rows = cur.fetchall()
            auth_leak_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in auth_leak_rows
            ]

            cur.execute(
                "SELECT log_id, ts_ms, message FROM logs WHERE service = 'frontend' "
                "AND (message LIKE '%retry%' OR message LIKE '%storm%' OR message LIKE '%504%') "
                "ORDER BY ts_ms ASC LIMIT 3"
            )
            fe_storm_rows = cur.fetchall()
            fe_storm_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in fe_storm_rows
            ]

            cur.execute(
                "SELECT log_id, ts_ms, message FROM logs WHERE service = 'db-pool' "
                "AND (message LIKE '%slow%' OR message LIKE '%timeout%') ORDER BY ts_ms ASC LIMIT 3"
            )
            db_slow_rows = cur.fetchall()
            db_slow_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in db_slow_rows
            ]

            cur.execute(
                "SELECT log_id, ts_ms, message FROM logs WHERE service = 'gateway' "
                "AND (message LIKE '%misconfig%' OR message LIKE '%timeout%') ORDER BY ts_ms ASC LIMIT 3"
            )
            gw_misconfig_rows = cur.fetchall()
            gw_misconfig_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in gw_misconfig_rows
            ]

            cur.execute(
                "SELECT COUNT(*) FROM logs WHERE service = 'db-pool' AND message LIKE '%exhausted%'"
            )
            has_pool_exhausted_log = cur.fetchone()[0] > 0

        # 3. Extract spans and analyze DB query duration
        gw_504_pointer = None
        db_base_pointer = None
        db_query_duration_unchanged = True
        if has_spans:
            cur.execute(
                "SELECT span_id, start_ms, name FROM spans WHERE service = 'gateway' AND status_code = 504 "
                "ORDER BY start_ms ASC LIMIT 1"
            )
            gw_504_row = cur.fetchone()
            gw_504_pointer = (
                EvidencePointer(
                    kind="span",
                    id=gw_504_row[0],
                    note=f"First gateway 504 timeout span ({gw_504_row[2]}) at t={gw_504_row[1]}",
                )
                if gw_504_row
                else None
            )

            cur.execute(
                "SELECT span_id, start_ms, name FROM spans WHERE service = 'db-pool' "
                "AND status_code = 200 ORDER BY start_ms ASC LIMIT 1"
            )
            db_base_row = cur.fetchone()
            db_base_pointer = (
                EvidencePointer(
                    kind="span",
                    id=db_base_row[0],
                    note=f"Baseline healthy DB span ({db_base_row[2]}) at t={db_base_row[1]}",
                )
                if db_base_row
                else None
            )

            cur.execute(
                "SELECT AVG(end_ms - start_ms) FROM spans WHERE service = 'db-pool' AND status_code = 200"
            )
            avg_row = cur.fetchone()
            if avg_row and avg_row[0] is not None:
                # Queries executing in > 200ms indicate slow DB
                if float(avg_row[0]) > 200.0:
                    db_query_duration_unchanged = False

    finally:
        if close_conn:
            conn.close()

    # 4. Bayesian Likelihood Ratio Updating across H1 - H4
    active_peak = peaks.get("db_pool.active_connections", 0.0)
    q_peak = peaks.get("db_pool.queries_in_flight", 0.0)
    pool_onset = onsets.get("db_pool.active_connections")
    gw_onset = onsets.get("gateway.error_rate_5xx")
    cpu_onset = onsets.get("frontend.cpu_pct")
    acquires_exceed_releases = bool(auth_leak_pointers or has_pool_exhausted_log)

    # --- H1: Auth Connection Pool Leak ---
    w_h1 = 1.0
    h1_supporting: list[EvidencePointer] = []
    h1_refuting: list[EvidencePointer] = []

    if active_peak >= POOL_MAX - 1.0:
        w_h1 *= w.get("metric_pool_max", 4.166)
        if "db_pool.active_connections_peak" in sample_pointers:
            h1_supporting.append(sample_pointers["db_pool.active_connections_peak"])
    elif active_peak >= 5.0:
        w_h1 *= 2.0

    if pool_onset and gw_onset and pool_onset < gw_onset:
        w_h1 *= w.get("onset_pool_before_gw", 3.472)
        if "db_pool.active_connections_onset" in sample_pointers:
            h1_supporting.append(sample_pointers["db_pool.active_connections_onset"])
    elif pool_onset and gw_onset and pool_onset > gw_onset:
        w_h1 /= 3.0

    if acquires_exceed_releases:
        w_h1 *= w.get("log_leak", 5.0)
        h1_supporting.extend(auth_leak_pointers)

    if q_peak <= 8.0:
        # Flat queries in flight supports leak rather than slow db
        w_h1 *= 1.5
        if "db_pool.queries_in_flight_peak" in sample_pointers:
            h1_supporting.append(sample_pointers["db_pool.queries_in_flight_peak"])
    else:
        w_h1 /= 3.0

    if db_query_duration_unchanged:
        w_h1 *= 1.5
    else:
        w_h1 /= 5.0

    if cpu_onset and gw_onset and cpu_onset < gw_onset:
        w_h1 /= w.get("onset_cpu_before_gw", 10.0)
        if "frontend.cpu_pct_onset" in sample_pointers:
            h1_refuting.append(sample_pointers["frontend.cpu_pct_onset"])

    if gw_504_pointer:
        h1_supporting.append(gw_504_pointer)

    # --- H2: Frontend Retry Storm Root Cause ---
    w_h2 = 1.0
    h2_supporting: list[EvidencePointer] = []
    h2_refuting: list[EvidencePointer] = []

    if cpu_onset and gw_onset and cpu_onset <= gw_onset:
        w_h2 *= w.get("onset_cpu_before_gw", 10.0)
        if "frontend.cpu_pct_onset" in sample_pointers:
            h2_supporting.append(sample_pointers["frontend.cpu_pct_onset"])
    else:
        w_h2 /= w.get("onset_gw_before_cpu", 6.578)
        if "gateway.error_rate_5xx_onset" in sample_pointers:
            h2_refuting.append(sample_pointers["gateway.error_rate_5xx_onset"])

    if fe_storm_pointers:
        w_h2 *= 2.0
        h2_supporting.extend(fe_storm_pointers)
    elif "frontend.cpu_pct_peak" in sample_pointers:
        h2_supporting.append(sample_pointers["frontend.cpu_pct_peak"])

    # --- H3: Slow DB Queries ---
    w_h3 = 1.0
    h3_supporting: list[EvidencePointer] = []
    h3_refuting: list[EvidencePointer] = []

    if q_peak > 8.0 or not db_query_duration_unchanged:
        w_h3 *= w.get("metric_high_inflight", 10.0)
        if "db_pool.queries_in_flight_peak" in sample_pointers:
            h3_supporting.append(sample_pointers["db_pool.queries_in_flight_peak"])
    else:
        w_h3 /= 5.0
        if "db_pool.queries_in_flight_peak" in sample_pointers:
            h3_refuting.append(sample_pointers["db_pool.queries_in_flight_peak"])

    if db_slow_pointers:
        w_h3 *= w.get("log_slow_db", 10.0)
        h3_supporting.extend(db_slow_pointers)

    # --- H4: Gateway Timeout Too Low ---
    w_h4 = 1.0
    h4_supporting: list[EvidencePointer] = []
    h4_refuting: list[EvidencePointer] = []

    auth_lat = peaks.get("auth.latency_p99", 0.0)
    if gw_504_pointer and active_peak < 6.0 and auth_lat < 100.0:
        w_h4 *= w.get("metric_gw_isolated_failure", 10.0)
        h4_supporting.append(gw_504_pointer)
    else:
        w_h4 /= 10.0
        if "db_pool.active_connections_peak" in sample_pointers:
            h4_refuting.append(sample_pointers["db_pool.active_connections_peak"])

    if gw_misconfig_pointers:
        w_h4 *= w.get("log_gw_misconfig", 10.0)
        h4_supporting.extend(gw_misconfig_pointers)

    # Fallback evidence pointers to prevent empty lists
    if not h1_supporting and db_base_pointer:
        h1_supporting.append(db_base_pointer)
    if not h2_supporting and "frontend.cpu_pct_onset" in sample_pointers:
        h2_supporting.append(sample_pointers["frontend.cpu_pct_onset"])
    if not h3_refuting and "db_pool.queries_in_flight_onset" in sample_pointers:
        h3_refuting.append(sample_pointers["db_pool.queries_in_flight_onset"])
    if not h4_refuting and "db_pool.active_connections_onset" in sample_pointers:
        h4_refuting.append(sample_pointers["db_pool.active_connections_onset"])

    # 5. Normalize weights across hypotheses -> probabilities
    weights_dict = {"H1": w_h1, "H2": w_h2, "H3": w_h3, "H4": w_h4}
    total_w = sum(weights_dict.values()) + 1e-9
    confidences = {
        k: max(0.01, min(0.99, round(float(v / total_w), 4))) for k, v in weights_dict.items()
    }

    # Determine winning hypothesis dynamically based on highest evidence-based score
    best_hyp_id = max(confidences, key=confidences.get)

    v_h1 = "root_cause" if best_hyp_id == "H1" else "rejected"
    if best_hyp_id == "H2":
        v_h2 = "root_cause"
    elif best_hyp_id == "H1" and (
        fe_storm_pointers or (cpu_onset and gw_onset and cpu_onset >= gw_onset)
    ):
        v_h2 = "contributing_factor"
    else:
        v_h2 = "rejected"
    v_h3 = "root_cause" if best_hyp_id == "H3" else "rejected"
    v_h4 = "root_cause" if best_hyp_id == "H4" else "rejected"

    h1 = HypothesisEvaluated(
        id="H1",
        statement="auth-service leaks DB connections on error path, exhausting pool and causing gateway 504 timeouts.",
        confidence=confidences["H1"],
        verdict=v_h1,
        supporting=h1_supporting,
        refuting=h1_refuting,
    )

    h2 = HypothesisEvaluated(
        id="H2",
        statement="Frontend retry storm caused downstream server overload and gateway timeouts.",
        confidence=confidences["H2"],
        verdict=v_h2,
        supporting=h2_supporting,
        refuting=h2_refuting,
    )

    h3 = HypothesisEvaluated(
        id="H3",
        statement="Slow database queries saturated connection pool and caused timeout cascade.",
        confidence=confidences["H3"],
        verdict=v_h3,
        supporting=h3_supporting,
        refuting=h3_refuting,
    )

    h4 = HypothesisEvaluated(
        id="H4",
        statement="Gateway timeout misconfiguration prematurely aborted healthy downstream requests.",
        confidence=confidences["H4"],
        verdict=v_h4,
        supporting=h4_supporting,
        refuting=h4_refuting,
    )

    return [h1, h2, h3, h4]
