"""Heatwave detection.

This is the core of the evaluation (section 7 of the brief), so the constants
are named, at the top, and each rule below is commented with the reason it
exists rather than only what it does.

The definition: a heatwave is three or more consecutive days where the daily
maximum temperature is above the 95th percentile *for that calendar day*,
measured against a fixed 1990-2020 baseline.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Section 7.2 constants. Change these and the whole analysis changes with them,
# which is why they live here rather than being inlined at the call sites.
# --------------------------------------------------------------------------
BASELINE_START = 1990
BASELINE_END = 2020
PERCENTILE = 95
WINDOW_DAYS = 7  # half-window: +/- 7 days around each calendar day
MIN_RUN_DAYS = 3

# Section 7.7.4 / 7.7.6: a year with more than this share of days still missing
# is flagged low quality.
LOW_QUALITY_MISSING_FRACTION = 0.05

TMAX_COLUMN = "tmax"

# Days in the day-of-year scheme. 366 so that Feb 29 has a slot of its own.
DAYS_IN_YEAR = 366


@dataclass(frozen=True)
class HeatwaveEvent:
    """Section 7.6, one detected heatwave."""

    start: pd.Timestamp
    end: pd.Timestamp
    duration_days: int
    mean_intensity_c: float
    peak_temp_c: float

    # Section 7.5.5: a run crossing a year boundary is one event, counted in
    # the year it starts. The start year is derived, never stored separately,
    # so the two can never disagree.
    @property
    def year(self) -> int:
        return int(self.start.year)


def day_of_year_index(dates: pd.DatetimeIndex) -> np.ndarray:
    """Map dates onto 1..366, the scheme the brief's pseudo-code uses.

    Feb 29 keeps its own number (60 in a leap year), which is why this uses
    `dayofyear` rather than a month/day pair. It falls inside the circular
    window of both late February and early March, so it is still well
    constrained by surrounding days either way.
    """
    return dates.dayofyear.to_numpy()


def compute_thresholds(series: pd.DataFrame) -> np.ndarray:
    """Section 7.4: one threshold per calendar day, from the baseline only.

    For each day-of-year d, take every baseline value within +/-7 days of d
    (wrapping around the year boundary) and return the 95th percentile. This
    produces 366 thresholds.

    The window wraps so that 1 January is judged against late December, which
    is the point of a *calendar day* threshold: the seasons join up.

    NaNs are ignored by np.nanpercentile. In a leap year the target day
    contributes 32 values rather than 31, which is a minor asymmetry the brief
    explicitly accepts.
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
        # Circular window: a day near the start of the year also draws on days
        # near the end of it.
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
    """Section 7.5: turn the series into a list of events.

    `series` is expected to already be sliced to the user's requested years.
    """
    dates = series.index
    if len(dates) == 0:
        return []

    tmax = series[TMAX_COLUMN].to_numpy(dtype=float)
    limits = threshold_for_dates(dates, thresholds)

    # NaN never counts as above threshold. Comparing NaN is always False, which
    # is the behaviour we want: an unknown day cannot prove a heatwave, it can
    # only fail to disprove one.
    above = tmax > limits

    # Group consecutive True values into runs. Any change in the value starts a
    # new group, and cumsum gives each group a stable id. Grouping the frame by
    # that series splits it into the runs.
    above_series = pd.Series(above, index=dates)
    run_id = (above_series != above_series.shift()).cumsum()

    events: list[HeatwaveEvent] = []

    # Only the days that were above threshold can belong to a run. Grouping the
    # whole frame would also hand back the stretches that were *not* above,
    # which would turn a long cool or missing spell into one enormous fake event.
    above_days = series.loc[above_series]

    for _, group in above_days.groupby(run_id.loc[above_series]):
        if len(group) < min_run_days:
            continue

        event_tmax = group[TMAX_COLUMN].to_numpy(dtype=float)
        event_limits = threshold_for_dates(group.index, thresholds)

        # Section 7.5.6: a NaN day is False, so it already breaks a run and an
        # event is never extended across a long missing gap. No extra work is
        # needed for that, which is why it is not checked again here.
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
    """Section 7.7.6: years with more than 5 percent of days still missing."""
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
    unknown rather than zero.

    This is the same helper the yearly rows and the summary card both call, so
    a long event contributes one value per day everywhere rather than one value
    per event in some places and per day in others.
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
    """Section 7.7, one row per year in the range.

    Years with no events are still present, with null averages, so the chart and
    the table have a row for every year the user asked about. A gap in the
    table would read as missing data rather than as "nothing happened".
    """
    bad_years = set(low_quality_years(series))

    rows: list[dict] = []
    for year in sorted(set(series.index.year)):
        year_events = [event for event in events if event.year == year]

        # Heatwave days for the year: the days each event covers. A run is
        # always contiguous, so the inclusive date range is the day count.
        # Year membership comes from the event's start year, per 7.5.5, so an
        # event straddling New Year contributes its days only to the later row.
        heatwave_days = sum(e.duration_days for e in year_events)

        rows.append(
            {
                "year": int(year),
                "events": len(year_events),
                "heatwave_days": heatwave_days,
                # Null when there are no events, because a mean over nothing is
                # not zero, it is unknown.
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
                # The brief's wording is "average maximum temperature during
                # heatwave days", so this is the mean of the daily maxima across
                # the event days, not the mean of the per-event peaks. Those
                # differ, because a long event contributes several days and a
                # short one contributes few.
                "avg_max_temp_during_events_c": mean_max_temp_on_event_days(
                    series, year_events
                ),
                "low_quality": int(year) in bad_years,
            }
        )

    return rows