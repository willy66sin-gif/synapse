"""
POST /doctrine/submissions tests (Tier 2 CORENET X Parallel Entry,
2026-09-02).

Same stub-session infrastructure as tests/test_supervisor_router.py:
a fake AsyncSession that records add()/commit() calls instead of
touching a real database, so these tests exercise the real FastAPI
route (validation, receipt_timestamp/staleness computation, evidence
signing) without needing Postgres.
"""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.core.repository import get_db_session
from src.main import app

VALID_SUBMISSION = {
    "submission_id": "SUB-0001",
    "submitting_party_id": "Acme Architects",
    "jurisdiction_code": "SG",
    "citations": ["SS EN 1992-1-1", "SS 555:2016"],
    "ambiguity_resolution_notes": "Local wind-load clause interpreted per SCDF guidance letter dated 2026-06-01.",
    "submitted_at": "2026-08-12T09:30:00",
    "signed_off": True,
    "corenet_x_reference": "CNX-2026-00417",
    "corenet_x_gateway": "DESIGN",
    "corenet_x_approval_date": "2026-08-01",
    "entered_by": "QP",
}


class _StubSession:
    """Records every add()/commit() call; never touches a real database."""

    def __init__(self):
        self.added = []
        self.commit_count = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1


def _client_with_stub(stub_session):
    async def _override_db_session():
        yield stub_session

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_a_complete_submission_is_accepted_and_evidenced():
    stub = _StubSession()
    before = datetime.now(timezone.utc)

    with _client_with_stub(stub) as client:
        response = client.post("/doctrine/submissions", json=VALID_SUBMISSION)

    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    evidence = response.json()

    assert evidence["type"] == "DoctrineSubmissionReceiptRecord"
    assert evidence["submission"]["submission_id"] == "SUB-0001"
    assert evidence["submission"]["corenet_x_gateway"] == "DESIGN"
    assert "sha256_signature" in evidence

    # receipt_timestamp is server-set, between the two bounds taken
    # immediately around the request -- never the client-supplied value
    # (there wasn't one) and never absent.
    receipt_timestamp = datetime.fromisoformat(evidence["submission"]["receipt_timestamp"])
    assert before <= receipt_timestamp <= after

    # staleness_days = receipt_timestamp minus corenet_x_approval_date
    # (2026-08-01), computed server-side, not supplied by the client.
    expected_staleness = (receipt_timestamp.date() - date(2026, 8, 1)).days
    assert evidence["staleness_days"] == expected_staleness

    # Two rows persisted: the submission itself, and its own signed
    # receipt evidence -- two separate commits, same "distinct evidence
    # types live in distinct tables" convention as every other domain.
    assert len(stub.added) == 2
    assert stub.commit_count == 2
    submission_row, receipt_row = stub.added
    assert submission_row.__class__.__name__ == "DoctrineSubmissionRecord"
    assert receipt_row.__class__.__name__ == "DoctrineSubmissionReceiptAuditEntry"


def test_client_supplied_receipt_timestamp_422s_at_schema_boundary():
    """Same fail-closed discipline as src/airlock/router.py -- a
    structurally invalid body (including a client-supplied
    receipt_timestamp) never reaches the handler body, so nothing is
    ever persisted."""
    stub = _StubSession()
    body = {**VALID_SUBMISSION, "receipt_timestamp": "2026-08-12T09:30:00Z"}

    with _client_with_stub(stub) as client:
        response = client.post("/doctrine/submissions", json=body)

    assert response.status_code == 422
    assert stub.added == []
    assert stub.commit_count == 0


def test_missing_required_field_422s_and_persists_nothing():
    stub = _StubSession()
    body = {key: value for key, value in VALID_SUBMISSION.items() if key != "corenet_x_reference"}

    with _client_with_stub(stub) as client:
        response = client.post("/doctrine/submissions", json=body)

    assert response.status_code == 422
    assert stub.added == []
    assert stub.commit_count == 0


def test_unknown_field_422s_and_persists_nothing():
    stub = _StubSession()
    body = {**VALID_SUBMISSION, "review_status": "APPROVED"}

    with _client_with_stub(stub) as client:
        response = client.post("/doctrine/submissions", json=body)

    assert response.status_code == 422
    assert stub.added == []
    assert stub.commit_count == 0
