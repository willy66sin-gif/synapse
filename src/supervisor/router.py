"""
Admin-override HTTP endpoint.

Per CLAUDE.md's Admin-Override Evidence Principle: an override never
mutates the original adjudication record. This router validates the
request (fail-closed — malformed input 422s via Pydantic before this
function body runs, same discipline as src/airlock/router.py),
authorizes it and checks the claim actually exists via pure logic
(src/supervisor/logic.py's evaluate_override), and — only if accepted
— persists a new, distinct signed OverrideRecord evidence entry and
pushes a Maestro alert announcing it. Rejections are never evidenced,
persisted, or announced.

Maestro-wiring decision (see CLAUDE.md's Locked Design Principles for
the full reasoning): an accepted override DOES trigger a Maestro
alert. An override is exactly the kind of event someone other than
the overriding supervisor should hear about — a human just intervened
in a fail-closed system. This is the one place src/supervisor/
touches src/maestro/: src/supervisor/logic.py's evaluate_override()
stays fully decoupled (zero imports from Maestro), same as Core is
decoupled from it; only this orchestration layer wires the two
together, mirroring how src/airlock/router.py wires Core and Evidence
together without either of those depending on the router.

Rejection status codes: unlike /airlock/claims (where NO_GO is a
valid adjudication outcome, always 200), a rejected override here
means the administrative action did not happen at all — so it uses
real HTTP semantics: 403 for an unauthenticated issuer, 404 for a
claim with no adjudication record to override.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import fetch_issuer_record, get_db_session
from src.evidence.emitter import emit_override_evidence
from src.evidence.repository import fetch_latest_adjudication_record
from src.maestro.adapters.telegram import TelegramAdapter
from src.maestro.adapters.whatsapp import WhatsAppAdapter
from src.maestro.schemas import OutboundAlert, RuleConditionResult
from src.supervisor.logic import evaluate_override
from src.supervisor.repository import persist_override_record
from src.supervisor.schemas import OverrideRecord

router = APIRouter(prefix="/supervisor", tags=["supervisor"])


@router.post("/override")
async def submit_override(
    override: OverrideRecord,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    issuer_record = await fetch_issuer_record(session, override.issuer_id)
    original_record = await fetch_latest_adjudication_record(session, override.claim_id)

    outcome = evaluate_override(override, issuer_record, original_record)

    if not outcome.accepted:
        if issuer_record is None:
            raise HTTPException(status_code=403, detail=outcome.reason)
        raise HTTPException(status_code=404, detail=outcome.reason)

    evidence = emit_override_evidence(override.model_dump(mode="json"))
    await persist_override_record(session, evidence)

    alert = OutboundAlert(
        claim_id=override.claim_id,
        decision="GO",
        rule_trace=[
            RuleConditionResult(rule_id="admin_override", passed=True, reason=override.justification)
        ],
        evaluated_at=evidence["recorded_at"],
        recipient_id=override.issuer_id,
        escalation_contact=f"Overriding Supervisor: {override.issuer_id}",
    )
    notifications = [adapter.send_alert(alert) for adapter in (WhatsAppAdapter(), TelegramAdapter())]

    return {
        "evidence": evidence,
        "notifications": [result.model_dump() for result in notifications],
    }
