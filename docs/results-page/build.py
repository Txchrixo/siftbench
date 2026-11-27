"""Build the public results page from the checked-in aggregate export."""

import argparse
from html import escape
import json
from math import ceil
from pathlib import Path
from runpy import run_path
from shutil import copyfile
from string import Template


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUMMARY = ROOT / "benchmark/evaluation-v2/summary.json"
CHARTS = SUMMARY.with_name("public-charts.json")
BRAND = ROOT / "assets/brand"
REPORT = ROOT / "src/reviewbench/report"
REVIEWERS = {
    "coderabbit": ("CodeRabbit", "reviewer-coderabbit.svg"),
    "greptile": ("Greptile", "reviewer-greptile.png"),
    "proofline": ("Proofline", "reviewer-proofline.png"),
}


def score(numerator: int, denominator: int, interval: list[int] | None) -> str:
    if not denominator:
        return '<span class="muted">Unavailable</span>'
    percent = round(100 * numerator / denominator)
    details = (
        f'<span class="range" aria-hidden="true" style="--score:{percent}%;'
        f'--low:{interval[0]}%;--high:{interval[1]}%"><i></i></span>'
        f'<small>95% CI {interval[0]}–{interval[1]}%</small>'
        if interval is not None
        else f'<span class="range" aria-hidden="true" style="--score:{percent}%"></span>'
             '<small>Interval omitted</small>'
    )
    return f'<strong>{percent}%</strong><span class="fraction">{numerator}/{denominator}</span>{details}'


def timing_stats(metrics: dict) -> str:
    if not metrics["n"]:
        return '<span class="muted">Not measured</span>'
    interval = metrics["p50_ci95"]
    ci = f' · 95% CI {interval[0]:g}–{interval[1]:g} s' if interval else ""
    return (f'<strong>{metrics["p50"]:g} s</strong> median · {metrics["p95"]:g} s p95'
            f'<small>n = {metrics["n"]}{ci}</small>')


