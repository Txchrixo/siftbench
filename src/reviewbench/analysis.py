"""Operational metrics and paired comparisons over validated result bundles."""

from collections import Counter, defaultdict
from itertools import combinations
from math import ceil, isfinite
import re
from statistics import median

from .grading import ScoreCalculator
from .manifest import canonical_json, digest


SCORING_POLICY_VERSION = 1


def sample_key(pair: dict) -> tuple[str, str, int]:
    return pair["case_id"], pair["condition"], pair["repeat_index"]


def _sample_index(bundle: dict) -> dict:
    samples = {}
    for pair in bundle["pairs"]:
        key = sample_key(pair)
        identity = (pair.get("input_digest"), pair.get("base_sha"), pair.get("head_sha"))
        if samples.setdefault(key, identity) != identity:
            raise ValueError("Reviewers did not receive the same pinned input for a sample")
    return samples


def _elapsed(start, end) -> float | None:
    if all(type(value) in (int, float) and isfinite(value) for value in (start, end)) and end >= start:
        return round(end - start, 3)
    return None


def _distribution(values: list[float]) -> dict:
    values.sort()
    return {"n": len(values), "p50": median(values) if values else None,
            "p95": values[ceil(.95 * len(values)) - 1] if values else None}


def _scope(pairs: list[dict]) -> dict:
    totals = Counter(pair["status"] for pair in pairs)
    return {"planned": len(pairs), "completed": totals["completed"], "status_counts": dict(sorted(totals.items()))}


def _metrics(pairs: list[dict]) -> dict:
    complete = [pair for pair in pairs if pair["status"] == "completed"]
    verdicts, completion, first_finding, costs = Counter(), [], [], defaultdict(list)
    matched = expected = graded_prs = false_positives = high_expected = high_missed = 0
    for pair in pairs:
        if cost := pair.get("cost"):
            costs[cost["currency"]].append(cost["amount_minor"])
    for pair in complete:
        claims = pair["claims"]
        decisions = Counter(claim["decision"] or "ungraded" for claim in claims)
        verdicts.update(decisions)
        fully_graded = decisions["ungraded"] == decisions["unresolved"] == 0
        if fully_graded:
            graded_prs += 1
            false_positives += decisions["false_positive"]
        hits = {claim["expected_id"] for claim in claims if claim["decision"] == "confirmed_expected"}
        references = {item["expected_id"] for item in pair["expected"]}
        severe = {item["expected_id"] for item in pair["expected"] if item.get("severity") in {"critical", "high"}}
        matched += len(hits & references)
        expected += len(references)
        if fully_graded:
            high_expected += len(severe)
            high_missed += len(severe - hits)
        timing = pair.get("timing", {})
        started = timing.get("review_started_at")
        if (seconds := _elapsed(started, timing.get("published_at"))) is not None:
            completion.append(seconds)
        verified_times = [seconds for claim in claims if claim["decision"] in {"confirmed_expected", "confirmed_novel"}
                          if (seconds := _elapsed(started, claim.get("published_at"))) is not None]
        if verified_times:
            first_finding.append(min(verified_times))
    novel = verdicts["confirmed_novel"]
    return {**_scope(pairs), "matched": matched, "expected": expected, "novel": novel,
            "adjudicated": matched + novel + verdicts["false_positive"], "false_positives": verdicts["false_positive"],
            "false_positives_per_graded_pr": {"false_positives": false_positives, "graded_prs": graded_prs},
            "high_severity": {"expected": high_expected, "missed": high_missed},
            "verdict_counts": dict(sorted(verdicts.items())), "completion_seconds": _distribution(completion),
            "first_verified_finding_seconds": _distribution(first_finding),
            "observed_cost_minor": {currency: {"n": len(amounts), "total": sum(amounts), "p50": median(amounts)}
                                    for currency, amounts in sorted(costs.items())}}


def campaign_metrics(bundle: dict, *, reviewer_names: bool = False) -> dict:
    """Build an allowlisted summary without carrying raw bundle fields into any export."""
    providers = defaultdict(list)
    for pair in bundle["pairs"]:
        providers[pair["provider_id"]].append(pair)
    scores = ScoreCalculator.calculate(bundle)
    reviewers, aliases = [], {}
    for index, (provider, pairs) in enumerate(sorted(providers.items()), start=1):
        aliases[provider] = provider if reviewer_names else f"reviewer-{index}"
        by_repeat = defaultdict(list)
        for pair in pairs:
            by_repeat[pair["repeat_index"]].append(pair)
        reviewers.append({"reviewer": aliases[provider], "operations": _metrics(pairs),
                          "shared_initial_scores": scores["providers"][provider],
                          "repeats": [{"repeat_index": repeat, **_metrics(sample)} for repeat, sample in sorted(by_repeat.items())]})
    pairwise = []
    sample_sets = {name: {sample_key(pair): pair for pair in pairs} for name, pairs in providers.items()}
    for left, right in combinations(sorted(providers), 2):
        first, second = sample_sets[left], sample_sets[right]
        planned = sorted(first.keys() & second.keys())
        completed = [key for key in planned if first[key]["status"] == second[key]["status"] == "completed"]
        pairwise.append({"planned_in_both": len(planned), "completed_in_both": len(completed), "reviewers": [
            {"reviewer": aliases[name], "metrics": _metrics([source[key] for key in completed])}
            for name, source in ((left, first), (right, second))]})
    return {"summary_schema_version": 1, "scoring_policy_version": SCORING_POLICY_VERSION, "fixture": bundle["fixture"],
            "shared_initial_conditions": len(scores["shared"]), "coverage": _scope(bundle["pairs"]), "reviewers": reviewers,
            "pairwise": pairwise}


