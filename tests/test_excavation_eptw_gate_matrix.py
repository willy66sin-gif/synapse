"""
ePTW gate (Rule 0) failure matrix for work_type=EXCAVATION, through the
real POST /airlock/claims HTTP layer.

Setup pattern reused from tests/test_excavation_claim_scenarios.py
(itself copied from tests/test_airlock_maestro.py): fake AsyncSession/
Redis via FastAPI dependency_overrides, recording Maestro adapter spies,
sessions retained so persisted AdjudicationAuditEntry rows can be
inspected. Copied, not imported: tests/ is not a package.

One extension: _StubSession resolves IssuerRole queries per issuer_id
(read from the statement's bound parameter) instead of returning one
fixed list for any issuer. Needed by the split-issuer test, which has to
tell which issuer's roles Rule 0 actually consults.

Permit windows are built around real now (tests/test_core_eptw.py:38-40
pattern); no test places a timestamp at a permit edge and the clock is
never monkeypatched.

Not duplicated here: "issuer holds SA only -> NO_GO, R-PTW-01" is
already covered verbatim by tests/test_excavation_claim_scenarios.py's
test_excavation_sa_only_issuer_fails_closed_at_rule_0.
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
OTHER_ISSUER = "USR-RTO-02"

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id=CLAIM_ISSUER, role="SUPERINTENDENT", clearance_level=3)
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

RTO_AND_SA = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
RTO_ONLY = [AuthorityRoleType.RTO]
SA_ONLY = [AuthorityRoleType.SA]
NEITHER_RTO_NOR_SA = [AuthorityRoleType.PE]

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


def _client_with_stubs(sessions: list, roles_by_issuer: dict, zone_data=VALID_LOW_HAZARD_ZONE):
    async def _override_db_session():
        session = _StubSession(issuer_row=SUPERINTENDENT_ROW, roles_by_issuer=roles_by_issuer)
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
        "issuer_id": CLAIM_ISSUER,
    }
    base.update(overrides)
    return base


def _excavation_claim(**overrides) -> dict:
    base = {
        "claim_id": "CLM-20261002-101",
        "timestamp": CLAIM_TIMESTAMP,
        "project_id": "PROJ-TEST-01",
        "issuer_id": CLAIM_ISSUER,
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
    unsigned = {k: v for k, v in evidence.items() if k != "sha256_signature"}
    expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode("utf-8")).hexdigest()
    assert evidence["sha256_signature"] == expected


def _assert_no_go(response, sessions, maestro_calls, reason_code: str, core_trace: list[tuple[str, bool]]):
    """Asserts verdict, reason code, Core rule_trace prefix, signed evidence
    carrying the code (response + persisted row), and the alert's code."""
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "NO_GO"
    assert evidence["reason_code"] == reason_code

    trace = evidence["rule_trace"]
    # Core entries first; the router appends profile_check and
    # project_scope_check after adjudicate() returns.
    assert [(e["rule_id"], e["passed"]) for e in trace[: len(core_trace)]] == core_trace
    assert [e["rule_id"] for e in trace[len(core_trace) :]] == ["profile_check", "project_scope_check"]

    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions)
    assert len(persisted) == 1
    assert persisted[0].decision == "NO_GO"
    assert persisted[0].record["reason_code"] == reason_code
    assert persisted[0].record["sha256_signature"] == evidence["sha256_signature"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    assert all(alert.reason_code == reason_code for _, alert in maestro_calls)
    return evidence


PTW_FAIL = [("ptw_precondition_check", False)]


def test_missing_ptw_context_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    response = client.post("/airlock/claims", json=_excavation_claim(ptw_context=None))

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "No permit-to-work context provided" in evidence["reason"]


def test_pending_status_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    response = client.post("/airlock/claims", json=_excavation_claim(ptw_context=_ptw_context(status="PENDING")))

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "status 'PENDING' is not APPROVED" in evidence["reason"]


def test_permit_zone_mismatch_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    response = client.post("/airlock/claims", json=_excavation_claim(ptw_context=_ptw_context(zone_id="ZONE-01")))

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "zone 'ZONE-01' does not match claim zone 'ZONE-B3'" in evidence["reason"]


def test_permit_type_mismatch_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    response = client.post(
        "/airlock/claims",
        json=_excavation_claim(ptw_context=_ptw_context(permit_type=WorkType.HOT_WORK.value)),
    )

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "type 'HOT_WORK' does not match claimed work_type 'EXCAVATION'" in evidence["reason"]


def test_expired_permit_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    expired = _ptw_context(
        valid_from=(NOW - timedelta(hours=3)).isoformat(),
        valid_until=(NOW - timedelta(hours=2)).isoformat(),
    )
    response = client.post("/airlock/claims", json=_excavation_claim(ptw_context=expired))

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "is outside its valid window" in evidence["reason"]


def test_issuer_with_neither_rto_nor_sa_fails_r_ptw_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: NEITHER_RTO_NOR_SA})

    response = client.post("/airlock/claims", json=_excavation_claim())

    evidence = _assert_no_go(response, sessions, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "Issuer does not hold an admissible role (['RTO'])" in evidence["reason"]


def test_rto_only_issuer_passes_rule_0_and_1_then_fails_r_zone_01(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_ONLY})

    response = client.post("/airlock/claims", json=_excavation_claim())

    evidence = _assert_no_go(
        response,
        sessions,
        maestro_calls,
        "R-ZONE-01",
        [("ptw_precondition_check", True), ("authority_check", True), ("zone_safety_check", False)],
    )
    assert "Issuer does not hold an admissible role (['SA']) for zone 'ZONE-B3'" in evidence["reason"]
    assert evidence["authority_binding_id"] == ["BIND-SA-01"]


