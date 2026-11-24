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
            app_slug = publisher.removesuffix("[bot]")
            eligible = []
            for item in checks:
                app = item.get("app")
                ended = _timestamp(item.get("completed_at"))
                started = _timestamp(item.get("started_at"))
                pull_requests = item.get("pull_requests")
                if (isinstance(app, dict) and str(app.get("slug", "")).lower() == app_slug
                        and item.get("name") == self.target.completion_check and item.get("head_sha") == pr.head_sha
                        and isinstance(pull_requests, list)
                        and any(isinstance(pull, dict) and pull.get("number") == pr.number for pull in pull_requests)
                        and item.get("status") == "completed" and started is not None and ended is not None
                        and threshold <= started <= ended <= captured_at and type(item.get("id")) is int):
                    eligible.append((ended, item["id"], item))
            if not eligible:
                return ReviewObservation("pending", None, (), {})
            check = min(eligible, key=lambda item: item[:2])[2]
            if check.get("conclusion") != "success":
                return ReviewObservation("failed", f"check:{check['id']}", (), {"check": check})
        elif first is None:
            return ReviewObservation("pending", None, (), {})

        inline = [item for item in comments if first and item.get("pull_request_review_id") == first["id"]
                  and _author(item, publisher) and item.get("commit_id") == pr.head_sha]
        summaries = [item for item in issue_comments if check and _author(item, publisher)
                     and threshold <= (_timestamp(item.get("created_at")) or 0) <= captured_at]
        claims = [_claim("review", first, pr)] if first else []
        claims.extend(_claim("inline", item, pr) for item in inline)
        claims.extend(_claim("issue", item, pr) for item in summaries)
        signal = f"check:{check['id']}" if check else f"review:{first['id']}"
        return ReviewObservation("completed", signal, tuple(claim for claim in claims if claim is not None),
                                 {"first_review": first, "check": check, "inline": inline, "summaries": summaries})
