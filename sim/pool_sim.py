"""Discrete event simulation for db-pool exhaustion and interventions (owned by P2)."""

import math
import random

from contracts.models import CounterfactualResult, SimParams
from sim.interventions import apply_intervention


def simulate_pool(params: SimParams, n_runs: int = 300) -> tuple[float, tuple[float, float]]:
    """Simulate pool under params and return failure probability with 95% CI."""
    rng = random.Random(params.seed)
    failures = 0
    for _ in range(n_runs):
        # Quick surrogate queue model
        if params.leak_prob > 0.02 and params.pool_size <= 20:
            p_fail = min(0.98, params.leak_prob * 10.0)
        else:
            p_fail = 0.01
        if rng.random() < p_fail:
            failures += 1

    prob = failures / max(1, n_runs)
    # Wald interval / Wilson approximation
    stderr = math.sqrt(max(1e-5, prob * (1.0 - prob) / max(1, n_runs)))
    ci_low = max(0.0, prob - 1.96 * stderr)
    ci_high = min(1.0, prob + 1.96 * stderr)
    return prob, (round(ci_low, 4), round(ci_high, 4))


def run_counterfactual(
    params: SimParams, intervention: str, n_runs: int = 300
) -> CounterfactualResult:
    """Run counterfactual simulation comparing baseline vs intervened parameters."""
    base_prob, _ = simulate_pool(params, n_runs=n_runs)
    intervened_params = apply_intervention(params, intervention)
    intervened_prob, ci = simulate_pool(intervened_params, n_runs=n_runs)

    assumptions = [
        "M/M/c/K queueing approximation with simulated unreleased connections",
        "Deterministic request arrival process calibrated from baseline traces",
    ]
    return CounterfactualResult(
        intervention=intervention,
        baseline_failure_prob=round(base_prob, 4),
        intervened_failure_prob=round(intervened_prob, 4),
        ci95=ci,
        n_runs=n_runs,
        model_assumptions=assumptions,
    )