def test_rule_0_checks_claim_issuer_roles_not_permit_issuer(maestro_calls):
    """Split roles between ClaimPayload.issuer_id and PtwContext.issuer_id.
    Rule 0's RTO check consumes issuer_roles fetched for the claim's
    issuer (src/airlock/router.py's fetch_issuer_roles(session,
    claim.issuer_id)); PtwContext.issuer_id is not read by any rule."""
    # Case A: claim issuer lacks RTO, permit issuer holds RTO+SA -> Rule 0 fails.
    sessions_a: list = []
    client = _client_with_stubs(sessions_a, {CLAIM_ISSUER: SA_ONLY, OTHER_ISSUER: RTO_AND_SA})

    response = client.post(
        "/airlock/claims", json=_excavation_claim(ptw_context=_ptw_context(issuer_id=OTHER_ISSUER))
    )

    evidence = _assert_no_go(response, sessions_a, maestro_calls, "R-PTW-01", PTW_FAIL)
    assert "Issuer does not hold an admissible role (['RTO'])" in evidence["reason"]
    assert [q for s in sessions_a for q in s.role_queries] == [CLAIM_ISSUER]

    # Case B: claim issuer holds RTO+SA, permit issuer holds nothing -> GO.
    maestro_calls.clear()
    app.dependency_overrides.clear()
    sessions_b: list = []
    client = _client_with_stubs(sessions_b, {CLAIM_ISSUER: RTO_AND_SA})

    response = client.post(
        "/airlock/claims", json=_excavation_claim(ptw_context=_ptw_context(issuer_id=OTHER_ISSUER))
    )

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "GO"
    assert evidence["reason_code"] is None
    _assert_signature_valid(evidence)
    persisted = _persisted_audit_entries(sessions_b)
    assert len(persisted) == 1
    assert persisted[0].record["reason_code"] is None
    assert [q for s in sessions_b for q in s.role_queries] == [CLAIM_ISSUER]
    assert maestro_calls == []


def test_timezone_naive_valid_from_rejected_with_422(maestro_calls):
    sessions: list = []
    client = _client_with_stubs(sessions, {CLAIM_ISSUER: RTO_AND_SA})

    naive = _ptw_context(valid_from=NOW.replace(tzinfo=None).isoformat())
    response = client.post("/airlock/claims", json=_excavation_claim(ptw_context=naive))

    assert response.status_code == 422
    locs = [tuple(err["loc"]) for err in response.json()["detail"]]
    assert locs == [("body", "ptw_context", "valid_from")]

    # Observe only: a schema-level 422 currently leaves no evidence record
    # (same open policy question pinned in
    # tests/test_excavation_claim_scenarios.py's malformed-payload test).
    assert _persisted_audit_entries(sessions) == []
    assert all(s.added == [] for s in sessions)
    assert maestro_calls == []
