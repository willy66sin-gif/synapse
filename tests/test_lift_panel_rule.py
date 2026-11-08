"""
Rule 3 matrix: lift control panel OEM consent (R-LIFT-01), Step 3,
2026-10-11.

Layers, same shape as the existing rule tests:
  - check_lift_panel_oem_consent() directly: all eight combinations of
    the three claim flags, then the effective-date/as_of matrix (unit
    style, mirroring tests/test_core_eptw.py's approach to
    verify_ptw_precondition);
  - adjudicate(), for ordering against Rules 0-2 and what rule_trace
    carries;
  - the real POST /airlock/claims HTTP layer, for the hashed evidence
    record, escalation routing, and the Maestro alert;
  - the two polling routes, for non-retroactivity: a stored claim is
    re-judged as of when it was FIRST adjudicated, not as of the poll.

The shipped effective date is a placeholder
(EFFECTIVE_DATE_UNCONFIRMED). Every test that needs a real date
installs its own through the `effective_date` fixture -- an arbitrary
test value, not a claim about when the regulation commences.

HTTP setup reused from tests/test_lifting_civil_hotwork_confined_matrix.py
(fake IssuerRole lookup, fake Redis, recording Maestro adapters).
Copied, not imported: tests/ is not a package.

The grounding values asserted here are the ones src/core/rules.py
records as EXTERNAL_SOURCE. These tests pin that the rule cites them;
they do not, and cannot, check them against the document itself.
"""
import dataclasses
import hashlib
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import src.airlock.router as airlock_router
import src.core.rules as rules
from src.airlock.schemas import ClaimPayload
from src.core.evaluator import adjudicate
from src.core.models import AuthorizedIssuer, IssuerProject, IssuerRole
from src.core.repository import get_db_session, get_redis_client
from src.core.roles import AuthorityRoleType
from src.core.rules import (
    EFFECTIVE_DATE_UNCONFIRMED,
    GATE_ADMISSIBLE_ROLES,
    LIFT_PANEL_OEM_CONSENT_GROUNDING,
    REASON_CODE_LIFT_PANEL_OEM_CONSENT,
    IssuerRecord,
    ZoneRecord,
    check_lift_panel_oem_consent,
)
from src.evidence.models import AdjudicationAuditEntry
from src.evidence.repository import fetch_earliest_adjudication_record
from src.maestro.directory import ActivationMode, resolve_authority
from src.maestro.schemas import DeliveryResult, OutboundAlert
from src.main import app

CLAIM_ISSUER = "USR-SUP-01"
SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)
SUPERINTENDENT_ROW = AuthorizedIssuer(issuer_id=CLAIM_ISSUER, role="SUPERINTENDENT", clearance_level=3)
RTO_AND_SA = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
LOW_HAZARD_ZONE = ZoneRecord(hazard_level="LOW", active_crane=False)

GROUNDING_CITATION = {
    "instrument_issuing_authority": "BCA",
    "instrument_reference_number": "BCA/EMEG/GEN/2026/018",
    "provision": "Regulation 38(1)(a), Building Control (Fixed Installations) Regulations 2025",
    "basis": "EXTERNAL_SOURCE",
}

PANEL_WORK_WITHOUT_CONSENT = {
    "lift_control_panel_work": True,
    "non_lift_component_installed": True,
    "oem_consent_obtained": False,
}

