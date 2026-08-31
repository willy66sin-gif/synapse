"""
GET /frontline/blocked/{claim_id} tests.

Same stubbing approach as tests/test_supervisor_blocked_screen.py: a
fake AsyncSession returning a controlled AdjudicationAuditEntry row,
injected via FastAPI's dependency_overrides, no live Postgres.

Unlike the Supervisor Blocked Screen (NO_GO-only, 409 on GO), this
route renders for GO and NO_GO alike -- the Frontline persona's
question is "Can I proceed?", which GO answers just as validly.

GO Freshness Phase 1 (2026-08-31, Willy-authorized): the
GET /frontline/blocked/{claim_id}/status class below tests
frontline_status_json(), the new polling endpoint. Its stubbing is
necessarily richer than the HTML route's above -- that route only ever
reads one persisted evidence row, but frontline_status_json()
re-fetches issuer/zone state and re-runs adjudicate() fresh, so its
fake AsyncSession has to serve three distinct query shapes off the
same session, same shapes tests/test_airlock_maestro.py's stubs
already serve for POST /airlock/claims.
"""
import pytest
from fastapi.testclient import TestClient

from src.airlock.schemas import WorkType
from src.core.models import AuthorizedIssuer, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.evidence.models import AdjudicationAuditEntry
from src.main import app

NO_GO_EVIDENCE = {
    "claim_id": "CLM-EPTW-301",
    "decision": "NO_GO",
    "reason": (
        "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
        "for high-risk work_type 'EXCAVATION'."
    ),
    "reason_code": "R-PTW-01",
    "authority_binding_id": "BIND-999",
    "rule_trace": [
        {
            "rule_id": "ptw_precondition_check",
            "passed": False,
            "reason": (
                "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided "
                "for high-risk work_type 'EXCAVATION'."
            ),
        },
    ],
    "evaluated_at": "2026-07-31T10:00:00+00:00",
    "input_payload": {
        "claim_id": "CLM-EPTW-301",
        "issuer_id": "USR-SUP-01",
        "zone_id": "ZONE-01",
        "action_type": "EXCAVATION",
    },
    "sha256_signature": "irrelevant-for-this-test",
}

GO_EVIDENCE = {
    "claim_id": "CLM-101",
    "decision": "GO",
    "reason": "Claim 'CLM-101' cleared for execution in ZONE-01.",
    "reason_code": None,
    "authority_binding_id": None,
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
    "evaluated_at": "2026-07-31T10:05:00+00:00",
    "input_payload": {
        "claim_id": "CLM-101",
        "issuer_id": "USR-SUP-01",
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
    },
    "sha256_signature": "irrelevant-for-this-test",
}


class _StubResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _StubSession:
    def __init__(self, row=None):
        self._row = row

    async def execute(self, stmt):
        return _StubResult(self._row)


def _client_with_record(record):
    row = None
    if record is not None:
        row = AdjudicationAuditEntry(claim_id=record["claim_id"], decision=record["decision"], record=record)

    async def _override_db_session():
        yield _StubSession(row=row)

    app.dependency_overrides[get_db_session] = _override_db_session
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_frontline_screen_renders_for_no_go_claim():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/frontline/blocked/CLM-EPTW-301")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    assert "CLM-EPTW-301" in body
    # R-PTW-01 resolves to RTO (2026-08-18, direct confirmation) --
    # live-resolved via resolve_authority(), not the fixture's raw
    # (stale) authority_binding_id field -- see src/frontline/router.py.
    assert "RTO" in body
    assert "BIND-RTO-01" in body
    assert "No permit-to-work context provided" in body
    assert '"/static/frontline-screen/frontline-screen.js"' in body

    # Frontline screen must never expose rule internals or override mechanics.
    assert "ptw_precondition_check" not in body
    assert "rule_trace" not in body
    assert "/supervisor/override" not in body
    assert "Contact Supervisor" not in body

    # WCAG 1.4.4/1.4.10: reflow/zoom must stay available -- scaling never disabled.
    assert '<meta name="viewport" content="width=device-width, initial-scale=1" />' in body
    assert "user-scalable=no" not in body
    assert "maximum-scale=1" not in body


