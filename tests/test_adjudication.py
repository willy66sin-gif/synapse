"""
Deterministic rule evaluation scenarios.

Per CLAUDE.md: adjudication logic requires 100% path coverage.
Each rule needs at least a pass-case and a fail-case test.

The three core scenarios below port synapse_mdm.py's `run_tests`
cases 1:1 (Nominal/CLEARED, Safety Violation/BLOCKED, Authority
Failure/REJECTED). Unlike the reference harness, adjudicate() here
never touches a database or cache itself — IssuerRecord/ZoneRecord
stand in for what src/core/repository.py would have fetched from
PostgreSQL/Redis, so these are plain synchronous unit tests.
"""
from datetime import datetime, timedelta, timezone

from src.airlock.schemas import ClaimPayload
from src.core.evaluator import adjudicate
from src.core.roles import AuthorityRoleType
from src.core.rules import IssuerRecord, ZoneRecord

SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)
SITE_ENGINEER = IssuerRecord(role="SITE_ENGINEER", clearance_level=1)

# 2026-08-27, Authority Admissibility handoff: authority_check() now
# gates on GATE_ADMISSIBLE_ROLES membership (src/core/rules.py), not
# authority_level/clearance_level. SUPERINTENDENT holds RTO for these
# fixtures' GO/zone-reaching scenarios; SITE_ENGINEER deliberately does
# not, to keep the existing insufficient-admissibility test scenarios
# failing the way they did before this change (see each test below).
#
# 2026-08-28, R-ZONE-01/R-PTW-01 Admissibility handoff: check_zone_safety()
# now also gates on GATE_ADMISSIBLE_ROLES (SA) -- SA added to
# SUPERINTENDENT_ROLES so the existing GO-path tests below still reach
# GO (they were never about zone-admissibility, and shouldn't start
# failing there as a side effect of this pass).
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
NO_ROLES: list[AuthorityRoleType] = []

LOW_HAZARD_ZONE = ZoneRecord(hazard_level="LOW", active_crane=False)
HIGH_HAZARD_ZONE = ZoneRecord(hazard_level="HIGH", active_crane=True)


def _claim(**overrides) -> ClaimPayload:
    base = {
        "claim_id": "CLM-101",
        "timestamp": "2026-07-27T10:00:00Z",
        "project_id": "PROJ-TEST-01",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {"truck_id": "SG1234A", "weight_tons": 12.5},
        "work_type": "NOMINAL_CIVIL",  # bypasses the ePTW gate — these tests predate it
    }
    base.update(overrides)
    # 2026-11-03: Rule 0 now treats a LIFT_OPERATION label on non-high-risk
    # work as LIFTING work needing a permit. A lift claim that doesn't pick
    # its own work_type is therefore built as declared LIFTING work with a
    # valid permit (window around real now, same zone, RTO issuer via
    # SUPERINTENDENT_ROLES), so it still reaches Rule 2 as these tests
    # intend. Pattern from tests/test_lifting_civil_hotwork_confined_matrix.py.
    if base["action_type"] == "LIFT_OPERATION" and "work_type" not in overrides:
        now = datetime.now(timezone.utc)
        base["work_type"] = "LIFTING"
        base["ptw_context"] = {
            "ptw_id": "PTW-2026-0110",
            "status": "APPROVED",
            "valid_from": (now - timedelta(hours=24)).isoformat(),
            "valid_until": (now + timedelta(hours=24)).isoformat(),
            "permit_type": "LIFTING",
            "zone_id": base["zone_id"],
            "issuer_id": base["issuer_id"],
        }
    return ClaimPayload(**base)


def test_nominal_claim_is_cleared():
    """synapse_mdm.py Test Case 1: Nominal -> CLEARED."""
    claim = _claim()

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "GO"
    assert verdict["claim_id"] == "CLM-101"
    assert all(rule["passed"] for rule in verdict["rule_trace"])
    assert verdict["reason_code"] is None


def test_lift_operation_in_high_hazard_zone_is_blocked():
    """synapse_mdm.py Test Case 2: Safety Violation -> BLOCKED."""
    claim = _claim(
        claim_id="CLM-102",
        zone_id="ZONE-02",
        action_type="LIFT_OPERATION",
        payload_data={"crane_id": "CR-01"},
    )

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=HIGH_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "NO_GO"
    assert "Safety Violation" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"


