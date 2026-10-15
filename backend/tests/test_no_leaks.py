"""Sweep every failure path and assert no internal detail escapes.

No stack trace, no file path, no driver name, no exception repr in any response
body. A leaked traceback tells an attacker the framework versions, the install
layout and often the connection string's shape.
"""

import asyncio

import httpx
import pytest
import respx

from app import db, ingest, ratelimit
from app.main import app
from tests.test_api import range_params, small_payload, synthetic_payload

pytestmark = pytest.mark.skipif(
    not db.settings.mongodb_uri, reason="MONGODB_URI is not set"
)

# Substrings that must never appear in a client-facing body.
LEAKS = [
    "Traceback",
    "File \"",
    "site-packages",
    "pymongo",
    "pydantic",
    "fastapi",
    "starlette",
    "anyio",
    "httpx",
    "open-meteo",
    "mongodb+srv",
    "mongodb://",
    "cluster0",
    "MongoClient",
    "ValidationError",
    "AttributeError",
    "KeyError",
    "Error:",
    "app/",
    ".py",
]


def assert_clean(body_text: str, label: str) -> None:
    lowered = body_text.lower()
    for leak in LEAKS:
        assert leak.lower() not in lowered, f"{label} leaked {leak!r}: {body_text[:200]}"


@pytest.fixture(autouse=True)
def clean_state():
    """Every test here starts from nothing.

    Both rate-limit budgets and both caches are process-wide or shared, so
    without this a test that exhausts a budget silently fails the next one, and
    a warm cache quietly skips the very failure being tested.
    """
    ratelimit.reset()
    db.clear_locks()
    db.get_collection(db.ANALYSES_COLLECTION).delete_many({})
    db.get_collection(db.DAILY_COLLECTION).delete_many({})
    yield
    ratelimit.reset()


async def _get(path: str, params: dict):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, params=params)


def test_422_body_is_clean():
    response = asyncio.run(
        _get(
            "/api/analysis",
            {"min_lat": 23, "max_lat": 26, "min_lon": 89, "max_lon": 91,
             "start_year": 1990, "end_year": 2024},
        )
    )
    assert response.status_code == 422
    assert_clean(response.text, "422")


def test_429_body_is_clean():
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=small_payload())
        )

        async def run():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://t"
            ) as client:
                for i in range(12):
                    lat = 10.0 + i * 0.5
                    r = await client.get(
                        "/api/analysis",
                        params={
                            "min_lat": lat, "max_lat": lat + 0.4,
                            "min_lon": 50 + i * 0.5, "max_lon": 50.4 + i * 0.5,
                            "start_year": 1990, "end_year": 2024,
                        },
                    )
                    if r.status_code == 429:
                        return r
                return r

        response = asyncio.run(run())

    assert response.status_code == 429
    assert_clean(response.text, "429")


def test_502_body_is_clean():
    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(return_value=httpx.Response(503))
        response = asyncio.run(_get("/api/analysis", range_params()))

    assert response.status_code == 502, response.text
    assert_clean(response.text, "502")


def test_404_body_is_clean():
    response = asyncio.run(_get("/api/does-not-exist", {}))
    assert response.status_code == 404
    assert_clean(response.text, "404")


def test_500_body_is_clean_when_something_really_breaks(monkeypatch):
    """Force an unexpected exception and confirm nothing internal escapes."""
    import app.analysis as analysis_module

    def explode(*args, **kwargs):
        raise RuntimeError(
            "boom: mongodb+srv://user:secret@cluster0.example.mongodb.net"
        )

    monkeypatch.setattr(analysis_module, "run_analysis", explode)
    db.get_collection(db.ANALYSES_COLLECTION).delete_many({})

    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )
        response = asyncio.run(_get("/api/analysis", range_params()))

    assert response.status_code == 500
    assert_clean(response.text, "500")
    assert "secret" not in response.text
    assert "example.mongodb.net" not in response.text


def test_cache_outage_body_is_clean(monkeypatch):
    """A dead database must degrade to recomputing, not leak driver internals."""
    from pymongo.errors import ServerSelectionTimeoutError

    def boom(*args, **kwargs):
        raise ServerSelectionTimeoutError(
            "host 'cluster0.example.mongodb.net:27017' replica set unreachable"
        )

    monkeypatch.setattr(db, "find_analysis", boom)
    monkeypatch.setattr(db, "find_series", boom)

    with respx.mock:
        respx.get(ingest.ARCHIVE_URL).mock(
            return_value=httpx.Response(200, json=synthetic_payload())
        )
        response = asyncio.run(_get("/api/analysis", range_params()))

    assert response.status_code == 200
    assert_clean(response.text, "cache outage")


def test_csv_error_path_is_clean():
    """An invalid export request returns the same clean 422, not a CSV."""
    response = asyncio.run(
        _get(
            "/api/analysis/export.csv",
            {"region": "dhaka", "start_year": 2020, "end_year": 1995},
        )
    )
    assert response.status_code == 422
    assert_clean(response.text, "export.csv 422")