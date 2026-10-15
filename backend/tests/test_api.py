"""Tests for the cache layer.

These run against the real Atlas cluster when MONGODB_URI is set, and skip with
an explanatory message when it is not. That is deliberate: a mocked Mongo would
not exercise TTL indexes, upsert semantics, or the real round-trip cost, and
those are exactly the things a cache layer gets wrong.

Every test works in a throwaway database named after the test, and drops it
afterwards, so a failed run cannot leave junk behind in the shared cluster.
"""

import asyncio
import csv
import io
import uuid

import httpx
import pytest
import respx

from app import db, ingest, ratelimit
from app.config import settings
from app.main import app
from app.routes import CSV_COLUMNS, _csv_cell
from app.schemas import last_complete_year

from app.ratelimit import CACHE_CONTROL_MAX_AGE, UPSTREAM_LIMIT

pytestmark = pytest.mark.skipif(
    not settings.mongodb_uri,
    reason="MONGODB_URI is not set; skipping the cache tests that need a real Atlas",
)


@pytest.fixture
def test_db(monkeypatch):
    """Point the app at a fresh, uniquely named database, then drop it."""
    name = f"climate_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(settings, "mongodb_db", name, raising=False)

    yield name

    database = db.get_client()[name] if db.get_client() else None
    if database is not None:
        database.drop_collection(db.DAILY_COLLECTION)
        database.drop_collection(db.ANALYSES_COLLECTION)
        database.client.drop_database(name)
    db.close_client()
    db.clear_locks()
    # Rate-limit counters are process-wide, so a test that exhausts one would
    # otherwise refuse every later test.
    ratelimit.reset()


# The API requires at least a ten year span, and the synthetic series has to
# cover the whole requested range or the yearly rows come back short.
START_YEAR = 1990
END_YEAR = 1999
PAYLOAD_YEARS = 31  # 1990-2020, so the fixed baseline is fully covered


def synthetic_payload(years=PAYLOAD_YEARS):
    """A realistic ERA5-shaped payload: seasonal cycle plus a warming trend.

    The trend is what makes heatwave detection actually fire, so these tests
    exercise the whole pipeline rather than an empty result.
    """
    import pandas as pd

    index = pd.date_range(f"{START_YEAR}-01-01", periods=years * 365, freq="D")
    # Seasonal swing of 6 C around a 30 C mean, plus a warming trend. The trend
    # term is deliberately tiny: 0.001 * 11315 is about 11 C over 31 years,
    # enough to make detection fire without pushing values past the 60 C
    # plausibility limit, which would be flagged as a data error.
    values = [
        30.0 + 6.0 * ((i % 365) / 365.0) + 0.001 * i
        for i in range(len(index))
    ]
    return {
        "daily": {
            "time": [d.strftime("%Y-%m-%d") for d in index],
            ingest.DAILY_VARIABLE: [round(v, 2) for v in values],
        }
    }


def range_params(start=START_YEAR, end=END_YEAR):
    return {"region": "dhaka", "start_year": start, "end_year": end}


def small_payload(years=3):
    """A deliberately short payload, for tests that only care about status codes.

    Storing a cleaned series in Atlas means writing one document per location,
    and the full 31-year payload is roughly 11,000 dates plus 11,000 floats per
    write. A burst test makes one write per request, and against a free tier
    that dominates the runtime.

    The response is still a valid 200: `clean_series` reindexes only across what
    the payload covers, and the requested range is simply clipped to the years
    that exist. Nothing here asserts on the analysis numbers.
    """
    import pandas as pd

    index = pd.date_range(f"{START_YEAR}-01-01", periods=years * 365, freq="D")
    values = [30.0 + 6.0 * ((i % 365) / 365.0) + 0.02 * i for i in range(len(index))]
    return {
        "daily": {
            "time": [d.strftime("%Y-%m-%d") for d in index],
            ingest.DAILY_VARIABLE: [round(v, 2) for v in values],
        }
    }