# Arbitrary test effective date, and adjudication times either side of
# it. Singapore is UTC+8, so 1 Nov 00:00 there is 31 Oct 16:00 UTC.
TEST_EFFECTIVE_DATE = "2026-11-01"
WELL_BEFORE = datetime(2026, 10, 1, 4, 0, 0, tzinfo=timezone.utc)
WELL_AFTER = datetime(2026, 12, 1, 4, 0, 0, tzinfo=timezone.utc)
LAST_SECOND_BEFORE_IN_SINGAPORE = datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc)
FIRST_SECOND_OF_DAY_IN_SINGAPORE = datetime(2026, 10, 31, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def effective_date(monkeypatch):
    """Installs a confirmed effective date in place of the shipped placeholder, for one test."""

    def _set(value: str = TEST_EFFECTIVE_DATE) -> str:
        monkeypatch.setattr(
            rules,
            "LIFT_PANEL_OEM_CONSENT_GROUNDING",
            dataclasses.replace(LIFT_PANEL_OEM_CONSENT_GROUNDING, effective_date=value),
        )
        return value

    return _set


def _claim_dict(**overrides) -> dict:
    base = {
        "claim_id": "CLM-LIFT-301",
        "timestamp": "2026-10-11T10:00:00Z",
        "project_id": "PROJ-TEST-01",
        "issuer_id": CLAIM_ISSUER,
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "COMPONENT_INSTALL",
        "payload_data": {},
        "work_type": "NOMINAL_CIVIL",
    }
    base.update(overrides)
    return base


def _claim(**overrides) -> ClaimPayload:
    return ClaimPayload(**_claim_dict(**overrides))


# --- The shipped effective date is a placeholder ---


def test_effective_date_is_still_an_unconfirmed_placeholder():
    """
    TRIPWIRE. The date Regulation 38(1)(a) takes effect has not been
    confirmed, and the shipped constant says so rather than guessing.
    When the confirmed date replaces the placeholder in
    src/core/rules.py, this test fails -- update it deliberately, in
    the same change, to pin the confirmed date instead.
    """
    assert EFFECTIVE_DATE_UNCONFIRMED == "UNCONFIRMED"
    assert LIFT_PANEL_OEM_CONSENT_GROUNDING.effective_date == EFFECTIVE_DATE_UNCONFIRMED


def test_placeholder_is_not_a_date_and_not_the_letters_issue_date():
    with pytest.raises(ValueError):
        datetime.fromisoformat(LIFT_PANEL_OEM_CONSENT_GROUNDING.effective_date)
    assert LIFT_PANEL_OEM_CONSENT_GROUNDING.effective_date != "2026-09-30"


@pytest.mark.parametrize("as_of", [None, WELL_BEFORE, WELL_AFTER])
def test_unconfirmed_effective_date_fails_an_in_scope_claim_closed_whatever_the_time(as_of):
    outcome = check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), as_of)

    assert outcome.passed is False
    assert outcome.reason.startswith("FAIL_CLOSED_EFFECTIVE_DATE_UNCONFIRMED:")
    assert "has not been confirmed" in outcome.reason
    assert "BCA/EMEG/GEN/2026/018" in outcome.reason


@pytest.mark.parametrize(
    "flags", [{}, {"lift_control_panel_work": True}, {**PANEL_WORK_WITHOUT_CONSENT, "oem_consent_obtained": True}]
)
def test_unconfirmed_effective_date_does_not_touch_claims_the_rule_does_not_reach(flags):
    """Out-of-scope and consent-declared claims pass without any date being needed."""
    assert check_lift_panel_oem_consent(_claim(**flags), None).passed is True


# --- Schema: the three flags are additive ---


def test_the_three_flags_default_to_false_so_existing_payloads_stay_valid():
    claim = _claim()

    assert claim.lift_control_panel_work is False
    assert claim.non_lift_component_installed is False
    assert claim.oem_consent_obtained is False


# --- check_lift_panel_oem_consent(): all eight flag combinations, requirement in force ---


@pytest.mark.parametrize(
    "panel_work, non_lift_component, oem_consent",
    list(itertools.product([False, True], repeat=3)),
)
def test_rule_fails_only_for_panel_work_with_non_lift_component_and_no_consent(
    effective_date, panel_work, non_lift_component, oem_consent
):
    effective_date()
    claim = _claim(
        lift_control_panel_work=panel_work,
        non_lift_component_installed=non_lift_component,
        oem_consent_obtained=oem_consent,
    )

    outcome = check_lift_panel_oem_consent(claim, WELL_AFTER)

    should_fail = panel_work and non_lift_component and not oem_consent
    assert outcome.rule_id == "lift_panel_oem_consent_check"
    assert outcome.passed is (not should_fail)


