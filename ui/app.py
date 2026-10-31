"""Autonomous Incident Investigation Dashboard (owned by P4).

Track 09 — Multi-Service Post-Mortem & Timeline Reconstruction Under Telemetry Skew.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path when executed directly via `streamlit run ui/app.py`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import importlib  # noqa: E402

import streamlit as st  # noqa: E402

import ui.panels  # noqa: E402
import ui.panels.agent_stream  # noqa: E402
import ui.panels.counterfactual  # noqa: E402
import ui.panels.dag_view  # noqa: E402
import ui.panels.post_mortem  # noqa: E402
import ui.panels.raw_telemetry  # noqa: E402
import ui.panels.timeline  # noqa: E402

# Hot-reload panels so module updates take effect immediately in running Streamlit processes
importlib.reload(ui.panels.raw_telemetry)
importlib.reload(ui.panels.post_mortem)
importlib.reload(ui.panels.timeline)
importlib.reload(ui.panels.dag_view)
importlib.reload(ui.panels.counterfactual)
importlib.reload(ui.panels.agent_stream)
importlib.reload(ui.panels)

from agent.runner import run_investigation  # noqa: E402
from contracts.models import AgentEvent, IncidentReport  # noqa: E402
from ui.panels import (  # noqa: E402
    render_agent_stream_panel,
    render_counterfactual_panel,
    render_dag_panel,
    render_post_mortem_panel,
    render_raw_telemetry_panel,
    render_timeline_panel,
)
from ui.panels.raw_telemetry import fetch_evidence_row  # noqa: E402

# Page configuration
st.set_page_config(
    page_title="Autonomous Incident Investigation",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT_DIR = Path(__file__).resolve().parent.parent
GOLDEN_REPORT_PATH = ROOT_DIR / "fixtures" / "golden_report.json"
INCIDENT_DB_PATH = ROOT_DIR / "data" / "incident.db"
TELEMETRY_JSONL_PATH = ROOT_DIR / "data" / "telemetry.jsonl"
SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"

# Custom CSS for Sleek Two-Colour Jungle Green & Off-White Theme
st.markdown(
    """
    <style>
    /* Global App & Off-White Background */
    .stApp {
        background-color: #FAF9F6;
        color: #1B2A22;
        font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    }

    /* Main container spacing */
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2.5rem;
        max-width: 96%;
    }

    h1, h2, h3, h4, h5, h6 {
        font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
        color: #1B4332 !important;
        font-weight: 600;
        letter-spacing: -0.015em;
    }

    p, span, label, li {
        color: #2C3E35;
    }

    code {
        background-color: #EBF3EE !important;
        color: #1B4332 !important;
        padding: 2px 5px !important;
        border-radius: 4px !important;
        font-size: 0.88em !important;
    }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background-color: #F4F3EE;
        border: 1px solid #D8E2DC;
        border-left: 4px solid #2D6A4F;
        border-radius: 6px;
        padding: 12px 16px;
    }

    div[data-testid="stMetricValue"] {
        font-size: 24px !important;
        font-weight: 700 !important;
        color: #1B4332 !important;
    }

    div[data-testid="stMetricLabel"] {
        color: #52665A !important;
        font-weight: 600 !important;
        font-size: 12px !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Sidebar styling: Deep Jungle Green */
    section[data-testid="stSidebar"] {
        background-color: #1B4332 !important;
        border-right: 1px solid #2D6A4F;
    }

    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4,
    section[data-testid="stSidebar"] h5,
    section[data-testid="stSidebar"] h6 {
        color: #FAF9F6 !important;
    }

    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] li,
    section[data-testid="stSidebar"] .stCaption {
        color: #E6F2EB !important;
    }

    section[data-testid="stSidebar"] code {
        background-color: rgba(250, 249, 246, 0.12) !important;
        color: #FAF9F6 !important;
    }

    section[data-testid="stSidebar"] hr {
        border-color: rgba(250, 249, 246, 0.18) !important;
    }

    /* Sidebar Selectbox & Inputs */
    section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background-color: #2D6A4F !important;
        color: #FAF9F6 !important;
        border-color: #52B788 !important;
        border-radius: 6px;
    }

    section[data-testid="stSidebar"] div[data-baseweb="select"] span,
    section[data-testid="stSidebar"] div[data-baseweb="select"] div {
        color: #FAF9F6 !important;
    }

    section[data-testid="stSidebar"] svg {
        fill: #FAF9F6 !important;
    }

    /* Sidebar Buttons */
    section[data-testid="stSidebar"] .stButton > button {
        background-color: transparent !important;
        color: #FAF9F6 !important;
        border: 1px solid #74C69D !important;
        border-radius: 6px;
        font-weight: 500;
        transition: all 0.15s ease;
    }

    section[data-testid="stSidebar"] .stButton > button:hover {
        background-color: #2D6A4F !important;
        color: #FAF9F6 !important;
        border-color: #FAF9F6 !important;
    }

    section[data-testid="stSidebar"] .stButton > button[kind="primary"],
    section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
        background-color: #FAF9F6 !important;
        color: #1B4332 !important;
        border: 1px solid #FAF9F6 !important;
        font-weight: 600;
    }

    section[data-testid="stSidebar"] .stButton > button[kind="primary"] span,
    section[data-testid="stSidebar"] .stButton > button[kind="primary"] p,
    section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] span,
    section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] p {
        color: #1B4332 !important;
    }

    section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
    section[data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"]:hover {
        background-color: #D8F3DC !important;
        color: #1B4332 !important;
        border-color: #D8F3DC !important;
    }

    /* Navigation Tabs styling */
    div[data-baseweb="tab-list"] {
        gap: 6px;
        border-bottom: 2px solid #D8E2DC !important;
        background-color: #F4F3EE !important;
        padding: 6px 8px 0 8px;
        border-radius: 8px 8px 0 0;
    }

    button[data-baseweb="tab"] {
        font-size: 14px !important;
        font-weight: 500 !important;
        color: #52665A !important;
        padding: 10px 20px !important;
        border-radius: 6px 6px 0 0 !important;
        background-color: transparent !important;
        border: none !important;
        transition: all 0.15s ease !important;
    }

    button[data-baseweb="tab"]:hover {
        color: #1B4332 !important;
        background-color: rgba(45, 106, 79, 0.08) !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] {
        color: #FAF9F6 !important;
        font-weight: 600 !important;
        background-color: #2D6A4F !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] p,
    button[data-baseweb="tab"][aria-selected="true"] span {
        color: #FAF9F6 !important;
    }

    div[data-baseweb="tab-highlight"] {
        background-color: #1B4332 !important;
    }

    /* Main Area Buttons */
    .stButton > button {
        background-color: #F4F3EE;
        color: #1B4332;
        border: 1px solid #B7CEB8;
        border-radius: 6px;
        font-weight: 500;
        font-size: 13px;
        transition: all 0.15s ease;
    }

    .stButton > button:hover {
        background-color: #2D6A4F;
        color: #FAF9F6;
        border-color: #2D6A4F;
    }

    .stButton > button:hover p,
    .stButton > button:hover span {
        color: #FAF9F6 !important;
    }

    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {
        background-color: #2D6A4F;
        color: #FAF9F6;
        border: 1px solid #2D6A4F;
    }

    .stButton > button[kind="primary"] p,
    .stButton > button[kind="primary"] span {
        color: #FAF9F6 !important;
    }

    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid="baseButton-primary"]:hover {
        background-color: #1B4332;
        border-color: #1B4332;
    }

    /* Progress bar */
    div[data-testid="stProgress"] > div > div > div > div {
        background-color: #2D6A4F !important;
    }

    /* Dividers & Borders */
    hr {
        border: none;
        border-top: 1px solid #D8E2DC !important;
        margin: 1.25rem 0;
    }

    /* Expanders */
    div[data-testid="stExpander"] {
        border: 1px solid #D8E2DC !important;
        border-left: 4px solid #2D6A4F !important;
        border-radius: 6px;
        background-color: #F4F3EE;
        margin-bottom: 0.65rem;
    }

    .streamlit-expanderHeader {
        font-weight: 600;
        font-size: 14px;
        color: #1B4332 !important;
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

    if p.suffix == ".db":
        if not p.exists() and TELEMETRY_JSONL_PATH.exists():
            from ingest.load_jsonl import load_telemetry

            load_telemetry(TELEMETRY_JSONL_PATH, p)
        if p.exists():
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

if "evidence_version" not in st.session_state:
    st.session_state["evidence_version"] = 0

if "skew_corrected" not in st.session_state:
    # Default to False so Act 1 of demo immediately exposes the raw unaligned clock paradox
    st.session_state["skew_corrected"] = False


# ==========================================
# Sidebar: Controls & Dataset Selection
# ==========================================
with st.sidebar:
    st.markdown("### Investigation Control")
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
        "Run Investigation", type="primary", use_container_width=True
    )
    load_fixture = col_btn2.button("Load Fixture", use_container_width=True)

    if st.button("Reset", use_container_width=True):
        st.session_state["events"] = []
        st.session_state["report"] = load_golden_report()
        st.session_state["selected_evidence_id"] = ""
        st.session_state["evidence_version"] = 0
        st.session_state["skew_corrected"] = False
        st.rerun()

    if load_fixture:
        st.session_state["report"] = load_golden_report()
        st.success("Loaded golden report fixture.")
        st.rerun()

    st.markdown("---")

    # Telemetry Summary Info Card
    st.markdown("#### Dataset Info")
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
st.title("Autonomous Incident Investigation")
st.markdown(
    "Multi-Service Post-Mortem & Timeline Reconstruction Under Telemetry Skew, Clock Desynchronization & Packet Loss"
)

