import io
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from reviewbench.github import GitHubClient, InvalidInput, PullRequest, RateLimited
from reviewbench.manifest import ManifestLoader
from reviewbench.providers import ReviewerAdapter


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(json.dumps(data).encode())
        self.headers = headers or {}


def test_private_repositories_and_pagination(manifest_data):
    class GitHubStub:
        def open(self, request, *, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.netloc == "api.github.com"
            if parsed.path == "/repos/team/one":
                return Response({"full_name": "Team/One", "private": True})
            if parsed.path == "/repos/team/two":
                return Response({"full_name": "Team/Two", "private": False})
            assert parsed.path == "/repos/team/one/pulls"
            if "page=1&" in parsed.query:
                return Response([], {"Link": '<https://example.invalid/ignored>; rel="next"'})
            assert "page=2&" in parsed.query
            return Response([])

    manifest = ManifestLoader.parse(manifest_data)
    client = GitHubClient("ephemeral-test", manifest.allowed_repositories, opener=GitHubStub())
    one, two = manifest.pairs()
    assert client.find_pull(one, "siftbench/attempt") is None
    with pytest.raises(InvalidInput, match="private"):
        client.find_pull(two, "siftbench/attempt")
    with pytest.raises(InvalidInput, match="allowlist"):
        client.get_pull("external/other", 42)


def test_rate_limit_is_distinguished_from_access_denial(manifest_data):
    class GitHubStub:
        headers = {"Retry-After": "25"}

        def open(self, request, *, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", self.headers, io.BytesIO())

    manifest = ManifestLoader.parse(manifest_data)
    fake = GitHubStub()
    client = GitHubClient("ephemeral-test", manifest.allowed_repositories, opener=fake)
    with pytest.raises(RateLimited) as limited:
        client.get_pull("team/one", 1)
    assert limited.value.retry_after == 25
    fake.headers = {}
    with pytest.raises(InvalidInput, match="403"):
        client.get_pull("team/one", 1)


def test_completion_requires_correct_publisher_and_head(manifest_data):
    target = ManifestLoader.parse(manifest_data).targets[0]
    pr = PullRequest(40, "https://github.com/team/one/pull/40", "team/one", "main", "siftbench/attempt", "a" * 40, "b" * 40)
    review = {"id": 1, "submitted_at": "1970-01-01T00:01:45Z", "state": "COMMENTED", "body": "",
              "user": {"login": "different[bot]"}, "commit_id": pr.head_sha}
    adapter = ReviewerAdapter(target)
    assert adapter.observe(pr, 100, [review], [], [], [], captured_at=110).state == "pending"
    review["user"]["login"] = target.publisher
    finished = adapter.observe(pr, 100, [review], [], [], [], captured_at=110)
    assert finished.state == "completed" and finished.claims == ()
    review["commit_id"] = pr.base_sha
    assert adapter.observe(pr, 100, [review], [], [], [], captured_at=110).state == "pending"
