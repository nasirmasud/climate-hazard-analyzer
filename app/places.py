"""Reverse geocoding a clicked point into a short place name.

The map sets a box rather than a single named place, so without this the
Location dropdown could only ever say "Custom area". A short name makes the
selection readable, and it comes from OpenStreetMap Nominatim, which needs no
API key.

Three rules shape this module, all of them about not breaking the page:

1. **Never raise.** Every failure path returns `None`, which the frontend reads
   as "no name" and renders as the plain fallback label. A lookup that fails must
   not disturb the four coordinate inputs or block an analysis.
2. **Never be slow.** The request has a short timeout and is not awaited by
   anything the user is waiting on. The coordinates fill in immediately; the name
   arrives afterwards, if at all.
3. **Never leak coordinates.** The label is built only from Nominatim's own
   address fields. There is no branch anywhere that falls back to printing
   "23.81, 90.41" as if that were a place name.

The call goes through the backend rather than the browser so that it inherits
the existing rate limiting and Mongo cache: repeated clicks on the same spot
cost nothing, and Nominatim's usage policy is respected in one place.
"""

import logging
from typing import Any

import httpx

from . import db, ratelimit

logger = logging.getLogger("climate")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Nominatim's usage policy requires an identifying User-Agent and a maximum of
# one request per second. The timeout is deliberately short: a slow lookup is
# worth less than a prompt fallback.
REQUEST_TIMEOUT_SECONDS = 3.0

# Place names change rarely, so they are cached for far longer than analyses.
PLACE_TTL_DAYS = 180

PLACE_COLLECTION = "place_names"

# Nominatim's own field priority, most specific first. Cities are the common
# case, but a click on open country has no city, so the chain has to fall
# through to progressively smaller settlements before giving up.
NAME_KEYS = (
    "city",
    "town",
    "village",
    "municipality",
    "hamlet",
    "isolated_dwelling",
    "county",
)

USER_AGENT = "climate-trend-analyzer/1.0 (heatmap trend analysis demo)"


def short_place_name(payload: Any) -> str | None:
    """Turn a Nominatim reverse response into "Dhaka, Bangladesh".

    Returns None when nothing usable is present, which is the caller's signal to
    use the plain fallback label.

    Deliberately takes the parsed payload rather than doing the request, so this
    is a pure function and can be tested against real captured responses.
    """
    if not isinstance(payload, dict):
        return None

    address = payload.get("address")
    if not isinstance(address, dict):
        return None

    # Most specific settlement first, falling back to the record's own name.
    primary = None
    for key in NAME_KEYS:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            primary = value.strip()
            break

    if primary is None:
        fallback = payload.get("name")
        if isinstance(fallback, str) and fallback.strip():
            primary = fallback.strip()

    if primary is None:
        return None

    country = address.get("country")
    if isinstance(country, str) and country.strip():
        country = country.strip()
        # Avoid "France, France" for a country that is also the settlement name.
        if country.lower() != primary.lower():
            return f"{primary}, {country}"

    return primary


def build_query(lat: float, lon: float) -> dict[str, str]:
    """Nominatim reverse parameters.

    Coordinates arrive already validated and bounded, so they are safe to
    interpolate. `zoom=10` asks for a settlement rather than a full street
    address, which is what "short form" means here.

    `accept-language=en` matters more than it looks. Without it Nominatim replies
    in the local language, and Dhaka comes back as "ঢাকা, বাংলাদেশ" rather than
    the "Dhaka, Bangladesh" the dropdown is meant to read. The response is still
    valid JSON either way, so the bug is silent — it just looks wrong.
    """
    return {
        "lat": f"{lat}",
        "lon": f"{lon}",
        "format": "jsonv2",
        "zoom": "10",
        "accept-language": "en",
    }


def place_key(lat: float, lon: float) -> str:
    """Cache key for a point, rounded the same way the daily series is."""
    return f"{round(lat, 2)}_{round(lon, 2)}"


def find_cached(lat: float, lon: float) -> str | None:
    """A previously looked-up name, or None on a miss.

    A cached None is not stored, so a failed lookup does not poison the cache
    with an empty result that would then persist for months.
    """
    collection = db.get_collection(PLACE_COLLECTION)
    if collection is None:
        return None

    try:
        document = collection.find_one({"_id": place_key(lat, lon)})
    except Exception as exc:  # a cache failure must not break the lookup
        logger.warning("place_names lookup failed: %s", exc)
        return None

    if document is None:
        return None

    name = document.get("name")
    return name if isinstance(name, str) and name else None


def store(lat: float, lon: float, name: str) -> None:
    """Cache a successful lookup."""
    collection = db.get_collection(PLACE_COLLECTION)
    if collection is None:
        return

    try:
        collection.replace_one(
            {"_id": place_key(lat, lon)},
            {
                "_id": place_key(lat, lon),
                "name": name,
                "looked_up_at": db._utcnow(),
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("Could not store place name: %s", exc)


async def lookup_place_name(lat: float, lon: float, client_key: str) -> str | None:
    """The short name for a point, or None.

    `client_key` identifies the caller for rate limiting. It is passed in rather
    than derived here, so there is one definition of "who is this" in the whole
    codebase.

    Checks the cache first, then the rate budget, then the network.

    **The whole body is wrapped.** The invariant is that this function never
    raises, and individual guards are not enough to guarantee it: a helper can be
    swapped out, and a cache write failing after a successful lookup must not turn
    a nice-to-have into a 500. One outer guard makes the guarantee structural
    rather than a property of each call site.
    """
    try:
        return await _lookup(lat, lon, client_key)
    except Exception as exc:
        # Anything at all: an unexpected driver error, a bug in the extraction,
        # a timeout escaping from a helper. The caller's fallback covers it.
        logger.warning("Place-name lookup failed for %s/%s: %s", lat, lon, exc)
        return None


async def _lookup(lat: float, lon: float, client_key: str) -> str | None:
    """The actual lookup. `lookup_place_name` owns the error handling."""
    cached = await db.run_in_pool(find_cached, lat, lon)
    if cached:
        return cached

    if not ratelimit.spend_place(client_key).allowed:
        logger.info("Place-name rate limit reached; using fallback label")
        return None

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(NOMINATIM_URL, params=build_query(lat, lon))
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        # Timeouts, 4xx, 5xx, malformed JSON: all the same to the caller.
        logger.info("Place-name lookup failed for %s/%s: %s", lat, lon, exc)
        return None

    name = short_place_name(payload)
    if name:
        try:
            await db.run_in_pool(store, lat, lon, name)
        except Exception as exc:
            # The name was found; failing to cache it only costs a repeat
            # request later, so it must not discard the answer.
            logger.warning("Could not cache place name: %s", exc)

    return name