"""Evaluation runner: executes scripted investigation across scenarios and generates eval/report.md."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np  # noqa: E402

from datagen.scenarios import DEFAULT_SEEDS, SCENARIOS  # noqa: E402
from eval.calibration import evaluate_hypothesis_scores, extract_features_from_db  # noqa: E402
from ingest.load_jsonl import load_telemetry  # noqa: E402

SCENARIOS_DIR = ROOT_DIR / "data" / "scenarios"
WEIGHTS_FILE = ROOT_DIR / "eval" / "fitted_weights.json"
REPORT_FILE = ROOT_DIR / "eval" / "report.md"


def run_evaluation() -> dict[str, Any]:
    """Evaluate investigation accuracy and calibration across all scenario datasets."""
    # Load calibrated weights if available, else defaults
    weights = {}
    if WEIGHTS_FILE.exists():
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            weights = json.load(f).get("weights", {})

    all_predictions = []
    scenarios_evaluated = 0
    correct_count = 0
    total_brier = 0.0

    # Also evaluate the primary incident dataset if it exists
    primary_jsonl = ROOT_DIR / "data" / "telemetry.jsonl"
    primary_gt = ROOT_DIR / "data" / "ground_truth.json"
    if primary_jsonl.exists() and primary_gt.exists():
        tmp_db = ROOT_DIR / "data" / "eval_primary_tmp.db"
        load_telemetry(primary_jsonl, tmp_db)
        feat = extract_features_from_db(tmp_db)
        if tmp_db.exists():
            tmp_db.unlink()

        with open(primary_gt, "r", encoding="utf-8") as f:
            gt = json.load(f)

        scores = evaluate_hypothesis_scores(feat, weights)
        predicted_svc = max(scores, key=scores.get)
        predicted_conf = scores[predicted_svc]
        true_svc = gt["root_cause_service"]
        is_correct = predicted_svc == true_svc

        if is_correct:
            correct_count += 1
        scenarios_evaluated += 1

        for svc in ["auth-service", "db-pool", "frontend", "gateway"]:
            y = 1.0 if svc == true_svc else 0.0
            p = scores.get(svc, 0.0)
            total_brier += (p - y) ** 2

        all_predictions.append(
            {
                "scenario": "primary_telemetry",
                "seed": 42,
                "predicted_root_cause": predicted_svc,
                "true_root_cause": true_svc,
                "confidence": predicted_conf,
                "correct": is_correct,
                "scores": scores,
            }
        )

    # Evaluate all canonical scenarios
    for sc in SCENARIOS:
        for seed in DEFAULT_SEEDS:
            jsonl_file = SCENARIOS_DIR / f"{sc}_{seed}.jsonl"
            gt_file = SCENARIOS_DIR / f"{sc}_{seed}_gt.json"
            if not jsonl_file.exists() or not gt_file.exists():
                continue

            tmp_db = ROOT_DIR / "data" / f"eval_tmp_{sc}_{seed}.db"
            load_telemetry(jsonl_file, tmp_db)
            feat = extract_features_from_db(tmp_db)
            if tmp_db.exists():
                tmp_db.unlink()

            with open(gt_file, "r", encoding="utf-8") as f:
                gt = json.load(f)

            scores = evaluate_hypothesis_scores(feat, weights)
            predicted_svc = max(scores, key=scores.get)
            predicted_conf = scores[predicted_svc]
            true_svc = gt["root_cause_service"]
            is_correct = predicted_svc == true_svc

            if is_correct:
                correct_count += 1
            scenarios_evaluated += 1

            for svc in ["auth-service", "db-pool", "frontend", "gateway"]:
                y = 1.0 if svc == true_svc else 0.0
                p = scores.get(svc, 0.0)
                total_brier += (p - y) ** 2

            all_predictions.append(
                {
                    "scenario": sc,
                    "seed": seed,
                    "predicted_root_cause": predicted_svc,
                    "true_root_cause": true_svc,
                    "confidence": predicted_conf,
                    "correct": is_correct,
                    "scores": scores,
                }
            )

    top1_accuracy = correct_count / max(1, scenarios_evaluated)
    brier_score = total_brier / (max(1, scenarios_evaluated) * 4.0)

    # Reliability table bins
    bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    reliability_rows = []
    for low, high in bins:
        bin_items = [
            p
            for p in all_predictions
            if low <= p["confidence"] < high or (high == 1.0 and p["confidence"] == 1.0)
        ]
        count = len(bin_items)
        if count > 0:
            avg_pred = float(np.mean([p["confidence"] for p in bin_items]))
            empirical_acc = float(np.mean([1.0 if p["correct"] else 0.0 for p in bin_items]))
        else:
            avg_pred = (low + high) / 2.0
            empirical_acc = 0.0
        reliability_rows.append(
            {
                "bin": f"[{low:.1f}, {high:.1f}]",
                "count": count,
                "avg_predicted_conf": round(avg_pred, 3),
                "empirical_accuracy": round(empirical_acc, 3),
                "calibration_gap": round(abs(avg_pred - empirical_acc), 3) if count > 0 else 0.0,
            }
        )

    # Generate Markdown report
    report_content = f"""# Autonomous Incident Investigation Evaluation Report

## Summary
- **Scenarios Evaluated:** {scenarios_evaluated}
- **Top-1 Root Cause Accuracy:** {top1_accuracy:.2%} ({correct_count}/{scenarios_evaluated})
- **Brier Score (Multi-class Mean):** {brier_score:.4f}

## Reliability Calibration Table
The reliability table compares model-assigned evidence confidence against observed empirical diagnostic accuracy across prediction bins.

| Confidence Bin | Predictions Count | Mean Predicted Confidence | Empirical Accuracy | Calibration Gap |
| :--- | :--- | :--- | :--- | :--- |
"""
    for r in reliability_rows:
        report_content += (
            f"| `{r['bin']}` | {r['count']} | {r['avg_predicted_conf']:.3f} | "
            f"{r['empirical_accuracy']:.3f} | {r['calibration_gap']:.3f} |\n"
        )

    report_content += """
## Detailed Scenario Results

| Scenario | Seed | Predicted Root Cause | True Root Cause | Confidence | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
"""
    for p in all_predictions:
        badge = "PASS" if p["correct"] else "FAIL"
        report_content += (
            f"| `{p['scenario']}` | `{p['seed']}` | `{p['predicted_root_cause']}` | "
            f"`{p['true_root_cause']}` | `{p['confidence']:.2f}` | **{badge}** |\n"
        )

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    return {
        "scenarios_evaluated": scenarios_evaluated,
        "top1_accuracy": round(top1_accuracy, 4),
        "brier_score": round(brier_score, 4),
        "reliability_table": reliability_rows,
        "report_path": str(REPORT_FILE),
    }


def main():
    parser = argparse.ArgumentParser(description="Run evaluation across all incident scenarios")
    parser.parse_args()

    results = run_evaluation()
    print("Evaluation completed:")
    print(f"  Scenarios Evaluated: {results['scenarios_evaluated']}")
    print(f"  Top-1 Accuracy:      {results['top1_accuracy']:.2%}")
    print(f"  Brier Score:         {results['brier_score']:.4f}")
    print(f"  Report written to:   {results['report_path']}")


if __name__ == "__main__":
    main()
