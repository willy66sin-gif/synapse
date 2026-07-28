"""
Fail-closed ingestion endpoint.

Any payload that does not validate against ClaimPayload (schemas.py)
must be rejected immediately — HTTP 422, no partial parsing, no
best-effort recovery, no NLP fallback on unstructured text.
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
    "does this claim exist" against.
    """
    issuer_record = await fetch_issuer_record(session, claim.issuer_id)
    zone_record = await fetch_zone_record(redis_client, claim.zone_id)

    verdict = adjudicate(claim, issuer_record, zone_record)
    evidence = emit_evidence(claim.model_dump(mode="json"), verdict)
    await persist_adjudication_record(session, evidence)

    return evidence
