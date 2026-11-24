import pytest


@pytest.fixture
def manifest_data():
    return {
        "run_id": "pilot", "allowed_repositories": ["team/one", "team/two"],
        "rubric_digest": "f" * 64, "timeout_seconds": 60, "settling_seconds": 2,
        "cases": [{"case_id": "pagination", "condition": "code_only", "title": "Page reports",
                   "body": "Review the pagination change.", "base_ref": "main", "base_sha": "a" * 40,
                   "head_sha": "b" * 40}],
        "targets": [{"provider_id": "one", "condition": "code_only", "repository": "team/one",
                     "publisher": "one[bot]"},
                    {"provider_id": "two", "condition": "code_only", "repository": "team/two",
                     "publisher": "two[bot]"}],
    }
