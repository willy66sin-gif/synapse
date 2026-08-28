"""
ePTW (Permit-to-Work) precondition gate tests.

Covers src/core/rules.py's verify_ptw_precondition directly, unit
style (mirroring tests/test_adjudication.py's approach), plus a small
set of adjudicate()-level tests proving the gate actually runs first
in the pipeline, per the explicit ordering requirement it was added
under — not just that the pure function's logic is correct in
isolation.
"""
from datetime import datetime, timedelta, timezone

from src.airlock.schemas import ClaimPayload, PtwContext, WorkType
from src.core.evaluator import adjudicate
from src.core.roles import AuthorityRoleType
from src.core.rules import IssuerRecord, ZoneRecord, verify_ptw_precondition

SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)
LOW_HAZARD_ZONE = ZoneRecord(hazard_level="LOW", active_crane=False)

# 2026-08-27, Authority Admissibility handoff: authority_check() now
# gates on GATE_ADMISSIBLE_ROLES membership, not clearance_level --
# SUPERINTENDENT needs RTO to reach GO / later rules in these tests.
#
# 2026-08-28, R-ZONE-01/R-PTW-01 Admissibility handoff:
# verify_ptw_precondition() now also gates on GATE_ADMISSIBLE_ROLES
# (RTO, same role as authority_check's) and check_zone_safety() gates
# on it too (SA) -- SA added here so this file's adjudicate()-level GO
# tests still reach GO through zone_safety_check. The direct
# verify_ptw_precondition() unit tests below only ever need RTO (SA is
# never consulted by that function), but pass SUPERINTENDENT_ROLES
# uniformly for simplicity -- extra roles present are never a problem,
# per the same intersection semantics classify_authority_failure()
# already established.
SUPERINTENDENT_ROLES = [AuthorityRoleType.RTO, AuthorityRoleType.SA]
NO_ROLES: list[AuthorityRoleType] = []

NOW = datetime.now(timezone.utc)
VALID_FROM = (NOW - timedelta(hours=1)).isoformat()
VALID_UNTIL = (NOW + timedelta(hours=1)).isoformat()


def _ptw(**overrides) -> PtwContext:
    base = {
        "ptw_id": "PTW-001",
        "status": "APPROVED",
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
        "permit_type": WorkType.EXCAVATION,
        "zone_id": "ZONE-01",
        "issuer_id": "USR-SUP-01",
    }
    base.update(overrides)
    return PtwContext(**base)


def _claim(**overrides) -> ClaimPayload:
    base = {
        "claim_id": "CLM-EPTW-001",
        "timestamp": "2026-07-28T10:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "EXCAVATION_WORK",
        "payload_data": {},
        "work_type": WorkType.EXCAVATION,
        "ptw_context": _ptw(),
    }
    base.update(overrides)
    return ClaimPayload(**base)


# --- verify_ptw_precondition: fail-closed branches ---


def test_missing_ptw_context_fails_closed():
    claim = _claim(ptw_context=None)

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION" in outcome.reason


def test_status_pending_fails_closed():
    claim = _claim(ptw_context=_ptw(status="PENDING"))

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION" in outcome.reason


def test_status_expired_fails_closed():
    claim = _claim(ptw_context=_ptw(status="EXPIRED"))

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False


def test_status_revoked_fails_closed():
    claim = _claim(ptw_context=_ptw(status="REVOKED"))

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False


def test_permit_expired_fails_closed():
    """valid_until in the past — the permit lapsed before this claim."""
    claim = _claim(
        ptw_context=_ptw(
            valid_from=(NOW - timedelta(hours=3)).isoformat(),
            valid_until=(NOW - timedelta(hours=2)).isoformat(),
        )
    )

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION" in outcome.reason


def test_permit_not_yet_valid_fails_closed():
    """valid_from in the future — the permit hasn't started yet."""
    claim = _claim(
        ptw_context=_ptw(
            valid_from=(NOW + timedelta(hours=1)).isoformat(),
            valid_until=(NOW + timedelta(hours=2)).isoformat(),
        )
    )

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION" in outcome.reason


def test_zone_mismatch_fails_closed():
    claim = _claim(zone_id="ZONE-01", ptw_context=_ptw(zone_id="ZONE-02"))

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "does not match claim zone" in outcome.reason


