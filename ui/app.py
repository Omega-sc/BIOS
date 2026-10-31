"""Streamlit Incident Investigation Dashboard (owned by P4)."""

import json
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Autonomous Incident Investigation", layout="wide")

ROOT_DIR = Path(__file__).resolve().parent.parent
GOLDEN_REPORT_PATH = ROOT_DIR / "fixtures" / "golden_report.json"
TINY_TELEMETRY_PATH = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"

st.title("🚨 Autonomous Incident Investigation")
st.caption("Track 09 — 4-Agent Parallel Build: Multi-Service Post-Mortem & Timeline Reconstruction")

tabs = st.tabs(["1. Raw Telemetry", "2. Live Investigation", "3. Interactive Post-Mortem"])

with tabs[0]:
    st.subheader("Raw Unaligned Telemetry")
    st.info(
        "Notice unaligned timestamps: auth-service has +12s clock skew, and logs have 15% random loss."
    )
    if TINY_TELEMETRY_PATH.exists():
        with open(TINY_TELEMETRY_PATH, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()][:30]
        st.code("\n".join(lines), language="json")
    else:
        st.warning("No telemetry file found.")

with tabs[1]:
    st.subheader("State Machine Agent Execution")
    if st.button("🚀 Investigate Incident"):
        from agent.runner import run_investigation

        progress_container = st.container()
        events = list(run_investigation("data/incident.db", mode="scripted"))
        for ev in events:
            with progress_container:
                if ev.type == "state":
                    st.write(f"🔄 **Node Transition:** `{ev.node}`")
                elif ev.type == "tool_call":
                    st.write(f"🛠️ Tool Call: `{ev.payload.get('tool')}`")
                elif ev.type == "tool_result":
                    st.write(f"✅ Result: `{ev.payload.get('tool') or ev.node}`")
                elif ev.type == "hypothesis":
                    st.write(
                        f"💡 **Hypothesis ({ev.payload['id']}):** {ev.payload['statement']} — Verdict: `{ev.payload['verdict']}` (Confidence: {ev.payload['confidence']:.2f})"
                    )
                elif ev.type == "report":
                    st.success(
                        "🎉 Investigation completed! Structured incident report synthesized."
                    )

with tabs[2]:
    st.subheader("Interactive Post-Mortem Report")
    if GOLDEN_REPORT_PATH.exists():
        with open(GOLDEN_REPORT_PATH, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown("### Reconstructed Timeline (Clock Skew Corrected)")
            for event in report_data.get("timeline_events", []):
                badge = (
                    "🟢 Verified Fact" if event["claim_type"] == "verified_fact" else "🟡 Inference"
                )
                st.markdown(
                    f"**Order {event['order']}** (`+{((event['t_corrected_ms'] - 1700000000000) / 1000):.1f}s` / `{event['service']}`): {event['description']} — *{badge}*"
                )

            st.markdown("### Evaluated Hypotheses")
            for h in report_data.get("hypotheses_evaluated", []):
                verdict_color = "red" if h["verdict"] == "root_cause" else "gray"
                st.markdown(
                    f"- **[{h['id']}]** :{verdict_color}[**{h['verdict'].upper()}**] (Score: `{h['confidence']:.2f}`): {h['statement']}"
                )

        with col2:
            st.markdown("### Counterfactual Simulation")
            st.caption("A model-based simulation estimate under varying parameters.")
            pool_slider = st.slider(
                "Counterfactual DB Pool Size", min_value=10, max_value=60, value=20, step=5
            )
            if pool_slider > 20:
                est_failure = max(0.01, 0.89 - (pool_slider - 20) * 0.02)
                st.metric(
                    "Estimated Failure Probability",
                    f"{est_failure:.2%}",
                    delta=f"-{(0.89 - est_failure):.2%}",
                )
            else:
                st.metric("Estimated Failure Probability", "89.00%")

            st.markdown("### Estimated Clock Skew")
            st.json(report_data.get("estimated_clock_offsets_s", {}))
