"""
Telegram webhook adapter.

Stub only: no live API calls. Inbound parsing is shaped to the real
Telegram Bot API update contract, and send_alert reports what would
have been sent instead of calling out to Telegram.

A real integration later needs (not set up here): a bot registered via
@BotFather to obtain a bot token, and a registered HTTPS webhook URL
(via setWebhook) — or long-polling as an alternative to webhooks.
"""
from src.maestro.adapters.base import ChannelAdapter
from src.maestro.formatting import render_alert_text
from src.maestro.schemas import DeliveryResult, OutboundAlert, StatusQuery


class TelegramAdapter(ChannelAdapter):
    channel_name = "telegram"

    def send_alert(self, alert: OutboundAlert) -> DeliveryResult:
        text = render_alert_text(alert)
        return DeliveryResult(
            claim_id=alert.claim_id,
            channel=self.channel_name,
            delivered=True,
            detail=f"[stub] would send Telegram message to chat {alert.recipient_id}: {text}",
        )

    def parse_inbound_status_query(self, raw_payload: dict) -> StatusQuery:
        """Expects the Telegram Bot API update shape: message.chat.id / message.text."""
        try:
            message = raw_payload["message"]
            requester_id = str(message["chat"]["id"])
            claim_id = message["text"].strip()
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Malformed Telegram webhook payload: {exc}") from exc

        return StatusQuery(claim_id=claim_id, requester_id=requester_id, channel=self.channel_name)
