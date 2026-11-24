import io
import json
from dataclasses import replace
from subprocess import CompletedProcess
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


def test_summary_only_completion_requires_check_on_same_pr_and_head(manifest_data):
    target = replace(ManifestLoader.parse(manifest_data).targets[0], completion_check="Reviewer check")
    pr = PullRequest(40, "https://github.com/team/one/pull/40", "team/one", "main", "siftbench/attempt", "a" * 40, "b" * 40)
    timestamp = "1970-01-01T00:01:45Z"
    check = {"id": 12, "name": target.completion_check, "app": {"slug": target.publisher.removesuffix("[bot]")},
             "head_sha": pr.head_sha, "pull_requests": [{"number": 39}], "status": "completed", "conclusion": "success",
             "started_at": timestamp, "completed_at": timestamp}
    summary = {"id": 20, "user": {"login": target.publisher}, "body": "Review complete. No findings.",
               "html_url": pr.url + "#issuecomment-20", "created_at": timestamp}
    unrelated_summary = {**summary, "id": 21, "user": {"login": "different[bot]"}}
    adapter = ReviewerAdapter(target)
    assert adapter.observe(pr, 100, [], [], [summary], [check], captured_at=110).state == "pending"
    check["pull_requests"] = [{"number": pr.number}]
    observations = adapter.observe(pr, 100, [], [], [summary, unrelated_summary], [check], captured_at=110)
    assert observations.state == "completed"
    assert observations.signal == "check:12"
    assert observations.claims == (("issue:20", summary["html_url"], None, summary["body"]),)


def test_live_preflight_only_reads_and_rejects_a_changed_base(manifest_data):
    class GitHubStub:
        base = "a" * 40

        def open(self, request, *, timeout):
            assert request.get_method() == "GET"
            path = urlsplit(request.full_url).path
            if path == "/repos/team/one":
                return Response({"full_name": "team/one", "private": True})
            if path == "/repos/team/one/git/ref/heads/main":
                return Response({"object": {"sha": self.base}})
            assert path == f"/repos/team/one/git/commits/{'b' * 40}"
            return Response({"sha": "b" * 40})

    manifest = ManifestLoader.parse(manifest_data)
    fake = GitHubStub()
    client = GitHubClient("temporary-test", manifest.allowed_repositories, opener=fake)
    assert client.check_input(manifest.pairs()[0]) == "/repos/team/one"
    fake.base = "c" * 40
    with pytest.raises(InvalidInput, match="pinned SHA"):
        client.check_input(manifest.pairs()[0])


def test_github_cli_transport_preserves_request_and_rate_limit(monkeypatch):
    outputs = iter([
        (0, b'HTTP/2.0 201 Created\r\nContent-Type: application/json\r\n\r\n{"number":4}'),
        (1, b'HTTP/2.0 429 Too Many Requests\nRetry-After: 17\n\n{}'),
    ])

    def run(command, *, input, **kwargs):
        assert command[:5] == ["gh", "api", "--hostname", "github.com", "--method"]
        assert "repos/team/one/pulls" in command
        if command[5] == "POST":
            assert command[-2:] == ["--input", "-"]
            assert json.loads(input) == {"title": "Review"}
        code, stdout = next(outputs)
        return CompletedProcess(command, code, stdout, b"")

    monkeypatch.setattr("reviewbench.github.subprocess.run", run)
    client = GitHubClient(None, ("team/one",))
    assert client._request("POST", "/repos/team/one/pulls", body={"title": "Review"})[0] == {"number": 4}
    with pytest.raises(RateLimited) as limited:
        client._request("GET", "/repos/team/one/pulls")
    assert limited.value.retry_after == 17