# Handle Investigation Execution
if start_investigation:
    progress_bar = st.progress(0, text="Starting autonomous investigation...")

    events_accum: list[AgentEvent] = []
    final_report: IncidentReport | None = None

    with st.spinner("Agent investigator executing state machine..."):
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

    progress_bar.progress(100, text="Investigation completed.")
    st.session_state["events"] = events_accum
    if final_report:
        st.session_state["report"] = final_report

    st.success(f"Investigation complete. {len(events_accum)} events processed.")
    st.rerun()


# ==========================================
# Cross-Tab Selected Evidence Quick Inspector
# ==========================================
active_evidence_id = st.session_state.get("selected_evidence_id", "").strip()
if active_evidence_id:
    ev_row = fetch_evidence_row(effective_db_path, active_evidence_id)
    with st.container():
        insp_col1, insp_col2 = st.columns([5, 1])
        with insp_col1:
            st.markdown(
                f"""
                <div style="background-color: #EBF3EE; border-left: 4px solid #1B4332; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px; font-size: 13.5px; color: #1B4332;">
                    <b>Selected Evidence Pointer:</b> <code>{active_evidence_id}</code>
                    {" — Verified in SQLite table <code>" + str(ev_row.get("raw_sql_table")) + "</code> (also synced to Raw Telemetry tab)" if ev_row else " — Not found in current dataset"}
                </div>
                """,
                unsafe_allow_html=True,
            )
        with insp_col2:
            if st.button("Clear Selection", key="clear_active_evidence", use_container_width=True):
                st.session_state["selected_evidence_id"] = ""
                st.session_state["evidence_version"] = (
                    st.session_state.get("evidence_version", 0) + 1
                )
                st.rerun()
        if ev_row:
            with st.expander(f"Inspect Raw SQLite Record: {active_evidence_id}", expanded=False):
                st.json(ev_row)


# ==========================================
# Main Layout: 6 Full-Width Function Tabs
# ==========================================
tabs = st.tabs(
    [
        "Raw Telemetry",
        "Post-Mortem & Hypotheses",
        "Reconstructed Timeline",
        "Topology & Failure Frontier",
        "Counterfactual Simulator",
        "Agent Stream",
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

with tabs[0]:
    render_raw_telemetry_panel(effective_db_path)

with tabs[1]:
    render_post_mortem_panel(current_report)

with tabs[2]:
    tl_events = current_report.timeline_events if current_report else []
    render_timeline_panel(tl_events, clock_offsets=clock_offsets)

with tabs[3]:
    render_dag_panel(
        dag_data=dag_data_from_events,
        failure_frontier=frontier_from_events,
    )

with tabs[4]:
    render_counterfactual_panel(
        db_path=effective_db_path,
        initial_cf=initial_cf,
    )

with tabs[5]:
    render_agent_stream_panel(current_events)
