"""
GET /supervisor/blocked/{claim_id}/status tests (2026-09-02, Frontline/
Supervisor consistency follow-up, Item 3 -- polling parity).

Same stubbing approach as tests/test_frontline_router.py's status-
endpoint section (the pattern this endpoint reuses, not reinvents): a
fake AsyncSession serving the three distinct query shapes
blocked_screen_status() issues (AdjudicationAuditEntry,
AuthorizedIssuer, IssuerRole), plus a fake Redis client for zone state.
No live Postgres/Redis.
"""
import pytest
from fastapi.testclient import TestClient

from src.airlock.schemas import WorkType
from src.core.models import AuthorizedIssuer, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.evidence.models import AdjudicationAuditEntry
from src.main import app

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

# Persisted GO -- zone state will have since changed underneath it in
# several tests below, same "stale persisted decision" setup as
# tests/test_frontline_router.py's STALE_GO_EVIDENCE.
STALE_GO_EVIDENCE = {
    "claim_id": "CLM-SUP-FRESH-401",
    "decision": "GO",
    "reason": "Claim 'CLM-SUP-FRESH-401' cleared for execution in ZONE-01.",
    "reason_code": None,
    "authority_binding_id": None,
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
    "evaluated_at": "2026-08-30T09:00:00+00:00",
    "input_payload": {
        "claim_id": "CLM-SUP-FRESH-401",
        "timestamp": "2026-08-30T09:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {},
        "work_type": WorkType.NOMINAL_CIVIL.value,
        "ptw_context": None,
        "is_design_alteration": False,
        "alteration_description": None,
    },
    "sha256_signature": "irrelevant-for-this-test",
}


class _StatusStubResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._row


class _StatusStubSession:
    def __init__(self, evidence_row=None, issuer_row=None, issuer_roles=None):
        self._evidence_row = evidence_row
        self._issuer_row = issuer_row
        self._issuer_roles = issuer_roles or []
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AdjudicationAuditEntry:
            latest = self.added[-1] if self.added else self._evidence_row
            return _StatusStubResult(latest)
        if entity is AuthorizedIssuer:
            return _StatusStubResult(self._issuer_row)
        if entity is IssuerRole:
            return _StatusStubResult(self._issuer_roles)
        raise AssertionError(f"blocked_screen_status() issued an unexpected query: {stmt}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


class _StatusStubRedis:
    def __init__(self, zone_data=None):
        self._zone_data = zone_data

    async def hgetall(self, key):
        if key.endswith(":sensor"):
            return {}
        return self._zone_data or {}


def _evidence_row(record):
    return AdjudicationAuditEntry(claim_id=record["claim_id"], decision=record["decision"], record=record)


def _status_client(evidence_row=None, issuer_row=None, issuer_roles=None, zone_data=None, session=None):
    db_session = session or _StatusStubSession(evidence_row=evidence_row, issuer_row=issuer_row, issuer_roles=issuer_roles)

    async def _override_db_session():
        yield db_session

    async def _override_redis_client():
        yield _StatusStubRedis(zone_data=zone_data)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_status_endpoint_reflects_fresh_state_not_stale_persisted_decision():
    """The claim was persisted as GO. Zone state has since gone bad --
    the poll endpoint must re-adjudicate and report the fresh NO_GO, not
    the stale persisted GO. Same freshness guarantee
    tests/test_frontline_router.py's identically-named test already
    proves for Frontline."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=None,  # zone no longer exists -> R-ZONE-01
    )

    response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["claim_id"] == "CLM-SUP-FRESH-401"
    assert body["evidence"]["decision"] == "NO_GO"
    assert body["evidence"]["reason_code"] == "R-ZONE-01"


def test_status_endpoint_returns_200_for_a_go_transition_not_409():
    """Deliberate difference from the HTML route: the HTML route 409s on
    GO (NO_GO-only surface), but this status endpoint must NOT -- gating
    it the same way would make a NO_GO -> GO transition poll come back
    as a permanent 409 and freeze the screen exactly when freshness
    matters most."""
    no_go_persisted = {**STALE_GO_EVIDENCE, "decision": "NO_GO", "reason_code": "R-ZONE-01"}
    client = _status_client(
        evidence_row=_evidence_row(no_go_persisted),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=VALID_LOW_HAZARD_ZONE,  # zone restored -> GO
    )

    response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")

    assert response.status_code == 200
    assert response.json()["evidence"]["decision"] == "GO"


def test_status_endpoint_unchanged_poll_writes_no_evidence():
    session = _StatusStubSession(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE), issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES
    )
    client = _status_client(zone_data=VALID_LOW_HAZARD_ZONE, session=session)  # stays GO

    for _ in range(3):
        response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")
        assert response.json()["evidence"]["decision"] == "GO"

    assert session.added == []
    assert session.committed == 0


def test_status_endpoint_transition_writes_exactly_one_evidence_record():
    session = _StatusStubSession(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE), issuer_row=SUPERINTENDENT_ROW, issuer_roles=SUPERINTENDENT_ROLES
    )
    client = _status_client(zone_data=None, session=session)  # zone gone -> NO_GO

    response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")

    assert response.status_code == 200
    assert response.json()["evidence"]["decision"] == "NO_GO"
    assert len(session.added) == 1
    assert session.committed == 1
    written = session.added[0].record
    assert written["decision"] == "NO_GO"
    assert written["type"] == "AdjudicationRecord"


def test_status_endpoint_authority_binding_id_and_role_come_from_the_same_call():
    """Item 2's fix applies to this new endpoint too -- not just the
    HTML route."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=None,  # zone gone -> NO_GO / R-ZONE-01 -> SA
    )

    response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")

    body = response.json()
    assert body["evidence"]["authority_binding_id"] == "BIND-SA-01"
    assert body["assignedRole"] == "SA"


def test_status_endpoint_response_shape_matches_html_route_payload():
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=VALID_LOW_HAZARD_ZONE,
    )

    response = client.get("/supervisor/blocked/CLM-SUP-FRESH-401/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"evidence", "assignedRole", "escalationContact", "issuerId", "overrideEndpoint"}
    assert set(body["evidence"].keys()) == {
        "claim_id",
        "decision",
        "reason",
        "reason_code",
        "authority_binding_id",
        "rule_trace",
        "evaluated_at",
    }


def test_status_endpoint_returns_404_for_unknown_claim():
    client = _status_client(evidence_row=None)

    response = client.get("/supervisor/blocked/CLM-DOES-NOT-EXIST/status")

    assert response.status_code == 404
    assert "CLM-DOES-NOT-EXIST" in response.json()["detail"]
