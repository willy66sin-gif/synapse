"""
Persistence for signed evidence records — the "immutable audit
storage" half of CLAUDE.md's PostgreSQL role (the "rule registry"
half is src/core/repository.py's AuthorizedIssuer lookups).

Kept alongside src/evidence/emitter.py's pure signing logic, mirroring
how src/core/repository.py is kept separate from src/core/rules.py's
pure logic: signing stays pure and I/O-free, this module does the
actual database work.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.evidence.models import AdjudicationAuditEntry


async def persist_adjudication_record(session: AsyncSession, evidence: dict) -> None:
    """Appends a signed AdjudicationRecord to the audit trail. Never updates or deletes existing rows."""
    session.add(
        AdjudicationAuditEntry(
            claim_id=evidence["claim_id"],
            decision=evidence["decision"],
            record=evidence,
        )
    )
    await session.commit()


async def fetch_latest_adjudication_record(session: AsyncSession, claim_id: str) -> Optional[dict]:
    """Returns the most recently persisted evidence dict for this claim_id, or None if it was never adjudicated."""
    result = await session.execute(
        select(AdjudicationAuditEntry)
        .where(AdjudicationAuditEntry.claim_id == claim_id)
        .order_by(AdjudicationAuditEntry.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row.record if row is not None else None
