from reviewbench.stats import median_interval, percent_interval, wilson_interval


def test_wilson_interval_keeps_perfect_scores_honest():
    low, high = wilson_interval(5, 5)
    assert round(low, 3) == 0.566 and high == 1.0
    low, high = wilson_interval(0, 5)
    assert low == 0.0 and round(high, 3) == 0.434
    assert wilson_interval(0, 0) is None and wilson_interval(6, 5) is None


def test_wilson_interval_narrows_with_more_observations():
    small, large = wilson_interval(8, 10), wilson_interval(80, 100)
    assert small[1] - small[0] > large[1] - large[0]
    assert percent_interval(4, 4) == "95% CI 51-100%"


def test_median_interval_is_deterministic_and_needs_enough_data():
    assert median_interval([1.0, 2.0, 3.0, 4.0]) is None
    values = [10.0, 12.0, 15.0, 20.0, 21.0, 25.0, 30.0]
    first = median_interval(values)
    assert first == median_interval(list(reversed(values)))
    assert min(values) <= first[0] <= 20.0 <= first[1] <= max(values)


def test_report_readings_gain_an_interval_only_when_they_are_ratios():
    from reviewbench.report import _with_interval

    assert _with_interval("3/4 (75%)") == "3/4 (75%, 95% CI 30-95%)"
    assert _with_interval("No reference findings") == "No reference findings"


def test_interval_chart_shares_one_axis_and_explains_each_range():
    from reviewbench.report import _interval_chart, _range_bar

    rows = [("greptile", "6/6 (100%)", "4/4 (100%)"), ("proofline", "10/10 (100%)", "3/4 (75%)"),
            ("coderabbit", "No scored findings", "No reference findings")]
    chart = _interval_chart(rows)
    assert chart.count('class="interval-row"') == 3
    # Four scored readings, each with a scale, a likely range, an observed score and a keyboard summary.
    assert chart.count('data-range-help="') == 16
    for part in ("observed score", "likely range", "scale"):
        assert f"Proofline · Reference recall · {part}" in chart
    assert 'data-range-range="Likely 30-95%"' in chart
    assert "No scored findings" in chart
    assert _interval_chart([("a", "No scored findings", "No reference findings")]) == ""
    bar = _range_bar(3, 4, title="Proofline · Reference recall", metric="Reference recall")
    assert 'data-range-detail="3 of 4 expected problems found"' in bar and "Wide: there are too few" in bar
    assert bar.count("range-part") == 3 and "Left end 0%: no expected problem found" in bar
    assert '<span class="range-label">Likely 30-95%' in bar and bar.index("range-label") > bar.index("</svg></span>")
