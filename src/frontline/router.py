"""
Frontline Worker verdict view.

First implementation of the Frontline Worker persona (CLAUDE.md's
Stage 2 Frontline Worker Contract -- approved design, previously
unbuilt) and the first consumer of the Escalation vs. Override --
Decoupling Principle (Locked, 5 Aug 2026). Deliberately a separate
surface from src/supervisor/: no rule internals, pipeline/architecture
concepts, technical evidence detail, or override mechanics belong
here -- this route's response shape has no field that could expose
any of them.

GET /frontline/blocked/{claim_id}: reuses the same
fetch_latest_adjudication_record() src/supervisor/router.py's
GET /supervisor/blocked/{claim_id} already uses. 404 if the claim was
never adjudicated. Unlike the Supervisor Blocked Screen (deliberately
NO_GO-only), this route renders for GO *and* NO_GO -- the Frontline
persona's question is "Can I proceed?", which GO answers just as
validly as NO_GO does (see the spec's Section 3, Persona Questions).

Unlike the supervisor route, this one DOES call
src/maestro/directory.py's resolve_authority(zone_id, reason_code)
directly, to surface a real assigned_role ("General Duty Officer"
today) rather than the bare authority_binding_id alone -- the
Decoupling Principle's v0.1 guidance requires the actually-resolved
role, not a hardcoded/invented title like "Supervisor". zone_id comes
from evidence["input_payload"]["zone_id"], the persisted original
ClaimPayload -- the same source src/airlock/router.py itself reads it
from before adjudication.
"""
import html
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.schemas import ClaimPayload
from src.core.evaluator import adjudicate
from src.core.repository import (
    fetch_issuer_record,
    fetch_issuer_roles,
    fetch_zone_record,
    get_db_session,
    get_redis_client,
)
from src.evidence.emitter import emit_evidence
from src.evidence.repository import fetch_latest_adjudication_record, persist_adjudication_record
from src.maestro.directory import resolve_authority

router = APIRouter(prefix="/frontline", tags=["frontline"])

FRONTLINE_SCREEN_JS_URL = "/static/frontline-screen/frontline-screen.js"


