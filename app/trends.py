"""Trend analysis on yearly heatwave counts.

**Mann-Kendall** decides the headline. It is rank-based, so it is robust to the
spikes and dips event counts always have, and it answers "is there a consistent
direction?" with a p-value. Sen's slope, its measure of size, is the median of
every pairwise slope, so it ignores outliers too.

**Linear regression** only draws the trend line. Least squares is pulled around
by a single extreme year, so it does not decide the label.

The Mann-Kendall p-value alone decides the direction, at p < 0.05. A series can
have a positive slope and no significant trend, and saying "increasing" then
would be overstating what 30 years of noisy counts can support.
"""

import logging

import numpy as np
import pymannkendall
from scipy.stats import linregress

logger = logging.getLogger("climate")

SIGNIFICANCE_LEVEL = 0.05

DIRECTION_INCREASING = "increasing"
DIRECTION_DECREASING = "decreasing"
DIRECTION_NONE = "no_clear_trend"


def _finite(value: float, fallback: float = 0.0) -> float:
    """Replace NaN and infinity with a usable number.

    A constant series has no variance, so linregress returns NaN, and JSON has no
    NaN literal either. Substituting zero keeps the "nothing measurable" case
    honest and parseable.
    """
    return float(value) if np.isfinite(value) else fallback


def analyse_trend(yearly: list[dict]) -> dict:
    """Run both tests on the yearly event counts, excluding low-quality years.

    A year with over 5 percent of its days missing has a count built on an
    unknown amount of data, and a count that is low only because data is missing
    is not evidence that fewer heatwaves happened. Including such years would
    drag both tests toward a spurious "no clear trend" and could reverse the
    direction.

    The exclusion is confined to the tests and the fitted line. The `yearly` rows
    and the `summary` totals are untouched, so the table still shows every year
    the user asked about, flagged.

    **Invariant:** `fitted` has one value per *included* year, in the same order
    as `yearly` with the flagged rows removed, so it is shorter whenever a year
    was flagged. Callers must join on the `low_quality` flag, not on position.
    """
    included = [row for row in yearly if not row.get("low_quality")]
    excluded = [row["year"] for row in yearly if row.get("low_quality")]

    if excluded:
        logger.info(
            "Excluding %d low-quality year(s) from trend tests: %s",
            len(excluded),
            ", ".join(str(year) for year in excluded),
        )

    years = [row["year"] for row in included]
    counts = [row["events"] for row in included]

    if len(counts) < 3:
        # Neither test means anything on fewer than three points. The API already
        # enforces a ten year minimum, so this only guards direct callers — but
        # excluding low-quality years can legitimately drop a short series below
        # three, which is a real possibility rather than a theoretical one.
        logger.warning(
            "Trend requested for only %d usable year(s) after excluding %d "
            "low-quality year(s); reporting no trend",
            len(counts),
            len(excluded),
        )
        return {
            "direction": DIRECTION_NONE,
            "significant": False,
            "mann_kendall_p": 1.0,
            "sen_slope_per_year": 0.0,
            "regression_slope_per_year": 0.0,
            "fitted": [float(count) for count in counts],
        }

    mk = pymannkendall.original_test(counts, alpha=SIGNIFICANCE_LEVEL)

    p_value = _finite(mk.p, fallback=1.0)
    significant = p_value < SIGNIFICANCE_LEVEL

    # The direction follows significance, never the raw slope. A trend label is
    # only shown when the test supports it.
    if significant and mk.trend == "increasing":
        direction = DIRECTION_INCREASING
    elif significant and mk.trend == "decreasing":
        direction = DIRECTION_DECREASING
    else:
        direction = DIRECTION_NONE

    regression = linregress(years, counts)

    return {
        "direction": direction,
        "significant": significant,
        "mann_kendall_p": p_value,
        "sen_slope_per_year": _finite(mk.slope),
        "regression_slope_per_year": _finite(regression.slope),
        # linregress does not return the fitted values, so they are rebuilt
        # here: y = intercept + slope * year.
        "fitted": [
            _finite(regression.intercept + regression.slope * year)
            for year in years
        ],
    }


def compare_periods(yearly: list[dict]) -> dict:
    """Compare two periods by events per year, never by raw totals.

    Raw totals mislead whenever the lengths differ: ten years at 2 events a year
    totals 20, and twenty years at 1 a year totals 20 too, but they are opposite
    stories.

    The periods are the first and last third of the range, or the first and last
    two decades when the range is long enough to have them. Either way the labels
    go back in the response so the UI can name them rather than assume.
    """
    if not yearly:
        return {
            "early": {"label": "", "events_per_year": 0.0, "avg_duration_days": None},
            "late": {"label": "", "events_per_year": 0.0, "avg_duration_days": None},
        }

    # Excluded here too, for consistency with the trend tests: a chart line built
    # from good years beside a period comparison that counted the bad ones would
    # make one response disagree with itself.
    usable = [row for row in yearly if not row.get("low_quality")]
    source = usable or yearly

    span = len(source)

    if span >= 40:
        early_years = 20
        late_years = 20
    else:
        third = max(span // 3, 1)
        early_years = third
        late_years = third

    early = source[:early_years]
    late = source[-late_years:]

    return {
        "early": _period_stats(early),
        "late": _period_stats(late),
    }


def _period_stats(rows: list[dict]) -> dict:
    """One period's numbers, labelled with the years it actually covers."""
    if not rows:
        return {"label": "", "events_per_year": 0.0, "avg_duration_days": None}

    total_events = sum(row["events"] for row in rows)
    events_per_year = total_events / len(rows)

    # Averaged only over years that actually had events, so one empty year does
    # not drag the figure towards zero.
    durations = [
        row["avg_duration_days"]
        for row in rows
        if row["avg_duration_days"] is not None
    ]
    avg_duration = float(np.mean(durations)) if durations else None

    label = f"{rows[0]['year']}-{rows[-1]['year']}"

    return {
        "label": label,
        "events_per_year": events_per_year,
        "avg_duration_days": avg_duration,
    }