# Slide Deck Outline: Autonomous Incident Investigation

**Track**: Track 09 — Autonomous Incident Investigation
**Topic**: Multi-Service Post-Mortem & Timeline Reconstruction Under Telemetry Skew, Clock Desynchronization & Packet Loss
**Format**: 8-Slide Executive & Technical Presentation Deck

---

## Slide 1: Title & Executive Summary

### Title
**Autonomous Incident Investigation**: Reconstructing Causality Through the Fog of Corrupted Telemetry

### Subtitle
*A Deterministic & Agentic Architecture for Automated SRE Root-Cause Analysis Under Clock Skew, Packet Drop, and Misleading Cascades*

### Key Visuals
- High-level system workflow: Raw Corrupted Telemetry $\to$ Causal Tools $\to$ State Machine Agent $\to$ Bayesian Scoring $\to$ Counterfactual Verification $\to$ Verified Post-Mortem Report.
- KPI Badges:
  - **100.00%** Top-1 Root Cause Accuracy across 21 Scenarios
  - **0.0038** Multi-Class Brier Calibration Score
  - **100%** Grounded Evidence Row Resolution
  - **< 30s** Autonomous Investigation Time

### Presenter Notes & Takeaway
> "In distributed production systems, incidents are rarely clean. Telemetry arrives with unaligned clocks, dropped logs, and cascading retry storms that mislead human responders. We present an autonomous investigator that mathematically uncovers true causality and provides verifiable remediations."

---

## Slide 2: The Incident: Citizen-Facing 504 Cascade

### Problem Statement
- **The Outage**: High-volume citizen-facing services fail with widespread `HTTP 504 Gateway Timeout` errors.
- **Immediate Alert Storm**:
  - `gateway.error_rate_5xx` spikes to 65%.
  - `frontend.cpu_pct` surges past 88% saturation.
  - Citizens experience service unavailability.
- **The SRE Dilemma**:
  - Responders open monitoring dashboards and see conflicting signals across 4 microservices (`gateway`, `frontend`, `auth-service`, `db-pool`).
  - Misleading symptom: Frontend CPU spikes trigger on-call alerts first, prompting engineers to chase frontend scaling.

### Architectural Context
```
 [Frontend Client] ---> [API Gateway] ---> [Auth-Service] ---> [Database Pool]
   (CPU Spike: 88%)      (504 Timeouts: 65%)   (Leak on Error Path)   (Fixed Cap: 20 conns)
```

---

## Slide 3: The Fog of Telemetry: Skew & Packet Corruption

### Real-World Telemetry Realities Injected in Track 09
1. **Clock Desynchronization**:
   - `auth-service` suffers from a **+12,000 ms (+12.0s)** uncorrected wall-clock drift.
   - Root service `gateway` and downstream `db-pool` run on reference clocks ($\Delta = 0.0\text{s}$).
2. **Packet Drop & Telemetry Loss**:
   - 15% random log drop simulating buffer overflow and UDP syslog drops.
   - ~3% orphan spans where parent traces were dropped in transit.
3. **The Causal Reversal Paradox**:
   - In raw timestamps: Gateway 504 errors appear at $t = 95\text{s}$.
   - Auth-service connection exhaustion errors appear at $t = 107\text{s} - 112\text{s}$.
   - **Paradox**: Downstream auth appears to fail *after* upstream gateway times out!

### Visual Comparison: Raw vs Corrected Timelines
```
RAW (Uncorrected):
  t=90s: db-pool baseline (2/20)
  t=95s: Gateway 504 Errors [PARADOX: Gateway fails first!]
  t=107s: auth-service pool exhaustion [Appears 12s late]
  t=125s: frontend CPU spike

CORRECTED (-12.0s on auth-service):
  t=90s: db-pool saturation (20/20) [TRUE ROOT CAUSE ONSET]
  t=95s: Gateway 504 Timeouts [Direct consequence of pool lockup]
  t=125s: frontend CPU spike [Client retry cascade amplifier]
```

---

## Slide 4: Multi-Step Autonomous State Machine Architecture

### Deterministic & Agentic Pipeline
A pure Python generator pipeline streaming typed `AgentEvent` records (`state`, `tool_call`, `tool_result`, `hypothesis`, `report`):

```
+-----------------------------------------------------------------------------------------+
| Node 1: Telemetry Ingestion                                                             |
|   - estimate_clock_offsets() -> isolates +12.0s skew from cross-service span deltas     |
|   - rank_metric_onsets() -> rolling median derivative inflection detection              |
|   - query_spans(causal=True) -> causal span DAG ordering and orphan detection          |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| Node 2: Topology Builder & Failure Frontier                                             |
|   - build_dependency_dag() -> NetworkX service graph with latency & error propagation   |
|   - failure_frontier() -> pinpoints auth-service -> db-pool boundary                     |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| Node 3: Hypothesis Generator & Scoring                                                  |
|   - Generates candidate hypotheses (H1: Leak, H2: Retry Storm, H3: Slow DB, H4: Config) |
|   - Bayesian likelihood ratio updating against real SQLite telemetry                     |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| Node 4: Counterfactual Verifier                                                         |
|   - fit_sim_params() -> Poisson arrival rate, mean service time, leak probability       |
|   - run_counterfactual() -> Discrete-event M/M/c/K simulation (300 runs in 0.45s)        |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| Node 5: Report Synthesizer & Validator                                                  |
|   - validate_incident_report() -> 100% database row verification                        |
|   - Emits contract-compliant IncidentReport                                             |
+-----------------------------------------------------------------------------------------+
```

