"""
CLAUDE.md's Locked Design Principles, enforced at the rendering layer:

- Supervisor UI Principle: never a standalone verdict — a NO_GO must
  always name the rule and the failing condition in the rendered text.
- Escalation Requirement: every rendered alert, GO or NO_GO, must
  state the escalation contact.
"""
from src.maestro.formatting import render_alert_text
from src.maestro.schemas import OutboundAlert, RuleConditionResult

PASSING_RULE = RuleConditionResult(rule_id="zone_safety_check", passed=True, reason="Zone Safety Validated")
FAILING_RULE = RuleConditionResult(
    rule_id="zone_safety_check",
    passed=False,
    reason="Safety Violation: Heavy lift requested in high-hazard zone 'ZONE-02'.",
)

ESCALATION_CONTACT = "Site Superintendent: +1-555-0100"


def test_go_alert_renders_without_failure_language():
    alert = OutboundAlert(
        claim_id="CLM-101",
        decision="GO",
        rule_trace=[PASSING_RULE],
        evaluated_at="2026-07-27T10:00:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    text = render_alert_text(alert)

    assert "CLM-101" in text
    assert "GO" in text
    assert "NO-GO" not in text


def test_go_alert_still_states_escalation_contact():
    """Escalation Requirement applies regardless of decision — GO alerts included."""
    alert = OutboundAlert(
        claim_id="CLM-101",
        decision="GO",
        rule_trace=[PASSING_RULE],
        evaluated_at="2026-07-27T10:00:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    text = render_alert_text(alert)

    assert ESCALATION_CONTACT in text


def test_no_go_alert_renders_rule_and_failing_condition():
    alert = OutboundAlert(
        claim_id="CLM-102",
        decision="NO_GO",
        rule_trace=[FAILING_RULE],
        conflicting_condition=FAILING_RULE,
        evaluated_at="2026-07-27T10:05:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    text = render_alert_text(alert)

    assert "CLM-102" in text
    assert "zone_safety_check" in text
    assert "Heavy lift requested in high-hazard zone 'ZONE-02'" in text


def test_no_go_alert_states_escalation_contact():
    alert = OutboundAlert(
        claim_id="CLM-102",
        decision="NO_GO",
        rule_trace=[FAILING_RULE],
        conflicting_condition=FAILING_RULE,
        evaluated_at="2026-07-27T10:05:00+00:00",
        recipient_id="+15551234567",
        escalation_contact=ESCALATION_CONTACT,
    )

    text = render_alert_text(alert)

    assert ESCALATION_CONTACT in text
