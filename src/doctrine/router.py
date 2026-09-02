"""
DoctrineSubmission creation endpoint (Tier 2 CORENET X Parallel Entry,
2026-09-02).

New: no prior endpoint existed for src/doctrine/ -- this is the first
write path into the previously zero-row doctrine_submissions registry
(see src/doctrine/models.py's own docstring for why it shipped empty
until now). Mirrors src/airlock/router.py's POST /airlock/claims shape
(validate -> persist -> emit signed evidence -> persist evidence), but
does not adjudicate anything and never touches src/core/'s evaluator --
per this build's own non-goals, a DoctrineSubmission is a parallel
record only.

receipt_timestamp is set here, server-side, from
datetime.now(timezone.utc) -- never accepted from the request body.
src/doctrine/schemas.py's DoctrineSubmission already rejects a
non-null client-supplied receipt_timestamp at the schema boundary
(422), before this handler body ever runs; this function does not
duplicate that check, it only ever sees a validated submission whose
receipt_timestamp is still None.

staleness_days (receipt_timestamp minus corenet_x_approval_date) is
computed here and threaded into the signed evidence record -- not
persisted as its own column anywhere, per this build's explicit
"derived, not stored" instruction.

Duplicate submission_id handling (2026-09-02 follow-up):
submission_id is DoctrineSubmissionRecord's primary key
(src/doctrine/models.py) -- a repeated one raises IntegrityError on
commit. Caught here specifically (not a broad except, same discipline
as this codebase's other narrow except clauses) and turned into a
clean 409, plain string detail -- mirrors src/supervisor/router.py's
existing GET /supervisor/blocked/{claim_id} 409 shape ("Claim '...' is
not blocked ...") rather than inventing a new error-response shape.
Caught before any evidence is computed or persisted, so a rejected
duplicate produces neither a second DoctrineSubmissionRecord row nor a
DoctrineSubmissionReceiptAuditEntry for it.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session
from src.doctrine.repository import persist_doctrine_submission, persist_doctrine_submission_receipt
from src.doctrine.schemas import DoctrineSubmission
from src.evidence.emitter import emit_doctrine_submission_evidence

router = APIRouter(prefix="/doctrine", tags=["doctrine"])


@router.post("/submissions")
async def submit_doctrine_submission(
    submission: DoctrineSubmission, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    FastAPI + Pydantic v2 already enforce fail-closed validation here --
    a malformed body (missing required field, unknown field, or a
    client-supplied receipt_timestamp) 422s before this line runs.
    """
    receipt_timestamp = datetime.now(timezone.utc)

    stored_fields = submission.model_dump()
    stored_fields["receipt_timestamp"] = receipt_timestamp
    try:
        await persist_doctrine_submission(session, stored_fields)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A doctrine submission with submission_id '{submission.submission_id}' already exists.",
        ) from exc

    submission_json = submission.model_dump(mode="json")
    submission_json["receipt_timestamp"] = receipt_timestamp.isoformat()
    staleness_days = (receipt_timestamp.date() - submission.corenet_x_approval_date).days

    evidence = emit_doctrine_submission_evidence(submission_json, staleness_days)
    await persist_doctrine_submission_receipt(session, evidence)

    return evidence
