"""MongoDB Atlas connection, indexes, and the cache read/write path.

Two collections, for two different jobs:

* **`daily_temps`** holds a location's cleaned daily series, keyed by coordinates
  alone so it is reusable across *any* year range. This is what stops a second
  request for the same city from calling Open-Meteo again.
* **`analyses`** holds a finished response body for one exact set of parameters.

The read path is ordered so the cache is cheap: `analyses`, then `daily_temps`,
and only then Open-Meteo. A new year range for a city we already hold costs one
document read and no upstream call.

Every pymongo call here is blocking and awaited through `run_in_threadpool` by
the caller. This project uses pymongo only, never motor, so there is exactly one
style to explain. A cache outage is never fatal: if MongoDB is unreachable the
app still answers, just by recomputing.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from .config import settings
from .ingest import INTERPOLATED_COLUMN, TMAX_COLUMN, QualitySummary

logger = logging.getLogger("climate")

DAILY_COLLECTION = "daily_temps"
ANALYSES_COLLECTION = "analyses"

# An analysis is cheap to recompute and expensive to store forever, so stored
# bodies expire. Thirty days also means a user returning after a month gets fresh
# upstream data rather than a result frozen at their last visit.
ANALYSIS_TTL_DAYS = 30

# The daily series outlives the analyses built from it, because one fetch serves
# every year range a user might ask for. Ninety days bounds how stale the
# upstream data can be without making anyone pay for a refetch inside a season.
#
# The two TTLs are deliberately different: an analysis is a finished answer to
# one question, the series is the evidence behind many answers.
SERIES_TTL_DAYS = 90

# Place names barely change, so they are cached far longer than anything else.
PLACE_TTL_DAYS = 180

PLACE_COLLECTION = "place_names"

# Guards against a pathological upstream response being stored as if it were
# real data. The Atlas document limit is 16 MB, so this is far below it.
MAX_STORED_DAYS = 40000

_client: MongoClient | None = None


def get_client() -> MongoClient | None:
    """The shared client, or None when no URI is configured.

    One client is reused for the process lifetime, because MongoClient maintains
    its own connection pool and is documented as thread-safe.
    """
    global _client

    if not settings.mongodb_uri:
        return None

    if _client is None:
        _client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )
        logger.info("MongoDB client created for database '%s'", settings.mongodb_db)

    return _client


def get_database() -> Database | None:
    client = get_client()
    if client is None:
        return None
    return client[settings.mongodb_db]


def get_collection(name: str) -> Collection | None:
    database = get_database()
    if database is None:
        return None
    return database[name]


def close_client() -> None:
    """Close the pool. Used on shutdown and by tests."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# --------------------------------------------------------------------------
# Cache keys
# --------------------------------------------------------------------------


def daily_key(lat: float, lon: float) -> str:
    """Key for a location's daily series, e.g. "23.81_90.41".

    Coordinates are rounded to two decimals *before* the key is built. Without
    that, 23.810000 and 23.81 would be two cache entries for the same place.
    """
    return f"{round(lat, 2)}_{round(lon, 2)}"


def analysis_key(
    lat: float, lon: float, start_year: int, end_year: int, hazard: str
) -> str:
    """Key for one finished analysis.

    `LOGIC_VERSION` is part of the key on purpose. When the detection logic
    changes, every stored analysis becomes wrong, and bumping the version means
    the old ones are simply never read again instead of being served as if they
    were still correct.
    """
    return (
        f"{daily_key(lat, lon)}:{start_year}:{end_year}:{hazard}:"
        f"{settings.logic_version}"
    )


# --------------------------------------------------------------------------
# Indexes
# --------------------------------------------------------------------------


