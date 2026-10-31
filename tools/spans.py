"""Trace spans query and causality checking stub (owned by P2)."""

import sqlite3
from pathlib import Path

from contracts.models import Span


def fetch_causal_spans(db: str | Path | sqlite3.Connection, trace_id: str) -> list[Span]:
    """Fetch spans for trace_id with validated parent-child ordering."""
    return []
