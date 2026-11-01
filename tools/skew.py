"""Clock skew estimation module (owned by P2).

Estimates per-service clock skew from cross-service parent->child spans during
healthy baseline without reading configuration constants or ground truth.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict, deque
from pathlib import Path


def _get_connection(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating whether it needs closing."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    return sqlite3.connect(str(db)), True


def estimate_clock_offsets(db: str | Path | sqlite3.Connection) -> dict[str, float]:
    """Estimate clock offset in seconds per service.

    Analyzes cross-service parent->child spans during healthy baseline (status_code == 200)
    to compute relative clock offsets, subtracting minimal network/dispatch latency.
    The discovered reference service serves as the 0.0 reference clock.

    Returns:
        dict[str, float]: Mapping from service name to estimated clock offset in seconds.
    """
    conn, should_close = _get_connection(db)
    try:
        cur = conn.cursor()

        # Check if spans table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='spans'")
        if not cur.fetchone():
            return {}

        # Query all distinct services
        cur.execute("SELECT DISTINCT service FROM spans")
        services = {row[0] for row in cur.fetchall()}
        if not services:
            cur.execute("SELECT DISTINCT service FROM metrics")
            services = {row[0] for row in cur.fetchall()}

        if not services:
            return {}

        # Identify reference service: service owning the most root spans (parent_span_id IS NULL)
        cur.execute(
            "SELECT service, COUNT(*) FROM spans WHERE parent_span_id IS NULL GROUP BY service ORDER BY COUNT(*) DESC LIMIT 1"
        )
        root_row = cur.fetchone()
        root_span_service = root_row[0] if root_row else None

        if root_span_service:
            ref_service = root_span_service
        else:
            # Fallback: most-called upstream service in cross-service spans
            cur.execute(
                "SELECT p.service, COUNT(*) FROM spans p JOIN spans c ON p.span_id = c.parent_span_id WHERE p.service != c.service GROUP BY p.service ORDER BY COUNT(*) DESC LIMIT 1"
            )
            up_row = cur.fetchone()
            if up_row and up_row[0]:
                ref_service = up_row[0]
            else:
                ref_service = sorted(services)[0]

        # Query parent-child span deltas during healthy baseline (before leak/error onset)
        cur.execute(
            """
            SELECT p.service, c.service, (c.start_ms - p.start_ms), (c.end_ms - p.end_ms)
            FROM spans p
            JOIN spans c ON p.span_id = c.parent_span_id
            WHERE p.service != c.service
              AND (p.status_code = 200 OR p.status_code = 0)
              AND (c.status_code = 200 OR c.status_code = 0)
              AND (p.start_ms < (SELECT COALESCE(MIN(start_ms), 999999999999999) FROM spans WHERE status_code >= 500))
            """
        )
        rows = cur.fetchall()

        if not rows:
            # Fallback to all successful spans if baseline query yielded no rows
            cur.execute(
                """
                SELECT p.service, c.service, (c.start_ms - p.start_ms), (c.end_ms - p.end_ms)
                FROM spans p
                JOIN spans c ON p.span_id = c.parent_span_id
                WHERE p.service != c.service
                  AND (p.status_code = 200 OR p.status_code = 0)
                  AND (c.status_code = 200 OR c.status_code = 0)
                """
            )
            rows = cur.fetchall()

        if not rows:
            return {svc: 0.0 for svc in services}

        pair_start_deltas: dict[tuple[str, str], list[int]] = defaultdict(list)
        pair_end_deltas: dict[tuple[str, str], list[int]] = defaultdict(list)
        for p_svc, c_svc, d_start, d_end in rows:
            pair_start_deltas[(p_svc, c_svc)].append(d_start)
            if d_end is not None:
                pair_end_deltas[(p_svc, c_svc)].append(d_end)

        # Graph of service offsets: edge from u to v with relative delta: skew(v) - skew(u)
        # child.start - parent.start = skew(c) - skew(p) + transit_delay
        # Under symmetric transit, transit_delay = max(0, (start_delta - end_delta) / 2)
        # skew(c) - skew(p) = start_delta - transit_delay
        graph: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (p_svc, c_svc), dts in pair_start_deltas.items():
            median_start = statistics.median(dts)
            end_dts = pair_end_deltas.get((p_svc, c_svc), [])
            if end_dts:
                median_end = statistics.median(end_dts)
                transit_est = max(0.0, (median_start - median_end) / 2.0)
                rel_skew_ms = median_start - transit_est
            else:
                transit_est = min(2.0, max(0.0, float(median_start))) if median_start >= 0 else 0.0
                rel_skew_ms = median_start - transit_est

            if abs(rel_skew_ms) < 25.0:
                rel_skew_ms = 0.0

            graph[p_svc].append((c_svc, rel_skew_ms))
            graph[c_svc].append((p_svc, -rel_skew_ms))

        # BFS across all connected components in graph
        offsets_ms: dict[str, float] = {}
        for start_svc in [ref_service] + sorted(services):
            if start_svc not in offsets_ms:
                offsets_ms[start_svc] = 0.0
                queue: deque[str] = deque([start_svc])
                visited: set[str] = {start_svc}

                while queue:
                    curr = queue.popleft()
                    curr_offset = offsets_ms[curr]
                    for neighbor, rel_ms in graph[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            offsets_ms[neighbor] = curr_offset + rel_ms
                            queue.append(neighbor)

        # Re-center offsets so that ref_service is exactly 0.0
        ref_offset = offsets_ms.get(ref_service, 0.0)
        for svc in list(offsets_ms.keys()):
            offsets_ms[svc] -= ref_offset

        # Build final offsets in seconds
        offsets_s: dict[str, float] = {}
        for svc in services:
            ms = offsets_ms.get(svc, 0.0)
            s = round(ms / 1000.0, 2)
            if abs(s) < 0.05:
                s = 0.0
            offsets_s[svc] = s

        return offsets_s

    except Exception:
        # Fallback returns empty dict
        return {}
    finally:
        if should_close:
            conn.close()


def compute_pairwise_skew(db: str | Path | sqlite3.Connection) -> dict[str, float]:
    """Compatibility alias for pairwise clock offset estimation."""
    return estimate_clock_offsets(db)
