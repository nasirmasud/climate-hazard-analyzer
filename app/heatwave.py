"""Heatwave detection.

A heatwave is three or more consecutive days where the daily maximum exceeds the
95th percentile *for that calendar day*, against a fixed 1990-2020 baseline.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Changing any of these changes the whole analysis, which is why they are named
# here rather than inlined at the call sites.
BASELINE_START = 1990
BASELINE_END = 2020
PERCENTILE = 95
WINDOW_DAYS = 7  # half-window, +/- days around each calendar day
MIN_RUN_DAYS = 3

# Above this share of missing days, a year is flagged low quality.
LOW_QUALITY_MISSING_FRACTION = 0.05

TMAX_COLUMN = "tmax"

# 366 so Feb 29 has a slot of its own.
DAYS_IN_YEAR = 366


@dataclass(frozen=True)
class HeatwaveEvent:
    """One detected heatwave."""

    start: pd.Timestamp
    end: pd.Timestamp
    duration_days: int
    mean_intensity_c: float
    peak_temp_c: float

    # A run crossing a year boundary is one event, counted in the year it starts.
    # Derived rather than stored, so the two can never disagree.
    @property
    def year(self) -> int:
        return int(self.start.year)


def day_of_year_index(dates: pd.DatetimeIndex) -> np.ndarray:
    """Map dates onto 1..366.

    Feb 29 keeps its own number, which is why this uses `dayofyear` rather than a
    month/day pair. It still sits inside the circular windows of both late
    February and early March, so surrounding days constrain it either way.
    """
    return dates.dayofyear.to_numpy()


def compute_thresholds(series: pd.DataFrame) -> np.ndarray:
    """One threshold per calendar day, from the baseline only.

    For each day-of-year d, take every baseline value within +/-7 days of d
    (wrapping the year boundary) and return the 95th percentile, giving 366
    thresholds. The wrap is the point of a *calendar day* threshold: the seasons
    join up.

    NaNs are ignored by np.nanpercentile. In a leap year the target day
    contributes 32 values rather than 31, a minor asymmetry.
    """
    baseline = series.loc[
        f"{BASELINE_START}-01-01" : f"{BASELINE_END}-12-31", TMAX_COLUMN
    ]
    if baseline.empty:
        raise ValueError("The series has no baseline years to compute thresholds from.")

    values = baseline.to_numpy(dtype=float)
    day_numbers = day_of_year_index(baseline.index)

    thresholds = np.full(DAYS_IN_YEAR, np.nan, dtype=float)

    for day in range(1, DAYS_IN_YEAR + 1):
        offsets = np.arange(-WINDOW_DAYS, WINDOW_DAYS + 1)
        window_days = ((day - 1 + offsets) % DAYS_IN_YEAR) + 1

        in_window = np.isin(day_numbers, window_days)
        window_values = values[in_window]
        window_values = window_values[~np.isnan(window_values)]

        if window_values.size:
            thresholds[day - 1] = np.nanpercentile(window_values, PERCENTILE)

    return thresholds


def threshold_for_dates(dates: pd.DatetimeIndex, thresholds: np.ndarray) -> np.ndarray:
    """Join the calendar-day threshold onto each date in the requested range."""
    return thresholds[day_of_year_index(dates) - 1]


def detect_events(
    series: pd.DataFrame,
    thresholds: np.ndarray,
    min_run_days: int = MIN_RUN_DAYS,
) -> list[HeatwaveEvent]:
    """Turn a range-sliced series into a list of events.

    `series` is expected to already be sliced to the user's requested years.
    """
    dates = series.index
    if len(dates) == 0:
        return []

    tmax = series[TMAX_COLUMN].to_numpy(dtype=float)
    limits = threshold_for_dates(dates, thresholds)

    # NaN never counts as above threshold, because comparing NaN is always False.
    # An unknown day cannot establish a heatwave.
    above = tmax > limits

    # Any change in the value starts a new group, and cumsum gives each group a
    # stable id. Grouping the frame by that id splits it into the runs.
    above_series = pd.Series(above, index=dates)
    run_id = (above_series != above_series.shift()).cumsum()

    events: list[HeatwaveEvent] = []

    # Filtering to the above-threshold days matters: grouping the whole frame
    # would hand back the cool stretches too, turning a long spell into one huge
    # fake event.
    above_days = series.loc[above_series]

    for _, group in above_days.groupby(run_id.loc[above_series]):
        if len(group) < min_run_days:
            continue

        event_tmax = group[TMAX_COLUMN].to_numpy(dtype=float)
        event_limits = threshold_for_dates(group.index, thresholds)

        # A NaN day is False, so it already breaks a run: events are never
        # extended across a long missing gap. No extra check is needed here.
        intensity = event_tmax - event_limits

        events.append(
            HeatwaveEvent(
                start=group.index[0],
                end=group.index[-1],
                duration_days=int(len(group)),
                mean_intensity_c=float(np.mean(intensity)),
                peak_temp_c=float(np.nanmax(event_tmax)),
            )
        )

    return events


def slice_range(series: pd.DataFrame, start_year: int, end_year: int) -> pd.DataFrame:
    """Restrict the series to the user's requested years, inclusive."""
    return series.loc[f"{start_year}-01-01" : f"{end_year}-12-31"]


