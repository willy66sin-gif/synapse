"""
Project-scoping boundary tests (2026-09-15, Ring-Fencing Concept Note,
26 Aug 2026 -- Willy-authorized implementation): POST /airlock/claims'
cross-project reference check (src/airlock/project_check.py, wired into
src/airlock/router.py) and the plain missing-project_id 422.

Same entity-dispatch stubbing approach as tests/test_airlock_profile.py,
extended with an IssuerProject query shape alongside the existing
AuthorizedIssuer/IssuerRole/CertifiedProfileRecord shapes.
"""
import pytest
from fastapi.testclient import TestClient

from src.airlock.models import ProjectScopeRejectionAuditEntry
from src.airlock.schemas import WorkType
from src.core.models import AuthorizedIssuer, IssuerProject, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.main import app
from src.profiles.models import CertifiedProfileRecord
from src.profiles.schemas import ProfileLineage

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

PROFILE_IN_PROJECT_A = CertifiedProfileRecord(
    profile_id="SG-BC-2024",
    project_id="PROJ-A",
    jurisdiction_code="SG",
    version="2024.1",
    lineage=ProfileLineage.STANDALONE,
    base_profile_id=None,
    base_profile_version=None,
    parameters={},
    accountable_architect="Jane Tan, ARB-1234",
)


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _StubSession:
    """Entity-dispatch stub serving AuthorizedIssuer, IssuerRole,
    IssuerProject, and CertifiedProfileRecord queries off one session --
    same convention tests/test_airlock_profile.py already established,
    extended with IssuerProject for this pass."""

    def __init__(self, issuer_row=None, issuer_roles=None, issuer_projects=None, profile_row=None):
        self._issuer_row = issuer_row
        self._issuer_roles = issuer_roles or []
        self._issuer_projects = issuer_projects or []
        self._profile_row = profile_row
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AuthorizedIssuer:
            return _Result(row=self._issuer_row)
        if entity is IssuerRole:
            return _Result(rows=self._issuer_roles)
        if entity is IssuerProject:
            return _Result(rows=self._issuer_projects)
        if entity is CertifiedProfileRecord:
            return _Result(row=self._profile_row)
        raise AssertionError(f"unexpected query in test_airlock_project.py stub: {stmt}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


class _StubRedis:
    def __init__(self, zone_data=None):
        self._zone_data = zone_data or {}

    async def hgetall(self, key):
        if key.endswith(":sensor"):
            return {}
        return self._zone_data


def _client(session):
    async def _override_db():
        yield session

    async def _override_redis():
        yield _StubRedis(zone_data=VALID_LOW_HAZARD_ZONE)

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_redis_client] = _override_redis
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _claim(**overrides) -> dict:
    base = {
        "claim_id": "CLM-PROJECT-601",
        "timestamp": "2026-09-15T10:00:00Z",
        "project_id": "PROJ-A",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {},
        "work_type": WorkType.NOMINAL_CIVIL.value,
    }
    base.update(overrides)
    return base


def _project_scope_entry(rule_trace):
    return next(rule for rule in rule_trace if rule["rule_id"] == "project_scope_check")


# --- Missing project_id: plain 422, no reason_code, no evidence record ---


def test_missing_project_id_is_rejected_with_a_plain_structural_422():
    """project_id is a required ClaimPayload field -- a claim omitting
    it entirely never reaches submit_claim()'s body at all, rejected by
    FastAPI/Pydantic before any application code runs. No reason_code,
    no ProjectScopeRejectionRecord -- a Part 1B-class structural
    rejection, not a Part 1 R-PROJECT-01 adjudication failure (see
    docs/failure-modes.md's own Part 1B for this exact distinction)."""
    claim = _claim()
    del claim["project_id"]
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES)
    client = _client(session)

    response = client.post("/airlock/claims", json=claim)

    assert response.status_code == 422
    body = response.json()
    # Generic Pydantic validation-error shape (a list of {loc, msg, type}
    # entries) -- NOT the {"reason_code": ..., "message": ...} dict shape
    # every reason-code rejection (R-PROFILE-*, R-PROJECT-01, R-DEV-*)
    # uses. This is the concrete test for "structural rejection" vs.
    # "adjudicated/business-rule rejection" from docs/failure-modes.md's
    # own Part 1B.
    assert isinstance(body["detail"], list)
    assert session.added == []


# --- Cross-project reference: issuer recognized, wrong project ---


def test_issuer_not_authorized_for_claimed_project_is_rejected():
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        issuer_projects=["PROJ-B"],  # issuer is real, but not on PROJ-A
    )
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(project_id="PROJ-A"))

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["reason_code"] == "R-PROJECT-01"

    assert len(session.added) == 1
    rejection = session.added[0]
    assert isinstance(rejection, ProjectScopeRejectionAuditEntry)
    assert rejection.claim_id == "CLM-PROJECT-601"
    assert rejection.project_id == "PROJ-A"
    assert rejection.reason_code == "R-PROJECT-01"
    assert rejection.record["type"] == "ProjectScopeRejectionRecord"
    assert session.committed == 1


def test_issuer_authorized_for_claimed_project_proceeds_normally():
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        issuer_projects=["PROJ-A", "PROJ-B"],  # authorized on several, including the claimed one
    )
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(project_id="PROJ-A"))

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "GO"
    entry = _project_scope_entry(body["rule_trace"])
    assert entry["passed"] is True
    assert not any(isinstance(a, ProjectScopeRejectionAuditEntry) for a in session.added)


def test_unrecognized_issuer_is_not_preempted_by_project_scope_check():
    """An unrecognized issuer stays R-AUTH-01's job -- check_project_scope()
    must not fire (or mask R-AUTH-01 with a misleading R-PROJECT-01) when
    issuer_record is None, per src/airlock/project_check.py's own
    docstring."""
    session = _StubSession(issuer_row=None, issuer_roles=[], issuer_projects=[])
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(project_id="PROJ-A"))

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "NO_GO"
    assert body["reason_code"] == "R-AUTH-01"
    entry = _project_scope_entry(body["rule_trace"])
    assert entry["passed"] is True


# --- Cross-project reference: resolved profile belongs to a different project ---


def test_profile_belonging_to_a_different_project_is_rejected():
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        issuer_projects=["PROJ-A"],
        profile_row=PROFILE_IN_PROJECT_A,  # profile belongs to PROJ-A
    )
    client = _client(session)

    response = client.post(
        "/airlock/claims", json=_claim(project_id="PROJ-B", profile_id="SG-BC-2024")
    )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["reason_code"] == "R-PROJECT-01"

    assert len(session.added) == 1
    rejection = session.added[0]
    assert isinstance(rejection, ProjectScopeRejectionAuditEntry)
    assert rejection.project_id == "PROJ-B"


def test_profile_belonging_to_the_same_project_proceeds_normally():
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        issuer_projects=["PROJ-A"],
        profile_row=PROFILE_IN_PROJECT_A,
    )
    client = _client(session)

    response = client.post(
        "/airlock/claims", json=_claim(project_id="PROJ-A", profile_id="SG-BC-2024")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "GO"
