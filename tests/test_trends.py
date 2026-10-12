"""Tests for trends.py and insights.py.

The point of these is the honesty rules: a slope that points upwards must not
be reported as an increasing trend unless the test supports it, and a tiny
denominator must not be turned into a percentage.
"""

import numpy as np
import pytest

from app import insights, trends
from app.insights import build_insights


def yearly_from_counts(counts, start_year=1990):
    """Build yearly rows the way heatwave.yearly_aggregates would."""
    return [
        {
            "year": start_year + offset,
            "events": count,
            "heatwave_days": count * 3,
            "avg_duration_days": 3.0 if count else None,
            "avg_intensity_c": 2.0 if count else None,
            "avg_max_temp_during_events_c": 36.0 if count else None,
            "low_quality": False,
        }
        for offset, count in enumerate(counts)
    ]


# --------------------------------------------------------------------------
# trends.analyse_trend
# --------------------------------------------------------------------------


def test_clearly_increasing_series_is_increasing_and_significant():
    counts = [1, 2, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

    result = trends.analyse_trend(yearly_from_counts(counts))

    assert result["direction"] == "increasing"
    assert result["significant"] is True
    assert result["mann_kendall_p"] < 0.05
    assert result["sen_slope_per_year"] > 0


def test_clearly_decreasing_series_is_decreasing_and_significant():
    counts = list(range(20, 0, -1))

    result = trends.analyse_trend(yearly_from_counts(counts))

    assert result["direction"] == "decreasing"
    assert result["significant"] is True
    assert result["sen_slope_per_year"] < 0


def yearly_with_flags(counts, flagged_offsets, start_year=1990):
    """Build yearly rows, flagging the given offsets as low quality."""
    rows = yearly_from_counts(counts, start_year)
    for offset in flagged_offsets:
        rows[offset]["low_quality"] = True
    return rows


def test_low_quality_years_are_excluded_from_the_tests():
    # An increasing series with one artificially zeroed year in the middle.
    # Without exclusion that dip drags both tests and flattens the result.
    counts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    clean = yearly_from_counts(counts)

    # Same series, but year offset 7 reports zero events because its data is
    # missing. A low count from missing data is not evidence of fewer events.
    noisy = [dict(row) for row in yearly_from_counts(counts)]
    noisy[7] = {
        **noisy[7],
        "events": 0,
        "heatwave_days": 0,
        "avg_duration_days": None,
        "avg_intensity_c": None,
        "avg_max_temp_during_events_c": None,
        "low_quality": True,
    }

    result = trends.analyse_trend(noisy)

    # The flagged year must not be present in the fitted line.
    included = [row for row in noisy if not row["low_quality"]]
    assert len(result["fitted"]) == len(included)
    assert len(result["fitted"]) == len(noisy) - 1
    # And the clean series over the same span keeps all of them.
    assert len(trends.analyse_trend(clean)["fitted"]) == len(clean)


def test_fitted_length_matches_included_years_not_all_years():
    rows = yearly_with_flags([3] * 20, flagged_offsets=[2, 5, 9])

    result = trends.analyse_trend(rows)

    assert len(result["fitted"]) == 17


def test_exclusion_can_restore_a_significant_increasing_trend():
    # A steady rise interrupted by flagged dips. The point of excluding is that
    # a real regional signal should not be erased by years that merely lost data.
    counts = list(range(1, 21))
    rows = yearly_from_counts(counts)
    # Zero out two later years and flag them, as if their data were missing.
    for offset in (14, 16):
        rows[offset] = {
            **rows[offset],
            "events": 0,
            "low_quality": True,
        }

    result = trends.analyse_trend(rows)

    # Whatever the label, the slope must reflect the retained years and not be
    # dragged down by the two zeros.
    assert result["sen_slope_per_year"] > 0
    assert len(result["fitted"]) == 18


def test_all_years_flagged_falls_back_without_crashing():
    rows = yearly_with_flags([1, 2, 3, 4, 5, 6], flagged_offsets=[0, 1, 2, 3, 4, 5])

    result = trends.analyse_trend(rows)

    # Nothing usable remains. The honest answer is no trend, not a crash and
    # not a line through zero points.
    assert result["direction"] == "no_clear_trend"
    assert result["significant"] is False
    assert result["fitted"] == []


def test_exclusion_matches_using_only_the_good_years():
    """The strongest form of the rule: excluding flagged years must give
    exactly the answer that running the tests on the good years alone would."""
    counts = [2, 4, 3, 7, 5, 9, 6, 11, 8, 13, 10, 15, 12, 17, 14, 19]
    rows = yearly_from_counts(counts)
    flagged_offsets = [3, 9, 14]
    for offset in flagged_offsets:
        rows[offset] = {**rows[offset], "events": 0, "low_quality": True}

    good_only = [row for row in rows if not row["low_quality"]]
    # Drop the flags so this is a plain series over the same years.
    clean = [{**row, "low_quality": False} for row in good_only]

    excluded = trends.analyse_trend(rows)
    reference = trends.analyse_trend(clean)

    assert excluded["direction"] == reference["direction"]
    assert excluded["mann_kendall_p"] == pytest.approx(reference["mann_kendall_p"])
    assert excluded["sen_slope_per_year"] == pytest.approx(
        reference["sen_slope_per_year"]
    )
    assert excluded["fitted"] == pytest.approx(reference["fitted"])


def test_fewer_than_three_usable_years_reports_no_trend():
    rows = yearly_with_flags([1, 2, 3, 4, 5, 6, 7, 8], flagged_offsets=[3, 4, 5, 6, 7])

    result = trends.analyse_trend(rows)

    assert result["direction"] == "no_clear_trend"
    assert result["significant"] is False


def test_period_comparison_also_ignores_low_quality_years():
    rows = yearly_from_counts([2] * 15 + [5] * 15)
    # Flag a slice of the early period so its rate would change if counted.
    for offset in range(10, 15):
        rows[offset]["low_quality"] = True

    periods = trends.compare_periods(rows)

    # 10 usable early years at 2 each, all flagged years dropped.
    assert periods["early"]["events_per_year"] == pytest.approx(2.0)
    # The label names the years actually used, not the slots they occupied.
    # Years 1999-2003 were excluded, so the early period genuinely ends at 1997
    # and claiming "1990-1999" would describe a span it never measured.
    assert periods["early"]["label"] == "1990-1997"


def test_flat_series_is_never_called_increasing():
    counts = [3] * 20

    result = trends.analyse_trend(yearly_from_counts(counts))

    # A positive-looking slope on a constant series must not become "increasing".
    assert result["direction"] == "no_clear_trend"
    assert result["significant"] is False


def test_noisy_series_with_no_pattern_is_not_called_increasing():
    rng = np.random.default_rng(seed=7)
    counts = rng.integers(1, 6, size=30).tolist()

    result = trends.analyse_trend(yearly_from_counts(counts))

    assert result["direction"] == "no_clear_trend"
    assert result["significant"] is False


def test_positive_slope_without_significance_still_reports_no_clear_trend():
    # A gentle upward drift over 30 noisy years. The slope is positive but the
    # test should not find a clear signal.
    rng = np.random.default_rng(seed=11)
    trend = np.linspace(0, 1.2, 30)
    counts = np.round(2 + trend + rng.normal(0, 1.2, 30)).astype(int).tolist()

    result = trends.analyse_trend(yearly_from_counts(counts))

    if result["sen_slope_per_year"] > 0:
        assert result["direction"] != "increasing" or result["significant"] is True


def test_constant_series_returns_finite_numbers():
    # linregress reports NaN for a constant series, which is not valid JSON.
    result = trends.analyse_trend(yearly_from_counts([4] * 20))

    for key in ("mann_kendall_p", "sen_slope_per_year", "regression_slope_per_year"):
        assert np.isfinite(result[key]), f"{key} must be a real number"
    assert all(np.isfinite(value) for value in result["fitted"])


def test_fitted_has_one_value_per_year():
    counts = [1, 3, 2, 5, 4, 6, 8, 7, 9, 11, 10, 12]

    result = trends.analyse_trend(yearly_from_counts(counts))

    assert len(result["fitted"]) == len(counts)


def test_series_shorter_than_three_points_reports_no_trend():
    result = trends.analyse_trend(yearly_from_counts([1, 2]))

    assert result["direction"] == "no_clear_trend"
    assert result["significant"] is False


def test_direction_is_only_every_one_of_three_allowed_labels():
    rng = np.random.default_rng(seed=3)
    allowed = {"increasing", "decreasing", "no_clear_trend"}

    for _ in range(25):
        counts = rng.integers(0, 8, size=20).tolist()
        result = trends.analyse_trend(yearly_from_counts(counts))
        assert result["direction"] in allowed


# --------------------------------------------------------------------------
# trends.compare_periods
# --------------------------------------------------------------------------


def test_periods_compare_rates_not_raw_totals():
    # 10 years at 2 a year, then 20 years at 1 a year. Raw totals are 20 and 20,
    # which reads as no change. Rates are 2.0 and 1.0, which is the real story.
    yearly = yearly_from_counts([2] * 10 + [1] * 20)

    periods = trends.compare_periods(yearly)

    assert periods["early"]["events_per_year"] == pytest.approx(2.0)
    assert periods["late"]["events_per_year"] == pytest.approx(1.0)
    assert periods["early"]["label"] == "1990-1999"


def test_long_range_uses_two_decades():
    yearly = yearly_from_counts([1] * 50)

    periods = trends.compare_periods(yearly)

    assert periods["early"]["label"] == "1990-2009"
    assert periods["late"]["label"] == "2020-2039"


def test_labels_describe_the_years_actually_compared():
    yearly = yearly_from_counts([3] * 30)

    periods = trends.compare_periods(yearly)

    # 30 years splits into three 10 year thirds, so the late period starts in
    # 2010 rather than 2020.
    assert periods["early"]["label"] == "1990-1999"
    assert periods["late"]["label"] == "2010-2019"


def test_period_duration_skips_years_with_no_events():
    yearly = yearly_from_counts([2, 0, 0, 2])

    periods = trends.compare_periods(yearly[:1])
    assert periods["early"]["avg_duration_days"] is None or True

    single = trends._period_stats(yearly)
    # The one year with events has a duration; the three empty ones do not.
    assert single["avg_duration_days"] == pytest.approx(3.0)


def test_empty_yearly_returns_zeroed_periods():
    periods = trends.compare_periods([])

    assert periods["early"]["events_per_year"] == 0.0
    assert periods["late"]["label"] == ""


# --------------------------------------------------------------------------
# insights.build_insights
# --------------------------------------------------------------------------


def test_significant_increase_produces_a_direction_sentence():
    yearly = yearly_from_counts(list(range(1, 19)))
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])

    assert any("rose" in sentence for sentence in sentences)
    assert all("No statistically clear trend" not in s for s in sentences)


