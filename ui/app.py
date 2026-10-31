"""Autonomous Incident Investigation Dashboard (owned by P4).

Track 09 — Multi-Service Post-Mortem & Timeline Reconstruction Under Telemetry Skew.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from agent.runner import run_investigation
from contracts.models import AgentEvent, IncidentReport
from ui.panels import (
    render_agent_stream_panel,
    render_counterfactual_panel,
    render_dag_panel,
    render_post_mortem_panel,
    render_raw_telemetry_panel,
    render_timeline_panel,
)

# Page configuration
st.set_page_config(
    page_title="Autonomous Incident Investigation",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT_DIR = Path(__file__).resolve().parent.parent
GOLDEN_REPORT_PATH = ROOT_DIR / "fixtures" / "golden_report.json"
INCIDENT_DB_PATH = ROOT_DIR / "data" / "incident.db"
SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"

# Custom CSS for high readability and projector mode
st.markdown(
    """
    <style>
    /* Main container and headers */
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 98%;
    }
    h1, h2, h3, h4 {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color: #0f172a;
    }
    /* Metric styling */
    div[data-testid="stMetricValue"] {
        font-size: 26px !important;
        font-weight: 700 !important;
    }
    /* Expander styling */
    .streamlit-expanderHeader {
        font-weight: 600;
        font-size: 14px;
    }
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #f8fafc;
        border-right: 1px solid #e2e8f0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_golden_report() -> IncidentReport | None:
    """Load pre-computed golden incident report fixture."""
    if GOLDEN_REPORT_PATH.exists():
        with open(GOLDEN_REPORT_PATH, "r", encoding="utf-8") as f:
            return IncidentReport.model_validate_json(f.read())
    return None


def resolve_db_path(selected_path_str: str) -> str:
    """Resolve dataset path, converting JSONL to cached SQLite if needed."""
    p = Path(selected_path_str)
    if not p.is_absolute():
        p = ROOT_DIR / p

    if p.suffix == ".db" and p.exists():
        return str(p)

    if p.suffix == ".jsonl" and p.exists():
        cache_dir = ROOT_DIR / "data" / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_db = cache_dir / f"{p.stem}.db"
        if not cached_db.exists() or cached_db.stat().st_mtime < p.stat().st_mtime:
            from ingest.load_jsonl import load_telemetry

            load_telemetry(p, cached_db)
        return str(cached_db)

    return str(INCIDENT_DB_PATH)


# Initialize Session State
if "events" not in st.session_state:
    st.session_state["events"] = []

if "report" not in st.session_state:
    st.session_state["report"] = load_golden_report()

if "selected_evidence_id" not in st.session_state:
    st.session_state["selected_evidence_id"] = ""

if "evidence_lookup_input" not in st.session_state:
    st.session_state["evidence_lookup_input"] = ""

if "skew_corrected" not in st.session_state:
    # Default to False so Act 1 of demo immediately exposes the raw unaligned clock paradox
    st.session_state["skew_corrected"] = False


# ==========================================
# Sidebar: Controls & Dataset Selection
# ==========================================
with st.sidebar:
    st.markdown("### 🚨 Investigation Control")
    st.caption("Track 09 — Autonomous Incident Investigation")

    # Dataset Selector
    dataset_options = {
        "Primary Incident (data/incident.db)": str(INCIDENT_DB_PATH),
        "Tiny Telemetry Sample (fixtures/tiny_telemetry.jsonl)": str(
            ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"
        ),
    }

    # Add available canonical scenario datasets
    if SCENARIOS_DIR.exists():
        scenario_files = sorted(SCENARIOS_DIR.glob("*_101.jsonl"))
        for sc_file in scenario_files:
            dataset_options[f"Scenario: {sc_file.stem}"] = str(sc_file)

    selected_dataset_label = st.selectbox(
        "Select Dataset",
        list(dataset_options.keys()),
        index=0,
        help="Choose the telemetry dataset for analysis.",
    )
    raw_dataset_path = dataset_options[selected_dataset_label]
    effective_db_path = resolve_db_path(raw_dataset_path)

    # Mode Selector
    mode_options = {
        "replay": "Replay (Offline Recorded Trace - Instant)",
        "scripted": "Scripted (Deterministic State Machine Pipeline)",
        "live": "Live (Autonomous LLM Tool-Calling Loop)",
    }
    selected_mode = st.selectbox(
        "Investigation Mode",
        options=list(mode_options.keys()),
        format_func=lambda m: mode_options[m],
        index=0,
        help="Replay streams offline recorded traces; Scripted runs the deterministic pipeline; Live runs tool calling.",
    )

    st.markdown("---")

    # Action Buttons
    col_btn1, col_btn2 = st.columns(2)
    start_investigation = col_btn1.button(
        "🚀 Investigate", type="primary", use_container_width=True
    )
    load_fixture = col_btn2.button("⚡ Golden Fixture", use_container_width=True)

    if st.button("🔄 Reset Dashboard", use_container_width=True):
        st.session_state["events"] = []
        st.session_state["report"] = load_golden_report()
        st.session_state["selected_evidence_id"] = ""
        st.session_state["evidence_lookup_input"] = ""
        st.session_state["skew_corrected"] = False
        st.rerun()

    if load_fixture:
        st.session_state["report"] = load_golden_report()
        st.success("Loaded golden report fixture!")
        st.rerun()

    st.markdown("---")

    # Telemetry Summary Info Card
    st.markdown("#### 📦 Dataset Telemetry Info")
    st.markdown(
        f"""
        - **Source:** `{Path(raw_dataset_path).name}`
        - **Format:** `{"SQLite DB" if raw_dataset_path.endswith(".db") else "JSONL Telemetry"}`
        - **Known Skew:** `auth-service` (+12.0s uncorrected)
        - **Target Services:** `gateway`, `auth-service`, `db-pool`, `frontend`
        """
    )


