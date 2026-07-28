"""
Persistence for signed override evidence records. Mirrors
src/evidence/repository.py's pattern: I/O only, no logic — the
authorization/existence checks live in src/supervisor/logic.py and
stay pure and I/O-free.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from src.supervisor.models import OverrideAuditEntry


async def persist_override_record(session: AsyncSession, override_evidence: dict) -> None:
    """Appends a signed OverrideRecord to the audit trail. Never updates or deletes existing rows."""
    session.add(
        OverrideAuditEntry(
            claim_id=override_evidence["claim_id"],
            issuer_id=override_evidence["issuer_id"],
            record=override_evidence,
        )
    )
    await session.commit()
