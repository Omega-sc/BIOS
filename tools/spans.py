"""Trace spans query and causality checking module (owned by P2).

Handles span queries with clock skew correction, orphan span detection,
causal DFS ordering within traces, and root ordering across traces.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from contracts.models import Span
from tools.skew import estimate_clock_offsets


def _get_connection(db: str | Path | sqlite3.Connection) -> tuple[sqlite3.Connection, bool]:
    """Return an active connection and a boolean indicating whether it needs closing."""
    if isinstance(db, sqlite3.Connection):
        return db, False
    return sqlite3.connect(str(db)), True


def query_spans(
    db: str | Path | sqlite3.Connection,
    service: str | None = None,
    status_code: int | None = None,
    time_window: tuple[int, int] | None = None,
    trace_id: str | None = None,
    causal: bool = True,
    limit: int | None = None,
) -> list[Span]:
    """Query spans from the database with filtering, clock correction, and causal ordering.

    Args:
        db: SQLite database connection or path.
        service: Optional filter by service name.
        status_code: Optional filter by HTTP status code.
        time_window: Optional (start_ms, end_ms) window interpreted in corrected time.
        trace_id: Optional filter for a specific trace ID.
        causal: If True, corrects clock skew and orders causally (DFS over parent_span_id
                tree within trace, ordered across traces by root span). If False,
                orders by raw start_ms ASC.
        limit: Optional maximum number of spans to return.

    Returns:
        list[Span]: List of Span models with t_corrected_start_ms and orphan flags.
    """
    conn, should_close = _get_connection(db)
    try:
        # 1. Compute estimated clock offsets in milliseconds
        offsets_s = estimate_clock_offsets(conn)
        offsets_ms = {svc: int(s * 1000) for svc, s in offsets_s.items()}

        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='spans'")
        if not cur.fetchone():
            return []

        # Query all spans joining parent to detect orphans
        # A span is an orphan if parent_span_id is not NULL but no span exists with span_id = parent_span_id
        sql = """
            SELECT c.span_id, c.trace_id, c.parent_span_id, c.service, c.name,
                   c.start_ms, c.end_ms, c.status_code, c.attrs,
                   (c.parent_span_id IS NOT NULL AND p.span_id IS NULL) AS is_orphan
            FROM spans c
            LEFT JOIN spans p ON c.parent_span_id = p.span_id
        """
        params: list[Any] = []
        if trace_id:
            sql += " WHERE c.trace_id = ?"
            params.append(trace_id)

        cur.execute(sql, params)
        rows = cur.fetchall()

        if not rows:
            return []

        # Convert rows to Span objects
        all_spans: list[Span] = []
        for row in rows:
            span_id = row[0]
            t_id = row[1]
            parent_id = row[2]
            svc = row[3]
            name = row[4]
            start_ms = row[5]
            end_ms = row[6]
            sc = row[7]
            attrs = json.loads(row[8]) if row[8] else {}
            is_orphan = bool(row[9])

            corr_ms = offsets_ms.get(svc, 0) if causal else 0
            t_corrected = start_ms - corr_ms if causal else start_ms

            all_spans.append(
                Span(
                    span_id=span_id,
                    trace_id=t_id,
                    parent_span_id=parent_id,
                    service=svc,
                    name=name,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    status_code=sc,
                    attrs=attrs,
                    t_corrected_start_ms=t_corrected,
                    orphan=is_orphan,
                )
            )

        if not causal:
            # Simple sorting by raw start_ms ASC
            filtered: list[Span] = []
            for s in all_spans:
                if service is not None and s.service != service:
                    continue
                if status_code is not None and s.status_code != status_code:
                    continue
                if time_window is not None:
                    if not (time_window[0] <= s.start_ms <= time_window[1]):
                        continue
                filtered.append(s)
            filtered.sort(key=lambda s: s.start_ms)
            return filtered[:limit] if limit is not None else filtered

        # Causal Ordering:
        # 1. Group spans by trace_id
        traces: dict[str, list[Span]] = defaultdict(list)
        for s in all_spans:
            traces[s.trace_id].append(s)

        # 2. Determine trace ordering across traces by root span corrected start time
        def get_trace_order_key(t_spans: list[Span]) -> int:
            root_spans = [s for s in t_spans if s.parent_span_id is None]
            spans_to_check = root_spans if root_spans else t_spans
            return min(
                s.t_corrected_start_ms if s.t_corrected_start_ms is not None else s.start_ms
                for s in spans_to_check
            )

        sorted_trace_ids = sorted(traces.keys(), key=lambda tid: get_trace_order_key(traces[tid]))

        # 3. Within each trace, DFS over parent_span_id tree
        ordered_spans: list[Span] = []

        for tid in sorted_trace_ids:
            t_spans = traces[tid]
            span_map = {s.span_id: s for s in t_spans}
            children: dict[str, list[Span]] = defaultdict(list)
            roots: list[Span] = []

            for s in t_spans:
                if s.parent_span_id and s.parent_span_id in span_map:
                    children[s.parent_span_id].append(s)
                else:
                    # Root or orphan span without parent in this trace
                    roots.append(s)

            # Sort roots and children by corrected start time
            roots.sort(
                key=lambda s: (
                    s.t_corrected_start_ms if s.t_corrected_start_ms is not None else s.start_ms
                )
            )
            visited_ids: set[str] = set()

            def dfs(span: Span) -> None:
                if span.span_id in visited_ids:
                    return
                visited_ids.add(span.span_id)
                ordered_spans.append(span)
                for child in sorted(
                    children.get(span.span_id, []),
                    key=lambda c: (
                        c.t_corrected_start_ms if c.t_corrected_start_ms is not None else c.start_ms
                    ),
                ):
                    dfs(child)

            for root in roots:
                dfs(root)

            # Visit any disconnected spans not reached
            for s in sorted(
                t_spans,
                key=lambda s: (
                    s.t_corrected_start_ms if s.t_corrected_start_ms is not None else s.start_ms
                ),
            ):
                if s.span_id not in visited_ids:
                    dfs(s)

        # 4. Filter by service, status_code, time_window (interpreted in corrected time)
        final_result: list[Span] = []
        for s in ordered_spans:
            if service is not None and s.service != service:
                continue
            if status_code is not None and s.status_code != status_code:
                continue
            if time_window is not None:
                t_corr = (
                    s.t_corrected_start_ms if s.t_corrected_start_ms is not None else s.start_ms
                )
                if not (time_window[0] <= t_corr <= time_window[1]):
                    continue
            final_result.append(s)
            if limit is not None and len(final_result) >= limit:
                break

        return final_result

    except Exception:
        return []
    finally:
        if should_close:
            conn.close()


def fetch_causal_spans(db: str | Path | sqlite3.Connection, trace_id: str) -> list[Span]:
    """Fetch spans for trace_id with validated parent-child ordering."""
    return query_spans(db, trace_id=trace_id, causal=True)
