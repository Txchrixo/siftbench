"""Operational and paired campaign views for self-contained reports."""

from dataclasses import dataclass
from html import escape
from importlib.resources import files
from pathlib import Path

from . import (_SELECT_CHEVRON, _csp_hash, _encoded, _label, _link, _pull_number,
               _ratio, _report_styles, _reviewer, _reviewer_display)
from .icons import icon
from ..analysis import campaign_metrics
from ..manifest import canonical_json


_METRICS = (
    ("Precision", "Share of scored findings confirmed"),
    ("Reference recall", "Expected findings detected"),
    ("False positives", "Per graded pull request"),
    ("Severe findings missed", "High or critical references on graded PRs"),
    ("Review completion", "Time until the review was published"),
    ("First verified finding", "Time until the first confirmed finding was published"),
    ("Observed cost", "Median per attempt, in currency minor units"),
)

_MISSING_LABELS = {
    "Precision": "No scored findings",
    "Reference recall": "No reference findings to score",
    "Severe findings missed": "No graded high or critical references",
}


@dataclass(frozen=True)
class _MetricState:
    label: str
    detail: str = ""
    incomplete: bool = False


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _false_positive_reading(metrics: dict, *, paired: bool) -> tuple[str, str] | _MetricState:
    reading = metrics["false_positives_per_graded_pr"]
    graded, completed = reading["graded_prs"], metrics["completed"]
    if not completed:
        return _MetricState("No matched reviews" if paired else "No completed reviews")
    if not graded:
        return _MetricState("Grading incomplete", f'0 of {_plural(completed, "completed PR")} graded', incomplete=True)
    detail = (f'Across {_plural(graded, "graded PR")}' if graded == completed
              else f'{graded} of {_plural(completed, "completed PR")} graded')
    return _plural(reading["false_positives"], "false positive"), detail


def _latency(stats: dict) -> tuple[str, str] | None:
    if stats["n"]:
        return f'{stats["p50"]:g} s', f'Median · p95 {stats["p95"]:g} s · n={stats["n"]}'
    return None


def _fraction(numerator: int, denominator: int, unit: str, *, percent: bool = False) -> tuple[str, str] | None:
    if not denominator:
        return None
    count = f"{numerator} / {denominator}"
    return (f"{numerator / denominator:.0%}", count) if percent else (count, unit)


def _values(metrics: dict, *, paired: bool = False) -> tuple:
    severe = metrics["high_severity"]
    costs = tuple((f'{currency} {stats["p50"]:g}', f'n={stats["n"]}')
                  for currency, stats in metrics["observed_cost_minor"].items())
    precision = _fraction(metrics["matched"] + metrics["novel"], metrics["adjudicated"], "", percent=True)
    recall = _fraction(metrics["matched"], metrics["expected"], "", percent=True)
    grading_pending = any(metrics["verdict_counts"].get(key) for key in ("ungraded", "unresolved"))
    if not metrics["completed"]:
        precision = recall = _MetricState("No matched reviews" if paired else "No completed reviews")
    elif grading_pending:
        precision = (precision[0], f"{precision[1]} · Provisional") if precision else _MetricState("Grading incomplete", incomplete=True)
        if recall:
            recall = recall[0], f"{recall[1]} · Provisional"
    return (
        precision, recall,
        _false_positive_reading(metrics, paired=paired),
        _fraction(severe["missed"], severe["expected"], "Missed / expected"),
        _latency(metrics["completion_seconds"]),
        _latency(metrics["first_verified_finding_seconds"]),
        costs or None,
    )


def _value(value: tuple | _MetricState | None, label: str) -> str:
    if isinstance(value, _MetricState) or value is None:
        status = value if isinstance(value, _MetricState) else _MetricState(_MISSING_LABELS.get(label, "Not measured"))
        state_class = " metric-state-incomplete" if status.incomplete else ""
        detail = f'<small>{escape(status.detail)}</small>' if status.detail else ""
        return (f'<span class="metric-reading metric-state{state_class}">'
                f'<strong>{escape(status.label)}</strong>{detail}</span>')
    records = (value,) if isinstance(value[0], str) else value
    return "".join(f'<span class="metric-reading"><strong>{escape(main)}</strong>'
                   f'<small>{escape(detail)}</small></span>' for main, detail in records)


