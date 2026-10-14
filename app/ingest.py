"""Fetch and clean daily maximum temperature from Open-Meteo (ERA5).

One request per location covers the full supported range, 1990-01-01 to the end
of the last complete year. Retries happen on network errors and 5xx only, never
on 4xx, because a 4xx means the request itself is wrong and repeating it cannot
help.

Long gaps are never filled with zeros or means. Inventing a temperature would
let a fabricated day influence a heatwave event, which is worse than admitting
the day is unknown.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from .schemas import last_complete_year

logger = logging.getLogger("climate")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# ~13,000 daily values in one request, so 30s is generous.
REQUEST_TIMEOUT_SECONDS = 30.0

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.5

# Outside this range cannot be a real daily maximum anywhere on Earth, so it is
# a data error rather than a heatwave.
MAX_PLAUSIBLE_C = 60.0
MIN_PLAUSIBLE_C = -60.0

# Gaps this short are interpolated; longer ones are not.
MAX_INTERPOLATED_GAP_DAYS = 2

# A year with more than this share of days still missing is flagged low quality.
LOW_QUALITY_MISSING_FRACTION = 0.05

TMAX_COLUMN = "tmax"
INTERPOLATED_COLUMN = "interpolated"

# Only this one daily variable is ever requested.
DAILY_VARIABLE = "temperature_2m_max"


class UpstreamError(RuntimeError):
    """Open-Meteo could not be reached, or kept failing, after all retries.

    The route turns this into a 502 so the frontend can tell "the weather
    service is down" apart from "your request was invalid".
    """


@dataclass(frozen=True)
class QualitySummary:
    """How much of the series is real and how much is not."""

    total_days: int
    missing_days: int
    interpolated_days: int
    low_quality_years: list[int]


def fetch_period() -> tuple[str, str]:
    """The full supported range: 1990-01-01 to the end of the last complete year.

    Fetching the whole range once per location is deliberate. The user's year
    selection is applied later, from this same series, which is what lets a new
    year range be served without calling Open-Meteo again.
    """
    start_year = 1990
    end_year = last_complete_year()
    return f"{start_year}-01-01", f"{end_year}-12-31"


def build_query(lat: float, lon: float) -> dict[str, str]:
    """Build the Open-Meteo query for one location.

    Coordinates arrive already validated and rounded to two decimals, so they are
    safe to interpolate as floats. Units are left at the Open-Meteo default, C.
    """
    start_date, end_date = fetch_period()
    return {
        "latitude": f"{lat}",
        "longitude": f"{lon}",
        "start_date": start_date,
        "end_date": end_date,
        "daily": DAILY_VARIABLE,
        # Pins timestamps to the location's own local day rather than UTC, so a
        # day means the same thing to the user.
        "timezone": "auto",
    }


def _is_retryable(exc: Exception) -> bool:
    """Network errors and 5xx are worth retrying. A 4xx never is.

    A 400 means the parameters are wrong and a 429 means we are being rate
    limited; retrying either immediately just wastes the attempt.
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