def low_quality_years(series: pd.DataFrame) -> list[int]:
    """Years with more than 5 percent of days still missing."""
    flagged = []
    for year, group in series.groupby(series.index.year):
        if group.empty:
            continue
        if group[TMAX_COLUMN].isna().mean() > LOW_QUALITY_MISSING_FRACTION:
            flagged.append(int(year))
    return flagged


def mean_max_temp_on_event_days(
    series: pd.DataFrame, events: list[HeatwaveEvent]
) -> float | None:
    """Mean daily maximum across every day covered by any of these events.

    Returns None when there are no events, because a mean over no days is
    unknown rather than zero. Shared by the yearly rows and the summary card, so
    a long event contributes one value per day in both, not one value per event.
    """
    if not events:
        return None

    days: list[pd.Timestamp] = []
    for event in events:
        days.extend(pd.date_range(event.start, event.end, freq="D"))

    day_values = series[TMAX_COLUMN].reindex(days).dropna()
    if day_values.empty:
        return None
    return float(day_values.mean())


def yearly_aggregates(
    series: pd.DataFrame,
    events: list[HeatwaveEvent],
) -> list[dict]:
    """One row per year in the range.

    Years with no events still appear, with null averages, so the chart and table
    have a row for every year asked about. A gap would read as missing data
    rather than as "nothing happened".
    """
    bad_years = set(low_quality_years(series))

    rows: list[dict] = []
    for year in sorted(set(series.index.year)):
        year_events = [event for event in events if event.year == year]

        # Heatwave days for the year: the days each event covers. A run is always
        # contiguous, so the inclusive date range is the day count. Membership
        # comes from the event's start year, so an event straddling New Year
        # contributes all of its days to the earlier row.
        heatwave_days = sum(e.duration_days for e in year_events)

        rows.append(
            {
                "year": int(year),
                "events": len(year_events),
                "heatwave_days": heatwave_days,
                # Null when there are no events: a mean over nothing is not zero.
                "avg_duration_days": (
                    float(np.mean([e.duration_days for e in year_events]))
                    if year_events
                    else None
                ),
                "avg_intensity_c": (
                    float(np.mean([e.mean_intensity_c for e in year_events]))
                    if year_events
                    else None
                ),
                # Mean of daily maxima across the event days, not the mean of the
                # per-event peaks: a long event contributes several days and a
                # short one contributes few.
                "avg_max_temp_during_events_c": mean_max_temp_on_event_days(
                    series, year_events
                ),
                "low_quality": int(year) in bad_years,
            }
        )

    return rows