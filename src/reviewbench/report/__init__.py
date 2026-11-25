import base64
import hashlib
import json
import re
from html import escape
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from ..grading import DECISIONS, EVIDENCE_TYPES, ScoreCalculator
from ..manifest import canonical_json, digest, identifier
from ..store import StoreError
from .comment import blocks, inline, review_text
from .diff import render_diff


_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
_STATES = frozenset({
    "planned", "preparing", "awaiting_trigger", "awaiting_review", "completed",
    "invalid_input", "needs_attention", "timed_out", "failed", "quota",
})
_SELECT_END = ('</select><svg class="select-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true" '
               'focusable="false"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg></span></label>')


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


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({numerator / denominator:.0%})" if denominator else "N/A"


def _label(value: str | None) -> str:
    return "Ungraded" if value is None else value.replace("_", " ").capitalize()


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
                seen_claims.add(claim["claim_id"])
                for piece in claim["evidence"]:
                    if piece["type"] not in EVIDENCE_TYPES or digest(piece["text"].encode()) != piece["digest"]:
                        raise StoreError("Invalid evidence or digest mismatch")
            if any(not isinstance(finding["matching_criteria"], str) for finding in pair["expected"]):
                raise StoreError("Expected findings need text criteria")
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


def _encoded(name: str) -> str:
    return base64.b64encode(files(__package__).joinpath(name).read_bytes()).decode("ascii")


