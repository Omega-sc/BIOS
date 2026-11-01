"""Hypothesis confidence scoring based on telemetry evidence (owned by P3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent.config import HYPOTHESIS_CONFIG
from contracts.models import EvidencePointer, HypothesisEvaluated
from tools.anomaly import detect_incident_anomalies
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
    incident_detected: bool | None = None,
) -> list[HypothesisEvaluated]:
    """Evaluate candidate hypotheses against telemetry SQLite database.

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

        # Discover distinct services across spans, metrics, logs
        span_services: list[str] = []
        if has_spans:
            cur.execute("SELECT DISTINCT service FROM spans")
            span_services = [r[0] for r in cur.fetchall() if r[0]]

        metric_services: list[str] = []
        if has_metrics:
            cur.execute("SELECT DISTINCT service FROM metrics")
            metric_services = [r[0] for r in cur.fetchall() if r[0]]

        log_services: list[str] = []
        if has_logs:
            cur.execute("SELECT DISTINCT service FROM logs")
            log_services = [r[0] for r in cur.fetchall() if r[0]]

        all_services = list(dict.fromkeys(span_services + metric_services + log_services))

        # Discover root service (most root spans)
        root_svc = None
        if has_spans:
            cur.execute(
                "SELECT service, COUNT(*) FROM spans WHERE parent_span_id IS NULL GROUP BY service ORDER BY COUNT(*) DESC LIMIT 1"
            )
            r_row = cur.fetchone()
            root_svc = r_row[0] if r_row else None
        if not root_svc and span_services:
            root_svc = span_services[0]

        # Discover leaf service (callee with no outgoing cross-service spans)
        leaf_svc = None
        if has_spans:
            cur.execute(
                """
                SELECT c.service, COUNT(*)
                FROM spans c
                WHERE c.parent_span_id IS NOT NULL
                  AND c.service NOT IN (
                      SELECT DISTINCT p.service FROM spans p
                      JOIN spans ch ON p.span_id = ch.parent_span_id
                      WHERE p.service != ch.service
                  )
                GROUP BY c.service ORDER BY COUNT(*) DESC LIMIT 1
                """
            )
            l_row = cur.fetchone()
            leaf_svc = l_row[0] if l_row else None
        if not leaf_svc and len(span_services) > 1:
            leaf_svc = span_services[-1]

        # Discover mid service (service between root and leaf)
        mid_candidates = [s for s in span_services if s != root_svc and s != leaf_svc]
        mid_svc = mid_candidates[0] if mid_candidates else None

        # Discover client service (service with no spans but present in metrics/logs)
        client_candidates = [s for s in all_services if s not in span_services]
        client_svc = client_candidates[0] if client_candidates else None

        # Incident detection gate (P2): abstain if telemetry is within healthy baseline bounds
        if incident_detected is None:
            detection = detect_incident_anomalies(conn)
            incident_detected = detection.incident_detected

        if not incident_detected:
            mid_name = mid_svc or "upstream service"
            root_name = root_svc or "entrypoint service"
            leaf_name = leaf_svc or "downstream database"
            client_name = client_svc or "client service"
            return [
                HypothesisEvaluated(
                    id="H1",
                    statement=f"{mid_name} leaks resource connections on error path, exhausting pool and causing {root_name} timeout cascade.",
                    confidence=0.10,
                    verdict="unresolved",
                    supporting=[],
                    refuting=[],
                    missing_evidence=[
                        "Telemetry within healthy baseline bounds; no resource exhaustion detected."
                    ],
                ),
                HypothesisEvaluated(
                    id="H2",
                    statement=f"{client_name} retry storm caused downstream server overload and {root_name} timeouts.",
                    confidence=0.10,
                    verdict="unresolved",
                    supporting=[],
                    refuting=[],
                    missing_evidence=[
                        "Telemetry within healthy baseline bounds; no request storm or CPU spike detected."
                    ],
                ),
                HypothesisEvaluated(
                    id="H3",
                    statement=f"Slow queries on {leaf_name} saturated connection pool and caused timeout cascade.",
                    confidence=0.10,
                    verdict="unresolved",
                    supporting=[],
                    refuting=[],
                    missing_evidence=[
                        "Telemetry within healthy baseline bounds; no query latency elongation detected."
                    ],
                ),
                HypothesisEvaluated(
                    id="H4",
                    statement=f"{root_name} timeout misconfiguration prematurely aborted healthy downstream requests.",
                    confidence=0.10,
                    verdict="unresolved",
                    supporting=[],
                    refuting=[],
                    missing_evidence=[
                        "Telemetry within healthy baseline bounds; no timeout errors observed."
                    ],
                ),
            ]

        # 1. Estimate clock offsets and metric onsets with skew correction
        offsets = estimate_clock_offsets(conn)
        ranked_onsets = rank_metric_onsets(conn)
        onsets: dict[str, int | None] = {o.metric: o.onset_corrected_ms for o in ranked_onsets}

        # Collect all metrics and discover metric roles
        all_metrics: list[str] = []
        if has_metrics:
            cur.execute("SELECT DISTINCT metric FROM metrics")
            all_metrics = [r[0] for r in cur.fetchall() if r[0]]

        pool_metric = None
        qif_metric = None
        gw_err_metric = None
        cpu_metric = None
        mid_lat_metric = None

        for m in all_metrics:
            if "active_connection" in m or "conn" in m:
                pool_metric = m
            elif "queries_in_flight" in m or "qif" in m or "flight" in m:
                qif_metric = m
            elif "error_rate" in m or "5xx" in m or "err" in m:
                gw_err_metric = m
            elif "cpu" in m:
                cpu_metric = m
            elif "latency" in m or "p99" in m or "duration" in m:
                mid_lat_metric = m

        if not pool_metric and leaf_svc and has_metrics:
            cur.execute(
                "SELECT metric, MAX(value) FROM metrics WHERE service = ? GROUP BY metric ORDER BY MAX(value) DESC",
                (leaf_svc,),
            )
            m_rows = cur.fetchall()
            if m_rows:
                pool_metric = m_rows[0][0]
                if len(m_rows) > 1:
                    qif_metric = m_rows[1][0]

        if not gw_err_metric and root_svc and has_metrics:
            cur.execute("SELECT metric FROM metrics WHERE service = ? LIMIT 1", (root_svc,))
            r = cur.fetchone()
            if r:
                gw_err_metric = r[0]

        if not cpu_metric and client_svc and has_metrics:
            cur.execute("SELECT metric FROM metrics WHERE service = ? LIMIT 1", (client_svc,))
            r = cur.fetchone()
            if r:
                cpu_metric = r[0]

        if not mid_lat_metric and mid_svc and has_metrics:
            cur.execute("SELECT metric FROM metrics WHERE service = ? LIMIT 1", (mid_svc,))
            r = cur.fetchone()
            if r:
                mid_lat_metric = r[0]

        target_metrics = [
            m
            for m in [pool_metric, qif_metric, gw_err_metric, cpu_metric, mid_lat_metric]
            if m is not None
        ]

        metric_data: dict[str, list[tuple[int, float]]] = {}
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

            peak_idx = vals.index(peak_val)
            peak_ts = pts[peak_idx][0]
            sample_pointers[f"{m}_peak"] = EvidencePointer(
                kind="metric",
                id=f"{m}@{peak_ts}",
                note=f"Peak value {peak_val:.2f} observed at t={peak_ts}",
            )

            onset_corrected = onsets.get(m)
            if onset_corrected is not None:
                cur.execute("SELECT service FROM metrics WHERE metric = ? LIMIT 1", (m,))
                s_row = cur.fetchone()
                svc = s_row[0] if s_row else ""
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
        leak_pointers = []
        storm_pointers = []
        slow_query_pointers = []
        misconfig_pointers = []
        has_pool_exhausted_log = False

        if has_logs:
            mid_filter = f"service = '{mid_svc}' AND " if mid_svc else ""
            cur.execute(
                f"SELECT log_id, ts_ms, message FROM logs WHERE {mid_filter}"
                "(message LIKE '%leak%' OR message LIKE '%release%') ORDER BY ts_ms ASC LIMIT 3"
            )
            auth_leak_rows = cur.fetchall()
            leak_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in auth_leak_rows
            ]

            client_filter = f"service = '{client_svc}' AND " if client_svc else ""
            cur.execute(
                f"SELECT log_id, ts_ms, message FROM logs WHERE {client_filter}"
                "(message LIKE '%retry%' OR message LIKE '%storm%' OR message LIKE '%504%') "
                "ORDER BY ts_ms ASC LIMIT 3"
            )
            fe_storm_rows = cur.fetchall()
            storm_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in fe_storm_rows
            ]

            leaf_filter = f"service = '{leaf_svc}' AND " if leaf_svc else ""
            cur.execute(
                f"SELECT log_id, ts_ms, message FROM logs WHERE {leaf_filter}"
                "(message LIKE '%slow%' OR message LIKE '%timeout%') ORDER BY ts_ms ASC LIMIT 3"
            )
            db_slow_rows = cur.fetchall()
            slow_query_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in db_slow_rows
            ]

            root_filter = f"service = '{root_svc}' AND " if root_svc else ""
            cur.execute(
                f"SELECT log_id, ts_ms, message FROM logs WHERE {root_filter}"
                "(message LIKE '%misconfig%' OR message LIKE '%timeout%') ORDER BY ts_ms ASC LIMIT 3"
            )
            gw_misconfig_rows = cur.fetchall()
            misconfig_pointers = [
                EvidencePointer(kind="log", id=r[0], note=r[2]) for r in gw_misconfig_rows
            ]

            cur.execute("SELECT COUNT(*) FROM logs WHERE message LIKE '%exhausted%'")
            has_pool_exhausted_log = cur.fetchone()[0] > 0

        # 3. Extract spans and analyze DB query duration
        gw_504_pointer = None
        db_base_pointer = None
        db_query_duration_unchanged = True

        if has_spans:
            cur.execute(
                "SELECT span_id, start_ms, name, service FROM spans WHERE status_code >= 500 "
                "ORDER BY start_ms ASC LIMIT 1"
            )
            gw_504_row = cur.fetchone()
            gw_504_pointer = (
                EvidencePointer(
                    kind="span",
                    id=gw_504_row[0],
                    note=f"First error span on {gw_504_row[3]} ({gw_504_row[2]}) at t={gw_504_row[1]}",
                )
                if gw_504_row
                else None
            )

            leaf_filter_sp = f"service = '{leaf_svc}' AND " if leaf_svc else ""
            cur.execute(
                f"SELECT span_id, start_ms, name, service FROM spans WHERE {leaf_filter_sp}"
                "status_code = 200 ORDER BY start_ms ASC LIMIT 1"
            )
            db_base_row = cur.fetchone()
            db_base_pointer = (
                EvidencePointer(
                    kind="span",
                    id=db_base_row[0],
                    note=f"Baseline healthy downstream span on {db_base_row[3]} ({db_base_row[2]}) at t={db_base_row[1]}",
                )
                if db_base_row
                else None
            )

            cur.execute(
                f"SELECT AVG(end_ms - start_ms) FROM spans WHERE {leaf_filter_sp}status_code = 200"
            )
            avg_row = cur.fetchone()
            if avg_row and avg_row[0] is not None:
                if float(avg_row[0]) > 200.0:
                    db_query_duration_unchanged = False

    finally:
        if close_conn:
            conn.close()

    # 4. Bayesian Likelihood Ratio Updating across H1 - H4
    active_peak = peaks.get(pool_metric, 0.0) if pool_metric else 0.0
    q_peak = peaks.get(qif_metric, 0.0) if qif_metric else 0.0
    pool_onset = onsets.get(pool_metric) if pool_metric else None
    gw_onset = onsets.get(gw_err_metric) if gw_err_metric else None
    cpu_onset = onsets.get(cpu_metric) if cpu_metric else None
    acquires_exceed_releases = bool(leak_pointers or has_pool_exhausted_log)

    # Estimate capacity from data plateau
    pool_capacity = max(10.0, active_peak)

    # --- H1: Resource / Connection Pool Leak ---
    w_h1 = 1.0
    h1_supporting: list[EvidencePointer] = []
    h1_refuting: list[EvidencePointer] = []

    if active_peak >= pool_capacity - 1.0 and active_peak >= 5.0:
        w_h1 *= w.get("metric_pool_max", 4.166)
        if pool_metric and f"{pool_metric}_peak" in sample_pointers:
            h1_supporting.append(sample_pointers[f"{pool_metric}_peak"])
    elif active_peak >= 5.0:
        w_h1 *= 2.0

    if pool_onset and gw_onset and pool_onset < gw_onset:
        w_h1 *= w.get("onset_pool_before_gw", 3.472)
        if pool_metric and f"{pool_metric}_onset" in sample_pointers:
            h1_supporting.append(sample_pointers[f"{pool_metric}_onset"])
    elif pool_onset and gw_onset and pool_onset > gw_onset:
        w_h1 /= 3.0

    if acquires_exceed_releases:
        w_h1 *= w.get("log_leak", 5.0)
        h1_supporting.extend(leak_pointers)

    if q_peak <= 8.0:
        w_h1 *= 1.5
        if qif_metric and f"{qif_metric}_peak" in sample_pointers:
            h1_supporting.append(sample_pointers[f"{qif_metric}_peak"])
    else:
        w_h1 /= 3.0

    if db_query_duration_unchanged:
        w_h1 *= 1.5
    else:
        w_h1 /= 5.0

    if cpu_onset and gw_onset and cpu_onset < gw_onset:
        w_h1 /= w.get("onset_cpu_before_gw", 10.0)
        if cpu_metric and f"{cpu_metric}_onset" in sample_pointers:
            h1_refuting.append(sample_pointers[f"{cpu_metric}_onset"])

    if gw_504_pointer:
        h1_supporting.append(gw_504_pointer)
    if db_base_pointer:
        h1_supporting.append(db_base_pointer)

    # --- H2: Client Retry Storm Root Cause ---
    w_h2 = 1.0
    h2_supporting: list[EvidencePointer] = []
    h2_refuting: list[EvidencePointer] = []

    if cpu_onset and gw_onset and cpu_onset <= gw_onset:
        w_h2 *= w.get("onset_cpu_before_gw", 10.0)
        if cpu_metric and f"{cpu_metric}_onset" in sample_pointers:
            h2_supporting.append(sample_pointers[f"{cpu_metric}_onset"])
    else:
        w_h2 /= w.get("onset_gw_before_cpu", 6.578)
        if gw_err_metric and f"{gw_err_metric}_onset" in sample_pointers:
            h2_refuting.append(sample_pointers[f"{gw_err_metric}_onset"])

    if storm_pointers:
        w_h2 *= 2.0
        h2_supporting.extend(storm_pointers)
    elif cpu_metric and f"{cpu_metric}_peak" in sample_pointers:
        h2_supporting.append(sample_pointers[f"{cpu_metric}_peak"])

    # --- H3: Slow Dependency Queries ---
    w_h3 = 1.0
    h3_supporting: list[EvidencePointer] = []
    h3_refuting: list[EvidencePointer] = []

    if q_peak > 8.0 or not db_query_duration_unchanged:
        w_h3 *= w.get("metric_high_inflight", 10.0)
        if qif_metric and f"{qif_metric}_peak" in sample_pointers:
            h3_supporting.append(sample_pointers[f"{qif_metric}_peak"])
    else:
        w_h3 /= 5.0
        if qif_metric and f"{qif_metric}_peak" in sample_pointers:
            h3_refuting.append(sample_pointers[f"{qif_metric}_peak"])

    if slow_query_pointers:
        w_h3 *= w.get("log_slow_db", 10.0)
        h3_supporting.extend(slow_query_pointers)

    # --- H4: Timeout Misconfiguration ---
    w_h4 = 1.0
    h4_supporting: list[EvidencePointer] = []
    h4_refuting: list[EvidencePointer] = []

    mid_lat = peaks.get(mid_lat_metric, 0.0) if mid_lat_metric else 0.0
    if gw_504_pointer and active_peak < 6.0 and mid_lat < 100.0:
        w_h4 *= w.get("metric_gw_isolated_failure", 10.0)
        h4_supporting.append(gw_504_pointer)
    else:
        w_h4 /= 10.0
        if pool_metric and f"{pool_metric}_peak" in sample_pointers:
            h4_refuting.append(sample_pointers[f"{pool_metric}_peak"])

    if misconfig_pointers:
        w_h4 *= w.get("log_gw_misconfig", 10.0)
        h4_supporting.extend(misconfig_pointers)

    weights_dict = {"H1": w_h1, "H2": w_h2, "H3": w_h3, "H4": w_h4}
    # Normalize with explicit 'none of the above' prior mass
    total_w = sum(weights_dict.values()) + HYPOTHESIS_CONFIG.none_of_above_mass + 1e-9
    confidences = {
        k: max(0.01, min(0.99, round(float(v / total_w), 4))) for k, v in weights_dict.items()
    }

    sorted_hyps = sorted(confidences.items(), key=lambda kv: kv[1], reverse=True)
    best_hyp_id, best_conf = sorted_hyps[0]
    runner_up_id, runner_up_conf = sorted_hyps[1] if len(sorted_hyps) > 1 else ("", 0.0)
    margin = best_conf - runner_up_conf
    best_weight = weights_dict[best_hyp_id]
    supporting_map = {
        "H1": h1_supporting,
        "H2": h2_supporting,
        "H3": h3_supporting,
        "H4": h4_supporting,
    }
    best_supporting = supporting_map[best_hyp_id]

    has_evidence = best_weight >= HYPOTHESIS_CONFIG.min_evidence_score and len(best_supporting) > 0
    has_margin = margin >= HYPOTHESIS_CONFIG.min_confidence_margin

    missing_map: dict[str, list[str]] = {"H1": [], "H2": [], "H3": [], "H4": []}

    if not has_evidence or not has_margin:
        best_verdict = "unresolved"
        confidences[best_hyp_id] = min(best_conf, HYPOTHESIS_CONFIG.max_unresolved_confidence)
        if not has_evidence:
            missing_map[best_hyp_id].append(
                f"Insufficient corroborating evidence: score ({best_weight:.2f}) < threshold ({HYPOTHESIS_CONFIG.min_evidence_score}). Missing definitive metric onset or error traces."
            )
        if not has_margin:
            missing_map[best_hyp_id].append(
                f"Ambiguous hypothesis separation: margin ({margin:.2f}) over {runner_up_id} < threshold ({HYPOTHESIS_CONFIG.min_confidence_margin})."
            )
    else:
        best_verdict = "root_cause"

    v_h1 = best_verdict if best_hyp_id == "H1" else "rejected"
    if best_hyp_id == "H2":
        v_h2 = best_verdict
    elif (
        best_hyp_id == "H1"
        and best_verdict == "root_cause"
        and (storm_pointers or (cpu_onset and gw_onset and cpu_onset >= gw_onset))
    ):
        v_h2 = "contributing_factor"
    else:
        v_h2 = "rejected"
    v_h3 = best_verdict if best_hyp_id == "H3" else "rejected"
    v_h4 = best_verdict if best_hyp_id == "H4" else "rejected"

    # Generate statements bound to discovered service names (never hardcoded scenario literals)
    mid_name = mid_svc or "upstream service"
    root_name = root_svc or "entrypoint service"
    leaf_name = leaf_svc or "downstream database"
    client_name = client_svc or "client service"

    h1 = HypothesisEvaluated(
        id="H1",
        statement=f"{mid_name} leaks resource connections on error path, exhausting pool and causing {root_name} timeout cascade.",
        confidence=confidences["H1"],
        verdict=v_h1,
        supporting=h1_supporting,
        refuting=h1_refuting,
        missing_evidence=missing_map["H1"],
    )

    h2 = HypothesisEvaluated(
        id="H2",
        statement=f"{client_name} retry storm caused downstream server overload and {root_name} timeouts.",
        confidence=confidences["H2"],
        verdict=v_h2,
        supporting=h2_supporting,
        refuting=h2_refuting,
        missing_evidence=missing_map["H2"],
    )

    h3 = HypothesisEvaluated(
        id="H3",
        statement=f"Slow queries on {leaf_name} saturated connection pool and caused timeout cascade.",
        confidence=confidences["H3"],
        verdict=v_h3,
        supporting=h3_supporting,
        refuting=h3_refuting,
        missing_evidence=missing_map["H3"],
    )

    h4 = HypothesisEvaluated(
        id="H4",
        statement=f"{root_name} timeout misconfiguration prematurely aborted healthy downstream requests.",
        confidence=confidences["H4"],
        verdict=v_h4,
        supporting=h4_supporting,
        refuting=h4_refuting,
        missing_evidence=missing_map["H4"],
    )

    return [h1, h2, h3, h4]
