"""Smoke test suite validating bootstrap contracts, models, fixtures, and stubs."""

import json
import sqlite3
from pathlib import Path

from contracts.constants import POOL_MAX, SCHEMA_VERSION, SERVICES, SKEW_MS
from contracts.models import (
    IncidentReport,
    LogLine,
    MetricPoint,
    Span,
)
from sim.fit import fit_sim_params
from sim.pool_sim import run_counterfactual
from tools.api import (
    build_dependency_dag,
    estimate_clock_offsets,
    failure_frontier,
    get_metric_derivatives,
    query_spans,
    rank_metric_onsets,
)

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_constants():
    assert SCHEMA_VERSION == "1.0"
    assert "auth-service" in SERVICES
    assert "gateway" in SERVICES
    assert "db-pool" in SERVICES
    assert SKEW_MS["auth-service"] == 12_000
    assert POOL_MAX == 20


def test_schema_sql():
    schema_path = ROOT_DIR / "contracts" / "schema.sql"
    assert schema_path.exists()
    conn = sqlite3.connect(":memory:")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    # verify tables exist
    cursor = conn.cursor()
    tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "spans" in tables
    assert "logs" in tables
    assert "metrics" in tables
    conn.close()


def test_fixtures_golden_report():
    golden_path = ROOT_DIR / "fixtures" / "golden_report.json"
    assert golden_path.exists()
    with open(golden_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    report = IncidentReport.model_validate(data)
    assert report.schema_version == "1.0"
    assert len(report.timeline_events) > 0
    assert any(h.verdict == "root_cause" for h in report.hypotheses_evaluated)
    assert "auth-service" in report.estimated_clock_offsets_s


def test_fixtures_tiny_telemetry():
    telemetry_path = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"
    assert telemetry_path.exists()
    count = 0
    spans, logs, metrics = 0, 0, 0
    with open(telemetry_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            count += 1
            rec = json.loads(line)
            rtype = rec.get("type")
            if rtype == "span":
                spans += 1
                Span.model_validate(rec)
            elif rtype == "log":
                logs += 1
                LogLine.model_validate(rec)
            elif rtype == "metric":
                metrics += 1
                MetricPoint.model_validate(rec)
            else:
                raise ValueError(f"Unknown type: {rtype}")
    assert count >= 30
    assert spans > 0
    assert logs > 0
    assert metrics > 0


def test_tools_stubs():
    conn = sqlite3.connect(":memory:")
    with open(ROOT_DIR / "contracts" / "schema.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    offsets = estimate_clock_offsets(conn)
    assert isinstance(offsets, dict)
    assert "auth-service" in offsets

    spans = query_spans(conn)
    assert isinstance(spans, list)

    onsets = rank_metric_onsets(conn)
    assert len(onsets) > 0

    deriv = get_metric_derivatives(conn, "db_pool.active_connections")
    assert deriv.metric == "db_pool.active_connections"

    dag = build_dependency_dag(conn)
    assert dag.number_of_nodes() > 0

    frontier = failure_frontier(dag)
    assert isinstance(frontier, str)
    conn.close()


def test_sim_stubs():
    params = fit_sim_params(":memory:")
    assert params.pool_size == 20
    cf = run_counterfactual(params, "fix_leak", n_runs=50)
    assert cf.intervention == "fix_leak"
    assert len(cf.ci95) == 2


def test_agent_investigation_stream():
    from agent.runner import run_investigation

    events = list(run_investigation(":memory:", mode="scripted"))
    event_types = [e.type for e in events]
    assert "state" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "hypothesis" in event_types
    assert "report" in event_types
