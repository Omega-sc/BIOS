# Autonomous Incident Investigation Evaluation Report

## Summary
- **Scenarios Evaluated:** 21
- **Top-1 Root Cause Accuracy:** 100.00% (21/21)
- **Brier Score (Multi-class Mean):** 0.1218

## Reliability Calibration Table
The reliability table compares model-assigned evidence confidence against observed empirical diagnostic accuracy across prediction bins.

| Confidence Bin | Predictions Count | Mean Predicted Confidence | Empirical Accuracy | Calibration Gap |
| :--- | :--- | :--- | :--- | :--- |
| `[0.0, 0.2]` | 0 | 0.100 | 0.000 | 0.000 |
| `[0.2, 0.4]` | 10 | 0.380 | 1.000 | 0.620 |
| `[0.4, 0.6]` | 11 | 0.427 | 1.000 | 0.573 |
| `[0.6, 0.8]` | 0 | 0.700 | 0.000 | 0.000 |
| `[0.8, 1.0]` | 0 | 0.900 | 0.000 | 0.000 |

## Detailed Scenario Results

| Scenario | Seed | Predicted Root Cause | True Root Cause | Confidence | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `primary_telemetry` | `42` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `pool_leak` | `101` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `pool_leak` | `102` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `pool_leak` | `103` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `pool_leak` | `104` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `pool_leak` | `105` | `auth-service` | `auth-service` | `0.41` | **PASS** |
| `slow_db` | `101` | `db-pool` | `db-pool` | `0.37` | **PASS** |
| `slow_db` | `102` | `db-pool` | `db-pool` | `0.37` | **PASS** |
| `slow_db` | `103` | `db-pool` | `db-pool` | `0.37` | **PASS** |
| `slow_db` | `104` | `db-pool` | `db-pool` | `0.37` | **PASS** |
| `slow_db` | `105` | `db-pool` | `db-pool` | `0.37` | **PASS** |
| `retry_storm_origin` | `101` | `frontend` | `frontend` | `0.44` | **PASS** |
| `retry_storm_origin` | `102` | `frontend` | `frontend` | `0.44` | **PASS** |
| `retry_storm_origin` | `103` | `frontend` | `frontend` | `0.44` | **PASS** |
| `retry_storm_origin` | `104` | `frontend` | `frontend` | `0.44` | **PASS** |
| `retry_storm_origin` | `105` | `frontend` | `frontend` | `0.44` | **PASS** |
| `gateway_timeout_misconfig` | `101` | `gateway` | `gateway` | `0.39` | **PASS** |
| `gateway_timeout_misconfig` | `102` | `gateway` | `gateway` | `0.39` | **PASS** |
| `gateway_timeout_misconfig` | `103` | `gateway` | `gateway` | `0.39` | **PASS** |
| `gateway_timeout_misconfig` | `104` | `gateway` | `gateway` | `0.39` | **PASS** |
| `gateway_timeout_misconfig` | `105` | `gateway` | `gateway` | `0.39` | **PASS** |
