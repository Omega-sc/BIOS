"""Counterfactual verifier node for investigation pipeline (owned by P3)."""

from __future__ import annotations

from collections.abc import Iterator

from agent.state import IncidentState
from contracts.models import AgentEvent
from sim.fit import fit_sim_params
from sim.pool_sim import run_counterfactual


def counterfactual_verifier_node(state: IncidentState, n_runs: int = 200) -> Iterator[AgentEvent]:
    """Execute counterfactual discrete-event simulations to verify root cause and contributing factors."""
    state.current_node = "counterfactual_verifier"

    start_ev = AgentEvent(
        type="state",
        node="counterfactual_verifier",
        payload={"status": "running_simulation"},
    )
    state.record_event(start_ev)
    yield start_ev

    # 1. Fit simulation parameters dynamically from DB telemetry
    tc_fit = AgentEvent(
        type="tool_call",
        node="counterfactual_verifier",
        payload={"tool": "fit_sim_params", "args": {"db": state.db_path}},
    )
    state.record_event(tc_fit)
    yield tc_fit

    sim_params = fit_sim_params(state.db_path)
    state.sim_params = sim_params

    tr_fit = AgentEvent(
        type="tool_result",
        node="counterfactual_verifier",
        payload={"tool": "fit_sim_params", "result": sim_params.model_dump()},
    )
    state.record_event(tr_fit)
    yield tr_fit

    # 2. Run counterfactual for H1 ("fix_leak")
    tc_leak = AgentEvent(
        type="tool_call",
        node="counterfactual_verifier",
        payload={
            "tool": "run_counterfactual",
            "args": {"intervention": "fix_leak", "n_runs": n_runs},
        },
    )
    state.record_event(tc_leak)
    yield tc_leak

    cf_leak = run_counterfactual(sim_params, "fix_leak", n_runs=n_runs)
    state.counterfactuals["fix_leak"] = cf_leak

    tr_leak = AgentEvent(
        type="tool_result",
        node="counterfactual_verifier",
        payload={"intervention": "fix_leak", "result": cf_leak.model_dump()},
    )
    state.record_event(tr_leak)
    yield tr_leak

    # 3. Run counterfactual for H1 ("pool_size=50")
    tc_pool50 = AgentEvent(
        type="tool_call",
        node="counterfactual_verifier",
        payload={
            "tool": "run_counterfactual",
            "args": {"intervention": "pool_size=50", "n_runs": n_runs},
        },
    )
    state.record_event(tc_pool50)
    yield tc_pool50

    cf_pool50 = run_counterfactual(sim_params, "pool_size=50", n_runs=n_runs)
    state.counterfactuals["pool_size=50"] = cf_pool50

    tr_pool50 = AgentEvent(
        type="tool_result",
        node="counterfactual_verifier",
        payload={"intervention": "pool_size=50", "result": cf_pool50.model_dump()},
    )
    state.record_event(tr_pool50)
    yield tr_pool50

    # 4. Run counterfactual for H2 ("no_retries")
    tc_retries = AgentEvent(
        type="tool_call",
        node="counterfactual_verifier",
        payload={
            "tool": "run_counterfactual",
            "args": {"intervention": "no_retries", "n_runs": n_runs},
        },
    )
    state.record_event(tc_retries)
    yield tc_retries

    cf_retries = run_counterfactual(sim_params, "no_retries", n_runs=n_runs)
    state.counterfactuals["no_retries"] = cf_retries

    tr_retries = AgentEvent(
        type="tool_result",
        node="counterfactual_verifier",
        payload={"intervention": "no_retries", "result": cf_retries.model_dump()},
    )
    state.record_event(tr_retries)
    yield tr_retries

    # 5. Verdict logic based on counterfactual outcomes
    for h in state.hypotheses:
        if h.id == "H1":
            h.counterfactual = cf_leak
            # Fixing the leak eliminates failures -> proves H1 is root cause
            if cf_leak.baseline_failure_prob > 0.05 and cf_leak.intervened_failure_prob <= 0.05:
                h.verdict = "root_cause"
        elif h.id == "H2":
            h.counterfactual = cf_retries
            # Removing retries leaves failures elevated while H1 is root cause -> contributing factor
            if cf_retries.intervened_failure_prob > 0.05 and any(
                other.id == "H1" and other.verdict == "root_cause" for other in state.hypotheses
            ):
                h.verdict = "contributing_factor"
        elif h.id in ("H3", "H4"):
            if not any(
                other.id == h.id and other.verdict == "root_cause" for other in state.hypotheses
            ):
                h.verdict = "rejected"

    state.current_node = "report_synthesizer"
