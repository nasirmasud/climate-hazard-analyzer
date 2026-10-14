"""Application settings, read from environment variables or a local .env file.

Every value has a safe default except the MongoDB URI, so the app runs with
nothing but a .env holding the connection string.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Connection string for the Atlas cluster. Never commit a real one.
    #
    # Field names must match the .env keys exactly: pydantic-settings upper-cases
    # the field name to find the variable, and a mismatch does not raise. The
    # unknown key is silently ignored and this stays empty, so the app starts and
    # fails later at connection time with a confusing "empty host" error.
    mongodb_uri: str = ""
    mongodb_db: str = "climate"

    # Only these origins may call the API from a browser. Accepts a
    # comma-separated list; see `frontend_origins`.
    frontend_origin: str = "http://localhost:5173"

    # Bump this whenever heatwave detection logic changes, so cached analyses
    # written by the old logic are never reused.
    logic_version: str = "v1"

    port: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def frontend_origins(self) -> set[str]:
        """`FRONTEND_ORIGIN` split into individual origins.

        Accepts a comma-separated list, because one machine commonly needs to
        allow both a deployed frontend and a local one. Reading the raw value as
        a single origin silently rejected every browser request rather than
        erroring, and the API still answered when opened in a tab, because a
        direct navigation sends no Origin header.

        A property rather than a second field, so it cannot drift from the
        environment variable and can be tested without reimporting settings.
        """
        return {origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()}

    @property
    def has_local_frontend(self) -> bool:
        """True when every configured frontend origin points at this machine.

        "Every", not "any", and that is the security-relevant detail. This gates
        the development port sweep in `main.py`, so a single real hostname voting
        yes would enable it on a public deployment and let any page on a
        visitor's machine read this API's responses.
        """
        origins = self.frontend_origins
        if not origins:
            return False
        return all(
            origin.startswith(("http://localhost", "http://127.0.0.1"))
            for origin in origins
        )


settings = Settings()

if not settings.mongodb_uri:
    # Loud at import time, because the alternative is a confusing failure deep
    # inside pymongo on the first request rather than at startup.
    import logging

    logger = logging.getLogger("climate")
    logger.warning(
        "MONGODB_URI is not set. Caching is disabled and every request will "
        "recompute from Open-Meteo. Copy .env.example to .env to enable it."
    )