def build(output: Path) -> None:
    summary = json.loads(SUMMARY.read_bytes())
    charts = json.loads(CHARTS.read_bytes())
    coverage = summary["coverage"]
    reviewers = summary["reviewers"]
    expected = reviewers[0]["shared_graded_scores"]["expected"]
    overlap = charts["reference_overlap"]
    if sum(group["count"] for group in overlap) != expected:
        raise ValueError("Overlap counts do not match public reference total")

    bounds = charts["publication_bounds_seconds"]
    bin_headers = [f"<{bounds[0]}", *(f"{start}–<{end}" for start, end in zip(bounds, bounds[1:])),
                   f"≥{bounds[-1]}"]
    max_p95 = max(reviewer["operations"]["completion_seconds"]["p95"] or 0 for reviewer in reviewers)
    limit = max(60, ceil(max_p95 / 60) * 60)
    rows = []
    coverage_rows = []
    finding_rows = []
    timing_rows = []
    first_finding_rows = []
    distribution_rows = []
    for reviewer in reviewers:
        key = reviewer["reviewer"]
        name, logo = REVIEWERS[key]
        label = f'<span class="reviewer"><img src="{logo}" width="20" height="20" alt="">{escape(name)}</span>'
        metrics = reviewer["shared_graded_scores"]
        intervals = reviewer["score_ci95_percent"]
        precision = score(metrics["matched"] + metrics["novel"], metrics["adjudicated"], intervals["precision"])
        recall = score(metrics["matched"], metrics["expected"], intervals["reference_recall"])
        rows.append(f'<tr><th scope="row">{label}</th><td>{precision}</td><td>{recall}</td></tr>')
        finding_rows.append(f'<tr><th scope="row">{label}</th><td>{metrics["novel"]}</td>'
                            f'<td>{metrics["false_positives"]}</td><td>{metrics["duplicates"]}</td></tr>')
        if sum(group["count"] for group in overlap if key in group["reviewers"]) != metrics["matched"]:
            raise ValueError(f"Overlap counts do not match public recall for {name}")

        operations = reviewer["operations"]
        completion = operations["completion_seconds"]
        bins = charts["publication_bins"].get(key, [0] * len(bin_headers))
        if len(bins) != len(bin_headers) or sum(bins) != completion["n"]:
            raise ValueError(f"Timing distribution does not match public sample size for {name}")
        track = ""
        if completion["n"]:
            p50, p95 = (100 * completion[field] / limit for field in ("p50", "p95"))
            track = f'<span class="timing-track" style="--p50:{p50:.3f}%;--p95:{p95:.3f}%" aria-hidden="true"></span>'
        timing_rows.append(f'<li><div>{label}<p>{timing_stats(completion)}</p></div>{track}</li>')
        first_finding_rows.append(f'<tr><th scope="row">{label}</th>'
                                  f'<td>{timing_stats(operations["first_verified_finding_seconds"])}</td></tr>')
        distribution_rows.append(f'<tr><th scope="row">{label}</th>'
                                 + "".join(f"<td>{count}</td>" for count in bins) + "</tr>")

        counts = operations["status_counts"]
        quota = counts.get("quota", 0)
        failed = counts.get("failed", 0)
        other = operations["planned"] - operations["completed"] - quota - failed
        coverage_rows.append(f'<tr><th scope="row">{label}</th>'
                             f'<td>{operations["completed"]}/{operations["planned"]}</td>'
                             f'<td>{quota}</td><td>{failed}</td><td>{other}</td></tr>')

    overlap_rows = []
    for group in overlap:
        names = [REVIEWERS[key][0] for key in group["reviewers"]]
        label = {0: "Detected by no reviewer", 1: f"{', '.join(names)} only",
                 len(reviewers): "All three reviewers"}.get(len(names), " and ".join(names))
        count = group["count"]
        overlap_rows.append(f'<li><span>{escape(label)}</span>'
                            f'<span class="overlap-track" aria-hidden="true"><i style="--count:{100 * count / expected:.3f}%"></i></span>'
                            f'<strong>{count}</strong></li>')
    agreement = charts["annotation_agreement"]
    icon = run_path(str(REPORT / "icons.py"))["icon"]
    template = Template((HERE / "index.html").read_text(encoding="utf-8"))
    html = template.substitute(
        completed=coverage["completed"], planned=coverage["planned"],
        scored_prs=summary["scored_prs"], expected=expected,
        score_rows="\n".join(rows), coverage_rows="\n".join(coverage_rows),
        finding_rows="\n".join(finding_rows), timing_rows="\n".join(timing_rows),
        timing_limit=limit, timing_midpoint=limit // 2,
        first_finding_rows="\n".join(first_finding_rows),
        distribution_headers="".join(f'<th scope="col">{escape(label)}</th>' for label in bin_headers),
        distribution_rows="\n".join(distribution_rows), overlap_rows="\n".join(overlap_rows),
        unanimous=agreement["unanimous"], annotated=agreement["claims"],
        agreement_percent=f'{100 * agreement["unanimous"] / agreement["claims"]:.2f}',
        kappa=f'{agreement["kappa"]:.4f}',
        **{f"icon_{name}": icon(name) for name in ("overview", "coverage", "compare", "info", "external", "file")},
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(html, encoding="utf-8", newline="\n")
    copyfile(SUMMARY, output / "summary.json")
    copyfile(CHARTS, output / "public-charts.json")
    for asset in ("results.css", "results.js"):
        copyfile(HERE / asset, output / asset)
    copyfile(REPORT / "theme.css", output / "theme.css")
    copyfile(REPORT / "footer-wordmark.svg", output / "footer-wordmark.svg")
    for asset in ("logo-on-dark.svg", "favicon.svg", "manrope-latin.woff2", "OFL-Manrope.txt"):
        copyfile(BRAND / asset, output / asset)
    for _, logo in REVIEWERS.values():
        copyfile(REPORT / logo, output / logo)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
