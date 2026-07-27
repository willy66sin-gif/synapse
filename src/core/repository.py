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
from src.core.models import AuthorizedIssuer
from src.core.rules import IssuerRecord, ZoneRecord

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


async def fetch_zone_record(redis_client: Redis, zone_id: str) -> Optional[ZoneRecord]:
    """
    Real replacement for synapse_mdm.py's ACTIVE_ZONES lookup. Zone
    state lives in Redis as a hash at key `zone:{zone_id}` with fields
    `hazard_level` and `active_crane` ("true"/"false"). Returns None if
    the zone has no state cached — the same "zone does not exist" case
    check_zone_safety() already handles.
    """
    data = await redis_client.hgetall(f"zone:{zone_id}")
    if not data:
        return None
    return ZoneRecord(
        hazard_level=data["hazard_level"],
        active_crane=data.get("active_crane") == "true",
    )
