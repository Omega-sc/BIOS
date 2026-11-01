"""State definitions for autonomous incident investigation agent (owned by P3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.models import (
    AgentEvent,
    CounterfactualResult,
    HypothesisEvaluated,
    IncidentReport,
    LogLine,
    MetricOnset,
    MetricPoint,
    SimParams,
    Span,
    TimelineEvent,
)


class IncidentState(BaseModel):
    """Execution state tracked across pipeline nodes in the investigation agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    db_path: str
    mode: Literal["live", "replay", "scripted"] = "scripted"
    current_node: str = "telemetry_ingestion"

    # Ingestion & skew correction
    clock_offsets: dict[str, float] = Field(default_factory=dict)
    spans: list[Span] = Field(default_factory=list)
    logs: list[LogLine] = Field(default_factory=list)
    metrics: list[MetricPoint] = Field(default_factory=list)
    metric_onsets: list[MetricOnset] = Field(default_factory=list)

    # Topology & failure frontier
    dag: Any = None  # nx.DiGraph or node-link dict
    failure_frontier: str | None = None

    # Hypotheses & scoring
    hypotheses: list[HypothesisEvaluated] = Field(default_factory=list)

    # Simulation & counterfactual verification
    sim_params: SimParams | None = None
    counterfactuals: dict[str, CounterfactualResult] = Field(default_factory=dict)

    # Timeline & synthesized report
    timeline_events: list[TimelineEvent] = Field(default_factory=list)
    incident_detected: bool = True
    data_quality: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    investigation_seconds: float | None = None
    report: IncidentReport | None = None

    # Step tracing and LLM messaging
    step_count: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)

    def record_event(self, event: AgentEvent) -> None:
        """Append an event to trace and increment step count."""
        self.events.append(event)
        self.step_count += 1


# Backwards compatibility alias
InvestigationState = IncidentState