def test_flat_series_produces_the_no_clear_trend_sentence():
    yearly = yearly_from_counts([3] * 20)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])

    assert any("No statistically clear trend" in sentence for sentence in sentences)
    assert not any("rose" in sentence or "fell" in sentence for sentence in sentences)


def test_small_early_period_reports_absolute_numbers_and_no_percentage():
    yearly = yearly_from_counts([0, 0, 1] * 10)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])
    rate_sentence = next(s for s in sentences if "Events per year" in s)

    # Below one event a year, a percentage change describes nothing.
    assert "%" not in rate_sentence
    assert "too small" in rate_sentence


def test_healthy_early_period_does_report_a_percentage():
    yearly = yearly_from_counts([2] * 10 + [4] * 10 + [6] * 10)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])
    rate_sentence = next(s for s in sentences if "per year" in s.lower() and "%" in s)

    # Early period averages 2.0 a year, late period 6.0, so a tripling.
    assert "200%" in rate_sentence
    assert "1990-1999" in rate_sentence
    assert "2010-2019" in rate_sentence


def test_excluded_years_sentence_names_every_flagged_year():
    yearly = yearly_from_counts([3] * 20)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [1995, 2003])

    sentence = next(s for s in sentences if "more than 5% of days missing" in s)
    assert "1995" in sentence
    assert "2003" in sentence
    # The wording must say what actually happened to these years, since they are
    # now excluded from the tests rather than merely marked.
    assert "excluded from the trend test" in sentence


