from copy import deepcopy
from pathlib import Path
import json

import pytest

from reviewbench.analysis import attach_costs, campaign_metrics, compare_campaigns
from reviewbench.audit import audit_campaign
from reviewbench.corpus import prepare_corpus
from reviewbench.github import PullRequest
from reviewbench.manifest import ManifestLoader


def _bundle(manifest, run_id="pilot"):
    pairs = []
    for item in manifest.pairs():
        variant = item.variant
        pairs.append({"case_id": variant.case_id, "condition": variant.condition, "repeat_index": item.repeat_index,
                      "provider_id": item.target.provider_id, "pair_id": item.pair_id, "attempt_id": item.pair_id,
                      "base_sha": variant.base_sha, "head_sha": variant.head_sha, "input_digest": variant.input_digest,
                      "pr_url": f"https://github.com/{item.target.repository}/pull/{item.repeat_index + 1}",
                      "status": "completed", "claims": [], "expected": [{"expected_id": "reference", "severity": "high"}],
                      "timing": {"review_started_at": 100, "published_at": 115}})
    return {"fixture": False, "run_id": run_id, "manifest_digest": manifest.manifest_digest,
            "rubric_digest": manifest.rubric_digest, "pairs": pairs}


def test_paired_scope_keeps_repeats_and_missing_data_visible(manifest_data):
    manifest_data["repeats"] = 2
    primary = _bundle(ManifestLoader.parse(manifest_data))
    after = deepcopy(primary)
    after["pairs"][1]["status"] = "timed_out"
    added = deepcopy(after["pairs"][0])
    added.update(provider_id="third", status="quota")
    after["pairs"].append(added)
    result = compare_campaigns(primary, after)
    first, second = result["reviewers"]
    assert result["planned_samples"] == 2 and first["completed_in_both"] == 1 and second["completed_in_both"] == 2
    assert first["after_coverage"]["status_counts"]["timed_out"] == 1
    assert first["after"]["high_severity"] == {"expected": 1, "missed": 1}
    assert first["after"]["completion_seconds"] == {"n": 1, "p50": 15, "p95": 15}
    summary = campaign_metrics(after, reviewer_names=True)
    assert len(summary["reviewers"][0]["repeats"]) == 2
    peer = next(row for row in summary["pairwise"] if {item["reviewer"] for item in row["reviewers"]} == {"one", "two"})
    assert peer["completed_in_both"] == 1
    after["pairs"][0]["input_digest"] = "f" * 64
    with pytest.raises(ValueError, match="pinned input"):
        compare_campaigns(primary, after)


def test_cost_and_first_verified_finding_use_explicit_evidence(manifest_data):
    bundle = _bundle(ManifestLoader.parse(manifest_data))
    first = bundle["pairs"][0]
    first["claims"] = [{"decision": "confirmed_novel", "published_at": 107}]
    attach_costs(bundle, [{"attempt_id": first["attempt_id"], "currency": "USD", "amount_minor": 23,
                           "source": "Private statement URL"}])
    summary, other = campaign_metrics(bundle)["reviewers"]
    assert summary["operations"]["first_verified_finding_seconds"]["p50"] == 7
    assert summary["operations"]["observed_cost_minor"]["USD"]["total"] == 23
    assert other["operations"]["observed_cost_minor"] == {}
    assert "Private statement URL" not in str(summary)


def test_audit_checks_source_identity_without_affecting_score(manifest_data):
    manifest = ManifestLoader.parse(manifest_data)
    bundle = _bundle(manifest)
    pair = bundle["pairs"][0]
    pair["capture_signal"] = "review:42"
    publisher = manifest.pairs()[0].target.publisher
    body = "Review description"
    pair["claims"] = [{"source_object_id": "review:42", "body": body, "source_url": pair["pr_url"] + "#pullrequestreview-42"}]

    class SourceClient:
        def get_pull(self, repository, number):
            return PullRequest(1, pair["pr_url"], repository, "main", "head", pair["base_sha"], pair["head_sha"])

        def review_sources(self, pr):
            return ([{"id": 42, "body": body, "html_url": pr.url + "#pullrequestreview-42", "state": "COMMENTED",
                      "user": {"login": publisher}, "commit_id": pr.head_sha}], [], [], [])

    original = deepcopy(bundle)
    result = audit_campaign(bundle, manifest, SourceClient())[0]
    assert result["publication_sources"] == ["review:42"]
    assert result["claims"][0]["matches"] is True
    assert bundle == original


def test_real_catalog_compiles_into_isolated_targets_without_gold_in_pr_bodies():
    path = Path(__file__).resolve().parents[1] / "benchmark" / "evaluation-v2" / "candidates.json"
    catalog = json.loads(path.read_bytes())
    assert {case["language"] for case in catalog["cases"]} == {"Go", "Python", "TypeScript"}
    cases = [{"case_id": case["case_id"], "condition": "code_only", "kind": "reference"} for case in catalog["cases"]]
    rubric = json.dumps({"cases": cases, "expected": [dict(case, expected_id="fixture-reference",
                                                         matching_criteria="Fixture-only grading evidence") for case in cases]}).encode()
    groups = sorted({case["isolation_group"] for case in catalog["cases"]})
    targets = [{"provider_id": name, "condition": "code_only", "isolation_group": group,
                "repository": f"team/{name}-{group}", "publisher": f"{name}[bot]"}
               for name in ("first", "second") for group in groups]
    manifest = prepare_corpus(catalog, targets, rubric, "multistack-fixture", 3)
    assert len(manifest.pairs()) == 42
    assert all(p.target.isolation_group == p.variant.isolation_group for p in manifest.pairs())
    assert b"Fixture-only grading evidence" not in manifest.json
