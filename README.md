# Track 09: Autonomous Incident Investigation

A multi-step investigation agent that reconstructs an incident timeline from fragmented telemetry (logs, metrics, traces) across 3 microservices with simulated clock skew and lossy logs, finds the root cause, rejects a red herring, and emits a structured post-mortem with evidence pointers and a counterfactual simulation.

## Scenario
A public food-rationing/subsidy portal crashes.
- **Services:** `gateway`, `auth-service`, `db-pool` (plus a `frontend` client source emitting CPU metric and retry logs).
- **Ground-Truth Root Cause:** `auth-service` leaks DB connections (misconfigured pool where error paths skip `release()`), exhausting the DB pool, causing slow/failed auth, resulting in gateway 504 errors.
- **Red Herring:** `frontend.cpu_pct` spikes ~30s *after* 504 onset because clients retry aggressively. It is an amplifier, not the root cause.
- **Telemetry Impairments:**
  - `+12 s` clock skew on `auth-service` timestamps (spans, logs, metrics).
  - `15%` random log loss.
  - Optional `~3%` orphan spans.

## System Architecture

The pipeline is implemented as a deterministic Python state machine:
```
telemetry_ingestion -> topology_builder -> hypothesis_generator -> counterfactual_verifier -> report_synthesizer
```

- `contracts/`: Data schemas, Pydantic v2 models, constants, and SQLite tables.
- `datagen/`: Synthetic multi-tier telemetry and scenario generation.
- `ingest/`: SQLite database loading and schema management.
- `tools/`: Dynamic clock skew estimation, span query API, metric onset detection, and dependency DAG builder.
- `sim/`: Discrete event connection pool simulation and counterfactual verification.
- `agent/`: State machine investigation runner, evidence scoring, and report synthesizer.
- `eval/`: Automated multi-scenario benchmarking, top-1 accuracy, and probability calibration.
- `ui/`: Interactive Streamlit dashboard with real-time tool execution, timeline view, and counterfactual simulation.

## Quick Start

### Installation
```bash
uv venv --python 3.11 .venv
# On Windows: .venv\Scripts\activate
# On Unix: source .venv/bin/activate
uv pip install -r requirements.txt
```

### Running Tests and Checks
```bash
make check
make smoke
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
