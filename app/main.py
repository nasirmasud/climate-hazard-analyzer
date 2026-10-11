"""FastAPI application: CORS, shared error handling, router mounting.

Error bodies are normalised here so every failure looks the same to the
frontend, whatever raised it, and no stack trace ever reaches a client.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .routes import router

logger = logging.getLogger("climate")


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """The single error shape used for every failure: 422, 429, 500, 502."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


app = FastAPI(
    title="Climate Hazard Trend Analyzer",
    version="1.0.0",
    description="Heatwave trend analysis from Open-Meteo (ERA5) daily data.",
)

# Allow the deployed frontend, plus the Vite dev server.
allowed_origins = {settings.frontend_origin, "http://localhost:5173"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


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
        messages.append(f"{field}: {error['msg']}" if field else error["msg"])

    detail = " ".join(messages) or "Invalid request."
    # Strip pydantic's "Value error, " prefix, it adds noise for the user.
    detail = detail.replace("Value error, ", "")
    return error_response(422, "invalid_request", detail)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the real cause, return something safe."""
    logger.exception("Unhandled error on %s", request.url.path)
    return error_response(
        500, "internal_error", "Something went wrong on our side. Please try again."
    )


app.include_router(router, prefix="/api")
