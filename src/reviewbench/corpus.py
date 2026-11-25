"""Compile approved corpus cases and private target mappings into a pinned manifest."""

import json
from collections import Counter

from .manifest import Manifest, ManifestLoader, digest, identifier


def prepare_corpus(catalog: dict, targets: list[dict], rubric_bytes: bytes, run_id: str, repeats: int) -> Manifest:
    rubric = json.loads(rubric_bytes)
    if not isinstance(rubric, dict) or not isinstance(rubric.get("expected"), list) or not isinstance(rubric.get("cases"), list):
        raise ValueError("Corpus rubric must contain approved cases and expected findings")
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or not isinstance(catalog.get("cases"), list):
        raise ValueError("Unsupported corpus catalog")
    if not isinstance(targets, list) or not all(isinstance(target, dict) and isinstance(target.get("repository"), str)
                                                for target in targets):
        raise ValueError("Private target configuration must be an array of target objects")
    inputs = {}
    for source in catalog["cases"]:
        if not isinstance(source, dict):
            raise ValueError("Invalid corpus case")
        case = identifier(source.get("case_id"), "case_id")
        condition = identifier(source.get("condition", "code_only"), "condition")
        if (case, condition) in inputs:
            raise ValueError("Duplicate corpus case condition")
        inputs[case, condition] = source
    expected = Counter()
    for finding in rubric["expected"]:
        if not isinstance(finding, dict):
            raise ValueError("Invalid reference finding in corpus rubric")
        expected[identifier(finding.get("case_id"), "case_id"), identifier(finding.get("condition"), "condition")] += 1
    cases, approved = [], set()
    for item in rubric["cases"]:
        if not isinstance(item, dict):
            raise ValueError("Invalid approved case in corpus rubric")
        key = identifier(item.get("case_id"), "case_id"), identifier(item.get("condition"), "condition")
        source = inputs.get(key)
        kind = item.get("kind")
        if source is None or key in approved or kind not in ("clean", "reference", "context"):
            raise ValueError("Unknown or duplicate case approval")
        if kind == "clean" and expected[key] or kind == "reference" and not expected[key]:
            raise ValueError("Clean cases require zero references and reference cases require at least one")
        approved.add(key)
        if any(field not in source for field in ("title", "base_sha", "head_sha", "isolation_group", "license", "license_url")):
            raise ValueError("Corpus case requires pinned commits, isolation group and license provenance")
        body = source.get("body", "Review the proposed change in the pinned repository.")
        cases.append({"case_id": key[0], "condition": key[1], "isolation_group": source["isolation_group"],
                      "title": source["title"], "body": body, "base_ref": f"siftbench-base/{key[0]}",
                      "base_sha": source["base_sha"], "head_sha": source["head_sha"]})
    if expected.keys() - approved:
        raise ValueError("Reference finding belongs to a case missing from the approved corpus")
    return ManifestLoader.parse({"run_id": run_id, "allowed_repositories": [target["repository"] for target in targets],
                                 "rubric_digest": digest(rubric_bytes), "repeats": repeats, "cases": cases, "targets": targets})
