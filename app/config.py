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

    # Only these origins may call the API from a browser. A comma-separated
    # list is accepted; see `frontend_origins`.
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

        The variable accepts a comma-separated list, because a machine commonly
        needs to allow both a deployed frontend and a local one, and reading
        the whole string as a single origin broke that silently: `main.py` put
        the raw value in the allowlist as one entry, so a browser never matched
        it and every request was rejected with no `Access-Control-Allow-Origin`
        header. The symptom looks exactly like a dead backend — the API works
        when opened in a tab, because a direct navigation sends no Origin
        header and CORS never applies.

        It worked by accident for `http://localhost:5173`, because the
        localhost test only inspects the start of the string and the
        development port sweep then happened to cover it. An unlisted origin
        stayed unreachable, and reordering the list so the local entry was not
        first would have broken local development too.

        This is a property rather than a second field so it cannot drift out of
        sync with the environment variable, and so a test can exercise the
        parsing without reimporting the settings module.
        """
        return {origin.strip() for origin in self.frontend_origin.split(",") if origin.strip()}

    @property
    def has_local_frontend(self) -> bool:
        """True when every configured frontend origin points at this machine.

        "Every", not "any", and that is the security-relevant detail. This
        property gates the development port sweep in `main.py`, so allowing a
        single real hostname to vote yes would enable it on a public deployment
        — letting any page running on a visitor's machine read this API's
        responses. A list mixing a deployed frontend with a local one therefore
        does *not* get the sweep; name the local port explicitly instead.
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
    # inside pymongo on the first request rather than at startup. Phase 1 and 2
    # run without a database, so this is a warning, not an error.
    import logging

    logger = logging.getLogger("climate")
    logger.warning(
        "MONGODB_URI is not set. Caching is disabled and every request will "
        "recompute from Open-Meteo. Copy .env.example to .env to enable it."
    )
