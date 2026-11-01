from typing import Literal

from pydantic import BaseModel, Field


class Span(BaseModel):
    span_id: str
    trace_id: str
    parent_span_id: str | None
    service: str
    name: str
    start_ms: int
    end_ms: int
    status_code: int  # HTTP-like: 200/500/504; 0 = unset
    attrs: dict = {}
    t_corrected_start_ms: int | None = None
    orphan: bool = False


class LogLine(BaseModel):
    log_id: str
    trace_id: str | None
    span_id: str | None
    service: str
    ts_ms: int
    level: str
    message: str


class MetricPoint(BaseModel):
    service: str
    metric: str
    ts_ms: int
    value: float


class MetricOnset(BaseModel):
    metric: str
    service: str
    onset_corrected_ms: int | None
    peak_slope: float
    z_score: float
    baseline_mean: float
    baseline_std: float


class SimParams(BaseModel):
    pool_size: int = 20
    arrival_rate_per_s: float
    leak_prob: float
    mean_service_ms: float
    acquire_timeout_ms: int = 5000
    gateway_timeout_ms: int = 3000
    retry_max: int = 3
    window_s: int = 600
    seed: int = 0


class EvidencePointer(BaseModel):
    kind: Literal["span", "log", "metric"]
    id: str  # span_id | log_id | "<metric>@<t_corrected_ms>"
    note: str = ""


class CounterfactualResult(BaseModel):
    intervention: str  # "fix_leak" | "pool_size=50" | "no_retries"
    baseline_failure_prob: float
    intervened_failure_prob: float
    ci95: tuple[float, float]
    n_runs: int
    model_assumptions: list[str]


class TimelineEvent(BaseModel):
    order: int
    t_corrected_ms: int
    service: str
    description: str
    claim_type: Literal["verified_fact", "inference"]
    evidence: list[EvidencePointer]


class HypothesisEvaluated(BaseModel):
    id: str
    statement: str
    confidence: float = Field(ge=0, le=1)
    verdict: Literal["root_cause", "contributing_factor", "rejected", "unresolved"]
    supporting: list[EvidencePointer]
    refuting: list[EvidencePointer]
    counterfactual: CounterfactualResult | None = None
    missing_evidence: list[str] = Field(default_factory=list)


class IncidentReport(BaseModel):
    schema_version: str = "1.0"
    incident_detected: bool = True
    timeline_events: list[TimelineEvent]
    hypotheses_evaluated: list[HypothesisEvaluated]
    evidence_pointers: list[EvidencePointer]
    estimated_clock_offsets_s: dict[str, float]
    remediation: list[str]
    data_quality: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    investigation_seconds: float | None = None


class AgentEvent(BaseModel):
    type: Literal["state", "tool_call", "tool_result", "hypothesis", "report"]
    node: str
    payload: dict