def test_repeat_request_is_served_from_the_cache(test_db):
    """The second identical request must not call Open-Meteo again."""
    with respx.mock:
        route = respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)
        params = range_params()

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                first = await client.get("/api/analysis", params=params)
                second = await client.get("/api/analysis", params=params)
                return first, second

        first, second = asyncio.run(run())

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text

        assert first.json()["cached"] is False
        assert second.json()["cached"] is True
        assert route.call_count == 1

        # The two bodies must be identical apart from the cached flag.
        first_body = {k: v for k, v in first.json().items() if k != "cached"}
        second_body = {k: v for k, v in second.json().items() if k != "cached"}
        assert first_body == second_body


def test_new_year_range_reuses_the_stored_daily_series(test_db):
    """A different range for the same city must not re-fetch upstream.

    This is the check the two-collection design exists for. `analyses` is keyed
    by range, so a new range always misses there. `daily_temps` is keyed by
    coordinates alone, so it hits, and Open-Meteo is never called again.
    """
    with respx.mock:
        route = respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                first = await client.get("/api/analysis", params=range_params(1990, 1999))
                second = await client.get("/api/analysis", params=range_params(1992, 2020))
                return first, second

        first, second = asyncio.run(run())

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text

        # Different answers, because different ranges were analysed.
        assert first.json()["params"]["start_year"] == 1990
        assert second.json()["params"]["start_year"] == 1992
        assert first.json()["yearly"] != second.json()["yearly"]

        # The second computed fresh from stored daily data (cached False, since
        # no stored *analysis* matched) but still made zero upstream calls.
        assert second.json()["cached"] is False
        assert route.call_count == 1

    async def repeat():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/api/analysis", params=range_params(1992, 2020))

    assert asyncio.run(repeat()).json()["cached"] is True


def test_two_simultaneous_cold_requests_cause_one_upstream_call(test_db):
    """The per-key lock must collapse a cold stampede into a single fetch."""
    payload = synthetic_payload()
    with respx.mock:
        route = respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        transport = httpx.ASGITransport(app=app)
        params = range_params()

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                responses = await asyncio.gather(
                    *[client.get("/api/analysis", params=params) for _ in range(4)]
                )
                return responses

        responses = asyncio.run(run())

        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        assert route.call_count == 1

        cached_flags = [r.json()["cached"] for r in responses]
        assert cached_flags.count(False) == 1
        assert cached_flags.count(True) == 3


def test_response_reconciles_with_itself(test_db):
    """The cards, chart, insights and table agree.

    The check that matters most, because a cache bug is the one thing that would
    break it: a stored body from different parameters would still be valid JSON
    and still pass a shape test.
    """
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get(
                    "/api/analysis", params=range_params()
                )

        response = asyncio.run(run())
        assert response.status_code == 200, response.text
        body = response.json()

    summary = body["summary"]
    yearly = body["yearly"]
    events = body["events"]

    assert summary["total_events"] == sum(row["events"] for row in yearly)
    assert summary["total_events"] == len(events)

    span = body["params"]["end_year"] - body["params"]["start_year"] + 1
    assert len(yearly) == span
    assert [row["year"] for row in yearly] == list(
        range(body["params"]["start_year"], body["params"]["end_year"] + 1)
    )

    # `fitted` covers the included years only, so the two arrays differ in length
    # whenever any year was flagged.
    included = [row for row in yearly if not row["low_quality"]]
    assert len(body["trend"]["fitted"]) == len(included)
    assert body["trend"]["direction"] in ("increasing", "decreasing", "no_clear_trend")

    assert all(event["duration_days"] >= 3 for event in events)

    if not body["trend"]["significant"]:
        assert body["trend"]["direction"] == "no_clear_trend"


INVALID_REQUESTS = [
    ("box too tall", {"min_lat": 23.0, "max_lat": 26.0, "min_lon": 89.0, "max_lon": 91.0}),
    ("box too wide", {"min_lat": 23.0, "max_lat": 24.0, "min_lon": 89.0, "max_lon": 92.0}),
    ("min_lat above max_lat", {"min_lat": 25.0, "max_lat": 23.0, "min_lon": 89.0, "max_lon": 91.0}),
    ("unknown region", {"region": "london"}),
    ("lat out of range", {"lat": 999, "lon": 90.41}),
]


