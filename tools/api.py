"""Tools API definitions and tool registry for Track 09 incident investigation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import networkx as nx

from tools.dag import build_dependency_dag, failure_frontier, to_node_link_json
from tools.metrics import get_metric_derivatives, rank_metric_onsets
from tools.skew import estimate_clock_offsets
from tools.spans import query_spans


def tool_estimate_clock_offsets(db: str | Path | sqlite3.Connection) -> dict[str, float]:
    """Return JSON-serializable dictionary of estimated per-service clock offsets in seconds."""
    return estimate_clock_offsets(db)


def tool_query_spans(
    db: str | Path | sqlite3.Connection,
    service: str | None = None,
    status_code: int | None = None,
    time_window: tuple[int, int] | list[int] | None = None,
    trace_id: str | None = None,
    causal: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Return JSON-serializable summary of queried spans, capped to avoid giant dumps."""
    cap = min(limit or 50, 100)
    spans = query_spans(
        db,
        service=service,
        status_code=status_code,
        time_window=time_window,
        trace_id=trace_id,
        causal=causal,
        limit=cap,
    )
    return {
        "count": len(spans),
        "limit": cap,
        "orphan_count": sum(1 for s in spans if s.orphan),
        "spans": [s.model_dump() for s in spans],
    }


def tool_get_metric_derivatives(
    db: str | Path | sqlite3.Connection, metric_name: str
) -> dict[str, Any]:
    """Return JSON-serializable summary of numerical derivative inflection and baseline stats."""
    onset = get_metric_derivatives(db, metric_name)
    return onset.model_dump()


def tool_rank_metric_onsets(db: str | Path | sqlite3.Connection) -> list[dict[str, Any]]:
    """Return JSON-serializable list of ranked metric onsets."""
    onsets = rank_metric_onsets(db)
    return [o.model_dump() for o in onsets]


def tool_build_dependency_dag(db: str | Path | sqlite3.Connection) -> dict[str, Any]:
    """Return JSON-serializable node-link summary of service dependency DAG."""
    dag = build_dependency_dag(db)
    return json.loads(to_node_link_json(dag))


def tool_failure_frontier(
    dag: nx.DiGraph | dict[str, Any] | None = None,
    db: str | Path | sqlite3.Connection | None = None,
) -> dict[str, str]:
    """Return JSON-serializable failure frontier boundary description."""
    frontier = failure_frontier(dag, db=db)
    return {"failure_frontier": frontier}


def tool_to_node_link_json(dag: nx.DiGraph) -> str:
    """Return node-link JSON string."""
    return to_node_link_json(dag)


def get_tool_specs() -> list[dict[str, Any]]:
    """Return JSON tool specifications for LLM agent function calling."""
    return [
        {
            "name": "estimate_clock_offsets",
            "description": "Estimate clock skew in seconds per service by analyzing cross-service parent->child span delays during healthy baseline.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {
                        "type": "string",
                        "description": "Path to SQLite database containing incident telemetry.",
                    }
                },
                "required": ["db"],
            },
        },
        {
            "name": "query_spans",
            "description": "Query distributed trace spans with clock correction, orphan detection, and causal ordering (DFS within trace, gateway root order across traces). Returns capped JSON summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Path to SQLite database."},
                    "service": {
                        "type": "string",
                        "description": "Optional filter by service name (e.g. 'auth-service', 'gateway', 'db-pool').",
                    },
                    "status_code": {
                        "type": "integer",
                        "description": "Optional filter by HTTP status code (e.g. 504, 500, 200).",
                    },
                    "time_window": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional [start_ms, end_ms] window interpreted in corrected time.",
                    },
                    "trace_id": {
                        "type": "string",
                        "description": "Optional filter for specific trace ID.",
                    },
                    "causal": {
                        "type": "boolean",
                        "description": "Whether to apply clock correction and causal DFS ordering (default True).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of spans to return (default 50).",
                    },
                },
                "required": ["db"],
            },
        },
        {
            "name": "get_metric_derivatives",
            "description": "Compute numerical derivative inflection, peak slope, and baseline statistics for a single metric.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Path to SQLite database."},
                    "metric_name": {
                        "type": "string",
                        "description": "Name of metric (e.g. 'db_pool.active_connections').",
                    },
                },
                "required": ["db", "metric_name"],
            },
        },
        {
            "name": "rank_metric_onsets",
            "description": "Rank all emitted metrics chronologically by anomalous onset timestamp using rolling median and 4-sigma derivative threshold.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Path to SQLite database."},
                },
                "required": ["db"],
            },
        },
        {
            "name": "build_dependency_dag",
            "description": "Construct directed acyclic service dependency graph annotated with call counts, mean latencies, error propagation fractions, and metric onsets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Path to SQLite database."},
                },
                "required": ["db"],
            },
        },
        {
            "name": "failure_frontier",
            "description": "Identify the earliest failing service boundary along the dependency path using connection pool leak and metric signature analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dag": {
                        "type": "object",
                        "description": "NetworkX DiGraph or node-link representation of service topology (optional if db provided).",
                    },
                    "db": {
                        "type": "string",
                        "description": "Path to SQLite database (optional if dag provided).",
                    },
                },
            },
        },
    ]


TOOLS: dict[str, Callable[..., Any]] = {
    "estimate_clock_offsets": tool_estimate_clock_offsets,
    "query_spans": tool_query_spans,
    "get_metric_derivatives": tool_get_metric_derivatives,
    "rank_metric_onsets": tool_rank_metric_onsets,
    "build_dependency_dag": tool_build_dependency_dag,
    "failure_frontier": tool_failure_frontier,
    "to_node_link_json": tool_to_node_link_json,
}

__all__ = [
    "estimate_clock_offsets",
    "query_spans",
    "get_metric_derivatives",
    "rank_metric_onsets",
    "build_dependency_dag",
    "failure_frontier",
    "to_node_link_json",
    "get_tool_specs",
    "TOOLS",
]
