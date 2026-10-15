import logging

import numpy as np
import pandas as pd

from . import heatwave, ingest, insights, trends
from .ingest import QualitySummary, UpstreamError

logger = logging.getLogger("climate")


def compute_summary(
    series: pd.DataFrame,
    events: list[heatwave.HeatwaveEvent],
    span_years: int,
) -> dict:
    """Summary fields, derived from the same event list as the yearly rows.

    One source for both is what makes the cards, chart and table reconcile.
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
        # Mean daily maximum across every event day, which differs from the mean
        # of per-event peaks. Same helper as the yearly rows, so the summary card
        # and the table cannot disagree.
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
    """Runs the pipeline downstream of the fetch, in the order it must happen.

    Omits region, params and threshold, which the route fills from the request.
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
            # selected years, so a reader sees the extent of everything missing.
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