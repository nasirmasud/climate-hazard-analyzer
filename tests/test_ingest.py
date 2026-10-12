"""Tests for ingest.py: the cleaning rules from section 6 of the brief.

Synthetic daily payloads rather than the live network, except for the retry
tests, which use respx to intercept httpx transport.
"""

import httpx
import pandas as pd
import pytest
import respx

from app import ingest
from app.ingest import ARCHIVE_URL, UpstreamError, build_query, fetch_period


def payload_for(days, values):
    """Build an Open-Meteo-shaped daily payload from parallel lists."""
    assert len(days) == len(values), "time and value arrays must be the same length"
    return {
        "daily": {
            "time": list(days),
            ingest.DAILY_VARIABLE: list(values),
        }
    }


def clean(days, values):
    """Run the raw payload through parse and clean, as the pipeline does."""
    return ingest.clean_series(ingest.parse_daily(payload_for(days, values)))


DAYS_10 = [f"2020-01-{day:02d}" for day in range(1, 11)]


def test_nulls_become_nan_and_short_gaps_are_filled():
    frame = clean(DAYS_10, [10, None, 12, 13, None, 15, 16, 17, 18, 19])

    # Each null is an isolated single-day gap, within the interpolation limit.
    assert frame.loc["2020-01-02", "tmax"] == pytest.approx(11.0)
    assert frame.loc["2020-01-05", "tmax"] == pytest.approx(14.0)
    assert frame["tmax"].isna().sum() == 0


def test_gap_of_two_days_is_interpolated_and_marked():
    frame = clean(DAYS_10, [10, 11, None, None, 14, 15, 16, 17, 18, 19])

    assert frame.loc["2020-01-03", "tmax"] == pytest.approx(12.0)
    assert frame.loc["2020-01-04", "tmax"] == pytest.approx(13.0)
    assert bool(frame.loc["2020-01-03", "interpolated"]) is True
    assert bool(frame.loc["2020-01-01", "interpolated"]) is False


def test_gap_of_three_days_is_left_missing():
    frame = clean(DAYS_10, [10, 11, None, None, None, 15, 16, 17, 18, 19])

    # The brief says a long gap is never filled, even a three day one.
    assert frame["tmax"].isna().sum() == 3
    assert not frame.loc["2020-01-03":"2020-01-05", "interpolated"].any()


def test_absent_days_are_reindexed_as_explicit_rows():
    # The response silently skips 2020-01-05; reindexing must restore the row
    # rather than letting the series quietly shorten.
    days = [day for day in DAYS_10 if day != "2020-01-05"]
    values = [value for value in [10, 11, 12, 13, 14, 15, 16, 17, 18, 19] if value != 14]
    frame = clean(days, values)

    assert len(frame) == 10
    assert [d.strftime("%Y-%m-%d") for d in frame.index] == DAYS_10


def test_physically_impossible_values_are_treated_as_missing():
    frame = clean(DAYS_10, [10, 95.0, 12, 13, -80.0, 15, 16, 17, 18, 19])

    # 95 C and -80 C are data errors, not extreme heat. Once flagged they become
    # ordinary single-day gaps, so the neighbours interpolate across them.
    assert frame.loc["2020-01-02", "tmax"] == pytest.approx(11.0)
    assert frame.loc["2020-01-05", "tmax"] == pytest.approx(14.0)
    assert frame["tmax"].max() < 20
    assert frame["tmax"].min() > 0


def test_impossible_value_at_the_series_edge_is_not_extrapolated():
    frame = clean(DAYS_10, [10, 11, 12, 13, 14, 15, 16, 17, 18, 95.0])

    # The last day is an outlier with nothing beyond it, so there is nothing to
    # interpolate towards. It must stay missing rather than repeat 18 C.
    assert pd.isna(frame.loc["2020-01-10", "tmax"])


def test_values_just_inside_the_bounds_are_kept():
    frame = clean(DAYS_10, [59.9, 11, 12, 13, 14, 15, 16, 17, 18, -59.9])

    assert frame.loc["2020-01-01", "tmax"] == 59.9
    assert frame.loc["2020-01-10", "tmax"] == -59.9


