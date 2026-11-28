from pathlib import Path
from runpy import run_path
import json
import re


ROOT = Path(__file__).resolve().parents[1]


def test_public_results_page_reuses_the_report_charts(tmp_path):
    run_path(str(ROOT / "docs/results-page/build.py"))["build"](tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    charts = json.loads((tmp_path / "public-charts.json").read_text(encoding="utf-8"))

    assert not re.search(r"\$\{?[a-z_]+", html)
    assert html.count('<g class="latency-point" tabindex="0"') == sum(map(len, charts["publication_seconds"].values()))
    assert html.count('<li class="overlap-row') == len(charts["reference_overlap"])
    assert html.count('class="info-trigger"') >= 8
    assert 'data-info-template="public_latency"' in html
    assert "data-range-title" in html and 'class="interval-plot"' in html
    assert all((tmp_path / asset).is_file() for asset in ("charts.css", "info.js", "theme.css"))


def test_public_page_names_no_pull_request():
    export = json.loads((ROOT / "benchmark/evaluation-v2/public-charts.json").read_text(encoding="utf-8"))

    assert set(export) == {"publication_bounds_seconds", "publication_bins", "publication_seconds",
                           "reference_overlap", "annotation_agreement"}
    assert all(isinstance(value, float) for values in export["publication_seconds"].values() for value in values)
    assert all(set(group) == {"reviewers", "count"} for group in export["reference_overlap"])
