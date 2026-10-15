"""Tests for heatwave.py: the detection rules.

Every case is synthetic so the expected answer is obvious by inspection. The
thresholds are injected directly where the test is about run detection, and built
from real data only where the calendar-day logic is what is under test.
"""

import numpy as np
import pandas as pd
import pytest

from app import heatwave
from app.heatwave import (
    HeatwaveEvent,
    compute_thresholds,
    detect_events,
    yearly_aggregates,
)

FLAT_THRESHOLD = 30.0


def make_series(values_by_date: dict[str, float | None]) -> pd.DataFrame:
    """Build a tmax frame, reindexed so absent dates become explicit NaN rows."""
    index = pd.to_datetime(sorted(values_by_date))
    frame = pd.DataFrame(
        {"tmax": [values_by_date[d.strftime("%Y-%m-%d")] for d in index]}, index=index
    )
    full = pd.date_range(index.min(), index.max(), freq="D")
    return frame.reindex(full)


def constant_thresholds(value: float = FLAT_THRESHOLD) -> np.ndarray:
    """A threshold series with no seasonal shape, for isolating run logic."""
    return np.full(heatwave.DAYS_IN_YEAR, value, dtype=float)


def test_two_day_streak_is_not_a_heatwave():
    series = make_series(
        {"2020-05-01": 25, "2020-05-02": 35, "2020-05-03": 36, "2020-05-04": 25}
    )

    events = detect_events(series, constant_thresholds())

    assert events == []


def test_three_day_streak_is_one_event():
    series = make_series(
        {
            "2020-05-01": 25,
            "2020-05-02": 35,
            "2020-05-03": 36,
            "2020-05-04": 37,
            "2020-05-05": 25,
        }
    )

    events = detect_events(series, constant_thresholds())

    assert len(events) == 1
    event = events[0]
    assert event.start == pd.Timestamp("2020-05-02")
    assert event.end == pd.Timestamp("2020-05-04")
    assert event.duration_days == 3
    assert event.peak_temp_c == pytest.approx(37.0)
    # Intensity is tmax minus threshold, averaged over the event days.
    assert event.mean_intensity_c == pytest.approx((5 + 6 + 7) / 3)


def test_two_streaks_split_by_one_cool_day_are_two_events():
    series = make_series(
        {
            "2020-05-01": 25,
            "2020-05-02": 35,
            "2020-05-03": 36,
            "2020-05-04": 37,
            "2020-05-05": 25,
            "2020-05-06": 35,
            "2020-05-07": 36,
            "2020-05-08": 37,
            "2020-05-09": 25,
        }
    )

    events = detect_events(series, constant_thresholds())

    assert len(events) == 2
    assert events[0].start == pd.Timestamp("2020-05-02")
    assert events[1].start == pd.Timestamp("2020-05-06")
    assert all(event.duration_days == 3 for event in events)


def test_nan_day_never_counts_as_above_threshold():
    series = make_series(
        {
            "2020-05-01": 25,
            "2020-05-02": 35,
            "2020-05-03": None,
            "2020-05-04": 36,
            "2020-05-05": 37,
            "2020-05-06": 25,
        }
    )

    events = detect_events(series, constant_thresholds())

    # The unknown day breaks the streak, leaving two two-day runs, so nothing
    # qualifies. An unknown day must not be able to manufacture an event.
    assert events == []


def test_long_nan_gap_does_not_extend_an_event():
    # Three known hot days, a 10 day hole, then three more. The hole is not
    # above threshold, so the run cannot continue through it.
    values = {"2020-05-01": 25, "2020-05-02": 35, "2020-05-03": 36, "2020-05-04": 37}
    for offset in range(5, 15):
        values[f"2020-05-{offset:02d}"] = None
    values.update({"2020-05-15": 35, "2020-05-16": 36, "2020-05-17": 37, "2020-05-18": 25})
    series = make_series(values)

    events = detect_events(series, constant_thresholds())

    assert len(events) == 2
    assert events[0].start == pd.Timestamp("2020-05-02")
    assert events[1].start == pd.Timestamp("2020-05-15")


def test_year_boundary_streak_is_one_event_counted_in_its_start_year():
    series = make_series(
        {
            "2020-12-30": 35,
            "2020-12-31": 36,
            "2021-01-01": 37,
            "2021-01-02": 25,
        }
    )

    events = detect_events(series, constant_thresholds())

    # One event, not two two-day fragments, and it belongs to the earlier year.
    assert len(events) == 1
    assert events[0].duration_days == 3
    assert events[0].year == 2020


def test_thresholds_are_computed_for_every_calendar_day():
    index = pd.date_range("1990-01-01", "2020-12-31", freq="D")
    values = 25.0 + 10.0 * np.sin(np.arange(len(index)) / 365.25 * 2 * np.pi)
    series = pd.DataFrame({"tmax": values}, index=index)

    thresholds = compute_thresholds(series)

    assert len(thresholds) == heatwave.DAYS_IN_YEAR
    assert not np.isnan(thresholds).any()


def test_threshold_window_wraps_the_year_boundary():
    # Only the first and last two weeks of the year have values. Mid-year is
    # entirely missing, so any mid-year threshold must be NaN while the new year
    # days still get a threshold from data on the other side of the boundary.
    index = pd.to_datetime(
        ["1990-01-01", "1990-01-08", "1990-12-24", "1990-12-31"]
    )
    series = pd.DataFrame({"tmax": [10.0, 20.0, 5.0, 15.0]}, index=index)

    thresholds = compute_thresholds(series)

    # Day 1 (Jan 1) sees Dec 24-31 and Jan 1-8 together.
    assert not np.isnan(thresholds[0])
    # Day 365 (Dec 31) sees Dec 24-31, Jan 1 and Jan 8 across the wrap.
    assert not np.isnan(thresholds[364])
    # Mid-July has no data anywhere in its window.
    assert np.isnan(thresholds[181])


