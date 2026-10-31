"""Pipeline state machine nodes (owned by P3).

Nodes:
- telemetry_ingestion
- topology_builder
- hypothesis_generator
- counterfactual_verifier
- report_synthesizer
"""

from agent.nodes.counterfactual_verifier import counterfactual_verifier_node
from agent.nodes.hypothesis_generator import hypothesis_generator_node
from agent.nodes.report_synthesizer import report_synthesizer_node
from agent.nodes.telemetry_ingestion import telemetry_ingestion_node
from agent.nodes.topology_builder import topology_builder_node

PIPELINE_NODES = [
    "telemetry_ingestion",
    "topology_builder",
    "hypothesis_generator",
    "counterfactual_verifier",
    "report_synthesizer",
]

__all__ = [
    "PIPELINE_NODES",
    "telemetry_ingestion_node",
    "topology_builder_node",
    "hypothesis_generator_node",
    "counterfactual_verifier_node",
    "report_synthesizer_node",
]