def test_unauthenticated_issuer_is_rejected():
    """synapse_mdm.py's authority-failure case: Authority Failure -> REJECTED."""
    claim = _claim(issuer_id="USR-UNKNOWN")

    verdict = adjudicate(claim, issuer_record=None, zone_record=LOW_HAZARD_ZONE, issuer_roles=NO_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert "Authority Failure" in verdict["reason"]
    assert verdict["reason_code"] == "R-AUTH-01"


def test_issuer_holding_multiple_roles_passes_via_the_one_admissible_role():
    """2026-08-27, Authority Admissibility handoff: confirms the
    intersection check handles one-role-among-several naturally --
    holding PE (not admissible for authority_check on its own) plus RTO
    (the actually-admissible role) must still pass, not require RTO to
    be the issuer's only role. This is the real-world precedent
    IssuerRole's join-table shape was built for (an issuer independently
    holding several roles at once). SA also included (2026-08-28,
    R-ZONE-01/R-PTW-01 Admissibility handoff) so this claim still
    reaches GO through the now-also-gated zone_safety_check."""
    claim = _claim()

    verdict = adjudicate(
        claim,
        issuer_record=SUPERINTENDENT,
        zone_record=LOW_HAZARD_ZONE,
        issuer_roles=[AuthorityRoleType.PE, AuthorityRoleType.RTO, AuthorityRoleType.SA],
    )

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None


def test_missing_admissible_role_on_nominal_work_is_rejected():
    """Authority Check branch (2026-08-27, Authority Admissibility
    handoff): issuer is recognized but holds none of
    GATE_ADMISSIBLE_ROLES' required roles (RTO) for authority_check, on
    NOMINAL_CIVIL work -- R-AUTH-03 (2026-08-06 R-AUTH-01
    disambiguation), not R-AUTH-01 (that code is now
    unauthenticated-issuer-only, a different failure mode).
    authority_level/clearance_level are still on the fixture/schema
    (not deleted, per the handoff's explicit instruction) but no longer
    read by this decision -- SITE_ENGINEER simply holds no roles."""
    claim = _claim(issuer_id="USR-ENG-02", authority_level=0)  # work_type: NOMINAL_CIVIL, from _claim()'s base

    verdict = adjudicate(claim, issuer_record=SITE_ENGINEER, zone_record=LOW_HAZARD_ZONE, issuer_roles=NO_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert "Authority Failure" in verdict["reason"]
    assert verdict["reason_code"] == "R-AUTH-03"


def test_r_auth_02_is_unreachable_for_high_risk_work_given_a_valid_permit():
    """R-AUTH-02 (authority_check's high-risk-work admissibility
    failure) is intentionally DORMANT as of the 2026-08-28
    R-ZONE-01/R-PTW-01 Admissibility handoff, not an oversight: Rule 0
    (verify_ptw_precondition) now gates the identical role (RTO) on
    the identical work types before Rule 1 (authority_check) ever
    runs, so a claim can only reach Rule 1 for high-risk work once RTO
    is already confirmed present -- R-AUTH-02's "recognized issuer,
    missing RTO, high-risk work" condition can never be true by the
    time Rule 1 evaluates it. This was flagged and explicitly accepted
    at implementation time (not resolved by minting a new reason code
    or reordering gate ownership -- both out of scope for that
    handoff); this test guards against that becoming true again
    silently, e.g. if a future change reorders Rule 0 after Rule 1, or
    changes Rule 0's gate to a role other than RTO.

    Formerly test_missing_admissible_role_on_high_risk_work_is_rejected,
    which asserted the now-unreachable R-AUTH-02 outcome directly; this
    replaces it with the current, correct behavior -- the claim is
    rejected for R-PTW-01 (Rule 0), never reaches Rule 1 at all."""
    claim = _claim(
        issuer_id="USR-ENG-02",
        authority_level=0,
        work_type="EXCAVATION",
        ptw_context={
            "ptw_id": "PTW-900",
            "status": "APPROVED",
            "valid_from": "2020-01-01T00:00:00+00:00",
            "valid_until": "2099-01-01T00:00:00+00:00",
            "permit_type": "EXCAVATION",
            "zone_id": "ZONE-01",
            "issuer_id": "USR-ENG-02",
        },
    )

    verdict = adjudicate(claim, issuer_record=SITE_ENGINEER, zone_record=LOW_HAZARD_ZONE, issuer_roles=NO_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-PTW-01"
    assert verdict["reason_code"] != "R-AUTH-02"
    rule_ids = [rule["rule_id"] for rule in verdict["rule_trace"]]
    assert rule_ids == ["ptw_precondition_check"]


def test_unknown_zone_is_blocked():
    """Zone Safety Check branch: zone_id has no Redis-backed state at all."""
    claim = _claim(zone_id="ZONE-99")

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=None, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert "Safety Violation" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"


def test_missing_sa_admissible_role_on_zone_safety_is_rejected():
    """2026-08-28, R-ZONE-01/R-PTW-01 Admissibility handoff:
    check_zone_safety() now gates on GATE_ADMISSIBLE_ROLES (SA), same
    intersection pattern as authority_check(). Issuer holds RTO
    (authority_check passes, so the claim actually reaches Rule 2) but
    not SA -- zone_safety_check must reject it, reusing R-ZONE-01
    (least new surface area, same call made for authority_check's
    reason-code reuse in the prior pass) rather than minting a new
    code."""
    claim = _claim()

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=[AuthorityRoleType.RTO]
    )

    assert verdict["decision"] == "NO_GO"
    assert "Safety Violation" in verdict["reason"]
    assert "admissible role" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"
    rule_ids = [rule["rule_id"] for rule in verdict["rule_trace"]]
    assert rule_ids == ["ptw_precondition_check", "authority_check", "zone_safety_check"]


def test_sa_admissible_role_present_passes_zone_safety():
    """Positive-path companion to the rejection above: SA present (plus
    RTO for authority_check) reaches GO through zone_safety_check."""
    claim = _claim()

    verdict = adjudicate(
        claim,
        issuer_record=SUPERINTENDENT,
        zone_record=LOW_HAZARD_ZONE,
        issuer_roles=[AuthorityRoleType.RTO, AuthorityRoleType.SA],
    )

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None


def test_hazard_violation_reason_unchanged_when_sa_also_missing():
    """check_zone_safety()'s existing hazard-specific check (LIFT_OPERATION
    in a HIGH-hazard zone) is ordered before the new SA-admissibility
    check (see check_zone_safety()'s docstring) -- an issuer missing
    both SA and a valid hazard posture still gets the pre-existing,
    more specific hazard reason, not the generic admissibility one."""
    claim = _claim(
        claim_id="CLM-102",
        zone_id="ZONE-02",
        action_type="LIFT_OPERATION",
        payload_data={"crane_id": "CR-01"},
    )

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=HIGH_HAZARD_ZONE, issuer_roles=[AuthorityRoleType.RTO]
    )

    assert verdict["decision"] == "NO_GO"
    assert "Heavy lift requested" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"


def test_adjudicate_returns_deterministic_verdict():
    """Same input -> same output, every time (no probabilistic logic)."""
    claim = _claim()

    first = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )
    second = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert first == second


