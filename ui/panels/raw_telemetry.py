"""Raw unaligned telemetry panel and evidence row lookup viewer (owned by P4)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


def _get_connection(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def load_telemetry_records(
    db_path: str | Path,
    limit: int = 200,
    service_filter: str = "All",
    type_filter: str = "All",
    errors_only: bool = False,
    clock_offsets: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Load telemetry records from SQLite database for display in raw telemetry view."""
    conn = _get_connection(db_path)
    records: list[dict[str, Any]] = []

    if clock_offsets is None:
        try:
            from tools.skew import estimate_clock_offsets

            clock_offsets = estimate_clock_offsets(db_path)
        except Exception:
            clock_offsets = {}

    skewed_services = {s for s, off in (clock_offsets or {}).items() if abs(off) >= 0.5}

    try:
        cur = conn.cursor()

        # Check existing tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

        # Baseline timestamp for relative time calculation
        t_base = 0
        if "spans" in tables:
            cur.execute("SELECT MIN(start_ms) FROM spans")
            r = cur.fetchone()
            if r and r[0] is not None:
                t_base = r[0]

        # 1. Spans
        if "spans" in tables and type_filter in ("All", "spans"):
            query = "SELECT span_id, trace_id, service, name, start_ms, end_ms, status_code, attrs FROM spans"
            conditions = []
            params: list[Any] = []

            if service_filter != "All":
                conditions.append("service = ?")
                params.append(service_filter)
            if errors_only:
                conditions.append("status_code >= 500")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY start_ms ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            for sid, tid, svc, name, s_ms, e_ms, sc, attrs in cur.fetchall():
                dur_ms = e_ms - s_ms
                status_str = f"HTTP {sc}" if sc > 0 else "OK"
                records.append(
                    {
                        "record_id": sid,
                        "type": "span",
                        "service": svc,
                        "raw_timestamp_ms": s_ms,
                        "rel_time_s": round((s_ms - t_base) / 1000.0, 1),
                        "name_or_metric": name,
                        "status_or_level": status_str,
                        "duration_ms": dur_ms,
                        "details": f"trace:{tid[:8]}... dur:{dur_ms}ms status:{sc}",
                        "is_error": sc >= 500,
                        "is_skewed": svc in skewed_services,
                    }
                )

        # 2. Logs
        if "logs" in tables and type_filter in ("All", "logs"):
            query = "SELECT log_id, trace_id, service, ts_ms, level, message FROM logs"
            conditions = []
            params = []

            if service_filter != "All":
                conditions.append("service = ?")
                params.append(service_filter)
            if errors_only:
                conditions.append("level IN ('ERROR', 'WARN')")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY ts_ms ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            for lid, tid, svc, ts, lvl, msg in cur.fetchall():
                records.append(
                    {
                        "record_id": lid,
                        "type": "log",
                        "service": svc,
                        "raw_timestamp_ms": ts,
                        "rel_time_s": round((ts - t_base) / 1000.0, 1),
                        "name_or_metric": lvl,
                        "status_or_level": lvl,
                        "duration_ms": None,
                        "details": msg,
                        "is_error": lvl in ("ERROR", "FATAL"),
                        "is_skewed": svc in skewed_services,
                    }
                )

        # 3. Metrics
        if "metrics" in tables and type_filter in ("All", "metrics"):
            query = "SELECT service, metric, ts_ms, value FROM metrics"
            conditions = []
            params = []

            if service_filter != "All":
                conditions.append("service = ?")
                params.append(service_filter)
            if errors_only:
                conditions.append(
                    "((metric LIKE '%error%' AND value > 0.05) OR (metric LIKE '%active%' AND value >= 18))"
                )

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY ts_ms ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            for svc, m_name, ts, val in cur.fetchall():
                is_err = ("error" in m_name and val > 0.05) or ("active" in m_name and val >= 18)
                if errors_only and not is_err:
                    continue
                records.append(
                    {
                        "record_id": f"{m_name}@{ts}",
                        "type": "metric",
                        "service": svc,
                        "raw_timestamp_ms": ts,
                        "rel_time_s": round((ts - t_base) / 1000.0, 1),
                        "name_or_metric": m_name,
                        "status_or_level": f"{val:.2f}",
                        "duration_ms": None,
                        "details": f"value = {val:.4f}",
                        "is_error": is_err,
                        "is_skewed": svc in skewed_services,
                    }
                )

    finally:
        conn.close()

    if errors_only:
        records = [r for r in records if r["is_error"]]

    if not records:
        return pd.DataFrame(
            columns=[
                "record_id",
                "type",
                "service",
                "raw_timestamp_ms",
                "rel_time_s",
                "name_or_metric",
                "status_or_level",
                "duration_ms",
                "details",
                "is_error",
                "is_skewed",
            ]
        )

    # Sort stably by raw timestamp
    records.sort(key=lambda x: x["raw_timestamp_ms"])
    return pd.DataFrame(records[:limit])


