import time
from collections.abc import Callable

from .capture import ReviewCollector
from .github import InvalidInput, RateLimited, RemoteError, Transport, verify_pull
from .manifest import ExperimentPair, Manifest
from .store import RunStore


class CampaignRunner:
    def __init__(self, store: RunStore, transport: Transport, clock: Callable[[], float] = time.time):
        self.store = store
        self.transport = transport
        self.clock = clock
        self.collector = ReviewCollector(store, transport)

    def step(self, manifest: Manifest) -> list[dict]:
        with self.store.lock(manifest.run_id):
            self.store.register(manifest)
            for pair in manifest.pairs():
                self._advance(manifest, pair)
        return self.store.statuses(manifest.run_id)

    def _advance(self, manifest: Manifest, pair: ExperimentPair) -> None:
        now = self.clock()
        attempt = self.store.attempt(pair, now, manifest.timeout_seconds)
        attempt_id = attempt["attempt_id"]
        state = attempt["status"]
        if state in {"completed", "timed_out", "quota", "invalid_input", "needs_attention", "failed"}:
            return
        if now >= attempt["deadline_at"]:
            self.store.transition(attempt_id, "timed_out", now, "Capture deadline elapsed")
            return
        if attempt["next_poll_at"] is not None and now < attempt["next_poll_at"]:
            return

        head_ref = f"siftbench/{attempt_id}"
        try:
            if state in {"planned", "preparing"}:
                if state == "planned":
                    self.store.transition(attempt_id, "preparing", now, "Preparing pinned PR")
                pr = self.transport.find_pull(pair, head_ref)
                if pr is None:
                    if not self.store.record_create_intent(attempt_id, head_ref):
                        self.store.transition(attempt_id, "needs_attention", now,
                                              "Prior PR creation could not be confirmed")
                        return
                    pr = self.transport.create_pull(pair, head_ref)
                verify_pull(pair, pr, head_ref)
                state = "awaiting_trigger" if pair.target.trigger == "manual" else "awaiting_review"
                self.store.attach_pr(attempt_id, pr.number, pr.url, state, now)
            elif state == "awaiting_review":
                pr = self.transport.get_pull(pair.target.repository, attempt["pr_number"])
                verify_pull(pair, pr, head_ref)
            else:
                return
            if state == "awaiting_review":
                self.collector.collect(pair, attempt, pr, self.clock(), manifest.settling_seconds)
        except InvalidInput as error:
            self.store.transition(attempt_id, "invalid_input", now, str(error))
        except RateLimited as error:
            self.store.schedule_poll(attempt_id, now + error.retry_after, str(error))
        except RemoteError as error:
            self.store.schedule_poll(attempt_id, now + 5, str(error))
