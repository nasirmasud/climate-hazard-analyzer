"""FastAPI application: CORS, shared error handling, router mounting.

Error bodies are normalised here so every failure looks the same to the
frontend, whatever raised it, and no stack trace ever reaches a client.
"""

import logging

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from . import db, ratelimit
from .config import settings
from .routes import router

logger = logging.getLogger("climate")


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """The single error shape used for every failure: 422, 429, 500, 502."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the cache indexes once, at boot, and close the pool on shutdown.

    Doing this at startup rather than lazily means a slow first request is
    avoided and an unreachable database is discovered at boot instead of on a
    user's first click. `ensure_indexes` swallows connection errors on purpose:
    the app is still useful without a cache, so a cache outage must degrade
    performance rather than stop the app starting.
    """
    await run_in_threadpool(db.ensure_indexes)
    yield
    await run_in_threadpool(db.close_client)


app = FastAPI(
    title="Climate Hazard Trend Analyzer",
    version="1.0.0",
    description="Heatwave trend analysis from Open-Meteo (ERA5) daily data.",
    lifespan=lifespan,
)

# The deployed frontends, always. `frontend_origins` splits a comma-separated
# FRONTEND_ORIGIN, because reading the raw string as one allowlist entry silently
# matched nothing at all and every browser request was rejected.
#
# Named rather than kept local so tests can assert what was actually configured
# instead of inferring it from a .env file that is deliberately not committed.
_allowed = set(settings.frontend_origins)

# In development, allow any localhost port rather than a fixed 5173.
#
# Vite takes the next free port when 5173 is already taken, so a hard-coded port
# means any stray dev server silently breaks the app: the preflight gets no
# Access-Control-Allow-Origin, `fetch` rejects, and the UI reports "backend
# unreachable" while the API is perfectly healthy. That failure is confusing
# because opening the API URL in a tab still works — a direct navigation sends
# no Origin header, so CORS never applies to it.
#
# This only applies when every configured frontend origin is itself local, so a
# deployed instance still refuses every origin except the real ones. A deployed
# app is public, and allowing localhost there would let any page running on a
# user's machine read its responses.
#
# The trade-off is worth stating plainly, because it surprises people: listing a
# deployed origin alongside a local one on the same instance switches the sweep
# off. A developer running both against one backend therefore has to name the
# exact port they use.
if settings.has_local_frontend:
    _allowed |= {f"http://localhost:{port}" for port in range(5173, 5192)}
    _allowed |= {f"http://127.0.0.1:{port}" for port in range(5173, 5192)}

ALLOWED_ORIGINS = sorted(_allowed)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# slowapi needs its limiter reachable from app.state for its own decorators and
# for the middleware below. The two budgets in ratelimit.py are counted
# separately and deliberately.
app.state.limiter = ratelimit.limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Turn a slowapi rejection into the same error shape as everything else.

    Task 89: one error body across 422, 429, 500 and 502. Without this the
    framework's default would return `{"detail": ...}` and the frontend would
    have to special-case 429.
    """
    return error_response(429, "rate_limited", "Too many requests.")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Turn Pydantic's error list into one readable sentence.

    The frontend shows this message directly, so it is written for a person
    rather than for a developer.
    """
    messages = []
    for error in exc.errors():
        field = ".".join(str(p) for p in error["loc"] if p not in ("query", "body"))
        msg = _clean_validation_message(str(error["msg"]))

        messages.append(f"{field}: {msg}" if field else msg)

    detail = " ".join(m for m in messages if m) or "Invalid request."
    return error_response(422, "invalid_request", detail)


def _clean_validation_message(raw: str) -> str:
    """Reduce Pydantic's error text to the sentence a person should read.

    A `model_validator` failure arrives as the whole summary string:

        "1 validation error for AnalysisRequest\\n  Value error, Unknown region
         'london'. [type=value_error, input_value={...}, input_type=dict]\\n
         For further information visit https://errors.pydantic.dev/..."

    Three things have to go: the count header, the trailing type/input/URL
    metadata, and the `input_value` dump — which would otherwise echo the user's
    whole request back at them. What remains is the sentence the validator
    wrote on purpose.
    """
    text = raw.strip()

    # Drop the leading "N validation error(s) for <Model>" header line.
    if text.startswith(("1 validation error for ", "2 validation errors for ")):
        text = text.split("\n", 1)[-1]

    # Drop trailing metadata: everything from the bracketed type marker on, and
    # the "For further information visit ..." footer.
    text = text.split(" [type=", 1)[0]
    text = text.split("For further information visit", 1)[0]

    # What is left may still carry pydantic's own prefix.
    text = text.replace("Value error, ", "")

    return " ".join(text.split()).strip()


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Catch ValueError raised inside a Pydantic model validator.

    FastAPI's own validation errors arrive as `RequestValidationError` and are
    handled above. But a `model_validator` that raises `ValueError` propagates
    as a bare `ValueError`, bypassing that handler entirely and landing in the
    generic 500 below.

    The effect was that every rule in `AnalysisRequest.check_location` and
    `check_years` — oversized box, reversed years, unknown region, year span
    too short — returned "Something went wrong on our side" with a 500, telling
    the user the server had failed when in fact their input was rejected. That
    is exactly the message a client-side validation bug is supposed to prevent
    showing.

    These messages are written for a person and are shown verbatim in the UI, so
    they pass straight through.
    """
    message = _clean_validation_message(str(exc)) or "Invalid request."
    logger.info("Rejected invalid request to %s: %s", request.url.path, message)
    return error_response(422, "invalid_request", message)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the real cause, return something safe."""
    logger.exception("Unhandled error on %s", request.url.path)
    return error_response(
        500, "internal_error", "Something went wrong on our side. Please try again."
    )


app.include_router(router, prefix="/api")
