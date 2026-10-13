"""API endpoints.

`/api/analysis` is the only endpoint that does real work. It follows a three
tier read path, and the order is what makes the cache worth having:

1. `analyses` — has this exact request already been answered?
2. `daily_temps` — do we already hold this location's full daily series?
3. Open-Meteo — only when both of the above miss.

Two rules hold throughout:

* **A cache problem must never fail a request.** Every pymongo call goes
  through a wrapper that logs and returns None on failure, so an unreachable
  database degrades performance rather than availability.
* **The lock is held across the computation, not just the fetch.** That is what
  makes concurrent cold requests collapse into one upstream call *and* one
  computation, instead of merely one fetch.
"""

import csv
import io
import json
import logging

import pandas as pd
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import analysis, db, heatwave, ingest, places, ratelimit
from .ingest import QualitySummary
from .regions import REGIONS
from .schemas import (
    AnalysisRequest,
    AnalysisResponse,
    HealthOut,
    PlaceNameOut,
    PresetRegion,
)

logger = logging.getLogger("climate")

router = APIRouter()

# The message a 502 carries. Defined once so the cached path and the
# uncached path cannot word the same failure differently.
UPSTREAM_MESSAGE = (
    "The weather data service did not respond. Please try again in a moment."
)

RATE_LIMIT_MESSAGE = (
    "Too many requests. Please wait a moment before trying again."
)


def _rate_limited(retry_after: int) -> JSONResponse:
    """A 429 carrying the shared error shape and a Retry-After header.

    `Retry-After` is in seconds, per RFC 9110. Without it a well-behaved client
    has no idea how long to wait and will simply retry immediately, which is the
    opposite of what a rate limit is for.
    """
    return JSONResponse(
        status_code=429,
        content={"error": {"code": "rate_limited", "message": RATE_LIMIT_MESSAGE}},
        headers={"Retry-After": str(retry_after)},
    )


def _rate_limited_response(retry_after: int) -> Response:
    """Kept for callers typed on Response. The 429 body is built once above."""
    return _rate_limited(retry_after)


@router.get("/health", response_model=HealthOut)
async def health() -> Response:
    """Liveness check. Also used by Render and the frontend to detect a
    service that is still waking up from a free-tier cold start."""
    # No cache: a cached health check would report a stale "ok" for a service
    # that has since died, which is the opposite of what this endpoint is for.
    return JSONResponse(
        content={"status": "ok"},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/regions", response_model=list[PresetRegion])
async def list_regions() -> Response:
    """The preset regions the frontend offers in its dropdown."""
    return JSONResponse(
        content=[PresetRegion(id=r.id, name=r.name, lat=r.lat, lon=r.lon).model_dump() for r in REGIONS],
        headers={"Cache-Control": f"public, max-age={ratelimit.CACHE_CONTROL_MAX_AGE}"},
    )


@router.get("/place-name", response_model=PlaceNameOut)
async def get_place_name(request: Request, lat: float, lon: float) -> Response:
    """A short name for a coordinate, reverse geocoded via OpenStreetMap.

    Returns `{"name": null}` rather than an error for every failure: no result
    found, a timeout, a rate limit, or an unreachable service. The frontend has
    a perfectly good label to fall back to, and a lookup that fails must never
    interrupt the analysis the user actually asked for.

    The general budget applies. The Nominatim budget is charged inside the
    lookup, so it only applies to requests that genuinely reach the network.
    """
    general = ratelimit.spend_general(ratelimit.client_key(request))
    if not general.allowed:
        return _rate_limited(general.retry_after)

    name = await places.lookup_place_name(lat, lon, ratelimit.client_key(request))

    return JSONResponse(
        content=PlaceNameOut(name=name).model_dump(),
        # A short TTL: a name is stable, but the frontend would rather ask again
        # in five minutes than show a stale one for a day.
        headers={"Cache-Control": f"public, max-age={ratelimit.CACHE_CONTROL_MAX_AGE}"},
    )


# --------------------------------------------------------------------------
# Helpers. Each one isolates a failure mode so the endpoint below reads as the
# happy path it actually is.
# --------------------------------------------------------------------------


async def _safe_cache_call(func, *args):
    """Run a cache read, returning None if the database is unusable.

    `db` already swallows PyMongoError inside its own helpers, but a
    driver-level failure (unreachable host, pool timeout, DNS) can surface
    before that. Either way the answer is the same: behave as if it were a
    cache miss.
    """
    try:
        return await run_in_threadpool(func, *args)
    except Exception as exc:
        logger.warning("Cache read failed, treating as a miss: %s", exc)
        return None


async def _safe_cache_write(func, *args) -> None:
    """Run a cache write, swallowing anything it throws.

    A failed cache write must not fail the request the user actually asked for.
    They get their answer; the next request simply recomputes.
    """
    try:
        await run_in_threadpool(func, *args)
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)


