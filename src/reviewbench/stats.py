"""Small interval estimates for ratios and medians. Deterministic and dependency free."""

from math import sqrt
from random import Random
from statistics import median


_Z = 1.959964
_MIN_BOOTSTRAP = 5


def wilson_interval(successes: int, total: int, z: float = _Z) -> tuple[float, float] | None:
    """95% Wilson score interval for a proportion, or None when there is nothing to estimate."""
    if total <= 0 or not 0 <= successes <= total:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def median_interval(values: list[float], *, resamples: int = 2000, level: float = .95) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the median. The seed comes from the data so reports replay identically."""
    if len(values) < _MIN_BOOTSTRAP:
        return None
    ordered = sorted(values)
    rng = Random(repr(ordered))
    medians = sorted(median(rng.choices(ordered, k=len(ordered))) for _ in range(resamples))
    tail = (1 - level) / 2
    return medians[int(tail * resamples)], medians[min(resamples - 1, int((1 - tail) * resamples))]


def percent_interval(successes: int, total: int) -> str | None:
    interval = wilson_interval(successes, total)
    if not interval:
        return None
    low, high = (round(value * 100) for value in interval)
    return f"95% CI {low}-{high}%"
