"""Metric onset and inflection detection stub (owned by P2)."""

import sqlite3
from pathlib import Path

from contracts.models import MetricOnset


def detect_onset(db: str | Path | sqlite3.Connection, metric: str) -> MetricOnset:
    """Detect inflection onset using sliding baseline and derivative."""
    return MetricOnset(
        metric=metric,
        service="db-pool",
        onset_corrected_ms=1700000090000,
        peak_slope=1.0,
        z_score=3.5,
        baseline_mean=0.0,
        baseline_std=1.0,
    )
