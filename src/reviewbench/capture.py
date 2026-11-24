from .github import PullRequest, Transport
from .manifest import ExperimentPair, canonical_json
from .store import RunStore


class ReviewCollector:
    def __init__(self, store: RunStore, transport: Transport):
        self.store = store
        self.transport = transport

    def collect(self, pair: ExperimentPair, attempt, pr: PullRequest, now: float, settling: int) -> None:
        review = self.transport.observe(pair, pr, attempt["started_at"])
        attempt_id = attempt["attempt_id"]
        if review.state in {"quota", "failed"}:
            self.store.transition(attempt_id, review.state, now, review.signal or review.state)
        elif review.state == "completed" and review.signal:
            observed = self.store.first_completion(attempt_id, now)
            if now >= observed + settling:
                unique_claims = tuple(dict((claim[0], claim) for claim in review.claims).values())
                self.store.freeze_capture(attempt_id, pr.head_sha, review.signal, now,
                                          canonical_json(review.raw), list(unique_claims))
