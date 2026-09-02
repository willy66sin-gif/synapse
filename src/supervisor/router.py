"""
Admin-override HTTP endpoint -- RETIRED (2026-08-05).

Per CLAUDE.md's Supervisor Override Retirement principle (Locked,
5 Aug 2026): Supervisor override is retired entirely, not scoped down.
POST /supervisor/override now returns 410 Gone unconditionally,
without touching the database, src/supervisor/logic.py's
evaluate_override(), or src/maestro/. The replacement mechanism is a
fresh, re-adjudicated claim submitted through POST /airlock/claims by
the specific licensed/registered authority role that owns the gate
being changed -- never an override, never an appeal.

evaluate_override() itself (src/supervisor/logic.py), OverrideRecord
(src/supervisor/schemas.py), OverrideAuditEntry (src/supervisor/models.py),
and persist_override_record() (src/supervisor/repository.py) are left
intact, not deleted -- per instruction, pending formal removal once
nothing else references this retired path. They are simply no longer
called from this handler. The OverrideRecord type is still used below
only so a malformed request body keeps 422ing at the schema boundary
(unrelated to retirement, same fail-closed discipline as
src/airlock/router.py) before the handler body runs.

Historical context, retained for anyone reading git history: this
endpoint previously validated the request, authorized it and checked
claim existence via evaluate_override(), and -- only if accepted --
persisted a signed OverrideRecord evidence entry and pushed a Maestro
alert. See CLAUDE.md's Admin-Override Evidence/Notification Principles
for that retired design, and the Supervisor Override Retirement
principle for why it no longer runs.

GET /supervisor/blocked/{claim_id} (2026-07-31): serves
frontend/blocked-screen/'s <blocked-screen> component populated with
the real, persisted evidence record for claim_id — not the component's
standalone demo fixture. Fetches via the same
fetch_latest_adjudication_record() this file already uses for the
override flow above, same pattern. 404 if the claim was never
adjudicated; 409 if it was but the decision was GO (the Blocked Screen
is a NO_GO-only surface — a claim existing with the wrong decision is
a different failure mode than not existing at all, so it gets a
different status code).

Role label resolution (2026-08-06, Task A): this route now DOES call
src/maestro/directory.py's resolve_authority() -- previously
deliberately skipped as "Maestro's job, out of scope here", which left
the screen meant for the person expected to act showing no role
information at all, just a bare binding ID. Same pattern
src/frontline/router.py already uses: resolve_authority(zone_id,
reason_code), then binding.role for the display label (already
resolved via src/core/roles.py's role_type_label() at binding-
construction time, not re-resolved here) and binding.binding_id for
the (still secondary/reference-only, unchanged) trace ID.

GET /supervisor/blocked/{claim_id}/status (2026-09-02, Frontline/
Supervisor consistency follow-up, Item 3): polling parity with
src/frontline/router.py's frontline_status_json(), the exact same
pattern reused, not reinvented -- see that function's own docstring for
the full GO Freshness design (re-adjudicate fresh every call,
transition-only evidence write, concurrency re-check) and
blocked_screen_status()'s own docstring below for the one deliberate
difference (no NO_GO-only 409 gate on this endpoint).
"""
import html
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.airlock.schemas import ClaimPayload
from src.billing.service import on_claim_finalized
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
from src.frontline.router import _verdict_transitioned
from src.maestro.directory import resolve_authority
from src.profiles.repository import fetch_certified_profile
from src.supervisor.schemas import OverrideRecord

router = APIRouter(prefix="/supervisor", tags=["supervisor"])

BLOCKED_SCREEN_JS_URL = "/static/blocked-screen/blocked-screen.js"

OVERRIDE_RETIRED_DETAIL = (
    "Supervisor override has been retired. A verdict can only change via a new, "
    "verifiable claim submitted through POST /airlock/claims by the specific "
    "licensed/registered authority role that owns the gate, re-adjudicated fresh "
    "through Core. See CLAUDE.md's Supervisor Override Retirement principle."
)


