"""
Persistence for signed evidence records — the "immutable audit
storage" half of CLAUDE.md's PostgreSQL role (the "rule registry"
half is src/core/repository.py's AuthorizedIssuer lookups).

Kept alongside src/evidence/emitter.py's pure signing logic, mirroring
how src/core/repository.py is kept separate from src/core/rules.py's
pure logic: signing stays pure and I/O-free, this module does the
actual database work.
"""
from datetime import datetime
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


async def fetch_adjudication_records_in_range(
    session: AsyncSession, period_start: datetime, period_end: datetime
) -> list[dict]:
    """
    Every persisted AdjudicationRecord whose evaluated_at falls within
    [period_start, period_end) -- the source data
    src/billing/statement.py's generate_statement() (Hamilton Labs
    statement-of-accounts, 2026-09-01) consolidates into a billing
    statement.

    AdjudicationAuditEntry has no dedicated evaluated_at column (that
    timestamp lives only inside `record`'s JSON, per
    src/evidence/emitter.py's emit_evidence()) -- filtered here in
    Python after fetching every row, not a SQL WHERE clause, matching
    this table's existing "no dedicated timestamp column" shape rather
    than adding one as a side effect of this pass. Acceptable at this
    project's current stage (no production data volume, per
    CLAUDE.md's Developer Directives) -- would need a real column and
    an indexed WHERE clause if this table ever grows large.
    """
    result = await session.execute(select(AdjudicationAuditEntry).order_by(AdjudicationAuditEntry.id.asc()))
    rows = result.scalars().all()
    return [
        row.record
        for row in rows
        if period_start <= datetime.fromisoformat(row.record["evaluated_at"]) < period_end
    ]
