"""
Persistence and lookup for RegulatoryInstrument records and their
creation-event evidence (Step 1, 2026-10-11).

I/O only, mirrors src/doctrine/repository.py's separation from pure
logic -- src/regulatory/router.py stays the place receipt_timestamp
and the derived day-counts get decided, this module does the actual
database work.

Reads return plain dicts, not RegulatoryInstrument: that schema
rejects a non-null receipt_timestamp by design (it is the *creation*
shape), so a stored row -- which always has one -- cannot be
represented by it.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.regulatory.models import RegulatoryInstrumentReceiptAuditEntry, RegulatoryInstrumentRecord


def _row_to_dict(row: RegulatoryInstrumentRecord) -> dict:
    return {
        "instrument_id": row.instrument_id,
        "issuing_authority": row.issuing_authority,
        "reference_number": row.reference_number,
        "publish_date": row.publish_date.isoformat(),
        "effective_date": row.effective_date.isoformat(),
        "grounding_category": row.grounding_category.value,
        "receipt_timestamp": row.receipt_timestamp.isoformat(),
    }


async def persist_regulatory_instrument(session: AsyncSession, instrument: dict) -> None:
    """
    Inserts one row into the regulatory_instruments registry table.
    instrument is a plain dict (RegulatoryInstrument.model_dump(), plus
    the server-set receipt_timestamp), same decoupling as
    src/doctrine/repository.py's persist_doctrine_submission().
    """
    session.add(
        RegulatoryInstrumentRecord(
            instrument_id=instrument["instrument_id"],
            issuing_authority=instrument["issuing_authority"],
            reference_number=instrument["reference_number"],
            publish_date=instrument["publish_date"],
            effective_date=instrument["effective_date"],
            grounding_category=instrument["grounding_category"],
            receipt_timestamp=instrument["receipt_timestamp"],
        )
    )
    await session.commit()


async def persist_regulatory_instrument_receipt(session: AsyncSession, evidence: dict) -> None:
    """Appends a hashed RegulatoryInstrumentReceiptRecord to its own audit trail -- never updates or deletes existing rows."""
    session.add(
        RegulatoryInstrumentReceiptAuditEntry(
            instrument_id=evidence["instrument"]["instrument_id"],
            issuing_authority=evidence["instrument"]["issuing_authority"],
            record=evidence,
        )
    )
    await session.commit()


async def fetch_regulatory_instrument(session: AsyncSession, instrument_id: str) -> Optional[dict]:
    """Resolves instrument_id to its stored instrument, or None if no registry row exists."""
    result = await session.execute(
        select(RegulatoryInstrumentRecord).where(RegulatoryInstrumentRecord.instrument_id == instrument_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _row_to_dict(row)


async def list_regulatory_instruments(
    session: AsyncSession,
    issuing_authority: Optional[str] = None,
    reference_number: Optional[str] = None,
) -> list[dict]:
    """
    Every stored instrument, optionally narrowed by exact
    issuing_authority and/or reference_number match. Exact match only,
    no pattern or free-text search. Ordered by receipt, then
    instrument_id, so the same registry contents always list in the
    same order.
    """
    stmt = select(RegulatoryInstrumentRecord)
    if issuing_authority is not None:
        stmt = stmt.where(RegulatoryInstrumentRecord.issuing_authority == issuing_authority)
    if reference_number is not None:
        stmt = stmt.where(RegulatoryInstrumentRecord.reference_number == reference_number)
    stmt = stmt.order_by(RegulatoryInstrumentRecord.receipt_timestamp, RegulatoryInstrumentRecord.instrument_id)

    result = await session.execute(stmt)
    return [_row_to_dict(row) for row in result.scalars().all()]