def operational_summary(bundle: dict) -> list[str]:
    lines = ["", "## Operational metrics", ""]
    for row in campaign_metrics(bundle, reviewer_names=True)["reviewers"]:
        lines.extend((f'### {_reviewer(row["reviewer"])}', ""))
        for (label, _), value in zip(_METRICS[2:], _values(row["operations"])[2:], strict=True):
            if isinstance(value, _MetricState):
                reading = " · ".join(filter(None, (value.label, value.detail)))
            elif value is None:
                reading = _MISSING_LABELS.get(label, "Not measured")
            else:
                values = (value,) if isinstance(value[0], str) else value
                reading = " / ".join(f"{main} ({detail})" for main, detail in values)
            lines.append(f"- {label}: {reading}")
        lines.append("")
    return lines


def _matrix(headers: list[tuple[str, str]], values: list[tuple], *, paired: bool = False,
            operational: bool = False) -> str:
    measured = []
    unavailable = []
    for index, (label, note) in enumerate(_METRICS):
        if operational and index < 2:
            continue
        readings = [column[index] for column in values]
        observed = any(any(value is not None for value in reading) if paired else reading is not None
                       for reading in readings)
        if not observed:
            unavailable.append(label)
            continue
        row = [f'<tr><th scope="row">{label}<span class="coverage-case">{note}</span></th>']
        for reading in readings:
            if paired:
                before, after = reading
                row.append('<td><div class="metric-transition">'
                           f'<div><span class="metric-stage">Before</span>{_value(before, label)}</div>'
                           f'{icon("compare")}<div><span class="metric-stage">After</span>{_value(after, label)}</div></div></td>')
            else:
                row.append(f'<td>{_value(reading, label)}</td>')
        measured.append("".join(row) + "</tr>")
    html = ['<div class="metric-panel">']
    if measured:
        if len(headers) > 1:
            html.append('<p class="metric-scroll-hint subtle">Scroll sideways to see more reviewers.</p>')
        html.append('<div class="scroll analysis-table" tabindex="0" role="region" aria-label="Metrics by reviewer">'
                    '<table><thead><tr><th scope="col">Metric</th>')
        for reviewer, detail in headers:
            html.append(f'<th scope="col">{_reviewer_display(reviewer)}<span class="coverage-case">{escape(detail)}</span></th>')
        html.extend(("</tr></thead><tbody>", *measured, "</tbody></table></div>"))
    not_measured = [label for label in unavailable if label not in _MISSING_LABELS]
    if unavailable:
        html.append('<div class="metric-notes">')
        if not_measured:
            html.append('<p class="metric-note"><strong>Not measured</strong>'
                        f'<span>{escape(" · ".join(not_measured))}</span></p>')
        for label in unavailable:
            if label in _MISSING_LABELS:
                html.append(f'<p class="metric-note"><strong>{label}</strong><span>{_MISSING_LABELS[label]}</span></p>')
        html.append("</div>")
    html.append("</div>")
    return "".join(html)


