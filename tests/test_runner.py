from dataclasses import replace

import pytest

from reviewbench.fixture import FixtureTransport
from reviewbench.github import RateLimited, ReviewObservation
from reviewbench.manifest import ManifestLoader
from reviewbench.runner import CampaignRunner
from reviewbench.store import RunStore, StoreError


def test_resume_reconciles_a_pr_after_interruption(tmp_path, manifest_data):
    class InterruptedTransport(FixtureTransport):
        interrupted = False

        def create_pull(self, pair, head_ref):
            pull = super().create_pull(pair, head_ref)
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt
            return pull

    manifest_data["settling_seconds"] = 0
    manifest = ManifestLoader.parse(manifest_data)
    transport = InterruptedTransport({})
    with pytest.raises(KeyboardInterrupt):
        CampaignRunner(RunStore(tmp_path), transport).step(manifest)
    assert len(transport.pulls) == 1
    created = next(iter(transport.pulls.values()))
    persisted = RunStore(tmp_path)
    rows = CampaignRunner(persisted, transport).step(persisted.load_manifest("pilot"))
    assert len(transport.pulls) == 2
    assert rows[0]["pr_url"] == created.url
    assert all(row["status"] == "completed" for row in rows)
    with pytest.raises(StoreError, match="cannot mix"):
        persisted.register(manifest, "github")


def test_unconfirmed_write_requires_attention(tmp_path, manifest_data):
    store = RunStore(tmp_path)
    manifest = ManifestLoader.parse(manifest_data)
    store.register(manifest)
    pair = manifest.pairs()[0]
    attempt = store.attempt(pair, 100, manifest.timeout_seconds)
    store.transition(attempt["attempt_id"], "preparing", 100, "interrupted")
    store.record_create_intent(attempt["attempt_id"], f"siftbench/{attempt['attempt_id']}")
    transport = FixtureTransport({})
    result = CampaignRunner(store, transport, clock=lambda: 101).step(manifest)
    assert result[0]["status"] == "needs_attention"
    assert all(pr.repository != pair.target.repository for pr in transport.pulls.values())


def test_rate_limit_and_head_mismatch_do_not_score(tmp_path, manifest_data):
    class LimitedTransport(FixtureTransport):
        attempts = 0

        def find_pull(self, pair, head_ref):
            if pair.target.provider_id == "one" and not self.attempts:
                self.attempts += 1
                raise RateLimited(70)
            return super().find_pull(pair, head_ref)

        def create_pull(self, pair, head_ref):
            return replace(super().create_pull(pair, head_ref), head_sha="c" * 40)

    current = [100]
    transport = LimitedTransport({})
    runner = CampaignRunner(RunStore(tmp_path), transport, clock=lambda: current[0])
    manifest = ManifestLoader.parse(manifest_data)
    first = runner.step(manifest)
    assert [pair["status"] for pair in first] == ["preparing", "planned"]
    assert not transport.pulls
    current[0] = 150
    runner.step(manifest)
    assert transport.attempts == 1
    current[0] = 171
    final = runner.step(manifest)
    assert [pair["status"] for pair in final] == ["invalid_input", "invalid_input"]
    assert transport.attempts == 1


def test_on_time_publication_survives_settling_and_restart(tmp_path, manifest_data):
    class SettlingTransport(FixtureTransport):
        published = True

        def observe(self, pair, pr, started_at):
            if self.published:
                return replace(super().observe(pair, pr, started_at), published_at=159)
            return ReviewObservation("pending", None, (), {})

    manifest_data["settling_seconds"] = 10
    manifest = ManifestLoader.parse(manifest_data)
    transport = SettlingTransport({"one": ["A finding"]})
    store = RunStore(tmp_path)
    now = [100]
    runner = CampaignRunner(store, transport, clock=lambda: now[0])
    transport.published = False
    runner.step(manifest)
    now[0] = 159
    transport.published = True
    assert all(row["status"] == "awaiting_review" for row in runner.step(manifest))
    transport.published = False
    now[0] = 171
    resumed = CampaignRunner(RunStore(tmp_path), transport, clock=lambda: now[0]).step(manifest)
    assert all(row["status"] == "completed" for row in resumed)
    assert len(transport.pulls) == 2


@pytest.mark.parametrize("published, expected", [(159, "completed"), (161, "timed_out")])
def test_last_poll_uses_source_publication_time(tmp_path, manifest_data, published, expected):
    class LatePoll(FixtureTransport):
        def observe(self, pair, pr, started_at):
            if now[0] < published:
                return ReviewObservation("pending", None, (), {})
            return replace(super().observe(pair, pr, started_at), published_at=published)

    manifest_data["settling_seconds"] = 0
    now = [100]
    manifest = ManifestLoader.parse(manifest_data)
    runner = CampaignRunner(RunStore(tmp_path), LatePoll({}), clock=lambda: now[0])
    runner.step(manifest)
    now[0] = 165
    assert all(row["status"] == expected for row in runner.step(manifest))


def test_changed_head_is_excluded_from_capture(tmp_path, manifest_data):
    class MismatchedTransport(FixtureTransport):
        def create_pull(self, pair, head_ref):
            return replace(super().create_pull(pair, head_ref), head_sha="c" * 40)

    result = CampaignRunner(RunStore(tmp_path), MismatchedTransport({})).step(ManifestLoader.parse(manifest_data))
    assert {pair["status"] for pair in result} == {"invalid_input"}
