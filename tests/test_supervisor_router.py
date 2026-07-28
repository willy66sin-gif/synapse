"""
POST /supervisor/override tests.

Unlike tests/test_airlock.py's HTTP tests (which only ever exercise
malformed-body rejection and never reach the handler body), these
tests need the handler's actual authorization/existence-check
branches to run — so the fake session here is a real stub capable of
returning controlled query results per table, not just `yield None`.
No live Postgres/Redis is used; this stays consistent with the
existing "testable without database side-effects" principle by
injecting fake data at the session boundary instead.
"""
import hashlib
import json

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
    """Returns a canned row keyed by which table the query targets."""

    def __init__(self, issuer_row=None, adjudication_row=None):
        self._issuer_row = issuer_row
        self._adjudication_row = adjudication_row
        self.added = []
        self.committed = False

    async def execute(self, stmt):
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


def test_malformed_override_body_rejected():
    async def _fake_db_session():
        yield None

    app.dependency_overrides[get_db_session] = _fake_db_session
    with TestClient(app) as client:
        response = client.post("/supervisor/override", json={"claim_id": "CLM-102"})

    assert response.status_code == 422


def test_unauthenticated_issuer_rejected_with_403():
    stub = _StubSession(issuer_row=None, adjudication_row=None)
    with _client_with_stub(stub) as client:
        response = client.post("/supervisor/override", json=VALID_OVERRIDE)

    assert response.status_code == 403
    assert "Authority Failure" in response.json()["detail"]
    assert stub.added == []  # rejected: never persisted


def test_nonexistent_claim_rejected_with_404():
    issuer_row = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
    stub = _StubSession(issuer_row=issuer_row, adjudication_row=None)
    with _client_with_stub(stub) as client:
        response = client.post("/supervisor/override", json=VALID_OVERRIDE)

    assert response.status_code == 404
    assert "Override Rejected" in response.json()["detail"]
    assert stub.added == []  # rejected: never persisted


def test_valid_override_accepted_end_to_end():
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

    assert response.status_code == 200
    body = response.json()

    evidence = body["evidence"]
    assert evidence["type"] == "OverrideRecord"
    assert evidence["claim_id"] == "CLM-102"
    assert evidence["issuer_id"] == "USR-SUP-01"

    # Evidence-signature integrity, same style as tests/test_evidence.py
    unsigned = {k: v for k, v in evidence.items() if k != "sha256_signature"}
    expected_signature = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()
    assert evidence["sha256_signature"] == expected_signature

    # Accepted: persisted for real (via the stub session)
    assert len(stub.added) == 1
    assert stub.committed is True

    # Maestro wiring: both stub channels notified, escalation info present
    notifications = body["notifications"]
    channels = {n["channel"] for n in notifications}
    assert channels == {"whatsapp", "telegram"}
    for notification in notifications:
        assert notification["delivered"] is True
        assert "USR-SUP-01" in notification["detail"]