def _table(label: str, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    columns = "".join(f'<th scope="col">{header}</th>' for header in headers)
    body = "".join(f'<tr><th scope="row">{row[0]}</th>'
                   + "".join(f'<td>{cell}</td>' for cell in row[1:]) + "</tr>" for row in rows)
    return (f'<div class="scroll analysis-table" tabindex="0" role="region" aria-label="{label}">'
            f'<table><thead><tr>{columns}</tr></thead><tbody>{body}</tbody></table></div>')


def operational_metrics(bundle: dict) -> str:
    metrics = campaign_metrics(bundle, reviewer_names=True)
    reviewers = metrics["reviewers"]
    headers = [(row["reviewer"], f'{row["operations"]["completed"]}/{row["operations"]["planned"]} completed')
               for row in reviewers]
    values = [_values(row["operations"]) for row in reviewers]
    html = ['<section id="operational-metrics" class="analysis-section" aria-labelledby="operations-heading">'
            '<div class="analysis-heading"><h2 id="operations-heading">Operational metrics</h2>'
            '<p class="subtle">Review quality, timing and measured costs across all attempts, including repeats.</p></div>',
            _matrix(headers, values, operational=True)]
    repeats = [(_reviewer_display(row["reviewer"]), str(repeat["repeat_index"] + 1),
                f'{repeat["completed"]} / {repeat["planned"]}',
                _ratio(repeat["matched"], repeat["expected"], empty="No completed reviews" if not repeat["completed"] else "No reference findings"))
               for row in reviewers if len(row["repeats"]) > 1 for repeat in row["repeats"]]
    if repeats:
        html.append('<details class="analysis-disclosure"><summary>Results by repetition</summary>'
                    + _table("Results by repetition", ("Reviewer", "Repeat", "Completed", "Reference recall"), repeats)
                    + "</details>")
    if metrics["pairwise"]:
        rows = []
        for pair in metrics["pairwise"]:
            peers = pair["reviewers"]
            rows.append((" <span class='subtle'>/</span> ".join(_reviewer_display(peer["reviewer"]) for peer in peers),
                         f'{pair["completed_in_both"]} / {pair["planned_in_both"]}',
                         _peer_scores(peers, index=0), _peer_scores(peers, index=1)))
        html.append('<details class="analysis-disclosure"><summary>Pairwise scores on matched samples</summary>'
                    + _table("Pairwise scores", ("Reviewer pair", "Matched samples", "Precision", "Reference recall"), rows)
                    + "</details>")
    return "".join(html) + "</section>"


def _peer_scores(peers: list[dict], *, index: int) -> str:
    result = []
    for peer in peers:
        metrics = peer["metrics"]
        value = _values(metrics, paired=True)[index]
        label = _METRICS[index][0]
        result.append(f'<div class="peer-score"><small>{escape(_reviewer(peer["reviewer"]))}</small>{_value(value, label)}</div>')
    return "".join(result)


def _attempt(case: dict, stage: str, provider: str, *, fixture: bool) -> str:
    state = case[f"{stage}_status"]
    raw_url = case[f"{stage}_pr"]
    href = None if fixture else _link(raw_url)
    source = ""
    if href:
        number = _pull_number(raw_url)
        label = f"PR #{number}" if number else "Pull request"
        accessible = escape(f'{stage.capitalize()} · {_reviewer(provider)} · {label} · {case.get("case_title") or case["case_id"]}', quote=True)
        source = f'<a href="{href}" aria-label="{accessible}">{label}{icon("external")}</a>'
    elif not fixture:
        source = '<span class="sample-source-note subtle">PR link not recorded</span>'
    return (f'<div class="sample-attempt"><span class="metric-stage">{stage.capitalize()}</span>'
            f'<span class="state {escape(state, quote=True)}">{escape(_label(state))}</span>{source}</div>')


def _coverage(result: dict) -> str:
    html = ['<section id="campaign-coverage" class="analysis-section" aria-labelledby="campaign-coverage-title">'
            '<div class="analysis-heading"><h2 id="campaign-coverage-title">Pull request coverage</h2>'
            '<p class="subtle">Check which reviews completed and open their original GitHub pull requests.</p></div>',
            '<div class="coverage-filters" hidden><div><label for="coverage-reviewer">Reviewer</label>'
            '<span class="select-field"><select id="coverage-reviewer">'
            '<button type="button"><selectedcontent></selectedcontent></button>']
    for index, row in enumerate(result["reviewers"]):
        html.append(f'<option value="{index}"><span class="option-check" aria-hidden="true">{icon("check")}</span>'
                    f'{_reviewer_display(row["reviewer"])}</option>')
    html.append(f'</select>{_SELECT_CHEVRON}</span></div>'
                '<label class="coverage-filter-check"><input type="checkbox" id="coverage-changes">Only status changes</label></div>'
                '<p class="visually-hidden" id="coverage-filter-status" role="status"></p>')
    repeated = any(case["repeat_index"] for row in result["reviewers"] for case in row["cases"])
    for index, row in enumerate(result["reviewers"]):
        provider = row["reviewer"]
        count = sum(case["before_status"] != case["after_status"] for case in row["cases"])
        html.append(f'<div class="sample-panel" data-reviewer-index="{index}"><div class="sample-panel-heading">'
                    f'<h3>{_reviewer_display(provider)}</h3><span class="subtle">{count} status {"change" if count == 1 else "changes"}</span></div>'
                    '<ul class="sample-list">')
        for case in row["cases"]:
            case_id = case["case_id"]
            title = case.get("case_title") or (f"Case {case_id}" if case_id.isdigit() else _label(case_id))
            context = _label(case["condition"])
            if repeated:
                context += f' · Repeat {case["repeat_index"] + 1}'
            changed = case["before_status"] != case["after_status"]
            html.append(f'<li class="sample-row" data-changed="{str(changed).lower()}">'
                        f'<div class="sample-title">{icon("pr")}<div><strong>{escape(title)}</strong>'
                        f'<span class="coverage-case">{escape(context)}</span></div></div>'
                        + _attempt(case, "before", provider, fixture=result["fixture"])
                        + _attempt(case, "after", provider, fixture=result["fixture"]) + "</li>")
        html.append('</ul><p class="sample-empty subtle" hidden>No matching pull requests.</p></div>')
    return "".join(html) + "</section>"


def _settings(result: dict) -> str:
    html = ['<details class="analysis-disclosure" id="campaign-settings"><summary>Recorded reviewer settings</summary>'
            '<div class="campaign-pair">']
    for side in ("before", "after"):
        html.append(f'<section><h3>{side.capitalize()}</h3>')
        if not result[f"{side}_configuration"]:
            html.append('<p class="subtle">This campaign bundle does not record reviewer settings.</p>')
        for row in result[f"{side}_configuration"]:
            html.append(f'<div class="recorded-config">{_reviewer_display(row["provider"])}'
                        f'<span class="coverage-case">{escape(_label(row.get("condition")))}</span><dl>')
            for name, key in (("Trigger", "trigger"), ("Completion signal", "completion_policy"),
                              ("Check", "check"), ("Vendor version", "vendor_version")):
                value = row[key]
                if key == "check" and not value and row["completion_policy"] == "review":
                    value = "GitHub review used"
                html.append(f'<div><dt>{name}</dt><dd>{escape(str(value or "Not recorded"))}</dd></div>')
            html.append("</dl></div>")
        html.append("</section>")
    return "".join(html) + "</div></details>"


def write_comparison(result: dict, out: Path) -> None:
    css = _report_styles(operational=True)
    script = files(__package__).joinpath("analysis.js").read_text(encoding="utf-8")
    policy = (f"default-src 'none'; style-src 'sha256-{_csp_hash(css)}'; script-src 'sha256-{_csp_hash(script)}'; "
              "img-src data:; font-src data:; base-uri 'none'; form-action 'none'")
    html = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta http-equiv="Content-Security-Policy" content="{policy}">',
            f'<title>Compare campaigns | SiftBench</title><style>{css}</style></head>',
            '<body><a class="skip-link" href="#main">Skip to report</a><header class="shell"><div class="shell-inner">',
            f'<a class="brand" href="#main"><img alt="" src="data:image/svg+xml;base64,{_encoded("mark-on-dark.svg")}">SiftBench</a>',
            f'<nav aria-label="Campaign comparison"><a href="#matched-metrics">{icon("compare")}Metrics</a>'
            f'<a href="#campaign-coverage">{icon("coverage")}Coverage</a></nav></div></header>',
            '<main id="main" class="campaign-comparison" tabindex="-1"><header class="analysis-heading campaign-heading">',
            '<h1>Compare campaigns</h1>',
            f'<p class="subtle">{result["planned_samples"]} pinned samples across {len(result["reviewers"])} shared '
            f'{"reviewers" if len(result["reviewers"]) != 1 else "reviewer"}'
            f'{" · Fixture data" if result["fixture"] else ""}</p></header><dl class="campaign-pair">']
    for side in ("before", "after"):
        coverage = [row[f"{side}_coverage"] for row in result["reviewers"]]
        done, planned = sum(row["completed"] for row in coverage), sum(row["planned"] for row in coverage)
        html.append(f'<div><dt>{side.capitalize()}</dt><dd><code>{escape(result[f"{side}_run"])}</code>'
                    f'<span class="campaign-total"><strong>{done} / {planned}</strong> completed in shared scope</span></dd></div>')
    html.append('</dl><section id="matched-metrics" class="analysis-section" aria-labelledby="matched-metrics-title">'
                '<div class="analysis-heading"><h2 id="matched-metrics-title">Matched metrics</h2>'
                '<p class="subtle">Each reviewer is compared on the same pull requests and repetitions completed in both campaigns.</p></div>')
    reviewers = result["reviewers"]
    headers = [(row["reviewer"], f'{row["completed_in_both"]} / {row["planned_in_both"]} matched completions') for row in reviewers]
    values = [tuple(zip(_values(row["before"], paired=True), _values(row["after"], paired=True), strict=True))
              for row in reviewers]
    html.extend((_matrix(headers, values, paired=True), "</section>", _coverage(result), _settings(result),
                 f'</main><footer class="report-footer"><img alt="SiftBench" width="270" height="64" '
                 f'src="data:image/svg+xml;base64,{_encoded("footer-wordmark.svg")}"></footer>',
                 f"<script>{script}</script></body></html>"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_bytes(canonical_json(result) + b"\n")
    (out / "comparison.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")
