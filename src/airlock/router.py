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

Escalation Ownership (2026-07-31): on NO_GO, authority is resolved via
src/maestro/directory.py's resolve_authority(zone_id, reason_code)
*before* emit_evidence() runs, so the resolved authority_binding_id
can be part of the signed evidence record itself, not appended after
signing. OutboundAlert.from_evidence_record() resolves authority again
internally for the alert's fields — a second call to the same pure,
deterministic lookup, not a second decision; see its docstring.
"""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.profile_check import (
    ProfileIdMissingError,
    ProfileIdUnresolvableError,
    check_profile_requirement,
)
from src.airlock.repository import persist_profile_rejection_record
from src.airlock.schemas import ClaimPayload
from src.config import settings
from src.core.evaluator import adjudicate
from src.core.repository import (
    fetch_issuer_record,
    fetch_issuer_roles,
    fetch_zone_record,
    get_db_session,
    get_redis_client,
)
from src.evidence.emitter import emit_evidence, emit_profile_rejection_evidence
from src.evidence.repository import persist_adjudication_record
from src.maestro.adapters.telegram import TelegramAdapter
from src.maestro.adapters.whatsapp import WhatsAppAdapter
from src.maestro.directory import resolve_authority
from src.maestro.schemas import OutboundAlert
from src.profiles.repository import fetch_certified_profile

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

    GO Freshness Phase 3a, Part A (2026-08-31): the profile_id
    requirement check runs first, before any issuer/zone I/O or
    adjudicate() call -- Airlock is upstream of Core, so a claim that's
    about to be rejected for a missing/unresolvable profile_id (only
    possible when settings.profile_id_enforcement_enabled is True)
    never wastefully reaches Core at all. See
    src/airlock/profile_check.py's module docstring for the full
    grace-period design. When the claim proceeds (either because
    enforcement is off, or because profile_id resolved), the resulting
    ProfileCheckOutcome is appended to Verdict.rule_trace below --
    reusing adjudicate()'s existing rule_trace shape for this note
    rather than adding a new top-level field to the evidence record,
    and deliberately NOT extending adjudicate()'s own signature/pure
    logic to do this itself (that's GO Freshness Phase 3a Part B's
    separate, not-yet-authorized-at-the-time-of-this-comment scope).

    Wires the full pipeline: profile_id check -> fetch issuer/zone
    state -> adjudicate (pure) -> resolve escalation authority (NO_GO
    only) -> emit signed evidence record (carrying that resolved
    authority_binding_id) -> persist it, so a subsequent admin-override
    request has something real to check "does this claim exist"
    against -> on NO_GO only, notify Maestro.
    """
    profile = None
    if claim.profile_id is not None:
        profile = await fetch_certified_profile(session, claim.profile_id)

    try:
        profile_outcome = check_profile_requirement(
            claim.profile_id, profile, settings.profile_id_enforcement_enabled
        )
    except (ProfileIdMissingError, ProfileIdUnresolvableError) as exc:
        rejection_evidence = emit_profile_rejection_evidence(claim.claim_id, claim.profile_id, exc.reason_code)
        await persist_profile_rejection_record(session, rejection_evidence)
        raise HTTPException(
            status_code=422, detail={"reason_code": exc.reason_code, "message": str(exc)}
        ) from exc

    issuer_record = await fetch_issuer_record(session, claim.issuer_id)
    zone_record = await fetch_zone_record(redis_client, claim.zone_id)
    # 2026-08-27, Authority Admissibility handoff: fetch_issuer_roles()
    # was already built and tested but unread until now -- resolved
    # unconditionally, same pattern as issuer_record/zone_record above
    # (an already-fetched record adjudicate() consults, not a second
    # I/O boundary inside Core).
    issuer_roles = await fetch_issuer_roles(session, claim.issuer_id)

    # GO Freshness Phase 3a, Part B (2026-08-31): `profile` was already
    # resolved above (Part A's profile_id check needed it regardless of
    # enforcement state) -- threaded straight into adjudicate() here for
    # consistency with src/frontline/router.py's poll path, which now
    # also fetches and passes one. Free: no second fetch, no new I/O
    # boundary. None in the same cases frontline_status_json() would
    # also see None (no profile_id submitted, or an unresolvable one
    # during the grace period) -- adjudicate() treats None exactly as
    # it did before Part B existed.
    verdict = adjudicate(claim, issuer_record, zone_record, issuer_roles, profile)
    # GO Freshness Phase 3a, Part A: appended here, not inside
    # adjudicate() -- see this function's own docstring above.
    verdict["rule_trace"].append(asdict(profile_outcome))

    authority_binding_id = None
    if verdict["decision"] == "NO_GO":
        # 2026-08-18: resolve_authority() now returns a list (reason_code
        # binding first, then QP/QE if claim.is_design_alteration) -- see
        # its own docstring. Persisted here as a list of binding_ids,
        # same "list, not a single winner" shape used everywhere else
        # this pass touched. Still gated to NO_GO only, unchanged from
        # before this pass -- GO's persisted authority_binding_id stays
        # None, matching the existing, locked NO_GO Notification
        # Principle (GO never triggers a Maestro alert either way); a
        # GO claim's design-alteration bindings are still fully visible
        # live via the Frontline/Supervisor screens' own
        # resolve_authority() calls against evidence["input_payload"].
        authority_binding_id = [
            binding.binding_id
            for binding in resolve_authority(claim.zone_id, verdict["reason_code"], claim.is_design_alteration)
        ]

    evidence = emit_evidence(claim.model_dump(mode="json"), verdict, authority_binding_id=authority_binding_id)
    await persist_adjudication_record(session, evidence)

    if verdict["decision"] == "NO_GO":
        alert = OutboundAlert.from_evidence_record(evidence, zone_id=claim.zone_id)
        for adapter in (WhatsAppAdapter(), TelegramAdapter()):
            adapter.send_alert(alert)

    return evidence