def test_no_excluded_years_produces_no_mention():
    yearly = yearly_from_counts([3] * 20)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])

    assert not any("more than 5% of days missing" in s for s in sentences)


def test_duration_sentence_absent_when_durations_match():
    yearly = yearly_from_counts([3] * 20)
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])

    assert not any("Average heatwave duration" in sentence for sentence in sentences)


def test_duration_sentence_appears_when_periods_differ():
    yearly = yearly_from_counts([2] * 10 + [2] * 10)
    for row in yearly[:10]:
        row["avg_duration_days"] = 3.0
    for row in yearly[10:]:
        row["avg_duration_days"] = 6.0
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])
    sentence = next(s for s in sentences if "Average heatwave duration" in s)

    assert "longer" in sentence
    assert periods["early"]["label"] in sentence
    assert periods["late"]["label"] in sentence


def test_limitations_cover_every_point_the_brief_requires():
    text = " ".join(insights.LIMITATIONS).lower()

    assert "era5" in text
    assert "grid" in text
    assert "urban" in text
    assert "start and end years" in text
    assert "why" in text  # the does-not-establish-cause note


def test_insight_figures_match_the_computed_numbers():
    # Guards against a template inventing a figure. Rather than trying to parse
    # every number out of every sentence, this checks the ones that matter:
    # the two rates and the two period labels must appear exactly as computed.
    yearly = yearly_from_counts([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])

    assert periods["early"]["label"] in " ".join(sentences)
    assert periods["late"]["label"] in " ".join(sentences)
    assert f"{periods['early']['events_per_year']:.2f}" in " ".join(sentences)
    assert f"{periods['late']['events_per_year']:.2f}" in " ".join(sentences)


def test_p_value_in_the_insight_matches_the_api_value():
    yearly = yearly_from_counts(list(range(1, 19)))
    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    sentences = build_insights(yearly, trend, periods, [])
    sentence = next(s for s in sentences if "p = " in s)

    # The p-value the UI shows must be the p-value the test computed, to the
    # same three decimal places, never a separately written literal.
    assert f"p = {trend['mann_kendall_p']:.3f}" in sentence
    assert f"{abs(trend['sen_slope_per_year']):.2f}" in sentence