@pytest.mark.parametrize(
    "label,params", INVALID_REQUESTS, ids=[label for label, _ in INVALID_REQUESTS]
)
def test_invalid_requests_return_422_with_a_readable_message(test_db, label, params):
    """Every rejected request is a 422 carrying a sentence, not a 500.

    A `model_validator` raising `ValueError` bypasses FastAPI's
    `RequestValidationError` handler and lands in the generic 500, telling the
    user the server had failed when their own input was at fault.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    query = {**params, "start_year": 1990, "end_year": 2024}

    async def run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/api/analysis", params=query)

    response = asyncio.run(run())

    assert response.status_code == 422, f"{label}: {response.text}"
    body = response.json()

    # The shared error shape.
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "invalid_request"

    message = body["error"]["message"]
    assert message
    # No pydantic scaffolding, and crucially no echo of the request back.
    assert "validation error" not in message.lower()
    assert "Value error" not in message
    assert "errors.pydantic.dev" not in message
    assert "input_value" not in message


def test_year_range_rules_return_422(test_db):
    """Reversed years, a short span and a future end year are all 422."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    cases = [
        {"region": "dhaka", "start_year": 2020, "end_year": 1995},
        {"region": "dhaka", "start_year": 2015, "end_year": 2020},
        {"region": "dhaka", "start_year": 1990, "end_year": 2030},
    ]

    async def run():
        results = []
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            for query in cases:
                results.append(
                    await client.get("/api/analysis", params=query)
                )
        return results

    for response in asyncio.run(run()):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_request"


def test_box_is_analysed_at_its_centre_point(test_db):
    """ERA5 is a grid, so a box resolves to one point: its centre.

    An assumption worth pinning with a test rather than leaving implicit in the
    schema.
    """
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get(
                    "/api/analysis",
                    params={
                        "min_lat": 23.4,
                        "max_lat": 24.2,
                        "min_lon": 89.9,
                        "max_lon": 90.9,
                        "start_year": START_YEAR,
                        "end_year": END_YEAR,
                    },
                )

        response = asyncio.run(run())

    assert response.status_code == 200, response.text
    region = response.json()["region"]

    assert region["name"] == "Custom box"
    # (23.4 + 24.2) / 2 = 23.8, (89.9 + 90.9) / 2 = 90.4
    assert region["lat"] == pytest.approx(23.8)
    assert region["lon"] == pytest.approx(90.4)
    assert region["source_point"] == "center of box"


def test_box_coordinates_are_rounded_to_two_decimals(test_db):
    """Near-identical boxes must share one cache entry.

    Without rounding, 23.800001 and 23.80 would be two cache rows and two
    upstream fetches for the same place.
    """
    from app import db

    assert db.daily_key(23.8000001, 90.4000001) == "23.8_90.4"
    assert db.daily_key(23.804, 90.404) == "23.8_90.4"


def test_preset_response_reports_its_source(test_db):
    """A preset says so, so the UI never has to guess how it was resolved."""
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get("/api/analysis", params=range_params())

        response = asyncio.run(run())

    assert response.status_code == 200, response.text
    region = response.json()["region"]
    assert region["name"] == "Dhaka"
    assert region["lat"] == pytest.approx(23.81)
    assert region["lon"] == pytest.approx(90.41)
    assert region["source_point"] == "preset"


def test_rate_limit_burst_returns_429_with_retry_after(test_db):
    """A burst past the upstream limit is refused, with a usable Retry-After.

    Each request uses a distinct box, so each one is a genuine cache miss and
    genuinely reaches the upstream limiter. Reusing one location would hit
    `daily_temps` and never spend the budget.
    """
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=small_payload())
        )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        budget = int(UPSTREAM_LIMIT.split()[0])

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                responses = []
                for i in range(budget + 3):
                    lat = 10.0 + i * 0.5
                    responses.append(
                        await client.get(
                            "/api/analysis",
                            params={
                                "min_lat": lat,
                                "max_lat": lat + 0.4,
                                "min_lon": 50.0 + i * 0.5,
                                "max_lon": 50.4 + i * 0.5,
                                "start_year": START_YEAR,
                                "end_year": END_YEAR,
                            },
                        )
                    )
                return responses

        responses = asyncio.run(run())

    codes = [r.status_code for r in responses]
    assert codes[:budget] == [200] * budget, codes
    assert 429 in codes, codes

    refused = next(r for r in responses if r.status_code == 429)

    # The header is what lets a client back off instead of spinning.
    retry_after = refused.headers.get("Retry-After")
    assert retry_after is not None, "a 429 must tell the client how long to wait"
    assert int(retry_after) > 0

    body = refused.json()
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "rate_limited"


