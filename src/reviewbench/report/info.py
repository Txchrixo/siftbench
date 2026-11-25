"""Explanations behind the small (i) buttons. Trusted static markup, rendered once per page."""

from html import escape

from .icons import icon

# Tone keys (ok, bad, warn, hl, mute) map to theme colours in report.css as .t-* and b.*.

_ENTRIES: dict[str, dict] = {
    "precision": {
        "kind": "Quality metric", "tone": "ok", "title": "Precision",
        "lead": 'Of the findings the benchmark <b class="hl">checked and ruled on</b>, how many were real problems.',
        "formula": (("Confirmed findings", "expected + additional"), ("Findings with a verdict", "confirmed + false positives")),
        "points": (
            ("ok", "Higher is better", "Few wrong comments for a developer to wade through."),
            ("warn", "Can still move", "Marked Provisional while findings are waiting for a verdict."),
        ),
        "note": "Duplicates, context requests and review messages are left out, so they never lower precision.",
    },
    "recall": {
        "kind": "Quality metric", "tone": "hl", "title": "Reference recall",
        "lead": 'How many of the problems the benchmark <b class="hl">knows are in the code</b> the reviewer actually caught.',
        "formula": (("Expected findings detected", "confirmed against the rubric"), ("Expected findings", "listed in the rubric")),
        "points": (
            ("ok", "Higher is better", "100% means nothing from the rubric was missed."),
            ("hl", "Same yardstick", "The rubric was frozen before the reviews ran, so every reviewer faces the same list."),
        ),
        "note": "It ignores noise. Read it next to precision.",
    },
    "additional": {
        "kind": "Quality metric", "tone": "ok", "title": "Additional findings",
        "lead": 'Real problems the reviewer found that <b class="ok">were not in the rubric</b>.',
        "points": (
            ("ok", "Verified, not assumed", "Each one was confirmed with evidence before it counted."),
            ("hl", "Counts toward precision", "They raise precision but not reference recall."),
        ),
        "note": "Two reviewers share a row on the Problems tab only when verified as the same issue.",
    },
    "false_positives": {
        "kind": "Quality metric", "tone": "bad", "title": "False positives",
        "lead": 'Comments the benchmark checked and found <b class="bad">wrong or misleading</b>.',
        "points": (
            ("bad", "Lower is better", "Each one costs a developer time to read and dismiss."),
            ("hl", "Per graded pull request", "Operational metrics show how many occurred across fully graded pull requests."),
        ),
        "note": "A reasonable question is counted as a context request, not a false positive.",
    },
    "context_requests": {
        "kind": "Additional metric", "tone": "warn", "title": "Context requests",
        "lead": 'Questions or requests for more context that the benchmark judged <b class="warn">reasonable</b>.',
        "points": (
            ("mute", "Neutral", "They are not findings, so they neither raise nor lower precision."),
        ),
        "note": "Useful to see how often a reviewer asks instead of asserting.",
    },
    "duplicates": {
        "kind": "Additional metric", "tone": "mute", "title": "Duplicates",
        "lead": 'Comments that <b class="hl">repeat a finding</b> already counted for the same reviewer.',
        "points": (
            ("mute", "Counted once", "The first comment is scored. Repeats are set aside."),
        ),
        "note": "Duplicates change neither precision nor recall.",
    },
    "unresolved": {
        "kind": "Grading status", "tone": "warn", "title": "Unresolved",
        "lead": 'Findings the grader <b class="warn">could not settle yet</b>: the evidence was missing or conflicting.',
        "points": (
            ("warn", "Scores are provisional", "While any remain, precision and recall can still change."),
        ),
        "note": "Open the finding to see why it is waiting.",
    },
    "ungraded": {
        "kind": "Grading status", "tone": "mute", "title": "Ungraded",
        "lead": 'Captured findings that <b class="hl">no verdict has been recorded for</b> yet.',
        "points": (
            ("warn", "Scores are provisional", "Ungraded findings are not in any score so far."),
            ("mute", "Not the same as unresolved", "Unresolved findings were reviewed but could not be decided."),
        ),
    },
    "completed_attempts": {
        "kind": "Campaign total", "tone": "ok", "title": "Completed attempts",
        "lead": 'Reviews that <b class="ok">finished and were captured</b>, out of every review the campaign planned.',
        "points": (
            ("hl", "One attempt", "A single reviewer on a single pull request condition, including repeats."),
            ("warn", "Incomplete is information", "Timeouts and failures are kept in the report, not hidden."),
        ),
    },
    "interval": {
        "kind": "Statistics", "tone": "hl", "title": "95% confidence interval",
        "lead": 'The range the true rate could plausibly fall in, given <b class="hl">how few findings</b> were observed.',
        "points": (
            ("hl", "Wide means unsure", "5 of 5 correct is 100%, but the interval can still start near 57%."),
            ("warn", "Slightly optimistic", "Each finding counts separately. Findings one reviewer leaves on the same pull request are related, so the range is a little too narrow when a reviewer has several per pull request."),
            ("ok", "Overlap", "When two reviewers' ranges overlap heavily, the data cannot separate them."),
        ),
        "note": "Timing intervals resample the observed times around the median and appear from 5 observations.",
    },
    "scored_conditions": {
        "kind": "Campaign total", "tone": "hl", "title": "Scored conditions",
        "lead": 'Pull request setups that <b class="hl">every reviewer completed</b>. Only these feed the score table.',
        "points": (
            ("ok", "Like for like", "Every reviewer is compared on identical work."),
            ("mute", "Repeats excluded", "Repeated runs are shown in the details, not in the scores."),
        ),
    },
    "reviewers": {
        "kind": "Campaign total", "tone": "hl", "title": "Reviewers",
        "lead": 'The AI code reviewers compared in this campaign, each run on the same pinned pull requests.',
        "points": (
            ("hl", "Pinned input", "Every review starts from the same base and head commits."),
        ),
    },
    "campaign": {
        "kind": "Run", "tone": "hl", "title": "Campaign",
        "lead": 'One planned run of reviewers over a <b class="hl">fixed set of pinned pull requests</b>.',
        "points": (
            ("hl", "The ID", "Names the archived run these results were built from."),
            ("ok", "Replayable", "The report can be rebuilt from that archive without calling a reviewer again."),
        ),
    },
    "fixture": {
        "kind": "Run", "tone": "warn", "title": "Fixture run",
        "lead": '<b class="warn">Local test data</b>, used to try the benchmark offline.',
        "points": (
            ("warn", "Not live results", "It does not measure how any real reviewer performs."),
        ),
    },
    "scope": {
        "kind": "Scoring", "tone": "hl", "title": "Scored or outside score",
        "lead": 'Whether this row counts toward the shared results.',
        "points": (
            ("hl", "Scored", "Every reviewer completed it, and it is not a repeat."),
            ("mute", "Outside score", "At least one reviewer did not complete it, or it is a repeat. It stays visible but unscored."),
        ),
    },
    "statuses": {
        "kind": "Coverage", "tone": "hl", "title": "Attempt statuses",
        "lead": 'Where each review attempt stands. Select a status to open the attempt.',
        "points": (
            ("ok", "Completed", "The review was captured and verified."),
            ("warn", "Waiting", "Awaiting trigger or review, needs attention, or limited by provider quota."),
            ("bad", "Did not finish", "Timed out, failed, or invalid input. Kept in the report as evidence."),
            ("mute", "Planned", "Scheduled or preparing, not started yet."),
        ),
        "note": "Grading incomplete means the review finished but some findings still await a verdict.",
    },
    "latency": {
        "kind": "Timing", "tone": "hl", "title": "Review publication times",
        "lead": 'How long a reviewer took from <b class="hl">starting its review</b> to <b class="hl">publishing</b> a verified result.',
        "points": (
            ("hl", "Each dot", "One observed review. Select it to open the original evidence."),
            ("hl", "Dashed line", "The median: half the reviews were faster, half slower."),
            ("warn", "Not a race", "Reviewers may have run in separate campaigns, so network and queue conditions differ."),
        ),
    },
    "severe_missed": {
        "kind": "Operational metric", "tone": "bad", "title": "Severe findings missed",
        "lead": 'Rubric problems rated <b class="bad">high or critical</b> that the reviewer did not catch.',
        "formula": (("Severe findings missed", "not detected"), ("Severe expected findings", "high or critical")),
        "points": (("bad", "Lower is better", "Missing a severe problem matters more than missing a minor one."),),
        "note": "Counted only on pull requests that are fully graded.",
    },
    "review_completion": {
        "kind": "Operational metric", "tone": "hl", "title": "Review completion",
        "lead": 'Time from <b class="hl">review start</b> until the review was <b class="hl">published</b>.',
        "points": (
            ("hl", "Median and p95", "Median is the typical review. p95 is the slow end: 95% finished sooner."),
            ("mute", "n", "How many valid times the figure rests on."),
        ),
    },
    "first_verified": {
        "kind": "Operational metric", "tone": "ok", "title": "First verified finding",
        "lead": 'Time until the first finding the benchmark <b class="ok">confirmed</b> was published.',
        "points": (
            ("hl", "Why it differs", "A review can finish later than its first useful comment."),
            ("mute", "n", "How many reviews produced a confirmed finding."),
        ),
    },
    "observed_cost": {
        "kind": "Operational metric", "tone": "warn", "title": "Observed cost",
        "lead": 'Median cost per attempt from <b class="warn">recorded billing data</b>, in the currency’s minor unit.',
        "points": (
            ("hl", "Minor unit", "For example cents, so 250 means 2.50."),
            ("ok", "Measured, not estimated", "Missing billing data shows as not measured."),
        ),
    },
    "reference_findings": {
        "kind": "Rubric", "tone": "hl", "title": "Expected findings",
        "lead": 'Problems the benchmark authors <b class="hl">know are in the pull request</b>, written down before any reviewer ran.',
        "points": (
            ("ok", "Detected", "A reviewer comment was confirmed against this problem."),
            ("bad", "Missed", "The review was graded and nothing matched."),
            ("mute", "Not evaluated", "No completed review, or grading is still pending."),
        ),
        "note": "Select a result to open its evidence. Select a problem name to read its matching criteria.",
    },
    "problems": {
        "kind": "Comparison", "tone": "hl", "title": "Compare by problem",
        "lead": 'One row per problem, with the reviewers side by side.',
        "points": (
            ("hl", "Reference", "Comes from the rubric."),
            ("ok", "Additional", "Shares a row only when verified as the same problem."),
        ),
    },
    "findings": {
        "kind": "Findings", "tone": "hl", "title": "Captured findings",
        "lead": 'Every comment the reviewer left on the pull request, each with its own verdict.',
        "points": (
            ("hl", "Findings", "Comments about the code. Select one to see the diff, the original text and the verdict."),
            ("mute", "Review messages", "Announcements and summaries. They are listed apart and excluded from scoring."),
        ),
    },
    "verdict": {
        "kind": "Grading", "tone": "hl", "title": "Benchmark verdict",
        "lead": 'The ruling given to this comment after checking it against the code.',
        "points": (
            ("ok", "Confirmed expected", "Matches a rubric problem."),
            ("ok", "Confirmed novel", "A real problem outside the rubric."),
            ("bad", "False positive", "Wrong or misleading."),
            ("warn", "Justified context request", "A reasonable question, not a finding."),
            ("warn", "Unresolved", "Evidence is missing or conflicting."),
            ("mute", "Duplicate, out of scope or awaiting", "Not scored."),
        ),
    },
    "evidence": {
        "kind": "Grading", "tone": "ok", "title": "Verification evidence",
        "lead": 'What backs the verdict. A ruling without evidence cannot be trusted.',
        "points": (
            ("hl", "Code", "The reasoning traced through the pinned source."),
            ("hl", "Test", "A check that was run against the code."),
            ("hl", "Case snapshot", "Compared with the recorded state of the case."),
            ("hl", "AWS live", "Observed in a live deployment."),
        ),
    },
    "captured_diff": {
        "kind": "Evidence", "tone": "hl", "title": "Captured GitHub diff",
        "lead": 'The lines of code the review comment was attached to, <b class="hl">saved with the review</b>.',
        "points": (
            ("ok", "Green +", "A line added by the pull request."),
            ("bad", "Red −", "A line removed by the pull request."),
            ("hl", "Reviewer comment", "Marks the exact line the reviewer commented on."),
        ),
        "note": "Only a short excerpt is kept here. Open Files changed to see the whole pull request.",
    },
    "location": {
        "kind": "Evidence", "tone": "warn", "title": "Reported location",
        "lead": 'The file and line the comment <b class="warn">named</b>, without a saved code excerpt.',
        "points": (
            ("mute", "No diff here", "GitHub did not provide the surrounding code with this comment."),
        ),
        "note": "Open the original comment on GitHub to see it in context.",
    },
    "diff": {
        "kind": "Files changed", "tone": "hl", "title": "Reading the diff",
        "lead": 'The pull request’s changes, from the <b class="hl">pinned base to the pinned head</b>.',
        "points": (
            ("ok", "Green +", "Added lines."),
            ("bad", "Red −", "Removed lines."),
            ("hl", "Reviewer comments", "Appear under the line they were left on. Select one to expand it, or open the finding and its evidence."),
            ("mute", "Side by side", "Shows before and after next to each other. Unified stacks them in one column."),
        ),
    },
    "raw_comment": {
        "kind": "Evidence", "tone": "hl", "title": "Raw comment source",
        "lead": 'The reviewer’s comment <b class="hl">exactly as captured</b>, with no formatting applied.',
        "points": (
            ("ok", "Why it is kept", "It is the original record. Markdown, HTML and badges stay visible so nothing was rewritten."),
            ("hl", "The readable version", "Reviewer comment above shows the same text formatted."),
        ),
        "note": "Use Copy to take the original text.",
    },
    "revisions": {
        "kind": "Pinned input", "tone": "hl", "title": "Commit revisions",
        "lead": 'The exact versions of the code this review was run on, as <b class="hl">Git commit IDs</b>.',
        "points": (
            ("hl", "Base", "The code before the pull request."),
            ("hl", "Pinned head", "The last commit of the pull request, fixed when the benchmark started."),
            ("ok", "Same input", "Reviewers with the same pair of commits read exactly the same diff."),
        ),
        "note": "Select a commit to open it on GitHub.",
    },
    "severity": {
        "kind": "Reviewer label", "tone": "warn", "title": "Reviewer severity",
        "lead": 'The severity <b class="warn">the reviewer itself</b> attached to its comment.',
        "points": (
            ("mute", "Not scored", "It is the reviewer’s own opinion and does not change the verdict."),
            ("hl", "Different from the rubric", "Severe findings missed uses the rubric’s severity, not this one."),
        ),
    },
}

