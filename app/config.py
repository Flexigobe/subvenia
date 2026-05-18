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