# --- hazard_level fail-closed (2026-11-02): a LIFT_OPERATION claim
# against a zone whose hazard_level is missing or not exactly "LOW" or
# "HIGH" is NO_GO under R-ZONE-01. hazard_level=None is what
# fetch_zone_record() returns when the Redis hash has no such field.


def _lift_claim() -> ClaimPayload:
    return _claim(claim_id="CLM-110", zone_id="ZONE-02", action_type="LIFT_OPERATION", payload_data={"crane_id": "CR-01"})


def _assert_unrecognised_hazard_no_go(verdict, hazard_level) -> None:
    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-ZONE-01"
    assert verdict["reason"] == (
        f"Safety Violation: Hazard level for zone 'ZONE-02' is missing or unrecognised "
        f"({hazard_level!r}); lift operation cannot be cleared."
    )
    assert verdict["rule_trace"][-1] == {"rule_id": "zone_safety_check", "passed": False, "reason": verdict["reason"]}


def test_lift_operation_with_missing_hazard_level_is_blocked():
    zone = ZoneRecord(hazard_level=None, active_crane=True)

    verdict = adjudicate(_lift_claim(), issuer_record=SUPERINTENDENT, zone_record=zone, issuer_roles=SUPERINTENDENT_ROLES)

    _assert_unrecognised_hazard_no_go(verdict, None)


