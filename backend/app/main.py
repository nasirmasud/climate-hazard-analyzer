"""FastAPI application: CORS, shared error handling, router mounting.

Error bodies are normalised here so every failure looks the same to the frontend
and no stack trace ever reaches a client.
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

    Boot-time means a slow first request is avoided and an unreachable database
    is discovered at boot rather than on a user's first click. `ensure_indexes`
    swallows connection errors deliberately: the app is still useful without a
    cache, so an outage must degrade performance rather than stop startup.
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
# FRONTEND_ORIGIN, because reading the raw string as one allowlist entry matched
# nothing and silently rejected every browser request.
#
# Named rather than kept local so tests can assert what was configured instead
# of inferring it from a .env that is deliberately not committed.
_allowed = set(settings.frontend_origins)

# In development, allow any localhost port rather than a fixed 5173, because Vite
# takes the next free one and a hard-coded port silently breaks the preflight.
#
# Gated on every configured origin being local: a deployed app is public, and
# allowing localhost there would let any page on a user's machine read its
# responses. The surprise worth knowing is that listing a deployed origin beside
# a local one switches this sweep off, so a developer running both against one
# backend has to name the exact port they use.
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
# its exception handler. The two budgets in ratelimit.py are counted separately
# and deliberately.
app.state.limiter = ratelimit.limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Turn a slowapi rejection into the same error shape as everything else.

    The framework default would return `{"detail": ...}`, forcing the frontend to
    special-case 429.
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

    A `model_validator` failure arrives as the whole summary string: a count
    header, a `[type=..., input_value={...}]` trailer and a documentation URL.
    The `input_value` dump matters most, because it would echo the user's whole
    request back at them. What remains is the sentence the validator wrote.
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
    handled above, but a `model_validator` raising `ValueError` propagates as a
    bare `ValueError` and bypasses that handler, landing in the generic 500
    below. Every rule in check_location and check_years would then report a
    server failure for input the user simply got wrong.

    These messages are written for a person and shown verbatim in the UI, so they
    pass straight through.
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
