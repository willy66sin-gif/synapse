"""
GO Freshness Phase 3a, Part A tests: profile_id grace-period behavior
at POST /airlock/claims (src/airlock/profile_check.py's decision logic,
wired into src/airlock/router.py).

Same dependency-override stubbing approach as tests/test_airlock_maestro.py,
extended with a CertifiedProfileRecord query shape
(src/profiles/repository.py's fetch_certified_profile()) alongside the
existing AuthorizedIssuer/IssuerRole shapes this file's stub session
already needs to serve -- entity-dispatch stub, same pattern
tests/test_frontline_router.py's _StatusStubSession already established
for a session serving more than one query shape off one fake AsyncSession.
"""
import pytest
from fastapi.testclient import TestClient

from src.airlock.models import ProfileRejectionAuditEntry
from src.airlock.schemas import WorkType
from src.config import settings
from src.core.models import AuthorizedIssuer, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.main import app
from src.profiles.models import CertifiedProfileRecord
from src.profiles.schemas import ProfileLineage

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

VALID_PROFILE_ROW = CertifiedProfileRecord(
    profile_id="SG-BC-2024",
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
    """Entity-dispatch stub serving AuthorizedIssuer, IssuerRole, and
    (new for this pass) CertifiedProfileRecord queries off one session --
    tracks every add()/commit() so tests can assert exactly what got
    persisted (an AdjudicationAuditEntry on the normal path, a
    ProfileRejectionAuditEntry on a fail-closed profile rejection)."""

    def __init__(self, issuer_row=None, issuer_roles=None, profile_row=None):
        self._issuer_row = issuer_row
        self._issuer_roles = issuer_roles or []
        self._profile_row = profile_row
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AuthorizedIssuer:
            return _Result(row=self._issuer_row)
        if entity is IssuerRole:
            return _Result(rows=self._issuer_roles)
        if entity is CertifiedProfileRecord:
            return _Result(row=self._profile_row)
        raise AssertionError(f"unexpected query in test_airlock_profile.py stub: {stmt}")

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


@pytest.fixture(autouse=True)
def _reset_enforcement_flag():
    """profile_id_enforcement_enabled defaults False -- reset after every
    test so one test flipping it never leaks into the next (settings is a
    module-level singleton shared with src/airlock/router.py's import)."""
    original = settings.profile_id_enforcement_enabled
    yield
    settings.profile_id_enforcement_enabled = original


def _claim(**overrides) -> dict:
    base = {
        "claim_id": "CLM-PROFILE-501",
        "timestamp": "2026-08-31T10:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {},
        "work_type": WorkType.NOMINAL_CIVIL.value,
    }
    base.update(overrides)
    return base


def _profile_check_entry(rule_trace):
    return next(rule for rule in rule_trace if rule["rule_id"] == "profile_check")


# --- Flag off (default) ---


def test_flag_off_no_profile_id_proceeds_with_grace_period_note():
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES)
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim())

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "GO"
    entry = _profile_check_entry(body["rule_trace"])
    assert entry["passed"] is True
    assert "no profile_id was submitted" in entry["reason"]
    assert "profile_id_enforcement_enabled is False" in entry["reason"]
    assert not any(isinstance(a, ProfileRejectionAuditEntry) for a in session.added)


def test_flag_off_valid_profile_id_is_resolved_and_used():
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES, profile_row=VALID_PROFILE_ROW
    )
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(profile_id="SG-BC-2024"))

    assert response.status_code == 200
    body = response.json()
    entry = _profile_check_entry(body["rule_trace"])
    assert entry["passed"] is True
    assert "SG-BC-2024" in entry["reason"]
    assert "validated" in entry["reason"]
    assert body["input_payload"]["profile_id"] == "SG-BC-2024"


def test_flag_off_unresolvable_profile_id_does_not_block_but_is_noted():
    """Design call this pass had to make (not decided upstream): during
    the grace period, an unresolvable profile_id does NOT reject the
    claim. Reasoning -- the entire point of defaulting the enforcement
    flag off is that nothing gets rejected because of this new
    dimension until Willy explicitly turns it on (see
    src/airlock/profile_check.py's module docstring); an early adopter
    who supplies a wrong/typo'd profile_id ahead of enforcement should
    not be penalized any more than one who supplies nothing at all. The
    mismatch is still recorded in rule_trace -- auditable, not silently
    dropped -- just not fail-closed."""
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES, profile_row=None)
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(profile_id="SG-DOES-NOT-EXIST"))

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "GO"
    entry = _profile_check_entry(body["rule_trace"])
    assert entry["passed"] is True
    assert "does not resolve" in entry["reason"]
    assert "not enforced" in entry["reason"]
    assert not any(isinstance(a, ProfileRejectionAuditEntry) for a in session.added)


# --- Flag on ---


def test_flag_on_missing_profile_id_fails_closed():
    settings.profile_id_enforcement_enabled = True
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES)
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim())

    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "R-PROFILE-01"

    assert len(session.added) == 1
    rejection = session.added[0]
    assert isinstance(rejection, ProfileRejectionAuditEntry)
    assert rejection.claim_id == "CLM-PROFILE-501"
    assert rejection.profile_id is None
    assert rejection.reason_code == "R-PROFILE-01"
    assert rejection.record["type"] == "ProfileRejectionRecord"
    assert session.committed == 1


def test_flag_on_unresolvable_profile_id_fails_closed():
    settings.profile_id_enforcement_enabled = True
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES, profile_row=None)
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(profile_id="SG-DOES-NOT-EXIST"))

    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "R-PROFILE-02"

    assert len(session.added) == 1
    rejection = session.added[0]
    assert rejection.profile_id == "SG-DOES-NOT-EXIST"
    assert rejection.reason_code == "R-PROFILE-02"


def test_flag_on_valid_profile_id_proceeds_normally():
    settings.profile_id_enforcement_enabled = True
    session = _StubSession(
        issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES, profile_row=VALID_PROFILE_ROW
    )
    client = _client(session)

    response = client.post("/airlock/claims", json=_claim(profile_id="SG-BC-2024"))

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "GO"
    entry = _profile_check_entry(body["rule_trace"])
    assert entry["passed"] is True
    assert not any(isinstance(a, ProfileRejectionAuditEntry) for a in session.added)


def test_flag_on_missing_profile_id_never_reaches_adjudicate():
    """Airlock-upstream-of-Core ordering check: a claim rejected for a
    missing profile_id must never produce an AdjudicationRecord at all --
    only ONE row gets added (the rejection), not two."""
    settings.profile_id_enforcement_enabled = True
    session = _StubSession(issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES)
    client = _client(session)

    client.post("/airlock/claims", json=_claim())

    assert len(session.added) == 1
    assert isinstance(session.added[0], ProfileRejectionAuditEntry)
