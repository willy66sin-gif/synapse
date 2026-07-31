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
from src.airlock.schemas import ClaimPayload
from src.core.evaluator import adjudicate
from src.core.rules import IssuerRecord, ZoneRecord

SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)
SITE_ENGINEER = IssuerRecord(role="SITE_ENGINEER", clearance_level=1)

LOW_HAZARD_ZONE = ZoneRecord(hazard_level="LOW", active_crane=False)
HIGH_HAZARD_ZONE = ZoneRecord(hazard_level="HIGH", active_crane=True)


def _claim(**overrides) -> ClaimPayload:
    base = {
        "claim_id": "CLM-101",
        "timestamp": "2026-07-27T10:00:00Z",
        "issuer_id": "USR-SUP-01",
        "authority_level": 3,
        "zone_id": "ZONE-01",
        "action_type": "MATERIAL_ENTRY",
        "payload_data": {"truck_id": "SG1234A", "weight_tons": 12.5},
        "work_type": "NOMINAL_CIVIL",  # bypasses the ePTW gate — these tests predate it
    }
    base.update(overrides)
    return ClaimPayload(**base)


def test_nominal_claim_is_cleared():
    """synapse_mdm.py Test Case 1: Nominal -> CLEARED."""
    claim = _claim()

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE)

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

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=HIGH_HAZARD_ZONE)

    assert verdict["decision"] == "NO_GO"
    assert "Safety Violation" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"


def test_unauthenticated_issuer_is_rejected():
    """synapse_mdm.py's authority-failure case: Authority Failure -> REJECTED."""
    claim = _claim(issuer_id="USR-UNKNOWN")

    verdict = adjudicate(claim, issuer_record=None, zone_record=LOW_HAZARD_ZONE)

    assert verdict["decision"] == "NO_GO"
    assert "Authority Failure" in verdict["reason"]
    assert verdict["reason_code"] == "R-AUTH-01"


def test_insufficient_authority_level_is_rejected():
    """Authority Check branch: authority_level below the issuer's clearance_level."""
    claim = _claim(issuer_id="USR-ENG-02", authority_level=0)

    verdict = adjudicate(claim, issuer_record=SITE_ENGINEER, zone_record=LOW_HAZARD_ZONE)

    assert verdict["decision"] == "NO_GO"
    assert "Authority Failure" in verdict["reason"]
    assert verdict["reason_code"] == "R-AUTH-01"


def test_unknown_zone_is_blocked():
    """Zone Safety Check branch: zone_id has no Redis-backed state at all."""
    claim = _claim(zone_id="ZONE-99")

    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=None)

    assert verdict["decision"] == "NO_GO"
    assert "Safety Violation" in verdict["reason"]
    assert verdict["reason_code"] == "R-ZONE-01"


def test_adjudicate_returns_deterministic_verdict():
    """Same input -> same output, every time (no probabilistic logic)."""
    claim = _claim()

    first = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE)
    second = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE)

    assert first == second