def ensure_indexes() -> None:
    """Create indexes at startup. Safe to call repeatedly.

    MongoDB treats an identical create_index as a no-op, so this does not need
    guarding against already existing.
    """
    try:
        daily = get_collection(DAILY_COLLECTION)
        analyses = get_collection(ANALYSES_COLLECTION)
    except PyMongoError as exc:
        logger.warning("Could not reach MongoDB to set up indexes: %s", exc)
        return

    if daily is None or analyses is None:
        logger.warning("MONGODB_URI is not set; skipping index creation")
        return

    try:
        # _id is indexed automatically and uniquely. fetched_at carries a TTL, so
        # a series older than 90 days is dropped and the next request refetches
        # it, which is what stops a corrected upstream dataset being served
        # indefinitely.
        daily.create_index(
            [("fetched_at", ASCENDING)],
            name="fetched_at_ttl",
            expireAfterSeconds=SERIES_TTL_DAYS * 24 * 60 * 60,
        )

        # Reverse-geocoded names. Same reasoning, much longer: a town does not
        # move, and a failed lookup is never cached, so this only grows when a
        # name was genuinely found.
        places = get_collection(PLACE_COLLECTION)
        if places is not None:
            places.create_index(
                [("looked_up_at", ASCENDING)],
                name="looked_up_at_ttl",
                expireAfterSeconds=PLACE_TTL_DAYS * 24 * 60 * 60,
            )

        # TTL: MongoDB deletes the document 30 days after created_at. This is a
        # background sweep, so a document can outlive its expiry briefly.
        analyses.create_index(
            [("created_at", ASCENDING)],
            name="created_at_ttl",
            expireAfterSeconds=ANALYSIS_TTL_DAYS * 24 * 60 * 60,
        )
        logger.info("MongoDB indexes ensured")
    except PyMongoError as exc:
        # Index creation failing is not worth failing startup over: the cache
        # still works without the TTL, it just cleans up less promptly.
        logger.warning("Index creation failed (continuing without it): %s", exc)


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


def _utcnow() -> datetime:
    # Atlas stores naive UTC datetimes. Passing an aware datetime would make
    # MongoDB keep the offset and the TTL comparison would then be wrong.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def series_to_document(series: pd.DataFrame, quality: QualitySummary) -> dict[str, Any]:
    """Turn the cleaned frame into a storable document.

    Three parallel arrays rather than nested objects: MongoDB documents have a
    16 MB limit and BSON field-name overhead is significant, so for 13,000 days
    the flat arrays are both smaller and much cheaper to decode into a DataFrame.
    """
    index = series.index
    values = series[TMAX_COLUMN]

    # NaN has no BSON representation, so missing days become None. The parallel
    # `interpolated` array is what distinguishes "filled in" from "unknown" on
    # the way back in.
    tmax_values: list[float | None] = [
        None if pd.isna(value) else float(value) for value in values
    ]
    interpolated_values: list[bool] = [
        bool(flag) for flag in series[INTERPOLATED_COLUMN].fillna(False)
    ]

    return {
        "dates": [day.strftime("%Y-%m-%d") for day in index],
        "tmax": tmax_values,
        "interpolated": interpolated_values,
        "period_start": index.min().strftime("%Y-%m-%d"),
        "period_end": index.max().strftime("%Y-%m-%d"),
        "quality": {
            "total_days": quality.total_days,
            "missing_days": quality.missing_days,
            "interpolated_days": quality.interpolated_days,
            "low_quality_years": list(quality.low_quality_years),
        },
    }


def document_to_series(document: dict[str, Any]) -> pd.DataFrame:
    """Rebuild the cleaned frame from a stored document.

    The result matches `ingest.clean_series`, so everything downstream is
    identical whether the data came from the network or the cache. Divergent
    paths would let a cached result disagree with a fresh one.
    """
    index = pd.to_datetime(pd.Index(document["dates"]), format="%Y-%m-%d")
    values = [np.nan if v is None else float(v) for v in document["tmax"]]

    frame = pd.DataFrame({TMAX_COLUMN: values}, index=index)
    frame.index.name = "date"

    if "interpolated" in document:
        frame[INTERPOLATED_COLUMN] = [bool(flag) for flag in document["interpolated"]]
    else:
        frame[INTERPOLATED_COLUMN] = False

    return frame


def quality_from_document(document: dict[str, Any]) -> QualitySummary:
    """Rebuild the quality summary from a stored document."""
    quality = document.get("quality") or {}
    return QualitySummary(
        total_days=int(quality.get("total_days", 0)),
        missing_days=int(quality.get("missing_days", 0)),
        interpolated_days=int(quality.get("interpolated_days", 0)),
        low_quality_years=[int(year) for year in quality.get("low_quality_years", [])],
    )


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def find_analysis(key: str) -> dict[str, Any] | None:
    """Return a stored response body, or None on a miss."""
    collection = get_collection(ANALYSES_COLLECTION)
    if collection is None:
        return None

    try:
        document = collection.find_one({"_id": key})
    except PyMongoError as exc:
        logger.warning("analyses lookup failed for %s: %s", key, exc)
        return None

    if document is None:
        return None

    result = document.get("result")
    return result if isinstance(result, dict) else None


