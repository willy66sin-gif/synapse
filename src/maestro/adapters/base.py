"""
Abstract channel adapter interface.

This is the seam between Maestro's channel-agnostic schema
(src/maestro/schemas.py) and any specific messaging channel. Every
concrete channel — WhatsApp, Telegram, and whatever comes later —
implements this same interface, which is what makes the channel
interchangeable per CLAUDE.md's System Principle ("Core decides,
Maestro delivers").

Adapters only ever transport an already-decided OutboundAlert or parse
an inbound query. They must never evaluate rules, alter a decision, or
import anything from src/core/ or src/airlock/.
"""
from abc import ABC, abstractmethod

from src.maestro.schemas import DeliveryResult, OutboundAlert, StatusQuery


class ChannelAdapter(ABC):
    channel_name: str

    @abstractmethod
    def send_alert(self, alert: OutboundAlert) -> DeliveryResult:
        """Outbound: push a GO/NO_GO decision to alert.recipient_id on this channel."""

    @abstractmethod
    def parse_inbound_status_query(self, raw_payload: dict) -> StatusQuery:
        """Inbound: parse this channel's raw webhook payload into a StatusQuery."""