def test_failure_reason_names_the_provision_the_instrument_and_the_effective_date(effective_date):
    effective_date()

    outcome = check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), WELL_AFTER)

    assert outcome.passed is False
    assert outcome.reason.startswith("Regulatory Requirement Not Met:")
    assert "OEM technical assessment and consent" in outcome.reason
    assert "Regulation 38(1)(a), Building Control (Fixed Installations) Regulations 2025" in outcome.reason
    assert "BCA/EMEG/GEN/2026/018" in outcome.reason
    assert "in force from 2026-11-01" in outcome.reason


def test_declared_consent_passes_with_its_own_reason():
    outcome = check_lift_panel_oem_consent(
        _claim(**{**PANEL_WORK_WITHOUT_CONSENT, "oem_consent_obtained": True}), WELL_AFTER
    )

    assert outcome.passed is True
    assert outcome.reason == "OEM consent declared for non-lift component in lift control panel."


@pytest.mark.parametrize(
    "flags",
    [{}, PANEL_WORK_WITHOUT_CONSENT, {**PANEL_WORK_WITHOUT_CONSENT, "oem_consent_obtained": True}],
)
def test_every_outcome_cites_the_regulatory_instrument(flags):
    """Not-applicable, failed, and consent-declared outcomes all carry the same citation."""
    outcome = check_lift_panel_oem_consent(_claim(**flags), WELL_AFTER)

    assert outcome.grounding is LIFT_PANEL_OEM_CONSENT_GROUNDING
    assert outcome.grounding.instrument_issuing_authority == "BCA"
    assert outcome.grounding.instrument_reference_number == "BCA/EMEG/GEN/2026/018"
    assert outcome.grounding.basis == "EXTERNAL_SOURCE"


def test_rule_is_keyed_on_the_flags_not_on_work_type_or_action_type(effective_date):
    effective_date()

    for action_type in ["MATERIAL_ENTRY", "LIFT_OPERATION"]:
        claim = _claim(action_type=action_type, **PANEL_WORK_WITHOUT_CONSENT)

        assert check_lift_panel_oem_consent(claim, WELL_AFTER).passed is False


def test_oem_consent_is_not_an_authority_role_gate():
    """R-LIFT-01 has no admissible-role row: OEM consent is a claim precondition, not a role."""
    assert REASON_CODE_LIFT_PANEL_OEM_CONSENT == "R-LIFT-01"
    assert REASON_CODE_LIFT_PANEL_OEM_CONSENT not in GATE_ADMISSIBLE_ROLES
    assert len(AuthorityRoleType) == 7


# --- check_lift_panel_oem_consent(): effective date vs. as_of (non-retroactivity) ---


def test_in_scope_claim_first_adjudicated_before_the_effective_date_passes(effective_date):
    effective_date()

    outcome = check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), WELL_BEFORE)

    assert outcome.passed is True
    assert outcome.reason.startswith("Requirement not yet in force when this claim was first adjudicated")
    assert "effective 2026-11-01" in outcome.reason
    assert outcome.evaluated_as_of == WELL_BEFORE.isoformat()


def test_in_scope_claim_first_adjudicated_after_the_effective_date_fails(effective_date):
    effective_date()

    outcome = check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), WELL_AFTER)

    assert outcome.passed is False
    assert outcome.evaluated_as_of == WELL_AFTER.isoformat()


def test_effective_date_boundary_is_midnight_in_singapore_not_utc(effective_date):
    effective_date()
    claim = _claim(**PANEL_WORK_WITHOUT_CONSENT)

    # 31 Oct 23:59:59 in Singapore: the day before -- not yet in force.
    assert check_lift_panel_oem_consent(claim, LAST_SECOND_BEFORE_IN_SINGAPORE).passed is True
    # 1 Nov 00:00:00 in Singapore (still 31 Oct in UTC): in force.
    assert check_lift_panel_oem_consent(claim, FIRST_SECOND_OF_DAY_IN_SINGAPORE).passed is False


