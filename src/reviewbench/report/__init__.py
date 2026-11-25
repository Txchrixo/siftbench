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
        for reviewer in ("greptile", "proofline"):
            css = css.replace(f"__MARK_{reviewer.upper()}__", _encoded(f"reviewer-{reviewer}.png"))
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
                '<section id="overview" data-report-view tabindex="-1"><div class="title"><div><h1>Campaign overview</h1>'
                f'<p class="run-meta"><span>{scope}</span> <code>{escape(bundle["run_id"])}</code></p></div></div>']
        groups: dict[str, dict[str, dict]] = {}
        for pair in bundle["pairs"]:
            groups.setdefault(_case_key(pair), {})[pair["provider_id"]] = pair
        providers = list(metrics["providers"])
        html.append('<p class="view-intro">See how the reviewers performed on changes they both reviewed.</p>'
                    if len(providers) == 2 else '<p class="view-intro">See the results on cases completed by every reviewer.</p>')
        if isinstance(bundle.get("analysis"), str) and bundle["analysis"]:
            html.append(f'<p class="analysis-note">{escape(bundle["analysis"])}</p>')
        completed = sum(pair["status"] == "completed" for pair in bundle["pairs"])
        html.append('<dl class="run-stats" aria-label="Campaign totals">')
        for label, value in (("Reviews captured", f'{completed} / {len(bundle["pairs"])}'),
                             ("Case conditions scored", len(metrics["shared"])), ("Reviewers", len(providers))):
            html.append(f'<div><dt>{label}</dt><dd>{value}</dd></div>')
        html.append('</dl>')
        self._scores(html, metrics["shared"], rows)
        html.append('<div class="overview-paths"><a href="#coverage"><strong>Check coverage</strong>'
                    '<span>See which reviews completed and where there are gaps.</span></a>'
                    '<a href="#comparison"><strong>Compare findings</strong>'
                    '<span>Pick a case and trace a verdict to the original comment and evidence.</span></a></div></section>')
        self._coverage(html, groups, providers)
        self._comparison(html, groups, providers, bundle)
        html.append('</main><footer class="report-footer"><img alt="SiftBench" width="270" height="64" '
                    f'src="data:image/svg+xml;base64,{_encoded("footer-wordmark.svg")}"></footer>')
        html.append(f"<script>{script}</script></body></html>")
        (output / "results.json").write_bytes(canonical_json(bundle) + b"\n")
        (output / "report.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")
        (output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
        (output / "OFL-Manrope.txt").write_bytes(root.joinpath("OFL-Manrope.txt").read_bytes())

    @staticmethod
    def _coverage(html: list[str], groups: dict, providers: list[str]) -> None:
        html.append('<section id="coverage" data-report-view tabindex="-1"><div class="view-heading"><h1>Review coverage</h1>'
                    '<p class="view-intro">Each row is one case and condition. Select a reviewer status to open that review.</p></div>')
        if not groups:
            html.append("<p>No cases to compare.</p></section>")
            return
        html.append('<div class="scroll" tabindex="0" role="region" aria-label="Case coverage"><table><thead><tr>'
                    '<th scope="col">Case and condition</th>')
        html.extend(f'<th scope="col">{_reviewer_display(provider)}</th>' for provider in providers)
        html.append("</tr></thead><tbody>")
        for group in groups.values():
            first = next(iter(group.values()))
            case = _case_display(first)
            title = first.get("case_title")
            html.append('<tr><th scope="row">')
            if title:
                html.append(f'<span class="coverage-title">{escape(title)}</span>')
            html.append(f'<span class="coverage-case">{escape(case)}</span></th>')
            for provider in providers:
                pair = group.get(provider)
                if pair:
                    status = _review_status(pair)
                    label = escape(f"{_reviewer(provider)}, {case}: {status}", quote=True)
                    html.append(f'<td><a class="state {pair["status"]}" href="#pair-{pair["pair_id"]}" '
                                f'aria-label="{label}">{status}</a></td>')
                else:
                    html.append('<td>N/A</td>')
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    @staticmethod
    def _scores(html: list[str], shared: list[tuple[str, str]], rows: list[tuple[str, ...]]) -> None:
        html.append('<section id="scores" tabindex="-1"><div class="section-heading"><h2>Reviewer scores</h2>'
                    '<p class="subtle">Scores use only the case conditions completed by every reviewer.</p></div>')
        if not rows:
            html.append("<p>No reviewer results are available.</p></section>")
            return
        ReportBuilder._score_table(html, "Reviewer scores", ("Precision", "Reference recall", "Additional findings"), rows, (1, 2, 4))
        html.append('<details class="score-scope"><summary>How to read the scores</summary><dl class="metric-guide">'
                    '<div><dt>Precision</dt><dd>How many scored findings were confirmed.</dd></div>'
                    '<div><dt>Reference recall</dt><dd>How many expected findings the reviewer caught.</dd></div>'
                    '<div><dt>Additional findings</dt><dd>Verified findings outside the expected set.</dd></div></dl>')
        if shared:
            cases = [escape(_case_display({"case_id": case, "condition": condition, "repeat_index": 0}))
                     for case, condition in shared]
            html.append(f'<p class="subtle"><strong>Scored case conditions.</strong> {" / ".join(cases)}</p>')
        html.append('</details><details class="score-scope"><summary>Additional metrics</summary>')
        ReportBuilder._score_table(html, "Additional metrics", ("False positives", "Context requests", "Duplicates", "Unresolved", "Ungraded"), rows, (3, 5, 6, 7, 8))
        html.append('</details></section>')

    @staticmethod
    def _score_table(html: list[str], label: str, columns: tuple[str, ...], rows: list[tuple[str, ...]], indexes: tuple[int, ...]) -> None:
        html.append(f'<div class="scroll score-table" tabindex="0" role="region" aria-label="{label}">'
                    '<table><thead><tr><th scope="col">Reviewer</th>')
        html.extend(f'<th scope="col" class="metric">{column}</th>' for column in columns)
        html.append('</tr></thead><tbody>')
        for row in rows:
            html.append(f'<tr><th scope="row">{escape(_reviewer(row[0]))}</th>')
            for index in indexes:
                value, divider, percentage = row[index].partition(" (")
                suffix = f' <span class="score-percent">({escape(percentage)}</span>' if divider else ""
                html.append(f'<td class="metric">{escape(value)}{suffix}</td>')
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
            label = f'{title} · {_label(case["condition"])}' if title else _case_display(case)
            if title and case["repeat_index"]:
                label += f' · Repeat {case["repeat_index"]}'
            html.append(f'<option value="{escape(key, quote=True)}" data-title="{escape(title, quote=True)}">'
                        f'{icon("pr")}<span class="option-label">{escape(label)}</span></option>')
        html.append(_SELECT_END)
        html.append('<fieldset class="reviewer-picker"><legend>Reviewers</legend>'
                    '<details id="reviewer-select"><summary><span class="visually-hidden">Select reviewers</span>'
                    '<span id="reviewer-preview"></span><span id="reviewer-count"></span></summary>'
                    '<div class="reviewer-panel">')
        if len(providers) > 2:
            html.append('<div class="reviewer-actions"><button type="button" data-select-reviewers="all">Select all</button>'
                        '<button type="button" data-select-reviewers="none">Clear selection</button></div>')
        html.append('<div class="reviewer-options">')
        for provider in providers:
            html.append(f'<label class="reviewer-option"><input type="checkbox" name="reviewer" '
                        f'value="{escape(provider, quote=True)}" checked>{_reviewer_display(provider)}</label>')
        html.append('</div></div></details></fieldset>')
        html.append('</div><h2 id="selected-case" hidden></h2><p id="comparison-status" class="visually-hidden" role="status"></p>')
        changes = {(item["base_sha"], item["head_sha"]): item["files"] for item in bundle.get("changes", [])}
        for key, group in groups.items():
            pairs = list(group.values())
            revisions = {(pair.get("base_sha"), pair.get("head_sha")) for pair in pairs}
            if len(revisions) == 1:
                self._files(html, key, changes.get(next(iter(revisions))), group)
            self._issues(html, key, group, providers, bundle.get("finding_groups", []))
        html.append('<div class="comparison-grid">')
        inspections: list[str] = []
        for key, group in groups.items():
            for pair in group.values():
                self._findings(html, inspections, key, pair, bundle["fixture"])
        html.append('</div><p id="comparison-empty" class="empty" ' + ('hidden' if groups else '') +
                    '>No reviews are available for this selection.</p>')
        html.append('<div class="inspection-list"><h2 class="inspection-heading">Finding detail</h2>')
        html.extend(inspections)
        html.append('</div></section>')

    @staticmethod
    def _files(html: list[str], key: str, changes: list[dict] | None, group: dict) -> None:
        if changes is None:
            return
        html.append(f'<details class="case-files" data-context-case="{escape(key, quote=True)}">'
                    f'<summary>{icon("diff")}Files changed <span>{len(changes)}</span></summary>')
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
                    location = f'{side[0]}{line}'
                    source = _link(claim.get("source_url"))
                    original = f'<a href="{source}">{icon("external")}GitHub comment</a>' if source else ''
                    comment = (f'<details class="diff-comment" data-diff-provider="{escape(provider)}">'
                               f'<summary><code class="diff-location">{location}</code>{_reviewer_display(provider)} '
                               f'<strong>{inline(title or "Review comment")}</strong></summary>'
                               + (f'<p class="diff-severity">Reviewer severity · {escape(severity)}</p>' if severity else '') +
                               f'<div class="review-copy">{blocks(body)}</div>'
                               f'<div class="diff-comment-links"><a href="#claim-{claim["claim_id"]}">Open finding and evidence</a>'
                               f'{original}</div></details>')
                    annotations.setdefault(path, {}).setdefault((side, line), []).append(comment)
        for item in changes:
            path = item["path"]
            count = "Binary" if item["added"] is None else f'+{item["added"]} / −{item["removed"]}'
            auto_open = ' open' if len(changes) == 1 else ''
            html.append(f'<details class="changed-file" data-file-path="{escape(path, quote=True)}"{auto_open}>'
                        f'<summary>{icon("file")}<code>{escape(path)}</code><span>{count}</span></summary>'
                        f'{render_file_patch(item["patch"], path, annotations.get(path))}</details>')
        if len(changes) > 1:
            html.append('<p class="empty" data-no-files role="status" hidden>No file paths match this filter.</p>')
        if not changes:
            html.append('<p class="empty">These commits have no file changes.</p>')
        html.append('</details>')

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
            path = urlsplit(pair["pr_url"]).path
            if re.fullmatch(r"/[^/]+/[^/]+/pull/[0-9]+", path):
                number = path.rsplit("/", 1)[1]
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
        summaries = [claim for claim in pair["claims"] if claim["decision"] == "out_of_scope"
                     and (claim.get("source_object_id") or "").startswith("review:")]
        findings = [claim for claim in pair["claims"] if claim not in summaries]
        for subset, label in ((findings, "Captured findings"), (summaries, "Formal review summaries")):
            if not subset:
                continue
            html.append(f'<h4 class="claim-group">{label} <span>{len(subset)}</span></h4><ul class="claim-index">')
            for claim in subset:
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
                    f'<p class="verdict {decision or "ungraded"}">{_label(decision)}</p>')
        if claim["rationale"]:
            html.append(f'<p class="rationale">{escape(claim["rationale"])}</p>')
        if claim["evidence"]:
            html.append(f'<div class="verification"><h5>{icon("evidence")}Verification evidence</h5>')
            for piece in claim["evidence"]:
                html.append(f'<div class="evidence"><strong>{_label(piece["type"])}</strong>'
                            f'<p>{escape(piece["text"])}</p></div>')
            html.append('</div>')
        html.append(f'</div></div><div class="inspector-nav"><a href="#comparison">{icon("back")}Back to findings</a></div></article>')
