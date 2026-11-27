from pathlib import Path

import pytest

from reviewbench.grading import ScoreCalculator
from reviewbench.manifest import digest
from reviewbench.report import ReportBuilder, _link
from reviewbench.report.overlap import reference_overlap
from reviewbench.store import StoreError


def test_metrics_only_use_shared_completed_baseline_pairs():
    def pair(provider, case, status, *decisions, repeat=0):
        return {"provider_id": provider, "case_id": case, "condition": "code_only", "status": status,
                "repeat_index": repeat, "expected": [{"expected_id": "known"}],
                "claims": [{"decision": decision, "expected_id": "known" if decision == "confirmed_expected" else None}
                           for decision in decisions]}

    result = ScoreCalculator.calculate({"pairs": [
        pair("a", "shared", "completed", "confirmed_expected", "confirmed_expected", "duplicate",
             "confirmed_novel", "false_positive", "unresolved", "justified_context_request", None),
        pair("b", "shared", "completed"),
        pair("a", "blocked", "completed", "confirmed_expected"),
        pair("b", "blocked", "quota"),
        pair("a", "shared", "completed", "false_positive", repeat=1),
    ]})
    assert result["shared"] == [("shared", "code_only")]
    assert result["providers"]["a"] == {"matched": 1, "expected": 1, "adjudicated": 3, "novel": 1,
                                         "false_positives": 1, "duplicates": 1, "unresolved": 1,
                                         "justified_context_requests": 1, "ungraded": 1}
    assert result["providers"]["b"]["adjudicated"] == 0
    assert result["providers"]["b"]["expected"] == 1


def test_reference_overlap_counts_each_graded_reference_once():
    def pair(provider, case, *decisions, repeat=0):
        return {"provider_id": provider, "case_id": case, "condition": "code_only", "repeat_index": repeat,
                "expected": [{"expected_id": "known", "matching_criteria": "Known problem"}],
                "claims": [{"decision": decision, "expected_id": "known"} for decision in decisions]}

    providers = ["coderabbit", "greptile"]
    cases = [pair("coderabbit", "one", "confirmed_expected", "confirmed_expected"), pair("greptile", "one"),
             pair("coderabbit", "one", repeat=1), pair("greptile", "one", repeat=1),
             pair("coderabbit", "two", "confirmed_expected"), pair("greptile", "two", "confirmed_expected"),
             pair("coderabbit", "pending", "unresolved"), pair("greptile", "pending"),
             pair("coderabbit", "outside"), pair("greptile", "outside")]
    samples = {("one", "code_only", 0), ("one", "code_only", 1), ("two", "code_only", 0), ("pending", "code_only", 0)}

    html = reference_overlap(cases, samples, providers)
    assert html.startswith('<details class="additional-scores interval-details" id="reference-overlap">')
    assert "3 reference findings" in html
    assert all(f'>{label}</span>' in html for label in ("All 2 reviewers", "CodeRabbit only", "Detected by no reviewer"))
    assert html.count('value="1"') == 3
    assert 'href="#problems-one:code_only:1"' in html
    assert "problems-pending" not in html and "problems-outside" not in html


def test_untrusted_report_rejects_bad_identifiers_and_evidence(tmp_path):
    builder = ReportBuilder()
    with pytest.raises(ValueError, match="run_id"):
        builder.write({"schema_version": 1, "fixture": True, "run_id": "<img>", "pairs": []}, tmp_path)
    bundle = {"schema_version": 1, "fixture": True, "run_id": "pilot", "pairs": [
        {"case_id": "sample", "condition": "code_only", "repeat_index": 0, "status": "completed",
         "provider_id": "reviewer", "pair_id": "a" * 64, "expected": [], "claims": [
            {"claim_id": "b" * 64, "body": "condition", "decision": "confirmed_novel", "rationale": "test",
             "evidence": [{"type": "test", "text": "changed", "digest": digest(b"original")}]}]},
    ]}
    with pytest.raises(StoreError, match="digest mismatch"):
        builder.write(bundle, tmp_path)
    assert not list(tmp_path.iterdir())


def test_report_link_boundaries_and_packaged_palette():
    assert _link("https://github.com/team/repo/pull/1#discussion_r2")
    assert _link("https://github.com@other.example/pull/1") is None
    assert _link("javascript:alert(1)") is None
    theme = Path(__file__).resolve().parents[1] / "theme.css"
    packaged = theme.parent / "src" / "reviewbench" / "report" / "theme.css"
    assert packaged.read_bytes() == theme.read_bytes()
