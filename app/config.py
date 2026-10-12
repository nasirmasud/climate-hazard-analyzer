"""Application settings, read from environment variables (or a local .env file).

Every value here has a safe default except the MongoDB URI, so a developer can
run the app with nothing but a .env file containing the connection string.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Connection string for the MongoDB Atlas cluster. Never commit a real one.
    #
    # The field names must match the keys in .env exactly. pydantic-settings
    # upper-cases the field name to find the variable, so `mongodb_uri` reads
    # MONGODB_URI. A mismatch does not raise: the unknown key is silently
    # ignored and this stays empty, so the app starts and only fails later at
    # connection time with a confusing "empty host" error.
    mongodb_uri: str = ""
    mongodb_db: str = "climate"

    # Only this origin may call the API from a browser.
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


settings = Settings()

if not settings.mongodb_uri:
    # Loud at import time, because the alternative is a confusing failure deep
    # inside pymongo on the first request rather than at startup. Phase 1 and 2
    # run without a database, so this is a warning, not an error.
    import logging

    logger = logging.getLogger("climate")
    logger.warning(
        "MONGODB_URI is not set. Caching is disabled and every request will "
        "recompute from Open-Meteo. Copy .env.example to .env to enable it."
    )
