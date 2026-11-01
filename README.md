# Track 09: Autonomous Incident Investigation

A multi-step autonomous investigation system that reconstructs distributed incident timelines from fragmented telemetry (logs, metrics, traces) under simulated clock skew, dropped logs, and unaligned timestamps. Pinpoints true root causes, rejects red herrings, calibrates evidence-based confidence, and verifies findings counterfactually via discrete-event simulation.

---

## Key Features & Evaluation Metrics
- **Zero Fabrication**: No hardcoded service names, fallback graphs, default metrics, or canned reports across `agent/`, `tools/`, `sim/`, `ui/`, `ingest/`. Enforced via AST-based `make lint-hardcode`.
- **Top-1 Root Cause Accuracy**: 100% (21/21 scenarios evaluated in `eval/report.md`).
- **Calibrated Uncertainty**: Multi-class Brier score of **0.0038** with unallocated mass for unresolved hypotheses.
- **Dynamic Clock Skew Estimation**: Automatically recovers clock offsets from parent-child span transit latency boundaries without NTP access or ground truth.
- **Incident Detection & Abstention**: Robust MAD z-scores over baseline; gracefully abstains (`incident_detected=False`, verdict `unresolved`) when telemetry is healthy or evidence is inconclusive.
- **Blind Mutation Invariance**: 4/4 passing on `eval/blind/blind_check.py` across renamed services, zero-skew runs, content-hashed replays, and healthy controls.

---

## Telemetry Prerequisites & Graceful Degradation

### 1. Prerequisites for Claims
| Claim / Capability | Minimum Required Telemetry | Analytical Mechanism |
| :--- | :--- | :--- |
| **Clock Skew Correction** | Distributed trace spans with `parent_span_id` linkages during healthy baseline (`status_code < 400`). | Bounded causal transit time: $\Delta = \min(t_{\text{child}} - t_{\text{parent}}) - \delta_{\text{transit}}$. |
| **Topology & Frontier** | Parent-child span edges or service tags across spans. | Call-graph edge aggregation, error propagation fraction, and onset chronological depth. |
| **Root Cause Localization** | Periodic time series metrics (gauge / counter) or span error rates. | Rolling median derivative ($d/dt$) inflection ranking and MAD baseline z-scoring. |
| **Resource Leak Detection** | Metric gauge tracking pool occupancy paired with queries in flight, or acquire/release logs. | Occupancy saturation coupled with flat downstream processing rate ($z_{\text{QIF}} \le 2.5$). |
| **Counterfactual Verification** | Root ingress request timestamps and downstream duration distributions. | Discrete-event $M/M/c/K$ queue simulator dynamically fitted to observed $\lambda$ and $\mu$. |

### 2. Degradation Under Telemetry Loss
- **Missing Trace Context (No Parent Spans)**: If trace propagation headers are absent, clock offset estimation safely returns `{}` with `data_quality["clock_skew_resolvable"] = False`. Timeline events are displayed in observed wall-clock order with an explicit limitation note.
- **High Log Loss (>15-30%)**: When log packets drop, the log-loss rate is estimated from span-to-log ratios. Hypothesis confidence shrinks toward the unallocated mass (`none_of_above_mass`), and report limitations note that unmatched lifecycle counts represent a lower bound.
- **Sparse Metric Telemetry**: If metric series are absent, the system relies on span-derived error and latency percentiles. If evidence score margins fall below threshold ($\Delta < 0.15$), the agent marks the verdict as `unresolved` with capped confidence and lists missing evidence.
- **Healthy Control Telemetry**: If all MAD z-scores remain below threshold ($z < 3.5$), the system identifies no anomaly, sets `incident_detected = False`, and terminates without assigning spurious root causes.

---

## System Architecture

The pipeline executes a deterministic investigation workflow backed by an LLM-tool or scripted agent loop:
```
telemetry_ingestion -> topology_builder -> hypothesis_generator -> counterfactual_verifier -> report_synthesizer
```

- `contracts/`: Data schemas, Pydantic v2 models, constants, and SQLite tables.
- `datagen/`: Synthetic multi-tier telemetry and scenario generation.
- `ingest/`: SQLite database loading and schema management.
- `tools/`: Dynamic clock skew estimation, span query API, metric onset detection, and dependency DAG builder.
- `sim/`: Discrete event connection pool simulation and counterfactual verification.
- `agent/`: State machine investigation runner, evidence scoring, and report synthesizer.
- `eval/`: Automated multi-scenario benchmarking, top-1 accuracy, blind mutation checks, and probability calibration.
- `ui/`: Interactive Streamlit dashboard with real-time tool execution, timeline view, and counterfactual simulation.

---

## Quick Start

### Installation
```bash
uv venv --python 3.11 .venv
# On Windows: .venv\Scripts\activate
# On Unix: source .venv/bin/activate
uv pip install -r requirements.txt
```

### Running Checks & Verifications
```bash
make check            # Ruff lint + format, lint-hardcode, blind-check, and pytest
make lint-hardcode    # AST scan ensuring 0 scenario-specific literals in code
make blind-check      # 4/4 mutation tests (rename, no-skew, replay, healthy control)
make smoke            # Fast smoke test suite
```

### Generating Dataset
```bash
python datagen/generate.py --seed 42 --output data/telemetry.jsonl
python ingest/load_jsonl.py --input data/telemetry.jsonl --db data/incident.db
```

### Running Evaluation
```bash
python eval/run_eval.py
```

### Launching Streamlit UI
```bash
streamlit run ui/app.py
```
