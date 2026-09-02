"""
GET /supervisor/blocked/{claim_id} tests.

Serves the real, persisted Blocked Screen for a NO_GO claim -- not a
mock fixture. Same stubbing approach as tests/test_supervisor_router.py:
a fake AsyncSession returning a controlled AdjudicationAuditEntry row,
injected via FastAPI's dependency_overrides, no live Postgres.
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
    "input_payload": {"claim_id": "CLM-EPTW-301", "issuer_id": "USR-SUP-01", "zone_id": "ZONE-01"},
    "sha256_signature": "irrelevant-for-this-test",
}

ZONE_NO_GO_EVIDENCE = {
    "claim_id": "CLM-ZONE-301",
    "decision": "NO_GO",
    "reason": "Safety Violation: Zone 'ZONE-99' does not exist.",
    "reason_code": "R-ZONE-01",
    "authority_binding_id": "BIND-SA-01",
    "rule_trace": [
        {"rule_id": "zone_safety_check", "passed": False, "reason": "Safety Violation: Zone 'ZONE-99' does not exist."},
    ],
    "evaluated_at": "2026-08-06T10:00:00+00:00",
    "input_payload": {"claim_id": "CLM-ZONE-301", "issuer_id": "USR-SUP-01", "zone_id": "ZONE-99"},
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
    "input_payload": {"claim_id": "CLM-101", "issuer_id": "USR-SUP-01", "zone_id": "ZONE-01"},
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


def test_blocked_screen_renders_for_real_no_go_claim():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/supervisor/blocked/CLM-EPTW-301")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    assert "CLM-EPTW-301" in body
    assert "R-PTW-01" in body
    # 2026-09-02, Frontline/Supervisor consistency Item 2:
    # authority_binding_id is now live-resolved (same resolve_authority()
    # call as assignedRole), not the persisted evidence["authority_binding_id"]
    # fixture value -- "BIND-999" (deliberately stale/mismatched in this
    # fixture) must never reach the page; "BIND-RTO-01" (R-PTW-01's real,
    # live-resolved binding) must.
    assert "BIND-999" not in body
    assert '"authority_binding_id": "BIND-RTO-01"' in body
    assert "ptw_precondition_check" in body
    assert '"/static/blocked-screen/blocked-screen.js"' in body
    # 2026-08-06, Task A: this route calls resolve_authority() for
    # assignedRole. R-PTW-01 now resolves to RTO (2026-08-18, direct
    # confirmation), superseding the earlier "deliberately unrouted"
    # state (see CLAUDE.md's Open Items for that history).
    assert '"assignedRole": "RTO"' in body


def test_blocked_screen_renders_resolved_role_for_zone_safety_no_go():
    """Task A's before/after case: R-ZONE-01 now shows a resolved
    assignedRole ("SA" -- the bare code, since no confirmed human-
    readable label for SA exists yet in src/core/roles.py's
    ROLE_TYPE_LABELS; see that module's own comment) where previously
    this route surfaced no role information at all, just the bare
    binding ID."""
    client = _client_with_record(ZONE_NO_GO_EVIDENCE)

    response = client.get("/supervisor/blocked/CLM-ZONE-301")

    assert response.status_code == 200
    body = response.text
    assert "CLM-ZONE-301" in body
    assert "R-ZONE-01" in body
    assert "BIND-SA-01" in body
    assert '"assignedRole": "SA"' in body


def test_blocked_screen_returns_404_for_unknown_claim():
    client = _client_with_record(None)

    response = client.get("/supervisor/blocked/CLM-DOES-NOT-EXIST")

    assert response.status_code == 404
    assert "CLM-DOES-NOT-EXIST" in response.json()["detail"]


def test_blocked_screen_returns_409_for_go_claim():
    """The Blocked Screen is a NO_GO-only surface -- a GO claim exists,
    but there's nothing blocked to show, which is a different failure
    mode than the claim not existing at all (404, above)."""
    client = _client_with_record(GO_EVIDENCE)

    response = client.get("/supervisor/blocked/CLM-101")

    assert response.status_code == 409


def test_blocked_screen_js_is_served_as_static_asset():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/static/blocked-screen/blocked-screen.js")

    assert response.status_code == 200
    assert "customElements.define" in response.text
