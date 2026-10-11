"""
POST /profiles tests (Step 2, 2026-10-11).

Same stub-session infrastructure as tests/test_doctrine_router.py: a
fake AsyncSession that records add()/commit() calls, simulates the
certified_profiles.profile_id primary key with the real
sqlalchemy.exc.IntegrityError, and answers the one lookup this route
makes (the base profile, for a BASE_ANNEX body) from a dict of canned
rows keyed by profile_id.

Fixture profiles are invented placeholders -- no real code, annex
value, or architect.
"""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.core.repository import get_db_session
from src.main import app
from src.profiles.models import CertifiedProfileRecord
from src.profiles.schemas import ProfileLineage

STANDALONE_PROFILE = {
    "profile_id": "PROJ-A-CODE",
    "project_id": "PROJ-A",
    "jurisdiction_code": "SG",
    "version": "2024.1",
    "lineage": "STANDALONE",
    "parameters": {"example_parameter": 1},
    "accountable_architect": "Jane Tan, ARB-1234",
}

GLOBAL_PROFILE = {**STANDALONE_PROFILE, "profile_id": "SHARED-BASE-CODE", "project_id": "GLOBAL"}

ANNEX_PROFILE = {
    "profile_id": "PROJ-A-ANNEX",
    "project_id": "PROJ-A",
    "jurisdiction_code": "SG",
    "version": "2024.1-a",
    "lineage": "BASE_ANNEX",
    "base_ref": {"base_profile_id": "SHARED-BASE-CODE", "base_profile_version": "2024.1"},
    "parameters": {"example_parameter": 2},
    "accountable_architect": "Jane Tan, ARB-1234",
}


def _base_row(project_id="GLOBAL", version="2024.1"):
    return CertifiedProfileRecord(
        profile_id="SHARED-BASE-CODE",
        project_id=project_id,
        jurisdiction_code="SG",
        version=version,
        lineage=ProfileLineage.STANDALONE,
        base_profile_id=None,
        base_profile_version=None,
        parameters={"example_parameter": 1},
        accountable_architect="Jane Tan, ARB-1234",
    )


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _StubSession:
    def __init__(self, existing_rows=None):
        self.added = []
        self.committed = []
        self.commit_count = 0
        self.rollback_count = 0
        self.lookups = []
        self._rows = {row.profile_id: row for row in (existing_rows or [])}
        self._pending = []

    async def execute(self, stmt):
        (profile_id,) = stmt.compile().params.values()
        self.lookups.append(profile_id)
        return _StubResult(self._rows.get(profile_id))

    def add(self, obj):
        self.added.append(obj)
        self._pending.append(obj)

    async def commit(self):
        for obj in self._pending:
            if isinstance(obj, CertifiedProfileRecord) and obj.profile_id in self._rows:
                self._pending = []
                raise IntegrityError(
                    "INSERT INTO certified_profiles",
                    {},
                    Exception("UNIQUE constraint failed: certified_profiles.profile_id"),
                )
        for obj in self._pending:
            if isinstance(obj, CertifiedProfileRecord):
                self._rows[obj.profile_id] = obj
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


