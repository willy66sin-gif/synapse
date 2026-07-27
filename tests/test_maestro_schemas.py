"""
Channel-agnostic Maestro schema tests.

Covers: valid GO/NO_GO construction, building from a real
evidence/emitter.py record (the actual downstream boundary), and the
fail-closed validation that a decision must always carry a matching
conflicting_condition — never a standalone verdict.
"""
import pytest
from pydantic import ValidationError

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


def test_go_alert_without_conflicting_condition_is_valid():
    alert = OutboundAlert(
        claim_id="CLM-101",
        decision="GO",
        rule_trace=[PASSING_RULE],
        conflicting_condition=None,
        evaluated_at="2026-07-27T10:00:00+00:00",
        recipient_id="+15551234567",
    )

    assert alert.decision == "GO"
    assert alert.conflicting_condition is None


def test_no_go_alert_requires_conflicting_condition():
    alert = OutboundAlert(
        claim_id="CLM-102",
        decision="NO_GO",
        rule_trace=[FAILING_RULE],
        conflicting_condition=FAILING_RULE,
        evaluated_at="2026-07-27T10:05:00+00:00",
        recipient_id="+15551234567",
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
        )


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        OutboundAlert(
            claim_id="CLM-101",
            decision="GO",
            rule_trace=[PASSING_RULE],
            evaluated_at="2026-07-27T10:00:00+00:00",
            recipient_id="+15551234567",
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

    alert = OutboundAlert.from_evidence_record(evidence, recipient_id="+15551234567")

    assert alert.decision == "GO"
    assert alert.conflicting_condition is None
    assert alert.claim_id == "CLM-101"


def test_from_evidence_record_builds_from_real_evidence_no_go():
    claim_payload = {"claim_id": "CLM-102", "issuer_id": "USR-SUP-01"}
    verdict = adjudicate(
        _claim(claim_payload["claim_id"], claim_payload["issuer_id"], zone_id="ZONE-99"),
        issuer_record=SUPERINTENDENT,
        zone_record=None,
    )
    evidence = emit_evidence(claim_payload, verdict)

    alert = OutboundAlert.from_evidence_record(evidence, recipient_id="+15551234567")

    assert alert.decision == "NO_GO"
    assert alert.conflicting_condition is not None
    assert "Safety Violation" in alert.conflicting_condition.reason


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
    )
