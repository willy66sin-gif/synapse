"""
Fail-closed ingestion endpoint.

Any payload that does not validate against ClaimPayload (schemas.py)
must be rejected immediately — HTTP 422, no partial parsing, no
best-effort recovery, no NLP fallback on unstructured text.

Maestro wiring (2026-07-31, resolving CLAUDE.md's "Maestro is not
wired into /airlock/claims" Open Item): a NO_GO adjudication — for any
reason_code (ePTW, authority, or zone-safety failure) — now triggers a
Maestro alert, the same way src/supervisor/router.py already does for
an accepted override. GO adjudications deliberately do not; that's not
an oversight, it's the actual decision recorded in CLAUDE.md's
Changelog. Same pattern as the override endpoint: build an
OutboundAlert from the just-emitted evidence record, then hand it to
both WhatsAppAdapter and TelegramAdapter unconditionally. Unlike the
override endpoint, notification results aren't added to this
endpoint's response body — Maestro delivery stays a side effect here,
so the response shape for /airlock/claims is unchanged either way.
"""
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.schemas import ClaimPayload
from src.core.evaluator import adjudicate
from src.core.repository import (
    fetch_issuer_record,
    fetch_zone_record,
    get_db_session,
    get_redis_client,
)
from src.evidence.emitter import emit_evidence
from src.evidence.repository import persist_adjudication_record
from src.maestro.adapters.telegram import TelegramAdapter
from src.maestro.adapters.whatsapp import WhatsAppAdapter
from src.maestro.schemas import OutboundAlert

router = APIRouter(prefix="/airlock", tags=["airlock"])


@router.post("/claims")
async def submit_claim(
    claim: ClaimPayload,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> dict:
    """
    FastAPI + Pydantic v2 already enforce fail-closed behavior here:
    a malformed body is rejected before this function body runs.

    Wires the full pipeline: fetch issuer/zone state -> adjudicate
    (pure) -> emit signed evidence record -> persist it, so a
    subsequent admin-override request has something real to check
    "does this claim exist" against -> on NO_GO only, notify Maestro.
    """
    issuer_record = await fetch_issuer_record(session, claim.issuer_id)
    zone_record = await fetch_zone_record(redis_client, claim.zone_id)

    verdict = adjudicate(claim, issuer_record, zone_record)
    evidence = emit_evidence(claim.model_dump(mode="json"), verdict)
    await persist_adjudication_record(session, evidence)

    if verdict["decision"] == "NO_GO":
        alert = OutboundAlert.from_evidence_record(
            evidence,
            recipient_id=claim.issuer_id,
            escalation_contact=f"Submit an override via POST /supervisor/override (issuer_id={claim.issuer_id}).",
        )
        for adapter in (WhatsAppAdapter(), TelegramAdapter()):
            adapter.send_alert(alert)

    return evidence