# ==========================================
# Main App Header
# ==========================================
st.title("🚨 Autonomous Incident Investigation")
st.markdown(
    "**Multi-Service Post-Mortem & Timeline Reconstruction Under Telemetry Skew, Clock Desynchronization & Packet Loss**"
)

# Handle Investigation Execution
if start_investigation:
    progress_bar = st.progress(0, text="Starting autonomous investigation...")
    status_container = st.empty()

    events_accum: list[AgentEvent] = []
    final_report: IncidentReport | None = None

    with st.spinner("Agent investigator executing state machine..."):
        # For replay mode on jsonl, pass original path if it matches replay conventions
        inv_path = raw_dataset_path if selected_mode == "replay" else effective_db_path

        step_idx = 0
        for ev in run_investigation(inv_path, mode=selected_mode):
            events_accum.append(ev)
            step_idx += 1
            progress_pct = min(100, int((step_idx / 25.0) * 100))
            progress_bar.progress(progress_pct, text=f"Node `{ev.node}` — Event: {ev.type}")

            if ev.type == "report":
                try:
                    final_report = IncidentReport.model_validate(ev.payload)
                except Exception:
                    pass

    progress_bar.progress(100, text="Investigation completed!")
    st.session_state["events"] = events_accum
    if final_report:
        st.session_state["report"] = final_report

    st.success(f"🎉 Investigation finished! {len(events_accum)} events processed.")
    st.rerun()


# ==========================================
# Main Layout: 2 Columns
# ==========================================
col_left, col_right = st.columns([1, 1.45])

# Left Column: Raw Unaligned Telemetry & Direct Evidence Lookup
with col_left:
    render_raw_telemetry_panel(effective_db_path)

# Right Column: Multi-tab Investigation & Post-Mortem Suite
with col_right:
    right_tabs = st.tabs(
        [
            "📋 Post-Mortem & Hypotheses",
            "⏱️ Reconstructed Timeline",
            "🗺️ Topology & Failure Frontier",
            "🧪 Counterfactual Simulator",
            "⚡ Live Agent Stream",
        ]
    )

    current_report: IncidentReport | None = st.session_state.get("report")
    current_events: list[AgentEvent] = st.session_state.get("events", [])
    clock_offsets = (
        current_report.estimated_clock_offsets_s
        if current_report
        else {"auth-service": 12.0, "gateway": 0.0, "db-pool": 0.0}
    )

    # Dynamically extract DAG data, failure frontier, and counterfactuals from events/report
    dag_data_from_events = None
    frontier_from_events = "auth-service -> db-pool"
    initial_cf = None

    for ev in current_events:
        if ev.type == "tool_result" and ev.payload.get("tool") == "build_dependency_dag":
            dag_data_from_events = ev.payload
            if ev.payload.get("frontier"):
                frontier_from_events = ev.payload["frontier"]

    if current_report:
        for h in current_report.hypotheses_evaluated:
            if h.counterfactual:
                initial_cf = h.counterfactual
                break

    with right_tabs[0]:
        render_post_mortem_panel(current_report)

    with right_tabs[1]:
        tl_events = current_report.timeline_events if current_report else []
        render_timeline_panel(tl_events, clock_offsets=clock_offsets)

    with right_tabs[2]:
        render_dag_panel(
            dag_data=dag_data_from_events,
            failure_frontier=frontier_from_events,
        )

    with right_tabs[3]:
        render_counterfactual_panel(
            db_path=effective_db_path,
            initial_cf=initial_cf,
        )

    with right_tabs[4]:
        render_agent_stream_panel(current_events)
