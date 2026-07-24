from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_APP_BASE_URL = "http://127.0.0.1:8007"


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://quizzy:quizzy@localhost:5432/quizzy"
    app_base_url: str = LOCAL_APP_BASE_URL
    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    default_difficulty: str = "intermediate"
    mcp_auth_enabled: bool = True
    mcp_bearer_token: str = ""
    mcp_allowed_origins: str = ""
    mcp_max_request_bytes: int = 2_000_000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def app_base_url_is_configured(self) -> bool:
        return "app_base_url" in self.model_fields_set


@lru_cache
def get_settings() -> Settings:
    return Settings()