@router.post("/override")
async def submit_override(override: OverrideRecord) -> dict:
    """
    Retired (2026-08-05) -- see this module's doc comment. The
    OverrideRecord parameter is kept only so a structurally malformed
    body still 422s at the schema boundary before this line runs, same
    as always; a well-formed body now always gets 410, unconditionally,
    with no database access, no evaluate_override() call, and no
    Maestro notification.
    """
    raise HTTPException(status_code=410, detail=OVERRIDE_RETIRED_DETAIL)


@router.get("/blocked/{claim_id}", response_class=HTMLResponse)
async def blocked_screen(claim_id: str, session: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    evidence = await fetch_latest_adjudication_record(session, claim_id)

    if evidence is None:
        raise HTTPException(status_code=404, detail=f"No adjudication record found for claim '{claim_id}'.")

    if evidence["decision"] != "NO_GO":
        raise HTTPException(
            status_code=409,
            detail=f"Claim '{claim_id}' is not blocked (decision={evidence['decision']}) — nothing to show.",
        )

    zone_id = evidence.get("input_payload", {}).get("zone_id")
    is_design_alteration = evidence.get("input_payload", {}).get("is_design_alteration", False)
    # 2026-08-18: resolve_authority() now returns every applicable
    # binding (reason_code tier, plus QP/QE if is_design_alteration) --
    # joined into a single display string here since blocked-screen.js
    # renders assignedRole as a scalar (unchanged, out of this pass's
    # scope).
    #
    # Escalation-owner pairing fix (2026-09-02, Frontline/Supervisor
    # consistency follow-up, Item 2): authority_binding_id below is now
    # this SAME live `bindings` list, joined the same way as
    # assignedRole -- not evidence["authority_binding_id"] (the
    # persisted value from src/evidence/emitter.py, frozen at
    # adjudication time). Previously this route paired a live-resolved
    # role with a historical binding ID; the two could silently
    # mismatch each other if src/maestro/directory.py's routing table
    # changed between adjudication time and view time. Matches
    # src/frontline/router.py's existing fully-live model exactly: both
    # fields now come from the same resolve_authority() call, always
    # mutually consistent, never a persisted/live mix.
    bindings = resolve_authority(zone_id, evidence["reason_code"], is_design_alteration)

    blocked_screen_data = {
        "evidence": {
            "claim_id": evidence["claim_id"],
            "decision": evidence["decision"],
            "reason": evidence["reason"],
            "reason_code": evidence["reason_code"],
            "authority_binding_id": ", ".join(binding.binding_id for binding in bindings),
            "rule_trace": evidence["rule_trace"],
            "evaluated_at": evidence["evaluated_at"],
        },
        "assignedRole": ", ".join(binding.role for binding in bindings),
        "escalationContact": "Your site supervisor",
        "issuerId": evidence.get("input_payload", {}).get("issuer_id", ""),
        "overrideEndpoint": "/supervisor/override",
    }

    return HTMLResponse(_render_blocked_screen_page(blocked_screen_data))


@router.get("/blocked/{claim_id}/status")
async def blocked_screen_status(
    claim_id: str,
    session: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis_client),
) -> dict:
    """
    Supervisor polling parity (2026-09-02, Frontline/Supervisor
    consistency follow-up, Item 3). JSON counterpart to
    GET /supervisor/blocked/{claim_id}, polled by blocked-screen.js the
    same way src/frontline/router.py's frontline_status_json() is
    already polled by frontline-screen.js -- same re-adjudicate-fresh-
    every-call design, same transition-only evidence-write behavior,
    reusing _verdict_transitioned() from that module directly rather
    than reimplementing the same diff logic here. See that function's
    own docstring for the full GO Freshness design (staleness gap,
    read/write boundary, concurrency re-check) -- unchanged and not
    repeated here.

    Unlike GET /supervisor/blocked/{claim_id} (the HTML route, still
    NO_GO-only -- 409 on GO, unchanged), this status endpoint returns
    200 for GO too: gating it the same way would make a NO_GO -> GO
    transition poll come back as a permanent 409, freezing the screen
    exactly when freshness matters most. blocked-screen.js's `_render()`
    already handles a GO payload correctly (it only conditionally shows
    the NO_GO-specific "Do not proceed."/conflict box), so this needs no
    client-side branching either.

    Response shape matches blocked_screen()'s blocked_screen_data
    exactly, so the client's existing `data` setter/_render() path
    handles a poll response exactly like the initial server-rendered
    payload. authority_binding_id/assignedRole both come from this
    call's single resolve_authority() call -- same Item 2 fix as the
    HTML route above, not a second, independently-paired pair.

    evaluated_at reflects the latest actually-persisted record: the
    original evidence's timestamp when nothing changed, or the newly
    written transition evidence's timestamp when a transition just got
    persisted (verdict itself carries no evaluated_at -- that's only
    added at signing time by src/evidence/emitter.py's emit_evidence()).
    """
    evidence = await fetch_latest_adjudication_record(session, claim_id)

    if evidence is None:
        raise HTTPException(status_code=404, detail=f"No record found for claim '{claim_id}'.")

    claim = ClaimPayload(**evidence["input_payload"])

    issuer_record = await fetch_issuer_record(session, claim.issuer_id)
    zone_record = await fetch_zone_record(redis_client, claim.zone_id)
    issuer_roles = await fetch_issuer_roles(session, claim.issuer_id)
    certified_profile = None
    if claim.profile_id is not None:
        certified_profile = await fetch_certified_profile(session, claim.profile_id)

    verdict = adjudicate(claim, issuer_record, zone_record, issuer_roles, certified_profile)

    evaluated_at = evidence["evaluated_at"]
    if _verdict_transitioned(verdict, evidence):
        # Re-check immediately before writing -- see
        # frontline_status_json()'s own docstring's Concurrency note for
        # exactly what this does and does not guarantee.
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
            evaluated_at = transition_evidence["evaluated_at"]
            # Same billing-statement event trigger as
            # src/airlock/router.py's submit_claim() and
            # src/frontline/router.py's frontline_status_json() -- a
            # poll-detected verdict transition is equally "a relevant
            # claim-outcome record finalizing." Same broad-except
            # boundary rationale as both of those call sites: this
            # polling endpoint's own availability must not depend on
            # the billing subsystem's health.
            try:
                await on_claim_finalized(session)
            except Exception as exc:  # noqa: BLE001 - infra boundary, see comment above
                print(f"Billing statement event trigger failed: {exc}")
        else:
            evaluated_at = latest_at_write_time["evaluated_at"]

    bindings = resolve_authority(claim.zone_id, verdict["reason_code"], claim.is_design_alteration)

    return {
        "evidence": {
            "claim_id": verdict["claim_id"],
            "decision": verdict["decision"],
            "reason": verdict["reason"],
            "reason_code": verdict["reason_code"],
            "authority_binding_id": ", ".join(binding.binding_id for binding in bindings),
            "rule_trace": verdict["rule_trace"],
            "evaluated_at": evaluated_at,
        },
        "assignedRole": ", ".join(binding.role for binding in bindings),
        "escalationContact": "Your site supervisor",
        "issuerId": claim.issuer_id,
        "overrideEndpoint": "/supervisor/override",
    }


def _render_blocked_screen_page(data: dict) -> str:
    """
    Renders a standalone page hosting <blocked-screen>, fed real data
    via a JSON hand-off (safer than templating individual fields into
    markup, and the component's own _render() already HTML-escapes
    everything it interpolates — see blocked-screen.js). The `<` ->
    `\\u003c` replacement stops any string field (e.g. reason text
    built from user-submitted zone_id/ptw_id) from prematurely closing
    the <script> tag it's embedded in.
    """
    claim_id = html.escape(data["evidence"]["claim_id"])
    payload = json.dumps(data).replace("<", "\\u003c")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Blocked — {claim_id}</title>
</head>
<body>
  <blocked-screen id="screen"></blocked-screen>
  <script type="module" src="{BLOCKED_SCREEN_JS_URL}"></script>
  <script type="module">
    document.getElementById("screen").data = {payload};
  </script>
</body>
</html>"""
