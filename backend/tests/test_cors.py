"""Tests for the CORS allowlist.

These exist because the bug they would have caught is invisible in normal use: a
comma-separated FRONTEND_ORIGIN read as one allowlist entry matched no browser at
all, yet the API kept working, because opening an endpoint in a tab sends no
Origin header and CORS never applied.

Nothing here needs a database: the health endpoint is unauthenticated and touches
nothing, so these run without MONGODB_URI and without Atlas.

The tests read the live allowlist rather than hardcoding an origin, because `.env`
is deliberately not committed. A test asserting "localhost:5177 is allowed" would
pass or fail depending on which FRONTEND_ORIGIN the developer happened to have.
"""

import asyncio

import httpx

from app.config import Settings, settings
from app.main import ALLOWED_ORIGINS, app

HEALTH = "/api/health"

# Never a real deployment, so it can never be in any allowlist.
UNLISTED = "https://not-allowed.example"


def _send(origin: str | None, *, preflight: bool = False) -> httpx.Response:
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if preflight:
        headers["Access-Control-Request-Method"] = "GET"
        headers["Access-Control-Request-Headers"] = "accept"

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def run() -> httpx.Response:
        method = "OPTIONS" if preflight else "GET"
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(method, HEALTH, headers=headers)

    return asyncio.run(run())


def allowed_origin_for(origin: str, *, preflight: bool = False) -> str | None:
    """The Access-Control-Allow-Origin a browser at `origin` would receive."""
    return _send(origin, preflight=preflight).headers.get("access-control-allow-origin")


class TestOriginParsing:
    """`FRONTEND_ORIGIN` is documented as accepting a comma-separated list."""

    def test_a_single_origin_is_still_one_entry(self):
        assert Settings(frontend_origin="https://climate-trend.vercel.app").frontend_origins == {
            "https://climate-trend.vercel.app"
        }

    def test_a_comma_separated_list_becomes_several_entries(self):
        # As one entry this matched no browser, so every response was blocked
        # with no header to explain why.
        parsed = Settings(
            frontend_origin="http://localhost:5173,https://climate-trend.vercel.app"
        ).frontend_origins
        assert parsed == {
            "http://localhost:5173",
            "https://climate-trend.vercel.app",
        }

    def test_surrounding_whitespace_is_trimmed(self):
        # Pydantic does not strip a comma-separated list, and a stray space
        # turns into an allowlist entry no browser will ever send.
        parsed = Settings(
            frontend_origin=" http://localhost:5173 , https://climate-trend.vercel.app "
        ).frontend_origins
        assert parsed == {
            "http://localhost:5173",
            "https://climate-trend.vercel.app",
        }

    def test_empty_entries_are_dropped(self):
        parsed = Settings(frontend_origin="https://a.example,,  ,https://b.example").frontend_origins
        assert parsed == {"https://a.example", "https://b.example"}

    def test_order_does_not_change_the_result(self):
        forwards = Settings(frontend_origin="http://localhost:5173,https://a.example")
        backwards = Settings(frontend_origin="https://a.example,http://localhost:5173")
        assert forwards.frontend_origins == backwards.frontend_origins


class TestLocalSweepGate:
    """The development port sweep must never open on a public deployment."""

    def test_a_local_only_configuration_enables_the_sweep(self):
        assert Settings(frontend_origin="http://localhost:5173").has_local_frontend is True

    def test_a_deployed_origin_does_not(self):
        deployed = Settings(frontend_origin="https://climate-trend.vercel.app")
        assert deployed.has_local_frontend is False

    def test_mixing_deployed_and_local_does_not(self):
        # `any` instead of `all` here would let a deployed frontend vote for the
        # sweep, letting any page on a visitor's machine read this API's responses.
        mixed = Settings(frontend_origin="https://climate-trend.vercel.app,http://localhost:5173")
        assert mixed.has_local_frontend is False

    def test_an_empty_configuration_does_not(self):
        # `all()` over an empty set is True, which would open the sweep on an
        # instance whose FRONTEND_ORIGIN was never set at all.
        assert Settings(frontend_origin="").has_local_frontend is False


class TestConfiguredAllowlist:
    """What `main.py` actually built, checked against the settings it came from."""

    def test_every_configured_origin_is_allowed(self):
        assert settings.frontend_origins <= set(ALLOWED_ORIGINS)

    def test_an_unlisted_origin_is_not_allowed(self):
        assert UNLISTED not in ALLOWED_ORIGINS

    def test_the_localhost_sweep_matches_the_gate(self):
        # The sweep is a development convenience with a security cost, so it has
        # to follow the gate exactly — present only when every configured origin
        # is local, and never present otherwise.
        has_sweep = "http://localhost:5177" in ALLOWED_ORIGINS
        assert has_sweep is settings.has_local_frontend

    def test_the_sweep_stays_inside_its_range(self):
        if not settings.has_local_frontend:
            return
        assert "http://localhost:5191" in ALLOWED_ORIGINS
        assert "http://localhost:5192" not in ALLOWED_ORIGINS


class TestMiddleware:
    """The allowlist as a browser experiences it."""

    def test_each_configured_origin_is_allowed(self):
        for origin in settings.frontend_origins:
            assert allowed_origin_for(origin) == origin

    def test_an_unlisted_origin_gets_no_header(self):
        # The absence of the header is the whole enforcement. A response that
        # simply omits it is what makes the browser refuse to hand the body over.
        assert allowed_origin_for(UNLISTED) is None

    def test_a_preflight_from_a_configured_origin_is_answered(self):
        for origin in settings.frontend_origins:
            assert allowed_origin_for(origin, preflight=True) == origin

    def test_a_preflight_from_an_unlisted_origin_is_not_answered(self):
        assert allowed_origin_for(UNLISTED, preflight=True) is None

    def test_a_request_with_no_origin_is_answered_normally(self):
        # A direct navigation or curl always works, so "it works in a tab" proves
        # nothing about CORS.
        response = _send(None)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}