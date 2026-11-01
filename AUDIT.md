# Audit: hardcoding and blind-robustness (BIOS repo)

Method: grep for scenario literals in `agent/ tools/ sim/ ui/ ingest/`, ran `pytest` (62 pass), then ran
`blind_check.py` (mutates the telemetry: renamed services/metrics, no skew, different dataset under the
replay path, healthy-only control).

## What works
- Skew estimation is data-driven: recovered 11.99 s on the original data and reports ~0 when no skew exists.
- Evidence validation works: it caught a fabricated pointer instead of letting it through.
- In-distribution scenarios: 8/8 correct on the scenario files I re-ran (see caveat below).

## Blind check results: 1 of 4 pass
| Check | Result | Cause |
|---|---|---|
| Renamed services/metrics | **Crash** | Hardcoded metric names; fallback evidence pointer `db_pool.active_connections@1700000000000` doesn't exist, so validation aborts the run |
| No skew present | Pass | |
| Replay mode, different data | **Fail** | `runner._get_replay_file_for_db` picks a canned file by DB *filename*, so any `incident.db` streams the recorded auth-service report |
| Healthy-only control | **Fail** | H1 "root_cause" at 0.83. Confidence is normalized over 4 fixed hypotheses and there is no abstain path |

## Hardcoding inventory (file: what)
- `agent/scoring.py`: all metric names (L108-112, 261-265), `METRIC_SOURCES`, `POOL_MAX` (L273), service-specific SQL for auth-service/frontend/db-pool/gateway (L171-248), and the four hypothesis statements as fixed strings naming `auth-service` (L402+). The hypothesis set equals the generator's four scenario types.
- `agent/nodes/report_synthesizer.py`: service and metric literals throughout (L32-250); a fabricated baseline timeline event with a fixed metric pointer (L240-255).
- `agent/nodes/telemetry_ingestion.py` L78-84, `agent/llm.py` L235: fixed metric name in tool calls.
- `agent/validate.py` L8, L76; `tools/metrics.py` L15, 53, 64, 156, 184: `METRIC_SOURCES` and a `"db-pool"` default; metric list comes from the constant, not from the data.
- `tools/skew.py` L39, 49, 162: fallback returns fixed service names. L58-61: reference clock chosen by name (`gateway`, then `db-pool`) before the root-span heuristic.
- `tools/dag.py` L80-95, 125-179, 204-284: `gateway`/`frontend` special-cased; a fabricated fallback DAG (`call_count=100`, `mean_latency=50.0`); `failure_frontier` returns the literal `"auth-service -> db-pool"` on several paths, including as the last-resort default.
- `tools/spans.py` L139: gateway as root by name.
- `sim/fit.py` L13, 43, 59, 68: pool size taken from `POOL_MAX` instead of estimated; service names in SQL.
- `sim/pool_sim.py` L147: assumption text names auth-service.
- `runner.py`: replay chosen by filename (above).

## Caveats on the headline eval numbers
- `eval/report.md` shows 21/21 accuracy and Brier 0.0038. The scenarios come from the same generator (`datagen/scenarios.py`) the scorer was written for, and the weights are fitted on those same runs, so this is in-sample.
- Confidence is a softmax over four fixed hypotheses, so it will always name one. It is not calibrated for unseen failure types.

## Fix order
1. Delete fabrication: fallback evidence pointer, fallback DAG, filename-keyed replay (key replay on a hash of the DB content, or fall back to scripted when it doesn't match).
2. Add an abstain path: if no hypothesis's evidence passes a minimum score, verdict `unresolved`.
3. Discover instead of declare: services, metric owners, reference clock, pool capacity (from data), resource lifecycles (matched acquire/release by id).
4. Express hypotheses as signatures bound to discovered entities; generate statements from the bound names.
5. Make `blind_check.py` part of `make check`; add scenario types the scorer has not been tuned on.
