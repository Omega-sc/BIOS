"""Verification tests for canonical scenarios and datasets matrix."""

import json
from pathlib import Path

from contracts.models import LogLine, MetricPoint, Span
from datagen.scenarios import DEFAULT_SEEDS, SCENARIOS

ROOT_DIR = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"


def test_scenario_files_exist():
    assert SCENARIOS_DIR.exists()
    for sc in SCENARIOS:
        for seed in DEFAULT_SEEDS:
            jsonl_path = SCENARIOS_DIR / f"{sc}_{seed}.jsonl"
            gt_path = SCENARIOS_DIR / f"{sc}_{seed}_gt.json"
            assert jsonl_path.exists(), f"Missing scenario file {jsonl_path}"
            assert gt_path.exists(), f"Missing ground truth file {gt_path}"


def test_scenario_ground_truth_content():
    for sc in SCENARIOS:
        for seed in DEFAULT_SEEDS:
            gt_path = SCENARIOS_DIR / f"{sc}_{seed}_gt.json"
            with open(gt_path, "r", encoding="utf-8") as f:
                gt = json.load(f)
            assert gt["scenario"] == sc
            assert gt["seed"] == seed
            assert "root_cause_service" in gt
            assert "failing_resource" in gt
            assert gt["clock_skews_ms"]["auth-service"] == 12_000


def test_scenario_records_validity():
    # Spot check one of each scenario
    for sc in SCENARIOS:
        jsonl_path = SCENARIOS_DIR / f"{sc}_101.jsonl"
        assert jsonl_path.exists()
        line_count = 0
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line_count += 1
                rec = json.loads(line)
                rtype = rec["type"]
                if rtype == "span":
                    Span.model_validate(rec)
                elif rtype == "log":
                    LogLine.model_validate(rec)
                elif rtype == "metric":
                    MetricPoint.model_validate(rec)
                else:
                    raise ValueError(f"Unknown type {rtype}")
        assert line_count >= 1500


def test_scenario_counts_match_ground_truth():
    for sc in SCENARIOS:
        for seed in DEFAULT_SEEDS:
            gt_path = SCENARIOS_DIR / f"{sc}_{seed}_gt.json"
            jsonl_path = SCENARIOS_DIR / f"{sc}_{seed}.jsonl"
            with open(gt_path, "r", encoding="utf-8") as f:
                gt = json.load(f)
            records = [json.loads(line) for line in open(jsonl_path, "r", encoding="utf-8")]
            traces = set(r["trace_id"] for r in records if r.get("trace_id"))
            assert len(records) == gt["total_records"], f"Record count mismatch in {sc}_{seed}"
            assert len(traces) == gt["total_traces"], f"Trace count mismatch in {sc}_{seed}"