def find_series(lat: float, lon: float) -> tuple[pd.DataFrame, QualitySummary] | None:
    """Return the stored daily series for a location, or None on a miss."""
    collection = get_collection(DAILY_COLLECTION)
    if collection is None:
        return None

    try:
        document = collection.find_one({"_id": daily_key(lat, lon)})
    except PyMongoError as exc:
        logger.warning("daily_temps lookup failed for %s: %s", daily_key(lat, lon), exc)
        return None

    if document is None or "dates" not in document:
        return None

    try:
        return document_to_series(document), quality_from_document(document)
    except (KeyError, ValueError, TypeError) as exc:
        # A malformed document is treated as a miss rather than an error, so a
        # bad write can never take the endpoint down.
        logger.warning("Ignoring malformed daily_temps document: %s", exc)
        return None


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def store_series(lat: float, lon: float, series: pd.DataFrame, quality: QualitySummary) -> None:
    """Store a location's full daily series.

    Uses replace_one with upsert so re-fetching a location updates it in place
    rather than accumulating duplicates.
    """
    collection = get_collection(DAILY_COLLECTION)
    if collection is None:
        return

    if len(series) > MAX_STORED_DAYS:
        logger.warning(
            "Refusing to store %d days for %s/%s (limit %d)",
            len(series),
            lat,
            lon,
            MAX_STORED_DAYS,
        )
        return

    document = series_to_document(series, quality)
    document.update(
        {
            "_id": daily_key(lat, lon),
            "lat": round(lat, 2),
            "lon": round(lon, 2),
            "fetched_at": _utcnow(),
        }
    )

    try:
        collection.replace_one({"_id": document["_id"]}, document, upsert=True)
        logger.info(
            "Stored %d days for %s/%s", len(series), round(lat, 2), round(lon, 2)
        )
    except PyMongoError as exc:
        # A failed cache write must not fail the request. The user gets their
        # answer; the next request just recomputes.
        logger.warning("Could not store daily_temps for %s/%s: %s", lat, lon, exc)


def store_analysis(key: str, result: dict[str, Any]) -> None:
    """Store a finished response body against its key."""
    collection = get_collection(ANALYSES_COLLECTION)
    if collection is None:
        return

    try:
        collection.replace_one(
            {"_id": key},
            {"_id": key, "result": result, "created_at": _utcnow()},
            upsert=True,
        )
    except PyMongoError as exc:
        logger.warning("Could not store analysis %s: %s", key, exc)


# --------------------------------------------------------------------------
# Single-flight locking
# --------------------------------------------------------------------------


# One lock per cache key, so two users who both ask for cold Dhaka at the same
# moment produce one upstream call rather than two.
#
# Each entry carries a waiter count alongside the lock, because a lock can be
# *unlocked* while another coroutine is still queued behind it. Dropping the entry
# at that moment would let a third caller create a fresh lock and run
# concurrently with the queued one, silently breaking single-flight.
_locks: dict[str, tuple[asyncio.Lock, list[int]]] = {}


def lock_for(key: str) -> asyncio.Lock:
    """Get (or create) the lock guarding one cache key, and count this caller.

    Single-flight deduplication. Callers do not queue requests; they wait for
    the first to finish and then find the result already stored. Every call to
    this function must be paired with `release_lock`.
    """
    entry = _locks.get(key)
    if entry is None:
        entry = (asyncio.Lock(), [0])
        _locks[key] = entry
    entry[1][0] += 1
    return entry[0]


def release_lock(key: str) -> None:
    """Give up this caller's claim, dropping the entry when nobody is left."""
    entry = _locks.get(key)
    if entry is None:
        return

    lock, waiters = entry
    waiters[0] -= 1

    # Only drop it when no caller is left at all. Checking `not lock.locked()`
    # alone would be wrong: an unlocked lock can still have waiters queued.
    if waiters[0] <= 0 and not lock.locked():
        _locks.pop(key, None)


def clear_locks() -> None:
    """Forget all locks. Used by tests."""
    _locks.clear()


async def run_in_pool(func, *args):
    """Run a blocking helper in the threadpool.

    The single entry point for blocking work from async code, so there is exactly
    one style to explain and no route can accidentally call pymongo on the event
    loop.
    """
    return await run_in_threadpool(func, *args)