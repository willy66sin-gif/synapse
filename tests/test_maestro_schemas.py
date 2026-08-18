"""
Channel-agnostic Maestro schema tests.

Covers: valid GO/NO_GO construction, building from a real
evidence/emitter.py record (the actual downstream boundary), and the
fail-closed validation of CLAUDE.md's Locked Design Principles — a
decision must always carry a matching conflicting_condition (never a
standalone verdict), and every alert must carry an escalation_contact
(Escalation Requirement).
"""
import pytest
from pydantic import ValidationError

from src.airlock.schemas import ClaimPayload, WorkType
from src.core.evaluator import adjudicate
from src.core.rules import IssuerRecord, ZoneRecord
from src.evidence.emitter import emit_evidence
from src.maestro.schemas import OutboundAlert, RuleConditionResult

SUPERINTENDENT = IssuerRecord(role="SUPERINTENDENT", clearance_level=3)
LOW_HAZARD_ZONE = ZoneRecord(hazard_level="LOW", active_crane=False)
HIGH_HAZARD_ZONE = ZoneRecord(hazard_level="HIGH", active_crane=True)

PASSING_RULE = RuleConditionResult(rule_id="authority_check", passed=True, reason="Authority Validated")
FAILING_RULE = RuleConditionResult(
    rule_id="zone_safety_check",
    passed=False,
    reason="Safety Violation: Zone 'ZONE-02' does not exist.",
)

ESCALATION_CONTACT = "Site Superintendent: +1-555-0100"


