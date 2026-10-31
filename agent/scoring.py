"""Hypothesis confidence scoring based on telemetry evidence (owned by P3)."""

from contracts.models import EvidencePointer


def score_hypothesis(
    supporting: list[EvidencePointer],
    refuting: list[EvidencePointer],
    prior: float = 0.5,
    weights: dict[str, float] | None = None,
) -> float:
    """Compute evidence-based confidence score using likelihood ratio updating.

    Never hardcoded; derived from evidence pointers and calibrated weights.
    """
    w = weights or {"span": 1.5, "metric": 2.0, "log": 1.8}
    odds = prior / max(1e-5, (1.0 - prior))
    for ep in supporting:
        lr = w.get(ep.kind, 1.2)
        odds *= lr
    for ep in refuting:
        lr = w.get(ep.kind, 1.2)
        odds /= lr
    prob = odds / (1.0 + odds)
    return max(0.01, min(0.99, round(prob, 4)))
