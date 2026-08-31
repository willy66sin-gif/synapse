"""
Persistence for Airlock-stage profile_id rejection records.

I/O only, mirrors src/evidence/repository.py's/src/telemetry/repository.py's
separation from pure logic -- src/airlock/profile_check.py stays the
place the pure "does this claim satisfy the profile_id requirement"
decision gets made; this module does the actual database write.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.models import ProfileRejectionAuditEntry


async def persist_profile_rejection_record(session: AsyncSession, evidence: dict) -> None:
    """
    Appends a signed ProfileRejectionRecord to its own audit trail --
    never updates or deletes existing rows, same discipline as
    src/evidence/repository.py's persist_adjudication_record() and
    src/telemetry/repository.py's persist_sensor_zone_rejection_record().
    """
    session.add(
        ProfileRejectionAuditEntry(
            claim_id=evidence["claim_id"],
            profile_id=evidence["profile_id"],
            reason_code=evidence["reason_code"],
            record=evidence,
        )
    )
    await session.commit()
