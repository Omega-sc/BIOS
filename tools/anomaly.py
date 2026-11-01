"""Incident detection gate using robust z-scores (MAD) over early baseline telemetry."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import INCIDENT_CONFIG, IncidentDetectionConfig


@dataclass
class SeriesAnomalySummary:
    series_name: str
    baseline_median: float
    baseline_mad: float
    max_eval_val: float
    max_z_score: float
    is_anomalous: bool
    reason: str = ""


@dataclass
class IncidentDetectionResult:
    incident_detected: bool
    max_z_score: float
    anomalous_series: list[str] = field(default_factory=list)
    series_summaries: list[SeriesAnomalySummary] = field(default_factory=list)
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_detected": self.incident_detected,
            "max_z_score": round(self.max_z_score, 3),
            "anomalous_series": self.anomalous_series,
            "reason": self.reason,
            "total_series_evaluated": len(self.series_summaries),
        }


def _calculate_median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    mid = n // 2
    if n % 2 == 1:
        return sorted_v[mid]
    return (sorted_v[mid - 1] + sorted_v[mid]) / 2.0


def _calculate_mad(values: list[float], median: float) -> float:
    if not values:
        return 0.0
    diffs = [abs(v - median) for v in values]
    return _calculate_median(diffs)


def _compute_percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    d0 = sorted_v[int(f)] * (c - k)
    d1 = sorted_v[int(c)] * (k - f)
    return d0 + d1


def detect_incident_anomalies(
    db: str | Path | sqlite3.Connection,
    config: IncidentDetectionConfig | None = None,
) -> IncidentDetectionResult:
    """Compare span-derived series and metrics against early baseline using robust z-scores (MAD).

    Span-derived series per service:
      - per-service error rate (errors / total requests in window)
      - per-service p99 latency (ms)
      - per-service request rate (requests / sec)
    Metrics:
      - every metric series in SQLite metrics table

    If max robust z-score across all series exceeds mad_z_threshold (or error rate exceeds 5%),
    incident_detected is set to True. Otherwise returns False.
    """
    cfg = config or INCIDENT_CONFIG

    close_conn = False
    if isinstance(db, (str, Path)):
        conn = sqlite3.connect(str(db))
        close_conn = True
    else:
        conn = db

    try:
        cur = conn.cursor()

        # Check existing tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        has_spans = "spans" in tables
        has_metrics = "metrics" in tables

        if not has_spans and not has_metrics:
            return IncidentDetectionResult(
                incident_detected=False,
                max_z_score=0.0,
                reason="No telemetry tables found in database.",
            )

        # Determine overall time window [t_min, t_max]
        t_min: int | None = None
        t_max: int | None = None

        if has_spans:
            cur.execute("SELECT MIN(start_ms), MAX(end_ms) FROM spans")
            s_row = cur.fetchone()
            if s_row and s_row[0] is not None:
                t_min = s_row[0]
                t_max = s_row[1]

        if has_metrics:
            cur.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM metrics")
            m_row = cur.fetchone()
            if m_row and m_row[0] is not None:
                t_min = min(t_min, m_row[0]) if t_min is not None else m_row[0]
                t_max = max(t_max, m_row[1]) if t_max is not None else m_row[1]

        if t_min is None or t_max is None or t_max <= t_min:
            return IncidentDetectionResult(
                incident_detected=False,
                max_z_score=0.0,
                reason="Insufficient telemetry timespan to evaluate baseline.",
            )

        duration = t_max - t_min
        baseline_cutoff = t_min + int(cfg.baseline_time_fraction * duration)

        summaries: list[SeriesAnomalySummary] = []
        overall_max_z = 0.0
        anomalous_series: list[str] = []

        # 1. Evaluate Metrics Series
        if has_metrics:
            cur.execute("SELECT DISTINCT metric FROM metrics")
            metrics = [r[0] for r in cur.fetchall() if r[0]]

            for m in metrics:
                cur.execute(
                    "SELECT ts_ms, value FROM metrics WHERE metric = ? ORDER BY ts_ms ASC",
                    (m,),
                )
                rows = cur.fetchall()
                if not rows:
                    continue

                base_vals = [r[1] for r in rows if r[0] <= baseline_cutoff]
                eval_vals = [r[1] for r in rows if r[0] > baseline_cutoff]

                # Fallback if baseline window has too few points
                if len(base_vals) < cfg.min_baseline_samples:
                    k = max(cfg.min_baseline_samples, int(0.25 * len(rows)))
                    base_vals = [r[1] for r in rows[:k]]
                    eval_vals = [r[1] for r in rows[k:]]

                if not eval_vals:
                    eval_vals = base_vals

                med = _calculate_median(base_vals)
                mad = _calculate_mad(base_vals, med)
                # Scale incorporates relative baseline magnitude (20%) to avoid flagging nominal sensor jitter
                scale = max(1.4826 * mad, 0.20 * (abs(med) + 1.0), 1.0)

                max_eval = max(eval_vals) if eval_vals else med
                # Incidents in resource gauges, latency, CPU, and error counters present as positive elevations
                z_scores = [max(0.0, v - med) / scale for v in eval_vals]
                max_z = max(z_scores) if z_scores else 0.0
                overall_max_z = max(overall_max_z, max_z)

                is_anom = max_z >= cfg.mad_z_threshold
                summaries.append(
                    SeriesAnomalySummary(
                        series_name=f"metric:{m}",
                        baseline_median=round(med, 3),
                        baseline_mad=round(mad, 3),
                        max_eval_val=round(max_eval, 3),
                        max_z_score=round(max_z, 3),
                        is_anomalous=is_anom,
                        reason=f"z={max_z:.2f} (baseline median={med:.2f}, scale={scale:.2f})"
                        if is_anom
                        else "",
                    )
                )
                if is_anom:
                    anomalous_series.append(f"metric:{m}")

        # 2. Evaluate Span-Derived Series (error rate, p99 latency, request rate) per service
        if has_spans:
            cur.execute("SELECT DISTINCT service FROM spans")
            services = [r[0] for r in cur.fetchall() if r[0]]

            # Bucket width: minimum 1s, maximum 5s
            bucket_width_ms = max(1000, min(5000, duration // 30))

            for svc in services:
                cur.execute(
                    """
                    SELECT start_ms, end_ms, status_code
                    FROM spans
                    WHERE service = ?
                    ORDER BY start_ms ASC
                    """,
                    (svc,),
                )
                svc_spans = cur.fetchall()
                if not svc_spans:
                    continue

                # Bucket spans into discrete time bins
                buckets: dict[int, list[tuple[int, int, int]]] = {}
                for sp in svc_spans:
                    b_idx = (sp[0] - t_min) // bucket_width_ms
                    buckets.setdefault(b_idx, []).append(sp)

                error_rates: list[tuple[int, float]] = []
                p99_latencies: list[tuple[int, float]] = []
                req_rates: list[tuple[int, float]] = []

                for b_idx, b_spans in sorted(buckets.items()):
                    b_time = t_min + b_idx * bucket_width_ms
                    n = len(b_spans)
                    # Errors in spans correspond to HTTP 4xx/5xx or non-zero / non-200 status codes
                    errs = sum(1 for s in b_spans if s[2] >= 400 or (s[2] != 0 and s[2] != 200))
                    e_rate = errs / n if n > 0 else 0.0
                    r_rate = n / (bucket_width_ms / 1000.0)
                    latencies = [float(s[1] - s[0]) for s in b_spans]
                    p99_lat = _compute_percentile(latencies, 99.0)

                    error_rates.append((b_time, e_rate))
                    p99_latencies.append((b_time, p99_lat))
                    req_rates.append((b_time, r_rate))

                # Check each span series
                series_map = {
                    f"span_error_rate:{svc}": (error_rates, True, False),
                    f"span_p99_latency:{svc}": (p99_latencies, False, True),
                    f"span_request_rate:{svc}": (req_rates, False, False),
                }

                for s_name, (data_pts, is_error_rate, is_latency) in series_map.items():
                    if not data_pts:
                        continue
                    b_pts = [p[1] for p in data_pts if p[0] <= baseline_cutoff]
                    e_pts = [p[1] for p in data_pts if p[0] > baseline_cutoff]

                    if len(b_pts) < cfg.min_baseline_samples:
                        k = max(cfg.min_baseline_samples, int(0.25 * len(data_pts)))
                        b_pts = [p[1] for p in data_pts[:k]]
                        e_pts = [p[1] for p in data_pts[k:]]

                    if not e_pts:
                        e_pts = b_pts

                    med = _calculate_median(b_pts)
                    mad = _calculate_mad(b_pts, med)

                    if is_error_rate:
                        scale = max(1.4826 * mad, 0.02)
                    elif is_latency:
                        scale = max(1.4826 * mad, 0.20 * (abs(med) + 1.0), 1.0)
                    else:
                        scale = max(1.4826 * mad, 0.30 * (abs(med) + 1.0), 0.5)

                    max_eval = max(e_pts) if e_pts else med
                    z_scores = [max(0.0, v - med) / scale for v in e_pts]
                    max_z = max(z_scores) if z_scores else 0.0
                    overall_max_z = max(overall_max_z, max_z)

                    # Anomaly condition: z-score exceeds threshold OR absolute error rate exceeds 5%
                    is_anom = max_z >= cfg.mad_z_threshold or (
                        is_error_rate and max_eval >= cfg.min_error_rate_anomaly
                    )

                    summaries.append(
                        SeriesAnomalySummary(
                            series_name=s_name,
                            baseline_median=round(med, 3),
                            baseline_mad=round(mad, 3),
                            max_eval_val=round(max_eval, 3),
                            max_z_score=round(max_z, 3),
                            is_anomalous=is_anom,
                            reason=f"z={max_z:.2f}, max_val={max_eval:.3f}" if is_anom else "",
                        )
                    )
                    if is_anom:
                        anomalous_series.append(s_name)

        incident_detected = len(anomalous_series) > 0

        if incident_detected:
            reason = (
                f"Incident detected across {len(anomalous_series)} series "
                f"(max robust z-score: {overall_max_z:.2f} >= threshold {cfg.mad_z_threshold}). "
                f"Anomalous series: {', '.join(anomalous_series[:5])}"
            )
        else:
            reason = (
                f"No incident detected: telemetry across {len(summaries)} series remains within "
                f"healthy baseline bounds (max robust z-score: {overall_max_z:.2f} < threshold {cfg.mad_z_threshold})."
            )

        return IncidentDetectionResult(
            incident_detected=incident_detected,
            max_z_score=overall_max_z,
            anomalous_series=anomalous_series,
            series_summaries=summaries,
            reason=reason,
            details={
                "baseline_cutoff_ms": baseline_cutoff,
                "duration_ms": duration,
                "anomalous_count": len(anomalous_series),
                "total_series": len(summaries),
            },
        )

    finally:
        if close_conn:
            conn.close()