def test_thresholds_ignore_nan_values():
    index = pd.date_range("1990-01-01", "2020-12-31", freq="D")
    values = np.full(len(index), 30.0)
    values[::3] = np.nan  # a third of the baseline is missing
    series = pd.DataFrame({"tmax": values}, index=index)

    thresholds = compute_thresholds(series)

    # The observed values are all 30, so every threshold is 30. A NaN-poisoned
    # percentile would have produced NaN here instead.
    assert np.allclose(thresholds, 30.0)


def test_february_29_has_its_own_threshold_slot():
    index = pd.to_datetime(["2020-02-28", "2020-02-29", "2020-03-01"])
    series = pd.DataFrame({"tmax": [10.0, 50.0, 20.0]}, index=index)

    day_numbers = heatwave.day_of_year_index(series.index)

    # dayofyear gives Feb 29 its own number, which is what keeps it out of the
    # Feb 28 bucket.
    assert list(day_numbers) == [59, 60, 61]


def test_leap_day_is_within_its_own_circular_window():
    # Baseline around a leap year's Feb 29, with the surrounding days spread out
    # far enough that the window has to be circular to see them all.
    dates = [
        "1992-02-20",
        "1992-02-22",
        "1992-02-24",
        "1992-02-26",
        "1992-02-28",
        "1992-02-29",
        "1992-03-01",
        "1992-03-02",
        "1992-03-04",
        "1992-03-06",
    ]
    series = pd.DataFrame({"tmax": np.arange(10.0, 20.0)}, index=pd.to_datetime(dates))

    thresholds = compute_thresholds(series)

    # Day 60 (Feb 29 in a leap year) has data around it, so it gets a threshold.
    assert not np.isnan(thresholds[59])


def test_yearly_aggregates_sum_to_the_event_list():
    series = make_series(
        {
            "2019-05-01": 25,
            "2019-05-02": 35,
            "2019-05-03": 36,
            "2019-05-04": 37,
            "2019-05-05": 25,
            "2020-05-01": 25,
            "2020-05-02": 35,
            "2020-05-03": 36,
            "2020-05-04": 37,
            "2020-05-05": 35,
            "2020-05-06": 36,
            "2020-05-07": 37,
            "2020-05-08": 25,
        }
    )
    events = detect_events(series, constant_thresholds())

    rows = yearly_aggregates(series, events)

    assert [row["year"] for row in rows] == [2019, 2020]
    assert sum(row["events"] for row in rows) == len(events)
    assert rows[0]["events"] == 1
    assert rows[0]["heatwave_days"] == 3
    assert rows[1]["events"] == 1
    # 2020-05-02 through 05-07 is one unbroken six day run, not two events.
    assert rows[1]["heatwave_days"] == 6
    assert rows[1]["avg_duration_days"] == pytest.approx(6.0)
    assert rows[0]["avg_duration_days"] == pytest.approx(3.0)
    # Mean daily max across the three event days, not the mean of one peak.
    assert rows[0]["avg_max_temp_during_events_c"] == pytest.approx((35 + 36 + 37) / 3)


def test_year_with_no_events_has_a_row_and_null_averages():
    series = make_series({"2020-05-01": 20, "2020-05-02": 21, "2020-05-03": 22})

    rows = yearly_aggregates(series, [])

    assert len(rows) == 1
    assert rows[0]["events"] == 0
    assert rows[0]["heatwave_days"] == 0
    # Null, not zero: a mean over no events is unknown, not zero.
    assert rows[0]["avg_duration_days"] is None
    assert rows[0]["avg_intensity_c"] is None
    assert rows[0]["avg_max_temp_during_events_c"] is None


def test_year_boundary_event_is_counted_once_in_its_start_year():
    series = make_series(
        {
            "2020-12-30": 35,
            "2020-12-31": 36,
            "2021-01-01": 37,
            "2021-01-02": 25,
            "2021-06-01": 20,
        }
    )
    events = detect_events(series, constant_thresholds())

    rows = yearly_aggregates(series, events)

    by_year = {row["year"]: row for row in rows}
    assert by_year[2020]["events"] == 1
    assert by_year[2021]["events"] == 0
    assert sum(row["events"] for row in rows) == len(events)


def test_low_quality_year_is_flagged_over_five_percent_missing():
    index = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    values = np.full(len(index), 25.0)
    values[:40] = np.nan  # 11 percent missing
    series = pd.DataFrame({"tmax": values}, index=index)

    rows = yearly_aggregates(series, [])

    assert rows[0]["low_quality"] is True


def test_detect_events_on_an_empty_slice_returns_nothing():
    series = pd.DataFrame(
        {"tmax": []}, index=pd.DatetimeIndex([], name="date"), dtype=float
    )

    assert detect_events(series, constant_thresholds()) == []


def test_heatwave_event_year_comes_from_the_start_date():
    event = HeatwaveEvent(
        start=pd.Timestamp("2020-12-30"),
        end=pd.Timestamp("2021-01-01"),
        duration_days=3,
        mean_intensity_c=5.0,
        peak_temp_c=36.0,
    )

    assert event.year == 2020