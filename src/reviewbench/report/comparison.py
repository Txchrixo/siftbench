"""Self-contained, private report for two runs on matched samples."""

from html import escape
from importlib.resources import files
from pathlib import Path

from . import _csp_hash, _link, _ratio, _reviewer
from ..manifest import canonical_json


def _latency(stats: dict) -> str:
    return f'{stats["p50"]} s median · {stats["p95"]} s p95 · n={stats["n"]}' if stats["n"] else "Unavailable"


def _cost(metrics: dict) -> str:
    return ", ".join(f'{currency} {totals["p50"]} minor units median · n={totals["n"]}'
                     for currency, totals in metrics["observed_cost_minor"].items()) or "Unavailable"


def metric_rows(metrics: dict) -> tuple[tuple[str, str], ...]:
    false = metrics["false_positives_per_graded_pr"]
    severe = metrics["high_severity"]
    return (
        ("Precision", _ratio(metrics["matched"] + metrics["novel"], metrics["adjudicated"])),
        ("Reference recall", _ratio(metrics["matched"], metrics["expected"])),
        ("False positives / graded PR", f'{false["false_positives"]} / {false["graded_prs"]}' if false["graded_prs"] else "Unavailable"),
        ("High or critical references missed on graded PRs", f'{severe["missed"]} / {severe["expected"]}' if severe["expected"] else "Unavailable"),
        ("Review completion latency", _latency(metrics["completion_seconds"])),
        ("Time to first verified finding", _latency(metrics["first_verified_finding_seconds"])),
        ("Observed cost per attempt", _cost(metrics)),
    )


def write_comparison(result: dict, out: Path) -> None:
    root = files(__package__)
    css = root.joinpath("theme.css").read_text(encoding="utf-8")
    css += """
      * { box-sizing: border-box; }
      html { color-scheme: dark; }
      body { margin: 0; background: var(--background); color: var(--text-primary); font: 15px/1.6 system-ui, sans-serif; }
      main { max-width: 72rem; margin: auto; padding: clamp(1rem, 4vw, 3rem); }
      h1, h2, p { margin: 0 0 .75rem; }
      p { color: var(--text-secondary); overflow-wrap: anywhere; }
      section { margin-block: 2rem; }
      .matrix { overflow: auto; background: var(--surface); border-radius: .4rem; }
      table { width: 100%; min-width: 30rem; border-collapse: collapse; }
      th, td { padding: .65rem .8rem; border-bottom: 1px solid var(--border); text-align: left; }
      thead { color: var(--text-secondary); }
      summary { cursor: pointer; padding-block: .6rem; }
      a { color: var(--info); }
      :focus-visible { outline: 2px solid var(--info); outline-offset: 2px; }
    """
    html = ['<!doctype html><html lang="en"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'sha256-{_csp_hash(css)}\'; base-uri \'none\'">',
            f'<title>Compare campaigns | SiftBench</title><style>{css}</style><main><h1>Compare campaigns</h1>',
            f'<p>{escape(result["before_run"])} → {escape(result["after_run"])}</p>',
            f'<p>{result["planned_samples"]} pinned samples. Metrics include only the same samples completed in both runs for each reviewer. Coverage includes every sample planned for that reviewer in both runs.</p>']
    if result["fixture"]:
        html.append('<p>Fixture data</p>')
    for pair in result["reviewers"]:
        provider = escape(_reviewer(pair["reviewer"]))
        html.append(f'<section><h2>{provider}</h2><p>{pair["completed_in_both"]} / {pair["planned_in_both"]} matched completions.</p>')
        html.append('<div class="matrix" tabindex="0" role="region" aria-label="Matched metrics">'
                    '<table><thead><tr><th scope="col">Metric</th><th scope="col">Before</th><th scope="col">After</th></tr></thead><tbody>')
        for (label, first), (_, second) in zip(metric_rows(pair["before"]), metric_rows(pair["after"]), strict=True):
            html.append(f'<tr><th scope="row">{label}</th><td>{escape(first)}</td><td>{escape(second)}</td></tr>')
        html.append('</tbody></table></div><details><summary>Per-sample coverage and source links</summary>'
                    '<div class="matrix" tabindex="0" role="region" aria-label="Case coverage"><table><thead><tr>'
                    '<th scope="col">Case condition</th><th scope="col">Before</th><th scope="col">After</th></tr></thead><tbody>')
        for case in pair["cases"]:
            label = escape(f'{case["case_id"]} · {case["condition"]} · Repeat {case["repeat_index"] + 1}')
            html.append(f'<tr><th scope="row">{label}</th>')
            for side in ("before", "after"):
                status = escape(case[f"{side}_status"].replace("_", " ").capitalize())
                link = _link(case[f"{side}_pr"])
                html.append(f'<td>{status} · <a href="{link}">PR</a></td>' if link else f'<td>{status}</td>')
            html.append('</tr>')
        html.append('</tbody></table></div></details></section>')
    html.append('<details><summary>Recorded reviewer settings</summary>')
    for side in ("before", "after"):
        html.append(f'<h2>{side.capitalize()}</h2>')
        if not result[f"{side}_configuration"]:
            html.append('<p>Unavailable in the source bundle.</p>')
        for row in result[f"{side}_configuration"]:
            settings = ' · '.join(f'{name.replace("_", " ")}: {value if value is not None else "unavailable"}' for name, value in row.items())
            html.append(f'<p>{escape(settings)}</p>')
    html.append('</details></main></html>')
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_bytes(canonical_json(result) + b"\n")
    (out / "comparison.html").write_text("\n".join(html) + "\n", encoding="utf-8", newline="\n")