def test_lift_operation_with_unknown_hazard_level_is_blocked():
    zone = ZoneRecord(hazard_level="MEDIUM", active_crane=True)

    verdict = adjudicate(_lift_claim(), issuer_record=SUPERINTENDENT, zone_record=zone, issuer_roles=SUPERINTENDENT_ROLES)

    _assert_unrecognised_hazard_no_go(verdict, "MEDIUM")


def test_lift_operation_with_differently_cased_hazard_level_is_blocked():
    zone = ZoneRecord(hazard_level="High", active_crane=True)

    verdict = adjudicate(_lift_claim(), issuer_record=SUPERINTENDENT, zone_record=zone, issuer_roles=SUPERINTENDENT_ROLES)

    _assert_unrecognised_hazard_no_go(verdict, "High")


def test_lift_operation_in_low_hazard_zone_is_still_cleared():
    verdict = adjudicate(
        _lift_claim(), issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None


def test_lift_operation_in_high_hazard_zone_reason_is_unchanged():
    verdict = adjudicate(
        _lift_claim(), issuer_record=SUPERINTENDENT, zone_record=HIGH_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-ZONE-01"
    assert verdict["reason"] == "Safety Violation: Heavy lift requested in high-hazard zone 'ZONE-02'."


def test_non_lift_claim_with_missing_hazard_level_is_unchanged():
    zone = ZoneRecord(hazard_level=None, active_crane=False)

    verdict = adjudicate(_claim(), issuer_record=SUPERINTENDENT, zone_record=zone, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None


# --- Rule 0 lift label (2026-11-03): a NOMINAL_CIVIL claim labelled
# LIFT_OPERATION is treated as LIFTING work and needs a LIFTING permit.
# work_type is passed explicitly so _claim() keeps it NOMINAL_CIVIL.

LIFT_LABEL_CLAUSE = (
    " Permit required because action_type 'LIFT_OPERATION' on work_type 'NOMINAL_CIVIL' is treated as LIFTING work."
)


def _permit(permit_type: str, zone_id: str = "ZONE-01") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "ptw_id": "PTW-2026-0120",
        "status": "APPROVED",
        "valid_from": (now - timedelta(hours=24)).isoformat(),
        "valid_until": (now + timedelta(hours=24)).isoformat(),
        "permit_type": permit_type,
        "zone_id": zone_id,
        "issuer_id": "USR-SUP-01",
    }


def test_nominal_civil_lift_label_without_permit_is_rejected_at_rule_0():
    claim = _claim(action_type="LIFT_OPERATION", work_type="NOMINAL_CIVIL")

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-PTW-01"
    assert verdict["reason"] == (
        "FAIL_CLOSED_EPTW_PRECONDITION: No permit-to-work context provided for high-risk work_type 'LIFTING'."
        + LIFT_LABEL_CLAUSE
    )
    assert [entry["rule_id"] for entry in verdict["rule_trace"]] == ["ptw_precondition_check"]


def test_nominal_civil_lift_label_with_lifting_permit_proceeds_to_rule_2():
    claim = _claim(action_type="LIFT_OPERATION", work_type="NOMINAL_CIVIL", ptw_context=_permit("LIFTING"))

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None
    assert verdict["rule_trace"][0] == {
        "rule_id": "ptw_precondition_check",
        "passed": True,
        "reason": "Permit 'PTW-2026-0120' validated for 'LIFTING'." + LIFT_LABEL_CLAUSE,
    }
    assert verdict["rule_trace"][2] == {"rule_id": "zone_safety_check", "passed": True, "reason": "Zone Safety Validated"}


def test_nominal_civil_lift_label_with_wrong_permit_type_is_rejected_at_rule_0():
    claim = _claim(action_type="LIFT_OPERATION", work_type="NOMINAL_CIVIL", ptw_context=_permit("EXCAVATION"))

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-PTW-01"
    assert verdict["reason"] == (
        "FAIL_CLOSED_EPTW_PRECONDITION: Permit 'PTW-2026-0120' type 'EXCAVATION' does not match claimed "
        "work_type 'LIFTING'." + LIFT_LABEL_CLAUSE
    )


def test_nominal_civil_material_entry_without_permit_is_still_cleared():
    claim = _claim(work_type="NOMINAL_CIVIL")

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES)

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None
    assert verdict["rule_trace"][0]["reason"] == "No permit required for work_type 'NOMINAL_CIVIL'."
