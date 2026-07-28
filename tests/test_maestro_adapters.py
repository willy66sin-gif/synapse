"""
Channel adapter tests.

The parametrized tests run the same assertions against both
WhatsAppAdapter and TelegramAdapter through the shared ChannelAdapter
interface — proving the channel is interchangeable, per CLAUDE.md's
System Principle ("Core decides, Maestro delivers"). Channel-specific
webhook-shape tests are kept separate since that's exactly the part
that isn't interchangeable.
"""
import pytest

from src.maestro.adapters.telegram import TelegramAdapter
from src.maestro.adapters.whatsapp import WhatsAppAdapter
from src.maestro.schemas import OutboundAlert, RuleConditionResult

PASSING_RULE = RuleConditionResult(rule_id="authority_check", passed=True, reason="Authority Validated")
FAILING_RULE = RuleConditionResult(
    rule_id="zone_safety_check",
    passed=False,
    reason="Safety Violation: Zone 'ZONE-02' does not exist.",
)

ESCALATION_CONTACT = "Site Superintendent: +1-555-0100"

GO_ALERT = OutboundAlert(
    claim_id="CLM-101",
    decision="GO",
    rule_trace=[PASSING_RULE],
    evaluated_at="2026-07-27T10:00:00+00:00",
    recipient_id="+15551234567",
    escalation_contact=ESCALATION_CONTACT,
)

NO_GO_ALERT = OutboundAlert(
    claim_id="CLM-102",
    decision="NO_GO",
    rule_trace=[FAILING_RULE],
    conflicting_condition=FAILING_RULE,
    evaluated_at="2026-07-27T10:05:00+00:00",
    recipient_id="+15551234567",
    escalation_contact=ESCALATION_CONTACT,
)

ADAPTERS = [WhatsAppAdapter(), TelegramAdapter()]


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.channel_name)
def test_send_alert_reports_delivered_for_go(adapter):
    result = adapter.send_alert(GO_ALERT)

    assert result.delivered is True
    assert result.channel == adapter.channel_name
    assert result.claim_id == "CLM-101"


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.channel_name)
def test_send_alert_never_hides_the_failing_condition(adapter):
    """Even stubbed, the adapter must not collapse NO_GO to a bare verdict."""
    result = adapter.send_alert(NO_GO_ALERT)

    assert result.delivered is True
    assert "zone_safety_check" in result.detail
    assert "Safety Violation" in result.detail


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.channel_name)
def test_send_alert_states_escalation_contact_for_go_and_no_go(adapter):
    """Escalation Requirement: every alert states how to escalate, regardless of decision."""
    go_result = adapter.send_alert(GO_ALERT)
    no_go_result = adapter.send_alert(NO_GO_ALERT)

    assert ESCALATION_CONTACT in go_result.detail
    assert ESCALATION_CONTACT in no_go_result.detail


def test_whatsapp_parses_real_cloud_api_webhook_shape():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "15551234567", "text": {"body": "CLM-101"}},
                            ]
                        }
                    }
                ]
            }
        ]
    }

    query = WhatsAppAdapter().parse_inbound_status_query(payload)

    assert query.claim_id == "CLM-101"
    assert query.requester_id == "15551234567"
    assert query.channel == "whatsapp"


def test_whatsapp_rejects_malformed_webhook_payload():
    with pytest.raises(ValueError, match="Malformed WhatsApp webhook payload"):
        WhatsAppAdapter().parse_inbound_status_query({"entry": []})


def test_telegram_parses_real_bot_api_update_shape():
    payload = {"message": {"chat": {"id": 987654321}, "text": "CLM-101"}}

    query = TelegramAdapter().parse_inbound_status_query(payload)

    assert query.claim_id == "CLM-101"
    assert query.requester_id == "987654321"
    assert query.channel == "telegram"


def test_telegram_rejects_malformed_update_payload():
    with pytest.raises(ValueError, match="Malformed Telegram webhook payload"):
        TelegramAdapter().parse_inbound_status_query({"update_id": 1})
