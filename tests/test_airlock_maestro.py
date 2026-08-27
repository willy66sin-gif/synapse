"""
POST /airlock/claims -> Maestro wiring tests.

Covers the 2026-07-31 decision (see CLAUDE.md's Changelog): a NO_GO
adjudication triggers a Maestro alert for every failure class
(R-PTW-01, R-AUTH-01, R-ZONE-01); a GO adjudication triggers none.

Same stubbing approach as tests/test_supervisor_router.py: a fake
AsyncSession/Redis client injected via FastAPI's dependency_overrides,
no live Postgres/Redis. WhatsAppAdapter/TelegramAdapter are swapped for
recording spies at the src.airlock.router import site — the real
adapters are already no-op stubs (no network I/O), but spying lets
these tests assert exactly what was sent, including reason_code,
without depending on adapter internals like render_alert_text.
"""
import pytest
from fastapi.testclient import TestClient

import src.airlock.router as airlock_router
from src.airlock.schemas import WorkType
from src.core.models import AuthorizedIssuer
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.main import app
from src.maestro.schemas import DeliveryResult

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)

# 2026-08-27, Authority Admissibility handoff: authority_check() now
# gates on GATE_ADMISSIBLE_ROLES membership (src/core/rules.py), not
# clearance_level -- tests that need Rule 1 to pass now also need this
# role list threaded through _client_with_stubs.
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO]

VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}


class _StubResult:
    """
    Backs both fetch_issuer_record()'s scalar_one_or_none() query shape
    and (2026-08-27, Authority Admissibility handoff) fetch_issuer_roles()'s
    scalars().all() shape -- one canned result object serving both,
    same "canned regardless of statement shape" convention this stub
    already used before this addition, extended to a second shape now
    that the router calls a second query.
    """

    def __init__(self, row, roles):
        self._row = row
        self._roles = roles

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return self._roles


class _StubSession:
    def __init__(self, issuer_row=None, issuer_roles=None):
        self._issuer_row = issuer_row
        self._issuer_roles = issuer_roles or []
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        return _StubResult(self._issuer_row, self._issuer_roles)

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
    """Patches the two adapter classes at their src.airlock.router import
    site (mirrors how src/supervisor/router.py wires the same two
    adapters) and returns the shared list they record calls into."""
    calls: list = []
    monkeypatch.setattr(airlock_router, "WhatsAppAdapter", _make_recording_adapter("whatsapp", calls))
    monkeypatch.setattr(airlock_router, "TelegramAdapter", _make_recording_adapter("telegram", calls))
    return calls


def _client_with_stubs(issuer_row=None, zone_data=None, issuer_roles=None):
    async def _override_db_session():
        yield _StubSession(issuer_row=issuer_row, issuer_roles=issuer_roles)

    async def _override_redis_client():
        yield _StubRedis(zone_data=zone_data)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _claim(**overrides) -> dict:
    base = {
        "claim_id": "CLM-201",
        "timestamp": "2026-07-31T10:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {},
        "work_type": WorkType.NOMINAL_CIVIL.value,
    }
    base.update(overrides)
    return base