async def _get_series(
    lat: float, lon: float, request
) -> tuple[pd.DataFrame | None, QualitySummary | None, JSONResponse | None]:
    """Tiers 2 and 3: the location's series, from cache or from Open-Meteo.

    Returns (series, quality, error_response). On failure or refusal it returns
    (None, None, response) so the caller has a single place to decide what a
    problem means for the user.

    The upstream rate limit is charged here, at the point of the fetch, rather
    than at the edge of the route. That is what makes a cache hit free: only a
    request that genuinely goes to Open-Meteo spends this budget.
    """
    stored = await _safe_cache_call(db.find_series, lat, lon)
    if stored is not None:
        return stored[0], stored[1], None

    decision = ratelimit.spend_upstream(ratelimit.client_key(request))
    if not decision.allowed:
        return None, None, _rate_limited(decision.retry_after)

    try:
        series, quality = await ingest.fetch_daily_temps(lat, lon)
    except ingest.UpstreamError as exc:
        # 502 rather than 500: the request was fine, the weather service was not.
        logger.warning("Ingest failed for %s/%s: %s", lat, lon, exc)
        return None, None, _upstream_error()

    await _safe_cache_write(db.store_series, lat, lon, series, quality)
    return series, quality, None


def _upstream_error() -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={"error": {"code": "upstream_error", "message": UPSTREAM_MESSAGE}},
    )


# The header is fixed text, never derived from input. A formula injection
# (`=`, `+`, `-`, `@` as the first character of a cell) would be executed by
# Excel and LibreOffice when the file is opened, so any value that could carry
# one is prefixed with an apostrophe.
CSV_COLUMNS = (
    "year",
    "events",
    "heatwave_days",
    "avg_duration_days",
    "avg_intensity_c",
    "avg_max_temp_during_events_c",
    "low_quality",
)


def _csv_cell(value) -> str:
    """Render one cell safely for a spreadsheet."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = f"{value:.2f}" if isinstance(value, float) else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def build_csv(yearly) -> str:
    """Render the yearly rows as CSV.

    Uses the `csv` module rather than joining strings, so a value containing a
    comma or a quote is quoted correctly instead of shifting every column after
    it.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)

    for row in yearly:
        # The rows may be YearlyOut models or plain dicts depending on whether
        # this came from a fresh computation or a cached body, so read them the
        # same way either way rather than assuming one shape.
        if isinstance(row, dict):
            values = [row.get(column) for column in CSV_COLUMNS]
        else:
            values = [getattr(row, column, None) for column in CSV_COLUMNS]
        writer.writerow([_csv_cell(value) for value in values])

    return buffer.getvalue()


def _csv_filename(name: str, start_year: int, end_year: int) -> str:
    """A filename built only from validated values.

    The region name comes from our own preset list, never from the query string,
    so it cannot contain a path separator or a quote. Anything unexpected falls
    back to a constant rather than being echoed into a header.
    """
    slug = "".join(
        char for char in name.lower() if char.isalnum() or char in (" ", "-")
    ).strip().replace(" ", "-")
    if not slug:
        slug = "region"
    return f"{slug}-heatwaves-{start_year}-{end_year}.csv"


@router.get("/analysis", response_model=AnalysisResponse)
async def get_analysis(
    request: Request,
    params: AnalysisRequest = Depends(),
) -> Response:
    """Run the full heatwave analysis for one location and year range."""
    result = await _analyse(request, params)
    if isinstance(result, Response):
        return result
    return _with_cache_headers(result)


