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
escalation — Maestro only ever displays it, never acts on it). As of
the Supervisor Override Retirement (2026-08-05), that contact point no
longer names an override mechanism — see from_evidence_record()'s own
doc comment.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from src.maestro.directory import resolve_authority


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
    # List-valued as of 2026-08-18: resolve_authority() now returns every
    # applicable AuthorityBinding (reason_code tier, plus QP/QE if the
    # claim is a design alteration) -- see from_evidence_record() below.
    authority_binding_id: list[str] = []
    assigned_role: list[str] = []
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
    def from_evidence_record(cls, evidence: dict, zone_id: Optional[str]) -> "OutboundAlert":
        """
        Builds an OutboundAlert from a src/evidence/emitter.py record.
        Takes a plain dict (the emitted evidence shape) rather than
        importing src.evidence directly, so Maestro depends on
        Evidence's output shape without Evidence ever knowing Maestro
        exists.

        reason_code is carried straight through from the evidence
        record (present on every record since emit_evidence() started
        persisting it — R-<DOMAIN>-<NUMBER>, or None on GO; see
        CLAUDE.md's Reason Code Convention).

        Escalation Ownership (2026-07-31, Escalation Ownership
        Principle): who owns escalation is a resolved fact, not
        caller-supplied operational detail, so this now calls
        src/maestro/directory.py's resolve_authority(zone_id,
        reason_code, is_design_alteration) itself — keyed on the
        adjudicated failure reason (not claim_type/work_type) plus the
        claim's self-declared design-alteration flag — and populates
        recipient_id, authority_binding_id, assigned_role, and
        escalation_contact from every resolved AuthorityBinding. This
        applies uniformly to GO and NO_GO alike (GO resolves
        reason_code=None, landing on the ("*", "*") catch-all unless
        RTO's own routing applies) — the Escalation Requirement already
        applies to every alert regardless of decision. is_design_alteration
        is read from evidence["input_payload"] (2026-08-18) — the same
        already-established path zone_id/action_type are read from
        elsewhere in this codebase, since ClaimPayload's full model_dump()
        is what src/evidence/emitter.py persists as input_payload.

        List-valued as of 2026-08-18 (resolve_authority() now returns
        every applicable binding, not one — the reason_code tier and
        the design-alteration tier are orthogonal and both can be true
        at once): authority_binding_id and assigned_role are the full
        lists, in resolve_authority()'s order. recipient_id and
        escalation_contact stay single, human-readable strings (joined
        with ", " across all resolved bindings) rather than becoming
        lists themselves — they feed directly into adapter/formatting
        f-string interpolation (src/maestro/adapters/, formatting.py),
        which expects a scalar to display, not a structured value to
        parse. recipient_id falls back to a binding's role name when no
        real contact_id is on file yet (true for every binding today),
        rather than fabricating one.

        zone_id is not on the evidence record itself — Evidence stays
        decoupled from Airlock's ClaimPayload shape — so it's passed
        explicitly by the caller instead of reached for inside
        evidence["input_payload"].

        Supervisor Override Retirement (2026-08-05): escalation_contact
        no longer builds a directory.SUPERVISOR_OVERRIDE_URL link — the
        override endpoint it pointed to is retired, and an escalation
        contact must not point somewhere that no longer does anything.
        It now states the resolved authority(s) directly (role and
        binding_id), matching what the Frontline Worker screen already
        shows for the same resolved binding(s).
        """
        rule_trace = [RuleConditionResult(**rule) for rule in evidence["rule_trace"]]
        conflicting = None
        if evidence["decision"] == "NO_GO":
            conflicting = next((rule for rule in rule_trace if not rule.passed), None)

        is_design_alteration = evidence.get("input_payload", {}).get("is_design_alteration", False)
        bindings = resolve_authority(zone_id, evidence["reason_code"], is_design_alteration)
        recipient_id = ", ".join(binding.contact_id or binding.role for binding in bindings)
        escalation_contact = ", ".join(f"{binding.role} ({binding.binding_id})" for binding in bindings)

        return cls(
            claim_id=evidence["claim_id"],
            decision=evidence["decision"],
            rule_trace=rule_trace,
            conflicting_condition=conflicting,
            reason_code=evidence["reason_code"],
            authority_binding_id=[binding.binding_id for binding in bindings],
            assigned_role=[binding.role for binding in bindings],
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
