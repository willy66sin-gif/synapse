"""
GET /frontline/blocked/{claim_id} tests.

Same stubbing approach as tests/test_supervisor_blocked_screen.py: a
fake AsyncSession returning a controlled AdjudicationAuditEntry row,
injected via FastAPI's dependency_overrides, no live Postgres.

Unlike the Supervisor Blocked Screen (NO_GO-only, 409 on GO), this
route renders for GO and NO_GO alike -- the Frontline persona's
question is "Can I proceed?", which GO answers just as validly.
"""
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
    },
    "sha256_signature": "irrelevant-for-this-test",
}

GO_EVIDENCE = {
    "claim_id": "CLM-101",
    "decision": "GO",
    "reason": "Claim 'CLM-101' cleared for execution in ZONE-01.",
    "reason_code": None,
    "authority_binding_id": None,
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
    "evaluated_at": "2026-07-31T10:05:00+00:00",
    "input_payload": {
        "claim_id": "CLM-101",
        "issuer_id": "USR-SUP-01",
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
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
    row = None
    if record is not None:
        row = AdjudicationAuditEntry(claim_id=record["claim_id"], decision=record["decision"], record=record)

    async def _override_db_session():
        yield _StubSession(row=row)

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_frontline_screen_renders_for_no_go_claim():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/frontline/blocked/CLM-EPTW-301")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    assert "CLM-EPTW-301" in body
    assert "General Duty Officer" in body
    assert "BIND-999" in body
    assert "No permit-to-work context provided" in body
    assert '"/static/frontline-screen/frontline-screen.js"' in body

    # Frontline screen must never expose rule internals or override mechanics.
    assert "ptw_precondition_check" not in body
    assert "rule_trace" not in body
    assert "/supervisor/override" not in body
    assert "Contact Supervisor" not in body

    # WCAG 1.4.4/1.4.10: reflow/zoom must stay available -- scaling never disabled.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1" />' in body
    assert "user-scalable=no" not in body
    assert "maximum-scale=1" not in body


def test_frontline_screen_renders_for_go_claim():
    """Unlike the Supervisor Blocked Screen, GO is a valid, renderable
    state here -- not a 409."""
    client = _client_with_record(GO_EVIDENCE)

    response = client.get("/frontline/blocked/CLM-101")

    assert response.status_code == 200
    body = response.text
    assert "CLM-101" in body
    assert "General Duty Officer" in body
    assert "MATERIAL_ENTRY" in body


def test_frontline_screen_returns_404_for_unknown_claim():
    client = _client_with_record(None)

    response = client.get("/frontline/blocked/CLM-DOES-NOT-EXIST")

    assert response.status_code == 404
    assert "CLM-DOES-NOT-EXIST" in response.json()["detail"]


def test_frontline_screen_js_is_served_as_static_asset():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/static/frontline-screen/frontline-screen.js")

    assert response.status_code == 200
    assert "customElements.define" in response.text
