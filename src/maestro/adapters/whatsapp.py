"""
WhatsApp webhook adapter.

Stub only: no live API calls. Inbound parsing is shaped to the real
WhatsApp Business (Meta Cloud API) webhook contract, and send_alert
reports what would have been sent instead of calling out to Meta.

A real integration later needs (not set up here): a Meta WhatsApp
Business Account, a verified sending phone number, a permanent access
token, and a webhook verify token + HTTPS callback URL registered with
Meta (or, via Twilio instead of Meta directly: a Twilio Account SID,
auth token, and an approved WhatsApp sender).
"""
from src.maestro.adapters.base import ChannelAdapter
from src.maestro.formatting import render_alert_text
from src.maestro.schemas import DeliveryResult, OutboundAlert, StatusQuery


class WhatsAppAdapter(ChannelAdapter):
    channel_name = "whatsapp"

    def send_alert(self, alert: OutboundAlert) -> DeliveryResult:
        text = render_alert_text(alert)
        return DeliveryResult(
            claim_id=alert.claim_id,
            channel=self.channel_name,
            delivered=True,
            detail=f"[stub] would send WhatsApp message to {alert.recipient_id}: {text}",
        )

    def parse_inbound_status_query(self, raw_payload: dict) -> StatusQuery:
        """Expects the WhatsApp Cloud API webhook shape: entry[0].changes[0].value.messages[0]."""
        try:
            message = raw_payload["entry"][0]["changes"][0]["value"]["messages"][0]
            requester_id = message["from"]
            claim_id = message["text"]["body"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Malformed WhatsApp webhook payload: {exc}") from exc

        return StatusQuery(claim_id=claim_id, requester_id=requester_id, channel=self.channel_name)
