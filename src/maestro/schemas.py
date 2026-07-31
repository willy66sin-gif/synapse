"""
Channel-agnostic outbound message contract for Maestro.

Per CLAUDE.md's System Principle ("Core decides, Maestro delivers"):
Core (src/core/) produces a decision and knows nothing about how it
gets delivered. Maestro sits downstream of src/evidence/emitter.py and
is the only layer that knows a decision needs to reach a person on
some channel — but even Maestro's own contract (this module) stays
channel-agnostic. WhatsApp/Telegram specifics live only in
src/maestro/adapters/.

OutboundAlert also encodes two of CLAUDE.md's Locked Design
Principles: the Supervisor UI Principle (never a standalone verdict —
GO/NO_GO is always paired with the full rule trace, and a NO_GO always
carries the specific failing condition), and the Escalation
Requirement (every alert, GO or NO_GO, must carry a contact point for
requesting an override — Maestro only ever displays it, never acts on
it).
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class RuleConditionResult(BaseModel):
    """One evaluated sub-condition. Mirrors src/core/rules.py's RuleOutcome."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    passed: bool
    reason: str


class OutboundAlert(BaseModel):
    """
    The payload Maestro hands to any delivery adapter. Carries the
    decision plus the full rule trace and, for NO_GO, the specific
    conflicting condition — a bare decision is never valid on its own.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    decision: Literal["GO", "NO_GO"]
    rule_trace: list[RuleConditionResult]
    conflicting_condition: Optional[RuleConditionResult] = None
    reason_code: Optional[str] = None
    evaluated_at: str
    recipient_id: str
    escalation_contact: str

    @model_validator(mode="after")
    def _conflicting_condition_matches_decision(self) -> "OutboundAlert":
        if self.decision == "NO_GO" and self.conflicting_condition is None:
            raise ValueError("NO_GO alerts must carry a conflicting_condition.")
        if self.decision == "GO" and self.conflicting_condition is not None:
            raise ValueError("GO alerts must not carry a conflicting_condition.")
        return self

    @classmethod
    def from_evidence_record(cls, evidence: dict, recipient_id: str, escalation_contact: str) -> "OutboundAlert":
        """
        Builds an OutboundAlert from a src/evidence/emitter.py record.
        Takes a plain dict (the emitted evidence shape) rather than
        importing src.evidence directly, so Maestro depends on
        Evidence's output shape without Evidence ever knowing Maestro
        exists. escalation_contact is operational contact info, not
        part of adjudication, so it can't be derived from the evidence
        record — it must be supplied by the caller.

        reason_code is carried straight through from the evidence
        record (present on every record since emit_evidence() started
        persisting it — R-<DOMAIN>-<NUMBER>, or None on GO; see
        CLAUDE.md's Reason Code Convention). This only makes the field
        available on OutboundAlert — no adapter or formatting logic
        branches on it yet; that's a deliberately separate, still-open
        decision (see CLAUDE.md's Open Items).
        """
        rule_trace = [RuleConditionResult(**rule) for rule in evidence["rule_trace"]]
        conflicting = None
        if evidence["decision"] == "NO_GO":
            conflicting = next((rule for rule in rule_trace if not rule.passed), None)

        return cls(
            claim_id=evidence["claim_id"],
            decision=evidence["decision"],
            rule_trace=rule_trace,
            conflicting_condition=conflicting,
            reason_code=evidence["reason_code"],
            evaluated_at=evidence["evaluated_at"],
            recipient_id=recipient_id,
            escalation_contact=escalation_contact,
        )


class StatusQuery(BaseModel):
    """Channel-agnostic representation of an inbound 'what's the status of claim X' query."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    requester_id: str
    channel: str


class DeliveryResult(BaseModel):
    """What a channel adapter reports back after attempting to send an OutboundAlert."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    channel: str
    delivered: bool
    detail: str
