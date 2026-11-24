import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .manifest import ExperimentPair, Manifest, ManifestLoader, digest, identifier


class StoreError(ValueError):
    """An operation would violate the persisted campaign contract."""


_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS campaign (
    run_id TEXT PRIMARY KEY, manifest_digest TEXT NOT NULL,
    rubric_digest TEXT NOT NULL, manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_variant (
    variant_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES campaign,
    case_id TEXT NOT NULL, condition TEXT NOT NULL, base_sha TEXT NOT NULL,
    head_sha TEXT NOT NULL, input_digest TEXT NOT NULL,
    UNIQUE (run_id, case_id, condition), UNIQUE (run_id, variant_id)
);
CREATE TABLE IF NOT EXISTS experiment_pair (
    pair_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES campaign,
    variant_id TEXT NOT NULL, provider_id TEXT NOT NULL, repeat_index INTEGER NOT NULL,
    FOREIGN KEY (run_id, variant_id) REFERENCES case_variant(run_id, variant_id),
    UNIQUE (run_id, variant_id, provider_id, repeat_index)
);
CREATE TABLE IF NOT EXISTS attempt (
    attempt_id TEXT PRIMARY KEY, pair_id TEXT NOT NULL REFERENCES experiment_pair,
    attempt_number INTEGER NOT NULL, status TEXT NOT NULL, repository TEXT NOT NULL,
    pr_number INTEGER, pr_url TEXT, started_at REAL NOT NULL, deadline_at REAL NOT NULL,
    first_completion_at REAL, next_poll_at REAL, reason TEXT,
    UNIQUE (pair_id, attempt_number), UNIQUE (repository, pr_number)
);
CREATE TABLE IF NOT EXISTS attempt_event (
    event_id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt,
    status TEXT NOT NULL, observed_at REAL NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_operation (
    operation_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt,
    operation_type TEXT NOT NULL, correlation_key TEXT NOT NULL, status TEXT NOT NULL,
    UNIQUE (attempt_id, operation_type)
);
CREATE TABLE IF NOT EXISTS artifact (
    artifact_id TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL, source_type TEXT NOT NULL,
    relative_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_capture (
    capture_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES attempt,
    raw_artifact_id TEXT NOT NULL REFERENCES artifact, observed_head_sha TEXT NOT NULL,
    frozen_at REAL NOT NULL, completion_signal TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finding_claim (
    claim_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES review_capture,
    source_object_id TEXT NOT NULL, source_url TEXT NOT NULL, source_locator TEXT,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expected_finding (
    expected_id TEXT NOT NULL, variant_id TEXT NOT NULL REFERENCES case_variant,
    matching_criteria TEXT NOT NULL, PRIMARY KEY (variant_id, expected_id)
);
CREATE TABLE IF NOT EXISTS verdict (
    verdict_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES finding_claim,
    variant_id TEXT NOT NULL REFERENCES case_variant, expected_id TEXT,
    revision INTEGER NOT NULL, decision TEXT NOT NULL, rationale TEXT NOT NULL,
    FOREIGN KEY (variant_id, expected_id) REFERENCES expected_finding(variant_id, expected_id),
    UNIQUE (claim_id, revision)
);
CREATE TABLE IF NOT EXISTS verdict_evidence (
    verdict_id TEXT NOT NULL REFERENCES verdict, artifact_id TEXT NOT NULL REFERENCES artifact,
    PRIMARY KEY (verdict_id, artifact_id)
);
PRAGMA user_version = 1;
COMMIT;
"""


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise StoreError("Invalid artifact digest")
        return self.root / sha[:2] / sha

    def put(self, data: bytes) -> str:
        sha = digest(data)
        path = self.path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        self.read(sha)
        return sha

    def read(self, sha: str) -> bytes:
        try:
            data = self.path(sha).read_bytes()
        except OSError as error:
            raise StoreError(f"Cannot read artifact {sha}") from error
        if digest(data) != sha:
            raise StoreError(f"Artifact digest mismatch: {sha}")
        return data


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        self.artifacts = ArtifactStore(root / "artifacts")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.root / "runs.sqlite3", timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise StoreError(f"Unsupported database schema version: {version}")
            if version == 0:
                connection.executescript(_SCHEMA)
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def lock(self, run_id: str) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / f"{identifier(run_id, 'run_id')}.lock"
        with lock.open("a+b") as file:
            try:
                if os.name == "nt":
                    import msvcrt

                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise StoreError(f"Campaign already locked or unavailable: {run_id}") from error
            # Closing the descriptor releases the OS lock even after process termination.
            yield

    def register(self, manifest: Manifest) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT manifest_digest FROM campaign WHERE run_id = ?", (manifest.run_id,)).fetchone()
            if row:
                if row["manifest_digest"] != manifest.manifest_digest:
                    raise StoreError("Campaign already exists with different pinned inputs")
                return
            db.execute("INSERT INTO campaign VALUES (?, ?, ?, ?)",
                       (manifest.run_id, manifest.manifest_digest, manifest.rubric_digest, manifest.json.decode()))
            variants = set()
            for pair in manifest.pairs():
                if pair.variant_id not in variants:
                    case = pair.variant
                    db.execute("INSERT INTO case_variant VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (pair.variant_id, manifest.run_id, case.case_id, case.condition,
                                case.base_sha, case.head_sha, case.input_digest))
                    variants.add(pair.variant_id)
                db.execute("INSERT INTO experiment_pair VALUES (?, ?, ?, ?, ?)",
                           (pair.pair_id, manifest.run_id, pair.variant_id, pair.target.provider_id, pair.repeat_index))

    def load_manifest(self, run_id: str) -> Manifest:
        with self.connect() as db:
            row = db.execute("SELECT * FROM campaign WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise StoreError(f"Unknown campaign: {run_id}")
        raw = row["manifest_json"].encode()
        if digest(raw) != row["manifest_digest"]:
            raise StoreError("Stored manifest digest mismatch")
        return ManifestLoader.parse(json.loads(raw))

    def save_artifact(self, data: bytes, source_type: str) -> str:
        sha = self.artifacts.put(data)
        artifact_id = digest(f"{source_type}:{sha}".encode())
        relative_path = self.artifacts.path(sha).relative_to(self.root).as_posix()
        with self.connect() as db:
            db.execute("INSERT INTO artifact VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                       (artifact_id, sha, source_type, relative_path))
        return artifact_id

    def read_artifact(self, artifact_id: str) -> bytes:
        with self.connect() as db:
            row = db.execute("SELECT content_sha256 FROM artifact WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if not row:
            raise StoreError(f"Unknown artifact: {artifact_id}")
        return self.artifacts.read(row["content_sha256"])

    def attempt(self, pair: ExperimentPair, now: float, timeout: int) -> sqlite3.Row:
        with self.connect() as db:
            attempt_id = digest(f"{pair.pair_id}:0".encode())
            cursor = db.execute(
                """INSERT INTO attempt
                (attempt_id, pair_id, attempt_number, status, repository, started_at, deadline_at)
                VALUES (?, ?, 0, 'planned', ?, ?, ?) ON CONFLICT DO NOTHING""",
                (attempt_id, pair.pair_id, pair.target.repository, now, now + timeout),
            )
            if cursor.rowcount:
                self._event(db, attempt_id, "planned", now, "Attempt created")
            return db.execute("SELECT * FROM attempt WHERE attempt_id = ?", (attempt_id,)).fetchone()

    @staticmethod
    def _event(db: sqlite3.Connection, attempt_id: str, status: str, now: float, reason: str) -> None:
        db.execute("INSERT INTO attempt_event (attempt_id, status, observed_at, reason) VALUES (?, ?, ?, ?)",
                   (attempt_id, status, now, reason))

    def transition(self, attempt_id: str, status: str, now: float, reason: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE attempt SET status = ?, reason = ? WHERE attempt_id = ?", (status, reason, attempt_id))
            self._event(db, attempt_id, status, now, reason)

    def record_create_intent(self, attempt_id: str, head_ref: str) -> bool:
        with self.connect() as db:
            result = db.execute(
                "INSERT INTO remote_operation VALUES (?, ?, 'create_pr', ?, 'intent') ON CONFLICT DO NOTHING",
                (digest(f"{attempt_id}:create_pr".encode()), attempt_id, head_ref),
            )
            return result.rowcount == 1

    def attach_pr(self, attempt_id: str, number: int, url: str, status: str, now: float) -> None:
        with self.connect() as db:
            db.execute("UPDATE attempt SET pr_number = ?, pr_url = ?, status = ?, reason = NULL WHERE attempt_id = ?",
                       (number, url, status, attempt_id))
            db.execute("UPDATE remote_operation SET status = 'reconciled' WHERE attempt_id = ? AND operation_type = 'create_pr'",
                       (attempt_id,))
            self._event(db, attempt_id, status, now, "Pinned PR attached")

    def first_completion(self, attempt_id: str, now: float) -> float:
        with self.connect() as db:
            db.execute("UPDATE attempt SET first_completion_at = COALESCE(first_completion_at, ?) WHERE attempt_id = ?",
                       (now, attempt_id))
            return db.execute("SELECT first_completion_at FROM attempt WHERE attempt_id = ?", (attempt_id,)).fetchone()[0]

    def schedule_poll(self, attempt_id: str, next_poll: float, reason: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE attempt SET next_poll_at = ?, reason = ? WHERE attempt_id = ?",
                       (next_poll, reason, attempt_id))

    def freeze_capture(self, attempt_id: str, head_sha: str, signal: str, now: float, raw: bytes,
                       claims: list[tuple[str, str, str | None, str]]) -> None:
        artifact_id = self.save_artifact(raw, "review_capture")
        capture_id = digest(f"{attempt_id}:capture".encode())
        with self.connect() as db:
            db.execute("INSERT INTO review_capture VALUES (?, ?, ?, ?, ?, ?)",
                       (capture_id, attempt_id, artifact_id, head_sha, now, signal))
            for source_id, url, locator, body in claims:
                claim_id = digest(f"{capture_id}:{source_id}".encode())
                db.execute("INSERT INTO finding_claim VALUES (?, ?, ?, ?, ?, ?)",
                           (claim_id, capture_id, source_id, url, locator, body))
            db.execute("UPDATE attempt SET status = 'completed', reason = NULL WHERE attempt_id = ?", (attempt_id,))
            self._event(db, attempt_id, "completed", now, signal)

    def statuses(self, run_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""
                SELECT p.pair_id, v.case_id, v.condition, p.provider_id, p.repeat_index,
                       a.attempt_id, a.pr_url, COALESCE(a.status, 'planned') AS status, a.reason
                FROM experiment_pair p JOIN case_variant v USING (variant_id)
                LEFT JOIN attempt a ON a.pair_id = p.pair_id AND a.attempt_number = (
                    SELECT max(a2.attempt_number) FROM attempt a2 WHERE a2.pair_id = p.pair_id
                )
                WHERE p.run_id = ? ORDER BY v.case_id, v.condition, p.provider_id, p.repeat_index
            """, (run_id,)).fetchall()
        return [dict(row) for row in rows]
