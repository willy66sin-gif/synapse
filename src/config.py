"""
Environment & connection configuration.

No hardcoded credentials. Load from environment variables / .env at runtime.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/synapse"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
