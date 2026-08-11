"""
Certified profile lookup.

I/O only, mirrors src/telemetry/repository.py's/src/intake/repository.py's
separation from pure logic: this module does the actual database work;
src/core/profile_resolution.py stays the place the pure merge/
consistency logic runs, over records this module has already fetched.

Returns None on no match — same convention src/core/repository.py's
fetch_issuer_record/fetch_zone_record and src/telemetry/repository.py's
fetch_device_public_key already use for "record does not exist".

A BASE_ANNEX profile's base is fetched with a second, ordinary call to
this same function (base_profile_id is just another profile_id in this
table — see src/profiles/models.py's module docstring) — there is no
separate "fetch the base" function.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.profiles.models import CertifiedProfileRecord
from src.profiles.schemas import BaseProfileRef, CertifiedProfile


async def fetch_certified_profile(session: AsyncSession, profile_id: str) -> Optional[CertifiedProfile]:
    """Resolves profile_id to its CertifiedProfile, or None if no registry row exists."""
    result = await session.execute(
        select(CertifiedProfileRecord).where(CertifiedProfileRecord.profile_id == profile_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None

    base_ref = None
    if row.base_profile_id is not None:
        base_ref = BaseProfileRef(
            base_profile_id=row.base_profile_id,
            base_profile_version=row.base_profile_version,
        )

    return CertifiedProfile(
        profile_id=row.profile_id,
        jurisdiction_code=row.jurisdiction_code,
        version=row.version,
        lineage=row.lineage,
        base_ref=base_ref,
        parameters=row.parameters,
    )
