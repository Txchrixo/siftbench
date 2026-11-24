import json
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ..grading import ScoreCalculator
from ..manifest import canonical_json
from ..store import StoreError


def _link(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc == "github.com" and parts.path.startswith("/"):
        return escape(url, quote=True)
    return None


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({numerator / denominator:.0%})" if denominator else "N/A"


def _label(value: str | None) -> str:
    return "Ungraded" if value is None else value.replace("_", " ").capitalize()


class ReportBuilder:
    @staticmethod
    def load_bundle(path: Path) -> dict:
        result = json.loads(path.read_bytes())
        if not isinstance(result, dict) or result.get("schema_version") != 1 or not isinstance(result.get("pairs"), list):
            raise StoreError("Unsupported results bundle")
        return result

    def write(self, bundle: dict, output: Path) -> None:
        output.mkdir(parents=True, exist_ok=True)
        metrics = ScoreCalculator.calculate(bundle)
        shared = ", ".join(f"{case} ({condition})" for case, condition in metrics["shared"]) or "No shared completed cases"
        scope = "Fixture run" if bundle["fixture"] else "Campaign"
        title = f"{scope}: {bundle['run_id']}"
        md = [f"# {title}", "", f"Shared completed cases: {shared}", "", "## Scores", ""]
        html = ["<!doctype html>", '<html lang="en"><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                "<title>SiftBench report</title>",
                "<style>html{color-scheme:light dark}body{font:16px/1.6 system-ui;max-width:72rem;margin:0 auto;padding:1.25rem}"
                "pre,blockquote,code{overflow-wrap:anywhere}section{margin-block:2rem}li{margin-block:.5rem}</style>",
                f"<main><h1>SiftBench</h1><p>{escape(title)}</p><section><h2>Scores</h2>",
                f"<p>Shared completed cases: {escape(shared)}</p><ul>"]
        for provider, counts in metrics["providers"].items():
            precision = _ratio(counts["matched"] + counts["novel"], counts["adjudicated"])
            recall = _ratio(counts["matched"], counts["expected"])
            row = f"{provider}: precision {precision}, recall {recall}, {counts['ungraded']} ungraded"
            html.append(f"<li>{escape(row)}</li>")
            md.append("- " + row)
        html.append("</ul></section><section><h2>Coverage</h2><ul>")
        md.extend(["", "## Coverage", ""])
        for pair in bundle["pairs"]:
            pair_id = escape(pair["pair_id"], quote=True)
            label = self._pair_label(pair)
            html.append(f'<li><a href="#pair-{pair_id}">{escape(label)}</a></li>')
            md.append("- " + label)
        html.append("</ul></section><section><h2>Findings</h2>")
        for pair in bundle["pairs"]:
            html.append(f'<article id="pair-{escape(pair["pair_id"], quote=True)}"><h3>{escape(self._pair_label(pair))}</h3>')
            if not pair["claims"]:
                html.append("<p>No findings captured for this pair.</p>")
            for claim in pair["claims"]:
                anchor = escape(claim["claim_id"], quote=True)
                html.append(f'<section id="claim-{anchor}"><h4>{escape(_label(claim["decision"]))}</h4>'
                            f'<blockquote>{escape(claim["body"])}</blockquote>')
                url = _link(claim["source_url"])
                if url:
                    source_label = "Fixture comment URL" if bundle["fixture"] else "Original GitHub comment"
                    html.append(f'<p><a href="{url}">{source_label}</a></p>')
                if claim["rationale"]:
                    html.append(f'<p>{escape(claim["rationale"])}</p>')
                for piece in claim["evidence"]:
                    html.append(f'<p><strong>{escape(_label(piece["type"]))} evidence</strong><br>{escape(piece["text"])}</p>')
                html.append("</section>")
            html.append("</article>")
        html.append("</section></main></html>")
        (output / "results.json").write_bytes(canonical_json(bundle) + b"\n")
        (output / "report.html").write_text("\n".join(html) + "\n", encoding="utf-8")
        (output / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    @staticmethod
    def _pair_label(pair: dict) -> str:
        repeat = f" repeat {pair['repeat_index']}" if pair["repeat_index"] else ""
        return f"{pair['case_id']} ({pair['condition']}) / {pair['provider_id']}{repeat}: {_label(pair['status'])}"
