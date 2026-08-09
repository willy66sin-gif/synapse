"""
Identity crosswalk lookups.

I/O only, mirrors src/core/repository.py's separation from
src/core/rules.py's pure logic: this module does the actual database
work; src/intake/adapters/*.py stays the place mapping *decisions*
(which lookup to call, what a miss means for the claim) get made.

Returns None on no match -- same convention src/core/repository.py's
fetch_issuer_record/fetch_zone_record already use for "record does not
exist" -- so a crosswalk miss reads the same way those misses already
do, rather than introducing a second convention for the same kind of
absence.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.intake.models import ExternalIdType, IdentityCrosswalkEntry


async def _resolve(
    session: AsyncSession, source_system: str, external_id: str, id_type: ExternalIdType
) -> Optional[str]:
    result = await session.execute(
        select(IdentityCrosswalkEntry.synapse_id).where(
            IdentityCrosswalkEntry.source_system == source_system,
            IdentityCrosswalkEntry.external_id == external_id,
            IdentityCrosswalkEntry.external_id_type == id_type,
        )
    )
    return result.scalar_one_or_none()


async def resolve_issuer(session: AsyncSession, source_system: str, external_id: str) -> Optional[str]:
    """Resolves an external issuer identifier to Synapse's issuer_id, or None if no crosswalk row exists."""
    return await _resolve(session, source_system, external_id, ExternalIdType.ISSUER)


async def resolve_zone(session: AsyncSession, source_system: str, external_id: str) -> Optional[str]:
    """Resolves an external zone/location identifier to Synapse's zone_id, or None if no crosswalk row exists."""
    return await _resolve(session, source_system, external_id, ExternalIdType.ZONE)