def test_a_standalone_project_profile_is_persisted_and_evidenced():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=STANDALONE_PROFILE)

    assert response.status_code == 200
    evidence = response.json()

    assert evidence["type"] == "CertifiedProfileReceiptRecord"
    assert evidence["profile"] == {**STANDALONE_PROFILE, "base_ref": None}
    unsigned = {key: value for key, value in evidence.items() if key != "sha256_signature"}
    assert evidence["sha256_signature"] == hashlib.sha256(
        json.dumps(unsigned, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # STANDALONE: no base to look up.
    assert stub.lookups == []
    assert len(stub.added) == 2
    assert stub.commit_count == 2
    profile_row, receipt_row = stub.added
    assert profile_row.__class__.__name__ == "CertifiedProfileRecord"
    assert profile_row.project_id == "PROJ-A"
    assert profile_row.lineage == ProfileLineage.STANDALONE
    assert profile_row.base_profile_id is None
    assert profile_row.base_profile_version is None
    assert profile_row.parameters == {"example_parameter": 1}
    assert receipt_row.__class__.__name__ == "CertifiedProfileReceiptAuditEntry"
    assert receipt_row.project_id == "PROJ-A"
    assert receipt_row.record == evidence


def test_a_global_shared_code_is_created_through_the_same_route():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=GLOBAL_PROFILE)

    assert response.status_code == 200
    assert response.json()["profile"]["project_id"] == "GLOBAL"
    assert stub.added[0].project_id == "GLOBAL"
    assert stub.added[1].project_id == "GLOBAL"


def test_a_project_annex_on_a_global_base_is_accepted():
    stub = _StubSession(existing_rows=[_base_row(project_id="GLOBAL")])

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=ANNEX_PROFILE)

    assert response.status_code == 200
    assert stub.lookups == ["SHARED-BASE-CODE"]
    profile_row = stub.added[0]
    assert profile_row.lineage == ProfileLineage.BASE_ANNEX
    assert profile_row.base_profile_id == "SHARED-BASE-CODE"
    assert profile_row.base_profile_version == "2024.1"
    assert profile_row.project_id == "PROJ-A"


def test_a_project_annex_on_a_base_in_the_same_project_is_accepted():
    stub = _StubSession(existing_rows=[_base_row(project_id="PROJ-A")])

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=ANNEX_PROFILE)

    assert response.status_code == 200


def test_an_annex_on_another_projects_base_is_rejected_and_persists_nothing():
    stub = _StubSession(existing_rows=[_base_row(project_id="PROJ-B")])

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=ANNEX_PROFILE)

    assert response.status_code == 422
    assert "PROJ-B" in response.json()["detail"]
    assert stub.added == []
    assert stub.commit_count == 0


def test_an_annex_on_a_missing_base_is_rejected_and_persists_nothing():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=ANNEX_PROFILE)

    assert response.status_code == 422
    assert "SHARED-BASE-CODE" in response.json()["detail"]
    assert stub.added == []
    assert stub.commit_count == 0


def test_an_annex_pinned_to_a_version_the_base_is_not_at_is_rejected():
    stub = _StubSession(existing_rows=[_base_row(version="2025.0")])

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=ANNEX_PROFILE)

    assert response.status_code == 422
    assert "2024.1" in response.json()["detail"]
    assert stub.added == []


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in STANDALONE_PROFILE.items() if key != "project_id"},
        {**STANDALONE_PROFILE, "project_id": None},
        {key: value for key, value in STANDALONE_PROFILE.items() if key != "accountable_architect"},
        {**STANDALONE_PROFILE, "lineage": "BASE_ANNEX"},
        {**STANDALONE_PROFILE, "base_ref": ANNEX_PROFILE["base_ref"]},
        {**STANDALONE_PROFILE, "is_global": True},
    ],
)
def test_malformed_profile_422s_and_persists_nothing(body):
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        response = client.post("/profiles", json=body)

    assert response.status_code == 422
    assert stub.added == []
    assert stub.commit_count == 0


def test_duplicate_profile_id_returns_409_and_persists_no_duplicate():
    stub = _StubSession()

    with _client_with_stub(stub) as client:
        first_response = client.post("/profiles", json=STANDALONE_PROFILE)
        second_response = client.post("/profiles", json={**STANDALONE_PROFILE, "version": "2025.0"})

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert "PROJ-A-CODE" in second_response.json()["detail"]

    assert stub.rollback_count == 1
    assert stub.commit_count == 2
    committed_by_type = [obj.__class__.__name__ for obj in stub.committed]
    assert committed_by_type.count("CertifiedProfileRecord") == 1
    assert committed_by_type.count("CertifiedProfileReceiptAuditEntry") == 1
