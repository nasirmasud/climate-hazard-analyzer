"""Tests for reverse geocoding.

The point of every test here is that a failed lookup is a *normal* outcome and
never an error. The frontend has a perfectly good fallback label, so anything
that turns a missing name into a failed request is a bug.
"""

import asyncio
import uuid

import httpx
import pytest
import respx

from app import db, places, ratelimit
from app.config import settings
from app.main import app

pytestmark = pytest.mark.skipif(
    not settings.mongodb_uri,
    reason="MONGODB_URI is not set; skipping the place-name tests that need Atlas",
)

# Real captured Nominatim responses, trimmed only where noted.
DHAKA = {
    "name": "Dhaka",
    "address": {
        "city": "Dhaka",
        "state": "Dhaka Division",
        "country": "Bangladesh",
        "country_code": "bd",
    },
}

RURAL = {
    "name": "Khalia",
    "address": {
        "village": "Khalia",
        "county": "Chattogram District",
        "country": "Bangladesh",
    },
}

COUNTY_ONLY = {
    "name": "Somewhere",
    "address": {"county": "Bumthang District", "country": "Bhutan"},
}


@pytest.fixture(autouse=True)
def clean_state():
    # The place-name cache is keyed by coordinate and would otherwise carry a
    # name from an earlier test into a later one, turning a failure case into a
    # cache hit.
    db.get_collection(places.PLACE_COLLECTION).delete_many({})
    ratelimit.reset()
    db.clear_locks()
    yield
    db.get_collection(places.PLACE_COLLECTION).delete_many({})
    ratelimit.reset()


# --------------------------------------------------------------------------
# The pure name extraction. This is where the label text is decided.
# --------------------------------------------------------------------------


class TestShortPlaceName:
    def test_city_and_country(self):
        assert places.short_place_name(DHAKA) == "Dhaka, Bangladesh"

    def test_falls_through_to_a_smaller_settlement(self):
        # A click on open country has no city, so the chain must reach village.
        assert places.short_place_name(RURAL) == "Khalia, Bangladesh"

    def test_falls_through_to_county(self):
        assert places.short_place_name(COUNTY_ONLY) == (
            "Bumthang District, Bhutan"
        )

    def test_does_not_repeat_a_name_that_is_also_the_country(self):
        payload = {"address": {"city": "France", "country": "France"}}
        assert places.short_place_name(payload) == "France"

    def test_copes_with_no_country(self):
        payload = {"address": {"city": "Atlantis"}}
        assert places.short_place_name(payload) == "Atlantis"

    def test_uses_the_record_name_when_no_address_field_matches(self):
        payload = {"name": "Mount Doom", "address": {"country": "New Zealand"}}
        assert places.short_place_name(payload) == "Mount Doom, New Zealand"

    # The shapes Nominatim returns when it genuinely cannot geocode.
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"error": "Unable to geocode"},
            {"address": None},
            {"address": "not a dict"},
            {"address": {}},
            {"address": {"city": "", "country": "Brazil"}},
            {"address": {"city": "   "}},
            {"address": {"city": None, "country": "Brazil"}},
            "a string",
            [],
        ],
    )
    def test_returns_none_when_there_is_nothing_to_show(self, payload):
        assert places.short_place_name(payload) is None

    def test_never_invents_a_name_from_coordinates(self):
        """A label is a place name, never the numbers that located it."""
        # Even a payload that quotes the query back must not produce it.
        payload = {
            "lat": "23.81",
            "lon": "90.41",
            "display_name": "23.81, 90.41",
            "address": {"city": "Dhaka", "country": "Bangladesh"},
        }
        name = places.short_place_name(payload)
        assert name == "Dhaka, Bangladesh"
        assert "23.81" not in name
        assert "90.41" not in name


# --------------------------------------------------------------------------
# The query
# --------------------------------------------------------------------------


