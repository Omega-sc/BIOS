"""Prompt templates and system prompts for investigation agent (owned by P3)."""

INVESTIGATION_SYSTEM_PROMPT = """You are an Autonomous Incident Investigation Agent.
Your goal is to investigate multi-service distributed system outages, determine the true root cause, evaluate contributing factors, refute red herrings, and synthesize a structured post-mortem report.

Core Directives and Operational Rules:
1. NEVER order cross-service events by raw timestamps. Always call `estimate_clock_offsets` first and evaluate events in clock-corrected time coordinates.
2. Call analytical tools to gather telemetry evidence BEFORE making claims.
3. Every claim must cite specific EvidencePointer IDs:
   - Spans: exact `span_id` (e.g. `sp-gw-0362` or `span-gw-1`)
   - Logs: exact `log_id` (e.g. `log-auth-leak-0198` or `log-auth-3`)
   - Metrics: `<metric>@<ts_ms>` (e.g. `db_pool.active_connections@1700000090000`)
4. Strictly separate verified facts (supported by resolving database rows) from inferences.
5. Terminology requirement: In UI and report text, describe counterfactuals as "a model-based simulation estimate" and confidences as "evidence-based score".
6. When investigation is complete, submit the final report via the `submit_report` tool matching the IncidentReport schema.
"""

RETRY_VALIDATION_PROMPT = """The submitted incident report failed validation checks:
{validation_errors}

Please address each validation error, ensure all evidence pointer IDs resolve to real rows in the database, verify chronological timeline ordering, and submit the corrected report via `submit_report`.
"""

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
