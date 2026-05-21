"""Settings centralizados leídos del entorno."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


# Railway crea SQLALCHEMY_URL por defecto al añadir Postgres; nuestro código lee
# DATABASE_URL. Si la primera está y la segunda no, hacemos el alias antes de que
# pydantic-settings construya Settings.
if "DATABASE_URL" not in os.environ and "SQLALCHEMY_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["SQLALCHEMY_URL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones"

    @field_validator("database_url", mode="before")
    @classmethod
    def _ensure_psycopg_driver(cls, v: str) -> str:
        # Railway / Heroku-style URLs come as "postgresql://..." or "postgres://...".
        # SQLAlchemy with psycopg3 needs "postgresql+psycopg://".
        if isinstance(v, str):
            if v.startswith("postgres://"):
                v = "postgresql://" + v[len("postgres://") :]
            if v.startswith("postgresql://"):
                v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

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

    # LLM scoring provider — "gemini" o "anthropic" (default: anthropic si hay key)
    llm_provider: str = "auto"  # auto: anthropic si hay key, sino gemini

    # Gemini LLM scoring
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Anthropic Claude LLM scoring (alternativa a Gemini)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"  # Haiku 4.5: rápido y barato

    # Brevo (transactional email)
    brevo_api_key: str = ""
    alert_from_email: str = "alertas@flexigobe.com"
    alert_admin_email: str = ""  # if set, system alerts (sync failures) go here

    # Admin panel (HTTP Basic). Si admin_pass='' el panel queda deshabilitado en prod.
    # En dev, se autogenera al arrancar si está vacío (ver app/main.py lifespan).
    admin_user: str = ""
    admin_pass: str = ""

    # Rate limiting (POST /search only, sliding window 1h)
    rate_limit_per_hour: int = 60

    # SEO / analytics (all optional, all without cookies)
    plausible_domain: str = ""  # e.g. "subvenciones.flexigobe.com" — if set, Plausible script is included
    plausible_src: str = "https://plausible.io/js/script.js"  # change if self-hosted
    seo_canonical_origin: str = ""  # e.g. "https://subvenciones.flexigobe.com" — used for absolute URLs in sitemap + canonical


@lru_cache
def get_settings() -> Settings:
    return Settings()
