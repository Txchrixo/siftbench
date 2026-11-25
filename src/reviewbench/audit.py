"""Read-only reconciliation of archived observations with their GitHub sources."""

import re
from urllib.parse import urlsplit

from .github import GitHubClient, RemoteError
from .manifest import Manifest, digest


def _objects(items: list[dict], publisher: str) -> dict[str, dict]:
    return {str(item["id"]): item for item in items if type(item.get("id")) is int
            and isinstance(item.get("user"), dict) and str(item["user"].get("login", "")).lower() == publisher}


def audit_campaign(bundle: dict, manifest: Manifest, client: GitHubClient) -> list[dict]:
    """Return per-pair evidence checks without changing statuses, captures, or scores."""
    targets = {(target.provider_id, target.condition, target.isolation_group): target for target in manifest.targets}
    if bundle["fixture"] or bundle.get("manifest_digest") != manifest.manifest_digest:
        raise ValueError("Audit requires a live bundle and its pinned manifest")
    results = []
    for pair in bundle["pairs"]:
        target = targets.get((pair["provider_id"], pair["condition"], pair.get("isolation_group")))
        result = {"case": pair["case_id"], "condition": pair["condition"], "provider": pair["provider_id"],
                  "pull": pair.get("pr_url"), "head": pair.get("head_sha"), "claims": [],
                  "publication_sources": [], "other_provider_objects": [], "differences": []}
        results.append(result)
        if pair.get("isolation_group"):
            result["isolation_group"] = pair["isolation_group"]
        if not pair.get("pr_url"):
            result["availability"] = "no_pr"
            continue
        if target is None:
            raise ValueError("Audit pair is outside the pinned manifest")
        parts = urlsplit(pair["pr_url"])
        match = re.fullmatch(f"/{re.escape(target.repository)}/pull/([1-9][0-9]*)", parts.path.lower())
        if parts.scheme != "https" or parts.netloc != "github.com" or not match:
            raise ValueError("Audit PR is outside the target repository")
        try:
            pr = client.get_pull(target.repository, int(match[1]))
            if (pr.base_sha, pr.head_sha) != (pair.get("base_sha"), pair.get("head_sha")):
                result["availability"] = "different"
                result["differences"].append("Current PR base or head differs from the frozen revision")
                continue
            if pr.title is not None and pr.title != pair.get("case_title", pr.title):
                result["differences"].append("Current PR title differs from the archived title")
            _audit_sources(pair, pr, target.publisher, client.review_sources(pr), result)
            if result["differences"]:
                result["availability"] = "different"
            elif not result["claims"] and not result["publication_sources"]:
                result["availability"] = "no_archived_review_sources"
            else:
                result["availability"] = "verified"
        except RemoteError:
            result["availability"] = "unavailable"
            result["differences"].append("GitHub sources could not be verified")
    return results


def _audit_sources(pair, pr, publisher, sources, result):
    reviews, comments, conversation, checks = sources
    by_kind = {kind: _objects(items, publisher) for kind, items in zip(
        ("review", "inline", "issue"), (reviews, comments, conversation), strict=True)}
    used = set()
    for claim in pair["claims"]:
        identity = claim.get("source_object_id") or ""
        kind, _, oid = identity.partition(":")
        item = by_kind.get(kind, {}).get(oid)
        matches = bool(item and item.get("body") == claim["body"] and item.get("html_url") == claim["source_url"])
        if matches and kind in {"review", "inline"}:
            matches = item.get("commit_id") == pr.head_sha
        if matches and kind == "inline":
            path = item.get("path")
            location = f'{path}:{item["line"]}' if type(item.get("line")) is int else path
            matches = claim.get("source_locator") == location
            matches &= all(claim.get(key) == item.get(source) for key, source in (("diff_hunk", "diff_hunk"), ("diff_side", "side")) if key in claim)
        result["claims"].append({"id": identity, "matches": matches, "url": claim.get("source_url")})
        used.add((kind, oid))
        if not matches:
            result["differences"].append(f"Archived source {identity} changed or is unavailable")
    signals = {value for value in (pair.get("capture_signal"), (pair.get("publication") or {}).get("signal")) if value}
    for signal in sorted(signals):
        kind, _, oid = signal.partition(":")
        if kind == "review":
            review = by_kind["review"].get(oid)
            valid = review and review.get("commit_id") == pr.head_sha and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
            result["formal_review_id"] = oid
        elif kind == "check":
            valid = any(str(check.get("id")) == oid and check.get("head_sha") == pr.head_sha
                        and check.get("status") == "completed" and check.get("conclusion") == "success"
                        and (check.get("app") or {}).get("slug", "").lower() == publisher.removesuffix("[bot]")
                        and any(entry.get("number") == pr.number for entry in (check.get("pull_requests") or []) if isinstance(entry, dict))
                        for check in checks)
        else:
            valid = False
        used.add((kind, oid))
        if valid:
            result["publication_sources"].append(signal)
        else:
            result["differences"].append(f"Completion signal {signal} changed or is unavailable")
    for summary in (pair.get("publication") or {}).get("summaries", []):
        oid = urlsplit(summary["url"]).fragment.removeprefix("issuecomment-")
        item = by_kind["issue"].get(oid)
        if item and isinstance(item.get("body"), str) and digest(item["body"].encode()) == summary["body_digest"]:
            result["publication_sources"].append(f"issue:{oid}")
        else:
            result["differences"].append(f"Publication summary issue:{oid} changed or is unavailable")
        used.add(("issue", oid))
    for kind, items in by_kind.items():
        for oid, item in items.items():
            if (kind, oid) not in used:
                result["other_provider_objects"].append({"kind": kind, "id": oid, "url": item.get("html_url"),
                                                         "has_body": bool((item.get("body") or "").strip())})
