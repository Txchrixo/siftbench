import json
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .manifest import ExperimentPair, canonical_json


class RemoteError(RuntimeError):
    """A remote action failed without proving that a write did not happen."""


class InvalidInput(RemoteError):
    """The remote revisions or repository do not match the experiment."""


class NotFound(RemoteError):
    """The specified GitHub resource is unavailable to this client."""


class RateLimited(RemoteError):
    def __init__(self, retry_after: float):
        super().__init__("GitHub API rate limit")
        self.retry_after = retry_after


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    repository: str
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class ReviewObservation:
    state: str
    signal: str | None
    claims: tuple[tuple[str, str, str | None, str], ...]
    raw: object


class Transport(Protocol):
    def find_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest | None: ...
    def create_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest: ...
    def get_pull(self, repository: str, number: int) -> PullRequest: ...
    def observe(self, pair: ExperimentPair, pr: PullRequest, started_at: float) -> ReviewObservation: ...


def verify_pull(pair: ExperimentPair, pr: PullRequest, head_ref: str) -> None:
    case = pair.variant
    if (pr.repository, pr.base_ref, pr.head_ref, pr.base_sha, pr.head_sha) != (
        pair.target.repository, case.base_ref, head_ref, case.base_sha, case.head_sha
    ):
        raise InvalidInput("Pull request does not match the pinned repository, refs, and SHAs")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, token: str, allowed_repositories: tuple[str, ...], *, opener=None):
        if not token or not token.strip() or any(char.isspace() for char in token):
            raise ValueError("Provide a GitHub token through SIFTBENCH_GITHUB_TOKEN or GH_TOKEN")
        self._token = token
        self._allowed = frozenset(allowed_repositories)
        self._private: set[str] = set()
        self._opener = opener or build_opener(_NoRedirect())

    def _request(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None):
        url = "https://api.github.com" + path
        if query:
            url += "?" + urlencode(query)
        request = Request(url, data=canonical_json(body) if body is not None else None, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
            "User-Agent": "SiftBench",
        })
        try:
            with self._opener.open(request, timeout=15) as response:
                data = response.read(8_388_609)
                if len(data) > 8_388_608:
                    raise RemoteError("GitHub response exceeded the capture size limit")
                return json.loads(data), response.headers
        except HTTPError as error:
            try:
                self._raise_http(error)
            finally:
                error.close()
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise RemoteError("GitHub request failed or response was invalid") from error

    @staticmethod
    def _raise_http(error: HTTPError) -> None:
        headers = error.headers or {}
        if error.code == 429 or (error.code == 403 and (
                headers.get("Retry-After") is not None or headers.get("X-RateLimit-Remaining") == "0")):
            retry = headers.get("Retry-After")
            until = headers.get("X-RateLimit-Reset")
            delay = 60.0
            try:
                if retry is not None:
                    try:
                        delay = float(retry)
                    except ValueError:
                        delay = parsedate_to_datetime(retry).timestamp() - time.time()
                elif until is not None:
                    delay = float(until) - time.time()
            except (ValueError, TypeError, OverflowError, OSError):
                pass
            from math import isfinite

            raise RateLimited(max(1, delay) if isfinite(delay) else 60) from error
        if error.code == 404:
            raise NotFound("GitHub resource not found or not accessible") from error
        if error.code in (401, 403) or 300 <= error.code < 400:
            raise InvalidInput(f"GitHub refused the authorized operation (HTTP {error.code})") from error
        raise RemoteError(f"GitHub request failed (HTTP {error.code})") from error

    def _list(self, path: str, query: dict | None = None, *, object_key: str | None = None) -> list[dict]:
        result = []
        for page in range(1, 101):
            body, headers = self._request("GET", path, query={**(query or {}), "page": page, "per_page": 100})
            items = body.get(object_key) if object_key and isinstance(body, dict) else body
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise RemoteError("Unexpected GitHub listing format")
            result.extend(items)
            # Increment the page on the same validated endpoint. Never follow an arbitrary URL.
            if not re.search(r'\brel="next"', headers.get("Link", "")):
                return result
        raise RemoteError("GitHub listing exceeded the pagination limit")

    def _repository(self, repository: str, *, refresh: bool = False) -> str:
        if repository not in self._allowed:
            raise InvalidInput("Repository is outside the allowlist")
        path = f"/repos/{repository}"
        if refresh or repository not in self._private:
            body, _ = self._request("GET", path)
            if (not isinstance(body, dict) or body.get("private") is not True
                    or str(body.get("full_name", "")).lower() != repository or body.get("archived")):
                raise InvalidInput("Expected an accessible private test repository")
            self._private.add(repository)
        return path

    @staticmethod
    def _parse_pull(data: dict, repository: str) -> PullRequest:
        try:
            base, head = data["base"], data["head"]
            number = data["number"]
            if (type(number) is not int or number < 1 or base["repo"]["full_name"].lower() != repository
                    or head["repo"]["full_name"].lower() != repository):
                raise InvalidInput("Pull request references another repository")
            return PullRequest(number, f"https://github.com/{repository}/pull/{number}", repository,
                               base["ref"], head["ref"], base["sha"], head["sha"])
        except (KeyError, TypeError, AttributeError) as error:
            raise RemoteError("Unexpected GitHub pull request format") from error

    def find_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest | None:
        repo = pair.target.repository
        path = self._repository(repo)
        candidates = self._list(path + "/pulls", {"state": "all", "base": pair.variant.base_ref,
                                                  "head": f"{repo.split('/')[0]}:{head_ref}"})
        pulls = [self._parse_pull(item, repo) for item in candidates]
        matches = [pr for pr in pulls if pr.head_ref == head_ref and pr.base_ref == pair.variant.base_ref]
        if len(matches) > 1:
            raise InvalidInput("More than one PR matches the attempt branch and base")
        return matches[0] if matches else None

    def _branch_sha(self, path: str, ref: str) -> str:
        data, _ = self._request("GET", f"{path}/git/ref/heads/{quote(ref, safe='/')}")
        try:
            return data["object"]["sha"]
        except (KeyError, TypeError) as error:
            raise RemoteError("Unexpected GitHub reference format") from error

    def create_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest:
        repo, case = pair.target.repository, pair.variant
        path = self._repository(repo, refresh=True)
        if self._branch_sha(path, case.base_ref) != case.base_sha:
            raise InvalidInput("Base branch no longer points at the pinned SHA")
        try:
            self._request("GET", f"{path}/git/commits/{case.head_sha}")
        except NotFound as error:
            raise InvalidInput("Pinned head is missing from the test repository. Import the pilot commit first.") from error
        try:
            current = self._branch_sha(path, head_ref)
        except NotFound:
            data, _ = self._request("POST", path + "/git/refs", body={"ref": f"refs/heads/{head_ref}", "sha": case.head_sha})
            try:
                current = data["object"]["sha"]
            except (KeyError, TypeError) as error:
                raise RemoteError("Could not verify the new head branch") from error
        if current != case.head_sha or self._branch_sha(path, case.base_ref) != case.base_sha:
            raise InvalidInput("Branch revisions changed before creating the PR")
        data, _ = self._request("POST", path + "/pulls", body={"title": case.title, "body": case.body,
                                                              "base": case.base_ref, "head": head_ref, "draft": False})
        return self._parse_pull(data, repo)

    def get_pull(self, repository: str, number: int) -> PullRequest:
        path = self._repository(repository)
        data, _ = self._request("GET", f"{path}/pulls/{number}")
        return self._parse_pull(data, repository)

    def verify_trigger_comment(self, pr: PullRequest, comment_id: int, started_at: float) -> tuple[str, float]:
        from .providers import _timestamp

        path = self._repository(pr.repository)
        data, _ = self._request("GET", f"{path}/issues/comments/{comment_id}")
        expected = f"https://api.github.com{path}/issues/{pr.number}"
        timestamp = _timestamp(data.get("created_at")) if isinstance(data, dict) else None
        if timestamp is None or timestamp < int(started_at) or str(data.get("issue_url", "")).lower() != expected.lower():
            raise InvalidInput("Trigger comment does not belong to this attempt's PR and time window")
        return f"{pr.url}#issuecomment-{comment_id}", timestamp

    def observe(self, pair: ExperimentPair, pr: PullRequest, started_at: float) -> ReviewObservation:
        from .providers import ReviewerAdapter

        path = self._repository(pr.repository)
        pull_path = f"{path}/pulls/{pr.number}"
        reviews = self._list(pull_path + "/reviews")
        comments = self._list(pull_path + "/comments")
        summaries, checks = [], []
        if pair.target.completion_check:
            summaries = self._list(f"{path}/issues/{pr.number}/comments")
            checks = self._list(f"{path}/commits/{pr.head_sha}/check-runs", {"filter": "all"}, object_key="check_runs")
        return ReviewerAdapter(pair.target).observe(pr, started_at, reviews, comments, summaries, checks,
                                                   captured_at=time.time())