def test_frontline_screen_renders_for_go_claim():
    """Unlike the Supervisor Blocked Screen, GO is a valid, renderable
    state here -- not a 409."""
    client = _client_with_record(GO_EVIDENCE)

    response = client.get("/frontline/blocked/CLM-101")

    assert response.status_code == 200
    body = response.text
    assert "CLM-101" in body
    # GO (reason_code=None) resolves to RTO (2026-08-18, direct confirmation).
    assert "RTO" in body
    assert "MATERIAL_ENTRY" in body


def test_frontline_screen_returns_404_for_unknown_claim():
    client = _client_with_record(None)

    response = client.get("/frontline/blocked/CLM-DOES-NOT-EXIST")

    assert response.status_code == 404
    assert "CLM-DOES-NOT-EXIST" in response.json()["detail"]


def test_frontline_screen_js_is_served_as_static_asset():
    client = _client_with_record(NO_GO_EVIDENCE)

    response = client.get("/static/frontline-screen/frontline-screen.js")

    assert response.status_code == 200
    assert "customElements.define" in response.text


# --- GO Freshness Phase 1: GET /frontline/blocked/{claim_id}/status ---

SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id="USR-SUP-01", role="SUPERINTENDENT", clearance_level=3)
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
VALID_LOW_HAZARD_ZONE = {"hazard_level": "LOW", "active_crane": "false"}

# Original claim as originally submitted and persisted -- adjudicated
# GO at submission time. input_payload here must be a real,
# ClaimPayload-reconstructable dict (unlike NO_GO_EVIDENCE/GO_EVIDENCE
# above, which predate this endpoint and only need the fields the HTML
# route reads directly) -- frontline_status_json() rebuilds
# ClaimPayload(**evidence["input_payload"]) to re-run adjudicate().
STALE_GO_EVIDENCE = {
    "claim_id": "CLM-FRESH-401",
    "decision": "GO",
    "reason": "Claim 'CLM-FRESH-401' cleared for execution in ZONE-01.",
    "reason_code": None,
    "authority_binding_id": None,
    "rule_trace": [{"rule_id": "authority_check", "passed": True, "reason": "Authority Validated"}],
    "evaluated_at": "2026-08-30T09:00:00+00:00",
    "input_payload": {
        "claim_id": "CLM-FRESH-401",
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
    """
    Serves the three distinct query shapes frontline_status_json()
    issues against the one fake session below:
    fetch_latest_adjudication_record()'s AdjudicationAuditEntry lookup
    (scalar_one_or_none), fetch_issuer_record()'s AuthorizedIssuer
    lookup (scalar_one_or_none), and fetch_issuer_roles()'s
    IssuerRole.role_type lookup (scalars().all()). Picking the right
    canned result per call -- rather than one fixed result for every
    query, as tests/test_frontline_router.py's simpler _StubResult
    above does for the single-query HTML route -- is what makes a
    from-scratch re-adjudication stubbable at all.
    """

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

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AdjudicationAuditEntry:
            return _StatusStubResult(self._evidence_row)
        if entity is AuthorizedIssuer:
            return _StatusStubResult(self._issuer_row)
        if entity is IssuerRole:
            return _StatusStubResult(self._issuer_roles)
        raise AssertionError(f"frontline_status_json() issued an unexpected query: {stmt}")

    def add(self, obj):
        raise AssertionError(
            "frontline_status_json() must never persist -- GO Freshness Phase 1 explicitly "
            "does not emit evidence for a poll-detected change (see its own docstring)."
        )

    async def commit(self):
        raise AssertionError("frontline_status_json() must never commit -- see add() above.")


class _StatusStubRedis:
    def __init__(self, zone_data=None):
        self._zone_data = zone_data

    async def hgetall(self, key):
        # fetch_zone_record() calls hgetall twice: once for the
        # human-declared `zone:{zone_id}` hash, once for the
        # (unused-in-these-tests) `zone:{zone_id}:sensor` hash
        # (src/core/rules.py's sensor_zone_redis_key()). Returning {}
        # for the sensor key and letting the human-declared zone_data
        # flow through for the plain zone: key matches
        # fetch_zone_record()'s own sensor-over-human precedence
        # fallback (no sensor data -> human-declared value wins).
        if key.endswith(":sensor"):
            return {}
        return self._zone_data or {}


def _status_client(evidence_row=None, issuer_row=None, issuer_roles=None, zone_data=None):
    async def _override_db_session():
        yield _StatusStubSession(evidence_row=evidence_row, issuer_row=issuer_row, issuer_roles=issuer_roles)

    async def _override_redis_client():
        yield _StatusStubRedis(zone_data=zone_data)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_redis_client] = _override_redis_client
    return TestClient(app)


def _evidence_row(record):
    return AdjudicationAuditEntry(claim_id=record["claim_id"], decision=record["decision"], record=record)


def test_status_endpoint_reflects_fresh_state_not_stale_persisted_decision():
    """The claim was persisted as GO. Zone state has since gone bad
    (ZONE-01 no longer exists in Redis) -- the poll endpoint must
    re-adjudicate and report the fresh NO_GO, not the stale persisted GO."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=None,  # zone no longer exists -> R-ZONE-01
    )

    response = client.get("/frontline/blocked/CLM-FRESH-401/status")

    assert response.status_code == 200
    body = response.json()
    assert body["claimId"] == "CLM-FRESH-401"
    assert body["decision"] == "NO_GO"
    assert body["reasonCode"] == "R-ZONE-01"


def test_status_endpoint_re_evaluates_on_every_call_not_cached():
    """Two calls against a session whose backing zone state changes
    between them must return two different decisions -- proof this
    endpoint re-runs adjudicate() fresh each time, not once and cached."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=VALID_LOW_HAZARD_ZONE,
    )

    first = client.get("/frontline/blocked/CLM-FRESH-401/status")
    assert first.json()["decision"] == "GO"

    # Simulate the underlying zone state changing between polls by
    # re-registering the dependency override with different zone_data --
    # same session-per-call FastAPI DI pattern this file already uses,
    # just re-armed mid-test.
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=None,
    )
    second = client.get("/frontline/blocked/CLM-FRESH-401/status")
    assert second.json()["decision"] == "NO_GO"


