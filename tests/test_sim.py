"""Unit tests for P2 simulation: parameter fitting, discrete event simulation, and interventions."""

import time
from pathlib import Path

from contracts.models import CounterfactualResult, SimParams
from sim.fit import fit_sim_params
from sim.pool_sim import run_counterfactual, simulate_pool

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "incident.db"


def test_fit_sim_params_real_db():
    """Verify fit_sim_params estimates parameters from incident database telemetry."""
    assert DB_PATH.exists(), "data/incident.db must exist"
    params = fit_sim_params(DB_PATH)

    assert isinstance(params, SimParams)
    assert params.pool_size == 20
    # Arrival rate in generator is ~1.65 req/s (window 480s, ~780 gateway spans)
    assert 1.0 <= params.arrival_rate_per_s <= 3.0, (
        f"Expected arrival rate ≈ 1.6 req/s, got {params.arrival_rate_per_s}"
    )
    # Mean service time for db-pool acquire/query
    assert 15.0 <= params.mean_service_ms <= 60.0, (
        f"Expected mean service time 15-60ms, got {params.mean_service_ms}"
    )
    # Leak probability computed from unreleased acquires / total acquires
    assert 0.01 <= params.leak_prob <= 0.10, f"Expected leak prob 0.01-0.10, got {params.leak_prob}"
    # Timeouts parsed from logs
    assert params.gateway_timeout_ms == 3000
    assert params.acquire_timeout_ms == 5000
    assert params.window_s >= 400


def test_fit_sim_params_fallback():
    """Verify fit_sim_params provides sensible defaults on empty/invalid database."""
    params = fit_sim_params(":memory:")
    assert isinstance(params, SimParams)
    assert params.pool_size == 20
    assert params.arrival_rate_per_s > 0
    assert params.mean_service_ms > 0


def test_fit_sim_params_scenario_dataset():
    """Verify fit_sim_params on scenario dataset where db-pool spans are named get_connection."""
    import tempfile

    from ingest.load_jsonl import load_telemetry

    scenario_file = ROOT_DIR / "data" / "scenarios" / "pool_leak_101.jsonl"
    if scenario_file.exists():
        with tempfile.TemporaryDirectory() as td:
            db_p = Path(td) / "scenario.db"
            load_telemetry(scenario_file, db_p)
            params = fit_sim_params(db_p)
            assert isinstance(params, SimParams)
            assert params.pool_size == 20
            assert params.arrival_rate_per_s > 0
            assert params.mean_service_ms > 0
            assert params.leak_prob > 0.0


def test_simulate_pool_speed_and_ci():
    """Verify discrete event simulation is fast (<1.0s for 300 runs) and CI bounds are valid."""
    params = SimParams(
        pool_size=20,
        arrival_rate_per_s=1.63,
        leak_prob=0.033,
        mean_service_ms=30.0,
        acquire_timeout_ms=5000,
        gateway_timeout_ms=3000,
        retry_max=3,
        window_s=480,
        seed=42,
    )

    t0 = time.time()
    prob, (ci_low, ci_high) = simulate_pool(params, n_runs=300)
    elapsed = time.time() - t0

    # Must execute within 1s to satisfy fast path for UI slider
    assert elapsed < 1.0, f"300 simulation runs took {elapsed:.3f}s, expected < 1.0s"
    assert 0.0 <= ci_low <= prob <= ci_high <= 1.0, (
        f"Invalid CI95 bounds: low={ci_low}, prob={prob}, high={ci_high}"
    )


def test_intervention_fix_leak():
    """Verify fix_leak intervention drops failure probability to near zero (P(504) ≈ 0)."""
    params = SimParams(
        pool_size=20,
        arrival_rate_per_s=1.63,
        leak_prob=0.033,
        mean_service_ms=30.0,
        acquire_timeout_ms=5000,
        gateway_timeout_ms=3000,
        retry_max=3,
        window_s=480,
        seed=42,
    )

    cf = run_counterfactual(params, "fix_leak", n_runs=300)
    assert isinstance(cf, CounterfactualResult)
    assert cf.intervention == "fix_leak"
    assert cf.baseline_failure_prob > 0.20
    # Intervened failure probability should be ≈ 0
    assert cf.intervened_failure_prob <= 0.02, (
        f"Expected fix_leak failure prob <= 0.02, got {cf.intervened_failure_prob}"
    )
    assert len(cf.model_assumptions) >= 2


def test_intervention_pool_size_50():
    """Verify pool_size=50 delays exhaustion, significantly reducing failures over window."""
    params = SimParams(
        pool_size=20,
        arrival_rate_per_s=1.63,
        leak_prob=0.033,
        mean_service_ms=30.0,
        acquire_timeout_ms=5000,
        gateway_timeout_ms=3000,
        retry_max=3,
        window_s=480,
        seed=42,
    )

    cf = run_counterfactual(params, "pool_size=50", n_runs=300)
    assert cf.intervention == "pool_size=50"
    # Enlarged pool accommodates leaked connections, reducing failures during window
    assert cf.intervened_failure_prob < cf.baseline_failure_prob
    assert any("50" in a for a in cf.model_assumptions)


def test_intervention_no_retries():
    """Verify no_retries intervention shows failures still persist (retries are amplifier)."""
    params = SimParams(
        pool_size=20,
        arrival_rate_per_s=1.63,
        leak_prob=0.033,
        mean_service_ms=30.0,
        acquire_timeout_ms=5000,
        gateway_timeout_ms=3000,
        retry_max=3,
        window_s=480,
        seed=42,
    )

    cf = run_counterfactual(params, "no_retries", n_runs=300)
    assert cf.intervention == "no_retries"
    # Even without retries, pool exhaustion causes primary requests to fail
    assert cf.intervened_failure_prob > 0.10, (
        f"Failures should still persist without retries, got {cf.intervened_failure_prob}"
    )
    assert any("amplifier" in a.lower() for a in cf.model_assumptions)
