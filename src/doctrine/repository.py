"""
Persistence for DoctrineSubmission records and their creation-event
evidence (Tier 2 CORENET X Parallel Entry, 2026-09-02).

I/O only, mirrors src/billing/repository.py's/src/evidence/repository.py's
separation from pure logic -- src/doctrine/router.py stays the place
receipt_timestamp/staleness get decided, this module does the actual
database writes.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from src.doctrine.models import DoctrineSubmissionReceiptAuditEntry, DoctrineSubmissionRecord


async def persist_doctrine_submission(session: AsyncSession, submission: dict) -> None:
    """
    Inserts one row into the doctrine_submissions registry table.
    submission is a plain dict (DoctrineSubmission.model_dump(), plus
    the server-set receipt_timestamp) -- not the pydantic type itself,
    so this module stays decoupled from src/doctrine/schemas.py the
    same way every other persist_*_record() in this codebase is
    decoupled from its corresponding schema type.
    """
    session.add(
        DoctrineSubmissionRecord(
            submission_id=submission["submission_id"],
            submitting_party_id=submission["submitting_party_id"],
            jurisdiction_code=submission["jurisdiction_code"],
            citations=submission["citations"],
            ambiguity_resolution_notes=submission["ambiguity_resolution_notes"],
            submitted_at=submission["submitted_at"],
            signed_off=submission["signed_off"],
            corenet_x_reference=submission["corenet_x_reference"],
            corenet_x_gateway=submission["corenet_x_gateway"],
            corenet_x_approval_date=submission["corenet_x_approval_date"],
            receipt_timestamp=submission["receipt_timestamp"],
            entered_by=submission["entered_by"],
        )
    )
    await session.commit()


async def persist_doctrine_submission_receipt(session: AsyncSession, evidence: dict) -> None:
    """Appends a signed DoctrineSubmissionReceiptRecord to its own audit trail -- never updates or deletes existing rows."""
    session.add(
        DoctrineSubmissionReceiptAuditEntry(
            submission_id=evidence["submission"]["submission_id"],
            corenet_x_gateway=evidence["submission"]["corenet_x_gateway"],
            record=evidence,
        )
    )
    await session.commit()
