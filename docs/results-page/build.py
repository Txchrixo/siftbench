"""Build the public results page from the checked-in aggregate export."""

import argparse
from html import escape
import json
from pathlib import Path
from shutil import copyfile
from string import Template


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUMMARY = ROOT / "benchmark/evaluation-v2/summary.json"
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
             '<small>95% CI unavailable</small>'
    )
    return f'<strong>{percent}%</strong><span class="fraction">{numerator}/{denominator}</span>{details}'


def build(output: Path) -> None:
    summary = json.loads(SUMMARY.read_bytes())
    coverage = summary["coverage"]
    rows = []
    coverage_rows = []
    for reviewer in summary["reviewers"]:
        name, logo = REVIEWERS[reviewer["reviewer"]]
        label = f'<img src="{logo}" width="20" height="20" alt="">{escape(name)}'
        metrics = reviewer["shared_graded_scores"]
        intervals = reviewer["score_ci95_percent"]
        precision = score(metrics["matched"] + metrics["novel"], metrics["adjudicated"], intervals["precision"])
        recall = score(metrics["matched"], metrics["expected"], intervals["reference_recall"])
        rows.append(f'<tr><th scope="row"><span>{label}</span></th><td>{precision}</td><td>{recall}</td></tr>')

        operations = reviewer["operations"]
        counts = operations["status_counts"]
        quota = counts.get("quota", 0)
        failed = counts.get("failed", 0)
        other = operations["planned"] - operations["completed"] - quota - failed
        coverage_rows.append(f'<tr><th scope="row">{escape(name)}</th>'
                             f'<td>{operations["completed"]}/{operations["planned"]}</td>'
                             f'<td>{quota}</td><td>{failed}</td><td>{other}</td></tr>')

    template = Template((HERE / "index.html").read_text(encoding="utf-8"))
    html = template.substitute(
        completed=coverage["completed"], planned=coverage["planned"],
        scored_prs=summary["scored_prs"],
        expected=summary["reviewers"][0]["shared_graded_scores"]["expected"],
        score_rows="\n".join(rows), coverage_rows="\n".join(coverage_rows),
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(html, encoding="utf-8", newline="\n")
    copyfile(SUMMARY, output / "summary.json")
    copyfile(HERE / "results.css", output / "results.css")
    copyfile(REPORT / "theme.css", output / "theme.css")
    for asset in ("logo-on-dark.svg", "favicon.svg", "manrope-latin.woff2", "OFL-Manrope.txt"):
        copyfile(BRAND / asset, output / asset)
    for _, logo in REVIEWERS.values():
        copyfile(REPORT / logo, output / logo)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