class TestBuildQuery:
    def test_requests_english_and_a_settlement_zoom(self):
        query = places.build_query(23.81, 90.41)
        assert query["lat"] == "23.81"
        assert query["lon"] == "90.41"
        assert query["format"] == "jsonv2"
        assert query["zoom"] == "10"
        # Without this, Dhaka comes back in Bengali.
        assert query["accept-language"] == "en"

    def test_rounds_the_cache_key_to_two_decimals(self):
        assert places.place_key(23.814567, 90.412345) == "23.81_90.41"


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def get(lat: float, lon: float):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(
                "/api/place-name", params={"lat": lat, "lon": lon}
            )

    return asyncio.run(run())


class TestEndpoint:
    def test_returns_a_short_name(self):
        with respx.mock:
            respx.get(places.NOMINATIM_URL).mock(
                return_value=httpx.Response(200, json=DHAKA)
            )
            response = get(23.81, 90.41)

        assert response.status_code == 200
        assert response.json() == {"name": "Dhaka, Bangladesh"}

    def test_a_point_with_no_name_is_a_normal_200_with_null(self):
        # Open water: not an error, and definitely not a 500.
        with respx.mock:
            respx.get(places.NOMINATIM_URL).mock(
                return_value=httpx.Response(
                    200, json={"error": "Unable to geocode"}
                )
            )
            response = get(0.0, 0.0)

        assert response.status_code == 200
        assert response.json() == {"name": None}

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(404),
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, text="not json"),
        ],
    )
    def test_every_upstream_response_failure_degrades_to_null(self, response):
        with respx.mock:
            respx.get(places.NOMINATIM_URL).mock(return_value=response)
            result = get(23.81, 90.41)

        # Never an error status: the frontend's fallback is the whole point.
        assert result.status_code == 200
        assert result.json() == {"name": None}

    @pytest.mark.parametrize(
        "error",
        [httpx.ConnectError("no route"), httpx.ReadTimeout("too slow")],
    )
    def test_every_upstream_exception_degrades_to_null(self, error):
        with respx.mock:
            # respx raises via side_effect; return_value expects a Response.
            respx.get(places.NOMINATIM_URL).mock(side_effect=error)
            result = get(23.81, 90.41)

        assert result.status_code == 200
        assert result.json() == {"name": None}

    def test_a_second_lookup_of_the_same_point_is_served_from_cache(self):
        db.get_collection(places.PLACE_COLLECTION).delete_many({})

        with respx.mock:
            route = respx.get(places.NOMINATIM_URL).mock(
                return_value=httpx.Response(200, json=DHAKA)
            )

            first = get(23.81, 90.41)
            second = get(23.81, 90.41)

        assert first.json() == second.json() == {"name": "Dhaka, Bangladesh"}
        assert route.call_count == 1, "the second lookup should hit Mongo"

    def test_a_failed_lookup_is_not_cached(self):
        db.get_collection(places.PLACE_COLLECTION).delete_many({})

        with respx.mock:
            route = respx.get(places.NOMINATIM_URL).mock(
                return_value=httpx.Response(500)
            )
            get(23.81, 90.41)
            get(23.81, 90.41)

        # Storing the failure would make a transient outage look permanent.
        assert route.call_count == 2

    def test_an_unreachable_database_still_answers(self, monkeypatch):
        """A cache outage must not turn a nicety into a failure."""

        def boom(*args, **kwargs):
            raise RuntimeError("mongodb is down")

        monkeypatch.setattr(places, "find_cached", boom)
        monkeypatch.setattr(places, "store", boom)

        with respx.mock:
            respx.get(places.NOMINATIM_URL).mock(
                return_value=httpx.Response(200, json=DHAKA)
            )
            response = get(23.81, 90.41)

        assert response.status_code == 200
        # The name is lost, but the endpoint answers rather than failing.
        assert response.json() == {"name": None}

    def test_no_credentials_or_internals_appear_in_the_body(self):
        with respx.mock:
            respx.get(places.NOMINATIM_URL).mock(return_value=httpx.Response(500))
            response = get(23.81, 90.41)

        body = response.text.lower()
        for leak in ("traceback", "nominatim", "httpx", "mongodb", "cluster0", ".py"):
            assert leak not in body, f"leaked {leak}"