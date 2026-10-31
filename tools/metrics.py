"""Metric onset and inflection detection module (owned by P2).

Computes metric derivative inflection onsets using rolling median and numpy gradient.
Corrects metric timestamps with estimated clock skew.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.constants import METRIC_SOURCES
from contracts.models import MetricOnset
from tools.skew import estimate_clock_offsets


def _get_connection(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating whether it needs closing."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    return sqlite3.connect(str(db)), True


def get_metric_derivatives(db: str | Path | sqlite3.Connection, metric_name: str) -> MetricOnset:
    """Compute onset inflection, peak slope, and baseline stats for a given metric.

    Pipeline:
      1. Correct timestamps using estimated service clock skew.
      2. Apply rolling median filter to suppress transient sensor noise.
      3. Compute numerical gradient with respect to time in seconds.
      4. Detect onset as the first timestamp where the derivative exceeds
         baseline mean + 4*sigma for 3 consecutive samples.

    Args:
        db: SQLite database connection or path.
        metric_name: Name of metric (e.g., 'db_pool.active_connections').

    Returns:
        MetricOnset: Fitted onset metrics with corrected timestamp.
    """
    conn, should_close = _get_connection(db)
    try:
        cur = conn.cursor()

        # Check if table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'")
        if not cur.fetchone():
            return MetricOnset(
                metric=metric_name,
                service=METRIC_SOURCES.get(metric_name, "db-pool"),
                onset_corrected_ms=None,
                peak_slope=0.0,
                z_score=0.0,
                baseline_mean=0.0,
                baseline_std=1.0,
            )

        # Determine emitting service
        cur.execute("SELECT service FROM metrics WHERE metric = ? LIMIT 1", (metric_name,))
        row = cur.fetchone()
        service = row[0] if row else METRIC_SOURCES.get(metric_name, "db-pool")

        # Get clock skew offsets
        offsets_s = estimate_clock_offsets(conn)
        corr_ms = int(offsets_s.get(service, 0.0) * 1000)

        # Query metric time-series
        cur.execute(
            "SELECT ts_ms, value FROM metrics WHERE metric = ? ORDER BY ts_ms ASC",
            (metric_name,),
        )
        rows = cur.fetchall()

        if not rows:
            return MetricOnset(
                metric=metric_name,
                service=service,
                onset_corrected_ms=None,
                peak_slope=0.0,
                z_score=0.0,
                baseline_mean=0.0,
                baseline_std=1.0,
            )

        ts_raw = np.array([r[0] for r in rows], dtype=np.int64)
        ts_corrected = ts_raw - corr_ms
        values = np.array([float(r[1]) for r in rows], dtype=np.float64)

        if len(values) < 5:
            b_mean = float(np.mean(values))
            b_std = float(np.std(values))
            return MetricOnset(
                metric=metric_name,
                service=service,
                onset_corrected_ms=int(ts_corrected[0]) if len(ts_corrected) > 0 else None,
                peak_slope=0.0,
                z_score=0.0,
                baseline_mean=round(b_mean, 2),
                baseline_std=round(max(0.01, b_std), 2),
            )

        # 1. Rolling median filter (window=7, center=True)
        w = min(7, len(values) if len(values) % 2 == 1 else len(values) - 1)
        med = (
            pd.Series(values)
            .rolling(window=max(3, w), center=True, min_periods=1)
            .median()
            .to_numpy()
        )

        # 2. Numpy gradient with respect to time in seconds
        time_s = ts_corrected / 1000.0
        if np.all(np.diff(time_s) > 0):
            grad = np.gradient(med, time_s)
        else:
            grad = np.gradient(med)

        # 3. Baseline statistics (initial 35 samples or first 20% of series)
        baseline_n = max(5, min(35, int(len(values) * 0.25)))
        b_val = values[:baseline_n]
        baseline_mean = float(np.mean(b_val))
        baseline_std = float(np.std(b_val))

        b_grad = grad[:baseline_n]
        b_grad_mean = float(np.mean(b_grad))
        b_grad_std = float(np.std(b_grad))

        # 4. Onset detection: derivative exceeds baseline mean + 4*sigma for 3 consecutive samples
        threshold = b_grad_mean + 4.0 * max(b_grad_std, 1e-4)
        onset_idx = None
        for i in range(len(grad) - 2):
            if grad[i] > threshold and grad[i + 1] > threshold and grad[i + 2] > threshold:
                onset_idx = i
                break

        onset_corrected_ms = int(ts_corrected[onset_idx]) if onset_idx is not None else None
        peak_slope = float(np.max(grad))
        z_score = float((np.max(values) - baseline_mean) / max(baseline_std, 1e-4))

        return MetricOnset(
            metric=metric_name,
            service=service,
            onset_corrected_ms=onset_corrected_ms,
            peak_slope=round(peak_slope, 3),
            z_score=round(z_score, 2),
            baseline_mean=round(baseline_mean, 2),
            baseline_std=round(max(0.01, baseline_std), 2),
        )

    except Exception:
        return MetricOnset(
            metric=metric_name,
            service="db-pool",
            onset_corrected_ms=None,
            peak_slope=0.0,
            z_score=0.0,
            baseline_mean=0.0,
            baseline_std=1.0,
        )
    finally:
        if should_close:
            conn.close()


def rank_metric_onsets(db: str | Path | sqlite3.Connection) -> list[MetricOnset]:
    """Rank all metrics chronologically by anomalous onset time.

    Returns:
        list[MetricOnset]: Metrics sorted by onset_corrected_ms (earliest first, None at end).
    """
    conn, should_close = _get_connection(db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'")
        if not cur.fetchone():
            return []

        cur.execute("SELECT DISTINCT metric FROM metrics")
        metric_names = [row[0] for row in cur.fetchall()]
        if not metric_names:
            metric_names = list(METRIC_SOURCES.keys())

        onsets = [get_metric_derivatives(conn, m) for m in metric_names]
        # Sort by (onset is None, onset_corrected_ms)
        return sorted(
            onsets, key=lambda o: (o.onset_corrected_ms is None, o.onset_corrected_ms or 0)
        )
    except Exception:
        return []
    finally:
        if should_close:
            conn.close()


def detect_onset(db: str | Path | sqlite3.Connection, metric: str) -> MetricOnset:
    """Compatibility alias for get_metric_derivatives."""
    return get_metric_derivatives(db, metric)
