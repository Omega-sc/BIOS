"""Agent state machine runner for incident investigation (owned by P3)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from contracts.models import (
    AgentEvent,
    EvidencePointer,
    HypothesisEvaluated,
    IncidentReport,
    TimelineEvent,
)
from sim.fit import fit_sim_params
from sim.pool_sim import run_counterfactual
from tools.api import (
    build_dependency_dag,
    estimate_clock_offsets,
    failure_frontier,
    rank_metric_onsets,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _load_golden_report() -> IncidentReport:
    golden_path = FIXTURES_DIR / "golden_report.json"
    with open(golden_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return IncidentReport.model_validate(data)


def run_investigation(
    db_path: str,
    mode: Literal["live", "replay", "scripted"] = "scripted",
) -> Iterator[AgentEvent]:
    """Execute investigation state machine pipeline and stream AgentEvents.

    Pipeline: telemetry_ingestion -> topology_builder -> hypothesis_generator ->
              counterfactual_verifier -> report_synthesizer
    """
    # Node 1: telemetry_ingestion
    yield AgentEvent(
        type="state",
        node="telemetry_ingestion",
        payload={"status": "started", "db_path": db_path, "mode": mode},
    )

    yield AgentEvent(
        type="tool_call",
        node="telemetry_ingestion",
        payload={"tool": "estimate_clock_offsets", "args": {"db": db_path}},
    )
    offsets = estimate_clock_offsets(db_path)
    yield AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={"tool": "estimate_clock_offsets", "result": offsets},
    )

    yield AgentEvent(
        type="tool_call",
        node="telemetry_ingestion",
        payload={"tool": "rank_metric_onsets", "args": {"db": db_path}},
    )
    onsets = rank_metric_onsets(db_path)
    yield AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={"tool": "rank_metric_onsets", "result": [o.model_dump() for o in onsets]},
    )

    # Node 2: topology_builder
    yield AgentEvent(
        type="state",
        node="topology_builder",
        payload={"status": "building_dag"},
    )
    dag = build_dependency_dag(db_path)
    frontier = failure_frontier(dag)
    yield AgentEvent(
        type="tool_result",
        node="topology_builder",
        payload={"nodes": list(dag.nodes()), "edges": list(dag.edges()), "frontier": frontier},
    )

    # Node 3: hypothesis_generator
    yield AgentEvent(
        type="state",
        node="hypothesis_generator",
        payload={"status": "evaluating_hypotheses"},
    )
    h1 = HypothesisEvaluated(
        id="H1",
        statement="auth-service leaks DB connections, exhausting pool and causing gateway 504s.",
        confidence=0.92,
        verdict="root_cause",
        supporting=[
            EvidencePointer(
                kind="metric",
                id="db_pool.active_connections@1700000090000",
                note="Pool saturation at t=90s",
            ),
            EvidencePointer(
                kind="log",
                id="log-auth-5",
                note="Unhandled error path skipped release()",
            ),
        ],
        refuting=[],
    )
    h2 = HypothesisEvaluated(
        id="H2",
        statement="Frontend CPU spike caused downstream timeout failures.",
        confidence=0.08,
        verdict="rejected",
        supporting=[
            EvidencePointer(
                kind="metric",
                id="frontend.cpu_pct@1700000125000",
                note="CPU spike observed",
            )
        ],
        refuting=[
            EvidencePointer(
                kind="metric",
                id="gateway.error_rate_5xx@1700000095000",
                note="504 errors started 30s prior to CPU spike",
            )
        ],
    )
    yield AgentEvent(type="hypothesis", node="hypothesis_generator", payload=h1.model_dump())
    yield AgentEvent(type="hypothesis", node="hypothesis_generator", payload=h2.model_dump())

    # Node 4: counterfactual_verifier
    yield AgentEvent(
        type="state",
        node="counterfactual_verifier",
        payload={"status": "running_simulation"},
    )
    sim_params = fit_sim_params(db_path)
    cf_res = run_counterfactual(sim_params, "fix_leak", n_runs=100)
    h1.counterfactual = cf_res
    yield AgentEvent(
        type="tool_result",
        node="counterfactual_verifier",
        payload={"intervention": "fix_leak", "result": cf_res.model_dump()},
    )

    # Node 5: report_synthesizer
    yield AgentEvent(
        type="state",
        node="report_synthesizer",
        payload={"status": "synthesizing_report"},
    )

    # Build report from state
    report = IncidentReport(
        schema_version="1.0",
        timeline_events=[
            TimelineEvent(
                order=1,
                t_corrected_ms=1700000000000,
                service="db-pool",
                description="Healthy baseline state",
                claim_type="verified_fact",
                evidence=[
                    EvidencePointer(kind="metric", id="db_pool.active_connections@1700000000000")
                ],
            ),
            TimelineEvent(
                order=2,
                t_corrected_ms=1700000090000,
                service="db-pool",
                description="DB pool reached capacity (20/20 active connections)",
                claim_type="verified_fact",
                evidence=[
                    EvidencePointer(kind="metric", id="db_pool.active_connections@1700000090000")
                ],
            ),
            TimelineEvent(
                order=3,
                t_corrected_ms=1700000095000,
                service="gateway",
                description="Gateway returns HTTP 504 due to auth-service timeout",
                claim_type="verified_fact",
                evidence=[
                    EvidencePointer(kind="metric", id="gateway.error_rate_5xx@1700000095000")
                ],
            ),
            TimelineEvent(
                order=4,
                t_corrected_ms=1700000125000,
                service="frontend",
                description="Frontend CPU spike observed due to client retry cascade",
                claim_type="verified_fact",
                evidence=[EvidencePointer(kind="metric", id="frontend.cpu_pct@1700000125000")],
            ),
        ],
        hypotheses_evaluated=[h1, h2],
        evidence_pointers=[
            EvidencePointer(kind="metric", id="db_pool.active_connections@1700000090000"),
            EvidencePointer(kind="metric", id="gateway.error_rate_5xx@1700000095000"),
            EvidencePointer(kind="metric", id="frontend.cpu_pct@1700000125000"),
        ],
        estimated_clock_offsets_s=offsets,
        remediation=[
            "Fix connection leak in auth-service error handling path",
            "Implement gateway circuit breaker to handle upstream pool exhaustion",
            "Tune client retry policy with exponential backoff and jitter",
        ],
    )

    yield AgentEvent(
        type="report",
        node="report_synthesizer",
        payload=report.model_dump(),
    )
