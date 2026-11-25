import base64
import hashlib
import json
import re
from math import isfinite
from html import escape
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from ..grading import DECISIONS, EVIDENCE_TYPES, SEVERITIES, ScoreCalculator
from ..manifest import canonical_json, digest, identifier
from ..store import StoreError
from .comment import blocks, inline, review_text
from .context import publication_text, validate_context
from .diff import render_diff, render_file_patch
from .icons import icon
from .info import info, info_templates
from ..stats import percent_bounds, percent_interval
from .issues import issue_rows


_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_REVIEWER_MARKS = {"greptile": "png", "proofline": "png", "coderabbit": "svg"}
_STATES = frozenset({
    "planned", "preparing", "awaiting_trigger", "awaiting_review", "completed",
    "invalid_input", "needs_attention", "timed_out", "failed", "quota",
})
def _footer(navigation: bool = True) -> str:
    return ('<footer class="report-footer"><div class="footer-top"><p class="footer-tagline">'
            'Evidence-first benchmark for AI code reviewers.</p>'
            + ('<nav aria-label="Footer"><a href="#overview">Overview</a><a href="#coverage">Coverage</a>'
               '<a href="#comparison">Compare</a></nav>' if navigation else '') +
            '<p class="footer-legal">© 2026 SiftBench. Reviewer names and logos belong to their owners.</p></div>'
            f'<img class="footer-wordmark" alt="SiftBench" width="270" height="64" '
            f'src="data:image/svg+xml;base64,{_encoded("footer-wordmark.svg")}"></footer>')