---

## Slide 5: Graph Topology & Failure Frontier Localization

### Diagnostic Signatures
- **Connection Pool Leak Signature**:
  - `db_pool.active_connections` surges to ceiling ($20/20$).
  - `db_pool.queries_in_flight` remains flat at baseline ($2.1$, $z = 2.09$, no onset).
  - Auth service logs reveal `skipped release()` on token validation error paths.
- **Why It Refutes Slow DB**:
  - If database queries were slow (H3), `queries_in_flight` would surge proportionally with active connections.
  - Because `queries_in_flight` remained flat, the database engine is healthy; connections are leaked outside the database.
- **Failure Frontier Output**:
  - Frontier localized to: `auth-service -> db-pool`.

```dot
digraph Topology {
  rankdir=LR;
  frontend -> gateway [label="calls"];
  gateway -> "auth-service" [label="upstream req", color="#f59e0b"];
  "auth-service" -> "db-pool" [label="🔥 FAILURE FRONTIER\n(Leak Signature: active=20, QIF=flat)", color="#dc2626", penwidth=3];
}
```

---

## Slide 6: Bayesian Confidence Calibration & Red Herring Rejection

### Empirical Confidence vs Hallucination
- **Hypothesis Prior & Likelihood Ratios**:
  $$\text{Odds}(H_i \mid E) = \text{Odds}(H_i) \times \prod_{j} LR(E_j \mid H_i)$$
  Normalized posterior: $P(H_i \mid E) = \frac{\text{Odds}(H_i \mid E)}{1 + \sum \text{Odds}(H_k \mid E)}$.
- **Evaluation Across 21 Scenarios**:
  - Trained on seeds 101, 102, 103 across 4 scenario classes (`pool_leak`, `slow_db`, `retry_storm_origin`, `gateway_timeout_misconfig`).
  - Evaluated on held-out test split (seeds 104, 105).
  - **Top-1 Root Cause Accuracy**: **100.00%**
  - **Multi-Class Brier Score**: **0.0038** (near-perfect calibration).
- **Rejecting the Red Herring (H2)**:
  - Metric derivative analysis proves `frontend.cpu_pct` onset ($t = 242.5\text{s}$) lagged `gateway.error_rate_5xx` onset ($t = 212.5\text{s}$) by **exactly 30.0 seconds**.
  - Formally demoted to `contributing_factor` with score $0.08$.

---

## Slide 7: Counterfactual Verification via Discrete-Event Simulation

### Proving Causality with What-If Simulation
- **Queueing Dynamics ($M/M/c/K$ with Leaks)**:
  - Arrival rate: $\lambda = 1.63\text{ req/s}$ (Poisson).
  - Mean service time: $\mu = 29.8\text{ ms}$ (Exponential).
  - Leak probability: $p_{\text{leak}} = 0.0332$.
  - Pool capacity: $c = 20$.

### The 3 Canonical Interventions
| Intervention | Target Mechanism | Intervened Failure Prob | 95% Confidence Interval | Scientific Interpretation |
| :--- | :--- | :---: | :---: | :--- |
| **`fix_leak`** | Sets $p_{\text{leak}} = 0.0$ | **0.000 (0.0%)** | `[0.000, 0.000]` | **Proves Root Cause**: Eliminating leak reduces failure to zero. |
| **`pool_size=50`** | Scales pool capacity to 50 | **0.000 (0.0%)** | `[0.000, 0.000]` | **Capacity Band-Aid**: Delays exhaustion; leak will still saturate any finite pool. |
| **`no_retries`** | Disables client retry backoff | **0.223 (22.3%)** | `[0.176, 0.270]` | **Proves Amplifier**: Outage persists even with zero retries. |

---

## Slide 8: Synthesized Post-Mortem & Verified Remediations

### IncidentReport Contract Compliance
- **Verified Facts vs Inferences**:
  - Strict contract enforcement: Every `verified_fact` must have a resolving row pointer in SQLite.
  - 100% of cited evidence pointers resolve to valid `span_id`, `log_id`, or `metric@timestamp` records.
- **Production-Ready Code Remediations**:
  1. **Guaranteed Release**: Wrap connection acquisition in `try ... finally` or Python context manager (`with pool.acquire()`) to ensure release across all exception paths.
  2. **Gateway Circuit Breaking**: Implement circuit breaker with exponential backoff and jitter on the gateway $\to$ auth-service upstream route.
  3. **High-Watermark Alerting**: Configure Prometheus/Datadog alert on `db_pool.active_connections > 80%` saturation sustained for $> 15\text{s}$.

### Summary
*Track 09 delivers a resilient, mathematically grounded, and interactive autonomous incident investigation system capable of turning noisy cloud telemetry into verified, actionable engineering post-mortems.*