def freeze_corpus(manifest, rubric: bytes) -> dict:
    if digest(rubric) != manifest.rubric_digest:
        raise ValueError("Corpus rubric does not match the pinned digest")
    cases = [{"case_id": case.case_id, "condition": case.condition, "base_sha": case.base_sha,
              "head_sha": case.head_sha, "input_digest": case.input_digest} for case in manifest.cases]
    core = {"rubric_digest": manifest.rubric_digest, "repeats": manifest.repeats, "cases": cases}
    return {"corpus_schema_version": 1, "corpus_digest": digest(canonical_json(core)), **core}


def compare_campaigns(before: dict, after: dict) -> dict:
    """Require the same planned corpus and show scores on explicit matched pairs."""
    if before["fixture"] != after["fixture"] or before.get("rubric_digest") != after.get("rubric_digest"):
        raise ValueError("Comparison requires the same frozen rubric and execution mode")
    if not isinstance(before.get("rubric_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", before["rubric_digest"]):
        raise ValueError("Comparison requires a pinned rubric digest")
    baseline, comparison = _sample_index(before), _sample_index(after)
    if not baseline or baseline.keys() != comparison.keys():
        raise ValueError("Comparison requires the same planned case conditions and repetition indexes")
    legacy_same_manifest = bool(before.get("manifest_digest")) and before["manifest_digest"] == after.get("manifest_digest")
    for key, old in baseline.items():
        new = comparison[key]
        input_matches = old[0] == new[0] if old[0] and new[0] else legacy_same_manifest
        if old[1:] != new[1:] or not all(old[1:]) or not input_matches:
            raise ValueError("Comparison cannot verify identical pinned case inputs")
    previous = {(pair["provider_id"], sample_key(pair)): pair for pair in before["pairs"]}
    current = {(pair["provider_id"], sample_key(pair)): pair for pair in after["pairs"]}
    reviewers = sorted({provider for provider, _ in previous.keys() & current.keys()})
    if not reviewers:
        raise ValueError("Comparison requires at least one common reviewer")
    comparisons = []
    for reviewer in reviewers:
        planned = [key for key in sorted(baseline) if (reviewer, key) in previous and (reviewer, key) in current]
        first, second = [previous[reviewer, key] for key in planned], [current[reviewer, key] for key in planned]
        matched = [(old, new) for old, new in zip(first, second) if old["status"] == new["status"] == "completed"]
        for old, new in matched:
            if sorted(canonical_json(item) for item in old["expected"]) != sorted(canonical_json(item) for item in new["expected"]):
                raise ValueError("Comparison requires the same loaded reference findings for matched samples")
        comparisons.append({"reviewer": reviewer, "planned_in_both": len(planned), "completed_in_both": len(matched),
                            "before_coverage": _scope(first), "after_coverage": _scope(second),
                            "before": _metrics([old for old, _ in matched]), "after": _metrics([new for _, new in matched]),
                            "cases": [{"case_id": key[0], "condition": key[1], "repeat_index": key[2],
                                       "case_title": new.get("case_title") or old.get("case_title"),
                                       "before_status": old["status"], "after_status": new["status"],
                                       "before_pr": old.get("pr_url"), "after_pr": new.get("pr_url")}
                                      for key, old, new in zip(planned, first, second)]})
    configs = []
    for bundle in (before, after):
        entries = bundle.get("reviewer_configuration", [])
        configs.append([{field: row.get(field) for field in ("provider", "condition", "trigger", "check", "completion_policy", "vendor_version")}
                        for row in entries if row.get("provider") in reviewers])
    return {"comparison_schema_version": 1, "scoring_policy_version": SCORING_POLICY_VERSION,
            "fixture": before["fixture"], "before_run": before["run_id"], "after_run": after["run_id"],
            "planned_samples": len(baseline), "corpus_digest": digest(canonical_json(sorted(baseline.items()))),
            "before_configuration": configs[0], "after_configuration": configs[1], "reviewers": comparisons}


def attach_costs(bundle: dict, records: list[dict]) -> None:
    """Attach externally measured costs to exact attempt IDs, with source provenance."""
    indexed = {pair["attempt_id"]: pair for pair in bundle["pairs"] if pair.get("attempt_id")}
    seen = set()
    if not isinstance(records, list):
        raise ValueError("Cost evidence must be an array")
    for item in records:
        if not isinstance(item, dict) or set(item) != {"attempt_id", "currency", "amount_minor", "source"}:
            raise ValueError("Invalid cost evidence fields")
        attempt, currency, amount, source = (item[key] for key in ("attempt_id", "currency", "amount_minor", "source"))
        if (not isinstance(attempt, str) or attempt not in indexed or attempt in seen
                or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
                or type(amount) is not int or not 0 <= amount <= 10 ** 12
                or not isinstance(source, str) or not source.strip() or len(source) > 2048):
            raise ValueError("Invalid or duplicate measured cost evidence")
        seen.add(attempt)
        indexed[attempt]["cost"] = {"currency": currency, "amount_minor": amount, "source_digest": digest(source.encode())}
