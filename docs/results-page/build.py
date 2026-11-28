"""Build the public results page from the checked-in aggregate export.

Charts, tooltips and explanation bubbles come from the report package, so the page reads like the
HTML reports. Run with the repository's `src` folder on PYTHONPATH.
"""

import argparse
from html import escape
import json
from pathlib import Path
from shutil import copyfile
from string import Template

from reviewbench.report import _interval_chart, _range_bar, _ratio, _reviewer
from reviewbench.report.icons import icon
from reviewbench.report.info import info, info_templates
from reviewbench.report.latency import Sample, latency_figure


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUMMARY = ROOT / "benchmark/evaluation-v2/summary.json"
CHARTS = SUMMARY.with_name("public-charts.json")
BRAND = ROOT / "assets/brand"
REPORT = ROOT / "src/reviewbench/report"
LOGOS = {"coderabbit": "reviewer-coderabbit.svg", "greptile": "reviewer-greptile.png", "proofline": "reviewer-proofline.png"}


def reviewer_label(key: str) -> str:
    return f'<span class="reviewer"><img src="{LOGOS[key]}" width="20" height="20" alt="">{escape(_reviewer(key))}</span>'


def score(successes: int, total: int, bounds: list[int] | None, reviewer: str, metric: str) -> str:
    if not total:
        return '<span class="muted">Unavailable</span>'
    bar = _range_bar(successes, total, title=f"{_reviewer(reviewer)} · {metric}", metric=metric,
                     bounds=tuple(bounds) if bounds else None)
    return (f'<strong>{round(100 * successes / total)}%</strong><span class="fraction">{successes}/{total}</span>'
            + (bar or '<small>Interval omitted</small>'))


def timing(metrics: dict) -> str:
    if not metrics["n"]:
        return '<span class="muted">Not measured</span>'
    interval = metrics["p50_ci95"]
    likely = f' · Likely {interval[0]:g}–{interval[1]:g} s' if interval else ""
    return (f'<strong>{metrics["p50"]:g} s</strong> median · {metrics["p95"]:g} s p95'
            f'<small>n = {metrics["n"]}{likely}</small>')


def overlap(groups: list[dict], expected: int, reviewers: int) -> str:
    rows = []
    for group in groups:
        names = [_reviewer(key) for key in group["reviewers"]]
        label = ("Detected by no reviewer" if not names else f"All {reviewers} reviewers" if len(names) == reviewers
                 else f"{names[0]} only" if len(names) == 1 else f'{", ".join(names[:-1])} and {names[-1]}')
        row_class = "overlap-row" if names else "overlap-row overlap-missed"
        rows.append(f'<li class="{row_class}"><span class="overlap-label">{escape(label)}</span>'
                    f'<meter min="0" max="{expected}" value="{group["count"]}" aria-hidden="true"></meter>'
                    f'<span class="overlap-count">{group["count"]}<span class="visually-hidden"> of {expected} reference findings</span></span></li>')
    return "".join(rows)


