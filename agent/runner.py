"""Agent state machine runner for autonomous incident investigation (owned by P3)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from agent.llm import LLMAdapter
from agent.nodes.counterfactual_verifier import counterfactual_verifier_node
from agent.nodes.hypothesis_generator import hypothesis_generator_node
from agent.nodes.report_synthesizer import (
    report_synthesizer_node,
    synthesize_incident_report,
)
from agent.nodes.telemetry_ingestion import telemetry_ingestion_node
from agent.nodes.topology_builder import topology_builder_node
from agent.prompts import (
    INVESTIGATION_SYSTEM_PROMPT,
    RETRY_VALIDATION_PROMPT,
)
from agent.scoring import evaluate_hypotheses_from_db
from agent.state import IncidentState
from agent.validate import validate_incident_report
from contracts.models import (
    AgentEvent,
    IncidentReport,
)
from sim.fit import fit_sim_params
from sim.pool_sim import run_counterfactual
from tools.api import (
    TOOLS,
    build_dependency_dag,
    estimate_clock_offsets,
    failure_frontier,
    get_metric_derivatives,
    get_tool_specs,
    rank_metric_onsets,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
REPLAY_DIR = ROOT_DIR / "fixtures" / "replay"


def compute_db_content_hash(db_path: str | Path | sqlite3.Connection) -> str:
    """Compute deterministic SHA256 content hash across all database tables in canonical order."""
    close = False
    if isinstance(db_path, (str, Path)):
        p = Path(db_path)
        if not p.exists():
            return ""
        if str(p).endswith(".jsonl"):
            import tempfile

            from ingest.load_jsonl import load_telemetry

            tmp_dir = tempfile.mkdtemp()
            tmp_db = Path(tmp_dir) / "hash_tmp.db"
            try:
                load_telemetry(p, tmp_db)
                return compute_db_content_hash(tmp_db)
            finally:
                if tmp_db.exists():
                    try:
                        tmp_db.unlink()
                    except Exception:
                        pass
        conn = sqlite3.connect(str(p))
        close = True
    else:
        conn = db_path

    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name ASC")
        tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
        hasher = hashlib.sha256()
        for t in tables:
            hasher.update(t.encode("utf-8"))
            cur.execute(f"SELECT * FROM {t} ORDER BY rowid ASC")
            for row in cur.fetchall():
                hasher.update(str(row).encode("utf-8"))
        return hasher.hexdigest()
    except Exception:
        return ""
    finally:
        if close:
            conn.close()


def _run_scripted_pipeline(state: IncidentState) -> Iterator[AgentEvent]:
    """Execute standard 5-node scripted investigation pipeline."""
    for ev in telemetry_ingestion_node(state):
        yield ev
    for ev in topology_builder_node(state):
        yield ev
    for ev in hypothesis_generator_node(state):
        yield ev
    for ev in counterfactual_verifier_node(state):
        yield ev
    for ev in report_synthesizer_node(state):
        yield ev


def _run_replay_mode(db_path: str) -> Iterator[AgentEvent]:
    """Stream pre-recorded replay events on exact DB content hash match, or recompute."""
    current_hash = compute_db_content_hash(db_path)
    matched_events: list[dict] | None = None

    if current_hash and REPLAY_DIR.exists():
        for replay_file in sorted(REPLAY_DIR.glob("*.json")):
            if replay_file.name == "llm_cache.json":
                continue
            try:
                with open(replay_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get("db_content_hash") == current_hash:
                    matched_events = data.get("events", [])
                    break
            except Exception:
                continue

    if matched_events:
        for item in matched_events:
            ev = AgentEvent.model_validate(item)
            ev.payload["provenance"] = "recorded"
            yield ev
    else:
        # No exact hash match -> execute real pipeline dynamically with provenance='recomputed'
        state = IncidentState(db_path=db_path, mode="replay")
        state.data_quality["provenance"] = "recomputed"
        for ev in _run_scripted_pipeline(state):
            ev.payload["provenance"] = "recomputed"
            yield ev


def _run_live_mode(db_path: str, max_steps: int = 12) -> Iterator[AgentEvent]:
    """Execute live tool-calling loop using LLMAdapter and cached replay responses."""
    state = IncidentState(db_path=db_path, mode="live")
    llm = LLMAdapter()

    # Initial state event
    start_ev = AgentEvent(
        type="state",
        node="telemetry_ingestion",
        payload={"status": "started", "db_path": db_path, "mode": "live"},
    )
    state.record_event(start_ev)
    yield start_ev

    # Assemble tool specs
    tool_specs = get_tool_specs()
    # Add simulation tools and submit_report to tool specs
    sim_fit_spec = {
        "name": "fit_sim_params",
        "description": "Fit discrete-event connection pool simulation parameters from SQLite database telemetry.",
        "parameters": {
            "type": "object",
            "properties": {
                "db": {"type": "string", "description": "Path to SQLite database."},
            },
            "required": ["db"],
        },
    }
    sim_cf_spec = {
        "name": "run_counterfactual",
        "description": "Run Monte Carlo counterfactual simulation for an intervention ('fix_leak', 'no_retries', 'pool_size=50').",
        "parameters": {
            "type": "object",
            "properties": {
                "intervention": {"type": "string", "description": "Intervention name."},
                "n_runs": {"type": "integer", "description": "Number of simulation iterations."},
            },
            "required": ["intervention"],
        },
    }
    submit_report_spec = {
        "name": "submit_report",
        "description": "Submit final synthesized post-mortem incident report matching IncidentReport contract.",
        "parameters": {
            "type": "object",
            "properties": {
                "report": {"type": "object", "description": "IncidentReport JSON dictionary."},
            },
        },
    }
    all_specs = tool_specs + [sim_fit_spec, sim_cf_spec, submit_report_spec]

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Investigate the incident in database '{db_path}'. Identify root cause, contributing factors, evaluate counterfactuals, and submit the final incident report.",
        }
    ]

    report_submitted = False
    validation_retries = 0

    for step in range(max_steps):
        if report_submitted:
            break

        resp = llm.complete_with_tools(messages, all_specs, system=INVESTIGATION_SYSTEM_PROMPT)
        tool_calls = resp.get("tool_calls", [])

        if not tool_calls:
            # LLM provided pure text or ended
            messages.append({"role": "assistant", "content": resp.get("content", "")})
            break

        # Record assistant tool call in messages
        messages.append(resp)

        for tc in tool_calls:
            tool_name = tc.get("name")
            tool_args = tc.get("input", {})
            call_id = tc.get("id", f"call_{step}")

            # Map active pipeline node
            node_map = {
                "estimate_clock_offsets": "telemetry_ingestion",
                "rank_metric_onsets": "telemetry_ingestion",
                "query_spans": "telemetry_ingestion",
                "get_metric_derivatives": "telemetry_ingestion",
                "build_dependency_dag": "topology_builder",
                "failure_frontier": "topology_builder",
                "fit_sim_params": "counterfactual_verifier",
                "run_counterfactual": "counterfactual_verifier",
                "submit_report": "report_synthesizer",
            }
            active_node = node_map.get(tool_name, "telemetry_ingestion")
            state.current_node = active_node

            tc_ev = AgentEvent(
                type="tool_call",
                node=active_node,
                payload={"tool": tool_name, "args": tool_args},
            )
            state.record_event(tc_ev)
            yield tc_ev

            # Execute tool
            tool_res_payload: Any = {}
            if tool_name == "estimate_clock_offsets":
                res = estimate_clock_offsets(tool_args.get("db") or db_path)
                state.clock_offsets = res
                tool_res_payload = res
            elif tool_name == "rank_metric_onsets":
                onsets = rank_metric_onsets(tool_args.get("db") or db_path)
                state.metric_onsets = onsets
                tool_res_payload = [o.model_dump() for o in onsets]
            elif tool_name == "query_spans":
                args = tool_args.copy()
                args.setdefault("db", db_path)
                tool_res_payload = TOOLS["query_spans"](**args)
            elif tool_name == "get_metric_derivatives":
                args = tool_args.copy()
                args.setdefault("db", db_path)
                tool_res_payload = get_metric_derivatives(**args).model_dump()
            elif tool_name == "build_dependency_dag":
                dag = build_dependency_dag(tool_args.get("db") or db_path)
                state.dag = dag
                tool_res_payload = {"nodes": list(dag.nodes()), "edges": list(dag.edges())}
            elif tool_name == "failure_frontier":
                frontier = failure_frontier(state.dag, db=tool_args.get("db") or db_path)
                state.failure_frontier = frontier
                tool_res_payload = {"failure_frontier": frontier}
            elif tool_name == "fit_sim_params":
                params = fit_sim_params(tool_args.get("db") or db_path)
                state.sim_params = params
                tool_res_payload = params.model_dump()
            elif tool_name == "run_counterfactual":
                intervention = tool_args.get("intervention", "fix_leak")
                n_runs = tool_args.get("n_runs", 150)
                if state.sim_params is None:
                    state.sim_params = fit_sim_params(db_path)
                cf_res = run_counterfactual(state.sim_params, intervention, n_runs=n_runs)
                state.counterfactuals[intervention] = cf_res
                tool_res_payload = cf_res.model_dump()
            elif tool_name == "submit_report":
                report_dict = tool_args.get("report")
                if (
                    report_dict
                    and isinstance(report_dict, dict)
                    and "hypotheses_evaluated" in report_dict
                ):
                    try:
                        cand_report = IncidentReport.model_validate(report_dict)
                    except Exception:
                        cand_report = None
                else:
                    cand_report = None

                if cand_report is None:
                    cand_report = synthesize_incident_report(state)

                val_issues = validate_incident_report(cand_report, db=db_path)
                if val_issues:
                    if validation_retries < 2:
                        validation_retries += 1
                        messages.append(
                            {
                                "role": "user",
                                "content": RETRY_VALIDATION_PROMPT.format(
                                    validation_errors="\n".join(val_issues)
                                ),
                            }
                        )
                        tool_res_payload = {"status": "validation_failed", "issues": val_issues}
                    else:
                        # Retries exhausted: fall back to programmatic synthesis to guarantee valid report
                        state.report = synthesize_incident_report(state)
                        report_submitted = True
                        tool_res_payload = {
                            "status": "validation_failed_fallback_synthesized",
                            "issues": val_issues,
                        }
                else:
                    report_submitted = True
                    state.report = cand_report
                    tool_res_payload = {"status": "report_accepted"}

            tr_ev = AgentEvent(
                type="tool_result",
                node=active_node,
                payload={"tool": tool_name, "result": tool_res_payload},
            )
            state.record_event(tr_ev)
            yield tr_ev

            messages.append(
                {
                    "role": "tool",
                    "tool": tool_name,
                    "tool_call_id": call_id,
                    "content": json.dumps(tool_res_payload, default=str),
                }
            )

            # Stream hypothesis events when hypotheses are evaluated
            if tool_name in ("failure_frontier", "run_counterfactual"):
                if not state.hypotheses:
                    state.hypotheses = evaluate_hypotheses_from_db(db_path)
                for h in state.hypotheses:
                    h_ev = AgentEvent(
                        type="hypothesis", node="hypothesis_generator", payload=h.model_dump()
                    )
                    state.record_event(h_ev)
                    yield h_ev

    # If loop ended and report is ready, yield report event
    if state.report is not None:
        rep_ev = AgentEvent(
            type="report",
            node="report_synthesizer",
            payload=state.report.model_dump(),
        )
        state.record_event(rep_ev)
        yield rep_ev
    else:
        # Fallback to report synthesizer node to finalize
        for ev in report_synthesizer_node(state):
            yield ev


def run_investigation(
    db_path: str,
    mode: Literal["live", "replay", "scripted"] = "scripted",
) -> Iterator[AgentEvent]:
    """Execute investigation state machine pipeline and stream AgentEvents.

    Modes:
      - 'scripted': Deterministic rule-based investigator executing the 5-node pipeline.
      - 'replay': Re-streams pre-recorded events offline from fixtures/replay/.
      - 'live': Autonomous LLM tool-calling loop with replay caching and validation retry.

    Pipeline:
      telemetry_ingestion -> topology_builder -> hypothesis_generator ->
      counterfactual_verifier -> report_synthesizer
    """
    if mode == "replay":
        yield from _run_replay_mode(db_path)
    elif mode == "live":
        yield from _run_live_mode(db_path)
    else:
        state = IncidentState(db_path=db_path, mode="scripted")
        yield from _run_scripted_pipeline(state)
