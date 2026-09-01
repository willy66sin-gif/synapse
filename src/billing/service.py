"""
Statement-of-accounts orchestration: dual trigger wiring for the
Hamilton Labs billing statement (2026-09-01).

Two triggers, one shared "is a statement due" decision
(is_period_due()) and one shared generate-sign-persist-send pipeline
(generate_and_send_if_due()) -- avoids two independently-drifting
copies of the same logic, per this pass's own requirements:

  - Scheduled: src/billing/scheduler.py's standalone loop calls
    run_scheduled_check() on a poll interval, wall-clock-driven, so a
    statement still goes out even during a stretch with zero claim
    traffic.
  - Event-triggered: src/airlock/router.py's submit_claim() and
    src/frontline/router.py's frontline_status_json() both call
    on_claim_finalized() immediately after persisting a new
    AdjudicationRecord -- i.e. exactly when a real claim-outcome
    record finalizes, per this pass's own wording. Uses the identical
    is_period_due() check, so an active system sends its statement the
    moment a period elapses rather than waiting for the next scheduled
    poll.

Both call sites share generate_and_send_if_due(), which:
  1. Looks up the last attempted BillingStatementRecord (any delivered
     state) to find the last period's end -- the next period starts
     there, or (if no statement has ever been attempted)
     cadence_days before now.
  2. If cadence_days haven't elapsed since that period_end, does
     nothing and returns None -- not due yet, no evidence emitted for
     a no-op.
  3. If due, fetches AdjudicationRecords for [period_start, period_end)
     (src/evidence/repository.py's fetch_adjudication_records_in_range()),
     builds the statement (src/billing/statement.py's
     generate_statement(), pure), attempts delivery
     (src/billing/email_sender.py's send_statement_email()), and --
     regardless of success or failure -- signs and persists a
     BillingStatementRecord for the attempt (this pass's own "every
     send... including failed-send attempts" instruction). A
     BillingConfigIncompleteError from send_statement_email() is
     itself recorded as a failed-send evidence record, not left to
     propagate and silently drop the attempt from the audit trail.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.billing.email_sender import BillingConfigIncompleteError, send_statement_email
from src.billing.repository import fetch_last_billing_statement_record, persist_billing_statement_record
from src.billing.statement import generate_statement
from src.config import Settings
from src.config import settings as default_settings
from src.evidence.emitter import emit_billing_statement_evidence
from src.evidence.repository import fetch_adjudication_records_in_range


def is_period_due(last_period_end: Optional[datetime], now: datetime, cadence_days: int) -> bool:
    """
    Pure decision, testable without real waiting. True when no
    statement has ever been attempted (last_period_end is None -- the
    very first period is always due) or when cadence_days have
    elapsed since the last attempted period's end.
    """
    if last_period_end is None:
        return True
    return now >= last_period_end + timedelta(days=cadence_days)


async def generate_and_send_if_due(
    session: AsyncSession, settings: Settings = default_settings, now: Optional[datetime] = None
) -> Optional[dict]:
    """
    Shared by both triggers -- see this module's own docstring.
    Returns the persisted BillingStatementRecord evidence dict if a
    statement was attempted this call, or None if nothing was due.
    """
    now = now or datetime.now(timezone.utc)

    last_record = await fetch_last_billing_statement_record(session)
    last_period_end = (
        datetime.fromisoformat(last_record["statement"]["period_end"]) if last_record is not None else None
    )

    if not is_period_due(last_period_end, now, settings.billing_statement_cadence_days):
        return None

    period_start = last_period_end or (now - timedelta(days=settings.billing_statement_cadence_days))
    period_end = now

    records = await fetch_adjudication_records_in_range(session, period_start, period_end)
    statement = generate_statement(records, period_start, period_end, settings.billing_statement_recipient)

    try:
        result = send_statement_email(statement, settings)
    except BillingConfigIncompleteError as exc:
        evidence = emit_billing_statement_evidence(
            statement.model_dump(mode="json"), delivered=False, detail=str(exc)
        )
    else:
        evidence = emit_billing_statement_evidence(
            statement.model_dump(mode="json"), delivered=result.delivered, detail=result.detail
        )

    await persist_billing_statement_record(session, evidence)
    return evidence


async def on_claim_finalized(session: AsyncSession, settings: Settings = default_settings) -> Optional[dict]:
    """Event trigger: call immediately after a claim-outcome record finalizes."""
    return await generate_and_send_if_due(session, settings)


async def run_scheduled_check(session: AsyncSession, settings: Settings = default_settings) -> Optional[dict]:
    """Scheduled trigger: call from src/billing/scheduler.py's cadence-driven poll loop."""
    return await generate_and_send_if_due(session, settings)
