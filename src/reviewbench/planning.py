"""Estimate campaign launch size from operator-supplied limits."""

import re
from collections import defaultdict
from math import ceil


def estimate(manifest, limits: dict, concurrency: int) -> dict:
    if concurrency < 1 or not isinstance(limits, dict):
        raise ValueError("Expected positive concurrency and an object of reviewer limits")
    by_provider = defaultdict(list)
    for pair in manifest.pairs():
        by_provider[pair.target.provider_id].append(pair)
    if limits.keys() - by_provider.keys():
        raise ValueError("Limit configuration names a reviewer outside the manifest")
    reviewers = []
    for provider, pairs in sorted(by_provider.items()):
        settings = limits.get(provider, {})
        fields = ("reviews_available", "credits_available", "credits_per_review", "cost_minor_per_review")
        if not isinstance(settings, dict) or settings.keys() - {*fields, "currency"}:
            raise ValueError(f"{provider}: unknown limit field")
        for field in fields:
            if field in settings and (type(settings[field]) is not int or settings[field] < 0):
                raise ValueError(f"{provider}: {field} must be a nonnegative integer")
        currency = settings.get("currency")
        has_cost = "cost_minor_per_review" in settings
        if has_cost != bool(isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency)):
            raise ValueError(f"{provider}: cost requires a three-letter currency")
        count = len(pairs)
        repositories = sorted({pair.target.repository for pair in pairs})
        pinned_inputs = {(pair.target.repository, pair.variant.input_digest, pair.head_sha) for pair in pairs}
        by_check = sum(bool(pair.target.completion_check) for pair in pairs)
        credits = settings["credits_per_review"] * count if "credits_per_review" in settings else None
        manual = sum(pair.target.trigger == "manual" for pair in pairs)
        waves = ceil(count / concurrency)
        warnings = []
        for demand, available, label in ((count, settings.get("reviews_available"), "review quota"),
                                         (credits, settings.get("credits_available"), "credit allowance")):
            if demand is not None and available is not None and demand > available:
                warnings.append(f"Planned demand exceeds the {label} by {demand - available}")
        reviewers.append({"reviewer": provider, "repositories": repositories,
                          "reviews": count, "estimated_credits": credits,
                          "estimated_cost_minor": settings["cost_minor_per_review"] * count if has_cost else None,
                          "currency": currency, "manual_triggers": manual,
                          "new_pr_writes": count, "new_head_ref_writes_at_most": count,
                          "github_reads_preflight": 3 * len(pinned_inputs),
                          "github_reads_to_open_fresh_prs": 6 * count + len(repositories),
                          "github_reads_one_poll_per_open_pr": 3 * count + 2 * by_check,
                          "waves": waves, "full_window_seconds": waves * (manifest.timeout_seconds + manifest.settling_seconds),
                          "warnings": warnings})
    return {"repositories": len(manifest.targets), "attempts": sum(row["reviews"] for row in reviewers),
            "max_active_per_reviewer": concurrency, "review_timeout_seconds": manifest.timeout_seconds,
            "settling_seconds": manifest.settling_seconds,
            "parallel_full_window_seconds": max((row["full_window_seconds"] for row in reviewers), default=0),
            "github_read_estimates_exclude": ["extra pages", "additional polls", "retries and rate-limit recovery"],
            "reviewers": reviewers}
