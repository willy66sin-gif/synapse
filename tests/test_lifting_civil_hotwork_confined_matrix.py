"""
Rule 0 / 1 / 2 matrix for LIFTING, NOMINAL_CIVIL, HOT_WORK and
CONFINED_SPACE (plus one EXCAVATION + LIFT_OPERATION cross-case),
through the real POST /airlock/claims HTTP layer.

Setup pattern reused from tests/test_excavation_eptw_gate_matrix.py:
per-issuer fake IssuerRole lookup, fake Redis, recording Maestro
adapters, retained sessions for inspecting persisted
AdjudicationAuditEntry rows. Copied, not imported: tests/ is not a
package.

Zone hazard values: only "LOW" and "HIGH" are used anywhere in tests/.
HIGH_HAZARD_ZONE below takes its values from tests/test_adjudication.py:38
(hazard_level="HIGH", active_crane=True), written in the Redis-hash
string form of tests/test_airlock_maestro.py:40.

Permit windows span real now +/- 24h -- wide enough that the wall-clock
read in verify_ptw_precondition (src/core/rules.py:376) cannot land at
an edge during a test run. The clock is never monkeypatched.
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

CLAIM_ISSUER = "USR-SUP-01"

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id=CLAIM_ISSUER, role="SUPERINTENDENT", clearance_level=3)
LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}
HIGH_HAZARD_ZONE = {"hazard_level": "HIGH", "active_crane": "true"}

RTO_AND_SA = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
RTO_ONLY = [AuthorityRoleType.RTO]
SA_ONLY = [AuthorityRoleType.SA]

NOW = datetime.now(timezone.utc)
CLAIM_TIMESTAMP = NOW.isoformat()
VALID_FROM = (NOW - timedelta(hours=24)).isoformat()
VALID_UNTIL = (NOW + timedelta(hours=24)).isoformat()

ROUTER_TRACE = ["profile_check", "project_scope_check"]
ALL_CORE_PASS = [("ptw_precondition_check", True), ("authority_check", True), ("zone_safety_check", True)]


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
    def __init__(self, issuer_row, roles_by_issuer):
        self._issuer_row = issuer_row
        self._roles_by_issuer = roles_by_issuer
        self.role_queries: list[str] = []
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AuthorizedIssuer:
            return _StubResult(self._issuer_row)
        if entity is IssuerRole:
            (issuer_id,) = stmt.compile().params.values()
            self.role_queries.append(issuer_id)
            return _StubResult(self._roles_by_issuer.get(issuer_id, []))
        if entity is IssuerProject:
            return _StubResult(["PROJ-TEST-01"])
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


def _client_with_stubs(sessions: list, roles: list, zone_data=LOW_HAZARD_ZONE):
    async def _override_db_session():
        session = _StubSession(issuer_row=SUPERINTENDENT_ROW, roles_by_issuer={CLAIM_ISSUER: roles})
        sessions.append(session)
        yield session

    async def _override_redis_client():
        yield _StubRedis(zone_data=zone_data)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


def _ptw_context(permit_type: WorkType, **overrides) -> dict:
    base = {
        "ptw_id": "PTW-2026-9902",
        "status": "APPROVED",
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
        "permit_type": permit_type.value,
        "zone_id": "ZONE-B3",
        "issuer_id": CLAIM_ISSUER,
    }
    base.update(overrides)
    return base


def _claim(work_type: WorkType, action_type: str, ptw_context, **overrides) -> dict:
    base = {
        "claim_id": "CLM-20261002-201",
        "timestamp": CLAIM_TIMESTAMP,
        "project_id": "PROJ-TEST-01",
        "issuer_id": CLAIM_ISSUER,
        "authority_level": 3,
        "zone_id": "ZONE-B3",
        "action_type": action_type,
        "payload_data": {},
        "work_type": work_type.value,
        "ptw_context": ptw_context,
    }
    base.update(overrides)
    return base


def _persisted_audit_entries(sessions: list) -> list:
    return [obj for s in sessions for obj in s.added if isinstance(obj, AdjudicationAuditEntry)]


def _assert_signature_valid(evidence: dict) -> None:
    unsigned = {k: v for k, v in evidence.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()
    assert evidence["sha256_signature"] == expected


def _assert_trace(evidence: dict, core_trace: list[tuple[str, bool]]) -> None:
    trace = evidence["rule_trace"]
    assert [(e["rule_id"], e["passed"]) for e in trace[: len(core_trace)]] == core_trace
    assert [e["rule_id"] for e in trace[len(core_trace) :]] == ROUTER_TRACE


def _assert_no_go(response, sessions, maestro_calls, reason_code: str, core_trace: list[tuple[str, bool]]) -> dict:
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "NO_GO"
    assert evidence["reason_code"] == reason_code
    _assert_trace(evidence, core_trace)

    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions)
    assert len(persisted) == 1
    assert persisted[0].decision == "NO_GO"
    assert persisted[0].record["reason_code"] == reason_code
    assert persisted[0].record["sha256_signature"] == evidence["sha256_signature"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    assert all(alert.reason_code == reason_code for _, alert in maestro_calls)
    return evidence


def _assert_go(response, sessions, maestro_calls) -> dict:
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "GO"
    assert evidence["reason_code"] is None
    _assert_trace(evidence, ALL_CORE_PASS)

    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions)
    assert len(persisted) == 1
    assert persisted[0].decision == "GO"
    assert persisted[0].record["sha256_signature"] == evidence["sha256_signature"]

    assert maestro_calls == []
    return evidence


ZONE_FAIL_AFTER_0_AND_1 = [("ptw_precondition_check", True), ("authority_check", True), ("zone_safety_check", False)]


# 1
def test_lifting_lift_operation_in_high_hazard_zone_fails_r_zone_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=HIGH_HAZARD_ZONE)

    claim = _claim(WorkType.LIFTING, "LIFT_OPERATION", _ptw_context(WorkType.LIFTING))
    response = client.post("/airlock/claims", json=claim)

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-ZONE-01", ZONE_FAIL_AFTER_0_AND_1)
    assert "Heavy lift requested in high-hazard zone 'ZONE-B3'" in evidence["reason"]


# 2
def test_lifting_lift_operation_in_low_hazard_zone_is_go(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=LOW_HAZARD_ZONE)

    claim = _claim(WorkType.LIFTING, "LIFT_OPERATION", _ptw_context(WorkType.LIFTING))
    response = client.post("/airlock/claims", json=claim)

    _assert_go(response, sessions, maestro_calls)


# 3
def test_lifting_without_lift_operation_label_in_high_hazard_zone_is_go(maestro_calls):
    # pins CURRENT behaviour; the check keys on the action_type label, not
    # work_type. Open design question, not an endorsement.
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=HIGH_HAZARD_ZONE)

    claim = _claim(WorkType.LIFTING, "CRANE_RIGGING", _ptw_context(WorkType.LIFTING))
    response = client.post("/airlock/claims", json=claim)

    _assert_go(response, sessions, maestro_calls)


# 4
def test_nominal_civil_rto_only_issuer_fails_r_zone_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_ONLY, zone_data=LOW_HAZARD_ZONE)

    claim = _claim(WorkType.NOMINAL_CIVIL, "MATERIAL_ENTRY", None)
    response = client.post("/airlock/claims", json=claim)

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-ZONE-01", ZONE_FAIL_AFTER_0_AND_1)
    assert evidence["rule_trace"][0]["reason"] == "No permit required for work_type 'NOMINAL_CIVIL'."
    assert "Issuer does not hold an admissible role (['SA']) for zone 'ZONE-B3'" in evidence["reason"]


# 5
def test_nominal_civil_with_attached_permit_is_go(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=LOW_HAZARD_ZONE)

    claim = _claim(WorkType.NOMINAL_CIVIL, "MATERIAL_ENTRY", _ptw_context(WorkType.NOMINAL_CIVIL))
    response = client.post("/airlock/claims", json=claim)

    evidence = _assert_go(response, sessions, maestro_calls)
    assert evidence["rule_trace"][0]["reason"] == "No permit required for work_type 'NOMINAL_CIVIL'."


# 6
def test_hot_work_shared_path_is_go(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=LOW_HAZARD_ZONE)

    claim = _claim(WorkType.HOT_WORK, "HOT_WORK_TASK", _ptw_context(WorkType.HOT_WORK))
    response = client.post("/airlock/claims", json=claim)

    evidence = _assert_go(response, sessions, maestro_calls)
    assert evidence["rule_trace"][0]["reason"] == "Permit 'PTW-2026-9902' validated for 'HOT_WORK'."


# 7
def test_confined_space_sa_only_issuer_stops_at_rule_0(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, SA_ONLY, zone_data=LOW_HAZARD_ZONE)

    claim = _claim(WorkType.CONFINED_SPACE, "CONFINED_SPACE_ENTRY", _ptw_context(WorkType.CONFINED_SPACE))
    response = client.post("/airlock/claims", json=claim)

    # Rules 1 and 2 never run: only ptw_precondition_check precedes the
    # two router-appended entries.
    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", [("ptw_precondition_check", False)])
    assert "Issuer does not hold an admissible role (['RTO']) for high-risk work_type 'CONFINED_SPACE'" in evidence[
        "reason"
    ]


# 8
def test_excavation_with_lift_operation_label_in_high_hazard_zone_fails_r_zone_01(maestro_calls):
    # pins CURRENT behaviour; a LIFT_OPERATION label on a non-lifting
    # work_type still triggers the hazard check. Open design question, not
    # an endorsement.
    sessions: list = []
    client = _client_with_stubs(sessions, RTO_AND_SA, zone_data=HIGH_HAZARD_ZONE)

    claim = _claim(WorkType.EXCAVATION, "LIFT_OPERATION", _ptw_context(WorkType.EXCAVATION))
    response = client.post("/airlock/claims", json=claim)

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-ZONE-01", ZONE_FAIL_AFTER_0_AND_1)
    assert "Heavy lift requested in high-hazard zone 'ZONE-B3'" in evidence["reason"]
