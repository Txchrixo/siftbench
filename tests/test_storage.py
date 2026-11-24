import sqlite3

import pytest

from reviewbench.manifest import ManifestError, ManifestLoader
from reviewbench.store import RunStore, StoreError


def test_manifest_is_deterministic_and_survives_reopen(tmp_path, manifest_data):
    manifest = ManifestLoader.parse(manifest_data)
    manifest_data["targets"].reverse()
    assert ManifestLoader.parse(manifest_data) == manifest
    assert len({pair.pair_id for pair in manifest.pairs()}) == 2

    store = RunStore(tmp_path)
    store.register(manifest)
    pair = manifest.pairs()[0]
    attempt = store.attempt(pair, 20, 60)
    assert store.record_create_intent(attempt["attempt_id"], "siftbench/pilot")
    reopened = RunStore(tmp_path)
    reopened.register(manifest)
    assert reopened.load_manifest("pilot") == manifest
    assert reopened.attempt(pair, 50, 60)["deadline_at"] == 80
    assert not reopened.record_create_intent(attempt["attempt_id"], "siftbench/pilot")
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM experiment_pair").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO remote_operation VALUES ('id', 'missing', 'create_pr', 'key', 'intent')")

    manifest_data["cases"][0]["body"] = "A changed input"
    with pytest.raises(StoreError, match="different pinned inputs"):
        reopened.register(ManifestLoader.parse(manifest_data))


def test_manifest_rejects_outside_repository_and_equal_shas(manifest_data):
    manifest_data["targets"][0]["repository"] = "other/repo"
    with pytest.raises(ManifestError, match="allowlist"):
        ManifestLoader.parse(manifest_data)
    manifest_data["targets"][0]["repository"] = "team/one"
    manifest_data["cases"][0]["head_sha"] = "a" * 40
    with pytest.raises(ManifestError, match="must differ"):
        ManifestLoader.parse(manifest_data)


def test_artifact_integrity_and_lock(tmp_path):
    store = RunStore(tmp_path)
    with store.lock("pilot"), pytest.raises(StoreError, match="locked"):
        with RunStore(tmp_path).lock("pilot"):
            pass

    with RunStore(tmp_path).lock("pilot"):
        artifact = store.save_artifact(b"original", "test")
    assert store.read_artifact(artifact) == b"original"
    with store.connect() as db:
        sha = db.execute("SELECT content_sha256 FROM artifact WHERE artifact_id = ?", (artifact,)).fetchone()[0]
    store.artifacts.path(sha).write_bytes(b"tampered")
    with pytest.raises(StoreError, match="digest mismatch"):
        store.read_artifact(artifact)
    store.artifacts.path(sha).unlink()
    with pytest.raises(StoreError, match="Cannot read"):
        store.read_artifact(artifact)
