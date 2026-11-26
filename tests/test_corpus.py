import json
from dataclasses import replace

import pytest

from reviewbench.analysis import campaign_metrics, compare_campaigns
from reviewbench.cli import main
from reviewbench.discovery import discover, git, license_at
from reviewbench.fixture import FixtureTransport
from reviewbench.grading import ScoreCalculator, VerdictImporter, build_bundle
from reviewbench.manifest import ManifestLoader, canonical_json
from reviewbench.planning import estimate
from reviewbench.preparation import _check_cross_case, draft_rubric, prepare_repos
from reviewbench.providers import check_matches_pull
from reviewbench.report import ReportBuilder
from reviewbench.report.context import attach_corpus_changes
from reviewbench.runner import CampaignRunner
from reviewbench.store import RunStore


AUTHORS = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
MIT = "Permission is hereby granted, free of charge, to any person. The software is provided as is."
PROVIDERS = ("coderabbit", "greptile", "proofline")


def commit(path, message):
    git(path, "add", "--all")
    git(path, "commit", "-m", message, extra_env=AUTHORS)
    return git(path, "rev-parse", "HEAD").stdout.decode().strip()


def repository(root, name, license_text):
    path = root / name
    git(root, "init", "--template=", "--initial-branch=main", str(path))
    (path / "LICENSE").write_text(license_text, encoding="utf-8")
    file = path / "reports.py"
    file.write_text("count = 0\n", encoding="utf-8")
    base = commit(path, "Initial snapshot")
    (path / "settings.ts").write_text("export const label = 'report';\n" * 20, encoding="utf-8")
    clean = commit(path, "Report labels")
    git(path, "checkout", "--detach", base)
    file.write_text("count = 0\n" + "# report row\n" * 20 + "count = -1\n", encoding="utf-8")
    head = commit(path, "Count report rows")
    file.write_text("count = 0\n" + "# report row\n" * 20 + "count = 1\n", encoding="utf-8")
    fix = commit(path, "Fix negative count\n\nFollow-up to #1")
    return {"repository": f"example/{name}", "path": name, "pulls": [
        {"number": 1, "title": "Count report rows", "base_sha": base, "head_sha": head, "merge_sha": head},
        {"number": 2, "title": "Report labels", "base_sha": base, "head_sha": clean, "kind": "clean"},
    ]}, fix


@pytest.fixture
def corpus(tmp_path):
    good, fix = repository(tmp_path, "good", MIT)
    bad, _ = repository(tmp_path, "bad", "GNU GENERAL PUBLIC LICENSE, version 3")
    tiny = good["pulls"][0] | {"number": 3, "base_sha": good["pulls"][0]["head_sha"], "head_sha": fix}
    sources = {"repositories": [good | {"pulls": good["pulls"] + [tiny, good["pulls"][0]]}, bad]}
    catalog, report = discover(sources, tmp_path, target=2, min_languages=2)
    rubric = draft_rubric(catalog, sources, tmp_path)
    reference = next(case for case in rubric["cases"] if case["kind"] == "reference")
    assert reference["fix_sha"] == fix
    rubric["expected"] = [{"case_id": reference["case_id"], "condition": "code_only", "expected_id": "negative-count",
                           "matching_criteria": "Identify the negative count returned by the change", "severity": "medium"}]
    targets = [{"provider_id": provider, "publisher": f"{provider}[bot]", "repository": f"owner/{provider}",
                "isolation_group": "example-good", "condition": "code_only"} for provider in PROVIDERS]
    return sources, catalog, rubric, targets, report


def approve(rubric):
    for case in rubric["cases"]:
        case.update(approved_by="operator", approved_at="2026-11-25T12:00:00+01:00")


def test_explicit_license_alternatives_require_both_permissive_files(tmp_path):
    git(tmp_path, "init", "--template=", "--initial-branch=main")
    (tmp_path / "LICENSE").write_text(
        "This software is made available under the terms of *either* of the licenses\n"
        "found in LICENSE.APACHE or LICENSE.BSD. Contributions to this software is made\n"
        "under the terms of *both* these licenses.\n", encoding="utf-8",
    )
    (tmp_path / "LICENSE.APACHE").write_text("Apache License\nVersion 2.0, January 2004", encoding="utf-8")
    bsd = tmp_path / "LICENSE.BSD"
    bsd.write_text("Redistributions of source code must retain notices. Redistributions in binary form.", encoding="utf-8")
    assert license_at(tmp_path, commit(tmp_path, "Permissive alternatives")) == ("BSD-2-Clause", "LICENSE.BSD")
    bsd.write_text("GNU General Public License", encoding="utf-8")
    with pytest.raises(ValueError, match="Non-permissive"):
        license_at(tmp_path, commit(tmp_path, "Mismatched alternatives"))


