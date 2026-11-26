import json
import re
from pathlib import Path

from .manifest import canonical_json, digest, identifier
from .providers import _timestamp
from .store import RunStore, StoreError
from .stats import clustered_ratio_intervals


DECISIONS = frozenset({"confirmed_expected", "confirmed_novel", "false_positive", "justified_context_request",
                       "duplicate", "out_of_scope", "unresolved"})
EVIDENCE_TYPES = frozenset({"code", "test", "case_snapshot", "aws_live"})
SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def fully_graded(pair: dict) -> bool:
    return pair.get("rubric_ready", True) and all(
        claim["decision"] not in {None, "unresolved"} for claim in pair["claims"]
    )


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
            row["body"] = re.sub(r"<!--.*?-->", "", row["body"], flags=re.DOTALL).strip()
    return result


class VerdictImporter:
    def __init__(self, store: RunStore):
        self.store = store

    def load(self, run_id: str, rubric_path: Path, verdicts_path: Path, *, grader_id: str | None = None,
             adjudicator_id: str | None = None) -> None:
        if grader_id and adjudicator_id:
            raise StoreError("Record an independent assessment or adjudicate a verdict in one import")
        if grader_id is not None:
            identifier(grader_id, "grader_id")
        if adjudicator_id is not None:
            identifier(adjudicator_id, "adjudicator_id")
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
                severity = item.get("severity")
                if severity is not None and (not isinstance(severity, str) or severity not in SEVERITIES):
                    raise StoreError("Unknown reference severity")
                expected.append((expected_id, variant_id, _require_text(item.get("matching_criteria"), "matching criteria"), severity))

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
            db.executemany("INSERT INTO expected_finding VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", expected)
            for claim_id, variant_id, expected_id, decision, rationale, artifacts in prepared:
                if grader_id is not None:
                    previous_assessment = db.execute("SELECT revision, decision, expected_id, rationale, evidence_ids "
                                                     "FROM assessment WHERE claim_id = ? AND grader_id = ? ORDER BY revision DESC LIMIT 1",
                                                     (claim_id, grader_id)).fetchone()
                    evidence_ids = canonical_json(artifacts).decode()
                    values = decision, expected_id, rationale, evidence_ids
                    if previous_assessment and tuple(previous_assessment)[1:] == values:
                        continue
                    revision = previous_assessment["revision"] + 1 if previous_assessment else 1
                    db.execute("INSERT INTO assessment VALUES (?, ?, ?, ?, ?, ?, ?)", (claim_id, grader_id, revision, *values))
                    continue
                if adjudicator_id is not None:
                    reviewers = {row[0] for row in db.execute("SELECT DISTINCT grader_id FROM assessment WHERE claim_id = ?", (claim_id,))}
                    if len(reviewers - {adjudicator_id}) < 2:
                        raise StoreError("Adjudication requires two independent graders in addition to the adjudicator")
                current = db.execute("SELECT * FROM verdict WHERE claim_id = ? ORDER BY revision DESC LIMIT 1",
                                     (claim_id,)).fetchone()
                if current and (current["expected_id"], current["decision"], current["rationale"]) == (expected_id, decision, rationale):
                    previous = [row[0] for row in db.execute("SELECT artifact_id FROM verdict_evidence WHERE verdict_id = ? ORDER BY artifact_id",
                                                            (current["verdict_id"],))]
                    adjudicator = db.execute("SELECT adjudicator_id FROM adjudication WHERE verdict_id = ?", (current["verdict_id"],)).fetchone()
                    if previous == artifacts and (adjudicator[0] if adjudicator else None) == adjudicator_id:
                        continue
                revision = current["revision"] + 1 if current else 1
                verdict_id = digest(f"{claim_id}:{revision}".encode())
                db.execute("INSERT INTO verdict VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (verdict_id, claim_id, variant_id, expected_id, revision, decision, rationale))
                db.executemany("INSERT INTO verdict_evidence VALUES (?, ?)", [(verdict_id, artifact) for artifact in artifacts])
                if adjudicator_id is not None:
                    db.execute("INSERT INTO adjudication VALUES (?, ?)", (verdict_id, adjudicator_id))


def assessments_for_run(store: RunStore, run_id: str) -> list[dict]:
    store.load_manifest(run_id)
    with store.connect() as db:
        rows = db.execute("""
            SELECT a.claim_id, a.grader_id, a.revision, a.decision, a.expected_id, a.rationale, a.evidence_ids
            FROM assessment a JOIN finding_claim f USING (claim_id) JOIN review_capture c USING (capture_id)
            JOIN attempt t USING (attempt_id) JOIN experiment_pair p USING (pair_id)
            WHERE p.run_id = ? ORDER BY a.claim_id, a.grader_id, a.revision
        """, (run_id,)).fetchall()
    records = [dict(row) for row in rows]
    for row in records:
        row["evidence_ids"] = json.loads(row["evidence_ids"])
    return records


def build_bundle(store: RunStore, run_id: str) -> dict:
    manifest = store.load_manifest(run_id)
    pairs = store.statuses(run_id)
    inputs = {pair.pair_id: pair for pair in manifest.pairs()}
    with store.connect() as db:
        mode = db.execute("SELECT execution_mode FROM campaign WHERE run_id = ?", (run_id,)).fetchone()[0]
        attempts = {row["attempt_id"]: row for row in db.execute("""
            SELECT a.attempt_id, a.started_at, a.review_started_at, a.deadline_at, a.first_completion_at
            FROM attempt a JOIN experiment_pair p USING (pair_id) WHERE p.run_id = ?
        """, (run_id,))}
        for pair in pairs:
            pinned = inputs[pair["pair_id"]]
            variant = pinned.variant
            pair["base_sha"], pair["head_sha"] = variant.base_sha, pinned.head_sha
            pair["case_title"] = variant.title
            if variant.provenance:
                pair["case_title"] = variant.provenance["title"]
                pair["review_title"] = variant.title
                pair["source"] = variant.provenance
            pair["input_digest"] = variant.input_digest
            if variant.isolation_group is not None:
                pair["isolation_group"] = variant.isolation_group
            if attempt := attempts.get(pair["attempt_id"]):
                pair["timing"] = {key: attempt[key] for key in ("started_at", "review_started_at", "deadline_at", "first_completion_at")}
            pair["expected"] = [dict(row) for row in db.execute("""
                SELECT f.expected_id, f.matching_criteria, f.severity FROM expected_finding f
                JOIN experiment_pair p USING (variant_id) WHERE p.pair_id = ? ORDER BY f.expected_id
            """, (pair["pair_id"],))]
            for finding in pair["expected"]:
                if finding["severity"] is None:
                    del finding["severity"]
            if variant.provenance:
                pair["rubric_ready"] = len(pair["expected"]) == variant.provenance["reference_count"]
            capture = db.execute("SELECT * FROM review_capture WHERE attempt_id = ?", (pair["attempt_id"],)).fetchone()
            pair["claims"] = []
            pair["capture_signal"] = capture["completion_signal"] if capture else None
            if not capture:
                continue
            if capture["observed_head_sha"] != pinned.head_sha:
                raise StoreError("Review capture does not match the pinned commit")
            raw = json.loads(store.read_artifact(capture["raw_artifact_id"]))
            pair["timing"]["frozen_at"] = capture["frozen_at"]
            check, review = raw.get("check") or {}, raw.get("first_review") or {}
            is_check = pair["capture_signal"].startswith("check:")
            if is_check and isinstance(check.get("html_url"), str):
                pair["capture_check_url"] = check["html_url"]
            pair["timing"]["published_at"] = _timestamp(check.get("completed_at") if is_check else review.get("submitted_at"))
            inline = {f'inline:{item["id"]}': item for item in raw.get("inline", ())
                      if isinstance(item, dict) and type(item.get("id")) is int}
            sources = {**inline, **{f'issue:{item["id"]}': item for item in raw.get("summaries", ())
                                    if isinstance(item, dict) and type(item.get("id")) is int}}
            if type(review.get("id")) is int:
                sources[f'review:{review["id"]}'] = review
            claims = db.execute("""
                SELECT f.*, v.verdict_id, v.decision, v.expected_id, v.rationale, v.revision, a.adjudicator_id
                FROM finding_claim f LEFT JOIN verdict v ON v.claim_id = f.claim_id AND v.revision = (
                    SELECT max(v2.revision) FROM verdict v2 WHERE v2.claim_id = f.claim_id)
                LEFT JOIN adjudication a USING (verdict_id)
                WHERE f.capture_id = ? ORDER BY f.claim_id
            """, (capture["capture_id"],)).fetchall()
            for row in claims:
                claim = dict(row)
                original = sources.get(claim["source_object_id"], {})
                if original.get("html_url") == claim["source_url"]:
                    claim["published_at"] = _timestamp(original.get("created_at") or original.get("submitted_at"))
                source = inline.get(claim["source_object_id"], {})
                hunk = source.get("diff_hunk")
                if (isinstance(hunk, str) and 0 < len(hunk) <= 16_384
                        and source.get("commit_id") == pinned.head_sha
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
    return {"schema_version": 1, "operational_schema_version": 1, "run_id": run_id, "manifest_digest": manifest.manifest_digest,
            **({"scoring_policy_version": 2} if any(case.provenance for case in manifest.cases) else {}),
            "rubric_digest": manifest.rubric_digest, "fixture": mode == "fixture",
            "reviewer_configuration": [{"provider": t.provider_id, "condition": t.condition, "publisher": t.publisher, "isolation_group": t.isolation_group,
                                        "trigger": t.trigger, "check": t.completion_check,
                                        "completion_policy": t.completion_policy or ("check" if t.completion_check else "review"),
                                        "vendor_version": t.vendor_version} for t in manifest.targets], "pairs": pairs}


class ScoreCalculator:
    @staticmethod
    def calculate(bundle: dict) -> dict:
        pairs = bundle["pairs"]
        providers = sorted({pair["provider_id"] for pair in pairs})
        repeated = bundle.get("scoring_policy_version") == 2
        completed = {
            provider: {(pair["case_id"], pair["condition"], pair["repeat_index"]): pair for pair in pairs
                       if pair["provider_id"] == provider and pair["status"] == "completed"
                       and (pair["repeat_index"] == 0 or repeated)
                       and (not repeated or fully_graded(pair))}
            for provider in providers
        }
        samples = sorted(set.intersection(*(set(value) for value in completed.values())) if providers else set())
        shared = sorted({key[:2] for key in samples})
        scores, clusters = {}, {}
        for provider in providers:
            expected_count = matched = novel = false_positives = justified = duplicate = unresolved = ungraded = 0
            for key in samples:
                pair = completed[provider][key]
                expected = {finding["expected_id"] for finding in pair["expected"]}
                claims = pair["claims"]
                hits = len({claim["expected_id"] for claim in claims if claim["decision"] == "confirmed_expected"} & expected)
                extra = sum(claim["decision"] == "confirmed_novel" for claim in claims)
                false = sum(claim["decision"] == "false_positive" for claim in claims)
                expected_count += len(expected)
                matched += hits
                novel += extra
                false_positives += false
                if repeated:
                    counts = clusters.setdefault(key[0], {}).setdefault(provider, [0, 0, 0, 0])
                    for index, count in enumerate((hits + extra, hits + extra + false, hits, len(expected))):
                        counts[index] += count
                justified += sum(claim["decision"] == "justified_context_request" for claim in claims)
                duplicate += sum(claim["decision"] == "duplicate" for claim in claims)
                unresolved += sum(claim["decision"] == "unresolved" for claim in claims)
                ungraded += sum(claim["decision"] is None for claim in claims)
            scores[provider] = {"matched": matched, "expected": expected_count, "novel": novel,
                                "false_positives": false_positives, "justified_context_requests": justified,
                                "duplicates": duplicate, "unresolved": unresolved, "ungraded": ungraded,
                                "adjudicated": matched + novel + false_positives}
        if repeated:
            return {"shared": shared, "shared_samples": samples, "providers": scores, "scored_prs": len(clusters),
                    "intervals": clustered_ratio_intervals(clusters, providers)}
        return {"shared": shared, "providers": scores}
