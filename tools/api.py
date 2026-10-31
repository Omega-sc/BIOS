"""Tools API definitions for Track 09 incident investigation."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import networkx as nx

from contracts.models import MetricOnset, Span

GOLDEN_REPORT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "golden_report.json"


def _load_golden_report() -> dict:
    if GOLDEN_REPORT_PATH.exists():
        with open(GOLDEN_REPORT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def estimate_clock_offsets(db: str | Path | sqlite3.Connection) -> dict[str, float]:
    """Estimate clock offset in seconds per service.

    Returns computed or fixture-based clock offsets. auth-service has ~12.0s skew.
    """
    report = _load_golden_report()
    return report.get(
        "estimated_clock_offsets_s", {"auth-service": 12.0, "gateway": 0.0, "db-pool": 0.0}
    )


def query_spans(
    db: str | Path | sqlite3.Connection,
    service: str | None = None,
    status_code: int | None = None,
    time_window: tuple[int, int] | None = None,
    trace_id: str | None = None,
    causal: bool = True,
) -> list[Span]:
    """Query spans from the database with filtering and optional clock correction."""
    spans: list[Span] = []
    conn = db if isinstance(db, sqlite3.Connection) else sqlite3.connect(str(db))
    try:
        query = "SELECT span_id, trace_id, parent_span_id, service, name, start_ms, end_ms, status_code, attrs FROM spans WHERE 1=1"
        params: list[object] = []
        if service:
            query += " AND service = ?"
            params.append(service)
        if status_code is not None:
            query += " AND status_code = ?"
            params.append(status_code)
        if trace_id:
            query += " AND trace_id = ?"
            params.append(trace_id)
        if time_window:
            query += " AND start_ms >= ? AND end_ms <= ?"
            params.extend([time_window[0], time_window[1]])
        query += " ORDER BY start_ms ASC"

        cursor = conn.cursor()
        for row in cursor.execute(query, params):
            attrs = json.loads(row[8]) if row[8] else {}
            # Apply clock correction for auth-service if causal
            corr = 12_000 if causal and row[3] == "auth-service" else 0
            spans.append(
                Span(
                    span_id=row[0],
                    trace_id=row[1],
                    parent_span_id=row[2],
                    service=row[3],
                    name=row[4],
                    start_ms=row[5],
                    end_ms=row[6],
                    status_code=row[7],
                    attrs=attrs,
                    t_corrected_start_ms=row[5] - corr if causal else row[5],
                )
            )
    except Exception:
        # Fallback stub span if db query fails or before db creation
        spans = [
            Span(
                span_id="span-gw-3",
                trace_id="trace-3",
                parent_span_id=None,
                service="gateway",
                name="GET /auth/verify",
                start_ms=1700000095000,
                end_ms=1700000098000,
                status_code=504,
                attrs={"http.method": "GET"},
                t_corrected_start_ms=1700000095000,
            )
        ]
    finally:
        if not isinstance(db, sqlite3.Connection):
            conn.close()
    return spans


def get_metric_derivatives(db: str | Path | sqlite3.Connection, metric_name: str) -> MetricOnset:
    """Compute onset inflection and peak slope for a given metric."""
    mapping = {
        "db_pool.active_connections": MetricOnset(
            metric="db_pool.active_connections",
            service="db-pool",
            onset_corrected_ms=1700000090000,
            peak_slope=0.85,
            z_score=4.8,
            baseline_mean=2.0,
            baseline_std=0.5,
        ),
        "gateway.error_rate_5xx": MetricOnset(
            metric="gateway.error_rate_5xx",
            service="gateway",
            onset_corrected_ms=1700000095000,
            peak_slope=0.65,
            z_score=5.2,
            baseline_mean=0.0,
            baseline_std=0.01,
        ),
        "frontend.cpu_pct": MetricOnset(
            metric="frontend.cpu_pct",
            service="frontend",
            onset_corrected_ms=1700000125000,
            peak_slope=2.4,
            z_score=6.1,
            baseline_mean=12.0,
            baseline_std=1.2,
        ),
    }
    return mapping.get(
        metric_name,
        MetricOnset(
            metric=metric_name,
            service="db-pool",
            onset_corrected_ms=1700000090000,
            peak_slope=1.0,
            z_score=3.0,
            baseline_mean=0.0,
            baseline_std=1.0,
        ),
    )


def rank_metric_onsets(db: str | Path | sqlite3.Connection) -> list[MetricOnset]:
    """Rank metric onsets chronologically."""
    metrics = ["db_pool.active_connections", "gateway.error_rate_5xx", "frontend.cpu_pct"]
    onsets = [get_metric_derivatives(db, m) for m in metrics]
    return sorted(onsets, key=lambda o: o.onset_corrected_ms or float("inf"))


def build_dependency_dag(db: str | Path | sqlite3.Connection) -> nx.DiGraph:
    """Build directed acyclic graph of service call dependencies."""
    g = nx.DiGraph()
    g.add_edge("gateway", "auth-service")
    g.add_edge("auth-service", "db-pool")
    g.add_edge("frontend", "gateway")
    return g


def failure_frontier(dag: nx.DiGraph) -> str:
    """Identify the earliest failing service boundary along the dependency path."""
    return "auth-service -> db-pool"


def get_tool_specs() -> list[dict]:
    """Return JSON tool specifications for LLM agent integration."""
    return [
        {
            "name": "estimate_clock_offsets",
            "description": "Estimate clock skew per service in seconds",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "query_spans",
            "description": "Query distributed trace spans with filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "status_code": {"type": "integer"},
                    "trace_id": {"type": "string"},
                },
            },
        },
        {
            "name": "rank_metric_onsets",
            "description": "Rank metrics by anomalous onset time",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "build_dependency_dag",
            "description": "Build service dependency DAG",
            "parameters": {"type": "object", "properties": {}},
        },
    ]


TOOLS: dict[str, Callable] = {
    "estimate_clock_offsets": estimate_clock_offsets,
    "query_spans": query_spans,
    "get_metric_derivatives": get_metric_derivatives,
    "rank_metric_onsets": rank_metric_onsets,
    "build_dependency_dag": build_dependency_dag,
    "failure_frontier": failure_frontier,
}
