# Fix Log: Track 09 Autonomous Incident Investigation

## Phase 1: Stop Fabrication and Traps

### 1. What Changed
- **Removed Fake Baseline Timeline Event**:
  - In `agent/nodes/report_synthesizer.py`, deleted fabricated baseline `TimelineEvent` and fake metric pointer (`metric:db_pool.active_connections@1700000000.000`). Reconstructed timeline now strictly contains discovered telemetry events. Empty timelines populate `limitations` instead of inventing events.
- **Removed Fabricated Dependency Graph & Hardcoded Failure Frontier**:
  - In `tools/dag.py`, removed the fallback graph (`call_count=100`, `mean_latency=50.0`) and hardcoded returns (`"auth-service -> db-pool"`).
  - Implemented dynamic node and edge discovery from `spans`, `metrics`, and `logs`.
  - Re-implemented `failure_frontier` to compute directly from graph attributes: identifies deepest failing node whose dependencies are healthy, or earliest edge failing at caller-callee boundary. Returns `None` on healthy or empty data.
- **Dynamic Clock Skew Estimation**:
  - In `tools/skew.py`, removed hardcoded fallback dictionaries (`{"gateway": 0.0, "auth-service": 0.0, "db-pool": 0.0}`) returning `{}` when no cross-service spans exist.
  - Dynamically selected reference clock based on the service owning the most root spans (`parent_span_id IS NULL`), falling back to the most-called upstream service, rather than hardcoding `"gateway"`.
- **Decoupled Metric & Capacity Constants**:
  - Removed scenario constants (`METRIC_SOURCES`, `SERVICES`, `POOL_MAX`, `SKEW_MS`) from runtime logic in `tools/metrics.py`, `agent/validate.py`, `agent/scoring.py`, and `sim/fit.py`.
  - Metric-to-service mapping is resolved dynamically from SQLite `metrics` table.
  - Pool capacity in `sim/fit.py` is estimated from data (observed gauge plateau/maximum or span concurrency) rather than fixed constant `POOL_MAX=20`.
- **Content-Hashed Replay Verification**:
  - In `agent/runner.py`, replaced filename-based replay selection (`"incident.db"` in path) with a cryptographic SHA-256 hash of all SQLite table rows in deterministic order.
  - Replay files in `fixtures/replay/` are only replayed when their stored `db_content_hash` matches exactly. On match, streamed events are tagged `payload["provenance"] = "recorded"`.
  - When hash mismatches or is absent (such as mutated or renamed databases), the real investigation pipeline runs dynamically, tagging events `payload["provenance"] = "recomputed"`.
  - Added visible "RECORDED RUN" notification banner in `ui/app.py` when replaying verified recordings.
- **Additive Schema & State Updates**:
  - Updated `contracts/models.py` and `agent/state.py` with additive fields on `IncidentReport` and `IncidentState`:
    - `incident_detected: bool = True`
    - `data_quality: dict = Field(default_factory=dict)`
    - `limitations: list[str] = Field(default_factory=list)`
    - `investigation_seconds: float | None = None`
  - Updated validation in `agent/validate.py` to allow empty timelines when `report.limitations` are present or `report.incident_detected` is False.
- **Test Invariant Rewrites (Hard Rules Compliance)**:
  - In `tests/test_tools.py`, updated `test_failure_frontier_across_scenarios` to assert the invariant property `ff_slow in ("db-pool", "auth-service -> db-pool")` instead of an arbitrary hardcoded string.
  - In `tests/test_smoke.py`, updated `test_tools_stubs` to test invariants: on an empty database, tools must not fabricate offsets or topology (`offsets == {}`, `dag.number_of_nodes() == 0`, `frontier is None`); on populated telemetry, discovered entities must match ingested services.
- **Blind Check Migration**:
  - Moved `blind_check.py` to `eval/blind/blind_check.py` and left clean entrypoint forwarder in root `blind_check.py`.

### 2. Evidence
- **Blind Mutation Checks (`eval/blind/blind_check.py`)**:
  - `[PASS] renamed services/metrics: runs and cites only real names`
  - `[PASS] no skew present: does not invent one`
  - `[PASS] replay mode with different data: does not return canned report`
  - `[FAIL] no-incident control: abstains instead of naming a root cause` (Expected target for P2)
  - Result: **3/4 Passed** (baseline was 1/4).
- **Unit & Integration Test Suite (`tests/`)**:
  - 62 of 62 tests passing (`uv run pytest -v tests/`).
- **Code Quality**:
  - `uv run ruff check .` passed with 0 errors.
  - `uv run ruff format --check .` passed (64 files formatted).

### 3. Remaining Gaps & Limits
- Incident detection gate is not yet implemented (investigation does not abstain on healthy telemetry where no anomaly exists, producing false positive root causes; addressed in Phase 2).
- Hypothesis statements still use heuristic role mapping (e.g., `mid_svc`, `leaf_svc`) rather than fully deterministic structural detectors (addressed in Phase 3).
