"""Prompt templates for investigation state machine nodes (owned by P3)."""

HYPOTHESIS_GENERATION_PROMPT = """Analyze the provided service topology and anomalous metric onsets:
Topology: {topology}
Metric Onsets: {onsets}
Formulate candidate root cause hypotheses and identify potential red herrings.
"""

REPORT_SYNTHESIS_PROMPT = """Synthesize an incident post-mortem report based on verified telemetry:
Timeline: {timeline}
Hypotheses: {hypotheses}
Counterfactuals: {counterfactuals}
"""
