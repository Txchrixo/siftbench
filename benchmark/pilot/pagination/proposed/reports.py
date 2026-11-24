"""List scheduled reports one page at a time."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Report:
    report_id: int
    scheduled_at: int
    title: str


Cursor = int


def next_page(
    reports: list[Report], *, size: int = 20, after: Cursor | None = None,
) -> tuple[list[Report], Cursor | None]:
    """Return a sorted page and a cursor for the next request."""
    if size < 1:
        raise ValueError("Page size must be positive")
    ordered = sorted(reports, key=lambda report: (report.scheduled_at, report.report_id))
    eligible = [report for report in ordered if after is None or report.scheduled_at > after]
    page = eligible[:size]
    if len(eligible) > size:
        return page, page[-1].scheduled_at
    return page, None