def _query_evidence_from_conn(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    """Query a single sqlite3 connection for an evidence pointer."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}

    # Case A: Metric pointer format: "<metric_name>@<ts_ms>"
    if "@" in evidence_id and "metrics" in tables:
        parts = evidence_id.split("@")
        metric_name = parts[0]
        try:
            target_ts = int(parts[1])
        except ValueError:
            target_ts = 0

        # Find nearest metric point
        cur.execute(
            """
            SELECT service, metric, ts_ms, value
            FROM metrics
            WHERE metric = ?
            ORDER BY ABS(ts_ms - ?) ASC
            LIMIT 1
            """,
            (metric_name, target_ts),
        )
        row = cur.fetchone()
        if row and abs(row[2] - target_ts) <= 60000:
            return {
                "kind": "metric",
                "id": evidence_id,
                "service": row[0],
                "metric": row[1],
                "ts_ms": row[2],
                "value": row[3],
                "target_ts_ms": target_ts,
                "delta_ms": row[2] - target_ts,
                "raw_sql_table": "metrics",
            }

    # Case B: Span pointer
    if "spans" in tables:
        cur.execute(
            """
            SELECT span_id, trace_id, parent_span_id, service, name, start_ms, end_ms, status_code, attrs
            FROM spans
            WHERE span_id = ?
            """,
            (evidence_id,),
        )
        s_row = cur.fetchone()
        if s_row:
            try:
                attrs_dict = json.loads(s_row[8]) if s_row[8] else {}
            except Exception:
                attrs_dict = str(s_row[8])
            return {
                "kind": "span",
                "id": s_row[0],
                "trace_id": s_row[1],
                "parent_span_id": s_row[2],
                "service": s_row[3],
                "name": s_row[4],
                "start_ms": s_row[5],
                "end_ms": s_row[6],
                "duration_ms": s_row[6] - s_row[5],
                "status_code": s_row[7],
                "attrs": attrs_dict,
                "raw_sql_table": "spans",
            }

    # Case C: Log pointer
    if "logs" in tables:
        cur.execute(
            """
            SELECT log_id, trace_id, span_id, service, ts_ms, level, message
            FROM logs
            WHERE log_id = ?
            """,
            (evidence_id,),
        )
        l_row = cur.fetchone()
        if l_row:
            return {
                "kind": "log",
                "id": l_row[0],
                "trace_id": l_row[1],
                "span_id": l_row[2],
                "service": l_row[3],
                "ts_ms": l_row[4],
                "level": l_row[5],
                "message": l_row[6],
                "raw_sql_table": "logs",
            }

    return None


def fetch_evidence_row(db_path: str | Path, evidence_id: str) -> dict[str, Any] | None:
    """Fetch exact raw database row for an evidence pointer ID.

    Searches db_path first, with automatic fallback across fixture and scenario caches.
    """
    clean_id = (evidence_id or "").strip()
    if not clean_id:
        return None

    def _try_file(p: Path) -> dict[str, Any] | None:
        if not p.exists():
            return None
        try:
            conn = _get_connection(p)
            try:
                res = _query_evidence_from_conn(conn, clean_id)
                if res:
                    res["source_dataset"] = p.name
                    return res
            finally:
                conn.close()
        except Exception:
            pass
        return None

    primary_path = Path(db_path) if db_path else None
    if primary_path and primary_path.exists():
        row = _try_file(primary_path)
        if row:
            return row

    # Fallback to other scenario/fixture databases (e.g. tiny_telemetry.db or incident.db)
    root_dir = Path(__file__).resolve().parent.parent.parent
    cache_dir = root_dir / "data" / ".cache"
    tiny_cache = cache_dir / "tiny_telemetry.db"
    tiny_fixture = root_dir / "fixtures" / "tiny_telemetry.jsonl"

    if not tiny_cache.exists() and tiny_fixture.exists():
        try:
            from ingest.load_jsonl import load_telemetry

            cache_dir.mkdir(parents=True, exist_ok=True)
            load_telemetry(tiny_fixture, tiny_cache)
        except Exception:
            pass

    fallback_candidates: list[Path] = []
    if tiny_cache.exists():
        fallback_candidates.append(tiny_cache)

    incident_path = root_dir / "data" / "incident.db"
    if incident_path.exists() and incident_path != primary_path:
        fallback_candidates.append(incident_path)

    if cache_dir.exists():
        for f in sorted(cache_dir.glob("*.db")):
            if f not in fallback_candidates and f != primary_path:
                fallback_candidates.append(f)

    for cand in fallback_candidates:
        row = _try_file(cand)
        if row:
            return row

    return None


def style_telemetry_dataframe(
    df: pd.DataFrame,
    skewed_services: set[str] | None = None,
) -> Any:
    """Apply styling highlighting errors and skewed records."""
    if df.empty:
        return df

    def _row_style(row: pd.Series) -> list[str]:
        status_val = str(row.get("Status / Val", "")).upper()
        svc = str(row.get("Service", ""))
        is_err = (
            "500" in status_val
            or "504" in status_val
            or "ERROR" in status_val
            or "FATAL" in status_val
        )
        is_warn = "WARN" in status_val
        is_skewed = svc in (skewed_services or set())

        if is_err:
            return ["background-color: #D8F3DC; color: #1B4332; font-weight: 600;"] * len(row)
        if is_warn:
            return ["background-color: #EBF3EE; color: #2D6A4F;"] * len(row)
        if is_skewed:
            return ["background-color: #F4F3EE; color: #1B4332;"] * len(row)
        return [""] * len(row)

    return df.style.apply(_row_style, axis=1)


def render_raw_telemetry_panel(db_path: str | Path) -> None:
    """Render raw telemetry dataframe and direct evidence row lookup."""
    st.subheader("Raw Unaligned Telemetry")

    st.markdown(
        """
        <div style="background-color: #EBF3EE; border-left: 4px solid #2D6A4F; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #1B4332;">
            <b>Unaligned Wall-Clock Timestamps</b>: Services may emit records with uncorrected clock skew relative
            to upstream reference services. In uncorrected coordinates, downstream and upstream symptoms may appear
            out of order, presenting a false causal paradox. Telemetry also reflects dropped logs and span loss.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not Path(db_path).exists():
        st.warning(f"Database file not found: `{db_path}`")
        return

    # Extract distinct services for dynamic filtering
    service_options = ["All"]
    try:
        conn = _get_connection(db_path)
        cur = conn.cursor()
        svcs = set()
        for tbl in ["spans", "logs", "metrics"]:
            cur.execute(f"SELECT DISTINCT service FROM {tbl}")
            for r in cur.fetchall():
                if r[0]:
                    svcs.add(str(r[0]))
        conn.close()
        service_options.extend(sorted(svcs))
    except Exception:
        pass

    # Filter controls in columns
    fcol1, fcol2, fcol3 = st.columns([1, 1, 1])
    with fcol1:
        type_filter = st.selectbox(
            "Record Type",
            ["All", "spans", "logs", "metrics"],
            key="raw_type_filter",
        )
    with fcol2:
        service_filter = st.selectbox(
            "Service",
            service_options,
            key="raw_svc_filter",
        )
    with fcol3:
        errors_only = st.checkbox("Errors only (5xx / ERROR)", value=False, key="raw_err_filter")

    df = load_telemetry_records(
        db_path,
        limit=150,
        service_filter=service_filter,
        type_filter=type_filter,
        errors_only=errors_only,
    )

    if not df.empty:
        display_df = df[
            [
                "rel_time_s",
                "service",
                "type",
                "name_or_metric",
                "status_or_level",
                "details",
                "record_id",
            ]
        ].copy()
        display_df = display_df.rename(
            columns={
                "rel_time_s": "Rel Time (s)",
                "service": "Service",
                "type": "Type",
                "name_or_metric": "Name / Metric",
                "status_or_level": "Status / Val",
                "details": "Details",
                "record_id": "Evidence ID",
            }
        )

        skewed_svcs = set(df[df["is_skewed"]]["service"].unique()) if "is_skewed" in df else set()
        styled_df = style_telemetry_dataframe(display_df, skewed_services=skewed_svcs)
        st.dataframe(
            styled_df,
            use_container_width=True,
            height=340,
            hide_index=True,
        )
        st.caption(
            f"Showing {len(df)} sample raw records from `{Path(db_path).name}` (wall-clock time, highlighted by status and service skew)."
        )
    else:
        st.info("No records matched the selected filters.")

    st.divider()

    # Evidence Row Direct SQLite Lookup
    st.markdown("#### Evidence Row Direct SQLite Lookup")
    st.caption("Verify any cited evidence pointer directly against raw SQLite records.")

    # Dynamically extract sample IDs from database
    sample_ids = [""]
    try:
        conn = _get_connection(db_path)
        cur = conn.cursor()
        cur.execute("SELECT span_id FROM spans LIMIT 2")
        sample_ids.extend([r[0] for r in cur.fetchall() if r[0]])
        cur.execute("SELECT log_id FROM logs LIMIT 2")
        sample_ids.extend([r[0] for r in cur.fetchall() if r[0]])
        cur.execute("SELECT metric, ts_ms FROM metrics LIMIT 2")
        sample_ids.extend([f"{r[0]}@{r[1]}" for r in cur.fetchall() if r[0]])
        conn.close()
    except Exception:
        pass

    def _on_sample_select() -> None:
        val = st.session_state.get("quick_evidence_sample", "")
        if val:
            st.session_state["selected_evidence_id"] = val
            st.session_state["evidence_version"] = st.session_state.get("evidence_version", 0) + 1

    col_input, col_sample = st.columns([1.5, 1])
    with col_sample:
        st.selectbox(
            "Quick Select Sample Evidence",
            sample_ids,
            key="quick_evidence_sample",
            on_change=_on_sample_select,
        )

    current_val = st.session_state.get("selected_evidence_id", "")
    current_ver = st.session_state.get("evidence_version", 0)

    with col_input:
        evidence_id_input = st.text_input(
            "Evidence ID (span_id, log_id, or metric@timestamp)",
            value=current_val,
            placeholder="e.g. span_id, log_id, or metric@ts",
            key=f"evidence_input_{current_ver}",
        )

    lookup_id = evidence_id_input.strip()
    if lookup_id:
        result = fetch_evidence_row(db_path, lookup_id)
        if result:
            src_desc = result.get("source_dataset", Path(db_path).name)
            st.success(f"Found in SQLite table `{result.get('raw_sql_table')}` ({src_desc})")
            st.json(result)
        else:
            st.warning(f"No database row found matching Evidence ID `{lookup_id}`.")
