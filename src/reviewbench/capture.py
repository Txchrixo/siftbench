from collections.abc import Callable

from .github import InvalidInput, PullRequest, RemoteError, Transport
from .manifest import ExperimentPair, canonical_json
from .store import RunStore


class ReviewCollector:
    def __init__(self, store: RunStore, transport: Transport):
        self.store = store
        self.transport = transport

    def collect(self, pair: ExperimentPair, attempt, pr: PullRequest, clock: Callable[[], float], settling: int) -> None:
        attempt_id = attempt["attempt_id"]
        deadline = attempt["deadline_at"]
        observed = attempt["first_completion_at"]
        candidate = self.store.load_candidate(attempt_id) if observed is not None else None
        try:
            review = self.transport.observe(pair, pr, attempt["review_started_at"])
        except InvalidInput:
            raise
        except RemoteError:
            if candidate is None or clock() < observed + settling:
                raise
            self._freeze(attempt_id, pr, clock(), candidate)
            return
        now = clock()
        published = review.published_at if review.published_at is not None else now
        if published <= deadline and (candidate is None or candidate["signal"] == review.signal):
            if review.state == "completed" and review.signal:
                candidate = {"signal": review.signal, "raw": review.raw,
                             "claims": list({claim[0]: claim for claim in review.claims}.values())}
                observed = self.store.save_candidate(attempt_id, now, canonical_json(candidate))
            elif candidate is None and review.state in {"quota", "failed"}:
                self.store.transition(attempt_id, review.state, now, review.signal or review.state)
                return
        if candidate is not None:
            if now >= observed + settling:
                self._freeze(attempt_id, pr, now, candidate)
        elif now >= deadline:
            self.store.transition(attempt_id, "timed_out", now, "Capture deadline elapsed")

    def _freeze(self, attempt_id: str, pr: PullRequest, now: float, candidate: dict) -> None:
        self.store.freeze_capture(attempt_id, pr.head_sha, candidate["signal"], now,
                                  canonical_json(candidate["raw"]), candidate["claims"])
