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
from .issues import issue_rows


_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_STATES = frozenset({
    "planned", "preparing", "awaiting_trigger", "awaiting_review", "completed",
    "invalid_input", "needs_attention", "timed_out", "failed", "quota",
})
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


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({numerator / denominator:.0%})" if denominator else "N/A"


def _label(value: str | None) -> str:
    return "Ungraded" if value is None else value.replace("_", " ").capitalize()


def _review_status(pair: dict) -> str:
    if pair.get("publication"):
        return "Verified publication" if pair["status"] == "completed" else "Formal review missing"
    return _label(pair["status"])


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


def _case_name(pair: dict) -> str:
    repeat = f", repeat {pair['repeat_index']}" if pair["repeat_index"] else ""
    return f"{pair['case_id']} ({pair['condition']}{repeat})"


def _case_display(pair: dict) -> str:
    case_id = pair["case_id"]
    name = f"Case {case_id}" if case_id.isdigit() else _label(case_id)
    condition = _label(pair["condition"])
    repeat = f" · Repeat {pair['repeat_index']}" if pair["repeat_index"] else ""
    return f"{name} · {condition}{repeat}"


def _reviewer(value: str) -> str:
    return {"proofline": "Proofline", "greptile": "Greptile", "coderabbit": "CodeRabbit"}.get(value, value)


def _reviewer_display(provider: str) -> str:
    name = escape(_reviewer(provider))
    mark = f'<span class="reviewer-mark {provider}" aria-hidden="true"></span>' if provider in {"greptile", "proofline"} else ""
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
    for reviewer in ("greptile", "proofline"):
        css = css.replace(f"__MARK_{reviewer.upper()}__", _encoded(f"reviewer-{reviewer}.png"))
    if assistants:
        for assistant in ("openai", "claude", "mistral"):
            css = css.replace(f"__ASK_{assistant.upper()}__", _encoded(f"assistant-{assistant}.svg"))
    return css


