import json

import pytest

from reviewbench.cli import main
from reviewbench.fixture import FixtureTransport
from reviewbench.grading import ScoreCalculator, VerdictImporter, assessments_for_run, build_bundle, claims_for_grading
from reviewbench.manifest import ManifestLoader, canonical_json, digest
from reviewbench.runner import CampaignRunner
from reviewbench.store import RunStore, StoreError


def test_fixture_replay_from_capture_through_report(tmp_path, manifest_data):
    rubric = tmp_path / "rubric.json"
    rubric.write_bytes(canonical_json({"expected": [{"case_id": "pagination", "condition": "code_only",
                                                       "expected_id": "same-time", "matching_criteria": "Rows share a timestamp"}]}))
    manifest_data.update(rubric_digest=digest(rubric.read_bytes()), settling_seconds=0)
    manifest = ManifestLoader.parse(manifest_data)
    state = tmp_path / "state"
    store = RunStore(state)
    transport = FixtureTransport({"one": ["A row with the same timestamp would be skipped. <script>"], "two": []})
    statuses = CampaignRunner(store, transport).step(manifest)
    assert [row["status"] for row in statuses] == ["completed", "completed"]
    assert len(transport.pulls) == 2
    assert CampaignRunner(RunStore(state), transport).step(manifest) == statuses
    assert len(transport.pulls) == 2

    before_grading = tmp_path / "before-grading"
    assert main(["--state-dir", str(state), "report", "--run-id", "pilot", "--out", str(before_grading)]) == 0
    before_html = (before_grading / "report.html").read_text(encoding="utf-8")
    before_metrics = before_html.split('id="operational-metrics"', 1)[1].split("</section>", 1)[0]
    assert "Grading incomplete" in before_metrics and "0 of 1 completed PR graded" in before_metrics
    assert "<strong>0 false positives</strong><small>Across 1 graded PR</small>" in before_metrics
    assert "Diff not included" in before_html and "Awaiting verdict" in before_html
    before_md = (before_grading / "summary.md").read_text(encoding="utf-8")
    assert "False positives: Grading incomplete" in before_md
    assert "0 false positives (Across 1 graded PR)" in before_md

    claim, = claims_for_grading(store, manifest.run_id)
    assert "source_url" not in claim
    grades = tmp_path / "grades.jsonl"
    grades.write_bytes(canonical_json({"claim_id": claim["claim_id"], "decision": "confirmed_expected",
                                      "expected_id": "same-time", "rationale": "Only a timestamp is compared.",
                                      "evidence": [{"type": "test", "text": "Expected ID 42 on page two, got no rows."}]}) + b"\n")
    importer = VerdictImporter(store)
    importer.load(manifest.run_id, rubric, grades, grader_id="first")
    with pytest.raises(StoreError, match="two independent graders"):
        importer.load(manifest.run_id, rubric, grades, adjudicator_id="lead")
    importer.load(manifest.run_id, rubric, grades, grader_id="second")
    assert len(assessments_for_run(store, manifest.run_id)) == 2
    assert build_bundle(store, manifest.run_id)["pairs"][0]["claims"][0]["decision"] is None
    importer.load(manifest.run_id, rubric, grades, adjudicator_id="lead")
    importer.load(manifest.run_id, rubric, grades, adjudicator_id="lead")
    bundle = build_bundle(store, manifest.run_id)
    assert bundle["pairs"][0]["claims"][0]["revision"] == 1
    assert bundle["pairs"][0]["claims"][0]["adjudicator_id"] == "lead"
    results = ScoreCalculator.calculate(bundle)["providers"]
    assert (results["one"]["matched"], results["one"]["expected"], results["two"]["matched"]) == (1, 1, 0)

    first = tmp_path / "first"
    assert main(["--state-dir", str(state), "report", "--run-id", "pilot", "--out", str(first)]) == 0
    html = (first / "report.html").read_text(encoding="utf-8")
    assert "Fixture run" in html and "&lt;script&gt;" in html and html.count("<script>") == 1
    operations = html.split('id="operational-metrics"', 1)[1].split("</section>", 1)[0]
    assert operations.count("<strong>Not measured</strong>") == 1
    assert "Grading incomplete" not in operations
    assert 'aria-label="Metrics by reviewer"' in operations
    assert "#discussion_r1" in html and "Expected ID 42" in html
    assert f'Pinned head <code>{manifest.cases[0].head_sha}</code>' in html
    assert f'Base <code>{manifest.cases[0].base_sha}</code>' in html
    preview = html.split('<textarea id="benchmark-prompt"', 1)[1].split('</textarea>', 1)[0]
    assert "2 of 2 review attempts completed" in preview and "This report uses fixture data" in preview
    assert "A row with the same timestamp" not in preview and "Expected ID 42" not in preview
    assert manifest.cases[0].head_sha not in preview and "https://" not in preview
    assert json.loads((first / "results.json").read_bytes()) == bundle
    sharing = tmp_path / "public.json"
    assert main(["export", "--bundle", str(first / "results.json"), "--out", str(sharing), "--visibility", "public"]) == 0
    public = sharing.read_text(encoding="utf-8")
    assert '"metric_availability"' in public and '"not_measured"' in public
    assert "https://" not in public and "A row with the same timestamp" not in public and "Expected ID 42" not in public
    assert "adjudicator" not in public and "reviewer-1" in public and manifest.cases[0].head_sha not in public
    assert main(["compare-runs", "--before", str(first / "results.json"), "--after", str(first / "results.json"),
                 "--out", str(tmp_path / "compare")]) == 0
    comparison = (tmp_path / "compare" / "comparison.html").read_text(encoding="utf-8")
    assert comparison.count("1 / 1 matched completions") == 2
    assert "Only status changes" in comparison
    assert comparison.count(manifest.cases[0].title) == 2
    replay = tmp_path / "replay"
    assert main(["report", "--bundle", str(first / "results.json"), "--out", str(replay)]) == 0
    for name in ("report.html", "summary.md", "results.json"):
        assert (first / name).read_bytes() == (replay / name).read_bytes()

    for pair in bundle["pairs"]:
        del pair["base_sha"], pair["head_sha"]
    legacy = tmp_path / "legacy.json"
    legacy.write_bytes(canonical_json(bundle))
    assert main(["report", "--bundle", str(legacy), "--out", str(tmp_path / "legacy")]) == 0

    with store.connect() as db:
        db.execute("UPDATE review_capture SET observed_head_sha = ?", ("c" * 40,))
    with pytest.raises(StoreError, match="does not match the pinned commit"):
        build_bundle(store, manifest.run_id)