def test_permit_type_mismatch_fails_closed():
    claim = _claim(work_type=WorkType.EXCAVATION, ptw_context=_ptw(permit_type=WorkType.HOT_WORK))

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is False
    assert "does not match claimed work_type" in outcome.reason


def test_all_four_high_risk_types_require_ptw_when_missing():
    for work_type in (WorkType.EXCAVATION, WorkType.LIFTING, WorkType.HOT_WORK, WorkType.CONFINED_SPACE):
        claim = _claim(work_type=work_type, ptw_context=None)

        outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

        assert outcome.passed is False, f"{work_type} should require a permit"


# --- verify_ptw_precondition: issuer admissibility (2026-08-28, R-ZONE-01/R-PTW-01 Admissibility handoff) ---


def test_missing_rto_admissible_role_fails_closed_with_valid_permit():
    """A fully valid, matching permit still fails closed if the issuer
    doesn't hold RTO -- reuses R-PTW-01 (checked via the reason text
    prefix; adjudicate()-level tests cover the reason_code itself),
    least new surface area, same call already made for
    authority_check's admissibility gate."""
    claim = _claim()

    outcome = verify_ptw_precondition(claim, issuer_roles=NO_ROLES)

    assert outcome.passed is False
    assert "FAIL_CLOSED_EPTW_PRECONDITION" in outcome.reason
    assert "admissible role" in outcome.reason


def test_rto_admissible_role_present_passes():
    """Positive-path companion: RTO present, valid permit -> passes."""
    claim = _claim()

    outcome = verify_ptw_precondition(claim, issuer_roles=[AuthorityRoleType.RTO])

    assert outcome.passed is True


def test_missing_rto_role_does_not_mask_a_missing_permit():
    """Admissibility is checked last (see verify_ptw_precondition()'s
    docstring) -- a claim missing the permit AND the RTO role still
    fails for the pre-existing ctx-missing reason, not the new
    admissibility one, since the ctx check runs first."""
    claim = _claim(ptw_context=None)

    outcome = verify_ptw_precondition(claim, issuer_roles=NO_ROLES)

    assert outcome.passed is False
    assert "No permit-to-work context provided" in outcome.reason


# --- verify_ptw_precondition: pass-through ---


def test_valid_permit_passes():
    claim = _claim()

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is True
    assert outcome.rule_id == "ptw_precondition_check"


def test_nominal_civil_bypasses_precondition_even_without_context():
    claim = _claim(work_type=WorkType.NOMINAL_CIVIL, ptw_context=None)

    outcome = verify_ptw_precondition(claim, issuer_roles=SUPERINTENDENT_ROLES)

    assert outcome.passed is True


# --- adjudicate(): confirm the gate is actually wired in first ---


def test_adjudicate_rejects_before_authority_check_even_runs():
    """
    The PTW gate must run first: a claim missing both a valid permit
    AND a valid issuer should be rejected for the PTW reason — proving
    the gate short-circuits before authority_check is even evaluated,
    not just that it happens to also fail.
    """
    claim = _claim(issuer_id="USR-UNKNOWN", ptw_context=None)

    verdict = adjudicate(claim, issuer_record=None, zone_record=LOW_HAZARD_ZONE, issuer_roles=NO_ROLES)

    assert verdict["decision"] == "NO_GO"
    assert verdict["reason_code"] == "R-PTW-01"
    assert len(verdict["rule_trace"]) == 1
    assert verdict["rule_trace"][0]["rule_id"] == "ptw_precondition_check"


def test_adjudicate_accepts_valid_high_risk_claim_end_to_end():
    claim = _claim()

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "GO"
    assert verdict["reason_code"] is None
    rule_ids = [rule["rule_id"] for rule in verdict["rule_trace"]]
    assert rule_ids == ["ptw_precondition_check", "authority_check", "zone_safety_check"]


def test_adjudicate_nominal_civil_unaffected_by_eptw_gate():
    """Non-high-risk claims pass the precondition transparently and
    behave exactly as they did before the ePTW gate existed."""
    claim = _claim(work_type=WorkType.NOMINAL_CIVIL, ptw_context=None, action_type="MATERIAL_ENTRY")

    verdict = adjudicate(
        claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE, issuer_roles=SUPERINTENDENT_ROLES
    )

    assert verdict["decision"] == "GO"
    assert verdict["rule_trace"][0]["rule_id"] == "ptw_precondition_check"
    assert verdict["rule_trace"][0]["passed"] is True
