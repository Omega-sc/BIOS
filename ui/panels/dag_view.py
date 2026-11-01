"""Service topology DAG view panel with dynamic Graphviz rendering."""

from __future__ import annotations

from typing import Any

import streamlit as st


def generate_dag_dot(
    dag_data: dict[str, Any] | None = None,
    failure_frontier: str | None = None,
) -> str:
    """Generate Graphviz DOT specification dynamically highlighting the failure frontier.

    Annotates edges with call counts and mean latencies, and highlights the failure frontier
    boundary and failing nodes using the Jungle Green and Off-White palette.
    """
    ff = (failure_frontier or "").strip()

    dot_lines = [
        "digraph ServiceTopology {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  node [fontname="Helvetica,Arial,sans-serif", fontsize=11, fontcolor="#1B4332", shape=box, style="rounded,filled", margin="0.2,0.15"];',
        '  edge [fontname="Helvetica,Arial,sans-serif", fontsize=10, color="#52665A", fontcolor="#2C3E35"];',
        "",
        "  // Nodes",
    ]

    nodes_list: list[dict[str, Any]] = []
    edges_list: list[dict[str, Any]] = []

    if dag_data and isinstance(dag_data, dict):
        nodes_list = dag_data.get("nodes", []) or []
        edges_list = dag_data.get("links", []) or dag_data.get("edges", []) or []

    # If no nodes provided in dag_data but a failure frontier is specified, infer nodes
    if not nodes_list and ff:
        if "->" in ff:
            parts = [p.strip() for p in ff.split("->")]
            for p in parts:
                nodes_list.append({"id": p, "error_rate": 0.0, "span_count": 0})
            if not edges_list and len(parts) >= 2:
                edges_list.append(
                    {
                        "source": parts[0],
                        "target": parts[1],
                        "call_count": 100,
                        "mean_latency": 25.0,
                    }
                )
        else:
            nodes_list.append({"id": ff, "error_rate": 1.0, "span_count": 0})

    # Render discovered nodes
    rendered_node_ids = set()
    for n in nodes_list:
        if isinstance(n, dict):
            nid = str(n.get("id", ""))
            err_rate = float(n.get("error_rate", 0.0))
            span_count = int(n.get("span_count", 0))
        else:
            nid = str(n)
            err_rate = 0.0
            span_count = 0

        if not nid or nid in rendered_node_ids:
            continue
        rendered_node_ids.add(nid)

        is_frontier_node = nid in ff

        if is_frontier_node:
            fill_color = "#D8F3DC"
            border_color = "#1B4332"
            pen_width = "3.5"
            role_label = "[Failure Frontier]"
        elif err_rate > 0.05:
            fill_color = "#F4F3EE"
            border_color = "#2D6A4F"
            pen_width = "2.0"
            role_label = f"[Error Rate: {err_rate * 100:.1f}%]"
        else:
            fill_color = "#FAF9F6"
            border_color = "#52665A"
            pen_width = "1.5"
            role_label = "[Healthy]"

        label_parts = [f'"{nid}"', role_label]
        if span_count > 0:
            label_parts.append(f"spans: {span_count}")
        node_label = "\\n".join(label_parts)

        dot_lines.append(
            f'  "{nid}" [label="{node_label}", fillcolor="{fill_color}", color="{border_color}", penwidth={pen_width}];'
        )

    dot_lines.append("")
    dot_lines.append("  // Edges with call counts and mean latencies")

    # Render edges
    for e in edges_list:
        if isinstance(e, dict):
            src = str(e.get("source", "") or e.get("from", ""))
            tgt = str(e.get("target", "") or e.get("to", ""))
            calls = int(e.get("call_count", 0))
            lat = float(e.get("mean_latency", 0.0))
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            src = str(e[0])
            tgt = str(e[1])
            calls = 0
            lat = 0.0
        else:
            continue

        if not src or not tgt:
            continue

        is_ff_edge = ff and (f"{src} -> {tgt}" in ff or f"{src}->{tgt}" in ff.replace(" ", ""))

        if is_ff_edge:
            if calls > 0:
                edge_label = f"FAILURE FRONTIER\\ncalls: {calls}, lat: {lat:.1f}ms"
            else:
                edge_label = "FAILURE FRONTIER"
            dot_lines.append(
                f'  "{src}" -> "{tgt}" [label="{edge_label}", color="#1B4332", fontcolor="#1B4332", penwidth=4.0];'
            )
        else:
            if calls > 0:
                edge_label = f" calls: {calls} \\n lat: {lat:.1f}ms "
            else:
                edge_label = ""
            dot_lines.append(
                f'  "{src}" -> "{tgt}" [label="{edge_label}", color="#52665A", penwidth=1.5];'
            )

    dot_lines.append("}")
    return "\n".join(dot_lines)


def render_dag_panel(
    dag_data: dict[str, Any] | None = None,
    failure_frontier: str | None = None,
) -> None:
    """Render the service dependency DAG and failure frontier analysis."""
    st.subheader("Service Topology & Failure Frontier")

    ff = (failure_frontier or "None detected").strip()

    ff_explanation = (
        f"Localized failure boundary: <code>{ff}</code>. Cross-service error propagation, call graph topology, "
        "and metric onset ordering isolate this boundary as the earliest point of system breakdown."
        if ff != "None detected"
        else "No anomalous failure frontier identified. System dependencies are healthy or unobserved."
    )

    st.markdown(
        f"""
        <div style="background-color: #EBF3EE; border-left: 4px solid #1B4332; padding: 12px 16px; border-radius: 4px; margin-bottom: 16px;">
            <b style="color: #1B4332; font-size: 15px;">Localized Failure Frontier: <code>{ff}</code></b><br/>
            <span style="color: #2C3E35; font-size: 13px;">
                {ff_explanation}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    dot_str = generate_dag_dot(dag_data, failure_frontier=ff)

    try:
        st.graphviz_chart(dot_str, use_container_width=True)
    except Exception as e:
        st.error(f"Failed to render graphviz chart: {e}")
        st.code(dot_str, language="dot")

    # Topology Summary Table
    st.markdown("#### Discovered Service Metrics & Boundary Assessment")
    nodes = (dag_data or {}).get("nodes", [])
    if nodes:
        col1, col2 = st.columns(2)
        mid = (len(nodes) + 1) // 2
        with col1:
            for n in nodes[:mid]:
                if isinstance(n, dict):
                    nid = n.get("id", "unknown")
                    err_rate = n.get("error_rate", 0.0)
                    spans = n.get("span_count", 0)
                    onset = n.get("metric_onset")
                else:
                    nid = str(n)
                    err_rate = 0.0
                    spans = 0
                    onset = None
                onset_str = f"+{onset / 1000.0:.1f}s" if onset else "None"
                st.markdown(
                    f"- **`{nid}`**: Error Rate: `{err_rate * 100:.1f}%` | Spans: `{spans}` | Onset: `{onset_str}`"
                )
        with col2:
            for n in nodes[mid:]:
                if isinstance(n, dict):
                    nid = n.get("id", "unknown")
                    err_rate = n.get("error_rate", 0.0)
                    spans = n.get("span_count", 0)
                    onset = n.get("metric_onset")
                else:
                    nid = str(n)
                    err_rate = 0.0
                    spans = 0
                    onset = None
                onset_str = f"+{onset / 1000.0:.1f}s" if onset else "None"
                st.markdown(
                    f"- **`{nid}`**: Error Rate: `{err_rate * 100:.1f}%` | Spans: `{spans}` | Onset: `{onset_str}`"
                )
    else:
        st.info("No service topology metadata discovered yet.")
