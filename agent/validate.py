"""Validation module for incident report consistency and contract compliance (owned by P3)."""

from contracts.models import IncidentReport


def validate_incident_report(report: IncidentReport) -> list[str]:
    """Validate that report contains at least one root cause and evidence pointers."""
    issues = []
    root_causes = [h for h in report.hypotheses_evaluated if h.verdict == "root_cause"]
    if not root_causes:
        issues.append("Report must evaluate and establish at least one root_cause.")
    if not report.timeline_events:
        issues.append("Report must include reconstructed timeline events.")
    for ev in report.timeline_events:
        if not ev.evidence:
            issues.append(f"Timeline event {ev.order} has no supporting evidence pointers.")
    return issues
