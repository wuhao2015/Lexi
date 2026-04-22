from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: Optional[str] = None  # default: backend/data/lexi.db
    jwt_secret: str = "change-me-in-production-use-long-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Ordered fallback list; first model is tried first.
    gemini_models: str = "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.5-pro,gemini-3-flash-preview"

    gemini_daily_verify_limit: int = 0  # 0 = no proactive cap
    verify_cooldown_seconds: int = 3600

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def preferred_gemini_models(self) -> list[str]:
        models = [m.strip() for m in self.gemini_models.split(",") if m.strip()]
        if self.gemini_model and self.gemini_model not in models:
            models.insert(0, self.gemini_model)
        if not models and self.gemini_model:
            models = [self.gemini_model]
        return models


@lru_cache
def get_settings() -> Settings:
    return Settings()