def test_go_alert_without_conflicting_condition_is_valid():
    alert = OutboundAlert(
        claim_id="CLM-101",
        decision="GO",
        rule_trace=[PASSING_RULE],
        conflicting_condition=None,
        evaluated_at="2026-07-27T10:00:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    assert alert.decision == "GO"
    assert alert.conflicting_condition is None
    assert alert.escalation_contact == ESCALATION_CONTACT


def test_no_go_alert_requires_conflicting_condition():
    alert = OutboundAlert(
        claim_id="CLM-102",
        decision="NO_GO",
        rule_trace=[FAILING_RULE],
        conflicting_condition=FAILING_RULE,
        evaluated_at="2026-07-27T10:05:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    assert alert.decision == "NO_GO"
    assert alert.conflicting_condition.rule_id == "zone_safety_check"


def test_no_go_without_conflicting_condition_is_rejected():
    """Fail-closed: a NO_GO alert must never be a standalone verdict."""
    with pytest.raises(ValidationError):
        OutboundAlert(
            claim_id="CLM-102",
            decision="NO_GO",
            rule_trace=[FAILING_RULE],
            conflicting_condition=None,
            evaluated_at="2026-07-27T10:05:00+00:00",
            recipient_id="+15551234567",
            escalation_contact=ESCALATION_CONTACT,
        )


def test_go_with_conflicting_condition_is_rejected():
    """A GO alert carrying a failing condition is a contradiction, also rejected."""
    with pytest.raises(ValidationError):
        OutboundAlert(
            claim_id="CLM-101",
            decision="GO",
            rule_trace=[PASSING_RULE],
            conflicting_condition=FAILING_RULE,
            evaluated_at="2026-07-27T10:00:00+00:00",
            recipient_id="+15551234567",
            escalation_contact=ESCALATION_CONTACT,
        )


def test_missing_escalation_contact_is_rejected():
    """Fail-closed: CLAUDE.md's Escalation Requirement applies to every alert, GO or NO_GO."""
    with pytest.raises(ValidationError):
        OutboundAlert(
            claim_id="CLM-101",
            decision="GO",
            rule_trace=[PASSING_RULE],
            evaluated_at="2026-07-27T10:00:00+00:00",
            recipient_id="+15551234567",
        )


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        OutboundAlert(
            claim_id="CLM-101",
            decision="GO",
            rule_trace=[PASSING_RULE],
            evaluated_at="2026-07-27T10:00:00+00:00",
            recipient_id="+15551234567",
            escalation_contact=ESCALATION_CONTACT,
            unexpected_field="nope",
        )


def test_from_evidence_record_builds_from_real_evidence_go():
    """Integration point: Maestro sits downstream of the real evidence emitter, not a fixture."""
    claim_payload = {"claim_id": "CLM-101", "issuer_id": "USR-SUP-01"}
    verdict = adjudicate(
        _claim(claim_payload["claim_id"], claim_payload["issuer_id"]),
        issuer_record=SUPERINTENDENT,
        zone_record=LOW_HAZARD_ZONE,
    )
    evidence = emit_evidence(claim_payload, verdict)

    alert = OutboundAlert.from_evidence_record(evidence, zone_id="ZONE-01")

    assert alert.decision == "GO"
    assert alert.conflicting_condition is None
    assert alert.claim_id == "CLM-101"
    assert alert.reason_code is None
    # GO (reason_code=None) resolves to RTO (2026-08-18, direct
    # confirmation) -- see src/maestro/directory.py's DIRECTORY_MAP (the
    # Escalation Requirement still applies regardless of decision).
    # List-valued as of 2026-08-18 -- no is_design_alteration on this
    # minimal claim_payload dict, so just the one reason_code binding.
    assert alert.authority_binding_id == ["BIND-RTO-01"]
    assert alert.assigned_role == ["RTO"]
    assert alert.recipient_id == "RTO"  # no contact_id on file yet
    # Supervisor Override Retirement (5 Aug 2026): states the resolved authority
    # directly, not an override URL -- see OutboundAlert.from_evidence_record().
    assert alert.escalation_contact == "RTO (BIND-RTO-01)"


def test_from_evidence_record_builds_from_real_evidence_no_go():
    """R-ZONE-01 now resolves via its own reason_code routing entry
    (2026-08-06, Task 3), not the ("*", "*") catch-all -- see
    src/maestro/directory.py's DIRECTORY_MAP."""
    claim_payload = {"claim_id": "CLM-102", "issuer_id": "USR-SUP-01"}
    verdict = adjudicate(
        _claim(claim_payload["claim_id"], claim_payload["issuer_id"], zone_id="ZONE-99"),
        issuer_record=SUPERINTENDENT,
        zone_record=None,
    )
    evidence = emit_evidence(claim_payload, verdict)

    alert = OutboundAlert.from_evidence_record(evidence, zone_id="ZONE-99")

    assert alert.decision == "NO_GO"
    assert alert.conflicting_condition is not None
    assert "Safety Violation" in alert.conflicting_condition.reason
    assert alert.reason_code == "R-ZONE-01"
    assert alert.authority_binding_id == ["BIND-SA-01"]
    assert alert.assigned_role == ["SA"]


def test_from_evidence_record_carries_eptw_reason_code_through():
    """reason_code must flow Verdict -> emit_evidence() -> OutboundAlert unchanged,
    not just for the zone-safety case above but for the ePTW gate too."""
    claim_payload = {"claim_id": "CLM-EPTW-900", "issuer_id": "USR-SUP-01"}
    claim = ClaimPayload(
        claim_id=claim_payload["claim_id"],
        timestamp="2026-07-31T10:00:00Z",
        issuer_id=claim_payload["issuer_id"],
        authority_level=3,
        zone_id="ZONE-01",
        action_type="EXCAVATION_WORK",
        payload_data={},
        work_type=WorkType.EXCAVATION,
        ptw_context=None,
    )
    verdict = adjudicate(claim, issuer_record=SUPERINTENDENT, zone_record=LOW_HAZARD_ZONE)
    evidence = emit_evidence(claim_payload, verdict)

    alert = OutboundAlert.from_evidence_record(evidence, zone_id="ZONE-01")

    assert alert.decision == "NO_GO"
    assert alert.reason_code == "R-PTW-01"
    assert evidence["reason_code"] == "R-PTW-01"
    # R-PTW-01 resolves to RTO (2026-08-18, direct confirmation) -- see DIRECTORY_MAP.
    assert alert.authority_binding_id == ["BIND-RTO-01"]


def test_outbound_alert_accepts_reason_code_directly():
    """Sanity check on the field itself, independent of from_evidence_record."""
    alert = OutboundAlert(
        claim_id="CLM-101",
        decision="NO_GO",
        rule_trace=[FAILING_RULE],
        conflicting_condition=FAILING_RULE,
        reason_code="R-AUTH-01",
        evaluated_at="2026-07-27T10:05:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    assert alert.reason_code == "R-AUTH-01"


def _claim(claim_id: str, issuer_id: str, zone_id: str = "ZONE-01"):
    from src.airlock.schemas import ClaimPayload

    return ClaimPayload(
        claim_id=claim_id,
        timestamp="2026-07-27T10:00:00Z",
        issuer_id=issuer_id,
        authority_level=3,
        zone_id=zone_id,
        action_type="MATERIAL_ENTRY",
        payload_data={"truck_id": "SG1234A", "weight_tons": 12.5},
        work_type="NOMINAL_CIVIL",
    )
