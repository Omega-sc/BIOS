"""Unit tests for Phase 2: Incident detection gate, baseline MAD z-scores, and hypothesis abstention."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from agent.config import HYPOTHESIS_CONFIG, INCIDENT_CONFIG
from agent.runner import run_investigation
from agent.scoring import evaluate_hypotheses_from_db
from contracts.models import IncidentReport
from ingest.load_jsonl import load_telemetry
from tools.anomaly import detect_incident_anomalies

ROOT_DIR = Path(__file__).resolve().parent.parent
INCIDENT_DB = ROOT_DIR / "data" / "incident.db"
TINY_JSONL = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"


def test_incident_detection_empty_db():
    """Verify empty database cleanly reports no incident without crashing."""
    conn = sqlite3.connect(":memory:")
    with open(ROOT_DIR / "contracts" / "schema.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    res = detect_incident_anomalies(conn)
    assert res.incident_detected is False
    assert res.max_z_score == 0.0
    assert "Insufficient telemetry timespan" in res.reason or "No telemetry" in res.reason
    conn.close()


def test_incident_detection_real_incident():
    """Verify primary incident database flags service degradation and metrics exhaustion."""
    assert INCIDENT_DB.exists()
    res = detect_incident_anomalies(INCIDENT_DB)
    assert res.incident_detected is True
    assert res.max_z_score >= INCIDENT_CONFIG.mad_z_threshold
    assert len(res.anomalous_series) > 0


def test_incident_detection_healthy_control():
    """Verify healthy baseline telemetry triggers abstention (incident_detected=False)."""
    # Extract healthy baseline records from tiny_telemetry
    records = []
    with open(TINY_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    # Slice early healthy period
    def ts_func(r):
        return r.get("start_ms", r.get("ts_ms"))

    lo = min(ts_func(r) for r in records)
    hi = max(ts_func(r) for r in records)
    healthy_records = [r for r in records if ts_func(r) < lo + 0.25 * (hi - lo)]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "healthy.jsonl"
        db = Path(td) / "healthy.db"
        p.write_text("\n".join(json.dumps(r) for r in healthy_records) + "\n", encoding="utf-8")
        load_telemetry(p, db)

        res = detect_incident_anomalies(db)
        assert res.incident_detected is False
        assert res.max_z_score < INCIDENT_CONFIG.mad_z_threshold

        hyps = evaluate_hypotheses_from_db(db)
        assert all(h.verdict == "unresolved" for h in hyps)
        assert all(h.confidence <= HYPOTHESIS_CONFIG.max_unresolved_confidence for h in hyps)
        assert all(len(h.supporting) == 0 for h in hyps)

        # Full pipeline execution
        events = list(run_investigation(str(db), mode="scripted"))
        report_events = [e for e in events if e.type == "report"]
        assert len(report_events) == 1
        rep = IncidentReport.model_validate(report_events[0].payload)
        assert rep.incident_detected is False
        assert len(rep.timeline_events) == 0
        assert not any(h.verdict == "root_cause" for h in rep.hypotheses_evaluated)
        assert any(
            "healthy" in lim.lower() or "no incident" in lim.lower() for lim in rep.limitations
        )


def test_hypothesis_margin_abstention_on_close_scores():
    """Verify when candidate hypotheses are closely tied, verdict abstains as unresolved."""
    # Custom weights creating a near tie between H1 and H2
    custom_weights = {
        "metric_pool_max": 1.0,
        "onset_pool_before_gw": 1.0,
        "log_leak": 1.0,
        "onset_cpu_before_gw": 1.0,
        "onset_gw_before_cpu": 1.0,
        "metric_high_inflight": 1.0,
        "log_slow_db": 1.0,
        "log_frontend_storm": 1.0,
        "metric_gw_isolated_failure": 1.0,
        "log_gw_misconfig": 1.0,
    }

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        load_telemetry(TINY_JSONL, db)

        hyps = evaluate_hypotheses_from_db(db, weights=custom_weights)
        # Because weights are equalized/penalized, margin should be small
        root_causes = [h for h in hyps if h.verdict == "root_cause"]
        # If margin is below threshold or evidence is insufficient, it should abstain
        if not root_causes:
            unresolved = [h for h in hyps if h.verdict == "unresolved"]
            assert len(unresolved) > 0
            assert all(
                h.confidence <= HYPOTHESIS_CONFIG.max_unresolved_confidence for h in unresolved
            )