async def _analyse(
    request: Request, params: AnalysisRequest
) -> AnalysisResponse | Response:
    """The analysis itself, returning either the model or an error response.

    Split out from the endpoint so the CSV export can reuse exactly this logic.
    Duplicating it would risk the downloaded file and the on-screen table
    disagreeing, which is precisely the inconsistency section 8.7 forbids.

    The response is built once and validated by the response model, so a field
    can never reach the frontend that the contract does not describe.
    """
    # The general budget is charged before any work, including the cache
    # lookup. A caller who has exhausted it gets a 429 without the server doing
    # any work on their behalf.
    general = ratelimit.spend_general(ratelimit.client_key(request))
    if not general.allowed:
        return _rate_limited(general.retry_after)

    name, lat, lon, source_point = params.resolve_location()
    start_year = params.start_year
    end_year = params.end_year
    key = db.analysis_key(lat, lon, start_year, end_year, params.hazard)

    # Tier 1, outside the lock: the common case for a returning user costs one
    # document read and no waiting.
    cached = await _safe_cache_call(db.find_analysis, key)
    if cached is not None:
        return AnalysisResponse(**{**cached, "cached": True})

    lock = db.lock_for(key)
    try:
        async with lock:
            # Re-check under the lock. The request we queued behind may have
            # been for exactly this key, in which case there is nothing to do.
            cached = await _safe_cache_call(db.find_analysis, key)
            if cached is not None:
                return AnalysisResponse(**{**cached, "cached": True})

            series, quality, failure = await _get_series(lat, lon, request)
            if series is None:
                return failure or _upstream_error()

            # The pandas work is CPU-bound and would block the event loop, so
            # it runs in a threadpool. This is the one style used throughout.
            #
            # It runs *inside* the lock deliberately. Holding the lock across
            # the computation is what collapses a cold stampede into one fetch
            # *and* one computation; computing outside it would let N callers
            # each do identical work, which is exactly what the lock exists to
            # prevent.
            body = await run_in_threadpool(
                analysis.run_analysis, series, quality, start_year, end_year
            )

            response = AnalysisResponse(
                region={
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "source_point": source_point,
                },
                params={
                    "start_year": start_year,
                    "end_year": end_year,
                    "hazard": params.hazard,
                },
                # Every one of these comes from the constants in heatwave.py, so
                # the UI never hard-codes the method description.
                threshold={
                    "percentile": heatwave.PERCENTILE,
                    "window_days": heatwave.WINDOW_DAYS,
                    "min_run_days": heatwave.MIN_RUN_DAYS,
                    "baseline_start": heatwave.BASELINE_START,
                    "baseline_end": heatwave.BASELINE_END,
                    "unit": "C",
                },
                # False because this request computed the result. A body read
                # back from the cache returned above with cached=True.
                cached=False,
                **body,
            )

            # Store the *full* response, not just the analysis part. A cache
            # hit rebuilds the response from this document alone, so anything
            # omitted here would be missing from every cached response. The
            # `cached` flag is dropped on purpose: a stored `cached: false`
            # would be read back as a lie.
            stored_body = response.model_dump(mode="json")
            stored_body.pop("cached", None)
            await _safe_cache_write(db.store_analysis, key, stored_body)

            return response
    finally:
        db.release_lock(key)


def _with_cache_headers(response: AnalysisResponse) -> Response:
    """A 200 analysis response with the shared cache header attached.

    Built through the response model rather than by hand, so the JSON is exactly
    what the model validated and the headers cannot change its shape.
    """
    return JSONResponse(
        content=json.loads(response.model_dump_json()),
        headers={"Cache-Control": f"public, max-age={ratelimit.CACHE_CONTROL_MAX_AGE}"},
    )


@router.get("/analysis/export.csv")
async def export_csv(
    request: Request,
    params: AnalysisRequest = Depends(),
) -> Response:
    """The same yearly table as the JSON endpoint, as a CSV download.

    `_analyse` is the same function the JSON endpoint calls, so the file and the
    page can never disagree, and the cache has already been consulted on the way
    through — a download of an analysis already on screen costs one document
    read and no upstream fetch.
    """
    result = await _analyse(request, params)

    if isinstance(result, Response):
        # An error came back from the pipeline (upstream failure, rate limit).
        # Returning it as-is keeps one error shape across both endpoints.
        return result

    analysis_response = result

    body = build_csv(analysis_response.yearly)
    filename = _csv_filename(
        analysis_response.region.name,
        analysis_response.params.start_year,
        analysis_response.params.end_year,
    )

    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Matches the frontend's staleTime, so the browser does not
            # re-download a file it already has.
            "Cache-Control": f"public, max-age={ratelimit.CACHE_CONTROL_MAX_AGE}",
        },
    )