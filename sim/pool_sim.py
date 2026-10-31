"""Discrete event simulation for db-pool exhaustion and counterfactual interventions (owned by P2)."""

from __future__ import annotations

import heapq
import math

import numpy as np

from contracts.models import CounterfactualResult, SimParams
from sim.interventions import apply_intervention


def simulate_pool(params: SimParams, n_runs: int = 300) -> tuple[float, tuple[float, float]]:
    """Simulate connection pool under params and return failure probability with 95% CI.

    Fast discrete-event simulation modeling:
      - Exponential/Poisson request arrivals over window_s
      - Connection acquisition from pool with capacity pool_size
      - Random leak probability where connections are permanently withheld
      - Acquire timeout and gateway 504 timeout thresholds
      - Optional client retries up to retry_max

    Args:
        params: SimParams configuration.
        n_runs: Number of Monte Carlo simulation runs (default 300).

    Returns:
        tuple[float, tuple[float, float]]: (mean_failure_prob, (ci_low, ci_high)).
    """
    import collections

    rng = np.random.default_rng(params.seed or 42)
    failure_rates: list[float] = []

    arr_rate = max(0.1, params.arrival_rate_per_s)
    win_s = params.window_s
    n_reqs = max(50, int(arr_rate * win_s))
    st_scale = max(0.001, params.mean_service_ms / 1000.0)
    timeout = min(params.gateway_timeout_ms, params.acquire_timeout_ms) / 1000.0
    retry_max = params.retry_max
    pool_sz = params.pool_size

    for _ in range(n_runs):
        interarrivals = rng.exponential(1.0 / arr_rate, size=n_reqs)
        arrivals = np.cumsum(interarrivals)
        st = rng.exponential(st_scale, size=n_reqs)
        leaks = rng.random(size=n_reqs) < params.leak_prob

        free_conns = pool_sz
        # Priority queue holds (t, event_type, req_id, retry_cnt)
        # event_type: 0 = RELEASE, 1 = ARRIVAL
        pq: list[tuple[float, int, int, int]] = []
        for i in range(n_reqs):
            if arrivals[i] > win_s:
                break
            heapq.heappush(pq, (arrivals[i], 1, i, 0))

        failed_requests = 0
        total_requests = 0
        wait_queue: collections.deque[tuple[float, int, int, bool, float]] = collections.deque()

        while pq:
            t, ev_type, req_id, retry_cnt = heapq.heappop(pq)
            if ev_type == 0:  # RELEASE
                conns_freed = 1
                while conns_freed > 0 and wait_queue:
                    arr_t, q_id, q_retry, q_leak, q_st = wait_queue.popleft()
                    if t - arr_t <= timeout:
                        conns_freed -= 1
                        if not q_leak:
                            heapq.heappush(pq, (t + q_st, 0, q_id, q_retry))
                    else:
                        failed_requests += 1
                        if q_retry < retry_max:
                            backoff = rng.uniform(0.5, 1.5)
                            if arr_t + timeout + backoff < win_s:
                                heapq.heappush(
                                    pq, (arr_t + timeout + backoff, 1, q_id, q_retry + 1)
                                )
                if conns_freed > 0:
                    free_conns += conns_freed
            else:  # ARRIVAL
                total_requests += 1
                is_leak = bool(leaks[req_id]) if req_id < len(leaks) else False
                req_st = float(st[req_id]) if req_id < len(st) else st_scale
                if free_conns > 0:
                    free_conns -= 1
                    if not is_leak:
                        heapq.heappush(pq, (t + req_st, 0, req_id, retry_cnt))
                else:
                    wait_queue.append((t, req_id, retry_cnt, is_leak, req_st))

        # Drain unfulfilled requests in wait queue
        while wait_queue:
            arr_t, q_id, q_retry, q_leak, q_st = wait_queue.popleft()
            failed_requests += 1
            rem = retry_max - q_retry
            if rem > 0:
                failed_requests += rem
                total_requests += rem

        rate = failed_requests / max(1, total_requests)
        failure_rates.append(rate)

    prob = float(np.mean(failure_rates))
    se = float(np.std(failure_rates)) / math.sqrt(max(1, n_runs))
    ci_low = max(0.0, prob - 1.96 * se)
    ci_high = min(1.0, prob + 1.96 * se)
    return round(prob, 4), (round(ci_low, 4), round(ci_high, 4))


def run_counterfactual(
    params: SimParams, intervention: str, n_runs: int = 300
) -> CounterfactualResult:
    """Run counterfactual simulation comparing baseline vs intervened parameters.

    Args:
        params: Baseline SimParams.
        intervention: Intervention string ('fix_leak', 'pool_size=50', 'no_retries').
        n_runs: Monte Carlo iterations (default 300).

    Returns:
        CounterfactualResult: Baseline and intervened failure probabilities with 95% CI.
    """
    base_prob, _ = simulate_pool(params, n_runs=n_runs)
    intervened_params = apply_intervention(params, intervention)
    intervened_prob, ci = simulate_pool(intervened_params, n_runs=n_runs)

    if intervention == "fix_leak":
        assumptions = [
            "Token validator error path fixed to guarantee connection release",
            "Elimination of connection leak reduces pool exhaustion probability to zero",
            "Steady-state request arrival distribution matches observed telemetry",
        ]
    elif intervention.startswith("pool_size="):
        size = intervention.split("=")[1]
        assumptions = [
            f"Connection pool size increased to {size}",
            "Delays connection pool exhaustion under persistent leak but does not prevent exhaustion",
            "Downstream database supports increased concurrent connections",
        ]
    elif intervention == "no_retries":
        assumptions = [
            "Client retry backoff eliminated (retries = 0) upon 504 timeout",
            "Failure onset and underlying connection exhaustion remain unchanged; retries act as amplifier",
            "Leaked connections in auth-service pool remain unreleased",
        ]
    else:
        assumptions = [
            f"Intervention '{intervention}' evaluated via discrete-event queue simulation",
            "Standard M/M/c/K queueing approximation with unreleased connection tracking",
        ]

    return CounterfactualResult(
        intervention=intervention,
        baseline_failure_prob=round(base_prob, 4),
        intervened_failure_prob=round(intervened_prob, 4),
        ci95=ci,
        n_runs=n_runs,
        model_assumptions=assumptions,
    )
