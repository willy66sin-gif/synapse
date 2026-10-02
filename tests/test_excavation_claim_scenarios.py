"""
Excavation claim scenarios through the real POST /airlock/claims HTTP layer.

Three scenarios for a work_type=EXCAVATION claim in ZONE-B3:
  1. GO path -- issuer holds RTO and SA, well-formed APPROVED PtwContext.
  2. SA-only issuer -- same claim, NO_GO at Rule 0 (R-PTW-01), because
     verify_ptw_precondition() requires RTO (src/core/rules.py's
     GATE_ADMISSIBLE_ROLES[REASON_CODE_PTW_PRECONDITION]).
  3. The original malformed field-submitted payload -- rejected at the
     Pydantic 422 boundary; observes what evidence exists afterwards.

Stubbing approach copied from tests/test_airlock_maestro.py (fake
AsyncSession/Redis via FastAPI dependency_overrides, recording Maestro
adapter spies, no live Postgres/Redis). Copied rather than imported:
tests/ is not a package. The one deliberate difference is that
_client_with_stubs() here keeps a handle on every _StubSession it
yields, so tests can inspect what was persisted (session.added).

PtwContext window: verify_ptw_precondition() compares valid_from/
valid_until against datetime.now(timezone.utc), not the claim's own
timestamp. So, mirroring tests/test_core_eptw.py's NOW +/- 1h window,
the claim timestamp is set to NOW as well -- the window then covers
both the claim timestamp and evaluation time.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import src.airlock.router as airlock_router
from src.airlock.schemas import WorkType
from src.core.models import AuthorizedIssuer, IssuerProject, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.evidence.models import AdjudicationAuditEntry
from src.main import app
from src.maestro.schemas import DeliveryResult

# Same issuer row / zone hash shapes as tests/test_airlock_maestro.py.
SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

RTO_AND_SA_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
SA_ONLY_ROLES = [AuthorityRoleType.SA]

NOW = datetime.now(timezone.utc)
CLAIM_TIMESTAMP = NOW.isoformat()
VALID_FROM = (NOW - timedelta(hours=1)).isoformat()
VALID_UNTIL = (NOW + timedelta(hours=1)).isoformat()


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value


class _StubSession:
    def __init__(self, issuer_row=None, issuer_roles=None, issuer_projects=None):
        self._issuer_row = issuer_row
        self._issuer_roles = issuer_roles or []
        self._issuer_projects = issuer_projects if issuer_projects is not None else ["PROJ-TEST-01"]
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AuthorizedIssuer:
            return _StubResult(self._issuer_row)
        if entity is IssuerRole:
            return _StubResult(self._issuer_roles)
        if entity is IssuerProject:
            return _StubResult(self._issuer_projects)
        raise AssertionError(f"submit_claim() issued an unexpected query: {stmt}")

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class _StubRedis:
    def __init__(self, zone_data=None):
        self._zone_data = zone_data or {}

    async def hgetall(self, key):
        return self._zone_data


def _make_recording_adapter(label: str, calls: list):
    class _RecordingAdapter:
        def send_alert(self, alert):
            calls.append((label, alert))
            return DeliveryResult(claim_id=alert.claim_id, channel=label, delivered=True, detail="recorded")

    return _RecordingAdapter


@pytest.fixture
def maestro_calls(monkeypatch):
    calls: list = []
    monkeypatch.setattr(airlock_router, "WhatsAppAdapter", _make_recording_adapter("whatsapp", calls))
    monkeypatch.setattr(airlock_router, "TelegramAdapter", _make_recording_adapter("telegram", calls))
    return calls


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client_with_stubs(sessions: list, issuer_row=None, zone_data=None, issuer_roles=None):
    async def _override_db_session():
        session = _StubSession(issuer_row=issuer_row, issuer_roles=issuer_roles)
        sessions.append(session)
        yield session

    async def _override_redis_client():
        yield _StubRedis(zone_data=zone_data)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


def _ptw_context(**overrides) -> dict:
    base = {
        "ptw_id": "PTW-2026-9902",
        "status": "APPROVED",
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
        "permit_type": WorkType.EXCAVATION.value,
        "zone_id": "ZONE-B3",
        "issuer_id": "USR-SUP-01",
    }
    base.update(overrides)
    return base


def _excavation_claim(**overrides) -> dict:
    base = {
        "claim_id": "CLM-20261002-101",
        "timestamp": CLAIM_TIMESTAMP,
        "project_id": "PROJ-TEST-01",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-B3",
        "action_type": "EXCAVATION_WORK",
        "payload_data": {},
        "work_type": WorkType.EXCAVATION.value,
        "ptw_context": _ptw_context(),
    }
    base.update(overrides)
    return base


def _persisted_audit_entries(sessions: list) -> list:
    return [obj for s in sessions for obj in s.added if isinstance(obj, AdjudicationAuditEntry)]


def _assert_signature_valid(evidence: dict) -> None:
    # Same recomputation as tests/test_evidence.py's signature tests.
    unsigned = {k: v for k, v in evidence.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()
    assert evidence["sha256_signature"] == expected


def test_excavation_go_path_rto_and_sa_issuer(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(
        sessions, issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE, issuer_roles=RTO_AND_SA_ROLES
    )

    response = client.post("/airlock/claims", json=_excavation_claim())

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["type"] == "AdjudicationRecord"
    assert evidence["decision"] == "GO"
    assert evidence["reason_code"] is None
    assert evidence["authority_binding_id"] is None

    trace = evidence["rule_trace"]
    assert [entry["rule_id"] for entry in trace] == [
        "ptw_precondition_check",
        "authority_check",
        "zone_safety_check",
        "profile_check",
        "project_scope_check",
    ]
    assert trace[0]["passed"] is True
    assert trace[0]["reason"] == "Permit 'PTW-2026-9902' validated for 'EXCAVATION'."
    assert trace[1]["passed"] is True
    assert trace[2]["passed"] is True

    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions)
    assert len(persisted) == 1
    assert persisted[0].decision == "GO"
    assert persisted[0].record["sha256_signature"] == evidence["sha256_signature"]

    assert maestro_calls == []


def test_excavation_sa_only_issuer_fails_closed_at_rule_0(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(
        sessions, issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE, issuer_roles=SA_ONLY_ROLES
    )

    response = client.post("/airlock/claims", json=_excavation_claim())

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "NO_GO"
    assert evidence["reason_code"] == "R-PTW-01"
    assert evidence["authority_binding_id"] == ["BIND-RTO-01"]

    # Evaluation stops at Rule 0: authority_check and zone_safety_check
    # never run. The two trailing entries are appended by the router
    # after adjudicate() returns (src/airlock/router.py), not Core rules.
    trace = evidence["rule_trace"]
    assert [entry["rule_id"] for entry in trace] == [
        "ptw_precondition_check",
        "profile_check",
        "project_scope_check",
    ]
    assert trace[0]["passed"] is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION: Issuer does not hold an admissible role" in trace[0]["reason"]
    assert "['RTO']" in trace[0]["reason"]

    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions)
    assert len(persisted) == 1
    assert persisted[0].record["reason_code"] == "R-PTW-01"

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.decision == "NO_GO"
        assert alert.reason_code == "R-PTW-01"
        assert alert.conflicting_condition.rule_id == "ptw_precondition_check"
        assert alert.authority_binding_id == ["BIND-RTO-01"]
        assert alert.assigned_role == ["RTO"]


def test_original_malformed_excavation_payload_rejected_with_422(maestro_calls, monkeypatch):
    sessions: list = []
    client = _client_with_stubs(
        sessions, issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE, issuer_roles=SA_ONLY_ROLES
    )

    emitted: list = []
    for name in ("emit_evidence", "emit_profile_rejection_evidence", "emit_project_scope_rejection_evidence"):
        original = getattr(airlock_router, name)
        monkeypatch.setattr(
            airlock_router, name, lambda *a, _n=name, _o=original, **kw: emitted.append(_n) or _o(*a, **kw)
        )

    payload = {
        "claim_id": "CLM-20261002-101",
        "action_type": "HIGH_RISK_EXCAVATION",
        "zone_id": "ZONE-B3",
        "authority_role": "SA",
        "ptw_context": {"eptw_id": "PTW-2026-9902", "status": "APPROVED"},
        "telemetry": {"excavation_depth_meters": 2.1, "ground_disturbance_detected": True},
    }

    response = client.post("/airlock/claims", json=payload)

    assert response.status_code == 422
    errors = {(err["type"], tuple(err["loc"])) for err in response.json()["detail"]}
    assert ("extra_forbidden", ("body", "authority_role")) in errors
    assert ("extra_forbidden", ("body", "telemetry")) in errors
    assert ("extra_forbidden", ("body", "ptw_context", "eptw_id")) in errors
    for field in ("timestamp", "project_id", "issuer_id", "authority_level", "payload_data", "work_type"):
        assert ("missing", ("body", field)) in errors

    # RECORDS CURRENT BEHAVIOUR -- OPEN POLICY QUESTION, NOT AN ENDORSEMENT.
    # A schema-level 422 at the Airlock currently produces no signed
    # evidence record of any type: FastAPI rejects the body before
    # submit_claim() runs, and src/main.py registers no
    # RequestValidationError handler. Whether a rejected submission
    # should itself leave an audit record is undecided. If that policy
    # changes, this assertion is expected to change with it.
    assert emitted == []
    assert _persisted_audit_entries(sessions) == []
    assert all(s.added == [] for s in sessions)
    assert maestro_calls == []
