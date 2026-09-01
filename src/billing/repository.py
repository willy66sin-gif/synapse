"""
Persistence for billing-statement send records (Hamilton Labs
statement-of-accounts, 2026-09-01).

I/O only, mirrors src/airlock/repository.py's/src/evidence/repository.py's
separation from pure logic -- src/billing/statement.py stays the place
the pure "what does this period's statement look like" decision gets
made, src/billing/email_sender.py stays the place the pure-ish (one
external call) send attempt happens; this module does the actual
database write/read.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.models import BillingStatementAuditEntry


async def persist_billing_statement_record(session: AsyncSession, evidence: dict) -> None:
    """
    Appends a signed BillingStatementRecord to its own audit trail --
    never updates or deletes existing rows, same discipline as every
    other persist_*_record() in this codebase. Called for every send
    attempt regardless of outcome -- evidence["delivered"] is False
    for a failed attempt, never omitted.
    """
    session.add(
        BillingStatementAuditEntry(
            period_start=evidence["statement"]["period_start"],
            period_end=evidence["statement"]["period_end"],
            delivered=evidence["delivered"],
            record=evidence,
        )
    )
    await session.commit()


async def fetch_last_billing_statement_record(session: AsyncSession) -> Optional[dict]:
    """
    Most recently persisted BillingStatementRecord (delivered or not),
    or None if a statement has never been attempted. Used by
    src/billing/service.py's generate_and_send_if_due() to find the
    last billed period's end -- the next period starts there,
    regardless of whether the last attempt actually delivered.
    """
    result = await session.execute(
        select(BillingStatementAuditEntry).order_by(BillingStatementAuditEntry.id.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    return row.record if row is not None else None
