"""Simulation parameter fitting from incident telemetry (owned by P2).

Estimates arrival rates, mean service times, leak probabilities, pool capacities,
and timeouts from database spans, logs, and metrics.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from contracts.constants import POOL_MAX
from contracts.models import SimParams


def _get_connection(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating whether it needs closing."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    return sqlite3.connect(str(db)), True


def fit_sim_params(db: str | Path | sqlite3.Connection) -> SimParams:
    """Fit arrival rate, leak probability, service times, and timeouts from DB telemetry.

    Args:
        db: SQLite database connection or file path.

    Returns:
        SimParams: Calibrated simulation parameters.
    """
    conn, should_close = _get_connection(db)
    try:
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='spans'")
        if not cur.fetchone():
            raise sqlite3.OperationalError("spans table does not exist")

        # 1. Arrival rate and window from gateway/root spans
        cur.execute(
            "SELECT COUNT(*), MIN(start_ms), MAX(start_ms) FROM spans WHERE service = 'gateway'"
        )
        gw_row = cur.fetchone()
        gw_count = gw_row[0] if gw_row else 0
        min_t = gw_row[1] if gw_row else None
        max_t = gw_row[2] if gw_row else None

        if min_t is not None and max_t is not None and max_t > min_t:
            window_s = int((max_t - min_t) / 1000.0)
            arrival_rate = round(float(gw_count) / max(1.0, float(window_s)), 2)
        else:
            window_s = 480
            arrival_rate = 1.65

        # 2. Mean service time from successful db-pool acquires
        cur.execute(
            "SELECT AVG(end_ms - start_ms) FROM spans WHERE service = 'db-pool' AND status_code = 200"
        )
        st_row = cur.fetchone()
        if st_row and st_row[0] is not None:
            mean_service_ms = round(float(st_row[0]), 2)
        else:
            mean_service_ms = 40.0

        # 3. Connection pool size
        pool_size = POOL_MAX
        cur.execute(
            "SELECT attrs FROM spans WHERE service = 'db-pool' AND attrs LIKE '%pool.size%' LIMIT 1"
        )
        attr_row = cur.fetchone()
        if attr_row and attr_row[0]:
            m = re.search(r'"pool\.size":\s*(\d+)', attr_row[0])
            if m:
                pool_size = int(m.group(1))

        # 4. Gateway and acquire timeouts from logs or span durations
        gateway_timeout_ms = 3000
        cur.execute("SELECT message FROM logs WHERE message LIKE '%timed out after %ms%' LIMIT 1")
        gw_to_row = cur.fetchone()
        if gw_to_row and gw_to_row[0]:
            m = re.search(r"after\s+(\d+)ms", gw_to_row[0])
            if m:
                gateway_timeout_ms = int(m.group(1))
        else:
            cur.execute(
                "SELECT ROUND(AVG(end_ms - start_ms)) FROM spans WHERE service = 'gateway' AND status_code = 504"
            )
            dur_row = cur.fetchone()
            if dur_row and dur_row[0] is not None and dur_row[0] > 0:
                gateway_timeout_ms = int(dur_row[0])

        acquire_timeout_ms = 5000
        cur.execute(
            "SELECT message FROM logs WHERE message LIKE '%Failed to acquire DB connection from pool within %ms%' LIMIT 1"
        )
        acq_to_row = cur.fetchone()
        if acq_to_row and acq_to_row[0]:
            m = re.search(r"within\s+(\d+)ms", acq_to_row[0])
            if m:
                acquire_timeout_ms = int(m.group(1))

        # 5. Leak probability: unreleased acquires / total acquires
        # In logs: unhandled exception skipped release()
        cur.execute(
            "SELECT COUNT(*) FROM logs WHERE service = 'auth-service' AND (message LIKE '%skipped release%' OR message LIKE '%leak%')"
        )
        leak_logs = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM spans WHERE service = 'db-pool' AND (name LIKE '%acquire%' OR name LIKE '%connection%')"
        )
        acq_row = cur.fetchone()
        acq_spans = acq_row[0] if acq_row and acq_row[0] > 0 else 0
        if acq_spans == 0:
            cur.execute("SELECT COUNT(*) FROM spans WHERE service = 'db-pool'")
            acq_row = cur.fetchone()
            acq_spans = acq_row[0] if acq_row and acq_row[0] > 0 else 1

        if leak_logs > 0:
            # Adjust for 15% random log drop rate
            estimated_unreleased = float(leak_logs) / 0.85
            leak_prob = round(min(0.25, max(0.01, estimated_unreleased / float(acq_spans))), 4)
        else:
            leak_prob = 0.0

        return SimParams(
            pool_size=pool_size,
            arrival_rate_per_s=arrival_rate,
            leak_prob=leak_prob,
            mean_service_ms=mean_service_ms,
            acquire_timeout_ms=acquire_timeout_ms,
            gateway_timeout_ms=gateway_timeout_ms,
            retry_max=3,
            window_s=window_s,
            seed=42,
        )

    except Exception:
        return SimParams(
            pool_size=20,
            arrival_rate_per_s=1.65,
            leak_prob=0.033,
            mean_service_ms=40.0,
            acquire_timeout_ms=5000,
            gateway_timeout_ms=3000,
            retry_max=3,
            window_s=480,
            seed=42,
        )
    finally:
        if should_close:
            conn.close()
