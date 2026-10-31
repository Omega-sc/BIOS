"""Reconstructed timeline panel with Plotly Gantt/Scatter and clock skew correction toggle (owned by P4)."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from contracts.models import TimelineEvent


def create_timeline_figure(
    timeline_events: list[TimelineEvent | dict[str, Any]],
    skew_corrected: bool = True,
    clock_offsets: dict[str, float] | None = None,
) -> go.Figure:
    """Create interactive Plotly timeline scatter/Gantt chart.

    When skew_corrected is False:
        auth-service timestamps are shifted late (+12.0s), demonstrating the paradoxical
        inversion where gateway 504 errors appear to precede downstream auth pool exhaustion.

    When skew_corrected is True:
        auth-service timestamps are corrected, restoring true chronological order.
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
            font=dict(size=14, color="#64748b"),
        )
        return fig

    offsets = clock_offsets or {
        "auth-service": 12.0,
        "gateway": 0.0,
        "db-pool": 0.0,
        "frontend": 0.0,
    }
    auth_offset_ms = int(offsets.get("auth-service", 12.0) * 1000)

    # Base reference timestamp (incident start)
    base_t = 1700000000000

    service_y_map = {
        "db-pool": 1,
        "auth-service": 2,
        "gateway": 3,
        "frontend": 4,
    }

    colors = {
        "db-pool": "#ef4444",
        "auth-service": "#f97316",
        "gateway": "#eab308",
        "frontend": "#3b82f6",
    }

    fig = go.Figure()

    # Process events and calculate effective timestamps
    processed_events = []
    for item in timeline_events:
        if isinstance(item, dict):
            ev = TimelineEvent.model_validate(item)
        else:
            ev = item

        svc = ev.service
        claim_type = ev.claim_type

        # Calculate displayed timestamp
        if not skew_corrected and svc == "auth-service":
            # Add +12s skew back to auth-service
            display_t_ms = ev.t_corrected_ms + auth_offset_ms
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
    y_line = [service_y_map.get(e["service"], 2) for e in processed_events]
    fig.add_trace(
        go.Scatter(
            x=x_line,
            y=y_line,
            mode="lines",
            line=dict(color="#cbd5e1", width=2, dash="dot"),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    # Add markers by claim type
    for c_type, marker_symbol, label_prefix in [
        ("verified_fact", "circle", "🟢 Verified Fact"),
        ("inference", "diamond", "🟡 Inference"),
    ]:
        type_events = [e for e in processed_events if e["claim_type"] == c_type]
        if not type_events:
            continue

        x_pts = [e["rel_s"] for e in type_events]
        y_pts = [service_y_map.get(e["service"], 2) for e in type_events]
        m_colors = [colors.get(e["service"], "#64748b") for e in type_events]
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
                    line=dict(width=2, color="#0f172a"),
                ),
                text=[f"#{e['order']}" for e in type_events],
                textposition="top center",
                textfont=dict(size=11, color="#1e293b", family="sans-serif"),
                hovertext=custom_text,
                hoverinfo="text",
            )
        )

    # Layout styling
    fig.update_layout(
        title=dict(
            text=(
                "<b>Reconstructed Incident Chronology</b>: "
                + (
                    "True Causal Time (-12.0s Skew Corrected)"
                    if skew_corrected
                    else "⚠️ Raw Wall-Clock (Uncorrected +12.0s Skew)"
                )
            ),
            font=dict(size=14, color="#1e293b"),
        ),
        xaxis=dict(
            title="Time Relative to Incident Start (seconds)",
            gridcolor="#f1f5f9",
            zerolinecolor="#94a3b8",
            ticksuffix="s",
            showgrid=True,
        ),
        yaxis=dict(
            title="",
            tickmode="array",
            tickvals=[1, 2, 3, 4],
            ticktext=["db-pool", "auth-service", "gateway", "frontend"],
            gridcolor="#f1f5f9",
            range=[0.5, 4.5],
        ),
        height=380,
        margin=dict(l=80, r=30, t=50, b=50),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        hoverlabel=dict(
            bgcolor="#ffffff",
            font_size=12,
            font_family="sans-serif",
        ),
    )

    return fig


