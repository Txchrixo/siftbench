"""Extract the anonymous public chart data from an archived campaign bundle.

Only counts and elapsed times leave the bundle. Pull requests, reviewer text, repository links and
rubric content stay in the private report.
"""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import json
from pathlib import Path

from reviewbench.analysis import _elapsed, sample_key
from reviewbench.grading import ScoreCalculator


def export(bundle: dict) -> dict:
    samples = ScoreCalculator.calculate(bundle)["shared_samples"]
    bounds = [60, 120, 180, 240, 360, 600]
    buckets = defaultdict(lambda: [0] * (len(bounds) + 1))
    times = defaultdict(list)
    detected = {}
    for pair in bundle["pairs"]:
        provider = pair["provider_id"]
        if pair["status"] == "completed":
            timing = pair.get("timing", {})
            duration = _elapsed(timing.get("review_started_at"), timing.get("published_at"))
            if duration is not None:
                buckets[provider][bisect_right(bounds, duration)] += 1
                times[provider].append(duration)
        if (sample := sample_key(pair)) in samples:
            for reference in pair["expected"]:
                detected.setdefault((sample, reference["expected_id"]), set())
            for claim in pair["claims"]:
                if claim["decision"] == "confirmed_expected":
                    detected.setdefault((sample, claim["expected_id"]), set()).add(provider)

    combinations = Counter(tuple(sorted(reviewers)) for reviewers in detected.values())
    agreement = bundle["annotation_agreement"]
    return {
        "publication_bounds_seconds": bounds,
        "publication_bins": dict(sorted(buckets.items())),
        "publication_seconds": {provider: sorted(values) for provider, values in sorted(times.items())},
        "reference_overlap": [
            {"reviewers": reviewers, "count": count}
            for reviewers, count in sorted(combinations.items(), key=lambda row: (-len(row[0]), -row[1], row[0]))
        ],
        "annotation_agreement": {key: agreement[key] for key in ("claims", "unanimous", "kappa")},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = export(json.loads(args.bundle.read_bytes()))
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
