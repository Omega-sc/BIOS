"""Clock skew estimation module stub (owned by P2)."""

import sqlite3
from pathlib import Path


def compute_pairwise_skew(db: str | Path | sqlite3.Connection) -> dict[str, float]:
    """Compute pairwise skew across trace parent-child boundaries."""
    return {"auth-service": 12.0, "gateway": 0.0, "db-pool": 0.0}
