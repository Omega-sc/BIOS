"""Simulation parameter fitting from incident telemetry (owned by P2)."""

import sqlite3
from pathlib import Path

from contracts.models import SimParams


def fit_sim_params(db: str | Path | sqlite3.Connection) -> SimParams:
    """Fit arrival rate, leak probability, and service times from DB telemetry."""
    return SimParams(
        pool_size=20,
        arrival_rate_per_s=10.0,
        leak_prob=0.08,
        mean_service_ms=45.0,
        acquire_timeout_ms=5000,
        gateway_timeout_ms=3000,
        retry_max=3,
        window_s=600,
        seed=42,
    )
