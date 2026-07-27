"""
Shared human-readable rendering of an OutboundAlert.

Enforces the Supervisor UI design principle in one place: every
channel adapter renders alerts through render_alert_text, so "never a
standalone verdict" holds regardless of which channel is delivering
it, instead of each adapter re-implementing (and potentially
violating) that rule independently.
"""
from src.maestro.schemas import OutboundAlert


def render_alert_text(alert: OutboundAlert) -> str:
    if alert.decision == "GO":
        last_rule = alert.rule_trace[-1] if alert.rule_trace else None
        rule_note = f" (last check passed: {last_rule.rule_id})" if last_rule else ""
        return f"Claim {alert.claim_id}: GO{rule_note}."

    condition = alert.conflicting_condition
    return f"Claim {alert.claim_id}: NO-GO — rule '{condition.rule_id}' failed: {condition.reason}"
