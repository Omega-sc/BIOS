"""SQLite ingestion module for telemetry JSONL records."""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def init_db(db_path: str | Path, schema_path: str | Path | None = None) -> sqlite3.Connection:
    """Initialize SQLite database with contracts/schema.sql."""
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    if db_file.exists():
        db_file.unlink()

    conn = sqlite3.connect(str(db_file))
    if schema_path is None:
        schema_path = Path(__file__).resolve().parent.parent / "contracts" / "schema.sql"

    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def load_telemetry(jsonl_path: str | Path, db_path: str | Path) -> dict[str, int]:
    """Load JSONL telemetry into SQLite database with batch inserts."""
    conn = init_db(db_path)
    cur = conn.cursor()

    spans_batch: list[tuple[Any, ...]] = []
    logs_batch: list[tuple[Any, ...]] = []
    metrics_batch: list[tuple[Any, ...]] = []

    counts = {"spans": 0, "logs": 0, "metrics": 0, "total": 0}

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rtype = rec.get("type")
            counts["total"] += 1

            if rtype == "span":
                spans_batch.append(
                    (
                        rec["span_id"],
                        rec["trace_id"],
                        rec.get("parent_span_id"),
                        rec["service"],
                        rec["name"],
                        rec["start_ms"],
                        rec["end_ms"],
                        rec.get("status_code", 0),
                        json.dumps(rec.get("attrs", {})),
                    )
                )
                counts["spans"] += 1
            elif rtype == "log":
                logs_batch.append(
                    (
                        rec["log_id"],
                        rec.get("trace_id"),
                        rec.get("span_id"),
                        rec["service"],
                        rec["ts_ms"],
                        rec["level"],
                        rec["message"],
                    )
                )
                counts["logs"] += 1
            elif rtype == "metric":
                metrics_batch.append(
                    (
                        rec["service"],
                        rec["metric"],
                        rec["ts_ms"],
                        float(rec["value"]),
                    )
                )
                counts["metrics"] += 1

    cur.executemany(
        "INSERT INTO spans (span_id, trace_id, parent_span_id, service, name, start_ms, end_ms, status_code, attrs) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        spans_batch,
    )
    cur.executemany(
        "INSERT INTO logs (log_id, trace_id, span_id, service, ts_ms, level, message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        logs_batch,
    )
    cur.executemany(
        "INSERT INTO metrics (service, metric, ts_ms, value) VALUES (?, ?, ?, ?)",
        metrics_batch,
    )

    conn.commit()
    conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description="Ingest telemetry JSONL into SQLite database")
    parser.add_argument(
        "--input", type=str, default="data/telemetry.jsonl", help="Input JSONL path"
    )
    parser.add_argument("--db", type=str, default="data/incident.db", help="Output SQLite DB path")
    args = parser.parse_args()

    counts = load_telemetry(args.input, args.db)
    print(f"Ingested {counts['total']} records into {args.db}:")
    print(f"  Spans:   {counts['spans']}")
    print(f"  Logs:    {counts['logs']}")
    print(f"  Metrics: {counts['metrics']}")


if __name__ == "__main__":
    main()
