"""Live agent execution stream panel (owned by P4)."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from contracts.models import AgentEvent


def format_event_summary(ev: AgentEvent) -> dict[str, Any]:
    """Format an AgentEvent into a display-friendly dictionary."""
    ev_type = ev.type
    node = ev.node
    payload = ev.payload

    if ev_type == "state":
        status = payload.get("status", "in_progress")
        return {
            "icon": "🔄",
            "title": f"Node Transition: {node}",
            "badge": "State Transition",
            "badge_color": "#3b82f6",
            "summary": f"Pipeline entered `{node}` (status: {status}).",
            "details": payload,
        }

    if ev_type == "tool_call":
        tool = payload.get("tool", "unknown_tool")
        args = payload.get("args", {})
        return {
            "icon": "🛠️",
            "title": f"Tool Call: {tool}()",
            "badge": "Tool Invocation",
            "badge_color": "#8b5cf6",
            "summary": f"Executing tool `{tool}` with parameters: {json.dumps(args, default=str)}",
            "details": args,
        }

    if ev_type == "tool_result":
        tool = payload.get("tool", node)
        res = payload.get("result", payload)
        # Summarize result if large
        if isinstance(res, list):
            res_summary = f"{len(res)} items returned"
        elif isinstance(res, dict):
            keys = list(res.keys())[:4]
            res_summary = f"Result fields: {', '.join(keys)}"
        else:
            res_summary = str(res)[:80]

        return {
            "icon": "✅",
            "title": f"Tool Result: {tool}",
            "badge": "Tool Output",
            "badge_color": "#10b981",
            "summary": f"Tool `{tool}` completed: {res_summary}",
            "details": res,
        }

    if ev_type == "hypothesis":
        h_id = payload.get("id", "H?")
        stmt = payload.get("statement", "")
        verdict = payload.get("verdict", "unresolved")
        conf = payload.get("confidence", 0.0)
        verdict_color = (
            "#dc2626"
            if verdict == "root_cause"
            else "#d97706"
            if verdict == "contributing_factor"
            else "#64748b"
        )

        return {
            "icon": "💡",
            "title": f"Hypothesis {h_id}: {verdict.upper()}",
            "badge": f"Verdict: {verdict}",
            "badge_color": verdict_color,
            "summary": f"**[{h_id}]** {stmt} — Verdict: `{verdict}` (Evidence-based score: `{conf:.2f}`)",
            "details": payload,
        }

    if ev_type == "report":
        version = payload.get("schema_version", "1.0")
        hyps = payload.get("hypotheses_evaluated", [])
        tl = payload.get("timeline_events", [])
        return {
            "icon": "🎉",
            "title": f"Synthesized Post-Mortem Report (v{version})",
            "badge": "Incident Report",
            "badge_color": "#059669",
            "summary": f"Synthesis complete with {len(tl)} ordered timeline events and {len(hyps)} evaluated hypotheses.",
            "details": payload,
        }

    return {
        "icon": "ℹ️",
        "title": f"Event: {ev_type}",
        "badge": ev_type,
        "badge_color": "#64748b",
        "summary": str(payload)[:100],
        "details": payload,
    }


def render_agent_stream_panel(events: list[AgentEvent], is_running: bool = False) -> None:
    """Render streaming agent events feed."""
    st.subheader("⚡ Live Agent Investigation Stream")

    if is_running:
        st.info("🔄 Autonomous investigation pipeline is currently running...")

    if not events:
        st.info(
            "No investigation has been executed yet. Click **'🚀 Investigate Incident'** in the sidebar to run."
        )
        return

    # Metrics summary header
    m1, m2, m3, m4 = st.columns(4)
    tool_calls = sum(1 for e in events if e.type == "tool_call")
    hyps_evaluated = sum(1 for e in events if e.type == "hypothesis")
    has_report = any(e.type == "report" for e in events)

    with m1:
        st.metric("Total Events", len(events))
    with m2:
        st.metric("Tools Invoked", tool_calls)
    with m3:
        st.metric("Hypotheses Tested", hyps_evaluated)
    with m4:
        st.metric("Report Status", "Completed 🎉" if has_report else "In Progress 🔄")

    st.markdown("---")

    # Render events in reverse chronological or standard order
    for idx, ev in enumerate(events):
        info = format_event_summary(ev)
        badge_html = f"<span style='background-color:{info['badge_color']};color:white;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;'>{info['badge']}</span>"

        with st.expander(
            f"{info['icon']} {info['title']} (#{idx + 1})",
            expanded=(idx >= len(events) - 3 or ev.type in ("hypothesis", "report")),
        ):
            st.markdown(f"{badge_html} &nbsp; {info['summary']}", unsafe_allow_html=True)
            if info["details"]:
                st.json(info["details"])