def test_boundary_is_the_same_instant_whatever_offset_as_of_is_expressed_in(effective_date):
    effective_date()
    claim = _claim(**PANEL_WORK_WITHOUT_CONSENT)
    same_instant_in_new_york = FIRST_SECOND_OF_DAY_IN_SINGAPORE.astimezone(timezone(timedelta(hours=-4)))

    assert check_lift_panel_oem_consent(claim, same_instant_in_new_york).passed is False


def test_in_scope_claim_with_no_adjudication_time_fails_closed_not_defaulting_to_now(effective_date):
    effective_date("2999-01-01")  # far future: "now" would pass; no time at all must not

    outcome = check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), None)

    assert outcome.passed is False
    assert outcome.reason.startswith("FAIL_CLOSED_NO_ADJUDICATION_TIME:")
    assert outcome.evaluated_as_of is None


def test_naive_as_of_is_a_caller_bug_and_raises(effective_date):
    effective_date()

    with pytest.raises(ValueError):
        check_lift_panel_oem_consent(_claim(**PANEL_WORK_WITHOUT_CONSENT), datetime(2026, 12, 1, 4, 0, 0))


@pytest.mark.parametrize("claimed_timestamp", ["2020-01-01T00:00:00Z", "2999-01-01T00:00:00Z", "not a timestamp"])
def test_the_claims_own_timestamp_cannot_move_it_across_the_effective_date(effective_date, claimed_timestamp):
    """ClaimPayload.timestamp is submitter-supplied and unverified: backdating it changes nothing."""
    effective_date()
    claim = _claim(timestamp=claimed_timestamp, **PANEL_WORK_WITHOUT_CONSENT)

    assert check_lift_panel_oem_consent(claim, WELL_AFTER).passed is False
    assert check_lift_panel_oem_consent(claim, WELL_BEFORE).passed is True


# --- adjudicate(): ordering and trace ---


def test_adjudicate_rejects_panel_work_without_consent_as_r_lift_01(effective_date):
    effective_date()
    claim = _claim(**PANEL_WORK_WITHOUT_CONSENT)

    verdict = adjudicate(claim, SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA, as_of=WELL_AFTER)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-LIFT-01"
    assert [(rule["rule_id"], rule["passed"]) for rule in verdict["rule_trace"]] == [
        ("ptw_precondition_check", True),
        ("authority_check", True),
        ("zone_safety_check", True),
        ("lift_panel_oem_consent_check", False),
    ]
    assert verdict["reason"] == verdict["rule_trace"][-1]["reason"]
    assert verdict["rule_trace"][-1]["grounding"] == {**GROUNDING_CITATION, "effective_date": "2026-11-01"}
    assert verdict["rule_trace"][-1]["evaluated_as_of"] == WELL_AFTER.isoformat()


def test_adjudicate_passes_as_of_through_so_an_earlier_claim_is_go(effective_date):
    effective_date()
    claim = _claim(**PANEL_WORK_WITHOUT_CONSENT)

    verdict = adjudicate(claim, SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA, as_of=WELL_BEFORE)

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None
    assert verdict["rule_trace"][-1]["passed"] is True
    assert verdict["rule_trace"][-1]["evaluated_as_of"] == WELL_BEFORE.isoformat()


