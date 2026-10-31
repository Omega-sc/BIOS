"""Record and freeze replay event streams and LLM responses for Track 09 (owned by P3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.runner import run_investigation  # noqa: E402
from ingest.load_jsonl import load_telemetry  # noqa: E402

REPLAY_DIR = ROOT_DIR / "fixtures" / "replay"
INCIDENT_DB = ROOT_DIR / "data" / "incident.db"
TINY_JSONL = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"


def record_all_replays() -> None:
    """Record replay event streams for primary and tiny telemetry."""
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Primary incident telemetry replay
    print(f"Recording primary incident replay from {INCIDENT_DB}...")
    events_inc = [e.model_dump() for e in run_investigation(str(INCIDENT_DB), mode="scripted")]
    inc_file = REPLAY_DIR / "incident_db_replay.json"
    with open(inc_file, "w", encoding="utf-8") as f:
        json.dump(events_inc, f, indent=2)
    print(f"Saved {len(events_inc)} events to {inc_file}")

    # 2. Tiny telemetry replay
    print(f"Recording tiny telemetry replay from {TINY_JSONL}...")
    tmp_db = ROOT_DIR / "data" / "tiny_replay_tmp.db"
    try:
        load_telemetry(TINY_JSONL, tmp_db)
        events_tiny = [e.model_dump() for e in run_investigation(str(tmp_db), mode="scripted")]
        tiny_file = REPLAY_DIR / "tiny_telemetry_replay.json"
        with open(tiny_file, "w", encoding="utf-8") as f:
            json.dump(events_tiny, f, indent=2)
        print(f"Saved {len(events_tiny)} events to {tiny_file}")
    finally:
        if tmp_db.exists():
            tmp_db.unlink()

    # 3. Pre-populate LLM cache for live mode offline execution
    print("Recording LLM live mode cache entries...")
    live_events = list(run_investigation(str(INCIDENT_DB), mode="live"))
    print(f"Generated {len(live_events)} live investigation events and cached LLM responses.")


if __name__ == "__main__":
    record_all_replays()