def test_status_endpoint_go_to_no_go_transition_writes_no_evidence():
    """Explicit coverage for GO Freshness Phase 1 item 4: a poll-
    detected GO -> NO_GO transition must render without writing any
    new evidence record. _StatusStubSession.add()/.commit() raise if
    called at all, so this test fails loudly if that boundary is
    ever crossed."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=None,  # -> NO_GO
    )

    response = client.get("/frontline/blocked/CLM-FRESH-401/status")

    assert response.status_code == 200
    assert response.json()["decision"] == "NO_GO"
    # No AssertionError raised by the stub session's add()/commit() ==
    # neither was called == no evidence was persisted.


def test_status_endpoint_returns_404_for_unknown_claim():
    client = _status_client(evidence_row=None)

    response = client.get("/frontline/blocked/CLM-DOES-NOT-EXIST/status")

    assert response.status_code == 404
    assert "CLM-DOES-NOT-EXIST" in response.json()["detail"]


def test_status_endpoint_response_shape_matches_html_embedded_payload():
    """Same field set the HTML route embeds in its inline <script> --
    the client's `data` setter must be able to consume either
    interchangeably (see frontline-screen.js's _poll())."""
    client = _status_client(
        evidence_row=_evidence_row(STALE_GO_EVIDENCE),
        issuer_row=SUPERINTENDENT_ROW,
        issuer_roles=SUPERINTENDENT_ROLES,
        zone_data=VALID_LOW_HAZARD_ZONE,
    )

    response = client.get("/frontline/blocked/CLM-FRESH-401/status")

    assert response.status_code == 200
    assert set(response.json().keys()) == {
        "claimId",
        "decision",
        "reasonCode",
        "reason",
        "workActivity",
        "traceId",
        "assignedRole",
    }
