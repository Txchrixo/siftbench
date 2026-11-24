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


_DIGEST = re.compile(r"[0-9a-f]{64}")
_STATES = frozenset({
    "planned", "preparing", "awaiting_trigger", "awaiting_review", "completed",
    "invalid_input", "needs_attention", "timed_out", "failed", "quota",
})


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
                        or claim["decision"] not in DECISIONS | {None}
                        or (claim["rationale"] is not None and not isinstance(claim["rationale"], str))
                        or not isinstance(claim["evidence"], list)
                        or claim["decision"] == "confirmed_expected" and claim["expected_id"] not in expected):
                    raise StoreError("Invalid or duplicate claim")
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
                '<body><header class="shell"><div class="shell-inner">',
                f'<div class="brand"><img alt="" src="data:image/svg+xml;base64,{_encoded("mark-on-dark.svg")}">SiftBench</div>',
                '<nav aria-label="Report"><a href="#coverage">Coverage</a> <a href="#scores">Scores</a>'
                ' <a href="#comparison">Findings</a></nav></div></header>',
                f'<main><div class="title"><h1>Review benchmark</h1><p class="subtle">{escape(title)}</p></div>']
        groups: dict[str, dict[str, dict]] = {}
        for pair in bundle["pairs"]:
            groups.setdefault(_case_key(pair), {})[pair["provider_id"]] = pair
        providers = list(metrics["providers"])
        self._coverage(html, groups, providers)
        self._scores(html, shared, rows)
        self._comparison(html, groups, providers, bundle["fixture"])
        html.append(f"</main><script>{script}</script></body></html>")
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
        html.extend(f'<th scope="col">{escape(provider)}</th>' for provider in providers)
        html.append("</tr></thead><tbody>")
        for group in groups.values():
            html.append(f'<tr><th scope="row">{escape(_case_name(next(iter(group.values()))))}</th>')
            for provider in providers:
                pair = group.get(provider)
                html.append(f'<td><a class="state {pair["status"]}" href="#pair-{pair["pair_id"]}">'
                            f'{escape(_label(pair["status"]))}</a></td>' if pair else '<td>N/A</td>')
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    @staticmethod
    def _scores(html: list[str], shared: str, rows: list[tuple[str, ...]]) -> None:
        html.append(f'<section id="scores"><div class="section-heading"><h2>Scores</h2>'
                    f'<p class="subtle">Shared completed cases across all providers: {escape(shared)}.</p></div>')
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
            html.append(f'<tr><th scope="row">{escape(provider)}</th>')
            html.extend(f'<td class="metric">{escape(count)}</td>' for count in counts)
            html.append("</tr>")
        html.append("</tbody></table></div></section>")

    def _comparison(self, html: list[str], groups: dict, providers: list[str], fixture: bool) -> None:
        html.append('<section id="comparison"><div class="section-heading"><h2>Compare findings</h2></div>'
                    '<div id="comparison-controls" class="filters" hidden><label>Case<select id="compare-case">')
        html.extend(f'<option value="{escape(key, quote=True)}">{escape(_case_name(next(iter(group.values()))))}</option>'
                    for key, group in groups.items())
        html.append("</select></label>")
        for title, field, selected in (("First reviewer", "left", 0), ("Second reviewer", "right", 1)):
            html.append(f'<label>{title}<select id="compare-{field}"><option value="">None</option>')
            for index, provider in enumerate(providers):
                selection = " selected" if selected == index else ""
                html.append(f'<option value="{escape(provider, quote=True)}"{selection}>{escape(provider)}</option>')
            html.append("</select></label>")
        html.append('</div><div class="comparison-grid">')
        for key, group in groups.items():
            for pair in group.values():
                self._findings(html, key, pair, fixture)
        html.append('</div><p id="comparison-empty" role="status" ' + ('hidden' if groups else '') +
                    '>No findings are available for this selection.</p></section>')

    @staticmethod
    def _findings(html: list[str], key: str, pair: dict, fixture: bool) -> None:
        provider = escape(pair["provider_id"])
        html.append(f'<article class="findings" id="pair-{pair["pair_id"]}" data-case="{escape(key, quote=True)}" '
                    f'data-provider="{provider}"><h3>{provider}</h3>'
                    f'<p class="subtle">{escape(_case_name(pair))} · {_label(pair["status"])}</p>')
        if pair["reason"]:
            html.append(f'<p>{escape(pair["reason"])}</p>')
        if not pair["claims"]:
            message = "Review completed without captured findings." if pair["status"] == "completed" else "A completed review is required for scoring."
            html.append(f"<p>{message}</p>")
        for claim in pair["claims"]:
            html.append(f'<div class="finding" id="claim-{claim["claim_id"]}"><h4>{_label(claim["decision"])}</h4>'
                        f'<blockquote>{escape(claim["body"])}</blockquote>')
            url = _link(claim["source_url"])
            if url:
                title = "Fixture comment URL" if fixture else "Original GitHub comment"
                html.append(f'<p><a href="{url}">{title}</a></p>')
            if claim["rationale"]:
                html.append(f'<p>{escape(claim["rationale"])}</p>')
            if claim["evidence"]:
                html.append("<details open><summary>Evidence</summary>")
                for piece in claim["evidence"]:
                    html.append(f'<p><strong>{_label(piece["type"])}</strong><br>{escape(piece["text"])}</p>')
                html.append("</details>")
            html.append("</div>")
        if pair["status"] == "completed":
            matched = {claim["expected_id"] for claim in pair["claims"] if claim["decision"] == "confirmed_expected"}
            missing = [finding for finding in pair["expected"] if finding["expected_id"] not in matched]
            if missing:
                html.append('<div class="finding"><h4>Expected findings without a confirmed match</h4><ul>')
                html.extend(f'<li>{escape(finding["matching_criteria"])}</li>' for finding in missing)
                html.append("</ul></div>")
        html.append("</article>")
