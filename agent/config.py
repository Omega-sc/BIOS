"""Configuration parameters and thresholds for Track 09 incident investigation.

All thresholds are chosen based on statistical properties and empirical validation
on development scenarios (pool_leak, slow_db, retry_storm, timeout_misconfig).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IncidentDetectionConfig:
    """Configuration for robust baseline and anomaly detection gate (P2)."""

    # Baseline fraction: the early fraction of telemetry used to establish healthy baseline.
    # 0.25 (first quartile) provides sufficient observation window to estimate typical variance
    # while leaving 75% of the timeframe for anomaly onset detection.
    baseline_time_fraction: float = 0.25

    # Robust z-score threshold using Median Absolute Deviation (MAD).
    # A value of 3.5 corresponds to ~3.5 robust standard deviations away from baseline median.
    # Under a normal distribution, z > 3.5 has probability p < 0.0005, making it a reliable
    # barrier against false positive detections on nominal operational jitter.
    mad_z_threshold: float = 3.5

    # Minimum absolute error rate threshold.
    # An error rate exceeding 5% (0.05) is clinically anomalous regardless of baseline variance.
    min_error_rate_anomaly: float = 0.05

    # Minimum number of samples required to compute baseline statistics.
    min_baseline_samples: int = 3


@dataclass(frozen=True)
class HypothesisEvaluationConfig:
    """Configuration for hypothesis scoring, margin thresholding, and abstention (P2)."""

    # Minimum evidence weight (likelihood multiplier) required for a hypothesis to be declared root cause.
    # Hypotheses with evidence score < 2.0 lack sufficient positive observational evidence.
    min_evidence_score: float = 2.0

    # Minimum confidence margin between top hypothesis and runner-up.
    # If the margin is < 0.15 (15%), evidence is deemed ambiguous and the investigation abstains
    # by marking the top hypothesis as 'unresolved'.
    min_confidence_margin: float = 0.15

    # Maximum confidence allowed when a hypothesis verdict is 'unresolved'.
    # Strictly capped at 0.45 to prevent unjustified confidence on ambiguous or insufficient evidence.
    max_unresolved_confidence: float = 0.45

    # Prior mass assigned to 'none of the above' / unresolved when computing normalized confidences.
    none_of_above_mass: float = 0.5


INCIDENT_CONFIG = IncidentDetectionConfig()
HYPOTHESIS_CONFIG = HypothesisEvaluationConfig()
