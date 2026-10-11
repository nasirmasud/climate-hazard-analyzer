"""Application settings, read from environment variables (or a local .env file).

Every value here has a safe default except the MongoDB URI, so a developer can
run the app with nothing but a .env file containing the connection string.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Connection string for the MongoDB Atlas cluster. Never commit a real one.
    mongo_uri: str = ""
    mongo_db: str = "climate"

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
