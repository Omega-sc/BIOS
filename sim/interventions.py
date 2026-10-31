"""Intervention definitions for counterfactual simulation (owned by P2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from contracts.models import CounterfactualResult, SimParams

if TYPE_CHECKING:
    pass


def apply_intervention(params: SimParams, intervention: str) -> SimParams:
    """Return modified SimParams corresponding to the counterfactual scenario.

    Supported interventions:
      - 'fix_leak': Sets leak_prob to 0.0.
      - 'pool_size=<N>': Scales connection pool size to N (e.g. 'pool_size=50').
      - 'no_retries': Disables client retries (retry_max = 0).
    """
    modified = params.model_copy()
    if intervention == "fix_leak":
        modified.leak_prob = 0.0
    elif intervention.startswith("pool_size="):
        size = int(intervention.split("=")[1])
        modified.pool_size = size
    elif intervention == "no_retries":
        modified.retry_max = 0
    return modified


def run_counterfactual(
    params: SimParams, intervention: str, n_runs: int = 300
) -> CounterfactualResult:
    """Run counterfactual simulation comparing baseline vs intervened parameters."""
    from sim.pool_sim import run_counterfactual as _run

    return _run(params, intervention, n_runs=n_runs)