async def _get_with_retries(client: httpx.AsyncClient, params: dict[str, str]) -> dict:
    """GET the archive API, retrying with exponential backoff.

    Returns the decoded JSON body, or raises UpstreamError.
    """
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(ARCHIVE_URL, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if not _is_retryable(exc):
                logger.warning("Open-Meteo rejected the request: %s", exc)
                raise UpstreamError(
                    "The weather data service rejected this request. "
                    "Please check the location and try again."
                ) from exc

            last_error = exc
            if attempt < MAX_ATTEMPTS:
                delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Open-Meteo attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    logger.error("Open-Meteo failed after %d attempts: %s", MAX_ATTEMPTS, last_error)
    raise UpstreamError(
        "The weather data service did not respond. Please try again in a moment."
    ) from last_error


def parse_daily(payload: dict) -> pd.DataFrame:
    """Parse `daily.time` and `daily.temperature_2m_max` into one column.

    Raises UpstreamError if the response does not look like the daily payload we
    asked for, rather than failing later with a confusing KeyError.
    """
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise UpstreamError("The weather data service returned an unexpected response.")

    times = daily.get("time")
    values = daily.get(DAILY_VARIABLE)
    if not times or values is None or len(times) != len(values):
        raise UpstreamError("The weather data service returned an unexpected response.")

    # pandas 2.x wants an explicit format for the plain YYYY-MM-DD strings
    # Open-Meteo returns; without it the index comes back as object dtype.
    index = pd.to_datetime(pd.Index(times), format="%Y-%m-%d")
    frame = pd.DataFrame({TMAX_COLUMN: pd.to_numeric(values)}, index=index)
    frame.index.name = "date"
    return frame


def clean_series(raw: pd.DataFrame) -> pd.DataFrame:
    """Clean the raw frame, in a fixed order.

    Returns a frame with two columns: `tmax` and `interpolated` (whether that
    day's value was filled in rather than observed).
    """
    frame = raw.copy()

    # 1. Nulls to NaN. to_numeric already did this; the pass also catches ""
    #    and "null", and keeps the step visible.
    frame[TMAX_COLUMN] = pd.to_numeric(frame[TMAX_COLUMN], errors="coerce")

    # 2. Physically impossible values are data errors, not extreme heat.
    #    Done before reindexing so an outlier cannot be interpolated around.
    impossible = frame[TMAX_COLUMN].notna() & (
        (frame[TMAX_COLUMN] > MAX_PLAUSIBLE_C) | (frame[TMAX_COLUMN] < MIN_PLAUSIBLE_C)
    )
    outlier_count = int(impossible.sum())
    if outlier_count:
        logger.warning("Flagged %d physically impossible value(s) as missing", outlier_count)
    frame.loc[impossible, TMAX_COLUMN] = np.nan

    # 3. Reindex to a complete daily range, so days absent from the response
    #    become explicit NaN rows rather than silently shortening the series.
    full_index = pd.date_range(frame.index.min(), frame.index.max(), freq="D")
    frame = frame.reindex(full_index)
    frame.index.name = "date"

    # 4. Linear interpolation, but only across short gaps.
    #
    #    pandas' `limit` cannot express this rule: it fills the first N NaNs of
    #    *every* gap, so a 7 day hole with limit=2 would gain two invented days
    #    and still look interpolated. A gap is instead kept or dropped whole, by
    #    measuring each NaN block's run length.
    missing = frame[TMAX_COLUMN].isna()

    # A new block starts wherever the missing/not-missing pattern changes.
    # shift(fill_value=False) makes the very first row start a new block.
    block_id = (missing != missing.shift(fill_value=False)).cumsum()
    block_length = missing.groupby(block_id).transform("sum")

    short_gap = missing & (block_length <= MAX_INTERPOLATED_GAP_DAYS)

    # Interpolate the interior, then restore every NaN in a long gap. limit_area
    # refuses to extrapolate past the first and last real values, so no day
    # outside the data is invented.
    interpolated_all = frame[TMAX_COLUMN].interpolate(
        method="linear",
        limit_area="inside",
    )
    keep_missing = missing & ~short_gap
    frame[TMAX_COLUMN] = interpolated_all.mask(keep_missing)

    # 5. Anything still NaN stays NaN, never zero or a mean.
    frame[INTERPOLATED_COLUMN] = missing & ~keep_missing

    return frame


def summarize_quality(frame: pd.DataFrame) -> QualitySummary:
    """How much of the series is real.

    `missing_days` counts days *still* missing after cleaning, so interpolated
    days are not double-counted as missing.
    """
    total_days = int(len(frame))
    interpolated_days = int(frame[INTERPOLATED_COLUMN].sum())
    missing_days = total_days - int(frame[TMAX_COLUMN].notna().sum())

    low_quality_years = []
    for year, group in frame.groupby(frame.index.year):
        if len(group) == 0:
            continue
        still_missing_fraction = group[TMAX_COLUMN].isna().sum() / len(group)
        if still_missing_fraction > LOW_QUALITY_MISSING_FRACTION:
            low_quality_years.append(int(year))

    return QualitySummary(
        total_days=total_days,
        missing_days=missing_days,
        interpolated_days=interpolated_days,
        low_quality_years=low_quality_years,
    )


async def fetch_daily_temps(lat: float, lon: float) -> tuple[pd.DataFrame, QualitySummary]:
    """The full ingest path: one upstream call, then parse, clean and summarise.

    Returns the cleaned series and its quality summary. The caller must run this
    off the event loop, because the pandas work is CPU-bound.
    """
    params = build_query(lat, lon)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        payload = await _get_with_retries(client, params)

    frame = clean_series(parse_daily(payload))
    quality = summarize_quality(frame)

    logger.info(
        "Ingested %s/%s: %d days, %d missing, %d interpolated, %d low-quality years",
        lat,
        lon,
        quality.total_days,
        quality.missing_days,
        quality.interpolated_days,
        len(quality.low_quality_years),
    )
    return frame, quality