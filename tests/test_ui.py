"""Unit and integration tests for UI dashboard panels and components (owned by P4)."""

import json
from pathlib import Path

from contracts.models import AgentEvent, TimelineEvent
from ui.panels.agent_stream import format_event_summary
from ui.panels.counterfactual import compute_counterfactual, get_cached_sim_params
from ui.panels.dag_view import generate_dag_dot
from ui.panels.raw_telemetry import fetch_evidence_row, load_telemetry_records
from ui.panels.timeline import create_timeline_figure

ROOT_DIR = Path(__file__).resolve().parent.parent
INCIDENT_DB = ROOT_DIR / "data" / "incident.db"
GOLDEN_REPORT = ROOT_DIR / "fixtures" / "golden_report.json"


def test_load_telemetry_records():
    assert INCIDENT_DB.exists(), "incident.db must exist"

    # 1. Load all records
    df = load_telemetry_records(INCIDENT_DB, limit=100)
    assert not df.empty
    assert "service" in df.columns
    assert "raw_timestamp_ms" in df.columns
    assert "is_skewed" in df.columns
    assert "is_error" in df.columns

    # 2. Filter by service
    df_auth = load_telemetry_records(INCIDENT_DB, limit=50, service_filter="auth-service")
    assert not df_auth.empty
    assert all(df_auth["service"] == "auth-service")
    assert all(df_auth["is_skewed"])

    # 3. Filter by type
    df_spans = load_telemetry_records(INCIDENT_DB, limit=50, type_filter="spans")
    assert not df_spans.empty
    assert all(df_spans["type"] == "span")

    # 4. Filter by errors only
    df_errs = load_telemetry_records(INCIDENT_DB, limit=50, errors_only=True)
    if not df_errs.empty:
        assert all(df_errs["is_error"])

    # 5. Styling
    from ui.panels.raw_telemetry import style_telemetry_dataframe

    styled = style_telemetry_dataframe(df.head(10))
    assert hasattr(styled, "to_html")


def test_evidence_row_direct_lookup():
    assert INCIDENT_DB.exists()

    # 1. Lookup metric pointer with @ format
    m_res = fetch_evidence_row(INCIDENT_DB, "db_pool.active_connections@1700000090000")
    assert m_res is not None
    assert m_res["kind"] == "metric"
    assert m_res["metric"] == "db_pool.active_connections"
    assert m_res["raw_sql_table"] == "metrics"

    # 2. Lookup existing span
    df_spans = load_telemetry_records(INCIDENT_DB, limit=5, type_filter="spans")
    test_sid = df_spans.iloc[0]["record_id"]
    s_res = fetch_evidence_row(INCIDENT_DB, test_sid)
    assert s_res is not None
    assert s_res["kind"] == "span"
    assert s_res["id"] == test_sid
    assert s_res["raw_sql_table"] == "spans"

    # 3. Lookup existing log
    df_logs = load_telemetry_records(INCIDENT_DB, limit=5, type_filter="logs")
    test_lid = df_logs.iloc[0]["record_id"]
    l_res = fetch_evidence_row(INCIDENT_DB, test_lid)
    assert l_res is not None
    assert l_res["kind"] == "log"
    assert l_res["id"] == test_lid
    assert l_res["raw_sql_table"] == "logs"

    # 4. Non-existent ID returns None
    assert fetch_evidence_row(INCIDENT_DB, "non_existent_id_99999") is None
    assert fetch_evidence_row(INCIDENT_DB, "") is None


def test_dag_dot_generation():
    # 1. Failure frontier rendering
    dot = generate_dag_dot(failure_frontier="auth-service -> db-pool")
    assert dot.startswith("digraph")
    assert "auth-service" in dot
    assert "db-pool" in dot
    assert "FAILURE FRONTIER" in dot
    assert "#1B4332" in dot

    # 2. Alternative failure frontier
    dot_alt = generate_dag_dot(failure_frontier="gateway -> auth-service")
    assert dot_alt.startswith("digraph")
    assert "gateway" in dot_alt
    assert "auth-service" in dot_alt
    assert "FAILURE FRONTIER" in dot_alt

    # 3. Frontier on arbitrary/renamed services
    dot_renamed = generate_dag_dot(failure_frontier="service-x -> service-y")
    assert "service-x" in dot_renamed
    assert "service-y" in dot_renamed
    assert "FAILURE FRONTIER" in dot_renamed

    # 4. Custom dag_data edge and node annotations
    custom_dag = {
        "nodes": [
            {"id": "service-a", "error_rate": 0.0, "span_count": 50},
            {"id": "service-b", "error_rate": 0.25, "span_count": 50},
        ],
        "links": [
            {"source": "service-a", "target": "service-b", "call_count": 999, "mean_latency": 12.3}
        ],
    }
    dot_custom = generate_dag_dot(dag_data=custom_dag, failure_frontier="service-a -> service-b")
    assert "service-a" in dot_custom
    assert "service-b" in dot_custom
    assert "calls: 999" in dot_custom
    assert "lat: 12.3ms" in dot_custom
    assert "FAILURE FRONTIER" in dot_custom