@router.get("/blocked/{claim_id}", response_class=HTMLResponse)
async def frontline_status_screen(claim_id: str, session: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    evidence = await fetch_latest_adjudication_record(session, claim_id)

    if evidence is None:
        raise HTTPException(status_code=404, detail=f"No record found for claim '{claim_id}'.")

    zone_id = evidence.get("input_payload", {}).get("zone_id")
    is_design_alteration = evidence.get("input_payload", {}).get("is_design_alteration", False)
    # 2026-08-18: resolve_authority() now returns every applicable
    # binding (reason_code tier, plus QP/QE if is_design_alteration) --
    # joined into single display strings here since frontline-screen.js
    # renders traceId/assignedRole as scalars (unchanged, out of this
    # pass's scope) -- see resolve_authority()'s own docstring for why
    # both can apply to the same claim.
    bindings = resolve_authority(zone_id, evidence["reason_code"], is_design_alteration)

    frontline_screen_data = {
        "claimId": evidence["claim_id"],
        "decision": evidence["decision"],
        "reasonCode": evidence["reason_code"],
        "reason": _frontline_reason(evidence),
        "workActivity": evidence.get("input_payload", {}).get("action_type", ""),
        "traceId": ", ".join(binding.binding_id for binding in bindings),
        "assignedRole": ", ".join(binding.role for binding in bindings),
    }

    return HTMLResponse(_render_frontline_screen_page(frontline_screen_data))


@router.get("/blocked/{claim_id}/status")
async def frontline_status_json(
    claim_id: str,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> dict:
    """
    GO Freshness Phase 1 (2026-08-31, Willy-authorized, scoped to
    permit-context/zone fields only -- see CLAUDE.md's GO Freshness
    Principle decision, 2026-08-28). JSON counterpart to
    GET /frontline/blocked/{claim_id}, meant to be polled by
    frontend/frontline-screen/frontline-screen.js after its initial
    server-rendered page load.

    Deliberately does NOT just re-read the last persisted evidence row
    -- that would only change if a *new* claim got submitted, and would
    never notice a permit or zone state that changed since this claim
    was originally adjudicated, which is exactly the gap Phase 1 closes.
    Instead this re-runs the actual evaluation path fresh, every call:
    reconstructs the original ClaimPayload from the persisted
    evidence's input_payload (claim_id, timestamp, ptw_context, zone_id
    etc. don't change poll-to-poll), then re-fetches issuer/zone state
    and calls src/core/evaluator.py's adjudicate() again -- the exact
    same Core entrypoint, same fetch_issuer_record/fetch_zone_record/
    fetch_issuer_roles calls, src/airlock/router.py's POST
    /airlock/claims already uses for a first-time submission. No rule
    logic is duplicated here.

    404 if the claim was never adjudicated -- same as the HTML route.

    Read/write boundary (GO Freshness Phase 1b, Part A, 2026-08-31 --
    supersedes Phase 1's "no persistence, no emit_evidence()" design,
    which left this an open question rather than deciding it): this
    endpoint is now CONDITIONALLY read-write, not purely read-only.
    Every call still re-evaluates fresh (unchanged). After evaluating,
    it compares the fresh verdict's (decision, reason_code) against the
    most recently persisted evidence for this claim -- if and only if
    they differ (_verdict_transitioned()), this poll just detected a
    fail-closed gate genuinely firing (or clearing), and gets the same
    treatment as any other adjudication: emit_evidence() +
    persist_adjudication_record(), the exact same functions/entrypoint
    src/airlock/router.py's POST /airlock/claims already uses, not a
    parallel emission path. An unchanged poll result touches the
    evidence store not at all -- no read-modify-write, no row locked,
    nothing -- so "poll every N seconds forever" does not mean "write
    every N seconds forever."

    Concurrency note: immediately before writing, this re-fetches the
    latest persisted evidence a second time and re-checks the same
    (decision, reason_code) comparison, narrowing the window where two
    overlapping polls could both observe the same transition and both
    write. This is a best-effort re-check, NOT a hard guarantee -- no
    unique constraint, row lock, or other concurrency primitive exists
    anywhere in this codebase's evidence-write path (checked: none of
    src/evidence/models.py, src/core/models.py, src/telemetry/models.py
    declare one), and inventing one was explicitly out of scope for
    this pass. A true guarantee would need a DB-level uniqueness
    constraint or SELECT ... FOR UPDATE, neither of which exists today.

    The response shape matches frontline_screen_data above exactly, so
    the client's `data` setter can consume this response the same way
    it already consumes the server-rendered initial payload -- no
    client-side branching on which one it received. This is entirely
    independent of whether this call happened to write evidence.
    """
    evidence = await fetch_latest_adjudication_record(session, claim_id)

    if evidence is None:
        raise HTTPException(status_code=404, detail=f"No record found for claim '{claim_id}'.")

    claim = ClaimPayload(**evidence["input_payload"])

    issuer_record = await fetch_issuer_record(session, claim.issuer_id)
    zone_record = await fetch_zone_record(redis_client, claim.zone_id)
    issuer_roles = await fetch_issuer_roles(session, claim.issuer_id)

    verdict = adjudicate(claim, issuer_record, zone_record, issuer_roles)

    if _verdict_transitioned(verdict, evidence):
        # Re-check immediately before writing -- see this function's own
        # docstring's Concurrency note for exactly what this does and
        # does not guarantee.
        latest_at_write_time = await fetch_latest_adjudication_record(session, claim_id)
        if _verdict_transitioned(verdict, latest_at_write_time):
            transition_authority_binding_id = None
            if verdict["decision"] == "NO_GO":
                transition_authority_binding_id = [
                    binding.binding_id
                    for binding in resolve_authority(claim.zone_id, verdict["reason_code"], claim.is_design_alteration)
                ]
            transition_evidence = emit_evidence(
                claim.model_dump(mode="json"), verdict, authority_binding_id=transition_authority_binding_id
            )
            await persist_adjudication_record(session, transition_evidence)

    bindings = resolve_authority(claim.zone_id, verdict["reason_code"], claim.is_design_alteration)

    return {
        "claimId": verdict["claim_id"],
        "decision": verdict["decision"],
        "reasonCode": verdict["reason_code"],
        "reason": _frontline_reason(verdict),
        "workActivity": claim.action_type,
        "traceId": ", ".join(binding.binding_id for binding in bindings),
        "assignedRole": ", ".join(binding.role for binding in bindings),
    }


def _verdict_transitioned(verdict: dict, evidence: dict) -> bool:
    """
    GO Freshness Phase 1b (2026-08-31), Part A: true if `verdict`
    genuinely differs from the previously persisted `evidence` on the
    parts that define what was actually decided -- decision or
    reason_code. Mirrors frontend/blocked-screen/blocked-screen.js's own
    "isRealVerdictChange" definition (see its class doc comment / `data`
    setter) -- the established precedent in this codebase for what
    counts as a real verdict change worth reacting to, vs. cosmetic
    noise (e.g. `reason`'s free-text wording, `rule_trace` detail,
    `evaluated_at` timestamps -- none of those alone make this true).
    """
    return verdict["decision"] != evidence["decision"] or verdict["reason_code"] != evidence.get("reason_code")


def _frontline_reason(evidence: dict) -> str:
    """
    Plain-language reason for a frontline worker: the specific failing
    rule's own `reason` string, not the full rule_trace and not its
    rule_id. Mirrors the "conflicting condition" concept from the
    Supervisor UI Principle, but surfaces only its human-readable text
    -- CLAUDE.md's Stage 2 Frontline Worker Contract explicitly
    excludes rule internals from this screen. Empty on GO, where there
    is nothing to explain.

    Takes `dict` (not the narrower Verdict TypedDict) because both
    callers pass it a persisted evidence record: frontline_status_screen()
    passes a full AdjudicationRecord dict, and frontline_status_json()
    (GO Freshness Phase 1) passes a freshly-computed Verdict -- both
    happen to share the "decision"/"rule_trace"/"reason" keys this
    function actually reads, so one implementation serves both without
    a second, near-duplicate reason-extraction function.
    """
    if evidence["decision"] != "NO_GO":
        return ""
    failing = next((rule for rule in evidence.get("rule_trace", []) if rule.get("passed") is False), None)
    return failing["reason"] if failing else evidence["reason"]


def _render_frontline_screen_page(data: dict) -> str:
    """
    Same JSON hand-off pattern as src/supervisor/router.py's
    _render_blocked_screen_page: safer than templating individual
    fields into markup, and the component's own _render() already
    HTML-escapes everything it interpolates. The `<` -> `\\u003c`
    replacement stops any string field (e.g. a reason built from
    user-submitted zone_id/ptw_id) from prematurely closing the
    <script> tag it's embedded in.
    """
    claim_id = html.escape(data["claimId"])
    payload = json.dumps(data).replace("<", "\\u003c")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Status — {claim_id}</title>
</head>
<body>
  <frontline-screen id="screen"></frontline-screen>
  <script type="module" src="{FRONTLINE_SCREEN_JS_URL}"></script>
  <script type="module">
    document.getElementById("screen").data = {payload};
  </script>
</body>
</html>"""