def build(output: Path) -> None:
    summary = json.loads(SUMMARY.read_bytes())
    charts = json.loads(CHARTS.read_bytes())
    coverage = summary["coverage"]
    reviewers = summary["reviewers"]
    expected = reviewers[0]["shared_graded_scores"]["expected"]
    groups = charts["reference_overlap"]
    if sum(group["count"] for group in groups) != expected:
        raise ValueError("Overlap counts do not match public reference total")

    bounds = charts["publication_bounds_seconds"]
    bin_headers = [f"<{bounds[0]}", *(f"{start}–<{end}" for start, end in zip(bounds, bounds[1:])), f"≥{bounds[-1]}"]
    rows, interval_rows, intervals = [], [], {}
    coverage_rows, finding_rows, timing_rows, distribution_rows = [], [], [], []
    samples = {}
    for reviewer in reviewers:
        key = reviewer["reviewer"]
        label = reviewer_label(key)
        metrics = reviewer["shared_graded_scores"]
        ci = reviewer["score_ci95_percent"]
        confirmed = metrics["matched"] + metrics["novel"]
        rows.append(f'<tr><th scope="row">{label}</th>'
                    f'<td>{score(confirmed, metrics["adjudicated"], ci["precision"], key, "Precision")}</td>'
                    f'<td>{score(metrics["matched"], metrics["expected"], ci["reference_recall"], key, "Reference recall")}</td></tr>')
        interval_rows.append((key, _ratio(confirmed, metrics["adjudicated"]), _ratio(metrics["matched"], metrics["expected"])))
        intervals[key] = ci
        finding_rows.append(f'<tr><th scope="row">{label}</th><td>{metrics["novel"]}</td>'
                            f'<td>{metrics["false_positives"]}</td><td>{metrics["duplicates"]}</td></tr>')
        if sum(group["count"] for group in groups if key in group["reviewers"]) != metrics["matched"]:
            raise ValueError(f"Overlap counts do not match public recall for {_reviewer(key)}")

        operations = reviewer["operations"]
        completion = operations["completion_seconds"]
        bins = charts["publication_bins"].get(key, [0] * len(bin_headers))
        times = charts["publication_seconds"].get(key, [])
        if len(bins) != len(bin_headers) or sum(bins) != completion["n"] or len(times) != completion["n"]:
            raise ValueError(f"Timing data does not match public sample size for {_reviewer(key)}")
        samples[key] = [Sample(seconds, _reviewer(key)) for seconds in times]
        timing_rows.append(f'<tr><th scope="row">{label}</th><td>{timing(completion)}</td>'
                           f'<td>{timing(operations["first_verified_finding_seconds"])}</td></tr>')
        distribution_rows.append(f'<tr><th scope="row">{label}</th>' + "".join(f"<td>{count}</td>" for count in bins) + "</tr>")

        counts = operations["status_counts"]
        quota, failed = counts.get("quota", 0), counts.get("failed", 0)
        other = operations["planned"] - operations["completed"] - quota - failed
        coverage_rows.append(f'<tr><th scope="row">{label}</th><td>{operations["completed"]}/{operations["planned"]}</td>'
                             f'<td>{quota}</td><td>{failed}</td><td>{other}</td></tr>')

    agreement = charts["annotation_agreement"]
    template = Template((HERE / "index.html").read_text(encoding="utf-8"))
    html = template.substitute(
        completed=coverage["completed"], planned=coverage["planned"],
        scored_prs=summary["scored_prs"], expected=expected,
        score_rows="\n".join(rows), interval_chart=_interval_chart(interval_rows, intervals),
        coverage_rows="\n".join(coverage_rows), finding_rows="\n".join(finding_rows),
        latency_figure=latency_figure(samples, explanation="public_latency",
                                      link_hint="Hover or focus a dot to read its time."),
        timing_rows="\n".join(timing_rows),
        distribution_headers="".join(f'<th scope="col">{escape(label)}</th>' for label in bin_headers),
        distribution_rows="\n".join(distribution_rows), overlap_rows=overlap(groups, expected, len(reviewers)),
        unanimous=agreement["unanimous"], annotated=agreement["claims"],
        agreement_percent=f'{100 * agreement["unanimous"] / agreement["claims"]:.2f}',
        kappa=f'{agreement["kappa"]:.4f}', info_templates=info_templates(),
        **{f"icon_{name}": icon(name) for name in ("overview", "coverage", "compare", "info", "external", "file")},
        **{f"info_{key}": info(key) for key in ("completed_attempts", "scored_conditions", "precision", "recall",
                                                 "duplicates", "review_completion", "first_verified", "agreement")},
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(html, encoding="utf-8", newline="\n")
    copyfile(SUMMARY, output / "summary.json")
    copyfile(CHARTS, output / "public-charts.json")
    for asset in ("results.css", "results.js"):
        copyfile(HERE / asset, output / asset)
    for asset in ("theme.css", "charts.css", "info.js", "footer-wordmark.svg", *LOGOS.values()):
        copyfile(REPORT / asset, output / asset)
    for asset in ("logo-on-dark.svg", "favicon.svg", "manrope-latin.woff2", "OFL-Manrope.txt"):
        copyfile(BRAND / asset, output / asset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
