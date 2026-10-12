"""The analysis pipeline: raw daily temperatures in, one API response out.

This module owns the order of operations, which matters because each step
depends on the one before:

1. fetch and clean the full 1990-onward daily series for the location
2. compute the calendar-day thresholds from the fixed 1990-2020 baseline
3. slice the series down to the years the user asked about
4. detect events, then aggregate them per year
5. run the trend tests and the period comparison on those yearly counts
6. build the response

Step 2 runs on the *whole* series, not the sliced one, because the thresholds
must come from the fixed baseline regardless of which years the user selects.
If the thresholds followed the user's range, choosing a different start year
would move the threshold and quietly change the trend being measured.

The pandas work here is CPU-bound and synchronous. The route runs this in a
threadpool so a long analysis cannot block the event loop.
"""

import logging

import numpy as np
import pandas as pd

from . import heatwave, ingest, insights, trends
from .ingest import QualitySummary, UpstreamError

logger = logging.getLogger("climate")

# Section 8.2: the trend line the chart draws. Only the threshold maths above is
# project specific; this is just the shape of a heatwave.


def compute_summary(
    series: pd.DataFrame,
    events: list[heatwave.HeatwaveEvent],
    span_years: int,
) -> dict:
    """Section 9.3 summary fields, derived from the same event list as yearly.

    Deriving both from one source is what makes the cards, the chart and the
    table reconcile: there is no second place for a total to come from.
    """
    if not events:
        return {
            "total_events": 0,
            "avg_duration_days": None,
            "avg_intensity_c": None,
            "avg_max_temp_during_events_c": None,
            "events_per_year": 0.0,
        }

    return {
        "total_events": len(events),
        "avg_duration_days": float(
            np.mean([event.duration_days for event in events])
        ),
        "avg_intensity_c": float(
            np.mean([event.mean_intensity_c for event in events])
        ),
        # The mean daily maximum across every event day, which is a different
        # figure from the mean of the per-event peaks and is what section 7.7.5
        # asks for. Reuses the same helper the yearly rows use, so the summary
        # card and the table cannot disagree.
        "avg_max_temp_during_events_c": heatwave.mean_max_temp_on_event_days(
            series, events
        ),
        # Years, not events: a range twice as long is not twice the rate.
        "events_per_year": len(events) / span_years if span_years else 0.0,
    }


def run_analysis(
    series: pd.DataFrame,
    quality: QualitySummary,
    start_year: int,
    end_year: int,
) -> dict:
    """Everything downstream of the fetch, in the order it has to happen.

    Returns the response body minus the region, params and threshold blocks,
    which the route fills in from the request it already has.
    """
    thresholds = heatwave.compute_thresholds(series)

    selected = heatwave.slice_range(series, start_year, end_year)
    events = heatwave.detect_events(selected, thresholds)
    yearly = heatwave.yearly_aggregates(selected, events)

    trend = trends.analyse_trend(yearly)
    periods = trends.compare_periods(yearly)

    span_years = end_year - start_year + 1
    summary = compute_summary(selected, events, span_years)

    # Low quality years are recomputed from the selected range, so a year
    # outside the user's range never shows up in their data quality strip.
    selected_bad_years = heatwave.low_quality_years(selected)

    return {
        "summary": summary,
        "yearly": yearly,
        "events": [
            {
                "start": event.start.strftime("%Y-%m-%d"),
                "end": event.end.strftime("%Y-%m-%d"),
                "duration_days": event.duration_days,
                "mean_intensity_c": event.mean_intensity_c,
                "peak_temp_c": event.peak_temp_c,
            }
            for event in events
        ],
        "trend": trend,
        "period_comparison": periods,
        "data_quality": {
            # Quality is reported for the full fetched series, not just the
            # selected years, because that is what was actually retrieved and
            # a reader needs to know the extent of what was missing.
            "total_days": quality.total_days,
            "missing_days": quality.missing_days,
            "interpolated_days": quality.interpolated_days,
            "low_quality_years": selected_bad_years,
        },
        "insights": insights.build_insights(
            yearly, trend, periods, selected_bad_years
        ),
        "limitations": insights.LIMITATIONS,
    }


__all__ = ["run_analysis", "compute_summary", "UpstreamError"]