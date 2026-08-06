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
different status code). Deliberately does not call
src/maestro/directory.py's resolve_authority() — that's Maestro's job
when building an OutboundAlert for an actual alert, out of scope here;
this route only ever surfaces authority_binding_id, which is already
part of the persisted evidence record itself (None on GO, which this
route never serves anyway).
"""
import html
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session
from src.evidence.repository import fetch_latest_adjudication_record
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

    blocked_screen_data = {
        "evidence": {
            "claim_id": evidence["claim_id"],
            "decision": evidence["decision"],
            "reason": evidence["reason"],
            "reason_code": evidence["reason_code"],
            "authority_binding_id": evidence.get("authority_binding_id"),
            "rule_trace": evidence["rule_trace"],
            "evaluated_at": evidence["evaluated_at"],
        },
        "escalationContact": "Your site supervisor",
        "issuerId": evidence.get("input_payload", {}).get("issuer_id", ""),
        "overrideEndpoint": "/supervisor/override",
    }

    return HTMLResponse(_render_blocked_screen_page(blocked_screen_data))


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
