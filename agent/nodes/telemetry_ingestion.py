"""Telemetry ingestion node for investigation pipeline (owned by P3)."""

from __future__ import annotations

from collections.abc import Iterator

from agent.state import IncidentState
from contracts.models import AgentEvent
from tools.api import (
    estimate_clock_offsets,
    get_metric_derivatives,
    query_spans,
    rank_metric_onsets,
)


def telemetry_ingestion_node(state: IncidentState) -> Iterator[AgentEvent]:
    """Execute telemetry ingestion: estimate clock offsets, rank metric onsets, and sample spans."""
    state.current_node = "telemetry_ingestion"

    start_ev = AgentEvent(
        type="state",
        node="telemetry_ingestion",
        payload={"status": "started", "db_path": state.db_path, "mode": state.mode},
    )
    state.record_event(start_ev)
    yield start_ev

    # 1. Estimate clock offsets
    tc_offsets = AgentEvent(
        type="tool_call",
        node="telemetry_ingestion",
        payload={"tool": "estimate_clock_offsets", "args": {"db": state.db_path}},
    )
    state.record_event(tc_offsets)
    yield tc_offsets

    offsets = estimate_clock_offsets(state.db_path)
    state.clock_offsets = offsets

    tr_offsets = AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={"tool": "estimate_clock_offsets", "result": offsets},
    )
    state.record_event(tr_offsets)
    yield tr_offsets

    # 2. Rank metric onsets
    tc_onsets = AgentEvent(
        type="tool_call",
        node="telemetry_ingestion",
        payload={"tool": "rank_metric_onsets", "args": {"db": state.db_path}},
    )
    state.record_event(tc_onsets)
    yield tc_onsets

    onsets = rank_metric_onsets(state.db_path)
    state.metric_onsets = onsets

    tr_onsets = AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={
            "tool": "rank_metric_onsets",
            "result": [o.model_dump() for o in onsets],
        },
    )
    state.record_event(tr_onsets)
    yield tr_onsets

    # 3. Compute metric derivatives for earliest anomalous metric (if any)
    if onsets:
        target_metric = onsets[0].metric
        tc_deriv = AgentEvent(
            type="tool_call",
            node="telemetry_ingestion",
            payload={
                "tool": "get_metric_derivatives",
                "args": {"db": state.db_path, "metric_name": target_metric},
            },
        )
        state.record_event(tc_deriv)
        yield tc_deriv

        deriv = get_metric_derivatives(state.db_path, target_metric)

        tr_deriv = AgentEvent(
            type="tool_result",
            node="telemetry_ingestion",
            payload={
                "tool": "get_metric_derivatives",
                "result": deriv.model_dump(),
            },
        )
        state.record_event(tr_deriv)
        yield tr_deriv

    # 4. Query sample causal spans
    tc_spans = AgentEvent(
        type="tool_call",
        node="telemetry_ingestion",
        payload={"tool": "query_spans", "args": {"db": state.db_path, "limit": 50, "causal": True}},
    )
    state.record_event(tc_spans)
    yield tc_spans

    spans = query_spans(state.db_path, limit=50, causal=True)
    state.spans = spans

    tr_spans = AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={
            "tool": "query_spans",
            "count": len(spans),
            "orphan_count": sum(1 for s in spans if s.orphan),
        },
    )
    state.record_event(tr_spans)
    yield tr_spans

    state.current_node = "topology_builder"