def test_root_mit_license_filename_still_requires_permissive_text(tmp_path):
    git(tmp_path, "init", "--template=", "--initial-branch=main")
    license_file = tmp_path / "MIT-LICENSE"
    license_file.write_text(MIT, encoding="utf-8")
    assert license_at(tmp_path, commit(tmp_path, "MIT license")) == ("MIT", "MIT-LICENSE")
    license_file.write_text("GNU General Public License", encoding="utf-8")
    with pytest.raises(ValueError, match="Non-permissive"):
        license_at(tmp_path, commit(tmp_path, "License changed"))


def test_discovery_filters_and_summarizes(corpus, tmp_path):
    sources, catalog, _, _, report = corpus
    assert {case["language"] for case in catalog["cases"]} == {"Python", "TypeScript"}
    assert all(case["license"] == "MIT" and case["base_sha"] in case["license_url"] for case in catalog["cases"])
    assert "Non-permissive" in report and "Outside size limits" in report and "Duplicate source PR" in report
    capped, _ = discover(sources, tmp_path, target=2, min_languages=2, project_cap=1)
    assert len(capped["cases"]) == 1
    assert capped["selection"]["deficits"]


def test_prepare_blocks_approval_and_leaks(corpus, tmp_path):
    sources, catalog, rubric, targets, _ = corpus
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="approved_by"):
        prepare_repos(catalog, targets, canonical_json(rubric), sources, tmp_path, output, run_id="fixture", repeats=3)
    approve(rubric)
    reference = next(case for case in catalog["cases"] if case["kind"] == "reference")
    reference["title"] = rubric["expected"][0]["matching_criteria"]
    with pytest.raises(ValueError, match="exposes the rubric"):
        prepare_repos(catalog, targets, canonical_json(rubric), sources, tmp_path, output, run_id="fixture", repeats=3)
    assert not output.exists()


def test_revert_may_restore_its_own_base_but_cannot_leak_into_another_case(tmp_path):
    source, _ = repository(tmp_path, "revert", MIT)
    repo = tmp_path / source["path"]
    original = source["pulls"][0]
    git(repo, "checkout", "--detach", original["head_sha"])
    (repo / "reports.py").write_text("count = 0\n", encoding="utf-8")
    restored = commit(repo, "Revert regression")
    case = original | {"case_id": "regression", "fix_sha": restored}
    _check_cross_case([case], repo, tmp_path)
    other = {"case_id": "other", "base_sha": restored, "head_sha": restored}
    with pytest.raises(ValueError, match="exposes the correction"):
        _check_cross_case([case, other], repo, tmp_path)