def test_adjudicate_without_as_of_fails_an_in_scope_claim_closed(effective_date):
    """A caller that omits as_of gets a fail-closed NO_GO, never a silent 'now'."""
    effective_date()

    verdict = adjudicate(_claim(**PANEL_WORK_WITHOUT_CONSENT), SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-LIFT-01"
    assert verdict["reason"].startswith("FAIL_CLOSED_NO_ADJUDICATION_TIME:")


def test_adjudicate_clears_panel_work_with_consent():
    claim = _claim(**{**PANEL_WORK_WITHOUT_CONSENT, "oem_consent_obtained": True})

    verdict = adjudicate(claim, SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA, as_of=WELL_AFTER)

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None
    assert verdict["rule_trace"][-1]["rule_id"] == "lift_panel_oem_consent_check"
    assert verdict["rule_trace"][-1]["passed"] is True
    assert verdict["rule_trace"][-1]["grounding"] == {
        **GROUNDING_CITATION,
        "effective_date": EFFECTIVE_DATE_UNCONFIRMED,
    }


def test_only_the_lift_panel_trace_entry_carries_grounding_and_as_of():
    """Rules 0-2 keep exactly the three keys they always had."""
    verdict = adjudicate(_claim(), SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA, as_of=WELL_AFTER)

    for entry in verdict["rule_trace"][:3]:
        assert set(entry) == {"rule_id", "passed", "reason"}
    assert set(verdict["rule_trace"][3]) == {"rule_id", "passed", "reason", "grounding", "evaluated_as_of"}


def test_an_earlier_rule_failure_is_reported_ahead_of_r_lift_01(effective_date):
    """Rule 3 runs last: a claim that also fails authority reports the authority failure, and Rule 3 is never reached."""
    effective_date()
    claim = _claim(**PANEL_WORK_WITHOUT_CONSENT)

    verdict = adjudicate(claim, SUPERINTENDENT, LOW_HAZARD_ZONE, issuer_roles=[], as_of=WELL_AFTER)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-AUTH-03"
    assert "lift_panel_oem_consent_check" not in [rule["rule_id"] for rule in verdict["rule_trace"]]


# --- Escalation routing: R-LIFT-01 -> RTO ---


def test_r_lift_01_routes_to_rto_not_qp_and_not_the_catch_all():
    """An execution-stage failure: RTO's continuous gate owns it. QP stays dormant (2026-08-18 rule)."""
    bindings = resolve_authority("ZONE-01", "R-LIFT-01")

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.binding_id == "BIND-RTO-LIFT-01"
    assert binding.role_type == AuthorityRoleType.RTO
    assert binding.role == "RTO"
    assert binding.activation == ActivationMode.CONTINUOUS
    assert binding.discipline is None


def test_r_lift_01_design_alteration_claim_also_gets_the_design_alteration_bindings():
    """The reason_code tier and the design-alteration tier stay orthogonal."""
    bindings = resolve_authority("ZONE-01", "R-LIFT-01", is_design_alteration=True)

    assert [binding.binding_id for binding in bindings] == ["BIND-RTO-LIFT-01", "BIND-QP-DA-01", "BIND-QE-DA-01"]


# --- HTTP layer ---


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
    """
    Entity-dispatch stub. `stored` is the claim's persisted evidence
    rows in insertion order, standing in for the append-only
    adjudication_records table: an ascending-id query gets the first,
    a descending-id query the last, and add() appends -- so a
    poll-written transition record is visible to later queries.
    """

    def __init__(self, stored=None):
        self.stored = list(stored or [])
        self.added = []

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is AuthorizedIssuer:
            return _StubResult(SUPERINTENDENT_ROW)
        if entity is IssuerRole:
            return _StubResult(RTO_AND_SA)
        if entity is IssuerProject:
            return _StubResult(["PROJ-TEST-01"])
        if entity is AdjudicationAuditEntry and stmt._limit_clause is not None:
            if not self.stored:
                return _StubResult(None)
            ascending = " ASC" in str(stmt.compile()) or " DESC" not in str(stmt.compile())
            return _StubResult(self.stored[0] if ascending else self.stored[-1])
        raise AssertionError(f"unexpected query in test_lift_panel_rule.py stub: {stmt}")

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, AdjudicationAuditEntry):
            self.stored.append(obj)

    async def commit(self):
        pass


class _StubRedis:
    async def hgetall(self, key):
        if key.endswith(":sensor"):
            return {}
        return {"hazard_level": "LOW", "active_crane": "false"}


@pytest.fixture
def maestro_calls(monkeypatch):
    calls: list = []

    def _make_recording_adapter(label: str):
        class _RecordingAdapter:
            def send_alert(self, alert):
                calls.append((label, alert))
                return DeliveryResult(claim_id=alert.claim_id, channel=label, delivered=True, detail="recorded")

        return _RecordingAdapter

    monkeypatch.setattr(airlock_router, "WhatsAppAdapter", _make_recording_adapter("whatsapp"))
    monkeypatch.setattr(airlock_router, "TelegramAdapter", _make_recording_adapter("telegram"))
    return calls


