"""
RegulatoryInstrument registry endpoints (Step 1, 2026-10-11).

POST /regulatory/instruments mirrors src/doctrine/router.py's
POST /doctrine/submissions shape exactly (validate -> persist -> emit
signed evidence -> persist evidence). It does not adjudicate anything
and never touches src/core/'s evaluator: a registered instrument is a
reference record only.

receipt_timestamp is set here, server-side, from
datetime.now(timezone.utc) -- never accepted from the request body.
src/regulatory/schemas.py's RegulatoryInstrument already rejects a
non-null client-supplied value at the schema boundary (422) before
this handler body runs.

Two derived day-counts are computed here and threaded into the signed
evidence record, not persisted as columns -- same "derived, not
stored" rule as src/doctrine/router.py's staleness_days:
  - staleness_days: receipt date minus publish_date (how long after
    publication Synapse was told).
  - days_until_effective: effective_date minus receipt date (negative
    once the instrument was already in effect when received).
These are the only consumers of publish_date/effective_date anywhere
in this codebase. Nothing in adjudication reads either date.

Duplicate handling: a repeated instrument_id (primary key) or a
repeated (issuing_authority, reference_number) pair (unique
constraint) both raise IntegrityError on commit -- caught here
specifically and turned into a clean 409, before any evidence is
computed or persisted, same as src/doctrine/router.py.

GET routes (the first registry read routes in this codebase -- no
other registry table has one): GET /regulatory/instruments lists the
registry, optionally narrowed by exact issuing_authority and/or
reference_number; GET /regulatory/instruments/{instrument_id} returns
one, or 404. reference_number is a query parameter, never a path
segment, because real reference numbers contain "/".
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session
from src.evidence.emitter import emit_regulatory_instrument_evidence
from src.regulatory.repository import (
    fetch_regulatory_instrument,
    list_regulatory_instruments,
    persist_regulatory_instrument,
    persist_regulatory_instrument_receipt,
)
from src.regulatory.schemas import RegulatoryInstrument

router = APIRouter(prefix="/regulatory", tags=["regulatory"])


@router.post("/instruments")
async def register_regulatory_instrument(
    instrument: RegulatoryInstrument, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """
    FastAPI + Pydantic v2 already enforce fail-closed validation here --
    a malformed body (missing required field, unknown field, unknown
    grounding_category, or a client-supplied receipt_timestamp) 422s
    before this line runs.
    """
    receipt_timestamp = datetime.now(timezone.utc)

    stored_fields = instrument.model_dump()
    stored_fields["receipt_timestamp"] = receipt_timestamp
    try:
        await persist_regulatory_instrument(session, stored_fields)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"A regulatory instrument with instrument_id '{instrument.instrument_id}', or with "
                f"reference_number '{instrument.reference_number}' from issuing_authority "
                f"'{instrument.issuing_authority}', already exists."
            ),
        ) from exc

    instrument_json = instrument.model_dump(mode="json")
    instrument_json["receipt_timestamp"] = receipt_timestamp.isoformat()
    staleness_days = (receipt_timestamp.date() - instrument.publish_date).days
    days_until_effective = (instrument.effective_date - receipt_timestamp.date()).days

    evidence = emit_regulatory_instrument_evidence(instrument_json, staleness_days, days_until_effective)
    await persist_regulatory_instrument_receipt(session, evidence)

    return evidence


@router.get("/instruments")
async def list_instruments(
    issuing_authority: Optional[str] = None,
    reference_number: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    return await list_regulatory_instruments(session, issuing_authority, reference_number)


@router.get("/instruments/{instrument_id}")
async def get_instrument(instrument_id: str, session: AsyncSession = Depends(get_db_session)) -> dict:
    instrument = await fetch_regulatory_instrument(session, instrument_id)
    if instrument is None:
        raise HTTPException(
            status_code=404, detail=f"No regulatory instrument with instrument_id '{instrument_id}' exists."
        )
    return instrument
