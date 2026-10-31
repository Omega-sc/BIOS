"""Intervention definitions for counterfactual simulation (owned by P2)."""

from contracts.models import SimParams


def apply_intervention(params: SimParams, intervention: str) -> SimParams:
    """Return modified SimParams corresponding to the counterfactual scenario."""
    modified = params.model_copy()
    if intervention == "fix_leak":
        modified.leak_prob = 0.0
    elif intervention.startswith("pool_size="):
        size = int(intervention.split("=")[1])
        modified.pool_size = size
    elif intervention == "no_retries":
        modified.retry_max = 0
    return modified