@pytest.fixture
def client_for():
    def _client(session: _StubSession) -> TestClient:
        async def _override_db_session():
            yield session

        async def _override_redis_client():
            yield _StubRedis()

        app.dependency_overrides[get_db_session] = _override_db_session
        app.dependency_overrides[get_redis_client] = _override_redis_client
        return TestClient(app)

    yield _client
    app.dependency_overrides.clear()


def _audit_entries(session: _StubSession) -> list:
    return [obj for obj in session.added if isinstance(obj, AdjudicationAuditEntry)]


def _lift_entry(rule_trace: list) -> dict:
    return next(rule for rule in rule_trace if rule["rule_id"] == "lift_panel_oem_consent_check")


# --- POST /airlock/claims: first adjudication, as of the server's clock ---


def test_http_in_scope_claim_under_the_shipped_placeholder_fails_closed(client_for, maestro_calls):
    """What actually happens today, with the effective date unconfirmed."""
    session = _StubSession()

    response = client_for(session).post("/airlock/claims", json=_claim_dict(**PANEL_WORK_WITHOUT_CONSENT))

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "NO_GO"
    assert evidence["reason_code"] == "R-LIFT-01"
    assert evidence["reason"].startswith("FAIL_CLOSED_EFFECTIVE_DATE_UNCONFIRMED:")
    assert _lift_entry(evidence["rule_trace"])["grounding"]["effective_date"] == "UNCONFIRMED"


