import json
from pathlib import Path

from .github import PullRequest, ReviewObservation
from .manifest import ExperimentPair


class FixtureTransport:
    """An in-memory GitHub stand-in for independently authored pilot cases."""

    def __init__(self, cases: dict):
        self.cases = cases
        self.pulls: dict[tuple[str, str], PullRequest] = {}

    @classmethod
    def load(cls, path: Path):
        cases = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cases, dict):
            raise ValueError("Fixture must be a JSON object keyed by provider")
        return cls(cases)

    def find_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest | None:
        return self.pulls.get((pair.target.repository, head_ref))

    def create_pull(self, pair: ExperimentPair, head_ref: str) -> PullRequest:
        repository = pair.target.repository
        number = int(pair.pair_id[:12], 16) + 1
        pr = PullRequest(number, f"https://github.com/{repository}/pull/{number}", repository,
                         pair.variant.base_ref, head_ref, pair.variant.base_sha, pair.variant.head_sha)
        self.pulls[(repository, head_ref)] = pr
        return pr

    def get_pull(self, repository: str, number: int) -> PullRequest:
        return next(pr for pr in self.pulls.values() if pr.repository == repository and pr.number == number)

    def observe(self, pair: ExperimentPair, pr: PullRequest, started_at: float) -> ReviewObservation:
        messages = self.cases.get(pair.target.provider_id, [])
        if not isinstance(messages, list) or any(not isinstance(message, str) or not message.strip() for message in messages):
            raise ValueError("Fixture reviewer messages must be strings")
        claims = tuple(
            (f"fixture:{index}", f"{pr.url}#discussion_r{index}", "pagination.py", message)
            for index, message in enumerate(messages, 1)
        )
        return ReviewObservation("completed", "fixture.completed", claims, {"fixture": True, "comments": messages})
