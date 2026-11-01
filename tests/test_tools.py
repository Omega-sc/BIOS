"""Unit tests for P2 tools: skew, spans, metrics, DAG, and API specifications."""

import json
import sqlite3
from pathlib import Path

import networkx as nx

from tools.api import (
    TOOLS,
    build_dependency_dag,
    estimate_clock_offsets,
    failure_frontier,
    get_metric_derivatives,
    get_tool_specs,
    query_spans,
    rank_metric_onsets,
    to_node_link_json,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "incident.db"


def test_estimate_clock_offsets_real_db():
    """Verify estimated auth offset is ≈12.0s (±0.1s) and gateway/db-pool are 0.0s."""
    assert DB_PATH.exists(), "data/incident.db must exist"
    offsets = estimate_clock_offsets(DB_PATH)

    assert isinstance(offsets, dict)
    assert "auth-service" in offsets
    assert "gateway" in offsets
    assert "db-pool" in offsets

    # Verify tolerance band (12.0s ± 0.1s)
    auth_offset = offsets["auth-service"]
    assert 11.9 <= auth_offset <= 12.1, f"Expected auth offset ≈ 12.0s, got {auth_offset}"
    assert offsets["gateway"] == 0.0
    assert offsets["db-pool"] == 0.0


def test_estimate_clock_offsets_synthetic():
    """Verify skew estimation on synthetic traces with custom offset (never hardcoded)."""
    conn = sqlite3.connect(":memory:")
    with open(ROOT_DIR / "contracts" / "schema.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    # Insert synthetic spans with +7.0s skew on worker-service
    cur = conn.cursor()
    # 20 healthy traces
    for i in range(20):
        t = 1000000 + i * 1000
        cur.execute(
            "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"gw-{i}", f"tr-{i}", None, "gateway", "GET /api", t, t + 50, 200, "{}"),
        )
        # worker called with +7.0s skew: start_ms = t + 2 + 7000
        cur.execute(
            "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"wk-{i}",
                f"tr-{i}",
                f"gw-{i}",
                "worker-service",
                "process",
                t + 7002,
                t + 7040,
                200,
                "{}",
            ),
        )

    offsets = estimate_clock_offsets(conn)
    assert "worker-service" in offsets
    assert 6.9 <= offsets["worker-service"] <= 7.1
    conn.close()


def test_query_spans_clock_correction():
    """Verify query_spans corrects auth-service timestamps by estimated skew."""
    spans = query_spans(DB_PATH, service="auth-service", limit=5)
    assert len(spans) > 0
    for s in spans:
        assert s.t_corrected_start_ms is not None
        # Difference between raw start_ms and corrected start_ms must be 12000 ms
        delta = s.start_ms - s.t_corrected_start_ms
        assert 11900 <= delta <= 12100


def test_query_spans_orphan_detection():
    """Verify orphan spans (dropped parent) are correctly flagged with orphan=True."""
    all_spans = query_spans(DB_PATH)
    orphans = [s for s in all_spans if s.orphan]
    assert len(orphans) == 40, f"Expected 40 orphan spans in incident.db, got {len(orphans)}"
    for s in orphans:
        assert s.parent_span_id is not None


def test_query_spans_causal_ordering():
    """Verify spans are ordered causally: DFS within trace, gateway root order across traces."""
    spans = query_spans(DB_PATH, causal=True, limit=30)
    assert len(spans) >= 30

    # Verify gateway root appears before its child spans within the same trace
    trace_span_indices: dict[str, dict[str, int]] = {}
    for idx, s in enumerate(spans):
        if s.trace_id not in trace_span_indices:
            trace_span_indices[s.trace_id] = {}
        trace_span_indices[s.trace_id][s.service] = idx

    for tid, svc_map in trace_span_indices.items():
        if "gateway" in svc_map and "auth-service" in svc_map:
            assert svc_map["gateway"] < svc_map["auth-service"], (
                f"Trace {tid}: gateway span at {svc_map['gateway']} should precede auth span at {svc_map['auth-service']}"
            )
        if "auth-service" in svc_map and "db-pool" in svc_map:
            assert svc_map["auth-service"] < svc_map["db-pool"], (
                f"Trace {tid}: auth span at {svc_map['auth-service']} should precede db-pool span at {svc_map['db-pool']}"
            )


def test_query_spans_time_window_corrected():
    """Verify time_window filter operates in corrected time coordinates."""
    # Window: first 2 seconds in corrected time (1700000000000 to 1700000002000)
    w_start = 1700000000000
    w_end = 1700000002000
    spans = query_spans(DB_PATH, time_window=(w_start, w_end))
    assert len(spans) > 0
    for s in spans:
        assert s.t_corrected_start_ms is not None
        assert w_start <= s.t_corrected_start_ms <= w_end


def test_query_spans_non_causal():
    """Verify causal=False uses raw start_ms ASC without skew shift."""
    spans = query_spans(DB_PATH, causal=False, limit=20)
    assert len(spans) == 20
    for i in range(len(spans) - 1):
        assert spans[i].start_ms <= spans[i + 1].start_ms


def test_metric_derivatives_active_connections():
    """Verify inflection detection on db_pool.active_connections identifies leak onset."""
    onset = get_metric_derivatives(DB_PATH, "db_pool.active_connections")
    assert onset.metric == "db_pool.active_connections"
    assert onset.service == "db-pool"
    assert onset.onset_corrected_ms is not None

    # Incident leak onset is at L=120s (1700000120000)
    rel_s = (onset.onset_corrected_ms - 1700000000000) / 1000.0
    assert 115.0 <= rel_s <= 130.0, f"Expected leak onset ≈ 120s, got {rel_s}s"
    assert onset.peak_slope > 0.1
    assert onset.z_score > 5.0


def test_metric_derivatives_auth_latency_correction():
    """Verify auth.latency_p99 timestamps are corrected by estimated skew."""
    onset = get_metric_derivatives(DB_PATH, "auth.latency_p99")
    assert onset.onset_corrected_ms is not None
    # Corrected timestamp should be aligned with unskewed timeline (around 120s)
    rel_s = (onset.onset_corrected_ms - 1700000000000) / 1000.0
    assert 115.0 <= rel_s <= 130.0, f"Expected corrected onset ≈ 120s, got {rel_s}s"


def test_rank_metric_onsets_order_and_frontend_delay():
    """Verify Phase B requirement:
    1. db_pool.active_connections < gateway.error_rate_5xx < frontend.cpu_pct.
    2. frontend onset is ≈30s after gateway 5xx onset.
    """
    onsets = rank_metric_onsets(DB_PATH)
    onset_map = {o.metric: o.onset_corrected_ms for o in onsets if o.onset_corrected_ms is not None}

    assert "db_pool.active_connections" in onset_map
    assert "gateway.error_rate_5xx" in onset_map
    assert "frontend.cpu_pct" in onset_map

    t_pool = onset_map["db_pool.active_connections"]
    t_gw = onset_map["gateway.error_rate_5xx"]
    t_fe = onset_map["frontend.cpu_pct"]

    # Strict chronological order check
    assert t_pool < t_gw, f"db_pool ({t_pool}) must precede gateway 5xx ({t_gw})"
    assert t_gw < t_fe, f"gateway 5xx ({t_gw}) must precede frontend CPU ({t_fe})"

    # Frontend onset is ≈30s after gateway 5xx onset (tolerance ±5s)
    delay_s = (t_fe - t_gw) / 1000.0
    assert 25.0 <= delay_s <= 35.0, f"Expected frontend delay ≈ 30s after gateway, got {delay_s}s"


def test_dependency_dag_attributes():
    """Verify build_dependency_dag produces valid node and edge attributes."""
    dag = build_dependency_dag(DB_PATH)
    assert isinstance(dag, nx.DiGraph)

    # Required nodes
    for svc in ["gateway", "auth-service", "db-pool", "frontend"]:
        assert dag.has_node(svc)
        node_attrs = dag.nodes[svc]
        assert "error_rate" in node_attrs
        assert "metric_onset" in node_attrs
        assert "span_count" in node_attrs
        assert 0.0 <= node_attrs["error_rate"] <= 1.0

    # Required edges
    assert dag.has_edge("gateway", "auth-service")
    assert dag.has_edge("auth-service", "db-pool")
    assert dag.has_edge("frontend", "gateway")

    # Edge attributes
    edge_gw_auth = dag.get_edge_data("gateway", "auth-service")
    assert edge_gw_auth["call_count"] > 100
    assert edge_gw_auth["mean_latency"] > 0.0
    assert 0.0 <= edge_gw_auth["error_propagation_fraction"] <= 1.0


def test_failure_frontier():
    """Verify failure_frontier pinpoints auth-service -> db-pool under connection pool leak."""
    dag = build_dependency_dag(DB_PATH)
    frontier = failure_frontier(dag, db=DB_PATH)
    assert frontier == "auth-service -> db-pool"


def test_to_node_link_json():
    """Verify to_node_link_json produces valid parseable JSON."""
    dag = build_dependency_dag(DB_PATH)
    nl_str = to_node_link_json(dag)
    data = json.loads(nl_str)
    assert "nodes" in data
    assert "links" in data
    assert len(data["nodes"]) >= 4


def test_tool_specs_and_registry():
    """Verify get_tool_specs and TOOLS registry match LLM calling expectations and return JSON-serializable outputs."""
    specs = get_tool_specs()
    assert isinstance(specs, list)
    spec_names = {s["name"] for s in specs}
    assert "estimate_clock_offsets" in spec_names
    assert "query_spans" in spec_names
    assert "rank_metric_onsets" in spec_names
    assert "build_dependency_dag" in spec_names
    assert "failure_frontier" in spec_names

    for s in specs:
        assert "description" in s
        assert "parameters" in s
        assert s["parameters"].get("type") == "object"

    for name in spec_names:
        assert name in TOOLS
        assert callable(TOOLS[name])

    # Verify that calling tools via TOOLS returns JSON-serializable summaries
    res_skew = TOOLS["estimate_clock_offsets"](db=DB_PATH)
    assert isinstance(res_skew, dict)
    json.dumps(res_skew)

    res_spans = TOOLS["query_spans"](db=DB_PATH, limit=10)
    assert isinstance(res_spans, dict)
    assert "spans" in res_spans
    assert len(res_spans["spans"]) <= 10
    json.dumps(res_spans)

    res_metric = TOOLS["get_metric_derivatives"](
        db=DB_PATH, metric_name="db_pool.active_connections"
    )
    assert isinstance(res_metric, dict)
    json.dumps(res_metric)

    res_ranked = TOOLS["rank_metric_onsets"](db=DB_PATH)
    assert isinstance(res_ranked, list)
    json.dumps(res_ranked)

    res_dag = TOOLS["build_dependency_dag"](db=DB_PATH)
    assert isinstance(res_dag, dict)
    assert "nodes" in res_dag
    json.dumps(res_dag)

    res_frontier = TOOLS["failure_frontier"](dag=res_dag, db=DB_PATH)
    assert isinstance(res_frontier, dict)
    assert "failure_frontier" in res_frontier
    json.dumps(res_frontier)


def test_estimate_clock_offsets_all_root_spans_dropped():
    """Verify clock offset estimation when 100% of gateway root spans are dropped."""
    conn = sqlite3.connect(":memory:")
    with open(ROOT_DIR / "contracts" / "schema.sql", "r", encoding="utf-8") as f:
        conn.executescript(f.read())

    cur = conn.cursor()
    # Insert spans where parent gateway spans were entirely dropped (missing in table)
    # Auth-service has +12s skew (start_ms = t + 2 + 12000), db-pool has no skew (start_ms = t + 7)
    for i in range(25):
        t = 1000000 + i * 1000
        cur.execute(
            "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"auth-{i}",
                f"tr-{i}",
                f"gw-missing-{i}",
                "auth-service",
                "auth",
                t + 12002,
                t + 12040,
                200,
                "{}",
            ),
        )
        cur.execute(
            "INSERT INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"db-{i}", f"tr-{i}", f"auth-{i}", "db-pool", "acquire", t + 7, t + 25, 200, "{}"),
        )

    offsets = estimate_clock_offsets(conn)
    assert "auth-service" in offsets
    assert "db-pool" in offsets
    # Difference between auth-service and db-pool must be ~12.0s
    auth_offset = offsets["auth-service"]
    db_offset = offsets["db-pool"]
    assert 11.8 <= (auth_offset - db_offset) <= 12.2
    conn.close()


