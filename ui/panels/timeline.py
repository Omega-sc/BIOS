"""Reconstructed timeline panel with Plotly Gantt/Scatter and dynamic clock skew correction toggle."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from contracts.models import TimelineEvent

PALETTE = ["#1B4332", "#2D6A4F", "#40916C", "#52665A", "#74C69D", "#B7CEB8"]


def create_timeline_figure(
    timeline_events: list[TimelineEvent | dict[str, Any]],
    skew_corrected: bool = True,
    clock_offsets: dict[str, float] | None = None,
) -> go.Figure:
    """Create interactive Plotly timeline scatter/Gantt chart.

    When skew_corrected is False:
        Timestamps reflect raw wall-clock time with estimated skew added back.

    When skew_corrected is True:
        Timestamps are corrected using estimated clock offsets, restoring chronological causal order.
    """
    if not timeline_events:
        fig = go.Figure()
        fig.add_annotation(
            text="No timeline events available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=14, color="#52665A"),
        )
        return fig

    offsets = clock_offsets or {}

    # Parse events
    events: list[TimelineEvent] = []
    for item in timeline_events:
        if isinstance(item, dict):
            events.append(TimelineEvent.model_validate(item))
        else:
            events.append(item)

    # Discover unique services preserving order of appearance
    unique_services: list[str] = []
    for ev in events:
        if ev.service and ev.service not in unique_services:
            unique_services.append(ev.service)
    if not unique_services:
        unique_services = ["unknown"]

    service_y_map = {svc: idx + 1 for idx, svc in enumerate(unique_services)}
    colors = {svc: PALETTE[i % len(PALETTE)] for i, svc in enumerate(unique_services)}

    # Base reference timestamp (earliest corrected event or 0)
    base_t = min((e.t_corrected_ms for e in events), default=0)

    fig = go.Figure()

    # Process events and calculate effective timestamps
    processed_events = []
    for ev in events:
        svc = ev.service
        claim_type = ev.claim_type
        svc_offset_ms = int(offsets.get(svc, 0.0) * 1000)

        # Calculate displayed timestamp
        if not skew_corrected:
            display_t_ms = ev.t_corrected_ms + svc_offset_ms
        else:
            display_t_ms = ev.t_corrected_ms

        rel_s = round((display_t_ms - base_t) / 1000.0, 1)

        processed_events.append(
            {
                "order": ev.order,
                "service": svc,
                "t_ms": display_t_ms,
                "rel_s": rel_s,
                "description": ev.description,
                "claim_type": claim_type,
                "evidence_count": len(ev.evidence),
                "evidence_ids": [p.id for p in ev.evidence],
            }
        )

    # Sort events by displayed timestamp
    processed_events.sort(key=lambda x: x["t_ms"])

    # Draw timeline connecting path
    x_line = [e["rel_s"] for e in processed_events]
    y_line = [service_y_map.get(e["service"], 1) for e in processed_events]
    fig.add_trace(
        go.Scatter(
            x=x_line,
            y=y_line,
            mode="lines",
            line=dict(color="#95D5B2", width=2, dash="dot"),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    # Add markers by claim type
    for c_type, marker_symbol, label_prefix in [
        ("verified_fact", "circle", "Verified Fact"),
        ("inference", "diamond", "Inference"),
    ]:
        type_events = [e for e in processed_events if e["claim_type"] == c_type]
        if not type_events:
            continue

        x_pts = [e["rel_s"] for e in type_events]
        y_pts = [service_y_map.get(e["service"], 1) for e in type_events]
        m_colors = [colors.get(e["service"], "#2D6A4F") for e in type_events]
        custom_text = [
            f"<b>Order #{e['order']}</b> (+{e['rel_s']}s)<br>"
            f"<b>Service:</b> {e['service']}<br>"
            f"<b>Claim:</b> {label_prefix}<br>"
            f"<b>Detail:</b> {e['description']}<br>"
            f"<b>Evidence:</b> {', '.join(e['evidence_ids'][:2])}"
            for e in type_events
        ]

        fig.add_trace(
            go.Scatter(
                x=x_pts,
                y=y_pts,
                mode="markers+text",
                name=label_prefix,
                marker=dict(
                    symbol=marker_symbol,
                    size=16,
                    color=m_colors,
                    line=dict(width=2, color="#1B4332"),
                ),
                text=[f"#{e['order']}" for e in type_events],
                textposition="top center",
                textfont=dict(size=11, color="#1B4332", family="sans-serif"),
                hovertext=custom_text,
                hoverinfo="text",
            )
        )

    # Layout styling
    title_text = (
        "<b>Reconstructed Incident Chronology</b>: True Causal Time (Skew Corrected)"
        if skew_corrected
        else "<b>Reconstructed Incident Chronology</b>: Raw Wall-Clock (Uncorrected Telemetry)"
    )

    fig.update_layout(
        title=dict(
            text=title_text,
            font=dict(size=14, color="#1B4332"),
        ),
        xaxis=dict(
            title="Time Relative to Incident Start (seconds)",
            gridcolor="#D8E2DC",
            zerolinecolor="#95D5B2",
            ticksuffix="s",
            showgrid=True,
        ),
        yaxis=dict(
            title="",
            tickmode="array",
            tickvals=list(range(1, len(unique_services) + 1)),
            ticktext=unique_services,
            gridcolor="#D8E2DC",
            range=[0.5, len(unique_services) + 0.5],
        ),
        height=380,
        margin=dict(l=80, r=30, t=50, b=50),
        plot_bgcolor="#FAF9F6",
        paper_bgcolor="#FAF9F6",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11, color="#1B4332"),
        ),
        hoverlabel=dict(
            bgcolor="#FAF9F6",
            font_size=12,
            font_family="sans-serif",
            font_color="#1B4332",
        ),
    )

    return fig


def _select_evidence(evidence_id: str) -> None:
    st.session_state["selected_evidence_id"] = evidence_id
    st.session_state["evidence_version"] = st.session_state.get("evidence_version", 0) + 1
    st.session_state["open_evidence_dialog"] = evidence_id


def render_timeline_panel(
    timeline_events: list[TimelineEvent | dict[str, Any]],
    clock_offsets: dict[str, float] | None = None,
) -> None:
    """Render reconstructed timeline panel with skew correction toggle."""
    st.subheader("Reconstructed Timeline & Causality Analysis")

    offsets = clock_offsets or {}
    skewed_svcs = [f"`{s}`: {off:+.1f}s" for s, off in offsets.items() if abs(off) >= 0.1]
    skew_summary = " | ".join(skewed_svcs) if skewed_svcs else "No significant skew detected"

    t_col1, t_col2 = st.columns([1.2, 1])
    with t_col1:
        skew_corrected = st.toggle(
            "Apply Skew Correction",
            value=st.session_state.get("skew_corrected", True),
            key="skew_toggle_widget",
        )
        st.session_state["skew_corrected"] = skew_corrected

    with t_col2:
        st.caption(f"Estimated Skew: {skew_summary}")

    if not skew_corrected:
        st.markdown(
            """
            <div style="background-color: #F4F3EE; border-left: 4px solid #1B4332; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #1B4332;">
                <b>Apparent Causal Paradox (Uncorrected Wall-Clock)</b>: In uncorrected coordinates,
                clock skew can shift root-cause downstream events later than symptom events in upstream callers,
                presenting an apparent causal inversion.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="background-color: #D8F3DC; border-left: 4px solid #2D6A4F; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #1B4332;">
                <b>True Causality Restored (Skew Corrected)</b>: Synchronizing event timestamps using estimated
                clock offsets restores true chronological order, aligning symptoms with their root cause origin.
            </div>
            """,
            unsafe_allow_html=True,
        )

    fig = create_timeline_figure(
        timeline_events,
        skew_corrected=skew_corrected,
        clock_offsets=offsets,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Event drill-down table
    st.markdown("#### Chronological Sequence & Evidence Citations")

    parsed_events: list[TimelineEvent] = []
    for item in timeline_events:
        if isinstance(item, dict):
            parsed_events.append(TimelineEvent.model_validate(item))
        else:
            parsed_events.append(item)

    if not parsed_events:
        st.info("No timeline events to display.")
        return

    # Render event cards
    for ev in sorted(parsed_events, key=lambda x: x.order):
        badge_bg = "#2D6A4F" if ev.claim_type == "verified_fact" else "#52665A"
        claim_label = "VERIFIED FACT" if ev.claim_type == "verified_fact" else "INFERENCE"

        with st.container():
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(
                    f"""
                    <div style="margin-bottom: 4px;">
                        <span style="background-color:{badge_bg};color:#FAF9F6;padding:2px 6px;border-radius:4px;font-size:10.5px;font-weight:700;">{claim_label}</span>
                        <b>Event #{ev.order}</b> &nbsp;|&nbsp; <code>{ev.service}</code> &nbsp;|&nbsp; <i>t = +{(ev.t_corrected_ms - parsed_events[0].t_corrected_ms) / 1000.0:.1f}s</i>
                    </div>
                    <div style="font-size: 13.5px; color: #2C3E35; margin-bottom: 6px;">
                        {ev.description}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with c2:
                if ev.evidence:
                    for ptr in ev.evidence:
                        is_active = ptr.id == st.session_state.get("selected_evidence_id")
                        btn_label = f"Inspect: {ptr.id}"
                        st.button(
                            btn_label,
                            key=f"btn_ev_{ev.order}_{ptr.id}",
                            type="primary" if is_active else "secondary",
                            on_click=_select_evidence,
                            args=(ptr.id,),
                        )
