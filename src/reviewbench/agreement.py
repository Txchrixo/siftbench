"""Agreement between independent annotators who judged the same review comments."""

import json
from collections import Counter
from itertools import combinations
from pathlib import Path

from .grading import DECISIONS


KINDS = ("ai", "human")


def load_annotations(path: Path) -> dict[str, str]:
    """Read one annotator's verdict file. Each line needs a claim ID and a known decision."""
    decisions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        claim_id, decision = item.get("claim_id"), item.get("decision")
        if not isinstance(claim_id, str) or decision not in DECISIONS:
            raise ValueError(f"{path.name}: invalid claim or decision")
        if claim_id in decisions:
            raise ValueError(f"{path.name}: duplicate claim {claim_id}")
        decisions[claim_id] = decision
    return decisions


def cohen_kappa(first: list[str], second: list[str]) -> float | None:
    """Chance-corrected agreement of two annotators. None when chance agreement is already 1."""
    n = len(first)
    if not n or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second)) / n
    left, right = Counter(first), Counter(second)
    expected = sum(left[label] * right[label] for label in left.keys() | right.keys()) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def fleiss_kappa(rows: list[list[str]]) -> float | None:
    """Chance-corrected agreement across the same number of annotators for every item."""
    n = len(rows)
    raters = len(rows[0]) if rows else 0
    if n < 1 or raters < 2 or any(len(row) != raters for row in rows):
        return None
    totals: Counter = Counter()
    per_item = []
    for row in rows:
        counts = Counter(row)
        totals.update(counts)
        per_item.append((sum(c * c for c in counts.values()) - raters) / (raters * (raters - 1)))
    expected = sum((count / (n * raters)) ** 2 for count in totals.values())
    return None if expected == 1 else (sum(per_item) / n - expected) / (1 - expected)


def interpretation(kappa: float | None) -> str:
    """Landis and Koch labels, used only as a reading aid."""
    if kappa is None:
        return "Not defined"
    if kappa < 0:
        return "Poor"
    for limit, label in ((.2, "Slight"), (.4, "Fair"), (.6, "Moderate"), (.8, "Substantial")):
        if kappa <= limit:
            return label
    return "Almost perfect"


def compare(annotators: list[dict], final: dict[str, str | None]) -> dict:
    """Summarize agreement. `annotators` items are {"name", "kind", "decisions"}; `final` maps claim to its recorded verdict."""
    if len(annotators) < 2:
        raise ValueError("Agreement needs at least two annotators")
    names = [item["name"] for item in annotators]
    if len(set(names)) != len(names) or any(item["kind"] not in KINDS for item in annotators):
        raise ValueError("Annotators need unique names and a kind of ai or human")
    shared = set.intersection(*(set(item["decisions"]) for item in annotators))
    claims = sorted(shared)
    if not claims:
        raise ValueError("Annotators judged no claim in common")
    table = {claim: [item["decisions"][claim] for item in annotators] for claim in claims}
    unanimous = sum(len(set(row)) == 1 for row in table.values())
    pairs = []
    for (left, first), (right, second) in combinations(zip(names, annotators), 2):
        a, b = [first["decisions"][c] for c in claims], [second["decisions"][c] for c in claims]
        kappa = cohen_kappa(a, b)
        pairs.append({"annotators": [left, right], "agreement": round(sum(x == y for x, y in zip(a, b)) / len(claims), 4),
                      "kappa": None if kappa is None else round(kappa, 4)})
    overall = fleiss_kappa(list(table.values())) if len(annotators) > 2 else (pairs[0]["kappa"] if pairs else None)
    majority_final = 0
    disagreements = []
    for claim, row in table.items():
        recorded = final.get(claim)
        majority, votes = Counter(row).most_common(1)[0]
        majority = majority if votes > len(row) / 2 else None
        matches = recorded is not None and majority == recorded
        majority_final += matches
        if len(set(row)) > 1 or not matches:
            disagreements.append({"claim_id": claim, "decisions": dict(zip(names, row)), "recorded": recorded,
                                  "majority": majority, "annotators_split": len(set(row)) > 1})
    return {"annotators": [{"name": item["name"], "kind": item["kind"]} for item in annotators],
            "claims": len(claims), "unanimous": unanimous, "agreement": round(unanimous / len(claims), 4),
            "kappa": None if overall is None else round(overall, 4), "kappa_label": interpretation(overall),
            "kappa_method": "Fleiss" if len(annotators) > 2 else "Cohen", "pairs": pairs,
            "matches_recorded_verdict": majority_final, "disagreements": disagreements}
