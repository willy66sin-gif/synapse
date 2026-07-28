"""
Shared human-readable rendering of an OutboundAlert.

Enforces two of CLAUDE.md's Locked Design Principles in one place —
every channel adapter renders alerts through render_alert_text, so
neither rule can be silently dropped by an individual adapter:

- Supervisor UI Principle: never a standalone verdict.
- Escalation Requirement: every alert states how to escalate/override,
  regardless of channel or decision.
"""
from src.maestro.schemas import OutboundAlert


def render_alert_text(alert: OutboundAlert) -> str:
    if alert.decision == "GO":
        last_rule = alert.rule_trace[-1] if alert.rule_trace else None
        rule_note = f" (last check passed: {last_rule.rule_id})" if last_rule else ""
        body = f"Claim {alert.claim_id}: GO{rule_note}."
    else:
        condition = alert.conflicting_condition
        body = f"Claim {alert.claim_id}: NO-GO — rule '{condition.rule_id}' failed: {condition.reason}"

    return f"{body} To escalate/override, contact: {alert.escalation_contact}."