def test_local_corpus_pipeline_and_selected_resume(corpus, tmp_path, monkeypatch, capsys):
    sources, catalog, rubric, targets, _ = corpus
    approve(rubric)
    raw = canonical_json(rubric)
    output = tmp_path / "prepared"
    result = prepare_repos(catalog, targets, raw, sources, tmp_path, output, run_id="fixture", repeats=3)
    manifest = ManifestLoader.load(output / "campaign.json")
    assert result["attempts"] == 18
    assert estimate(manifest, {"coderabbit": {"reviews_available": 5, "cost_minor_per_review": 10,
                                               "currency": "USD"}}, 4)["reviewers"][0]["estimated_cost_minor"] == 60
    plan = estimate(manifest, {"coderabbit": {"reviews_available": 5}}, 4)
    assert plan["parallel_full_window_seconds"] == 3660
    assert plan["reviewers"][0]["warnings"]
    repository = output / "owner/coderabbit.git"
    source = tmp_path / "good"
    for case in manifest.cases:
        assert len(set(case.repeat_heads)) == 3
        assert git(repository, "rev-parse", case.base_ref).stdout.strip().decode() == case.base_sha
        diff_args = ("diff", "--no-ext-diff", "--no-renames")
        original = git(source, *diff_args, case.provenance["base_sha"], case.provenance["head_sha"]).stdout
        for head in case.repeat_heads:
            assert git(repository, *diff_args, case.base_sha, head).stdout == original
        assert git(repository, "cat-file", "-e", case.provenance["head_sha"], check=False).returncode
    fix = next(case["fix_sha"] for case in rubric["cases"] if "fix_sha" in case)
    assert git(repository, "cat-file", "-e", fix, check=False).returncode
    assert not git(repository, "remote").stdout
    assert "example-good" not in git(repository, "show-ref").stdout.decode()

    store = RunStore(tmp_path / "state")
    transport = FixtureTransport({})
    monkeypatch.setattr("reviewbench.cli._live_client", lambda _: transport)
    pilot = manifest.pairs()[0]
    selected = tmp_path / "pilot.json"
    selected.write_bytes(canonical_json([pilot.pair_id]))
    live = ["--state-dir", str(store.root), "run", "--manifest", str(output / "campaign.json"),
            "--execute", "--once", "--attempts", str(selected), "--max-attempts", "1"]
    assert main(live) == 0
    assert main(live) == 0
    assert len(transport.pulls) == 1
    assert capsys.readouterr().err == ""
    runner = CampaignRunner(store, transport, execution_mode="github")
    assert len(runner.step(manifest)) == 18
    assert len(transport.pulls) == 18
    pr = transport.find_pull(pilot, f"siftbench/{next(row['attempt_id'] for row in store.statuses('fixture') if row['pair_id'] == pilot.pair_id)}")
    check = {"app": {"slug": pilot.target.publisher.removesuffix("[bot]")}, "head_sha": pr.head_sha,
             "pull_requests": [{"number": pr.number}]}
    assert check_matches_pull(check, pr, pilot.target.publisher)
    second = next(pair for pair in manifest.pairs() if pair.target == pilot.target and pair.variant == pilot.variant and pair.repeat_index == 1)
    assert not check_matches_pull(check, replace(pr, head_sha=second.head_sha), pilot.target.publisher)

    rubric_file, empty = tmp_path / "rubric.json", tmp_path / "empty.jsonl"
    rubric_file.write_bytes(raw)
    empty.touch()
    VerdictImporter(store).load("fixture", rubric_file, empty)
    bundle = build_bundle(store, "fixture")
    attach_corpus_changes(bundle, {"example/good": (source, [])})
    assert len(bundle["changes"]) == 6
    assert bundle["scoring_policy_version"] == 2
    ReportBuilder().write(bundle, tmp_path / "report")
    rendered = (tmp_path / "report/report.html").read_text(encoding="utf-8")
    assert all(text in rendered for text in ("Report labels", "Source PR #2", "Attempt outcomes", "whole PRs"))


def test_repeated_scores_use_fully_graded_matches_and_resample_prs():
    pairs = []
    for case in range(6):
        for repeat in range(3):
            for provider in PROVIDERS:
                expected_hits = [case % 2 == 0, repeat < 2, True][PROVIDERS.index(provider)]
                status = "timed_out" if (case, repeat, provider) == (0, 2, "coderabbit") else "completed"
                decision = None if (case, repeat, provider) == (1, 2, "proofline") else (
                    "confirmed_expected" if expected_hits else "false_positive")
                pairs.append({"case_id": f"pr-{case}", "condition": "code_only", "repeat_index": repeat, "provider_id": provider,
                              "status": status, "expected": [{"expected_id": "bug"}],
                              "claims": [{"expected_id": "bug", "decision": decision}]})
    bundle = {"fixture": True, "scoring_policy_version": 2, "pairs": pairs}
    scores = ScoreCalculator.calculate(bundle)
    assert scores["scored_prs"] == 6
    assert len(scores["shared_samples"]) == 16
    assert scores["providers"]["greptile"]["matched"] == 12
    assert scores["providers"]["greptile"]["expected"] == 16
    assert scores["intervals"]["coderabbit"]["reference_recall"]
    assert scores["intervals"]["proofline"]["reference_recall"] is None
    assert scores == ScoreCalculator.calculate({"scoring_policy_version": 2, "pairs": list(reversed(pairs))})
    public = campaign_metrics(bundle)
    assert public["shared_graded_samples"] == 16
    assert "pr-0" not in json.dumps(public)
    assert "coderabbit" not in json.dumps(public)

    for pair in pairs:
        pair.update(input_digest="a" * 64, base_sha="b" * 40, head_sha="c" * 40)
    bundle.update(run_id="before", rubric_digest="d" * 64)
    compared = compare_campaigns(bundle, bundle | {"run_id": "after"})
    assert compared["scoring_policy_version"] == 2
    proofline = next(row for row in compared["reviewers"] if row["reviewer"] == "proofline")
    assert proofline["completed_in_both"] == 18
    assert proofline["graded_in_both"] == 17
    assert proofline["before"]["expected"] == 17
