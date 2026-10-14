"""Rate limiting, in two tiers.

A single limiter in front of the route cannot enforce both budgets, because
whether a request is cached is only known *after* the lookup, and a limiter
decorator runs before that.

* **`GENERAL_LIMIT`** applies to every analysis request, bounding the work the
  server will do.
* **`UPSTREAM_LIMIT`** is checked at the point of an Open-Meteo call, so a
  returning user reading from the cache never spends it. Spending it would make
  the cache pointless: the second visitor should not be charged for the first
  visitor's fetch.

Both are sliding windows, which avoids the boundary burst a fixed window allows:
a fixed window lets a client spend its whole allowance at 11:59:59 and again at
12:00:00. Storage is in-process memory, which resets on restart and is not shared
across replicas, and the README says so rather than implying a distributed
guarantee.

The counters come from `limits`, which slowapi is built on, so there is one
storage backend and one strategy rather than two layered on top of each other.
"""

import logging
import time
from dataclasses import dataclass

from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger("climate")

# Applied to every analysis request, cached or not.
GENERAL_LIMIT = "60 per minute"

# Applied only when a request is about to call Open-Meteo. Deliberately much
# stricter: each of these costs an upstream request and roughly a second of
# this process's CPU.
UPSTREAM_LIMIT = "6 per minute"

# Nominatim's usage policy caps clients at one request per second, so 60/min is
# their ceiling rather than a policy of ours. Keeping it separate stops a burst
# of place lookups starving the weather fetch, and vice versa.
PLACE_LIMIT = "60 per minute"

# Read endpoints are pure reads, so a short shared cache is safe and saves the
# browser a round trip. 300 seconds matches the frontend's staleTime.
CACHE_CONTROL_MAX_AGE = 300

_storage = MemoryStorage()
_counter = MovingWindowRateLimiter(_storage)

_general_item = parse(GENERAL_LIMIT)
_upstream_item = parse(UPSTREAM_LIMIT)
_place_item = parse(PLACE_LIMIT)

# Attached to the app so slowapi's decorators and exception handler work. Its own
# counters are unused; the two budgets above need finer control than a decorator
# allows.
limiter = Limiter(key_func=get_remote_address, default_limits=[])


@dataclass(frozen=True)
class Decision:
    """Whether a request may proceed, and if not, for how long it must wait."""

    allowed: bool
    retry_after: int = 0
    limit: str = ""


def _decision(item, key: str, limit_text: str) -> Decision:
    """Check the budget without spending it.

    `test()` rather than `hit()`: the general budget is checked on every request
    but charged once, and the upstream budget is charged at the fetch. `test()`
    reports whether a hit would succeed without recording one.

    Two `limits` API traps avoided here, both of which fail confusingly if used
    by name: `item.amount` is a plain int, not a method, and `WindowStats.count`
    is the namedtuple's own `tuple.count`. `remaining` is the field to read.
    """
    if _counter.test(item, key):
        return Decision(allowed=True, limit=limit_text)

    stats = _counter.get_window_stats(item, key)
    # Never advertise zero: a client that retries immediately would spin.
    retry_after = max(int(stats.reset_time - time.time()), 1)
    return Decision(allowed=False, retry_after=retry_after, limit=limit_text)


def check_general(key: str) -> Decision:
    """Whether the per-IP budget has room, without spending it."""
    return _decision(_general_item, key, GENERAL_LIMIT)


def spend_general(key: str) -> Decision:
    """Charge the per-IP budget for one analysis request.

    Charged rather than merely checked, because every request costs real work
    on this process even when it is answered from the cache.
    """
    if _counter.hit(_general_item, key):
        return Decision(allowed=True, limit=GENERAL_LIMIT)

    stats = _counter.get_window_stats(_general_item, key)
    retry_after = max(int(stats.reset_time - time.time()), 1)
    logger.warning(
        "General rate limit reached for %s; refusing for %ds", key, retry_after
    )
    return Decision(allowed=False, retry_after=retry_after, limit=GENERAL_LIMIT)


def spend_upstream(key: str) -> Decision:
    """Charge the upstream budget, if one is still available.

    Called immediately before an Open-Meteo request, so only real fetches are
    charged. Returns a Decision rather than raising, so the caller decides what
    a refusal looks like.
    """
    allowed = _counter.hit(_upstream_item, key)
    if allowed:
        return Decision(allowed=True, limit=UPSTREAM_LIMIT)

    stats = _counter.get_window_stats(_upstream_item, key)
    retry_after = max(int(stats.reset_time - time.time()), 1)
    logger.warning(
        "Upstream rate limit reached for %s; refusing fetch for %ds", key, retry_after
    )
    return Decision(allowed=False, retry_after=retry_after, limit=UPSTREAM_LIMIT)


def spend_place(key: str) -> Decision:
    """Charge the place-name budget for one Nominatim lookup.

    Separate from the weather budget because they are different services on
    different clocks, and a user clicking around the map must not be able to
    exhaust the allowance that protects the Open-Meteo fetch.
    """
    if _counter.hit(_place_item, key):
        return Decision(allowed=True, limit=PLACE_LIMIT)

    stats = _counter.get_window_stats(_place_item, key)
    retry_after = max(int(stats.reset_time - time.time()), 1)
    logger.warning("Place-name rate limit reached for %s", key)
    return Decision(allowed=False, retry_after=retry_after, limit=PLACE_LIMIT)


def reset() -> None:
    """Clear all budgets. Used by tests."""
    _storage.reset()


def client_key(request) -> str:
    """Identify the caller.

    The remote address is the primary key. `X-Forwarded-For` is consulted first
    only when the app is behind a proxy, because behind one every request would
    otherwise appear to come from the proxy's own address and share one budget.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # The left-most entry is the original client.
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate
    return get_remote_address(request)