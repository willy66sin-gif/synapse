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
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import get_db_session
from src.evidence.repository import fetch_latest_adjudication_record
from src.maestro.directory import resolve_authority

router = APIRouter(prefix="/frontline", tags=["frontline"])

FRONTLINE_SCREEN_JS_URL = "/static/frontline-screen/frontline-screen.js"


@router.get("/blocked/{claim_id}", response_class=HTMLResponse)
async def frontline_status_screen(claim_id: str, session: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    evidence = await fetch_latest_adjudication_record(session, claim_id)

    if evidence is None:
        raise HTTPException(status_code=404, detail=f"No adjudication record found for claim '{claim_id}'.")

    zone_id = evidence.get("input_payload", {}).get("zone_id")
    binding = resolve_authority(zone_id, evidence["reason_code"])

    frontline_screen_data = {
        "claimId": evidence["claim_id"],
        "decision": evidence["decision"],
        "reasonCode": evidence["reason_code"],
        "reason": _frontline_reason(evidence),
        "workActivity": evidence.get("input_payload", {}).get("action_type", ""),
        "traceId": binding.binding_id,
        "assignedRole": binding.role,
    }

    return HTMLResponse(_render_frontline_screen_page(frontline_screen_data))


def _frontline_reason(evidence: dict) -> str:
    """
    Plain-language reason for a frontline worker: the specific failing
    rule's own `reason` string, not the full rule_trace and not its
    rule_id. Mirrors the "conflicting condition" concept from the
    Supervisor UI Principle, but surfaces only its human-readable text
    -- CLAUDE.md's Stage 2 Frontline Worker Contract explicitly
    excludes rule internals from this screen. Empty on GO, where there
    is nothing to explain.
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
