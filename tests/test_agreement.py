import json

import pytest

from reviewbench.agreement import cohen_kappa, compare, fleiss_kappa, interpretation, load_annotations


def test_kappa_is_one_for_identical_and_negative_for_opposed_annotators():
    assert cohen_kappa(["a", "b"], ["a", "b"]) == 1
    assert cohen_kappa(["a", "b"], ["b", "a"]) == -1
    assert fleiss_kappa([["a", "a"], ["b", "b"]]) == 1
    assert cohen_kappa(["a", "a"], ["a", "a"]) is None


def test_fleiss_kappa_needs_a_full_matrix_of_raters():
    assert fleiss_kappa([["a", "b", "a"], ["a", "a"]]) is None
    assert fleiss_kappa([]) is None
    value = fleiss_kappa([["a", "a", "a"], ["b", "b", "a"], ["a", "b", "b"], ["b", "b", "b"]])
    assert round(value, 3) == .314  # worked by hand: (0.667 - 0.514) / (1 - 0.514)


def test_interpretation_labels_follow_the_usual_bands():
    assert [interpretation(value) for value in (None, -.1, .1, .3, .5, .7, .9)] == [
        "Not defined", "Poor", "Slight", "Fair", "Moderate", "Substantial", "Almost perfect"]


def test_compare_lists_disagreements_and_checks_the_recorded_verdict():
    annotators = [
        {"name": "one", "kind": "ai", "decisions": {"x": "confirmed_novel", "y": "false_positive", "z": "out_of_scope"}},
        {"name": "two", "kind": "ai", "decisions": {"x": "confirmed_novel", "y": "confirmed_novel", "z": "out_of_scope"}},
        {"name": "three", "kind": "human", "decisions": {"x": "confirmed_novel", "y": "confirmed_novel", "z": "out_of_scope"}},
    ]
    result = compare(annotators, {"x": "confirmed_novel", "y": "confirmed_novel", "z": "unresolved"})
    assert result["claims"] == 3 and result["unanimous"] == 2 and result["kappa_method"] == "Fleiss"
    assert result["matches_recorded_verdict"] == 2
    assert result["disagreements"] == [
        {"claim_id": "y", "recorded": "confirmed_novel", "majority": "confirmed_novel", "annotators_split": True,
         "decisions": {"one": "false_positive", "two": "confirmed_novel", "three": "confirmed_novel"}},
        {"claim_id": "z", "recorded": "unresolved", "majority": "out_of_scope", "annotators_split": False,
         "decisions": {"one": "out_of_scope", "two": "out_of_scope", "three": "out_of_scope"}}]


def test_compare_rejects_a_single_annotator_and_unknown_kinds():
    one = {"name": "one", "kind": "ai", "decisions": {"x": "duplicate"}}
    with pytest.raises(ValueError):
        compare([one], {})
    with pytest.raises(ValueError):
        compare([one, {**one, "name": "two", "kind": "robot"}], {})


def test_load_annotations_rejects_unknown_decisions_and_repeats(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"claim_id": "x", "decision": "duplicate"}) + "\n", encoding="utf-8")
    assert load_annotations(good) == {"x": "duplicate"}
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"claim_id": "x", "decision": "great"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_annotations(bad)
    repeated = tmp_path / "repeat.jsonl"
    repeated.write_text((json.dumps({"claim_id": "x", "decision": "duplicate"}) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError):
        load_annotations(repeated)
