"""Interactive post-mortem incident report panel (owned by P4)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from contracts.models import IncidentReport


def render_post_mortem_panel(
    report: IncidentReport | dict[str, Any] | None,
) -> None:
    """Render structured post-mortem incident report with verified facts vs inferences."""
    st.subheader("📋 Structured Post-Mortem Incident Report")

    if report is None:
        st.info(
            "No incident report synthesized yet. Run the investigation or load the golden report fixture."
        )
        return

    if isinstance(report, dict):
        rep = IncidentReport.model_validate(report)
    else:
        rep = report

    # Header statistics
    hcol1, hcol2, hcol3 = st.columns(3)
    root_cause_hyps = [h for h in rep.hypotheses_evaluated if h.verdict == "root_cause"]
    root_cause_title = root_cause_hyps[0].id if root_cause_hyps else "Undetermined"

    with hcol1:
        st.metric("Root Cause Identified", root_cause_title)
    with hcol2:
        facts_count = sum(1 for e in rep.timeline_events if e.claim_type == "verified_fact")
        st.metric("Verified Facts vs Inferences", f"{facts_count} / {len(rep.timeline_events)}")
    with hcol3:
        st.metric("Actionable Remediations", len(rep.remediation))

    st.markdown("---")

    # Section 1: Evaluated Hypotheses
    st.markdown("#### 💡 Evaluated Hypotheses & Likelihood Confidence")
    st.caption(
        "Bayesian posterior confidence computed via calibrated likelihood ratios. Terminology: *evidence-based score*."
    )

    for h in rep.hypotheses_evaluated:
        if h.verdict == "root_cause":
            badge_html = "<span style='background-color:#dc2626;color:white;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;'>🔴 ROOT CAUSE</span>"
            card_border = "#f87171"
            card_bg = "#fef2f2"
        elif h.verdict == "contributing_factor":
            badge_html = "<span style='background-color:#d97706;color:white;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;'>🟡 CONTRIBUTING FACTOR</span>"
            card_border = "#fbbf24"
            card_bg = "#fffbeb"
        else:
            badge_html = "<span style='background-color:#64748b;color:white;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;'>⚪ REJECTED</span>"
            card_border = "#cbd5e1"
            card_bg = "#f8fafc"

        with st.container():
            st.markdown(
                f"""
                <div style="background-color:{card_bg}; border: 1.5px solid {card_border}; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <span style="font-size: 15px; font-weight: 700; color: #0f172a;">[{h.id}] {h.statement}</span>
                        {badge_html}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Score bar
            s_col, b_col = st.columns([1, 3])
            with s_col:
                st.markdown(f"**Evidence-based score:** `{h.confidence:.2f}`")
            with b_col:
                st.progress(float(h.confidence))

            # Supporting and Refuting evidence pointers
            ecol1, ecol2 = st.columns(2)
            with ecol1:
                st.markdown(f"**Supporting Evidence ({len(h.supporting)}):**")
                if h.supporting:
                    for ptr in h.supporting:
                        if st.button(f"🔍 {ptr.id}", key=f"btn_supp_{h.id}_{ptr.id}"):
                            st.session_state["selected_evidence_id"] = ptr.id
                            st.session_state["evidence_lookup_input"] = ptr.id
                            st.rerun()
                        st.caption(f"↳ *{ptr.note}*")
                else:
                    st.caption("None cited.")

            with ecol2:
                st.markdown(f"**Refuting Evidence ({len(h.refuting)}):**")
                if h.refuting:
                    for ptr in h.refuting:
                        if st.button(f"🔍 {ptr.id}", key=f"btn_ref_{h.id}_{ptr.id}"):
                            st.session_state["selected_evidence_id"] = ptr.id
                            st.session_state["evidence_lookup_input"] = ptr.id
                            st.rerun()
                        st.caption(f"↳ *{ptr.note}*")
                else:
                    st.caption("None cited.")

            st.write("")

    st.markdown("---")

    # Section 2: Reconstructed Timeline
    st.markdown("#### ⏳ Reconstructed Timeline Events")
    for ev in rep.timeline_events:
        badge = "🟢 Verified Fact" if ev.claim_type == "verified_fact" else "🟡 Inference"
        rel_s = round((ev.t_corrected_ms - 1700000000000) / 1000.0, 1)

        with st.expander(f"Order #{ev.order} (+{rel_s}s / {ev.service}): {ev.description}"):
            st.markdown(f"**Claim Type:** `{badge}`")
            st.markdown(f"**Service:** `{ev.service}`")
            st.markdown(f"**Corrected Time:** `+{rel_s}s` (`{ev.t_corrected_ms} ms`)")
            if ev.evidence:
                st.markdown("**Evidence Pointers:**")
                for ptr in ev.evidence:
                    b_col, n_col = st.columns([1, 3])
                    with b_col:
                        if st.button(f"🔍 {ptr.id}", key=f"btn_tl_{ev.order}_{ptr.id}"):
                            st.session_state["selected_evidence_id"] = ptr.id
                            st.session_state["evidence_lookup_input"] = ptr.id
                            st.rerun()
                    with n_col:
                        st.write(f"*{ptr.note}*")

    st.markdown("---")

    # Section 3: Actionable Remediations
    st.markdown("#### 🛡️ Actionable Remediation Plan")
    for idx, rem in enumerate(rep.remediation, start=1):
        st.markdown(
            f"""
            <div style="background-color: #f0fdf4; border-left: 4px solid #16a34a; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;">
                <b style="color: #15803d;">Action Item #{idx}:</b> <span style="color: #166534;">{rem}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