_KEYS = {"Precision": "precision", "Reference recall": "recall", "False positives": "false_positives",
         "Severe findings missed": "severe_missed", "Review completion": "review_completion",
         "First verified finding": "first_verified", "Observed cost": "observed_cost",
         "Additional findings": "additional", "Context requests": "context_requests", "Duplicates": "duplicates",
         "Unresolved": "unresolved", "Ungraded": "ungraded"}


def info(key: str, label: str | None = None) -> str:
    """A button that opens the explanation for `key`. Hidden until the script confirms popover support."""
    key = _KEYS.get(key, key)
    title = escape(label or _ENTRIES[key]["title"], quote=True)
    return (f'<button class="info-trigger" type="button" data-info="{key}" aria-haspopup="dialog" '
            f'aria-expanded="false" aria-label="About {title}" hidden>{icon("info")}</button>')


def _body(entry: dict) -> str:
    parts = [f'<header class="info-head"><span class="info-badge t-{entry["tone"]}">{icon("info")}</span>'
             f'<div><p class="info-kind">{escape(entry["kind"])}</p><h4 class="info-title">{escape(entry["title"])}</h4></div></header>',
             f'<p class="info-lead">{entry["lead"]}</p>']
    if formula := entry.get("formula"):
        (top, top_note), (bottom, bottom_note) = formula
        parts.append('<div class="info-formula" aria-label="How it is calculated">'
                     f'<span class="info-term t-{entry["tone"]}"><strong>{escape(top)}</strong><small>{escape(top_note)}</small></span>'
                     '<span class="info-rule" aria-hidden="true"></span>'
                     f'<span class="info-term"><strong>{escape(bottom)}</strong><small>{escape(bottom_note)}</small></span></div>')
    if points := entry.get("points"):
        parts.append('<ul class="info-points">')
        for mark, label, text in points:
            parts.append(f'<li class="t-{mark}"><span class="info-dot" aria-hidden="true"></span>'
                         f'<div><strong>{escape(label)}</strong><span>{escape(text)}</span></div></li>')
        parts.append('</ul>')
    if note := entry.get("note"):
        parts.append(f'<p class="info-note">{escape(note)}</p>')
    return "".join(parts)


def info_templates() -> str:
    return "".join(f'<template data-info-template="{key}">{_body(entry)}</template>' for key, entry in _ENTRIES.items())
