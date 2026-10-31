"""Topology builder node for investigation pipeline (owned by P3)."""

from __future__ import annotations

from collections.abc import Iterator

from agent.state import IncidentState
from contracts.models import AgentEvent
from tools.api import (
    build_dependency_dag,
    failure_frontier,
)


def topology_builder_node(state: IncidentState) -> Iterator[AgentEvent]:
    """Build service dependency DAG and identify earliest failure frontier."""
    state.current_node = "topology_builder"

    start_ev = AgentEvent(
        type="state",
        node="topology_builder",
        payload={"status": "building_dag"},
    )
    state.record_event(start_ev)
    yield start_ev

    # 1. Build dependency DAG
    tc_dag = AgentEvent(
        type="tool_call",
        node="topology_builder",
        payload={"tool": "build_dependency_dag", "args": {"db": state.db_path}},
    )
    state.record_event(tc_dag)
    yield tc_dag

    dag = build_dependency_dag(state.db_path)
    state.dag = dag

    # 2. Pinpoint failure frontier
    tc_frontier = AgentEvent(
        type="tool_call",
        node="topology_builder",
        payload={"tool": "failure_frontier", "args": {"dag": "<graph>", "db": state.db_path}},
    )
    state.record_event(tc_frontier)
    yield tc_frontier

    frontier = failure_frontier(dag, db=state.db_path)
    state.failure_frontier = frontier

    nodes = list(dag.nodes()) if hasattr(dag, "nodes") else []
    edges = list(dag.edges()) if hasattr(dag, "edges") else []

    tr_dag = AgentEvent(
        type="tool_result",
        node="topology_builder",
        payload={
            "tool": "build_dependency_dag",
            "nodes": nodes,
            "edges": edges,
            "frontier": frontier,
        },
    )
    state.record_event(tr_dag)
    yield tr_dag

    state.current_node = "hypothesis_generator"
