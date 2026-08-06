"""
POST /supervisor/override tests -- retirement (2026-08-05).

Per CLAUDE.md's Supervisor Override Retirement principle: the endpoint
now returns 410 Gone unconditionally, regardless of issuer/claim
state, with zero database access. These tests replace the old
403/404/200 behavior tests (removed -- that behavior no longer
exists), but keep the same stub-session infrastructure to positively
prove the retired handler never touches the database at all: it never
executes a query, never adds a row, never commits.
"""
import pytest
from fastapi.testclient import TestClient

from src.core.models import AuthorizedIssuer
from src.core.repository import get_db_session
from src.evidence.models import AdjudicationAuditEntry
from src.main import app

VALID_OVERRIDE = {
    "claim_id": "CLM-102",
    "issuer_id": "USR-SUP-01",
    "justification": "Zone re-inspected and cleared by site safety officer.",
    "timestamp": "2026-07-28T12:00:00Z",
}


class _StubResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _StubSession:
    """Returns a canned row keyed by which table the query targets.
    For these retirement tests, execute() should never be called at
    all -- retained from the pre-retirement test suite so a regression
    that starts querying again would be caught immediately."""

    def __init__(self, issuer_row=None, adjudication_row=None):
        self._issuer_row = issuer_row
        self._adjudication_row = adjudication_row
        self.executed = False
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        self.executed = True
        table_name = stmt.column_descriptions[0]["entity"].__tablename__
        if table_name == "authorized_issuers":
            return _StubResult(self._issuer_row)
        if table_name == "adjudication_records":
            return _StubResult(self._adjudication_row)
        return _StubResult(None)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _client_with_stub(stub_session):
    async def _override_db_session():
        yield stub_session

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_malformed_override_body_still_422s_at_schema_boundary():
    """Unrelated to retirement: a structurally invalid body still fails
    closed via Pydantic before the (now-410) handler body ever runs --
    same fail-closed discipline as src/airlock/router.py."""
    async def _fake_db_session():
        yield None

    app.dependency_overrides[get_db_session] = _fake_db_session
    with TestClient(app) as client:
        response = client.post("/supervisor/override", json={"claim_id": "CLM-102"})

    assert response.status_code == 422


def test_override_returns_410_regardless_of_unauthenticated_issuer():
    stub = _StubSession(issuer_row=None, adjudication_row=None)
    with _client_with_stub(stub) as client:
        response = client.post("/supervisor/override", json=VALID_OVERRIDE)

    assert response.status_code == 410
    assert "retired" in response.json()["detail"].lower()
    assert stub.executed is False  # retired: never even queries the database


def test_override_returns_410_regardless_of_nonexistent_claim():
    issuer_row = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
    stub = _StubSession(issuer_row=issuer_row, adjudication_row=None)
    with _client_with_stub(stub) as client:
        response = client.post("/supervisor/override", json=VALID_OVERRIDE)

    assert response.status_code == 410
    assert stub.executed is False


def test_override_returns_410_even_for_a_well_formed_previously_valid_request():
    """The exact request that used to be accepted end-to-end (200,
    persisted, Maestro-notified) now gets 410 and has none of those
    side effects -- retirement means retirement, not a narrower gate."""
    issuer_row = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
    original_evidence = {
        "claim_id": "CLM-102",
        "decision": "NO_GO",
        "reason": "Safety Violation: Zone 'ZONE-02' does not exist.",
        "rule_trace": [],
        "evaluated_at": "2026-07-28T10:05:00+00:00",
        "input_payload": {},
        "sha256_signature": "irrelevant-for-this-test",
    }
    adjudication_row = AdjudicationAuditEntry(claim_id="CLM-102", decision="NO_GO", record=original_evidence)
    stub = _StubSession(issuer_row=issuer_row, adjudication_row=adjudication_row)

    with _client_with_stub(stub) as client:
        response = client.post("/supervisor/override", json=VALID_OVERRIDE)

    assert response.status_code == 410
    assert "/airlock/claims" in response.json()["detail"]  # points at the replacement mechanism

    # No side effects at all: no query, no persisted row, no commit.
    assert stub.executed is False
    assert stub.added == []
    assert stub.committed is False