_SELECT_CHEVRON = ('<svg class="select-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true" '
                   'focusable="false"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" '
                   'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _link(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme == "https" and parts.netloc == "github.com" and parts.path.startswith("/"):
        return escape(url, quote=True)
    return None


def _pull_number(url: str | None) -> str:
    if not url or not _link(url):
        return ""
    match = re.fullmatch(r"/[^/]+/[^/]+/pull/([0-9]+)", urlsplit(url).path)
    return match[1] if match else ""


def _ratio(numerator: int, denominator: int, *, empty: str = "No reference findings") -> str:
    return f"{numerator}/{denominator} ({numerator / denominator:.0%})" if denominator else empty


def _with_interval(text: str) -> str:
    """Add a 95% interval to a "k/n (p%)" reading. Other readings pass through unchanged."""
    match = re.fullmatch(r"([0-9]+)/([0-9]+) \((.+)\)", text)
    interval = percent_interval(int(match[1]), int(match[2])) if match else None
    return f"{match[1]}/{match[2]} ({match[3]}, {interval})" if interval else text


_METRIC_NOUNS = {"Precision": "findings confirmed as real problems",
                 "Reference recall": "expected problems found"}
_SCALE_ENDS = {"Precision": ("no finding confirmed", "every finding confirmed"),
               "Reference recall": ("no expected problem found", "every expected problem found")}


def _tip(**values: str) -> str:
    """Data attributes read by the shared tooltip script. Empty values are left out and all text is escaped."""
    return " ".join(f'data-range-{key}="{escape(value, quote=True)}"' for key, value in values.items() if value)


def _range_tips(successes: int, total: int, title: str, metric: str) -> dict[str, str]:
    """One explanation per part of the bar: the scale, the likely range and the observed score."""
    low, high = percent_bounds(successes, total)
    width = high - low
    verdict = ("Wide: there are too few observations to be sure of this score." if width >= 40
               else "Moderate: the score is a rough guide." if width >= 20
               else "Narrow: the score is fairly well supported.")
    left, right = _SCALE_ENDS.get(metric, ("none", "all"))
    noun = _METRIC_NOUNS.get(metric, "observations")
    score = round(successes / total * 100)
    return {
        "score": _tip(title=f"{title} · observed score", main=f"{score}%", detail=f"{successes} of {total} {noun}",
                      help="What was actually measured. It is only an estimate of the true rate."),
        "range": _tip(title=f"{title} · likely range", main=f"{low}-{high}%", detail="95% confidence interval",
                      help=f"The true rate could plausibly be anywhere in this range. {verdict}"),
        "scale": _tip(title=f"{title} · scale", main="0 to 100%", detail=f"Left end 0%: {left}. Right end 100%: {right}.",
                      help="The shorter the shaded range, the more reliable the score."),
        "summary": _tip(title=title, main=f"{score}%", detail=f"{successes} of {total} {noun}",
                        range=f"Likely {low}-{high}%", help=f"The true rate could plausibly be anywhere in that range. {verdict}"),
    }


def _range_bar(successes: int, total: int, *, title: str = "", metric: str = "") -> str:
    """A 0-100% bar. Its track, shaded range and score marker each explain themselves on hover."""
    bounds = percent_bounds(successes, total)
    if not bounds:
        return ""
    low, high = bounds
    point = min(max(round(successes / total * 100) - 1, 0), 98)
    tips = _range_tips(successes, total, title or metric, metric)
    return ('<span class="range"><span class="range-plot" tabindex="0" data-range-focus-only '
            f'{tips["summary"]}><svg class="range-bar" viewBox="0 0 100 16" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            f'<g class="range-part range-scale" {tips["scale"]}><rect class="range-track" x="0" y="7.5" width="100" height="1"/>'
            '<rect class="range-hit" x="0" y="0" width="100" height="16"/></g>'
            f'<g class="range-part range-span" {tips["range"]}><rect class="range-band" x="{low}" y="5" width="{max(high - low, 1)}" height="6" rx="1"/>'
            f'<rect class="range-hit" x="{low}" y="0" width="{max(high - low, 1)}" height="16"/></g>'
            f'<g class="range-part range-score" {tips["score"]}><rect class="range-point" x="{point}" y="2" width="2" height="12"/>'
            f'<rect class="range-hit" x="{max(point - 2, 0)}" y="0" width="6" height="16"/></g></svg></span>'
            f'<span class="range-label">Likely {low}-{high}%<span class="visually-hidden"> (95% confidence interval)</span></span></span>')


def _counts(text: str) -> tuple[int, int] | None:
    match = re.match(r"([0-9]+)/([0-9]+) \(", text)
    return (int(match[1]), int(match[2])) if match else None


def _interval_chart(rows: list[tuple[str, ...]]) -> str:
    """One shared 0-100% axis per metric, so overlapping ranges are visible across reviewers."""
    metrics = (("Precision", 1), ("Reference recall", 2))
    if not any(_counts(row[index]) for row in rows for _, index in metrics):
        return ""
    ticks = "".join(f"<li>{value}%</li>" for value in (0, 25, 50, 75, 100))
    out = ['<figure class="interval-figure" aria-labelledby="interval-title"><figcaption>'
           f'<h3><span id="interval-title">How sure are these scores?</span>{info("interval")}</h3>'
           '<p class="subtle">The line is the likely range and the dot is the observed score. '
           'Hover any part to see what it means. Where lines overlap, the data cannot tell the reviewers apart.</p></figcaption>'
           '<div class="interval-grid"><span class="interval-corner"></span>']
    out.extend(f'<h4 class="interval-heading">{label}</h4>' for label, _ in metrics)

    def x(value: float) -> float:
        return 10 + 280 * value / 100

    for row in rows:
        out.append(f'<div class="interval-row" data-reviewer="{escape(row[0], quote=True)}"><div class="interval-name">{_reviewer_display(row[0])}</div>')
        for label, index in metrics:
            counts = _counts(row[index])
            if not counts:
                out.append(f'<div class="interval-cell" data-metric="{label}"><span class="subtle interval-empty">{escape(row[index])}</span></div>')
                continue
            low, high = percent_bounds(*counts)
            score = round(counts[0] / counts[1] * 100)
            tips = _range_tips(*counts, f"{_reviewer(row[0])} · {label}", label)
            summary = escape(f"{_reviewer(row[0])}, {label}: {score}%, likely {low} to {high}%", quote=True)
            out.append(
                f'<div class="interval-cell" data-metric="{label}"><svg class="interval-plot" viewBox="0 0 300 26" aria-hidden="true" focusable="false">'
                f'<g class="interval-part interval-scale" {tips["scale"]}>'
                + "".join(f'<line class="interval-grid-line" x1="{x(v):g}" x2="{x(v):g}" y1="2" y2="24"/>' for v in (0, 25, 50, 75, 100))
                + '<rect class="interval-hit" x="0" y="0" width="300" height="26"/></g>'
                f'<g class="interval-part interval-span" {tips["range"]}>'
                f'<line class="interval-whisker" x1="{x(low):g}" x2="{x(high):g}" y1="13" y2="13"/>'
                f'<line class="interval-cap" x1="{x(low):g}" x2="{x(low):g}" y1="8" y2="18"/>'
                f'<line class="interval-cap" x1="{x(high):g}" x2="{x(high):g}" y1="8" y2="18"/>'
                f'<rect class="interval-hit" x="{x(low) - 3:g}" y="4" width="{x(high) - x(low) + 6:g}" height="18"/></g>'
                f'<g class="interval-part interval-score" {tips["score"]}><circle class="interval-dot" cx="{x(score):g}" cy="13" r="5"/>'
                f'<circle class="interval-hit" cx="{x(score):g}" cy="13" r="10"/></g></svg>'
                f'<span class="interval-focus" tabindex="0" data-range-focus-only {tips["summary"]} aria-label="{summary}"></span>'
                f'<span class="interval-value">{score}% <span class="subtle">Likely {low}-{high}%</span></span></div>')
        out.append('</div>')
    out.append('<span class="interval-corner"></span>'
               + "".join(f'<ol class="interval-ticks subtle" aria-hidden="true">{ticks}</ol>' for _ in metrics)
               + '</div></figure>')
    return "".join(out)


def _label(value: str | None) -> str:
    return "Ungraded" if value is None else value.replace("_", " ").capitalize()


def _review_status(pair: dict) -> str:
    if pair.get("publication") and pair["status"] != "completed":
        return "Formal review missing"
    return _label(pair["status"])


def _inline_comment_count(pair: dict) -> str:
    count = sum((claim.get("source_object_id") or "").startswith("inline:") for claim in pair["claims"])
    return f'{count} inline {"comment" if count == 1 else "comments"}'


def _grading_pending(pair: dict) -> bool:
    return any(claim["decision"] in {None, "unresolved"} for claim in pair["claims"])


def _attempt_status(pair: dict | None) -> str:
    return _review_status(pair) if pair else "Not planned for this reviewer"


def _report_notice(glyph: str, title: str, detail: str, action: str = "") -> str:
    return (f'<div class="report-notice">{icon(glyph)}<div><strong>{escape(title)}</strong>'
            f'<p class="subtle">{escape(detail)}</p>{action}</div></div>')


def _validate(bundle: dict) -> None:
    try:
        if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1 or type(bundle["fixture"]) is not bool:
            raise StoreError("Unsupported results bundle")
        identifier(bundle["run_id"], "run_id")
        seen_pairs, seen_pair_ids, seen_claims = set(), set(), set()
        if not isinstance(bundle["pairs"], list):
            raise StoreError("Results pairs must be an array")
        for pair in bundle["pairs"]:
            key = _case_key(pair), identifier(pair["provider_id"], "provider_id")
            for field in ("case_id", "condition"):
                identifier(pair[field], field)
            if "case_title" in pair and not isinstance(pair["case_title"], str):
                raise StoreError("Invalid case title")
            if "input_digest" in pair and not _DIGEST.fullmatch(pair["input_digest"]):
                raise StoreError("Invalid pinned input digest")
            if "timing" in pair:
                timing = pair["timing"]
                allowed = {"started_at", "review_started_at", "deadline_at", "first_completion_at", "frozen_at", "published_at"}
                if not isinstance(timing, dict) or not timing.keys() <= allowed or any(
                    value is not None and (type(value) not in (int, float) or not isfinite(value) or value < 0)
                    for value in timing.values()
                ):
                    raise StoreError("Invalid attempt timing")
            if "cost" in pair:
                cost = pair["cost"]
                if (set(cost) != {"currency", "amount_minor", "source_digest"} or not re.fullmatch(r"[A-Z]{3}", cost["currency"])
                        or type(cost["amount_minor"]) is not int or not 0 <= cost["amount_minor"] <= 10 ** 12
                        or not _DIGEST.fullmatch(cost["source_digest"])):
                    raise StoreError("Invalid measured cost")
            if (type(pair["repeat_index"]) is not int or pair["repeat_index"] < 0 or pair["status"] not in _STATES
                    or not _DIGEST.fullmatch(pair["pair_id"]) or key in seen_pairs or pair["pair_id"] in seen_pair_ids):
                raise StoreError("Invalid or duplicate results pair")
            seen_pairs.add(key)
            seen_pair_ids.add(pair["pair_id"])
            if any(field in pair for field in ("base_sha", "head_sha")) and (
                not all(_COMMIT_SHA.fullmatch(pair.get(field, "")) for field in ("base_sha", "head_sha"))
                or pair["base_sha"] == pair["head_sha"]
            ):
                raise StoreError("Invalid pinned commit pair")
            if not isinstance(pair["claims"], list) or not isinstance(pair["expected"], list):
                raise StoreError("Findings and claims must be arrays")
            if pair["status"] != "completed" and pair["claims"]:
                raise StoreError("Incomplete review cannot contain frozen claims")
            expected = {identifier(finding["expected_id"], "expected_id") for finding in pair["expected"]}
            if len(expected) != len(pair["expected"]):
                raise StoreError("Duplicate expected finding")
            for claim in pair["claims"]:
                if (not _DIGEST.fullmatch(claim["claim_id"]) or claim["claim_id"] in seen_claims
                        or not isinstance(claim["body"], str)
                        or claim.get("source_locator") is not None and not isinstance(claim["source_locator"], str)
                        or claim["decision"] not in DECISIONS | {None}
                        or (claim["rationale"] is not None and not isinstance(claim["rationale"], str))
                        or not isinstance(claim["evidence"], list)
                        or claim["decision"] == "confirmed_expected" and claim["expected_id"] not in expected):
                    raise StoreError("Invalid or duplicate claim")
                if "diff_hunk" in claim and (
                    not isinstance(claim["diff_hunk"], str) or not 0 < len(claim["diff_hunk"]) <= 16_384
                ):
                    raise StoreError("Invalid captured diff hunk")
                if "diff_side" in claim and claim["diff_side"] not in ("LEFT", "RIGHT"):
                    raise StoreError("Invalid captured diff side")
                published = claim.get("published_at")
                if published is not None and (type(published) not in (int, float) or not isfinite(published) or published < 0):
                    raise StoreError("Invalid claim publication time")
                seen_claims.add(claim["claim_id"])
                for piece in claim["evidence"]:
                    if piece["type"] not in EVIDENCE_TYPES or digest(piece["text"].encode()) != piece["digest"]:
                        raise StoreError("Invalid evidence or digest mismatch")
            if any(not isinstance(finding["matching_criteria"], str) for finding in pair["expected"]):
                raise StoreError("Expected findings need text criteria")
            if any("severity" in finding and finding["severity"] not in SEVERITIES for finding in pair["expected"]):
                raise StoreError("Invalid reference severity")
        validate_context(bundle)
    except (AttributeError, KeyError, TypeError) as error:
        raise StoreError("Invalid results bundle structure") from error


def _case_key(pair: dict) -> str:
    return f"{pair['case_id']}:{pair['condition']}:{pair['repeat_index']}"


def _case_name(case_id: str) -> str:
    return f"Case {case_id}" if case_id.isdigit() else _label(case_id)


def _case_title(pair: dict) -> str:
    return (pair.get("case_title") or "").strip() or _case_name(pair["case_id"])


def _case_condition(pair: dict) -> str:
    condition = _label(pair["condition"])
    repeat = f" · Repeat {pair['repeat_index'] + 1}" if pair["repeat_index"] else ""
    return f"{condition}{repeat}"


def _case_display(pair: dict) -> str:
    return f'{_case_name(pair["case_id"])} · {_case_condition(pair)}'


def _case_context(pair: dict, title: str) -> str:
    return _case_condition(pair) if title == _case_name(pair["case_id"]) else _case_display(pair)


def _reviewer(value: str) -> str:
    return {"proofline": "Proofline", "greptile": "Greptile", "coderabbit": "CodeRabbit"}.get(value, value)


def _reviewer_display(provider: str) -> str:
    name = escape(_reviewer(provider))
    mark = f'<span class="reviewer-mark {provider}" aria-hidden="true"></span>' if provider in _REVIEWER_MARKS else ""
    return f'<span class="reviewer-name">{mark}<span class="reviewer-label">{name}</span></span>'


def _encoded(name: str) -> str:
    return base64.b64encode(files(__package__).joinpath(name).read_bytes()).decode("ascii")


def _csp_hash(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode("ascii")


def _report_styles(*, operational: bool = False, assistants: bool = False) -> str:
    root = files(__package__)
    names = ("theme.css", "report.css", "analysis.css") if operational else ("theme.css", "report.css")
    css = "".join(root.joinpath(name).read_text(encoding="utf-8") for name in names)
    css = css.replace("__MANROPE__", _encoded("manrope-latin.woff2"))
    for reviewer, extension in _REVIEWER_MARKS.items():
        css = css.replace(f"__MARK_{reviewer.upper()}__", _encoded(f"reviewer-{reviewer}.{extension}"))
    if assistants:
        for assistant in ("openai", "claude", "mistral"):
            css = css.replace(f"__ASK_{assistant.upper()}__", _encoded(f"assistant-{assistant}.svg"))
    return css


def _score_rows(metrics: dict) -> list[tuple[str, ...]]:
    no_scope = "No shared completed cases"
    return [
        (provider, _ratio(counts["matched"] + counts["novel"], counts["adjudicated"],
                          empty=no_scope if not metrics["shared"] else
                          "Grading incomplete" if counts["ungraded"] or counts["unresolved"] else "No scored findings"),
         _ratio(counts["matched"], counts["expected"], empty="No reference findings" if metrics["shared"] else no_scope),
         *(str(counts[key]) if metrics["shared"] else no_scope for key in (
             "false_positives", "novel", "justified_context_requests", "duplicates", "unresolved", "ungraded")))
        for provider, counts in metrics["providers"].items()
    ]


class ReportBuilder:
    @staticmethod
    def load_bundle(path: Path) -> dict:
        result = json.loads(path.read_bytes())
        _validate(result)
        return result

    def write(self, bundle: dict, output: Path) -> None:
        _validate(bundle)
        output.mkdir(parents=True, exist_ok=True)
        metrics = ScoreCalculator.calculate(bundle)
        rows = _score_rows(metrics)
        shared = ", ".join(f"{case} ({condition})" for case, condition in metrics["shared"]) or "No shared completed cases"
        scope = "Fixture run" if bundle["fixture"] else "Campaign"
        title = f"{scope}: {bundle['run_id']}"
        md = [f"# {title}", "", f"Shared completed cases: {shared}", "", "## Scores", ""]
        if bundle["fixture"]:
            md.extend(("This summary uses local test data.", ""))
        if rows and not metrics["shared"]:
            md.extend(("Scores require at least one case condition completed by every reviewer.", ""))
        if metrics["shared"] and any(int(row[7]) or int(row[8]) for row in rows):
            md.extend(("Scores are provisional while findings still await a verdict.", ""))
        for provider, precision, recall, false, novel, context, duplicates, unresolved, ungraded in (rows if metrics["shared"] else ()):
            md.append(f"- {provider}: precision {_with_interval(precision)}, recall {_with_interval(recall)}, {false} false positives, "
                      f"{novel} novel, {context} justified context requests, {duplicates} duplicates, "
                      f"{unresolved} unresolved, {ungraded} ungraded")
        md.extend(["", "## Coverage", ""])
        for pair in bundle["pairs"]:
            case_title = escape(" ".join((pair.get("case_title") or "").split()))
            case_label = f'{case_title} ({_case_display(pair)})' if case_title else _case_display(pair)
            status = _review_status(pair)
            if pair["status"] == "completed":
                status += " · Grading incomplete" if _grading_pending(pair) else (
                    " · No captured comments" if not pair["claims"] and not pair.get("publication") else "")
            md.append(f"- {case_label} / {_reviewer(pair['provider_id'])}: {status}")

        root = files(__package__)
        operational = bool(rows) and (bundle.get("operational_schema_version") == 1 or any("cost" in pair for pair in bundle["pairs"]))
        if operational:
            from .comparison import operational_summary

            md.extend(operational_summary(bundle))
        css = _report_styles(operational=operational, assistants=True)
        script = root.joinpath("report.js").read_text(encoding="utf-8") + "\n" + root.joinpath("info.js").read_text(encoding="utf-8")
        policy = f"default-src 'none'; style-src 'sha256-{_csp_hash(css)}'; script-src 'sha256-{_csp_hash(script)}'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"
        html = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                f'<meta http-equiv="Content-Security-Policy" content="{policy}">',
                f"<title>{escape(title)} | SiftBench</title><style>{css}</style></head>",
                '<body><a class="skip-link" href="#main">Skip to report</a><header class="shell"><div class="shell-inner">',
                f'<a class="brand" href="#overview"><img alt="" src="data:image/svg+xml;base64,{_encoded("mark-on-dark.svg")}">SiftBench</a>',
                f'<nav aria-label="Report"><a href="#overview">{icon("overview")}Overview</a>'
                f'<a href="#coverage">{icon("coverage")}Coverage</a>'
                f'<a href="#comparison">{icon("compare")}Compare</a></nav></div></header><main id="main" tabindex="-1">',
                '<section id="overview" data-report-view tabindex="-1"><header class="overview-header">'
                '<div><h1>Campaign overview</h1>'
                f'<p class="run-meta"><span>{scope}{info("fixture" if bundle["fixture"] else "campaign")}</span><code>{escape(bundle["run_id"])}</code></p></div>'
                f'<div class="overview-actions"><a href="#coverage">{icon("coverage")}Check coverage</a>'
                f'<a href="#comparison">{icon("compare")}Compare findings</a>'
                f'<button id="ask-benchmark" type="button" aria-haspopup="dialog" hidden>{icon("ask")}Ask about this benchmark</button>'
                '</div></header>']
        groups: dict[str, dict[str, dict]] = {}
        for pair in bundle["pairs"]:
            groups.setdefault(_case_key(pair), {})[pair["provider_id"]] = pair
        providers = list(metrics["providers"])
        completed = sum(pair["status"] == "completed" for pair in bundle["pairs"])
        html.append('<dl class="run-stats" aria-label="Campaign totals">')
        for label, key, value in (("Completed attempts", "completed_attempts", f'{completed} / {len(bundle["pairs"])}'),
                                  ("Scored conditions", "scored_conditions", len(metrics["shared"])),
                                  ("Reviewers", "reviewers", len(providers))):
            html.append(f'<div><dt>{label}{info(key)}</dt><dd>{value}</dd></div>')
        html.append('</dl>')
        case_titles = {(pair["case_id"], pair["condition"]): pair.get("case_title") for pair in bundle["pairs"]
                       if pair["repeat_index"] == 0}
        numbers: dict[tuple[str, str], set[str]] = {}
        for pair in bundle["pairs"]:
            if pair["repeat_index"] == 0 and not bundle["fixture"]:
                numbers.setdefault((pair["case_id"], pair["condition"]), set()).add(_pull_number(pair.get("pr_url")))
        case_titles = {key: (title, " · ".join(f"#{number}" for number in sorted(numbers.get(key, set()) - {""}, key=int)))
                       for key, title in case_titles.items()}
        self._scores(html, metrics["shared"], rows, case_titles, analysis=bundle.get("analysis"))
        if operational:
            self._operations(html, bundle)
        html.append('</section>')
        self._coverage(html, groups, providers, set(metrics["shared"]), fixture=bundle["fixture"])
        self._comparison(html, groups, providers, bundle)
        if rows:
            html.append(f'<!--\n{root.joinpath("LICENSE-LobeIcons.txt").read_text(encoding="utf-8")}\n-->')
            self._ask(html, bundle, rows, metrics["shared"], completed)
        html.append(f'</main>{_footer()}')
        html.append(f"{info_templates()}<script>{script}</script></body></html>")
        (output / "results.json").write_bytes(canonical_json(bundle) + b"\n")
        (output / "report.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")
        (output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
        (output / "OFL-Manrope.txt").write_bytes(root.joinpath("OFL-Manrope.txt").read_bytes())

    @staticmethod
    def _operations(html: list[str], bundle: dict) -> None:
        from .comparison import operational_metrics

        html.append(operational_metrics(bundle))

    @staticmethod
    def _ask(html: list[str], bundle: dict, rows: list[tuple[str, ...]], shared: list[tuple[str, str]], completed: int) -> None:
        pairs = bundle["pairs"]
        publications = [pair for pair in pairs if pair.get("publication")]
        reviewers = [_reviewer(provider) for provider in sorted({pair["provider_id"] for pair in pairs})]
        repeats = max((pair["repeat_index"] for pair in pairs), default=0) + 1
        prompt = [
            "Help me interpret this SiftBench comparison of AI code reviewers. Treat it as a small pilot, not a ranking.", "",
            f"Reviewers: {', '.join(reviewers)}.",
            f"{completed} of {len(pairs)} review attempts completed. Scores cover {len(shared)} shared case conditions "
            "(pull request plus condition) that every reviewer completed. Repeats are excluded from scores.",
        ]
        if bundle["fixture"]:
            prompt.append("This report uses fixture data and does not measure live reviewer performance.")
        if publications:
            included = sum(pair["status"] == "completed" for pair in publications)
            prompt.append(f"A separate publication audit found {len(publications)} additional publications. This report treats {included} of those as completed attempts.")
        if shared:
            titles = {(pair["case_id"], pair["condition"]): _case_title(pair) for pair in pairs if pair["repeat_index"] == 0}
            prompt.extend(["", "Scored case conditions"])
            prompt.extend(f"- {titles.get(key, _case_name(key[0]))} · {_label(key[1])}" for key in shared)
        unfinished = [pair for pair in pairs if pair["status"] != "completed" and pair["repeat_index"] == 0]
        if unfinished:
            prompt.extend(["", "Attempts that did not complete (outside the scores)"])
            prompt.extend(f"- {_reviewer(pair['provider_id'])} · {_case_title(pair)} · {_label(pair['condition'])}: {_review_status(pair)}"
                          for pair in unfinished)
        prompt.extend(["", "Aggregated scores" if shared else "No shared case conditions are ready to score."])
        for provider, precision, recall, false, novel, context, duplicates, unresolved, ungraded in (rows if shared else ()):
            prompt.append(f"- {_reviewer(provider)} · Precision {_with_interval(precision)} · Reference recall {_with_interval(recall)} · Additional findings {novel} · "
                          f"False positives {false} · Justified context requests {context} · Duplicates {duplicates} · Unresolved {unresolved} · Ungraded {ungraded}")
        if shared:
            spread = []
            for provider in sorted({pair["provider_id"] for pair in pairs}):
                counts = [sum(claim["decision"] in {"confirmed_expected", "confirmed_novel", "false_positive"} for claim in pair["claims"])
                          for pair in pairs if pair["provider_id"] == provider and pair["repeat_index"] == 0
                          and pair["status"] == "completed" and (pair["case_id"], pair["condition"]) in set(shared)]
                spread.append(f"- {_reviewer(provider)}: {sum(counts)} findings with a verdict across "
                              f"{sum(1 for count in counts if count)} pull requests (most on one pull request: {max(counts, default=0)})")
            prompt.extend(["", "How findings are spread", *spread])
        prompt.extend(["", "Definitions",
                       "- Precision: confirmed findings (expected plus additional) divided by findings with a verdict (confirmed plus false positives). "
                       "Duplicates, context requests and review messages are excluded.",
                       "- Reference recall: expected findings detected divided by expected findings in a rubric frozen before the reviews.",
                       "- Additional findings: verified real problems outside the rubric.",
                       "- 95% CI: a Wilson interval that counts each finding separately. Findings one reviewer leaves on the same "
                       "pull request are related, so the interval is slightly too narrow when a reviewer has several findings per pull request.", "",
                       "Limits of this benchmark"])
        prompt.append(f"- Only {len(shared)} scored case conditions" + (", with one run each" if repeats == 1 else f", with up to {repeats} runs each")
                      + ". With so few cases, one finding can move a percentage by many points, which is why the 95% intervals are wide.")
        prompt.extend(["- The rubric and public case branches may have been seen by the reviewers beforehand.",
                       "- Verdicts come from a single grader. There is no inter-annotator agreement.",
                       "- Reviewers may have run in different campaigns, so timing and cost are not a controlled comparison."])
        if any(int(row[7]) or int(row[8]) for row in rows):
            prompt.append("- Some findings are unresolved or ungraded, so the scores are provisional.")
        if isinstance(bundle.get("analysis"), str) and bundle["analysis"].strip():
            prompt.extend(["", "Methodology note from the operator", " ".join(bundle["analysis"].split())])
        prompt.extend(["", "Questions",
                       "1. Which differences are larger than the noise you would expect from this sample size, if any?",
                       "2. What can and cannot be concluded about each reviewer from these cases?",
                       "3. What evidence would make a comparison reliable?", "",
                       "Use only the information above. Do not invent figures or generalize beyond the cases tested. "
                       "Say plainly when the data cannot support a conclusion."])
        preview = escape("\n".join(prompt))
        html.append('<dialog id="benchmark-dialog" class="benchmark-dialog" aria-labelledby="benchmark-dialog-title">'
                    '<header class="ask-heading"><div><p class="ask-kind">Assistant prompt</p>'
                    '<h2 id="benchmark-dialog-title">Ask about this benchmark</h2>'
                    '<p class="subtle">Share the aggregate results with an assistant to help interpret them.</p></div>'
                    f'<button id="close-benchmark-dialog" type="button" aria-label="Close" autofocus>{icon("close")}</button></header>'
                    '<ol class="ask-steps"><li><span class="ask-step" aria-hidden="true">1</span><div class="ask-step-body">'
                    '<label for="benchmark-prompt">Copy the prompt</label>'
                    f'<textarea id="benchmark-prompt" readonly rows="10" spellcheck="false">{preview}</textarea>'
                    f'<div class="ask-copy"><button id="copy-benchmark-prompt" type="button">{icon("copy")}Copy prompt</button>'
                    '<p id="benchmark-copy-status" class="subtle" role="status"></p></div></div></li>'
                    '<li><span class="ask-step" aria-hidden="true">2</span><div class="ask-step-body">'
                    '<p class="ask-label" id="ask-destinations-label">Paste it into an assistant</p>'
                    '<div class="ask-destinations" role="group" aria-labelledby="ask-destinations-label">')
        for brand, name, url in (("openai", "ChatGPT", "https://chatgpt.com/"), ("claude", "Claude", "https://claude.ai/new"),
                                 ("mistral", "Mistral Vibe", "https://chat.mistral.ai/")):
            html.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                        f'<span class="assistant-mark {brand}" aria-hidden="true"></span>{name}{icon("external")}</a>')
        html.append('</div></div></li></ol>'
                    '<p class="ask-privacy subtle">The prompt holds aggregate figures and case titles only. '
                    'No review text or source code is included.</p></dialog>')

    @staticmethod
    def _coverage(html: list[str], groups: dict, providers: list[str], shared: set[tuple[str, str]], *, fixture: bool) -> None:
        html.append('<section id="coverage" data-report-view tabindex="-1"><div class="view-heading"><h1>Review coverage</h1>'
                    '<p class="view-intro">Open a reviewer’s status to inspect the attempt, or follow the GitHub links to the original evidence.</p></div>')
        if not groups:
            html.append("<p>No cases to compare.</p></section>")
            return
        pairs = [pair for group in groups.values() for pair in group.values()]
        html.append(f'<div class="coverage-heading"><h2>Cases by reviewer{info("statuses")}</h2><p class="subtle">'
                    'Scored rows contribute to the shared results.</p>')
        if len(providers) > 1:
            html.append('<p class="coverage-scroll-hint subtle">Scroll sideways to see the other reviewers.</p>')
        html.append('</div><div class="scroll coverage-table" tabindex="0" role="region" aria-label="Pull request coverage"><table><thead><tr>'
                    '<th scope="col">Pull request and condition</th>')
        for provider in providers:
            attempts = [pair for pair in pairs if pair["provider_id"] == provider]
            done = sum(pair["status"] == "completed" for pair in attempts)
            html.append(f'<th scope="col">{_reviewer_display(provider)}'
                        f'<span class="coverage-case">{done}/{len(attempts)} completed</span></th>')
        html.append("</tr></thead><tbody>")
        for group in groups.values():
            first = next(iter(group.values()))
            title = _case_title(first)
            context = _case_context(first, title)
            html.append('<tr><th scope="row">')
            html.append(f'<span class="coverage-title">{escape(title)}</span>')
            scored = (first["case_id"], first["condition"]) in shared and not first["repeat_index"]
            scope = "Scored" if scored else "Outside score"
            scored_class = ' is-scored' if scored else ''
            html.append(f'<span class="coverage-case">{escape(context)}</span>'
                        f'<span class="coverage-scope{scored_class}">{scope}{info("scope")}</span></th>')
            for provider in providers:
                pair = group.get(provider)
                if pair:
                    ReportBuilder._coverage_attempt(html, pair, f"{title} · {context}", fixture=fixture)
                else:
                    html.append('<td class="subtle">Not planned for this reviewer</td>')
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    @staticmethod
    def _coverage_attempt(html: list[str], pair: dict, case: str, *, fixture: bool) -> None:
        status = _review_status(pair)
        reviewer = _reviewer(pair["provider_id"])
        label = escape(f"{reviewer}, {case}: {status}", quote=True)
        html.append(f'<td><a class="state {pair["status"]}" href="#pair-{pair["pair_id"]}" aria-label="{label}">'
                    f'{status}</a>')
        if pair["status"] == "completed":
            html.append(f'<p class="coverage-reason subtle">{_inline_comment_count(pair)}</p>')
        elif reason := pair.get("reason"):
            html.append(f'<p class="coverage-reason subtle">{escape(reason)}</p>')
        if pair["status"] == "completed" and _grading_pending(pair):
            html.append('<span class="coverage-grading">Grading incomplete</span>')
        pr_url = _link(pair.get("pr_url")) if not fixture else None
        if pr_url:
            number = _pull_number(pair["pr_url"])
            pr_label = f"PR #{number}" if number else "Pull request"
            name = escape(f"Open {reviewer} {pr_label} on GitHub", quote=True)
            html.append(f'<div class="coverage-links"><a href="{pr_url}" aria-label="{name}">{icon("pr")}{pr_label}</a>')
            if check := _link((pair.get("publication") or {}).get("check_url") or pair.get("capture_check_url")):
                html.append(f'<a href="{check}">{icon("external")}GitHub check</a>')
            elif number and (signal := re.fullmatch(r"review:([0-9]+)", pair.get("capture_signal") or "")):
                review = _link(urlsplit(pair["pr_url"])._replace(fragment=f"pullrequestreview-{signal[1]}").geturl())
                if review:
                    html.append(f'<a href="{review}">{icon("external")}GitHub review</a>')
            html.append('</div>')
        html.append('</td>')

    @staticmethod
    def _scores(html: list[str], shared: list[tuple[str, str]], rows: list[tuple[str, ...]], case_titles: dict,
                *, analysis: str | None = None) -> None:
        html.append('<div class="overview-scores"><section id="scores" tabindex="-1"><div class="score-heading"><h2>Reviewer scores</h2>')
        if shared:
            html.append(f'<p class="subtle">Scores use only the case conditions completed by every reviewer.{info("interval")}</p>')
        html.append('</div>')
        if not shared:
            html.append(_report_notice("coverage", "No shared scores yet",
                                      "At least one case condition must be completed by every reviewer.",
                                      '<a href="#coverage">Check review coverage</a>'))
        else:
            ReportBuilder._score_table(html, "Reviewer scores", ("Precision", "Reference recall", "Additional findings"), rows, (1, 2, 4), primary=True)
            html.append(_interval_chart(rows))
            if any(int(row[7]) or int(row[8]) for row in rows):
                html.append('<p class="score-grading-note subtle"><strong>Grading incomplete.</strong> '
                            'Scores reflect verdicts recorded so far. See Additional metrics for outstanding findings.</p>')
            html.append('<details class="additional-scores"><summary>Additional metrics</summary>')
            ReportBuilder._score_table(html, "Additional metrics", ("False positives", "Context requests", "Duplicates", "Unresolved", "Ungraded"), rows, (3, 5, 6, 7, 8))
            html.append('</details>')
        html.append('</section><aside class="scoring-scope" aria-labelledby="scoring-scope-title">'
                    '<h2 id="scoring-scope-title">Scoring scope</h2>'
                    f'<details open><summary>How to read the scores</summary><dl class="metric-guide">'
                    f'<div class="guide-precision"><dt>{icon("evidence")}Precision</dt><dd>Share of scored findings that were confirmed.</dd></div>'
                    f'<div class="guide-recall"><dt>{icon("coverage")}Reference recall</dt><dd>Share of expected findings the reviewer caught.</dd></div>'
                    f'<div class="guide-additional"><dt>{icon("publication")}Additional findings</dt><dd>Verified findings outside the expected set.</dd></div></dl></details>'
                    f'<details><summary>Scored case conditions <span class="scope-count">{len(shared)}</span></summary>')
        if shared:
            html.append('<ul class="scope-cases" role="list">')
            for case, condition in shared:
                title, number = case_titles.get((case, condition)) or (None, "")
                glyph = {"code_only": "source", "snapshot": "evidence"}.get(condition, "file")
                html.append(f'<li><span class="scope-pr">{icon("pr")}' + escape(number) + '</span>'
                            f'<strong class="scope-title" data-truncate-tip>{escape(title or _case_name(case))}</strong>'
                            f'<span class="scope-condition">{icon(glyph)}{escape(_label(condition))}</span></li>')
            html.append('</ul>')
        else:
            html.append('<p class="scope-empty subtle">No case conditions were completed by every reviewer.</p>')
        html.append('</details>')
        if isinstance(analysis, str) and analysis.strip():
            sentences: list[str] = []
            for item in re.split(r"(?<=\.)\s+(?=[A-Z])", " ".join(analysis.split())):
                if sentences and len(sentences[-1]) < 12:
                    sentences[-1] += f" {item}"
                elif item:
                    sentences.append(item)
            html.append('<details><summary>Campaign methodology</summary><ul class="scope-methodology">'
                        + "".join(f"<li>{escape(item)}</li>" for item in sentences) + '</ul></details>')
        html.append('</aside></div>')

    @staticmethod
    def _score_table(html: list[str], label: str, columns: tuple[str, ...], rows: list[tuple[str, ...]], indexes: tuple[int, ...], *, primary: bool = False) -> None:
        kind = " main-scores" if primary else ""
        html.append(f'<div class="scroll score-table{kind}" tabindex="0" role="region" aria-label="{label}">'
                    '<table><thead><tr><th scope="col">Reviewer</th>')
        html.extend(f'<th scope="col" class="metric">{column}{info(column)}</th>' for column in columns)
        html.append('</tr></thead><tbody>')
        for row in rows:
            html.append(f'<tr><th scope="row">{_reviewer_display(row[0])}</th>')
            for index in indexes:
                value, divider, percentage = row[index].partition(" (")
                fraction = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
                spread = (_range_bar(int(fraction[1]), int(fraction[2]), title=f"{_reviewer(row[0])} · {columns[indexes.index(index)]}",
                                     metric=columns[indexes.index(index)]) if fraction and index in (1, 2) else "")
                cell = (f'<strong>{escape(percentage.removesuffix(")"))}</strong><span class="score-ratio">{escape(value)}</span>{spread}'
                        if divider else f'<strong>{escape(value)}</strong>' if primary and value.isdigit()
                        else f'<span class="score-empty-text">{escape(value)}</span>' if primary else escape(value))
                html.append(f'<td class="metric">{cell}</td>')
            html.append('</tr>')
        html.append('</tbody></table></div>')

    def _comparison(self, html: list[str], groups: dict, providers: list[str], bundle: dict) -> None:
        html.append('<section id="comparison" data-report-view tabindex="-1"><div class="view-heading"><h1>Compare findings</h1>'
                    '<p class="view-intro">Choose a pull request and reviewers. Select any finding to see the code, original review, and benchmark verdict.</p></div>'
                    '<div id="comparison-controls" class="filters" hidden><label>Pull request and condition'
                    '<span class="select-field"><select id="compare-case">'
                    '<button type="button"><selectedcontent></selectedcontent></button>')
        for key, group in groups.items():
            case = next(iter(group.values()))
            case_id = case["case_id"]
            case_name = _case_name(case_id)
            title = _case_title(case)
            numbers = {provider: _pull_number(pair.get("pr_url")) for provider, pair in group.items()} if not bundle["fixture"] else {}
            known_numbers = {number for number in numbers.values() if number}
            reference = case_name if title != case_name else ""
            if len(known_numbers) == 1 and all(numbers.values()):
                reference = f'PR #{next(iter(known_numbers))}'
            elif known_numbers:
                reference = ", ".join(f"{_reviewer(provider)} #{number}" for provider, number in numbers.items() if number)
            completed = sum(pair["status"] == "completed" for pair in group.values())
            details = [reference, _label(case["condition"])]
            if case["repeat_index"]:
                details.append(f'Repeat {case["repeat_index"] + 1}')
            details.append(f'{completed}/{len(group)} reviews completed')
            metadata = " · ".join(filter(None, details))
            html.append(f'<option value="{escape(key, quote=True)}" data-title="{escape(title, quote=True)}">'
                        f'<span class="option-check" aria-hidden="true">{icon("check")}</span>'
                        f'{icon("pr")}<span class="option-label"><span class="option-title">{escape(title)}</span> '
                        f'<small class="option-meta">{escape(metadata)}</small></span></option>')
        html.append(f'</select>{_SELECT_CHEVRON}</span></label>')
        html.append('<fieldset class="reviewer-picker"><legend>Reviewers</legend>'
                    '<details id="reviewer-select"><summary><span class="visually-hidden">Select reviewers</span>'
                    f'<span id="reviewer-preview"></span><span id="reviewer-count"></span>{_SELECT_CHEVRON}</summary>'
                    '<div class="reviewer-panel">')
        html.append('<div class="reviewer-options">')
        for provider in providers:
            html.append(f'<label class="reviewer-option"><input type="checkbox" name="reviewer" '
                        f'value="{escape(provider, quote=True)}" checked>{icon("check")}{_reviewer_display(provider)}</label>')
        html.append('</div></div></details></fieldset>')
        html.append('</div><h2 id="selected-case" hidden></h2><p id="comparison-status" class="visually-hidden" role="status"></p>')
        html.append('<div class="comparison-tabs" aria-label="Comparison views" hidden>')
        for name, label, glyph in (("findings", "Findings", "publication"), ("problems", "Problems", "compare"),
                                   ("files", "Files changed", "diff")):
            html.append(f'<a id="tab-{name}" href="#comparison-{name}">{icon(glyph)}{label}'
                        + (' <span id="file-count" class="tab-count" hidden></span>' if name == "files" else '') + '</a>')
        html.append('</div><section id="comparison-findings" data-comparison-view="findings" aria-label="Findings">')
        html.append(f'<p class="claim-order subtle">Findings are ordered by file and line when available.{info("findings")}</p>')
        html.append('<div class="comparison-grid">')
        inspections: list[str] = []
        for key, group in groups.items():
            for pair in group.values():
                self._findings(html, inspections, key, pair, bundle["fixture"])
        html.append('</div><p id="comparison-empty" class="empty" ' + ('hidden' if groups else '') +
                    '>No reviews are available for this selection.</p>')
        html.append('<div class="inspection-list"><h2 class="inspection-heading">Finding detail</h2>')
        html.extend(inspections)
        html.append('</div></section><section id="comparison-problems" data-comparison-view="problems" aria-label="Problems">')
        self._reference_matrix(html, groups, providers)
        for key, group in groups.items():
            self._issues(html, key, group, providers, bundle.get("finding_groups", []))
        html.append('<p id="problems-unavailable" class="empty" hidden>Select a case and at least one reviewer to compare problems.</p></section>')
        html.append('<section id="comparison-files" data-comparison-view="files" aria-label="Files changed">')
        changes = {(item["base_sha"], item["head_sha"]): item["files"] for item in bundle.get("changes", [])}
        for key, group in groups.items():
            pairs = list(group.values())
            revisions = {(pair.get("base_sha"), pair.get("head_sha")) for pair in pairs}
            if len(revisions) == 1:
                revision = next(iter(revisions))
                self._files(html, key, changes.get(revision), group, pinned=all(revision))
            else:
                html.append(f'<div data-file-context data-context-case="{escape(key, quote=True)}">')
                html.append(_report_notice("diff", "Different revisions", "The attempts do not share the same base and head. Open each pull request to inspect its own changes."))
                html.append("</div>")
        html.append('<p id="files-unavailable" class="empty" hidden>No case to show files for.</p></section></section>')

    @staticmethod
    def _reference_matrix(html: list[str], groups: dict, providers: list[str]) -> None:
        rows = []
        for group in groups.values():
            case = next(iter(group.values()))
            if case["repeat_index"]:
                continue
            references = {item["expected_id"]: item for pair in group.values() for item in pair["expected"]}
            case_title = f'{_case_title(case)} · {_case_condition(case)}'
            for expected_id, finding in references.items():
                title = _label(expected_id.replace("-", " "))
                rows.append(f'<tr><th scope="row"><details class="reference-criterion">'
                            f'<summary>{escape(title)}</summary><p>{escape(finding["matching_criteria"])}</p></details>'
                            f'<span class="coverage-case">{escape(case_title)}</span></th>')
                for provider in providers:
                    pair = group.get(provider)
                    state, label, detail, destination = "unrated", "Not evaluated", "", ""
                    if pair is None:
                        detail = "No planned attempt"
                    else:
                        destination = f'#pair-{pair["pair_id"]}'
                        if pair["status"] != "completed":
                            detail = _review_status(pair)
                        else:
                            match = next((claim for claim in pair["claims"] if claim["decision"] == "confirmed_expected"
                                          and claim["expected_id"] == expected_id), None)
                            if match:
                                state, label = "detected", "Detected"
                                destination = f'#claim-{match["claim_id"]}'
                            elif _grading_pending(pair):
                                detail = "Grading pending"
                            elif any(item["expected_id"] == expected_id for item in pair["expected"]):
                                state, label = "missed", "Missed"
                            else:
                                detail = "Reference not loaded"
                    mark = f'{icon("check") if state == "detected" else icon("close") if state == "missed" else ""}{label}'
                    context = escape(f'{label} · {_reviewer(provider)} · {title}', quote=True)
                    content = (f'<a class="reference-state {state}" href="{escape(destination, quote=True)}" aria-label="{context}">{mark}</a>' if destination
                               else f'<span class="reference-state {state}">{mark}</span>')
                    rows.append(f'<td data-issue-provider="{escape(provider, quote=True)}">{content}'
                                + (f'<small class="coverage-case">{detail}</small>' if detail else "") + "</td>")
                rows.append("</tr>")
        if not rows:
            return
        html.append(f'<div id="reference-matrix" class="case-issues reference-matrix"><h3>Expected findings across cases{info("reference_findings")}</h3>'
                    '<p class="subtle">First attempt per PR and condition. Select a result to open its evidence.</p>'
                    '<p class="subtle reference-scroll-hint">Scroll the table to see more reviewers.</p>'
                    '<div class="scroll" tabindex="0" role="region" aria-label="Expected finding detection by reviewer">'
                    '<table><thead><tr><th scope="col">Expected finding</th>')
        for provider in providers:
            html.append(f'<th scope="col" data-issue-provider="{escape(provider, quote=True)}">{_reviewer_display(provider)}</th>')
        html.extend(("</tr></thead><tbody>", *rows, "</tbody></table></div></div>"))

    @staticmethod
    def _files(html: list[str], key: str, changes: list[dict] | None, group: dict, *, pinned: bool) -> None:
        case = escape(key, quote=True)
        if changes is None:
            html.append(f'<div data-file-context data-context-case="{case}">')
            if pinned:
                html.append(_report_notice("diff", "Diff not included", "Open Files changed on the original pull request or regenerate this report with a source checkout."))
            else:
                html.append(_report_notice("commit", "Revisions not recorded", "This report needs the base and head commits to show a pinned diff."))
            html.append("</div>")
            return
        html.append(f'<div class="case-files" data-file-context data-context-case="{case}" data-file-count="{len(changes)}">')
        html.append('<h3 class="file-section-heading">Files changed</h3>')
        if not changes:
            html.append(_report_notice("file", "No file changes", "The pinned base and head contain no file changes."))
            html.append("</div>")
            return
        html.append('<div class="diff-toolbar" hidden>'
                    '<label class="diff-view-control"><input type="checkbox" data-split-diff> Side-by-side diff</label>'
                    f'{info("diff")}')
        if len(changes) > 1:
            html.append('<label class="file-filter">Filter files <input type="search" data-file-filter></label>')
        html.append('</div>')
        annotations = {}
        for provider, pair in group.items():
            for claim in pair["claims"]:
                path, _, line = (claim.get("source_locator") or "").rpartition(":")
                side = claim.get("diff_side")
                if path and line.isdigit() and side in ("LEFT", "RIGHT") and claim.get("diff_hunk"):
                    title, severity, body = review_text(claim["body"])
                    heading = inline(title or "Review comment")
                    location = f'{side[0]}{line}'
                    source = _link(claim.get("source_url"))
                    original = f'<a href="{source}">{icon("external")}GitHub comment</a>' if source else ''
                    comment = (f'<details class="diff-comment" data-diff-provider="{escape(provider)}">'
                               f'<summary><code class="diff-location">{location}</code>{_reviewer_display(provider)} '
                               f'<strong class="diff-comment-title">{heading}</strong></summary>'
                               f'<div class="diff-comment-body"><h4>{heading}</h4>'
                               + (f'<p class="diff-severity">Reviewer severity · {escape(severity)}</p>' if severity else '') +
                               f'<div class="review-copy">{blocks(body)}</div></div>'
                               f'<div class="diff-comment-links"><a href="#claim-{claim["claim_id"]}">Open finding and evidence</a>'
                               f'{original}</div></details>')
                    annotations.setdefault(path, {}).setdefault((side, line), []).append(comment)
        for index, item in enumerate(changes):
            path = item["path"]
            count = "Binary" if item["added"] is None else f'+{item["added"]} / −{item["removed"]}'
            auto_open = ' open' if index == 0 else ''
            html.append(f'<details class="changed-file" name="changed-file-{case}" '
                        f'data-file-path="{escape(path, quote=True)}"{auto_open}>'
                        f'<summary>{icon("file")}<code>{escape(path)}</code><span>{count}</span></summary>'
                        f'{render_file_patch(item["patch"], path, annotations.get(path))}</details>')
        if len(changes) > 1:
            html.append('<p class="empty" data-no-files role="status" hidden>No file paths match this filter.</p>')
        html.append('</div>')

    @staticmethod
    def _issues(html: list[str], key: str, group: dict, providers: list[str], verified: list[dict]) -> None:
        rows = issue_rows(group, verified)
        html.append(f'<div class="case-issues" data-context-case="{escape(key, quote=True)}">'
                    f'<h3>Compare by problem{info("problems")}</h3>')
        if not rows:
            if not any(pair["status"] == "completed" for pair in group.values()):
                html.append(_report_notice("publication", "No completed reviews", "Check review coverage for the status of each attempt."))
            elif any(_grading_pending(pair) for pair in group.values()):
                html.append(_report_notice("evidence", "No problems confirmed yet", "Some captured findings are still awaiting a verdict."))
            else:
                html.append(_report_notice("evidence", "No problems to compare", "No reference findings or verified additional findings are recorded for this case."))
            html.append("</div>")
            return
        html.append('<p class="subtle">Reference problems come from the rubric. Additional findings share a row only when verified as the same problem.</p>'
                    '<p class="issue-scroll-hint subtle" hidden>Scroll the table to see the other reviewers.</p>'
                    '<div class="scroll" tabindex="0" role="region" aria-label="Problem comparison"><table><thead><tr>'
                    '<th scope="col">Problem</th>')
        for provider in providers:
            html.append(f'<th scope="col" data-issue-provider="{escape(provider)}">{_reviewer_display(provider)}</th>')
        html.append('</tr></thead><tbody>')
        for title, description, hits, reference in rows:
            kind = "Reference" if reference else "Additional"
            html.append(f'<tr><th scope="row"><span class="coverage-case">{kind}</span>{inline(title)}'
                        f'<p class="subtle issue-description">{escape(description)}</p></th>')
            for provider in providers:
                pair = group.get(provider)
                html.append(f'<td data-issue-provider="{escape(provider)}">')
                matches = hits.get(provider, [])
                if matches:
                    for index, claim_id in enumerate(matches, start=1):
                        label = f'View finding {index}' if len(matches) > 1 else 'View finding'
                        html.append(f'<a href="#claim-{claim_id}">{label}</a>')
                else:
                    message = _attempt_status(pair)
                    if pair and pair["status"] == "completed":
                        message = "Match pending grading" if _grading_pending(pair) else (
                            "Not detected" if reference else "No verified match")
                    html.append(f'<span class="subtle">{message}</span>')
                html.append('</td>')
            html.append('</tr>')
        html.append('</tbody></table></div></div>')

    @staticmethod
    def _findings(html: list[str], inspections: list[str], key: str, pair: dict, fixture: bool) -> None:
        provider = escape(pair["provider_id"])
        status = pair["status"]
        html.append(f'<article class="findings" id="pair-{pair["pair_id"]}" tabindex="-1" data-case="{escape(key, quote=True)}" '
                    f'data-provider="{provider}"><header class="review-head"><div><h3>{_reviewer_display(pair["provider_id"])}</h3>'
                    f'<p class="subtle">{escape(_case_condition(pair))}</p></div>'
                    f'<span class="state {status}">{_review_status(pair)}</span></header><div class="review-meta">')
        if status == "completed":
            html.append(f'<span>{_inline_comment_count(pair)}</span>')
        pr_url = _link(pair.get("pr_url"))
        if pr_url and not fixture:
            if number := _pull_number(pair["pr_url"]):
                path = urlsplit(pair["pr_url"]).path
                html.append(f'<a href="{pr_url}">{icon("pr")}PR #{number}</a>'
                            f'<a href="https://github.com{escape(path, quote=True)}/files">{icon("diff")}Files changed</a>')
            else:
                html.append(f'<a href="{pr_url}">{icon("pr")}Pull request</a>')
        html.append('</div>')
        if "head_sha" in pair:
            repository = re.match(r"(/[^/]+/[^/]+)/pull/", urlsplit(pair.get("pr_url") or "").path)
            rows = []
            for label, sha in (("Base", pair["base_sha"]), ("Pinned head", pair["head_sha"])):
                commit = _link(f"https://github.com{repository[1]}/commit/{sha}") if repository and not fixture else None
                label_text = escape(label.lower())
                actions = (f'<button class="sha-copy" type="button" data-copy="{sha}" aria-label="Copy full {label_text} commit ID" hidden>'
                           f'{icon("copy")}{icon("check")}</button>')
                if commit:
                    actions += f'<a class="sha-open" href="{commit}" aria-label="Open {label_text} commit on GitHub">{icon("external")}</a>'
                rows.append(f'<div><dt>{label}</dt><dd><code>{sha}</code>{actions}</dd></div>')
            html.append(f'<details class="revisions"><summary>{icon("commit")}Commit revisions</summary>'
                        f'<dl class="revision-list">{"".join(rows)}</dl>'
                        f'<p class="revision-note">The diff between these two commits is what the reviewer received. {info("revisions")}</p></details>')
        publication = pair.get("publication")
        if pair["reason"] and not (publication and status == "completed"):
            html.append(f'<p class="attempt-reason subtle">{escape(pair["reason"])}</p>')
        if not pair["claims"] and status == "completed":
            html.append(_report_notice("publication", "No captured findings", "This completed review has no actionable findings recorded."))
        elif not publication and not pair["claims"] and not pair["reason"]:
            detail = {
                "planned": "The review attempt has not started.",
                "preparing": "The runner is preparing the pull request.",
                "awaiting_trigger": "The reviewer still needs to be triggered.",
                "awaiting_review": "The runner is waiting for a completion signal.",
                "timed_out": "The review did not complete within the capture window.",
                "quota": "This attempt is limited by the provider quota.",
            }.get(status, "No findings were captured for this attempt.")
            html.append(_report_notice("publication", "No completed review", detail))
        messages = [claim for claim in pair["claims"] if claim["decision"] == "out_of_scope"
                    and (claim.get("source_object_id") or "").startswith("review:")]

        def file_position(claim: dict) -> tuple:
            path, _, line = (claim.get("source_locator") or "").rpartition(":")
            if path and re.fullmatch(r"[0-9]{1,9}", line):
                return (0, path.casefold(), path, int(line), claim.get("diff_side") or "")
            return (1,)

        findings = sorted((claim for claim in pair["claims"] if claim not in messages), key=file_position)
        for subset, label, tag in ((messages, "Review messages", "ul"), (findings, "Captured findings", "ol")):
            if not subset:
                continue
            html.append(f'<details class="claim-group" open><summary>{label} <span>{len(subset)}</span></summary>')
            html.append(f'<{tag} class="claim-index" role="list">')
            for index, claim in enumerate(subset, start=1):
                title, severity, body = review_text(claim["body"])
                decision = claim["decision"]
                is_message = tag == "ul"
                heading = inline(title) if title else "Reviewer comment"
                status = "Awaiting verdict" if decision is None else _label(decision)
                rank = f'<span class="claim-rank" aria-hidden="true">{index}.</span>' if not is_message else ""
                if is_message:
                    heading = "Review announcement" if claim["body"].startswith("<!-- proofline:review-comment -->") else "Review message"
                    status = "Excluded from scoring"
                locator = escape(claim.get("source_locator") or "")
                html.append(f'<li><a href="#claim-{claim["claim_id"]}" data-decision="{decision or "ungraded"}">'
                            f'<span class="claim-name">{rank}{heading}</span><span class="claim-meta">'
                            f'<span class="verdict {decision or "ungraded"}">{status}</span>')
                if locator:
                    html.append(f'<code>{locator}</code>')
                html.append('</span></a></li>')
                ReportBuilder._inspect(inspections, pair, claim, heading, severity, body, fixture, status)
            html.append(f'</{tag}></details>')
        if publication or (not fixture and _link(pair.get("capture_check_url"))):
            ReportBuilder._review_evidence(html, pair)
        if conversation := [url for raw in pair.get("conversation_links", []) if (url := _link(raw))]:
            html.append(f'<details class="review-publication"><summary>{icon("publication")}GitHub Conversation '
                        f'<span>{len(conversation)}</span></summary><p class="subtle">Additional reviewer comments outside the scored capture.</p>')
            for index, url in enumerate(conversation, start=1):
                html.append(f'<p><a href="{url}">{icon("external")}Open conversation comment {index} on GitHub</a></p>')
            html.append('</details>')
        if pair["status"] == "completed":
            matched = {claim["expected_id"] for claim in pair["claims"] if claim["decision"] == "confirmed_expected"}
            missing = [finding for finding in pair["expected"] if finding["expected_id"] not in matched]
            if missing:
                heading = "Reference matches pending grading" if _grading_pending(pair) else "Expected findings without a confirmed match"
                html.append(f'<div class="finding"><h4>{heading}</h4><ul>')
                html.extend(f'<li>{escape(finding["matching_criteria"])}</li>' for finding in missing)
                html.append("</ul></div>")
        html.append("</article>")

    @staticmethod
    def _review_evidence(html: list[str], pair: dict) -> None:
        publication = pair.get("publication") or {}
        html.append(f'<details class="review-publication review-evidence"><summary>{icon("publication")}Review evidence</summary>')
        if publication:
            provenance = ("Recovered in the separate publication audit." if pair["status"] == "completed"
                          else "Verified in a separate audit and outside this report’s scoring scope.")
        else:
            provenance = "Captured during the benchmark run."
        html.append(f'<p class="subtle">{provenance}</p>')
        if check := _link(publication.get("check_url") or pair.get("capture_check_url")):
            html.append(f'<p><a href="{check}">{icon("external")}Open GitHub completion check</a></p>')
        if title := publication.get("title"):
            html.append(f'<p class="subtle">{escape(title)}</p>')
        for summary in publication.get("summaries", []):
            html.append(f'<div class="review-copy">{blocks(publication_text(summary["body"]))}</div>')
            if source := _link(summary["url"]):
                html.append(f'<p><a href="{source}">Original reviewer summary on GitHub</a></p>')
        html.append('</details>')

    @staticmethod
    def _inspect(html: list[str], pair: dict, claim: dict, heading: str, severity: str, body: str, fixture: bool, status: str) -> None:
        claim_id = claim["claim_id"]
        decision = claim["decision"]
        locator = claim.get("source_locator") or ""
        html.append(f'<article class="finding inspector" id="claim-{claim_id}" tabindex="-1" '
                    f'data-pair="pair-{pair["pair_id"]}" aria-labelledby="title-{claim_id}">'
                    '<header class="inspector-head"><div>'
                    f'<p class="inspector-origin"><strong>{escape(_reviewer(pair["provider_id"]))}</strong>'
                    f'<span>{escape(_case_title(pair))} · {escape(_case_condition(pair))}</span></p>'
                    f'<h3 id="title-{claim_id}">{heading}</h3></div>')
        url = _link(claim["source_url"])
        if url:
            label = "Fixture comment URL" if fixture else "View original on GitHub"
            html.append(f'<a class="source-link" href="{url}">{icon("external")}{label}</a>')
        html.append('</header>')
        if locator or claim.get("diff_hunk"):
            source_label = "Captured GitHub diff" if claim.get("diff_hunk") else "Reported location"
            html.append(f'<div class="source-panel"><div class="source-heading"><span>{source_label}{info("captured_diff" if claim.get("diff_hunk") else "location")}</span>'
                        f'<code>{escape(locator)}</code></div>')
            if claim.get("diff_hunk"):
                html.append(render_diff(claim["diff_hunk"], locator, claim.get("diff_side", "RIGHT")))
            else:
                html.append('<p class="source-context-note subtle">The comment identified this location without a captured diff excerpt.</p>')
            html.append('</div>')
        elif (claim.get("source_object_id") or "").startswith("inline:"):
            html.append('<p class="source-context-note subtle">The inline comment has no recorded file or line location.</p>')
        html.append('<div class="inspection-body"><div class="observation"><h4>Reviewer comment</h4>')
        if severity:
            html.append(f'<p class="severity">Reviewer severity <strong>{escape(severity)}</strong>{info("severity")}</p>')
        if rendered := blocks(body):
            html.append(f'<blockquote class="review-copy">{rendered}</blockquote>')
        else:
            html.append('<p class="subtle">No readable comment body was captured.</p>')
        html.append(f'<details class="review-prompt raw-comment"><summary>{icon("source")}Raw comment source</summary>'
                    '<div class="prompt-body">'
                    f'<pre tabindex="0" role="region" aria-label="Raw comment source"><code>{escape(claim["body"])}</code></pre>'
                    f'<div class="prompt-tools">{info("raw_comment")}'
                    '<button type="button" class="prompt-copy" data-subject="Raw comment" aria-label="Copy raw comment" title="Copy raw comment" hidden>'
                    f'{icon("copy")}</button></div></div><p class="prompt-status" role="status"></p></details></div>')
        html.append(f'<div class="evaluation"><h4>Benchmark verdict{info("verdict")}</h4>'
                    f'<p class="verdict {decision or "ungraded"}">{status}</p>')
        if claim["rationale"]:
            html.append(f'<p class="rationale">{escape(claim["rationale"])}</p>')
        if claim["evidence"]:
            html.append(f'<div class="verification"><h5>{icon("evidence")}Verification evidence{info("evidence")}</h5>')
            for piece in claim["evidence"]:
                html.append(f'<div class="evidence"><strong>{_label(piece["type"])}</strong>'
                            f'<p>{escape(piece["text"])}</p></div>')
            html.append('</div>')
        elif decision in {None, "unresolved"}:
            html.append('<p class="subtle">No verification evidence has been attached to this finding.</p>')
        html.append(f'</div></div><div class="inspector-nav"><a href="#comparison">{icon("back")}Back to findings</a></div></article>')
