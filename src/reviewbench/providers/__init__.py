import re
from datetime import datetime
from urllib.parse import urlsplit

from ..github import PullRequest, ReviewObservation
from ..manifest import Target


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo else None
    except (ValueError, OSError, OverflowError):
        return None


def _author(item: dict, publisher: str) -> bool:
    author = item.get("user")
    return isinstance(author, dict) and str(author.get("login", "")).lower() == publisher


_CODERABBIT = "coderabbitai[bot]"
_CODERABBIT_MARKER = "<!-- coderabbit-ai-generated:v1 -->"


def check_matches_pull(item: dict, pr: PullRequest, publisher: str, *, name: str | None = None) -> bool:
    app = item.get("app")
    if (not isinstance(app, dict) or str(app.get("slug", "")).lower() != publisher.removesuffix("[bot]")
            or item.get("head_sha") != pr.head_sha or name is not None and item.get("name") != name):
        return False
    associated = item.get("pull_requests")
    if not isinstance(associated, list):
        return False
    if associated:
        return any(isinstance(pull, dict) and pull.get("number") == pr.number for pull in associated)
    if publisher != _CODERABBIT or item.get("name") != "CodeRabbit" or not isinstance(item.get("details_url"), str):
        return False
    try:
        url = urlsplit(item["details_url"])
    except ValueError:
        return False
    return (url.scheme == "https" and url.netloc == "app.coderabbit.ai"
            and url.path.lower() == f"/change-stack/{pr.repository}/pull/{pr.number}")


def check_completed_review(item: dict, publisher: str) -> bool:
    if item.get("status") != "completed" or item.get("conclusion") != "success":
        return False
    if publisher != _CODERABBIT:
        return True
    output = item.get("output")
    summary = output.get("summary") if isinstance(output, dict) else None
    return (isinstance(summary, str)
            and summary.partition("\n")[0].strip() == "Result: Success - Review completed")


def _coderabbit_review_envelope(item: dict, inline_count: int) -> bool:
    body = item.get("body")
    if not isinstance(body, str) or not body.lstrip().startswith(_CODERABBIT_MARKER):
        return False
    count = re.search(r"(?m)^\*\*Actionable comments posted: (\d+)\*\*\s*$", body)
    return (count is not None and count[1] == str(inline_count)
            and not re.search(r"(?i)outside diff range|nitpick comments|duplicate comments", body))


def _coderabbit_walkthrough(item: dict) -> bool:
    body = item.get("body")
    return (isinstance(body, str) and body.lstrip().startswith(_CODERABBIT_MARKER)
            and "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->" in body)


def _claim(surface: str, item: dict, pr: PullRequest) -> tuple[str, str, str | None, str] | None:
    body, url, item_id = item.get("body"), item.get("html_url"), item.get("id")
    if not isinstance(body, str) or not body.strip() or not isinstance(url, str) or type(item_id) is not int:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.path.lower() != f"/{pr.repository}/pull/{pr.number}":
        return None
    locator = item.get("path")
    if isinstance(locator, str) and type(item.get("line")) is int:
        locator = f"{locator}:{item['line']}"
    return f"{surface}:{item_id}", url, locator if isinstance(locator, str) else None, body


class ReviewerAdapter:
    """Use only configured GitHub signals authenticated as the expected publisher."""

    def __init__(self, target: Target):
        self.target = target

    def observe(self, pr: PullRequest, started_at: float, reviews: list[dict], comments: list[dict],
                issue_comments: list[dict], checks: list[dict], *, captured_at: float) -> ReviewObservation:
        publisher = self.target.publisher
        threshold = int(started_at)
        finished = []
        for review in reviews:
            submitted = _timestamp(review.get("submitted_at"))
            if (_author(review, publisher) and review.get("commit_id") == pr.head_sha
                    and submitted is not None and threshold <= submitted <= captured_at
                    and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
                    and type(review.get("id")) is int):
                finished.append((submitted, review["id"], review))
        first = min(finished, key=lambda item: item[:2])[2] if finished else None

        check = None
        if self.target.completion_check:
            eligible = []
            for item in checks:
                ended = _timestamp(item.get("completed_at"))
                started = _timestamp(item.get("started_at"))
                if (check_matches_pull(item, pr, publisher, name=self.target.completion_check)
                        and item.get("status") == "completed" and started is not None and ended is not None
                        and threshold <= started <= ended <= captured_at and type(item.get("id")) is int):
                    eligible.append((ended, item["id"], item))
            fallback = self.target.completion_policy == "review_or_check" and first is not None
            if not eligible and not fallback:
                return ReviewObservation("pending", None, (), {})
            if eligible:
                check = min(eligible, key=lambda item: item[:2])[2]
                if not check_completed_review(check, publisher):
                    if not fallback:
                        return ReviewObservation("failed", f"check:{check['id']}", (), {"check": check},
                                                 _timestamp(check["completed_at"]))
                    check = None
                elif fallback and _timestamp(first["submitted_at"]) <= _timestamp(check["completed_at"]):
                    check = None
        elif first is None:
            return ReviewObservation("pending", None, (), {})

        inline = [item for item in comments if first and item.get("pull_request_review_id") == first["id"]
                  and _author(item, publisher) and item.get("commit_id") == pr.head_sha]
        summaries = [item for item in issue_comments if check and _author(item, publisher)
                     and threshold <= (_timestamp(item.get("created_at")) or 0) <= captured_at]
        is_coderabbit = publisher == _CODERABBIT
        claims = ([_claim("review", first, pr)] if first
                  and not (is_coderabbit and _coderabbit_review_envelope(first, len(inline))) else [])
        claims.extend(_claim("inline", item, pr) for item in inline)
        claims.extend(_claim("issue", item, pr) for item in summaries
                      if not (is_coderabbit and _coderabbit_walkthrough(item)))
        signal = f"check:{check['id']}" if check else f"review:{first['id']}"
        return ReviewObservation("completed", signal, tuple(claim for claim in claims if claim is not None),
                                 {"first_review": first, "check": check, "inline": inline, "summaries": summaries},
                                 _timestamp(check["completed_at"] if check else first["submitted_at"]))
