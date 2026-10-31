"""Dataset sanity verification suite for Phase B / Gate G1."""

import json
import sqlite3
from pathlib import Path

from contracts.constants import POOL_MAX, T0_MS
from contracts.models import LogLine, MetricPoint, Span
from ingest.load_jsonl import load_telemetry

ROOT_DIR = Path(__file__).resolve().parent.parent
TELEMETRY_PATH = ROOT_DIR / "data" / "telemetry.jsonl"
GROUND_TRUTH_PATH = ROOT_DIR / "data" / "ground_truth.json"
VERSION_PATH = ROOT_DIR / "data" / "VERSION"
DB_PATH = ROOT_DIR / "data" / "incident.db"


def test_version_frozen():
    assert VERSION_PATH.exists()
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    assert version == "1"


def test_dataset_size_and_models():
    assert TELEMETRY_PATH.exists()
    line_count = 0
    spans = 0
    logs = 0
    metrics = 0

    with open(TELEMETRY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line_count += 1
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
                raise ValueError(f"Unknown record type: {rtype}")

    # ±15% of 5,000: 4,250 to 5,750 lines
    assert 4250 <= line_count <= 5750
    # ~800 traces -> ~2,200 to ~2,400 spans
    assert 2000 <= spans <= 2600
    # ~1,000 metrics
    assert 900 <= metrics <= 1400
    # logs after 15% drop
    assert 1300 <= logs <= 1900


def test_ground_truth_metadata():
    assert GROUND_TRUTH_PATH.exists()
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        gt = json.load(f)

    assert gt["scenario"] == "pool_leak"
    assert gt["root_cause_service"] == "auth-service"
    assert gt["failing_resource"] == "db-pool"
    assert gt["red_herring_metric"] == "frontend.cpu_pct"
    assert gt["clock_skews_ms"]["auth-service"] == 12_000

    # Verify timeline chronology: leak < pool_saturated < first_504 < red_herring
    assert gt["true_leak_onset_ms"] < gt["pool_exhausted_onset_ms"]
    assert gt["pool_exhausted_onset_ms"] < gt["first_504_onset_ms"]
    assert gt["first_504_onset_ms"] < gt["red_herring_onset_ms"]

    # Verify counts match telemetry file
    records = [json.loads(line) for line in open(TELEMETRY_PATH, "r", encoding="utf-8")]
    traces = set(r["trace_id"] for r in records if r.get("trace_id"))
    assert len(records) == gt["total_records"]
    assert len(traces) == gt["total_traces"]


def test_database_ingestion():
    temp_db = ROOT_DIR / "data" / "test_verify.db"
    if temp_db.exists():
        temp_db.unlink()
    try:
        counts = load_telemetry(TELEMETRY_PATH, temp_db)
        assert counts["spans"] > 2000
        assert counts["logs"] > 1300
        assert counts["metrics"] > 900

        conn = sqlite3.connect(str(temp_db))
        cur = conn.cursor()

        # Check spans
        cur.execute("SELECT COUNT(*) FROM spans")
        assert cur.fetchone()[0] == counts["spans"]

        # Check logs
        cur.execute("SELECT COUNT(*) FROM logs")
        assert cur.fetchone()[0] == counts["logs"]

        # Check metrics
        cur.execute("SELECT COUNT(*) FROM metrics")
        assert cur.fetchone()[0] == counts["metrics"]

        # Check clock skew presence in traces
        cur.execute(
            """
            SELECT g.start_ms, a.start_ms
            FROM spans g
            JOIN spans a ON a.parent_span_id = g.span_id
            WHERE g.service = 'gateway' AND a.service = 'auth-service'
            LIMIT 10
            """
        )
        rows = cur.fetchall()
        assert len(rows) > 0
        for gw_start, auth_start in rows:
            # auth_start is ~12,000ms ahead of gw_start due to clock skew
            diff = auth_start - gw_start
            assert 11_000 <= diff <= 13_000

        # Check pool saturation onset
        cur.execute(
            "SELECT MIN(ts_ms) FROM metrics WHERE metric = 'db_pool.active_connections' AND value >= ?",
            (float(POOL_MAX),),
        )
        pool_sat_ts = cur.fetchone()[0]
        assert pool_sat_ts is not None
        # Pool hits 20/20 at L+90s = 120s + 90s = 210s = T0 + 210_000ms
        assert abs(pool_sat_ts - (T0_MS + 210_000)) <= 5000

        # Check first 504 on gateway
        cur.execute(
            "SELECT MIN(start_ms) FROM spans WHERE service = 'gateway' AND status_code = 504"
        )
        first_504_ts = cur.fetchone()[0]
        assert first_504_ts is not None
        # First 504 at L+95s = 215s = T0 + 215_000ms
        assert abs(first_504_ts - (T0_MS + 215_000)) <= 5000

        # Check frontend CPU spike onset
        cur.execute(
            "SELECT MIN(ts_ms) FROM metrics WHERE metric = 'frontend.cpu_pct' AND value >= 70.0"
        )
        cpu_spike_ts = cur.fetchone()[0]
        assert cpu_spike_ts is not None
        # Frontend CPU spikes at L+125s = 245s = T0 + 245_000ms (after first 504)
        assert cpu_spike_ts > first_504_ts
        assert abs(cpu_spike_ts - (T0_MS + 245_000)) <= 5000

        conn.close()
    finally:
        if temp_db.exists():
            temp_db.unlink()
