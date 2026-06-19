from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TMC Driver Docs"
    app_secret_key: str = "change-me-in-production"
    database_url: str = "sqlite:///./tmc.db"

    superadmin_username: str = "admin"
    superadmin_password: str = "admin123"

    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_collector_phone: str | None = None
    telegram_collector_session: str = "telegram_collector"
    telegram_collector_backend_url: str | None = None
    telegram_collector_require_connected_groups: bool = True
    telegram_ingest_token: str | None = None
    public_base_url: str = "http://127.0.0.1:8000"

    google_credentials_file: str = "credentials.json"
    google_credentials_json: str | None = None
    google_drive_root_folder_id: str | None = None
    google_drive_create_public_links: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
