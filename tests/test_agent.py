"""Unit test suite for Agent Pipeline (owned by P3).

Tests:
  - IncidentState definitions and state progression
  - Pipeline nodes (telemetry_ingestion, topology_builder, hypothesis_generator, counterfactual_verifier, report_synthesizer)
  - Scripted, Replay, and Live investigation modes
  - Hypothesis confidence scoring and likelihood ratio updating
  - Contract and evidence pointer validation against SQLite database
  - Counterfactual simulation verification and verdict reasoning
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.llm import LLMAdapter
from agent.runner import run_investigation
from agent.scoring import evaluate_hypotheses_from_db, score_hypothesis
from agent.state import IncidentState, InvestigationState
from agent.validate import (
    enforce_claim_types,
    validate_evidence_pointer,
    validate_incident_report,
    validate_timeline_events,
)
from contracts.models import (
    AgentEvent,
    EvidencePointer,
    HypothesisEvaluated,
    IncidentReport,
    TimelineEvent,
)
from ingest.load_jsonl import load_telemetry

ROOT_DIR = Path(__file__).resolve().parent.parent
INCIDENT_DB = ROOT_DIR / "data" / "incident.db"
TINY_JSONL = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"


def test_incident_state_and_alias():
    state = IncidentState(db_path="data/incident.db", mode="scripted")
    assert state.db_path == "data/incident.db"
    assert state.mode == "scripted"
    assert state.current_node == "telemetry_ingestion"
    assert state.step_count == 0

    ev = AgentEvent(type="state", node="telemetry_ingestion", payload={"status": "init"})
    state.record_event(ev)
    assert state.step_count == 1
    assert len(state.events) == 1

    # Verify backwards-compatibility alias
    assert InvestigationState is IncidentState


def test_scripted_investigation_on_primary_incident_db():
    assert INCIDENT_DB.exists(), "Primary incident.db must exist"
    events = list(run_investigation(str(INCIDENT_DB), mode="scripted"))
    assert len(events) >= 20

    event_types = [e.type for e in events]
    assert "state" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "hypothesis" in event_types
    assert "report" in event_types

    # Node sequence verification
    nodes_visited = [e.node for e in events if e.type == "state"]
    expected_sequence = [
        "telemetry_ingestion",
        "topology_builder",
        "hypothesis_generator",
        "counterfactual_verifier",
        "report_synthesizer",
    ]
    assert nodes_visited == expected_sequence

    # Extract synthesized report
    report_events = [e for e in events if e.type == "report"]
    assert len(report_events) == 1
    report = IncidentReport.model_validate(report_events[0].payload)

    # 1. Root cause verification
    root_causes = [h for h in report.hypotheses_evaluated if h.verdict == "root_cause"]
    assert len(root_causes) == 1
    h1 = root_causes[0]
    assert h1.id == "H1"
    assert h1.confidence >= 0.85
    assert h1.counterfactual is not None
    assert h1.counterfactual.intervention == "fix_leak"
    assert h1.counterfactual.intervened_failure_prob <= 0.05

    # 2. Contributing factor verification
    contributing = [h for h in report.hypotheses_evaluated if h.verdict == "contributing_factor"]
    assert len(contributing) >= 1
    h2 = contributing[0]
    assert h2.id == "H2"
    assert h2.counterfactual is not None
    assert h2.counterfactual.intervention == "no_retries"
    assert h2.counterfactual.intervened_failure_prob > 0.05

    # 3. Rejected hypotheses
    rejected = [h for h in report.hypotheses_evaluated if h.verdict == "rejected"]
    rejected_ids = {h.id for h in rejected}
    assert "H3" in rejected_ids
    assert "H4" in rejected_ids

    # 4. Timeline ordering (chronological by corrected time)
    assert len(report.timeline_events) >= 4
    prev_ts = -1
    for ev in report.timeline_events:
        assert ev.t_corrected_ms >= prev_ts
        prev_ts = ev.t_corrected_ms
        assert len(ev.evidence) > 0

    # 5. Database evidence resolution: all pointers must resolve to real rows
    issues = validate_incident_report(report, db=str(INCIDENT_DB))
    assert issues == [], f"Report failed DB validation: {issues}"


def test_scripted_investigation_on_tiny_telemetry(tmp_path: Path):
    assert TINY_JSONL.exists()
    tiny_db = tmp_path / "tiny_test.db"
    load_telemetry(TINY_JSONL, tiny_db)

    events = list(run_investigation(str(tiny_db), mode="scripted"))
    assert len(events) >= 15

    report_events = [e for e in events if e.type == "report"]
    assert len(report_events) == 1
    report = IncidentReport.model_validate(report_events[0].payload)
    assert any(h.verdict == "root_cause" for h in report.hypotheses_evaluated)

    issues = validate_incident_report(report, db=str(tiny_db))
    assert issues == []


def test_replay_mode_offline():
    # Primary incident replay
    events_replay = list(run_investigation(str(INCIDENT_DB), mode="replay"))
    assert len(events_replay) >= 20
    assert any(e.type == "report" for e in events_replay)

    # Tiny telemetry replay
    events_tiny = list(run_investigation("fixtures/tiny_telemetry.jsonl", mode="replay"))
    assert len(events_tiny) >= 15
    assert any(e.type == "report" for e in events_tiny)


def test_live_mode_offline_fallback():
    events_live = list(run_investigation(str(INCIDENT_DB), mode="live"))
    assert len(events_live) >= 10
    event_types = set(e.type for e in events_live)
    assert "state" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "report" in event_types

    report_ev = [e for e in events_live if e.type == "report"][0]
    report = IncidentReport.model_validate(report_ev.payload)
    assert any(h.verdict == "root_cause" for h in report.hypotheses_evaluated)
    assert len(report.remediation) >= 3


def test_scoring_module_functionality():
    # 1. Custom odds updating
    s_ptrs = [EvidencePointer(kind="metric", id="m@1"), EvidencePointer(kind="log", id="l@1")]
    r_ptrs = [EvidencePointer(kind="metric", id="m@2")]
    conf = score_hypothesis(s_ptrs, r_ptrs, prior=0.5)
    assert 0.01 <= conf <= 0.99

    # 2. Database hypothesis scoring
    hyps = evaluate_hypotheses_from_db(str(INCIDENT_DB))
    assert len(hyps) == 4
    hyp_map = {h.id: h for h in hyps}
    assert hyp_map["H1"].confidence > hyp_map["H2"].confidence
    assert hyp_map["H1"].confidence > hyp_map["H3"].confidence
    assert hyp_map["H1"].confidence > hyp_map["H4"].confidence

    # Normalized confidences sum approximately to 1.0
    total_conf = sum(h.confidence for h in hyps)
    assert pytest.approx(total_conf, abs=0.05) == 1.0


def test_validation_module_edge_cases():
    # 1. Valid pointer
    valid_ptr = EvidencePointer(kind="metric", id="db_pool.active_connections@1700000000000")
    assert validate_evidence_pointer(valid_ptr, str(INCIDENT_DB)) is True

    # 2. Invalid span and log pointer
    invalid_span = EvidencePointer(kind="span", id="non-existent-span-id")
    assert validate_evidence_pointer(invalid_span, str(INCIDENT_DB)) is False

    invalid_log = EvidencePointer(kind="log", id="non-existent-log-id")
    assert validate_evidence_pointer(invalid_log, str(INCIDENT_DB)) is False

    # 3. Timeline event claim enforcement
    events = [
        TimelineEvent(
            order=1,
            t_corrected_ms=100,
            service="gateway",
            description="Fake claim",
            claim_type="verified_fact",
            evidence=[invalid_span],
        )
    ]
    enforced = enforce_claim_types(events, str(INCIDENT_DB))
    assert enforced[0].claim_type == "inference"

    # 4. Out-of-order timeline events
    bad_order_events = [
        TimelineEvent(
            order=1,
            t_corrected_ms=200,
            service="gateway",
            description="Later",
            claim_type="inference",
            evidence=[valid_ptr],
        ),
        TimelineEvent(
            order=2,
            t_corrected_ms=100,
            service="gateway",
            description="Earlier",
            claim_type="inference",
            evidence=[valid_ptr],
        ),
    ]
    issues = validate_timeline_events(bad_order_events, db=str(INCIDENT_DB))
    assert any("earlier than previous" in issue for issue in issues)

    # 5. Missing root cause
    empty_report = IncidentReport(
        schema_version="1.0",
        timeline_events=[],
        hypotheses_evaluated=[
            HypothesisEvaluated(
                id="H1",
                statement="test",
                confidence=0.5,
                verdict="rejected",
                supporting=[],
                refuting=[],
            )
        ],
        evidence_pointers=[],
        estimated_clock_offsets_s={},
        remediation=[],
    )
    report_issues = validate_incident_report(empty_report)
    assert any("root_cause" in issue for issue in report_issues)
    assert any("timeline" in issue for issue in report_issues)
    assert any("remediation" in issue for issue in report_issues)


def test_llm_adapter_complete():
    adapter = LLMAdapter(api_key="mock-test-key")
    resp = adapter.complete("Summarize incident status")
    assert isinstance(resp, str)
    assert len(resp) > 0


def test_scripted_investigation_p2_tools_and_counterfactual_coverage():
    events = list(run_investigation(str(INCIDENT_DB), mode="scripted"))
    tool_calls = [e.payload.get("tool") for e in events if e.type == "tool_call"]

    # All required P2 tools must be invoked in scripted mode
    expected_tools = {
        "estimate_clock_offsets",
        "rank_metric_onsets",
        "get_metric_derivatives",
        "query_spans",
        "build_dependency_dag",
        "failure_frontier",
        "fit_sim_params",
        "run_counterfactual",
    }
    assert expected_tools.issubset(set(tool_calls))

    # All three required counterfactual interventions executed
    cf_calls = [
        e.payload.get("args", {}).get("intervention")
        for e in events
        if e.type == "tool_call" and e.payload.get("tool") == "run_counterfactual"
    ]
    assert "fix_leak" in cf_calls
    assert "pool_size=50" in cf_calls
    assert "no_retries" in cf_calls


def test_dynamic_hypothesis_evaluation_across_scenarios(tmp_path: Path):
    """Verify hypothesis evaluation dynamically identifies root cause without reading ground truth."""
    scenarios_to_test = {
        "slow_db_101": "H3",
        "gateway_timeout_misconfig_101": "H4",
    }
    for sc, expected_rc in scenarios_to_test.items():
        jsonl_path = ROOT_DIR / "data" / "scenarios" / f"{sc}.jsonl"
        if not jsonl_path.exists():
            continue
        sc_db = tmp_path / f"{sc}.db"
        load_telemetry(jsonl_path, sc_db)

        hyps = evaluate_hypotheses_from_db(str(sc_db))
        best_hyp = max(hyps, key=lambda h: h.confidence)
        assert best_hyp.id == expected_rc, (
            f"Scenario {sc} expected {expected_rc}, got {best_hyp.id}"
        )
        assert best_hyp.verdict == "root_cause"


def test_skew_corrected_metric_evidence_resolution():
    """Verify EvidencePointer validation resolves clock-skew-corrected metric timestamps."""
    # auth-service metric with skew
    auth_ptr = EvidencePointer(kind="metric", id="auth.latency_p99@1700000000000")
    assert validate_evidence_pointer(auth_ptr, str(INCIDENT_DB)) is True


def test_live_mode_validation_exhaustion_fallback():
    """Verify live mode falls back to synthesized report when model submission fails validation."""
    from agent.runner import _run_live_mode

    # Trigger live mode with low max_steps
    events = list(_run_live_mode(str(INCIDENT_DB), max_steps=12))
    report_events = [e for e in events if e.type == "report"]
    assert len(report_events) == 1
    rep = IncidentReport.model_validate(report_events[0].payload)
    assert validate_incident_report(rep, db=str(INCIDENT_DB)) == []