def test_http_in_scope_claim_after_the_effective_date_is_no_go_r_lift_01_routed_to_rto(
    client_for, maestro_calls, effective_date
):
    effective_date("2026-01-01")  # already in force at the server's real "now"
    session = _StubSession()
    before = datetime.now(timezone.utc)

    response = client_for(session).post("/airlock/claims", json=_claim_dict(**PANEL_WORK_WITHOUT_CONSENT))

    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "NO_GO"
    assert evidence["reason_code"] == "R-LIFT-01"
    assert evidence["reason"].startswith("Regulatory Requirement Not Met:")
    assert evidence["authority_binding_id"] == ["BIND-RTO-LIFT-01"]

    # The citation and the time it was judged against are inside the
    # hashed record, on the failing entry. as_of is the server's clock.
    lift_entry = _lift_entry(evidence["rule_trace"])
    assert lift_entry["passed"] is False
    assert lift_entry["grounding"] == {**GROUNDING_CITATION, "effective_date": "2026-01-01"}
    assert before <= datetime.fromisoformat(lift_entry["evaluated_as_of"]) <= after
    unsigned = {key: value for key, value in evidence.items() if key != "sha256_signature"}
    assert evidence["sha256_signature"] == hashlib.sha256(
        json.dumps(unsigned, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # The three declared flags are part of the hashed input payload.
    assert {key: evidence["input_payload"][key] for key in PANEL_WORK_WITHOUT_CONSENT} == PANEL_WORK_WITHOUT_CONSENT

    persisted = _audit_entries(session)
    assert len(persisted) == 1
    assert persisted[0].decision == "NO_GO"
    assert persisted[0].record["sha256_signature"] == evidence["sha256_signature"]

    # NO_GO alerts go out on both channels, naming RTO as the escalation owner.
    assert {label for label, _ in maestro_calls} == {"whatsapp", "telegram"}
    for _, alert in maestro_calls:
        assert alert.reason_code == "R-LIFT-01"
        assert alert.authority_binding_id == ["BIND-RTO-LIFT-01"]
        assert alert.assigned_role == ["RTO"]
        assert alert.conflicting_condition.rule_id == "lift_panel_oem_consent_check"
        assert alert.conflicting_condition.grounding == lift_entry["grounding"]
        assert alert.conflicting_condition.evaluated_as_of == lift_entry["evaluated_as_of"]


def test_http_in_scope_claim_before_the_effective_date_is_go(client_for, maestro_calls, effective_date):
    effective_date("2999-01-01")  # not yet in force at the server's real "now"
    session = _StubSession()

    response = client_for(session).post("/airlock/claims", json=_claim_dict(**PANEL_WORK_WITHOUT_CONSENT))

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "GO"
    assert evidence["reason_code"] is None
    lift_entry = _lift_entry(evidence["rule_trace"])
    assert lift_entry["passed"] is True
    assert lift_entry["reason"].startswith("Requirement not yet in force")
    assert maestro_calls == []


def test_http_a_backdated_claim_timestamp_does_not_evade_the_rule(client_for, maestro_calls, effective_date):
    effective_date("2026-01-01")
    claim = _claim_dict(timestamp="2020-01-01T00:00:00Z", **PANEL_WORK_WITHOUT_CONSENT)

    response = client_for(_StubSession()).post("/airlock/claims", json=claim)

    assert response.json()["decision"] == "NO_GO"
    assert response.json()["reason_code"] == "R-LIFT-01"


def test_http_panel_work_with_consent_is_go_and_sends_no_alert(client_for, maestro_calls):
    claim = _claim_dict(**{**PANEL_WORK_WITHOUT_CONSENT, "oem_consent_obtained": True})

    response = client_for(_StubSession()).post("/airlock/claims", json=claim)

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "GO"
    assert evidence["reason_code"] is None
    assert evidence["authority_binding_id"] is None
    assert maestro_calls == []


def test_http_claim_omitting_the_three_flags_is_unaffected(client_for, maestro_calls):
    response = client_for(_StubSession()).post("/airlock/claims", json=_claim_dict())

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["decision"] == "GO"
    assert evidence["input_payload"]["lift_control_panel_work"] is False
    assert maestro_calls == []


@pytest.mark.parametrize("flag", ["lift_control_panel_work", "non_lift_component_installed", "oem_consent_obtained"])
def test_http_non_boolean_flag_is_rejected_at_the_schema_boundary(client_for, maestro_calls, flag):
    session = _StubSession()

    response = client_for(session).post("/airlock/claims", json=_claim_dict(**{flag: "per the OEM letter"}))

    assert response.status_code == 422
    assert _audit_entries(session) == []
    assert maestro_calls == []


# --- Polling routes: a stored claim is judged as of its FIRST adjudication ---

POLL_ROUTES = ["/frontline/blocked/CLM-LIFT-301/status", "/supervisor/blocked/CLM-LIFT-301/status"]


def _stored(decision: str, reason_code, evaluated_at: datetime) -> AdjudicationAuditEntry:
    record = {
        "claim_id": "CLM-LIFT-301",
        "decision": decision,
        "reason": "stored",
        "reason_code": reason_code,
        "rule_trace": [],
        "evaluated_at": evaluated_at.isoformat(),
        "input_payload": _claim(**PANEL_WORK_WITHOUT_CONSENT).model_dump(mode="json"),
    }
    return AdjudicationAuditEntry(claim_id="CLM-LIFT-301", decision=decision, record=record)


def _polled_verdict(response) -> tuple:
    body = response.json()
    if "evidence" in body:  # supervisor shape
        return body["evidence"]["decision"], body["evidence"]["reason_code"]
    return body["decision"], body["reasonCode"]


# An effective date already in the past at the server's real "now":
# every poll below happens after it. Stored first-adjudication times
# are placed either side of it.
POLL_EFFECTIVE_DATE = "2026-06-01"
ADJUDICATED_BEFORE_IT = datetime(2026, 5, 1, 4, 0, 0, tzinfo=timezone.utc)
ADJUDICATED_AFTER_IT = datetime(2026, 7, 1, 4, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("route", POLL_ROUTES)
def test_poll_does_not_flip_a_claim_cleared_before_the_requirement_took_effect(client_for, effective_date, route):
    """
    Non-retroactivity. The claim was cleared GO before the effective
    date; it is polled after it. Judged as of the poll it would be
    NO_GO (the next test shows exactly that for a later claim). Judged
    as of its first adjudication it stays GO, and nothing is written.
    """
    effective_date(POLL_EFFECTIVE_DATE)
    session = _StubSession(stored=[_stored("GO", None, ADJUDICATED_BEFORE_IT)])

    response = client_for(session).get(route)

    assert response.status_code == 200
    assert _polled_verdict(response) == ("GO", None)
    assert _audit_entries(session) == []


@pytest.mark.parametrize("route", POLL_ROUTES)
def test_poll_keeps_rejecting_a_claim_first_adjudicated_after_the_requirement_took_effect(
    client_for, effective_date, route
):
    effective_date(POLL_EFFECTIVE_DATE)
    session = _StubSession(stored=[_stored("NO_GO", "R-LIFT-01", ADJUDICATED_AFTER_IT)])

    response = client_for(session).get(route)

    assert response.status_code == 200
    assert _polled_verdict(response) == ("NO_GO", "R-LIFT-01")
    assert _audit_entries(session) == []


@pytest.mark.parametrize("route", POLL_ROUTES)
def test_poll_uses_the_earliest_record_not_the_latest(client_for, effective_date, route):
    """
    A claim whose history already holds a later record (written after
    the effective date) is still anchored to its first one. Anchored to
    the latest instead, this poll would return NO_GO.
    """
    effective_date(POLL_EFFECTIVE_DATE)
    session = _StubSession(
        stored=[_stored("GO", None, ADJUDICATED_BEFORE_IT), _stored("GO", None, ADJUDICATED_AFTER_IT)]
    )

    response = client_for(session).get(route)

    assert _polled_verdict(response) == ("GO", None)
    assert _audit_entries(session) == []


# --- fetch_earliest_adjudication_record() ---


@pytest.mark.asyncio
async def test_fetch_earliest_adjudication_record_asks_for_the_lowest_id_for_that_claim():
    first = _stored("GO", None, ADJUDICATED_BEFORE_IT)
    later = _stored("NO_GO", "R-LIFT-01", ADJUDICATED_AFTER_IT)
    statements = []

    class _RecordingSession(_StubSession):
        async def execute(self, stmt):
            statements.append(stmt)
            return await super().execute(stmt)

    record = await fetch_earliest_adjudication_record(_RecordingSession(stored=[first, later]), "CLM-LIFT-301")

    assert record == first.record
    compiled = str(statements[0].compile())
    assert "ORDER BY adjudication_records.id ASC" in compiled
    assert "CLM-LIFT-301" in statements[0].compile().params.values()


@pytest.mark.asyncio
async def test_fetch_earliest_adjudication_record_returns_none_for_an_unknown_claim():
    assert await fetch_earliest_adjudication_record(_StubSession(stored=[]), "CLM-MISSING") is None


# --- Maestro contract: a grounded trace entry must not break an alert ---


def test_outbound_alert_accepts_a_grounded_rule_trace_entry_and_ungrounded_ones_alike(effective_date):
    effective_date()
    verdict = adjudicate(
        _claim(**PANEL_WORK_WITHOUT_CONSENT), SUPERINTENDENT, LOW_HAZARD_ZONE, RTO_AND_SA, as_of=WELL_AFTER
    )
    evidence = {
        "claim_id": verdict["claim_id"],
        "decision": verdict["decision"],
        "reason_code": verdict["reason_code"],
        "rule_trace": verdict["rule_trace"],
        "evaluated_at": "2026-12-01T04:00:00+00:00",
        "input_payload": {},
    }

    alert = OutboundAlert.from_evidence_record(evidence, zone_id="ZONE-01")

    assert [rule.grounding for rule in alert.rule_trace[:3]] == [None, None, None]
    assert [rule.evaluated_as_of for rule in alert.rule_trace[:3]] == [None, None, None]
    assert alert.rule_trace[3].grounding == {**GROUNDING_CITATION, "effective_date": "2026-11-01"}
    assert alert.rule_trace[3].evaluated_as_of == WELL_AFTER.isoformat()
