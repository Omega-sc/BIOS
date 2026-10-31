"""Tests for evaluation and calibration modules."""

from pathlib import Path

from eval.calibration import fit_calibration_weights
from eval.run_eval import run_evaluation

ROOT_DIR = Path(__file__).resolve().parent.parent
WEIGHTS_FILE = ROOT_DIR / "eval" / "fitted_weights.json"
REPORT_FILE = ROOT_DIR / "eval" / "report.md"


def test_calibration_fitting():
    results = fit_calibration_weights()
    assert "weights" in results
    assert "train_metrics" in results
    assert "heldout_test_metrics" in results
    assert results["heldout_test_metrics"]["top1_accuracy"] >= 0.80
    assert results["heldout_test_metrics"]["brier_score"] <= 0.25
    assert WEIGHTS_FILE.exists()


def test_run_evaluation_report():
    eval_results = run_evaluation()
    assert eval_results["scenarios_evaluated"] >= 20
    assert eval_results["top1_accuracy"] >= 0.85
    assert eval_results["brier_score"] <= 0.25
    assert REPORT_FILE.exists()

    report_text = REPORT_FILE.read_text(encoding="utf-8")
    assert "Autonomous Incident Investigation Evaluation Report" in report_text
    assert "Reliability Calibration Table" in report_text
    assert "Top-1 Root Cause Accuracy" in report_text