def _csp_hash(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode("ascii")


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
        css = root.joinpath("theme.css").read_text(encoding="utf-8") + root.joinpath("report.css").read_text(encoding="utf-8")
        css = css.replace("__MANROPE__", _encoded("manrope-latin.woff2"))
        script = root.joinpath("report.js").read_text(encoding="utf-8")
        policy = f"default-src 'none'; style-src 'sha256-{_csp_hash(css)}'; script-src 'sha256-{_csp_hash(script)}'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"
        html = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                f'<meta http-equiv="Content-Security-Policy" content="{policy}">',
                f"<title>{escape(title)} | SiftBench</title><style>{css}</style></head>",
                '<body><a class="skip-link" href="#main">Skip to report</a><header class="shell"><div class="shell-inner">',
                f'<a class="brand" href="#main"><img alt="" src="data:image/svg+xml;base64,{_encoded("mark-on-dark.svg")}">SiftBench</a>',
                '<nav aria-label="Report"><a href="#scores">Scores</a><a href="#comparison">Findings</a>'
                '<a href="#coverage">Coverage</a></nav></div></header>',
                f'<main id="main" tabindex="-1"><div class="title"><div><h1>Reviewer benchmark</h1>'
                f'<p class="run-meta"><span>{scope}</span> <code>{escape(bundle["run_id"])}</code></p></div>'
                '<a class="action" href="#comparison">Compare findings <span aria-hidden="true">↓</span></a></div>']
        groups: dict[str, dict[str, dict]] = {}
        for pair in bundle["pairs"]:
            groups.setdefault(_case_key(pair), {})[pair["provider_id"]] = pair
        providers = list(metrics["providers"])
        if isinstance(bundle.get("analysis"), str) and bundle["analysis"]:
            html.append(f'<p class="analysis-note">{escape(bundle["analysis"])}</p>')
        completed = sum(pair["status"] == "completed" for pair in bundle["pairs"])
        html.append('<dl class="run-stats" aria-label="Campaign totals">')
        for label, value in (("Completed attempts", f'{completed} / {len(bundle["pairs"])}'),
                             ("Shared cases and conditions", len(metrics["shared"])), ("Reviewers", len(providers))):
            html.append(f'<div><dt>{label}</dt><dd>{value}</dd></div>')
        html.append('</dl>')
        self._scores(html, metrics["shared"], rows)
        self._comparison(html, groups, providers, bundle["fixture"])
        self._coverage(html, groups, providers)
        html.append('</main><footer class="report-footer"><img alt="SiftBench" width="270" height="64" '
                    f'src="data:image/svg+xml;base64,{_encoded("footer-wordmark.svg")}"></footer>')
        html.append(f"<script>{script}</script></body></html>")
        (output / "results.json").write_bytes(canonical_json(bundle) + b"\n")
        (output / "report.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")
        (output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
        (output / "OFL-Manrope.txt").write_bytes(root.joinpath("OFL-Manrope.txt").read_bytes())

    @staticmethod
    def _coverage(html: list[str], groups: dict, providers: list[str]) -> None:
        html.append('<section id="coverage"><div class="section-heading"><h2>Coverage</h2>'
                    '<p class="subtle">Open a status to read the captured findings and evidence.</p></div>')
        if not groups:
            html.append("<p>No cases to compare.</p></section>")
            return
        html.append('<div class="scroll" tabindex="0" role="region" aria-label="Case coverage"><table><thead><tr>'
                    '<th scope="col">Case and condition</th>')
        html.extend(f'<th scope="col">{escape(_reviewer(provider))}</th>' for provider in providers)
        html.append("</tr></thead><tbody>")
        for group in groups.values():
            case = _case_display(next(iter(group.values())))
            html.append(f'<tr><th scope="row">{escape(case)}</th>')
            for provider in providers:
                pair = group.get(provider)
                if pair:
                    status = _label(pair["status"])
                    label = escape(f"{_reviewer(provider)}, {case}: {status}", quote=True)
                    html.append(f'<td><a class="state {pair["status"]}" href="#pair-{pair["pair_id"]}" '
                                f'aria-label="{label}">{status}</a></td>')
                else:
                    html.append('<td>N/A</td>')
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    @staticmethod
    def _scores(html: list[str], shared: list[tuple[str, str]], rows: list[tuple[str, ...]]) -> None:
        html.append('<section id="scores"><div class="section-heading"><h2>Scores</h2>'
                    '<p class="subtle">Scores use cases completed by every reviewer.</p></div>')
        if not rows:
            html.append("<p>No reviewer results are available.</p></section>")
            return
        html.append('<p class="subtle small-screen-hint">Scroll the table to see all metrics.</p>'
                    '<div class="scroll score-table" tabindex="0" role="region" aria-label="Reviewer scores"><table><thead><tr>'
                    '<th scope="col">Reviewer</th>')
        columns = ("Precision", "Recall", "False positives", "Novel", "Context requests", "Duplicates", "Unresolved", "Ungraded")
        html.extend(f'<th scope="col" class="metric">{column}</th>' for column in columns)
        html.append("</tr></thead><tbody>")
        for provider, *counts in rows:
            html.append(f'<tr><th scope="row">{escape(_reviewer(provider))}</th>')
            html.extend(f'<td class="metric">{escape(count)}</td>' for count in counts)
            html.append("</tr>")
        html.append("</tbody></table></div>")
        if shared:
            cases = [escape(_case_display({"case_id": case, "condition": condition, "repeat_index": 0}))
                     for case, condition in shared]
            html.append(f'<details class="score-scope"><summary>Cases included in the scores <span>{len(cases)}</span></summary>'
                        f'<p class="subtle">{" / ".join(cases)}</p></details>')
        html.append("</section>")

    def _comparison(self, html: list[str], groups: dict, providers: list[str], fixture: bool) -> None:
        html.append('<section id="comparison"><div class="section-heading"><h2>Compare findings</h2>'
                    '<p class="subtle">Choose a case and reviewers, then open the evidence behind a verdict.</p></div>'
                    '<div id="comparison-controls" class="filters" hidden><label>Case and condition'
                    '<span class="select-field"><select id="compare-case">')
        html.extend(f'<option value="{escape(key, quote=True)}">{escape(_case_display(next(iter(group.values()))))}</option>'
                    for key, group in groups.items())
        html.append(_SELECT_END)
        for title, field, selected in (("First reviewer", "left", 0), ("Second reviewer", "right", 1)):
            html.append(f'<label>{title}<span class="select-field"><select id="compare-{field}">'
                        '<option value="">None</option>')
            for index, provider in enumerate(providers):
                selection = " selected" if selected == index else ""
                html.append(f'<option value="{escape(provider, quote=True)}"{selection}>{escape(_reviewer(provider))}</option>')
            html.append(_SELECT_END)
        html.append('</div><p id="comparison-status" class="visually-hidden" role="status"></p><div class="comparison-grid">')
        inspections: list[str] = []
        for key, group in groups.items():
            for pair in group.values():
                self._findings(html, inspections, key, pair, fixture)
        html.append('</div><p id="comparison-empty" class="empty" ' + ('hidden' if groups else '') +
                    '>No reviews are available for this selection.</p>')
        html.append('<div class="inspection-list">')
        html.extend(inspections)
        html.append('</div></section>')

    @staticmethod
    def _findings(html: list[str], inspections: list[str], key: str, pair: dict, fixture: bool) -> None:
        provider = escape(pair["provider_id"])
        status = pair["status"]
        count = len(pair["claims"])
        html.append(f'<article class="findings" id="pair-{pair["pair_id"]}" tabindex="-1" data-case="{escape(key, quote=True)}" '
                    f'data-provider="{provider}"><header class="review-head"><div><h3>{escape(_reviewer(pair["provider_id"]))}</h3>'
                    f'<p class="subtle">{escape(_case_display(pair))}</p></div>'
                    f'<span class="state {status}">{_label(status)}</span></header><div class="review-meta">')
        if status == "completed":
            html.append(f'<span>{count} captured {"comment" if count == 1 else "comments"}</span>')
        pr_url = _link(pair.get("pr_url"))
        if pr_url and not fixture:
            html.append(f'<a href="{pr_url}">Pull request <span aria-hidden="true">↗</span></a>')
        html.append('</div>')
        if "head_sha" in pair:
            html.append(f'<details class="revisions"><summary>Commit revisions</summary>'
                        f'<p>Base <code>{pair["base_sha"]}</code></p><p>'
                        f'Pinned head <code>{pair["head_sha"]}</code></p></details>')
        if pair["reason"]:
            html.append(f'<p class="empty">{escape(pair["reason"])}</p>')
        if not pair["claims"]:
            message = "Review completed without captured findings." if status == "completed" else "A completed review is required for scoring."
            html.append(f'<p class="empty">{message}</p>')
        if pair["claims"]:
            html.append('<ul class="claim-index">')
            for claim in pair["claims"]:
                title, severity, body = review_text(claim["body"])
                decision = claim["decision"]
                fallback = "Review summary" if decision == "out_of_scope" else "Reviewer comment"
                heading = inline(title) if title else fallback
                locator = escape(claim.get("source_locator") or "")
                html.append(f'<li><a href="#claim-{claim["claim_id"]}" data-decision="{decision or "ungraded"}">'
                            f'<span class="claim-name">{heading}</span><span class="claim-meta">'
                            f'<span class="verdict {decision or "ungraded"}">{_label(decision)}</span>')
                if locator:
                    html.append(f'<code>{locator}</code>')
                html.append('</span></a></li>')
                ReportBuilder._inspect(inspections, pair, claim, heading, severity, body, fixture)
            html.append('</ul>')
        if pair["status"] == "completed":
            matched = {claim["expected_id"] for claim in pair["claims"] if claim["decision"] == "confirmed_expected"}
            missing = [finding for finding in pair["expected"] if finding["expected_id"] not in matched]
            if missing:
                html.append('<div class="finding"><h4>Expected findings without a confirmed match</h4><ul>')
                html.extend(f'<li>{escape(finding["matching_criteria"])}</li>' for finding in missing)
                html.append("</ul></div>")
        html.append("</article>")

    @staticmethod
    def _inspect(html: list[str], pair: dict, claim: dict, heading: str, severity: str, body: str, fixture: bool) -> None:
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
            html.append(f'<a class="source-link" href="{url}">{label} <span aria-hidden="true">↗</span></a>')
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
        html.append('<details class="raw-comment"><summary>Raw comment source</summary>'
                    '<p class="subtle">Unformatted text captured with the review.</p>'
                    f'<pre>{escape(claim["body"])}</pre></details></div>')
        html.append('<div class="evaluation"><h4>Benchmark verdict</h4>'
                    f'<p class="verdict {decision or "ungraded"}">{_label(decision)}</p>')
        if claim["rationale"]:
            html.append(f'<p class="rationale">{escape(claim["rationale"])}</p>')
        if claim["evidence"]:
            html.append('<div class="verification"><h5>Verification evidence</h5>')
            for piece in claim["evidence"]:
                html.append(f'<div class="evidence"><strong>{_label(piece["type"])}</strong>'
                            f'<p>{escape(piece["text"])}</p></div>')
            html.append('</div>')
        html.append('</div></div><div class="inspector-nav"><a href="#comparison">Back to findings</a></div></article>')
