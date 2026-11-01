"""Report synthesizer node for investigation pipeline (owned by P3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from agent.scoring import evaluate_hypotheses_from_db
from agent.state import IncidentState
from agent.validate import enforce_claim_types, validate_incident_report
from contracts.models import (
    AgentEvent,
    EvidencePointer,
    IncidentReport,
    TimelineEvent,
)
from tools.skew import estimate_clock_offsets

REMEDIATION_RECOMMENDATIONS = [
    "Set a sane max_pool_size tuned to database connection capacity.",
    "Ensure database connections are released in try-finally or context managers across all exit paths.",
    "Implement connection pool leak detection threshold and watchdog alerts on saturation.",
    "Configure client retries with exponential backoff and randomized jitter to prevent retry storms.",
    "Introduce gateway circuit breaker to shed load when downstream auth and database dependencies timeout.",
]


def synthesize_timeline_events(state: IncidentState) -> list[TimelineEvent]:
    """Reconstruct chronological timeline events from verified database telemetry."""
    events: list[TimelineEvent] = []
    offsets = state.clock_offsets or {}

    conn = sqlite3.connect(state.db_path)
    try:
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs'")
        has_logs = cur.fetchone() is not None
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spans'")
        has_spans = cur.fetchone() is not None

        # 1. Add anomalous metric onset events
        seen_metrics: set[str] = set()
        for o in state.metric_onsets:
            if o.onset_corrected_ms is not None and o.metric not in seen_metrics:
                seen_metrics.add(o.metric)
                cur.execute("SELECT service FROM metrics WHERE metric = ? LIMIT 1", (o.metric,))
                s_row = cur.fetchone()
                svc = s_row[0] if s_row else o.service
                corr_ms = int(offsets.get(svc, 0.0) * 1000)
                raw_ts = o.onset_corrected_ms + corr_ms

                # Find closest real reading
                cur.execute(
                    "SELECT ts_ms, value FROM metrics WHERE metric = ? AND abs(ts_ms - ?) <= 15000 LIMIT 1",
                    (o.metric, raw_ts),
                )
                pt_row = cur.fetchone()
                actual_raw_ts = pt_row[0] if pt_row else raw_ts

                events.append(
                    TimelineEvent(
                        order=len(events) + 1,
                        t_corrected_ms=o.onset_corrected_ms,
                        service=svc,
                        description=f"Anomalous metric onset detected on {svc} for metric {o.metric}.",
                        claim_type="verified_fact",
                        evidence=[
                            EvidencePointer(
                                kind="metric",
                                id=f"{o.metric}@{actual_raw_ts}",
                                note=f"Anomalous onset identified at corrected t={o.onset_corrected_ms}",
                            )
                        ],
                    )
                )

        # 2. Add first error span event
        if has_spans:
            cur.execute(
                "SELECT span_id, service, start_ms, status_code, name FROM spans WHERE status_code >= 500 ORDER BY start_ms ASC LIMIT 1"
            )
            sp_row = cur.fetchone()
            if sp_row:
                sp_id, sp_svc, sp_start, sp_code, sp_name = sp_row
                sp_offset = int(offsets.get(sp_svc, 0.0) * 1000)
                events.append(
                    TimelineEvent(
                        order=len(events) + 1,
                        t_corrected_ms=sp_start - sp_offset,
                        service=sp_svc,
                        description=f"First observed HTTP {sp_code} error span ({sp_name}) on service {sp_svc}.",
                        claim_type="verified_fact",
                        evidence=[
                            EvidencePointer(
                                kind="span",
                                id=sp_id,
                                note=f"First observed error span on {sp_svc} (status {sp_code})",
                            )
                        ],
                    )
                )

        # 3. Add first relevant error or leak log event
        if has_logs:
            cur.execute(
                "SELECT log_id, service, ts_ms, message FROM logs WHERE "
                "level IN ('ERROR', 'FATAL') OR message LIKE '%leak%' OR message LIKE '%release%' "
                "OR message LIKE '%timeout%' OR message LIKE '%retry%' ORDER BY ts_ms ASC LIMIT 1"
            )
            log_row = cur.fetchone()
            if log_row:
                lg_id, lg_svc, lg_ts, lg_msg = log_row
                lg_offset = int(offsets.get(lg_svc, 0.0) * 1000)
                events.append(
                    TimelineEvent(
                        order=len(events) + 1,
                        t_corrected_ms=lg_ts - lg_offset,
                        service=lg_svc,
                        description=f"Error log on service {lg_svc}: {lg_msg[:80]}.",
                        claim_type="verified_fact",
                        evidence=[
                            EvidencePointer(
                                kind="log",
                                id=lg_id,
                                note=f"Log: {lg_msg[:60]}",
                            )
                        ],
                    )
                )

    finally:
        conn.close()

    if not events:
        msg = "No anomalous timeline events could be reconstructed from available telemetry."
        if msg not in state.limitations:
            state.limitations.append(msg)
        return []

    # Sort strictly by corrected time ascending and assign sequential orders
    events.sort(key=lambda e: e.t_corrected_ms)
    for idx, e in enumerate(events, start=1):
        e.order = idx

    # Enforce verified_fact vs inference based on actual database resolution
    return enforce_claim_types(events, db=state.db_path)


def synthesize_incident_report(state: IncidentState) -> IncidentReport:
    """Build and validate a complete IncidentReport from current pipeline state."""
    # Ensure hypotheses and clock offsets exist
    if not state.hypotheses:
        state.hypotheses = evaluate_hypotheses_from_db(state.db_path)
    if not state.clock_offsets:
        state.clock_offsets = estimate_clock_offsets(state.db_path)

    # Attach counterfactuals if verified
    if "fix_leak" in state.counterfactuals:
        for h in state.hypotheses:
            if h.id == "H1":
                h.counterfactual = state.counterfactuals["fix_leak"]
            elif h.id == "H2" and "no_retries" in state.counterfactuals:
                h.counterfactual = state.counterfactuals["no_retries"]

    # Reconstruct timeline events from verified telemetry
    if not state.incident_detected:
        timeline_events = []
        state.timeline_events = []
        lim = "No incident detected: telemetry remains within healthy baseline bounds; no degradation observed."
        if lim not in state.limitations:
            state.limitations.append(lim)
        remediation_actions = [
            "No remediation required: telemetry indicates healthy operational baseline."
        ]
    else:
        timeline_events = synthesize_timeline_events(state)
        state.timeline_events = timeline_events
        remediation_actions = REMEDIATION_RECOMMENDATIONS

    # Collect and deduplicate all evidence pointers across timeline and hypotheses
    seen_pointers: set[str] = set()
    all_pointers: list[EvidencePointer] = []

    for ev in timeline_events:
        for ep in ev.evidence:
            key = f"{ep.kind}:{ep.id}"
            if key not in seen_pointers:
                seen_pointers.add(key)
                all_pointers.append(ep)

    for h in state.hypotheses:
        for ep in h.supporting + h.refuting:
            key = f"{ep.kind}:{ep.id}"
            if key not in seen_pointers:
                seen_pointers.add(key)
                all_pointers.append(ep)

    report = IncidentReport(
        schema_version="1.0",
        incident_detected=state.incident_detected,
        timeline_events=timeline_events,
        hypotheses_evaluated=state.hypotheses,
        evidence_pointers=all_pointers,
        estimated_clock_offsets_s=state.clock_offsets,
        remediation=remediation_actions,
        data_quality=state.data_quality,
        limitations=state.limitations,
        investigation_seconds=state.investigation_seconds,
    )

    # Validate synthesized report
    validation_issues = validate_incident_report(report, db=state.db_path)
    if validation_issues:
        raise ValueError(f"Synthesized report failed contract validation: {validation_issues}")

    state.report = report
    return report


def report_synthesizer_node(state: IncidentState) -> Iterator[AgentEvent]:
    """Synthesize structured, contract-compliant IncidentReport and stream event."""
    state.current_node = "report_synthesizer"

    start_ev = AgentEvent(
        type="state",
        node="report_synthesizer",
        payload={"status": "synthesizing_report"},
    )
    state.record_event(start_ev)
    yield start_ev

    report = synthesize_incident_report(state)

    rep_ev = AgentEvent(
        type="report",
        node="report_synthesizer",
        payload=report.model_dump(),
    )
    state.record_event(rep_ev)
    yield rep_ev
    state.current_node = "completed"
