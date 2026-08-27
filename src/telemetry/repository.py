"""
Device registry lookup.

I/O only, mirrors src/intake/repository.py's/src/core/repository.py's
separation from pure logic: this module does the actual database work;
src/telemetry/trust.py stays the place the pure cryptographic check
and the "what does a miss mean" decision get made.

Returns None on no match -- same convention src/core/repository.py's
fetch_issuer_record/fetch_zone_record and src/intake/repository.py's
resolve_issuer/resolve_zone already use for "record does not exist".
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.telemetry.models import (
    DeviceRegistryEntry,
    SensorZoneStateAuditEntry,
    SensorZoneStateRejectionAuditEntry,
)


async def fetch_device_public_key(session: AsyncSession, device_id: str) -> Optional[str]:
    """Resolves device_id to its registered Ed25519 public key (PEM), or None if no registry row exists."""
    result = await session.execute(
        select(DeviceRegistryEntry.public_key).where(DeviceRegistryEntry.device_id == device_id)
    )
    return result.scalar_one_or_none()


async def persist_sensor_zone_state_record(session: AsyncSession, evidence: dict) -> None:
    """
    Appends a signed SensorZoneStateRecord to its own audit trail.
    Never updates or deletes existing rows — mirrors
    src/evidence/repository.py's persist_adjudication_record() and
    src/supervisor/repository.py's persist_override_record().
    """
    session.add(
        SensorZoneStateAuditEntry(
            zone_id=evidence["zone_id"],
            device_id=evidence["device_id"],
            record=evidence,
        )
    )
    await session.commit()


async def persist_sensor_zone_rejection_record(session: AsyncSession, evidence: dict) -> None:
    """
    Appends a signed SensorZoneStateRejectionRecord to its own audit
    trail — never updates or deletes existing rows, same discipline as
    persist_sensor_zone_state_record() above.
    """
    session.add(
        SensorZoneStateRejectionAuditEntry(
            zone_id=evidence["zone_id"],
            device_id=evidence["device_id"],
            reason_code=evidence["reason_code"],
            record=evidence,
        )
    )
    await session.commit()
