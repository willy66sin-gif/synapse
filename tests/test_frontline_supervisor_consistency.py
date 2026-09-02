"""
Frontline/Supervisor rendering-consistency tests (2026-09-02), following
up on the investigation that found the two screens independently
re-deriving fields that should trace back to a single source value on
the canonical evidence record.

Item 1: the NO_GO reason text must be identical on both screens -- both
now read evidence["reason"]/verdict["reason"] straight through; neither
re-derives its own copy from rule_trace any more (src/frontline/router.py
no longer has _frontline_reason(), blocked-screen.js's _renderConflict()
no longer reads conflicting.reason).

Same stub-session infrastructure as tests/test_frontline_router.py and
tests/test_supervisor_blocked_screen.py: a fake AsyncSession returning a
controlled AdjudicationAuditEntry row, no live Postgres.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient

from src.core.repository import get_db_session
from src.evidence.models import AdjudicationAuditEntry
from src.main import app

NO_GO_EVIDENCE = {
    "claim_id": "CLM-EPTW-301",
    "decision": "NO_GO",
    "reason": (
        "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
        "for high-risk work_type 'EXCAVATION'."
    ),
    "reason_code": "R-PTW-01",
    # Deliberately stale/mismatched vs. what resolve_authority() would
    # live-resolve for R-PTW-01 (BIND-RTO-01) -- proves Item 2's fix
    # actually stops this value from ever reaching the screen.
    "authority_binding_id": "BIND-999",
    "rule_trace": [
        {
            "rule_id": "ptw_precondition_check",
            "passed": False,
            "reason": (
                "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
                "for high-risk work_type 'EXCAVATION'."
            ),
        },
    ],
    "evaluated_at": "2026-07-31T10:00:00+00:00",
    "input_payload": {
        "claim_id": "CLM-EPTW-301",
        "issuer_id": "USR-SUP-01",
        "zone_id": "ZONE-01",
        "action_type": "EXCAVATION",
        "is_design_alteration": False,
    },
    "sha256_signature": "irrelevant-for-this-test",
}


class _StubResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _StubSession:
    def __init__(self, row=None):
        self._row = row

    async def execute(self, stmt):
        return _StubResult(self._row)


def _client_with_record(record):
    row = AdjudicationAuditEntry(claim_id=record["claim_id"], decision=record["decision"], record=record)

    async def _override_db_session():
        yield _StubSession(row=row)

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _embedded_data(html_body: str) -> dict:
    """
    Extracts the JSON payload both <frontline-screen>/<blocked-screen>
    pages hand off via `document.getElementById("screen").data = {...};`
    -- the same JSON hand-off pattern both routers'
    _render_*_screen_page() helpers use (src/frontline/router.py's
    _render_frontline_screen_page(), src/supervisor/router.py's
    _render_blocked_screen_page()).
    """
    match = re.search(r'\.data = (\{.*\});', html_body)
    assert match, "expected a `.data = {...};` JSON hand-off in the rendered page"
    return json.loads(match.group(1))


def test_frontline_and_supervisor_show_the_identical_reason_string():
    with _client_with_record(NO_GO_EVIDENCE) as client:
        frontline_response = client.get("/frontline/blocked/CLM-EPTW-301")

    with _client_with_record(NO_GO_EVIDENCE) as client:
        supervisor_response = client.get("/supervisor/blocked/CLM-EPTW-301")

    frontline_data = _embedded_data(frontline_response.text)
    supervisor_data = _embedded_data(supervisor_response.text)

    assert frontline_data["reason"] == supervisor_data["evidence"]["reason"]
    assert frontline_data["reason"] == NO_GO_EVIDENCE["reason"]
