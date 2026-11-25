"""Attach independently sourced context to an offline results bundle."""

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from ..manifest import digest


def attach_changes(bundle: dict, repository: Path) -> None:
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "--literal-pathspecs", "-C", str(repository), *args],
                                capture_output=True, check=False)
        if result.returncode:
            raise ValueError("Could not read the pinned changes from the supplied Git repository")
        return result.stdout

    revisions = {(pair["base_sha"], pair["head_sha"]) for pair in bundle["pairs"] if "head_sha" in pair}
    changes = []
    for base, head in sorted(revisions):
        for sha in (base, head):
            if not re.fullmatch(r"[0-9a-f]{40}", sha) or git("cat-file", "-t", sha).strip() != b"commit":
                raise ValueError("Files changed requires two pinned Git commit IDs")
        args = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames")
        entries = []
        for record in git(*args, "--numstat", "-z", base, head, "--").split(b"\0"):
            if not record:
                continue
            added, removed, path = record.decode("utf-8", errors="replace").split("\t", 2)
            patch = git(*args, "--no-color", base, head, "--", path).decode("utf-8", errors="replace")
            entries.append({"path": path, "added": int(added) if added != "-" else None,
                            "removed": int(removed) if removed != "-" else None, "patch": patch})
        changes.append({"base_sha": base, "head_sha": head, "files": entries})
    bundle["changes"] = changes


def attach_publications(bundle: dict, audit: list[dict]) -> None:
    pairs = {(p["provider_id"], p["case_id"], p["condition"], (p.get("pr_url") or "").lower(), p.get("head_sha")): p
             for p in bundle["pairs"]}
    for entry in audit:
        key = (entry["provider"], entry["case"], entry["condition"], entry["pull"].lower(), entry["head"])
        if key not in pairs:
            raise ValueError("Publication audit does not match a pinned review attempt")
        summaries = []
        for summary in entry["summaries"]:
            if digest(summary["body"].encode()) != summary["body_digest"]:
                raise ValueError("Publication summary digest mismatch")
            summaries.append({field: summary[field] for field in ("body", "body_digest", "url")})
        pairs[key]["publication"] = {"check_url": entry["check_url"], "title": entry["check_title"],
                                      "signal": entry["signal"], "summaries": summaries}


def validate_context(bundle: dict) -> None:
    revisions = {(pair.get("base_sha"), pair.get("head_sha")) for pair in bundle["pairs"]}
    seen = set()
    for change in bundle.get("changes", []):
        key = change["base_sha"], change["head_sha"]
        if not all(key) or key not in revisions or key in seen:
            raise ValueError("Invalid or duplicate files-changed revision")
        seen.add(key)
        paths = set()
        for item in change["files"]:
            counts = item["added"], item["removed"]
            if (not isinstance(item["path"], str) or not item["path"] or item["path"] in paths
                    or not isinstance(item["patch"], str)
                    or counts != (None, None) and not all(type(count) is int and count >= 0 for count in counts)):
                raise ValueError("Invalid files-changed record")
            paths.add(item["path"])
    claims = {}
    for pair in bundle["pairs"]:
        for claim in pair["claims"]:
            if claim["decision"] == "confirmed_novel" and claim["evidence"]:
                claims[claim["claim_id"]] = pair["case_id"], pair["condition"], pair["repeat_index"], pair["provider_id"]
        if publication := pair.get("publication"):
            if not all(isinstance(publication[key], str) for key in ("title", "check_url", "signal")):
                raise ValueError("Invalid publication evidence")
            for summary in publication["summaries"]:
                if digest(summary["body"].encode()) != summary["body_digest"]:
                    raise ValueError("Publication summary digest mismatch")
    grouped = set()
    for group in bundle.get("finding_groups", []):
        ids = set(group["claim_ids"])
        if (len(ids) != len(group["claim_ids"]) or ids & grouped or not ids <= claims.keys()
                or not all(isinstance(group[key], str) and group[key].strip() for key in ("title", "basis"))):
            raise ValueError("Invalid or overlapping verified finding group")
        matches = [claims[claim_id] for claim_id in ids]
        if len({match[:3] for match in matches}) != 1 or len({match[3] for match in matches}) < 2:
            raise ValueError("Finding groups require verified findings from different reviewers in the same case")
        grouped.update(ids)


class _SummaryText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3", "p", "sub"}:
            self.parts.append("\n\n")


def publication_text(body: str) -> str:
    parser = _SummaryText()
    parser.feed(body)
    parser.close()
    return "".join(parser.parts).strip()