def _select_evidence(evidence_id: str) -> None:
    st.session_state["selected_evidence_id"] = evidence_id
    st.session_state["evidence_version"] = st.session_state.get("evidence_version", 0) + 1


def render_timeline_panel(
    timeline_events: list[TimelineEvent | dict[str, Any]],
    clock_offsets: dict[str, float] | None = None,
) -> None:
    """Render reconstructed timeline panel with skew correction toggle."""
    st.subheader("⏱️ Reconstructed Timeline & Causality Analysis")

    t_col1, t_col2 = st.columns([1.2, 1])
    with t_col1:
        skew_corrected = st.toggle(
            "⏱️ Apply Skew Correction (-12.0s on auth-service)",
            value=st.session_state.get("skew_corrected", True),
            key="skew_toggle_widget",
        )
        st.session_state["skew_corrected"] = skew_corrected

    with t_col2:
        offsets = clock_offsets or {"auth-service": 12.0, "gateway": 0.0, "db-pool": 0.0}
        offset_val = offsets.get("auth-service", 12.0)
        st.caption(
            f"Estimated Skew: `auth-service`: +{offset_val:.1f}s | `gateway`: 0.0s | `db-pool`: 0.0s"
        )

    # Causal reversal banner
    if not skew_corrected:
        st.markdown(
            """
            <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #991b1b;">
                ❌ <b>Apparent Causal Paradox (Uncorrected Wall-Clock)</b>: Notice that Gateway HTTP 504 timeouts appear at
                <code>+95.0s</code>, which is <b>prior to</b> auth-service connection pool exhaustion appearing at <code>+102.0s - +112.0s</code>!
                This apparent paradox misleads responders into suspecting gateway timeouts rather than the downstream pool leak.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="background-color: #f0fdf4; border-left: 4px solid #22c55e; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #166534;">
                ✅ <b>True Causality Restored (Skew Corrected)</b>: By shifting <code>auth-service</code> timestamps forward by -12.0s,
                db-pool saturation occurs at <code>+90.0s</code>, strictly <b>preceding</b> gateway 504 errors at <code>+95.0s</code>.
                Frontend CPU spike follows at <code>+125.0s</code> (+30s lag), proving client retries are an amplifier rather than root cause.
            </div>
            """,
            unsafe_allow_html=True,
        )

    fig = create_timeline_figure(
        timeline_events,
        skew_corrected=skew_corrected,
        clock_offsets=clock_offsets,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table of timeline events
    st.markdown("#### Chronological Events Sequence")
    for item in timeline_events:
        if isinstance(item, dict):
            ev = TimelineEvent.model_validate(item)
        else:
            ev = item

        badge = "🟢 Verified Fact" if ev.claim_type == "verified_fact" else "🟡 Inference"
        rel_s = round((ev.t_corrected_ms - 1700000000000) / 1000.0, 1)

        with st.expander(f"Order #{ev.order} (+{rel_s}s / {ev.service}): {ev.description[:60]}..."):
            st.markdown(f"**Claim Type:** `{badge}`")
            st.markdown(f"**Description:** {ev.description}")
            st.markdown(f"**Corrected Timestamp:** `{ev.t_corrected_ms}` (+{rel_s}s)")
            if ev.evidence:
                st.markdown("**Cited Evidence:**")
                for ptr in ev.evidence:
                    b_col, n_col = st.columns([1.2, 3])
                    with b_col:
                        st.button(
                            f"🔍 {ptr.id}",
                            key=f"btn_tl_panel_{ev.order}_{ptr.id}",
                            on_click=_select_evidence,
                            args=(ptr.id,),
                        )
                    with n_col:
                        st.caption(f"[{ptr.kind}] *{ptr.note}*")