def test_failure_frontier_dag_only_without_db():
    """Verify failure_frontier accurately pinpoints auth-service -> db-pool from dag alone."""
    dag = build_dependency_dag(DB_PATH)
    frontier = failure_frontier(dag)
    assert frontier == "auth-service -> db-pool"


def test_failure_frontier_across_scenarios():
    """Verify failure_frontier distinguishes pool leaks, slow DBs, and gateway timeout misconfigs."""
    import tempfile

    from ingest.load_jsonl import load_telemetry

    # 1. Slow DB scenario
    slow_db_jsonl = ROOT_DIR / "data" / "scenarios" / "slow_db_101.jsonl"
    if slow_db_jsonl.exists():
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "slow_db.db"
            load_telemetry(slow_db_jsonl, db_p)
            dag_slow = build_dependency_dag(db_p)
            ff_slow = failure_frontier(dag_slow)
            assert ff_slow in ("db-pool", "auth-service -> db-pool")

    # 2. Gateway timeout misconfiguration
    gw_misconfig_jsonl = ROOT_DIR / "data" / "scenarios" / "gateway_timeout_misconfig_101.jsonl"
    if gw_misconfig_jsonl.exists():
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "gw_misconfig.db"
            load_telemetry(gw_misconfig_jsonl, db_p)
            dag_gw = build_dependency_dag(db_p)
            ff_gw = failure_frontier(dag_gw)
            assert ff_gw == "gateway"
