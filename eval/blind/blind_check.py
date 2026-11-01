"""Blind-robustness check for the incident-investigation agent.

Run from the repo root:  python eval/blind/blind_check.py [telemetry.jsonl]
Each check mutates the telemetry so that anything hardcoded to the original
scenario breaks. Names are mapped programmatically; nothing here assumes them.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agent.runner import run_investigation  # noqa: E402
from ingest.load_jsonl import load_telemetry  # noqa: E402

default_src = ROOT_DIR / "data" / "telemetry.jsonl"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else default_src
GT = SRC.parent / "ground_truth.json"
records = [
    json.loads(line) for line in SRC.read_text(encoding="utf-8").splitlines() if line.strip()
]
tmp = Path(tempfile.mkdtemp())


def opaque(p: str, s: str) -> str:
    return f"{p}-{hashlib.md5(s.encode()).hexdigest()[:5]}"


orig_services = sorted({r["service"] for r in records if "service" in r})
SVC = {s: opaque("svc", s) for s in orig_services}
MET = {m: opaque("m", m) for m in {r["metric"] for r in records if r["type"] == "metric"}}


def rename(rs):
    out = []
    for r in map(dict, rs):
        r["service"] = SVC.get(r.get("service"), r.get("service"))
        if "metric" in r:
            r["metric"] = MET[r["metric"]]
        if "message" in r:
            for a, b in SVC.items():
                r["message"] = r["message"].replace(a, b)
        out.append(r)
    return out


def shift(rs, svc, ms):
    for r in rs:
        if r.get("service") == svc:
            for k in ("start_ms", "end_ms", "ts_ms"):
                if k in r:
                    r[k] -= ms
    return rs


def investigate(rs, name, mode="scripted"):
    p = tmp / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rs) + "\n", encoding="utf-8")
    db = tmp / f"{name}.db"
    if mode == "replay":
        (tmp / name).mkdir(exist_ok=True)
        db = tmp / name / "incident.db"  # name that replay mode keys on
    load_telemetry(p, db)
    rep = None
    for ev in run_investigation(str(db), mode=mode):
        if ev.type == "report":
            rep = ev.payload.get("report", ev.payload)
    return rep, {r.get("service") for r in rs if "service" in r}


def foreign_names(rep, present):
    text = json.dumps(
        [h["statement"] for h in rep["hypotheses_evaluated"]]
        + [t["description"] for t in rep["timeline_events"]]
    )
    ghosts = [s for s in SVC if s not in present and s in text]  # original names not in this data
    svc_ghost = {t["service"] for t in rep["timeline_events"]} - present
    return ghosts, svc_ghost


def run(label, fn):
    try:
        ok, why = fn()
    except Exception as e:  # noqa: BLE001
        ok, why = False, f"crashed: {type(e).__name__}: {str(e)[:110]}"
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n       -> {why}"))
    return ok


def c_rename():
    rep, present = investigate(rename(records), "rename")
    g, sg = foreign_names(rep, present)
    return (not g and not sg), f"report mentions names absent from data: {g or sg}"


def c_noskew():
    gt = json.loads(GT.read_text(encoding="utf-8")) if GT.exists() else {}
    rs = [dict(r) for r in records]
    for svc, ms in gt.get("clock_skews_ms", {}).items():
        shift(rs, svc, ms)
    rep, _ = investigate(rs, "noskew")
    big = {k: v for k, v in rep["estimated_clock_offsets_s"].items() if abs(v) > 0.5}
    return not big, f"invented skew where none exists: {big}"


def c_replay():
    rep, present = investigate(rename(records), "replaytrap", mode="replay")
    g, sg = foreign_names(rep, present)
    return (not g and not sg), f"replay returned canned output for different data: {g or sg}"


def c_control():
    def ts(r):
        return r.get("start_ms", r.get("ts_ms"))

    lo, hi = min(map(ts, records)), max(map(ts, records))
    healthy = [r for r in records if ts(r) < lo + 0.25 * (hi - lo)]
    rep, _ = investigate(healthy, "control")
    bad = [
        (h["id"], round(h["confidence"], 2))
        for h in rep["hypotheses_evaluated"]
        if h["verdict"] == "root_cause" and h["confidence"] > 0.5
    ]
    return not bad, f"confident root cause on healthy-only data: {bad}"


def main() -> int:
    results = [
        run("renamed services/metrics: runs and cites only real names", c_rename),
        run("no skew present: does not invent one", c_noskew),
        run("replay mode with different data: does not return canned report", c_replay),
        run("no-incident control: abstains instead of naming a root cause", c_control),
    ]
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
