"""
Environment & connection configuration.

No hardcoded credentials. Load from environment variables / .env at runtime.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/synapse"
    redis_url: str = "redis://localhost:6379/0"
    # GO Freshness Phase 3a (2026-08-31, Willy-authorized): no
    # feature-flag naming convention existed anywhere in this codebase
    # before this field -- checked repo-wide, nothing matches
    # "_ENABLED"/"feature flag"/similar. This introduces that pattern:
    # a plain boolean Settings field, same style as database_url/
    # redis_url above (env-var driven via pydantic-settings' default
    # case-insensitive env-var binding -- PROFILE_ID_ENFORCEMENT_ENABLED
    # in the environment or .env sets this). Defaults False/off: a
    # manual switch Willy flips when ready, not a calculated date or
    # timer -- matches the already-locked GO Freshness Principle's
    # "context-bound, not time-bound" precedent. See
    # src/airlock/profile_check.py for what changes when this flips.
    profile_id_enforcement_enabled: bool = False


settings = Settings()