def _score_rows(metrics: dict) -> list[tuple[str, ...]]:
    return [
        (provider, _ratio(counts["matched"] + counts["novel"], counts["adjudicated"]),
         _ratio(counts["matched"], counts["expected"]), str(counts["false_positives"]),
         str(counts["novel"]), str(counts["justified_context_requests"]), str(counts["duplicates"]),
         str(counts["unresolved"]), str(counts["ungraded"]))
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
        for provider, precision, recall, false, novel, context, duplicates, unresolved, ungraded in rows:
            md.append(f"- {provider}: precision {precision}, recall {recall}, {false} false positives, "
                      f"{novel} novel, {context} justified context requests, {duplicates} duplicates, "
                      f"{unresolved} unresolved, {ungraded} ungraded")
        md.extend(["", "## Coverage", ""])
        for pair in bundle["pairs"]:
            md.append(f"- {_case_name(pair)} / {pair['provider_id']}: {_label(pair['status'])}")

        root = files(__package__)
        operational = bool(rows) and (bundle.get("operational_schema_version") == 1 or any("cost" in pair for pair in bundle["pairs"]))
        css = _report_styles(operational=operational, assistants=True)
        script = root.joinpath("report.js").read_text(encoding="utf-8")
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
                f'<p class="run-meta"><span>{scope}</span><code>{escape(bundle["run_id"])}</code></p></div>'
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
        for label, value in (("Completed attempts", f'{completed} / {len(bundle["pairs"])}'),
                             ("Scored conditions", len(metrics["shared"])), ("Reviewers", len(providers))):
            html.append(f'<div><dt>{label}</dt><dd>{value}</dd></div>')
        html.append('</dl>')
        case_titles = {(pair["case_id"], pair["condition"]): pair.get("case_title") for pair in bundle["pairs"]
                       if pair["repeat_index"] == 0}
        if isinstance(bundle.get("analysis"), str) and bundle["analysis"]:
            html.append(f'<p class="analysis-note"><strong>Analysis note</strong> {escape(bundle["analysis"])}</p>')
        self._scores(html, metrics["shared"], rows, case_titles)
        if operational:
            self._operations(html, bundle)
        html.append('</section>')
        self._coverage(html, groups, providers, set(metrics["shared"]), fixture=bundle["fixture"])
        self._comparison(html, groups, providers, bundle)
        if rows:
            html.append(f'<!--\n{root.joinpath("LICENSE-LobeIcons.txt").read_text(encoding="utf-8")}\n-->')
            self._ask(html, bundle, rows, metrics["shared"], completed)
        html.append('</main><footer class="report-footer"><img alt="SiftBench" width="270" height="64" '
                    f'src="data:image/svg+xml;base64,{_encoded("footer-wordmark.svg")}"></footer>')
        html.append(f"<script>{script}</script></body></html>")
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
        prompt = [
            "Help me interpret this SiftBench comparison of AI code reviewers.", "",
            f"{completed} of {len(pairs)} review attempts completed across {len(rows)} reviewers.",
            f"Scores cover {len(shared)} shared case conditions. The report scores only conditions completed by every reviewer, and excludes repeats.",
        ]
        if bundle["fixture"]:
            prompt.append("This report uses fixture data and does not measure live reviewer performance.")
        if publications:
            included = sum(pair["status"] == "completed" for pair in publications)
            prompt.append(f"A separate publication audit found {len(publications)} additional publications. This report treats {included} of those as completed attempts.")
        prompt.extend(["", "Aggregated scores"])
        for provider, precision, recall, false, novel, context, duplicates, unresolved, ungraded in rows:
            prompt.append(f"- {_reviewer(provider)} · Precision {precision} · Reference recall {recall} · Additional findings {novel} · "
                          f"False positives {false} · Justified context requests {context} · Duplicates {duplicates} · Unresolved {unresolved} · Ungraded {ungraded}")
        prompt.extend(["", "Precision is the share of scored findings confirmed. Reference recall is the share of expected findings found. "
                       "Additional findings are verified findings outside the expected set.", "",
                       "What differences matter in this run, and what conclusions would require more evidence? "
                       "Use only the figures and scope above. Keep conclusions specific to the cases tested."])
        preview = escape("\n".join(prompt))
        html.append('<dialog id="benchmark-dialog" class="benchmark-dialog" aria-labelledby="benchmark-dialog-title">'
                    '<div class="ask-heading"><h2 id="benchmark-dialog-title">Ask about this benchmark</h2>'
                    f'<button id="close-benchmark-dialog" type="button" aria-label="Close" autofocus>{icon("close")}</button></div>'
                    '<p class="subtle">Copy the aggregate results below, then paste them into the assistant of your choice.</p>'
                    '<label for="benchmark-prompt">Prompt preview</label>'
                    f'<textarea id="benchmark-prompt" readonly rows="10" spellcheck="false">{preview}</textarea>'
                    '<div class="ask-copy"><button id="copy-benchmark-prompt" type="button">Copy prompt</button>'
                    '<p id="benchmark-copy-status" class="subtle" role="status"></p></div>'
                    '<div class="ask-destinations" aria-label="Assistants" role="group">')
        for brand, name, url in (("openai", "ChatGPT", "https://chatgpt.com/"), ("claude", "Claude", "https://claude.ai/new"),
                                 ("mistral", "Mistral Vibe", "https://chat.mistral.ai/")):
            html.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                        f'<span class="assistant-mark {brand}" aria-hidden="true"></span>{name}{icon("external")}</a>')
        html.append('</div></dialog>')

    @staticmethod
    def _coverage(html: list[str], groups: dict, providers: list[str], shared: set[tuple[str, str]], *, fixture: bool) -> None:
        html.append('<section id="coverage" data-report-view tabindex="-1"><div class="view-heading"><h1>Review coverage</h1>'
                    '<p class="view-intro">Open a reviewer’s status to inspect the attempt, or follow the GitHub links to the original evidence.</p></div>')
        if not groups:
            html.append("<p>No cases to compare.</p></section>")
            return
        pairs = [pair for group in groups.values() for pair in group.values()]
        html.append('<div class="coverage-heading"><h2>Cases by reviewer</h2><p class="subtle">'
                    'Scored rows contribute to the shared results.</p>')
        if len(providers) > 1:
            html.append('<p class="coverage-scroll-hint subtle">Scroll sideways to see the other reviewers.</p>')
        html.append('</div><div class="scroll coverage-table" tabindex="0" role="region" aria-label="Case coverage"><table><thead><tr>'
                    '<th scope="col">Case and condition</th>')
        for provider in providers:
            attempts = [pair for pair in pairs if pair["provider_id"] == provider]
            done = sum(pair["status"] == "completed" for pair in attempts)
            html.append(f'<th scope="col">{_reviewer_display(provider)}'
                        f'<span class="coverage-case">{done}/{len(attempts)} completed</span></th>')
        html.append("</tr></thead><tbody>")
        for group in groups.values():
            first = next(iter(group.values()))
            case = _case_display(first)
            title = first.get("case_title")
            html.append('<tr><th scope="row">')
            if title:
                html.append(f'<span class="coverage-title">{escape(title)}</span>')
            scored = (first["case_id"], first["condition"]) in shared and not first["repeat_index"]
            scope = "Scored" if scored else "Outside score"
            scored_class = ' is-scored' if scored else ''
            html.append(f'<span class="coverage-case">{escape(case)}</span>'
                        f'<span class="coverage-scope{scored_class}">{scope}</span></th>')
            for provider in providers:
                pair = group.get(provider)
                if pair:
                    ReportBuilder._coverage_attempt(html, pair, case, fixture=fixture)
                else:
                    html.append('<td class="subtle">No attempt</td>')
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    @staticmethod
    def _coverage_attempt(html: list[str], pair: dict, case: str, *, fixture: bool) -> None:
        status = _review_status(pair)
        reviewer = _reviewer(pair["provider_id"])
        label = escape(f"{reviewer}, {case}: {status}", quote=True)
        html.append(f'<td><a class="state {pair["status"]}" href="#pair-{pair["pair_id"]}" aria-label="{label}">'
                    f'{status}</a>')
        if reason := pair.get("reason"):
            html.append(f'<p class="coverage-reason subtle">{escape(reason)}</p>')
        elif pair["status"] == "completed" and not pair.get("publication"):
            inline_count = sum((claim.get("source_object_id") or "").startswith("inline:") for claim in pair["claims"])
            html.append(f'<p class="coverage-reason subtle">{inline_count} inline {"comment" if inline_count == 1 else "comments"}</p>')
        pr_url = _link(pair.get("pr_url")) if not fixture else None
        if pr_url:
            number = _pull_number(pair["pr_url"])
            pr_label = f"PR #{number}" if number else "Pull request"
            name = escape(f"Open {reviewer} {pr_label} on GitHub", quote=True)
            html.append(f'<div class="coverage-links"><a href="{pr_url}" aria-label="{name}">{icon("pr")}{pr_label}</a>')
            if check := _link((pair.get("publication") or {}).get("check_url")):
                html.append(f'<a href="{check}">{icon("external")}Publication check</a>')
            elif number and (signal := re.fullmatch(r"review:([0-9]+)", pair.get("capture_signal") or "")):
                review = _link(urlsplit(pair["pr_url"])._replace(fragment=f"pullrequestreview-{signal[1]}").geturl())
                if review:
                    html.append(f'<a href="{review}">{icon("external")}GitHub review</a>')
            html.append('</div>')
        html.append('</td>')

    @staticmethod
    def _scores(html: list[str], shared: list[tuple[str, str]], rows: list[tuple[str, ...]], case_titles: dict) -> None:
        html.append('<div class="overview-scores"><section id="scores" tabindex="-1"><div class="score-heading"><h2>Reviewer scores</h2>'
                    '<p class="subtle">Scores use only the case conditions completed by every reviewer.</p></div>')
        if not rows:
            html.append('<p class="score-heading subtle">No reviewer results are available.</p></section></div>')
            return
        ReportBuilder._score_table(html, "Reviewer scores", ("Precision", "Reference recall", "Additional findings"), rows, (1, 2, 4), primary=True)
        html.append('<details class="additional-scores"><summary>Additional metrics</summary>')
        ReportBuilder._score_table(html, "Additional metrics", ("False positives", "Context requests", "Duplicates", "Unresolved", "Ungraded"), rows, (3, 5, 6, 7, 8))
        html.append('</details></section><aside class="scoring-scope" aria-labelledby="scoring-scope-title">'
                    '<h2 id="scoring-scope-title">Scoring scope</h2>'
                    '<details open><summary>How to read the scores</summary><dl class="metric-guide">'
                    '<div><dt>Precision</dt><dd>Share of scored findings that were confirmed.</dd></div>'
                    '<div><dt>Reference recall</dt><dd>Share of expected findings the reviewer caught.</dd></div>'
                    '<div><dt>Additional findings</dt><dd>Verified findings outside the expected set.</dd></div></dl></details>'
                    f'<details><summary>Scored case conditions <span class="scope-count">{len(shared)}</span></summary>')
        if shared:
            html.append('<ul class="scope-cases" role="list">')
            for case, condition in shared:
                title = case_titles.get((case, condition)) or (f"Case {case}" if case.isdigit() else _label(case))
                html.append(f'<li><strong>{escape(title)}</strong><span>{escape(_label(condition))}</span></li>')
            html.append('</ul>')
        else:
            html.append('<p class="scope-empty subtle">No case conditions were completed by every reviewer.</p>')
        html.append('</details></aside></div>')

    @staticmethod
    def _score_table(html: list[str], label: str, columns: tuple[str, ...], rows: list[tuple[str, ...]], indexes: tuple[int, ...], *, primary: bool = False) -> None:
        kind = " main-scores" if primary else ""
        html.append(f'<div class="scroll score-table{kind}" tabindex="0" role="region" aria-label="{label}">'
                    '<table><thead><tr><th scope="col">Reviewer</th>')
        html.extend(f'<th scope="col" class="metric">{column}</th>' for column in columns)
        html.append('</tr></thead><tbody>')
        for row in rows:
            html.append(f'<tr><th scope="row">{_reviewer_display(row[0])}</th>')
            for index in indexes:
                value, divider, percentage = row[index].partition(" (")
                cell = (f'<strong>{escape(percentage.removesuffix(")"))}</strong><span class="score-ratio">{escape(value)}</span>'
                        if divider else f'<strong>{escape(value)}</strong>' if primary else escape(value))
                html.append(f'<td class="metric">{cell}</td>')
            html.append('</tr>')
        html.append('</tbody></table></div>')

    def _comparison(self, html: list[str], groups: dict, providers: list[str], bundle: dict) -> None:
        html.append('<section id="comparison" data-report-view tabindex="-1"><div class="view-heading"><h1>Compare findings</h1>'
                    '<p class="view-intro">Choose a case and reviewers. Select any finding to see the code, original review, and benchmark verdict.</p></div>'
                    '<div id="comparison-controls" class="filters" hidden><label>Case and condition'
                    '<span class="select-field"><select id="compare-case">'
                    '<button type="button"><selectedcontent></selectedcontent></button>')
        for key, group in groups.items():
            case = next(iter(group.values()))
            title = case.get("case_title") or ""
            case_id = case["case_id"]
            case_name = f"Case {case_id}" if case_id.isdigit() else _label(case_id)
            numbers = {provider: _pull_number(pair.get("pr_url")) for provider, pair in group.items()} if not bundle["fixture"] else {}
            known_numbers = {number for number in numbers.values() if number}
            reference = case_name if title else ""
            if len(known_numbers) == 1 and all(numbers.values()):
                reference = f'PR #{next(iter(known_numbers))}'
            elif known_numbers:
                reference = ", ".join(f"{_reviewer(provider)} #{number}" for provider, number in numbers.items() if number)
            completed = sum(pair["status"] == "completed" for pair in group.values())
            details = [reference, _label(case["condition"])]
            if case["repeat_index"]:
                details.append(f'Repeat {case["repeat_index"]}')
            details.append(f'{completed}/{len(group)} reviews completed')
            metadata = " · ".join(filter(None, details))
            html.append(f'<option value="{escape(key, quote=True)}" data-title="{escape(title, quote=True)}">'
                        f'<span class="option-check" aria-hidden="true">{icon("check")}</span>'
                        f'{icon("pr")}<span class="option-label"><span class="option-title">{escape(title or case_name)}</span> '
                        f'<small class="option-meta">{escape(metadata)}</small></span></option>')
        html.append(f'</select>{_SELECT_CHEVRON}</span></label>')
        html.append('<fieldset class="reviewer-picker"><legend>Reviewers</legend>'
                    '<details id="reviewer-select"><summary><span class="visually-hidden">Select reviewers</span>'
                    f'<span id="reviewer-preview"></span><span id="reviewer-count"></span>{_SELECT_CHEVRON}</summary>'
                    '<div class="reviewer-panel">')
        if len(providers) > 2:
            html.append('<div class="reviewer-actions"><button type="button" data-select-reviewers="all">Select all</button>'
                        '<button type="button" data-select-reviewers="none">Clear selection</button></div>')
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
        html.append('<p class="claim-order subtle">Findings are ordered by file and line when available.</p>')
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
        for key, group in groups.items():
            self._issues(html, key, group, providers, bundle.get("finding_groups", []))
        html.append('<p id="problems-unavailable" class="empty" hidden>Select a case and at least one reviewer to compare problems.</p></section>')
        html.append('<section id="comparison-files" data-comparison-view="files" aria-label="Files changed">')
        changes = {(item["base_sha"], item["head_sha"]): item["files"] for item in bundle.get("changes", [])}
        for key, group in groups.items():
            pairs = list(group.values())
            revisions = {(pair.get("base_sha"), pair.get("head_sha")) for pair in pairs}
            if len(revisions) == 1:
                self._files(html, key, changes.get(next(iter(revisions))), group)
        html.append('<p id="files-unavailable" class="empty" hidden>No shared pinned diff is available for this case.</p></section></section>')

    @staticmethod
    def _files(html: list[str], key: str, changes: list[dict] | None, group: dict) -> None:
        if changes is None:
            return
        case = escape(key, quote=True)
        html.append(f'<div class="case-files" data-context-case="{case}" data-file-count="{len(changes)}">')
        html.append('<h3 class="file-section-heading">Files changed</h3>')
        html.append('<div class="diff-toolbar" hidden>'
                    '<label class="diff-view-control"><input type="checkbox" data-split-diff> Side-by-side diff</label>')
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
        if not changes:
            html.append('<p class="empty">These commits have no file changes.</p>')
        html.append('</div>')

    @staticmethod
    def _issues(html: list[str], key: str, group: dict, providers: list[str], verified: list[dict]) -> None:
        rows = issue_rows(group, verified)
        html.append(f'<div class="case-issues" data-context-case="{escape(key, quote=True)}">'
                    '<h3>Compare by problem</h3>')
        if not rows:
            html.append('<p class="empty">This change has no reference or verified additional findings.</p></div>')
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
                    message = "No attempt" if not pair else (
                        "No verified match" if pair["status"] == "completed" else "Review unavailable")
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
                    f'<p class="subtle">{escape(_case_display(pair))}</p></div>'
                    f'<span class="state {status}">{_review_status(pair)}</span></header><div class="review-meta">')
        if status == "completed":
            inline_count = sum((claim.get("source_object_id") or "").startswith("inline:") for claim in pair["claims"])
            html.append(f'<span>{inline_count} inline {"comment" if inline_count == 1 else "comments"}</span>')
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
            html.append(f'<details class="revisions"><summary>{icon("commit")}Commit revisions</summary>'
                        f'<p>Base <code>{pair["base_sha"]}</code></p><p>'
                        f'Pinned head <code>{pair["head_sha"]}</code></p></details>')
        if pair["reason"]:
            html.append(f'<p class="empty">{escape(pair["reason"])}</p>')
        if pair.get("publication"):
            ReportBuilder._publication(html, pair)
        elif not pair["claims"]:
            message = "Review completed without captured findings." if status == "completed" else "A completed review is required for scoring."
            html.append(f'<p class="empty">{message}</p>')
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
                status = _label(decision)
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
                html.append('<div class="finding"><h4>Expected findings without a confirmed match</h4><ul>')
                html.extend(f'<li>{escape(finding["matching_criteria"])}</li>' for finding in missing)
                html.append("</ul></div>")
        html.append("</article>")

    @staticmethod
    def _publication(html: list[str], pair: dict) -> None:
        publication = pair["publication"]
        html.append(f'<details class="review-publication"><summary>{icon("publication")}Verified publication</summary>')
        if pair["status"] != "completed":
            html.append('<p class="subtle">This publication is outside the formal capture used for these scores.</p>')
        html.append('<p>No actionable findings were recorded in the publication audit.</p>')
        if check := _link(publication["check_url"]):
            html.append(f'<p><a href="{check}">GitHub check · {escape(publication["title"])}</a></p>')
        for summary in publication["summaries"]:
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
                    f'<span>{escape(_case_display(pair))}</span></p><h3 id="title-{claim_id}">{heading}</h3></div>')
        url = _link(claim["source_url"])
        if url:
            label = "Fixture comment URL" if fixture else "View original on GitHub"
            html.append(f'<a class="source-link" href="{url}">{icon("external")}{label}</a>')
        html.append('</header>')
        if locator or claim.get("diff_hunk"):
            source_label = "Captured GitHub diff" if claim.get("diff_hunk") else "Reported location"
            html.append(f'<div class="source-panel"><div class="source-heading"><span>{source_label}</span>'
                        f'<code>{escape(locator)}</code></div>')
            if claim.get("diff_hunk"):
                html.append(render_diff(claim["diff_hunk"], locator, claim.get("diff_side", "RIGHT")))
            html.append('</div>')
        html.append('<div class="inspection-body"><div class="observation"><h4>Reviewer comment</h4>')
        if severity:
            html.append(f'<p class="severity">Reviewer severity <strong>{escape(severity)}</strong></p>')
        if rendered := blocks(body):
            html.append(f'<blockquote class="review-copy">{rendered}</blockquote>')
        html.append(f'<details class="raw-comment"><summary>{icon("source")}Raw comment source</summary>'
                    '<p class="subtle">Unformatted text captured with the review.</p>'
                    f'<pre>{escape(claim["body"])}</pre></details></div>')
        html.append('<div class="evaluation"><h4>Benchmark verdict</h4>'
                    f'<p class="verdict {decision or "ungraded"}">{status}</p>')
        if claim["rationale"]:
            html.append(f'<p class="rationale">{escape(claim["rationale"])}</p>')
        if claim["evidence"]:
            html.append(f'<div class="verification"><h5>{icon("evidence")}Verification evidence</h5>')
            for piece in claim["evidence"]:
                html.append(f'<div class="evidence"><strong>{_label(piece["type"])}</strong>'
                            f'<p>{escape(piece["text"])}</p></div>')
            html.append('</div>')
        html.append(f'</div></div><div class="inspector-nav"><a href="#comparison">{icon("back")}Back to findings</a></div></article>')
