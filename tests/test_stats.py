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
    assert percent_interval(4, 4) == "95% CI 51–100%"


def test_median_interval_is_deterministic_and_needs_enough_data():
    assert median_interval([1.0, 2.0, 3.0, 4.0]) is None
    values = [10.0, 12.0, 15.0, 20.0, 21.0, 25.0, 30.0]
    first = median_interval(values)
    assert first == median_interval(list(reversed(values)))
    assert min(values) <= first[0] <= 20.0 <= first[1] <= max(values)
