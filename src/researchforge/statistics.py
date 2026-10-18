"""Deterministic paired observations and predeclared percentile-bootstrap summaries.

Statistical indicators here do not prove random sampling, independence, or broad superiority.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from typing import Literal, TypedDict


class StatisticalInputError(ValueError):
    """Invalid or unpaired observations."""


class PairedComparison(TypedDict):
    status: Literal["POSITIVE_CI", "NON_POSITIVE_CI", "INCONCLUSIVE"]
    observation_count: int
    estimated_improvement: float
    interval_lower: float | None
    interval_upper: float | None
    confidence: float
    resamples: int
    seed: int
    method: str


def _check(observations: Mapping[str, float], name: str) -> None:
    if not observations:
        raise StatisticalInputError(f"{name} is empty")
    for case_id, value in observations.items():
        if not isinstance(case_id, str) or not case_id:
            raise StatisticalInputError("Every case ID must be a nonempty string")
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise StatisticalInputError(f"Non-numeric {name} observation")
        if not math.isfinite(value):
            raise StatisticalInputError(f"Non-finite {name} observation")


def paired_comparison(
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    *,
    direction: Literal["min", "max"],
    seed: int,
    confidence: float = 0.95,
    resamples: int = 2000,
) -> PairedComparison:
    """Return case-ID-paired improvement CI with fully seeded bootstrap resampling.

    This is an exploratory percentile interval, not a valid population-level
    significance test without an appropriate sampling design and independence unit.
    """
    _check(baseline, "baseline")
    _check(candidate, "candidate")
    if set(baseline) != set(candidate):
        raise StatisticalInputError("Unpaired case IDs or missing observations")
    if direction not in ("min", "max"):
        raise StatisticalInputError("Unknown metric direction")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise StatisticalInputError("Seed must be a nonnegative integer")
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise StatisticalInputError("Confidence must be in (0, 1)")
    if isinstance(resamples, bool) or not isinstance(resamples, int) or not 100 <= resamples <= 100_000:
        raise StatisticalInputError("Resample count must be between 100 and 100000")
    differences = [
        (baseline[key] - candidate[key]) if direction == "min"
        else (candidate[key] - baseline[key])
        for key in sorted(baseline)
    ]
    n = len(differences)
    improvement = sum(differences) / n
    if not math.isfinite(improvement):
        raise StatisticalInputError("Unrepresentable mean difference")
    if n < 2:
        return {
            "status": "INCONCLUSIVE",
            "observation_count": n,
            "estimated_improvement": improvement,
            "interval_lower": None,
            "interval_upper": None,
            "confidence": confidence,
            "resamples": 0,
            "seed": seed,
            "method": "paired-percentile-bootstrap-v1",
        }
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(differences) for _ in range(n)) / n for _ in range(resamples))
    alpha = (1.0 - confidence) / 2
    low = means[int(alpha * (resamples - 1))]
    high = means[int((1.0 - alpha) * (resamples - 1))]
    if not all(math.isfinite(v) for v in (low, high)):
        raise StatisticalInputError("Unrepresentable bootstrap interval")
    status: Literal["POSITIVE_CI", "NON_POSITIVE_CI", "INCONCLUSIVE"]
    status = "POSITIVE_CI" if low > 0 else "NON_POSITIVE_CI" if high < 0 else "INCONCLUSIVE"
    return {
        "status": status,
        "observation_count": n,
        "estimated_improvement": improvement,
        "interval_lower": low,
        "interval_upper": high,
        "confidence": confidence,
        "resamples": resamples,
        "seed": seed,
        "method": "paired-percentile-bootstrap-v1",
    }