def test_no_extrapolation_past_the_data():
    frame = clean(DAYS_10, [10, 11, 12, 13, 14, 15, 16, 17, 18, None])

    # A trailing NaN has nothing on one side, so interpolation cannot invent it.
    # It stays missing rather than extending the last real value.
    assert pd.isna(frame.loc["2020-01-10", "tmax"])


def test_quality_summary_separates_missing_from_interpolated():
    frame = clean(DAYS_10, [10, 11, None, None, None, None, None, None, None, 19])
    quality = ingest.summarize_quality(frame)

    assert quality.total_days == 10
    assert quality.missing_days == 7
    assert quality.interpolated_days == 0


def test_quality_counts_interpolated_days_as_present():
    frame = clean(DAYS_10, [10, 11, None, None, 14, 15, 16, 17, 18, 19])
    quality = ingest.summarize_quality(frame)

    assert quality.interpolated_days == 2
    assert quality.missing_days == 0


def year_with_missing_block(first_day: str, days_missing: int):
    """A full year of flat 20 C values with one long hole in it.

    The hole has to be longer than the two day interpolation limit, otherwise
    cleaning would fill it and the year would look complete.
    """
    index = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    values = [20.0] * len(index)
    start = index.get_loc(pd.Timestamp(first_day))
    for offset in range(days_missing):
        values[start + offset] = None
    return clean(index.strftime("%Y-%m-%d"), values)


def test_quality_flags_year_over_five_percent_missing():
    # 30 missing days out of 366 is 8.2 percent, above the 5 percent bar.
    quality = ingest.summarize_quality(year_with_missing_block("2020-06-01", 30))

    assert quality.missing_days == 30
    assert quality.low_quality_years == [2020]


def test_quality_ignores_year_under_five_percent_missing():
    # 10 missing days out of 366 is 2.7 percent, under the bar.
    quality = ingest.summarize_quality(year_with_missing_block("2020-06-01", 10))

    assert quality.missing_days == 10
    assert quality.low_quality_years == []


def test_query_matches_section_6_2():
    params = build_query(23.81, 90.41)

    assert params["latitude"] == "23.81"
    assert params["longitude"] == "90.41"
    assert params["daily"] == "temperature_2m_max"
    assert params["timezone"] == "auto"
    assert params["start_date"] == "1990-01-01"
    assert params["end_date"].endswith("-12-31")


def test_fetch_period_spans_to_the_last_complete_year():
    from datetime import date

    start, end = fetch_period()

    assert start == "1990-01-01"
    assert end == f"{date.today().year - 1}-12-31"


@respx.mock
async def test_retry_on_5xx_then_success():
    route = respx.get(ARCHIVE_URL)
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(500),
        httpx.Response(200, json=payload_for(DAYS_10, range(1, 11))),
    ]

    frame, _quality = await ingest.fetch_daily_temps(23.81, 90.41)

    assert len(frame) == 10
    assert route.call_count == 3


@respx.mock
async def test_no_retry_on_4xx():
    route = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(400, json={}))

    with pytest.raises(UpstreamError):
        await ingest.fetch_daily_temps(23.81, 90.41)

    # The parameters are wrong, so repeating the request cannot help.
    assert route.call_count == 1


@respx.mock
async def test_raises_after_exhausting_retries():
    route = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(502))

    with pytest.raises(UpstreamError):
        await ingest.fetch_daily_temps(23.81, 90.41)

    assert route.call_count == ingest.MAX_ATTEMPTS


@respx.mock
async def test_retry_on_network_error():
    route = respx.get(ARCHIVE_URL)
    route.side_effect = [
        httpx.ConnectError("no route to host"),
        httpx.Response(200, json=payload_for(DAYS_10, range(1, 11))),
    ]

    frame, _quality = await ingest.fetch_daily_temps(23.81, 90.41)

    assert len(frame) == 10
    assert route.call_count == 2


@respx.mock
async def test_malformed_payload_is_reported_clearly():
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json={"error": True}))

    with pytest.raises(UpstreamError):
        await ingest.fetch_daily_temps(23.81, 90.41)