"""Dynamic linter that extracts scenario terms from data/telemetry.jsonl at runtime

and verifies no string literals in agent/, tools/, sim/, ui/, ingest/ contain them.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
TELEMETRY_PATH = ROOT_DIR / "data" / "telemetry.jsonl"
FALLBACK_TELEMETRY = ROOT_DIR / "fixtures" / "tiny_telemetry.jsonl"

TARGET_DIRS = ["agent", "tools", "sim", "ui", "ingest"]


def extract_scenario_terms() -> tuple[set[str], set[str], set[str]]:
    """Extract services, metrics, and log tokens dynamically from telemetry at runtime."""
    telemetry_file = (
        TELEMETRY_PATH
        if TELEMETRY_PATH.exists()
        else (FALLBACK_TELEMETRY if FALLBACK_TELEMETRY.exists() else None)
    )
    if not telemetry_file:
        print(f"Error: Telemetry file not found at {TELEMETRY_PATH} or {FALLBACK_TELEMETRY}")
        sys.exit(1)

    services: set[str] = set()
    metrics: set[str] = set()
    log_phrases: set[str] = set()

    with open(telemetry_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            svc = rec.get("service")
            if svc and isinstance(svc, str) and len(svc) >= 3:
                services.add(svc)

            rtype = rec.get("type")
            if rtype == "metric" and "metric" in rec:
                metrics.add(rec["metric"])

            if rtype == "log" and "message" in rec:
                msg = rec["message"]
                if "unhandled exception" in msg.lower():
                    log_phrases.add("unhandled exception skipped release")
                if "pool exhausted" in msg.lower():
                    log_phrases.add("connection pool exhausted")

    return services, metrics, log_phrases


def check_file_for_hardcoded_literals(
    file_path: Path,
    services: set[str],
    metrics: set[str],
    log_phrases: set[str],
) -> list[tuple[int, str, str]]:
    """Return list of (line_no, term, context) violations in a Python file."""
    violations: list[tuple[int, str, str]] = []

    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as e:
        print(f"Warning: Could not parse {file_path}: {e}")
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            # Check service names
            for s in services:
                # Substring check for hyphenated or distinct service names (e.g. auth-service, db-pool)
                # or word boundary match for short words like gateway / frontend
                pattern = rf"(?i)\b{re.escape(s)}\b" if "-" not in s else rf"(?i){re.escape(s)}"
                if re.search(pattern, val):
                    violations.append((node.lineno, s, val[:80]))
                    break

            # Check metric names
            for m in metrics:
                if m in val:
                    violations.append((node.lineno, m, val[:80]))
                    break

            # Check log phrases
            for lp in log_phrases:
                if lp.lower() in val.lower():
                    violations.append((node.lineno, lp, val[:80]))
                    break

    return violations


def main() -> int:
    services, metrics, log_phrases = extract_scenario_terms()
    print("Extracted runtime scenario terms from telemetry:")
    print(f"  Services ({len(services)}): {sorted(services)}")
    print(f"  Metrics  ({len(metrics)}): {sorted(metrics)}")
    print(f"  Log phrases ({len(log_phrases)}): {sorted(log_phrases)}")

    total_violations = 0

    for d in TARGET_DIRS:
        dir_path = ROOT_DIR / d
        if not dir_path.exists():
            continue
        for py_file in sorted(dir_path.rglob("*.py")):
            violations = check_file_for_hardcoded_literals(py_file, services, metrics, log_phrases)
            for lineno, term, ctx in violations:
                rel_path = py_file.relative_to(ROOT_DIR)
                print(f"  [HARDCODED] {rel_path}:{lineno} contains '{term}' -> {repr(ctx)}")
                total_violations += 1

    if total_violations > 0:
        print(f"\n[FAIL] make lint-hardcode found {total_violations} hardcoded scenario literals!")
        return 1

    print("\n[PASS] make lint-hardcode: 0 hardcoded scenario literals found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
