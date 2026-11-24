from dataclasses import dataclass
from typing import Protocol

from .manifest import ExperimentPair


class RemoteError(RuntimeError):
    """A remote action failed without proving that a write did not happen."""


class InvalidInput(RemoteError):
    """The remote revisions or repository do not match the experiment."""


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
