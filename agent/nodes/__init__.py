"""Pipeline state machine nodes (owned by P3).

Nodes:
- telemetry_ingestion
- topology_builder
- hypothesis_generator
- counterfactual_verifier
- report_synthesizer
"""

PIPELINE_NODES = [
    "telemetry_ingestion",
    "topology_builder",
    "hypothesis_generator",
    "counterfactual_verifier",
    "report_synthesizer",
]
