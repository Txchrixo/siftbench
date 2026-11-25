"""Align claims using reference IDs and explicit, verified reviewer matches."""

from .comment import review_text


def issue_rows(pairs: dict, verified_groups: list[dict]) -> list[tuple[str, str, dict, bool]]:
    references: dict[str, str] = {}
    claims = {}
    matched: dict[str, dict] = {}
    for provider, pair in pairs.items():
        references.update({item["expected_id"]: item["matching_criteria"] for item in pair["expected"]})
        for claim in pair["claims"]:
            if claim["decision"] == "confirmed_expected":
                by_provider = matched.setdefault(claim["expected_id"], {})
                by_provider.setdefault(provider, []).append(claim["claim_id"])
            elif claim["decision"] == "confirmed_novel":
                claims[claim["claim_id"]] = (provider, claim)

    rows = [(key.replace("-", " ").replace("_", " ").capitalize(), criteria, matched.get(key, {}), True)
            for key, criteria in references.items()]
    for group in verified_groups:
        if all(claim_id in claims for claim_id in group["claim_ids"]):
            matches = {}
            for claim_id in group["claim_ids"]:
                provider, _ = claims.pop(claim_id)
                matches.setdefault(provider, []).append(claim_id)
            rows.append((group["title"], group["basis"], matches, False))
    for claim_id, (provider, claim) in claims.items():
        heading = review_text(claim["body"])[0] or "Additional verified finding"
        rows.append((heading, claim["rationale"] or "", {provider: [claim_id]}, False))
    return rows