def test_cached_reads_do_not_spend_the_upstream_budget(test_db):
    """The whole point of the cache: a hit must be free.

    Without this, a returning user reading a stored answer would be refused for
    making an upstream request they never made.
    """
    with respx.mock:
        route = respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=small_payload())
        )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                await client.get("/api/analysis", params=range_params())
                after_first = route.call_count

                ratelimit.reset()

                responses = []
                for _ in range(10):
                    responses.append(
                        await client.get("/api/analysis", params=range_params())
                    )
                return responses, after_first

        responses, after_first = asyncio.run(run())

    assert all(r.status_code == 200 for r in responses), [
        r.status_code for r in responses
    ]
    assert route.call_count == after_first


def test_health_is_never_cached(test_db):
    """A cached health check would report 'ok' for a service that has died."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            return await client.get("/api/health")

    response = asyncio.run(run())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_read_endpoints_send_the_shared_cache_header(test_db):
    """Regions and analysis both advertise 300 seconds."""
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                regions = await client.get("/api/regions")
                analysis = await client.get("/api/analysis", params=range_params())
                return regions, analysis

        regions, analysis = asyncio.run(run())

    expected = f"public, max-age={CACHE_CONTROL_MAX_AGE}"
    assert regions.headers["cache-control"] == expected
    assert analysis.headers["cache-control"] == expected


def test_csv_export_matches_the_json_response(test_db):
    """The downloaded file and the on-screen table must not disagree.

    Both endpoints call the same `_analyse`, so this is really a check that the
    CSV renders what the model produced, rather than re-deriving anything.
    """
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        params = range_params()

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                js = await client.get("/api/analysis", params=params)
                csv = await client.get("/api/analysis/export.csv", params=params)
                return js, csv

        js_response, csv_response = asyncio.run(run())

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")

    disposition = csv_response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    # The filename is built from the preset name and validated years only.
    assert "dhaka-heatwaves" in disposition
    assert f"{START_YEAR}-{END_YEAR}" in disposition

    rows = list(csv.reader(io.StringIO(csv_response.text)))
    assert rows[0] == list(CSV_COLUMNS)
    # One header row plus one row per year in the response.
    assert len(rows) == len(js_response.json()["yearly"]) + 1
    assert rows[1][0] == str(START_YEAR)


def test_csv_neutralises_formula_injection(test_db):
    """A cell starting =, +, - or @ is executed by Excel on open.

    Every value here comes from our own pipeline rather than user text, but the
    guard is cheap and the failure mode (code execution on the analyst's
    machine) is severe enough to be worth pinning with a test.
    """
    assert _csv_cell("=1+1") == "'=1+1"
    assert _csv_cell("+SUM(A1)") == "'+SUM(A1)"
    assert _csv_cell("-2") == "'-2"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell("1990") == "1990"
    assert _csv_cell(None) == ""


def test_cached_body_is_returned_verbatim(test_db):
    """A cache hit must not recompute or reshape the stored body."""
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)
        params = range_params()

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                await client.get("/api/analysis", params=params)
                return await client.get("/api/analysis", params=params)

        cached = asyncio.run(run()).json()

        key = db.analysis_key(23.81, 90.41, START_YEAR, END_YEAR, "heatwave")
        stored = db.find_analysis(key)

        assert stored is not None
        assert cached["cached"] is True
        # Everything except the flag is byte-identical to what was stored.
        assert {k: v for k, v in cached.items() if k != "cached"} == stored
        assert "cached" not in stored


def test_coordinates_are_rounded_before_the_cache_key(test_db):
    """Near-identical coordinates must share one cache entry."""
    assert db.daily_key(23.8100001, 90.4099999) == db.daily_key(23.81, 90.41)
    assert db.daily_key(23.81, 90.41) == "23.81_90.41"
    assert db.daily_key(23.81, 90.41) != db.daily_key(22.36, 91.78)


def test_analysis_key_includes_the_logic_version(test_db):
    """A logic change must invalidate every stored analysis."""
    first = db.analysis_key(23.81, 90.41, 1990, 2020, "heatwave")
    settings.logic_version = "v2"
    second = db.analysis_key(23.81, 90.41, 1990, 2020, "heatwave")
    settings.logic_version = "v1"

    assert first != second
    assert first.endswith(":v1")
    assert second.endswith(":v2")


def test_low_quality_years_are_excluded_from_the_trend(test_db):
    """Flagged years leave the tests and the fitted line.

    The synthetic payload has no gaps, so nothing is flagged naturally. Patching
    the quality summary is the only way to reach this path without inventing a
    13,000-day series with a real hole in it.
    """
    from app import analysis, ingest

    real_summary = ingest.summarize_quality

    def flagged(series):
        summary = real_summary(series)
        # Mark one year inside the analysed range as low quality.
        return ingest.QualitySummary(
            total_days=summary.total_days,
            missing_days=summary.missing_days,
            interpolated_days=summary.interpolated_days,
            low_quality_years=[START_YEAR + 4],
        )

    original = analysis.heatwave.low_quality_years
    monkey_target = analysis.heatwave
    monkey_target.low_quality_years = lambda series: [START_YEAR + 4]

    try:
        series = ingest.clean_series(
            ingest.parse_daily(synthetic_payload())
        )
        quality = flagged(series)

        body = analysis.run_analysis(
            series, quality, START_YEAR, END_YEAR
        )

        assert START_YEAR + 4 in body["data_quality"]["low_quality_years"]

        yearly = body["yearly"]
        included = [row for row in yearly if not row["low_quality"]]

        # The user's range is untouched: every year still has a row.
        assert len(yearly) == END_YEAR - START_YEAR + 1
        flagged_row = next(r for r in yearly if r["year"] == START_YEAR + 4)
        assert flagged_row["low_quality"] is True

        assert len(body["trend"]["fitted"]) == len(included)
        assert len(body["trend"]["fitted"]) < len(yearly)

        # The summary still counts every event, so the totals the user reads
        # are unaffected by the exclusion.
        assert body["summary"]["total_events"] == sum(r["events"] for r in yearly)
    finally:
        monkey_target.low_quality_years = original


def test_series_round_trips_through_the_database(test_db):
    """A stored series must rebuild into exactly what was saved."""
    # clean_series returns just the frame; the quality summary is computed from
    # it separately. Unpacking the return value would unpack the column names.
    series = ingest.clean_series(ingest.parse_daily(synthetic_payload()))
    quality = ingest.summarize_quality(series)

    db.store_series(23.81, 90.41, series, quality)
    restored = db.find_series(23.81, 90.41)

    assert restored is not None
    restored_series, restored_quality = restored

    assert len(restored_series) == len(series)
    assert list(restored_series.index) == list(series.index)

    # NaN equality: compare the NaN positions and the real values separately.
    original_nan = series["tmax"].isna()
    restored_nan = restored_series["tmax"].isna()
    assert list(original_nan) == list(restored_nan)
    assert (series["tmax"][~original_nan] - restored_series["tmax"][~restored_nan]
            ).abs().max() < 1e-9

    assert list(series["interpolated"]) == list(restored_series["interpolated"])
    assert restored_quality == quality


def test_unreachable_database_does_not_break_the_endpoint(test_db, monkeypatch):
    """A cache outage must degrade performance, not availability."""
    from pymongo.errors import ServerSelectionTimeoutError

    def boom(*args, **kwargs):
        raise ServerSelectionTimeoutError("no server available")

    monkeypatch.setattr(db, "find_analysis", boom)
    monkeypatch.setattr(db, "find_series", boom)
    monkeypatch.setattr(db, "store_series", boom)
    monkeypatch.setattr(db, "store_analysis", boom)

    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )

        transport = httpx.ASGITransport(app=app)

        async def run():
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get(
                    "/api/analysis",
                    params=range_params(),
                )

        response = asyncio.run(run())

    # Still served, just uncached.
    assert response.status_code == 200
    assert response.json()["cached"] is False
    assert response.json()["summary"]["total_events"] >= 0