def test_go_adjudication_triggers_no_maestro_call(maestro_calls):
    client = _client_with_stubs(
        issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    response = client.post("/airlock/claims", json=_claim())

    assert response.status_code == 200
    assert response.json()["decision"] == "GO"
    assert response.json()["authority_binding_id"] is None
    assert maestro_calls == []


def test_ptw_precondition_no_go_triggers_maestro_alert_with_reason_code(maestro_calls):
    client = _client_with_stubs(issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE)

    claim = _claim(
        claim_id="CLM-EPTW-201",
        work_type=WorkType.EXCAVATION.value,
        action_type="EXCAVATION_WORK",
        ptw_context=None,
    )
    response = client.post("/airlock/claims", json=claim)

    assert response.status_code == 200
    assert response.json()["decision"] == "NO_GO"
    assert response.json()["reason_code"] == "R-PTW-01"
    # R-PTW-01 resolves to RTO (2026-08-18, direct confirmation) -- see
    # DIRECTORY_MAP. List-valued (2026-08-18): no is_design_alteration
    # on this claim, so just the one reason_code binding.
    assert response.json()["authority_binding_id"] == ["BIND-RTO-01"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.decision == "NO_GO"
        assert alert.reason_code == "R-PTW-01"
        assert alert.conflicting_condition is not None
        assert alert.conflicting_condition.rule_id == "ptw_precondition_check"
        assert alert.authority_binding_id == ["BIND-RTO-01"]
        assert alert.assigned_role == ["RTO"]


def test_authority_failure_no_go_triggers_maestro_alert_with_reason_code(maestro_calls):
    client = _client_with_stubs(issuer_row=None, zone_data=VALID_LOW_HAZARD_ZONE)

    response = client.post("/airlock/claims", json=_claim(claim_id="CLM-AUTH-201"))

    assert response.status_code == 200
    assert response.json()["decision"] == "NO_GO"
    assert response.json()["reason_code"] == "R-AUTH-01"
    # R-AUTH-01 resolves to RTO (2026-08-18, direct confirmation) -- see DIRECTORY_MAP.
    assert response.json()["authority_binding_id"] == ["BIND-RTO-01"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.reason_code == "R-AUTH-01"
        assert alert.conflicting_condition.rule_id == "authority_check"
        assert alert.authority_binding_id == ["BIND-RTO-01"]
        assert alert.assigned_role == ["RTO"]


def test_zone_safety_no_go_triggers_maestro_alert_with_reason_code(maestro_calls):
    """R-ZONE-01 now resolves via its own reason_code routing entry
    (2026-08-06, Task 3), not the ("*", "*") catch-all -- see
    src/maestro/directory.py's DIRECTORY_MAP."""
    client = _client_with_stubs(
        issuer_row=SUPERINTENDENT_ROW, zone_data=None, issuer_roles=SUPERINTENDENT_ROLES
    )

    response = client.post("/airlock/claims", json=_claim(claim_id="CLM-ZONE-201", zone_id="ZONE-99"))

    assert response.status_code == 200
    assert response.json()["decision"] == "NO_GO"
    assert response.json()["reason_code"] == "R-ZONE-01"
    assert response.json()["authority_binding_id"] == ["BIND-SA-01"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.reason_code == "R-ZONE-01"
        assert alert.conflicting_condition.rule_id == "zone_safety_check"
        assert alert.authority_binding_id == ["BIND-SA-01"]
        assert alert.assigned_role == ["SA"]


def test_maestro_alert_carries_escalation_contact_and_recipient(maestro_calls):
    """recipient_id/escalation_contact are now resolved via
    src/maestro/directory.py, not sourced from the claim's issuer_id --
    escalation ownership is determined by (zone_id, reason_code), not
    by who submitted the claim."""
    client = _client_with_stubs(issuer_row=None, zone_data=VALID_LOW_HAZARD_ZONE)

    client.post("/airlock/claims", json=_claim(claim_id="CLM-AUTH-202", issuer_id="USR-UNKNOWN"))

    assert len(maestro_calls) == 2
    for _, alert in maestro_calls:
        # R-AUTH-01 resolves to RTO (2026-08-18, direct confirmation) -- see DIRECTORY_MAP.
        assert alert.recipient_id == "RTO"  # no contact_id on file yet -> role fallback
        # Supervisor Override Retirement (5 Aug 2026): escalation_contact states the
        # resolved authority directly, not an override URL -- see src/maestro/schemas.py.
        assert alert.escalation_contact == "RTO (BIND-RTO-01)"


# --- Design-alteration self-declaration (2026-08-18): end-to-end routing ---


def test_no_go_design_alteration_routes_to_rto_and_both_qp_and_qe(maestro_calls):
    """The two dimensions are orthogonal, confirmed explicitly to both
    apply at once: a NO_GO claim (R-AUTH-01 -> RTO) that's also a
    design alteration (-> QP and QE) resolves to all three, in that
    order, both in the persisted evidence and in the Maestro alert."""
    client = _client_with_stubs(issuer_row=None, zone_data=VALID_LOW_HAZARD_ZONE)

    response = client.post(
        "/airlock/claims",
        json=_claim(
            claim_id="CLM-AUTH-DA-201",
            is_design_alteration=True,
            alteration_description="Beam relocated 300mm to clear new duct routing.",
        ),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "NO_GO"
    assert response.json()["reason_code"] == "R-AUTH-01"
    assert response.json()["authority_binding_id"] == ["BIND-RTO-01", "BIND-QP-DA-01", "BIND-QE-DA-01"]

    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.authority_binding_id == ["BIND-RTO-01", "BIND-QP-DA-01", "BIND-QE-DA-01"]
        assert alert.assigned_role == ["RTO", "Qualified Person", "QE"]


def test_go_design_alteration_still_triggers_no_maestro_call(maestro_calls):
    """A GO claim that's also a design alteration still triggers zero
    Maestro alerts -- the locked NO_GO Notification Principle
    (CLAUDE.md) is untouched by this pass; design-alteration escalation
    is visible live via the Frontline/Supervisor screens instead (see
    src/frontline/router.py, src/supervisor/router.py). The persisted
    authority_binding_id also stays None on GO, unchanged from before
    this pass -- see src/airlock/router.py's own comment for why."""
    client = _client_with_stubs(
        issuer_row=SUPERINTENDENT_ROW, zone_data=VALID_LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    response = client.post(
        "/airlock/claims",
        json=_claim(
            claim_id="CLM-GO-DA-201",
            is_design_alteration=True,
            alteration_description="Beam relocated 300mm to clear new duct routing.",
        ),
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "GO"
    assert response.json()["authority_binding_id"] is None
    assert maestro_calls == []
