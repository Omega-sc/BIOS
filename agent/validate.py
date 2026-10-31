"""Validation module for incident report consistency and contract compliance (owned by P3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from contracts.constants import METRIC_SOURCES
from contracts.models import EvidencePointer, IncidentReport, TimelineEvent
from tools.skew import estimate_clock_offsets


def validate_evidence_pointer(
    pointer: EvidencePointer,
    db: str | Path | sqlite3.Connection,
) -> bool:
    """Validate that an EvidencePointer resolves to a real row in SQLite.

    Checks:
      - span: spans table for span_id
      - log: logs table for log_id
      - metric: metrics table for metric@ts (allowing tolerance for sampling intervals)
    """
    close_conn = False
    if isinstance(db, (str, Path)):
        conn = sqlite3.connect(str(db))
        close_conn = True
    else:
        conn = db

    try:
        cur = conn.cursor()
        if pointer.kind == "span":
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spans'")
            if not cur.fetchone():
                return False
            cur.execute("SELECT 1 FROM spans WHERE span_id = ? LIMIT 1", (pointer.id,))
            return cur.fetchone() is not None
        elif pointer.kind == "log":
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs'")
            if not cur.fetchone():
                return False
            cur.execute("SELECT 1 FROM logs WHERE log_id = ? LIMIT 1", (pointer.id,))
            return cur.fetchone() is not None
        elif pointer.kind == "metric":
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metrics'")
            if not cur.fetchone():
                return False
            parts = pointer.id.split("@")
            if len(parts) != 2:
                return False
            metric_name, ts_str = parts[0], parts[1]
            try:
                ts = int(ts_str)
            except ValueError:
                return False
            # Check exact match first
            cur.execute(
                "SELECT 1 FROM metrics WHERE metric = ? AND ts_ms = ? LIMIT 1",
                (metric_name, ts),
            )
            if cur.fetchone() is not None:
                return True
            # Allow proximity match within 15s sampling interval
            cur.execute(
                "SELECT 1 FROM metrics WHERE metric = ? AND abs(ts_ms - ?) <= 15000 LIMIT 1",
                (metric_name, ts),
            )
            if cur.fetchone() is not None:
                return True
            # Allow clock-skew-corrected match
            try:
                offsets = estimate_clock_offsets(conn)
                cur.execute("SELECT service FROM metrics WHERE metric = ? LIMIT 1", (metric_name,))
                s_row = cur.fetchone()
                svc = s_row[0] if s_row else METRIC_SOURCES.get(metric_name)
                corr_ms = int(offsets.get(svc, 0.0) * 1000) if svc else 0
                if corr_ms != 0:
                    cur.execute(
                        "SELECT 1 FROM metrics WHERE metric = ? AND abs((ts_ms - ?) - ?) <= 15000 LIMIT 1",
                        (metric_name, corr_ms, ts),
                    )
                    if cur.fetchone() is not None:
                        return True
            except Exception:
                pass
            return False
        return False
    finally:
        if close_conn:
            conn.close()


def enforce_claim_types(
    events: list[TimelineEvent],
    db: str | Path | sqlite3.Connection,
) -> list[TimelineEvent]:
    """Ensure claim_type == 'verified_fact' only when evidence pointers resolve to real DB rows.

    Otherwise, demotes claim_type to 'inference'.
    """
    enforced = []
    for ev in events:
        ev_copy = ev.model_copy(deep=True)
        if ev_copy.claim_type == "verified_fact":
            has_valid_evidence = False
            for ep in ev_copy.evidence:
                if validate_evidence_pointer(ep, db):
                    has_valid_evidence = True
                    break
            if not has_valid_evidence:
                ev_copy.claim_type = "inference"
        enforced.append(ev_copy)
    return enforced


def has_telemetry_tables(db: str | Path | sqlite3.Connection | None) -> bool:
    """Return True if the database contains spans, logs, or metrics tables."""
    if db is None:
        return False
    if isinstance(db, (str, Path)) and str(db) == ":memory:":
        return False
    close_conn = False
    if isinstance(db, (str, Path)):
        conn = sqlite3.connect(str(db))
        close_conn = True
    else:
        conn = db
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('spans', 'logs', 'metrics') LIMIT 1"
        )
        return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        if close_conn:
            conn.close()


def validate_timeline_events(
    events: list[TimelineEvent],
    db: str | Path | sqlite3.Connection | None = None,
) -> list[str]:
    """Validate chronological ordering and evidence resolution for timeline events."""
    issues = []
    if not events:
        issues.append("Report must include reconstructed timeline events.")
        return issues

    check_db = has_telemetry_tables(db)
    prev_ts = -1
    for ev in events:
        if ev.t_corrected_ms < prev_ts:
            issues.append(
                f"Timeline event {ev.order} timestamp {ev.t_corrected_ms} is earlier than previous event timestamp {prev_ts}."
            )
        prev_ts = ev.t_corrected_ms

        if not ev.evidence:
            issues.append(f"Timeline event {ev.order} has no supporting evidence pointers.")
        elif check_db:
            if ev.claim_type == "verified_fact":
                valid_any = any(validate_evidence_pointer(ep, db) for ep in ev.evidence)
                if not valid_any:
                    issues.append(
                        f"Timeline event {ev.order} claimed as 'verified_fact' has no evidence resolving to real rows in database."
                    )

    return issues


def validate_incident_report(
    report: IncidentReport,
    db: str | Path | sqlite3.Connection | None = None,
) -> list[str]:
    """Validate incident report consistency, contract compliance, and evidence resolution.

    Ensures:
      1. At least one root_cause is established.
      2. Timeline events are present and chronologically ordered by t_corrected_ms.
      3. All evidence pointers resolve to real rows in SQLite when db is provided.
      4. Remediation actions are present.
    """
    issues = []

    # 1. Root cause verification
    root_causes = [h for h in report.hypotheses_evaluated if h.verdict == "root_cause"]
    if not root_causes:
        issues.append("Report must evaluate and establish at least one root_cause.")

    # 2. Timeline validation
    issues.extend(validate_timeline_events(report.timeline_events, db=db))

    # 3. Evidence pointers verification against DB
    check_db = has_telemetry_tables(db)
    if check_db:
        for ep in report.evidence_pointers:
            if not validate_evidence_pointer(ep, db):
                issues.append(
                    f"Evidence pointer {ep.kind}:{ep.id} does not resolve to a real row in database."
                )

        for ev in report.timeline_events:
            for ep in ev.evidence:
                if not validate_evidence_pointer(ep, db):
                    issues.append(
                        f"Timeline event {ev.order} evidence pointer {ep.kind}:{ep.id} does not resolve to a real row in database."
                    )

        for h in report.hypotheses_evaluated:
            for ep in h.supporting + h.refuting:
                if not validate_evidence_pointer(ep, db):
                    issues.append(
                        f"Hypothesis {h.id} evidence pointer {ep.kind}:{ep.id} does not resolve to a real row in database."
                    )

    # 4. Remediation validation
    if not report.remediation:
        issues.append("Report must provide concrete remediation recommendations.")

    return issues
