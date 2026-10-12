"""Trend analysis on yearly heatwave counts.

Two independent tests are run on the same series, and they are used for
different jobs:

* **Mann-Kendall** (`pymannkendall.original_test`) decides the headline. It is
  a rank-based test: it compares every year against every other year and asks
  whether they mostly agree on the order, which makes it robust to the spikes
  and dips that event counts always have. It answers "is there a consistent
  direction?" and gives a p-value, plus Sen's slope as the size of the change.
  Sen's slope is the median of every pairwise slope, which again ignores
  outliers.
* **Linear regression** (`scipy.stats.linregress`) is only used to draw a trend
  line. Least squares is pulled around by a single extreme year, so it does
  not decide the label, only the line the chart shows.

The Mann-Kendall p-value alone decides the direction, at p < 0.05. A series can
have a positive slope and no significant trend, and in that case saying
"increasing" would be overstating what 30 years of noisy counts can support.
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

    A constant series has no variance, so linregress returns NaN for its
    p-value and correlation. JSON has no NaN literal either, so a NaN leaking
    into the response would produce a body no browser can parse. Substituting
    zero keeps the "nothing measurable" case honest.
    """
    return float(value) if np.isfinite(value) else fallback


def analyse_trend(yearly: list[dict]) -> dict:
    """Run both tests on the yearly event counts, excluding low-quality years.

    A year with more than 5 percent of its days still missing has an event count
    built on an unknown amount of data. A count that is too low because the data
    is missing is not evidence that fewer heatwaves happened, so including it
    would drag both tests toward a spurious "no clear trend" and could even
    reverse the direction. Section 8.4 allows these years to be excluded rather
    than merely marked, and that is the stricter, more honest reading.

    The exclusion is confined to the tests and the fitted line. The `yearly`
    rows themselves are untouched, so the table still shows every year the user
    asked about, flagged, and `summary` still counts every detected event. The
    user chose to see their whole range, not a silently shortened one.

    **Invariant for callers:** `fitted` has one value per *included* year, in
    the same order as `yearly` with the flagged rows removed. It is therefore
    shorter than `yearly` whenever any year was flagged. The frontend must join
    on the `low_quality` flag rather than assuming the two arrays line up
    index-for-index.
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
        # Neither test means anything on fewer than three points. Reporting
        # "no clear trend" is the honest answer, and the API already enforces a
        # ten year minimum, so this only guards direct callers. Excluding
        # low-quality years can legitimately drop a short series below three,
        # which is a real possibility rather than a theoretical one.
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
        # Least squares only draws the line; the label came from Mann-Kendall.
        "regression_slope_per_year": _finite(regression.slope),
        # The fitted line, recomputed from the returned slope and intercept.
        # linregress does not hand the fitted values back, and the brief wants
        # them, so they are rebuilt here: y = intercept + slope * year.
        "fitted": [
            _finite(regression.intercept + regression.slope * year)
            for year in years
        ],
    }


def compare_periods(yearly: list[dict]) -> dict:
    """Compare two periods by events per year, never by raw totals.

    Raw totals are misleading whenever the two periods are different lengths: ten
    years at 2 events a year totals 20, and twenty years at 1 a year totals 20
    too, but they are opposite stories. Dividing by the number of years in each
    period removes the length from the comparison entirely.

    The periods are the first and last third of the range, or the first and
    last two decades when the range is long enough to have them. Whichever is
    used, the labels go back in the response so the UI can name the periods
    rather than assuming one.
    """
    if not yearly:
        return {
            "early": {"label": "", "events_per_year": 0.0, "avg_duration_days": None},
            "late": {"label": "", "events_per_year": 0.0, "avg_duration_days": None},
        }

    # Low-quality years are excluded here too, for consistency with the trend
    # tests. Keeping them would make the two halves of the same response
    # disagree about which data is trustworthy: a chart line built from good
    # years beside a period comparison that silently counted the bad ones.
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