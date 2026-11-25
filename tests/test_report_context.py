import pytest

from reviewbench.manifest import digest
from reviewbench.report.context import attach_publications, publication_text, validate_context
from reviewbench.report.issues import issue_rows


def test_verified_publication_preserves_formal_capture_status():
    pair = {"provider_id": "reviewer", "case_id": "01", "condition": "code_only", "status": "timed_out",
            "pr_url": "https://github.com/owner/repo/pull/1", "head_sha": "a" * 40}
    body = '<h2><img src="https://example.test/a">Confidence 5/5</h2>Safe to merge.'
    entry = {"provider": "reviewer", "case": "01", "condition": "code_only", "pull": pair["pr_url"],
             "head": pair["head_sha"], "signal": "check:1", "check_title": "Review", "check_url": pair["pr_url"],
             "summaries": [{"body": body, "body_digest": digest(body.encode()), "url": pair["pr_url"]}]}
    attach_publications({"pairs": [pair]}, [entry])
    assert pair["status"] == "timed_out"
    assert publication_text(pair["publication"]["summaries"][0]["body"]) == "Confidence 5/5\n\nSafe to merge."
    with pytest.raises(ValueError, match="pinned review attempt"):
        attach_publications({"pairs": [pair]}, [dict(entry, head="b" * 40)])


def test_only_explicitly_grouped_additional_findings_share_a_row():
    pairs = {}
    for provider in ("first", "second"):
        claim = {"claim_id": provider, "decision": "confirmed_novel", "body": "**Same title**", "rationale": "",
                 "evidence": [object()]}
        pairs[provider] = {"provider_id": provider, "case_id": "01", "condition": "code_only", "repeat_index": 0,
                           "expected": [], "claims": [claim]}
    assert len(issue_rows(pairs, [])) == 2
    group = {"title": "Same verified issue", "basis": "Confirmed with a shared reproduction.",
             "claim_ids": ["first", "second"]}
    bundle = {"pairs": list(pairs.values()), "finding_groups": [group]}
    validate_context(bundle)
    row, = issue_rows(pairs, [group])
    assert row[2] == {"first": ["first"], "second": ["second"]}
    pairs["second"]["condition"] = "snapshot"
    with pytest.raises(ValueError, match="same case"):
        validate_context(bundle)
