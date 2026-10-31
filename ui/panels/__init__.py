"""UI Panels package (owned by P4)."""

from ui.panels.agent_stream import render_agent_stream_panel
from ui.panels.counterfactual import render_counterfactual_panel
from ui.panels.dag_view import render_dag_panel
from ui.panels.post_mortem import render_post_mortem_panel
from ui.panels.raw_telemetry import render_raw_telemetry_panel
from ui.panels.timeline import render_timeline_panel

__all__ = [
    "render_raw_telemetry_panel",
    "render_agent_stream_panel",
    "render_dag_panel",
    "render_timeline_panel",
    "render_post_mortem_panel",
    "render_counterfactual_panel",
]
