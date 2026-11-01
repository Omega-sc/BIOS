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


## Phase 2: Abstain and Incident Detection

### 1. What Changed
- **Robust Incident Detection Gate (`tools/anomaly.py`)**:
  - Implemented `detect_incident_anomalies(db, config)` computing Median Absolute Deviation (MAD) robust z-scores across early baseline telemetry:
    - Per-service span error rate (`status_code >= 400` or non-200)
    - Per-service p99 duration/latency
    - Per-service span request arrival rate
    - Every metric time series in SQLite `metrics` table
  - Uses directional shift modeling (elevations in error rate, p99 latency, resource saturation gauges) with baseline relative variance floor to eliminate false positives on nominal operational jitter.
  - Automatically flags anomalies when robust z-score $z \ge 3.5$ or absolute error rate $\ge 5\%$.
  - Exposed via `tools/api.py` registry as `tool_detect_incident_anomalies`.
- **Configurable Thresholds with Rationale (`agent/config.py`)**:
  - `IncidentDetectionConfig`:
    - `baseline_time_fraction = 0.25`: first quartile establishes healthy baseline period.
    - `mad_z_threshold = 3.5`: corresponds to $p < 0.0005$ under normality, barring false alarms.
    - `min_error_rate_anomaly = 0.05`: sustained 5% error rate is clinically anomalous.
  - `HypothesisEvaluationConfig`:
    - `min_evidence_score = 2.0`: hypotheses must have positive supporting evidence beyond prior.
    - `min_confidence_margin = 0.15`: requires at least 15% confidence separation between top hypothesis and runner-up to avoid guessing on ambiguous evidence.
    - `max_unresolved_confidence = 0.45`: caps confidence when evidence is ambiguous or weak.
    - `none_of_above_mass = 0.5`: explicit unallocated prior probability mass in Bayesian normalization.
- **Hypothesis Abstention & Confidence Calibration (`agent/scoring.py`)**:
  - Prior to hypothesis evaluation, runs incident detection gate.
  - If no incident detected (`incident_detected = False`):
    - All candidate hypotheses are given `verdict = "unresolved"`.
    - Confidence is capped at $\le 0.10$.
    - Supporting and refuting evidence lists remain empty (`[]`).
    - Explicit limitations recorded: healthy baseline confirmed.
  - If an incident is detected:
    - Best hypothesis verified against `min_evidence_score` and `min_confidence_margin`.
    - If evidence is below threshold or margin is too narrow: best hypothesis verdict is set to `"unresolved"`, confidence capped at 0.45, and missing evidence is explicitly recorded in `missing_evidence`.
- **Pipeline Nodes Abstention Integration**:
  - `agent/nodes/hypothesis_generator.py`: runs incident detection gate upfront and logs detection metadata to `state.data_quality["incident_detection"]`.
  - `agent/nodes/counterfactual_verifier.py`: skips counterfactual discrete-event simulation when `not state.incident_detected`; never upgrades an unresolved or refuting hypothesis to root cause.
  - `agent/nodes/report_synthesizer.py`: when `not state.incident_detected`, produces an empty timeline, sets `remediation = ["No remediation required: telemetry indicates healthy operational baseline."]`, and logs healthy limitations.
  - `contracts/models.py`: additively extended `HypothesisEvaluated` with `missing_evidence: list[str] = Field(default_factory=list)`.

### 2. Evidence
- **Blind Mutation Checks (`eval/blind/blind_check.py`)**:
  - `[PASS] renamed services/metrics: runs and cites only real names`
  - `[PASS] no skew present: does not invent one`
  - `[PASS] replay mode with different data: does not return canned report`
  - `[PASS] no-incident control: abstains instead of naming a root cause`
  - Result: **4/4 Passed (100% blind checks passing)**.
- **Scenario Verification**:
  - 20 of 20 incident scenarios in `data/scenarios/` correctly detected as incidents.
  - Healthy slice correctly detected as healthy control (`incident_detected=False`, max z-score = 1.78 < 3.5).
- **Unit & Integration Test Suite (`tests/`)**:
  - 66 of 66 tests passing (including 4 new dedicated incident detection and margin tests in `tests/test_incident_detection.py`).
- **Code Quality**:
  - `uv run ruff check .` passed with 0 errors.
  - `uv run ruff format --check .` passed (68 files formatted).

### 3. Remaining Gaps & Limits
- Detector logic for resource leaks, retry storms, slow DB, and timeout misconfigurations currently lives in `agent/scoring.py` heuristic rules; Phase 3 will decompose these into formal deterministic detector functions bound to discovered entities.
