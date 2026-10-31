"""Service topology DAG stub (owned by P2)."""

import sqlite3
from pathlib import Path

import networkx as nx


def infer_topology(db: str | Path | sqlite3.Connection) -> nx.DiGraph:
    """Infer topology DAG from parent-child span pairs."""
    g = nx.DiGraph()
    g.add_edge("gateway", "auth-service")
    g.add_edge("auth-service", "db-pool")
    g.add_edge("frontend", "gateway")
    return g