def test_timeline_figure_and_skew_correction():
    assert GOLDEN_REPORT.exists()
    with open(GOLDEN_REPORT, "r", encoding="utf-8") as f:
        rep_dict = json.load(f)

    tl_events = [TimelineEvent.model_validate(e) for e in rep_dict["timeline_events"]]
    auth_events = [e for e in tl_events if e.service == "auth-service"]
    assert len(auth_events) > 0

    # 1. Skew corrected = True
    fig_corr = create_timeline_figure(tl_events, skew_corrected=True)
    assert len(fig_corr.data) >= 2
    assert "True Causal Time" in fig_corr.layout.title.text

    # 2. Skew corrected = False
    fig_raw = create_timeline_figure(tl_events, skew_corrected=False)
    assert len(fig_raw.data) >= 2
    assert "Raw Wall-Clock" in fig_raw.layout.title.text

    # 3. Empty events fallback
    fig_empty = create_timeline_figure([])
    assert len(fig_empty.layout.annotations) == 1
    assert "No timeline events" in fig_empty.layout.annotations[0].text


def test_counterfactual_simulation_cache():
    params = get_cached_sim_params(str(INCIDENT_DB))
    p_dict = params.model_dump()

    # 1. fix_leak intervention
    res_leak = compute_counterfactual(p_dict, "fix_leak", n_runs=100)
    assert res_leak.intervention == "fix_leak"
    assert res_leak.intervened_failure_prob <= 0.05
    assert len(res_leak.ci95) == 2
    assert res_leak.ci95[0] <= res_leak.ci95[1]

    # 2. pool_size=50 intervention
    res_pool = compute_counterfactual(p_dict, "pool_size=50", n_runs=100)
    assert res_pool.intervention == "pool_size=50"
    assert res_pool.intervened_failure_prob <= res_pool.baseline_failure_prob

    # 3. no_retries intervention
    res_retries = compute_counterfactual(p_dict, "no_retries", n_runs=100)
    assert res_retries.intervention == "no_retries"
    assert res_retries.intervened_failure_prob > 0.05, "Failure must persist without retries"


def test_event_formatting():
    # Test all 5 event types
    ev_state = AgentEvent(type="state", node="telemetry_ingestion", payload={"status": "started"})
    s_info = format_event_summary(ev_state)
    assert s_info["icon"] == ""
    assert "telemetry_ingestion" in s_info["title"]

    ev_tool = AgentEvent(
        type="tool_call", node="telemetry_ingestion", payload={"tool": "query_spans", "args": {}}
    )
    t_info = format_event_summary(ev_tool)
    assert t_info["icon"] == ""
    assert "query_spans" in t_info["title"]

    ev_res = AgentEvent(
        type="tool_result",
        node="telemetry_ingestion",
        payload={"tool": "query_spans", "result": {"count": 10}},
    )
    r_info = format_event_summary(ev_res)
    assert r_info["icon"] == ""

    ev_hyp = AgentEvent(
        type="hypothesis",
        node="hypothesis_generator",
        payload={
            "id": "H1",
            "statement": "Leak in auth",
            "verdict": "root_cause",
            "confidence": 0.94,
        },
    )
    h_info = format_event_summary(ev_hyp)
    assert h_info["icon"] == ""
    assert "H1" in h_info["title"]
    assert "root_cause" in h_info["summary"]

    ev_rep = AgentEvent(
        type="report",
        node="report_synthesizer",
        payload={"schema_version": "1.0", "timeline_events": [], "hypotheses_evaluated": []},
    )
    rep_info = format_event_summary(ev_rep)
    assert rep_info["icon"] == ""


def test_streamlit_app_smoke():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT_DIR / "ui" / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception, f"AppTest threw an exception: {at.exception}"
    assert "Autonomous Incident Investigation" in at.title[0].value


def test_evidence_lookup_session_sync():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT_DIR / "ui" / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception

    # Simulate user clicking an evidence button in post-mortem:
    at.session_state["selected_evidence_id"] = "span-gw-3"
    at.session_state["evidence_version"] = 1
    at.run()
    assert not at.exception
    assert at.text_input(key="evidence_input_1").value == "span-gw-3"


def test_evidence_button_click_in_post_mortem():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT_DIR / "ui" / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception

    # Find evidence buttons (buttons starting with Inspect:)
    evidence_buttons = [b for b in at.button if b.label.startswith("Inspect:")]
    assert len(evidence_buttons) > 0, "Expected evidence buttons in rendered report"

    # Click the first evidence button to verify on_click callback execution
    evidence_buttons[0].click().run()
    assert not at.exception, f"Clicking evidence button caused exception: {at.exception}"
    assert at.session_state["selected_evidence_id"] != ""
    ver = at.session_state["evidence_version"]
    assert (
        at.text_input(key=f"evidence_input_{ver}").value == at.session_state["selected_evidence_id"]
    )
