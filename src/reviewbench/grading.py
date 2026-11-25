import json
from pathlib import Path

from .manifest import canonical_json, digest, identifier
from .store import RunStore, StoreError


DECISIONS = frozenset({"confirmed_expected", "confirmed_novel", "false_positive", "justified_context_request",
                       "duplicate", "out_of_scope", "unresolved"})
EVIDENCE_TYPES = frozenset({"code", "test", "case_snapshot", "aws_live"})


def _require_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"Missing {label}")
    return value


def claims_for_grading(store: RunStore, run_id: str, *, blind: bool = True) -> list[dict]:
    store.load_manifest(run_id)
    with store.connect() as db:
        records = db.execute("""
            SELECT f.claim_id, v.case_id, v.condition, f.body, f.source_locator, f.source_url
            FROM finding_claim f JOIN review_capture c USING (capture_id) JOIN attempt a USING (attempt_id)
            JOIN experiment_pair p USING (pair_id) JOIN case_variant v USING (variant_id)
            WHERE p.run_id = ? ORDER BY v.case_id, v.condition, f.claim_id
        """, (run_id,)).fetchall()
    result = [dict(row) for row in records]
    if blind:
        for row in result:
            row.pop("source_url")
    return result


class VerdictImporter:
    def __init__(self, store: RunStore):
        self.store = store

    def load(self, run_id: str, rubric_path: Path, verdicts_path: Path) -> None:
        manifest = self.store.load_manifest(run_id)
        raw_rubric = rubric_path.read_bytes()
        if digest(raw_rubric) != manifest.rubric_digest:
            raise StoreError("Rubric does not match the campaign's pinned digest")
        rubric = json.loads(raw_rubric)
        if not isinstance(rubric, dict) or not isinstance(rubric.get("expected"), list):
            raise StoreError("Rubric must have an expected array")
        lines = [json.loads(line) for line in verdicts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        with self.store.lock(run_id), self.store.connect() as db:
            variants = {(row["case_id"], row["condition"]): row["variant_id"] for row in db.execute(
                "SELECT * FROM case_variant WHERE run_id = ?", (run_id,))}
            references = set()
            expected = []
            for item in rubric["expected"]:
                if not isinstance(item, dict):
                    raise StoreError("Expected findings must be objects")
                variant_id = variants.get((item.get("case_id"), item.get("condition")))
                expected_id = identifier(item.get("expected_id"), "expected_id")
                if not variant_id or (variant_id, expected_id) in references:
                    raise StoreError("Unknown variant or duplicate reference finding in rubric")
                references.add((variant_id, expected_id))
                expected.append((expected_id, variant_id, _require_text(item.get("matching_criteria"), "matching criteria")))

            known = {row["claim_id"]: row["variant_id"] for row in db.execute("""
                SELECT f.claim_id, p.variant_id FROM finding_claim f JOIN review_capture c USING (capture_id)
                JOIN attempt a USING (attempt_id) JOIN experiment_pair p USING (pair_id) WHERE p.run_id = ?
            """, (run_id,))}
            prepared = []
            seen_claims = set()
            for item in lines:
                if not isinstance(item, dict):
                    raise StoreError("Verdicts must be JSON objects")
                claim_id = _require_text(item.get("claim_id"), "claim ID")
                decision = _require_text(item.get("decision"), "decision")
                variant_id = known.get(claim_id)
                if not variant_id or decision not in DECISIONS or claim_id in seen_claims:
                    raise StoreError("Unknown claim, duplicate claim, or invalid decision")
                seen_claims.add(claim_id)
                expected_id = item.get("expected_id")
                if (decision == "confirmed_expected") != (expected_id is not None):
                    raise StoreError("Only confirmed expected findings must identify a reference finding")
                if expected_id is not None and (variant_id, expected_id) not in references:
                    raise StoreError("Expected finding does not belong to the claim's case and condition")
                rationale = _require_text(item.get("rationale"), "rationale")
                evidence = item.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise StoreError("Every verdict requires evidence")
                artifacts = set()
                for piece in evidence:
                    if not isinstance(piece, dict) or piece.get("type") not in EVIDENCE_TYPES:
                        raise StoreError("Invalid evidence type")
                    artifacts.add(self.store.save_artifact(_require_text(piece.get("text"), "evidence text").encode(), piece["type"]))
                prepared.append((claim_id, variant_id, expected_id, decision, rationale, sorted(artifacts)))

            db.execute("BEGIN IMMEDIATE")
            db.executemany("INSERT INTO expected_finding VALUES (?, ?, ?) ON CONFLICT DO NOTHING", expected)
            for claim_id, variant_id, expected_id, decision, rationale, artifacts in prepared:
                current = db.execute("SELECT * FROM verdict WHERE claim_id = ? ORDER BY revision DESC LIMIT 1",
                                     (claim_id,)).fetchone()
                if current and (current["expected_id"], current["decision"], current["rationale"]) == (expected_id, decision, rationale):
                    previous = [row[0] for row in db.execute("SELECT artifact_id FROM verdict_evidence WHERE verdict_id = ? ORDER BY artifact_id",
                                                            (current["verdict_id"],))]
                    if previous == artifacts:
                        continue
                revision = current["revision"] + 1 if current else 1
                verdict_id = digest(f"{claim_id}:{revision}".encode())
                db.execute("INSERT INTO verdict VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (verdict_id, claim_id, variant_id, expected_id, revision, decision, rationale))
                db.executemany("INSERT INTO verdict_evidence VALUES (?, ?)", [(verdict_id, artifact) for artifact in artifacts])


def build_bundle(store: RunStore, run_id: str) -> dict:
    manifest = store.load_manifest(run_id)
    pairs = store.statuses(run_id)
    variants = {pair.pair_id: pair.variant for pair in manifest.pairs()}
    with store.connect() as db:
        for pair in pairs:
            variant = variants[pair["pair_id"]]
            pair["base_sha"], pair["head_sha"] = variant.base_sha, variant.head_sha
            pair["case_title"] = variant.title
            pair["expected"] = [dict(row) for row in db.execute("""
                SELECT f.expected_id, f.matching_criteria FROM expected_finding f
                JOIN experiment_pair p USING (variant_id) WHERE p.pair_id = ? ORDER BY f.expected_id
            """, (pair["pair_id"],))]
            capture = db.execute("SELECT * FROM review_capture WHERE attempt_id = ?", (pair["attempt_id"],)).fetchone()
            pair["claims"] = []
            pair["capture_signal"] = capture["completion_signal"] if capture else None
            if not capture:
                continue
            if capture["observed_head_sha"] != variant.head_sha:
                raise StoreError("Review capture does not match the pinned commit")
            raw = json.loads(store.read_artifact(capture["raw_artifact_id"]))
            inline = {f'inline:{item["id"]}': item for item in raw.get("inline", ())
                      if isinstance(item, dict) and type(item.get("id")) is int}
            claims = db.execute("""
                SELECT f.*, v.verdict_id, v.decision, v.expected_id, v.rationale, v.revision
                FROM finding_claim f LEFT JOIN verdict v ON v.claim_id = f.claim_id AND v.revision = (
                    SELECT max(v2.revision) FROM verdict v2 WHERE v2.claim_id = f.claim_id)
                WHERE f.capture_id = ? ORDER BY f.claim_id
            """, (capture["capture_id"],)).fetchall()
            for row in claims:
                claim = dict(row)
                source = inline.get(claim["source_object_id"], {})
                hunk = source.get("diff_hunk")
                if (isinstance(hunk, str) and 0 < len(hunk) <= 16_384
                        and source.get("commit_id") == variant.head_sha
                        and source.get("html_url") == claim["source_url"]):
                    claim["diff_hunk"] = hunk
                    if source.get("side") in ("LEFT", "RIGHT"):
                        claim["diff_side"] = source["side"]
                evidence = db.execute("""
                    SELECT a.artifact_id, a.source_type, a.content_sha256 FROM artifact a
                    JOIN verdict_evidence v USING (artifact_id) WHERE v.verdict_id = ? ORDER BY a.artifact_id
                """, (row["verdict_id"],)).fetchall()
                claim["evidence"] = [{"type": piece["source_type"], "digest": piece["content_sha256"],
                                      "text": store.read_artifact(piece["artifact_id"]).decode()} for piece in evidence]
                pair["claims"].append(claim)
    return {"schema_version": 1, "run_id": run_id, "manifest_digest": manifest.manifest_digest,
            "rubric_digest": manifest.rubric_digest, "fixture": bool(pairs) and all(
                pair["capture_signal"] in {None, "fixture.completed"} for pair in pairs)
            and any(pair["capture_signal"] == "fixture.completed" for pair in pairs), "pairs": pairs}


class ScoreCalculator:
    @staticmethod
    def calculate(bundle: dict) -> dict:
        pairs = bundle["pairs"]
        providers = sorted({pair["provider_id"] for pair in pairs})
        completed = {
            provider: {(pair["case_id"], pair["condition"]): pair for pair in pairs
                       if pair["provider_id"] == provider and pair["status"] == "completed" and pair["repeat_index"] == 0}
            for provider in providers
        }
        shared = sorted(set.intersection(*(set(value) for value in completed.values())) if providers else set())
        scores = {}
        for provider in providers:
            expected_count = matched = novel = false_positives = justified = duplicate = unresolved = ungraded = 0
            for key in shared:
                pair = completed[provider][key]
                expected = {finding["expected_id"] for finding in pair["expected"]}
                claims = pair["claims"]
                expected_count += len(expected)
                matched += len({claim["expected_id"] for claim in claims if claim["decision"] == "confirmed_expected"} & expected)
                novel += sum(claim["decision"] == "confirmed_novel" for claim in claims)
                false_positives += sum(claim["decision"] == "false_positive" for claim in claims)
                justified += sum(claim["decision"] == "justified_context_request" for claim in claims)
                duplicate += sum(claim["decision"] == "duplicate" for claim in claims)
                unresolved += sum(claim["decision"] == "unresolved" for claim in claims)
                ungraded += sum(claim["decision"] is None for claim in claims)
            scores[provider] = {"matched": matched, "expected": expected_count, "novel": novel,
                                "false_positives": false_positives, "justified_context_requests": justified,
                                "duplicates": duplicate, "unresolved": unresolved, "ungraded": ungraded,
                                "adjudicated": matched + novel + false_positives}
        return {"shared": shared, "providers": scores}
