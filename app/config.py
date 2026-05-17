"""Settings centralizados leídos del entorno."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones"

    # App
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # BDNS
    bdns_base_url: str = "https://www.infosubvenciones.es/bdnstrans/api"
    bdns_page_size: int = 100
    bdns_sync_hour: int = 3  # 03:00
    bdns_sync_minute: int = 0

    # Matching
    matching_candidate_limit: int = 30

    # Gemini LLM scoring
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"  # 1500 RPD free tier vs 250 RPD on 2.5-flash


@lru_cache
def get_settings() -> Settings:
    return Settings()
