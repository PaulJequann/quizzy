from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://quizzy:quizzy@localhost:5432/quizzy"
    app_base_url: str = "http://127.0.0.1:8007"
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    default_difficulty: str = "intermediate"
    mcp_bearer_token: str = ""
    mcp_allowed_origins: str = ""
    mcp_max_request_bytes: int = 2_000_000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
