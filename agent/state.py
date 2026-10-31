"""State machine definitions for incident investigation agent (owned by P3)."""

from typing import Any

from pydantic import BaseModel, Field

from contracts.models import HypothesisEvaluated, IncidentReport, MetricOnset, Span


class InvestigationState(BaseModel):
    db_path: str
    current_node: str = "telemetry_ingestion"
    clock_offsets: dict[str, float] = Field(default_factory=dict)
    topology_dag: Any = None  # nx.DiGraph
    spans: list[Span] = Field(default_factory=list)
    metric_onsets: list[MetricOnset] = Field(default_factory=list)
    hypotheses: list[HypothesisEvaluated] = Field(default_factory=list)
    report: IncidentReport | None = None
    messages: list[dict] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
