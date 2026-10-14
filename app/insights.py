"""Plain-language insight sentences, generated from the numbers.

Every sentence is assembled from values the API already computed, so the cards,
chart, insights and table cannot tell different stories. Wording is deliberately
hedged: these are reanalysis grid cells, not measurements at a person's street,
so the text describes what the data shows without claiming to have found a cause.
"""

from . import trends

# Always shown, on every response, not only when a trend exists.
LIMITATIONS = [
    "Data comes from ERA5 reanalysis, a modelled gridded estimate rather than "
    "on-site measurement.",
    "Resolution is a grid cell of roughly 25 km or more, so local conditions "
    "inside a city can differ from these numbers.",
    "Urban heat island effects are not modelled separately.",
    "Results depend on the start and end years chosen. A different range can "
    "show a different trend.",
    "This shows what changed in the data. It does not establish why.",
]


def build_insights(
    yearly: list[dict],
    trend: dict,
    periods: dict,
    low_quality_years: list[int],
) -> list[str]:
    """Assemble the insight sentences for one analysis."""
    sentences: list[str] = []

    sentences.extend(_frequency_sentence(trend))
    sentences.extend(_duration_sentence(periods))
    sentences.extend(_excluded_years_sentence(low_quality_years))
    sentences.extend(_rate_sentence(periods))

    return sentences


def _frequency_sentence(trend: dict) -> list[str]:
    """The headline sentence, only when the test supports a direction.

    A non-significant series gets the "no clear trend" wording instead, which is
    the easiest way to avoid overstating this data.
    """
    direction = trend["direction"]
    p_value = trend["mann_kendall_p"]
    slope = trend["sen_slope_per_year"]

    if direction == trends.DIRECTION_NONE:
        return [
            "No statistically clear trend in heatwave event frequency for this "
            f"range (p = {p_value:.3f})."
        ]

    verb = "rose" if direction == trends.DIRECTION_INCREASING else "fell"

    return [
        f"Heatwave event frequency {verb} over this range, by about "
        f"{abs(slope):.2f} events per year (p = {p_value:.3f})."
    ]


def _duration_sentence(periods: dict) -> list[str]:
    """Compare average duration between the two periods, when both have one."""
    early = periods.get("early") or {}
    late = periods.get("late") or {}

    early_duration = early.get("avg_duration_days")
    late_duration = late.get("avg_duration_days")

    if early_duration is None or late_duration is None:
        return []

    if abs(late_duration - early_duration) < 0.05:
        return []

    direction = "longer" if late_duration > early_duration else "shorter"

    return [
        f"Average heatwave duration was {direction} in {late.get('label', '')} "
        f"than in {early.get('label', '')}: "
        f"{early_duration:.1f} days versus {late_duration:.1f} days."
    ]


def _excluded_years_sentence(low_quality_years: list[int]) -> list[str]:
    """Name the years with too much missing data, because they are still in."""
    if not low_quality_years:
        return []

    years = ", ".join(str(year) for year in low_quality_years)

    # These years are excluded from the trend tests, the fitted line and the
    # period comparison, not merely flagged, so softer wording would mislead a
    # reader deciding whether to trust the trend.
    return [
        f"{len(low_quality_years)} year(s) had more than 5% of days missing "
        f"({years}). They are excluded from the trend test and the period "
        "comparison, but their events are still counted in the totals above."
    ]


def _rate_sentence(periods: dict) -> list[str]:
    """Compare events per year between the two periods.

    0.1 to 0.2 events a year is a 100% increase that describes nothing, so below
    roughly one event a year the absolute numbers are reported instead and no
    percentage appears.
    """
    early = periods.get("early") or {}
    late = periods.get("late") or {}

    early_rate = early.get("events_per_year")
    late_rate = late.get("events_per_year")

    if early_rate is None or late_rate is None:
        return []

    early_label = early.get("label", "")
    late_label = late.get("label", "")

    if early_rate < 1.0:
        return [
            f"Events per year were {early_rate:.2f} in {early_label} and "
            f"{late_rate:.2f} in {late_label}. The earlier period is too small "
            "for a percentage change to be meaningful."
        ]

    change = ((late_rate - early_rate) / early_rate) * 100
    direction = "more" if change >= 0 else "fewer"

    return [
        f"{abs(change):.0f}% {direction} heatwave events per year, from "
        f"{early_rate:.2f} in {early_label} to {late_rate:.2f} in {late_label}."
    ]