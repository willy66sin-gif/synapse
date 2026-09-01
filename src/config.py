"""
Environment & connection configuration.

No hardcoded credentials. Load from environment variables / .env at runtime.
"""
from typing import Optional

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

    # Hamilton Labs statement-of-accounts billing module (2026-09-01):
    # SMTP credentials and the recipient address are configuration
    # only, same "no hardcoded credentials" rule this file already
    # states at the top -- never a literal string anywhere in
    # src/billing/. No defaults on any of these (None, not a fake
    # placeholder) so src/billing/email_sender.py's fail-closed check
    # has something real to detect: "not configured" must raise, never
    # silently skip the send or silently report success with nothing
    # sent. smtp_username/smtp_password stay Optional even once the
    # others are set -- some SMTP relays genuinely require no auth,
    # so treating an unauthenticated relay as "misconfigured" would be
    # wrong, not fail-closed.
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    billing_statement_sender: Optional[str] = None
    billing_statement_recipient: Optional[str] = None
    # Billing-period cadence in days -- configurable per this pass's
    # own instruction ("make the cadence configurable, not
    # hardcoded"), same env-var-driven Settings-field pattern as
    # profile_id_enforcement_enabled above. Consulted by
    # src/billing/service.py's is_period_due(); 30 is a reasonable
    # default (a calendar-month-ish billing period), not a hardcoded
    # constant baked into the logic itself.
    billing_statement_cadence_days: int = 30


settings = Settings()
