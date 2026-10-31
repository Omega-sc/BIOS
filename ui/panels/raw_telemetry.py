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
) -> pd.DataFrame:
    """Load telemetry records from SQLite database for display in raw telemetry view."""
    conn = _get_connection(db_path)
    records: list[dict[str, Any]] = []

    try:
        cur = conn.cursor()

        # Check existing tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

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
                        "rel_time_s": round((s_ms - 1700000000000) / 1000.0, 1),
                        "name_or_metric": name,
                        "status_or_level": status_str,
                        "duration_ms": dur_ms,
                        "details": f"trace:{tid[:8]}... dur:{dur_ms}ms status:{sc}",
                        "is_error": sc >= 500,
                        "is_skewed": svc == "auth-service",
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
                        "rel_time_s": round((ts - 1700000000000) / 1000.0, 1),
                        "name_or_metric": lvl,
                        "status_or_level": lvl,
                        "duration_ms": None,
                        "details": msg,
                        "is_error": lvl in ("ERROR", "FATAL"),
                        "is_skewed": svc == "auth-service",
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
                        "rel_time_s": round((ts - 1700000000000) / 1000.0, 1),
                        "name_or_metric": m_name,
                        "status_or_level": f"{val:.2f}",
                        "duration_ms": None,
                        "details": f"value = {val:.4f}",
                        "is_error": is_err,
                        "is_skewed": svc == "auth-service",
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

    df = pd.DataFrame(records)
    df = df.sort_values(by="raw_timestamp_ms").reset_index(drop=True)
    return df


def fetch_evidence_row(db_path: str | Path, evidence_id: str) -> dict[str, Any] | None:
    """Fetch exact raw database row for an evidence pointer ID."""
    if not evidence_id or not Path(db_path).exists():
        return None

    conn = _get_connection(db_path)
    cur = conn.cursor()

    try:
        # Case A: Metric pointer format: "<metric_name>@<ts_ms>"
        if "@" in evidence_id:
            parts = evidence_id.split("@")
            metric_name = parts[0]
            try:
                target_ts = int(parts[1])
            except ValueError:
                target_ts = 0

            # Find nearest metric point within +/- 30s
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
            if row:
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

    finally:
        conn.close()

    return None


def style_telemetry_dataframe(df: pd.DataFrame):
    """Style raw telemetry records colored by level/status and unaligned clock skew."""

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
        is_skewed = svc == "auth-service"

        if is_err:
            return ["background-color: #fee2e2; color: #991b1b; font-weight: 600;"] * len(row)
        if is_warn:
            return ["background-color: #fef3c7; color: #92400e;"] * len(row)
        if is_skewed:
            return ["background-color: #fff7ed; color: #c2410c;"] * len(row)
        return [""] * len(row)

    return df.style.apply(_row_style, axis=1)


def render_raw_telemetry_panel(db_path: str | Path) -> None:
    """Render raw telemetry dataframe and direct evidence row lookup."""
    st.subheader("📡 Raw Unaligned Telemetry")

    st.markdown(
        """
        <div style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 10px 14px; border-radius: 4px; margin-bottom: 12px; font-size: 13.5px; color: #92400e;">
            ⚠️ <b>Unaligned Wall-Clock Timestamps</b>: Notice <code>auth-service</code> records arrive with a
            <b>+12.0s clock skew</b> relative to <code>gateway</code> and <code>db-pool</code>. In uncorrected coordinates,
            gateway 504 errors appear <i>before</i> auth pool exhaustion, presenting a false causal paradox.
            Additionally, telemetry reflects ~15% random log drop.
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not Path(db_path).exists():
        st.warning(f"Database file not found: `{db_path}`")
        return

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
            ["All", "gateway", "auth-service", "db-pool", "frontend"],
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

        styled_df = style_telemetry_dataframe(display_df)
        st.dataframe(
            styled_df,
            use_container_width=True,
            height=320,
            hide_index=True,
        )
        st.caption(
            f"Showing {len(df)} sample raw records from `{Path(db_path).name}` (wall-clock time, colored by status/skew)."
        )
    else:
        st.info("No records matched the selected filters.")

    st.divider()

    # Evidence Row Direct SQLite Lookup
    st.markdown("#### 🔍 Evidence Row Direct SQLite Lookup")
    st.caption("Verify any cited evidence pointer directly against raw SQLite records.")

    sample_ids = [
        "",
        "db_pool.active_connections@1700000090000",
        "span-gw-3",
        "log-auth-5",
        "frontend.cpu_pct@1700000125000",
        "log-auth-3",
        "gateway.error_rate_5xx@1700000095000",
    ]

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
            placeholder="e.g. span-gw-3 or log-auth-5",
            key=f"evidence_input_{current_ver}",
        )

    lookup_id = evidence_id_input.strip()
    if lookup_id:
        result = fetch_evidence_row(db_path, lookup_id)
        if result:
            st.success(f"✅ Found in SQLite table `{result.get('raw_sql_table')}`")
            st.json(result)
        else:
            st.warning(f"No database row found matching Evidence ID `{lookup_id}`.")
