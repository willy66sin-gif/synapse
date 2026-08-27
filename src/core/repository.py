"""
Data access for the Core adjudication engine.

This is the *only* place ACTIVE_ZONES / AUTHORIZED_ISSUERS-equivalent
state resolution happens. synapse_mdm.py hardcoded both as Python
dicts "for reference/testing only" — here issuer/authority records
come from PostgreSQL (SQLAlchemy Async) and zone state comes from
Redis, per CLAUDE.md's stack.

Deliberately kept separate from src/core/rules.py and
src/core/evaluator.py: those stay pure functions over already-fetched
records, this module does the actual I/O. FastAPI dependency
injection (get_db_session / get_redis_client) lets tests override both
with fakes, so route-level tests never need a live database or cache.
"""
from typing import AsyncIterator, Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.core.models import AuthorizedIssuer, IssuerRole
from src.core.roles import AuthorityRoleType
from src.core.rules import SENSOR_ELIGIBLE_ZONE_FIELDS, IssuerRecord, ZoneRecord, sensor_zone_redis_key

_engine = create_async_engine(settings.database_url)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

_redis_client: Optional[Redis] = None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a scoped Async SQLAlchemy session."""
    async with _session_factory() as session:
        yield session


async def get_redis_client() -> AsyncIterator[Redis]:
    """FastAPI dependency: yields the shared async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield _redis_client


async def fetch_issuer_record(session: AsyncSession, issuer_id: str) -> Optional[IssuerRecord]:
    """
    Real replacement for synapse_mdm.py's AUTHORIZED_ISSUERS lookup.
    Returns None if the issuer has no authority record on file — the
    same "unauthenticated" case check_authority() already handles.
    """
    result = await session.execute(
        select(AuthorizedIssuer).where(AuthorizedIssuer.issuer_id == issuer_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return IssuerRecord(role=row.role, clearance_level=row.clearance_level)


async def fetch_issuer_roles(session: AsyncSession, issuer_id: str) -> list[AuthorityRoleType]:
    """
    Read-only access to src/core/models.py's IssuerRole (2026-08-06,
    IssuerRole migration). Returns every role_type on file for
    issuer_id -- zero, one, or several, since an issuer may hold
    multiple independently-accredited roles. Not called from
    check_authority()/adjudicate() yet, and not populated with any
    real data yet -- this is read-access infrastructure only, so the
    table is genuinely queryable/testable; wiring it into adjudication
    logic is a separate, not-yet-decided task (see CLAUDE.md's Open
    Items).
    """
    result = await session.execute(
        select(IssuerRole.role_type).where(IssuerRole.issuer_id == issuer_id)
    )
    return list(result.scalars().all())


def _resolve_zone_field(field: str, sensor_data: dict, human_data: dict) -> Optional[str]:
    """
    Sensor/human precedence (src/core/rules.py's
    SENSOR_ELIGIBLE_ZONE_FIELDS): the sensor-sourced value wins when
    the field is sensor-eligible and a verified sensor has actually
    written it; otherwise falls back to the human-declared value —
    identical to today's behavior for any field not in that set (e.g.
    hazard_level), and for any sensor-eligible field a sensor has never
    written for this zone.
    """
    if field in SENSOR_ELIGIBLE_ZONE_FIELDS and field in sensor_data:
        return sensor_data[field]
    return human_data.get(field)


async def fetch_zone_record(redis_client: Redis, zone_id: str) -> Optional[ZoneRecord]:
    """
    Real replacement for synapse_mdm.py's ACTIVE_ZONES lookup. Zone
    state lives in Redis as a human-declared hash at key `zone:{zone_id}`
    with fields `hazard_level` and `active_crane` ("true"/"false") —
    written by scripts/seed_dev_data.py — plus, as of the 2026-08-27
    telemetry-ingestion-pathway build, an optional verified-telemetry
    hash at src/core/rules.py's sensor_zone_redis_key(zone_id).

    A zone's existence is still determined by the human-declared hash
    alone (empty -> None, the same "zone does not exist" case
    check_zone_safety() already handles) — a sensor has never been the
    sole source of truth for a zone's hazard_level, which has no
    sensor source at all. For each field in SENSOR_ELIGIBLE_ZONE_FIELDS,
    the sensor hash's value is used when present (precedence: verified
    sensor over human declaration); otherwise the human-declared value
    is used, unchanged from before this build.
    """
    human_data = await redis_client.hgetall(f"zone:{zone_id}")
    if not human_data:
        return None

    sensor_data = await redis_client.hgetall(sensor_zone_redis_key(zone_id))

    return ZoneRecord(
        hazard_level=_resolve_zone_field("hazard_level", sensor_data, human_data),
        active_crane=_resolve_zone_field("active_crane", sensor_data, human_data) == "true",
    )
