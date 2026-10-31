"""Hypothesis generator node for investigation pipeline (owned by P3)."""

from __future__ import annotations

from collections.abc import Iterator

from agent.scoring import evaluate_hypotheses_from_db
from agent.state import IncidentState
from contracts.models import AgentEvent


def hypothesis_generator_node(state: IncidentState) -> Iterator[AgentEvent]:
    """Formulate and score candidate hypotheses (H1-H4) against telemetry evidence."""
    state.current_node = "hypothesis_generator"

    start_ev = AgentEvent(
        type="state",
        node="hypothesis_generator",
        payload={"status": "evaluating_hypotheses"},
    )
    state.record_event(start_ev)
    yield start_ev

    # Dynamically score hypotheses from SQLite database telemetry
    hypotheses = evaluate_hypotheses_from_db(state.db_path)
    state.hypotheses = hypotheses

    for h in hypotheses:
        h_ev = AgentEvent(
            type="hypothesis",
            node="hypothesis_generator",
            payload=h.model_dump(),
        )
        state.record_event(h_ev)
        yield h_ev

    state.current_node = "counterfactual_verifier"
