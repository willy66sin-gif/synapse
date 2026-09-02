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
from sqlalchemy.exc import IntegrityError

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
    """
    Records every add()/commit() call; never touches a real database.

    Also simulates the real doctrine_submissions.submission_id primary-
    key uniqueness constraint (2026-09-02 duplicate-handling follow-up):
    commit() raises the real sqlalchemy.exc.IntegrityError -- not a
    stand-in exception -- if a pending DoctrineSubmissionRecord's
    submission_id was already committed by an earlier call, so
    tests below exercise the router's actual `except IntegrityError`
    handling, not a mocked substitute for it.

    self.committed is the authoritative "what a real database would
    actually hold" ledger: self.added records every add() call
    regardless of outcome (including a row whose commit() later
    failed), self.committed only ever gains rows from a commit() that
    succeeded.
    """

    def __init__(self):
        self.added = []
        self.committed = []
        self.commit_count = 0
        self.rollback_count = 0
        self._pending = []
        self._existing_submission_ids = set()

    def add(self, obj):
        self.added.append(obj)
        self._pending.append(obj)

    async def commit(self):
        for obj in self._pending:
            if obj.__class__.__name__ == "DoctrineSubmissionRecord" and obj.submission_id in self._existing_submission_ids:
                self._pending = []
                raise IntegrityError(
                    "INSERT INTO doctrine_submissions",
                    {},
                    Exception("UNIQUE constraint failed: doctrine_submissions.submission_id"),
                )
        for obj in self._pending:
            if obj.__class__.__name__ == "DoctrineSubmissionRecord":
                self._existing_submission_ids.add(obj.submission_id)
            self.committed.append(obj)
        self.commit_count += 1
        self._pending = []

    async def rollback(self):
        self.rollback_count += 1
        self._pending = []


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


def test_duplicate_submission_id_returns_409_not_500_and_persists_no_duplicate():
    """
    2026-09-02 duplicate-handling follow-up: a repeated submission_id
    used to raise an unhandled IntegrityError (500). It must now come
    back as a clean 409, and the rejected second attempt must create
    neither a second DoctrineSubmissionRecord row nor a second
    DoctrineSubmissionReceiptAuditEntry -- checked against
    stub.committed, the ledger of rows a real database would actually
    hold, not just stub.added (every add() attempt, including the
    rolled-back one).
    """
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        first_response = client.post("/doctrine/submissions", json=VALID_SUBMISSION)
        second_response = client.post("/doctrine/submissions", json=VALID_SUBMISSION)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert "SUB-0001" in second_response.json()["detail"]

    # The failed second attempt rolled back and never reached evidence
    # emission -- one rollback, and only the first attempt's two
    # commits (submission + receipt) ever succeeded.
    assert stub.rollback_count == 1
    assert stub.commit_count == 2

    committed_by_type = [obj.__class__.__name__ for obj in stub.committed]
    assert committed_by_type.count("DoctrineSubmissionRecord") == 1
    assert committed_by_type.count("DoctrineSubmissionReceiptAuditEntry") == 1
